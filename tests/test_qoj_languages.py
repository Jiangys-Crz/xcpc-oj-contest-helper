"""QOJ lists omit Language; the native submission summary supplies it."""
from dataclasses import asdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper.models import Contest, Rules, Submission, VPError
from vp_helper.qoj import QOJ
from test_other_judgement_sync import qoj_row, qoj_table


def detail(sid='100', language='GNU C++20', result='100'):
    row = qoj_row(sid, result).replace('/contest/123/problem/10', '/problem/10')
    row = row.replace('<a class="uoj-username" href="/user/profile/tester">tester</a>',
                      '<span class="uoj-username" data-nickname="tester">tester</span><span class="glyphicon glyphicon-lock"></span>')
    row = row.replace('</td><td><time', f'</td><td>1 ms</td><td>1024 KB</td><td>{language}</td><td>1024 B</td><td><time')
    return BeautifulSoup('<table><thead><tr>' + ''.join('<th>'+name+'</th>' for name in
        ['ID', 'Problem', 'Submitter', 'Result', 'Time', 'Memory', 'Language', 'File size', 'Submit time']) +
        '</tr></thead><tbody>'+row+'</tbody></table>', 'html.parser')


class QOJLanguageTests(unittest.TestCase):
    def api(self, rows=None):
        api = QOJ('https://qoj.ac/contest/123', {'handle': 'tester'})
        api.start_time = 1000
        api._page = Mock(return_value=qoj_table(rows or [qoj_row()]))
        api.web = Mock()
        api.web.get.side_effect = lambda url, **kwargs: SimpleNamespace(url=url,
            text=str(detail(url.rsplit('/', 1)[-1])))
        contest = Contest('qoj', '123', api.url, 'Fixture', 'Fixture', {'A': '10'}, Rules(18000))
        return api, contest

    def test_final_language_comes_from_native_detail_without_changing_verdict(self):
        api, contest = self.api([qoj_row(), qoj_row('101', 'Wrong Answer')])
        for _ in range(2):
            records = api.submissions(contest)
            self.assertEqual([(r.verdict, r.language) for r in records],
                             [('CORRECT', 'GNU C++20'), ('WRONG-ANSWER', 'GNU C++20')])
        self.assertEqual(api.web.get.call_count, 2)
        self.assertEqual(records[1].time, 200)
        self.assertFalse(records[1].practice)

    def test_language_backfill_is_bounded_and_advances_through_history(self):
        api, contest = self.api([qoj_row(str(sid)) for sid in range(100, 105)])
        for cycle, known in enumerate([2, 4, 5]):
            with patch('time.monotonic', return_value=cycle):
                records = api.submissions(contest)
            self.assertEqual(sum(bool(r.language) for r in records), known)
            self.assertEqual(api.web.get.call_count, known)
            self.assertTrue(all(r.verdict == 'CORRECT' for r in records))

    def test_saved_final_languages_survive_resume_without_detail_requests(self):
        for seed in ('seed_submissions', 'seed_pending_submissions'):
            with self.subTest(seed=seed):
                api, contest = self.api()
                event = Submission('100', 'me', 'A', 200, 'CORRECT', language='PyPy3')
                getattr(api, seed)([asdict(event) if seed == 'seed_pending_submissions' else event])
                self.assertEqual(api.submissions(contest)[0].language, 'PyPy3')
                api.web.get.assert_not_called()

    def test_unavailable_or_mismatched_details_retry_without_losing_final_records(self):
        for body in (str(detail()).replace('>tester<', '>other<'),
                     str(detail()).replace('/problem/10', '/problem/11'),
                     str(detail('999')), str(detail(language='—'))):
            with self.subTest(body=body[:40]):
                api, contest = self.api()
                api.web.get.side_effect = None
                api.web.get.return_value = SimpleNamespace(url='https://qoj.ac/submission/100', text=body)
                for now in (0, 1):
                    with patch('time.monotonic', return_value=now):
                        event = api.submissions(contest)[0]
                    self.assertEqual((event.verdict, event.language), ('CORRECT', None))
                self.assertEqual(api.web.get.call_count, 1)
                api.web.get.return_value.text = str(detail(language='PyPy3'))
                with patch('time.monotonic', return_value=31):
                    self.assertEqual(api.submissions(contest)[0].language, 'PyPy3')

    def test_timeout_or_redirect_only_delays_language_enrichment(self):
        for response in (VPError('timeout'), SimpleNamespace(url='https://qoj.ac/login', text=str(detail())),
                         SimpleNamespace(url='https://other.example/submission/100', text=str(detail()))):
            api, contest = self.api()
            api.web.get.side_effect = response if isinstance(response, Exception) else None
            api.web.get.return_value = response
            records = api.submissions(contest)
            self.assertEqual((records[0].verdict, records[0].language), ('CORRECT', None))
            self.assertTrue(api.submissions_complete)

    def test_failure_verdict_read_also_caches_horizontal_compiler_column(self):
        api, contest = self.api([qoj_row(status='0')])
        source = str(detail(result='0')) + '<div class="card-header">Details</div><div class="col-sm-2">Compile Error</div>'
        api.web.soup.return_value = BeautifulSoup(source, 'html.parser')
        with patch('time.monotonic', return_value=0):
            for _ in range(2):
                event = api.submissions(contest)[0]
                self.assertEqual((event.verdict, event.language), ('COMPILER-ERROR', 'GNU C++20'))
        api.web.soup.assert_called_once()
        api.web.get.assert_not_called()

    def test_pending_detail_completion_does_not_read_the_same_language_twice(self):
        api, contest = self.api([qoj_row(status='Judging', marker=True)])
        with patch('time.monotonic', return_value=0):
            self.assertEqual(api.submissions(contest)[0].verdict, 'PENDING')
        api.web.get.assert_not_called()
        with patch('time.monotonic', return_value=6):
            event = api.submissions(contest)[0]
        self.assertEqual((event.verdict, event.language), ('CORRECT', 'GNU C++20'))
        api.web.get.assert_called_once()

    def test_oi_language_enrichment_keeps_native_scores_hidden_during_participation(self):
        api, contest = self.api()
        contest.extra['scoring'] = 'OI'
        with patch('vp_helper.qoj.platform_now', return_value=1500):
            event = api.submissions(contest)[0]
        self.assertEqual((event.verdict, event.score, event.language), ('HIDDEN', None, 'GNU C++20'))


if __name__ == '__main__':
    unittest.main()
