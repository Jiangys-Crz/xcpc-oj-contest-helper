from copy import deepcopy
import io
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper import cli, commands, vp
from vp_helper.codeforces_live import verify_practice
from vp_helper.gym import Gym
from vp_helper.models import Contest, Rules, StartRejected, VPError
from vp_helper.qoj import QOJ
from vp_helper.receipts import record_receipt
from vp_helper.scoring import score_submissions
from vp_helper.sessions import attempts, boundary, end_attempt, merge_records, save_state
from vp_helper.storage import read_json, state_directory, write_json
from test_workflows import Isolated, URL


class LifecycleTests(Isolated):
    def test_removed_restart_cannot_launch_or_replace_old_attempt(self):
        path, state = cli.prepare_start(URL)
        source = Path(state['directory'])/'A.cpp'
        source.write_text('saved solution')
        before = path.read_bytes()
        self.clock = 2000
        self.api.start.side_effect = StartRejected('QOJ 拒绝再次 VP')
        with patch.object(cli, 'open_state') as open_state:
            with self.assertRaisesRegex(VPError, '只支持一次'):
                cli.start(URL, restart=True, scheduled_start=self.clock)
            open_state.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(boundary(path))
        self.assertEqual(read_json(state_directory()/'current.json')['state_path'], str(path))
        self.assertEqual(source.read_text(), 'saved solution')
        failed = [s for _, s in attempts() if s.get('status') == 'failed']
        self.assertEqual(len(failed), 0)
        self.api.start.assert_called_once()

    def test_preflight_rejection_has_no_new_workspace(self):
        self.api.check_start.side_effect = StartRejected('not allowed')
        with self.assertRaises(StartRejected):
            cli.prepare_start(URL, 3000)
        self.assertEqual(attempts(), [])
        self.assertFalse((self.root/'desktop').exists())
        self.api.start.assert_not_called()

    def test_legacy_duplicate_timer_is_rejected_without_another_native_post(self):
        old, original = cli.prepare_start(URL)
        self.clock = 2000
        path, state = self.legacy_round(old,scheduled_start=3000)
        self.assertIsNone(boundary(old))
        self.clock = 3000
        self.api.start.side_effect = StartRejected('no repeat')
        with self.assertRaises(StartRejected):
            vp.activate(path, state)
        self.assertEqual(read_json(path)['status'], 'failed')
        self.assertIsNone(boundary(old))
        self.assertEqual(read_json(state_directory()/'current.json')['state_path'], str(old))
        self.api.start.assert_called_once()

    def test_scheduled_monitor_exits_on_definite_refusal(self):
        path, state = cli.prepare_start(URL, 3000)
        self.clock = 3000
        self.api.start.side_effect = StartRejected('native refusal')
        view = Mock()
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=False)
        with patch.object(cli, 'TerminalDashboard', return_value=view), patch.object(cli, 'console'):
            cli.monitor(path)
        self.api.start.assert_called_once()
        self.assertEqual(read_json(path)['status'], 'failed')

    def test_old_native_clock_is_never_used_as_local_repeat(self):
        self.api.start.return_value = 1
        self.api.start.side_effect = None
        with self.assertRaises(StartRejected):
            cli.prepare_start(URL)
        self.assertFalse(read_json(state_directory()/'current.json'))

    def test_pending_unknown_request_cannot_be_sent_by_new_command(self):
        def timeout(*args, **kwargs):
            self.api.native_post_sent = True
            raise VPError('lost response')
        self.api.start.side_effect = timeout
        path, state = cli.prepare_start(URL)
        with self.assertRaisesRegex(VPError, '尚未确认'):
            cli.prepare_start(URL)
        self.api.start.assert_called_once()
        self.assertIsNone(boundary(path))

    def test_end_only_changes_local_state_and_preserves_sources_and_score(self):
        path, state = cli.prepare_start(URL)
        source = Path(state['directory'])/'A.cpp'
        source.write_text('saved solution')
        state['submissions'] = [{'id':'1','team':'me','problem':'A','time':10,'verdict':'CORRECT'}]
        write_json(path, state)
        self.clock = 1100
        self.api.reset_mock()
        ended_path, ended = end_attempt()
        self.assertEqual(ended_path, path)
        self.assertEqual(ended['status'], 'archived')
        self.assertEqual(boundary(path), 1100)
        self.assertEqual(ended['summary']['solved'], 1)
        self.assertEqual(source.read_text(), 'saved solution')
        self.assertEqual(self.api.mock_calls, [])
        self.clock = 1200
        end_attempt(state['attempt_id'])
        self.assertEqual(boundary(path), 1100)

    def test_ending_marks_later_receipts_practice_and_stale_save_cannot_reopen_it(self):
        path, state = cli.prepare_start(URL)
        stale = deepcopy(state)
        self.clock = 1100
        end_attempt()
        self.clock = 1200
        record_receipt(read_json(Path(state['directory'])/'contest.json'), '77', 'A', self.clock)
        stale['submissions'] = [{'id':'77','team':'me','problem':'A','time':200,'verdict':'CORRECT'}]
        save_state(path, stale)
        saved = read_json(path)
        self.assertEqual(saved['status'], 'archived')
        self.assertEqual(saved['ended_at'], 1100)
        self.assertTrue(saved['submissions'][0]['practice'])
        from vp_helper.models import Submission
        self.assertEqual(score_submissions([Submission(**s) for s in saved['submissions']], ['A'], Rules(18000), 300).solved, 0)

    def test_cancelled_local_reservation_never_starts_native_vp(self):
        path, state = cli.prepare_start(URL, 3000)
        self.clock = 1500
        _, ended = end_attempt()
        self.assertEqual(ended['status'], 'cancelled')
        self.clock = 4000
        self.assertEqual(vp.activate(path, state)['status'], 'cancelled')
        self.api.start.assert_not_called()

    def test_end_ignores_live_and_bt_workspaces(self):
        for mode in ('live', 'practice'):
            path = state_directory()/f'bt-qoj-123-{mode}.json'
            contest = Contest('qoj','123',URL,'fixture','fixture',{'A':'10'},Rules(18000))
            write_json(path, {'mode':mode,'status':'running','start_time':1000,'url':URL,
                              'handle':'tester','contest':contest.to_dict()})
            write_json(state_directory()/'current.json', {'state_path':str(path)})
            with self.assertRaises(VPError):
                end_attempt()
            self.assertIsNone(boundary(path))

    def test_end_command_dispatches_without_authentication(self):
        path, state = cli.prepare_start(URL)
        self.api.reset_mock()
        with patch('sys.argv', ['xcpc','end']), patch('sys.stdout', io.StringIO()) as out:
            commands.main()
        self.assertIn('本地 VP 已结束', out.getvalue())
        self.assertEqual(self.api.mock_calls, [])

    def test_ended_local_vp_does_not_block_another_contest(self):
        path, state = cli.prepare_start(URL)
        self.clock = 1100
        end_attempt()
        self.clock = 1200
        other = 'https://qoj.ac/contest/124'
        self.api.prepare.side_effect = lambda: Contest('qoj','124',other,'other','other',{'A':'20'},Rules(18000))
        _, new = cli.prepare_start(other)
        self.assertEqual(new['url'], other)
        self.assertEqual(read_json(path)['status'], 'archived')

    def test_bt_is_not_counted_as_previous_vp(self):
        path, state = cli.prepare_start(URL)
        state['mode'] = 'practice'
        write_json(path, state)
        self.clock = 2000
        _, next_state = cli.prepare_start(URL)
        self.assertEqual(next_state['attempt'], 1)
        self.assertIsNone(boundary(path))

    def test_locally_ended_cf_allows_practice_while_remote_virtual_continues(self):
        path, state = cli.prepare_start(URL)
        url = 'https://codeforces.com/contest/123'
        state['url'] = url
        write_json(path, state)
        self.clock = 1100
        end_attempt()
        client = Mock(id='123', url=url, handle='tester', platform='cf')
        client.api.return_value = [{'author':{'participantType':'VIRTUAL','members':[{'handle':'tester'}],
                                             'startTimeSeconds':1000}}]
        verify_practice(client, 18000)
        client.handle = 'other'
        client.api.return_value[0]['author']['members'] = [{'handle':'other'}]
        with self.assertRaises(VPError):
            verify_practice(client, 18000)


class NativeRefusalTests(Isolated):
    def qoj(self, body):
        client = QOJ(URL, {'handle':'tester'})
        client._page = Mock(return_value=BeautifulSoup('<nav><a href="/user/profile/tester">me</a></nav>'+body, 'html.parser'))
        client._registration = Mock(return_value=None)
        client.web = Mock()
        return client, self.api.prepare()

    def test_qoj_finished_registration_without_new_form_is_rejected(self):
        client, contest = self.qoj('')
        client._registration.return_value = (1, 100)
        with self.assertRaises(StartRejected):
            client.start(contest, fresh=True)
        client.web.post.assert_not_called()

    def test_qoj_active_native_registration_cannot_be_a_new_attempt(self):
        client, contest = self.qoj('<form id="form-regvir"><input name="_token" value="synthetic"></form>')
        client._registration.return_value = (900, 18000)
        with self.assertRaises(StartRejected):
            client.start(contest, fresh=True)
        client.web.post.assert_not_called()

    def test_qoj_http_200_rejection_is_definite_and_not_retried(self):
        client, contest = self.qoj('<form id="form-regvir"><input name="_token" value="synthetic"></form>')
        client.web.post.return_value = Mock(content='<div class="alert-danger">Already participated</div>')
        with self.assertRaisesRegex(StartRejected, 'Already participated'):
            client.start(contest, fresh=True)
        client.web.post.assert_called_once()

    def test_cf_active_native_registration_never_falls_back_to_local_timer(self):
        for kind in ('cf', 'gym'):
            url = 'https://codeforces.com/'+('contest' if kind=='cf' else 'gym')+'/123'
            client = Gym(url, {'handle':'tester'})
            client.web = Mock()
            client.web.soup.return_value = BeautifulSoup('<div id="header"><a href="/profile/tester">me</a></div>', 'html.parser')
            client._virtual_party = Mock(return_value={'startTimeSeconds':900})
            contest = Contest(kind,'123',url,'fixture','fixture',{'A':'A'},Rules(18000))
            with self.assertRaises(StartRejected):
                client.start(contest, requested_start=1000, fresh=True)
            self.assertFalse(contest.extra.get('local_attempt'))
            client.web.post.assert_not_called()
