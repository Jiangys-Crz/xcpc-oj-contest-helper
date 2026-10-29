#!/usr/bin/env python3
"""Per-user native macOS / Windows installer. Ubuntu continues to use install.sh."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import venv


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


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
    default = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'XCPC' if windows else Path.home() / 'Library/Application Support/XCPC'
    root = Path(args.target_root).expanduser().resolve() if args.target_root else default / 'app'
    commands = Path(args.target_bin).expanduser().resolve() if args.target_bin else (default / 'bin' if windows else Path.home() / '.local/bin')
    root.joinpath('releases').mkdir(parents=True, exist_ok=True)
    commands.mkdir(parents=True, exist_ok=True)
    marker = commands / '.xcpc-native-commands.json'
    if marker.exists():
        previous = json.loads(marker.read_text(encoding='utf8'))
        if previous.get('owner') != 'xcpc-native' or Path(previous.get('install_root', '')).resolve() != root:
            raise SystemExit('The command directory belongs to another installation; it was preserved.')
    if not marker.exists():
        for name in ('xcpc','vp','live','bt','submit'):
            if (commands / (name + '.exe' if windows else name)).exists():
                raise SystemExit('Existing command was preserved: ' + str(commands / name))
    release = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%dT%H%M%S')+'-', dir=root / 'releases'))
    published = False
    new_commands = not marker.exists()
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
        metadata = {'install_root':str(root),'bin_directory':str(commands),'local':True,'portable':True,
                    'source_directory':str(source),'source_revision':revision,'python':sys.executable,'submit_path':''}
        (release / 'installation.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2), encoding='utf8')
        if not args.skip_browser_registration:
            run([python, '-m', 'vp_helper.chromium_login'])
        # Windows EXEs bootstrap through current.json, so an update need not replace a running command.
        if not marker.exists():
            if windows:
                generator = "from pip._vendor.distlib.scripts import ScriptMaker; import sys; m=ScriptMaker(None,sys.argv[1]); m.variants={''}; [m.make(n+' = vp_helper.bootstrap:main') for n in ('xcpc','vp','live','bt','submit')]"
                run([python, '-c', generator, commands])
            else:
                for name in ('xcpc','vp','live','bt','submit'):
                    entry = commands / name
                    entry.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' -c ' + shlex.quote(
                        'import sys; sys.argv[0]='+repr(name)+'; from vp_helper.bootstrap import main; main()') + ' "$@"\n')
                    entry.chmod(0o755)
            marker.write_text(json.dumps({'owner':'xcpc-native','initial_release':str(release),'install_root':str(root)},ensure_ascii=False),encoding='utf8')
        temporary = root / '.current-new.json'
        temporary.write_text(json.dumps({'release':str(release)},ensure_ascii=False),encoding='utf8')
        os.replace(temporary, root / 'current.json')
        published = True
        print('XCPC installed. Add this folder to PATH: ' + str(commands))
        print('Load the XCPC login extension in your normal Chrome / Edge profile; see README.')
        if not windows:
            print('export PATH=' + shlex.quote(str(commands)) + ':"$PATH"')
    finally:
        if not published:
            if new_commands:
                for name in ('xcpc','vp','live','bt','submit'):
                    (commands / (name + '.exe' if windows else name)).unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
            shutil.rmtree(release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-root')
    parser.add_argument('--target-bin')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--skip-browser-registration', action='store_true', help=argparse.SUPPRESS)
    install(parser.parse_args())
