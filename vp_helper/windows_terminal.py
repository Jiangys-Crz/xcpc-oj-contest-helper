"""Native Windows dashboard + command pane, with no tmux or WSL dependency."""
import atexit
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from .storage import file_lock, read_json, write_json


_monitors = {}


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


def track_monitor(path, *, layout=None):
    if sys.platform != 'win32':
        return lambda: None
    path = Path(path).resolve()
    if path in _monitors:
        return _monitors[path]['cleanup']
    stamp = {'pid': os.getpid(), 'token': process_token(os.getpid()),
             'layout': layout or 'legacy'}
    target = path.with_suffix('.windows-monitor')
    request = path.with_suffix('.windows-reload')
    entry = {'generation': read_json(request).get('id')}
    write_json(target, stamp)
    def cleanup():
        with file_lock(path.with_suffix('.windows-control.lock')):
            if read_json(target) == stamp:
                target.unlink(missing_ok=True)
                request.unlink(missing_ok=True)
        if _monitors.get(path) is entry:
            _monitors.pop(path, None)
        atexit.unregister(cleanup)
    entry['cleanup'] = cleanup
    _monitors[path] = entry
    atexit.register(cleanup)
    return cleanup


def monitor_current(path):
    """A fresh request closes curses normally, then the same pane reloads it."""
    entry = _monitors.get(Path(path).resolve())
    return entry is None or entry['generation'] == read_json(Path(path).with_suffix('.windows-reload')).get('id')


def view_options(path):
    return {'continue_monitor': lambda: monitor_current(path)} if sys.platform == 'win32' else {}


def process_parents():
    """Read only PIDs/parent PIDs; no process command lines or credentials."""
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return {}
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        parents = {}
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            parents[entry.pid] = entry.parent
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return parents
    finally:
        kernel.CloseHandle(snapshot)


def foreground_terminal():
    """Split only an existing foreground Terminal that owns this process.

    ConPTY's GetConsoleWindow is an invisible message-only window, so use the
    Terminal GUI's class and process ancestry instead. A stale WT_SESSION in
    an unrelated console must never launch another window through `wt -w 0`.
    """
    if sys.platform != 'win32' or not os.environ.get('WT_SESSION'):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        window = user.GetForegroundWindow()
        name = ctypes.create_unicode_buffer(128)
        if not (window and user.GetClassNameW(window, name, len(name))
                and name.value == 'CASCADIA_HOSTING_WINDOW_CLASS'):
            return False
        owner = wintypes.DWORD()
        if not user.GetWindowThreadProcessId(window, ctypes.byref(owner)) or not owner.value:
            return False
        parents = process_parents()
        process = os.getpid(); seen = set()
        while process and process not in seen:
            if process == owner.value:
                return True
            seen.add(process); process = parents.get(process)
        return False
    except (AttributeError, OSError, ValueError):
        return False


def open_dashboard(path, state):
    from .cli import monitor
    path = Path(path).resolve()
    with file_lock(path.with_suffix('.windows-control.lock')):
        active = read_json(path.with_suffix('.windows-monitor'))
        if active.get('pid') and active.get('token') is not None and process_token(active['pid']) == active['token']:
            if active.get('layout') == 'current':
                if state.get('_reload_monitor') or state.get('practice_view'):
                    write_json(path.with_suffix('.windows-reload'), {'id': uuid4().hex})
                print('比赛面板已在原终端中显示：' + state['directory'])
                return
            # Migrate an older independent monitor, after checking PID identity.
            process_token(active['pid'], terminate=active['token'])
        with file_lock(path.with_suffix('.monitor.lock'), nonblocking=True):
            pass
        # Register before any network wait, including a scheduled VP start.
        cleanup = track_monitor(path, layout='current')
    previous = Path.cwd()
    split_shell = False
    try:
        os.chdir(state['directory'])
        # WT has an additional semicolon action parser after CreateProcess
        # quoting. Preserve literal paths by using this console in that case.
        literal_paths = (state['directory'], sys.executable, str(path))
        terminal = shutil.which('wt.exe') if not any(';' in p for p in literal_paths) and foreground_terminal() else None
        if terminal:
            command = [terminal, '-w', '0', 'split-pane', '-H', '-s', '0.35', '--title', 'XCPC commands',
                       '-d', state['directory'], sys.executable, '-m', 'vp_helper.windows_terminal', 'shell', str(path)]
            try:
                subprocess.run(command, check=True, timeout=15)
                split_shell = True
            except (OSError, subprocess.SubprocessError):
                # Do not retry an uncertain split or open another terminal.
                print('无法分屏；在当前终端显示比赛面板，按 q 返回。')
        else:
            print('在当前终端显示比赛面板，按 q 返回后可提交代码。')
        while True:
            monitor(path)
            entry = _monitors.get(path)
            generation = read_json(path.with_suffix('.windows-reload')).get('id')
            if entry is None or entry['generation'] == generation:
                break
            entry['generation'] = generation
    finally:
        try:
            os.chdir(previous)
        finally:
            cleanup()
    if not split_shell and previous != Path(state['directory']).resolve():
        # A child cannot change its caller's PowerShell directory. Reuse this
        # console for a shell in the contest folder after q; exit returns to
        # the original caller. Reopening from that folder needs no extra shell.
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
