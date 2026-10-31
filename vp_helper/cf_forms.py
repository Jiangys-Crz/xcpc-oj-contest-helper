"""Codeforces native submission forms, without executing page JavaScript."""
from __future__ import annotations

from copy import copy
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .models import VPError


def safe_url(value: str) -> str:
    """A submission URL may contain CSRF data; only display its public path."""
    try:
        p = urlsplit(str(value))
        return urlunsplit((p.scheme, p.hostname or '', p.path, '', ''))
    except ValueError:
        return '[request URL]'


def redact(message: str, secrets=()) -> str:
    message = str(message)
    for value in sorted({str(v) for v in secrets if v is not None and len(str(v)) >= 4}, key=len, reverse=True):
        message = message.replace(value, '[redacted]')
    message = re.sub(r'https?://[^\s<>"\']+', lambda m: safe_url(m[0]), message)
    return re.sub(r'(?i)(csrf_token|ftaa|bfaa|_tta)(["\']?\s*[:=]\s*["\']?)[^\s&,;<>"\']+',
                  r'\1\2[redacted]', message)


def _cookie_values(session, url: str = 'https://codeforces.com/') -> dict[str, str]:
    host = urlsplit(url).hostname
    path = urlsplit(url).path or '/'
    if host not in ('codeforces.com', 'www.codeforces.com'):
        return {}
    cookies = getattr(session, 'cookies', None)
    jar = getattr(cookies, 'jar', cookies)
    if isinstance(jar, dict):
        return {str(k): str(v) for k, v in jar.items()}
    result = {}
    priorities = {}
    try:
        for cookie in jar:
            domain = getattr(cookie, 'domain', '').lstrip('.').lower()
            cookie_path = getattr(cookie, 'path', '/') or '/'
            if (domain in ('codeforces.com', 'www.codeforces.com')
                    and (host == domain or cookie.domain_specified and host.endswith('.' + domain))
                    and (path == cookie_path or path.startswith(cookie_path)
                         and (cookie_path.endswith('/') or path[len(cookie_path):].startswith('/')))):
                priority = (len(cookie_path), len(domain), not cookie.domain_specified)
                if priority <= priorities.get(cookie.name, (-1, -1, False)):
                    continue
                result[cookie.name] = cookie.value
                priorities[cookie.name] = priority
    except (TypeError, AttributeError):
        pass
    return result


def form_secrets(form, session, url: str = 'https://codeforces.com/') -> list[str]:
    values = list(_cookie_values(session, url).values())
    for name in ('csrf_token', 'ftaa', 'bfaa', '_tta'):
        try:
            field = form[name]
            value = getattr(field, 'value', field)
            if value:
                values.append(str(value))
        except (KeyError, TypeError):
            pass
    return values


def honor_response_cookies(session, response) -> None:
    """Honor native rotations over equivalent imported host/domain cookies.

    Cookie snapshots lack host-only metadata. Requests/curl can consequently
    retain both `codeforces.com` and `.codeforces.com` entries after Set-Cookie,
    sending the stale session ID alongside the new one. Prefer the site's
    latest response, without changing unrelated hosts, names or paths.
    """
    cookies = getattr(session, 'cookies', None)
    jar = getattr(cookies, 'jar', cookies)
    if not hasattr(jar, 'clear') or not hasattr(jar, 'set_cookie'):
        return
    history = getattr(response, 'history', ())
    responses = [*(history if isinstance(history, (tuple, list)) else ()), response]
    for item in responses:
        source_host = urlsplit(str(getattr(item, 'url', ''))).hostname
        if source_host not in ('codeforces.com', 'www.codeforces.com'):
            continue
        updates = getattr(item, 'cookies', None)
        updates = getattr(updates, 'jar', updates)
        try:
            fresh_cookies = list(updates)
        except TypeError:
            continue
        for fresh in fresh_cookies:
            host = getattr(fresh, 'domain', '').lstrip('.').lower()
            path = getattr(fresh, 'path', '')
            if (host not in ('codeforces.com', 'www.codeforces.com')
                    or not (source_host == host or source_host.endswith('.' + host))
                    or not fresh.domain_specified and source_host != host
                    or not isinstance(path, str) or not path.startswith('/')
                    or re.search(r'[\x00-\x1f\x7f]', path)):
                continue
            for existing in list(jar):
                if (existing.name == fresh.name and existing.path == fresh.path
                        and existing.domain.lstrip('.').lower() == host
                        and (existing.domain != fresh.domain or fresh.is_expired())):
                    jar.clear(existing.domain, existing.path, existing.name)
            if not fresh.is_expired():
                jar.set_cookie(copy(fresh))


def _tta(value: str) -> int:
    # The public Codeforces.signForms()/tta() protocol, as published in
    # codeforces.org/s/90440/js/codeforces.js (also checked against 78055).
    # JS charCodeAt uses UTF-16 units.
    value = re.sub(r'%u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), value)
    value = unquote(value, encoding='latin-1')
    raw = value.encode('utf-16-le', errors='surrogatepass')
    chars = [int.from_bytes(raw[i:i+2], 'little') for i in range(0, len(raw), 2)]
    result = 0
    for i, char in enumerate(chars):
        result = (result + (i + 1) * (i + 2) * char) % 1009
        if i % 3 == 0:
            result += 1
        if i % 2 == 0:
            result *= 2
        if i:
            result -= (chars[i // 2] // 2) * (result % 5)
        result %= 1009
    return result


def sign_form(page, form, session, url: str = 'https://codeforces.com/') -> None:
    """Fill only the values the native page/browser supplies, never a new cookie."""
    values = {}
    for name in ('ftaa', 'bfaa'):
        field = form.select_one(f'[name="{name}"]')
        if field is not None and field.get('value'):
            continue
        match = re.search(r'(?:window\.)?_' + name + r'\s*=\s*["\']([^"\'\r\n]{1,256})["\']', str(page))
        if match:
            values[name] = match[1]
    cookie = _cookie_values(session, url).get('39ce7')
    if cookie and len(cookie) <= 4096:
        value = _tta(cookie)
        if value:  # Codeforces.signForms does not insert a zero signature.
            values['_tta'] = str(value)
    for name, value in values.items():
        field = form.select_one(f'[name="{name}"]')
        if field is None:
            field = page.new_tag('input', attrs={'type': 'hidden', 'name': name})
            form.append(field)
        field['value'] = value


def submission_target(page_url: str, action: str, base_url: str, problem: str | None = None) -> str:
    target = urljoin(page_url, action or page_url)
    try:
        p, base = urlsplit(target), urlsplit(base_url)
        accepted = {base.path.rstrip('/') + '/submit'}
        if problem:
            accepted.add(base.path.rstrip('/') + '/submit/' + problem.upper())
        if (p.scheme != 'https' or p.hostname not in ('codeforces.com', 'www.codeforces.com')
                or p.username or p.password or p.port or p.fragment or p.path.rstrip('/') not in accepted):
            raise ValueError
    except ValueError:
        raise VPError('Codeforces returned an unexpected submission destination. No code was submitted.') from None
    return target


def submission_headers(page_url: str, target: str) -> dict[str, str]:
    """Describe a native form navigation rather than a direct-address visit."""
    try:
        page, destination = urlsplit(page_url), urlsplit(target)
        invalid = (page.scheme != 'https' or destination.scheme != 'https'
                   or page.hostname not in ('codeforces.com', 'www.codeforces.com')
                   or destination.hostname not in ('codeforces.com', 'www.codeforces.com')
                   or page.username or page.password or page.port
                   or destination.username or destination.password or destination.port)
    except ValueError:
        invalid = True
    if invalid:
        raise VPError('Codeforces returned an unexpected form origin. No code was submitted.')
    # Profiles default to Sec-Fetch-Site:none for address-bar navigation. A
    # contest-page form POST instead carries the real page/target relation.
    return {'Origin': 'https://' + page.hostname, 'Referer': safe_url(page_url),
            'Sec-Fetch-Site': 'same-origin' if page.hostname == destination.hostname else 'same-site',
            'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1'}


def submission_error(page, status: int | None, response_url: str, secrets=(), *, headers=None, attempted=False) -> str:
    content = str(page)
    mitigation = (headers or {}).get('cf-mitigated', '')
    if (isinstance(mitigation, str) and mitigation.lower() == 'challenge'
            or 'cf-chl-' in content or 'Just a moment...' in content
            or page.select_one('input[name="cf-turnstile-response"], .g-recaptcha')):
        detail = 'The platform requests browser verification. Refresh Codeforces in the usual browser for this account.'
        if attempted:
            detail += ' Submission outcome is unknown; this may be verification after a successful upload. Check My submissions before retrying; the POST was not repeated.'
    elif status == 401 or urlsplit(response_url).path.rstrip('/') in ('/enter', '/login'):
        detail = 'The browser login expired. Log in to the configured Codeforces account and sync its login again.'
    else:
        errors = [node.get_text(' ', strip=True) for node in page.select('.error, .error-message, .error__message, .alert-danger')]
        detail = ' '.join(dict.fromkeys(value for value in errors if value))
        if not detail:
            detail = ('The platform is rate limiting submissions. Wait and check My submissions before retrying.'
                      if status == 429 else
                      'The native form was not accepted. Check My submissions before retrying; this request was not repeated.')
    label = f' (HTTP {status})' if isinstance(status, int) else ''
    return 'Codeforces did not confirm this submission' + label + ': ' + redact(detail, secrets)[:500]
