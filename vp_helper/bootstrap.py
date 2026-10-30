"""Stable native command shims resolve the current release without replacing running EXEs."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

MODULES = {'xcpc':'commands','vp':'cli','live':'live','bt':'practice','submit':'submit','printfile':'printfile'}


def launch_python(python, arguments, *, native_host=False):
    """Use CreateProcess on Windows; retain exec semantics on POSIX."""
    python = str(python)
    if sys.platform != 'win32':
        return os.execv(python, [python, *arguments])
    try:
        options = {'creationflags': 0x08000000} if native_host else {}
        result = subprocess.run([python, '-X', 'utf8', *arguments], **options)
    except OSError as exc:
        detail = getattr(exc, 'winerror', None) or exc.errno
        raise SystemExit(f'XCPC could not start its Python runtime (Windows error {detail}). '
                         f'Rerun install.ps1 to repair XCPC. Runtime: {python}') from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(result.returncode)


def main():
    if sys.platform == 'win32':
        os.environ['PYTHONUTF8'] = '1'
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8')
    name = Path(sys.argv[0]).stem
    if name not in MODULES:
        raise SystemExit('Unknown XCPC command')
    installation = json.loads((Path(sys.prefix).parent / 'installation.json').read_text(encoding='utf8'))
    root = Path(installation['install_root'])
    release = Path(json.loads((root / 'current.json').read_text(encoding='utf8'))['release'])
    if not release.resolve().is_relative_to((root / 'releases').resolve()):
        raise SystemExit('Invalid XCPC installation pointer')
    commands = [installation['bin_directory']]
    if installation.get('global') and sys.platform == 'darwin':
        for prefix in ('/opt/homebrew', '/usr/local'):
            if (Path(prefix) / 'bin/brew').is_file():
                commands += [prefix + '/bin', prefix + '/sbin']
                break
    os.environ['PATH'] = os.pathsep.join([*commands, os.environ.get('PATH', '')])
    if release.resolve() != Path(sys.prefix).parent.resolve():
        python = release / 'venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        return launch_python(python, ['-I', '-m', 'vp_helper.' + MODULES[name], *sys.argv[1:]])
    importlib.import_module('vp_helper.' + MODULES[name]).main()


if __name__ == '__main__':
    main()
