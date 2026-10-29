import copy
import io
import os
import re
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
    @staticmethod
    def cell_center(line, value, start=0):
        position=line.index(value,start)
        return cell_len(line[:position])+cell_len(value)/2,position+len(value)

    def fixed_lines(self, view, width, engine):
        if engine=='rich':
            return self.render(view.fixed(width),width).splitlines()
        renderer=TerminalDashboard()
        self.assertFalse(renderer.console.legacy_windows)
        return [''.join(segment.text for segment in line)
                for line in renderer._lines(view.fixed(width),width)]

    def test_score_tables_share_remaining_width_and_center_headers_and_values(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='中文 team')
        me.known_penalty=128
        own={'party':{'members':[{'handle':'中文 team'}]},'rank':127,'points':0,'penalty':128,
             'successfulHackCount':1,'unsuccessfulHackCount':2,'problemResults':[]}
        cf=copy.deepcopy(contest)
        cf.platform='cf'
        cf.extra['scoring']='CF'
        views=[(Dashboard(contest,120,me,'127','Gold',None,[]),('127','中文 team','0','128')),
               (LiveDashboard(contest,120,{'own':own},[]),('127','中文 team','0','128')),
               (LiveDashboard(cf,120,{'own':own},[]),('127','中文 team','0','1/2')),
               (PracticeDashboard(contest,120,[],handle='中文 team'),('—','中文 team','0','0'))]
        for platform in ('darwin','linux','win32'):
            with patch('sys.platform',platform):
                for engine in ('rich','curses'):
                    for view,values in views:
                        previous_summary_centers=None
                        for width in (100,140,160):
                            with self.subTest(platform=platform,engine=engine,view=type(view).__name__,width=width):
                                lines=self.fixed_lines(view,width,engine)
                                row=next(i for i,line in enumerate(lines) if 'rank' in line and 'team' in line)
                                header,separator,body=lines[row:row+3]
                                self.assertEqual(cell_len(separator),width)
                                centers=[self.cell_center(header,label)[0] for label in contest.problems]
                                gaps=[right-left for left,right in zip(centers,centers[1:])]
                                self.assertLessEqual(max(gaps)-min(gaps),1)
                                empty_centers=[cell_len(body[:match.start()])+0.5 for match in re.finditer('—',body)
                                               if cell_len(body[:match.start()])+0.5>=centers[0]-0.5]
                                self.assertEqual(len(empty_centers),len(centers))
                                for expected,actual in zip(centers,empty_centers):
                                    self.assertLessEqual(abs(expected-actual),0.5)
                                labels=('rank','team','points','hacks +/−') if view.contest.platform=='cf' else ('rank','team','solved','penalty')
                                summary_centers=[self.cell_center(header,label)[0] for label in labels]
                                if previous_summary_centers is not None:
                                    self.assertEqual(summary_centers,previous_summary_centers)
                                previous_summary_centers=summary_centers
                                position=0
                                for label,value in zip(labels,values):
                                    actual,position=self.cell_center(body,value,position)
                                    self.assertLessEqual(abs(self.cell_center(header,label)[0]-actual),0.5)

    def render(self, view, width=160):
        out = io.StringIO()
        Console(file=out, width=width, color_system=None).print(view)
        return out.getvalue()

    def contest(self):
        return Contest('qoj', '123', 'https://qoj.ac/contest/123', 'Fixture', 'Fixture',
                       {chr(65+i): str(10+i) for i in range(13)}, Rules(18000), original_start=1735689600,
                       extra={'scoring': 'ICPC', 'phase': 'CODING'})

    def test_submitted_problem_wraps_without_taking_width_from_other_problems(self):
        contest = self.contest()
        score = score_submissions([], list(contest.problems), contest.rules, 120)
        before = Dashboard(contest, 120, score, '1', '', None, [], practice=True)
        records = [Submission('1', 'me', 'A', 10, 'WRONG-ANSWER'), Submission('2', 'me', 'A', 20, 'PENDING')]
        after = copy.deepcopy(before)
        after.me = score_submissions(records, list(contest.problems), contest.rules, 120)
        for width in (100,140,160):
            for engine in ('rich','curses'):
                first=self.fixed_lines(before,width,engine)
                second=self.fixed_lines(after,width,engine)
                headers=lambda lines: next(line for line in lines if 'rank' in line and 'penalty' in line)
                self.assertEqual(headers(first),headers(second))
                self.assertNotIn('…','\n'.join(second))
                self.assertGreater(len(second),len(first))

    def test_summary_edge_padding_keeps_rank_and_prize_complete(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        for engine in ('rich','curses'):
            for prize in ('','Gold'):
                view=Dashboard(contest,120,me,'1',prize,None,[],practice=True)
                lines=self.fixed_lines(view,100,engine)
                header=next(line for line in lines if 'team' in line and 'penalty' in line)
                self.assertIn('rank',header)
                if prize:
                    self.assertTrue(any(line.startswith(' Gold Prize') for line in lines))
                else:
                    self.assertTrue(header.startswith(' rank'))

    def test_score_summary_does_not_expand_when_no_problems_are_available(self):
        contest=self.contest()
        contest.problems={}
        me=score_submissions([],[],contest.rules,120,name='tester')
        view=Dashboard(contest,120,me,'1','',None,[],practice=True)
        headers=[next(line for line in self.fixed_lines(view,width,'rich') if 'rank' in line and 'team' in line)
                 for width in (100,160)]
        self.assertEqual(headers[0].rstrip(),headers[1].rstrip())

    def test_narrow_score_tables_keep_every_problem_with_long_or_wide_team_names(self):
        contest=self.contest()
        cf=copy.deepcopy(contest)
        cf.platform='cf'
        cf.extra['scoring']='CF'
        for name in ('tester','中文队名测试甲乙','VeryLongTeamName'*6):
            me=score_submissions([],list(contest.problems),contest.rules,120,name=name)
            own={'party':{'members':[{'handle':name}]},'rank':127,'points':0,'penalty':128,
                 'problemResults':[{'points':0,'display':'123456789012345'}]}
            views=[Dashboard(contest,120,me,'127','Gold',None,[]),
                   LiveDashboard(contest,120,{'own':own},[]),
                   LiveDashboard(cf,120,{'own':own},[]),
                   PracticeDashboard(contest,120,[],handle=name)]
            for width in (50,52,72,80,100):
                for engine in ('rich','curses'):
                    for view in views:
                        if width==50 and view.contest.platform=='cf':
                            continue  # The points/hacks headers require two more cells.
                        with self.subTest(name=name,width=width,engine=engine,view=type(view).__name__):
                            lines=self.fixed_lines(view,width,engine)
                            header=next(line for line in lines if 'rank' in line and 'team' in line)
                            centers=[self.cell_center(header,label)[0] for label in contest.problems]
                            gaps=[right-left for left,right in zip(centers,centers[1:])]
                            self.assertLessEqual(max(gaps)-min(gaps),1)
                            self.assertTrue(all(cell_len(line)<=width for line in lines))

    def test_impossibly_narrow_score_pane_prompts_instead_of_hiding_problems(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        own={'party':{'members':[{'handle':'tester'}]},'rank':1,'points':0,'penalty':0,
             'problemResults':[]}
        for view in (Dashboard(contest,120,me,'1','',None,[]),LiveDashboard(contest,120,{'own':own},[])):
            for engine in ('rich','curses'):
                lines=self.fixed_lines(view,45,engine)
                self.assertIn('Enlarge the score pane to view all problems.','\n'.join(lines))
                self.assertFalse(any('rank' in line and 'team' in line for line in lines))

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
