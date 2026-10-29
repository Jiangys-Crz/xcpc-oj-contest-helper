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

from .platform_clock import platform_now
from .models import Contest, Reference, Submission, VPError, StartRejected
from .accounts import account_key, api_credentials
from .browser_login import browser_fields, refresh_browser_account, login_diagnostics, verify_stored_session
from .history_lines import enrich_history
from .parsing import contest_url, contest_id_url
from .receipts import ReceiptReader
from .scoring import medal_lines, rank_of, rank_label, prize_of, recent_submissions, revealed_problems, score_submissions, statistics, submission_history
from .storage import config_directory, workspace_directory as desktop, file_lock, read_history, read_json, state_directory, update_history, write_json
from .tui import display, TerminalDashboard

console = Console()
SHELL_HEIGHT_PERCENT = 35


def adapter(url: str, config: dict):
    platform, _, canonical = contest_url(url)
    if platform == "qoj":
        from .qoj import QOJ
        return QOJ(canonical, config.get("qoj", {}))
    if platform in ("cwnuoj", "nowcoder"):
        raise VPError("These contests use live " + canonical)
    from .gym import Gym
    return Gym(canonical, config.get(account_key(config, platform), {}))


def load_account(platform: str, *, live: bool = False) -> dict:
    path = config_directory() / "config.json"
    with file_lock(path.with_suffix(".lock")):
        return _load_account(path, platform, live=live)


def _load_account(path: Path, platform: str, *, live: bool = False) -> dict:
    config = read_json(path)
    key = account_key(config, platform)
    account = config.setdefault(key, {})
    account.pop("_login_verified", None)
    before = dict(account)
    verified = refresh_browser_account(platform, account) or verify_stored_session(platform, account)
    if platform in ("cf", "gym"):
        account.update(api_credentials(account, config, platform))
    if account != before:
        write_json(path, config)
    if not verified and account.get("cookie_source") != "manual":
        site = {"qoj": "QOJ", "cwnuoj": "CWNUOJ", "nowcoder": "Nowcoder"}.get(platform, "Codeforces")
        alias = {"cwnuoj": "paa", "nowcoder": "nk"}.get(platform, platform)
        recovered = {}
        detail = "\n".join(login_diagnostics(platform, account, verified_account=recovered))
        if recovered:
            account.update(recovered)
            verified = True
            write_json(path, config)
        else:
            raise VPError(f"无法验证 {site} 的 Firefox 登录。\n{detail}\n"
                          f"可运行 xcpc doctor {alias} 查看检查结果。")
    required = ("handle", "cookie", "api_key", "api_secret") if platform in ("cf", "gym") else ("handle", "cookie")
    missing = [key for key in required if not account.get(key)]
    if platform in ("cf", "gym") and any(key in missing for key in ("api_key", "api_secret")):
        missing = [key for key in missing if key not in ("api_key", "api_secret")] + ["api_key", "api_secret"]
    if missing and not sys.stdin.isatty():
        raise VPError(f"Missing {platform} account fields {', '.join(missing)} in {path}")
    if missing:
        console.print(f"First use: configure the existing {platform} login session.", markup=False)
        labels = {"handle": "Account handle", "cookie": "Browser login Cookie (copy the Cookie value only)", "api_key": "Codeforces API key", "api_secret": "Codeforces API secret"}
        for key in missing:
            if key == 'api_secret' and account.get('api_secret'):
                continue
            if key == 'api_key':
                from .accounts import prompt_api_credentials
                account.update(prompt_api_credentials())
                continue
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


def configure_submit(api, contest: Contest, directory: Path, *, validate_only: bool = False) -> None:
    script = api.account.get("submit_script") or shutil.which("submit")
    selected = state_directory() / "submit"
    bundled = bool(script and Path(script).is_symlink() and Path(os.readlink(script)) == selected)
    if not script or not Path(script).is_file() or not os.access(script, os.X_OK):
        raise VPError("The existing submit script is not installed; configure its path in ~/.config/vp/config.json.")
    if contest.platform == "qoj":
        ids = [int(value) for value in contest.problems.values()]
        if not bundled and ids != list(range(ids[0], ids[0] + len(ids))):
            raise VPError("This QOJ contest has nonconsecutive problem IDs. Use the installed submit script with problem_ids support.")
        if not validate_only:
            write_json(directory / "contest.json", {**api.submit_configuration(contest), "platform": "qoj",
                                                   **browser_fields(api.account)})
    elif bundled:
        if not validate_only:
            write_json(directory / "contest.json", {"name": contest.name, "platform": "gym",
                   "contest_id": int(contest.contest_id), "handle": api.handle,
                   "cookie": api.account["cookie"],
                   "user_agent": api.web.session.headers["User-Agent"], **browser_fields(api.account)})
    else:
        # ehnryx/cf_submit reads a plain contestid beside cf.py, not JSON.
        location = Path(api.account.get("cf_submit_directory") or Path(script).resolve().parent)
        if (location / "cf.py").is_file() or (location / "cf_login.py").is_file():
            if not validate_only:
                (location / "contestid").write_text(contest.contest_id, encoding="utf8")
        elif api.account.get("submit_config_format") == "contest.json":
            if not validate_only:
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
        if validate_only:
            return
        temporary = selected.with_name("submit.new")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(tool)
        os.replace(temporary, selected)


def _bind_submit_context(directory: Path, state_path: Path, state: dict, *, force: bool = False) -> None:
    saved = read_json(state_path)
    if saved.get("workspace_managed"):
        directory = Path(saved["directory"])
    path = directory / "contest.json"
    if not path.is_file():
        return
    config = read_json(path)
    owner = config.get("vp_context", {}).get("state_path")
    if owner and owner != str(state_path) and not force:
        return
    config.setdefault("handle", state["handle"])
    config["vp_context"] = {"state_path": str(state_path), "start_time": state["start_time"],
                            "url": state["url"], "handle": state["handle"]}
    write_json(path, config)


def bind_submit_context(directory: Path, state_path: Path, state: dict) -> None:
    with file_lock(state_path.parent / "workspaces.lock"):
        _bind_submit_context(directory, state_path, state)


def configure_contest(api, contest: Contest, directory: Path, path: Path, state: dict,
                      *, live: bool = False, overrides: dict | None = None) -> None:
    # Switching the shared folder's current round is atomic with respect to
    # old monitors, which may still be displaying historical records.
    with file_lock(path.parent / "workspaces.lock"):
        if live:
            from .live import configure_submission
            configure_submission(api, contest, directory)
        else:
            configure_submit(api, contest, directory)
        if overrides:
            config = read_json(directory / "contest.json")
            config.update(overrides)
            write_json(directory / "contest.json", config)
        _bind_submit_context(directory, path, state, force=True)


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
    shell_rows = max(4, rows * SHELL_HEIGHT_PERCENT // 100)
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


def restore_contest(path: Path, state: dict, config: dict) -> tuple[Path, dict]:
    from .live import live_adapter
    from .workspaces import refresh_name, rename_existing
    contest = Contest.from_dict(state["contest"])
    is_live = state.get("mode") == "live"
    overrides = None
    if state.get('mode') == 'practice':
        from .practice import Practice
        client = Practice(state['url'], config)
        api = client.api if contest.platform == 'qoj' else client
        refresh_name(client.api, contest)
        is_live = contest.platform != 'qoj'
        overrides = {'mode':'practice','base_url':state['url'],'platform':contest.platform,
                     'problem_ids':contest.problems,
                     'problems':''.join(contest.problems) if contest.platform == 'qoj' else list(contest.problems)}
    else:
        api = live_adapter(state["url"], config) if is_live else adapter(state["url"], config)
        refresh_name(api, contest)
    rename_existing(path, state, contest)
    directory = Path(state["directory"])
    configure_contest(api, contest, directory, path, state, live=is_live, overrides=overrides)
    write_json(path.parent / "current.json", {"state_path": str(path)})
    return path, state


def reopen_saved(path: Path, state: dict, config: dict) -> None:
    from .live import account_for
    from .sessions import boundary
    from . import practice
    contest = Contest.from_dict(state['contest'])
    if account_for(config, contest.platform).get('handle', '').casefold() != state['handle'].casefold():
        raise VPError('请在 Firefox 登录此比赛原来的账号后再恢复面板。')
    if state.get('mode') == 'practice':
        path, state = practice.prepare(state['url'])
    else:
        cutoff = boundary(path)
        ended = state.get('status') != 'scheduled' and (state.get('status') in ('complete', 'archived')
                 or cutoff is not None and platform_now(state) >= cutoff
                 or platform_now(state) >= state['start_time'] + contest.rules.duration)
        with file_lock(state_directory() / 'start.lock'):
            if ended:
                restored = practice._resume_vp(state_directory(), state['url'], state['handle'], config)
                if restored is None:
                    raise VPError('比赛记录发生变化，请重新运行 xcpc dashboard。')
                path, state = restored
            else:
                path, state = restore_contest(path, read_json(path), config)
    state['_reload_monitor'] = True
    open_state(path, state)


def dashboard() -> None:
    """Restore the current contest folder; never start or register a contest."""
    from .sessions import attempts, latest_session
    check_terminal()
    here = Path.cwd().resolve()
    bound = [(p, s) for p, s in attempts() if s.get('directory')
             and here.is_relative_to(Path(s['directory']).resolve())]
    if not bound:
        raise VPError('当前目录没有比赛记录。请进入原比赛文件夹或其子目录，再运行 xcpc dashboard。')
    _, owner = max(bound, key=lambda item: len(Path(item[1]['directory']).parts))
    previous = latest_session(owner['url'], owner['handle'], include_practice=True)
    if previous is None:
        raise VPError('此比赛尚未成功开始；请先运行原比赛命令检查状态。')
    platform = previous[1]['contest']['platform']
    config = load_account(platform, live=True)
    reopen_saved(previous[0], previous[1], config)


def start(url: str, *, live: bool = False, practice: bool = False, resume: bool = False,
          restart: bool = False, scheduled_start: float | None = None) -> None:
    # Release the cross-shell preparation lock before entering an attached TUI.
    if restart:
        raise VPError('已取消重复 VP；每场比赛只支持一次 VP，再次运行原命令会恢复已有会话。')
    if not practice and not resume:
        from .sessions import latest_session
        check_terminal()
        platform, _, url = contest_url(url)
        config = load_account(platform, live=True)
        from .live import account_for
        previous = latest_session(url, account_for(config, platform).get("handle", ""))
        if previous:
            reopen_saved(previous[0], previous[1], config)
            return
    if practice:
        from .practice import prepare
        path, state = prepare(url)
    elif live:
        from .live import prepare_live
        path, state = prepare_live(url)
    else:
        platform, _, _ = contest_url(url)
        if not resume and platform not in ("cf", "gym", "qoj"):
            raise VPError("没有可恢复的 VP/live 记录。请先运行 live CONTEST_URL 接入原站比赛。")
        if not resume and scheduled_start is None:
            from .scheduling import prompt_start
            scheduled_start = prompt_start(url)
        path, state = prepare_resume(url) if resume else prepare_start(url, scheduled_start)
    open_state(path, state)


def open_state(path: Path, state: dict) -> None:
    if not Path(state['directory']).is_dir():
        config = load_account(state['contest']['platform'], live=True)
        path, state = restore_contest(path, read_json(path), config)
    if state.get("workspace_target") and state.get("status") == "running":
        from .workspaces import promote
        with file_lock(path.parent / "start.lock"):
            state = read_json(path)
            promote(path, state)
    bind_submit_context(Path(state["directory"]), path, state)
    if tmux_exists(state["tmux_session"]):
        pane = state["tmux_session"] + ":0.0"
        dead = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_dead}"],
                              check=True, capture_output=True, text=True).stdout.strip()
        refresh_practice = bool(state.get("_reload_monitor") or state.get("practice_view") or state.get("mode") == "practice")
        if dead == "1" or refresh_practice:
            command = shlex.join([sys.executable, "-m", "vp_helper.cli", "_monitor", str(path)])
            # Reload the score pane after an update or login refresh. Keep the
            # lower shell and the original VP directory untouched.
            subprocess.run(["tmux", "respawn-pane", *(["-k"] if dead != "1" else []),
                            "-t", pane, "-c", state["directory"], command], check=True)
        subprocess.run(["tmux", "set-option", "-t", state["tmux_session"], "mouse", "on"], check=True)
        # When called from the lower shell, its terminal height is only that
        # pane's height. Read the whole tmux window before restoring the split.
        height = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{window_height}"],
                                check=True, capture_output=True, text=True).stdout.strip()
        if height.isdecimal() and int(height) > 0:
            subprocess.run(["tmux", "resize-pane", "-t", state["tmux_session"] + ":0.1",
                            "-y", str(max(4, int(height) * SHELL_HEIGHT_PERCENT // 100))], check=True)
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
    # A renamed workspace keeps the same tmux cwd. Repair an old daemon's
    # stale directory field when it is relaunched after an application update.
    here = Path.cwd()
    context = read_json(here / "contest.json").get("vp_context", {})
    with file_lock(path.with_suffix(".save.lock")):
        saved = read_json(path)
        if (saved and context.get("url") == saved.get("url") and context.get("handle") == saved.get("handle")
                and str(here) != saved.get("directory")):
            saved.update(directory=str(here), workspace_managed=True)
            write_json(path, saved)
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
                if cutoff is not None and platform_now(waiting) >= cutoff:
                    waiting["status"] = "cancelled"
                    write_json(path, waiting)
                    return
                remaining = waiting["start_time"] - platform_now(waiting)
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
                c = Contest.from_dict(waiting["contest"])
                view.update(display(c, -max(0, remaining), score_submissions([], list(c.problems), c.rules, 0,
                                                                          name=waiting["handle"]),
                                    "—", "", None, [], error or "Waiting for scheduled start"))
                time.sleep(0.2)
        if waiting.get("status") in ("cancelled", "failed"):
            return
    if read_json(path).get("status") in ("cancelled", "failed"):
        raise VPError("此 VP 未启动。请查看历史记录中的失败原因。")
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        state = read_json(path)
        if state.get('practice_view') and 'practice_rank' not in state:
            from .practice import saved_rank
            from .sessions import save_state
            state['practice_rank'] = saved_rank(state)
            save_state(path, state)
        contest = Contest.from_dict(state["contest"])
        config = read_json(config_directory() / "config.json")
        api = adapter(contest.url, config)
        if state.get("handle") and api.handle.casefold() != state["handle"].casefold():
            raise VPError("The configured account changed; restore this VP's account before resuming it.")
        api.start_time = state["start_time"]
        if state.get('start_time_source') == 'codeforces_api':
            api.observed_start_time = state['start_time']
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
                elapsed = platform_now(state) - state["start_time"]
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
                        native_start = getattr(api, 'observed_start_time', None)
                        if (contest.platform in ('cf', 'gym') and isinstance(native_start, (int, float))
                                and native_start != state['start_time']):
                            from .sessions import reconcile_native_start
                            old_start = state['start_time']
                            state['submissions'] = [asdict(s) for s in submissions.values()]
                            save_state(path, state)
                            state = reconcile_native_start(path, state, native_start)
                            incoming = [Submission(**{**asdict(s), 'time': s.time + old_start - native_start})
                                        for s in incoming]
                            submissions = {s['id']: Submission(**s) for s in state['submissions']}
                            api.start_time = reference_api.start_time = state['start_time']
                            receipts = ReceiptReader(path, state)
                            bind_submit_context(Path(state['directory']), path, state)
                            elapsed = platform_now(state) - state['start_time']
                            scoring_elapsed = min(elapsed, cutoff-state['start_time']) if interrupted else elapsed
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
                if boundary(path) is not None and platform_now(state) >= boundary(path):
                    shown_message = "Archived VP · new submissions from this directory are practice"
                if now >= next_render:
                    state["practice_view"] = bool(state.get("practice_view") or read_json(path).get("practice_view"))
                    if state['practice_view']:
                        from .practice import saved_rank
                        state.setdefault('practice_rank', read_json(path).get('practice_rank', saved_rank(state)))
                        shown_rank = state['practice_rank']
                    elif shown_rank not in ('', '?', '—') and state.get('last_rank') != shown_rank:
                        state['last_rank'] = shown_rank
                        save_state(path, state)
                    dashboard = display(contest, max(0, scoring_elapsed) if interrupted else elapsed,
                                        private, shown_rank, prize, stats, records, shown_message)
                    dashboard.practice = state["practice_view"]
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
                                 recent=recent, submission_history=records, last_sync_time=platform_now(state),
                                 status="archived" if interrupted and elapsed >= cutoff-state["start_time"] else "complete" if finalized or preserve_final else "running")
                    state['sync_revision'] = state.get('sync_revision', 0) + 1
                    state["summary"] = {"solved": private.solved,
                                        "penalty": private.penalty if private.results_complete else "",
                                        "rank": state.get('practice_rank', '—') if state['practice_view'] else rank if finalized else "", "prize": prize}
                    save_state(path, state)
                    row = {"start_time": datetime.fromtimestamp(state["start_time"], timezone.utc).isoformat(),
                           "contest": contest.name, "url": contest.url, "solved": private.solved,
                           "penalty": private.penalty if private.results_complete else "", "rank": state.get('practice_rank', '—') if state['practice_view'] else rank if finalized else "", "prize": prize}
                    if finalized or not preserve_final:
                        aliases = [(datetime.fromtimestamp(at, timezone.utc).isoformat(), state['url'])
                                   for at in state.get('start_time_aliases', [])]
                        update_history(Path(state["history_path"]), row, previous_keys=tuple(aliases))
                time.sleep(0.1)


def main() -> None:
    args = sys.argv[1:]
    try:
        if '--restart' in args:
            raise VPError('已取消 --restart；每场比赛只支持一次 VP，重复原命令即可恢复。')
        if args and args[0] == "history":
            from .attempt_history import history as attempts_history
            attempts_history(args[1:])
        elif len(args) == 2 and args[0] == "_monitor":
            monitor(Path(args[1]))
        elif len(args) == 2 and args[0] in ("cf", "qoj", "gym"):
            start(contest_id_url(*args))
        elif len(args) == 1 and args[0] not in ("-h", "--help"):
            platform, _, url = contest_url(args[0])
            start(url)
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
