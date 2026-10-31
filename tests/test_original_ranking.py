from copy import deepcopy
from itertools import count
from unittest.mock import Mock, patch
import unittest

from vp_helper import cli
from vp_helper.models import Cell, Reference, Rules, Submission, TeamScore, VPError
from vp_helper.originals import OriginalReplay
from vp_helper.ranking import original_rank, verified_rank
from vp_helper.scoring import score_submissions
from vp_helper.storage import read_json
from test_cf_start_sync import ImmediatePool
from test_workflows import Isolated, URL


class RankProofTests(unittest.TestCase):
    def setUp(self):
        self.score=TeamScore('me','my team',{'A':Cell(1,solved=True,solve_time=20)})
        self.rows=[TeamScore('onsite-1','official one',{'A':Cell(1,solved=True,solve_time=10)}),
                   TeamScore('onsite-2','official two',{'A':Cell(1,solved=True,solve_time=15)}),
                   TeamScore('starred','starred team',{'A':Cell(1,solved=True,solve_time=1)},eligible=False)]

    def test_rank_uses_all_official_originals_and_competition_ties(self):
        reference=Reference(self.rows,complete=True)
        proof=original_rank(self.score,reference,URL)
        self.assertEqual(proof['rank'],'3')
        self.assertEqual(proof['officialParticipants'],2)
        self.assertEqual(verified_rank({'url':URL,'reference_rank':proof},self.score),'3')
        self.rows[1].cells['A'].solve_time=20
        self.assertEqual(original_rank(self.score,reference,URL)['rank'],'=2')
        self.assertEqual(verified_rank({'url':URL,'reference_rank':original_rank(self.score,reference,URL)},self.score),'2')

    def test_empty_starred_incomplete_or_mixed_scoring_reference_has_no_rank(self):
        for rows,complete in (([],True),([self.rows[-1]],True),(self.rows,False),
                              ([self.rows[0],self.rows[0]],True),
                              ([TeamScore('ioi','points',{'A':Cell()},scoring='IOI',known_points=100)],True)):
            self.assertIsNone(original_rank(self.score,Reference(rows,complete=complete),URL))

    def test_proof_is_bound_to_score_contest_and_nonempty_roster(self):
        proof=original_rank(self.score,Reference(self.rows,complete=True),URL)
        for change in ({'rank':'1','officialParticipants':0},{'contestUrl':URL+'0'},
                       {'scoreKey':[-99,0]},{'source':'native'},{'rank':'9999'}):
            altered={**proof,**change}
            self.assertEqual(verified_rank({'url':URL,'reference_rank':altered},self.score),'—')
        self.score.cells['A'].solve_time=19
        self.assertEqual(verified_rank({'url':URL,'reference_rank':proof},self.score),'—')

    def test_empty_replay_cannot_become_a_complete_scoreboard(self):
        from vp_helper.models import Contest
        contest=Contest('gym','1','https://codeforces.com/gym/1','Empty','Empty',{'A':'A'},Rules(3600))
        with self.assertRaisesRegex(VPError,'official participants'):
            OriginalReplay(contest,[],[],[])

    def test_original_public_statistics_include_compilation_errors_and_starred_teams(self):
        from vp_helper.models import Contest
        contest=Contest('gym','1','https://codeforces.com/gym/1','Original','Original',{'A':'A'},Rules(3600))
        teams=[{'id':'onsite','name':'official','official':True},{'id':'star','name':'starred','official':False}]
        events=[Submission('1','onsite','A',10,'COMPILER-ERROR'),Submission('2','onsite','A',20,'CORRECT'),
                Submission('3','star','A',30,'COMPILER-ERROR'),Submission('4','star','A',40,'CORRECT')]
        expected=[score_submissions(events,['A'],contest.rules,3600,team_id=t['id'],name=t['name']) for t in teams]
        for row,team in zip(expected,teams):row.eligible=team['official']
        replay=OriginalReplay(contest,teams,events,expected)
        early=replay.statistics_at(contest,15)
        self.assertEqual(early,{'submitted':{'A':1},'attempted':{'A':1},'accepted':{'A':0}})
        self.assertEqual(replay.statistics_at(contest,3600,released=True),
                         {'submitted':{'A':4},'attempted':{'A':2},'accepted':{'A':2}})

    def test_canonical_ignored_events_survive_original_cache_round_trip(self):
        from vp_helper.models import Contest
        from vp_helper.originals import archive_submissions, cache_archive_submissions, cache_path
        from vp_helper.storage import read_json
        import tempfile,os
        contest=Contest('gym','1','https://codeforces.com/gym/1','Original','Original',{'A':'A'},Rules(3600))
        metadata={'teams':[{'id':'onsite'}],'problems':['A'],'source':'https://example.test/archive'}
        events=[Submission('1','onsite','A',10,'IGNORED'),Submission('2','onsite','A',20,'CORRECT')]
        with tempfile.TemporaryDirectory() as cache,patch.dict(os.environ,{'XDG_CACHE_HOME':cache}):
            cache_archive_submissions(metadata,events)
            saved=read_json(cache_path('runs',metadata['source']+'/run.json'))
            self.assertEqual(archive_submissions(saved['runs'],metadata,contest),events)


class MonitorOriginalRankTests(Isolated):
    def test_rank_and_public_counts_only_use_original_participants(self):
        path,state=cli.prepare_start(URL)
        self.clock=1120
        # Two formal teams already solved, one starred team is irrelevant to rank/statistics.
        original=score_submissions([Submission('o1','o1','A',10,'CORRECT')],['A'],Rules(18000),120,team_id='o1')
        star=deepcopy(original);star.id='star';star.eligible=False
        self.api.submissions.return_value=[Submission('mine','me','A',60,'CORRECT')]
        self.api.reference.return_value=Reference([original,star],complete=True,statistics_complete=True,released=True)
        view=Mock();view.__enter__=Mock(return_value=view);view.__exit__=Mock(return_value=False)
        view.input.side_effect=[True]*3+[False]
        with patch.object(cli,'TerminalDashboard',return_value=view),patch.object(cli,'ThreadPoolExecutor',ImmediatePool), \
             patch('time.sleep'),patch('time.monotonic',side_effect=count(0,1)):
            cli.monitor(path)
        saved=read_json(path)
        self.assertEqual(saved['reference_rank']['rank'],'2')
        self.assertEqual(saved['reference_rank']['officialParticipants'],1)
        self.assertEqual(saved['statistics_source'],'original')
        self.assertEqual(saved['public_statistics']['submitted']['A'],2)
        self.assertEqual(saved['public_statistics']['attempted']['A'],2)
        self.assertEqual(saved['public_statistics']['accepted']['A'],2)
        self.assertEqual(view.update.call_args.args[0].rank,'2')
