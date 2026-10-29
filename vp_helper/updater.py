"""Update the installed application without changing the user's checkout."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .models import VPError
from .storage import read_json

REPOSITORY = "https://gitee.com/Zyihan_Crz/xcpc-vp-system.git"


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
        raise VPError("获取更新代码失败。请检查网络连接及 Gitee 仓库访问权限；原版本未改动。") from None


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
        return [python, str(source / 'install.py'), '--target-root', data['install_root'],
                '--target-bin', data['bin_directory']]
    command = ["bash", str(source/"install.sh"), "--python", python,
               "--target-root", data["install_root"], "--target-bin", data["bin_directory"]]
    if data["local"]:
        command.append("--local")
    if data.get("submit_path"):
        command.append(data["submit_path"])
    return command


def update(*, repository: str = REPOSITORY) -> None:
    data = installation()
    ensure_git()
    key = hashlib.sha256(data["install_root"].encode()).hexdigest()[:16]
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home()/".cache"))/"vp/updates"/key
    with update_lock(cache/"update.lock"):
        bare = cache/"repository.git"
        if not bare.exists():
            _git(["init", "--bare", str(bare)])
            _git(["--git-dir", str(bare), "remote", "add", "origin", repository])
        if _git(["--git-dir", str(bare), "remote", "get-url", "origin"]) != repository:
            raise VPError("更新缓存的仓库地址不匹配，已停止更新；原版本未改动。")
        print("正在检查 Gitee 最新版本…", flush=True)
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
                               check=True, timeout=1800)
        except (subprocess.SubprocessError, OSError):
            raise VPError("更新安装未完成，请查看日志：" + str(log)) from None
        print("更新完成（" + revision[:8] + "）。查看帮助：xcpc --help")


def main() -> None:
    try:
        update()
    except (VPError, OSError) as exc:
        print("xcpc update: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
