"""Preserve native compiler labels without inferring historical source types."""
from __future__ import annotations

NOWCODER_LANGUAGES = {"2": "C++（clang++18）", "38": "C++(g++ 13)", "39": "C(gcc 10)",
                     "1": "C", "4": "Java", "11": "Python3", "25": "pypy3",
                     "17": "Go", "27": "Rust", "29": "Kotlin"}
_HEADERS = {"language", "lang", "compiler", "programming language", "语言", "编译语言", "编译器"}


def language_name(value, platform=None):
    if platform == 'nowcoder' and type(value) in (int, str) and str(value) in NOWCODER_LANGUAGES:
        return NOWCODER_LANGUAGES[str(value)]
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 160 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    if value.isdecimal() or value.casefold() in ('unknown', 'n/a', 'none', 'null', '—', '-'):
        return None
    return value


def record_language(raw, platform=None):
    if not isinstance(raw, dict):
        return None
    for key in ('programmingLanguage', 'languageName', 'langName', 'compilerName', 'language', 'lang', 'compilerId', 'compiler'):
        if (name := language_name(raw.get(key), platform)) is not None:
            return name
    return None


def language_fields(raw, platform=None):
    name = record_language(raw, platform)
    return {'language': name} if name is not None else {}


def merge_language(previous, incoming):
    """Language-only enrichment does not change the chosen verdict/revision."""
    result = dict(incoming)
    name = language_name(result.get('language'))
    old = language_name((previous or {}).get('language'))
    if old and (not name or name.casefold() == 'other' and old.casefold() != 'other'):
        name = old
    if name:
        result['language'] = name
    else:
        result.pop('language', None)
    return result


def html_language(row, headers=None):
    """Read an explicitly identified compiler column; never guess a position."""
    node = row.select_one('.language, .language-cell, .status-language-cell, [data-language]')
    if node is not None:
        value = language_name(node.get('data-language') or node.get_text(' ', strip=True))
        if value:
            return value
    table = row.find_parent('table')
    if headers is None and table is not None:
        headers = [node.get_text(' ', strip=True).casefold() for node in table.select('thead th')]
        if not headers:
            headers = [node.get_text(' ', strip=True).casefold() for node in table.select('tr th')]
    cells = row.select('td')
    for index, header in enumerate(headers or []):
        if header.strip().casefold() in _HEADERS and index < len(cells):
            return language_name(cells[index].get_text(' ', strip=True))
    # A detail page may use vertical key/value rows instead of a list header.
    for detail in row.select('tr'):
        values = detail.select('th,td')
        if len(values) == 2 and values[0].get_text(' ', strip=True).rstrip(':：').casefold() in _HEADERS:
            return language_name(values[1].get_text(' ', strip=True))
    return None
