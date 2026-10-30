"""QOJ's native OI/IOI score schema, without starting an actual VP."""
import json
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper.models import Contest, Rules, Submission
from vp_helper.qoj import QOJ
from vp_helper.scoring import rank_of, score_submissions


URL = "https://qoj.ac/contest/123"


def contest(scoring="OI"):
    return Contest("qoj", "123", URL, "QOJ fixture", "QOJ fixture", {"A": "10", "B": "11"}, Rules(7200, 0),
                   extra={"scoring": scoring, "native_scoring": scoring, "score_policy": "latest" if scoring == "OI" else "best",
                          "oi_time_tiebreak": scoring == "OI", "problem_max_scores": {"A": 100, "B": 100}})


def scoreboard(scoring="OI", pending=False):
    # QOJ's public standings JS: [points, time, sid, triesBefore, fullScore, hidden, ...].
    # Native rows: [totalPoints, tieSeconds, userInfo, rank, ...].
    rows = [[150, 180, ["one", 1500, 0, "One"], 1],
            [150, 240, ["two", 1500, 1, "Two"], 2],
            [200, 0, ["tester", 1500, 4, "Tester"], 1]]
    scores = {"one": {"0": [100, 121, 11, 1, 100, int(pending), []], "1": [50, 89, 12, 0, 100, 0, []]},
              "two": [[100, 181, 21, 0, 100, 0, []], [50, 89, 22, 0, 100, 0, []]],
              "tester": [[100, 60, 31, 0, 100, 0, []], [100, 120, 32, 0, 100, 0, []]]}
    return '<script>' + ';'.join(key + '=' + json.dumps(value) for key, value in {
        "contest_type": scoring, "problems_id": ["A", "B"], "standings": rows, "score": scores}.items()) + ';</script>'


def submission_table(score, *, maximum=100, timestamp="1970-01-01T00:18:20Z"):
    return BeautifulSoup('<table><thead><tr><th>ID</th><th>User</th><th>Problem</th><th>Score</th><th>Time</th></tr></thead><tbody>'
        '<tr><td><a href="/submission/77">77</a></td><td><a class="uoj-username" href="/user/profile/tester">tester</a></td>'
        f'<td><a href="/contest/123/problem/10">A</a></td><td><span class="uoj-score" data-max="{maximum}">{score}</span></td>'
        f'<td><time datetime="{timestamp}">{timestamp}</time></td></tr></tbody></table>', "html.parser")


class QOJPointModes(unittest.TestCase):
    def setUp(self):
        self.api = QOJ(URL, {"handle": "tester"})
        self.api.web = Mock()
        self.api.start_time = 1000

    def test_prepare_native_oi_ioi_rules_and_maxima(self):
        for scoring in ("OI", "IOI"):
            with self.subTest(scoring=scoring):
                body = '<h1>Point contest</h1>Ruleset: ' + scoring + ' Penalty: 0 minutes<table>'
                body += '<tr><td>A</td><td><a href="/contest/123/problem/10">A</a></td></tr>'
                body += '<tr><td>B</td><td><a href="/contest/123/problem/11">B</a></td></tr></table>'
                body += '<a href="download.php?id=1">Statements</a>' + scoreboard(scoring)
                self.api._page = Mock(return_value=BeautifulSoup(body, "html.parser"))
                self.api.web.soup.return_value = BeautifulSoup('<table><tr><td><a href="/contest/123">Point contest</a>2 hours</td></tr></table>', "html.parser")
                c = self.api.prepare()
                self.assertEqual(c.extra["scoring"], scoring)
                self.assertEqual(c.extra["score_policy"], "latest" if scoring == "OI" else "best")
                self.assertEqual(c.extra["problem_max_scores"], {"A": 100, "B": 100})
                self.assertEqual(c.extra["oi_time_tiebreak"], scoring == "OI")
                self.assertEqual(c.rules.penalty_minutes, 0)
                self.api.web.post.assert_not_called()

    def test_numeric_partial_is_native_points_and_does_not_require_failure_details(self):
        for scoring in ("OI", "IOI"):
            with self.subTest(scoring=scoring), patch("vp_helper.qoj.platform_now", return_value=9000):
                self.api._page = Mock(return_value=submission_table(45))
                item = self.api.submissions(contest(scoring))[0]
                self.assertEqual((item.verdict, item.score), ("PARTIAL", 45))
                self.api.web.soup.assert_not_called()

    def test_one_hundred_points_is_not_accepted_for_two_hundred_point_problem(self):
        self.api._page = Mock(return_value=submission_table(100, maximum=200))
        with patch("vp_helper.qoj.platform_now", return_value=9000):
            item = self.api.submissions(contest())[0]
        self.assertEqual((item.verdict, item.score), ("PARTIAL", 100))

    def test_full_points_are_accepted_and_zero_keeps_native_failure_verdict(self):
        self.api._page = Mock(return_value=submission_table(100))
        with patch("vp_helper.qoj.platform_now", return_value=9000):
            self.assertEqual(self.api.submissions(contest())[0].verdict, "CORRECT")
            self.api._page.return_value = submission_table(0)
            self.api.web.soup.return_value = BeautifulSoup('<div><div class="card-header">Details</div><div class="uoj-status">Wrong Answer</div></div>', "html.parser")
            item = self.api.submissions(contest())[0]
        self.assertEqual((item.verdict, item.score), ("WRONG-ANSWER", 0))

    def test_oi_sample_feedback_during_vp_cannot_become_full_acceptance(self):
        self.api._page = Mock(return_value=submission_table(100))
        with patch("vp_helper.qoj.platform_now", return_value=1500):
            item = self.api.submissions(contest())[0]
        self.assertEqual((item.verdict, item.score), ("HIDDEN", None))

    def test_graded_pending_details_include_awarded_points(self):
        self.api.web.get.return_value = Mock(url="https://qoj.ac/submission/77", text=str(submission_table(35)))
        with patch("vp_helper.qoj.platform_now", return_value=9000):
            result = self.api.pending_detail(contest(), {"id": "77", "problem": "A"})
        self.assertEqual(result, {"verdict": "PARTIAL", "score": 35})

    def test_final_reference_preserves_point_totals_ties_and_excludes_virtual_rows(self):
        reference = self.api.graded_reference_from_html(scoreboard(), contest(), 7200, released=True)
        self.assertTrue(reference.complete)
        self.assertFalse(reference.statistics_complete)
        self.assertEqual([row.name for row in reference.rows], ["One", "Two"])
        self.assertEqual([row.points for row in reference.rows], [150, 150])
        self.assertLess(reference.rows[0].rank_key, reference.rows[1].rank_key)
        self.assertEqual(reference.rows[0].cells["B"].score, 50)
        self.assertFalse(reference.rows[0].cells["B"].solved)

    def test_ioi_equal_points_share_rank_without_oi_time_tie(self):
        reference = self.api.graded_reference_from_html(scoreboard("IOI"), contest("IOI"), 7200, released=True)
        self.assertEqual(reference.rows[0].rank_key, reference.rows[1].rank_key)

    def test_no_final_scoreboard_leak_before_end_or_when_native_results_hidden(self):
        self.assertFalse(self.api.graded_reference_from_html(scoreboard(), contest(), 1000).complete)
        self.assertFalse(self.api.graded_reference_from_html(scoreboard(pending=True), contest(), 7200, released=True).complete)

    def test_oi_last_score_and_native_time_tiebreak_differ_from_ioi_best_score(self):
        events = [Submission("1", "me", "A", 121, "CORRECT", score=100),
                  Submission("2", "me", "A", 249, "PARTIAL", score=50)]
        c = contest()
        oi = score_submissions(events, list(c.problems), c.rules, 7200, scoring="OI", oi_time_tiebreak=True)
        ioi = score_submissions(events, list(c.problems), c.rules, 7200, scoring="IOI")
        self.assertEqual((oi.points, oi.rank_key, oi.solved), (50, (-50, 240), 0))
        self.assertEqual((ioi.points, ioi.rank_key, ioi.solved), (100, (-100, 0), 1))

    def test_later_known_oi_result_clears_obsolete_unknown_score(self):
        c = contest()
        result = score_submissions([Submission("1", "me", "A", 1, "UNAVAILABLE"),
                                    Submission("2", "me", "A", 2, "PARTIAL", score=35)],
                                   list(c.problems), c.rules, 7200, scoring="OI")
        self.assertEqual(result.points, 35)
        self.assertTrue(result.results_complete)

