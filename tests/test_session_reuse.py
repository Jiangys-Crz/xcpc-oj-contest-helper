from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from vp_helper import cli, live, practice
from vp_helper.models import Contest, Rules, VPError
from vp_helper.qoj import QOJ
from vp_helper.sessions import attempts, end_attempt
from vp_helper.storage import read_json, state_directory, write_json
from test_workflows import Isolated, URL


class SessionReuseTests(Isolated):
    def test_plain_vp_command_resumes_without_new_registration_or_time_prompt(self):
        path,state=cli.prepare_start(URL)
        self.api.reset_mock()
        with patch.object(cli,'open_state') as opened,patch('vp_helper.scheduling.prompt_start') as prompt:
            cli.start(URL)
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0],path)
        prompt.assert_not_called()
        self.api.start.assert_not_called()
        self.api.prepare.assert_not_called()
        self.assertEqual(len(attempts()),1)

    def test_explicit_restart_reuses_folder_and_keeps_source_and_prior_history(self):
        first,old=cli.prepare_start(URL)
        source=Path(old['directory'])/'A.cpp'
        source.write_text('preserved code')
        self.clock=2000
        with patch.object(cli,'open_state') as opened,patch('vp_helper.scheduling.prompt_start',return_value=2000):
            cli.start(URL,restart=True)
        new=opened.call_args.args[1]
        self.assertNotEqual(opened.call_args.args[0],first)
        self.assertEqual(new['directory'],old['directory'])
        self.assertEqual(source.read_text(),'preserved code')
        self.assertEqual(len(list((self.root/'desktop').iterdir())),1)
        self.assertEqual(len(attempts()),2)
        self.assertEqual(read_json(source.parent/'contest.json')['vp_context']['start_time'],2000)
        self.assertEqual(read_json(first)['start_time'],1000)

    def test_bt_keeps_the_same_latest_vp_round_and_folder(self):
        path,state=cli.prepare_start(URL)
        self.clock=1500
        end_attempt()
        for _ in range(2):
            reopened,current=practice.prepare(URL)
            self.assertEqual(reopened,path)
            self.assertEqual(current['directory'],state['directory'])
        self.assertEqual(len(attempts()),1)
        self.assertEqual(Path(state['directory']).name,'test contest')

    def test_bt_selects_latest_round_even_if_current_pointer_is_older(self):
        first,_=cli.prepare_start(URL)
        self.clock=2000
        latest,state=cli.prepare_start(URL)
        self.clock=2500
        end_attempt(state['attempt_id'])
        write_json(state_directory()/'current.json',{'state_path':str(first)})
        reopened,_=practice.prepare(URL)
        self.assertEqual(reopened,latest)

    def live_record(self):
        url='https://oj.cwnupaa.com/contest/'+'a'*24
        contest=Contest('cwnuoj','a'*24,url,'CWNUOJ contest name','legacy',{'A':'10'},Rules(100),
                        original_start=1000,extra={'uid':42,'phase':'FINISHED','scoring':'ICPC','domain':'system'})
        directory=self.root/'desktop'/contest.name
        directory.mkdir(parents=True)
        (directory/'A.cpp').write_text('original live code')
        path=state_directory()/'live-cwnuoj-test.json'
        state={'mode':'live','url':url,'start_time':1000,'handle':'tester','directory':str(directory),
               'contest':contest.to_dict(),'status':'complete','tmux_session':'saved-live','submissions':[]}
        write_json(path,state)
        write_json(state_directory()/'current.json',{'state_path':str(path)})
        self.stack.enter_context(patch.object(cli,'load_account',return_value={'cwnuoj':self.api.account}))
        self.stack.enter_context(patch.object(live,'live_adapter',return_value=self.api))
        self.stack.enter_context(patch.object(live,'configure_submission',side_effect=lambda a,c,d:
            write_json(d/'contest.json',{'platform':'cwnuoj','mode':'live','base_url':url,'handle':'tester'})))
        return url,path,state

    def test_vp_url_restores_existing_live_instead_of_trying_native_vp(self):
        url,path,state=self.live_record()
        self.api.reset_mock()
        with patch.object(cli,'open_state') as opened,patch('vp_helper.scheduling.prompt_start') as prompt:
            cli.start(url)
        self.assertEqual(opened.call_args.args[0],path)
        self.assertEqual(opened.call_args.args[1]['mode'],'live')
        self.assertEqual((Path(state['directory'])/'A.cpp').read_text(),'original live code')
        self.api.start.assert_not_called()
        self.api.prepare.assert_not_called()
        prompt.assert_not_called()

    def test_bt_uses_existing_latest_live_record_without_new_state_or_folder(self):
        url,path,state=self.live_record()
        self.clock=1200
        for _ in range(2):
            reopened,current=practice.prepare(url)
            self.assertEqual(reopened,path)
            self.assertEqual(current['mode'],'live')
            self.assertTrue(current['practice_view'])
            self.assertEqual(current['directory'],state['directory'])
        self.assertEqual(len(attempts()),1)
        self.assertEqual(len(list((self.root/'desktop').iterdir())),1)

    def test_non_managed_same_name_folder_is_never_overwritten(self):
        directory=self.root/'desktop'/'test contest'
        directory.mkdir(parents=True)
        (directory/'A.cpp').write_text('unrelated user code')
        with self.assertRaisesRegex(VPError,'未覆盖'):
            cli.prepare_start(URL)
        self.assertEqual((directory/'A.cpp').read_text(),'unrelated user code')
        self.api.start.assert_not_called()

    def test_qoj_uses_raw_title_without_year_or_site_and_selects_ccpc_by_name(self):
        for title in ('QOJ Training Round','ICPC Practice','2025 CCPC Final','CCPC 新赛制练习','ccpc training'):
            api=QOJ(URL,{'handle':'tester'})
            api._page=Mock(return_value=BeautifulSoup(f'<h1>{title}</h1>Ruleset: ICPC<table><tr><td>A</td>'
                '<td><a href="/contest/123/problem/10">A</a></td></tr></table><a href="download.php?x=1">Statements</a>','html.parser'))
            api.web=Mock()
            api.web.soup.return_value=BeautifulSoup('<table><tr><td><a href="/contest/123">round</a>5 hours</td></tr></table>','html.parser')
            contest=api.prepare()
            self.assertEqual(contest.name,title)
            self.assertEqual(contest.rules.problem_reveal_count,50 if 'CCPC' in title.upper() else None)
            self.assertEqual(contest.rules.duration,18000)

    def test_standalone_bt_monitor_never_schedules_statement_downloads(self):
        from concurrent.futures import Future
        path,state=cli.prepare_start(URL)
        state['mode']='practice'
        write_json(path,state)
        client=Mock(handle='tester')
        client.submissions.return_value=[]
        view=Mock()
        view.input.side_effect=[True,True,False]
        view.__enter__=Mock(return_value=view)
        view.__exit__=Mock(return_value=None)
        class Pool:
            def __init__(self,**kw): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def submit(self,fn,*args):
                task=Future();task.set_result(fn(*args));return task
        with patch.object(practice,'Practice',return_value=client),patch.object(practice,'TerminalDashboard',return_value=view), \
                patch.object(practice,'ThreadPoolExecutor',Pool),patch('time.sleep'):
            practice.monitor(path)
        client.statements.assert_not_called()

    def test_hydro_correction_polling_has_no_tid_and_filters_other_records(self):
        from vp_helper.hydro import HydroLive
        import test_hydro_live as h
        client=HydroLive(h.URL,{'handle':'tester','uid':42})
        client._identified=True
        contest=Contest('cwnuoj',h.TID,h.URL,'fixture','fixture',{'A':'10'},Rules(7200),
                        original_start=8500,extra={'uid':42,'practice':True,'phase':'FINISHED','end_time':8200})
        sid=f'{8600:08x}'+'1'*16
        records=[{'_id':sid,'uid':42,'pid':10,'domainId':'system','status':1},
                 {'_id':f'{8601:08x}'+ '2'*16,'uid':43,'pid':10,'status':1},
                 {'_id':f'{8602:08x}'+ '3'*16,'uid':42,'pid':10,'contest':'b'*24,'status':1}]
        client.get_json=Mock(side_effect=[{'rdocs':records},{'rdocs':[]}])
        events=client.submissions(contest)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['id'],sid)
        self.assertTrue(events[0]['practice'])
        for call in client.get_json.call_args_list:
            self.assertNotIn('tid',call.kwargs)
        self.assertEqual(events[0]['verdict'],'CORRECT')
