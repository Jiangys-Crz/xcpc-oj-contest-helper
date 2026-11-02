"""Web and third-party submissions do not require a local submit receipt."""
from datetime import datetime, timezone
from unittest.mock import Mock

from bs4 import BeautifulSoup

from vp_helper import cli
from vp_helper.gym import Gym
from vp_helper.models import Contest, Rules, Submission, VPError
from vp_helper.qoj import QOJ
from vp_helper.scoring import score_submissions
from vp_helper.sessions import end_attempt, merge_submissions, save_state
from vp_helper.storage import read_json, write_json
from test_workflows import Isolated, URL


class ExternalSubmissionTests(Isolated):
    def qoj(self, rows):
        client = QOJ(URL, {'handle': 'tester'})
        client.web.get = Mock(side_effect=VPError('No detail metadata in this fixture'))
        client.start_time = self.clock
        client._page = Mock(return_value=BeautifulSoup(
            '<table><thead><tr><th>#</th><th>Problem</th><th>Submitter</th><th>Score</th><th>Submit time</th></tr></thead>'
            '<tbody>' + ''.join(rows) + '</tbody></table>', 'html.parser'))
        client._registration = Mock(return_value=(self.clock, 18000))
        return client

    def row(self, sid, at, *, handle='tester', ident='123', relative=False):
        stamp = ('00:01:00' if relative else
                 '<time datetime="' + datetime.fromtimestamp(at, timezone.utc).isoformat() + '"></time>')
        native = f'<sup><a href="/contest/{ident}">#</a></sup>' if relative else ''
        return (f'<tr><td><a href="/submission/{sid}">{sid}</a>{native}</td>'
                f'<td><a href="/contest/{ident}/problem/10">A</a></td>'
                f'<td><a class="uoj-username" href="/user/profile/{handle}">{handle}</a></td>'
                f'<td>100</td><td>{stamp}</td></tr>')

    def test_qoj_reads_web_submissions_without_receipts_and_filters_user_contest_and_time(self):
        self.clock = datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
        path, state = cli.prepare_start(URL)
        client = self.qoj([
            self.row('100', self.clock+60), self.row('101', self.clock+61, handle='other'),
            self.row('102', self.clock+62, ident='456'), self.row('103', self.clock-1),
        ])
        result = merge_submissions(path, state, client.submissions(Contest.from_dict(state['contest'])))
        self.assertEqual([(s.id, s.time, s.verdict, s.practice) for s in result], [('100', 60, 'CORRECT', False)])
        save_state(path, {**state, 'submissions': [vars(s) for s in result]})
        self.assertEqual(read_json(path)['submissions'][0]['id'], '100')
        self.assertFalse(path.with_suffix('.receipts').exists())

    def test_qoj_reads_relative_web_submission_against_verified_native_start(self):
        self.clock = datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
        path, state = cli.prepare_start(URL)
        client = self.qoj([self.row('100', self.clock+60, relative=True)])
        result = merge_submissions(path, state, client.submissions(Contest.from_dict(state['contest'])))
        self.assertEqual([(s.id, s.time) for s in result], [('100', 60)])
        client._registration.assert_called_once()
        self.assertFalse(path.with_suffix('.receipts').exists())

    def test_cf_and_gym_read_matching_native_web_submissions_without_receipts(self):
        for platform, segment in (('cf', 'contest'), ('gym', 'gym')):
            client = Gym(f'https://codeforces.com/{segment}/123', {'handle': 'tester'})
            client.start_time = self.clock
            own = {'participantType': 'VIRTUAL', 'startTimeSeconds': self.clock, 'members': [{'handle': 'tester'}]}
            raw = {'id': 100, 'contestId': 123, 'creationTimeSeconds': self.clock+60,
                   'relativeTimeSeconds': 60, 'author': own, 'problem': {'index': 'A'}, 'verdict': 'OK'}
            client.api = Mock(return_value=[raw,
                {**raw, 'id': 101, 'author': {**own, 'members': [{'handle': 'other'}]}},
                {**raw, 'id': 102, 'author': {**own, 'startTimeSeconds': self.clock-100}},
                {**raw, 'id': 103, 'contestId': 456},
            ])
            contest = Contest(platform, '123', client.url, 'Fixture', 'Fixture', {'A': 'A'}, Rules(18000))
            result = client.submissions(contest)
            self.assertEqual([(s.id, s.time, s.verdict) for s in result], [('100', 60, 'CORRECT')])

    def test_web_practice_after_natural_end_is_retained_without_receipt(self):
        path, state = cli.prepare_start(URL)
        result = merge_submissions(path, state, [Submission('100', 'me', 'A', 18060, 'CORRECT')])
        self.assertTrue(result[0].practice)
        self.assertEqual(score_submissions(result, ['A'], Rules(18000), 18100).solved, 0)
        self.assertFalse(path.with_suffix('.receipts').exists())

    def test_web_practice_after_manual_end_is_saved_and_never_changes_vp_score(self):
        path, state = cli.prepare_start(URL)
        state['submissions'] = [vars(Submission('99', 'me', 'A', 60, 'WRONG-ANSWER'))]
        write_json(path, state)
        self.clock = 1100
        _, ended = end_attempt(state['attempt_id'])
        records = merge_submissions(path, ended, [Submission('100', 'me', 'A', 120, 'CORRECT')])
        self.assertEqual([(s.id, s.practice) for s in records], [('99', False), ('100', True)])
        save_state(path, {**ended, 'submissions': [vars(s) for s in records]})
        saved = read_json(path)
        self.assertTrue(saved['submissions'][1]['practice'])
        self.assertEqual(saved['summary']['solved'], 0)
        self.assertEqual(saved['start_time'], 1000)
        self.assertFalse(path.with_suffix('.receipts').exists())
