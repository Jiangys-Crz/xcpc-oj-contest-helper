"""Independent attempt boundaries and ownership of locally submitted records."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time

from .models import Contest, Submission, VPError
from .storage import file_lock, read_json, state_directory, write_json


def attempts(root: Path | None = None) -> list[tuple[Path, dict]]:
    result = []
    for path in (root or state_directory()).glob("*.json"):
        if not path.name.startswith(("vp-", "live-", "bt-")):
            continue
        state = read_json(path)
        if state.get("contest") and state.get("url") and state.get("start_time") is not None:
            result.append((path, state))
    return sorted(result, key=lambda p: (p[1]["start_time"], p[0].name))


def boundary(path: Path) -> float | None:
    return read_json(path.with_suffix(".closed")).get("capture_until")


def close_attempt(path: Path, at: float, next_id: str = "", *, reason: str = "replaced") -> None:
    old = boundary(path)
    write_json(path.with_suffix(".closed"), {"capture_until": min(old, at) if old is not None else at,
                                           "next_attempt": next_id, "reason": reason})


def receipt_owners(path: Path, state: dict) -> dict[str, tuple[Path, dict]]:
    result = {}
    for directory in path.parent.glob("*.receipts"):
        owner = directory.with_suffix(".json")
        for receipt in directory.glob("*.json"):
            data = read_json(receipt)
            if data.get("url") == state["url"] and data.get("handle", state["handle"]) == state["handle"]:
                result[str(data.get("id"))] = (owner, data)
    return result


def merge_records(path: Path, state: dict, incoming: list[dict]) -> list[dict]:
    """Partition server records; explicit receipts keep old-directory practice."""
    cutoff = boundary(path)
    owners = receipt_owners(path, state)
    values = {str(e["id"]): dict(e) for e in state.get("submissions", [])}
    for event in incoming:
        values[str(event["id"])] = dict(event)
    contest = Contest.from_dict(state["contest"])
    for sid, (owner, receipt) in owners.items():
        if owner == path and sid not in values:
            values[sid] = {k: receipt[k] for k in ("id", "team", "problem", "time", "verdict")}
    result = []
    for sid, event in values.items():
        at = state["start_time"] + event["time"]
        owner = owners.get(sid)
        if owner and owner[0] != path:
            continue
        if cutoff is not None and at >= cutoff and not owner:
            continue
        event["practice"] = bool(state.get("mode") == "practice" or event.get("practice")
                                 or owner and owner[1].get("practice") or event["time"] >= contest.rules.duration
                                 or cutoff is not None and at >= cutoff)
        result.append(event)
    return result


def merge_submissions(path: Path, state: dict, incoming: list[Submission]) -> list[Submission]:
    return [Submission(**event) for event in merge_records(path, state, [asdict(s) for s in incoming])]


def save_state(path: Path, state: dict) -> None:
    # Only submission data is shared with independent history refreshes. Writers
    # merge IDs under a separate lock, so a slow GET cannot erase new receipts.
    with file_lock(path.with_suffix(".save.lock")):
        previous = read_json(path)
        if previous.get("practice_view"):
            state["practice_view"] = True
        combined = {str(e["id"]): e for e in previous.get("submissions", [])}
        combined.update({str(e["id"]): e for e in state.get("submissions", [])})
        state["submissions"] = merge_records(path, state, list(combined.values()))
        cutoff = boundary(path)
        if state.get("mode", "vp") == "vp" and cutoff is not None and time.time() >= cutoff:
            state["status"] = "cancelled" if previous.get("status") == "cancelled" or state.get("status") in ("scheduled", "cancelled") else "archived"
            if previous.get("ended_at") is not None:
                state["ended_at"] = previous["ended_at"]
        write_json(path, state)


def end_attempt(attempt_id: str | None = None) -> tuple[Path, dict]:
    """End only local counting. Never contact or change the native site."""
    from .scoring import score_submissions
    root = state_directory()
    with file_lock(root / "start.lock"):
        all_saved = attempts(root)
        saved = [(p, s) for p, s in all_saved if s.get("mode", "vp") == "vp"]
        if attempt_id:
            selected = [(p, s) for p, s in saved if attempt_id in (p.stem, s.get("attempt_id"))]
        else:
            here = Path.cwd().resolve()
            bound = [(p, s) for p, s in all_saved if s.get("directory")
                     and here.is_relative_to(Path(s["directory"]).resolve())]
            if bound:
                path, state = max(bound, key=lambda item: (len(Path(item[1]["directory"]).parts), item[1]["start_time"]))
                if state.get("mode", "vp") != "vp":
                    raise VPError("当前目录是补题或实时比赛工作区。请用 xcpc end 轮次ID 指定要结束的本地 VP。")
                selected = [(path, state)]
            else:
                current = read_json(root / "current.json").get("state_path")
                active = [(p, s) for p, s in saved if s.get("status") in ("scheduled", "running")
                          and (boundary(p) is None or time.time() < boundary(p))
                          and time.time() < s["start_time"] + s["contest"]["rules"]["duration"]]
                selected = [(p, s) for p, s in active if str(p) == current]
                if not selected:
                    if len(active) > 1:
                        raise VPError("有多场本地 VP 正在进行。请在对应比赛目录运行 xcpc end，或使用 xcpc end 轮次ID。")
                    selected = active
        if len(selected) != 1:
            raise VPError("未找到正在进行的本地 VP。可用 vp history 查询轮次，再运行 xcpc end 轮次ID。")
        path, _ = selected[0]
        with file_lock(path.with_suffix(".activate.lock")):
            state = read_json(path)
            if state.get("status") in ("failed", "cancelled", "complete", "archived"):
                return path, state
            now = time.time()
            cancelled = state.get("status") == "scheduled" or now < state["start_time"]
            close_attempt(path, now, reason="manual")
            state.update(status="cancelled" if cancelled else "archived", ended_at=now)
            contest = Contest.from_dict(state["contest"])
            state["submissions"] = merge_records(path, state, [])
            score = score_submissions([Submission(**e) for e in state["submissions"]], list(contest.problems),
                                      contest.rules, max(0, now-state["start_time"]))
            state["summary"] = {"solved": score.solved, "penalty": score.penalty if score.results_complete else "",
                                "rank": "", "prize": ""}
            save_state(path, state)
            return path, state


def locally_ended(url: str, handle: str, native_start: float) -> bool:
    return any(s.get("mode", "vp") == "vp" and s["url"] == url
               and s["handle"].casefold() == handle.casefold()
               and s["contest"].get("extra", {}).get("native_start_time", s["start_time"]) == native_start
               and read_json(p.with_suffix(".closed")).get("reason") == "manual"
               and boundary(p) is not None and time.time() >= boundary(p)
               for p, s in attempts())
