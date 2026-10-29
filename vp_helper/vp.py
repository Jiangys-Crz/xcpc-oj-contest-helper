"""Create a fresh, scheduled or immediate VP without replacing prior attempts."""
from __future__ import annotations

from pathlib import Path
import time
from uuid import uuid4

from .models import Contest, VPError
from .parsing import contest_url
from .sessions import attempts, boundary, close_attempt
from .storage import file_lock, read_json, write_json


def activate(path: Path, state: dict) -> dict:
    from . import cli
    with file_lock(path.with_suffix(".activate.lock"), nonblocking=True):
        state = read_json(path)
        if state.get("status") != "scheduled":
            return state
        cutoff = boundary(path)
        if cutoff is not None and time.time() >= cutoff:
            state["status"] = "cancelled"
            write_json(path, state)
            return state
        contest = Contest.from_dict(state["contest"])
        config = cli.load_account(contest.platform)
        api = cli.adapter(contest.url, config)
        if api.handle.casefold() != state["handle"].casefold():
            raise VPError("The scheduled VP account changed. Restore its Firefox login.")
        requested = state["requested_start"]
        try:
            if contest.extra.get("local_attempt"):
                started = requested
            elif state.get("activation_uncertain"):
                recovered = (api._virtual_party() if contest.platform in ("cf", "gym")
                             else api._registration(duration=contest.rules.duration))
                started = recovered.get("startTimeSeconds") if isinstance(recovered, dict) else recovered[0] if recovered else None
                if started is None or abs(started-state["activation_target"]) > 60:
                    raise VPError("The platform has not confirmed the requested VP start. The start request will not be repeated.")
            elif contest.platform in ("cf", "gym"):
                state["activation_target"] = max(requested, time.time())
                started = api.start(contest, requested_start=state["activation_target"])
            else:
                state["activation_target"] = max(requested, time.time())
                started = api.start(contest)
                if abs(started-state["activation_target"]) > 60:
                    contest.extra.update(local_attempt=True, native_start_time=started)
                    started = requested
        except (VPError, OSError) as exc:
            state.update(activation_error=str(exc),
                         activation_uncertain=state.get("activation_uncertain") or getattr(api, "native_post_sent", False) is True)
            write_json(path, state)
            raise
        state.update(start_time=started, contest=contest.to_dict(), status="running")
        cli.configure_submit(api, contest, Path(state["directory"]))
        write_json(path, state)
        cli.bind_submit_context(Path(state["directory"]), path, state)
        return state


def prepare(url: str, scheduled_start: float | None = None) -> tuple[Path, dict]:
    from . import cli
    cli.check_terminal()
    platform, _, canonical = contest_url(url)
    if platform not in ("qoj", "cf", "gym"):
        raise VPError("Use live cf ID, live paa ID, live nk ID or live CONTEST_URL for this contest.")
    root = cli.state_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(root / "start.lock"):
        current = read_json(root / "current.json")
        old = read_json(Path(current["state_path"])) if current else {}
        if (old and old.get("url") != canonical and old.get("status") not in ("complete", "archived")
                and time.time() < old.get("start_time", 0) + old.get("contest", {}).get("rules", {}).get("duration", 0)):
            raise VPError(f"Another contest is still active: {old.get('url')}.")
        config = cli.load_account(platform)
        api = cli.adapter(canonical, config)
        previous = [(p, s) for p, s in attempts(root)
                    if s.get("mode") != "live" and s["url"] == canonical
                    and s["handle"].casefold() == api.handle.casefold()]
        cli.console.print("Reading contest information…", markup=False)
        contest = api.prepare()
        contest.directory_name = " ".join(contest.directory_name.split())
        number = len(previous) + 1
        attempt_id = uuid4().hex[:10]
        requested = scheduled_start if scheduled_start is not None else time.time()
        if previous:
            while any(abs(requested-s["start_time"]) < 0.0000005 for _, s in previous):
                requested += 0.000001
            latest = max(previous, key=lambda item: item[1].get("attempt", 0))[1]
            contest.extra.update(local_attempt=True,
                                 native_start_time=latest["contest"].get("extra", {}).get("native_start_time", latest["start_time"]))
        contest.extra.update(attempt=number, attempt_id=attempt_id)
        directory = cli.desktop() / f"{contest.directory_name} VP{number}-{attempt_id}"
        directory.mkdir(parents=True, exist_ok=False)
        cli.configure_submit(api, contest, directory)
        for name, pdf in contest.pdfs.items():
            try:
                api.web.pdf(pdf, directory / name)
            except (VPError, OSError) as exc:
                cli.console.print(f"Statement download skipped: {exc}. Use the original contest page.", markup=False)
        path = root / f"vp-{platform}-{contest.contest_id}-{attempt_id}.json"
        state = {"mode": "vp", "attempt": number, "attempt_id": attempt_id, "contest": contest.to_dict(),
                 "url": canonical, "start_time": requested, "requested_start": requested,
                 "handle": api.handle, "directory": str(directory), "tmux_session": path.stem,
                 "status": "scheduled", "submissions": [], "recent": [],
                 "history_path": str(cli.desktop() / "vp_history.csv")}
        write_json(path, state)
        cli.bind_submit_context(directory, path, state)
        write_json(root / "current.json", {"state_path": str(path)})
        for previous_path, _ in previous:
            close_attempt(previous_path, requested, attempt_id)
        # Codeforces accepts a scheduled native registration now. QOJ starts
        # when the background monitor reaches the requested wall-clock time.
        if (platform in ("cf", "gym") and not previous) or requested <= time.time():
            try:
                state = activate(path, state)
            except (VPError, OSError) as exc:
                cli.console.print("Waiting for automatic start verification: " + str(exc), markup=False)
                state = read_json(path)
        cli.console.print(f"VP #{number} · {attempt_id} · {directory}", markup=False)
        return path, state
