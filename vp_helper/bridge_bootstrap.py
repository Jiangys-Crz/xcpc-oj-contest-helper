"""Windows Native Messaging EXE follows the application's current release."""
import json
import os
from pathlib import Path
import sys


def main():
    installation = json.loads((Path(sys.prefix).parent / 'installation.json').read_text(encoding='utf8'))
    root = Path(installation['install_root'])
    release = Path(json.loads((root / 'current.json').read_text(encoding='utf8'))['release'])
    if not release.resolve().is_relative_to((root / 'releases').resolve()):
        raise SystemExit(1)
    if release.resolve() != Path(sys.prefix).parent.resolve():
        python = release / 'venv/Scripts/python.exe'
        return os.execv(str(python), [str(python), '-u', '-m', 'vp_helper.browser_bridge', *sys.argv[1:]])
    from .browser_bridge import main as host
    host()
