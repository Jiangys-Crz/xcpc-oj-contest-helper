from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import cli, vp
from vp_helper.gym import Gym
from vp_helper.models import Contest, Rules, VPError
from vp_helper.storage import read_json, write_json


class GymActivationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ,{
            'XDG_STATE_HOME':str(self.root/'state'),
            'XDG_CONFIG_HOME':str(self.root/'config')}))
        self.url='https://codeforces.com/gym/102181'
        self.server=datetime(2026,10,29,12,0,tzinfo=timezone.utc).timestamp()
        self.started=self.server-3600
        self.client=Gym(self.url,{'handle':'tester'})
        self.client.api=Mock(return_value={'rows':[]})
        self.client.web=Mock()
        self.client.web.get.return_value=Mock(
            text='<div id="header"><a href="/profile/tester">me</a></div>'
                 '<span class="countdown before-contest-102181-finish">04:00:00</span>',
            headers={'Date':'Thu, 29 Oct 2026 12:00:00 GMT'})
        self.client.start=Mock(side_effect=AssertionError('recovery must not register again'))
        self.stack.enter_context(patch.object(cli,'load_account',return_value={'handle':'tester'}))
        self.stack.enter_context(patch.object(cli,'adapter',return_value=self.client))
        self.publish=self.stack.enter_context(patch.object(vp,'_publish_started'))
        contest=Contest('gym','102181',self.url,'Gym fixture','Gym fixture',{'A':'A'},Rules(18000))
        self.path=self.root/'vp-gym-102181-fixture.json'
        self.state={'mode':'vp','status':'scheduled','url':self.url,'handle':'tester',
                    'contest':contest.to_dict(),'start_time':self.started,
                    'requested_start':self.started-33,'activation_target':self.started,
                    'activation_uncertain':True,'submissions':[]}
        write_json(self.path,self.state)

    def test_native_countdown_recovers_uncertain_vp_without_another_post(self):
        recovered=vp.activate(self.path,self.state)
        self.assertEqual(recovered['status'],'running')
        self.assertEqual(recovered['start_time'],self.started)
        self.assertEqual(recovered['requested_start'],self.started-33)
        self.assertFalse(recovered['activation_uncertain'])
        self.assertEqual(read_json(self.path),recovered)
        self.publish.assert_called_once_with(self.path,recovered,self.client)
        vp.activate(self.path,recovered)
        self.publish.assert_called_once()
        self.client.start.assert_not_called()
        self.client.web.post.assert_not_called()

    def test_different_native_start_remains_uncertain_without_another_post(self):
        self.state['activation_target']=self.started-120
        write_json(self.path,self.state)
        with self.assertRaisesRegex(VPError,'has not confirmed'):
            vp.activate(self.path,self.state)
        saved=read_json(self.path)
        self.assertEqual(saved['status'],'scheduled')
        self.assertEqual(saved['start_time'],self.started)
        self.assertTrue(saved['activation_uncertain'])
        self.publish.assert_not_called()
        self.client.start.assert_not_called()
        self.client.web.post.assert_not_called()
