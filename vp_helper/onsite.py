"""Locate a matching public onsite archive without sending judge credentials."""
from __future__ import annotations

from contextlib import nullcontext
import html
import json
import math
import re
from urllib.parse import urlsplit

import requests

from .models import Contest, VPError
from .originals import OriginalReplay, cache_path
from .storage import read_json, write_json
from . import srk

_MAX_BYTES = 16 * 1024 * 1024


def rankland_pages(contest: Contest) -> list[str]:
    """Bounded name-derived candidates; exact problem URLs decide identity."""
    title = html.unescape(contest.name)
    years = list(dict.fromkeys(re.findall(r"\b(20\d{2})\b", title)))
    kind = re.search(r"\b(ICPC|CCPC)\b", title, re.I)
    if not kind or not years or len(years) > 2:
        return []
    invitation = re.search(r"\b(?:National\s+)?(?:Invitational|Invitation)\s*[(（]([A-Za-z][A-Za-z .-]{0,45})[)）]", title, re.I)
    regional = re.search(r"\b(?:ICPC|CCPC)\s+([A-Za-z][A-Za-z .-]{0,45}?)\s+(?:Reg|Regional(?:\s+Contest)?)\b", title, re.I)
    match = invitation or regional
    if not match:
        return []
    site = re.sub(r"[ .-]+", "", match[1].lower())
    if not re.fullmatch(r"[a-z]{2,46}", site):
        return []
    prefix = kind[1].lower()
    suffix = "invitational-" + site if invitation else site
    return [f"https://rl.algoux.cn/ranklist/{prefix}{year}{suffix}" for year in years]


def archive_urls(page_html: str) -> list[str]:
    # RankLand embeds a JSON-escaped URL in its server-rendered page. Decode
    # only slash escapes; do not execute scripts or accept arbitrary URLs.
    text = re.sub(r"\\+u002[fF]", "/", html.unescape(page_html)).replace("\\/", "/")
    return list(dict.fromkeys(re.findall(r"https://cdn\.algoux\.cn/rankland/file/[0-9]+/[A-Za-z0-9_.-]+\.srk\.json", text)))


def _trusted_page(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return bool(parts.scheme == "https" and parts.netloc == "rl.algoux.cn" and not parts.query and not parts.fragment
                and re.fullmatch(r"/ranklist/[a-z0-9-]+", parts.path))


def _trusted_archive(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return bool(parts.scheme == "https" and parts.netloc == "cdn.algoux.cn" and not parts.query and not parts.fragment
                and re.fullmatch(r"/rankland/file/[0-9]+/[A-Za-z0-9_.-]+\.srk\.json", parts.path))


def _read(web, url: str) -> bytes:
    if not (_trusted_page(url) or _trusted_archive(url)):
        raise VPError("The onsite archive URL is not a supported public source.")
    response = web.get(url, timeout=(5, 20), allow_redirects=False, stream=True)
    try:
        if response.status_code != 200:
            raise VPError("The public onsite archive is not available.")
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > _MAX_BYTES:
                raise VPError("The public onsite archive exceeds the supported size.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        response.close()


def _replay(data: dict, contest: Contest, source: str, page: str) -> OriginalReplay:
    if not _trusted_archive(source) or not _trusted_page(page):
        raise VPError("The cached onsite archive is not a supported public source.")
    metadata = srk.metadata(data, contest, source)
    native_start = contest.original_start
    archive_start = metadata.get("original_start")
    if (type(native_start) not in (int, float) or not math.isfinite(native_start)
            or type(archive_start) not in (int, float) or not math.isfinite(archive_start)
            or abs(native_start - archive_start) > 1 or metadata["duration"] != contest.rules.duration
            or metadata["penalty_minutes"] != contest.rules.penalty_minutes
            or not metadata["teams"] or not any(team["official"] for team in metadata["teams"])):
        raise VPError("The onsite archive's original schedule, rules or official roster does not match this contest.")
    events, final = srk.results(data, metadata, contest)
    replay = OriginalReplay(contest, metadata["teams"], events, final)
    metadata["source_page"] = page
    contest.extra["original_eligibility"] = metadata
    return replay


def load_replay(contest: Contest, *, web=None) -> OriginalReplay:
    """Use a public RankLand export only after exact native identity checks."""
    pages = rankland_pages(contest)
    if not pages:
        raise VPError("Original official participants are unavailable; no matching onsite archive was identified.")
    try:
        cached = read_json(cache_path("onsite", contest.url))
    except VPError:
        cached = {}  # A damaged local cache must not hide a valid public board.
    if not isinstance(cached, dict):
        cached = {}
    if cached.get("page") in pages and isinstance(cached.get("ranklist"), dict):
        try:
            return _replay(cached["ranklist"], contest, cached.get("source", ""), cached["page"])
        except (VPError, ValueError, TypeError, KeyError):
            pass
    last_error = "The original onsite scoreboard is unavailable."
    # Never pass the authenticated judge's WebSession here. This isolated
    # session has no judge cookie, API key, secret or Authorization header.
    with (nullcontext(web) if web is not None else requests.Session()) as public:
        for page in pages:
            try:
                body = _read(public, page).decode("utf8")
                sources = archive_urls(body)
                if not sources:
                    raise VPError("The public onsite page did not expose a supported archive.")
                for source in sources[:4]:
                    try:
                        data = json.loads(_read(public, source))
                        replay = _replay(data, contest, source, page)
                    except (requests.RequestException, UnicodeError, ValueError, TypeError, VPError) as exc:
                        last_error = str(exc)
                        continue
                    write_json(cache_path("onsite", contest.url), {"page": page, "source": source, "ranklist": data})
                    write_json(cache_path("srk", source), {"source": source, "ranklist": data})
                    write_json(cache_path("eligibility", contest.url), contest.extra["original_eligibility"])
                    return replay
            except (requests.RequestException, UnicodeError, ValueError, TypeError, VPError) as exc:
                last_error = str(exc)
    raise VPError("Original rank and public statistics are unavailable: " + last_error[:250])
