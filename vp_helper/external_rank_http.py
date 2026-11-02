"""Bounded, anonymous reads of the two supported public scoreboard hosts."""
import json
from urllib.parse import urlsplit

import requests

from .models import VPError


def read_public_json(web, url: str):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc not in ('board.xcpcio.com', 'rank.iccpc.cn')
            or parts.fragment):
        raise VPError('不支持的外榜数据地址。')
    try:
        response = web.get(url, timeout=(10, 30), allow_redirects=False, stream=True)
        try:
            if response.status_code != 200:
                raise VPError(f'外榜数据下载失败（HTTP {response.status_code}）。')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise VPError('外榜数据超过 32 MiB。')
                chunks.append(chunk)
            return json.loads(b''.join(chunks))
        finally:
            response.close()
    except (requests.RequestException, ValueError, UnicodeError):
        raise VPError('无法读取外榜 JSON，请检查链接或稍后重试。') from None
