from copy import deepcopy
from dataclasses import asdict
from itertools import count
from unittest.mock import Mock, patch

from vp_helper.cf_judgements import PendingJudgements, web_verdicts
from vp_helper.codeforces_live import CodeforcesLive
from vp_helper.gym import Gym
from vp_helper.models import Contest, Reference, Rules, Submission, VPError
from vp_helper.sessions import apply_submission_sync, reserve_submission_sync
from vp_helper.storage import read_json, write_json
import test_codeforces_live as cf
from test_cf_start_sync import ImmediatePool
from test_workflows import Isolated


def table(url, verdict='OK', handle='tester', problem='A', sid='100', waiting=False):
    path = url.split('codeforces.com')[1]
    return (f'<div id="header"><a href="/profile/{handle}">{handle}</a></div>'
            f'<table><tr data-submission-id="{sid}"><td><a href="/profile/{handle}">{handle}</a>'
            f'<a href="{path}/problem/{problem}">{problem}</a></td>'
            f'<td class="status-verdict-cell" submissionId="{sid}" waiting="{str(waiting).lower()}">'
            f'<span class="submissionVerdictWrapper" submissionId="{sid}" submissionVerdict="{verdict}">'
            'Accepted</span></td></tr></table>')


class CFJudgementSyncTests(Isolated):
    def test_slower_older_poll_cannot_replace_final_or_hide_web_submission(self):
        path, state = self.cli_state()
        older, newer = deepcopy(state), deepcopy(state)
        early = reserve_submission_sync(path)
        later = reserve_submission_sync(path)
        accepted = asdict(Submission('100', 'me', 'A', 60, 'CORRECT'))
        web = asdict(Submission('101', 'me', 'A', 70, 'WRONG-ANSWER'))
        apply_submission_sync(path, newer, [accepted, web], later)
        # Earlier GET finished after the newer GET (history vs active monitor).
        result = apply_submission_sync(path, older, [dict(accepted, verdict='PENDING')], early)
        self.assertEqual({e['id']: e['verdict'] for e in result}, {'100': 'CORRECT', '101': 'WRONG-ANSWER'})
        self.assertEqual(older['sync_revision'], later)
        # A subsequent genuine rejudge is allowed to replace a previous AC.
        revision = reserve_submission_sync(path)
        apply_submission_sync(path, older, [dict(accepted, verdict='PENDING')], revision)
        self.assertEqual(read_json(path)['submissions'][0]['verdict'], 'PENDING')

    def cli_state(self):
        from vp_helper import cli
        path, state = cli.prepare_start('https://qoj.ac/contest/123')
        state['submissions'] = [asdict(Submission('100', 'me', 'A', 60, 'PENDING'))]
        write_json(path, state)
        return path, state

    def test_api_final_replaces_receipt_and_web_records_need_no_receipt_on_cf_and_gym(self):
        for segment in ('contest', 'gym'):
            api = Gym(f'https://codeforces.com/{segment}/1234', {'handle': 'tester'})
            api.start_time = 1000
            rows = []
            for sid, result in ((100, 'WRONG_ANSWER'), (101, 'OK')):
                row = cf.submission(sid, kind='VIRTUAL', verdict=result, at=1060)
                row['relativeTimeSeconds'] = 60
                row['author']['startTimeSeconds'] = 1000
                rows.append(row)
            api.api = Mock(return_value=rows)
            events = api.submissions(cf.contest())
            self.assertEqual([(e.id, e.verdict) for e in events], [('100', 'WRONG-ANSWER'), ('101', 'CORRECT')])
            api.api.return_value[0]['verdict'] = 'OK'
            self.assertEqual(api.submissions(cf.contest())[0].verdict, 'CORRECT')

    def test_real_vp_monitor_updates_pending_and_discovers_web_record_without_receipts(self):
        from vp_helper import cli
        path, state = self.cli_state()
        contest = Contest('cf', '1234', cf.URL, 'CF sync fixture', 'CF sync fixture', {'A': 'A'}, Rules(18000))
        state.update(contest=contest.to_dict(), url=cf.URL, start_time=1000)
        write_json(path, state)
        api = Gym(cf.URL, {'handle': 'tester'})
        raw = cf.submission(100, kind='VIRTUAL', verdict='TESTING', at=1060)
        raw['author']['startTimeSeconds'] = 1000
        raw['relativeTimeSeconds'] = 60
        final = deepcopy(raw)
        final['verdict'] = 'OK'
        web = deepcopy(raw)
        web.update(id=101, verdict='WRONG_ANSWER', creationTimeSeconds=1070, relativeTimeSeconds=70)
        api.api = Mock(side_effect=[[raw], [final, web]])
        api.reference = Mock(return_value=Reference())
        self.clock = 1300
        view = Mock()
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=False)
        view.input.side_effect = [True]*4 + [False]
        with patch.object(cli, 'adapter', return_value=api), patch.object(cli, 'TerminalDashboard', return_value=view), \
             patch.object(cli, 'ThreadPoolExecutor', ImmediatePool), patch('time.sleep'), \
             patch('time.monotonic', side_effect=count(0, 10)):
            cli.monitor(path)
        saved = {e['id']: e['verdict'] for e in read_json(path)['submissions']}
        self.assertEqual(saved, {'100': 'CORRECT', '101': 'WRONG-ANSWER'})
        shown = {e['id']: e['verdict'] for e in view.update.call_args.args[0].submissions}
        self.assertEqual(shown, saved)
        self.assertFalse(path.with_suffix('.receipts').exists())

    def test_failed_later_history_page_still_returns_latest_results(self):
        for native in (False, True):
            api = Gym(cf.URL, {'handle': 'tester'}) if native else CodeforcesLive(cf.URL, {'handle': 'tester'})
            api.start_time = 1000
            rows = [cf.submission(i, kind='VIRTUAL' if native else 'CONTESTANT', verdict='OK', at=1100)
                    for i in range(1, 1001)]
            for row in rows:
                row['author']['startTimeSeconds'] = 1000
                row['relativeTimeSeconds'] = 100
            api.api = Mock(side_effect=[rows, VPError('later page timed out')])
            result = api.submissions(cf.contest())
            self.assertEqual(len(result), 1000)
            self.assertFalse(api.submissions_complete)
            self.assertIn('Recent submissions synced', api.submission_message)

    def test_pending_web_confirmation_does_not_flap_and_allows_later_rejudge(self):
        feed = PendingJudgements()
        api = Mock(url=cf.URL, handle='tester', submission_message='')
        api.web.get.return_value = Mock(url=cf.URL+'/my', text=table(cf.URL))
        pending = [{'id': '100', 'team': 'me', 'problem': 'A', 'time': 60, 'verdict': 'PENDING'}]
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=0):
            self.assertEqual(feed.update(api, pending)[0]['verdict'], 'PENDING')
        api.web.get.assert_not_called()
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=8):
            self.assertEqual(feed.update(api, pending)[0]['verdict'], 'CORRECT')
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=9):
            self.assertEqual(feed.update(api, pending)[0]['verdict'], 'CORRECT')
        api.web.get.return_value.text = table(cf.URL, verdict='TESTING', waiting=True)
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=13):
            self.assertEqual(feed.update(api, pending)[0]['verdict'], 'PENDING')
        api.web.get.return_value.text = table(cf.URL, verdict='WRONG_ANSWER')
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=18):
            self.assertEqual(feed.update(api, pending)[0]['verdict'], 'WRONG-ANSWER')

    def test_seeded_receipt_missing_from_api_can_be_confirmed_without_resubmitting(self):
        feed = PendingJudgements()
        event = Submission('100', 'me', 'A', 60, 'PENDING')
        feed.seed([event])
        api = Mock(url=cf.URL, handle='tester', submission_message='')
        api.web.get.return_value = Mock(url=cf.URL+'/my', text=table(cf.URL, verdict='IDLENESS_LIMIT_EXCEEDED'))
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=0): feed.update(api, [])
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=8):
            self.assertEqual(feed.update(api, [])[0]['verdict'], 'IDLENESS-LIMIT')
        api.web.session.post.assert_not_called()

    def test_vp_adapter_web_fallback_returns_typed_final_records(self):
        api = Gym(cf.URL, {'handle': 'tester'})
        api.start_time = 1000
        api.api = Mock(return_value=[])
        api.web.get = Mock(return_value=Mock(url=cf.URL+'/my', text=table(cf.URL)))
        api.seed_submissions([Submission('100', 'me', 'A', 60, 'PENDING')])
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=0):
            self.assertEqual(api.submissions(cf.contest()), [])
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=8):
            self.assertEqual(api.submissions(cf.contest()), [Submission('100', 'me', 'A', 60, 'CORRECT')])

    def test_foreign_user_contest_problem_or_id_cannot_resolve_a_pending_receipt(self):
        event = {'100': {'id': '100', 'problem': 'A', 'verdict': 'PENDING'}}
        for html in (table(cf.URL, problem='B'), table(cf.URL, sid='101'),
                     table('https://codeforces.com/contest/999'), table(cf.URL, handle='other')):
            try:
                result = web_verdicts(html, cf.URL, 'tester', event)
            except VPError:
                result = {}
            self.assertEqual(result, {})

    def test_native_web_verdict_attribute_handles_partial_infrastructure_and_testing(self):
        event = {'100': {'id': '100', 'problem': 'A', 'verdict': 'PENDING'}}
        for raw, expected in [('PARTIAL', 'PARTIAL'), ('FAILED', 'UNAVAILABLE'),
                              ('TESTING', 'PENDING'), ('SECURITY_VIOLATED', 'SECURITY-VIOLATED')]:
            with self.subTest(raw=raw):
                result = web_verdicts(table(cf.URL, verdict=raw), cf.URL, 'tester', event)
                self.assertEqual(result['100']['verdict'], expected)
        self.assertEqual(web_verdicts(table(cf.URL, verdict='NEW_FUTURE_STATUS'), cf.URL, 'tester', event), {})

    def test_web_check_failure_retains_api_records_and_retries_without_post(self):
        feed = PendingJudgements()
        api = Mock(url=cf.URL, handle='tester', submission_message='')
        api.web.get.side_effect = VPError('web verification unavailable')
        pending = [{'id': '100', 'problem': 'A', 'verdict': 'PENDING'}]
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=0): feed.update(api, pending)
        with patch('vp_helper.cf_judgements.time.monotonic', return_value=8):
            self.assertEqual(feed.update(api, pending), pending)
        self.assertIn('verification unavailable', api.submission_message)
        api.web.session.post.assert_not_called()
