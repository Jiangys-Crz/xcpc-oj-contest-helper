"""Import only judge cookies from the local Firefox profile and verify its user."""
from __future__ import annotations

import configparser
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .parsing import CWNUOJ_HOST, NOWCODER_HOST


def platform_host(platform: str) -> str:
    return {"qoj": "qoj.ac", "cwnuoj": CWNUOJ_HOST, "nowcoder": NOWCODER_HOST}.get(platform, "codeforces.com")


def cookie_hosts(host: str) -> tuple[str, ...]:
    hosts = (host, "." + host)
    parent = {CWNUOJ_HOST: "cwnupaa.com", NOWCODER_HOST: "nowcoder.com"}.get(host)
    return (*hosts, parent, "." + parent) if parent else hosts

_MAX_SESSION_SIZE = 16 * 1024 * 1024
_cache: dict = {}
_lock = threading.Lock()


@dataclass
class BrowserCookies:
    profile: Path
    cookie: str = field(repr=False)


def firefox_roots() -> list[Path]:
    home = Path.home()
    switched_user = False
    if os.geteuid() == 0 and os.environ.get("SUDO_USER") not in (None, "", "root"):
        import pwd
        try:
            home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
            switched_user = True
        except KeyError:
            pass
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) if not switched_user else home / ".config"
    roots = [config / "mozilla/firefox", home / ".mozilla/firefox",
             home / "snap/firefox/common/.config/mozilla/firefox",
             home / "snap/firefox/common/.mozilla/firefox",
             home / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
             home / ".var/app/org.mozilla.firefox/.mozilla/firefox"]
    return list(dict.fromkeys(p.expanduser().resolve() for p in roots))


def firefox_profiles(account: dict) -> list[Path]:
    if os.name != "posix":
        return []
    explicit = account.get("firefox_profile")
    profiles = [Path(explicit).expanduser().resolve()] if explicit else []
    # Verified logins cache their profile path. An automatically cached old
    # location must not hide the same user's newly migrated Firefox profile.
    if explicit and (account.get("firefox_profile_strict") or not account.get("handle")):
        return profiles
    for base in firefox_roots():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(base / "profiles.ini")
            preferred = [parser[s]["Default"] for s in parser.sections()
                         if s.startswith("Install") and parser[s].get("Default")]
            entries = [s for s in parser.sections() if s.startswith("Profile")]
            entries.sort(key=lambda s: (parser[s].get("Path") not in preferred,
                                       parser[s].get("Default") != "1"))
            for section in entries:
                name = parser[section].get("Path")
                if not name:
                    continue
                path = (base / name if parser[section].get("IsRelative", "1") == "1" else Path(name)).resolve()
                if path.is_dir() and path not in profiles:
                    profiles.append(path)
        except (OSError, configparser.Error):
            pass
        # Profile manager/custom installations may not list every profile in
        # profiles.ini. Inspect only immediate Firefox profile directories.
        try:
            for directory in (base, base / "Profiles"):
                if not directory.is_dir():
                    continue
                if (directory / "cookies.sqlite").is_file() and directory not in profiles:
                    profiles.append(directory)
                for candidate in sorted(directory.iterdir()):
                    if (candidate.is_dir() and (candidate / "cookies.sqlite").is_file()
                            and candidate.resolve() not in profiles):
                        profiles.append(candidate.resolve())
        except OSError:
            pass
    return profiles


def _decode_session(data: bytes) -> dict:
    """Firefox's mozLz40 header precedes a size and one LZ4 block."""
    if data[:8] != b"mozLz40\0" or len(data) < 12:
        raise ValueError("Invalid Firefox session header")
    expected = int.from_bytes(data[8:12], "little")
    if not 0 < expected <= _MAX_SESSION_SIZE:
        raise ValueError("Firefox session size is outside the supported range")
    block, position, result = memoryview(data)[12:], 0, bytearray()

    def length(value):
        nonlocal position
        if value == 15:
            while True:
                if position >= len(block):
                    raise ValueError("Truncated Firefox session")
                extra = block[position]
                position += 1
                value += extra
                if extra != 255:
                    break
        return value

    while position < len(block):
        token = block[position]
        position += 1
        literals = length(token >> 4)
        if position + literals > len(block) or len(result) + literals > expected:
            raise ValueError("Invalid Firefox session literals")
        result.extend(block[position:position + literals])
        position += literals
        if position == len(block):
            break
        if position + 2 > len(block):
            raise ValueError("Truncated Firefox session offset")
        distance = int.from_bytes(block[position:position + 2], "little")
        position += 2
        count = length(token & 15) + 4
        if not 0 < distance <= len(result) or len(result) + count > expected:
            raise ValueError("Invalid Firefox session match")
        for _ in range(count):
            result.append(result[-distance])
    if len(result) != expected:
        raise ValueError("Firefox session size mismatch")
    value = json.loads(result)
    if not isinstance(value, dict):
        raise ValueError("Invalid Firefox session object")
    return value


def _ordinary(attributes) -> bool:
    return not any(attributes.values()) if isinstance(attributes, dict) else attributes in (None, "")


def _session_cookie_rows(profile: Path, host: str) -> list[tuple[str, str, str]]:
    files = [profile / "sessionstore.jsonlz4",
             profile / "sessionstore-backups/recovery.jsonlz4",
             profile / "sessionstore-backups/recovery.baklz4",
             profile / "sessionstore-backups/previous.jsonlz4"]
    try:
        existing = [p for p in files if p.is_file()]
        if not existing:
            return []
        latest = max(existing, key=lambda p: p.stat().st_mtime_ns)
        if latest.stat().st_size > _MAX_SESSION_SIZE:
            return []
        state = _decode_session(latest.read_bytes())
        cookies = list(state.get("cookies", []))
        for window in state.get("windows", []):
            if not window.get("isPrivate"):
                cookies.extend(window.get("cookies", []))
        return [(c["name"], c["value"], c["host"]) for c in cookies
                if c.get("host", "") in cookie_hosts(host) and c.get("path", "/") == "/"
                and _ordinary(c.get("originAttributes")) and c.get("expiry", float("inf")) > time.time()
                and isinstance(c.get("name"), str) and isinstance(c.get("value"), str)]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def _cookie_values(rows, host: str) -> dict:
    values, priority = {}, {}
    for name, value, domain in rows:
        rank = int(domain.lstrip(".") == host)
        if rank >= priority.get(name, -1):
            values[name], priority[name] = value, rank
    return values


def _session_cookies(profile: Path, host: str) -> dict:
    return _cookie_values(_session_cookie_rows(profile, host), host)


def _header(values: dict) -> str:
    jar = SimpleCookie()
    for name, value in values.items():
        if (re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                and not any(ord(c) < 32 or ord(c) == 127 for c in value)):
            jar[name] = value
    return "; ".join(m.OutputString() for m in jar.values())


def browser_cookies(platform: str, account: dict, *, errors: list[str] | None = None) -> list[BrowserCookies]:
    host = platform_host(platform)
    candidates = []
    for profile in firefox_profiles(account):
        rows = []
        database = profile / "cookies.sqlite"
        try:
            if database.is_file():
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as conn:
                    hosts = cookie_hosts(host)
                    rows = list(conn.execute(
                        "SELECT name, value, host FROM moz_cookies WHERE host IN (" + ",".join("?" for _ in hosts) + ") AND path='/' "
                        "AND expiry > ? AND originAttributes='' ORDER BY lastAccessed",
                        (*hosts, int(time.time()))))
        except (OSError, sqlite3.Error):
            if errors is not None:
                errors.append(str(profile) + "：无法读取 Cookie 数据库；请确认使用同一个 Ubuntu 用户运行。")
        persistent = _cookie_values(rows, host)
        session_rows = _session_cookie_rows(profile, host)
        combined_rows = [*rows, *session_rows]
        combined = _cookie_values(combined_rows, host)
        seen = set()
        # Host and parent-domain cookies may share a name. Prefer the site's
        # cookie, but verify the latest parent variant as well: an old guest
        # host cookie must not hide a valid shared-domain login.
        latest_combined = {name: value for name, value, _ in combined_rows}
        latest_persistent = {name: value for name, value, _ in rows}
        for values in (combined, latest_combined, persistent, latest_persistent):
            header = _header(values)
            if header and header not in seen:
                candidates.append(BrowserCookies(profile, header))
                seen.add(header)
    return candidates


def identity_from_page(platform: str, html: str, expected: str = "") -> dict:
    """Use the same authenticated navigation identity for VP and practice."""
    from .models import VPError
    identity = {}
    if platform in ("cwnuoj", "nowcoder"):
        if platform == "cwnuoj":
            from .hydro import identity_from_html
        else:
            from .nowcoder import identity_from_html
        identity = identity_from_html(html)
        handles = {identity["handle"]}
    else:
        soup = BeautifulSoup(html, "html.parser")
        selector = "ul.nav-pills, .navbar, nav, header, .nav-item.dropdown .dropdown-menu" if platform == "qoj" else "#header"
        prefix = "/user/profile/" if platform == "qoj" else "/profile/"
        handles = {urlsplit(a["href"]).path[len(prefix):].rstrip("/")
                   for header in soup.select(selector) for a in header.select("a[href]")
                   if urlsplit(a["href"]).path.startswith(prefix)}
    if expected:
        handles = {h for h in handles if h == expected or platform != "qoj" and h.casefold() == expected.casefold()}
    if len(handles) != 1:
        raise VPError("网页登录账号与本地 handle 配置不匹配，或网页未返回登录账号。" +
                      ("CWNUOJ 的 handle 应为用户名，不是登录邮箱。" if platform == "cwnuoj" else ""))
    return {**identity, "handle": expected or handles.pop()}


def _verify(platform: str, candidate: BrowserCookies, expected: str, *, errors: list[str] | None = None) -> dict | None:
    from .http import WebSession
    host = platform_host(platform)
    origin = "https://" + host
    web = WebSession(origin, {"cookie": candidate.cookie}, auto_refresh=False)
    try:
        response = web.session.get(origin + "/", params={"locale": "en"}, timeout=(5, 15))
        web.check(response)
        if urlsplit(response.url).hostname != host:
            if errors is not None:
                errors.append("网站验证跳转到了其他域名。")
            return None
        identity = identity_from_page(platform, response.text, expected)
        jar = getattr(web.session.cookies, "jar", web.session.cookies)
        values = {c.name: c.value for c in jar
                  if c.domain in cookie_hosts(host) and c.path == "/"}
        return {**identity, "cookie": _header(values),
                "cookie_source": "firefox", "firefox_profile": str(candidate.profile)}
    except Exception as exc:
        # Do not replace a configured session on network failure or another login.
        if errors is not None:
            from .models import VPError
            errors.append(str(exc) if isinstance(exc, VPError) else "网站验证请求失败（" + type(exc).__name__ + "）；请检查此终端的网络连接。")
        return None
    finally:
        web.session.close()


def verify_stored_session(platform: str, account: dict) -> bool:
    if not account.get("cookie"):
        return False
    verified = _verify(platform, BrowserCookies(Path(account.get("firefox_profile") or "."), account["cookie"]),
                       account.get("handle", ""))
    if not verified:
        return False
    account.update({key: verified[key] for key in ("cookie", "handle", "uid") if key in verified})
    return True


def refresh_browser_account(platform: str, account: dict, *, force: bool = False) -> bool:
    if account.get("cookie_source") == "manual":
        return False
    for candidate in browser_cookies(platform, account):
        key = (platform_host(platform), str(candidate.profile), account.get("handle", ""),
               hashlib.sha256(candidate.cookie.encode()).digest())
        with _lock:
            cached = _cache.get(key)
            if not force and cached and time.monotonic() - cached[0] < 30:
                verified = cached[1]
            else:
                verified = _verify(platform, candidate, account.get("handle", ""))
                _cache[key] = (time.monotonic(), verified)
        if verified:
            account.update(verified)
            return True
    return False


def browser_fields(account: dict) -> dict:
    return {k: account[k] for k in ("cookie_source", "firefox_profile", "firefox_profile_strict") if account.get(k)}


def refresh_submission_login(path: Path, config: dict, platform: str) -> None:
    from .storage import write_json
    before = dict(config)
    if refresh_browser_account(platform, config) and config != before:
        write_json(path, config)


def login_diagnostics(platform: str, account: dict) -> list[str]:
    """Safe to share: locations, counts and fixed errors, never cookie values."""
    if account.get("cookie_source") == "manual":
        return ["当前配置使用手动 Cookie，未自动读取 Firefox。"]
    profiles = firefox_profiles(account)
    if not profiles:
        return ["未找到 Firefox 配置目录。请在 Firefox 打开 about:support 查看“配置文件夹”。",
                "已检查：" + "、".join(map(str, firefox_roots()))]
    messages = ["找到 Firefox 配置目录：" + "、".join(map(str, profiles))]
    errors = []
    candidates = browser_cookies(platform, account, errors=errors)
    if not candidates:
        return messages + errors + ["未读到该站点的普通窗口 Cookie。请在普通 Firefox 窗口登录对应网站；隐私窗口和容器登录不会混用。",
                                    "若仍失败，可关闭 Firefox 后再试一次，让登录状态写入磁盘。"]
    messages.append(f"读取到 {len(candidates)} 组该站点 Cookie。")
    for candidate in candidates:
        problems = []
        verified = _verify(platform, candidate, account.get("handle", ""), errors=problems)
        if verified:
            return messages + ["已验证该站点的 Firefox 登录：" + str(candidate.profile)]
        messages.extend(str(candidate.profile) + "：" + reason for reason in problems)
    return messages
