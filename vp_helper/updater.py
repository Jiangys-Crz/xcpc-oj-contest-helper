"""Update the installed application without changing the user's checkout."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

import requests
import sys

from .models import VPError
from .storage import read_json

REPOSITORY = "https://github.com/Jiangys-Crz/xcpc-oj-contest-helper.git"
LEGACY_REPOSITORY = "https://gitee.com/Zyihan_Crz/xcpc-vp-system.git"
LEGACY_GITHUB_REPOSITORY = "https://github.com/Zyihan-Crz/xcpc-oj-contest-helper.git"


def installation() -> dict:
    release = Path(sys.prefix).resolve().parent
    data = read_json(release / "installation.json")
    if (release.parent.name != "releases" or not data
            or Path(data.get("install_root", "")).resolve() != release.parent.parent
            or not isinstance(data.get("local"), bool)
            or not Path(data.get("bin_directory", "")).is_absolute()):
        installer = 'install.sh' if sys.platform == 'linux' else ('install.ps1' if sys.platform == 'win32' else 'install-macos.sh')
        raise VPError(f"未找到有效安装信息。请先用新版 {installer} 安装一次，再使用 xcpc update。")
    return data


@contextmanager
def update_lock(path: Path):
    if sys.platform == 'win32':
        from .storage import file_lock
        with file_lock(path, nonblocking=True):
            yield
        return
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise VPError("此安装已有更新任务正在运行，请稍后再试。") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, "GIT_TERMINAL_PROMPT":"0"},
                                capture_output=True, text=True, check=True, timeout=600)
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        raise VPError("获取更新代码失败。请检查网络连接及 GitHub 仓库访问权限；原版本未改动。") from None


def ensure_git() -> None:
    if shutil.which("git"):
        return
    apt = shutil.which("apt-get") if sys.platform == 'linux' else None
    if not apt:
        raise VPError("更新需要 Git，请先安装 Git 后重试。")
    prefix = [] if getattr(os, 'geteuid', lambda: 1)() == 0 else ["sudo", "--"]
    print("正在安装更新所需的 Git…", flush=True)
    try:
        subprocess.run([*prefix, apt, "update"], check=True)
        subprocess.run([*prefix, apt, "install", "-y", "git"], check=True)
    except (subprocess.SubprocessError, OSError):
        raise VPError("Git 安装失败；原版本未改动。请安装 Git 后重试。") from None


def installer_command(source: Path, data: dict) -> list[str]:
    python = data.get("python", "")
    if not python or not Path(python).is_file():
        python = getattr(sys, "_base_executable", sys.executable)
    python = str(Path(python).resolve())
    if data.get('portable'):
        if data.get('global'):
            targets = ['--target-root', data['install_root'], '--target-bin', data['bin_directory']]
            if sys.platform == 'win32':
                return ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(source / 'install.ps1'), *targets]
            return ['sh', str(source / 'install-macos.sh'), *targets]
        return [python, str(source / 'install.py'), '--target-root', data['install_root'],
                '--target-bin', data['bin_directory']]
    command = ["bash", str(source/"install.sh"), "--python", python,
               "--target-root", data["install_root"], "--target-bin", data["bin_directory"]]
    if data["local"]:
        command.append("--local")
    if data.get("submit_path"):
        command.append(data["submit_path"])
    return command



CLOUD_SERVER = "https://vp.cwnupaa.com"


class CloudUnavailable(VPError):
    """A connectivity failure permits fallback; integrity failures never do."""


def _download(url, *, stream=False):
    try:
        result = requests.get(url, timeout=(4, 30), allow_redirects=False, stream=stream,
                              headers={'User-Agent':'XCPC-Updater/0.9','Accept':'application/json' if not stream else 'application/gzip'})
    except requests.RequestException:
        raise CloudUnavailable('云端更新连接失败。') from None
    if result.status_code != 200:
        result.close()
        raise CloudUnavailable('云端更新暂不可用。')
    return result


def safe_extract(archive, destination):
    """Extract only bounded regular files/directories into a new owned directory."""
    destination = Path(destination).resolve()
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) > 20000 or sum(max(0,m.size) for m in members) > 1024*1024*1024:
            raise VPError('更新源码包过大，原版本未修改。')
        for member in members:
            # Backslashes and drive prefixes are paths on Windows too.
            path = Path(member.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name
                    or not (member.isfile() or member.isdir())
                    or not (destination/path).resolve().is_relative_to(destination)):
                raise VPError('更新源码包包含不安全的路径或链接，原版本未修改。')
        for member in members:
            target = destination/member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.extractfile(member) as reader, target.open('xb') as writer:
                    shutil.copyfileobj(reader,writer)
                target.chmod(0o600 | (member.mode & 0o111))
    roots = [destination, *[p for p in destination.iterdir() if p.is_dir()]]
    matches = [p for p in roots if (p/'install.py').is_file() and (p/'install.sh').is_file()]
    if len(matches) != 1:
        raise VPError('更新源码包缺少唯一安装入口，原版本未修改。')
    return matches[0]


def cloud_update(data):
    print('正在检查 VP 云端最新版本…', flush=True)
    response = _download(CLOUD_SERVER + '/api/client/releases/latest')
    try:
        manifest = response.json()['data']
    except (ValueError, KeyError, TypeError):
        raise VPError('云端更新清单无效，原版本未修改。') from None
    finally:
        response.close()
    if not isinstance(manifest, dict):
        raise VPError('云端更新清单必须为对象，原版本未修改。')
    revision = manifest.get('revision','')
    checksum = manifest.get('sha256','')
    if (not isinstance(revision,str) or not isinstance(checksum,str)
            or not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', revision)
            or not re.fullmatch(r'[0-9a-f]{64}',checksum)
            or manifest.get('sourceUrl') != CLOUD_SERVER + '/downloads/terminal/' + revision + '.tar.gz'):
        raise VPError('云端更新清单的来源或校验值无效，原版本未修改。')
    key = hashlib.sha256(data['install_root'].encode()).hexdigest()[:16]
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'vp/updates'/key
    with update_lock(cache/'update.lock'):
        if revision == data.get('source_revision'):
            print('已是最新版本（' + revision[:8] + '）。')
            return
        with tempfile.TemporaryDirectory(prefix='cloud-source-',dir=cache) as temporary:
            folder = Path(temporary)
            archive = folder/'release.tar.gz'
            response = _download(manifest['sourceUrl'], stream=True)
            digest = hashlib.sha256()
            total = 0
            try:
                with archive.open('wb') as target:
                    for chunk in response.iter_content(1024*1024):
                        total += len(chunk)
                        if total > 512*1024*1024:
                            raise VPError('更新源码压缩包过大，原版本未修改。')
                        digest.update(chunk)
                        target.write(chunk)
            except requests.RequestException:
                raise CloudUnavailable('云端源码下载未完成。') from None
            finally:
                response.close()
            if digest.hexdigest() != checksum:
                raise VPError('更新源码 SHA256 校验失败，原版本未修改。')
            try:
                source = safe_extract(archive, folder/'source')
            except tarfile.TarError:
                raise VPError('更新源码包格式无效，原版本未修改。') from None
            log = cache/'last-update.log'
            print('正在安装云端新版本，保留原安装路径、账号和比赛数据…', flush=True)
            try:
                with log.open('w') as output:
                    log.chmod(0o600)
                    subprocess.run(installer_command(source,data), stdout=output, stderr=subprocess.STDOUT,
                                   check=True, timeout=1800, env={**os.environ,'XCPC_SOURCE_REVISION':revision})
            except (subprocess.SubprocessError, OSError):
                raise VPError('更新安装未完成，请查看日志：' + str(log)) from None
        print('更新完成（' + revision[:8] + '）。查看帮助：xcpc --help')


def update(*, repository: str = REPOSITORY, cloud: bool = False, gitee: bool = False) -> None:
    data = installation()
    if cloud and not gitee and repository == REPOSITORY:
        try:
            cloud_update(data)
            return
        except CloudUnavailable:
            print("云端更新暂不可用，正在尝试 GitHub…", flush=True)
    ensure_git()
    key = hashlib.sha256(data["install_root"].encode()).hexdigest()[:16]
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home()/".cache"))/"vp/updates"/key
    with update_lock(cache/"update.lock"):
        bare = cache/"repository.git"
        if not bare.exists():
            _git(["init", "--bare", str(bare)])
            _git(["--git-dir", str(bare), "remote", "add", "origin", repository])
        previous_repository = _git(["--git-dir", str(bare), "remote", "get-url", "origin"])
        if previous_repository != repository:
            if repository == REPOSITORY and previous_repository in (LEGACY_REPOSITORY, LEGACY_GITHUB_REPOSITORY):
                # Preserve existing worktrees and local edits while moving only
                # this project's verified legacy cache to its new official origin.
                _git(["--git-dir", str(bare), "remote", "set-url", "origin", repository])
            else:
                raise VPError("更新缓存的仓库地址不匹配，已停止更新；原版本未改动。")
        print("正在检查 GitHub 最新版本…", flush=True)
        _git(["--git-dir", str(bare), "fetch", "--depth", "1", "origin", "main"])
        revision = _git(["--git-dir", str(bare), "rev-parse", "FETCH_HEAD"])
        if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision):
            raise VPError("远程版本编号无效；原版本未改动。")
        if revision == data.get("source_revision"):
            print("已是最新版本（" + revision[:8] + "）。")
            return
        source = cache/"sources"/revision
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            if (_git(["rev-parse", "HEAD"], cwd=source) != revision
                    or _git(["status", "--porcelain"], cwd=source)):
                raise VPError("更新缓存中存在本地改动，未覆盖：" + str(source))
        else:
            _git(["--git-dir", str(bare), "worktree", "add", "--detach", str(source), revision])
        if not (source / ('install.py' if data.get('portable') else 'install.sh')).is_file():
            raise VPError("最新代码缺少安装器；原版本未改动。")
        log = cache/"last-update.log"
        print("正在安装新版本，账号、保存目录及比赛记录将保留…", flush=True)
        try:
            with log.open("w") as output:
                log.chmod(0o600)
                subprocess.run(installer_command(source, data), stdout=output, stderr=subprocess.STDOUT,
                               check=True, timeout=1800, env={**os.environ, 'XCPC_SOURCE_REVISION': revision})
        except (subprocess.SubprocessError, OSError):
            raise VPError("更新安装未完成，请查看日志：" + str(log)) from None
        print("更新完成（" + revision[:8] + "）。查看帮助：xcpc --help")


def main() -> None:
    try:
        args = sys.argv[1:]
        if args not in ([], ["--github"], ["--cloud"], ["--gitee"]):
            raise VPError("Usage: xcpc update [--github | --cloud]")
        if args == ["--cloud"]:
            update(cloud=True)
        elif args == ["--gitee"]:
            print("更新源已迁移到 GitHub，正在使用 GitHub 更新。", flush=True)
            update(gitee=True)
        else:
            update()
    except (VPError, OSError) as exc:
        print("xcpc update: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
