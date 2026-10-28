from __future__ import annotations

import io
import unittest

from rich.console import Console

from vp_helper.models import Cell, Contest, Reference, Rules, TeamScore
from vp_helper.scoring import revealed_problems, statistics
from vp_helper.tui import Dashboard


class CCPCStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.contest = Contest("qoj", "4538", "https://qoj.ac/contest/4538", "CCPC 2026",
                               "CCPC 2026", dict.fromkeys("ABCD", "1"),
                               Rules(duration=18000, freeze_at=14400, problem_reveal_count=50))
        self.stats = {"submitted": dict(zip("ABCD", (7, 20, 9, 0))),
                      "attempted": dict(zip("ABCD", (5, 8, 6, 0))),
                      "accepted": dict(zip("ABCD", (2, 5, 3, 0)))}
        self.view = Dashboard(self.contest, 1000, TeamScore("me", "me", {}), "1", "", self.stats, [])

    def rows(self):
        output = io.StringIO()
        console = Console(file=output, width=100, color_system=None)
        console.print(self.view.public_statistics())
        return [line.split() for line in output.getvalue().splitlines()[1:]]

    def test_hidden_labels_still_show_all_three_counts_in_activity_order(self):
        self.assertEqual(self.rows(), [
            ["public", "?", "?", "?", "?"],
            ["submitted", "20", "9", "7", "0"],
            ["attempted", "8", "6", "5", "0"],
            ["accepted", "5", "3", "2", "0"],
        ])

    def test_revealed_columns_keep_labels_and_matching_counts(self):
        self.view.revealed = frozenset("A")
        self.assertEqual(self.rows(), [
            ["public", "A", "?", "?", "?"],
            ["submitted", "7", "20", "9", "0"],
            ["attempted", "5", "8", "6", "0"],
            ["accepted", "2", "5", "3", "0"],
        ])

    def test_missing_data_does_not_invent_counts(self):
        self.view.stats = None
        rows = self.rows()
        self.assertEqual(rows[0], ["public", "?", "?", "?", "?"])
        self.assertTrue(all(row[1:] == ["—"] * 4 for row in rows[1:]))

    def test_sort_ties_use_attempted_then_submitted(self):
        self.stats["accepted"] = dict.fromkeys("ABCD", 0)
        self.stats["attempted"] = dict.fromkeys("ABCD", 1)
        self.assertEqual(self.rows()[1], ["submitted", "20", "9", "7", "0"])

    def test_freeze_and_contest_end_reveal_all_labels_in_original_order(self):
        for elapsed in (14399, 14400, 18000):
            with self.subTest(elapsed=elapsed):
                self.view.elapsed = elapsed
                if elapsed < 14400:
                    self.assertEqual(self.rows()[0], ["public"] + ["?"] * 4)
                else:
                    self.assertEqual(self.rows()[0], ["public", "A", "B", "C", "D"])
                    self.assertEqual(self.rows()[1], ["submitted", "7", "20", "9", "0"])

    def test_threshold_includes_starred_teams_but_statistics_use_official_teams(self):
        rows = [TeamScore(str(i), str(i), {"A": Cell(tries=1, solved=True)}, eligible=i < 49)
                for i in range(50)]
        me = TeamScore("me", "me", {"B": Cell(tries=2, solved=True)})
        reference = Reference(rows, complete=True, statistics_complete=True)
        self.assertEqual(revealed_problems(self.contest, 1000, reference), frozenset("A"))
        counts = statistics(rows + [me], list("ABCD"))
        self.assertEqual(counts["accepted"]["A"], 49)
        self.assertEqual(counts["submitted"]["B"], 2)
        self.assertEqual(counts["attempted"]["B"], 1)
        self.assertEqual(counts["accepted"]["B"], 1)
        reference.rows = rows[:49] + [me]
        self.assertEqual(revealed_problems(self.contest, 1000, reference), frozenset())


if __name__ == "__main__":
    unittest.main()
