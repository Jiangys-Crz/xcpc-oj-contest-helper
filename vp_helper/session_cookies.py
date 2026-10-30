"""Keep imported browser snapshots compatible with native judge cookie rotation."""
from __future__ import annotations

from copy import copy
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from .browser_sites import BASES, canonical_host, group_domains


def import_cookie_header(session, origin: str, header: str) -> None:
    """Seed a jar, recording which entries lost domain metadata in the snapshot.

    A fixed Cookie request header would override every future Set-Cookie value.
    Browser exports are already verified first-party name/value snapshots, but
    do not preserve whether a name originally came from a parent domain.
    """
    host = urlsplit(origin).hostname
    values = SimpleCookie()
    values.load(header)
    imported = {}
    for name, value in values.items():
        try:
            session.cookies.set(name, value.value, domain=host, path='/')
            imported[(host, '/', name)] = value.value
        except TypeError:  # A narrower backend cookie API, without jar scopes.
            session.cookies.set(name, value.value)
    session._xcpc_cookie_origin = host
    session._xcpc_imported_cookies = imported


def honor_judge_response_cookies(session, response) -> None:
    """Prefer native updates without merging legitimate path/subdomain cookies.

    Only remove a different parent scope when the previous entry is known to
    be our synthetic snapshot import. Otherwise only leading-dot equivalents
    of the exact domain, name and path are reconciled. No other site is touched.
    """
    origin = getattr(session, '_xcpc_cookie_origin', None)
    if not isinstance(origin, str):
        return
    base = canonical_host(origin)
    if base not in (*BASES['qoj'], *BASES['cwnuoj'], *BASES['nowcoder']):
        return
    allowed = {domain.lstrip('.') for domain in group_domains(base)}
    cookies = getattr(session, 'cookies', None)
    jar = getattr(cookies, 'jar', cookies)
    if not hasattr(jar, 'clear') or not hasattr(jar, 'set_cookie'):
        return
    imported = getattr(session, '_xcpc_imported_cookies', {})
    if not isinstance(imported, dict):
        imported = {}
    history = getattr(response, 'history', ())
    responses = [*(history if isinstance(history, (list, tuple)) else ()), response]
    for item in responses:
        source = urlsplit(str(getattr(item, 'url', ''))).hostname
        if source not in allowed:
            continue
        updates = getattr(item, 'cookies', None)
        updates = getattr(updates, 'jar', updates)
        try:
            fresh_cookies = list(updates)
        except TypeError:
            continue
        for fresh in fresh_cookies:
            domain = getattr(fresh, 'domain', '').lstrip('.').lower()
            path = getattr(fresh, 'path', '')
            if (domain not in allowed or not isinstance(path, str) or not path.startswith('/')
                    or any(ord(char) < 32 or ord(char) == 127 for char in path)
                    or not (source == domain or fresh.domain_specified and source.endswith('.' + domain))):
                continue
            for existing in list(jar):
                if existing.name != fresh.name or existing.path != path:
                    continue
                key = (existing.domain, existing.path, existing.name)
                synthetic = (key in imported and imported[key] == existing.value
                             and existing.domain.lstrip('.').lower() == origin
                             and (origin == domain or fresh.domain_specified and origin.endswith('.' + domain)))
                equivalent = existing.domain.lstrip('.').lower() == domain
                if (synthetic or equivalent) and (existing.domain != fresh.domain or fresh.is_expired()):
                    jar.clear(existing.domain, existing.path, existing.name)
                if synthetic or equivalent:
                    imported.pop(key, None)
            if not fresh.is_expired():
                jar.set_cookie(copy(fresh))
