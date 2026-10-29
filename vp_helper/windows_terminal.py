"""Native Windows dashboard + command pane, with no tmux or WSL dependency."""
import atexit
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .storage import read_json, write_json


def process_token(pid, *, terminate=None):
    if type(pid) is not int or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | (0x100001 if terminate is not None else 0), False, pid)
    if not handle:
        return None
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, system, user))):
            return None
        if exited.dwLowDateTime or exited.dwHighDateTime:
            return None
        token = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if terminate is not None and token == terminate:
            if kernel.TerminateProcess(handle, 0):
                kernel.WaitForSingleObject(handle, 1000)
        return token
    finally:
        kernel.CloseHandle(handle)


def track_monitor(path):
    if sys.platform != 'win32':
        return
    stamp = {'pid': os.getpid(), 'token': process_token(os.getpid())}
    target = path.with_suffix('.windows-monitor')
    write_json(target, stamp)
    def cleanup():
        if read_json(target) == stamp:
            target.unlink(missing_ok=True)
    atexit.register(cleanup)


def open_dashboard(path, state):
    target = path.with_suffix('.windows-monitor')
    active = read_json(target)
    if active.get('pid') and active.get('token') is not None and process_token(active['pid']) == active['token']:
        if not state.get('_reload_monitor') and not state.get('practice_view'):
            print('比赛面板已打开：' + state['directory'])
            return
        # Only this recorded monitor process, verified by its creation time.
        process_token(active['pid'], terminate=active['token'])
    directory = state['directory']
    monitor = [sys.executable, '-m', 'vp_helper.cli', '_monitor', str(path)]
    terminal = shutil.which('wt.exe')
    if terminal:
        window = 'xcpc-' + path.stem
        command = [terminal, '-w', window, 'new-tab', '--title', 'XCPC', '-d', directory,
                   *monitor, ';', 'split-pane', '-H', '-s', '0.35', '-d', directory,
                   sys.executable, '-m', 'vp_helper.windows_terminal', 'shell', str(path)]
        try:
            subprocess.run(command, check=True, timeout=15)
            print('已打开比赛窗口，上方为详情，下方为比赛目录中的命令终端。')
            return
        except (OSError, subprocess.SubprocessError):
            pass
    subprocess.Popen(monitor, cwd=directory, creationflags=subprocess.CREATE_NEW_CONSOLE)
    print('详情已在独立终端打开；当前终端进入比赛目录。输入 exit 返回。')
    command_shell(path)


def command_shell(path):
    state = read_json(path)
    os.chdir(state['directory'])
    data = read_json(Path(sys.prefix).parent / 'installation.json')
    if data.get('bin_directory'):
        os.environ['PATH'] = data['bin_directory'] + os.pathsep + os.environ.get('PATH', '')
    shell = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    args = [shell, '-NoLogo', '-NoExit'] if shell else [os.environ.get('COMSPEC', 'cmd.exe'), '/K']
    subprocess.run(args, check=False)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'shell':
        command_shell(Path(sys.argv[2]))
