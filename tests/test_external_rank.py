from concurrent.futures import Future
from contextlib import ExitStack
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from isolation import isolate_local_state
from test_rank_xcpcio import PAGE, fixture
from vp_helper import cli, cloud, external_rank
from vp_helper.models import Contest, Medals, Reference, Rules, Submission, VPError
from vp_helper.rank_xcpcio import parse
from vp_helper.scoring import score_submissions
from vp_helper.sessions import save_state
from vp_helper.storage import read_json, state_directory, write_json


class ExternalRankTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        isolate_local_state(self.stack, self.root)
        self.contest = Contest('gym', '123', 'https://codeforces.com/gym/123',
                               'Fixture VP', 'Fixture VP', {'A': 'A', 'B': 'B'},
                               Rules(18000), medals=Medals(2, 3, 4, 'native'))
        self.path, self.state = self.saved('one')
        self.stdout = self.stack.enter_context(patch('sys.stdout', new_callable=io.StringIO))

    def saved(self, ident):
        directory = self.root / ('contest-' + ident)
        directory.mkdir()
        path = state_directory() / ('vp-gym-123-' + ident + '.json')
        state = {'contest': self.contest.to_dict(), 'url': self.contest.url,
                 'start_time': 1000, 'mode': 'vp', 'status': 'running', 'handle': 'tester',
                 'directory': str(directory), 'attempt_id': ident,
                 'submissions': [], 'recent': [], 'history_path': str(self.root / 'history.csv')}
        write_json(path, state)
        write_json(directory / 'contest.json', {'vp_context': {
            'state_path': str(path), 'url': self.contest.url, 'handle': 'tester'}})
        return path, state

    def dataset(self, title='正式队'):
        data = fixture()
        data[1][0]['name'] = title
        return parse(*data, self.contest, PAGE)

    def import_board(self, title='正式队', path=None, state=None):
        with patch('vp_helper.rank_xcpcio.load', return_value=self.dataset(title)):
            return external_rank.import_rank(path or self.path, state or self.state, PAGE)

    def test_import_does_not_touch_vp_clock_submissions_or_state(self):
        before = self.path.read_bytes()
        payload = self.import_board()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(payload['source'], PAGE)
        self.assertEqual(len(payload['events']), 5)
        self.assertEqual(read_json(external_rank.attachment_path(self.path)), payload)

    def test_failed_download_or_validation_preserves_old_attachment_and_state(self):
        self.import_board()
        before = external_rank.attachment_path(self.path).read_bytes(), self.path.read_bytes()
        bad = self.dataset()
        bad[0]['duration'] = 14400
        for provider in (Mock(side_effect=VPError('download failed')), Mock(return_value=bad)):
            with patch('vp_helper.rank_xcpcio.load', provider), self.assertRaises(VPError):
                external_rank.import_rank(self.path, self.state, PAGE)
            self.assertEqual((external_rank.attachment_path(self.path).read_bytes(), self.path.read_bytes()), before)

    def test_wrong_source_or_scoring_is_rejected_before_network(self):
        for value, contest in (
                ('https://evil.test/icpc/51st/wuhan', self.contest),
                (PAGE + '?redirect=other', self.contest),
                (PAGE, Contest.from_dict({**self.contest.to_dict(), 'extra': {'scoring': 'OI'}}))):
            with patch('vp_helper.rank_xcpcio.load') as provider, self.assertRaises(VPError):
                external_rank.prepare_import(value, contest)
            provider.assert_not_called()

    def test_binding_uses_current_directory_or_children(self):
        directory = Path(self.state['directory'])
        child = directory / 'solutions' / 'A'
        child.mkdir(parents=True)
        for here in (directory, child):
            with self.subTest(here=here), patch('pathlib.Path.cwd', return_value=here):
                path, state = external_rank.current_attempt()
                self.assertEqual(path, self.path)
                self.assertEqual(state['attempt_id'], 'one')

    def test_unrelated_directory_and_practice_or_failed_sessions_are_rejected(self):
        write_json(state_directory() / 'current.json', {'state_path': str(self.path)})
        with patch('pathlib.Path.cwd', return_value=self.root), self.assertRaises(VPError):
            external_rank.current_attempt()
        for change in ({'mode': 'practice'}, {'status': 'failed'}, {'status': 'cancelled'}):
            write_json(self.path, {**self.state, **change})
            with self.subTest(change=change), patch('pathlib.Path.cwd', return_value=Path(self.state['directory'])), \
                 self.assertRaises(VPError):
                external_rank.current_attempt()

    def test_icpc_live_session_can_select_an_external_board(self):
        write_json(self.path,{**self.state,'mode':'live'})
        with patch('pathlib.Path.cwd',return_value=Path(self.state['directory'])):
            self.assertEqual(external_rank.current_attempt()[1]['mode'],'live')

    def test_copied_contest_config_cannot_rebind_other_directory(self):
        unrelated = self.root / 'copied'
        unrelated.mkdir()
        write_json(unrelated / 'contest.json', {'vp_context': {'state_path': str(self.path)}})
        with patch('pathlib.Path.cwd', return_value=unrelated), self.assertRaises(VPError):
            external_rank.current_attempt()

    def test_two_attempts_stay_isolated_and_old_state_writer_cannot_erase_import(self):
        other, state = self.saved('two')
        old = deepcopy(self.state)
        payload = self.import_board()
        self.assertFalse(external_rank.attachment_path(other).exists())
        self.import_board('另一场外榜', other, state)
        save_state(self.path, old)
        self.assertEqual(read_json(external_rank.attachment_path(self.path)), payload)
        self.assertNotEqual(read_json(external_rank.attachment_path(other))['metadata']['teams'], payload['metadata']['teams'])

    def test_import_replace_clear_hot_reload_and_restart(self):
        watch = external_rank.ManualRank(self.path, self.contest)
        self.assertIsNone(watch.reference(self.contest, 200))
        self.import_board()
        first = watch.reference(self.contest, 200)
        self.assertTrue(first.complete)
        self.assertEqual(first.rows[0].solved, 1)
        self.assertEqual(self.contest.medals.gold, 1)
        self.import_board('第二份榜单')
        second = watch.reference(self.contest, 200)
        self.assertIn('第二份榜单', second.rows[0].name)
        fresh_contest = Contest.from_dict(self.state['contest'])
        restarted = external_rank.ManualRank(self.path, fresh_contest)
        self.assertIn('第二份榜单', restarted.reference(fresh_contest, 200).rows[0].name)
        with patch('vp_helper.external_rank.current_attempt', return_value=(self.path, self.state)):
            external_rank.command(['--clear'])
        self.assertIsNone(watch.reference(self.contest, 200))
        self.assertEqual(self.contest.medals, Medals(2, 3, 4, 'native'))
        self.assertFalse(external_rank.attachment_path(self.path).exists())

    def test_clear_after_saved_contest_restart_restores_native_medals(self):
        self.import_board()
        watch = external_rank.ManualRank(self.path, self.contest)
        watch.reference(self.contest, 200)
        saved_contest = Contest.from_dict(self.contest.to_dict())
        restarted = external_rank.ManualRank(self.path, saved_contest)
        restarted.reference(saved_contest, 200)
        external_rank.attachment_path(self.path).unlink()
        self.assertIsNone(restarted.reference(saved_contest, 200))
        self.assertEqual(saved_contest.medals, Medals(2, 3, 4, 'native'))

    def test_corrupt_attachment_never_silently_uses_native_rank(self):
        self.import_board()
        watch = external_rank.ManualRank(self.path, self.contest)
        watch.reference(self.contest, 200)
        external_rank.attachment_path(self.path).write_text('not JSON', encoding='utf8')
        reference = watch.reference(self.contest, 200)
        self.assertIsNotNone(reference)
        self.assertFalse(reference.complete)
        self.assertIn('外榜加载失败', reference.message)

    def test_rounding_survives_stale_state_writes_and_clear_restores_native_rule(self):
        old = deepcopy(self.state)
        metadata, events, _ = self.dataset()
        metadata['penalty_rounding'] = 'total_seconds'
        rules = Rules(18000, penalty_rounding='total_seconds')
        final = []
        for team in metadata['teams']:
            row = score_submissions(events, ['A', 'B'], rules, 18000,
                                    team_id=team['id'], name=team['name'])
            row.eligible = team['official']
            final.append(row)
        with patch('vp_helper.rank_xcpcio.load', return_value=(metadata, events, final)):
            external_rank.import_rank(self.path, self.state, PAGE)
        watch = external_rank.ManualRank(self.path, self.contest)
        watch.reference(self.contest, 200)
        self.assertEqual(self.contest.rules.penalty_rounding, 'total_seconds')
        save_state(self.path, old)
        saved = read_json(self.path)
        self.assertEqual(saved['contest']['rules']['penalty_rounding'], 'total_seconds')
        restarted = Contest.from_dict(saved['contest'])
        external_rank.attachment_path(self.path).unlink()
        self.assertIsNone(external_rank.ManualRank(self.path, restarted).reference(restarted, 200))
        self.assertEqual(restarted.rules.penalty_rounding, 'per_problem')
        save_state(self.path, old)
        self.assertEqual(read_json(self.path)['contest']['rules']['penalty_rounding'], 'per_problem')

    def test_interactive_command_and_room_source_constraints(self):
        with patch('vp_helper.external_rank.current_attempt', return_value=(self.path, self.state)), \
             patch('builtins.input', return_value=PAGE), \
             patch('vp_helper.rank_xcpcio.load', return_value=self.dataset()):
            external_rank.command([])
        self.assertTrue(external_rank.attachment_path(self.path).is_file())
        room = {**self.state, 'cloud': {'externalRankUrl': PAGE}}
        with patch('vp_helper.external_rank.current_attempt', return_value=(self.path, room)), \
             self.assertRaises(VPError):
            external_rank.command(['--clear'])
        with patch('vp_helper.external_rank.current_attempt', return_value=(self.path, room)), \
             self.assertRaises(VPError):
            external_rank.command(['https://board.xcpcio.com/icpc/51st/other'])

    def test_room_source_uses_valid_cache_and_downloads_if_missing(self):
        with patch('vp_helper.rank_xcpcio.load', return_value=self.dataset()) as provider:
            external_rank.sync_room_rank(self.path, self.state, PAGE)
            external_rank.sync_room_rank(self.path, self.state, PAGE)
        provider.assert_called_once()

    def test_cloud_replays_manual_rank_without_dashboard_and_uses_current_vp_time(self):
        self.import_board()
        self.assertNotIn('reference_rank', self.state)
        for elapsed, rank in ((100, '1'), (200, '2'), (18000, '2')):
            with patch.object(cloud, 'platform_now', return_value=1000+elapsed):
                payload = cloud.sync_payload(self.path, self.state)
            self.assertEqual((payload['rank'], payload['rankSource']), (rank, 'original'))
            self.assertEqual(payload['externalRankUrl'], PAGE)
        self.assertNotIn('reference_rank', read_json(self.path))

    def test_cloud_prizes_use_final_board_only_after_natural_or_local_end(self):
        payload=self.import_board()
        payload['metadata']['medals']={'gold':1,'silver':1,'bronze':0}
        write_json(external_rank.attachment_path(self.path),payload)
        self.state['submissions']=[asdict(Submission('mine','me','A',90,'CORRECT'))]
        with patch.object(cloud,'platform_now',return_value=1200):
            live=cloud.sync_payload(self.path,self.state)
        self.assertEqual(live['rank'],'1')
        self.assertIsNone(live['prize'])
        self.state['status']='complete'
        with patch.object(cloud,'platform_now',return_value=20000):
            final=cloud.sync_payload(self.path,self.state)
        self.assertEqual((final['rank'],final['prize'],final['prizeSource']),('2','Silver','original'))
        self.state.update(status='archived',ended_at=1200,submissions_complete=True)
        write_json(self.path.with_suffix('.closed'),{'capture_until':1200})
        with patch.object(cloud,'platform_now',return_value=1300):
            stopped=cloud.sync_payload(self.path,self.state)
        self.assertEqual((stopped['rank'],stopped['prize']),('2','Silver'))
        self.path.with_suffix('.closed').unlink()
        self.state.update(mode='live',status='running',snapshot={'own':{'rank':91}})
        with patch.object(cloud,'platform_now',return_value=1200):
            active_live=cloud.sync_payload(self.path,self.state)
        self.assertEqual((active_live['rank'],active_live['rankSource'],active_live['prize']),('91','native',None))
        self.state['status']='complete'
        with patch.object(cloud,'platform_now',return_value=20000):
            ended_live=cloud.sync_payload(self.path,self.state)
        self.assertEqual((ended_live['rank'],ended_live['prize']),('2','Silver'))
        self.state['submissions']=[asdict(Submission('mine','me','A',90,'PENDING'))]
        self.assertNotEqual(cloud.sync_payload(self.path,self.state)['prizeSource'],'original')

    def test_locally_ended_terminal_shows_final_external_rank_and_prize(self):
        from test_cf_start_sync import ImmediatePool
        payload=self.import_board();payload['metadata']['medals']={'gold':1,'silver':1,'bronze':0}
        write_json(external_rank.attachment_path(self.path),payload)
        incoming=[Submission('mine','me','A',90,'CORRECT')]
        self.state.update(status='archived',ended_at=1200,submissions=[asdict(e) for e in incoming])
        write_json(self.path,self.state);write_json(self.path.with_suffix('.closed'),{'capture_until':1200})
        client=Mock(handle='tester',submissions_complete=True);client.submissions.return_value=incoming
        view=Mock();view.__enter__=Mock(return_value=view);view.__exit__=Mock(return_value=False)
        view.input.side_effect=[True]*6+[False]
        with patch('pathlib.Path.cwd',return_value=Path(self.state['directory'])), \
             patch.object(cli,'adapter',return_value=client),patch.object(cli,'TerminalDashboard',return_value=view), \
             patch.object(cli,'ThreadPoolExecutor',ImmediatePool),patch('vp_helper.windows_terminal.track_monitor'), \
             patch('time.sleep'),patch('time.monotonic',side_effect=iter(range(1,1000))), \
             patch.object(cli,'platform_now',return_value=1300):
            cli.monitor(self.path)
        final=view.update.call_args.args[0]
        self.assertEqual((final.rank,final.prize),('2','Silver'))
        saved=read_json(self.path)
        self.assertEqual(saved['summary']['prize'],'Silver')
        self.assertEqual(saved['public_statistics']['accepted']['B'],1)

    def test_broken_manual_rank_does_not_upload_a_cached_native_rank(self):
        self.import_board()
        self.state['reference_rank'] = {'source':'original','contestUrl':self.contest.url,
            'rank':'1','officialParticipants':2,'scoring':'ICPC','scoreKey':[0,0]}
        external_rank.attachment_path(self.path).write_text('not JSON', encoding='utf8')
        payload = cloud.sync_payload(self.path, self.state)
        self.assertEqual((payload['rank'], payload['rankSource']), ('—', 'unavailable'))
        self.assertNotIn('externalRankUrl', payload)
        external_rank.attachment_path(self.path).unlink()
        self.assertIsNone(cloud.sync_payload(self.path, self.state)['externalRankUrl'])

    def test_finished_medal_remains_visible_while_next_submission_poll_is_running(self):
        self.import_board()
        incoming = [Submission('mine-a','me','A',1,'CORRECT'), Submission('mine-b','me','B',2,'CORRECT')]
        self.state['submissions'] = [asdict(event) for event in incoming]
        write_json(self.path, self.state)
        client = Mock(handle='tester', submissions_complete=True)
        calls = []
        class Pool:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, function, *args):
                calls.append(args)
                future = Future()
                if len(calls) == 1:
                    client.submissions_complete = True
                    future.set_result((incoming, ''))
                else:
                    # QOJ clears its public flag at the beginning of a GET.
                    client.submissions_complete = False
                return future
        view = Mock()
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=False)
        view.input.side_effect = [True]*12+[False]
        with patch('pathlib.Path.cwd', return_value=Path(self.state['directory'])), \
             patch.object(cli, 'adapter', return_value=client), \
             patch.object(cli, 'TerminalDashboard', return_value=view), \
             patch.object(cli, 'ThreadPoolExecutor', Pool), \
             patch('vp_helper.windows_terminal.track_monitor'), \
             patch('time.sleep'), patch('time.monotonic', side_effect=iter(range(1, 1000))), \
             patch.object(cli, 'platform_now', return_value=20000):
            cli.monitor(self.path)
        self.assertGreaterEqual(len(calls), 2)
        prizes = [call.args[0].prize for call in view.update.call_args_list]
        self.assertIn('Gold', prizes)
        self.assertTrue(all(prize == 'Gold' for prize in prizes[prizes.index('Gold'):]), prizes)

    def test_late_native_reference_cannot_overwrite_newly_imported_rank(self):
        native = Reference([], complete=True, statistics_complete=True, message='late native board')
        pending = Future()
        class Pool:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, function, *args):
                if len(args) == 4 and args[-1] is True:
                    return pending
                result = Future()
                result.set_result(([], ''))
                return result
        turns = iter(range(3))
        def next_frame():
            turn = next(turns)
            if turn == 1:
                self.import_board()
                pending.set_result((native, ''))
            return turn < 2
        view = Mock()
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=False)
        view.input.side_effect = next_frame
        client = Mock(handle='tester', submissions_complete=True)
        with patch('pathlib.Path.cwd', return_value=Path(self.state['directory'])), \
             patch.object(cli, 'adapter', return_value=client), \
             patch.object(cli, 'TerminalDashboard', return_value=view), \
             patch.object(cli, 'ThreadPoolExecutor', Pool), \
             patch('vp_helper.windows_terminal.track_monitor'), \
             patch('time.sleep'), patch('time.monotonic', side_effect=iter(range(1, 100))), \
             patch.object(cli, 'platform_now', return_value=1200):
            cli.monitor(self.path)
        final = view.update.call_args.args[0]
        # Only E70 is an eligible team ahead of our empty score; E71 is starred.
        self.assertEqual(final.rank, '2')
        self.assertNotIn('late native board', final.message)
        self.assertEqual(read_json(self.path)['start_time'], 1000)
        self.assertTrue(read_json(external_rank.attachment_path(self.path)))
