"""Independent attempt boundaries and ownership of locally submitted records."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
import time

from .platform_clock import platform_now
from .accounts import same_handle
from .models import Contest, Submission, VPError
from .submission_languages import merge_language, language_fields
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


def latest_session(url: str, handle: str, *, include_practice: bool = False) -> tuple[Path, dict] | None:
    root = state_directory()
    saved = [(p, s) for p, s in attempts(root) if s['url'] == url
             and s['handle'].casefold() == handle.casefold() and s.get('status') not in ('failed', 'cancelled')]
    native = [(p, s) for p, s in saved if s.get('mode', 'vp') in ('vp', 'live')]
    if any(s.get('mode', 'vp') == 'vp' and s.get('status') != 'scheduled' for _, s in native):
        native = [(p, s) for p, s in native if s.get('mode', 'vp') != 'vp' or s.get('status') != 'scheduled']
    candidates = native or (saved if include_practice else [])
    return max(candidates, key=lambda item: (item[1].get('created_at', item[1]['start_time']), item[1]['start_time'])) if candidates else None


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
            if (data.get("url") == state["url"]
                    and same_handle(state['contest']['platform'], data.get("handle", state["handle"]), state["handle"])):
                result[str(data.get("id"))] = (owner, data)
    return result


def merge_records(path: Path, state: dict, incoming: list[dict]) -> list[dict]:
    """Partition server records; explicit receipts keep old-directory practice."""
    closed = read_json(path.with_suffix(".closed"))
    cutoff = closed.get("capture_until")
    manual_practice = closed.get("reason") == "manual" and state.get("status") != "cancelled"
    owners = receipt_owners(path, state)
    values = {str(e["id"]): dict(e) for e in state.get("submissions", [])}
    for event in incoming:
        values[str(event["id"])] = merge_language(values.get(str(event["id"])), event)
    contest = Contest.from_dict(state["contest"])
    for sid, (owner, receipt) in owners.items():
        if owner == path and sid not in values:
            values[sid] = {**{k: receipt[k] for k in ("id", "team", "problem", "time", "verdict")},
                           **language_fields(receipt)}
    result = []
    for sid, event in values.items():
        at = state["start_time"] + event["time"]
        owner = owners.get(sid)
        if owner and owner[0] != path:
            continue
        if cutoff is not None and at >= cutoff and not owner and not manual_practice:
            continue
        event["practice"] = bool(state.get("mode") == "practice" or event.get("practice")
                                 or owner and owner[1].get("practice") or event["time"] >= contest.rules.duration
                                 or cutoff is not None and at >= cutoff)
        result.append(event)
    return result


def merge_submissions(path: Path, state: dict, incoming: list[Submission]) -> list[Submission]:
    return [Submission(**event) for event in merge_records(path, state, [asdict(s) for s in incoming])]


def reserve_submission_sync(path: Path) -> int:
    """Order observations when requests begin, rather than when slow responses finish."""
    with file_lock(path.with_suffix('.save.lock')):
        cursor = path.with_suffix('.sync-sequence')
        revision = max(read_json(cursor).get('revision', 0), read_json(path).get('sync_revision', 0)) + 1
        write_json(cursor, {'revision': revision})
        return revision


def apply_submission_sync(path: Path, state: dict, incoming: list[dict], revision: int) -> list[dict]:
    state['submissions'] = merge_records(path, state, incoming)
    state['sync_revision'] = revision
    state['last_sync_time'] = platform_now(state)
    save_state(path, state)
    return state['submissions']


def _rebase_start(state: dict, started: float) -> None:
    old = state['start_time']
    delta = old - started
    state['start_time_aliases'] = sorted(set(state.get('start_time_aliases', []) + [old]) - {started})
    state['start_time'] = started
    for key in ('submissions', 'submission_history', 'recent'):
        state[key] = [{**event, 'time': event['time'] + delta} if isinstance(event.get('time'), (int, float))
                      else event for event in state.get(key, [])]


def reconcile_native_start(path: Path, state: dict, started: float) -> dict:
    """Adopt the same CF/Gym VP's API epoch while preserving absolute record times."""
    if (state.get('mode', 'vp') != 'vp' or state['contest']['platform'] not in ('cf', 'gym')
            or not isinstance(started, (int, float)) or not math.isfinite(started)
            or abs(started - state['start_time']) > 60
            or state.get('start_time_source') == 'codeforces_api' and started != state['start_time']):
        raise VPError('Codeforces 的原站开始时间与本地会话不符，未修改比赛记录。')
    with file_lock(path.with_suffix('.save.lock')):
        saved = read_json(path)
        old = saved['start_time']
        if old == started:
            return saved
        if old != state['start_time']:
            raise VPError('比赛时间已由另一个同步任务更新，请恢复此面板后重试。')
        _rebase_start(saved, started)
        saved['start_time_source'] = 'codeforces_api'
        write_json(path, saved)
        # An in-flight submit with the old context is accepted via the saved
        # alias; record_receipt holds this same lock and uses the new epoch.
        for receipt in path.with_suffix('.receipts').glob('*.json'):
            event = read_json(receipt)
            if (event.get('start_time') == old and event.get('url') == saved['url']
                    and same_handle(saved['contest']['platform'], event.get('handle', saved['handle']), saved['handle'])):
                event.update(start_time=started, time=event['time'] + old - started)
                cutoff = boundary(path)
                event['practice'] = bool(saved.get('mode') == 'practice'
                                         or event['time'] >= saved['contest']['rules']['duration']
                                         or cutoff is not None and started + event['time'] >= cutoff)
                write_json(receipt, event)
        return saved


def save_state(path: Path, state: dict, *, practice_rank: str | None = None) -> None:
    # Only submission data is shared with independent history refreshes. Writers
    # merge IDs under a separate lock, so a slow GET cannot erase new receipts.
    with file_lock(path.with_suffix(".save.lock")):
        previous = read_json(path)
        from .external_rank import apply_saved_rules
        contest = Contest.from_dict(state['contest'])
        apply_saved_rules(path, contest)
        state['contest'] = contest.to_dict()
        if (state['start_time'] != previous.get('start_time', state['start_time'])
                and state['start_time'] in previous.get('start_time_aliases', [])):
            _rebase_start(state, previous['start_time'])
            state['start_time_aliases'] = previous['start_time_aliases']
            state['start_time_source'] = previous.get('start_time_source', 'codeforces_api')
        if previous.get("workspace_managed"):
            state["directory"] = previous["directory"]
            state["workspace_managed"] = True
            if "workspace_target" not in previous:
                state.pop("workspace_target", None)
        if previous.get('workspace_name'):
            state['workspace_name'] = previous['workspace_name']
            from .workspaces import cloud_workspace
            if not cloud_workspace(previous) and not cloud_workspace(state):
                state['contest'].update(name=state['workspace_name'], directory_name=state['workspace_name'])
            elif previous.get('contest', {}).get('extra', {}).get('room_name'):
                state['contest'].setdefault('extra', {})['room_name'] = previous['contest']['extra']['room_name']
            if state.get('mode', 'vp') == 'vp':
                from .parsing import ccpc_reveal_count
                selected = previous.get('contest', {}).get('extra', {}).get('contest_format') or state['contest'].get('extra', {}).get('contest_format')
                if selected:
                    state['contest'].setdefault('extra', {})['contest_format'] = selected
                state['contest']['rules']['problem_reveal_count'] = (50 if selected == 'CCPC' else None) if selected else ccpc_reveal_count(state['workspace_name'])
        if previous.get("cloud"):
            state["cloud"] = previous["cloud"]
        if (previous.get('room_board')
                and previous['room_board']['elapsed'] > (state.get('room_board') or {}).get('elapsed', -1)):
            state['room_board'] = previous['room_board']
        for key in ('cloud_restore', 'restored_submissions', 'restored_from'):
            if key in previous:
                state[key] = previous[key]
        if previous.get("practice_view"):
            state["practice_view"] = True
        # Only a freshly verified final reference may replace the BT rank.
        # Ordinary refreshes still preserve it against stale monitor writes.
        if practice_rank is not None:
            state['practice_rank'] = practice_rank
        elif 'practice_rank' in previous:
            state['practice_rank'] = previous['practice_rank']
            if previous.get('practice_view') and previous.get('mode', 'vp') == 'vp':
                # Keep a cached VP rank and its provenance together. Otherwise
                # a stale writer can hide that the rank needs an external board,
                # or resurrect an old source after a verified recalculation.
                if 'reference_rank' in previous:
                    state['reference_rank'] = previous['reference_rank']
                else:
                    state.pop('reference_rank', None)
        previous_records = {str(e["id"]): e for e in previous.get("submissions", [])}
        combined = {str(e["id"]): e for e in state.get("submissions", [])}
        if previous.get('sync_revision', 0) > state.get('sync_revision', 0):
            combined.update({sid: merge_language(combined.get(sid), event) for sid, event in previous_records.items()})
            state['sync_revision'] = previous['sync_revision']
            if 'last_sync_time' in previous:
                state['last_sync_time'] = previous['last_sync_time']
            if 'summary' in previous:
                state['summary'] = previous['summary']
        else:
            combined = {**previous_records, **{sid: merge_language(previous_records.get(sid), event)
                                              for sid, event in combined.items()}}
        state["submissions"] = merge_records(path, state, list(combined.values()))
        cutoff = boundary(path)
        if state.get("mode", "vp") == "vp" and cutoff is not None and platform_now(state) >= cutoff:
            state["status"] = "cancelled" if previous.get("status") == "cancelled" or state.get("status") in ("scheduled", "cancelled") else "archived"
            if previous.get("ended_at") is not None:
                state["ended_at"] = previous["ended_at"]
        write_json(path, state)
    from .cloud import queue_state
    queue_state(path, state)


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
                owner = read_json(Path(max(bound, key=lambda item: len(Path(item[1]["directory"]).parts))[1]["directory"]) / "contest.json").get("vp_context", {}).get("state_path")
                path, state = next((item for item in bound if str(item[0]) == owner), None) or max(
                    bound, key=lambda item: (len(Path(item[1]["directory"]).parts), item[1].get("created_at", item[1]["start_time"])))
                if state.get("mode", "vp") != "vp":
                    raise VPError("当前目录是补题或实时比赛工作区。请用 xcpc end 轮次ID 指定要结束的本地 VP。")
                selected = [(path, state)]
            else:
                current = read_json(root / "current.json").get("state_path")
                active = [(p, s) for p, s in saved if s.get("status") in ("scheduled", "running")
                          and (boundary(p) is None or platform_now(s) < boundary(p))
                          and platform_now(s) < s["start_time"] + s["contest"]["rules"]["duration"]]
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
            now = platform_now(state)
            cancelled = state.get("status") == "scheduled" or now < state["start_time"]
            close_attempt(path, now, reason="manual")
            state.update(status="cancelled" if cancelled else "archived", ended_at=now)
            contest = Contest.from_dict(state["contest"])
            state["submissions"] = merge_records(path, state, [])
            score = score_submissions([Submission(**e) for e in state["submissions"]], list(contest.problems),
                                      contest.rules, max(0, now-state["start_time"]), scoring=contest.extra.get('scoring', 'ICPC'),
                                      problem_max_scores=contest.extra.get('problem_max_scores', {}),
                                      oi_time_tiebreak=bool(contest.extra.get('oi_time_tiebreak')))
            state["summary"] = {"solved": score.solved, "penalty": score.penalty if score.results_complete else "",
                                "rank": "", "prize": ""}
            save_state(path, state)
            return path, state


def locally_ended(url: str, handle: str, native_start: float) -> bool:
    return any(s.get("mode", "vp") == "vp" and s["url"] == url
               and s["handle"].casefold() == handle.casefold()
               and s["contest"].get("extra", {}).get("native_start_time", s["start_time"]) == native_start
               and read_json(p.with_suffix(".closed")).get("reason") == "manual"
               and boundary(p) is not None and platform_now(s) >= boundary(p)
               for p, s in attempts())
