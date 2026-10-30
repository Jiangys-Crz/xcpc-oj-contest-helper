"""Regression checks for accidental test-history uploads to a real account."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from isolation import isolate_local_state
from vp_helper import cloud
from vp_helper.models import Contest, Rules
from vp_helper.storage import write_json


def synthetic_state():
    contest=Contest('qoj','123','https://qoj.ac/contest/123','Fixture','Fixture',{'A':'1'},Rules(3600))
    return {'mode':'vp','url':contest.url,'contest':contest.to_dict(),'start_time':1000,
            'status':'running','submissions':[]}


class TestStateIsolation(unittest.TestCase):
    def test_lifecycle_fixtures_cannot_read_outer_user_cloud_account(self):
        from test_codeforces_live import IsolatedState as CFState
        from test_hydro_live import IsolatedState as HydroState
        from test_nowcoder_live import IsolatedState as NowcoderState
        from test_resume import ResumeTests
        from test_workflows import Isolated as WorkflowState
        for fixture_type in (WorkflowState,CFState,HydroState,NowcoderState,ResumeTests):
            with self.subTest(fixture=fixture_type.__module__), tempfile.TemporaryDirectory() as temporary, ExitStack() as outer:
                directory=Path(temporary)
                outer.enter_context(patch.dict(os.environ,{
                    'XDG_CONFIG_HOME':str(directory/'outer-config'),
                    'XDG_STATE_HOME':str(directory/'outer-state'),
                }))
                account=cloud.configuration_path()
                write_json(account,{'token':'synthetic-outer-token','serverUrl':cloud.DEFAULT_SERVER,'user':{'id':'synthetic-outer-user'}})
                before=account.read_bytes()
                request=outer.enter_context(patch.object(cloud.requests,'request',side_effect=AssertionError('network forbidden')))
                fixture=fixture_type()
                try:
                    fixture.setUp()
                    self.assertNotEqual(cloud.configuration_path(),account)
                    self.assertTrue(cloud.configuration_path().is_relative_to(fixture.root))
                    path=cloud.state_directory()/'vp-qoj-123-fixture.json'
                    state=synthetic_state()
                    write_json(path,state)
                    cloud.queue_state(path,state)
                    cloud._start_worker.assert_not_called()
                    cloud.kick_sync_process.assert_not_called()
                    self.assertFalse(cloud._outbox().exists())
                finally:
                    fixture.doCleanups()
                request.assert_not_called()
                self.assertEqual(account.read_bytes(),before)
                self.assertFalse((directory/'outer-state').exists())

    def test_local_fixture_suppresses_workers_even_with_explicit_synthetic_login(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root=Path(temporary)
            isolate_local_state(stack,root)
            write_json(cloud.configuration_path(),{'token':'synthetic-token','serverUrl':cloud.DEFAULT_SERVER,'user':{'id':'fixture'}})
            path=cloud.state_directory()/'vp-qoj-123-fixture.json'
            state=synthetic_state()
            write_json(path,state)
            cloud.queue_state(path,state)
            self.assertEqual(len(list(cloud._outbox().glob('*.json'))),1)
            cloud._start_worker.assert_called_once()
            with self.assertRaisesRegex(AssertionError,'Local test attempted cloud I/O'):
                cloud.flush_once()


if __name__=='__main__':
    unittest.main()
