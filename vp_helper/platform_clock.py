"""Judge time shared by the monitor and submit processes; never change OS time."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from functools import lru_cache
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from .models import VPError
from .parsing import CWNUOJ_HOST, NOWCODER_HOST
from .storage import file_lock, read_json, state_directory, write_json

HOSTS = {'codeforces.com', 'qoj.ac', 'contest.ucup.ac', CWNUOJ_HOST, NOWCODER_HOST}


@lru_cache(maxsize=1)
def boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return f'process-{os.getpid()}'


def tick():
    # Linux BOOTTIME includes suspend, keeping the anchor correct on wake-up.
    return time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, 'CLOCK_BOOTTIME') else time.monotonic()


def _path(host):
    return state_directory() / f'clock-{host}.json'


def observe(response, sent_at=None):
    host = urlsplit(response.url).hostname
    if host not in HOSTS or urlsplit(response.url).scheme != 'https' or response.status_code != 200:
        return
    try:
        if int(response.headers.get('Age', '0')) != 0:
            return  # A cached Date is not the judge's current clock.
        date = parsedate_to_datetime(response.headers.get('Date', ''))
        if date.tzinfo is None:
            return
        epoch = date.timestamp()
        received = tick()
        delay = (received - sent_at) / 2 if isinstance(sent_at, (int, float)) else 0
        if not math.isfinite(epoch) or not 0 <= delay <= 30:
            return
    except (TypeError, ValueError, AttributeError, OverflowError):
        return
    try:
        path = _path(host)
        with file_lock(path.with_suffix('.lock')):
            previous = read_json(path)
            if previous.get('boot_id') == boot_id() and 0 <= received - previous.get('tick', 0) < 2:
                return
            write_json(path, {'server_time': epoch + delay, 'tick': received, 'boot_id': boot_id()})
    except (OSError, VPError):
        pass  # Clock caching must not turn a successful request into a failure.


def platform_now(target) -> float:
    url = target.get('url', '') if isinstance(target, dict) else target or ''
    host = urlsplit(url).hostname
    if host in HOSTS:
        try:
            saved = read_json(_path(host))
            if (saved.get('boot_id') == boot_id() and isinstance(saved.get('server_time'), (int, float))
                    and math.isfinite(saved['server_time'])):
                elapsed = tick() - saved.get('tick', float('inf'))
                if 0 <= elapsed <= 86400:
                    return saved['server_time'] + elapsed
        except (OSError, VPError, TypeError, ValueError):
            pass
    return time.time()
