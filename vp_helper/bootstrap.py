"""Stable native command shims resolve the current release without replacing running EXEs."""
import importlib
import json
import os
from pathlib import Path
import sys

MODULES = {'xcpc':'commands','vp':'cli','live':'live','bt':'practice','submit':'submit'}


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
    os.environ['PATH'] = installation['bin_directory'] + os.pathsep + os.environ.get('PATH', '')
    if release.resolve() != Path(sys.prefix).parent.resolve():
        python = release / 'venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        return os.execv(str(python), [str(python), '-m', 'vp_helper.' + MODULES[name], *sys.argv[1:]])
    importlib.import_module('vp_helper.' + MODULES[name]).main()


if __name__ == '__main__':
    main()
