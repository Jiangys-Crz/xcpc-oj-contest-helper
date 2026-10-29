"""One contest-named working folder shared by its saved sessions."""
from __future__ import annotations

from pathlib import Path
import re

from .models import Contest, VPError
from .storage import file_lock, read_json, write_json


def refresh_name(api, contest: Contest) -> None:
    if not callable(getattr(type(api), 'contest_name', None)):
        return
    title = api.contest_name()
    if not isinstance(title, str) or not title.strip():
        raise VPError('原站未返回实际比赛名称，原文件夹保留。')
    contest.name = title.strip()
    contest.directory_name = contest.name
    if contest.platform in ('qoj', 'cf', 'gym') and contest.extra.get('mode') not in ('live', 'practice'):
        from .parsing import ccpc_reveal_count
        contest.rules.problem_reveal_count = ccpc_reveal_count(contest.name)


def name(contest: Contest) -> str:
    if not isinstance(contest.name, str) or not contest.name.strip():
        raise VPError('原站未提供实际比赛名称，未建立编号或站点名称目录。')
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', contest.name)
    value = ' '.join(value.split()).strip(' .')
    if not value:
        raise VPError("原站未提供可用的比赛名称。")
    while len(value.encode('utf8')) > 220:
        value = value[:-1]
    return value


def _owners(root: Path, directory: Path):
    from .sessions import attempts
    return [(p, s) for p, s in attempts(root) if s.get('directory')
            and Path(s['directory']).resolve() == directory.resolve()]


def _check(root: Path, directory: Path, url: str, handle: str) -> None:
    if not directory.exists():
        return
    owners = _owners(root, directory)
    if not directory.is_dir() or not owners:
        raise VPError("比赛名称目录已经存在且未绑定本项目记录，未覆盖：" + str(directory))
    if any(s['url'] != url or s['handle'].casefold() != handle.casefold() for _, s in owners):
        raise VPError("另一个比赛或账号已使用同名目录，请用 xcpc path 选择其他保存目录：" + str(directory))


def allocate(base: Path, contest: Contest, root: Path, handle: str) -> tuple[Path, Path]:
    from .sessions import attempts
    previous = [s for _, s in attempts(root) if s['url'] == contest.url
                and s['handle'].casefold() == handle.casefold() and s.get('directory')
                and Path(s['directory']).is_dir() and s.get('status') not in ('failed', 'cancelled')]
    source = Path(previous[-1]['directory']) if previous else base / name(contest)
    target = source.parent / name(contest)
    _check(root, source, contest.url, handle)
    if source.resolve() != target.resolve() and target.exists():
        raise VPError("比赛名称目录已经存在，原代码目录保留：" + str(target))
    source.mkdir(parents=True, exist_ok=True)
    return source, target


def promote(path: Path, state: dict) -> None:
    with file_lock(path.parent / 'workspaces.lock'):
        source = Path(state['directory'])
        target = Path(state.get('workspace_target', source))
        owners = _owners(path.parent, source)
        missing = not source.exists()
        if source.is_symlink() and not source.is_dir():
            raise VPError('原比赛目录是不可用的符号链接，请先修复链接：' + str(source))
        if missing:
            _check(path.parent, target, state['url'], state['handle'])
            target.mkdir(parents=True, exist_ok=True)
        elif not source.is_dir():
            raise VPError('原比赛目录位置已被文件占用，未覆盖：' + str(source))
        elif source.resolve() != target.resolve():
            if target.exists():
                raise VPError("比赛名称目录已经存在，原目录保留：" + str(target))
            source.rename(target)
        if source.resolve() != target.resolve():
            for old_path, _ in owners:
                with file_lock(old_path.with_suffix('.save.lock')):
                    old = read_json(old_path)
                    old.update(directory=str(target), workspace_managed=True, workspace_name=state['contest']['name'])
                    old['contest'].update(name=state['contest']['name'], directory_name=state['contest']['name'])
                    old.pop('workspace_target', None)
                    write_json(old_path, old)
        state.update(directory=str(target), workspace_managed=True, workspace_name=state['contest']['name'])
        state.pop('workspace_target', None)
        # A monitor may have received verdicts while the command refreshed the
        # contest title. Renaming must never replace those with a stale snapshot.
        with file_lock(path.with_suffix('.save.lock')):
            current = read_json(path)
            combined = {str(record['id']): record for record in state.get('submissions', [])}
            combined.update({str(record['id']): record for record in current.get('submissions', [])})
            state['submissions'] = list(combined.values())
            for key in ('summary', 'medal_lines', 'recent', 'submission_history', 'snapshot', 'last_sync_time', 'ended_at', 'last_rank', 'practice_rank'):
                if key in current:
                    state[key] = current[key]
            if current.get('practice_view'):
                state['practice_view'] = True
            if current.get('status') in ('archived', 'cancelled', 'failed'):
                state['status'] = current['status']
            write_json(path, state)


def rename_existing(path: Path, state: dict, contest: Contest) -> None:
    source = Path(state['directory'])
    target = source.parent / name(contest)
    state['contest'] = contest.to_dict()
    if source.resolve() != target.resolve():
        state['workspace_target'] = str(target)
    promote(path, state)
