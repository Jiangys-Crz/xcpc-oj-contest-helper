from pathlib import Path
from unittest.mock import Mock, patch

from vp_helper import cli, live
from vp_helper.models import Contest, Rules, VPError
from vp_helper.sessions import attempts, boundary, end_attempt
from vp_helper.storage import read_json, state_directory, write_json
from test_workflows import Isolated, URL


class ParallelContestTests(Isolated):
    def another_vp(self):
        url='https://qoj.ac/contest/124'
        self.api.prepare.side_effect=lambda: Contest('qoj','124',url,'another contest','another contest',{'A':'20'},Rules(18000))
        return cli.prepare_start(url)

    def test_two_running_vps_keep_independent_files_clocks_and_scores(self):
        old,first=cli.prepare_start(URL)
        source=Path(first['directory'])/'A.cpp'
        source.write_text('preserve first contest solution')
        before=old.read_bytes()
        self.clock=1200
        new,second=self.another_vp()
        self.assertNotEqual(old,new)
        self.assertNotEqual(first['directory'],second['directory'])
        self.assertNotEqual(first['tmux_session'],second['tmux_session'])
        self.assertEqual(old.read_bytes(),before)
        self.assertIsNone(boundary(old))
        self.assertEqual(source.read_text(),'preserve first contest solution')
        self.assertEqual(first['start_time'],1000)
        self.assertEqual(second['start_time'],1200)
        self.assertEqual([s['status'] for _,s in attempts()],['running','running'])

    def test_current_practice_does_not_block_vp_or_end_other_vp_by_directory(self):
        old,practice=cli.prepare_start(URL)
        practice['mode']='practice'
        write_json(old,practice)
        self.clock=1200
        new,vp=self.another_vp()
        with patch('vp_helper.sessions.Path.cwd',return_value=Path(practice['directory'])):
            with self.assertRaisesRegex(VPError,'轮次ID'):
                end_attempt()
        self.assertIsNone(boundary(new))
        self.assertEqual(read_json(new)['status'],'running')

    def test_end_prefers_the_shells_own_vp_over_global_current(self):
        old,first=cli.prepare_start(URL)
        self.clock=1200
        new,second=self.another_vp()
        nested=Path(first['directory'])/'solutions'
        nested.mkdir()
        self.clock=1300
        with patch('vp_helper.sessions.Path.cwd',return_value=nested):
            ended,state=end_attempt()
            self.assertEqual(ended,old)
            self.assertEqual(state['status'],'archived')
            end_attempt()
        self.assertEqual(boundary(old),1300)
        self.assertIsNone(boundary(new))
        self.assertEqual(read_json(new)['status'],'running')
        self.assertEqual(read_json(state_directory()/'current.json')['state_path'],str(new))

    def test_active_vp_does_not_block_live_login_and_independent_workspace(self):
        old,first=cli.prepare_start(URL)
        before=old.read_bytes()
        url='https://codeforces.com/contest/125'
        api=Mock(handle='tester')
        contest=Contest('cf','125',url,'Live fixture','Live fixture',{'A':'A'},Rules(7200),
                        original_start=1000,extra={'scoring':'ICPC','phase':'CODING'})
        api.prepare.return_value=(contest,{})
        with patch.object(live,'live_adapter',return_value=api), \
                patch.object(live,'desktop',return_value=self.root/'desktop'), \
                patch.object(live,'configure_submission',side_effect=lambda a,c,d:write_json(d/'contest.json',{'handle':'tester'})):
            path,state=live.prepare_live(url)
        self.assertNotEqual(path,old)
        self.assertEqual(state['mode'],'live')
        self.assertEqual(old.read_bytes(),before)
        self.assertIsNone(boundary(old))
        api.start.assert_not_called()

    def test_unbound_end_does_not_guess_when_current_is_bt_and_multiple_vps_run(self):
        old,first=cli.prepare_start(URL)
        self.clock=1200
        new,second=self.another_vp()
        practice={**first,'mode':'practice','directory':str(self.root/'independent-bt')}
        bt_path=state_directory()/'bt-qoj-fixture.json'
        write_json(bt_path,practice)
        write_json(state_directory()/'current.json',{'state_path':str(bt_path)})
        with self.assertRaisesRegex(VPError,'多场'):
            end_attempt()
        self.assertIsNone(boundary(old))
        self.assertIsNone(boundary(new))
        selected,_=end_attempt(first['attempt_id'])
        self.assertEqual(selected,old)
        self.assertIsNone(boundary(new))
