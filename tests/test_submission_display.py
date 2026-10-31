import copy
from collections import Counter
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
                            rendered='\n'.join(lines)
                            self.assertNotIn('Enlarge the score pane',rendered)
                            self.assertNotIn('…',rendered)
                            for label in contest.problems:
                                self.assertRegex(rendered,r'(?m)(?:^|\s)'+label+r'(?:\s|$)')
                            self.assertTrue(any('rank' in line and 'team' in line for line in lines))
                            header=next(line for line in lines if 'rank' in line and 'team' in line)
                            for label in contest.problems:
                                self.assertRegex(header,r'(?:^|\s)'+label+r'(?:\s|$)')
                            if isinstance(view,LiveDashboard):
                                # The long native value may wrap vertically in
                                # its column, without dropping any digit.
                                score_header=lines.index(header)
                                body=lines[score_header+2:next(i for i,line in enumerate(lines)
                                                            if 'scoreboard ·' in line or 'Official public standings' in line)]
                                digits=Counter(re.findall(r'\d',''.join(body)))
                                metadata='127000' if view.contest.platform=='cf' else '1270128'
                                self.assertEqual(digits,Counter(metadata+'123456789012345'))
                            self.assertTrue(all(cell_len(line)<=width for line in lines))

    def test_structured_participant_names_keep_native_scoring_identity_unchanged(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='NativeHandle')
        own={'party':{'members':[{'handle':'NativeHandle'}]},'rank':127,'points':0,
             'penalty':128,'problemResults':[]}
        for title,subtitle in (('Code-Zero','曹荣智、李明、王红'),('nickname-with-hyphens','曹荣智')):
            views=[Dashboard(contest,120,me,'127','',None,[]),
                   LiveDashboard(contest,120,{'own':own},[]),
                   PracticeDashboard(contest,120,[],handle='NativeHandle')]
            for view in views:
                view.participant_title=title;view.participant_subtitle=subtitle
                for width in (80,100,130,160):
                    for engine in ('rich','curses'):
                        with self.subTest(title=title,view=type(view).__name__,width=width,engine=engine):
                            lines=self.fixed_lines(view,width,engine)
                            self.assertIn(title,'\n'.join(lines))
                            self.assertIn(subtitle,'\n'.join(lines))
                            title_row=next(i for i,line in enumerate(lines) if title in line)
                            subtitle_row=next(i for i,line in enumerate(lines) if subtitle in line)
                            self.assertEqual(subtitle_row,title_row+1)
                            self.assertNotIn('NativeHandle','\n'.join(lines))
                            self.assertTrue(all(cell_len(line)<=width for line in lines))
            self.assertEqual(me.name,'NativeHandle')
            self.assertEqual(own['party']['members'][0]['handle'],'NativeHandle')

    def test_active_scoreboard_shows_all_twenty_problems_and_summary_fields(self):
        contest=self.contest()
        contest.problems={chr(65+i):str(i) for i in range(20)}
        records=[Submission(str(i*3+j),'me',label,120+j,verdict)
                 for i,label in enumerate(contest.problems)
                 for j,verdict in enumerate(('WRONG-ANSWER','WRONG-ANSWER'))]
        me=score_submissions(records,list(contest.problems),contest.rules,600,name='NativeHandle')
        me.known_penalty=128
        own={'party':{'members':[{'handle':'NativeHandle'}]},'rank':127,'points':0,
             'penalty':128,'problemResults':[{'display':'2 + 1 tries'} for _ in contest.problems]}
        views=[Dashboard(contest,600,me,'127','',None,[]),
               LiveDashboard(contest,600,{'own':own},[])]
        for view in views:
            view.participant_title='Example Team';view.participant_subtitle='Alice、Bob、Carol'
            for width in (80,100,130,160):
                for engine in ('rich','curses'):
                    with self.subTest(view=type(view).__name__,width=width,engine=engine):
                        lines=self.fixed_lines(view,width,engine);rendered='\n'.join(lines)
                        for heading in ('rank','team','solved','penalty',*contest.problems):
                            self.assertRegex(rendered,r'(?m)(?:^|\s)'+heading+r'(?:\s|$)')
                        for value in ('127','128','Example Team','Alice、Bob、Carol'):
                            self.assertIn(value,rendered)
                        self.assertNotIn('…',rendered)
                        self.assertTrue(all(cell_len(line)<=width for line in lines))
                        header=next(line for line in lines if 'rank' in line and 'team' in line)
                        for label in contest.problems:
                            self.assertRegex(header,r'(?:^|\s)'+label+r'(?:\s|$)')
                        self.assertEqual(sum('rank' in line and 'team' in line for line in lines),1)
                        self.assertNotIn('public',header)
                        if width>=160:
                            self.assertGreaterEqual(rendered.count('tries'),20)

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
        for width in (80,100,130,156,160):
            with self.subTest(width=width):
                view=Dashboard(contest,19000,me,'3','Unknown',None,practice)
                console=Console(width=width,color_system='truecolor',legacy_windows=False)
                colors={(96,231,96),(232,114,114),(37,99,235),(139,92,246)}
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

    def test_practice_cells_are_purple_and_pending_cells_blue_without_recoloring_original_ac(self):
        contest=self.contest()
        records=[{'id':'1','problem':'A','time':10,'verdict':'CORRECT','practice':False},
                 {'id':'2','problem':'D','time':20,'verdict':'PENDING','practice':False},
                 {'id':'3','problem':'B','time':19000,'verdict':'CORRECT','practice':True}]
        events=[Submission(item['id'],'me',item['problem'],item['time'],item['verdict'])
                for item in records if not item['practice']]
        me=score_submissions(events,list(contest.problems),contest.rules,18000,name='tester')
        own={'party':{'members':[{'handle':'tester'}]},'rank':1,'points':1,'penalty':0,
             'problemResults':[{'points':1,'accepted':True},{'points':0},{'points':0},
                               {'points':0,'type':'PRELIMINARY'}]}
        views=[Dashboard(contest,19001,me,'1','',None,records),
               PracticeDashboard(contest,19001,records,handle='tester',restored=True),
               LiveDashboard(contest,19001,{'own':own},records,practice=True)]
        for view in views:
            with self.subTest(view=type(view).__name__):
                console=Console(width=160,color_system='truecolor',legacy_windows=False)
                backgrounds={tuple(segment.style.bgcolor.get_truecolor())
                             for line in console.render_lines(view.fixed(160),console.options)
                             for segment in line if segment.style and segment.style.bgcolor
                             and segment.style.bgcolor.type.name!='DEFAULT'}
                self.assertIn((96,231,96),backgrounds,'Original contest AC remains green')
                self.assertIn((139,92,246),backgrounds,'Practice AC uses purple')
                self.assertIn((37,99,235),backgrounds,'Pending or sealed results use blue')
        live=LiveDashboard(contest,19001,{'own':own},records)
        console=Console(width=160,color_system='truecolor',legacy_windows=False)
        backgrounds={tuple(segment.style.bgcolor.get_truecolor())
                     for line in console.render_lines(live.fixed(160),console.options)
                     for segment in line if segment.style and segment.style.bgcolor
                     and segment.style.bgcolor.type.name!='DEFAULT'}
        self.assertIn((37,99,235),backgrounds,'A native live pending cell also uses blue')
        rows=Dashboard.record_rows(LiveDashboard(contest,120,{},[
            {'id':str(i),'problem':'A','time':i,'verdict':value,'practice':True}
            for i,value in enumerate(('CORRECT','WRONG-ANSWER','PENDING','FROZEN'))]))
        colors=[tuple(console.get_style(row[3].style).color.get_truecolor()) for row in rows]
        self.assertEqual(colors,[(0,128,0),(255,0,0),(37,99,235),(37,99,235)])

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

    def test_impossible_header_width_explains_minimum_without_separating_headers(self):
        contest=self.contest()
        me=score_submissions([],list(contest.problems),contest.rules,120,name='tester')
        own={'party':{'members':[{'handle':'tester'}]},'rank':1,'points':0,'penalty':0,
             'problemResults':[]}
        for view in (Dashboard(contest,120,me,'1','',None,[]),LiveDashboard(contest,120,{'own':own},[])):
            for engine in ('rich','curses'):
                lines=self.fixed_lines(view,45,engine)
                rendered='\n'.join(lines)
                self.assertIn('Enlarge the score pane to at least 50 columns',rendered)
                self.assertIn('all headers on one row',rendered)
                self.assertFalse(any('rank' in line and 'team' in line for line in lines))
                self.assertTrue(all(cell_len(line)<=45 for line in lines))

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

    def test_narrow_submission_columns_fold_full_ids_timestamps_and_verdicts(self):
        contest=self.contest()
        records=[{'id':'a'*24,'problem':'A','time':65,'verdict':'IDLENESS-LIMIT'},
                 {'id':'396538368','problem':'M','time':125,'verdict':'WRONG-ANSWER'}]
        view=LiveDashboard(contest,120,{},records)
        with patch.dict(os.environ,{'TZ':'Asia/Shanghai'}):
            for width in (45,80,100,130,160):
                with self.subTest(width=width):
                    renderer=TerminalDashboard()
                    lines=[''.join(segment.text for segment in line)
                           for line in renderer._lines(view.records(),width)]
                    rendered='\n'.join(lines)
                    # Wrapping may put segments on separate lines, but cannot
                    # replace any of the original field text with an ellipsis.
                    self.assertNotIn('…',rendered)
                    expected=''.join(value.plain for row in Dashboard.record_rows(view) for value in row)
                    self.assertEqual(Counter(re.sub(r'\s','',rendered)),Counter(re.sub(r'\s','',expected)))
                    if width>=80:
                        for value in ('#'+'a'*24,'#396538368','2025-01-01 08:01:05',
                                      '2025-01-01 08:02:05','IDLENESS-LIMIT','WRONG-ANSWER'):
                            self.assertIn(value,rendered)
                    self.assertTrue(all(cell_len(line)<=width for line in lines))
                    header=self.render(Dashboard.record_heading(width,Dashboard.record_rows(view)),width)
                    for name in ('problem','submission','time','result'):
                        self.assertIn(name,header)

    def test_submission_columns_use_left_half_with_a_full_width_header_band(self):
        view=LiveDashboard(self.contest(),120,{},
                           [{'id':'396538368','problem':'A','time':65,'verdict':'WRONG-ANSWER'}])
        with patch.dict(os.environ,{'TZ':'Asia/Shanghai'}):
            for platform in ('darwin','linux','win32'):
                with patch('sys.platform',platform):
                    for width,target in ((80,56),(100,56),(160,80),(240,120)):
                        for engine in ('rich','curses'):
                            with self.subTest(platform=platform,width=width,engine=engine):
                                rows=Dashboard.record_rows(view)
                                heading=Dashboard.record_heading(width,rows)
                                console=Console(width=width,legacy_windows=False)
                                renderer=TerminalDashboard()
                                header_lines=(console.render_lines(heading,console.options,pad=False) if engine=='rich'
                                              else renderer._lines(heading,width))
                                record_lines=(console.render_lines(view.records(),console.options,pad=False) if engine=='rich'
                                              else renderer._lines(view.records(),width))
                                self.assertEqual(len(header_lines),1)
                                self.assertEqual(len(record_lines),1)
                                header=''.join(segment.text for segment in header_lines[0])
                                record=''.join(segment.text for segment in record_lines[0])
                                self.assertEqual(cell_len(header),width)
                                self.assertEqual(cell_len(record),target)
                                self.assertTrue(header[target:].isspace())
                                starts=[header.index(name) for name in ('problem','submission','time','result')]
                                values=('A','#396538368','2025-01-01 08:01:05','WRONG-ANSWER')
                                self.assertEqual([record.index(value) for value in values],starts)
                                for segment in header_lines[0]:
                                    if segment.cell_length:
                                        self.assertIsNotNone(segment.style)
                                        self.assertIsNotNone(segment.style.bgcolor)
                                        self.assertEqual(tuple(segment.style.bgcolor.get_truecolor()),(238,238,238))

    def test_long_submission_fields_expand_beyond_half_only_when_needed(self):
        view=LiveDashboard(self.contest(),120,{},
                           [{'id':'a'*24,'problem':'M','time':65,'verdict':'IDLENESS_LIMIT_EXCEEDED'}])
        with patch.dict(os.environ,{'TZ':'Asia/Shanghai'}):
            for width in (80,100,160,240):
                with self.subTest(width=width):
                    rows=Dashboard.record_rows(view)
                    table=Dashboard._record_table(width,rows)
                    self.assertGreaterEqual(table.width,width//2)
                    self.assertLessEqual(table.width,width)
                    if width==240:
                        self.assertEqual(table.width,120)
                    output=self.render(view.records(),width)
                    self.assertIn('#'+'a'*24,output)
                    self.assertIn('2025-01-01 08:01:05',output)
                    self.assertIn('IDLENESS_LIMIT_EXCEEDED',output)
                    self.assertNotIn('…',output)

    def test_small_terminal_scrolls_wrapped_fields_without_discarding_the_shared_header(self):
        contest=self.contest();contest.problems={chr(65+i):str(i) for i in range(20)}
        records=[Submission(str(i),'me',label,120,'WRONG-ANSWER')
                 for i,label in enumerate(contest.problems)]
        me=score_submissions(records,list(contest.problems),contest.rules,600,name='NativeHandle')
        view=Dashboard(contest,600,me,'127','',None,
                       [{'id':'396538368','problem':'T','time':125,'verdict':'WRONG-ANSWER'}],
                       participant_title='Example Team',participant_subtitle='Alice with a very long real name、Bob with a very long real name、Carol with a very long real name')
        for platform in ('darwin','linux','win32'):
            with patch('sys.platform',platform),self.subTest(platform=platform):
                renderer=TerminalDashboard();renderer.screen=Mock();renderer.curses=Mock()
                renderer.screen.getmaxyx.return_value=(16,80)
                rendered=[]
                renderer._draw=lambda row,segments,width,left=0: rendered.append((row,''.join(s.text for s in segments)))
                renderer.update(view)
                complete=renderer._lines(view.fixed(76),76)+renderer._lines(view.records(),76)
                self.assertEqual(renderer.total_lines,len(complete))
                self.assertGreater(renderer.total_lines,renderer.page_size)
                visible=[]
                for offset in range(0,renderer.total_lines,renderer.page_size):
                    renderer.offset=offset;rendered.clear();renderer.update(view)
                    visible.extend(line for row,line in rendered if row<14)
                output='\n'.join(visible)
                for name in ('rank','team','solved','penalty',*contest.problems):
                    self.assertRegex(output,r'(?m)(?:^|\s)'+name+r'(?:\s|$)')
                self.assertIn('396538368',output)
                self.assertTrue(all(cell_len(line)<=76 for line in visible))

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
