"""Query every independent VP/live attempt, including its practice records."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import time

from rich.table import Table

from .codeforces_live import submission_records
from .models import Contest, Submission, VPError
from .scoring import score_submissions, submission_history
from .sessions import attempts, boundary, merge_records, save_state
from .storage import config_directory, read_json
from .tui import Dashboard


def refresh(path: Path, state: dict) -> str:
    from . import cli, live
    if state.get("status") in ("scheduled", "failed", "cancelled") or time.time() < state["start_time"]:
        return ""
    contest = Contest.from_dict(state["contest"])
    config = read_json(config_directory() / "config.json")
    account = live.account_for(config, contest.platform)
    if account.get("handle", "").casefold() != state["handle"].casefold():
        return "Showing saved records; this attempt's login is not configured."
    try:
        if state.get("mode") == "practice":
            from .practice import Practice
            api = Practice(state["url"], config)
        else:
            api = live.live_adapter(state["url"], config) if state.get("mode") == "live" else cli.adapter(state["url"], config)
        api.start_time = state["start_time"]
        incoming = api.submissions(contest, state["start_time"]) if state.get("mode") == "practice" else api.submissions(contest)
        incoming = incoming if state.get("mode") in ("live", "practice") else [asdict(s) for s in incoming]
        state["submissions"] = merge_records(path, state, incoming)
        save_state(path, state)
        return ""
    except (VPError, OSError) as exc:
        return "Showing saved records: " + str(exc)


def history(args: list[str]) -> None:
    from .cli import console, history as legacy_history
    reopen = len(args) == 2 and args[1] == "--open"
    if len(args) > 1 and not reopen:
        raise VPError("Usage: vp history [ATTEMPT_ID [--open] | --live]")
    saved = attempts()
    if args == ["--live"]:
        saved = [(p, s) for p, s in saved if s.get("mode") == "live"]
    if not args or args == ["--live"]:
        if not saved:
            if not args:
                legacy_history()
            else:
                console.print("No live history yet.")
            return
        table = Table(title="VP / live history")
        for name in ("id", "mode", "contest", "start", "score", "practice", "state"):
            table.add_column(name)
        for path, state in reversed(saved):
            contest = Contest.from_dict(state["contest"])
            events = merge_records(path, state, [])
            if state.get("mode") in ("live", "practice"):
                own = state.get("snapshot", {}).get("own") or {}
                points = str(own.get("points", "—"))
                if state.get("mode") == "practice":
                    points = str(len({e["problem"] for e in events if e["verdict"] == "CORRECT"}))
            else:
                score = score_submissions([Submission(**e) for e in events], list(contest.problems),
                                          contest.rules, time.time()-state["start_time"])
                points = str(score.solved)
            table.add_row(state.get("attempt_id", path.stem), state.get("mode", "vp"), contest.name,
                          datetime.fromtimestamp(state["start_time"]).strftime("%Y-%m-%d %H:%M:%S"),
                          points, str(sum(bool(e.get("practice")) for e in events)),
                          "archived" if boundary(path) is not None and time.time() >= boundary(path) else state.get("status", ""))
        console.print(table)
        console.print("Details: vp history ATTEMPT_ID", markup=False)
        return
    matches = [(p, s) for p, s in saved if args[0] in (p.stem, s.get("attempt_id"))]
    if len(matches) != 1:
        raise VPError("Unknown or ambiguous attempt ID. Use vp history to list exact IDs.")
    path, state = matches[0]
    if reopen:
        if state.get("status") in ("failed", "cancelled"):
            raise VPError("此轮次未启动：" + state.get("activation_error", "本地预约已取消。"))
        from .cli import open_state
        open_state(path, state)
        return
    message = refresh(path, state)
    contest = Contest.from_dict(state["contest"])
    events = merge_records(path, state, [])
    records = (submission_records(events, state.get("snapshot", {}).get("phase", "FINISHED"))
               if state.get("mode") in ("live", "practice") else
               submission_history([Submission(**e) for e in events], list(contest.problems), contest.rules,
                                  time.time()-state["start_time"]))
    console.print(contest.name + " · " + state.get("attempt_id", path.stem), markup=False)
    console.print("Directory: " + state["directory"], markup=False)
    if state.get("summary"):
        console.print(" · ".join(f"{k}: {v}" for k, v in state["summary"].items()), markup=False)
    if state.get("medal_lines"):
        console.print(" · ".join(f"{k}: {v}" for k, v in state["medal_lines"].items()), markup=False)
    console.print(Dashboard._record_table() if not records else _records(records))
    if not records:
        console.print("No submissions yet.")
    if message:
        console.print(message, markup=False)


def _records(records):
    # Use the same verdict colors and practice labels as the terminal.
    from types import SimpleNamespace
    return Dashboard.records(SimpleNamespace(submissions=records, _record_table=Dashboard._record_table))
