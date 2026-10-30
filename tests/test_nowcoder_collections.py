"""Native zero-length Nowcoder training collections are BT-only workspaces."""
from contextlib import ExitStack
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper.models import Contest, Rules, VPError
from vp_helper.nowcoder import NowcoderLive, ORIGIN, problem_config, practice_collection
from vp_helper.practice import Practice, PracticeDashboard
from vp_helper.storage import write_json
from vp_helper import nowcoder_submit


FIXTURE = json.loads((Path(__file__).parent/'fixtures/nowcoder_996.json').read_text())
URL = ORIGIN + '/acm/contest/996'


def own_page():
    fields = FIXTURE['problemConfig']
    return ('<script>window.isLogin=true;window.globalInfo={ownerId:"42",ownerName:"fixture"};</script>'
            '<div class="acm-header"><a href="/acm/contest/profile/42">fixture</a></div>'
            '<script>window.pageInfo = {\n' + ',\n'.join(key+": '"+value+"'" for key,value in fields.items()) + '\n};</script>')


class CollectionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.api = NowcoderLive(URL, {'uid': 42, 'handle': '42', 'cookie_source': 'manual'})
        self.api._identified = True
        self.contest = Contest('nowcoder', '996', URL, 'fixture', 'fixture', {}, Rules(1))

    def test_actual_996_schedule_problem_ids_and_page_fields_prepare_as_untimed_bt(self):
        self.api.details = Mock(return_value=deepcopy(FIXTURE['info']))
        self.api.get_json = Mock(return_value=deepcopy(FIXTURE['problems']))
        practice = Practice.__new__(Practice)
        practice.platform, practice.id, practice.url = 'nowcoder', '996', URL
        practice.api, practice.account, practice.handle = self.api, {'uid':42, '_login_verified':True}, '42'
        contest = practice.prepare()
        self.assertEqual(contest.name, '0x01 基本算法-位运算')
        self.assertEqual(contest.problems, {'A':'50906','B':'50907','C':'50908','D':'50909','E':'17857'})
        self.assertTrue(contest.extra['practice_set']); self.assertTrue(contest.extra['practice'])
        self.assertEqual(contest.extra['phase'], 'FINISHED'); self.assertIsNone(contest.extra['end_time'])
        self.assertEqual(problem_config(own_page()), FIXTURE['problemConfig'])
        self.assertEqual(contest.original_start, FIXTURE['info']['startTime']/1000)

    def test_problem_set_is_rejected_in_live_even_when_not_registered(self):
        with self.assertRaisesRegex(VPError, 'bt only'):
            self.api.apply_metadata(self.contest, deepcopy(FIXTURE['info']))
        self.api.identity = Mock(); self.api.details = Mock(return_value=deepcopy(FIXTURE['info']))
        self.api.web = Mock()
        with self.assertRaisesRegex(VPError, 'bt only'):
            self.api.prepare()
        self.api.web.session.post.assert_not_called()

    def test_only_explicit_native_collections_can_omit_an_end_time(self):
        data = deepcopy(FIXTURE['info']); data.pop('endTime')
        self.api.apply_metadata(self.contest, data, require_registration=False)
        self.assertTrue(self.contest.extra['practice_set'])
        data.pop('startTime')
        self.api.apply_metadata(self.contest, data, require_registration=False)
        self.assertEqual(self.contest.original_start, 0)
        for mutation in ({'category': 1}, {'isFinished': False}, {'endTime': False}, {'startTime': float('nan')}):
            invalid = {**data, **mutation}
            self.assertFalse(practice_collection(invalid))
            with self.assertRaisesRegex(VPError, 'schedule'):
                self.api.apply_metadata(self.contest, invalid, require_registration=False)

    def test_existing_web_attempts_and_acceptance_are_read_without_practice_timing_or_points(self):
        self.api.apply_metadata(self.contest, deepcopy(FIXTURE['info']), require_registration=False)
        self.api.get_json = Mock(return_value=deepcopy(FIXTURE['problems']))
        self.api.problem_list(self.contest)
        start = self.contest.original_start
        native_records = [
            {'submissionId': 77, 'userId':42, 'isTeam':False, 'problemId':50906, 'submitTime':(start+30)*1000,'statusMessage':'答案错误','score':0},
            {'submissionId': 78, 'userId':42, 'isTeam':False, 'problemId':50906, 'submitTime':(start+50)*1000,'statusMessage':'答案正确','score':100},
            {'submissionId': 79, 'userId':42, 'isTeam':False, 'problemId':17857, 'submitTime':(start+60)*1000,'statusMessage':'答案错误','score':0},
        ]
        native = deepcopy(FIXTURE['anonymousStatus'])
        native['basicInfo'].update(basicUid=42, pageCount=1, statusCount=3)
        native['data'] = native_records
        self.api.get_json = Mock(return_value=native)
        records = self.api.submissions(self.contest)
        self.assertEqual([(e['problem'], e['verdict'], e['practice']) for e in records],
                         [('A','WRONG-ANSWER',True), ('A','CORRECT',True), ('E','WRONG-ANSWER',True)])
        self.assertTrue(self.api.submissions_complete)
        view = PracticeDashboard(self.contest, 100, records, handle='fixture', start_time=start)
        self.assertEqual((view.me.cells['A'].tries, view.me.cells['A'].solved), (2, True))
        self.assertEqual((view.me.cells['E'].tries, view.me.cells['E'].solved), (1, False))
        self.assertIsNone(view.me.points)
        self.assertEqual(start + records[0]['time'], start+30)

    def test_anonymous_empty_native_history_is_not_accepted_as_a_users_history(self):
        self.api.apply_metadata(self.contest, deepcopy(FIXTURE['info']), require_registration=False)
        self.api.get_json = Mock(return_value=deepcopy(FIXTURE['anonymousStatus']))
        self.api.web = Mock()
        with self.assertRaisesRegex(VPError, 'private view'):
            self.api.submissions(self.contest)
        self.assertFalse(self.api.submissions_complete)


class CollectionSubmitTests(unittest.TestCase):
    def test_existing_996_submit_protocol_requires_confirmation_and_reuses_standard_endpoint(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME':str(root/'config'), 'XDG_STATE_HOME':str(root/'state')}))
            source = root/'A.cpp'; source.write_text('int main(){}')
            state_path = root/'state/vp/collection.json'; state_path.parent.mkdir(parents=True)
            api = NowcoderLive(URL, {'uid':42, 'handle':'42', 'cookie_source':'manual'})
            api._identified = True; api.identity = Mock(); api.details = Mock(return_value=deepcopy(FIXTURE['info']))
            contest = Contest('nowcoder','996',URL,'collection','collection',{'A':'50906'},Rules(1))
            api.apply_metadata(contest, deepcopy(FIXTURE['info']), require_registration=False)
            state = {'mode':'practice','url':URL,'handle':'42','start_time':contest.original_start,'contest':contest.to_dict()}
            write_json(state_path,state)
            config = {'platform':'nowcoder','mode':'practice','base_url':URL,'contest_id':'996','uid':42,'handle':'42',
                      'cookie_source':'manual','problem_ids':{'A':'50906'},'vp_context':{'state_path':str(state_path),'url':URL,'handle':'42','start_time':state['start_time']}}
            api.get_json = Mock(return_value=deepcopy(FIXTURE['problems']))
            api.web = Mock(); api.web.get.return_value = Mock(url=URL+'/A',text=own_page())
            api.web.session.post.return_value = Mock(status_code=200,json=Mock(return_value={'code':0,'data':88}))
            stack.enter_context(patch('vp_helper.nowcoder_submit.NowcoderLive',return_value=api))
            refresh = stack.enter_context(patch('vp_helper.nowcoder_submit.refresh_submission_login'))
            stack.enter_context(patch('vp_helper.nowcoder_submit.submission_configuration',return_value=(root/'contest.json',config)))
            receipt = stack.enter_context(patch('vp_helper.nowcoder_submit.record_receipt'))
            with patch('builtins.input',return_value='n') as decision,patch('sys.stdout',io.StringIO()):
                self.assertIsNone(nowcoder_submit.submit(source))
            api.web.session.post.assert_not_called(); self.assertIn('(y/n)',decision.call_args.args[0])
            with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()) as output:
                self.assertEqual(nowcoder_submit.submit(source),'88')
            api.web.session.post.assert_called_once()
            call = api.web.session.post.call_args
            self.assertEqual(call.args[0], ORIGIN+'/nccommon/submit_cd')
            self.assertEqual({key:call.kwargs['data'][key] for key in ['questionId','tagId','subTagId','doneQuestionId']},
                             {'questionId':'520395','tagId':'4','subTagId':'1','doneQuestionId':'996'})
            self.assertIn('Check judging results here: '+ORIGIN+'/acm/contest/view-submission?submissionId=88',output.getvalue())
            self.assertEqual(refresh.call_count,2); receipt.assert_called_once()


if __name__ == '__main__':
    unittest.main()
