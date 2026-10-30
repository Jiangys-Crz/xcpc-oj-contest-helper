#!/usr/bin/env python3
"""Per-user native macOS / Windows installer. Ubuntu continues to use install.sh."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import stat
import sys
import tempfile
import time
import venv


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def replace_file(path, data, *, mode=None):
    descriptor, name = tempfile.mkstemp(prefix=path.name+'.xcpc-repair-',dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data)
            if mode is not None:
                os.fchmod(stream.fileno(),mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_windows_bootstraps(root, releases, source):
    """Repair owned Python modules, leaving loaded command / host EXEs intact."""
    backups = {}
    try:
        for initial in {Path(p).resolve() for p in releases if p}:
            if not initial.is_relative_to((root/'releases').resolve()):
                raise RuntimeError('The initial launcher release is outside this installation; it was preserved.')
            metadata = json.loads((initial/'installation.json').read_text(encoding='utf8'))
            if not metadata.get('portable') or Path(metadata.get('install_root','')).resolve() != root.resolve():
                raise RuntimeError('The initial launcher belongs to another installation; it was preserved.')
            package = initial/'venv/Lib/site-packages/vp_helper'
            cache = package/'__pycache__'
            if not package.resolve().is_relative_to(initial) or not cache.resolve().is_relative_to(initial):
                raise RuntimeError('The launcher package or cache points outside its managed release; it was preserved.')
            for name in ('bootstrap.py','bridge_bootstrap.py'):
                target = package/name
                if target.is_symlink():
                    raise RuntimeError('A launcher module is a custom symlink; it was preserved.')
                backups[target] = target.read_bytes()
                replace_file(target, (source/name).read_bytes())
                for cached in cache.glob(name[:-3]+'.*.pyc'):
                    cached.unlink(missing_ok=True)
        return backups
    except BaseException:
        restore_bootstraps(backups)
        raise


def restore_bootstraps(backups):
    for path, data in backups.items():
        replace_file(path, data)


def make_runtime_readable(root):
    """Publish application files for all users without following external links.

    The online bootstrap uses umask 077 for downloads. A global Ubuntu install
    must expose its runtime and copied source, while account data stays private
    in separate user configuration directories.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('Runtime permissions require a real application directory.')
    root_owned = getattr(os,'geteuid',lambda:-1)() == 0
    for directory, folders, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if root_owned:
            os.chown(current,0,0,follow_symlinks=False)
        current.chmod(((stat.S_IMODE(current.stat().st_mode)&0o777)|0o555)&~0o022)
        for name in folders:
            path=current/name
            if root_owned and path.is_symlink():
                os.chown(path,0,0,follow_symlinks=False)
        for name in files:
            path = current/name
            if root_owned:
                os.chown(path,0,0,follow_symlinks=False)
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)&0o777
            path.chmod((mode | 0o444 | (0o111 if mode & 0o111 else 0))&~0o022)


def configure_macos_path(commands, *, home=None, shell=None, all_shells=False):
    """Append one owned PATH block without replacing the user's shell settings."""
    explicit_home = home is not None
    home = Path(home) if explicit_home else Path.home()
    shell = shell or os.environ.get('SHELL', '/bin/zsh')
    shells = {'bash', 'zsh'} if all_shells else set()
    shells.add(Path(shell).name)
    files = []
    if 'bash' in shells:
        profile = next((home/name for name in ('.bash_profile','.bash_login','.profile') if (home/name).is_file()), home/'.bash_profile')
        files += [profile, home/'.bashrc']
    if 'zsh' in shells:
        dotdir = home if explicit_home else Path(os.environ.get('ZDOTDIR') or home).expanduser()
        files += [dotdir/'.zprofile', dotdir/'.zshrc']
    if 'fish' in shells:
        config = home/'.config' if explicit_home else Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config')
        files += [config/'fish/conf.d/xcpc-path.fish']
    if not files:
        return []
    directories = [Path(commands)]
    if sys.platform == 'darwin':
        for prefix in (os.environ.get('HOMEBREW_PREFIX'), '/opt/homebrew', '/usr/local'):
            if prefix and (Path(prefix)/'bin/brew').is_file():
                directories += [Path(prefix)/'bin', Path(prefix)/'sbin']
                break
    lines = ['# >>> XCPC command path >>>']
    for directory in reversed(directories):
        quoted = shlex.quote(str(directory))
        lines += ['case "$PATH" in', f'  {quoted}|{quoted}:*) ;;',
                  f'  *) export PATH={quoted}:"$PATH" ;;', 'esac']
    lines += ['# <<< XCPC command path <<<', '']
    block = '\n'.join(lines).encode('utf8')
    fish_lines = ['# >>> XCPC command path >>>']
    for directory in reversed(directories):
        quoted = "'" + str(directory).replace('\\', '\\\\').replace("'", "\\'") + "'"
        fish_lines += [f'if test "$PATH[1]" != {quoted}', f'  set -gx PATH {quoted} $PATH', 'end']
    fish_lines += ['# <<< XCPC command path <<<', '']
    fish_block = '\n'.join(fish_lines).encode('utf8')
    marker = re.compile(rb'^# >>> XCPC command path >>>\n.*?^# <<< XCPC command path <<<\n?', re.M | re.S)
    for file in files:
        target = file.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = target.read_bytes() if target.exists() else b''
        preserved = marker.sub(b'', previous).rstrip(b'\n')
        data = preserved + (b'\n\n' if preserved else b'') + (fish_block if file.suffix == '.fish' else block)
        if previous != data:
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            replace_file(target,data)
            target.chmod(mode)
    return files


def installation_targets(args, *, windows=None):
    windows = sys.platform == 'win32' if windows is None else windows
    personal = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'XCPC' if windows else Path.home() / 'Library/Application Support/XCPC'
    shared = Path(os.environ.get('ProgramW6432') or os.environ.get('ProgramFiles') or 'C:/Program Files') / 'XCPC' if windows else Path('/Library/Application Support/XCPC')
    shared_commands = shared/'bin' if windows else Path('/usr/local/bin')
    target_root = Path(args.target_root).expanduser().resolve() if args.target_root else None
    target_bin = Path(args.target_bin).expanduser().resolve() if args.target_bin else None
    explicit = bool(target_root or target_bin)
    global_target = not explicit or target_root == (shared/'app').resolve() or target_bin == shared_commands.resolve()
    default = shared if global_target else personal
    root = target_root or default/'app'
    commands = target_bin or (shared_commands if global_target else personal/'bin' if windows else Path.home()/'.local/bin')
    if (root == (shared/'app').resolve()) != (commands == shared_commands.resolve()):
        raise SystemExit('Do not mix shared and private installation targets. Use both system defaults, or two explicit private directories.')
    return root,commands,global_target,shared


def install(args):
    if sys.platform not in ('darwin', 'win32'):
        raise SystemExit('Ubuntu: use bash install.sh. This installer is for native macOS / Windows.')
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12 or newer is required; Python 3.13 is recommended.')
    source = Path(__file__).resolve().parent
    windows = sys.platform == 'win32'
    if windows:
        os.environ['PYTHONUTF8'] = '1'
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8')
    root,commands,global_install,shared = installation_targets(args,windows=windows)
    if global_install:
        if windows:
            import ctypes
            administrator = bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            administrator = os.geteuid() == 0
        if not administrator:
            raise SystemExit('A machine-wide XCPC installation requires administrator access. Run install.ps1 / install-macos.sh.')
        if not windows:
            os.umask(0o022)
    root.joinpath('releases').mkdir(parents=True, exist_ok=True)
    commands.mkdir(parents=True, exist_ok=True)
    if global_install and not windows:
        for folder in (root,root/'releases'):
            folder.chmod(stat.S_IMODE(folder.stat().st_mode)|0o555)
        if root.is_relative_to(shared):
            shared.chmod(stat.S_IMODE(shared.stat().st_mode)|0o555)
    marker = commands / '.xcpc-native-commands.json'
    if marker.exists():
        previous = json.loads(marker.read_text(encoding='utf8'))
        if previous.get('owner') != 'xcpc-native' or Path(previous.get('install_root', '')).resolve() != root:
            raise SystemExit('The command directory belongs to another installation; it was preserved.')
    if not marker.exists():
        for name in ('xcpc','vp','live','bt','submit','printfile'):
            if (commands / (name + '.exe' if windows else name)).exists():
                raise SystemExit('Existing command was preserved: ' + str(commands / name))
    release = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%dT%H%M%S')+'-', dir=root / 'releases'))
    published = False
    new_commands = not marker.exists()
    bootstrap_backups = {}
    added_printfile = False
    try:
        app = release / 'app'
        app.mkdir()
        shutil.copytree(source / 'vp_helper', app / 'vp_helper', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(source / 'pyproject.toml', app / 'pyproject.toml')
        for folder in ('third_party','browser_extension'):
            shutil.copytree(source / folder, release / folder)
        venv.EnvBuilder(with_pip=True).create(release / 'venv')
        scripts = release / 'venv' / ('Scripts' if windows else 'bin')
        python = scripts / ('python.exe' if windows else 'python')
        pip = [python, '-m', 'pip', 'install', '--disable-pip-version-check']
        packages = [app, 'selectolax', 'lxml', 'robobrowser', 'Werkzeug==0.16.1', 'prettytable<4']
        wheels = ['--find-links', source / 'wheels']
        try:
            run([*pip, '--no-index', *wheels, *packages])
        except subprocess.CalledProcessError:
            if args.offline:
                raise SystemExit('The offline wheels do not cover this OS / Python. Retry online without --offline.')
            run([*pip, '--prefer-binary', '--only-binary=cffi,curl-cffi,lxml,selectolax', *wheels, *packages])
        run([python, '-m', 'pip', 'check'])
        for module in ('commands','submit','live_submit','hydro_submit','nowcoder_submit'):
            run([python, '-m', 'vp_helper.'+module, '--help'])
        run([python, release / 'third_party/qoj/submit', '--help'])
        run([python, release / 'third_party/cf_submit/submit', '--help'])
        revision = ''
        if shutil.which('git'):
            try:
                revision = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
            except subprocess.CalledProcessError:
                pass
        if re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', os.environ.get('XCPC_SOURCE_REVISION','')):
            revision = os.environ['XCPC_SOURCE_REVISION']
        metadata = {'install_root':str(root),'bin_directory':str(commands),'local':not global_install,'global':global_install,'portable':True,
                    'source_directory':str(source),'source_revision':revision,'python':sys.executable,'submit_path':''}
        (release / 'installation.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2), encoding='utf8')
        if windows:
            from vp_helper.storage import config_directory
            initials = []
            if marker.exists():
                initials.append(previous.get('initial_release'))
            if not global_install:
                host_marker = config_directory()/'browser-bridge/host-installation.json'
                if host_marker.is_file():
                    host_initial = json.loads(host_marker.read_text(encoding='utf8')).get('release')
                    if host_initial and Path(host_initial).resolve().is_relative_to((root/'releases').resolve()):
                        initials.append(host_initial)
            bootstrap_backups = repair_windows_bootstraps(root, initials, source/'vp_helper')
            for initial in set(filter(None, initials)):
                old_python = Path(initial)/'venv/Scripts/python.exe'
                run([old_python, '-I', '-c',
                     'from vp_helper.bootstrap import launch_python; import sys; launch_python(sys.argv[1],sys.argv[2:])',
                     python, '-I', '-m', 'vp_helper.commands', '--help'], cwd=root)
        if not args.skip_browser_registration and not global_install:
            run([python, '-m', 'vp_helper.chromium_login'])
        # Windows EXEs bootstrap through current.json, so an update need not replace a running command.
        if not marker.exists():
            if windows:
                generator = "from pip._vendor.distlib.scripts import ScriptMaker; import sys; m=ScriptMaker(None,sys.argv[1]); m.variants={''}; [m.make(n+' = vp_helper.bootstrap:main') for n in ('xcpc','vp','live','bt','submit','printfile')]"
                run([python, '-c', generator, commands])
            else:
                for name in ('xcpc','vp','live','bt','submit','printfile'):
                    entry = commands / name
                    entry.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' -c ' + shlex.quote(
                        'import sys; sys.argv[0]='+repr(name)+'; from vp_helper.bootstrap import main; main()') + ' "$@"\n')
                    entry.chmod(0o755)
            marker.write_text(json.dumps({'owner':'xcpc-native','initial_release':str(release),'install_root':str(root),'global':global_install},ensure_ascii=False),encoding='utf8')
        print_entry = commands / ('printfile.exe' if windows else 'printfile')
        if not print_entry.exists():
            if windows:
                run([python, '-c', "from pip._vendor.distlib.scripts import ScriptMaker; import sys; m=ScriptMaker(None,sys.argv[1]); m.variants={''}; m.make('printfile = vp_helper.bootstrap:main')", commands])
            else:
                print_entry.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' -c ' + shlex.quote(
                    'import sys; sys.argv[0]="printfile"; from vp_helper.bootstrap import main; main()') + ' "$@"\n')
                print_entry.chmod(0o755)
            added_printfile = True
        if global_install and not windows:
            make_runtime_readable(release)
        if global_install and not windows:
            # macOS path_helper reads these entries for every user's login shell.
            machine_paths = ['/usr/local/bin']
            for prefix in ('/opt/homebrew','/usr/local'):
                if (Path(prefix)/'bin/brew').is_file():
                    machine_paths += [prefix+'/bin',prefix+'/sbin']
                    break
            path_file = Path('/etc/paths.d/xcpc-vp-system')
            path_file.parent.mkdir(parents=True,exist_ok=True)
            replace_file(path_file, ('\n'.join(dict.fromkeys(machine_paths))+'\n').encode('utf8'),mode=0o644)
        pointer=json.dumps({'release':str(release)},ensure_ascii=False).encode('utf8')
        if global_install and not windows:
            replace_file(root/'current.json',pointer,mode=0o644)
        else:
            replace_file(root/'current.json',pointer)
        published = True
        print('XCPC installed. Command directory: ' + str(commands))
        if global_install:
            print('Installed for all computer users. Account settings remain private to each user.')
            print('Run xcpc doctor cf in your own account to prepare the Chrome / Edge login extension.')
        else:
            print('Load the XCPC login extension in your normal Chrome / Edge profile; see README.')
        if not windows and not global_install:
            if not getattr(args, 'skip_path_configuration', False):
                try:
                    # The login shell can differ from Terminal / VS Code's
                    # shell. Configure both supported macOS startup families.
                    files = configure_macos_path(commands, all_shells=True)
                    if files:
                        print('已配置新终端的 PATH：' + '、'.join(map(str, files)))
                except (OSError, RuntimeError):
                    print('未能自动配置终端 PATH；请把下面一行加入你的 shell 启动文件。')
            print('当前终端请先执行下面一行；新打开的终端会自动生效：')
            print('export PATH=' + shlex.quote(str(commands)) + ':"$PATH"')
    finally:
        if not published:
            if added_printfile:
                (commands / ('printfile.exe' if windows else 'printfile')).unlink(missing_ok=True)
            restore_bootstraps(bootstrap_backups)
            if new_commands:
                for name in ('xcpc','vp','live','bt','submit','printfile'):
                    (commands / (name + '.exe' if windows else name)).unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
            shutil.rmtree(release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-root')
    parser.add_argument('--target-bin')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--skip-browser-registration', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--skip-path-configuration', action='store_true', help=argparse.SUPPRESS)
    install(parser.parse_args())
