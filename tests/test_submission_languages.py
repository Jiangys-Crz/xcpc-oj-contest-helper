"""Compiler metadata follows native records without changing judging or score."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper import cli, cloud, cloud_restore
from vp_helper.cf_judgements import PendingJudgements, web_verdicts
from vp_helper.codeforces_live import CodeforcesLive
from vp_helper.gym import Gym
from vp_helper.hydro import HydroLive, ORIGIN as HYDRO_ORIGIN
from vp_helper.models import Contest, Rules, Submission
from vp_helper.nowcoder import NowcoderLive, PROBLEMS
from vp_helper.pending_results import PendingResults
from vp_helper.qoj import QOJ
from vp_helper.receipts import record_receipt, ReceiptReader
from vp_helper.scoring import submission_history
from vp_helper.sessions import merge_records, save_state
from vp_helper.storage import read_json
from vp_helper.submission_languages import html_language, language_fields, language_name, merge_language
from test_codeforces_live import contest as cf_contest, submission as cf_submission
from test_hydro_live import TID, RID, details as hydro_details
from test_nowcoder_live import details as nk_details, problems, rank, record, status
from test_workflows import Isolated, URL


class LanguageParsingTests(unittest.TestCase):
    def test_native_named_fields_and_platform_scoped_numeric_compilers(self):
        for raw, expected in (({'programmingLanguage': 'GNU G++20 (64)'}, 'GNU G++20 (64)'),
                              ({'lang': 'cc.cc20o2'}, 'cc.cc20o2'),
                              ({'languageName': 'Python 3'}, 'Python 3')):
            self.assertEqual(language_fields(raw), {'language': expected})
        self.assertEqual(language_fields({'language': 11}, 'nowcoder'), {'language': 'Python3'})
        self.assertEqual(language_fields({'compilerId': '38'}, 'nowcoder'), {'language': 'C++(g++ 13)'})
        self.assertEqual(language_fields({'language': 9999}, 'nowcoder'), {})
        self.assertEqual(language_fields({'language': '11'}), {})
        for value in (None, 11, {}, [], '', 'unknown', 'n/a', 'x\nsecret', 'a'*161):
            self.assertIsNone(language_name(value))

    def test_html_compiler_requires_label_or_explicit_selector(self):
        source = '<table><thead><tr><th>ID</th><th>Language</th></tr></thead><tr><td>1</td><td>GNU G++20</td></tr></table>'
        soup = BeautifulSoup(source, 'html.parser')
        self.assertEqual(html_language(soup.select('tr')[-1]), 'GNU G++20')
        self.assertEqual(html_language(BeautifulSoup('<tr><td class="status-language-cell">PyPy 3</td></tr>', 'html.parser')), 'PyPy 3')
        self.assertEqual(html_language(BeautifulSoup('<table><tr><th>Language:</th><td>Java 17</td></tr></table>', 'html.parser')), 'Java 17')
        self.assertIsNone(html_language(BeautifulSoup('<tr><td>1</td><td>GNU G++20</td></tr>', 'html.parser')))

    def test_language_enrichment_is_nonmutating_and_keeps_selected_verdict(self):
        old = {'id':'1', 'verdict':'CORRECT', 'language':'GNU G++20'}
        incoming = {'id':'1', 'verdict':'PENDING'}
        before = deepcopy((old, incoming))
        self.assertEqual(merge_language(old, incoming), {**incoming, 'language':'GNU G++20'})
        self.assertEqual(merge_language(old, {**incoming, 'language':'other'})['language'], 'GNU G++20')
        self.assertEqual((old, incoming), before)

    def test_cf_live_and_bt_parse_final_rows_with_or_without_prior_cache(self):
        api = CodeforcesLive('https://codeforces.com/contest/1234', {'handle':'tester'})
        raw = {**cf_submission(), 'programmingLanguage':'GNU G++20'}
        api.api = Mock(return_value=[raw])
        for practice in (False, True):
            c = cf_contest(); c.extra['practice'] = practice
            self.assertEqual(api.submissions(c)[0]['language'], 'GNU G++20')
        api.api.return_value = [{**raw, 'programmingLanguage':'PyPy 3'}]
        self.assertEqual(api.submissions(cf_contest())[0]['language'], 'PyPy 3')

    def test_cf_and_gym_virtual_parse_native_compiler_on_final_records(self):
        for segment, platform in (('contest','cf'), ('gym','gym')):
            api = Gym(f'https://codeforces.com/{segment}/123', {'handle':'tester'})
            api.start_time = 1000
            api.api = Mock(return_value=[{'id':7, 'contestId':123, 'creationTimeSeconds':1100,
                'relativeTimeSeconds':100, 'problem':{'index':'A'}, 'verdict':'OK',
                'author':{'participantType':'VIRTUAL','startTimeSeconds':1000,'members':[{'handle':'tester'}]},
                'programmingLanguage':'GNU G++20'}])
            c = Contest(platform,'123',api.url,'Fixture','Fixture',{'A':'A'},Rules(3600))
            self.assertEqual(api.submissions(c)[0].language, 'GNU G++20')

    def test_qoj_final_cache_and_pending_rows_preserve_explicit_language(self):
        started = datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
        stamp = datetime.fromtimestamp(started+60, timezone.utc).isoformat()
        api = QOJ(URL, {'handle':'tester'}); api.start_time = started
        api._registration = Mock(return_value=(started,3600))
        row = (f'<tr><td><a href="/submission/7">7</a></td><td><a href="/contest/123/problem/10">A</a></td>'
               f'<td><a href="/user/profile/tester">tester</a></td><td>100</td><td>GNU G++20</td>'
               f'<td><time datetime="{stamp}"></time></td></tr>')
        api._page = Mock(return_value=BeautifulSoup('<table><thead><tr><th>#</th><th>Problem</th><th>Submitter</th>'
              '<th>Score</th><th>Language</th><th>Submit time</th></tr></thead><tbody>'+row+'</tbody></table>', 'html.parser'))
        c = Contest('qoj','123',URL,'Fixture','Fixture',{'A':'10'},Rules(3600))
        for _ in range(2):
            records = api.submissions(c)
            self.assertEqual((records[0].verdict, records[0].language), ('CORRECT','GNU G++20'))
        api.web.get = Mock(return_value=SimpleNamespace(url='https://qoj.ac/submission/7', text=str(api._page.return_value)))
        self.assertEqual(api.pending_detail(c, {'id':'7','problem':'A'})['language'], 'GNU G++20')

    def test_hydro_live_and_practice_retain_lang_identifier(self):
        api = HydroLive(HYDRO_ORIGIN+'/contest/'+TID, {'handle':'tester','uid':42})
        api._identified = True
        c = Contest('cwnuoj',TID,api.url,'Fixture','Fixture',{},Rules(1))
        with patch('vp_helper.hydro.time.time', return_value=1200):
            api.apply_metadata(c, hydro_details())
        raw = {'_id':RID,'uid':42,'pid':10,'contest':TID,'status':1,'lang':'cc.cc20o2'}
        api.get_json = Mock(side_effect=lambda path, **kw: {'rdocs':[raw] if kw.get('page')==1 else []})
        self.assertEqual(api.submissions(c)[0]['language'], 'cc.cc20o2')
        c.extra['practice'] = True
        self.assertEqual(api.submissions(c)[0]['language'], 'cc.cc20o2')

    def test_nowcoder_live_and_bt_map_native_numeric_compiler(self):
        api = NowcoderLive('https://ac.nowcoder.com/acm/contest/123', {'handle':'42','uid':42})
        api._identified = True
        c = Contest('nowcoder','123',api.url,'Fixture','Fixture',{},Rules(1))
        api.details = Mock(return_value=nk_details())
        api.get_json = Mock(side_effect=lambda path, **kw: deepcopy(problems() if path==PROBLEMS else rank()))
        api.snapshot(c)
        api.get_json = Mock(return_value=status([{**record(), 'language':11}]))
        self.assertEqual(api.submissions(c)[0]['language'], 'Python3')
        c.extra['practice'] = True
        api.get_json.return_value = status([{**record(), 'compilerId':38}])
        self.assertEqual(api.submissions(c)[0]['language'], 'C++(g++ 13)')

    def test_pending_detail_metadata_is_kept_before_and_after_judging(self):
        pending = PendingResults(); api = SimpleNamespace(submission_message='')
        event = {'id':'7','problem':'A','verdict':'PENDING'}
        with patch('vp_helper.pending_results.time.monotonic', side_effect=[0,7,13]):
            pending.update(api,[event],Mock())
            values = pending.update(api,[event],lambda e:{'verdict':'PENDING','language':'Python3'})
            self.assertEqual(values[0]['language'],'Python3')
            values = pending.update(api,values,lambda e:{'verdict':'CORRECT'})
        self.assertEqual((values[0]['language'],values[0]['verdict']),('Python3','CORRECT'))

    def test_cf_web_pending_fallback_preserves_language(self):
        url='https://codeforces.com/contest/1234'
        source = ('<div id="header"><a href="/profile/tester">tester</a></div>'
          '<table><tr data-submission-id="7"><td><a href="/contest/1234/problem/A">A</a>'
          '<a href="/profile/tester">tester</a></td><td class="status-language-cell">GNU G++20</td>'
          '<td class="status-verdict-cell" waiting="true">In queue</td></tr></table>')
        event={'id':'7','problem':'A','verdict':'PENDING'}
        self.assertEqual(web_verdicts(source,url,'tester',{'7':event})['7']['language'],'GNU G++20')
        api = SimpleNamespace(url=url,handle='tester',web=Mock(),submission_message='')
        api.web.get.return_value=SimpleNamespace(url=url+'/my',text=source)
        pending = PendingJudgements()
        with patch('vp_helper.cf_judgements.time.monotonic',side_effect=[0,9]):
            pending.update(api,[event]); values=pending.update(api,[event])
        self.assertEqual(values[0]['language'],'GNU G++20')


class LanguagePersistenceTests(Isolated):
    def test_receipt_history_cloud_and_restore_allowlist_keep_language(self):
        path,state = cli.prepare_start(URL)
        cfg=read_json(Path(state['directory'])/'contest.json')
        self.assertTrue(record_receipt(cfg,'7','A',1060,language='GNU G++20'))
        self.assertEqual(ReceiptReader(path,state).read()[0].language,'GNU G++20')
        records=merge_records(path,state,[])
        self.assertEqual(records[0]['language'],'GNU G++20')
        records=merge_records(path,{**state,'submissions':records},[{**records[0],'verdict':'CORRECT','language':None}])
        state['submissions']=records
        projected=submission_history([Submission(**e) for e in records],['A'],Rules(18000),100)
        self.assertEqual(projected[0]['language'],'GNU G++20')
        payload=cloud.sync_payload(path,state)
        self.assertEqual(payload['submissions'][0]['language'],'GNU G++20')
        self.assertNotIn('cookie',payload['submissions'][0])
        restored=cloud_restore._records({'submissions':payload['submissions']},state['start_time'],{'A':'10'})
        self.assertEqual(restored[0]['language'],'GNU G++20')
        old={**restored[0],'verdict':'CORRECT'}
        enriched=cloud_restore._merge([old],[{**restored[0],'verdict':'PENDING','language':'cpp'}])
        self.assertEqual(enriched[0]['verdict'],'CORRECT')
        self.assertEqual(enriched[0]['language'],'GNU G++20')

    def test_stale_sync_can_enrich_language_without_replacing_final_judgement(self):
        path,state=cli.prepare_start(URL)
        event=asdict(Submission('7','me','A',60,'CORRECT'))
        state.update(submissions=[event],sync_revision=2)
        save_state(path,state)
        stale=deepcopy(state)
        stale.update(submissions=[{**event,'verdict':'PENDING','language':'Python3'}],sync_revision=1)
        save_state(path,stale)
        saved=read_json(path)
        self.assertEqual(saved['submissions'][0]['verdict'],'CORRECT')
        self.assertEqual(saved['submissions'][0]['language'],'Python3')
        self.assertEqual(saved['sync_revision'],2)

    def test_old_model_state_remains_compatible_and_missing_language_is_not_guessed(self):
        event=Submission(**{'id':'7','team':'me','problem':'A','time':60,'verdict':'CORRECT'})
        self.assertIsNone(event.language)
        path,state=cli.prepare_start(URL)
        state['submissions']=[asdict(event)]
        payload=cloud.sync_payload(path,state)
        self.assertNotIn('language',payload['submissions'][0])


if __name__=='__main__':
    unittest.main()
