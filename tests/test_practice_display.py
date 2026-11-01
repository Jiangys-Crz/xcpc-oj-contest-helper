from copy import deepcopy
import io
import unittest

from rich.console import Console
from rich.table import Table

from vp_helper.models import Contest, Rules, Submission
from vp_helper.practice import PracticeDashboard
from vp_helper.scoring import score_submissions
from vp_helper.tui import Dashboard, LiveDashboard


class PracticeDisplayTests(unittest.TestCase):
    def test_live_practice_panel_uses_saved_rank_instead_of_updated_official_rank(self):
        self.contest.extra.update(scoring='ICPC',phase='FINISHED')
        own={'party':{'members':[{'handle':'tester'}]},'rank':999,'points':1,'penalty':1,
             'problemResults':[{'points':1},{'points':0}]}
        view=LiveDashboard(self.contest,19000,{'own':own},self.records,practice=True,practice_rank='5')
        score_table=next(item for item in view.fixed(180).renderables if isinstance(item,Table))
        self.assertEqual(score_table.columns[0]._cells[0],'5')

    def setUp(self):
        self.contest=Contest('qoj','123','https://qoj.ac/contest/123','Contest','Contest',
                             {'A':'10','B':'11'},Rules(18000))
        self.records=[{'id':'1','problem':'A','time':60,'verdict':'CORRECT','practice':False},
                      {'id':'2','problem':'B','time':18100,'verdict':'WRONG-ANSWER','practice':True},
                      {'id':'3','problem':'B','time':18200,'verdict':'CORRECT','practice':True}]

    def colors(self,renderable,background=False):
        console=Console(file=io.StringIO(),width=180,color_system='truecolor')
        colors=[]
        for segment in console.render(renderable):
            value=(segment.style.bgcolor if background else segment.style.color) if segment.style else None
            if value and value.type.name!='DEFAULT':
                colors.append(value.get_truecolor())
        return colors

    def test_vp_records_and_new_practice_ac_keep_different_top_cell_colors(self):
        me=score_submissions([Submission('1','me','A',60,'CORRECT')],list(self.contest.problems),self.contest.rules,18000)
        view=Dashboard(self.contest,19000,me,'1','',None,self.records,practice=True)
        before=deepcopy(self.records)
        backgrounds=self.colors(view.fixed(180),background=True)
        self.assertIn((96,231,96),backgrounds)
        self.assertIn((139,92,246),backgrounds)
        self.assertEqual(me.solved,1)
        self.assertFalse(me.cells['B'].solved)
        self.assertEqual(self.records,before)
        colors=self.colors(view.records())
        self.assertIn((0,128,0),colors)
        self.assertIn((255,0,0),colors)
        self.assertNotIn((139,92,246),colors)

    def test_standalone_bt_accepted_cell_is_purple(self):
        self.contest.extra['practice']=True
        records=[{**self.records[0],'practice':True}]
        view=PracticeDashboard(self.contest,19000,records,handle='tester')
        self.assertIn((139,92,246),self.colors(view.fixed(180),background=True))

    def test_live_ac_is_preserved_and_new_practice_ac_is_purple_on_all_live_platforms(self):
        own={'party':{'members':[{'handle':'tester'}]},'rank':5,'points':1,'penalty':1,
             'problemResults':[{'points':1,'bestSubmissionTimeSeconds':60},{'points':0}]}
        for platform in ('cf','cwnuoj','nowcoder'):
            self.contest.platform=platform
            self.contest.extra.update(scoring='ICPC',phase='FINISHED')
            view=LiveDashboard(self.contest,19000,{'own':own},self.records,practice=True)
            self.assertIn((139,92,246),self.colors(view.fixed(180),background=True))
            self.assertIn((96,231,96),self.colors(view.fixed(180),background=True))
            self.assertEqual(own['problemResults'][1]['points'],0)

    def test_practice_counts_ce_resubmissions_and_post_ac_attempts_without_score_or_time(self):
        self.contest.extra['practice'] = True
        self.contest.extra['scoring'] = 'OI'
        records = [{'id': str(i), 'problem': 'A', 'time': i * 60, 'verdict': v}
                   for i, v in enumerate(('COMPILER-ERROR', 'CORRECT', 'WRONG-ANSWER'), 1)]
        view = PracticeDashboard(self.contest, 1000, records, handle='tester')
        table = next(item for item in view.fixed(180).renderables if isinstance(item, Table) and item.columns[0].header == 'rank')
        self.assertEqual([c.header for c in table.columns], ['rank', 'team', 'solved', 'penalty', 'A', 'B'])
        self.assertEqual(table.columns[2]._cells[0],'—');self.assertEqual(table.columns[3]._cells[0],'—')
        self.assertEqual(table.columns[4]._cells[0].plain, '3 tries')
        self.assertIsNone(view.me.points)
        self.assertEqual(view.me.penalty, 0)

    def test_resumed_vp_combines_original_and_practice_attempts_but_keeps_original_ac_green(self):
        self.records += [{'id':'4','problem':'A','time':18300,'verdict':'WRONG-ANSWER','practice':True},
                         {'id':'5','problem':'A','time':18400,'verdict':'CORRECT','practice':True}]
        me = score_submissions([Submission('1','me','A',60,'CORRECT')],
                               list(self.contest.problems), self.contest.rules, 18000)
        view = Dashboard(self.contest,19000,me,'5','',None,self.records,practice=True)
        table = next(item for item in view.fixed(180).renderables if isinstance(item, Table) and item.columns[0].header == 'rank')
        self.assertEqual(table.columns[4]._cells[0].plain, '3 tries\n1')
        self.assertEqual(table.columns[5]._cells[0].plain, '2 tries')
        self.assertEqual(table.columns[2]._cells[0],'1');self.assertEqual(table.columns[3]._cells[0],'1')
        self.assertEqual(me.cells['A'].tries, 1)

    def test_scored_live_bt_does_not_turn_partial_points_into_an_original_ac(self):
        self.contest.extra.update(scoring='IOI', phase='FINISHED', problem_max_scores={'A':100,'B':100})
        own = {'party':{'members':[{'handle':'tester'}]}, 'rank':5, 'points':30,
               'problemResults':[{'points':30, 'accepted':False}, {'points':0}]}
        records = [{'id':'1','problem':'A','time':60,'verdict':'PARTIAL','practice':False},
                   {'id':'2','problem':'A','time':18100,'verdict':'CORRECT','practice':True}]
        view = LiveDashboard(self.contest,19000,{'own':own},records,practice=True,practice_rank='5')
        table = next(item for item in view.fixed(180).renderables if isinstance(item, Table))
        self.assertEqual([c.header for c in table.columns], ['rank','team','points','A','B'])
        self.assertEqual(table.columns[2]._cells[0],'30')
        self.assertEqual(table.columns[3]._cells[0].plain, '2 tries')
        self.assertIn((139,92,246), self.colors(view.fixed(180),background=True))

    def test_saved_live_ac_remains_visible_when_official_row_is_unavailable(self):
        self.contest.extra.update(scoring='ICPC',phase='FINISHED')
        view=LiveDashboard(self.contest,19000,{'own':None},self.records,practice=True)
        backgrounds=self.colors(view.fixed(180),background=True)
        self.assertIn((96,231,96),backgrounds)
        self.assertIn((139,92,246),backgrounds)

    def test_resumed_vp_keeps_original_summary_and_minutes_without_timing_new_practice_ac(self):
        me=score_submissions([Submission('1','me','A',60,'CORRECT')],
                             list(self.contest.problems),self.contest.rules,18000)
        me.known_penalty=137
        view=Dashboard(self.contest,19000,me,'5','',None,self.records,practice=True)
        before=deepcopy(me)
        table=next(item for item in view.fixed(180).renderables if isinstance(item,Table)
                   and item.columns[0].header=='rank')
        self.assertEqual([c.header for c in table.columns],['rank','team','solved','penalty','A','B'])
        self.assertEqual(table.columns[2]._cells[0],'1')
        self.assertEqual(table.columns[3]._cells[0],'137')
        self.assertEqual(table.columns[4]._cells[0].plain,'1 try\n1')
        self.assertEqual(table.columns[5]._cells[0].plain,'2 tries')
        self.assertEqual(me,before)

    def test_restored_practice_records_do_not_claim_a_synthetic_zero_penalty_as_original_score(self):
        view=PracticeDashboard(self.contest,19000,self.records,handle='tester',restored=True,practice_rank='5')
        self.assertEqual(view.me.scoring,'PRACTICE')
        self.assertFalse(view.contest.extra['practice'])
        table=next(item for item in view.fixed(180).renderables if isinstance(item,Table)
                   and item.columns[0].header=='rank')
        self.assertEqual(table.columns[2]._cells[0],'—')
        self.assertEqual(table.columns[3]._cells[0],'—')
        self.assertEqual(table.columns[4]._cells[0].plain,'1 try\n1')
        self.assertEqual(table.columns[5]._cells[0].plain,'2 tries')

    def test_live_practice_keeps_native_snapshot_penalty_and_original_ac_minute(self):
        self.contest.extra.update(scoring='ICPC',phase='FINISHED')
        own={'party':{'members':[{'handle':'tester'}]},'rank':5,'points':1,'penalty':137,
             'problemResults':[{'points':1,'accepted':True,'bestSubmissionTimeSeconds':60},{'points':0}]}
        view=LiveDashboard(self.contest,19000,{'own':own},self.records,practice=True)
        before=deepcopy(own)
        table=next(item for item in view.fixed(180).renderables if isinstance(item,Table))
        self.assertEqual(table.columns[2]._cells[0],'1')
        self.assertEqual(table.columns[3]._cells[0],'137')
        self.assertEqual(table.columns[4]._cells[0].plain,'1 try\n1')
        self.assertEqual(table.columns[5]._cells[0].plain,'2 tries')
        self.assertEqual(own,before)

    def test_manual_live_freeze_hides_native_counts_that_may_include_later_attempts(self):
        self.contest.extra.update(scoring='ICPC', phase='CODING', freeze_source='manual')
        self.contest.rules.freeze_at = 14400
        self.contest.rules.unfreeze_at = 18000
        snapshot = {'phase':'CODING', 'frozen':True, 'own':None,
                    'stats':{'submitted':{'A':9999, 'B':8888}}}
        view = LiveDashboard(self.contest,15000,snapshot,[])
        output = io.StringIO()
        Console(file=output,width=180,color_system=None).print(view.fixed(180))
        self.assertIn('Scoreboard frozen', output.getvalue())
        self.assertNotIn('9999', output.getvalue())
        self.assertNotIn('8888', output.getvalue())
