from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from itertools import count
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from isolation import isolate_local_state
from test_cf_start_sync import ImmediatePool
from test_rank_xcpcio import PAGE, fixture
from vp_helper import cli, external_rank
from vp_helper.models import Contest, Reference, Rules, Submission
from vp_helper.rank_xcpcio import parse
from vp_helper.sessions import save_state
from vp_helper.storage import read_json, state_directory, write_json
from vp_helper.workspaces import promote


class PracticeReferenceSummaryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        isolate_local_state(self.stack, self.root)
        self.contest = Contest('qoj', '123', 'https://qoj.ac/contest/123',
                               'Practice fixture', 'Practice fixture', {'A': 'A', 'B': 'B'},
                               Rules(18000, freeze_at=14400, problem_reveal_count=50))
        self.directory = self.root / 'contest'
        self.directory.mkdir()
        self.path = state_directory() / 'vp-qoj-123-practice.json'
        self.vp_submission = Submission('mine-a', 'me', 'A', 90, 'CORRECT')
        self.state = {'contest': self.contest.to_dict(), 'url': self.contest.url,
                      'start_time': 1000, 'mode': 'vp', 'status': 'complete',
                      'practice_view': True, 'practice_rank': '—', 'handle': 'tester',
                      'directory': str(self.directory), 'attempt_id': 'practice',
                      'submissions': [asdict(self.vp_submission)], 'recent': [],
                      'history_path': str(self.root / 'history.csv')}
        write_json(self.path, self.state)
        write_json(self.directory / 'contest.json', {'vp_context': {
            'state_path': str(self.path), 'url': self.contest.url, 'handle': 'tester'}})

    def import_board(self):
        with patch('vp_helper.rank_xcpcio.load', return_value=parse(*fixture(), self.contest, PAGE)):
            external_rank.import_rank(self.path, read_json(self.path), PAGE)

    def monitor(self, now=20000, incoming=None, reference=None):
        client = Mock(handle='tester', submissions_complete=True)
        client.submissions.return_value = incoming if incoming is not None else [self.vp_submission]
        client.reference.return_value = reference if reference is not None else Reference()
        view = Mock()
        view.__enter__ = Mock(return_value=view)
        view.__exit__ = Mock(return_value=False)
        view.input.side_effect = [True] * 6 + [False]
        with patch('pathlib.Path.cwd', return_value=self.directory), \
             patch.object(cli, 'adapter', return_value=client), \
             patch.object(cli, 'TerminalDashboard', return_value=view), \
             patch.object(cli, 'ThreadPoolExecutor', ImmediatePool), \
             patch('vp_helper.windows_terminal.track_monitor'), \
             patch('time.sleep'), patch('time.monotonic', side_effect=count(1)), \
             patch.object(cli, 'platform_now', return_value=now):
            cli.monitor(self.path)
        return view.update.call_args.args[0], read_json(self.path)

    def assert_final_reference(self, dashboard, state):
        self.assertTrue(dashboard.practice)
        # The official E70 team has two solves. The starred team with one AC
        # and the zero-solve official team do not place ahead of our VP score.
        self.assertEqual(dashboard.rank, '2')
        self.assertEqual(state['practice_rank'], '2')
        self.assertEqual(state['summary']['rank'], '2')
        self.assertEqual(state['reference_rank']['externalRankUrl'], PAGE)
        self.assertEqual(state['reference_rank']['officialParticipants'], 2)
        self.assertEqual((dashboard.me.solved, dashboard.me.penalty), (1, 1))
        self.assertEqual(dashboard.revealed, frozenset({'A', 'B'}))
        self.assertEqual(dashboard.stats, {
            'submitted': {'A': 4, 'B': 1},
            'attempted': {'A': 2, 'B': 1},
            'accepted': {'A': 2, 'B': 1},
        })
        self.assertEqual(state['public_statistics'], dashboard.stats)

    def test_finished_vp_replaces_placeholder_and_mid_vp_rank_with_final_external_rank(self):
        self.import_board()
        for old_rank in ('—', '1'):
            with self.subTest(old_rank=old_rank):
                write_json(self.path, {**self.state, 'practice_rank': old_rank})
                dashboard, state = self.monitor()
                self.assert_final_reference(dashboard, state)
                self.assertEqual(state['revealed_problems'], ['A', 'B'])

    def test_active_ccpc_freeze_shows_pending_blue_while_preserving_actual_submission(self):
        self.import_board()
        write_json(self.path, {**self.state, 'practice_view': False, 'status': 'running'})
        post_freeze = Submission('mine-b', 'me', 'B', 14400, 'CORRECT')
        dashboard, state = self.monitor(now=15600, incoming=[self.vp_submission, post_freeze])
        self.assertEqual(dashboard.me.solved, 1)
        self.assertEqual(dashboard.me.cells['B'].pending, 1)
        self.assertFalse(dashboard.me.cells['B'].solved)
        self.assertEqual(dashboard.stats['submitted']['B'], 0)
        self.assertEqual(dashboard.stats['attempted']['B'], 0)
        self.assertEqual(dashboard.stats['accepted']['B'], 0)
        self.assertEqual(state['summary']['solved'], 2)
        self.assertEqual(next(row['verdict'] for row in state['submissions'] if row['id']=='mine-b'), 'CORRECT')
        output = StringIO()
        Console(file=output, force_terminal=True, color_system='truecolor', no_color=False, width=100).print(dashboard)
        self.assertIn('48;2;37;99;235', output.getvalue())

    def test_late_import_repairs_previously_unavailable_bt_rank(self):
        dashboard, state = self.monitor()
        self.assertEqual(dashboard.rank, '—')
        self.assertIsNone(dashboard.stats)
        self.import_board()
        dashboard, state = self.monitor()
        self.assert_final_reference(dashboard, state)

    def test_natural_end_practice_ac_keeps_vp_score_rank_and_original_statistics(self):
        self.import_board()
        self.monitor()
        incoming = [self.vp_submission, Submission('practice-b', 'me', 'B', 18001, 'CORRECT')]
        dashboard, state = self.monitor(incoming=incoming)
        self.assert_final_reference(dashboard, state)
        self.assertFalse(dashboard.me.cells['B'].solved)
        self.assertTrue(next(record for record in dashboard.submissions if record['id'] == 'practice-b')['practice'])

    def test_early_end_reveals_final_ccpc_counts_and_excludes_new_practice_ac(self):
        self.import_board()
        write_json(self.path, {**self.state, 'status': 'archived', 'ended_at': 1200})
        write_json(self.path.with_suffix('.closed'), {'capture_until': 1200, 'reason': 'manual'})
        incoming = [self.vp_submission, Submission('practice-b', 'me', 'B', 201, 'CORRECT')]
        dashboard, state = self.monitor(now=1300, incoming=incoming)
        self.assert_final_reference(dashboard, state)
        self.assertEqual(state['status'], 'archived')
        self.assertEqual(state['statistics_at'], 19000)
        self.assertTrue(next(record for record in state['submissions'] if record['id'] == 'practice-b')['practice'])

    def test_reopen_and_stale_writes_cannot_restore_old_placeholder_rank(self):
        stale = deepcopy(self.state)
        self.import_board()
        self.monitor()
        save_state(self.path, stale)
        self.assertEqual(read_json(self.path)['practice_rank'], '2')
        dashboard, state = self.monitor()
        self.assert_final_reference(dashboard, state)

    def test_broken_or_cleared_external_board_does_not_display_its_cached_rank(self):
        self.import_board()
        dashboard, state = self.monitor()
        self.assert_final_reference(dashboard, state)
        attachment = external_rank.attachment_path(self.path)
        attachment.write_text('not JSON', encoding='utf8')
        dashboard, state = self.monitor()
        self.assertIn(dashboard.rank, ('—', '?'))
        self.assertEqual(state['summary']['rank'], dashboard.rank)
        self.assertIsNone(dashboard.stats)
        attachment.unlink()
        dashboard, state = self.monitor()
        self.assertEqual(dashboard.rank, '—')
        self.assertEqual(state['summary']['rank'], '—')

    def test_stale_write_preserves_external_provenance_when_board_becomes_unavailable(self):
        for broken in (False, True):
            with self.subTest(broken=broken):
                write_json(self.path, self.state)
                stale = deepcopy(self.state)
                self.import_board()
                self.monitor()
                save_state(self.path, stale)
                attachment = external_rank.attachment_path(self.path)
                if broken:
                    attachment.write_text('not JSON', encoding='utf8')
                else:
                    attachment.unlink()
                dashboard, state = self.monitor()
                self.assertIn(dashboard.rank, ('—', '?'))
                self.assertEqual(state['summary']['rank'], dashboard.rank)
                self.assertIsNone(dashboard.stats)
                self.assertEqual(state['reference_rank']['externalRankUrl'], PAGE)
                # Removing a broken attachment must not expose its cached rank.
                attachment.unlink(missing_ok=True)
                dashboard, state = self.monitor()
                self.assertEqual(dashboard.rank, '—')

    def test_fresh_native_reference_replaces_external_provenance_even_at_same_rank(self):
        self.import_board()
        self.monitor()
        stale = read_json(self.path)
        external_rank.attachment_path(self.path).unlink()
        _, _, rows = parse(*fixture(), self.contest, PAGE)
        reference = Reference(rows=rows, complete=True, released=True)
        dashboard, state = self.monitor(reference=reference)
        self.assertEqual(dashboard.rank, '2')
        self.assertEqual(state['practice_rank'], '2')
        self.assertNotIn('externalRankUrl', state['reference_rank'])
        native_proof = deepcopy(state['reference_rank'])
        save_state(self.path, stale)
        self.assertEqual(read_json(self.path)['reference_rank'], native_proof)
        dashboard, state = self.monitor()
        self.assertEqual(dashboard.rank, '2')

    def test_workspace_reopen_preserves_latest_rank_provenance(self):
        for native in (False, True):
            with self.subTest(native=native):
                write_json(self.path, self.state)
                stale = deepcopy(self.state)
                self.import_board()
                self.monitor()
                attachment = external_rank.attachment_path(self.path)
                if native:
                    stale = read_json(self.path)
                    attachment.unlink()
                    _, _, rows = parse(*fixture(), self.contest, PAGE)
                    self.monitor(reference=Reference(rows=rows, complete=True, released=True))
                expected = read_json(self.path)['reference_rank']
                promote(self.path, stale)
                self.assertEqual(read_json(self.path)['reference_rank'], expected)
                attachment.unlink(missing_ok=True)
                dashboard, state = self.monitor()
                self.assertEqual(dashboard.rank, '2' if native else '—')


if __name__ == '__main__':
    unittest.main()
