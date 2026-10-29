import copy
import io
import os
import unittest
from unittest.mock import patch

from rich.console import Console
from rich.cells import cell_len

from vp_helper.attempt_history import _records
from vp_helper.models import Contest, Rules, Submission
from vp_helper.practice import PracticeDashboard
from vp_helper.scoring import score_submissions
from vp_helper.submission_results import judging_url
from vp_helper.tui import Dashboard, LiveDashboard, TerminalDashboard


class SubmissionDisplayTests(unittest.TestCase):
    def test_windows_score_tables_center_without_expanding_empty_problem_cells(self):
        contest=self.contest()
        records=[Submission('1','me','A',10,'WRONG-ANSWER'),Submission('2','me','A',20,'PENDING')]
        for width in (100,140,160):
            for submissions in ([],records):
                me=score_submissions(submissions,list(contest.problems),contest.rules,120,name='中文 team')
                views=[Dashboard(contest,120,me,'1','',None,[],practice=True),
                       LiveDashboard(contest,120,{'phase':'CODING'},[],handle='中文 team'),
                       PracticeDashboard(contest,120,[],handle='中文 team')]
                with patch('sys.platform','win32'):
                    renderer=TerminalDashboard()
                    self.assertFalse(renderer.console.legacy_windows)
                    for view in views:
                        lines=renderer._lines(view.fixed(width),width)
                        text=[''.join(s.text for s in line) for line in lines]
                        separator=next(line for line in text if line.strip().startswith('─'))
                        left=cell_len(separator)-cell_len(separator.lstrip())
                        right=width-cell_len(separator.rstrip())
                        self.assertLessEqual(abs(left-right),1,(width,separator,left,right))
                        header=next(line for line in text if 'rank' in line and 'team' in line)
                        self.assertLess(header.index('D')-header.index('C'),5)

    def render(self, view, width=160):
        out = io.StringIO()
        Console(file=out, width=width, color_system=None).print(view)
        return out.getvalue()

    def contest(self):
        return Contest('qoj', '123', 'https://qoj.ac/contest/123', 'Fixture', 'Fixture',
                       {chr(65+i): str(10+i) for i in range(13)}, Rules(18000), original_start=1735689600,
                       extra={'scoring': 'ICPC', 'phase': 'CODING'})

    def test_empty_problem_cells_stay_small_and_only_submitted_cell_grows(self):
        contest = self.contest()
        score = score_submissions([], list(contest.problems), contest.rules, 120)
        before = Dashboard(contest, 120, score, '1', '', None, [], practice=True)
        first = self.render(before.fixed(160)).splitlines()
        records = [Submission('1', 'me', 'A', 10, 'WRONG-ANSWER'), Submission('2', 'me', 'A', 20, 'PENDING')]
        after = copy.deepcopy(before)
        after.me = score_submissions(records, list(contest.problems), contest.rules, 120)
        second = self.render(after.fixed(160)).splitlines()
        headers = lambda lines: next(line for line in lines if 'rank' in line and 'penalty' in line)
        h1, h2 = headers(first), headers(second)
        self.assertLess(h1.index('B') - h1.index('A'), 5)
        self.assertGreater(h2.index('B') - h2.index('A'), h1.index('B') - h1.index('A'))
        self.assertEqual(h2.index('D') - h2.index('C'), h1.index('D') - h1.index('C'))
        self.assertIn('1 + 1 tries', '\n'.join(second))

    def test_absolute_time_matches_vp_live_bt_and_history_even_after_rebase(self):
        contest = self.contest()
        records = [{'id': '12345', 'problem': 'A', 'time': 65, 'verdict': 'CORRECT'}]
        me = score_submissions([], list(contest.problems), contest.rules, 120)
        views = [Dashboard(contest, 120, me, '1', '', None, records, start_time=1735689600),
                 LiveDashboard(contest, 120, {}, records),
                 PracticeDashboard(contest, 120, records, start_time=1735689600)]
        with patch.dict(os.environ, {'TZ': 'Asia/Shanghai'}):
            for view in views:
                self.assertIn('2025-01-01 08:01:05', self.render(view.records()))
            self.assertIn('2025-01-01 08:01:05', self.render(_records(records, 1735689600)))
            views[0].start_time += 15
            views[0].submissions = [{**records[0], 'time': 50}]
            self.assertIn('2025-01-01 08:01:05', self.render(views[0].records()))
        with patch.dict(os.environ, {'TZ': 'UTC'}):
            self.assertIn('2025-01-01 00:01:05', self.render(views[1].records()))

    def test_long_hydro_id_and_full_verdict_fit_independent_bottom_columns(self):
        contest = self.contest()
        records = [{'id': 'a'*24, 'problem': 'A', 'time': 65, 'verdict': 'IDLENESS-LIMIT'}]
        view = LiveDashboard(contest, 120, {}, records)
        output = self.render(view.records())
        self.assertIn('#' + 'a'*24, output)
        self.assertIn('IDLENESS-LIMIT', output)
        self.assertEqual(len([line for line in output.splitlines() if line.strip()]), 1)

    def test_result_links_are_platform_records_and_invalid_ids_use_list(self):
        cases = [('https://codeforces.com/contest/123', '77', 'https://codeforces.com/contest/123/submission/77', '/my'),
                 ('https://codeforces.com/gym/100123', '77', 'https://codeforces.com/gym/100123/submission/77', '/my'),
                 ('https://qoj.ac/contest/123', '77', 'https://qoj.ac/submission/77', '/submissions'),
                 ('https://oj.cwnupaa.com/contest/'+'a'*24, 'b'*24, 'https://oj.cwnupaa.com/record/'+'b'*24, None),
                 ('https://oj.cwnupaa.com/d/training/contest/'+'a'*24, 'b'*24,
                  'https://oj.cwnupaa.com/d/training/record/'+'b'*24, None),
                 ('https://ac.nowcoder.com/acm/contest/123', '77',
                  'https://ac.nowcoder.com/acm/contest/view-submission?submissionId=77', '#submit/')]
        for url, sid, detail, suffix in cases:
            with self.subTest(url=url):
                self.assertEqual(judging_url(url, sid), detail)
                self.assertEqual(judging_url(url, '../other'), url+suffix if suffix else detail.rsplit('/', 1)[0])
