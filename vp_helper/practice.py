"""Standalone practice workspaces on the five supported judges."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .codeforces_live import CodeforcesLive, apply_metadata, submission_records, verify_login, verify_practice
from .platform_clock import platform_now
from .models import Contest, Rules, Submission, VPError
from .parsing import contest_url
from .scoring import score_practice_submissions
from .sessions import attempts, boundary, merge_records, save_state, latest_session, apply_submission_sync, reserve_submission_sync
from .storage import file_lock, read_json, write_json
from .tui import Dashboard, TerminalDashboard
from .workspaces import allocate, promote, refresh_name, rename_existing


class Practice:
    def __init__(self, url: str, config: dict):
        from . import cli
        from .live import live_adapter
        platform, self.id, self.url = contest_url(url)
        self.platform = platform
        if platform in ("cf", "gym"):
            from .live import account_for
            self.api = CodeforcesLive(self.url, account_for(config, platform), allow_gym=True)
        else:
            self.api = cli.adapter(self.url, config) if platform == "qoj" else live_adapter(self.url, config)
        self.account, self.handle, self.web = self.api.account, self.api.handle, self.api.web

    def prepare(self) -> Contest:
        c = Contest(self.platform, self.id, self.url, self.platform + " " + self.id,
                    self.platform + " " + self.id, {}, Rules(1))
        if self.platform in ("cf", "gym"):
            if not self.account.get("_login_verified"):
                verify_login(self.web.soup(self.url, params={"locale": "en"}), self.handle)
            data = self.api.api("contest.standings", contestId=self.id)
            apply_metadata(c, data["contest"], data["problems"])
            if c.extra["phase"] != "FINISHED":
                raise VPError("Use live or vp while the original contest is active; bt requires a finished contest.")
            verify_practice(self.api, c.rules.duration)
        elif self.platform in ("cwnuoj", "nowcoder"):
            if self.account.get("_login_verified") and self.account.get("uid") is not None:
                self.api._identified = True
            else:
                self.api.identity()
            self.api.apply_metadata(c, self.api.details(), require_registration=False)
            if c.extra["phase"] != "FINISHED":
                raise VPError("This contest has not finished. Use live for the existing participation.")
            if self.platform == "nowcoder":
                self.api.problem_list(c)
        else:
            page = self.api._page()
            if not self.account.get("_login_verified"):
                from .browser_login import identity_from_page
                identity_from_page("qoj", str(page), self.handle)
            c.name = self.api.contest_name(page)
            for row in page.select("tr"):
                link = row.select_one(f'a[href*="/contest/{self.id}/problem/"]')
                cells = row.select("td")
                label = cells[0].get_text(strip=True) if cells else ""
                if link and re.fullmatch(r"[A-Z][0-9]*", label):
                    c.problems[label] = urlsplit(link["href"]).path.rsplit("/", 1)[-1]
            if not c.problems:
                raise VPError("QOJ did not expose this contest's problems.")
            from .parsing import js_value
            ruleset = re.search(r'Ruleset:\s*(ICPC|IOI|OI)\b', page.get_text(' ', strip=True), re.I)
            scoring = (ruleset[1] if ruleset else js_value(str(page), 'contest_type', 'ICPC')).upper()
            if scoring not in ('ICPC', 'OI', 'IOI'):
                raise VPError('QOJ returned an unsupported contest ruleset.')
            c.extra.update(scoring=scoring, native_scoring=scoring)
            if scoring != 'ICPC':
                self.api.graded_maxima(str(page), c)
            c.extra["local_attempt"] = True
        c.extra.update(practice=True, mode="practice", scoring=c.extra.get("scoring", "ICPC"))
        # BT includes the contest's existing native submissions. Keep their
        # original epoch instead of treating this command invocation as a new
        # participation. QOJ may not expose a contest epoch; its adapter also
        # retains older absolute timestamps in a practice workspace.
        c.original_start = float(c.original_start if c.original_start is not None else int(platform_now(self.url)))
        self.handle = self.api.handle
        return c

    def seed_submissions(self, records) -> None:
        if hasattr(self.api, 'seed_submissions'):
            self.api.seed_submissions([Submission(**e) for e in records] if self.platform == 'qoj' else records)

    def seed_pending_submissions(self, records) -> None:
        from .pending_results import seed_pending
        seed_pending(self.api, self.platform, records)

    def submissions(self, contest: Contest, started: float) -> list[dict]:
        self.api.start_time = started
        values = self.api.submissions(contest)
        message = getattr(self.api, 'submission_message', '')
        self.submission_message = message if isinstance(message, str) else ''
        return [{**(asdict(s) if self.platform == "qoj" else s), "practice": True} for s in values]

    def statements(self, contest: Contest, directory: Path):
        if hasattr(self.api, "save_statements"):
            self.api.save_statements(contest, directory)


def saved_rank(state: dict) -> str:
    candidates = ([state.get('snapshot', {}).get('own', {}).get('rank')]
                  if isinstance(state.get('snapshot', {}).get('own'), dict) else [])
    candidates += [state.get('last_rank'), state.get('summary', {}).get('rank')]
    return next((str(value) for value in candidates if value is not None and str(value) not in ('', '?', '—')), '—')


def _resume_vp(root: Path, url: str, handle: str, config: dict) -> tuple[Path, dict] | None:
    from . import cli
    previous = latest_session(url, handle)
    if not previous:
        return None
    path, state = previous
    cutoff = boundary(path)
    ended = (state.get("status") in ("complete", "archived") or cutoff is not None and platform_now(state) >= cutoff
             or platform_now(state) >= state["start_time"] + state["contest"]["rules"]["duration"])
    if state.get("status") == "scheduled" or not ended:
        raise VPError("这场比赛尚未结束。VP 可先运行 xcpc end；live 请等待原站比赛结束后补题。")
    directory = Path(state["directory"])
    if state.get("mode") == "live":
        from .live import live_adapter
        native = live_adapter(url, config)
    else:
        native = cli.adapter(url, config)
    if native.handle.casefold() != state["handle"].casefold():
        raise VPError("请恢复此 VP 使用的原账号后再补题。")
    contest = Contest.from_dict(state['contest'])
    refresh_name(native, contest)
    rename_existing(path, state, contest)
    directory = Path(state["directory"])
    state["practice_view"] = True
    state.setdefault('practice_rank', saved_rank(state))
    save_state(path, state)
    cli.configure_contest(native, Contest.from_dict(state["contest"]), directory, path, state,
                          live=state.get("mode") == "live")
    write_json(root / "current.json", {"state_path": str(path)})
    cli.console.print("BT · 恢复原比赛目录：" + str(directory), markup=False)
    return path, state


def prepare(url: str) -> tuple[Path, dict]:
    from . import cli, live
    cli.check_terminal()
    platform, _, url = contest_url(url)
    config = cli.load_account(platform, live=True)
    api = Practice(url, config)
    root = cli.state_directory()
    with file_lock(root / "start.lock"):
        resumed = _resume_vp(root, url, api.handle, config)
        if resumed:
            return resumed
        contest = api.prepare()
        prior = [(p, s) for p, s in attempts(root) if s.get("mode") == "practice" and s["url"] == url
                 and s["handle"].casefold() == api.handle.casefold()]
        if prior:
            path, state = prior[-1]
            rename_existing(path, state, contest)
            directory = Path(state["directory"])
            target = directory
            if platform == 'qoj':
                contest.original_start = state['start_time']
            elif state['start_time'] != contest.original_start:
                from .sessions import _rebase_start
                _rebase_start(state, contest.original_start)
        else:
            attempt_id = uuid4().hex[:10]
            path = root / f"bt-{platform}-{contest.contest_id}-{attempt_id}.json"
            directory, target = allocate(cli.desktop(), contest, root, api.handle)
            state = {"mode": "practice", "attempt_id": attempt_id, "url": url,
                     "start_time": contest.original_start, "handle": api.handle, "submissions": [],
                     "directory": str(directory), "tmux_session": path.stem, "status": "running"}
            state["created_at"] = platform_now(url)
        directory.mkdir(parents=True, exist_ok=True)
        state["contest"] = contest.to_dict()
        if directory != target:
            state["workspace_target"] = str(target)
        write_json(path, state)
        cli.configure_contest(api.api if platform == "qoj" else api, contest, directory, path, state,
                              live=platform != "qoj", overrides={"mode":"practice", "base_url":url,
                              "platform":platform, "problem_ids":contest.problems,
                              "problems":"".join(contest.problems) if platform == "qoj" else list(contest.problems)})
        promote(path, state)
        directory = Path(state["directory"])
        cli.bind_submit_context(directory, path, state)
        write_json(root / "current.json", {"state_path": str(path)})
        cli.console.print("BT · " + str(directory), markup=False)
        return path, state


class PracticeDashboard(Dashboard):
    def __init__(self, contest, elapsed, records, message="", handle="", *, start_time=None):
        # This summary is only a view of the practice workspace. Original
        # records remain practice and never enter a VP's saved score.
        events = [Submission(str(s["id"]), "me", s["problem"], s["time"], s["verdict"]) for s in records]
        me = score_practice_submissions(events, list(contest.problems), elapsed, name=handle or "me")
        super().__init__(contest, elapsed, me, "—", "", None,
                         [{**s, "practice": True} for s in records], message, practice=True, start_time=start_time)


def monitor(path: Path) -> None:
    from . import cli
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        if sys.platform == 'win32':
            from .windows_terminal import track_monitor
            track_monitor(path)
        state = read_json(path)
        contest = Contest.from_dict(state["contest"])
        config = read_json(cli.config_directory() / "config.json")
        api = Practice(state["url"], config)
        if api.handle.casefold() != state["handle"].casefold():
            raise VPError("Restore this practice workspace's account before reopening it.")
        future = None
        next_poll = 0.0
        message = ""
        from .windows_terminal import view_options
        with ThreadPoolExecutor(max_workers=2) as pool, TerminalDashboard(**view_options(path)) as view:
            while view.input():
                if future is not None and future.done():
                    try:
                        apply_submission_sync(path, state, future.result(), poll_revision)
                        message = getattr(api, 'submission_message', '')
                        if not isinstance(message, str):
                            message = ''
                    except (VPError, OSError) as exc:
                        message = str(exc)
                    future = None
                    pending = any(s['verdict'] == 'PENDING' for s in state.get('submissions', []))
                    next_poll = time.monotonic() + (2.1 if pending else 5)
                if future is None and time.monotonic() >= next_poll:
                    poll_revision = reserve_submission_sync(path)
                    from .pending_results import seed_pending
                    seed_pending(api, contest.platform, state.get('submissions', []))
                    future = pool.submit(api.submissions, contest, state["start_time"])
                records = submission_records(merge_records(path, state, []), "FINISHED")
                view.update(PracticeDashboard(contest, platform_now(state)-state["start_time"], records, message,
                                             state.get("cloud", {}).get("label", state["handle"]), start_time=state['start_time']))
                time.sleep(0.2)


def main():
    if sys.argv[1:] in ([], ["-h"], ["--help"]):
        print("查看全部命令和设置：xcpc --help")
        return
    parser = argparse.ArgumentParser(description="Practice a Codeforces, Gym, QOJ, CWNUOJ or Nowcoder contest")
    parser.add_argument("url", metavar="CONTEST_URL")
    args = parser.parse_args()
    from . import cli
    try:
        cli.start(args.url, practice=True)
    except (VPError, OSError, cli.subprocess.CalledProcessError) as exc:
        cli.console.print("bt: " + str(exc), style="red", markup=False)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
