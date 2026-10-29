"""Native CF/Gym form structure supplied by the user, with synthetic values.

Date/offset semantics are from Codeforces.setupFormDatetimePicker in:
https://codeforces.com/codeforces.org/s/78055/js/codeforces.js?v=20160131
"""
from datetime import datetime
import os
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper.gym import Gym
from vp_helper.http import form_values
from vp_helper.models import Contest, Rules, VPError
from vp_helper.scheduling import registration_time


def registration(*, personal=True, team_checked=False):
    checked = ' checked' if not team_checked else ''
    individual = f'<input type="radio" name="takePartAs" value="personal"{checked}>' if personal else ''
    return BeautifulSoup(
        '<meta name="utc_offset" content="+03:00">'
        '<form method="post" action="/search"><input name="query"></form>'
        '<form class="contestRegistration" method="post" action="" enctype="multipart/form-data">'
        '<input type="hidden" name="csrf_token" value="fixture-token">'
        '<input type="hidden" name="action" value="formSubmitted">'
        '<input type="hidden" name="backUrl" value="">'
        '<input type="hidden" name="virtual" value="true">'
        + individual + '<input type="radio" name="takePartAs" value="team"'
        + (' checked' if team_checked else '') + '>'
        '<select name="teamId"><option value="-1"></option><option value="123" selected>Fixture Team</option></select>'
        '<input type="checkbox" name="teamMember#123" value="fixture-member" checked>'
        '<input type="hidden" name="initialDatetime" value="Oct/29/2026 12:00">'
        '<input type="hidden" name="clientTimezoneOffset" value="">'
        '<input name="startDay" value="">'
        '<input name="startTime" value="" class="virtualStartTime">'
        '<input type="submit" value="Register for virtual participation">'
        '</form>', 'html.parser')


class CFRegistrationTests(unittest.TestCase):
    def test_split_widget_uses_requested_local_time_and_east_positive_offset(self):
        when = datetime.fromisoformat('2026-10-29T20:30:10+08:00').timestamp()
        cases = (
            ('Asia/Shanghai', 'Oct/29/2026', '20:31', '480'),
            ('Asia/Kathmandu', 'Oct/29/2026', '18:16', '345'),
            ('America/New_York', 'Oct/29/2026', '08:31', '-240'),
            ('UTC', 'Oct/29/2026', '12:31', '0'),
        )
        for zone, day, clock, offset in cases:
            with self.subTest(zone=zone), patch.dict(os.environ, {'TZ': zone}):
                form = registration().select_one('form.contestRegistration')
                payload = form_values(form)
                registration_time(form, payload, when)
                self.assertEqual(payload['startDay'], day)
                self.assertEqual(payload['startTime'], clock)
                self.assertEqual(payload['clientTimezoneOffset'], offset)
                self.assertEqual(payload['initialDatetime'], 'Oct/29/2026 12:00')

    def test_minute_rounding_carries_date_and_year(self):
        form = registration().select_one('form.contestRegistration')
        payload = form_values(form)
        when = datetime.fromisoformat('2026-12-31T23:59:30+08:00').timestamp()
        with patch.dict(os.environ, {'TZ': 'Asia/Shanghai'}):
            registration_time(form, payload, when)
        self.assertEqual((payload['startDay'], payload['startTime']), ('Jan/01/2027', '00:00'))

    def test_locale_format_and_future_dst_offset_follow_selected_date(self):
        form = registration().select_one('form.contestRegistration')
        payload = form_values(form)
        payload['initialDatetime'] = '29.10.2026 12:00'
        when = datetime.fromisoformat('2026-12-01T13:00:00+00:00').timestamp()
        with patch.dict(os.environ, {'TZ': 'America/New_York'}):
            registration_time(form, payload, when)
        self.assertEqual((payload['startDay'], payload['startTime'], payload['clientTimezoneOffset']),
                         ('01.12.2026', '08:00', '-300'))

    def test_cf_and_gym_send_personal_split_form_once_then_verify_native_time(self):
        when = datetime.fromisoformat('2026-10-29T20:03:00+08:00').timestamp()
        for segment, ident in (('contest', '2181'), ('gym', '1002181')):
            for team_checked in (False, True):
                with self.subTest(segment=segment, team_checked=team_checked), patch.dict(os.environ, {'TZ': 'Asia/Shanghai'}):
                    client = Gym(f'https://codeforces.com/{segment}/{ident}', {'handle': 'tester'})
                    client.web = Mock()
                    client.web.soup.side_effect = [
                        BeautifulSoup('<div id="header"><a href="/profile/tester">me</a></div>', 'html.parser'),
                        registration(team_checked=team_checked),
                    ]
                    client.web.post.return_value = Mock(content='<div>registered</div>')
                    client._virtual_party = Mock(side_effect=[None, {'startTimeSeconds': when}])
                    client.before_native_post = Mock()
                    contest = Contest(client.platform, ident, client.url, 'Fixture contest', 'Fixture contest', {'A': 'A'}, Rules(18000))
                    self.assertEqual(client.start(contest, requested_start=when, fresh=True), when)
                    client.web.post.assert_called_once()
                    target, payload = client.web.post.call_args.args
                    self.assertEqual(target, f'https://codeforces.com/contestRegistration/{ident}/virtual/true')
                    self.assertEqual(payload, {
                        'csrf_token': 'fixture-token', 'action': 'formSubmitted', 'backUrl': '', 'virtual': 'true',
                        'takePartAs': 'personal', 'teamId': '-1', 'initialDatetime': 'Oct/29/2026 12:00',
                        'clientTimezoneOffset': '480', 'startDay': 'Oct/29/2026', 'startTime': '20:03',
                    })
                    client.before_native_post.assert_called_once()
                    self.assertEqual(client._virtual_party.call_count, 2)

    def test_unsupported_widget_or_team_only_form_does_not_send_registration(self):
        for broken in ('missing-offset', 'unknown-date', 'team-only'):
            with self.subTest(broken=broken):
                page = registration(personal=broken != 'team-only')
                if broken == 'missing-offset':
                    page.select_one('[name="clientTimezoneOffset"]').decompose()
                elif broken == 'unknown-date':
                    page.select_one('[name="initialDatetime"]')['value'] = 'unsupported'
                client = Gym('https://codeforces.com/contest/2181', {'handle': 'tester'})
                client.web = Mock()
                client.web.soup.side_effect = [
                    BeautifulSoup('<div id="header"><a href="/profile/tester">me</a></div>', 'html.parser'), page]
                client._virtual_party = Mock(return_value=None)
                client.before_native_post = Mock()
                contest = Contest('cf', '2181', client.url, 'Fixture', 'Fixture', {}, Rules(18000))
                with self.assertRaises(VPError):
                    client.start(contest, requested_start=2000000000, fresh=True)
                client.web.post.assert_not_called()
                client.before_native_post.assert_not_called()
