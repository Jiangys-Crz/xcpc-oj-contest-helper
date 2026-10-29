"""Independent attempt boundaries and ownership of locally submitted records."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

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


def close_attempt(path: Path, at: float, next_id: str) -> None:
    old = boundary(path)
    write_json(path.with_suffix(".closed"), {"capture_until": min(old, at) if old is not None else at,
                                           "next_attempt": next_id})


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
        combined = {str(e["id"]): e for e in previous.get("submissions", [])}
        combined.update({str(e["id"]): e for e in state.get("submissions", [])})
        state["submissions"] = merge_records(path, state, list(combined.values()))
        write_json(path, state)
