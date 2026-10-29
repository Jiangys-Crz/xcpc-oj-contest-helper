"""Create a fresh native VP; replace local counting only after confirmation."""
from __future__ import annotations

from pathlib import Path
import time
from uuid import uuid4

from .models import Contest, VPError, StartRejected
from .parsing import contest_url
from .sessions import attempts, boundary, close_attempt
from .storage import file_lock, read_json, write_json
from .workspaces import allocate, promote


def _publish_started(path: Path, state: dict, api) -> None:
    """Only a verified native start may replace an existing local attempt."""
    from . import cli
    promote(path, state)
    cli.configure_contest(api, Contest.from_dict(state["contest"]), Path(state["directory"]), path, state)
    for previous_path, previous in attempts(path.parent):
        if (previous_path != path and previous.get("mode", "vp") == "vp"
                and previous.get("status") not in ("failed", "cancelled")
                and previous["url"] == state["url"]
                and previous["handle"].casefold() == state["handle"].casefold()):
            close_attempt(previous_path, state["start_time"], state["attempt_id"])
    write_json(path.parent / "current.json", {"state_path": str(path)})
    cli.bind_submit_context(Path(state["directory"]), path, state)


def _fail(path: Path, state: dict, error: Exception) -> None:
    state.update(status="failed", activation_error=str(error), activation_uncertain=False)
    write_json(path, state)
    current = path.parent / "current.json"
    if read_json(current).get("state_path") == str(path):
        write_json(current, state.get("previous_current", {}))


def activate(path: Path, state: dict) -> dict:
    with file_lock(path.parent / "start.lock"):
        return _activate(path)


def _activate(path: Path) -> dict:
    from . import cli
    with file_lock(path.with_suffix(".activate.lock")):
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
        recovering = bool(state.get("activation_uncertain"))
        try:
            if recovering:
                recovered = (api._virtual_party() if contest.platform in ("cf", "gym")
                             else api._registration(duration=contest.rules.duration))
                started = recovered.get("startTimeSeconds") if isinstance(recovered, dict) else recovered[0] if recovered else None
                if started is None or abs(started-state["activation_target"]) > 60:
                    raise VPError("The platform has not confirmed the requested VP start. The start request will not be repeated.")
                if not isinstance(recovered, dict):
                    contest.rules.duration = recovered[1]
            else:
                state["activation_target"] = max(state["requested_start"], time.time())
                # If interrupted during POST, recovery is query-only, even when
                # the process could not persist the response.
                state["activation_uncertain"] = True
                write_json(path, state)
                api.native_post_sent = False
                if contest.platform in ("cf", "gym"):
                    started = api.start(contest, requested_start=state["activation_target"], fresh=True)
                else:
                    started = api.start(contest, fresh=True)
                if abs(started-state["activation_target"]) > 60:
                    raise StartRejected("原站返回的 VP 开始时间与本次请求不符，本次命令已取消。")
        except StartRejected as exc:
            _fail(path, state, exc)
            raise
        except (VPError, OSError) as exc:
            uncertain = recovering or getattr(api, "native_post_sent", False) is True
            state.update(activation_error=str(exc), activation_uncertain=bool(uncertain))
            write_json(path, state)
            raise
        state.update(start_time=started, contest=contest.to_dict(), status="running", activation_uncertain=False)
        write_json(path, state)
        _publish_started(path, state, api)
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
        config = cli.load_account(platform)
        api = cli.adapter(canonical, config)
        previous = [(p, s) for p, s in attempts(root)
                    if s.get("mode", "vp") == "vp" and s.get("status") not in ("failed", "cancelled") and s["url"] == canonical
                    and s["handle"].casefold() == api.handle.casefold()]
        pending = next((s for _, s in previous if s.get("status") == "scheduled" and s.get("activation_uncertain")), None)
        if pending:
            raise VPError("上一轮启动结果尚未确认。请用 vp history " + pending["attempt_id"] +
                          " --open 继续核验；不会重复发送启动请求。")
        cli.console.print("Reading contest information…", markup=False)
        contest = api.prepare()
        contest.directory_name = " ".join(contest.directory_name.split())
        number = len(previous) + 1
        attempt_id = uuid4().hex[:10]
        requested = scheduled_start if scheduled_start is not None else time.time()
        if platform == "qoj":
            api.check_start(contest)
        contest.extra.update(attempt=number, attempt_id=attempt_id)
        cli.configure_submit(api, contest, cli.desktop(), validate_only=True)
        directory, target = allocate(cli.desktop(), contest, root, api.handle)
        existing_folder = (directory / "contest.json").exists()
        if not existing_folder:
            cli.configure_submit(api, contest, directory)
        for name, pdf in contest.pdfs.items():
            if existing_folder or (directory / name).exists():
                continue
            try:
                api.web.pdf(pdf, directory / name)
            except (VPError, OSError) as exc:
                cli.console.print(f"Statement download skipped: {exc}. Use the original contest page.", markup=False)
        path = root / f"vp-{platform}-{contest.contest_id}-{attempt_id}.json"
        state = {"mode": "vp", "attempt": number, "attempt_id": attempt_id, "contest": contest.to_dict(), "created_at": time.time(),
                 "url": canonical, "start_time": requested, "requested_start": requested,
                 "handle": api.handle, "directory": str(directory), "tmux_session": path.stem,
                 "status": "scheduled", "submissions": [], "recent": [], "previous_current": current,
                 "history_path": str(cli.desktop() / "vp_history.csv")}
        if directory != target:
            state["workspace_target"] = str(target)
        write_json(path, state)
        cli.bind_submit_context(directory, path, state)
        # Codeforces confirms a future registration now; QOJ preflights the
        # reservation and sends its single native start request at the deadline.
        if platform in ("cf", "gym") or requested <= time.time():
            try:
                state = _activate(path)
            except StartRejected:
                raise
            except (VPError, OSError) as exc:
                state = read_json(path)
                if state.get("status") == "running":
                    raise VPError("原站已确认 VP，目录切换未完成。请用 vp history " + attempt_id + " --open 恢复。") from exc
                if not state.get("activation_uncertain"):
                    _fail(path, state, exc)
                    raise
                cli.console.print("Waiting for automatic start verification: " + str(exc), markup=False)
        if state.get("status") == "scheduled":
            write_json(root / "current.json", {"state_path": str(path)})
        cli.console.print(f"VP #{number} · {attempt_id} · {state['directory']}", markup=False)
        return path, state
