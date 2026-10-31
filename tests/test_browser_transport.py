"""Browser identity selection uses bounded read-only profile metadata."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vp_helper.browser_transport import DEFAULT_PROFILE, DEFAULT_USER_AGENT, transport_identity


UBUNTU154 = 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0'
LINUX154 = 'Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0'
DEFAULT = (DEFAULT_PROFILE, DEFAULT_USER_AGENT)


class BrowserTransportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.profile = self.root/'profile'; self.profile.mkdir()
        self.application = self.root/'Firefox application'; self.application.mkdir()
        self.stack.enter_context(patch('vp_helper.browser_transport._supported_profiles', return_value=frozenset({'firefox147','firefox154'})))
        self.stack.enter_context(patch('vp_helper.browser_transport._system_ubuntu', return_value=False))
        self.account = {'cookie_source':'firefox','firefox_profile':str(self.profile)}

    def compatibility(self, *, version='154.0_20260814094907/20260814094907', abi='Linux_x86_64-gcc3', ubuntu=False):
        (self.profile/'compatibility.ini').write_text('[Compatibility]\nLastVersion='+version+'\nLastOSABI='+abi+
            '\nLastAppDir='+str(self.application)+'\nLastPlatformDir='+str(self.application)+'\n')
        if ubuntu:
            (self.application/'distribution').mkdir(exist_ok=True)
            (self.application/'distribution/distribution.ini').write_text('[Global]\nid=canonical-002\nversion=1.0\nabout=Mozilla Firefox for Ubuntu\n')

    def test_ubuntu_arm_profile_uses_the_verified_desktop_ua_and_matching_154_tls(self):
        self.compatibility(abi='Linux_aarch64-gcc3', ubuntu=True)
        self.account['user_agent'] = DEFAULT_USER_AGENT  # Legacy synthetic value must not hide the installed version.
        before = {path:path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        original_account = dict(self.account)
        self.assertEqual(transport_identity(self.account), ('firefox154', UBUNTU154))
        self.assertEqual(self.account, original_account)
        self.assertEqual(before, {path:path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_system_ubuntu_is_used_only_for_a_recognized_linux_profile(self):
        self.compatibility(abi='Linux_aarch64-gcc3')
        with patch('vp_helper.browser_transport._system_ubuntu', return_value=True):
            self.assertEqual(transport_identity(self.account), ('firefox154', UBUNTU154))
        self.compatibility(abi='Unknown_target-gcc3')
        with patch('vp_helper.browser_transport._system_ubuntu', return_value=True):
            self.assertEqual(transport_identity(self.account), DEFAULT)

    def test_standard_x86_linux_is_supported_but_unknown_arm_vendor_is_not_guessed(self):
        self.compatibility()
        self.assertEqual(transport_identity(self.account), ('firefox154', LINUX154))
        self.compatibility(abi='Linux_aarch64-gcc3')
        self.assertEqual(transport_identity(self.account), DEFAULT)

    def test_valid_matching_recorded_ua_has_precedence_over_default_platform_metadata(self):
        self.compatibility(abi='Linux_aarch64-gcc3')
        self.account['user_agent'] = UBUNTU154
        self.assertEqual(transport_identity(self.account), ('firefox154', UBUNTU154))
        self.account.pop('firefox_profile')
        self.assertEqual(transport_identity(self.account), ('firefox154', UBUNTU154))

    def test_profile_override_is_parsed_as_json_without_executing_any_script(self):
        self.compatibility()
        (self.profile/'prefs.js').write_text('user_pref("general.useragent.override", "'+UBUNTU154+'");\n')
        self.assertEqual(transport_identity(self.account), ('firefox154', UBUNTU154))
        sentinel = self.root/'must-not-exist'
        (self.profile/'prefs.js').write_text('user_pref("general.useragent.override", require("fs").writeFileSync("'+str(sentinel)+'", "x"));\n')
        self.assertEqual(transport_identity(self.account), DEFAULT)
        self.assertFalse(sentinel.exists())

    def test_privacy_override_and_conflicting_or_site_specific_preferences_fall_back(self):
        self.compatibility()
        for content in ['user_pref("privacy.resistFingerprinting", true);',
                        'user_pref("privacy.resistFingerprinting", "true");',
                        'user_pref("general.useragent.override.codeforces.com", "'+UBUNTU154+'");',
                        'user_pref("general.useragent.override", false);']:
            with self.subTest(content=content):
                (self.profile/'prefs.js').write_text(content)
                self.assertEqual(transport_identity(self.account), DEFAULT)
        (self.profile/'prefs.js').write_text('user_pref("privacy.resistFingerprinting", true);')
        (self.profile/'user.js').write_text('user_pref("privacy.resistFingerprinting", false);')
        self.assertEqual(transport_identity(self.account), DEFAULT)

    def test_unsupported_profile_version_never_uses_new_ua_with_old_tls(self):
        self.compatibility()
        self.account['user_agent'] = UBUNTU154
        with patch('vp_helper.browser_transport._supported_profiles', return_value=frozenset({'firefox147'})):
            self.assertEqual(transport_identity(self.account), DEFAULT)
        self.compatibility(version='999.0')
        self.assertEqual(transport_identity(self.account), DEFAULT)

    def test_malformed_metadata_headers_and_oversized_preferences_keep_safe_default(self):
        self.compatibility(version='154.0; execute("x")')
        self.assertEqual(transport_identity(self.account), DEFAULT)
        self.compatibility()
        (self.profile/'prefs.js').write_text('x'*(2*1024*1024+1))
        self.assertEqual(transport_identity(self.account), DEFAULT)
        (self.profile/'prefs.js').unlink()
        self.account = {'cookie_source':'firefox','user_agent':UBUNTU154+'\r\nX-Test: injected'}
        self.assertEqual(transport_identity(self.account), DEFAULT)
        self.account['user_agent'] = UBUNTU154.replace('rv:154.0','rv:147.0')
        self.assertEqual(transport_identity(self.account), DEFAULT)

    def test_chrome_and_edge_retain_actual_extension_user_agent_without_profile_reads(self):
        self.compatibility(abi='Linux_aarch64-gcc3', ubuntu=True)
        for source in ('chrome','edge'):
            with self.subTest(source=source), patch('vp_helper.browser_transport._read_small', side_effect=AssertionError('No Firefox reads')):
                account = {**self.account,'cookie_source':source,'user_agent':'Fixture Chromium Native UA'}
                self.assertEqual(transport_identity(account), ('chrome','Fixture Chromium Native UA'))
                account['user_agent'] = 'Fixture\r\nInjected'
                self.assertEqual(transport_identity(account), ('chrome',''))

    def test_absent_profile_and_unknown_user_agent_keep_the_existing_pair(self):
        self.assertEqual(transport_identity({}), DEFAULT)
        self.assertEqual(transport_identity({'cookie_source':'manual','user_agent':'fixture'}), DEFAULT)
        self.assertEqual(transport_identity({'firefox_profile':'.','user_agent':'fixture'}), DEFAULT)


if __name__ == '__main__':
    unittest.main()
