from __future__ import annotations

from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import VPError
from .parsing import CWNUOJ_HOST, NOWCODER_HOST
from .browser_sites import canonical_host

# ---------------------------------------------------------------------------
# Transport selection.
#
# qoj.ac and codeforces.com sit behind Cloudflare, which fingerprints the TLS
# handshake.  Plain requests/urllib3 has a non-browser fingerprint and always
# receives "HTTP 403 / Just a moment..." — no combination of headers, matching
# User-Agent or replayed cookies changes that (verified empirically).
#
# curl_cffi bundles libcurl-impersonate and reproduces a real browser's TLS and
# HTTP/2 fingerprint, which passes the check.  When curl_cffi is unavailable the
# original requests behaviour is kept, so the tool still degrades gracefully.
# ---------------------------------------------------------------------------
_IMPERSONATE = "firefox147"

# "firefox" is a version-dependent alias: curl_cffi 0.11 resolves it to Firefox
# 135 while 0.16 resolves it to Firefox 147.  Pairing that alias with a
# hardcoded User-Agent would send a 147 header over a 135 fingerprint whenever an
# older curl_cffi is installed.  Naming the profile explicitly keeps the two in
# step; pyproject.toml requires a curl_cffi new enough to know this profile.
#
# curl_cffi keeps the profile's headers (User-Agent included) to itself rather
# than in session.headers, but cli.py and qoj.py read session.headers
# ["User-Agent"] as metadata for the submit script, so the key has to exist.  It
# holds exactly the string this profile sends, which leaves the request on the
# wire byte-for-byte identical.
_IMPERSONATED_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

# Bounded retry for GETs, mirroring the urllib3 policy this transport replaced:
# two retries with a 1s/2s backoff on transient statuses and on connection
# errors.  curl_cffi's own retry= only covers transport errors, not status
# codes, so the loop below covers both transports.  POSTs are never retried --
# starting a native VP must not be repeated behind the user's back.
_RETRY_TOTAL = 2
_RETRY_STATUS = frozenset({429, 502, 503, 504})
_RETRY_BACKOFF = 1.0
_BROWSER_PLATFORMS = {'qoj.ac': 'qoj', 'contest.ucup.ac': 'qoj', 'codeforces.com': 'cf',
                      CWNUOJ_HOST: 'cwnuoj', NOWCODER_HOST: 'nowcoder'}

try:
    from curl_cffi import requests as _requests

    try:
        from curl_cffi.requests.errors import RequestsError as _RequestException
    except Exception:  # pragma: no cover - defensive
        _RequestException = Exception
except ImportError:  # pragma: no cover - curl_cffi not installed
    import requests as _requests

    _IMPERSONATE = None
    _RequestException = _requests.RequestException


class WebSession:
    def __init__(self, origin: str, account: dict, *, auto_refresh: bool = True):
        self.origin = origin.rstrip("/")
        host = urlsplit(self.origin).hostname or ''
        canonical = canonical_host(host)
        if (auto_refresh and canonical in _BROWSER_PLATFORMS
                and account.get('cookie_source') in ('chrome', 'edge') and account.get('browser_host')):
            if canonical_host(account['browser_host']) != canonical_host(host):
                from .browser_login import refresh_browser_account
                selected = {**account, '_browser_target': host}
                platform = _BROWSER_PLATFORMS[canonical]
                if not refresh_browser_account(platform, selected):
                    raise VPError('请在已加载 XCPC 扩展的浏览器配置中登录 ' + host + ' 后同步。')
                selected.pop('_browser_target', None)
                account.update(selected)
        self.account = account
        self._browser_checked = False
        self._http1_only = False
        self._auto_refresh = (auto_refresh and bool(account.get("handle"))
                              and canonical in ("qoj.ac", "codeforces.com", CWNUOJ_HOST, NOWCODER_HOST))
        if account.get('cookie_source') in ('chrome', 'edge') and canonical == 'contest.ucup.ac':
            self._auto_refresh = auto_refresh and bool(account.get('handle'))
        if _IMPERSONATE:
            # Linux retains its fixed Firefox profile. Extension snapshots use
            # the Chromium transport and the actual normal-window User-Agent.
            chromium = account.get('cookie_source') in ('chrome', 'edge')
            self.session = _requests.Session(impersonate='chrome' if chromium else _IMPERSONATE)
            self.session.headers["User-Agent"] = account.get('user_agent') if chromium and account.get('user_agent') else _IMPERSONATED_USER_AGENT
        else:
            self.session = _requests.Session()
            self.session.headers["User-Agent"] = account.get(
                "user_agent", "Mozilla/5.0 (X11; Linux x86_64) VP/0.1"
            )
        self._set_cookies(account.get("cookie", ""))

    def _set_cookies(self, value: str) -> None:
        from .session_cookies import import_cookie_header
        import_cookie_header(self.session, self.origin, value)

    def _refresh_browser(self, *, force: bool = False) -> bool:
        if not self._auto_refresh:
            return False
        from .browser_login import refresh_browser_account
        host = urlsplit(self.origin).hostname
        platform = _BROWSER_PLATFORMS[canonical_host(host)]
        selected = {**self.account, '_browser_target': host} if self.account.get('cookie_source') in ('chrome', 'edge') else self.account
        if not refresh_browser_account(platform, selected, force=force):
            return False
        if selected is not self.account:
            selected.pop('_browser_target', None)
            self.account.update(selected)
        self.session.cookies.clear()
        self._set_cookies(self.account["cookie"])
        if self.account.get('cookie_source') in ('chrome', 'edge') and self.account.get('user_agent'):
            self.session.headers['User-Agent'] = self.account['user_agent']
        return True

    def get(self, url: str, *, retry: bool = True, **kwargs) -> object:
        timeout = kwargs.pop('timeout', (10, 40))
        host = urlsplit(url).hostname or ''
        first_party = canonical_host(host) == canonical_host(urlsplit(self.origin).hostname or '')
        if not self._browser_checked and first_party:
            self._browser_checked = True
            self._refresh_browser()
        refreshed = False
        retries = _RETRY_TOTAL if retry else 0
        for attempt in range(retries + 1):
            if attempt:
                # 1s then 2s -- the backoff the replaced urllib3 Retry used.
                time.sleep(_RETRY_BACKOFF * 2 ** (attempt - 1))
            try:
                request_options = dict(kwargs)
                if self._http1_only:
                    from curl_cffi.const import CurlHttpVersion
                    request_options['http_version'] = CurlHttpVersion.V1_1
                try:
                    from .platform_clock import tick
                    sent_at = tick()
                    r = self.session.get(url, timeout=timeout, **request_options)
                except _RequestException as exc:
                    if not (_IMPERSONATE and host == 'codeforces.com' and not self._http1_only
                            and type(exc).__name__ == 'SSLError'):
                        raise
                    from curl_cffi.const import CurlHttpVersion
                    self._http1_only = True
                    if urlsplit(url).path.startswith('/api/'):
                        time.sleep(2.1)
                    request_options['http_version'] = CurlHttpVersion.V1_1
                    sent_at = tick()
                    r = self.session.get(url, timeout=timeout, **request_options)
                if (not refreshed and first_party and r.status_code == 200
                        and canonical_host(urlsplit(r.url).hostname or '') == canonical_host(urlsplit(self.origin).hostname or '')
                        and urlsplit(r.url).path in ("/login", "/user/login", "/enter")):
                    refreshed = True
                    if self._refresh_browser(force=True):
                        # Only an idempotent GET is repeated after refreshing login.
                        sent_at = tick()
                        r = self.session.get(url, timeout=timeout, **request_options)
                from .session_cookies import honor_judge_response_cookies
                honor_judge_response_cookies(self.session, r)
            except _RequestException as exc:
                if attempt == retries:
                    detail = (str(exc).splitlines() or [''])[0][:500]
                    import re
                    detail = re.sub(r'https?://\S+', '[request URL]', detail)
                    for key in ('api_key', 'api_secret', 'cookie'):
                        if self.account.get(key):
                            detail = detail.replace(self.account[key], '[redacted]')
                    raise VPError(f"Connection failed: {host} ({type(exc).__name__}): {detail}") from None
                continue
            if r.status_code in _RETRY_STATUS and attempt < retries:
                continue
            return self.check(r, sent_at=sent_at)
        # The final iteration always returns or raises; this keeps the flow total.
        raise VPError(f"Connection failed: {host}")  # pragma: no cover

    def post(self, url: str, data: dict, *, headers: dict | None = None):
        try:
            # Never retry a native contest start automatically.
            options = {'headers': headers} if headers else {}
            if self._http1_only:
                from curl_cffi.const import CurlHttpVersion
                options['http_version'] = CurlHttpVersion.V1_1
            from .platform_clock import tick
            sent_at = tick()
            r = self.session.post(url, data=data, timeout=(10, 40), **options)
        except _RequestException:
            raise VPError("The server did not confirm starting the VP. Run the same URL to check the remote state.") from None
        return self.check(r, sent_at=sent_at)

    def check(self, r, *, sent_at=None):
        from .session_cookies import honor_judge_response_cookies
        honor_judge_response_cookies(self.session, r)
        if urlsplit(r.url).hostname in ('codeforces.com', 'www.codeforces.com'):
            from .cf_forms import honor_response_cookies
            honor_response_cookies(self.session, r)
        if r.status_code != 200:
            if (urlsplit(r.url).hostname == 'codeforces.com' and urlsplit(r.url).path.startswith('/api/')
                    and r.status_code in (400, 403, 429)):
                try:
                    data = r.json()
                except (ValueError, AttributeError):
                    data = None
                if isinstance(data, dict) and data.get('status') == 'FAILED':
                    return r
            raise VPError(f"{urlsplit(r.url).hostname} returned HTTP {r.status_code}")
        if urlsplit(r.url).path in ("/login", "/user/login", "/enter"):
            if self.account.get("cookie_source") == "manual":
                raise VPError("Login session expired; update the account cookie in ~/.config/vp/config.json.")
            browser = 'Chrome / Edge' if self.account.get('cookie_source') in ('chrome', 'edge') else 'Firefox'
            raise VPError(f"Login session expired. Log in to this site in {browser}, then run the command again.")
        # Cloudflare's interstitial is only meaningful for textual responses.
        # A PDF or other binary body must not be decoded here: requests happens
        # to tolerate it, but curl_cffi reports such a response with a non-codec
        # encoding ("binary") and raises LookupError/UnicodeDecodeError.
        content_type = (r.headers.get("Content-Type") or "").lower()
        if not content_type or "text/" in content_type or "html" in content_type:
            try:
                body = r.text[:3000]
            except Exception:
                body = ""
            if "Just a moment..." in body or "cf-chl-" in body:
                from .portable import login_browser
                browser = 'Ubuntu browser' if sys.platform == 'linux' else login_browser()
                raise VPError(f"The platform requests browser verification; refresh the login session in the {browser}.")
        from .platform_clock import observe
        observe(r, sent_at)
        return r

    def soup(self, url: str, **kwargs) -> BeautifulSoup:
        r = self.get(url, **kwargs)
        try:
            r.encoding = "utf8"
        except Exception:
            pass
        return BeautifulSoup(r.text, "html.parser")

    def pdf(self, url: str, target: Path) -> None:
        # Cookies are scoped to the OJ host, including for external attachments.
        r = self.get(url)
        if not r.content[:1024].lstrip().startswith(b"%PDF-"):
            raise VPError(f"The statement download is not a PDF: {target.name}")
        target.write_bytes(r.content)


def form_values(form) -> dict:
    result = {}
    for node in form.select("input[name], select[name], textarea[name]"):
        name = node.get("name")
        if node.get("type") in ("submit", "button", "file"):
            continue
        if node.get("type") in ("checkbox", "radio") and not node.has_attr("checked"):
            continue
        if node.name == "select":
            option = node.select_one("option[selected]") or node.select_one("option")
            result[name] = option.get("value", "") if option else ""
        elif node.name == "textarea":
            result[name] = node.get_text()
        else:
            result[name] = node.get("value", "")
    return result
