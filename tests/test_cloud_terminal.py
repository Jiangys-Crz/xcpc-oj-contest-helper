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
        self.state['reference_rank']={'source':'original','contestUrl':contest.url,'rank':'27',
                                     'officialParticipants':100,'scoring':'ICPC','scoreKey':[-1,0]}
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
        self.assertEqual(payload['rankSource'],'original')
        self.assertNotIn('solveTime',payload['cells']['B'])
        self.assertEqual(payload['submissions'][0]['submittedAt'],'2023-11-14T22:13:30+00:00')
        self.assertEqual(payload['submissions'][0]['url'],'https://qoj.ac/submission/1')

    def test_vp_never_uploads_a_native_virtual_or_room_position_without_original_proof(self):
        self.state.pop('reference_rank')
        self.state['cloud']=self.context
        self.state['last_rank']='1'
        self.state['summary']={'rank':'2'}
        self.state['snapshot']={'own':{'rank':3}}
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual((payload['rank'],payload['rankSource']),('—','unavailable'))

    def test_changed_score_cannot_reuse_an_old_verified_original_rank(self):
        self.state['submissions'].append({'id':'4','team':'me','problem':'B','time':30,'verdict':'CORRECT'})
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual((payload['rank'],payload['rankSource']),('—','unavailable'))

    def test_legacy_own_only_statistics_are_not_sent_as_original_public_statistics(self):
        self.state.update(public_statistics={kind:{'A':1,'B':0} for kind in ('submitted','attempted','accepted')},
                          revealed_problems=['A'],statistics_at=1700000100)
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['statisticsSource'],'unavailable')
        self.assertNotIn('publicStatistics',payload)
        self.state['statistics_source']='original'
        self.state['public_statistics']['submitted']['A']=500
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['statisticsSource'],'original')
        self.assertEqual(payload['publicStatistics']['submitted']['A'],500)

    def test_live_uploads_platform_wide_statistics_instead_of_own_attempt_counts(self):
        self.state['mode']='live'
        self.state['snapshot']={'at':1700000100,'own':{'rank':17},'stats':{
            'submitted':{'A':500,'B':600},'attempted':{'A':400,'B':300},'accepted':{'A':200,'B':100}}}
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual((payload['rank'],payload['rankSource']),('17','native'))
        self.assertEqual(payload['statisticsSource'],'native')
        self.assertEqual(payload['publicStatistics'],self.state['snapshot']['stats'])

    def test_partial_ioi_scores_are_not_published_as_acceptance_counts(self):
        self.state['mode']='live'
        self.state['contest']['extra']={'scoring':'IOI'}
        self.state['snapshot']={'at':1700000100,'stats':{'scored':{'A':200},'attempted':{'A':300}}}
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['publicStatistics']['accepted'],{})
        self.assertEqual(payload['publicStatistics']['submitted'],{})
        self.assertEqual(payload['publicStatistics']['attempted']['A'],300)

    def test_standalone_bt_has_no_start_end_time_and_counts_practice_accepts(self):
        self.state['mode']='practice'
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['mode'],'bt')
        self.assertNotIn('startedAt',payload);self.assertNotIn('endedAt',payload)
        self.assertEqual(payload['solved'],2)
        self.assertTrue(all(s['practice'] for s in payload['submissions']))

    def test_payload_keeps_true_local_creation_for_each_mode_and_never_invents_it_from_contest_start(self):
        for mode in ('vp','live','practice'):
            self.state['mode']=mode
            self.state['created_at']=1700000050.25
            payload=cloud.sync_payload(self.path,self.state)
            self.assertEqual(payload['createdAt'],'2023-11-14T22:14:10.250000+00:00')
            self.assertNotEqual(payload['createdAt'],payload.get('startedAt'))
            if mode=='practice':
                self.assertNotIn('startedAt',payload);self.assertNotIn('endedAt',payload)
        for value in (None,True,False,-1,'1700000050',float('nan'),float('inf'),1e30):
            with self.subTest(value=value):
                self.state['created_at']=value
                self.assertNotIn('createdAt',cloud.sync_payload(self.path,self.state))
        self.state.pop('created_at')
        self.assertNotIn('createdAt',cloud.sync_payload(self.path,self.state))

    def test_payload_preserves_compiler_error_scoring_rule_for_frozen_board(self):
        self.state['submissions']=[
            {'id':'1','team':'me','problem':'A','time':10,'verdict':'COMPILER-ERROR'},
            {'id':'2','team':'me','problem':'A','time':70,'verdict':'CORRECT'}]
        for compile_penalty in (False,True):
            with self.subTest(compile_penalty=compile_penalty):
                self.state['contest']['rules']['compile_penalty']=compile_penalty
                payload=cloud.sync_payload(self.path,self.state)
                self.assertEqual(payload['contest']['rules'],{
                    'duration':3600,'penalty_minutes':20,'compile_penalty':compile_penalty,
                    'freeze_minutes':0,'scoring':'ICPC','score_policy':'best','problem_max_scores':{},
                    'contest_format':'ICPC','problem_reveal_count':None})
                self.assertEqual(payload['cells']['A']['tries'],2 if compile_penalty else 1)
                self.assertEqual(payload['penalty'],21 if compile_penalty else 1)

    def test_native_partial_points_are_not_accepted_and_cf_total_can_include_hacks(self):
        self.state['contest']['extra'].update(scoring='IOI', problem_max_scores={'A':100,'B':100})
        self.state['submissions']=[{'id':'1','team':'me','problem':'A','time':10,'verdict':'PARTIAL','score':None,'points':75}]
        self.state['snapshot']={'own':{'rank':2,'points':75,'penalty':'','problemResults':[{'points':75,'accepted':False}]}}
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['points'],75)
        self.assertFalse(payload['cells']['A']['solved'])
        self.assertEqual(payload['submissions'][0]['points'],75)
        self.assertEqual(payload['penalty'],0)
        self.state['contest']['extra']['scoring']='CF'
        self.state['snapshot']['own']['points']=-50
        self.assertEqual(cloud.sync_payload(self.path,self.state)['points'],-50)

    def test_native_clock_penalty_and_negative_historic_bt_records(self):
        self.state['snapshot']={'own':{'rank':7,'points':1,'penalty':'0:25','problemResults':[]}}
        self.assertEqual(cloud.sync_payload(self.path,self.state)['penalty'],25)
        self.state['mode']='practice'
        self.state['submissions']=[{'id':'99','team':'me','problem':'A','time':-100,'verdict':'CORRECT'}]
        payload=cloud.sync_payload(self.path,self.state)
        self.assertTrue(payload['cells']['A']['practiceSolved'])
        self.assertEqual(payload['cells']['A']['tries'],1)
        self.assertEqual(payload['penalty'],0)
        self.assertEqual(payload['submissions'][0]['submittedAt'],cloud._iso(self.state['start_time']-100))

    def test_resumed_bt_counts_all_attempts_without_altering_original_score(self):
        self.state['practice_view']=True
        self.state['submissions']=[{'id':str(i),'team':'me','problem':'A','time':10+i,'verdict':v}
                                  for i,v in enumerate(['COMPILER-ERROR','CORRECT','WRONG-ANSWER','CORRECT'])]
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['cells']['A']['tries'],4)
        self.assertEqual(payload['solved'],1)
        self.assertEqual(payload['penalty'],0)
        self.assertTrue(payload['cells']['A']['solved'])

    def test_unconfigured_local_sessions_do_not_start_network_or_write_outbox(self):
        cloud.configuration_path().unlink()
        cloud.queue_state(self.path,self.state)
        self.worker.assert_not_called()
        self.assertFalse(cloud._outbox().exists())

    def test_external_state_file_cannot_use_saved_cloud_account_or_queue_upload(self):
        external=self.root/'temporary-fixture.json'
        write_json(external,self.state)
        with patch.object(cloud,'configuration_path',side_effect=AssertionError('account must not be read')), \
                patch.object(cloud,'_request') as request:
            cloud.queue_state(external,self.state)
        self.worker.assert_not_called()
        request.assert_not_called()
        self.assertFalse(cloud._outbox().exists())
        self.assertFalse(external.with_suffix('.cloud-owner').exists())

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

    def test_room_payload_binds_the_accepted_identity_and_original_schedule(self):
        self.state['cloud']=self.context
        payload=cloud.sync_payload(self.path,self.state)
        self.assertEqual(payload['roomId'],'room-1')
        self.assertEqual(payload['participantId'],'part-1')
        self.assertEqual(payload['roomStartsAt'],self.context['startsAt'])
        # The native start can be calibrated independently of the room's schedule.
        self.assertNotEqual(payload['startedAt'],payload['roomStartsAt'])
        self.state.pop('cloud')
        standalone=cloud.sync_payload(self.path,self.state)
        for key in ('roomId','participantId','roomStartsAt'):self.assertNotIn(key,standalone)

    def test_withdrawn_room_does_not_block_unrelated_uploads_and_is_retained_for_rejoin(self):
        self.state['cloud']=self.context
        cloud.queue_state(self.path,self.state)
        other_path=self.path.with_name('vp-qoj-456-fixture.json')
        other=deepcopy(self.state);other.pop('cloud')
        write_json(other_path,other);cloud.queue_state(other_path,other)
        for status in (403,409):
            with self.subTest(status=status):
                # The successful item is requeued for each rejection type.
                for item in cloud._outbox().glob('*.json'):
                    if read_json(item)['payload'].get('roomId'):
                        withdrawn=item
                other['submissions'][0]['verdict']='PENDING' if status==403 else 'CORRECT'
                cloud.queue_state(other_path,other)
                def upload(method,endpoint,payload,**kwargs):
                    if payload.get('roomId'):
                        raise cloud.CloudRequestError('Registration withdrawn',status)
                    return {'id':'independent-history'}
                with patch.object(cloud,'_request',side_effect=upload) as request:cloud.flush_once()
                self.assertEqual(request.call_count,2)
                remaining=list(cloud._outbox().glob('*.json'))
                self.assertEqual(remaining,[withdrawn])
                self.assertEqual(read_json(withdrawn)['payload']['participantId'],'part-1')
                self.assertEqual(read_json(cloud.state_directory()/'cloud-sync-status.json')['error'],'Registration withdrawn')
        with patch.object(cloud,'_request',return_value={'id':'original-room-history'}):cloud.flush_once()
        self.assertFalse(list(cloud._outbox().glob('*.json')))

    def test_http_rejections_keep_status_and_redact_secrets(self):
        response=Mock(status_code=403)
        response.json.return_value={'error':'Revoked '+self.saved['token']+' '+self.saved['password']}
        with patch.object(cloud.requests,'request',return_value=response),self.assertRaises(cloud.CloudRequestError) as rejected:
            cloud._request('POST','/api/client/sync',{'roomId':'room-1'})
        self.assertEqual(rejected.exception.status_code,403)
        self.assertNotIn(self.saved['token'],str(rejected.exception))
        self.assertNotIn(self.saved['password'],str(rejected.exception))

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
        reply={'room':{'id':'room-1','name':'Team VP','contestUrl':self.state['url'],'startsAt':self.context['startsAt']},'participant':participant,'canSubmit':True,'serverNow':'2026-10-30T11:00:00Z'}
        prepared={**self.state,'cloud':self.context}
        with patch.object(cloud,'authorize',return_value=(reply,self.saved)) as auth, \
                patch.object(cli,'load_account',return_value={'qoj':{'handle':'fixture'}}), \
                patch.object(sessions,'latest_session',return_value=None), \
                patch.object(vp,'prepare',return_value=(self.path,prepared)) as prepare, \
                patch.object(cli,'open_state') as panel,patch('builtins.input',side_effect=AssertionError('No manual scheduling')):
            cloud.start_room('vp.cwnupaa.com/vp/room-1')
        auth.assert_called_once_with('room-1',fresh=True,allow_restore=True)
        self.assertEqual(prepare.call_args.args,('https://qoj.ac/contest/123',1793361600.0))
        self.assertEqual(prepare.call_args.kwargs['cloud']['label'],'队伍-甲、乙')
        panel.assert_called_once()

    def room_reply(self):
        return {'room':{'id':'room-1','name':'Team VP','contestUrl':self.state['url'],
                        'startsAt':self.context['startsAt'],'freezeMinutes':0,'scoringSystem':'ICPC'},
                'participant':{'id':'part-1','captainId':'user-1','label':'队伍-甲、乙','type':'team','members':[]},
                'canSubmit':True,'serverNow':'2026-10-30T11:00:00Z'}

    def test_same_identity_rejoin_resumes_original_session_without_another_native_vp(self):
        from vp_helper import cli,vp,sessions
        self.state['cloud']=self.context;write_json(self.path,self.state)
        folder=Path(self.state['directory']);folder.mkdir()
        source=folder/'a.cpp';source.write_text('int main() {}\n')
        with patch.object(cloud,'authorize',return_value=(self.room_reply(),self.saved)), \
                patch.object(cli,'load_account',return_value={'qoj':{'handle':self.state['handle']}}), \
                patch.object(sessions,'latest_session',return_value=(self.path,self.state)), \
                patch.object(vp,'prepare') as prepare,patch.object(cli,'bind_submit_context') as bind, \
                patch.object(cli,'reopen_saved') as resume:
            cloud.start_room('vp.cwnupaa.com/vp/room-1')
        prepare.assert_not_called();resume.assert_called_once();bind.assert_called_once()
        saved=read_json(self.path)
        self.assertEqual(saved['cloud']['participantId'],'part-1')
        self.assertEqual(saved['submissions'],self.state['submissions'])
        self.assertEqual(saved['directory'],self.state['directory'])
        self.assertEqual(source.read_text(),'int main() {}\n')

    def test_rejoin_never_rebinds_a_previous_vp_to_another_identity_or_changed_settings(self):
        from vp_helper import cli,vp,sessions
        self.state['cloud']=self.context;write_json(self.path,self.state)
        before=self.path.read_bytes()
        for change,message in (({'participant':{'id':'part-2'}},'身份'),
                               ({'room':{'startsAt':'2026-10-30T12:05:00Z'}},'时间'),
                               ({'room':{'freezeMinutes':30}},'封榜'),
                               ({'room':{'scoringSystem':'CCPC'}},'赛制')):
            reply=self.room_reply()
            for key,fields in change.items():reply[key].update(fields)
            with self.subTest(change=change),patch.object(cloud,'authorize',return_value=(reply,self.saved)), \
                    patch.object(cli,'load_account',return_value={'qoj':{'handle':self.state['handle']}}), \
                    patch.object(sessions,'latest_session',return_value=(self.path,self.state)), \
                    patch.object(vp,'prepare') as prepare,patch.object(cli,'reopen_saved') as resume, \
                    self.assertRaisesRegex(VPError,message):
                cloud.start_room('vp.cwnupaa.com/vp/room-1')
            prepare.assert_not_called();resume.assert_not_called()
            self.assertEqual(self.path.read_bytes(),before)

    def test_context_checks_accept_equivalent_timezone_but_reject_stale_room_configuration(self):
        self.state['cloud']=self.context
        equivalent=self.room_reply();equivalent['room']['startsAt']='2026-10-30T20:00:00+08:00'
        with patch.object(cloud,'authorize',return_value=(equivalent,self.saved)):
            cloud.ensure_access(self.state)
        reply=self.room_reply();reply['room']['startsAt']='2026-10-30T12:00:02Z'
        with patch.object(cloud,'authorize',return_value=(reply,self.saved)),self.assertRaisesRegex(VPError,'时间'):
            cloud.ensure_access(self.state)

    def test_revoked_registration_blocks_submission_and_pending_native_activation(self):
        from vp_helper import cli,vp
        self.state.update(cloud=self.context,status='scheduled',requested_start=self.state['start_time'])
        write_json(self.path,self.state);before=self.path.read_bytes()
        with patch.object(cloud,'authorize',side_effect=cloud.CloudRequestError('Registration withdrawn',403)), \
                patch.object(cli,'adapter') as adapter:
            with self.assertRaisesRegex(VPError,'withdrawn'):cloud.check_submission({'vp_context':{'state_path':str(self.path)}})
            with self.assertRaisesRegex(VPError,'withdrawn'):vp._activate(self.path)
        adapter.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)

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
        self.assertEqual(upload.call_args.args[2],{'roomId':'room-1','participantId':'part-1','roomStartsAt':self.context['startsAt'],
                                                'filename':'a.cpp','language':'C++','code':'int main() {}\n'})
