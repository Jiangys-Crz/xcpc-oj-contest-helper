from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from vp_helper import freeze, live
from vp_helper.models import Contest, Rules, VPError


def contest(**extra):
    return Contest('qoj', '123', 'https://qoj.ac/contest/123', 'CCPC Fixture', 'CCPC Fixture',
                   {'A': '1'}, Rules(18000), original_start=1000, extra=extra)


class FreezeSettingsTests(TestCase):
    def test_unknown_icpc_prompts_minutes_before_end_and_releases_at_end(self):
        c = contest(scoring='ICPC')
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='45'):
            freeze.configure(c)
        self.assertEqual(c.rules.freeze_at, 15300)
        self.assertFalse(c.rules.frozen(15299))
        self.assertTrue(c.rules.frozen(15300))
        self.assertFalse(c.rules.frozen(18000))
        self.assertEqual(c.extra['freeze_override_minutes'], 45)

    def test_default_zero_and_explicit_native_zero_are_not_confused(self):
        c = contest(freeze_known=True, scoring='ICPC')
        with patch('builtins.input') as prompt:
            freeze.configure(c)
        prompt.assert_not_called()
        self.assertIsNone(c.rules.freeze_at)
        c.extra.clear()
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value=''):
            freeze.configure(c)
        self.assertEqual(c.extra['freeze_source'], 'manual')
        self.assertIsNone(c.rules.freeze_at)

    def test_native_freeze_and_start_boundary_zero_do_not_prompt(self):
        c = contest()
        c.rules.freeze_at = 0
        with patch('builtins.input') as prompt:
            freeze.configure(c)
        prompt.assert_not_called()
        self.assertTrue(c.rules.frozen(0))
        self.assertEqual(freeze.minutes_before_end(c), 300)

    def test_validation_and_retry_never_accept_invalid_minute_values(self):
        c = contest()
        for value in (-1, 301, True, '60', 0.5):
            with self.subTest(value=value), self.assertRaises(VPError):
                freeze.set_minutes(c, value, source='room')
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['-1', '1.5', '301', '0']), patch('builtins.print'):
            freeze.configure(c)
        self.assertIsNone(c.rules.freeze_at)

    def test_room_overrides_native_and_survives_official_duration_refresh(self):
        c = contest(freeze_known=True)
        c.rules.freeze_at = 14400
        freeze.configure(c, room_minutes=30)
        updated = contest(freeze_known=True)
        updated.rules = Rules(7200, freeze_at=3600)
        freeze.retain(updated, c)
        self.assertEqual(updated.rules.freeze_at, 5400)
        self.assertEqual(updated.extra['freeze_source'], 'room')
        freeze.configure(updated, room_minutes=0)
        self.assertIsNone(updated.rules.freeze_at)

    def test_manual_icpc_ccpc_selection_overrides_name_and_is_persisted(self):
        for value, threshold in [('ICPC', None), ('CCPC', 50), ('', None)]:
            c = contest()
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value=value):
                freeze.configure_format(c)
            self.assertEqual(c.rules.problem_reveal_count, threshold)
            restored = Contest.from_dict(c.to_dict())
            with patch('builtins.input') as prompt:
                freeze.configure_format(restored)
            prompt.assert_not_called()
            self.assertEqual(restored.rules.problem_reveal_count, threshold)
        c = contest()
        with patch('builtins.input') as prompt:
            freeze.configure_format(c, room_format='CCPC')
        prompt.assert_not_called()
        self.assertEqual(c.extra['scoring'], 'ICPC')
        self.assertEqual(c.rules.problem_reveal_count, 50)

    def test_live_refresh_preserves_choice_instead_of_reverting_to_native_freeze(self):
        c = contest(scoring='ICPC')
        freeze.configure(c, room_minutes=60)
        class API:
            def snapshot(self, refreshed):
                refreshed.rules = Rules(18000)
                return {'frozen': False, 'own': None}
        with patch('vp_helper.live.platform_now', return_value=16000):
            result, error = live.fetch(API(), deepcopy(c.to_dict()), directory='unused')
        self.assertEqual(error, '')
        self.assertEqual(result[0]['rules']['freeze_at'], 14400)
        self.assertTrue(result[1]['frozen'])

    def test_missing_native_refresh_preserves_release_delay(self):
        previous = contest(freeze_known=True, freeze_source='platform')
        previous.rules = Rules(1000, freeze_at=800, unfreeze_at=1200)
        refreshed = contest()
        refreshed.rules = Rules(1100)
        freeze.retain(refreshed, previous)
        self.assertEqual(refreshed.rules.freeze_at, 900)
        self.assertEqual(refreshed.rules.unfreeze_at, 1300)

    def test_platform_filters_rules_and_never_changes_native_virtual_scoring(self):
        qoj = contest(scoring='OI')
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='3'):
            freeze.configure_format(qoj)
        self.assertEqual(qoj.extra['contest_format'], 'OI')
        self.assertEqual(qoj.extra['scoring'], 'OI')
        cf = contest(scoring='ICPC')
        cf.platform = 'cf'
        self.assertEqual(freeze.supported_formats('cf'), ('ICPC', 'CCPC'))
        with self.assertRaisesRegex(VPError, '该平台'):
            freeze.configure_format(cf, room_format='OI')
        with self.assertRaisesRegex(VPError, '原站 VP'):
            freeze.configure_format(contest(scoring='ICPC'), room_format='IOI')
