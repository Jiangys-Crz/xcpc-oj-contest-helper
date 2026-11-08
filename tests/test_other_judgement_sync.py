from copy import deepcopy
from dataclasses import asdict
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

import test_hydro_live as hydro
import test_nowcoder_live as nk
from vp_helper import cli, live
from vp_helper.hydro import HydroLive
from vp_helper.models import Contest, Reference, Rules, Submission, VPError
from vp_helper.nowcoder import NowcoderLive
from vp_helper.pending_results import PendingResults, seed_pending
from vp_helper.qoj import QOJ
from vp_helper.storage import read_json, write_json
from test_cf_start_sync import ImmediatePool
from test_workflows import Isolated


def qoj_table(rows):
    return BeautifulSoup('<table><thead><tr><th>#</th><th>Problem</th><th>Submitter</th><th>Score</th><th>Time</th></tr></thead>'
                         '<tbody>'+''.join(rows)+'</tbody></table>', 'html.parser')


def qoj_row(sid='100', status='100', marker=False):
    return (f'<tr><td><a href="/submission/{sid}">{sid}</a></td>'
            '<td><a href="/contest/123/problem/10">A</a></td>'
            '<td><a class="uoj-username" href="/user/profile/tester">tester</a></td>'
            f'<td>{status}'+(f'<span id="status_details_{sid}"></span>' if marker else '')+
            '</td><td><time datetime="1970-01-01T00:20:00Z"></time></td></tr>')


class OtherJudgementSyncTests(Isolated):
    def qoj(self, rows):
        api=QOJ('https://qoj.ac/contest/123', {'handle':'tester'})
        api.start_time=1000
        api._page=Mock(return_value=qoj_table(rows))
        api.web=Mock()
        api.web.get.side_effect=VPError('No detail metadata in this fixture')
        c=Contest('qoj','123',api.url,'Fixture','Fixture',{'A':'10'},Rules(18000))
        return api,c

    def hydro(self):
        api=HydroLive(hydro.URL, {'handle':'tester','uid':42})
        api._identified=True
        c=Contest('cwnuoj',hydro.TID,hydro.URL,'Fixture','Fixture',{},Rules(1))
        api.apply_metadata(c,hydro.details())
        api._unfiltered_records=True
        return api,c

    def nk(self):
        api=NowcoderLive(nk.URL, {'handle':'42','uid':42})
        api._identified=True
        c=Contest('nowcoder','123',nk.URL,'Fixture','Fixture',{},Rules(1))
        api.apply_metadata(c,nk.details())
        c.problems={'A':'10'}
        return api,c

    def test_qoj_final_score_overrides_leftover_javascript_marker(self):
        api,c=self.qoj([qoj_row(status='Judging',marker=True)])
        self.assertEqual(api.submissions(c)[0].verdict,'PENDING')
        api._page.return_value=qoj_table([qoj_row(marker=True),qoj_row('101','Wrong Answer',marker=True)])
        records=api.submissions(c)
        self.assertEqual([(r.id,r.verdict) for r in records],[('100','CORRECT'),('101','WRONG-ANSWER')])

    def test_qoj_global_problem_links_are_imported_as_practice_unless_native_marked(self):
        first=qoj_row().replace('/contest/123/problem/10','/problem/10')
        second=qoj_row('101').replace('/contest/123/problem/10','/problem/10').replace(
            '</a></td><td><a href="/problem/10">','</a><sup><a href="/contest/123">#</a></sup></td><td><a href="/problem/10">')
        api,c=self.qoj([first,second])
        records=api.submissions(c)
        self.assertEqual([r.id for r in records],['100','101'])
        self.assertEqual([r.practice for r in records],[True,False])

    def test_qoj_zero_score_rejudge_refreshes_without_waiting_minutes(self):
        api,c=self.qoj([qoj_row(status='0')])
        def detail(label):
            return BeautifulSoup(f'<div class="card"><div class="card-header">Details</div><div class="col-sm-2">{label}</div></div>','html.parser')
        api.web.soup.side_effect=[detail('Wrong Answer'),detail('Runtime Error')]
        with patch('time.monotonic',return_value=0):
            self.assertEqual(api.submissions(c)[0].verdict,'WRONG-ANSWER')
        with patch('time.monotonic',return_value=14):
            self.assertEqual(api.submissions(c)[0].verdict,'RUN-ERROR')
        self.assertEqual(api.web.soup.call_count,2)

    def test_qoj_large_failed_history_does_not_trigger_unbounded_detail_requests(self):
        api,c=self.qoj([qoj_row(str(i),'0') for i in range(100,105)])
        detail=BeautifulSoup('<div class="card-header">Details</div><div class="col-sm-2">Wrong Answer</div>','html.parser')
        api.web.soup.return_value=detail
        with patch('time.monotonic',return_value=0):
            self.assertEqual([s.verdict for s in api.submissions(c)].count('WRONG-ANSWER'),2)
        self.assertEqual(api.web.soup.call_count,2)
        with patch('time.monotonic',return_value=2):
            self.assertEqual([s.verdict for s in api.submissions(c)].count('WRONG-ANSWER'),4)
        self.assertEqual(api.web.soup.call_count,4)

    def test_qoj_later_page_failure_keeps_current_web_results(self):
        api,c=self.qoj([qoj_row()])
        first=qoj_table([qoj_row()])
        first.append(BeautifulSoup('<a href="?page=2">2</a>','html.parser'))
        api._page.side_effect=[first,VPError('older page timeout')]
        self.assertEqual(api.submissions(c)[0].verdict,'CORRECT')
        self.assertFalse(api.submissions_complete)
        self.assertIn('Recent submissions synced',api.submission_message)

    def test_qoj_detail_failure_keeps_other_records_and_retries_quickly(self):
        api,c=self.qoj([qoj_row(status='0'),qoj_row('101')])
        detail=BeautifulSoup('<div class="card-header">Details</div><div class="col-sm-2">Compile Error</div>','html.parser')
        api.web.soup.side_effect=[VPError('detail timeout'),detail]
        with patch('time.monotonic',return_value=0):
            self.assertEqual([s.verdict for s in api.submissions(c)],['UNAVAILABLE','CORRECT'])
        with patch('time.monotonic',return_value=3):
            self.assertEqual([s.verdict for s in api.submissions(c)],['COMPILER-ERROR','CORRECT'])

    def test_qoj_pending_detail_and_same_account_checks(self):
        api,c=self.qoj([qoj_row(status='Judging',marker=True)])
        api.web.get.side_effect=None
        api.web.get.return_value=Mock(url='https://qoj.ac/submission/100',text=str(qoj_table([qoj_row()])))
        with patch('time.monotonic',return_value=0): api.submissions(c)
        with patch('time.monotonic',return_value=6):
            self.assertEqual(api.submissions(c)[0].verdict,'CORRECT')
        api.web.get.return_value.text=str(qoj_table([qoj_row()])).replace('/user/profile/tester','/user/profile/other')
        with self.assertRaises(VPError): api.pending_detail(c,{'id':'100','problem':'A'})

    def test_hydro_web_records_rejudge_and_lingering_list_pending(self):
        api,c=self.hydro()
        raw={'_id':hydro.RID,'uid':42,'pid':10,'domainId':'system','contest':hydro.TID,'status':20,'score':0}
        final={**raw,'status':1,'score':100}
        api.get_json=Mock(side_effect=lambda url,**params: {'rdoc':final} if url.endswith('/'+hydro.RID)
                         else {'ratingDynamicRecords':True,'rdocs':[raw],'ratingRecordsHasNext':False})
        with patch('time.monotonic',return_value=0):
            self.assertEqual(api.submissions(c)[0]['verdict'],'PENDING')
        with patch('time.monotonic',return_value=6):
            self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        with patch('time.monotonic',return_value=7):
            self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        raw.update(status=1,score=100)
        api.submissions(c)
        raw['status']=20
        with patch('time.monotonic',return_value=8):
            self.assertEqual(api.submissions(c)[0]['verdict'],'PENDING')

    def test_hydro_pending_detail_rejects_foreign_uid_contest_and_problem(self):
        api,c=self.hydro()
        raw={'_id':hydro.RID,'uid':42,'pid':10,'contest':hydro.TID,'status':1}
        for change in ({'uid':99},{'pid':99},{'contest':'b'*24},{'_id':'b'*24}):
            api.get_json=Mock(return_value={'rdoc':{**raw,**change}})
            with self.assertRaises(VPError): api.pending_detail(c,{'id':hydro.RID,'problem':'A'})

    def test_hydro_failed_later_page_and_practice_stream_preserve_contest_results(self):
        api,c=self.hydro()
        raw={'_id':hydro.RID,'uid':42,'pid':10,'status':1}
        api.get_json=Mock(side_effect=[{'ratingDynamicRecords':True,'rdocs':[raw], 'ratingRecordsHasNext':True,
                                      'ratingRecordsNextUrl':'/record?tid='+hydro.TID+'&page=2'},VPError('timeout')])
        self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        self.assertFalse(api.submissions_complete)
        c.extra['phase']='FINISHED'
        api.get_json.side_effect=[{'ratingDynamicRecords':True,'rdocs':[raw],'ratingRecordsHasNext':False},VPError('practice timeout')]
        self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        self.assertIn('Practice records',api.submission_message)

    def test_nowcoder_native_status_endpoint_resolves_lingering_list_pending(self):
        api,c=self.nk()
        api.get_json=Mock(return_value=nk.status([nk.record(verdict='正在评测')]))
        api.web=Mock()
        api.web.get.side_effect=[Mock(url=nk.URL+'/A',text=nk.page()),
                                 Mock(url=nk.ORIGIN+'/nccommon/status',json=Mock(return_value={
                                     'code':0,'id':77,'status':5,'judgeReplyDesc':'答案正确'}))]
        with patch('time.monotonic',return_value=0):
            self.assertEqual(api.submissions(c)[0]['verdict'],'PENDING')
        with patch('time.monotonic',return_value=6):
            self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        self.assertEqual(api.web.get.call_args.kwargs['params']['submissionId'],'77')
        api.web.session.post.assert_not_called()

    def test_nowcoder_status_cannot_resolve_a_different_submission(self):
        api,c=self.nk()
        api._status_tags['A']={'tagId':'4','subTagId':'1'}
        api.web=Mock()
        api.web.get.return_value=Mock(url=nk.ORIGIN+'/nccommon/status',json=Mock(return_value={
            'code':0,'id':999,'status':5,'judgeReplyDesc':'答案正确'}))
        with self.assertRaises(VPError): api.pending_detail(c,{'id':'77','problem':'A'})

    def test_nowcoder_failed_page_count_change_and_overlap_keep_recent_records(self):
        api,c=self.nk()
        api.get_json=Mock(side_effect=[nk.status([nk.record()],1,2,2),VPError('page timeout')])
        self.assertEqual(api.submissions(c)[0]['verdict'],'CORRECT')
        self.assertFalse(api.submissions_complete)
        api.get_json.side_effect=[nk.status([nk.record()],1,2,2), nk.status([nk.record(),nk.record(78)],2,2,3)]
        self.assertEqual([e['id'] for e in api.submissions(c)],['77','78'])
        self.assertFalse(api.submissions_complete)
        self.assertIn('history changed',api.submission_message)

    def test_pending_seeding_never_refreshes_qoj_failure_cache(self):
        api,c=self.qoj([])
        with patch('time.monotonic',return_value=0):
            api.seed_submissions([Submission('100','me','A',60,'WRONG-ANSWER')])
        original=deepcopy(api._verdict_cache)
        with patch('time.monotonic',return_value=500):
            seed_pending(api,'qoj',[Submission('100','me','A',60,'WRONG-ANSWER')])
        self.assertEqual(api._verdict_cache,original)

    def test_qoj_monitor_replaces_pending_and_imports_web_submission_without_receipts(self):
        path,state=cli.prepare_start('https://qoj.ac/contest/123')
        api,c=self.qoj([])
        state.update(contest=c.to_dict(),start_time=1000,submissions=[asdict(Submission('100','me','A',200,'PENDING'))])
        write_json(path,state)
        first=qoj_table([qoj_row(status='Judging',marker=True)])
        final=qoj_table([qoj_row(),qoj_row('101','Wrong Answer')])
        api._page.side_effect=[first,final]
        api.reference=Mock(return_value=Reference())
        self.clock=1300
        view=Mock()
        view.__enter__=Mock(return_value=view)
        view.__exit__=Mock(return_value=False)
        view.input.side_effect=[True]*4+[False]
        from itertools import count
        with patch.object(cli,'adapter',return_value=api),patch.object(cli,'TerminalDashboard',return_value=view), \
             patch.object(cli,'ThreadPoolExecutor',ImmediatePool),patch('time.sleep'),patch('time.monotonic',side_effect=count(0,10)):
            cli.monitor(path)
        self.assertEqual({e['id']:e['verdict'] for e in read_json(path)['submissions']},
                         {'100':'CORRECT','101':'WRONG-ANSWER'})
        self.assertFalse(path.with_suffix('.receipts').exists())

    def test_live_monitors_read_web_submissions_and_replace_pending_on_both_sites(self):
        from itertools import count
        for platform in ('cwnuoj','nowcoder'):
            with self.subTest(platform=platform):
                api,c=self.hydro() if platform=='cwnuoj' else self.nk()
                self.clock=1300
                api.web=Mock()
                snapshot={'phase':'CODING','frozen':False,'own':None,'stats':{},'at':1000}
                api.snapshot=Mock(return_value=snapshot)
                old_id=hydro.RID if platform=='cwnuoj' else '77'
                new_id=f'{1101:08x}'+ '2'*16 if platform=='cwnuoj' else '78'
                polls=[]
                def response(*args,**kwargs):
                    polls.append(1)
                    first=len(polls)==1
                    if platform=='cwnuoj':
                        rows=[{'_id':old_id,'uid':42,'pid':10,'status':20 if first else 1}]
                        if not first: rows.append({'_id':new_id,'uid':42,'pid':10,'status':2})
                        return {'ratingDynamicRecords':True,'rdocs':rows,'ratingRecordsHasNext':False}
                    rows=[nk.record(verdict='正在评测' if first else '答案正确')]
                    if not first: rows.append(nk.record(78,verdict='答案错误'))
                    return nk.status(rows)
                api.get_json=Mock(side_effect=response)
                directory=self.root/platform
                directory.mkdir()
                path=self.root/'state/vp'/f'live-{platform}-fixture.json'
                state={'mode':'live','url':c.url,'handle':api.handle,'contest':c.to_dict(),
                       'start_time':c.original_start,'directory':str(directory),
                       'history_path':str(self.root/(platform+'-history.csv')),
                       'submissions':[asdict(Submission(old_id,'me','A',100,'PENDING'))]}
                write_json(path,state)
                write_json(directory/'contest.json',{'platform':platform})
                view=Mock()
                view.__enter__=Mock(return_value=view)
                view.__exit__=Mock(return_value=False)
                view.input.side_effect=[True]*4+[False]
                with patch.object(live,'account_for',return_value=api.account),patch.object(live,'live_adapter',return_value=api), \
                     patch.object(live,'TerminalDashboard',return_value=view),patch.object(live,'ThreadPoolExecutor',ImmediatePool), \
                     patch('time.sleep'),patch('time.monotonic',side_effect=count(0,10)):
                    live.monitor_live(path)
                expected={old_id:'CORRECT',new_id:'WRONG-ANSWER'}
                self.assertEqual({e['id']:e['verdict'] for e in read_json(path)['submissions']},expected)
                self.assertEqual({e['id']:e['verdict'] for e in view.update.call_args.args[0].submissions},expected)
                self.assertFalse(path.with_suffix('.receipts').exists())
                api.web.session.post.assert_not_called()

    def test_many_pending_checks_are_bounded_and_eventually_visit_every_record(self):
        pending=[{'id':str(i),'problem':'A','verdict':'PENDING'} for i in range(5)]
        feed=PendingResults()
        api=Mock(submission_message='')
        lookup=Mock(return_value={'verdict':'CORRECT'})
        with patch('time.monotonic',return_value=0): feed.update(api,pending,lookup)
        for now in (6,8,10):
            before=lookup.call_count
            with patch('time.monotonic',return_value=now): feed.update(api,pending,lookup)
            self.assertLessEqual(lookup.call_count-before,2)
        self.assertEqual({call.args[0]['id'] for call in lookup.call_args_list},{str(i) for i in range(5)})
