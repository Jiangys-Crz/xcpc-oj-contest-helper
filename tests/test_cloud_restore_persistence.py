from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vp_helper.models import Contest,Rules
from vp_helper.sessions import save_state
from vp_helper.storage import read_json,write_json


class RestoredMetadataPersistenceTests(unittest.TestCase):
    def test_inflight_native_poll_cannot_erase_a_newly_imported_baseline(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_CONFIG_HOME':directory+'/config','XDG_STATE_HOME':directory+'/state'}):
            contest=Contest('qoj','123','https://qoj.ac/contest/123','Contest','Contest',{'A':'10'},Rules(3600))
            path=Path(directory)/'state/bt-qoj-fixture.json'
            stale={'mode':'practice','handle':'own-account','url':contest.url,'start_time':1000,'contest':contest.to_dict(),'submissions':[],'sync_revision':1}
            current=deepcopy(stale);current.update(cloud_restore={'historyId':'shared-history','roomId':'room'},restored_from='shared-history',restored_submissions=[{'id':'old','problem':'A','time':10,'verdict':'CORRECT','practice':False}],sync_revision=2,submissions=[{'id':'new','team':'own-account','problem':'A','time':30,'verdict':'CORRECT','practice':True}])
            write_json(path,current)
            with patch('vp_helper.cloud.queue_state'):
                save_state(path,stale)
            saved=read_json(path)
            for key in ('cloud_restore','restored_from','restored_submissions','submissions'):
                self.assertEqual(saved[key],current[key])
            self.assertEqual(saved['handle'],'own-account')
