"""Choose an available browser TLS profile and a consistent request User-Agent.

Only explicit account/profile metadata is read. Nothing launches a browser,
executes preferences, mutates a Firefox profile, or solves a browser challenge.
Matching these values does not guarantee that a browser clearance is reusable.
"""
from __future__ import annotations

from configparser import ConfigParser, Error as ConfigError
import json
from pathlib import Path
import re
import sys
from typing import get_args

DEFAULT_PROFILE = 'firefox147'
DEFAULT_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) '
                      'Gecko/20100101 Firefox/147.0')
_DEFAULT = (DEFAULT_PROFILE, DEFAULT_USER_AGENT)
_PREFERENCE = re.compile(
    r'^\s*user_pref\(\s*("(?:[^"\\]|\\.)*")\s*,\s*'
    r'("(?:[^"\\]|\\.)*"|true|false)\s*\)\s*;\s*$', re.MULTILINE)


def _supported_profiles() -> frozenset[str]:
    try:
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        return frozenset(get_args(BrowserTypeLiteral))
    except (ImportError, AttributeError):
        return frozenset({DEFAULT_PROFILE})


def _read_small(path: Path, maximum: int = 65536) -> str | None:
    try:
        with path.open('r', encoding='utf8') as stream:
            value = stream.read(maximum + 1)
        return value if len(value) <= maximum else None
    except FileNotFoundError:
        return ''
    except (OSError, UnicodeError):
        return None


def _safe_user_agent(value) -> str:
    return value if (isinstance(value, str) and 0 < len(value) <= 512
                     and all(32 <= ord(char) < 127 for char in value)) else ''


def _firefox_major(value: str) -> int | None:
    if not _safe_user_agent(value) or not value.startswith('Mozilla/5.0 '):
        return None
    firefox = re.search(r'\bFirefox/([1-9][0-9]{1,3})(?:\.[0-9]+)+(?: |$)', value)
    gecko = re.search(r'\brv:([1-9][0-9]{1,3})(?:\.[0-9]+)+\)', value)
    if not firefox or not gecko or firefox[1] != gecko[1] or ' Gecko/20100101 ' not in value:
        return None
    return int(firefox[1])


def _configuration(value: str | None) -> ConfigParser | None:
    if not value:
        return None
    parser = ConfigParser(interpolation=None)
    try:
        parser.read_string(value)
        return parser
    except ConfigError:
        return None


def _system_ubuntu() -> bool:
    if sys.platform != 'linux':
        return False
    value = _read_small(Path('/etc/os-release')) or ''
    return re.search(r'(?m)^ID=(?:ubuntu|"ubuntu"|\'ubuntu\')\s*$', value) is not None


def _distribution_ubuntu(compatibility: ConfigParser) -> bool:
    for name in ('LastAppDir', 'LastPlatformDir'):
        value = compatibility.get('Compatibility', name, fallback='')
        if not value or '\x00' in value:
            continue
        directory = Path(value)
        if not directory.is_absolute():
            continue
        distribution = _configuration(_read_small(directory/'distribution/distribution.ini'))
        if distribution is None:
            continue
        identifier = distribution.get('Global', 'id', fallback='').lower()
        about = distribution.get('Global', 'about', fallback='').lower()
        name = distribution.get('Global', 'name', fallback='').lower()
        if (identifier == 'ubuntu' or re.fullmatch(r'canonical-[0-9]{3}', identifier)
                or re.search(r'\bubuntu\b', about + ' ' + name)):
            return True
    return _system_ubuntu()


def _platform_string(abi: str, compatibility: ConfigParser) -> str:
    # The Ubuntu Firefox 154 build was also checked on Linux_aarch64: its
    # actual network User-Agent reduces the CPU token to x86_64. Do not use the
    # compiler ABI itself as a network architecture token.
    if re.match(r'^Linux_(?:aarch64|x86_64|x86|i[3-6]86)(?:-|$)', abi):
        if _distribution_ubuntu(compatibility):
            return 'X11; Ubuntu; Linux x86_64'
        if re.match(r'^Linux_(?:x86_64|x86|i[3-6]86)(?:-|$)', abi):
            return 'X11; Linux x86_64'
    elif abi.startswith('Darwin_'):
        return 'Macintosh; Intel Mac OS X 10.15'
    elif re.match(r'^WINNT_x86_64(?:-|$)', abi):
        return 'Windows NT 10.0; Win64; x64'
    return ''


def transport_identity(account: dict) -> tuple[str, str]:
    """Return ``(explicit curl profile, User-Agent)`` without changing account.

    Chrome/Edge retain their current transport and extension-provided UA.
    Firefox prefers a matching recorded UA or a safely parsed profile override.
    Known profile metadata can supply the regular desktop UA. Unsupported
    versions, privacy overrides and unknown platforms keep the old coherent
    Firefox 147 pair instead of combining new headers with an old TLS profile.
    """
    supplied = _safe_user_agent(account.get('user_agent'))
    if account.get('cookie_source') in ('chrome', 'edge'):
        return 'chrome', supplied

    profile_value = account.get('firefox_profile')
    compatibility = None
    preferences: dict = {}
    if isinstance(profile_value, str) and profile_value and '\x00' not in profile_value:
        profile = Path(profile_value).expanduser()
        if profile.is_absolute():
            compatibility = _configuration(_read_small(profile/'compatibility.ini'))
            for filename in ('prefs.js', 'user.js'):
                data = _read_small(profile/filename, 2 * 1024 * 1024)
                if data is None:
                    return _DEFAULT
                for match in _PREFERENCE.finditer(data):
                    try:
                        key, value = json.loads(match[1]), json.loads(match[2])
                        if key in preferences and preferences[key] != value and key in ('privacy.resistFingerprinting', 'general.useragent.override'):
                            return _DEFAULT
                        preferences[key] = value
                    except (ValueError, TypeError):
                        return _DEFAULT
                for line in data.splitlines():
                    if (re.match(r'^\s*user_pref\(\s*"(?:general\.useragent\.override|privacy\.resistFingerprinting)"', line)
                            and _PREFERENCE.fullmatch(line) is None):
                        return _DEFAULT

    if 'privacy.resistFingerprinting' in preferences and type(preferences['privacy.resistFingerprinting']) is not bool:
        return _DEFAULT
    if preferences.get('privacy.resistFingerprinting') is True:
        return _DEFAULT
    # Per-site overrides need a URL-specific identity; guessing one global UA
    # would silently ignore the browser's own customization.
    if any(key.startswith('general.useragent.override.') for key in preferences):
        return _DEFAULT

    version = compatibility.get('Compatibility', 'LastVersion', fallback='') if compatibility else ''
    match = re.fullmatch(r'([1-9][0-9]{1,3})\.[0-9]+(?:\.[0-9]+)*(?:esr)?(?:_[A-Za-z0-9/._+-]+)?', version)
    major = int(match[1]) if match else None
    override = preferences.get('general.useragent.override')
    if override is not None:
        supplied = _safe_user_agent(override)
        override_major = _firefox_major(supplied)
        if override_major is None or major is not None and override_major != major:
            return _DEFAULT
        major = override_major
    supplied_major = _firefox_major(supplied)
    if major is None:
        major = supplied_major
    target = f'firefox{major}' if major is not None else ''
    if target not in _supported_profiles():
        return _DEFAULT
    if supplied_major == major:
        return target, supplied
    if compatibility is None:
        return _DEFAULT
    platform = _platform_string(compatibility.get('Compatibility', 'LastOSABI', fallback=''), compatibility)
    if not platform:
        return _DEFAULT
    return target, f'Mozilla/5.0 ({platform}; rv:{major}.0) Gecko/20100101 Firefox/{major}.0'
