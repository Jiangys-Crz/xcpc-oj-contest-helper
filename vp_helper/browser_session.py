"""Requests-compatible CF HTML transport using the selected ordinary browser.

Only the fixed local RPC protocol receives form fields. Browser cookies and
request headers are supplied by the browser itself; API calls remain on the
ordinary HTTP transport. A dispatched POST is never retried or moved to curl.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from . import browser_rpc


def browser_account(account):
    return account.get('cookie_source') in ('chrome', 'edge')


def browser_target(method, url):
    """Keep registration and downloads on their established HTTP paths."""
    try:
        browser_rpc.validate_target(method, url)
    except browser_rpc.BrowserRPCError:
        return False
    return True


class BrowserSession:
    def __init__(self, account, *, cookies=None, headers=None):
        self.account = account
        self.cookies = cookies if cookies is not None else requests.cookies.RequestsCookieJar()
        self.headers = headers if headers is not None else requests.structures.CaseInsensitiveDict()
        if account.get('user_agent'):
            self.headers['User-Agent'] = account['user_agent']

    def require_available(self):
        if not browser_rpc.available(self.account):
            raise browser_rpc.BrowserRPCUnavailable(
                'Update XCPC, reload its extension at chrome://extensions or edge://extensions, '
                'then sync login in the same ordinary browser profile. Keep a Codeforces tab open.')

    def request(self, method, url, **options):
        method = str(method).upper()
        params = options.pop('params', None)
        if params:
            p = urlsplit(url)
            query = '&'.join(filter(None, (p.query, urlencode(params, doseq=True))))
            url = urlunsplit((p.scheme, p.netloc, p.path, query, p.fragment))
        # Validate before reading source bytes or placing anything in the queue.
        browser_rpc.validate_target(method, url)
        data = options.pop('data', None)
        fields = {}
        if data:
            pairs = data.items() if hasattr(data, 'items') else data
            for name, value in pairs:
                if name in fields:
                    previous = fields[name]
                    fields[name] = [*(previous if isinstance(previous, list) else [previous]), value]
                else:
                    fields[name] = value
        uploads = {}
        files = options.pop('files', None)
        if files:
            pairs = files.items() if hasattr(files, 'items') else files
            for name, value in pairs:
                if name != 'sourceFile' or name in uploads:
                    raise browser_rpc.BrowserRPCError('Only the selected source file can be uploaded. No request was sent.')
                if isinstance(value, (tuple, list)):
                    if len(value) < 2:
                        raise browser_rpc.BrowserRPCError('The source file is invalid. No request was sent.')
                    filename, body = value[:2]
                else:
                    body = value
                    filename = Path(getattr(body, 'name', '')).name
                if hasattr(body, 'read'):
                    body = body.read(browser_rpc.MAX_REQUEST + 1)
                uploads[name] = (filename, body)
        timeout = options.pop('timeout', 45)
        if isinstance(timeout, (tuple, list)):
            timeout = sum(v for v in timeout if isinstance(v, (int, float)))
        # Deliberately ignore Python Cookie/User-Agent/Fetch headers: the normal
        # tab supplies its own complete browser request context.
        response = browser_rpc.request(self.account, method, url, data=fields or None,
                                       files=uploads or None, timeout=timeout)
        from .cf_forms import honor_response_cookies
        honor_response_cookies(self, response)
        return response

    def get(self, url, **options):
        return self.request('GET', url, **options)

    def post(self, url, **options):
        return self.request('POST', url, **options)

    def close(self):
        pass


class BrowserRoutingSession:
    """Share metadata with curl while routing only allowed CF HTML through RPC."""
    def __init__(self, raw, account):
        self.raw = raw
        self.account = account
        self.cookies = raw.cookies
        self.headers = raw.headers
        self.browser = BrowserSession(account, cookies=self.cookies, headers=self.headers)

    @property
    def impersonate(self):
        return self.raw.impersonate

    @impersonate.setter
    def impersonate(self, value):
        self.raw.impersonate = value

    def request(self, method, url, **options):
        # No source is ever sent through raw curl, even if an invalid native
        # target gets here. Only registration POSTs retain the old workflow.
        p = urlsplit(url)
        if browser_target(method, url):
            return self.browser.request(method, url, **options)
        if str(method).upper() != 'GET' and not p.path.startswith('/contestRegistration/'):
            raise browser_rpc.BrowserRPCError('The browser submission destination is not supported. No request was sent.')
        return self.raw.request(method, url, **options)

    def get(self, url, **options):
        return self.request('GET', url, **options)

    def post(self, url, **options):
        return self.request('POST', url, **options)

    def close(self):
        self.raw.close()
