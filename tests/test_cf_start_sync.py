from concurrent.futures import Future
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from vp_helper import cli
from vp_helper.gym import Gym
from vp_helper.models import Contest, Reference, Rules, Submission, VPError
from vp_helper.receipts import record_receipt, ReceiptReader
from vp_helper.sessions import reconcile_native_start, save_state
from vp_helper.storage import read_history, read_json, update_history, write_json
from test_workflows import Isolated


class ImmediatePool:
    def __init__(self,**kwargs): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def submit(self,fn,*args):
        result=Future()
        result.set_result(fn(*args))
        return result


class CFStartSyncTests(Isolated):
    def saved_cf(self):
        path,state=cli.prepare_start('https://qoj.ac/contest/123')
        contest=Contest('cf','2181','https://codeforces.com/contest/2181','CF fixture','CF fixture',{'A':'A'},Rules(18000))
        state.update(contest=contest.to_dict(),url=contest.url,start_time=1199,
                     submissions=[asdict(Submission('100','me','A',39,'PENDING'))])
        write_json(path,state)
        config={'platform':'gym','base_url':contest.url,'contest_id':2181,'handle':'tester',
                'vp_context':{'state_path':str(path),'url':contest.url,'handle':'tester','start_time':1199}}
        write_json(Path(state['directory'])/'contest.json',config)
        record_receipt(config,'100','A',1238)
        return path,state,contest,config

    def client(self,contest):
        client=Gym(contest.url,{'handle':'tester'})
        client.start_time=1199
        # Equivalent to the reported native record, using synthetic account/ID.
        raw={'id':100,'contestId':2181,'creationTimeSeconds':1238,'relativeTimeSeconds':38,
             'verdict':'WRONG_ANSWER','problem':{'index':'A'},
             'author':{'participantType':'VIRTUAL','startTimeSeconds':1200,'members':[{'handle':'tester'}]}}
        client.api=Mock(return_value=[raw])
        client.web=Mock()
        client.reference=Mock(return_value=Reference())
        return client,raw

    def test_api_result_replaces_pending_and_reconciles_start_without_losing_context(self):
        path,state,contest,config=self.saved_cf()
        client,raw=self.client(contest)
        old=deepcopy(state)
        stamp=lambda at: datetime.fromtimestamp(at,timezone.utc).isoformat()
        update_history(Path(state['history_path']),{'start_time':stamp(1199),'url':contest.url,'contest':contest.name})
        self.clock=1300
        view=Mock()
        view.__enter__=Mock(return_value=view)
        view.__exit__=Mock(return_value=False)
        view.input.side_effect=[True,True,False]
        with patch.object(cli,'adapter',return_value=client),patch.object(cli,'TerminalDashboard',return_value=view), \
             patch.object(cli,'ThreadPoolExecutor',ImmediatePool),patch('time.sleep'), \
             patch('time.monotonic',side_effect=iter(range(1,30))):
            cli.monitor(path)
        saved=read_json(path)
        self.assertEqual(saved['start_time'],1200)
        self.assertEqual(saved['submissions'][0]['verdict'],'WRONG-ANSWER')
        self.assertEqual(saved['submissions'][0]['time'],38)
        self.assertEqual(view.update.call_args.args[0].elapsed,100)
        self.assertEqual(view.update.call_args.args[0].me.name,'tester')
        self.assertEqual(read_json(Path(state['directory'])/'contest.json')['vp_context']['start_time'],1200)
        self.assertEqual(read_json(path.with_suffix('.receipts')/'100.json')['time'],38)
        client.web.post.assert_not_called()
        rows=read_history(Path(state['history_path']))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['start_time'],stamp(1200))
        # A slow history writer cannot roll the native start back.
        old['last_sync_time']=9999999999  # Wall-clock skew must not win over a newer poll.
        save_state(path,old)
        self.assertEqual(read_json(path)['start_time'],1200)
        self.assertEqual(read_json(path)['submissions'][0]['time'],38)
        self.assertEqual(read_json(path)['submissions'][0]['verdict'],'WRONG-ANSWER')
        # A submit already in flight can still notify using its old context.
        self.assertTrue(record_receipt(config,'101','A',1245))
        self.assertEqual(read_json(path.with_suffix('.receipts')/'101.json')['time'],45)

    def test_different_account_contest_or_participation_cannot_reconcile(self):
        path,state,contest,_=self.saved_cf()
        for change in ('account','contest','old-vp'):
            client,raw=self.client(contest)
            if change=='account': raw['author']['members']=[{'handle':'other'}]
            elif change=='contest': raw['contestId']=1234
            else: raw['author']['startTimeSeconds']=1000
            self.assertEqual(client.submissions(contest),[])
            self.assertFalse(hasattr(client,'observed_start_time'))
        with self.assertRaises(VPError): reconcile_native_start(path,state,1500)
        self.assertEqual(read_json(path)['start_time'],1199)

    def test_ambiguous_nearby_virtual_participations_are_not_combined(self):
        _,_,contest,_=self.saved_cf()
        client,raw=self.client(contest)
        other=deepcopy(raw)
        other['id']=101
        other['author']['startTimeSeconds']=1210
        client.api.return_value=[raw,other]
        with self.assertRaises(VPError): client.submissions(contest)

    def test_unknown_registration_outcome_waits_for_the_actual_rounded_reservation(self):
        url='https://codeforces.com/contest/2181'
        self.api.prepare.side_effect=lambda: Contest('cf','2181',url,'Rounded CF','Rounded CF',{'A':'A'},Rules(18000))
        self.api.account['submit_config_format']='contest.json'
        self.api.web.session.headers={'User-Agent':'fixture'}
        self.api.registration_start=1200
        def unknown(*args,**kwargs):
            self.api.before_native_post(self.clock)
            self.api.native_post_sent=True
            raise VPError('response lost')
        self.api.start.side_effect=unknown
        path,state=cli.prepare_start(url,scheduled_start=1188)
        self.assertTrue(state['activation_uncertain'])
        self.assertEqual(state['requested_start'],1188)
        self.assertEqual(state['start_time'],1200)
        self.assertEqual(state['activation_target'],1200)
        self.api.start.assert_called_once()

    def test_receipt_practice_boundary_follows_corrected_native_start(self):
        path,state,_,config=self.saved_cf()
        state['contest']['rules']['duration']=100
        write_json(path,state)
        record_receipt(config,'101','A',1299)
        self.assertTrue(read_json(path.with_suffix('.receipts')/'101.json')['practice'])
        reconcile_native_start(path,state,1200)
        receipt=read_json(path.with_suffix('.receipts')/'101.json')
        self.assertEqual(receipt['time'],99)
        self.assertFalse(receipt['practice'])
