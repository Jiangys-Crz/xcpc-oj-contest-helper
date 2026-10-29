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

DOMAINS = {'qoj.ac', '.qoj.ac', 'codeforces.com', '.codeforces.com',
           'oj.cwnupaa.com', '.oj.cwnupaa.com', 'cwnupaa.com', '.cwnupaa.com',
           'ac.nowcoder.com', '.ac.nowcoder.com', 'nowcoder.com', '.nowcoder.com'}


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
    data = json.dumps(value).encode('utf8')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def store_snapshot(value):
    source, profile = value.get('source'), value.get('profile_id', '')
    if source not in ('chrome', 'edge') or not re.fullmatch(r'[a-f0-9-]{32,36}', profile):
        raise ValueError('Invalid browser profile')
    cookies = []
    for c in value.get('cookies', []):
        if (not isinstance(c, dict) or c.get('domain') not in DOMAINS or c.get('path') != '/'
                or c.get('partitionKey') or c.get('incognito')
                or not isinstance(c.get('name'), str) or not isinstance(c.get('value'), str)
                or any(ch in c['name'] + c['value'] for ch in '\r\n;')):
            continue
        expiry = c.get('expirationDate', -1)
        if isinstance(expiry, (int, float)) and math.isfinite(expiry):
            cookies.append({key: c[key] for key in ('name', 'value', 'domain', 'path')} | {'expires': expiry})
    agent = value.get('user_agent', '')
    if not isinstance(agent, str) or len(agent) > 1000 or '\n' in agent or '\r' in agent:
        raise ValueError('Invalid browser metadata')
    write_json(state_directory() / 'browser-login' / source / (profile + '.json'),
               {'source': source, 'profile_id': profile, 'user_agent': agent, 'updated_at': time.time(), 'cookies': cookies})


def main():
    if not sys.argv[1:] or sys.argv[1] != 'chrome-extension://' + extension_id() + '/':
        raise SystemExit(1)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        while (value := read_message(sys.stdin.buffer)) is not None:
            if value.get('type') == 'snapshot':
                store_snapshot(value)
                send_message(sys.stdout.buffer, {'type': 'stored'})
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
