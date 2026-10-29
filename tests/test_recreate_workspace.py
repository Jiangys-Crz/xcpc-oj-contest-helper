from pathlib import Path
import shutil
from unittest.mock import patch

from vp_helper import cli, practice
from vp_helper.models import VPError
from vp_helper.sessions import attempts
from vp_helper.storage import read_json, write_json
from test_workflows import Isolated, URL


class RecreateWorkspaceTests(Isolated):
    def test_missing_active_vp_folder_is_recreated_without_reset_or_new_start(self):
        path,state=cli.prepare_start(URL)
        state['submissions']=[{'id':'7','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]
        write_json(path,state)
        directory=Path(state['directory'])
        shutil.rmtree(directory)
        self.api.reset_mock()
        with patch.object(cli,'open_state') as opened,patch('vp_helper.scheduling.prompt_start') as prompt:
            cli.start(URL)
        current=opened.call_args.args[1]
        self.assertEqual(opened.call_args.args[0],path)
        self.assertEqual(current['start_time'],state['start_time'])
        self.assertEqual(current['submissions'],state['submissions'])
        self.assertTrue(directory.is_dir())
        self.assertEqual(read_json(directory/'contest.json')['vp_context']['state_path'],str(path))
        self.assertEqual(len(attempts()),1)
        self.api.start.assert_not_called()
        self.api.prepare.assert_not_called()
        prompt.assert_not_called()

    def test_missing_uncertain_vp_folder_does_not_resend_start_request(self):
        def timeout(*args,**kwargs):
            self.api.native_post_sent=True
            raise VPError('outcome unknown')
        self.api.start.side_effect=timeout
        path,state=cli.prepare_start(URL)
        shutil.rmtree(state['directory'])
        with patch.object(cli,'open_state') as opened:
            cli.start(URL)
        self.assertEqual(opened.call_args.args[0],path)
        self.assertTrue(opened.call_args.args[1]['activation_uncertain'])
        self.api.start.assert_called_once()

    def test_file_at_saved_directory_is_never_overwritten(self):
        path,state=cli.prepare_start(URL)
        folder=Path(state['directory'])
        shutil.rmtree(folder)
        folder.write_text('unrelated file')
        with patch.object(cli,'open_state'):
            with self.assertRaisesRegex(VPError,'文件占用'):
                cli.start(URL)
        self.assertEqual(folder.read_text(),'unrelated file')
        self.assertEqual(read_json(path)['directory'],str(folder))

    def test_ended_live_missing_directory_recovers_practice_and_frozen_rank(self):
        from test_session_reuse import SessionReuseTests
        url,path,state=SessionReuseTests.live_record(self)
        state.update(practice_rank='9',practice_view=True)
        state['submissions']=[{'id':'1','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]
        write_json(path,state)
        shutil.rmtree(state['directory'])
        self.clock=1200
        with patch.object(cli,'open_state') as opened:
            cli.start(url,live=True)
        restored=opened.call_args.args[1]
        self.assertEqual(opened.call_args.args[0],path)
        self.assertTrue(restored['practice_view'])
        self.assertEqual(restored['practice_rank'],'9')
        self.assertEqual(restored['submissions'],state['submissions'])
        self.assertTrue((Path(state['directory'])/'contest.json').is_file())
