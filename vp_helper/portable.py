"""Small native-platform boundaries; Linux keeps its existing tmux/install paths."""
import os
from pathlib import Path
import sys


def login_browser():
    return 'Chrome / Edge' if sys.platform in ('darwin', 'win32') else 'Firefox'


def scripts_directory():
    return Path(sys.prefix) / ('Scripts' if sys.platform == 'win32' else 'bin')


def installed_command(name):
    return scripts_directory() / (name + '.exe' if sys.platform == 'win32' else name)


def portable_installation():
    from .storage import read_json
    return read_json(Path(sys.prefix).parent / 'installation.json').get('portable', False)


def bundled_submit(script, selected=None):
    from .storage import read_json, state_directory
    path = Path(script) if script else None
    if not path:
        return False
    if path.is_symlink() and Path(os.readlink(path)) == (selected or state_directory() / 'submit'):
        return True
    if not path.is_file():
        return False
    data = read_json(Path(sys.prefix).parent / 'installation.json')
    return bool(data.get('portable') and path.parent.resolve() == Path(data['bin_directory']).resolve()
                and path.stem == 'submit')


def bind_submit(tool, selected=None):
    if portable_installation():
        return
    from .storage import state_directory
    selected = selected or state_directory() / 'submit'
    temporary = selected.with_name('submit.new')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(tool)
    os.replace(temporary, selected)
