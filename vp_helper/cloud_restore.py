"""Restore authorized cloud results into an untimed, own-account workspace.

Cloud records carry contest facts and verdicts only. Native problem mappings,
login cookies and submission configuration always come from this computer's
current judge account; importing a teammate's history never impersonates them.
"""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from uuid import uuid4

from .models import Contest, VPError
from .submission_languages import language_fields, merge_language
from .parsing import contest_url
from .platform_clock import platform_now
from .storage import file_lock, read_json, write_json


def ended(state: dict, path: Path | None = None) -> bool:
    from .sessions import boundary
    if state.get('mode') == 'practice':
        return True
    if state.get('status') in ('scheduled', 'failed', 'cancelled'):
        return False
    cutoff = boundary(path) if path is not None else None
    return bool(state.get('status') in ('complete', 'archived')
                or cutoff is not None and platform_now(state) >= cutoff
                or platform_now(state) >= state['start_time'] + state['contest']['rules']['duration'])


def _stamp(value) -> float:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        if not math.isfinite(stamp):
            raise ValueError
        return stamp
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise VPError('云端比赛记录包含无效提交时间，原本地记录未修改。') from None


def _records(snapshot: dict, started: float, problems: dict) -> list[dict]:
    values = snapshot.get('submissions')
    if not isinstance(values, list) or len(values) > 20000:
        raise VPError('云端提交记录格式无效，原本地记录未修改。')
    result = []
    for row in values:
        if not isinstance(row, dict) or row.get('problem') not in problems:
            raise VPError('云端提交题号与原站比赛不匹配，原本地记录未修改。')
        sid, verdict = row.get('id'), row.get('verdict')
        if not isinstance(sid, str) or not 0 < len(sid) <= 120 or not isinstance(verdict, str) or len(verdict) > 100:
            raise VPError('云端提交记录格式无效，原本地记录未修改。')
        event = {'id': sid, 'team': 'me', 'problem': row['problem'],
                 'time': _stamp(row.get('submittedAt')) - started,
                 'verdict': verdict, 'practice': row.get('practice') is True, **language_fields(row)}
        points = row.get('points')
        if type(points) in (int, float) and math.isfinite(points):
            event['score'] = points
        result.append(event)
    return result


def _merge(previous: list[dict], incoming: list[dict]) -> list[dict]:
    from .scoring import UNRESOLVED_VERDICTS
    pending = {'PENDING'} | UNRESOLVED_VERDICTS
    values = {str(row['id']): row for row in previous}
    for row in incoming:
        old = values.get(row['id'])
        if old and old.get('verdict') not in pending and row['verdict'] in pending:
            values[row['id']] = merge_language(row, old)
            continue
        values[row['id']] = merge_language(old, row)
    return list(values.values())


def _local_owner(path: Path, state: dict, account: dict) -> bool:
    context = state.get('cloud') or read_json(path.with_suffix('.cloud-owner'))
    return (str(context.get('userId')) == str(account['user']['id'])
            and context.get('serverUrl') == account['serverUrl'])


def restore_room(room_id: str, account: dict) -> tuple[Path, dict]:
    from . import cli, cloud
    cli.check_terminal()
    snapshot = cloud._request('POST', '/api/client/rooms/' + room_id + '/restore', {}, account=account)
    if not isinstance(snapshot, dict) or snapshot.get('roomId') != room_id:
        raise VPError('云端返回的补题记录与此房间不符。')
    platform, _, _ = contest_url(snapshot.get('contest', {}).get('url', ''))
    config = cli.load_account(platform, live=True)
    return restore_snapshot(snapshot, account, config)


def restore_native(url: str, config: dict, previous: tuple[Path, dict] | None = None) -> tuple[Path, dict] | None:
    from . import cloud
    if previous and not ended(previous[1], previous[0]):
        return None
    account = read_json(cloud.configuration_path())
    if not account.get('token'):
        return None
    try:
        result = cloud._request('POST', '/api/client/restore', {'contestUrl': url}, account=account)
    except cloud.CloudRequestError as exc:
        if exc.status_code == 404:  # Compatible with a server predating restore.
            return None
        raise
    except VPError:
        if previous:
            # Local saved VP/BT remains usable if the optional cloud is offline.
            return None
        raise
    snapshot = result.get('snapshot') if isinstance(result, dict) else None
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict) or snapshot.get('contest', {}).get('url') != url:
        raise VPError('云端返回的补题记录与此原站比赛不符。')
    return restore_snapshot(snapshot, account, config)


def restore_snapshot(snapshot: dict, account: dict, config: dict) -> tuple[Path, dict]:
    from . import cli
    from .live import account_for
    from .practice import Practice, saved_rank
    from .sessions import attempts
    from .workspaces import allocate, promote, cloud_workspace, _pinned_directory
    c = snapshot.get('contest')
    if not isinstance(c, dict) or not isinstance(snapshot.get('historyId'), str):
        raise VPError('云端补题资料不完整，原本地记录未修改。')
    platform, contest_id, url = contest_url(c.get('url', ''))
    native_handle = account_for(config, platform).get('handle', '')
    if not native_handle:
        raise VPError('请先登录自己的原站账号，再恢复补题。')
    root = cli.state_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    origin = {'serverUrl': account['serverUrl'], 'userId': str(account['user']['id']),
              'historyId': snapshot['historyId'], 'roomId': snapshot.get('roomId'),
              'participantId': snapshot.get('participantId'), 'revision': snapshot.get('revision', 0)}
    for key in ('participantTitle', 'participantSubtitle', 'participantType'):
        if isinstance(snapshot.get(key), str):
            origin[key] = snapshot[key]
    personal = snapshot.get('personalPractice') if isinstance(snapshot.get('personalPractice'), dict) else None
    with file_lock(root / 'start.lock'):
        candidates = [(p, s) for p, s in attempts(root) if s['url'] == url and s['handle'].casefold() == native_handle.casefold()]
        own = next(((p, s) for p, s in candidates if snapshot.get('isOwner') and _local_owner(p, s, account) and s.get('mode', 'vp') != 'practice'
                    and (p.stem == snapshot.get('localId') or s.get('cloud', {}).get('participantId') == snapshot.get('participantId')
                         and snapshot.get('participantId') and s.get('cloud', {}).get('serverUrl') == account['serverUrl']
                         and str(s.get('cloud', {}).get('userId')) == str(account['user']['id']))), None)
        if own:
            path, old = own
            if not ended(old, path):
                raise VPError('本地原站 VP 尚未结束，不能改为补题。')
            with file_lock(path.with_suffix('.save.lock')):
                state = read_json(path)
                incoming = _records(snapshot, state['start_time'], state['contest']['problems'])
                state['submissions'] = _merge(state.get('submissions', []), incoming)
                if personal:
                    original = {r['id'] for r in state['submissions'] if not r.get('practice')}
                    own_records = [{**r, 'practice': True} for r in _records(personal, state['start_time'], state['contest']['problems']) if r['id'] not in original]
                    state['submissions'] = _merge(state['submissions'], own_records)
                state['practice_view'] = True
                state.setdefault('practice_rank', saved_rank(state) if saved_rank(state) != '—' else str(snapshot.get('rank') or '—'))
                state['restored_from'] = origin
                if state['contest'].get('extra', {}).get('room_name') and isinstance(snapshot.get('roomName'), str):
                    state['contest']['extra']['room_name'] = snapshot['roomName']
                    origin['roomName'] = snapshot['roomName']
                write_json(path, state)
            path, state = cli.restore_contest(path, state, config)
            state['_reload_monitor'] = True
            return path, state
        # A separate BT session never inherits a room cloud binding, captain's
        # native handle or private submission configuration.
        prior = next(((p, s) for p, s in reversed(candidates) if s.get('mode') == 'practice'
                      and (not read_json(p.with_suffix('.cloud-owner')) or _local_owner(p, s, account))
                      and (not s.get('cloud_restore') or s['cloud_restore'].get('userId') == origin['userId']
                           and s['cloud_restore'].get('serverUrl') == origin['serverUrl'])), None)
        for path, state in candidates:
            if state.get('mode', 'vp') != 'practice' and not ended(state, path):
                raise VPError('此原站账号仍有正在进行的本地比赛，请结束后再恢复补题。')
        api = Practice(url, config)
        contest = api.prepare()  # Read-only metadata and own native login.
        if set(c.get('problems', [])) != set(contest.problems):
            raise VPError('云端题目列表与原站不一致，原本地记录未修改。')
        if prior:
            path, _ = prior
            state = read_json(path)
            room_name = state.get('contest', {}).get('extra', {}).get('room_name')
            if room_name:
                contest.extra['room_name'] = snapshot.get('roomName') or room_name
                origin['roomName'] = contest.extra['room_name']
            directory = _pinned_directory(path, state) if cloud_workspace(state) else Path(state['directory'])
            directory.mkdir(parents=True, exist_ok=True)
            if platform == 'qoj':
                contest.original_start = state['start_time']
            elif contest.original_start != state['start_time']:
                from .sessions import _rebase_start
                _rebase_start(state, contest.original_start)
        else:
            if isinstance(snapshot.get('roomName'), str) and snapshot['roomName'].strip():
                contest.extra['room_name'] = snapshot['roomName'].strip()
                origin['roomName'] = contest.extra['room_name']
            attempt_id = uuid4().hex[:10]
            path = root / f'bt-{platform}-{contest_id}-{attempt_id}.json'
            personal_id = personal.get('localId') if personal else None
            if (isinstance(personal_id, str) and re.fullmatch(r'bt-' + re.escape(platform + '-' + contest_id) + r'-[A-Za-z0-9_-]{1,100}', personal_id)
                    and not (root / (personal_id + '.json')).exists()):
                path = root / (personal_id + '.json')
            directory, target = allocate(cli.desktop(), contest, root, native_handle)
            state = {'mode': 'practice', 'attempt_id': attempt_id, 'url': url,
                     'start_time': contest.original_start, 'handle': native_handle, 'submissions': [],
                     'directory': str(directory), 'tmux_session': path.stem, 'status': 'running',
                     'created_at': platform_now(url)}
            if directory != target:
                state['workspace_target'] = str(target)
        incoming = _records(snapshot, state['start_time'], contest.problems)
        # A downloaded immutable baseline is displayed beside personal BT,
        # but excluded from this user's cloud sync and never sent as their VP.
        with file_lock(path.with_suffix('.cloud-owner.lock')):
            owner = read_json(path.with_suffix('.cloud-owner'))
            if owner and (str(owner.get('userId')) != origin['userId'] or owner.get('serverUrl') != origin['serverUrl']):
                raise VPError('此本地补题记录属于另一协会账号，未更改云端归属。')
            with file_lock(path.with_suffix('.save.lock')):
                fresh = read_json(path)
                if fresh:
                    if fresh['start_time'] != state['start_time']:
                        from .sessions import _rebase_start
                        _rebase_start(fresh, state['start_time'])
                    # A native verdict may finish while read-only metadata is
                    # fetched; importing the baseline must not erase that poll.
                    state = {**state, **fresh, 'start_time': state['start_time']}
                state.update(contest=contest.to_dict(), cloud_restore=origin, restored_submissions=incoming)
                if prior and str(directory) != state['directory']:
                    state['directory'] = str(directory)
                    state.pop('workspace_target', None)
                if personal:
                    state['submissions'] = _merge(state.get('submissions', []), [{**r, 'practice': True} for r in _records(personal, state['start_time'], contest.problems)])
                state.setdefault('practice_rank', str(snapshot.get('rank') or '—'))
                state.pop('cloud', None)
                write_json(path, state)
            if personal and path.stem == personal.get('localId') and type(personal.get('revision')) is int:
                owner['revision'] = max(owner.get('revision', 0), personal['revision'])
            write_json(path.with_suffix('.cloud-owner'), {**owner, 'userId': origin['userId'], 'serverUrl': origin['serverUrl']})
        cli.configure_contest(api.api if platform == 'qoj' else api, contest, directory, path, state,
                              live=platform != 'qoj', overrides={'mode': 'practice', 'base_url': url,
                              'platform': platform, 'problem_ids': contest.problems,
                              'problems': ''.join(contest.problems) if platform == 'qoj' else list(contest.problems)})
        promote(path, state)
        cli.bind_submit_context(Path(state['directory']), path, state)
        write_json(root / 'current.json', {'state_path': str(path)})
        state['_reload_monitor'] = True
        cli.console.print('BT · 云端记录已恢复：' + str(state['directory']), markup=False)
        return path, state


def display_records(state: dict, records: list[dict]) -> list[dict]:
    """Combine a read-only team baseline with own records, preserving colors."""
    imported = {str(row['id']): dict(row) for row in state.get('restored_submissions', [])}
    incoming = [{**row, 'practice': imported[str(row['id'])]['practice']} if str(row['id']) in imported else row
                for row in records]
    return _merge(list(imported.values()), incoming)
