from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vp_helper.identity import dashboard_identity, participant_identity, persist_display_identity
from vp_helper.storage import config_directory, read_json, write_json


class DisplayIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config'),'XDG_STATE_HOME':str(self.root/'state')}))

    def test_team_title_preserves_hyphens_and_member_snapshot(self):
        participant={'type':'team','label':'Code-Zero-甲、乙','members':[{'realname':'甲'},{'realname':'乙'}]}
        self.assertEqual(participant_identity(participant),('Code-Zero','甲、乙'))
        participant.update(participantTitle='Original-Team',participantSubtitle='Old Name、Member')
        self.assertEqual(participant_identity(participant),('Original-Team','Old Name、Member'))

    def test_individual_uses_nickname_then_realname(self):
        participant={'type':'individual','label':'nickname-with-hyphens-姓名','members':[{'username':'nickname-with-hyphens','realname':'姓名'}]}
        self.assertEqual(participant_identity(participant),('nickname-with-hyphens','姓名'))

    def test_optional_cloud_profile_never_changes_a_native_judge_handle(self):
        account={'token':'fixture','serverUrl':'https://vp.cwnupaa.com','user':{'id':'u1','username':'昵称','realname':'姓名'}}
        write_json(config_directory()/'cloud-account.json',account)
        state={'handle':'NativeHandle'};original=deepcopy(state);path=self.root/'state/vp/test.json'
        self.assertEqual(dashboard_identity(state,path),('NativeHandle',''))
        write_json(path.with_suffix('.cloud-owner'),{'userId':'u1','serverUrl':account['serverUrl']})
        self.assertEqual(dashboard_identity(state,path),('昵称','姓名'))
        self.assertEqual(state,original)
        write_json(path.with_suffix('.cloud-owner'),{'userId':'another','serverUrl':account['serverUrl']})
        self.assertEqual(dashboard_identity(state,path),('NativeHandle',''))

    def test_absent_or_broken_optional_profile_keeps_native_handle(self):
        state={'handle':'NativeHandle'}
        self.assertEqual(dashboard_identity(state),('NativeHandle',''))
        write_json(config_directory()/'cloud-account.json',{})
        (config_directory()/'cloud-account.json').write_text('{broken')
        self.assertEqual(dashboard_identity(state),('NativeHandle',''))

    def test_identity_enrichment_preserves_fresh_submissions_and_cannot_rebind(self):
        context={'serverUrl':'https://vp.cwnupaa.com','userId':'u1','roomId':'r1','participantId':'p1','label':'Team-甲'}
        saved={'cloud':deepcopy(context),'submissions':[{'id':'new'}],'start_time':123,'handle':'native'}
        path=self.root/'state/vp/session.json';write_json(path,saved)
        stale={'cloud':{**context,'participantTitle':'Team','participantSubtitle':'甲'},'submissions':[]}
        persist_display_identity(path,stale)
        result=read_json(path)
        self.assertEqual(result['submissions'],[{'id':'new'}]);self.assertEqual(result['start_time'],123)
        self.assertEqual(result['cloud']['participantTitle'],'Team')
        stale['cloud']['participantId']='changed';stale['cloud']['participantTitle']='Other'
        persist_display_identity(path,stale)
        self.assertEqual(read_json(path),result)
