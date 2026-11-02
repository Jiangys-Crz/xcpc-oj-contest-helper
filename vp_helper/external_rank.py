"""Explicit per-attempt external scoreboards, separate from mutable VP state."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import re
from urllib.parse import urlsplit

from .models import Cell, Contest, Medals, Reference, Submission, TeamScore, VPError
from .originals import OriginalReplay
from .storage import read_json, write_json


def source_url(value: str) -> str:
    if not isinstance(value, str):
        raise VPError('请输入支持的外榜链接。')
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        raise VPError('外榜链接格式无效。') from None
    path = parts.path.rstrip('/')
    host = parts.netloc.lower()
    valid = (host == 'board.xcpcio.com'
             and re.fullmatch(r'/icpc/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+', path)) or (
        host == 'rank.iccpc.cn' and re.fullmatch(
            r'/r/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', path))
    if parts.scheme != 'https' or parts.query or parts.fragment or not valid:
        raise VPError('外榜链接应为 https://board.xcpcio.com/icpc/... 或 https://rank.iccpc.cn/r/赛事ID。')
    return 'https://' + host + (path.lower() if host == 'rank.iccpc.cn' else path)


def attachment_path(path: Path) -> Path:
    return path.with_suffix('.external-rank')


def apply_saved_rules(path: Path, contest: Contest) -> None:
    """Keep history/slow state writers on the selected board's penalty precision."""
    marker = 'external_rank_native_rounding'
    if not attachment_path(path).exists():
        if marker in contest.extra:
            contest.rules.penalty_rounding = contest.extra.pop(marker)
        return
    try:
        data = read_json(attachment_path(path))
        metadata = data['metadata']
        rounding = metadata.get('penalty_rounding', 'per_problem')
        if (data['url'] == contest.url and metadata['duration'] == contest.rules.duration
                and metadata['problems'] == list(contest.problems)
                and metadata['penalty_minutes'] == contest.rules.penalty_minutes
                and rounding in ('per_problem', 'total_seconds')):
            contest.extra.setdefault(marker, contest.rules.penalty_rounding)
            contest.rules.penalty_rounding = rounding
    except (VPError, KeyError, TypeError, AttributeError):
        pass  # A broken optional board must not prevent saving own submissions.


def _contest_supported(contest: Contest) -> None:
    if contest.extra.get('scoring', 'ICPC') != 'ICPC':
        raise VPError('手动外榜只支持 ICPC / CCPC 计分。')


def _replay(payload: dict, contest: Contest) -> OriginalReplay:
    try:
        _contest_supported(contest)
        metadata = payload['metadata']
        if (payload['version'] != 1 or payload['url'] != contest.url
                or source_url(payload['source']) != payload['source']
                or metadata['duration'] != contest.rules.duration
                or metadata['penalty_minutes'] != contest.rules.penalty_minutes
                or metadata['problems'] != list(contest.problems)
                or metadata.get('compile_penalty', False) != contest.rules.compile_penalty):
            raise VPError('外榜的题号、比赛时长或罚时规则与当前 VP 不一致。')
        events = [Submission(**event) for event in payload['events']]
        final = [TeamScore(**{**row, 'cells': {p: Cell(**cell) for p, cell in row['cells'].items()}})
                 for row in payload['final']]
        replay_contest = deepcopy(contest)
        rounding = metadata.get('penalty_rounding', 'per_problem')
        if rounding not in ('per_problem', 'total_seconds'):
            raise VPError('外榜罚时精度不受支持。')
        replay_contest.rules.penalty_rounding = rounding
        return OriginalReplay(replay_contest, metadata['teams'], events, final)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise VPError('外榜数据不完整或格式不受支持。') from None


def prepare_import(value: str, contest: Contest) -> dict:
    page = source_url(value)
    _contest_supported(contest)
    if urlsplit(page).netloc == 'board.xcpcio.com':
        from .rank_xcpcio import load
    else:
        from .rank_ccpc import load
    try:
        metadata, events, final = load(page, deepcopy(contest))
        payload = {'version': 1, 'url': contest.url, 'source': page, 'metadata': metadata,
                   'events': [asdict(event) for event in events], 'final': [asdict(row) for row in final]}
        _replay(payload, contest)
        return payload
    except (KeyError, TypeError, ValueError, AttributeError):
        raise VPError('外榜数据不完整或格式不受支持，原榜单未修改。') from None


def import_rank(path: Path, state: dict, value: str) -> dict:
    contest = Contest.from_dict(state['contest'])
    payload = prepare_import(value, contest)
    # Only a completely validated download replaces the previous attachment.
    write_json(attachment_path(path), payload)
    return payload


def prepare_room_rank(path: Path, state: dict, value: str | None) -> dict | None:
    if not value:
        return
    page = source_url(value)
    try:
        cached = read_json(attachment_path(path))
        if cached.get('source') == page:
            _replay(cached, Contest.from_dict(state['contest']))
            return
    except (VPError, AttributeError):
        pass
    return prepare_import(page, Contest.from_dict(state['contest']))


def sync_room_rank(path: Path, state: dict, value: str | None) -> None:
    payload = prepare_room_rank(path, state, value)
    if payload is not None:
        write_json(attachment_path(path), payload)


def current_attempt() -> tuple[Path, dict]:
    from .sessions import attempts
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        config = directory / 'contest.json'
        if not config.is_file():
            continue
        context = read_json(config).get('vp_context', {})
        owner = context.get('state_path')
        match = next(((p, s) for p, s in attempts() if owner and p.resolve() == Path(owner).resolve()), None)
        if match is None or not here.is_relative_to(Path(match[1]['directory']).resolve()):
            raise VPError('当前目录未绑定有效比赛记录。')
        path, state = match
        if state.get('mode', 'vp') != 'vp' or state.get('status') in ('failed', 'cancelled'):
            raise VPError('请在有效 VP 的比赛目录运行 xcpc rank。')
        return path, state
    raise VPError('请进入 VP 比赛目录或其子目录，再运行 xcpc rank。')


def command(args: list[str]) -> None:
    if len(args) > 1 or args and args[0].startswith('-') and args[0] != '--clear':
        raise VPError('用法：xcpc rank [外榜链接 | --clear]')
    path, state = current_attempt()
    if args == ['--clear']:
        if state.get('cloud', {}).get('externalRankUrl'):
            raise VPError('此 VP 使用房间指定的外榜，不能在本地清除。')
        attachment_path(path).unlink(missing_ok=True)
        print('已恢复自动加载原榜，面板下一次刷新生效。')
        return
    value = args[0] if args else input('外榜链接：').strip()
    room_source = state.get('cloud', {}).get('externalRankUrl')
    if room_source and source_url(value) != source_url(room_source):
        raise VPError('此 VP 使用房间指定的外榜，不能在本地换成其他赛事。')
    payload = import_rank(path, state, value)
    print(f"已为 {state['contest']['name']} 导入外榜：{payload['source']}")
    print(f"{len(payload['metadata']['teams'])} 支队伍，{len(payload['events'])} 条提交；按当前 VP 已用时间回放，面板下一次刷新生效。")


class ManualRank:
    """Notice atomic imports without restarting tmux or altering VP clocks."""
    def __init__(self, path: Path, contest: Contest):
        self.path = attachment_path(path)
        self.stamp = None
        self.replay = None
        self.error = ''
        self.medals = None
        original = contest.extra.get('external_rank_native_medals', asdict(contest.medals) if contest.medals else None)
        self.original_medals = Medals(**original) if original else None
        self.original_rounding = contest.extra.get('external_rank_native_rounding', contest.rules.penalty_rounding)
        self.rounding = self.original_rounding
        self.active = False

    def reference(self, contest: Contest, elapsed: float) -> Reference | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            if self.active or 'external_rank_native_medals' in contest.extra:
                contest.medals = self.original_medals
                contest.extra.pop('external_rank_native_medals', None)
            self.active, self.stamp, self.replay = False, None, None
            if 'external_rank_native_rounding' in contest.extra:
                contest.rules.penalty_rounding = self.original_rounding
                contest.extra.pop('external_rank_native_rounding', None)
            return None
        self.active = True
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self.stamp:
            self.stamp, self.replay, self.error, self.medals = stamp, None, '', None
            try:
                payload = read_json(self.path)
                self.replay = _replay(payload, contest)
                self.rounding = payload['metadata'].get('penalty_rounding', 'per_problem')
                medals = payload['metadata'].get('medals')
                if isinstance(medals, dict) and all(type(medals.get(k)) is int and medals[k] >= 0 for k in ('gold', 'silver', 'bronze')):
                    self.medals = Medals(medals['gold'], medals['silver'], medals['bronze'], payload['source'])
            except VPError as exc:
                self.error = str(exc)
        contest.medals = self.medals
        if self.replay is None:
            return Reference(message='外榜加载失败：' + self.error)
        contest.extra['external_rank_native_rounding'] = self.original_rounding
        contest.extra['external_rank_native_medals'] = asdict(self.original_medals) if self.original_medals else None
        contest.rules.penalty_rounding = self.rounding
        return self.replay.at(contest, elapsed, released=elapsed >= contest.rules.duration)
