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
        self.assertIn((0,0,255),backgrounds)
        self.assertEqual(me.solved,1)
        self.assertFalse(me.cells['B'].solved)
        self.assertEqual(self.records,before)
        colors=self.colors(view.records())
        self.assertIn((0,128,0),colors)
        self.assertIn((255,0,0),colors)
        self.assertNotIn((0,0,255),colors)

    def test_standalone_bt_accepted_cell_is_blue(self):
        self.contest.extra['practice']=True
        records=[{**self.records[0],'practice':True}]
        view=PracticeDashboard(self.contest,19000,records,handle='tester')
        self.assertIn((0,0,255),self.colors(view.fixed(180),background=True))

    def test_live_ac_is_preserved_and_new_practice_ac_is_blue_on_all_live_platforms(self):
        own={'party':{'members':[{'handle':'tester'}]},'rank':5,'points':1,'penalty':1,
             'problemResults':[{'points':1,'bestSubmissionTimeSeconds':60},{'points':0}]}
        for platform in ('cf','cwnuoj','nowcoder'):
            self.contest.platform=platform
            self.contest.extra.update(scoring='ICPC',phase='FINISHED')
            view=LiveDashboard(self.contest,19000,{'own':own},self.records,practice=True)
            self.assertIn((0,0,255),self.colors(view.fixed(180),background=True))
            self.assertIn((0,128,0),self.colors(view.fixed(180)))
            self.assertEqual(own['problemResults'][1]['points'],0)

    def test_saved_live_ac_remains_visible_when_official_row_is_unavailable(self):
        self.contest.extra.update(scoring='ICPC',phase='FINISHED')
        view=LiveDashboard(self.contest,19000,{'own':None},self.records,practice=True)
        backgrounds=self.colors(view.fixed(180),background=True)
        self.assertIn((96,231,96),backgrounds)
        self.assertIn((0,0,255),backgrounds)
