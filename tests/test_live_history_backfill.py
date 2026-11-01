"""Web submissions predating a live command retain native timing and scores."""
from copy import deepcopy
import io
from unittest.mock import Mock, patch

from rich.console import Console

from vp_helper import cloud, live
from vp_helper.codeforces_live import CodeforcesLive, submission_records
from vp_helper.hydro import HydroLive
from vp_helper.models import Contest, Rules
from vp_helper.nowcoder import NowcoderLive, PROBLEMS, RANK, STATUS
from vp_helper.storage import read_json, write_json
from vp_helper.tui import Dashboard, LiveDashboard
from test_workflows import Isolated
import test_codeforces_live as cf
import test_hydro_live as hydro
import test_nowcoder_live as nk


class LiveHistoryBackfillTests(Isolated):
    def test_all_web_pages_before_first_live_entry_keep_native_rank_penalty_and_acceptance(self):
        self.clock=2800
        for platform in ('cf','cwnuoj','nowcoder'):
            with self.subTest(platform=platform):
                if platform=='cf':
                    url=cf.URL
                    api=CodeforcesLive(url,{'handle':'tester','cookie_source':'manual'})
                    api.web=Mock()
                    data=cf.standings(scoring='ICPC',points=1)
                    data['rows'][0]['problemResults'][0].update(points=1,type='FINAL')
                    pages=[[cf.submission(i,at=1100,testset='TESTS') for i in range(1,1001)],
                           [cf.submission(1001,at=1700,testset='TESTS')]]
                    api.api=Mock(side_effect=[data,*pages])
                    contest=cf.contest()
                    expected_count,expected_rank,expected_penalty=1001,'17',42
                elif platform=='cwnuoj':
                    url=hydro.URL
                    api=HydroLive(url,{'handle':'tester','uid':42,'cookie_source':'manual'})
                    api._identified=True
                    api.web=Mock()
                    api.details=Mock(return_value=hydro.details())
                    first={'ratingDynamicRecords':True,
                           'rdocs':[{'_id':f'{1100:08x}'+'1'*16,'uid':42,'pid':10,'status':1,'score':100,'contest':hydro.TID}],
                           'ratingRecordsHasNext':True,'ratingRecordsNextUrl':'/record?tid='+hydro.TID+'&page=2'}
                    second={'ratingDynamicRecords':True,
                            'rdocs':[{'_id':f'{1700:08x}'+'2'*16,'uid':42,'pid':11,'status':2,'score':0,'contest':hydro.TID}],
                            'ratingRecordsHasNext':False}
                    api.get_json=Mock(side_effect=[hydro.board(),first,first,second])
                    contest=Contest(platform,hydro.TID,url,'Hydro fixture','legacy',{},Rules(1))
                    expected_count,expected_rank,expected_penalty=2,'7',25
                else:
                    url=nk.URL
                    api=NowcoderLive(url,{'handle':'42','uid':42,'cookie_source':'manual'})
                    api._identified=True
                    api.web=Mock()
                    api.details=Mock(return_value=nk.details(now=self.clock))
                    first=nk.status([nk.record(77,when=1100)],1,2,2)
                    second=nk.status([{**nk.record(78,when=1700,verdict='答案错误'),'problemId':11}],2,2,2)
                    def response(path,**params):
                        if path==PROBLEMS:return deepcopy(nk.problems())
                        if path==RANK:return deepcopy(nk.rank())
                        self.assertEqual(path,STATUS)
                        return deepcopy(first if params['page']==1 else second)
                    api.get_json=Mock(side_effect=response)
                    contest=Contest(platform,'123',url,'Nowcoder fixture','legacy',{},Rules(1))
                    expected_count,expected_rank,expected_penalty=2,'17',20.5
                with patch('vp_helper.hydro.platform_now',return_value=self.clock), \
                        patch('vp_helper.nowcoder.platform_now',return_value=self.clock), \
                        patch('vp_helper.codeforces_live.platform_now',return_value=self.clock):
                    snapshot=api.snapshot(contest)
                    records=api.submissions(contest)
                self.assertTrue(api.submissions_complete)
                self.assertEqual(len(records),expected_count)
                self.assertTrue(all(contest.original_start+record['time']<self.clock for record in records))
                self.assertTrue(all(not record['practice'] for record in records))
                self.assertEqual(min(record['time'] for record in records),100)
                self.assertEqual((contest.original_start,contest.rules.duration),(1000,7200))
                directory=self.root/platform
                directory.mkdir()
                source=directory/'A.cpp'
                source.write_text('unchanged solution')
                path=self.root/'state/vp'/('late-live-'+platform+'.json')
                state={'mode':'live','url':url,'handle':api.handle,'start_time':1000,
                       'contest':contest.to_dict(),'directory':str(directory),'status':'running',
                       'submissions':[],'submissions_complete':api.submissions_complete,
                       'history_path':str(self.root/(platform+'-history.csv'))}
                write_json(path,state)
                live.save_progress(path,state,contest,snapshot,{record['id']:record for record in records},self.clock)
                saved=read_json(path)
                self.assertEqual(len(saved['submissions']),expected_count)
                self.assertEqual(str(saved['snapshot']['own']['rank']),expected_rank)
                projected=submission_records(saved['submissions'],'CODING')
                view=LiveDashboard(contest,self.clock-contest.original_start,snapshot,projected)
                self.assertTrue(any(row[2].plain=='1970-01-01 08:18:20' for row in Dashboard.record_rows(view)))
                output=io.StringIO()
                Console(file=output,width=180,color_system=None).print(view.fixed(180))
                self.assertIn(str(snapshot['own']['penalty']),output.getvalue())
                self.assertTrue(snapshot['own']['problemResults'][0]['accepted'])
                self.assertFalse(snapshot['own']['problemResults'][1]['accepted'])
                payload=cloud.sync_payload(path,saved)
                self.assertEqual(payload['rank'],expected_rank)
                self.assertEqual(payload['penalty'],expected_penalty)
                self.assertEqual(payload['solved'],1)
                self.assertEqual(len(payload['submissions']),expected_count)
                self.assertTrue(payload['cells']['A']['solved'])
                self.assertFalse(payload['cells']['B']['solved'])
                self.assertEqual(source.read_text(),'unchanged solution')
                api.web.post.assert_not_called()
