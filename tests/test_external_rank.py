from concurrent.futures import Future
from contextlib import ExitStack
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from isolation import isolate_local_state
from test_rank_xcpcio import PAGE, fixture
from vp_helper import cli, external_rank
from vp_helper.models import Contest, Medals, Reference, Rules, VPError
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

    def test_unrelated_directory_and_non_vp_modes_are_rejected(self):
        write_json(state_directory() / 'current.json', {'state_path': str(self.path)})
        with patch('pathlib.Path.cwd', return_value=self.root), self.assertRaises(VPError):
            external_rank.current_attempt()
        for change in ({'mode': 'practice'}, {'mode': 'live'}, {'status': 'failed'}, {'status': 'cancelled'}):
            write_json(self.path, {**self.state, **change})
            with self.subTest(change=change), patch('pathlib.Path.cwd', return_value=Path(self.state['directory'])), \
                 self.assertRaises(VPError):
                external_rank.current_attempt()

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
