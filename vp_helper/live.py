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
from .platform_clock import platform_now
from .scoring import UNRESOLVED_VERDICTS
from .models import Contest, VPError
from .accounts import account_key
from .parsing import contest_url
from .receipts import ReceiptReader
from .storage import workspace_directory as desktop, file_lock, read_history, read_json, state_directory, update_history, write_json
from .tui import LiveDashboard, TerminalDashboard
from .workspaces import allocate, promote, rename_existing

HISTORY_FIELDS = ["start_time", "contest", "url", "handle", "scoring", "points", "penalty", "rank", "phase", "frozen"]
PLATFORMS = {"cf": ("Codeforces", "vp-cf-submit", "cf_live_history.csv"),
             "gym": ("Codeforces Gym", "vp-cf-submit", "gym_live_history.csv"),
             "cwnuoj": ("CWNUOJ", "vp-hydro-submit", "cwnuoj_live_history.csv"),
             "nowcoder": ("Nowcoder", "vp-nowcoder-submit", "nowcoder_live_history.csv")}


def account_for(config: dict, platform: str) -> dict:
    return config.get(account_key(config, platform), {})


def live_adapter(url: str, config: dict):
    platform, _, canonical = contest_url(url)
    if platform in ("cf", "gym"):
        return CodeforcesLive(canonical, account_for(config, platform), **({"allow_gym": True} if platform == "gym" else {}))
    if platform == "cwnuoj":
        from .hydro import HydroLive
        return HydroLive(canonical, account_for(config, platform))
    if platform == "nowcoder":
        from .nowcoder import NowcoderLive
        return NowcoderLive(canonical, account_for(config, platform))
    raise VPError("Live mode supports regular Codeforces, CWNUOJ and Nowcoder contests.")


def configure_submission(api, contest: Contest, directory: Path) -> None:
    from .portable import bundled_submit, installed_command, bind_submit
    from .browser_login import browser_fields
    selected = state_directory() / "submit"
    script = api.account.get("submit_script") or shutil.which("submit")
    native = installed_command('vp-submit')
    installer = 'bash install.sh' if sys.platform == 'linux' else ('install.ps1' if sys.platform == 'win32' else 'sh install-macos.sh')
    if not native.is_file() or not os.access(native, os.X_OK):
        raise VPError(f"The live submit command is missing. Install this version with {installer}, then retry.")
    if not bundled_submit(script, selected):
        raise VPError(f"Live contests require the bundled submit entry. Use the submit installed by {installer}.")
    write_json(directory / "contest.json", {
        "platform": contest.platform, "mode": "live", "name": contest.name, "base_url": contest.url,
        "contest_id": int(contest.contest_id) if contest.platform in ("cf", "gym") else contest.contest_id,
        "problems": list(contest.problems), "problem_ids": contest.problems,
        **({"uid": contest.extra["uid"]} if contest.platform in ("cwnuoj", "nowcoder") else {}),
        "handle": api.handle, "cookie": api.account["cookie"],
        "user_agent": api.web.session.headers["User-Agent"], **browser_fields(api.account),
    })
    bind_submit(native, selected)


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
        config = cli.load_account(platform, live=True)
        api = live_adapter(canonical, config)
        if old.get("url") == canonical and old.get("mode") == "live" and api.handle.lower() != old.get("handle", "").lower():
            raise VPError("The configured account changed; restore this contest's account before resuming it.")
        cli.console.print(f"Checking {PLATFORMS[platform][0]} login, existing registration and official schedule…", markup=False)
        contest, snapshot = api.prepare()
        if (platform in ("cwnuoj", "nowcoder") and old.get("url") == canonical and old.get("mode") == "live"
                and old.get("contest", {}).get("extra", {}).get("uid") != contest.extra["uid"]):
            raise VPError("The user ID changed; restore this contest's account before resuming it.")
        name = (f"live-{platform}-{contest.contest_id}-{api.handle.lower()}" if platform in ("cf", "gym") else
                f"live-cwnuoj-{contest.extra['domain']}-{contest.contest_id}-{contest.extra['uid']}" if platform == "cwnuoj" else
                f"live-nowcoder-{contest.contest_id}-{contest.extra['uid']}")
        path = root / (name + ".json")
        saved = read_json(path)
        if saved and (saved.get("mode") != "live" or saved.get("handle", "").lower() != api.handle.lower()):
            raise VPError("The saved live participation belongs to another account or mode.")
        from .freeze import configure, retain
        if saved:
            retain(contest, Contest.from_dict(saved["contest"]))
        configure(contest)
        if saved:
            rename_existing(path, saved, contest)
            directory = Path(saved["directory"])
            target = directory
        else:
            directory, target = allocate(desktop(), contest, root, api.handle)
        state = {**saved, "mode": "live", "contest": contest.to_dict(), "url": canonical,
                 "start_time": contest.original_start, "handle": api.handle, "directory": str(directory),
                 "tmux_session": name, "status": "running", "submissions": saved.get("submissions", []),
                 "submissions_complete": saved.get("submissions_complete", False),
                 "snapshot": snapshot, "history_path": saved.get("history_path") or str(desktop() / PLATFORMS[platform][2])}
        state.setdefault("created_at", platform_now(canonical))
        if directory != target:
            state["workspace_target"] = str(target)
        write_json(path, state)
        cli.configure_contest(api, contest, directory, path, state, live=True)
        promote(path, state)
        directory = Path(state["directory"])
        cli.bind_submit_context(directory, path, state)
        write_json(root / "current.json", {"state_path": str(path)})
        cli.console.print("Prepared " + str(directory) + " · existing registration retained", markup=False)
        return path, state


def fetch(api, saved_contest: dict, directory: Path | None = None):
    try:
        contest = Contest.from_dict(saved_contest)
        if directory is None:
            records = api.submissions(contest)
            message = getattr(api, 'submission_message', '')
            return records, message if isinstance(message, str) else ''
        previous = Contest.from_dict(saved_contest)
        snapshot = api.snapshot(contest)
        from .freeze import retain
        retain(contest, previous)
        if contest.extra.get('freeze_source') in ('manual', 'room'):
            snapshot["frozen"] = bool(snapshot.get("frozen") or contest.rules.frozen(platform_now(contest.url) - contest.original_start))
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
    if state.get('practice_view'):
        from .practice import saved_rank
        state.setdefault('practice_rank', saved_rank(state))
    own = snapshot.get("own")
    final = (snapshot.get("phase") == "FINISHED" and not snapshot.get("frozen") and own is not None
             and state.get('submissions_complete', True)
             and submissions_at >= snapshot.get("at", float("inf"))
             and not any(e["verdict"] in UNRESOLVED_VERDICTS | {"PENDING"} for e in events.values() if not e.get("practice")))
    from .sessions import merge_records, save_state
    merged = merge_records(path, {**state, "contest": contest.to_dict()}, list(events.values()))
    state.update(contest=contest.to_dict(), start_time=contest.original_start, snapshot=snapshot,
                 submissions=merged, status="complete" if final else "running",
                 last_sync_time=submissions_at)
    from .external_rank import attachment_path
    state.pop('external_result', None)
    if attachment_path(path).exists():
        from .cloud import sync_payload
        result = sync_payload(path, state)
        if result['rankSource'] == 'original' or result['prize'] is not None:
            state['external_result'] = {'rank':result['rank'],'prize':result['prize']}
    save_state(path, state)
    if snapshot:
        update_history(Path(state["history_path"]), {
            "start_time": datetime.fromtimestamp(contest.original_start, timezone.utc).isoformat(),
            "contest": contest.name, "url": contest.url, "handle": state["handle"],
            "scoring": contest.extra["scoring"], "points": own["points"] if own else "",
            "penalty": own["penalty"] if own and contest.extra["scoring"] == "ICPC" else "",
            "rank": state.get('practice_rank', '—') if state.get('practice_view') else own["rank"] if own else "", "phase": snapshot["phase"], "frozen": snapshot["frozen"],
        }, fields=HISTORY_FIELDS, key_fields=("start_time", "url", "handle"))


def monitor_live(path: Path) -> None:
    from . import cli
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        if sys.platform == 'win32':
            from .windows_terminal import track_monitor
            track_monitor(path)
        state = read_json(path)
        if state.get('practice_view') and 'practice_rank' not in state:
            from .practice import saved_rank
            from .sessions import save_state
            state['practice_rank'] = saved_rank(state)
            save_state(path, state)
        contest = Contest.from_dict(state["contest"])
        config = read_json(cli.config_directory() / "config.json")
        account = account_for(config, contest.platform)
        if account.get("handle", "").lower() != state["handle"].lower():
            raise VPError("The configured account changed; restore this contest's account before resuming it.")
        api, reference_api = live_adapter(contest.url, config), live_adapter(contest.url, config)
        events = {s["id"]: s for s in state.get("submissions", [])}
        snapshot = state.get("snapshot", {})
        receipts = ReceiptReader(path, state)
        submission_future = reference_future = None
        next_submissions = next_reference = next_render = 0.0
        submissions_at = 0.0
        submission_start = contest.original_start
        submission_error = reference_error = ""
        backoff = 5.0
        from .windows_terminal import view_options
        with ThreadPoolExecutor(max_workers=3) as network, TerminalDashboard(**view_options(path)) as view:
            while view.input():
                now = time.monotonic()
                changed = False
                for receipt in receipts.read():
                    if receipt.id not in events:
                        events[receipt.id] = asdict(receipt)
                        next_submissions = 0.0
                        changed = True
                if submission_future is not None and submission_future.done():
                    incoming, submission_error = submission_future.result()
                    submission_future = None
                    if incoming is not None and submission_start == contest.original_start:
                        from .sessions import apply_submission_sync
                        state["submissions"] = list(events.values())
                        events = {e['id']: e for e in apply_submission_sync(path, state, incoming, poll_revision)}
                        state['submissions_complete'] = (getattr(api, 'submissions_complete', True) is not False
                                                         and bool(contest.problems))
                        submissions_at = platform_now(contest.url)
                        changed = True
                    pending = any(e["verdict"] == "PENDING" for e in events.values())
                    backoff = min(60.0, backoff * 2) if submission_error and incoming is None else 2.1 if pending else 5.0
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
                    remaining = contest.original_start - platform_now(contest.url)
                    next_reference = now + (max(2.1, min(30.0, remaining)) if remaining > 0 else 15.0)
                if reference_future is None and now >= next_reference:
                    reference_future = network.submit(fetch, reference_api, contest.to_dict(), Path(state["directory"]))
                if submission_future is None and now >= next_submissions and platform_now(contest.url) >= contest.original_start:
                    from .sessions import reserve_submission_sync
                    poll_revision = reserve_submission_sync(path)
                    from .pending_results import seed_pending
                    seed_pending(api, contest.platform, list(events.values()))
                    submission_start = contest.original_start
                    submission_future = network.submit(fetch, api, contest.to_dict())
                if changed:
                    save_progress(path, state, contest, snapshot, events, submissions_at)
                    cli.bind_submit_context(Path(state["directory"]), path, state)
                    # Populate newly published problem labels for submit.
                    with file_lock(path.parent / "workspaces.lock"):
                        state["directory"] = read_json(path).get("directory", state["directory"])
                        config_path = Path(state["directory"]) / "contest.json"
                        submission_config = read_json(config_path)
                        if submission_config.get("vp_context", {}).get("state_path") in (None, str(path)):
                            submission_config["problems"] = list(contest.problems)
                            submission_config["problem_ids"] = contest.problems
                            write_json(config_path, submission_config)
                phase = snapshot.get("phase", contest.extra["phase"])
                age = platform_now(contest.url) - snapshot.get("at", 0)
                message = submission_error or reference_error or snapshot.get("message", "")
                if phase != "BEFORE" and (not submissions_at or state.get('submissions_complete') is False):
                    history_message = ("Syncing submission history from the contest start..." if not submissions_at else
                                       "Submission history incomplete; retrying older pages.")
                    message = history_message + (" · " + message if message else "")
                if age > 45 and phase != "BEFORE":
                    message = "Standings stale · " + (message or "reconnecting")
                if submissions_at and platform_now(contest.url) - submissions_at > 15:
                    message = "Submissions stale · " + (message or "reconnecting")
                if now >= next_render:
                    state["practice_view"] = bool(state.get("practice_view") or read_json(path).get("practice_view"))
                    dashboard = LiveDashboard(contest, platform_now(contest.url) - contest.original_start, snapshot,
                                              submission_records(list(events.values()), phase), message,
                                              state["handle"], practice=state["practice_view"],
                                              practice_rank=read_json(path).get('practice_rank', state.get('practice_rank', '—')))
                    dashboard.participant_label = state.get("cloud", {}).get("label", "")
                    dashboard.final_rank = state.get('external_result', {}).get('rank')
                    dashboard.prize = state.get('external_result', {}).get('prize') or ''
                    from .identity import dashboard_identity
                    title, subtitle = dashboard_identity(state, path)
                    if state.get('cloud') or subtitle or title != state['handle']:
                        dashboard.participant_title, dashboard.participant_subtitle = title, subtitle
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
    if sys.argv[1:] in ([], ["-h"], ["--help"]):
        print("查看全部命令和设置：xcpc --help")
        return
    parser = argparse.ArgumentParser(usage="live cf ID | live paa ID | live nk ID | live CONTEST_URL",
                                     description="Attach to an existing Codeforces, CWNUOJ or Nowcoder contest registration")
    parser.add_argument("target", help="cf ID, paa ID (CWNUOJ), nk ID (Nowcoder), or CONTEST_URL")
    parser.add_argument("id", nargs="?")
    args = parser.parse_args()
    from . import cli
    try:
        from .parsing import contest_id_url
        if args.id is not None:
            if args.target not in ("cf", "paa", "nk"):
                raise VPError("Usage: live cf ID, live paa ID, live nk ID or live CONTEST_URL")
            url = contest_id_url(args.target, args.id)
        else:
            url = args.target
        cli.start(url, live=True)
    except (VPError, OSError, cli.subprocess.CalledProcessError) as exc:
        cli.console.print("live: " + str(exc), style="red", markup=False)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
