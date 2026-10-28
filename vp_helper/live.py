"""Lifecycle of existing Codeforces, CWNUOJ and Nowcoder participations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import sys
import time

from rich.table import Table

from .codeforces_live import CodeforcesLive, submission_records
from .models import Contest, VPError
from .parsing import contest_url
from .receipts import ReceiptReader
from .storage import desktop, file_lock, read_history, read_json, state_directory, update_history, write_json
from .tui import LiveDashboard, TerminalDashboard

HISTORY_FIELDS = ["start_time", "contest", "url", "handle", "scoring", "points", "penalty", "rank", "phase", "frozen"]
PLATFORMS = {"cf": ("Codeforces", "vp-cf-submit", "cf_live_history.csv"),
             "cwnuoj": ("CWNUOJ", "vp-hydro-submit", "cwnuoj_live_history.csv"),
             "nowcoder": ("Nowcoder", "vp-nowcoder-submit", "nowcoder_live_history.csv")}


def account_for(config: dict, platform: str) -> dict:
    return config.get(platform, config.get("gym", {}) if platform == "cf" else {})


def live_adapter(url: str, config: dict):
    platform, _, canonical = contest_url(url)
    if platform == "cf":
        return CodeforcesLive(canonical, account_for(config, platform))
    if platform == "cwnuoj":
        from .hydro import HydroLive
        return HydroLive(canonical, account_for(config, platform))
    if platform == "nowcoder":
        from .nowcoder import NowcoderLive
        return NowcoderLive(canonical, account_for(config, platform))
    raise VPError("Live mode supports regular Codeforces, CWNUOJ and Nowcoder contests.")


def configure_submission(api, contest: Contest, directory: Path) -> None:
    from .browser_login import browser_fields
    selected = state_directory() / "submit"
    script = api.account.get("submit_script") or shutil.which("submit")
    native = Path(sys.prefix) / "bin" / PLATFORMS[contest.platform][1]
    if not native.is_file() or not os.access(native, os.X_OK):
        raise VPError("The live submit command is missing. Install this version with bash install.sh, then retry.")
    if not script or not Path(script).is_symlink() or Path(os.readlink(script)) != selected:
        raise VPError("Live contests require the bundled submit entry. Use the submit installed by bash install.sh.")
    write_json(directory / "contest.json", {
        "platform": contest.platform, "mode": "live", "name": contest.name, "base_url": contest.url,
        "contest_id": int(contest.contest_id) if contest.platform == "cf" else contest.contest_id,
        "problems": list(contest.problems), "problem_ids": contest.problems,
        **({"uid": contest.extra["uid"]} if contest.platform in ("cwnuoj", "nowcoder") else {}),
        "handle": api.handle, "cookie": api.account["cookie"],
        "user_agent": api.web.session.headers["User-Agent"], **browser_fields(api.account),
    })
    temporary = selected.with_name("submit.new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(native)
    os.replace(temporary, selected)


def prepare_live(url: str) -> tuple[Path, dict]:
    from . import cli
    cli.check_terminal()
    platform, _, canonical = contest_url(url)
    if platform not in PLATFORMS:
        raise VPError("Live mode supports regular Codeforces, CWNUOJ and Nowcoder contests only.")
    root = state_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(root / "start.lock"):
        current = read_json(root / "current.json")
        old = read_json(Path(current["state_path"])) if current else {}
        if (old and (old.get("url") != canonical or old.get("mode") != "live")
                and old.get("status") != "complete"
                and time.time() < old.get("start_time", 0) + old.get("contest", {}).get("rules", {}).get("duration", 0)):
            raise VPError(f"Another contest is still active: {old.get('url')}. Resume it before switching contests.")
        config = cli.load_account(platform, live=True)
        api = live_adapter(canonical, config)
        if old.get("url") == canonical and old.get("mode") == "live" and api.handle.lower() != old.get("handle", "").lower():
            raise VPError("The configured account changed; restore this contest's account before resuming it.")
        cli.console.print(f"Checking {PLATFORMS[platform][0]} login, existing registration and official schedule…", markup=False)
        contest, snapshot = api.prepare()
        if (platform in ("cwnuoj", "nowcoder") and old.get("url") == canonical and old.get("mode") == "live"
                and old.get("contest", {}).get("extra", {}).get("uid") != contest.extra["uid"]):
            raise VPError("The user ID changed; restore this contest's account before resuming it.")
        name = (f"live-cf-{contest.contest_id}-{api.handle.lower()}" if platform == "cf" else
                f"live-cwnuoj-{contest.extra['domain']}-{contest.contest_id}-{contest.extra['uid']}" if platform == "cwnuoj" else
                f"live-nowcoder-{contest.contest_id}-{contest.extra['uid']}")
        path = root / (name + ".json")
        saved = read_json(path)
        if saved and (saved.get("mode") != "live" or saved.get("handle", "").lower() != api.handle.lower()):
            raise VPError("The saved live participation belongs to another account or mode.")
        directory = Path(saved["directory"]) if saved else desktop() / (contest.directory_name + " live")
        directory.mkdir(parents=True, exist_ok=True)
        configure_submission(api, contest, directory)
        state = {**saved, "mode": "live", "contest": contest.to_dict(), "url": canonical,
                 "start_time": contest.original_start, "handle": api.handle, "directory": str(directory),
                 "tmux_session": name, "status": "running", "submissions": saved.get("submissions", []),
                 "snapshot": snapshot, "history_path": str(desktop() / PLATFORMS[platform][2])}
        write_json(path, state)
        cli.bind_submit_context(directory, path, state)
        write_json(root / "current.json", {"state_path": str(path)})
        cli.console.print("Prepared " + str(directory) + " · existing registration retained", markup=False)
        return path, state


def fetch(api, saved_contest: dict, directory: Path | None = None):
    try:
        contest = Contest.from_dict(saved_contest)
        if directory is None:
            return api.submissions(contest), ""
        snapshot = api.snapshot(contest)
        return (contest.to_dict(), snapshot), ""
    except (VPError, OSError) as exc:
        return None, str(exc)


def fetch_statements(api, saved_contest: dict, directory: Path) -> str:
    try:
        api.save_statements(Contest.from_dict(saved_contest), directory)
        return ""
    except (VPError, OSError) as exc:
        return "Statement download skipped: " + str(exc)


def save_progress(path: Path, state: dict, contest: Contest, snapshot: dict,
                  events: dict[str, dict], submissions_at: float) -> None:
    own = snapshot.get("own")
    final = (snapshot.get("phase") == "FINISHED" and not snapshot.get("frozen") and own is not None
             and submissions_at >= snapshot.get("at", float("inf"))
             and not any(e["verdict"] in ("PENDING", "UNAVAILABLE", "HIDDEN", "SYSTEM-ERROR") for e in events.values() if not e.get("practice")))
    state.update(contest=contest.to_dict(), start_time=contest.original_start, snapshot=snapshot,
                 submissions=list(events.values()), status="complete" if final else "running",
                 last_sync_time=submissions_at)
    write_json(path, state)
    if snapshot:
        update_history(Path(state["history_path"]), {
            "start_time": datetime.fromtimestamp(contest.original_start, timezone.utc).isoformat(),
            "contest": contest.name, "url": contest.url, "handle": state["handle"],
            "scoring": contest.extra["scoring"], "points": own["points"] if own else "",
            "penalty": own["penalty"] if own and contest.extra["scoring"] == "ICPC" else "",
            "rank": own["rank"] if own else "", "phase": snapshot["phase"], "frozen": snapshot["frozen"],
        }, fields=HISTORY_FIELDS, key_fields=("start_time", "url", "handle"))


def monitor_live(path: Path) -> None:
    from . import cli
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        state = read_json(path)
        contest = Contest.from_dict(state["contest"])
        config = read_json(cli.config_directory() / "config.json")
        account = account_for(config, contest.platform)
        if account.get("handle", "").lower() != state["handle"].lower():
            raise VPError("The configured account changed; restore this contest's account before resuming it.")
        api, reference_api = live_adapter(contest.url, config), live_adapter(contest.url, config)
        events = {s["id"]: s for s in state.get("submissions", [])}
        snapshot = state.get("snapshot", {})
        receipts = ReceiptReader(path, state)
        submission_future = reference_future = statement_future = None
        next_submissions = next_reference = next_render = next_statements = 0.0
        submissions_at = 0.0
        submission_start = contest.original_start
        submission_error = reference_error = statement_error = ""
        backoff = 5.0
        with ThreadPoolExecutor(max_workers=3) as network, TerminalDashboard() as view:
            while view.input():
                now = time.monotonic()
                changed = False
                if statement_future is not None and statement_future.done():
                    statement_error = statement_future.result()
                    statement_future = None
                    next_statements = now + 300.0
                for receipt in receipts.read():
                    if receipt.id not in events:
                        events[receipt.id] = asdict(receipt)
                        next_submissions = 0.0
                        changed = True
                if submission_future is not None and submission_future.done():
                    incoming, submission_error = submission_future.result()
                    submission_future = None
                    if incoming is not None and submission_start == contest.original_start:
                        for event in incoming:
                            events[event["id"]] = event
                        submissions_at = time.time()
                        changed = True
                    pending = any(e["verdict"] == "PENDING" for e in events.values())
                    backoff = min(60.0, backoff * 2) if submission_error else 2.1 if pending else 5.0
                    next_submissions = now + backoff
                if reference_future is not None and reference_future.done():
                    incoming, reference_error = reference_future.result()
                    reference_future = None
                    if incoming is not None:
                        previous_start = contest.original_start
                        contest = Contest.from_dict(incoming[0])
                        snapshot = incoming[1]
                        if previous_start != contest.original_start:
                            # A delayed official start changes all relative times.
                            events.clear()
                            submissions_at = 0.0
                        changed = True
                        if snapshot["phase"] == "FINISHED" or previous_start != contest.original_start:
                            next_submissions = 0.0
                    # Refresh near the scheduled start promptly, with normal
                    # API backoff if the official scoreboard is not open yet.
                    remaining = contest.original_start - time.time()
                    next_reference = now + (max(2.1, min(30.0, remaining)) if remaining > 0 else 15.0)
                if reference_future is None and now >= next_reference:
                    reference_future = network.submit(fetch, reference_api, contest.to_dict(), Path(state["directory"]))
                if submission_future is None and now >= next_submissions and time.time() >= contest.original_start:
                    submission_start = contest.original_start
                    submission_future = network.submit(fetch, api, contest.to_dict())
                if statement_future is None and now >= next_statements and contest.problems:
                    statement_future = network.submit(fetch_statements, reference_api, contest.to_dict(), Path(state["directory"]))
                if changed:
                    save_progress(path, state, contest, snapshot, events, submissions_at)
                    cli.bind_submit_context(Path(state["directory"]), path, state)
                    # Populate newly published problem labels for submit.
                    config_path = Path(state["directory"]) / "contest.json"
                    submission_config = read_json(config_path)
                    submission_config["problems"] = list(contest.problems)
                    submission_config["problem_ids"] = contest.problems
                    write_json(config_path, submission_config)
                phase = snapshot.get("phase", contest.extra["phase"])
                age = time.time() - snapshot.get("at", 0)
                message = submission_error or reference_error or snapshot.get("message", "") or statement_error
                if age > 45 and phase != "BEFORE":
                    message = "Standings stale · " + (message or "reconnecting")
                if submissions_at and time.time() - submissions_at > 15:
                    message = "Submissions stale · " + (message or "reconnecting")
                if now >= next_render:
                    dashboard = LiveDashboard(contest, time.time() - contest.original_start, snapshot,
                                              submission_records(list(events.values()), phase), message,
                                              state["handle"])
                    view.update(dashboard)
                    next_render = now + 0.25
                time.sleep(0.1)


def history() -> None:
    from .cli import console
    rows = [row for _, _, filename in PLATFORMS.values()
            for row in read_history(desktop() / filename, fields=HISTORY_FIELDS)]
    table = Table(title="Live contests")
    columns = ("start_time", "contest", "scoring", "points", "penalty", "rank", "phase", "frozen")
    for key in columns:
        table.add_column(key)
    for row in sorted(rows, key=lambda r: r["start_time"], reverse=True):
        table.add_row(*(row.get(key) or "—" for key in columns))
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach to an existing Codeforces, CWNUOJ or Nowcoder contest registration")
    parser.add_argument("url", help="Codeforces, CWNUOJ or https://ac.nowcoder.com/acm/contest/ID contest URL")
    args = parser.parse_args()
    from . import cli
    try:
        cli.start(args.url, live=True)
    except (VPError, OSError, cli.subprocess.CalledProcessError) as exc:
        cli.console.print("live: " + str(exc), style="red", markup=False)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
