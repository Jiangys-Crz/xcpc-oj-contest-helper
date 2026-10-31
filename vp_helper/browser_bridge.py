"""Native messaging: accept only the bundled extension's judge-cookie snapshots."""
import json
import math
import os
import re
import struct
import sys
import time

from .storage import state_directory, write_json
from .chromium_login import extension_id
from .browser_sites import DOMAINS, HOSTS, first_party


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError('Incomplete header')
    size = struct.unpack('<I', header)[0]
    if not 0 < size <= 1024 * 1024:
        raise ValueError('Invalid message size')
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Incomplete message')
    return json.loads(data)


def send_message(stream, value):
    data = json.dumps(value,ensure_ascii=False).encode('utf8')
    if len(data)>1024*1024:
        raise ValueError('Native message exceeded the supported size')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def snapshot_cookies(value, *, cf_only=False):
    cookies = []
    for c in value.get('cookies', []):
        if (not isinstance(c, dict) or c.get('domain') not in DOMAINS or c.get('path') != '/'
                or not first_party(c) or c.get('incognito')
                or not isinstance(c.get('name'), str) or not isinstance(c.get('value'), str)
                or any(ch in c['name'] + c['value'] for ch in '\r\n;')):
            continue
        if cf_only and c.get('domain').lstrip('.') not in ('codeforces.com','www.codeforces.com'):
            continue
        expiry = c.get('expirationDate', c.get('expires', -1))
        if isinstance(expiry, (int, float)) and math.isfinite(expiry):
            saved = {key: c[key] for key in ('name', 'value', 'domain', 'path')} | {'expires': expiry}
            if c.get('partitionKey') is not None:
                saved['partitionKey'] = {'topLevelSite': c['partitionKey']['topLevelSite']}
            cookies.append(saved)
    return cookies


def store_snapshot(value):
    source, profile = value.get('source'), value.get('profile_id', '')
    if source not in ('chrome', 'edge') or not re.fullmatch(r'[a-f0-9-]{32,36}', profile):
        raise ValueError('Invalid browser profile')
    cookies = snapshot_cookies(value)
    agent = value.get('user_agent', '')
    if not isinstance(agent, str) or len(agent) > 1000 or '\n' in agent or '\r' in agent:
        raise ValueError('Invalid browser metadata')
    write_json(state_directory() / 'browser-login' / source / (profile + '.json'),
               {'source': source, 'profile_id': profile, 'user_agent': agent, 'updated_at': time.time(), 'cookies': cookies,
                'read_errors': [host for host in value.get('read_errors', []) if host in HOSTS],
                'capabilities': ['cf-request-v1'] if 'cf-request-v1' in value.get('capabilities', []) else []})



def cloud_message(value):
    from . import cloud
    from .models import VPError
    import contextlib
    import io
    try:
        if value['type'] == 'cloud-login':
            account, password = value.get('account'), value.get('password')
            if not isinstance(account, str) or not isinstance(password, str) or len(account)>200 or len(password)>1000:
                raise VPError('请输入有效的协会账号和密码。')
            cloud.login(account, password, interactive=False)
            cloud.queue_all()
            cloud.kick_sync_process()
        elif value['type'] == 'cloud-logout':
            with contextlib.redirect_stdout(io.StringIO()):
                cloud.logout()
        elif value['type'] != 'cloud-status':
            raise VPError('不支持的云端操作。')
        saved = cloud.read_json(cloud.configuration_path())
        return {'type':'cloud-account', 'user':saved.get('user'), 'serverUrl':saved.get('serverUrl',cloud.DEFAULT_SERVER)}
    except (VPError, OSError):
        return {'type':'cloud-account', 'error':'云端操作未完成，请检查协会账号密码和本机网络，或运行 xcpc login。'}


def main():
    if not sys.argv[1:] or sys.argv[1] != 'chrome-extension://' + extension_id() + '/':
        raise SystemExit(1)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        identity = None
        while (value := read_message(sys.stdin.buffer)) is not None:
            if str(value.get('type', '')).startswith('cloud-'):
                send_message(sys.stdout.buffer, cloud_message(value))
            elif value.get('type') == 'snapshot':
                current=(value['source'],value['profile_id'])
                if identity is not None and identity != current:
                    raise ValueError('Browser profile changed on native port')
                store_snapshot(value)
                identity=current
                send_message(sys.stdout.buffer, {'type': 'stored'})
            elif value.get('type') in ('cf-request-poll','cf-request-result'):
                if identity is None:
                    raise ValueError('Browser profile not established')
                from . import browser_rpc
                if value['type']=='cf-request-poll':
                    send_message(sys.stdout.buffer, {'type':'cf-request-job','job':browser_rpc.poll(*identity)})
                else:
                    browser_rpc.complete(*identity,value.get('id'),value.get('result'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
