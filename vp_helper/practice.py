"""Standalone practice workspaces on the five supported judges."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .codeforces_live import CodeforcesLive, apply_metadata, submission_records, verify_login, verify_practice
from .models import Contest, Rules, VPError
from .parsing import contest_url
from .sessions import attempts, merge_records, save_state
from .storage import file_lock, read_json, write_json
from .tui import Dashboard, TerminalDashboard, clock


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
            verify_login(self.web.soup(self.url, params={"locale": "en"}), self.handle)
            data = self.api.api("contest.standings", contestId=self.id)
            apply_metadata(c, data["contest"], data["problems"])
            if c.extra["phase"] != "FINISHED":
                raise VPError("Use live or vp while the original contest is active; bt requires a finished contest.")
            verify_practice(self.api, c.rules.duration)
        elif self.platform in ("cwnuoj", "nowcoder"):
            self.api.identity()
            self.api.apply_metadata(c, self.api.details(), require_registration=False)
            if c.extra["phase"] != "FINISHED":
                raise VPError("This contest has not finished. Use live for the existing participation.")
            if self.platform == "nowcoder":
                self.api.problem_list(c)
        else:
            page = self.api._page()
            nav = page.select_one("ul.nav-pills")
            if not nav or not nav.select_one(f'a[href="/user/profile/{self.handle}"]'):
                raise VPError("The QOJ Firefox login does not match this account.")
            title = page.select_one("h1")
            if title:
                c.name = title.get_text(" ", strip=True)
            for row in page.select("tr"):
                link = row.select_one(f'a[href*="/contest/{self.id}/problem/"]')
                cells = row.select("td")
                label = cells[0].get_text(strip=True) if cells else ""
                if link and re.fullmatch(r"[A-Z][0-9]*", label):
                    c.problems[label] = urlsplit(link["href"]).path.rsplit("/", 1)[-1]
            if not c.problems:
                raise VPError("QOJ did not expose this contest's problems.")
            c.extra["local_attempt"] = True
        c.extra.update(practice=True, mode="practice", scoring=c.extra.get("scoring", "ICPC"))
        c.original_start = float(int(time.time()))
        self.handle = self.api.handle
        return c

    def submissions(self, contest: Contest, started: float) -> list[dict]:
        self.api.start_time = started
        values = self.api.submissions(contest)
        return [{**(asdict(s) if self.platform == "qoj" else s), "practice": True} for s in values]

    def statements(self, contest: Contest, directory: Path):
        if hasattr(self.api, "save_statements"):
            self.api.save_statements(contest, directory)


def prepare(url: str) -> tuple[Path, dict]:
    from . import cli, live
    cli.check_terminal()
    platform, _, url = contest_url(url)
    config = cli.load_account(platform, live=True)
    api = Practice(url, config)
    contest = api.prepare()
    root = cli.state_directory()
    with file_lock(root / "start.lock"):
        prior = [(p, s) for p, s in attempts(root) if s.get("mode") == "practice" and s["url"] == url
                 and s["handle"].casefold() == api.handle.casefold()]
        if prior:
            path, state = prior[-1]
            directory = Path(state["directory"])
            contest.original_start = state["start_time"]
        else:
            attempt_id = uuid4().hex[:10]
            path = root / f"bt-{platform}-{contest.contest_id}-{attempt_id}.json"
            directory = cli.desktop() / f"{contest.directory_name} BT-{attempt_id}"
            state = {"mode": "practice", "attempt_id": attempt_id, "url": url,
                     "start_time": contest.original_start, "handle": api.handle, "submissions": [],
                     "directory": str(directory), "tmux_session": path.stem, "status": "running"}
        directory.mkdir(parents=True, exist_ok=True)
        if platform == "qoj":
            cli.configure_submit(api.api, contest, directory)
        else:
            live.configure_submission(api, contest, directory)
        submission_config = read_json(directory / "contest.json")
        submission_config.update(mode="practice", base_url=url, platform=platform,
                                 problem_ids=contest.problems,
                                 problems="".join(contest.problems) if platform == "qoj" else list(contest.problems))
        write_json(directory / "contest.json", submission_config)
        state["contest"] = contest.to_dict()
        write_json(path, state)
        cli.bind_submit_context(directory, path, state)
        write_json(root / "current.json", {"state_path": str(path)})
        cli.console.print("BT · " + str(directory), markup=False)
        return path, state


class PracticeDashboard:
    def __init__(self, contest, elapsed, records, message=""):
        self.contest, self.elapsed, self.submissions, self.message = contest, elapsed, records, message

    _record_table = staticmethod(Dashboard._record_table)
    records = Dashboard.records
    __rich_console__ = Dashboard.__rich_console__

    def fixed(self, width):
        table = Table(expand=True)
        table.add_column("problem")
        table.add_column("accepted")
        table.add_column("latest result")
        for label in self.contest.problems:
            records = [s for s in self.submissions if s["problem"] == label]
            table.add_row(label, "✓" if any(s["verdict"] == "CORRECT" for s in records) else "—",
                          records[0]["verdict"] if records else "—")
        header = self._record_table()
        header.add_row("problem", "submission", "time", "result")
        return Group(Text(self.contest.name + " · BT · " + clock(self.elapsed)), table, header)


def monitor(path: Path) -> None:
    from . import cli
    with file_lock(path.with_suffix(".monitor.lock"), nonblocking=True):
        state = read_json(path)
        contest = Contest.from_dict(state["contest"])
        config = read_json(cli.config_directory() / "config.json")
        api = Practice(state["url"], config)
        if api.handle.casefold() != state["handle"].casefold():
            raise VPError("Restore this practice workspace's account before reopening it.")
        future = statements = None
        next_poll = 0.0
        message = ""
        with ThreadPoolExecutor(max_workers=2) as pool, TerminalDashboard() as view:
            statements = pool.submit(api.statements, contest, Path(state["directory"]))
            while view.input():
                if statements is not None and statements.done():
                    try:
                        statements.result()
                    except (VPError, OSError):
                        pass
                    statements = None
                if future is not None and future.done():
                    try:
                        state["submissions"] = merge_records(path, state, future.result())
                        save_state(path, state)
                        message = ""
                    except (VPError, OSError) as exc:
                        message = str(exc)
                    future = None
                    next_poll = time.monotonic() + 5
                if future is None and time.monotonic() >= next_poll:
                    future = pool.submit(api.submissions, contest, state["start_time"])
                records = submission_records(merge_records(path, state, []), "FINISHED")
                view.update(PracticeDashboard(contest, time.time()-state["start_time"], records, message))
                time.sleep(0.2)


def main():
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
