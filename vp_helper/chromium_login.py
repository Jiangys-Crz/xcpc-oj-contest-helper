"""Normal-profile Chrome/Edge login snapshots, delivered by the bundled extension."""
import base64
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import sys

from .storage import config_directory, read_json, state_directory, write_json
from .browser_sites import BASES, PARENTS, canonical_host, group_domains, first_party
from .models import VPError

HOST = 'com.xcpc.browser'
NAMES = {'chrome': 'Google Chrome', 'edge': 'Microsoft Edge'}


def enabled():
    return sys.platform in ('darwin', 'win32')


def extension_source():
    bundled = Path(sys.prefix).parent / 'browser_extension'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent.parent / 'browser_extension'


def extension_id():
    key = read_json(extension_source() / 'manifest.json')['key']
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(c, 16)) for c in digest)


def ensure_user_bridge(*, force=False):
    """A shared program registers only the current user's private browser bridge."""
    if not enabled():
        return
    installed = read_json(Path(sys.prefix).parent / 'installation.json')
    if not installed.get('global'):
        return
    marker = config_directory() / 'browser-bridge/user-setup.json'
    expected = {'installRoot': installed['install_root'], 'revision': installed.get('source_revision', '')}
    launcher = marker.parent / ('host.exe' if sys.platform == 'win32' else 'host')
    extension = config_directory() / 'browser-extension/manifest.json'
    if not force and read_json(marker) == expected and launcher.is_file() and extension.is_file():
        return
    try:
        register_bridge()
        write_json(marker, expected)
    except (OSError, ValueError) as exc:
        raise VPError('无法配置当前用户的浏览器扩展连接；请检查个人配置目录权限：' + str(config_directory())) from exc


def register_bridge():
    if not enabled():
        return None
    root = config_directory() / 'browser-bridge'
    root.mkdir(parents=True, exist_ok=True)
    extension = config_directory() / 'browser-extension'
    shutil.copytree(extension_source(), extension, dirs_exist_ok=True)
    launcher = root / ('host.exe' if sys.platform == 'win32' else 'host')
    installation = read_json(Path(sys.prefix).parent / 'installation.json')
    if not installation.get('portable'):
        raise ValueError('Install the native XCPC application before registering its browser bridge.')
    install_root = Path(installation['install_root'])
    bootstrap = root / 'host.py'
    bootstrap.write_text('import json,os,sys\nfrom pathlib import Path\nroot=Path(' + repr(str(install_root)) + ')\n'
                         'release=Path(json.loads((root/"current.json").read_text(encoding="utf8"))["release"])\n'
                         'assert release.resolve().is_relative_to((root/"releases").resolve())\n'
                         'python=release/"venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")\n'
                         'os.execv(str(python),[str(python),"-u","-m","vp_helper.browser_bridge",*sys.argv[1:]])\n', encoding='utf8')
    base_python = installation.get('python') or getattr(sys, '_base_executable', sys.executable)
    if sys.platform == 'win32':
        initial = read_json(root / 'host-installation.json').get('release', '')
        if not launcher.is_file() or not initial or not Path(initial).is_dir():
            from pip._vendor.distlib.scripts import ScriptMaker
            maker = ScriptMaker(None, str(root))
            maker.variants = {''}
            maker.clobber = True
            maker.make('host = vp_helper.bridge_bootstrap:main')
            write_json(root / 'host-installation.json', {'release': str(Path(sys.prefix).parent)})
    else:
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(base_python)) + ' ' + shlex.quote(str(bootstrap)) + ' "$@"\n')
        launcher.chmod(0o700)
    manifest = {'name': HOST, 'description': 'XCPC judge login bridge', 'path': str(launcher),
                'type': 'stdio', 'allowed_origins': ['chrome-extension://' + extension_id() + '/']}
    if sys.platform == 'win32':
        import winreg
        destination = root / (HOST + '.json')
        write_json(destination, manifest)
        for vendor in (r'Google\Chrome', r'Microsoft\Edge'):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Software\\' + vendor + '\\NativeMessagingHosts\\' + HOST) as key:
                winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(destination))
    else:
        for vendor in ('Google/Chrome', 'Microsoft Edge'):
            destination = Path.home() / 'Library/Application Support' / vendor / 'NativeMessagingHosts' / (HOST + '.json')
            write_json(destination, manifest)
    return extension


def cookies(platform, account, *, errors=None):
    ensure_user_bridge()
    from .browser_login import BrowserCookies, _cookie_values, _header
    import time
    candidates = []
    root = state_directory() / 'browser-login'
    paths = [p for kind in NAMES for p in (root / kind).glob('*.json')]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        snapshot = read_json(path)
        kind = snapshot.get('source')
        if kind not in NAMES:
            continue
        seen = set()
        available = 0
        for base in BASES[platform]:
            target = account.get('_browser_target')
            if target and canonical_host(target) != base:
                continue
            items = [c for c in snapshot.get('cookies', [])
                     if c.get('domain') in group_domains(base) and c.get('path') == '/' and first_party(c)
                     and (c.get('expires', -1) <= 0 or c['expires'] > time.time())]
            available += len(items)
            for partitioned in (False, True):
                rows = [(c['name'], c['value'], c['domain']) for c in sorted(items, key=lambda c: bool(c.get('partitionKey')))
                        if not c.get('partitionKey') or partitioned]
                for host in (base, 'www.' + base, PARENTS.get(base, base), 'www.' + PARENTS.get(base, base)):
                    header = _header(_cookie_values(rows, host))
                    if header and (base, header) not in seen:
                        candidates.append(BrowserCookies(path, header, kind, snapshot.get('user_agent', ''), base))
                        seen.add((base, header))
        if errors is not None:
            errors.append(f"收到 {NAMES[kind]} 配置 {snapshot.get('profile_id', '')[-8:]} 的同步；该站可用 Cookie：{available} 项。")
            if any(host in group_domains(base) or '.' + host in group_domains(base)
                   for base in BASES[platform] for host in snapshot.get('read_errors', [])):
                errors.append('此站点 Cookie 读取失败或未授权。请在扩展详情中允许访问此比赛网站，再同步。')
    if not candidates and errors is not None:
        errors.append('未收到此站点登录信息。请确认登录页与 XCPC 扩展处于同一浏览器用户配置，并在扩展详情允许访问此站点后同步。')
    return candidates


if __name__ == '__main__':
    print('浏览器扩展文件夹：' + str(register_bridge()))
