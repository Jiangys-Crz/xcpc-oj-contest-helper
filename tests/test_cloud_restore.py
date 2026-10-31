from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from vp_helper import cli, cloud, cloud_restore
from vp_helper.models import Contest, Rules, VPError
from vp_helper.practice import PracticeDashboard
from vp_helper.storage import read_json, write_json


class CloudRestoreTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config'), 'XDG_STATE_HOME': str(self.root/'state')}))
        self.stack.enter_context(patch.object(cli, 'desktop', return_value=self.root/'contests'))
        self.stack.enter_context(patch.object(cli, 'check_terminal'))
        self.stack.enter_context(patch.object(cli.console, 'print'))
        self.stack.enter_context(patch.object(cli, 'bind_submit_context'))
        self.stack.enter_context(patch.object(cloud, '_start_worker'))
        self.url = 'https://qoj.ac/contest/3617'
        self.account = {'serverUrl': cloud.DEFAULT_SERVER, 'token': 'association-token', 'user': {'id': 'member', 'username': 'Member nickname', 'realname': 'Member name'}}
        write_json(cloud.configuration_path(), self.account)
        self.config = {'qoj': {'handle': 'MyOwnJudge', 'cookie': 'own-cookie'}}
        self.started = 1_700_000_000.0
        self.contest = Contest('qoj', '3617', self.url, 'Original contest', 'Original contest', {'A':'10','B':'11'}, Rules(3600), original_start=self.started, extra={'practice': True})
        self.snapshot = {'historyId': 'history-team', 'localId': 'vp-qoj-captain', 'isOwner': False, 'roomId': 'room', 'participantId': 'team', 'revision': 7,
                         'participantTitle': 'Team', 'participantSubtitle': 'Captain、Member', 'rank': '57',
                         'contest': {'url': self.url, 'name': self.contest.name, 'problems': ['A','B'], 'rules': {'duration': 3600}},
                         'submissions': [{'id':'101','problem':'A','submittedAt': self.iso(self.started+1000),'verdict':'CORRECT','practice':False}]}
        native = Mock(); native.handle = 'MyOwnJudge'; native.account = self.config['qoj']
        native.prepare.side_effect = lambda: Contest.from_dict(self.contest.to_dict())
        native.api = native
        self.native = native
        self.stack.enter_context(patch('vp_helper.practice.Practice', return_value=native))
        self.configure = self.stack.enter_context(patch.object(cli, 'configure_contest'))

    @staticmethod
    def iso(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def state(self, path, mode='practice'):
        directory = self.root/'contests'/self.contest.name
        directory.mkdir(parents=True, exist_ok=True)
        value = {'mode':mode, 'url':self.url, 'handle':'MyOwnJudge', 'start_time':self.started,
                 'contest':self.contest.to_dict(), 'directory':str(directory), 'status':'complete' if mode=='vp' else 'running',
                 'tmux_session':path.stem, 'submissions':[{'id':'202','team':'me','problem':'B','time':4000,'verdict':'CORRECT','practice':True}]}
        write_json(path,value)
        return value

    def test_teammate_restores_readonly_team_baseline_and_submits_with_own_judge_account(self):
        self.snapshot['cookie']='must-not-import'; self.snapshot['source']='must-not-import'
        path,state=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(state['mode'],'practice'); self.assertEqual(state['handle'],'MyOwnJudge')
        self.assertNotIn('cloud',state); self.assertEqual(state['cloud_restore']['historyId'],'history-team')
        self.assertFalse(state['restored_submissions'][0]['practice']); self.assertEqual(state['restored_submissions'][0]['time'],1000)
        self.assertNotIn('must-not-import',json.dumps(state)); self.assertEqual(state['handle'],'MyOwnJudge')
        self.assertEqual(self.configure.call_args.args[0].handle,'MyOwnJudge')
        self.assertEqual(self.configure.call_args.kwargs['overrides']['mode'],'practice')
        self.assertEqual(read_json(path)['handle'],'MyOwnJudge')

    def test_repeat_restore_keeps_workspace_files_and_fresh_personal_records(self):
        path=cli.state_directory()/'bt-qoj-3617-old.json'; previous=self.state(path)
        source=Path(previous['directory'])/'a.cpp'; source.write_text('keep my local program')
        restored,state=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(restored,path); self.assertEqual(source.read_text(),'keep my local program')
        self.assertEqual([r['id'] for r in state['submissions']],['202'])
        cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(len(list(cli.state_directory().glob('bt-*.json'))),1)
        self.assertEqual(source.read_text(),'keep my local program')

    def test_imported_team_records_are_never_uploaded_as_personal_bt_or_room_updates(self):
        path,state=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        state['submissions']=[*state['restored_submissions'],{'id':'202','team':'me','problem':'B','time':4000,'verdict':'CORRECT','practice':True}]
        payload=cloud.sync_payload(path,state)
        self.assertEqual(payload['mode'],'bt'); self.assertNotIn('roomId',payload); self.assertNotIn('participantId',payload)
        self.assertEqual([r['id'] for r in payload['submissions']],['202'])
        self.assertEqual(payload['cells']['A']['tries'],0); self.assertTrue(payload['cells']['B']['practiceSolved'])
        self.assertNotIn('association-token',json.dumps(payload)); self.assertNotIn('own-cookie',json.dumps(payload))

    def test_fresh_computer_restores_own_later_bt_and_reuses_safe_personal_cloud_local_id(self):
        self.snapshot['personalPractice']={'historyId':'personal-bt','localId':'bt-qoj-3617-own',
                                          'submissions':[{'id':'302','problem':'B','submittedAt':self.iso(self.started+4000),'verdict':'CORRECT','practice':True}]}
        path,state=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(path.stem,'bt-qoj-3617-own')
        self.assertEqual([r['id'] for r in state['submissions']],['302'])
        self.assertTrue(state['submissions'][0]['practice'])
        payload=cloud.sync_payload(path,state)
        self.assertEqual(payload['localId'],'bt-qoj-3617-own'); self.assertEqual([r['id'] for r in payload['submissions']],['302'])
        self.assertNotIn('roomId',payload)
        self.assertEqual(read_json(path.with_suffix('.cloud-owner')),{'userId':'member','serverUrl':cloud.DEFAULT_SERVER})

    def test_remote_personal_local_id_cannot_write_outside_state_directory(self):
        self.snapshot['personalPractice']={'localId':'../../outside','submissions':[]}
        path,_=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(path.parent,cli.state_directory());self.assertTrue(path.name.startswith('bt-qoj-3617-'))
        self.assertFalse((self.root/'outside').exists())

    def test_captain_merges_into_original_vp_without_resetting_attempt_or_completed_verdicts(self):
        path=cli.state_directory()/'vp-qoj-captain.json'; old=self.state(path,'vp')
        old['cloud']={'serverUrl':self.account['serverUrl'],'userId':'member','participantId':'team','roomId':'room'}
        old['last_rank']='57'; old['submissions'].append({'id':'101','team':'me','problem':'A','time':1000,'verdict':'CORRECT','practice':False})
        write_json(path,old); self.snapshot['isOwner']=True; self.snapshot['submissions'][0]['verdict']='PENDING'
        with patch.object(cli,'restore_contest',side_effect=lambda p,s,c:(p,s)):
            restored,state=cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(restored,path); self.assertEqual(state['mode'],'vp'); self.assertTrue(state['practice_view'])
        self.assertEqual(state['start_time'],old['start_time']); self.assertEqual(state['cloud'],old['cloud'])
        self.assertEqual(state['practice_rank'],'57'); self.assertEqual(state['handle'],'MyOwnJudge')
        self.assertEqual({r['id']:r['verdict'] for r in state['submissions']},{'101':'CORRECT','202':'CORRECT'})
        self.native.prepare.assert_not_called()

    def test_running_local_vp_and_mismatched_problem_list_are_not_overwritten(self):
        path=cli.state_directory()/'vp-qoj-current.json'; previous=self.state(path,'vp')
        previous['status']='running';previous['start_time']=9_000_000_000;write_json(path,previous)
        before=path.read_bytes()
        with self.assertRaisesRegex(VPError,'正在进行'):
            cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertEqual(path.read_bytes(),before)
        path.unlink();self.snapshot['contest']['problems']=['X']
        with self.assertRaisesRegex(VPError,'题目列表'):
            cloud_restore.restore_snapshot(self.snapshot,self.account,self.config)
        self.assertFalse(list(cli.state_directory().glob('bt-*.json')))

    def test_teammate_authorization_only_relaxes_for_ended_restore_not_native_start_or_submit(self):
        reply={'canSubmit':False,'canRestore':True,'room':{},'participant':{}}
        with patch.object(cloud,'_request',return_value=reply):
            with self.assertRaisesRegex(VPError,'仅队长'):
                cloud.authorize('room')
            result,_=cloud.authorize('room',allow_restore=True)
            self.assertTrue(result['canRestore'])
        with patch.object(cloud,'_request',return_value={**reply,'canRestore':False}):
            with self.assertRaisesRegex(VPError,'仅队长'):
                cloud.authorize('room',allow_restore=True)

    def test_room_command_after_end_never_registers_native_vp(self):
        returned=(cli.state_directory()/'bt-imported.json',{'mode':'practice'})
        with patch.object(cloud,'authorize',return_value=({'room':{'id':'room'},'participant':{},'canRestore':True},self.account)), \
             patch.object(cloud_restore,'restore_room',return_value=returned) as restore, \
             patch.object(cli,'open_state') as open_state, patch('vp_helper.vp.prepare') as prepare:
            cloud.start_room('https://vp.cwnupaa.com/vp/room')
        restore.assert_called_once_with('room',self.account);open_state.assert_called_once_with(*returned);prepare.assert_not_called()

    def test_native_command_restores_cloud_history_before_native_registration(self):
        returned=(cli.state_directory()/'bt-imported.json',{'mode':'practice'})
        with patch.object(cli,'load_account',return_value=self.config),patch.object(cloud_restore,'restore_native',return_value=returned) as restore, \
             patch.object(cli,'open_state') as open_state,patch.object(cli,'prepare_start') as prepare:
            cli.start(self.url)
        restore.assert_called_once();open_state.assert_called_once_with(*returned);prepare.assert_not_called()

    def test_restored_original_green_personal_practice_blue_and_no_timer(self):
        baseline={'id':'101','problem':'A','time':60,'verdict':'CORRECT','practice':False}
        personal={'id':'202','problem':'B','time':70,'verdict':'CORRECT','practice':True}
        records=cloud_restore.display_records({'restored_submissions':[baseline]},[personal,{**baseline,'verdict':'PENDING','practice':True}])
        self.assertEqual(next(r for r in records if r['id']=='101')['verdict'],'CORRECT')
        view=PracticeDashboard(self.contest,5000,records,restored=True,handle='MyOwnJudge')
        console=Console(file=io.StringIO(),width=180,color_system='truecolor')
        rendered=list(console.render(view.fixed(180)))
        colors=[s.style.bgcolor.get_truecolor() for s in rendered if s.style and s.style.bgcolor and s.style.bgcolor.type.name!='DEFAULT']
        self.assertIn((96,231,96),colors);self.assertIn((0,0,255),colors)
        self.assertNotIn(' / 01:00:00',''.join(s.text for s in rendered));self.assertTrue(view.practice)


if __name__=='__main__':
    unittest.main()
