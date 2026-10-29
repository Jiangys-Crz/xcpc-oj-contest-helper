"""Mixed final, waiting and unfamiliar results must not lose the rest of a poll."""
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup

import test_codeforces_live as cf
import test_hydro_live as hydro
import test_nowcoder_live as nk
from vp_helper.codeforces_live import CodeforcesLive
from vp_helper.gym import Gym
from vp_helper.hydro import HydroLive
from vp_helper.models import Contest, Rules, Submission
from vp_helper.nowcoder import NowcoderLive
from vp_helper.qoj import QOJ
from vp_helper.scoring import codeforces_verdict, score_submissions


class VerdictMappingTests(unittest.TestCase):
    def test_every_documented_cf_enum_and_future_outcome(self):
        expected = {
            'FAILED': 'UNAVAILABLE', 'OK': 'CORRECT', 'PARTIAL': 'PARTIAL',
            'COMPILATION_ERROR': 'COMPILER-ERROR', 'RUNTIME_ERROR': 'RUN-ERROR',
            'WRONG_ANSWER': 'WRONG-ANSWER', 'TIME_LIMIT_EXCEEDED': 'TIMELIMIT',
            'MEMORY_LIMIT_EXCEEDED': 'MEMORY-LIMIT', 'IDLENESS_LIMIT_EXCEEDED': 'IDLENESS-LIMIT',
            'SECURITY_VIOLATED': 'SECURITY-VIOLATED', 'CRASHED': 'UNAVAILABLE',
            'INPUT_PREPARATION_CRASHED': 'UNAVAILABLE', 'CHALLENGED': 'CHALLENGED',
            'SKIPPED': 'UNAVAILABLE', 'TESTING': 'PENDING', 'REJECTED': 'UNAVAILABLE',
            'SUBMITTED': 'PENDING', 'FUTURE_VERDICT': 'UNAVAILABLE', None: 'PENDING',
        }
        for raw, parsed in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(codeforces_verdict(raw), parsed)

    def test_cf_live_and_vp_mix_ile_ac_and_unknown_without_aborting(self):
        values = ['IDLENESS_LIMIT_EXCEEDED', 'OK', 'FUTURE_VERDICT', 'TESTING']
        live = CodeforcesLive(cf.URL, {'handle': 'tester'})
        live.api = Mock(return_value=[cf.submission(i, verdict=v) for i, v in enumerate(values, 1)])
        self.assertEqual([e['verdict'] for e in live.submissions(cf.contest())],
                         ['IDLENESS-LIMIT', 'CORRECT', 'UNAVAILABLE', 'PENDING'])
        for segment in ('contest', 'gym'):
            vp = Gym(f'https://codeforces.com/{segment}/1234', {'handle': 'tester'})
            vp.start_time = 1000
            inputs = []
            for i, v in enumerate(values, 1):
                row = cf.submission(i, kind='VIRTUAL', verdict=v, at=1000+i)
                row['relativeTimeSeconds'] = i
                row['author']['startTimeSeconds'] = 1000
                inputs.append(row)
            vp.api = Mock(return_value=inputs)
            self.assertEqual([e.verdict for e in vp.submissions(cf.contest())],
                             ['IDLENESS-LIMIT', 'CORRECT', 'UNAVAILABLE', 'PENDING'])

    def test_qoj_unknown_result_does_not_block_known_or_pending_rows(self):
        url = 'https://qoj.ac/contest/123'
        api = QOJ(url, {'handle': 'tester'})
        api.start_time = 1000
        labels = ['Dangerous Syscalls', 'Judgement Failed', 'Compiling', 'Presentation Error', 'future result', 'Accepted']
        rows = [f'<tr><td><a href="/submission/{i}">{i}</a></td>'
                '<td><a href="/contest/123/problem/10">A</a></td>'
                '<td><a class="uoj-username" href="/user/profile/tester">tester</a></td>'
                f'<td>{v}</td><td><time datetime="1970-01-01T00:20:00Z"></time></td></tr>'
                for i, v in enumerate(labels, 1)]
        api._page = Mock(return_value=BeautifulSoup('<table>' + ''.join(rows) + '</table>', 'html.parser'))
        contest = Contest('qoj', '123', url, 'Fixture', 'Fixture', {'A': '10'}, Rules(18000))
        self.assertEqual([e.verdict for e in api.submissions(contest)],
                         ['RUN-ERROR', 'UNAVAILABLE', 'PENDING', 'FORMAT-ERROR', 'UNAVAILABLE', 'CORRECT'])
        self.assertIn('#5', api.submission_message)

    def test_hydro_documented_numeric_states_and_unknown_are_all_retained(self):
        expected = {0: 'PENDING', 1: 'CORRECT', 2: 'WRONG-ANSWER', 3: 'TIMELIMIT', 4: 'MEMORY-LIMIT',
                    5: 'OUTPUT-LIMIT', 6: 'RUN-ERROR', 7: 'COMPILER-ERROR', 8: 'SYSTEM-ERROR',
                    9: 'CANCELLED', 10: 'UNAVAILABLE', 11: 'HACKED', 20: 'PENDING', 21: 'PENDING',
                    22: 'PENDING', 30: 'IGNORED', 31: 'FORMAT-ERROR', 32: 'HACK-SUCCESSFUL',
                    33: 'HACK-UNSUCCESSFUL', 999: 'UNAVAILABLE', None: 'HIDDEN'}
        api = HydroLive(hydro.URL, {'handle': 'tester', 'uid': 42})
        api._identified = True
        contest = Contest('cwnuoj', hydro.TID, hydro.URL, 'Fixture', 'Fixture', {}, Rules(1))
        api.apply_metadata(contest, hydro.details())
        contest.problems = {'A': '10'}
        rows = [{'_id': f'{1100:08x}{i:016x}', 'uid': 42, 'pid': 10, 'domainId': 'system', 'status': value}
                for i, value in enumerate(expected, 1)]
        api.get_json = Mock(return_value={'ratingDynamicRecords': True, 'rdocs': rows, 'ratingRecordsHasNext': False})
        self.assertEqual([e['verdict'] for e in api.submissions(contest)], list(expected.values()))

    def test_nowcoder_faq_runtime_variants_english_hidden_and_unknown(self):
        api = NowcoderLive(nk.URL, {'handle': '42', 'uid': 42})
        api._identified = True
        contest = Contest('nowcoder', '123', nk.URL, 'Fixture', 'Fixture', {}, Rules(1))
        api.apply_metadata(contest, nk.details())
        contest.problems = {'A': '10'}
        values = ['异常退出', '返回非零', '浮点错误', '段错误', '多种错误', '内部错误',
                  ' COMPILATION_ERROR ', 'Compiling', '答案正确', '--', 'new result']
        api.get_json = Mock(return_value=nk.status([nk.record(i, verdict=v) for i, v in enumerate(values, 1)]))
        self.assertEqual([e['verdict'] for e in api.submissions(contest)],
                         ['RUN-ERROR']*4 + ['MULTIPLE-ERRORS', 'SYSTEM-ERROR', 'COMPILER-ERROR',
                                          'PENDING', 'CORRECT', 'HIDDEN', 'UNAVAILABLE'])

    def test_infrastructure_hidden_and_hack_records_never_add_wrong_attempts(self):
        statuses = ['SYSTEM-ERROR', 'HIDDEN', 'CANCELLED', 'IGNORED', 'HACK-SUCCESSFUL',
                    'HACK-UNSUCCESSFUL', 'UNAVAILABLE', 'IDLENESS-LIMIT', 'CORRECT']
        score = score_submissions([Submission(str(i), 'me', 'A', 60+i, v) for i, v in enumerate(statuses)],
                                  ['A'], Rules(18000), 100)
        self.assertEqual(score.cells['A'].tries, 2)
        self.assertEqual(score.cells['A'].unresolved, 4)
        self.assertEqual(score.penalty, 21)
        self.assertFalse(score.results_complete)
