import copy
import io
import os
import re
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from rich.cells import cell_len

from vp_helper.attempt_history import _records
from vp_helper.models import Contest, Medals, Rules, Submission
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
               (PracticeDashboard(contest,120,[],handle='中文 team'),('—','中文 team'))]
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
                                labels=(('rank','team') if view.practice else
                                        ('rank','team','points','hacks +/−') if view.contest.platform=='cf' else
                                        ('rank','team','solved','penalty'))
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

    def test_submitted_problems_keep_complete_words_and_equal_column_spacing(self):
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
                headers=lambda lines: next(line for line in lines if 'rank' in line and 'team' in line)
                for lines in (first,second):
                    header=headers(lines)
                    centers=[self.cell_center(header,label)[0] for label in contest.problems]
                    gaps=[right-left for left,right in zip(centers,centers[1:])]
                    self.assertLessEqual(max(gaps)-min(gaps),1)
                self.assertNotIn('…','\n'.join(second))
                self.assertGreaterEqual(len(second),len(first))
                self.assertIn('tries','\n'.join(second))

    def test_summary_edge_padding_keeps_rank_and_prize_complete(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        for engine in ('rich','curses'):
            for prize in ('','Gold'):
                view=Dashboard(contest,120,me,'1',prize,None,[],practice=True)
                lines=self.fixed_lines(view,100,engine)
                header=next(line for line in lines if 'team' in line and 'rank' in line)
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
                            if isinstance(view,LiveDashboard):
                                # A 15-digit unbroken result cannot fit in
                                # thirteen readable problem columns here.
                                self.assertIn('Enlarge the score pane to view all problems.','\n'.join(lines))
                                self.assertFalse(any('rank' in line and 'team' in line for line in lines))
                                continue
                            header=next(line for line in lines if 'rank' in line and 'team' in line)
                            centers=[self.cell_center(header,label)[0] for label in contest.problems]
                            gaps=[right-left for left,right in zip(centers,centers[1:])]
                            self.assertLessEqual(max(gaps)-min(gaps),1)
                            self.assertTrue(all(cell_len(line)<=width for line in lines))

    def test_unavailable_awards_never_show_unknown_or_a_false_no_prize(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        unavailable=[]
        for prize in ('Unknown','None',''):
            output=self.render(Dashboard(contest,120,me,'3',prize,None,[]).fixed(160))
            self.assertNotIn('Unknown',output)
            self.assertNotIn('No Prize',output)
            unavailable.append(output)
        self.assertEqual(len(set(unavailable)),1)
        contest.medals=Medals(gold=1,silver=2,bronze=3,source='fixture official medals')
        self.assertIn('No Prize',self.render(Dashboard(contest,120,me,'3','None',None,[]).fixed(160)))
        self.assertIn('Gold Prize',self.render(Dashboard(contest,120,me,'3','Gold',None,[]).fixed(160)))

    def test_tied_ranks_display_only_the_number_in_vp_and_live_views(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        vp=Dashboard(contest,120,me,'=127','',None,[])
        live=LiveDashboard(contest,120,{'own':{'party':{'members':[{'handle':'tester'}]},'rank':'=127','points':0,'penalty':0,'problemResults':[]}},[])
        for view in (vp,live):
            rendered=self.render(view.fixed(160))
            self.assertIn('127',rendered)
            self.assertNotIn('=127',rendered)

    def test_colored_problem_boxes_have_the_same_complete_rectangle(self):
        contest=self.contest()
        records=[Submission('1','me','A',60,'CORRECT'),
                 Submission('2','me','B',60,'WRONG-ANSWER'),Submission('3','me','B',120,'CORRECT'),
                 Submission('4','me','C',60,'WRONG-ANSWER'),
                 Submission('5','me','D',60,'WRONG-ANSWER'),Submission('6','me','D',120,'PENDING')]
        me=score_submissions(records,list(contest.problems),contest.rules,18000,name='Example Team')
        practice=[{'id':'7','problem':'E','time':19000,'verdict':'CORRECT','practice':True}]
        for width in (130,156,160):
            with self.subTest(width=width):
                view=Dashboard(contest,19000,me,'3','Unknown',None,practice)
                console=Console(width=width,color_system='truecolor',legacy_windows=False)
                colors={(96,231,96),(232,114,114),(102,102,255),(0,0,255)}
                coordinates={color:set() for color in colors}
                for y,line in enumerate(console.render_lines(view.fixed(width),console.options)):
                    x=0
                    for segment in line:
                        background=segment.style.bgcolor if segment.style else None
                        color=background.get_truecolor() if background and background.type.name!='DEFAULT' else None
                        if color in coordinates:
                            coordinates[color].update((col,y) for col in range(x,x+segment.cell_length))
                        x+=segment.cell_length
                dimensions=[]
                for color,points in coordinates.items():
                    self.assertTrue(points,f'{color} must stay visible')
                    while points:
                        component={points.pop()};pending=list(component)
                        while pending:
                            x,y=pending.pop()
                            for neighbour in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                                if neighbour in points:
                                    points.remove(neighbour);component.add(neighbour);pending.append(neighbour)
                        xs,ys=zip(*component);size=(max(xs)-min(xs)+1,max(ys)-min(ys)+1)
                        self.assertEqual(len(component),size[0]*size[1])
                        dimensions.append(size)
                self.assertEqual(len(dimensions),5)
                self.assertEqual(len(set(dimensions)),1)

    def test_monitor_reload_callback_can_stop_without_reading_keyboard(self):
        callback=Mock(return_value=False)
        view=TerminalDashboard(continue_monitor=callback)
        view.screen=Mock()
        self.assertFalse(view.input())
        callback.assert_called_once_with()
        view.screen.getch.assert_not_called()
        callback.reset_mock(return_value=True)
        callback.return_value=True
        view.curses=Mock()
        view.screen.getch.return_value=-1
        self.assertTrue(view.input())
        callback.assert_called_once_with()

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
