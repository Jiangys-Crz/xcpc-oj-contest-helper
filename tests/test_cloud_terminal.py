from contextlib import ExitStack
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import cloud, browser_bridge, printfile
from vp_helper.models import Contest, Rules, VPError
from vp_helper.storage import write_json, read_json


class CloudTerminalTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config'),'XDG_STATE_HOME':str(self.root/'state')}))
        self.worker=self.stack.enter_context(patch.object(cloud,'_start_worker'))
        self.kick_sync_original=cloud.kick_sync_process
        self.kick=self.stack.enter_context(patch.object(cloud,'kick_sync_process'))
        self.saved={'serverUrl':cloud.DEFAULT_SERVER,'account':'fixture','password':'private-password',
                    'token':'private-token','user':{'id':'user-1','username':'测试','realname':'姓名','useraccount':'fixture','numberid':'100','email':'fixture@example.invalid','role':'user'}}
        write_json(cloud.configuration_path(),self.saved)
        contest=Contest('qoj','123','https://qoj.ac/contest/123','Fixture','Fixture',{'A':'11','B':'12'},Rules(3600))
        self.state={'contest':contest.to_dict(),'url':contest.url,'mode':'vp','status':'running','start_time':1700000000,
                    'handle':'private-judge-handle','cookie':'never-upload-cookie','api_secret':'never-upload-api',
                    'directory':str(self.root/'workspace'),'last_rank':'27','submissions':[
                        {'id':'1','team':'me','problem':'A','time':10,'verdict':'CORRECT'},
                        {'id':'2','team':'me','problem':'B','time':20,'verdict':'WRONG-ANSWER'},
                        {'id':'3','team':'me','problem':'B','time':3700,'verdict':'CORRECT','practice':True}]}
        self.path=self.root/'state/vp/vp-qoj-123-fixture.json';write_json(self.path,self.state)
        self.context={'serverUrl':cloud.DEFAULT_SERVER,'roomId':'room-1','roomName':'Team VP','userId':'user-1',
                      'participantId':'part-1','captainId':'user-1','label':'队伍-甲、乙','type':'team','startsAt':'2026-10-30T12:00:00Z'}

    def test_one_shot_sync_has_no_secret_arguments_or_environment_and_no_window(self):
        # Access the real implementation saved by the context manager patch.
        original = self.kick_sync_original
        for platform in ('darwin','win32'):
            with patch('sys.platform',platform),patch.object(cloud.subprocess,'Popen') as process:
                original()
            args=process.call_args.args[0]; options=process.call_args.kwargs
            self.assertEqual(args,[cloud.sys.executable,'-I','-m','vp_helper.cloud','_flush'])
            self.assertNotIn('env',options)
            for stream in ('stdin','stdout','stderr'):self.assertEqual(options[stream],cloud.subprocess.DEVNULL)
            self.assertEqual(options.get('creationflags'),0x08000200 if platform=='win32' else None)

    def test_one_shot_drain_stops_on_deadline_and_failure_retains_outbox(self):
        cloud.queue_state(self.path,self.state)
        with patch.object(cloud,'_request') as request:
            cloud.flush_once(deadline=0)
        request.assert_not_called()
        self.assertTrue(list(cloud._outbox().glob('*.json')))
        with patch.object(cloud,'flush_once') as flush:
            cloud.drain_pending()
        self.assertIn('deadline',flush.call_args.kwargs)

    def test_login_persists_all_identity_fields_privately_and_failure_keeps_original(self):
        reply={'user':self.saved['user'],'token':'fresh-token','expiresAt':'2026-12-01T00:00:00Z'}
        with patch.object(cloud,'_request',return_value=reply) as call:
            cloud.login('fixture','new-password',interactive=False)
        self.assertEqual(read_json(cloud.configuration_path())['user'],self.saved['user'])
        self.assertEqual(cloud.configuration_path().stat().st_mode&0o777,0o600)
        self.assertEqual(call.call_args.args[1],'/api/auth/login')
        before=cloud.configuration_path().read_bytes()
        with patch.object(cloud,'_request',side_effect=VPError('bad password')),self.assertRaises(VPError):
            cloud.login('other','wrong-password',interactive=False)
        self.assertEqual(cloud.configuration_path().read_bytes(),before)

    def test_payload_is_allowlisted_and_keeps_green_and_blue_distinct(self):
        self.state['contest']['extra']['secret']='never-upload-extra'
        payload=cloud.sync_payload(self.path,self.state)
        encoded=json.dumps(payload)
        for secret in ('never-upload','private-judge-handle',str(self.root),'private-password','private-token'):
            self.assertNotIn(secret,encoded)
        self.assertTrue(payload['cells']['A']['solved'])
        self.assertFalse(payload['cells']['B']['solved'])
        self.assertTrue(payload['cells']['B']['practiceSolved'])
        self.assertEqual(payload['rank'],'27');self.assertEqual(payload['solved'],1)
        self.assertNotIn('solveTime',payload['cells']['B'])
        self.assertEqual(payload['submissions'][0]['submittedAt'],'2023-11-14T22:13:30+00:00')
        self.assertEqual(payload['submissions'][0]['url'],'https://qoj.ac/submission/1')

    def test_standalone_bt_has_no_start_end_time_and_counts_practice_accepts(self):
        self.state['mode']='practice'
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['mode'],'bt')
        self.assertNotIn('startedAt',payload);self.assertNotIn('endedAt',payload)
        self.assertEqual(payload['solved'],2)
        self.assertTrue(all(s['practice'] for s in payload['submissions']))

    def test_payload_preserves_compiler_error_scoring_rule_for_frozen_board(self):
        self.state['submissions']=[
            {'id':'1','team':'me','problem':'A','time':10,'verdict':'COMPILER-ERROR'},
            {'id':'2','team':'me','problem':'A','time':70,'verdict':'CORRECT'}]
        for compile_penalty in (False,True):
            with self.subTest(compile_penalty=compile_penalty):
                self.state['contest']['rules']['compile_penalty']=compile_penalty
                payload=cloud.sync_payload(self.path,self.state)
                self.assertEqual(payload['contest']['rules'],{
                    'duration':3600,'penalty_minutes':20,'compile_penalty':compile_penalty})
                self.assertEqual(payload['cells']['A']['tries'],2 if compile_penalty else 1)
                self.assertEqual(payload['penalty'],21 if compile_penalty else 1)

    def test_unconfigured_local_sessions_do_not_start_network_or_write_outbox(self):
        cloud.configuration_path().unlink()
        cloud.queue_state(self.path,self.state)
        self.worker.assert_not_called()
        self.assertFalse(cloud._outbox().exists())

    def test_corrupt_optional_cloud_config_does_not_block_local_contests(self):
        cloud.configuration_path().write_text('{broken')
        self.assertIsNone(cloud.room_url('https://codeforces.com/contest/2181'))
        cloud.queue_state(self.path,self.state)
        self.worker.assert_not_called()

    def test_account_switch_never_reassigns_old_history_or_outbox(self):
        cloud.queue_state(self.path,self.state)
        items=list(cloud._outbox().glob('*.json')); self.assertEqual(len(items),1)
        owner=read_json(self.path.with_suffix('.cloud-owner'))
        switched={**self.saved,'user':{**self.saved['user'],'id':'user-2'}}
        write_json(cloud.configuration_path(),switched)
        changed=deepcopy(self.state);changed['last_rank']='9'
        cloud.queue_state(self.path,changed)
        self.assertEqual(read_json(self.path.with_suffix('.cloud-owner')),owner)
        with patch.object(cloud,'_request') as request:
            cloud.flush_once()
        request.assert_not_called()
        self.assertEqual(len(list(cloud._outbox().glob('*.json'))),1)

    def test_outbox_keeps_newer_result_when_request_finishes_out_of_order(self):
        cloud.queue_state(self.path,self.state)
        def arrived(*args,**kwargs):
            changed=deepcopy(self.state);changed['submissions'][1]['verdict']='CORRECT'
            cloud.queue_state(self.path,changed)
            return {'id':'history-1'}
        with patch.object(cloud,'_request',side_effect=arrived):cloud.flush_once()
        item=next(cloud._outbox().glob('*.json'))
        self.assertEqual(read_json(item)['payload']['submissions'][1]['verdict'],'CORRECT')
        with patch.object(cloud,'_request',return_value={'id':'history-1'}):cloud.flush_once()
        self.assertFalse(list(cloud._outbox().glob('*.json')))

    def test_network_failure_retains_outbox_and_error_without_credentials(self):
        cloud.queue_state(self.path,self.state)
        with patch.object(cloud,'_request',side_effect=VPError('offline')):cloud.flush_once()
        self.assertTrue(list(cloud._outbox().glob('*.json')))
        self.assertEqual(read_json(cloud.state_directory()/'cloud-sync-status.json')['error'],'offline')

    def test_member_cannot_authorize_local_vp_and_account_change_cannot_submit(self):
        with patch.object(cloud,'_request',return_value={'canSubmit':False}):
            with self.assertRaisesRegex(VPError,'队长'):cloud.authorize('room-1')
        self.state['cloud']={**self.context,'userId':'another-user'}
        with patch.object(cloud,'authorize') as auth,self.assertRaisesRegex(VPError,'账号'):
            cloud.ensure_access(self.state)
        auth.assert_not_called()

    def test_cloud_room_uses_online_schedule_without_prompting_start_time(self):
        from vp_helper import cli, vp, sessions
        participant={'id':'part-1','captainId':'user-1','label':'队伍-甲、乙','type':'team','members':[]}
        reply={'room':{'id':'room-1','name':'Team VP','contestUrl':self.state['url'],'startsAt':self.context['startsAt']},'participant':participant,'canSubmit':True}
        prepared={**self.state,'cloud':self.context}
        with patch.object(cloud,'authorize',return_value=(reply,self.saved)) as auth, \
                patch.object(cli,'load_account',return_value={'qoj':{'handle':'fixture'}}), \
                patch.object(sessions,'latest_session',return_value=None), \
                patch.object(vp,'prepare',return_value=(self.path,prepared)) as prepare, \
                patch.object(cli,'open_state') as panel,patch('builtins.input',side_effect=AssertionError('No manual scheduling')):
            cloud.start_room('vp.cwnupaa.com/vp/room-1')
        auth.assert_called_once_with('room-1',fresh=True)
        self.assertEqual(prepare.call_args.args,('https://qoj.ac/contest/123',1793361600.0))
        self.assertEqual(prepare.call_args.kwargs['cloud']['label'],'队伍-甲、乙')
        panel.assert_called_once()

    def test_transport_requires_https_and_never_follows_redirects(self):
        for url in ('http://example.com','https://user:pass@vp.cwnupaa.com','https://vp.cwnupaa.com/foreign','https://vp.cwnupaa.com?x=1'):
            with self.assertRaises(VPError):cloud.server_url(url)
        self.assertEqual(cloud.server_url('http://127.0.0.1:3100'),'http://127.0.0.1:3100')
        response=Mock(status_code=200);response.json.return_value={'data':{'id':'ok'}}
        with patch.object(cloud.requests,'request',return_value=response) as request:
            cloud._request('GET','/api/auth/me')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_native_cloud_message_returns_identity_without_password_or_token(self):
        with patch.object(cloud,'login') as login:
            result=browser_bridge.cloud_message({'type':'cloud-login','account':'fixture','password':'supplied-password'})
        login.assert_called_once_with('fixture','supplied-password',interactive=False)
        encoded=json.dumps(result)
        self.assertNotIn('private-password',encoded);self.assertNotIn('private-token',encoded)
        self.assertEqual(result['user']['id'],'user-1')

    def test_print_cancellation_sends_nothing_and_confirmation_posts_only_code(self):
        folder=self.root/'workspace';folder.mkdir();source=folder/'a.cpp';source.write_text('int main() {}\n')
        self.state['cloud']=self.context;write_json(self.path,self.state)
        config={'vp_context':{'state_path':str(self.path)}}
        with patch('sys.argv',['printfile',str(source)]),patch.object(printfile,'submission_configuration',return_value=(folder/'contest.json',config)), \
                patch.object(cloud,'ensure_access'),patch.object(cloud,'_request',return_value={'id':'job-1'}) as upload, \
                patch('sys.stdout',new=io.StringIO()) as output,patch('builtins.input',return_value='n'):
            printfile.main()
            self.assertIn('Virtual print information:',output.getvalue())
            upload.assert_not_called()
        with patch('sys.argv',['printfile',str(source)]),patch.object(printfile,'submission_configuration',return_value=(folder/'contest.json',config)), \
                patch.object(cloud,'ensure_access'),patch.object(cloud,'_request',return_value={'id':'job-1'}) as upload, \
                patch('sys.stdout',new=io.StringIO()),patch('builtins.input',return_value='y'):
            printfile.main()
        self.assertEqual(upload.call_args.args[2],{'roomId':'room-1','filename':'a.cpp','language':'C++','code':'int main() {}\n'})
