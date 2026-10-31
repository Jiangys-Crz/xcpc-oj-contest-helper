import os
import random
import getpass
from robobrowser import RoboBrowser
import json
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import requests
from vp_helper.browser_login import refresh_submission_login
from vp_helper.cf_forms import honor_response_cookies
from vp_helper.http import browser_challenge
from vp_helper.browser_transport import transport_identity, DEFAULT_PROFILE, DEFAULT_USER_AGENT
from vp_helper.models import VPError

# codeforces.com sits behind Cloudflare, which fingerprints the TLS handshake:
# a plain requests session is answered with "HTTP 403 / Just a moment...".
# curl_cffi reproduces a real browser's TLS and HTTP/2 fingerprint.  RoboBrowser
# accepts a replacement session because it only calls session.request/get/post
# and reads session.cookies.
try:
    from curl_cffi import requests as _requests

    _IMPERSONATE = 'firefox147'
except ImportError:  # pragma: no cover - curl_cffi not installed
    _requests = requests

    _IMPERSONATE = None


def _mime_from(data, files):
    """Express requests' data=/files= pair as a curl_cffi CurlMime.

    RoboBrowser serializes <input type="file"> fields into files= -- its
    FileInput.payload_key is 'files' -- and requests accepts (name, file object)
    pairs there.  curl_cffi has no equivalent, so those parts have to be read and
    rebuilt as CurlMime parts.
    """
    from curl_cffi import CurlMime

    mime = CurlMime()
    for name, value in (data or []):
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ''
        mime.addpart(name=name,
                     data=value if isinstance(value, bytes) else str(value).encode())
    for name, item in (files or []):
        filename = content_type = None
        if isinstance(item, (list, tuple)):
            if len(item) >= 3:
                filename, body, content_type = item[0], item[1], item[2]
            elif len(item) == 2:
                filename, body = item
            else:
                body = item[0]
        else:
            body = item
        if hasattr(body, 'read'):
            if not filename:
                filename = os.path.basename(getattr(body, 'name', '') or '') or None
            try:
                body.seek(0)
            except (OSError, ValueError):
                pass
            payload = body.read()
        else:
            payload = body
        if isinstance(payload, str):
            payload = payload.encode()
        if filename is None and content_type is None:
            mime.addpart(name=name, data=payload)
        else:
            # requests sends no Content-Type for a file object handed to it
            # without an explicit type, so this must not invent one either.
            mime.addpart(name=name, data=payload, filename=filename, content_type=content_type)
    return mime


class _FormSession(_requests.Session):
    """Honor native cookies with either transport and bridge curl file uploads.

    RoboBrowser builds file inputs as files=, which curl_cffi rejects with
    "files is not supported, use `multipart`". Requests accepts files= directly.
    """

    def request(self, method, url, *args, **kwargs):
        if _IMPERSONATE:
            files = kwargs.pop('files', None)
            if files:
                kwargs['multipart'] = _mime_from(kwargs.pop('data', None), files)
        response = super().request(method, url, *args, **kwargs)
        honor_response_cookies(self, response)
        refresh = getattr(self, '_xcpc_refresh', None)
        if (str(method).upper() == 'GET' and urlsplit(url).hostname in ('codeforces.com', 'www.codeforces.com')
                and urlsplit(str(getattr(response, 'url', ''))).hostname in ('codeforces.com', 'www.codeforces.com')
                and browser_challenge(response) and callable(refresh) and refresh()):
            # Refresh only a preflight GET; a POST may already have succeeded
            # before its following GET encounters verification.
            response = super().request(method, url, *args, **kwargs)
            honor_response_cookies(self, response)
        return response


def make_session(user_agent=None, cookie_source=None, *, account=None):
    """A browser session that can pass Cloudflare and accept RoboBrowser's forms."""
    if _IMPERSONATE:
        profile, actual_user_agent = transport_identity(account or {'cookie_source': cookie_source, 'user_agent': user_agent})
        if not actual_user_agent:
            profile, actual_user_agent = DEFAULT_PROFILE, DEFAULT_USER_AGENT
        session = _FormSession(impersonate=profile)
        session.headers['User-Agent'] = actual_user_agent
        return session
    session = _FormSession()
    if user_agent:
        session.headers['User-Agent'] = user_agent
    return session

root = '7'
""" converter """
def decode(s):
    global root
    res = ""
    length = len(s)
    i = 0
    while i < length:
        rng = ord(s[i])-ord(root)
        jump = ord(s[i+1])-ord(root)
        temp = 0
        for j in range (0, rng):
            temp += ord(s[i+j+2]) - ord(root) - jump
        res += str(chr(temp))
        i += rng + 2
    return res

def encode(s):
    global root
    res = ""
    length = len(s)
    for i in range (0, length):
        rng = random.randint(1, 20)
        res += str(chr(rng + ord(root)))
        jump = random.randint(1,10)
        res += str(chr(jump + ord(root)))
        curr = ord(s[i])
        for j in range (0, rng-1):
            temp = random.randint(0, min(curr, 2+int(curr/(rng-j))))
            res += str(chr(temp + ord(root) + jump))
            curr -= temp
        res += str(chr(curr + ord(root) + jump))
    return res

def get_secret(inclupass):
    context = Path('contest.json')
    if context.is_file():
        account = json.loads(context.read_text(encoding='utf8'))
        if account.get('platform') == 'gym' and account.get('handle'):
            return (account['handle'], None) if inclupass else account['handle']
    handle = None
    password = None
    secret_loc = os.path.join(os.path.dirname(__file__), "secret")
    if os.path.isfile(secret_loc):
        secretfile = open(secret_loc, "r")
        rawdata = secretfile.read().rstrip('\n').split()
        handle = decode(rawdata[0])
        if inclupass:
            password = decode(rawdata[1])
        secretfile.close()
    if inclupass:
        return handle, password
    else:
        return handle

""" set login """
def set_login(handle=None):
    if handle is None:
        handle = input("Handle: ")
    password = getpass.getpass("Password: ")

    browser = RoboBrowser(parser = "lxml", session = make_session())
    browser.open("https://codeforces.com/enter")
    enter_form = browser.get_form("enterForm")
    enter_form["handleOrEmail"] = handle
    enter_form["password"] = password
    browser.submit_form(enter_form)

    checks = list(map(lambda x: x.getText()[1:].strip(),
        browser.select("div.caption.titled")))
    if handle not in checks:
        print("Login Failed.")
        return
    else:
        secret_loc = os.path.join(os.path.dirname(__file__), "secret")
        secretfile = open(secret_loc, "w")
        secretfile.write(encode(handle) + " " + encode(password))
        secretfile.close()
        print("Successfully logged in as " + handle)

""" login """
def login():
    context = Path('contest.json')
    if context.is_file():
        account = json.loads(context.read_text(encoding='utf8'))
        if account.get('platform') == 'gym':
            refresh_submission_login(context, account, 'gym')
            session = make_session(account['user_agent'], account.get('cookie_source'), account=account)
            def set_current_cookies():
                session.cookies.clear()
                jar = SimpleCookie(); jar.load(account['cookie'])
                for item in jar.values():
                    session.cookies.set(item.key, item.value, domain='codeforces.com', path='/')
            def refresh_current_login():
                if not refresh_submission_login(context, account, 'gym', force=True):
                    return False
                profile, actual_user_agent = transport_identity(account)
                if _IMPERSONATE:
                    session.impersonate = profile
                if actual_user_agent:
                    session.headers['User-Agent'] = actual_user_agent
                set_current_cookies()
                return True
            set_current_cookies()
            session._xcpc_refresh = refresh_current_login
            browser = RoboBrowser(parser='lxml', session=session)
            browser.open('https://codeforces.com/')
            if browser_challenge(browser.response):
                raise VPError('Codeforces requests browser verification. Refresh it in your usual browser; no code was submitted.')
            logged_in = any(a.get('href', '').lower() == '/profile/' + account['handle'].lower()
                            for a in browser.select('#header a'))
            if not logged_in:
                browser_name = 'Chrome / Edge' if account.get('cookie_source') in ('chrome', 'edge') else 'Firefox'
                raise SystemExit(f'Codeforces login expired or belongs to another account. Log in to the configured account in {browser_name}, then run submit again.')
            return browser
    handle, password = get_secret(True)

    browser = RoboBrowser(parser = "lxml", session = make_session())
    browser.open("https://codeforces.com/enter")
    enter_form = browser.get_form("enterForm")
    enter_form["handleOrEmail"] = handle
    enter_form["password"] = password
    browser.submit_form(enter_form)

    checks = list(map(lambda x: x.getText()[1:].strip(), browser.select("div.caption.titled")))
    if handle not in checks:
        print("Login Corrupted.")
        return None
    else:
        return browser
