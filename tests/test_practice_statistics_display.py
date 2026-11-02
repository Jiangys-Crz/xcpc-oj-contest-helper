from __future__ import annotations

import io
import unittest

from rich.console import Console

from vp_helper.models import Cell, Contest, Rules, TeamScore
from vp_helper.practice import PracticeDashboard
from vp_helper.tui import Dashboard, PRACTICE_ACCEPTED


class PracticeStatisticsDisplayTests(unittest.TestCase):
    def setUp(self):
        self.contest = Contest('qoj', '4613', 'https://qoj.ac/contest/4613',
                               'CCPC contest', 'CCPC contest', {'A': '10', 'B': '11'},
                               Rules(18000, freeze_at=14400, problem_reveal_count=50))
        self.stats = {'submitted': {'A': 13, 'B': 29},
                      'attempted': {'A': 8, 'B': 19},
                      'accepted': {'A': 5, 'B': 11}}
        self.me = TeamScore('me', 'tester', {'A': Cell(tries=1, solved=True, solve_time=10),
                                           'B': Cell()})

    @staticmethod
    def render(view):
        output = io.StringIO()
        console = Console(file=output, width=140, color_system=None)
        console.print(view.fixed(140))
        return output.getvalue()

    @staticmethod
    def statistics_rows(text):
        return [line.split() for line in text.splitlines()
                if line.strip().startswith(('public', 'submitted', 'attempted', 'accepted'))]

    def test_ccpc_statistics_remain_visible_after_natural_or_early_end(self):
        for elapsed in (7000, 18000):
            with self.subTest(elapsed=elapsed):
                view = Dashboard(self.contest, elapsed, self.me, '27', '', self.stats, [], practice=True)
                text = self.render(view)
                self.assertIn('BT', text)
                self.assertIn('revealed 2/2', text)
                self.assertNotIn(' / 05:00:00', text)
                self.assertEqual(self.statistics_rows(text), [
                    ['public', 'A', 'B'],
                    ['submitted', '13', '29'],
                    ['attempted', '8', '19'],
                    ['accepted', '5', '11'],
                ])

    def test_icpc_statistics_remain_in_problem_columns(self):
        self.contest.rules.problem_reveal_count = None
        view = Dashboard(self.contest, 18000, self.me, '27', '', self.stats, [], practice=True)
        self.assertEqual(self.statistics_rows(self.render(view)), [
            ['submitted', '13', '29'],
            ['attempted', '8', '19'],
            ['accepted', '5', '11'],
        ])

    def test_unavailable_statistics_remain_unknown(self):
        view = Dashboard(self.contest, 18000, self.me, '27', '', None, [], practice=True)
        self.assertEqual(self.statistics_rows(self.render(view)), [
            ['public', 'A', 'B'],
            ['submitted', '—', '—'],
            ['attempted', '—', '—'],
            ['accepted', '—', '—'],
        ])

    def test_standalone_or_restored_practice_does_not_invent_a_reference_board(self):
        self.contest.extra['practice'] = True
        for restored in (False, True):
            with self.subTest(restored=restored):
                view = PracticeDashboard(self.contest, 7000, [], restored=restored)
                self.assertEqual(self.statistics_rows(self.render(view)), [])

    def test_practice_acceptance_keeps_its_color_without_changing_vp_score(self):
        records = [{'id': '1', 'problem': 'A', 'time': 600, 'verdict': 'CORRECT', 'practice': False},
                   {'id': '2', 'problem': 'B', 'time': 19000, 'verdict': 'CORRECT', 'practice': True}]
        view = Dashboard(self.contest, 19000, self.me, '27', '', self.stats, records, practice=True)
        console = Console(file=io.StringIO(), width=140, color_system='truecolor')
        segments = list(console.render(view.fixed(140)))
        self.assertTrue(any(s.style and s.style.bgcolor and str(s.style) == PRACTICE_ACCEPTED
                            for s in segments))
        self.assertEqual((view.me.solved, view.me.penalty, view.rank), (1, 10, '27'))
        self.assertEqual(self.statistics_rows(self.render(view))[-1], ['accepted', '5', '11'])


if __name__ == '__main__':
    unittest.main()
