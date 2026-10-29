from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import getpass
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from rich.console import Console
from rich.table import Table
from rich import box

from .models import Contest, Reference, Submission, VPError, StartRejected
from .browser_login import browser_fields, refresh_browser_account, login_diagnostics, verify_stored_session
from .history_lines import enrich_history
from .parsing import contest_url, contest_id_url
from .receipts import ReceiptReader
from .scoring import medal_lines, rank_of, rank_label, prize_of, recent_submissions, revealed_problems, score_submissions, statistics, submission_history
from .storage import config_directory, workspace_directory as desktop, file_lock, read_history, read_json, state_directory, update_history, write_json
from .tui import display, TerminalDashboard

console = Console()


def adapter(url: str, config: dict):
    platform, _, canonical = contest_url(url)
    if platform == "qoj":
        from .qoj import QOJ
        return QOJ(canonical, config.get("qoj", {}))
    if platform in ("cwnuoj", "nowcoder"):
        raise VPError("These contests use live " + canonical)
    from .gym import Gym
    return Gym(canonical, config.get(platform, config.get("gym", {})))


def load_account(platform: str, *, live: bool = False) -> dict:
    path = config_directory() / "config.json"
    with file_lock(path.with_suffix(".lock")):
        return _load_account(path, platform, live=live)


def _load_account(path: Path, platform: str, *, live: bool = False) -> dict:
    config = read_json(path)
    key = "gym" if platform == "cf" and "cf" not in config else platform
    account = config.setdefault(key, {})
    account.pop("_login_verified", None)
    before = dict(account)
    verified = refresh_browser_account(platform, account) or verify_stored_session(platform, account)
    if account != before:
        write_json(path, config)
    if not verified and account.get("cookie_source") != "manual":
        site = {"qoj": "QOJ", "cwnuoj": "CWNUOJ", "nowcoder": "Nowcoder"}.get(platform, "Codeforces")
        alias = {"cwnuoj": "paa", "nowcoder": "nk"}.get(platform, platform)
        detail = "\n".join(login_diagnostics(platform, account))
        raise VPError(f"无法验证 {site} 的 Firefox 登录。\n{detail}\n"
                      f"可运行 xcpc doctor {alias} 查看检查结果。")
    required = ("handle", "cookie") if platform in ("qoj", "cwnuoj", "nowcoder") or live else ("handle", "cookie", "api_key", "api_secret")
    missing = [key for key in required if not account.get(key)]
    if missing and not sys.stdin.isatty():
        raise VPError(f"Missing {platform} account fields {', '.join(missing)} in {path}")
    if missing:
        console.print(f"First use: configure the existing {platform} login session.", markup=False)
        labels = {"handle": "Account handle", "cookie": "Browser login Cookie (copy the Cookie value only)", "api_key": "Codeforces API key", "api_secret": "Codeforces API secret"}
        for key in missing:
            value = input(labels[key] + ": ").strip() if key == "handle" else getpass.getpass(labels[key] + ": ").strip()
            if not value:
                raise VPError("An account field was empty; no native VP was started.")
            account[key] = value
        write_json(path, config)
    account["_login_verified"] = verified
    return config


def history() -> None:
    rows = enrich_history(read_history(desktop() / "vp_history.csv"), state_directory())
    table = Table(box=box.SIMPLE, header_style="bold")
    columns = ("start_time", "contest", "solved", "penalty", "rank", "prize", "gold_line", "silver_line", "bronze_line")
    for key in columns:
        table.add_column(key.removesuffix("_line"))
    for row in sorted(rows, key=lambda r: r["start_time"], reverse=True):
        values = []
        for key in columns:
            value = row.get(key) or "—"
            if key.endswith("_line"):
                value = value.replace(" (", "\n(")
            values.append(value)
        table.add_row(*values)
    console.print(table)
    if rows:
        console.print("Medal lines: solved/penalty, then rank limit. Original official final standings; — = unavailable.", markup=False)


def configure_submit(api, contest: Contest, directory: Path) -> None:
    script = api.account.get("submit_script") or shutil.which("submit")
    selected = state_directory() / "submit"
    bundled = bool(script and Path(script).is_symlink() and Path(os.readlink(script)) == selected)
    if not script or not Path(script).is_file() or not os.access(script, os.X_OK):
        raise VPError("The existing submit script is not installed; configure its path in ~/.config/vp/config.json.")
    if contest.platform == "qoj":
        ids = [int(value) for value in contest.problems.values()]
        if not bundled and ids != list(range(ids[0], ids[0] + len(ids))):
            raise VPError("This QOJ contest has nonconsecutive problem IDs. Use the installed submit script with problem_ids support.")
        write_json(directory / "contest.json", {**api.submit_configuration(contest), "platform": "qoj",
                                               **browser_fields(api.account)})
    elif bundled:
        write_json(directory / "contest.json", {"name": contest.name, "platform": "gym",
                   "contest_id": int(contest.contest_id), "handle": api.handle,
                   "cookie": api.account["cookie"],
                   "user_agent": api.web.session.headers["User-Agent"], **browser_fields(api.account)})
    else:
        # ehnryx/cf_submit reads a plain contestid beside cf.py, not JSON.
        location = Path(api.account.get("cf_submit_directory") or Path(script).resolve().parent)
        if (location / "cf.py").is_file() or (location / "cf_login.py").is_file():
            (location / "contestid").write_text(contest.contest_id, encoding="utf8")
        elif api.account.get("submit_config_format") == "contest.json":
            write_json(directory / "contest.json", {"name": contest.name, "platform": "gym",
                        "base_url": contest.url, "contest_id": int(contest.contest_id),
                        "problems": "".join(contest.problems), "cookie": api.account["cookie"],
                        "user_agent": api.web.session.headers["User-Agent"], **browser_fields(api.account)})
        else:
            raise VPError("The Gym submit script's configuration format must be connected before a native VP is started.")
    if bundled:
        tool = Path(sys.prefix) / "bin" / "vp-submit"
        if not tool.is_file() or not os.access(tool, os.X_OK):
            raise VPError(f"The bundled {contest.platform} submit script is missing; reinstall vp.")
        temporary = selected.with_name("submit.new")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(tool)
        os.replace(temporary, selected)


def bind_submit_context(directory: Path, state_path: Path, state: dict) -> None:
    path = directory / "contest.json"
    if not path.is_file():
        return
    config = read_json(path)
    config.setdefault("handle", state["handle"])
    config["vp_context"] = {"state_path": str(state_path), "start_time": state["start_time"],
                            "url": state["url"], "handle": state["handle"]}
    write_json(path, config)


def tmux_exists(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode == 0


def attach(name: str) -> None:
    command = ["tmux", "switch-client" if os.environ.get("TMUX") else "attach-session", "-t", name]
    subprocess.run(command, check=True)


def launch(state_path: Path, state: dict) -> None:
    name = state["tmux_session"]
    directory = state["directory"]
    monitor = shlex.join([sys.executable, "-m", "vp_helper.cli", "_monitor", str(state_path)])
    # A percentage split (-p) cannot be resolved on a session that has no client
    # yet: tmux aborts with "size missing".  Give the session the real terminal
    # geometry and split by an absolute number of lines instead.
    columns, rows = shutil.get_terminal_size(fallback=(80, 24))
    shell_rows = max(4, rows * 3 // 10)
    subprocess.run(["tmux", "new-session", "-d", "-s", name,
                    "-x", str(columns), "-y", str(rows), "-c", directory, monitor], check=True)
    try:
        subprocess.run(["tmux", "set-option", "-t", name, "remain-on-exit", "on"], check=True)
        subprocess.run(["tmux", "set-option", "-t", name, "mouse", "on"], check=True)
        shell = os.environ.get("SHELL", "/bin/bash")
        subprocess.run(["tmux", "split-window", "-v", "-l", str(shell_rows), "-t", name,
                        "-c", directory, "exec " + shlex.quote(shell)], check=True)
        subprocess.run(["tmux", "select-pane", "-t", name + ":0.1"], check=True)
    except subprocess.CalledProcessError:
        # Preserve the monitor/session if only the shell pane failed to start.
        raise VPError(f"VP monitoring started in tmux session {name}; the shell pane failed to start.") from None
    attach(name)


def check_terminal() -> None:
    if sys.platform != "linux":
        raise VPError("vp is supported on Ubuntu only.")
    if not sys.stdin.isatty():
        raise VPError("Run vp in an interactive Ubuntu terminal.")
    if shutil.which("tmux") is None:
        raise VPError("tmux is missing; run the installer.")


def prepare_resume(url: str) -> tuple[Path, dict]:
    check_terminal()
    platform, _, canonical = contest_url(url)
    if platform in ("cwnuoj", "nowcoder"):
        raise VPError("These contests use live " + canonical)
    state_root = state_directory()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(state_root / "start.lock"):
        current = read_json(state_root / "current.json")
        if current:
            old = read_json(Path(current["state_path"]))
            if old.get("url") == canonical and old.get("mode") == "live":
                raise VPError("This is a live contest. Resume it with live " + canonical)
            # The saved participation outlives tmux. Let start() recreate a
            # missing session without requesting another native VP.
            if old.get("url") == canonical:
                # A resumed session still needs a usable local submission file;
                # refreshing it never starts or resets the native participation.
                config = load_account(platform)
                api = adapter(canonical, config)
                if api.handle != old.get("handle"):
                    raise VPError("The configured account changed; restore this VP's account before resuming it.")
                directory = Path(old["directory"])
                directory.mkdir(parents=True, exist_ok=True)
                configure_submit(api, Contest.from_dict(old["contest"]), directory)
                bind_submit_context(directory, Path(current["state_path"]), old)
                return Path(current["state_path"]), old
            if old.get("status") != "complete" and time.time() < old.get("start_time", 0) + old.get("contest", {}).get("rules", {}).get("duration", 0):
                raise VPError(f"A native VP is already running: {old.get('url')}. Open that URL to resume it.")
        config = load_account(platform)
        api = adapter(canonical, config)
        console.print("Reading contest information…", markup=False)
        contest = api.prepare()
        root = desktop()
        root.mkdir(parents=True, exist_ok=True)
        contest.directory_name = " ".join(contest.directory_name.split())
        directory = root / contest.directory_name
        directory.mkdir(exist_ok=True)
        configure_submit(api, contest, directory)
        for name, pdf in contest.pdfs.items():
            console.print("Downloading " + name, markup=False)
            try:
                api.web.pdf(pdf, directory / name)
            except (VPError, OSError) as exc:
                console.print(f"Statement download skipped: {exc}. Use the original contest page.", markup=False)
        console.print("Starting and verifying native virtual participation…", markup=False)
        started = api.start(contest)
        name = "vp-" + platform + "-" + contest.contest_id + "-" + str(int(started))
        path = state_root / (name + ".json")
        saved = read_json(path)
        state = {"contest": contest.to_dict(), "url": canonical, "start_time": started,
                 "handle": api.handle,
                 "directory": str(directory), "tmux_session": name, "status": "running",
                 "submissions": saved.get("submissions", []), "recent": saved.get("recent", []),
                 "history_path": str(root / "vp_history.csv")}
        write_json(path, state)
        bind_submit_context(directory, path, state)
        write_json(state_root / "current.json", {"state_path": str(path)})
        console.print("Prepared " + str(directory), markup=False)
        return path, state


def prepare_start(url: str, scheduled_start: float | None = None) -> tuple[Path, dict]:
    from .vp import prepare
    return prepare(url, scheduled_start)


def start(url: str, *, live: bool = False, practice: bool = False, resume: bool = False, scheduled_start: float | None = None) -> None:
    # Release the cross-shell preparation lock before entering an attached TUI.
    if practice:
        from .practice import prepare
        path, state = prepare(url)
    elif live:
        from .live import prepare_live
        path, state = prepare_live(url)
    else:
        path, state = prepare_resume(url) if resume else prepare_start(url, scheduled_start)
    open_state(path, state)


def open_state(path: Path, state: dict) -> None:
    bind_submit_context(Path(state["directory"]), path, state)
    if tmux_exists(state["tmux_session"]):
        pane = state["tmux_session"] + ":0.0"
        dead = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_dead}"],
                              check=True, capture_output=True, text=True).stdout.strip()
        if dead == "1":
            command = shlex.join([sys.executable, "-m", "vp_helper.cli", "_monitor", str(path)])
            subprocess.run(["tmux", "respawn-pane", "-t", pane, "-c", state["directory"], command], check=True)
        subprocess.run(["tmux", "set-option", "-t", state["tmux_session"], "mouse", "on"], check=True)
        attach(state["tmux_session"])
    else:
        launch(path, state)


def poll(api, contest: Contest, elapsed: float, refresh_reference: bool = False):
    """Keep slow requests outside the terminal's input and redraw loop."""
    try:
        result = api.reference(contest, elapsed) if refresh_reference else api.submissions(contest)
        message = getattr(api, "submission_message", "") if not refresh_reference else ""
        return result, message if isinstance(message, str) else ""
    except VPError as exc:
        return None, str(exc)


def monitor(path: Path) -> None:
    # tmux's default "screen" terminfo only exposes eight colors. Its terminal
    # supports the 256-color palette needed for orange and purple practice rows.
    if os.environ.get("TMUX") and os.environ.get("TERM") in ("screen", "tmux"):
        os.environ["TERM"] = "screen-256color"
    if read_json(path).get("mode") == "live":
        from .live import monitor_live
        monitor_live(path)
        return
    if read_json(path).get("mode") == "practice":
        from .practice import monitor as practice_monitor
        practice_monitor(path)
        return
    # Scheduled QOJ and uncertain registrations wait in the persistent monitor.
    waiting = read_json(path)
    if waiting.get("status") == "scheduled":
        from .vp import activate
        error = ""
        retry_at = 0.0
        with TerminalDashboard() as view:
            while waiting.get("status") == "scheduled":
                if not view.input():
                    return
                from .sessions import boundary
                cutoff = boundary(path)
                if cutoff is not None and time.time() >= cutoff:
                    waiting["status"] = "cancelled"
                    write_json(path, waiting)
                    return
                remaining = waiting["start_time"] - time.time()
                if remaining <= 0 and time.monotonic() >= retry_at:
                    try:
                        waiting = activate(path, waiting)
                    except StartRejected as exc:
                        console.print("VP 已取消：" + str(exc), markup=False)
                        return
                    except (VPError, OSError) as exc:
                        error = str(exc)
                        retry_at = time.monotonic() + 15
                    continue
                from .scoring import score_submissions
                c = Contest.from_dict(waiting["contest"])
                view.update(display(c, -max(0, remaining), score_submissions([], list(c.problems), c.rules, 0),
                                    "—", "", None, [], error or "Waiting for scheduled start"))
                time.sleep(0.2)
        if waiting.get("status") in ("cancelled", "failed"):
            return
    if read_json(path).get("status") in ("cancelled", "failed"):
        raise VPError("此 VP 未启动。请查看历史记录中的失败原因。")
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        state = read_json(path)
        contest = Contest.from_dict(state["contest"])
        config = read_json(config_directory() / "config.json")
        api = adapter(contest.url, config)
        if state.get("handle") and api.handle != state["handle"]:
            raise VPError("The configured account changed; restore this VP's account before resuming it.")
        api.start_time = state["start_time"]
        reference_api = adapter(contest.url, config)
        reference_api.start_time = state["start_time"]
        from .sessions import boundary, merge_submissions, save_state
        submissions = {x["id"]: Submission(**x) for x in state.get("submissions", [])}
        final_score_key = (score_submissions(list(submissions.values()), list(contest.problems),
                                           contest.rules, contest.rules.duration).rank_key
                           if state.get("status") == "complete" else None)
        if hasattr(api, "seed_submissions"):
            api.seed_submissions(list(submissions.values()))
        receipts = ReceiptReader(path, state)
        reference = Reference()
        last_poll = 0.0
        next_poll = next_reference = 0.0
        backoff = 5.0
        submission_error = reference_error = ""
        have_polled = False
        future = reference_future = None
        next_render = 0.0
        with ThreadPoolExecutor(max_workers=2) as network, TerminalDashboard() as view:
            while view.input():
                elapsed = time.time() - state["start_time"]
                cutoff = boundary(path)
                interrupted = cutoff is not None and cutoff < state["start_time"] + contest.rules.duration
                scoring_elapsed = min(elapsed, cutoff-state["start_time"]) if interrupted else elapsed
                now = time.monotonic()
                fresh_submissions = False
                for receipt in receipts.read():
                    if receipt.id not in submissions:
                        submissions[receipt.id] = receipt
                        fresh_submissions = True
                        next_poll = 0.0
                if future is not None and future.done():
                    incoming, error = future.result()
                    future = None
                    if incoming is not None:
                        fresh_submissions = True
                        have_polled = True
                        state["submissions"] = [asdict(s) for s in submissions.values()]
                        submissions = {s.id: s for s in merge_submissions(path, state, incoming)}
                        last_poll = now
                    submission_error = error
                    pending = any(s.verdict == "PENDING" for s in submissions.values())
                    backoff = min(60.0, max(5.0, backoff * 2)) if error and incoming is None else 2.0 if pending else 5.0
                    next_poll = now + backoff
                if reference_future is not None and reference_future.done():
                    refreshed, reference_error = reference_future.result()
                    reference_future = None
                    if refreshed is not None:
                        reference = refreshed
                    next_reference = now + 30
                if future is None and now >= next_poll:
                    future = network.submit(poll, api, contest, elapsed)
                if reference_future is None and now >= next_reference:
                    reference_future = network.submit(poll, reference_api, contest, scoring_elapsed, True)
                current_reference = (reference.replay.at(contest, scoring_elapsed, released=reference.released)
                                     if reference.replay is not None else reference)
                frozen = contest.rules.frozen(scoring_elapsed, current_reference.released)
                # Rebuild from canonical submissions, including when resuming old
                # state or offline. Discovery order is not submission chronology.
                recent = recent_submissions(list(submissions.values()), list(contest.problems), contest.rules, elapsed)
                records = submission_history(list(submissions.values()), list(contest.problems), contest.rules, elapsed)
                private = score_submissions(list(submissions.values()), list(contest.problems), contest.rules,
                                            scoring_elapsed, name=api.handle)
                public_elapsed = (math.nextafter(float(contest.rules.freeze_at), -math.inf)
                                  if contest.rules.problem_reveal_count and frozen else scoring_elapsed)
                public = score_submissions(list(submissions.values()), list(contest.problems), contest.rules,
                                           public_elapsed, public=True, released=current_reference.released, name=api.handle)
                all_final = all(s.verdict not in ("PENDING", "UNAVAILABLE") for s in submissions.values() if not s.practice and 0 <= s.time < contest.rules.duration)
                finalized = (not interrupted and elapsed >= contest.rules.duration and not frozen and current_reference.complete
                             and api.submissions_complete and have_polled and all_final)
                rank = "?" if frozen else str(rank_of(private, current_reference.rows)) if current_reference.complete and private.results_complete else "—"
                shown_rank = rank_label(private, current_reference.rows) if current_reference.complete and private.results_complete and not frozen else rank
                prize = prize_of(private, current_reference.rows, contest.medals) if finalized else ""
                stats = statistics(current_reference.rows + [public], list(contest.problems)) if current_reference.statistics_complete and public.results_complete else None
                shown_message = submission_error or reference_error or current_reference.message
                if finalized and current_reference.replay is not None and not submission_error and not reference_error:
                    shown_message = "Finished"
                elif not have_polled and not shown_message:
                    shown_message = "Connecting…"
                if finalized and not shown_message:
                    shown_message = "Finished"
                elif frozen and not shown_message:
                    shown_message = "Scoreboard frozen"
                if last_poll and now - last_poll > 15:
                    shown_message = "Results stale · " + shown_message
                if boundary(path) is not None and time.time() >= boundary(path):
                    shown_message = "Archived VP · new submissions from this directory are practice"
                if now >= next_render:
                    dashboard = display(contest, max(0, scoring_elapsed) if interrupted else elapsed,
                                        private, shown_rank, prize, stats, records, shown_message)
                    dashboard.revealed = revealed_problems(contest, scoring_elapsed, current_reference)
                    view.update(dashboard)
                    next_render = now + 0.25
                if fresh_submissions:
                    preserve_final = (state.get("status") == "complete" and all_final
                                      and private.rank_key == final_score_key)
                    if finalized:
                        final_score_key = private.rank_key
                        state["medal_lines"] = medal_lines(current_reference.rows, contest.medals)
                    state.update(contest=contest.to_dict(), submissions=[asdict(x) for x in submissions.values()],
                                 recent=recent, submission_history=records, last_sync_time=time.time(),
                                 status="archived" if interrupted and elapsed >= cutoff-state["start_time"] else "complete" if finalized or preserve_final else "running")
                    state["summary"] = {"solved": private.solved,
                                        "penalty": private.penalty if private.results_complete else "",
                                        "rank": rank if finalized else "", "prize": prize}
                    save_state(path, state)
                    row = {"start_time": datetime.fromtimestamp(state["start_time"], timezone.utc).isoformat(),
                           "contest": contest.name, "url": contest.url, "solved": private.solved,
                           "penalty": private.penalty if private.results_complete else "", "rank": rank if finalized else "", "prize": prize}
                    if finalized or not preserve_final:
                        update_history(Path(state["history_path"]), row)
                time.sleep(0.1)


def main() -> None:
    args = sys.argv[1:]
    try:
        if args and args[0] == "history":
            from .attempt_history import history as attempts_history
            attempts_history(args[1:])
        elif len(args) == 2 and args[0] == "_monitor":
            monitor(Path(args[1]))
        elif len(args) == 2 and args[0] in ("cf", "qoj", "gym"):
            from .scheduling import prompt_start
            start(contest_id_url(*args), scheduled_start=prompt_start())
        elif len(args) == 1 and args[0] not in ("-h", "--help"):
            platform, _, url = contest_url(args[0])
            if platform not in ("qoj", "cf", "gym"):
                raise VPError("Use live CONTEST_URL for CWNUOJ or Nowcoder.")
            from .scheduling import prompt_start
            start(url, scheduled_start=prompt_start())
        elif args in ([], ["-h"], ["--help"]):
            console.print("查看全部命令和设置：xcpc --help", markup=False)
        else:
            raise VPError("Usage: vp cf ID, vp qoj ID, vp gym ID, vp CONTEST_URL or vp history [ATTEMPT_ID]")
    except (VPError, OSError, subprocess.CalledProcessError) as exc:
        console.print("vp: " + str(exc), style="red", markup=False)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
