import unittest

from vp_helper.models import Cell, Rules, Submission, TeamScore
from vp_helper.scoring import score_practice_submissions, score_submissions


class ScoringModeTests(unittest.TestCase):
    def score(self, mode, events, **kwargs):
        return score_submissions(events, ['A', 'B'], Rules(3600), 3600,
                                 scoring=mode, problem_max_scores={'A': 100, 'B': 150}, **kwargs)

    def test_icpc_keeps_first_acceptance_penalty_and_compile_policy(self):
        events = [Submission('1', 'me', 'A', 10, 'COMPILER-ERROR'),
                  Submission('2', 'me', 'A', 20, 'WRONG-ANSWER'),
                  Submission('3', 'me', 'A', 120, 'CORRECT'),
                  Submission('4', 'me', 'A', 180, 'WRONG-ANSWER')]
        score = self.score('ICPC', events)
        self.assertEqual((score.solved, score.penalty, score.cells['A'].tries), (1, 22, 2))
        self.assertEqual(score.points, 1)

    def test_oi_uses_last_judged_score_even_when_it_reduces_an_earlier_ac(self):
        events = [Submission('1', 'me', 'A', 10, 'CORRECT', score=100),
                  Submission('2', 'me', 'A', 20, 'PARTIAL', score=35),
                  Submission('3', 'me', 'B', 30, 'COMPILER-ERROR', score=0)]
        score = self.score('OI', events)
        self.assertEqual(score.points, 35)
        self.assertEqual(score.penalty, 0)
        self.assertEqual(score.solved, 0)
        self.assertEqual(score.cells['A'].tries, 2)
        self.assertEqual(score.cells['B'].tries, 1)

    def test_oi_later_authoritative_score_supersedes_earlier_missing_points(self):
        score = self.score('OI', [Submission('1', 'me', 'A', 10, 'PARTIAL'),
                                  Submission('2', 'me', 'A', 20, 'PARTIAL', score=35)])
        self.assertEqual(score.points, 35)
        self.assertTrue(score.results_complete)

    def test_ioi_and_weekly_keep_best_score_and_do_not_stop_counting_after_ac(self):
        events = [Submission('1', 'me', 'A', 10, 'PARTIAL', score=40),
                  Submission('2', 'me', 'A', 20, 'CORRECT', score=100),
                  Submission('3', 'me', 'A', 30, 'PARTIAL', score=35),
                  Submission('4', 'me', 'B', 40, 'PARTIAL', score=90)]
        for mode in ('IOI', 'WEEKLY'):
            with self.subTest(mode=mode):
                score = self.score(mode, events)
                self.assertEqual((score.points, score.solved, score.penalty), (190, 1, 0))
                self.assertEqual(score.cells['A'].tries, 3)
                self.assertEqual(score.cells['B'].max_score, 150)

    def test_cf_uses_best_native_awarded_points_and_native_total_including_hacks(self):
        score = self.score('CF', [Submission('1', 'me', 'A', 10, 'CORRECT', score=420),
                                  Submission('2', 'me', 'A', 20, 'CORRECT', score=390)])
        self.assertEqual(score.points, 420)
        self.assertEqual(score.penalty, 0)
        score.known_points = -80
        self.assertEqual(score.points, -80)
        self.assertEqual(score.rank_key, (80, 0))

    def test_missing_or_invalid_native_points_are_not_invented_from_verdict(self):
        for value in (None, float('inf'), float('nan'), -5, True):
            score = self.score('CF', [Submission('1', 'me', 'A', 10, 'CORRECT', score=value)])
            self.assertIsNone(score.points)
            self.assertFalse(score.results_complete)
        unavailable = self.score('OI', [Submission('1', 'me', 'A', 10, 'UNAVAILABLE')])
        self.assertIsNone(unavailable.points)
        self.assertFalse(unavailable.score_complete)

    def test_score_ranking_uses_points_instead_of_ac_count_or_icpc_penalty(self):
        first = TeamScore('1', 'First', {'A': Cell(tries=3, score=90)}, scoring='IOI')
        second = TeamScore('2', 'Second', {'A': Cell(tries=1, solved=True, score=80)}, scoring='IOI')
        self.assertLess(first.rank_key, second.rank_key)
        self.assertEqual(first.penalty, 0)

    def test_freeze_hides_new_scored_results_but_keeps_earlier_awarded_score(self):
        rules = Rules(3600, freeze_at=1800)
        events = [Submission('1', 'me', 'A', 100, 'PARTIAL', score=30),
                  Submission('2', 'me', 'A', 2000, 'CORRECT', score=100)]
        public = score_submissions(events, ['A'], rules, 2500, public=True, scoring='IOI')
        private = score_submissions(events, ['A'], rules, 2500, scoring='IOI')
        self.assertEqual((public.points, public.cells['A'].pending), (30, 1))
        self.assertEqual(private.points, 100)

    def test_practice_counts_all_attempts_without_applying_any_scoring_rule(self):
        events = [Submission('1', 'me', 'A', -100, 'COMPILER-ERROR'),
                  Submission('2', 'me', 'A', 20, 'CORRECT', score=100),
                  Submission('3', 'me', 'A', 30, 'WRONG-ANSWER', score=0),
                  Submission('4', 'me', 'A', 40, 'PENDING'),
                  Submission('4', 'me', 'A', 40, 'CORRECT'),
                  Submission('5', 'other', 'A', 50, 'CORRECT'),
                  Submission('6', 'me', 'B', 60, 'IGNORED')]
        score = score_practice_submissions(events, ['A', 'B'], 100)
        self.assertEqual((score.cells['A'].tries, score.cells['A'].pending, score.solved), (4, 0, 1))
        self.assertEqual(score.cells['B'].tries, 0)
        self.assertIsNone(score.cells['A'].solve_time)
        self.assertIsNone(score.points)
        self.assertEqual(score.penalty, 0)

    def test_practice_pending_attempts_are_separate_and_never_double_counted(self):
        score = score_practice_submissions([
            Submission('1','me','A',10,'COMPILER-ERROR'),
            Submission('2','me','A',20,'CORRECT'),
            Submission('3','me','A',30,'PENDING'),
            Submission('4','me','A',40,'UNAVAILABLE')], ['A'], 50)
        self.assertEqual((score.cells['A'].tries,score.cells['A'].pending), (2,2))
        self.assertEqual(score.cells['A'].tries + score.cells['A'].pending, 4)
        self.assertTrue(score.cells['A'].solved)


if __name__ == '__main__':
    unittest.main()
