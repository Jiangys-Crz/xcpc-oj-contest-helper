from pathlib import Path
from unittest.mock import Mock, patch

from vp_helper import cli, commands
from vp_helper.models import Contest, Reference, Rules, VPError
from vp_helper.storage import read_json, write_json
from vp_helper.sessions import save_state
from test_workflows import Isolated, URL


class DashboardTests(Isolated):
    def test_waiting_scoreboard_shows_saved_account_handle_instead_of_internal_me(self):
        for platform,host,segment,ident in (('cf','codeforces.com','contest','123'),
                                          ('gym','codeforces.com','gym','123'),
                                          ('qoj','qoj.ac','contest','123')):
            with self.subTest(platform=platform):
                contest=Contest(platform,ident,f'https://{host}/{segment}/{ident}',
                                'Waiting '+platform,'Waiting '+platform,{'A':'10'},Rules(18000))
                path=self.root/f'vp-{platform}-waiting.json'
                state={'mode':'vp','url':contest.url,'contest':contest.to_dict(), 'handle':'ActualCFUser',
                       'directory':str(self.root),'start_time':1180,'requested_start':1180,
                       'status':'scheduled','submissions':[]}
                write_json(path,state)
                view=self.monitor_view()
                view.input.side_effect=[True,False]
                with patch.object(cli,'TerminalDashboard',return_value=view),patch('time.sleep'):
                    cli.monitor(path)
                dashboard=view.update.call_args.args[0]
                self.assertEqual(dashboard.me.name,'ActualCFUser')
                self.assertEqual(dashboard.me.id,'me')
                self.assertEqual(dashboard.elapsed,-180)
                self.api.start.assert_not_called()

    def monitor_view(self):
        self.api.submissions.return_value=[]
        self.api.reference.return_value=Reference()
        view=Mock()
        view.__enter__=Mock(return_value=view)
        view.__exit__=Mock(return_value=False)
        return view

    def test_started_qoj_monitor_renders_score_without_entering_schedule_branch(self):
        path,state=cli.prepare_start(URL)
        self.api.reset_mock()
        view=self.monitor_view()
        view.input.side_effect=[True,False]
        with patch.object(cli,'TerminalDashboard',return_value=view),patch('time.sleep'):
            cli.monitor(path)
        dashboard=view.update.call_args.args[0]
        self.assertEqual(dashboard.contest.name,'test contest')
        self.assertEqual(dashboard.elapsed,0)
        self.assertEqual(dashboard.me.name,'tester')
        self.assertEqual(dashboard.me.solved,0)
        self.api.start.assert_not_called()
        self.assertEqual(read_json(path)['start_time'],1000)

    def test_resumed_qoj_monitor_immediately_shows_saved_acceptance(self):
        path,state=cli.prepare_start(URL)
        state['submissions']=[{'id':'1','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]
        write_json(path,state)
        self.clock=1120
        self.api.reset_mock()
        view=self.monitor_view()
        view.input.side_effect=[True,False]
        with patch.object(cli,'TerminalDashboard',return_value=view),patch('time.sleep'):
            cli.monitor(path)
        dashboard=view.update.call_args.args[0]
        self.assertEqual(dashboard.elapsed,120)
        self.assertEqual(dashboard.me.solved,1)
        self.assertEqual(dashboard.submissions[0]['id'],'1')
        self.api.start.assert_not_called()
        self.assertEqual(read_json(path)['start_time'],1000)

    def test_scheduled_qoj_monitor_transitions_to_running_scoreboard(self):
        path,state=cli.prepare_start(URL,scheduled_start=1180)
        self.clock=1180
        view=self.monitor_view()
        view.input.side_effect=[True,True,False]
        with patch.object(cli,'TerminalDashboard',return_value=view),patch('time.sleep'):
            cli.monitor(path)
        self.api.start.assert_called_once()
        self.assertEqual(view.update.call_args.args[0].me.name,'tester')
        self.assertEqual(read_json(path)['status'],'running')
        self.assertEqual(read_json(path)['start_time'],1180)

    def test_practice_rank_is_frozen_across_reopen_and_stale_monitor_writes(self):
        path,state=cli.prepare_start(URL)
        state.update(status='complete',summary={'rank':'3'})
        write_json(path,state)
        folder=Path(state['directory'])
        with patch('vp_helper.cli.Path.cwd',return_value=folder),patch.object(cli,'open_state') as opened:
            cli.dashboard()
            self.assertEqual(opened.call_args.args[1]['practice_rank'],'3')
            stale=read_json(path)
            stale['summary']['rank']='8'
            stale['practice_rank']='99'
            save_state(path,stale)
            cli.dashboard()
            self.assertEqual(opened.call_args.args[1]['practice_rank'],'3')
        self.assertEqual(read_json(path)['practice_rank'],'3')

    def test_command_dispatches_to_current_folder_dashboard(self):
        with patch('sys.argv',['xcpc','dashboard']),patch.object(cli,'dashboard') as dashboard:
            commands.main()
        dashboard.assert_called_once()

    def test_active_vp_restores_from_subdirectory_without_registration_or_new_clock(self):
        path,state=cli.prepare_start(URL)
        folder=Path(state['directory'])
        (folder/'nested').mkdir()
        (folder/'A.cpp').write_text('original code')
        self.api.reset_mock()
        with patch('vp_helper.cli.Path.cwd',return_value=folder/'nested'),patch.object(cli,'open_state') as opened, \
             patch('vp_helper.scheduling.prompt_start') as prompt:
            cli.dashboard()
        self.assertEqual(opened.call_args.args[0],path)
        self.assertEqual(opened.call_args.args[1]['start_time'],1000)
        self.assertFalse(opened.call_args.args[1].get('practice_view'))
        self.assertEqual((folder/'A.cpp').read_text(),'original code')
        self.api.prepare.assert_not_called()
        self.api.start.assert_not_called()
        prompt.assert_not_called()

    def test_ended_vp_restores_practice_view_with_original_ac_and_files(self):
        path,state=cli.prepare_start(URL)
        state['submissions']=[{'id':'1','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]
        write_json(path,state)
        folder=Path(state['directory'])
        (folder/'A.cpp').write_text('preserved solution')
        self.clock=20000
        with patch('vp_helper.cli.Path.cwd',return_value=folder),patch.object(cli,'open_state') as opened:
            cli.dashboard()
        restored=opened.call_args.args[1]
        self.assertTrue(restored['practice_view'])
        self.assertEqual(restored['submissions'],state['submissions'])
        self.assertEqual((folder/'A.cpp').read_text(),'preserved solution')
        self.assertEqual(read_json(path)['start_time'],1000)
        self.api.start.assert_called_once()

    def test_live_command_during_vp_restores_vp_instead_of_starting_live(self):
        path,_=cli.prepare_start(URL)
        self.api.reset_mock()
        with patch.object(cli,'open_state') as opened,patch('vp_helper.live.prepare_live') as prepare:
            cli.start(URL,live=True)
        self.assertEqual(opened.call_args.args[0],path)
        self.assertEqual(opened.call_args.args[1]['mode'],'vp')
        self.api.start.assert_not_called()
        prepare.assert_not_called()

    def test_finished_live_restores_practice_with_saved_records(self):
        from test_session_reuse import SessionReuseTests
        url,path,state=SessionReuseTests.live_record(self)
        state['submissions']=[{'id':'1','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]
        write_json(path,state)
        self.api.reset_mock()
        self.clock=1200
        with patch('vp_helper.cli.Path.cwd',return_value=Path(state['directory'])),patch.object(cli,'open_state') as opened:
            cli.dashboard()
        restored=opened.call_args.args[1]
        self.assertEqual(opened.call_args.args[0],path)
        self.assertEqual(restored['mode'],'live')
        self.assertTrue(restored['practice_view'])
        self.assertEqual(restored['submissions'],state['submissions'])
        self.api.start.assert_not_called()
        self.api.prepare.assert_not_called()

    def test_waiting_qoj_keeps_its_timer_even_after_original_requested_window(self):
        path,state=cli.prepare_start(URL,scheduled_start=3000)
        self.clock=40000
        with patch('vp_helper.cli.Path.cwd',return_value=Path(state['directory'])),patch.object(cli,'open_state') as opened:
            cli.dashboard()
        self.assertEqual(opened.call_args.args[1]['status'],'scheduled')
        self.assertFalse(opened.call_args.args[1].get('practice_view'))
        self.api.start.assert_not_called()

    def test_dashboard_uses_folder_instead_of_unrelated_current_pointer(self):
        first,state=cli.prepare_start(URL)
        other=URL.replace('123','456')
        self.api.prepare.side_effect=lambda: Contest('qoj','456',other,'Other contest','Other contest',{'A':'10'},Rules(18000))
        cli.prepare_start(other)
        with patch('vp_helper.cli.Path.cwd',return_value=Path(state['directory'])),patch.object(cli,'open_state') as opened:
            cli.dashboard()
        self.assertEqual(opened.call_args.args[0],first)

    def test_dashboard_outside_contest_fails_without_starting_anything(self):
        cli.prepare_start(URL)
        self.api.reset_mock()
        with patch('vp_helper.cli.Path.cwd',return_value=self.root):
            with self.assertRaisesRegex(VPError,'进入原比赛文件夹'):
                cli.dashboard()
        self.api.start.assert_not_called()
        self.api.prepare.assert_not_called()
