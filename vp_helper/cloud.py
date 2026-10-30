"""Private cloud identity, room authorization and a durable redacted sync outbox."""
from __future__ import annotations

from datetime import datetime, timezone
import getpass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import requests
from .models import Contest, Submission, VPError
from .storage import config_directory, state_directory, file_lock, read_json, write_json

DEFAULT_SERVER = 'https://vp.cwnupaa.com'
_LOCK = threading.Lock()
_WAKE = threading.Event()
_WORKER = None


def configuration_path():
    return config_directory() / 'cloud-account.json'


def server_url(value=DEFAULT_SERVER):
    parts = urlsplit(str(value).strip().rstrip('/'))
    local = parts.hostname in ('localhost', '127.0.0.1', '::1')
    if (parts.scheme != 'https' and not (parts.scheme == 'http' and local)
            or not parts.hostname or parts.username or parts.password
            or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise VPError('云端地址必须为 HTTPS 网站根地址；仅本机测试可使用 HTTP。')
    return parts.geturl().rstrip('/')


def room_url(value):
    raw = str(value).strip()
    if raw.startswith('vp.cwnupaa.com/'):
        raw = 'https://' + raw
    parts = urlsplit(raw)
    match = re.fullmatch(r'/vp/([A-Za-z0-9_-]{1,100})/?', parts.path)
    if not match:
        return None
    configured = read_json(configuration_path()).get('serverUrl', DEFAULT_SERVER)
    if match and parts.netloc == urlsplit(configured).netloc:
        origin = server_url(parts.scheme + '://' + parts.netloc)
        if parts.query or parts.fragment:
            raise VPError('请使用不含查询参数的 VP 房间链接。')
        return origin, match[1]
    return None


def _request(method, endpoint, payload=None, *, account=None, authenticated=True, retry=True):
    saved = account if account is not None else read_json(configuration_path())
    base = server_url(saved.get('serverUrl', DEFAULT_SERVER))
    headers = {'Accept': 'application/json', 'User-Agent': 'XCPC-Cloud/0.9'}
    if authenticated:
        if not saved.get('token'):
            raise VPError('请先运行 xcpc login 登录协会账号。')
        headers['Authorization'] = 'Bearer ' + saved['token']
    try:
        response = requests.request(method, base + endpoint, json=payload,
                                    headers=headers, timeout=(4, 12), allow_redirects=False)
    except requests.RequestException:
        raise VPError('无法连接 VP 云端；本地记录保留，稍后会重试同步。') from None
    if response.status_code == 401 and authenticated and retry and saved.get('password'):
        current = read_json(configuration_path())
        if current.get('user', {}).get('id') == saved.get('user', {}).get('id'):
            refreshed = login(saved['account'], saved['password'], base_url=base, interactive=False)
            if str(refreshed['user']['id']) != str(saved.get('user', {}).get('id')):
                raise VPError('协会账号身份已变化，旧比赛数据未发送；请重新检查云端账号。')
            return _request(method, endpoint, payload, account=refreshed, retry=False)
    try:
        data = response.json()
    except ValueError:
        raise VPError('VP 云端返回了无效响应，请稍后重试。') from None
    if response.status_code >= 400 or not isinstance(data, dict) or 'data' not in data:
        message = data.get('error', '') if isinstance(data, dict) else ''
        message = message if isinstance(message, str) else ''
        for secret in (saved.get('password'), saved.get('token'), (payload or {}).get('password')):
            if secret:
                message = message.replace(str(secret), '[redacted]')
        raise VPError(message[:300] or f'VP 云端请求失败（HTTP {response.status_code}）。')
    return data['data']


def login(account=None, password=None, *, base_url=None, interactive=True):
    previous = read_json(configuration_path())
    base = server_url(base_url or previous.get('serverUrl', DEFAULT_SERVER))
    if interactive:
        print('协会账号登录 · ' + base)
        try:
            account = account or input('账号或邮箱（回车保留 ' + previous.get('account', '') + '）：').strip() or previous.get('account')
            password = password or getpass.getpass('密码（输入不回显；同账号回车保留）：')
        except (EOFError, KeyboardInterrupt):
            raise VPError('登录已取消，原账号未修改。') from None
        if not password and account == previous.get('account') and base == previous.get('serverUrl'):
            password = previous.get('password')
    if not isinstance(account, str) or not account.strip() or not isinstance(password, str) or not password:
        raise VPError('请输入协会官网的账号或邮箱和密码。')
    result = _request('POST', '/api/auth/login', {'account':account.strip(), 'password':password},
                      account={'serverUrl':base}, authenticated=False)
    if not isinstance(result, dict) or not result.get('token') or not result.get('user', {}).get('id'):
        raise VPError('VP 云端未返回有效账号，原配置未修改。')
    saved = {'serverUrl':base, 'account':account.strip(), 'password':password,
             'token':result['token'], 'expiresAt':result.get('expiresAt'),
             'user':{key:result['user'].get(key, '') for key in
                     ('id','useraccount','username','realname','numberid','email','role')}}
    with file_lock(configuration_path().with_suffix('.lock')):
        write_json(configuration_path(), saved)
    if interactive:
        print('已登录：' + (saved['user']['username'] or saved['user']['useraccount']) + ' · ' + saved['user']['realname'])
        queue_all()
        kick_sync_process()
    return saved


def logout():
    saved = read_json(configuration_path())
    if saved.get('token'):
        try:
            _request('POST', '/api/auth/logout', {}, account=saved, retry=False)
        except VPError:
            pass
    with file_lock(configuration_path().with_suffix('.lock')):
        configuration_path().unlink(missing_ok=True)
    print('已退出云端账号；本地比赛数据保留。')


def account_status():
    saved = read_json(configuration_path())
    if not saved.get('token'):
        print('尚未登录 VP 云端。运行 xcpc login 登录或切换账号。')
        return
    user = saved.get('user', {})
    print('云端：' + saved['serverUrl'])
    for key, label in (('useraccount','账号'),('username','昵称'),('realname','姓名'),('numberid','学号'),('email','邮箱')):
        print(label + '：' + str(user.get(key, '')))
    print('修改密码或切换账号：xcpc login')
    status = read_json(state_directory() / 'cloud-sync-status.json')
    if status.get('error'):
        print('同步：' + status['error'])
    print("待同步记录：" + str(sum(1 for p in _outbox().glob("*.json") if read_json(p).get("userId") == str(user.get("id")))))
    kick_sync_process()


def authorize(room_id, *, fresh=False):
    saved = read_json(configuration_path())
    if fresh:
        if not saved.get('account') or not saved.get('password'):
            saved = login()
        else:
            saved = login(saved['account'], saved['password'], base_url=saved['serverUrl'], interactive=False)
    result = _request('POST', '/api/client/rooms/' + room_id + '/authorize', {}, account=saved)
    if isinstance(result.get('serverNow'),str):
        try:
            offset = datetime.fromisoformat(result['serverNow'].replace('Z','+00:00')).timestamp()-time.time()
            with file_lock(configuration_path().with_suffix('.lock')):
                current = read_json(configuration_path())
                if current.get('user', {}).get('id') == saved.get('user', {}).get('id'):
                    current['serverOffset'] = offset
                    write_json(configuration_path(), current)
                    saved = current
        except (ValueError, OSError):
            pass
    if not result.get('canSubmit'):
        raise VPError('此房间以队伍参加，仅队长可以在本地创建 VP 和提交；队员可在网页查看榜单与虚拟打印。')
    return result, saved


def ensure_access(state, *, fresh=False):
    context = state.get('cloud')
    if not context:
        return
    saved = read_json(configuration_path())
    if str(saved.get('user', {}).get('id')) != str(context.get('userId')) or saved.get('serverUrl') != context.get('serverUrl'):
        raise VPError('当前云端账号与本场 VP 不符，请运行 xcpc login 切回原队长或参赛账号。')
    result, saved = authorize(context['roomId'], fresh=fresh)
    if str(result['participant']['id']) != str(context.get('participantId')):
        raise VPError('本场 VP 的参赛身份已变化，请联系房间创建者。')
    if result['room']['contestUrl'].rstrip('/') != state['url'].rstrip('/'):
        raise VPError('房间原站比赛链接已修改，不能使用旧的本地 VP 提交。')


def start_room(value):
    from . import cli, vp
    from .sessions import latest_session
    parsed = room_url(value)
    if not parsed:
        raise VPError('无效的 VP 云端房间链接。')
    base, room_id = parsed
    saved = read_json(configuration_path())
    if saved and saved.get('serverUrl') != base:
        raise VPError('此房间服务器与已登录账号不符，请先运行 xcpc login --server ' + base)
    result, saved = authorize(room_id, fresh=True)
    room, participant = result['room'], result['participant']
    from .parsing import contest_url
    platform, _, native_url = contest_url(room['contestUrl'])
    if platform not in ('cf','gym','qoj'):
        raise VPError('云端 VP 只支持 Codeforces、Gym 和 QOJ。')
    try:
        scheduled = datetime.fromisoformat(room['startsAt'].replace('Z','+00:00')).timestamp()
    except (ValueError, TypeError):
        raise VPError('房间开始时间无效。') from None
    config = cli.load_account(platform)
    from .live import account_for
    previous = latest_session(native_url, account_for(config, platform).get('handle', ''))
    context = {'serverUrl':base, 'roomId':room_id, 'roomName':room['name'],
               'userId':str(saved['user']['id']), 'participantId':str(participant['id']),
               'label':participant['label'], 'startsAt':room['startsAt'],
               'freezeMinutes':room.get('freezeMinutes', 0), 'scoringSystem':room.get('scoringSystem', 'ICPC'),
               'captainId':str(participant['captainId']), 'type':participant['type']}
    if previous:
        path, state = previous
        old = state.get('cloud', {})
        if old and (old.get('roomId') != room_id or str(old.get('userId')) != str(saved['user']['id'])):
            raise VPError('原站比赛已有其他云端房间的 VP；不能重复创建或混用记录。')
        if not old and (state.get('mode','vp') != 'vp' or abs(state['start_time'] - scheduled) > 60):
            raise VPError('此原站比赛已有不同开始时间的本地 VP，不能作为本场房间重新 VP。')
        if old and abs(datetime.fromisoformat(old['startsAt'].replace('Z','+00:00')).timestamp()-scheduled) > 1:
            raise VPError('房间时间已修改，现有原站 VP 预约不能自动重置，请联系房间管理员。')
        state['cloud'] = context
        contest = Contest.from_dict(state['contest'])
        from .freeze import configure, configure_format
        configure_format(contest, room_format=context['scoringSystem'])
        configure(contest, room_minutes=context['freezeMinutes'])
        state['contest'] = contest.to_dict()
        write_json(path, state)
        cli.bind_submit_context(Path(state['directory']), path, state)
        queue_state(path, state)
        cli.reopen_saved(path, state, config)
    else:
        clock = result.get('serverNow')
        try:
            server_now = datetime.fromisoformat(clock.replace('Z','+00:00')).timestamp() if isinstance(clock,str) else time.time()
        except ValueError:
            server_now = time.time()
        if scheduled < server_now - 5:
            raise VPError('房间已经开始，只能恢复此前已启动的本地 VP，不能从当前时间重新开始。')
        path, state = vp.prepare(native_url, scheduled, cloud=context)
        queue_state(path, state)
        cli.open_state(path, state)


def check_submission(config):
    context = config.get('vp_context', {})
    if context.get('state_path'):
        state = read_json(Path(context['state_path']))
        if state.get('cloud'):
            ensure_access(state)


def _iso(value):
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _penalty(value, fallback=0):
    if isinstance(value, str) and re.fullmatch(r'\d+:\d{2}(?::\d{2})?', value):
        from .parsing import seconds
        return int(seconds(value) // 60)
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return fallback


def _event_points(event):
    return event.get('score') if event.get('score') is not None else event.get('points')


def sync_payload(path, state):
    """Strict allowlist: judge credentials, local paths and source never leave the PC."""
    from .scoring import score_submissions, score_practice_submissions
    from .submission_results import judging_url
    contest = Contest.from_dict(state['contest'])
    duration = contest.rules.duration
    records = [dict(item) for item in state.get('submissions', [])]
    for event in records:
        event['practice'] = bool(state.get('mode') == 'practice' or event.get('practice') or event['time'] >= duration)
    def scoring_events(practice):
        return [Submission(str(e['id']), 'me', e['problem'], e['time'], e['verdict'], score=_event_points(e))
                for e in records if e['practice'] == practice]
    scoring = contest.extra.get('scoring', 'ICPC')
    maxima = contest.extra.get('problem_max_scores', {})
    score = score_submissions(scoring_events(False), list(contest.problems), contest.rules, duration,
                              scoring=scoring, problem_max_scores=maxima,
                              oi_time_tiebreak=bool(contest.extra.get('oi_time_tiebreak')))
    corrections = scoring_events(True)
    practice_score = score_practice_submissions(corrections, list(contest.problems), max([1] + [e.time for e in corrections]))
    original_attempts = score_practice_submissions(scoring_events(False), list(contest.problems), duration) if state.get('practice_view') else score
    cells = {label:{'tries':original_attempts.cells[label].tries+practice_score.cells[label].tries,
                    'pending':score.cells[label].pending+practice_score.cells[label].pending,
                    'solved':score.cells[label].solved, 'practiceSolved':practice_score.cells[label].solved,
                    'solveTime':score.cells[label].solve_time,
                    **({'points':score.cells[label].score} if score.cells[label].score is not None else {}),
                    **({'maxPoints':maxima[label]} if label in maxima else {})} for label in contest.problems}
    for cell in cells.values():
        if cell.get('solveTime') is None:
            cell.pop('solveTime', None)
    own = state.get('snapshot', {}).get('own') or {}
    rank = state.get('practice_rank') or state.get('last_rank') or state.get('summary', {}).get('rank') or own.get('rank') or '—'
    for index, label in enumerate(contest.problems):
        native = own.get('problemResults', [])
        if index < len(native):
            cell = native[index]
            accepted = cell.get('accepted', cell.get('points', 0) > 0 if scoring in ('ICPC', 'CF') else False)
            if accepted:
                cells[label]['solved'] = True
            native_tries = int(cell.get('rejectedAttemptCount', 0)) + int(bool(accepted))
            cells[label]['tries'] = max(original_attempts.cells[label].tries, native_tries) + practice_score.cells[label].tries
            if scoring != 'ICPC' and type(cell.get('points')) in (int, float):
                cells[label]['points'] = cell['points']
    mode = {'practice':'bt','live':'live'}.get(state.get('mode'), 'vp')
    solved = practice_score.solved if mode == 'bt' else sum(c['solved'] for c in cells.values())
    payload = {'localId':path.stem,'contest':{'platform':contest.platform,'contestId':contest.contest_id,
               'url':contest.url,'name':contest.name,'problems':list(contest.problems),
               'rules':{'duration':duration,'penalty_minutes':contest.rules.penalty_minutes,
                        'compile_penalty':contest.rules.compile_penalty,
                        'freeze_minutes':(duration-contest.rules.freeze_at)/60 if contest.rules.freeze_at is not None else 0,
                        'scoring':scoring,'score_policy':'subtasks' if contest.extra.get('score_policy') == 'subtasks' else 'latest' if scoring == 'OI' else 'best',
                        'problem_max_scores':maxima,'contest_format':contest.extra.get('contest_format', 'ICPC'),
                        'problem_reveal_count':contest.rules.problem_reveal_count}},
               'mode':mode,'status':state.get('status','running'),'rank':str(rank),'solved':solved,
               'penalty':0 if mode == 'bt' or scoring != 'ICPC' else _penalty(own.get('penalty'), score.penalty),'cells':cells,
               'submissions':[{'id':str(e['id']),'problem':e['problem'],'submittedAt':_iso(state['start_time']+e['time']),
                              'verdict':e['verdict'],'practice':e['practice'],
                              'url':judging_url(contest.url, str(e['id'])),
                              **({'points':_event_points(e)} if _event_points(e) is not None else {})} for e in records]}
    created = state.get('created_at')
    if type(created) in (int, float) and math.isfinite(created) and created >= 0:
        try:
            payload['createdAt'] = _iso(created)
        except (OverflowError, OSError, ValueError):
            pass  # Old or corrupt metadata must not block result synchronization.
    if mode != 'bt' and scoring != 'ICPC':
        points = own.get('points', score.points)
        if points is not None:
            payload['points'] = points
    if state.get('public_statistics') is not None and state.get('statistics_at') is not None:
        payload.update(publicStatistics=state['public_statistics'], revealedProblems=state.get('revealed_problems', []),
                       statisticsAt=_iso(state['statistics_at']))
    if mode != 'bt':
        payload['startedAt'] = _iso(state['start_time'])
        natural_end = state['start_time'] + duration
        if state.get('ended_at') is not None or state.get('status') in ('complete','archived') or time.time() >= natural_end:
            payload['endedAt'] = _iso(state.get('ended_at', natural_end))
    if state.get('cloud'):
        payload['roomId'] = state['cloud']['roomId']
    return payload


def _outbox():
    return state_directory() / 'cloud-outbox'


def queue_state(path, state):
    try:
        # Only managed sessions may use this user's saved cloud credentials.
        # Temporary/imported files are not authorized synchronization sources.
        if Path(path).resolve().parent != state_directory().resolve():
            return
        saved = read_json(configuration_path())
    except (VPError, OSError):
        return
    user_id = str(saved.get('user', {}).get('id', ''))
    if not saved.get('token') or not user_id or (state.get('status') in ('failed','cancelled') and not state.get('cloud')):
        return
    context = state.get('cloud', {})
    if context and (str(context.get('userId')) != user_id or context.get('serverUrl') != saved.get('serverUrl')):
        return
    try:
        payload = sync_payload(path, state)
        owner_path = path.with_suffix('.cloud-owner')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with file_lock(path.with_suffix('.cloud-owner.lock')):
            owner = read_json(owner_path)
            if owner and (str(owner.get('userId')) != user_id or owner.get('serverUrl') != saved['serverUrl']):
                return
            if owner.get('digest') == digest:
                _start_worker()
                return
            revision = max(int((time.time()+saved.get('serverOffset',0))*1000), owner.get('revision', 0)+1)
            payload['revision'] = revision
            key = hashlib.sha256((saved['serverUrl'] + user_id + path.stem).encode()).hexdigest()
            item = _outbox() / (key+'.json')
            with file_lock(item.with_suffix('.lock')):
                write_json(item, {'serverUrl':saved['serverUrl'],'userId':user_id,'payload':payload})
            write_json(owner_path, {'userId':user_id,'serverUrl':saved['serverUrl'],'revision':revision,'digest':digest})
        _start_worker()
    except (OSError, ValueError, KeyError, TypeError, VPError):
        # Cloud availability must never prevent saving the original local state.
        pass


def queue_all():
    from .sessions import attempts
    for path, state in attempts():
        queue_state(path, state)
    _start_worker()


def flush_once(*, deadline=None):
    saved = read_json(configuration_path())
    if not saved.get('token'):
        return
    for path in sorted(_outbox().glob('*.json')):
        if deadline is not None and time.monotonic() >= deadline:
            return
        pending = read_json(path)
        if pending.get('serverUrl') != saved.get('serverUrl') or str(pending.get('userId')) != str(saved.get('user', {}).get('id')):
            continue
        try:
            _request('POST','/api/client/sync',pending['payload'],account=saved)
            # A newer save can replace the same outbox item during the request.
            with file_lock(path.with_suffix('.lock')):
                if read_json(path).get('payload', {}).get('revision') == pending['payload']['revision']:
                    path.unlink(missing_ok=True)
            write_json(state_directory()/'cloud-sync-status.json',{'lastSuccess':time.time(),'error':''})
        except VPError as exc:
            write_json(state_directory()/'cloud-sync-status.json',{'error':str(exc),'at':time.time()})
            return


def _work():
    while True:
        _WAKE.wait(5)
        _WAKE.clear()
        try:
            flush_once()
        except (OSError, ValueError, KeyError, TypeError, VPError):
            pass


def _start_worker():
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name='xcpc-cloud-sync', daemon=True)
            _WORKER.start()
    _WAKE.set()


def login_command(args):
    if args in ([], ['--help'], ['-h']):
        if args:
            print('Usage: xcpc login [--server https://vp.cwnupaa.com]')
            return
        login()
    elif len(args) == 2 and args[0] == '--server':
        login(base_url=args[1])
    else:
        raise VPError('Usage: xcpc login [--server https://vp.cwnupaa.com]')



def kick_sync_process():
    """A bounded one-shot drain survives CLI / native-messaging process exit."""
    if not read_json(configuration_path()).get('token'):
        return
    options = {'creationflags':0x08000200} if sys.platform == 'win32' else {'start_new_session':True}
    try:
        subprocess.Popen([sys.executable,'-I','-m','vp_helper.cloud','_flush'], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,**options)
    except OSError:
        write_json(state_directory()/'cloud-sync-status.json',{'error':'后台同步未启动；记录保留，可运行 xcpc account 重试。','at':time.time()})


def drain_pending():
    from .models import VPError
    try:
        with file_lock(_outbox()/'.drain.lock',nonblocking=True):
            flush_once(deadline=time.monotonic()+55)
    except (VPError,OSError,ValueError,TypeError,KeyError):
        # Another active drain owns this batch, or the durable error is already recorded.
        pass


if __name__ == '__main__' and sys.argv[1:] == ['_flush']:
    drain_pending()
