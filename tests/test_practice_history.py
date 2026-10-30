from datetime import datetime, timezone
from unittest.mock import Mock
import unittest

from bs4 import BeautifulSoup

from vp_helper.models import Contest, Rules
from vp_helper.practice import Practice, PracticeDashboard
from vp_helper.qoj import QOJ


class PracticeHistoryTests(unittest.TestCase):
    def test_qoj_bt_reads_native_scoring_and_custom_full_score(self):
        wrapper=Practice.__new__(Practice)
        wrapper.platform,wrapper.id,wrapper.url='qoj','123','https://qoj.ac/contest/123'
        wrapper.account={'_login_verified':True}
        wrapper.handle='tester'
        wrapper.api=Mock(handle='tester')
        wrapper.api.contest_name.return_value='Point contest'
        wrapper.api.graded_maxima=QOJ.graded_maxima
        wrapper.api._page.return_value=BeautifulSoup('''
            <p>Ruleset: IOI</p><table><tr><td>A</td><td><a href="/contest/123/problem/10">Problem</a></td></tr></table>
            <script>var score={"tester":{"0":[100,60,77,0,200,0]}};</script>''','html.parser')
        prepared=wrapper.prepare()
        self.assertEqual(prepared.extra['scoring'],'IOI')
        self.assertEqual(prepared.extra['problem_max_scores'],{'A':200})

    def test_qoj_bt_loads_web_submissions_older_than_local_bt_without_a_receipt(self):
        url = 'https://qoj.ac/contest/123'
        contest = Contest('qoj', '123', url, 'Practice', 'Practice', {'A':'10'}, Rules(1),
                          original_start=2000, extra={'practice':True, 'scoring':'ICPC'})
        client = QOJ(url, {'handle':'tester'})
        stamp = datetime.fromtimestamp(1000, timezone.utc).isoformat()
        client._page = Mock(return_value=BeautifulSoup(
            '<table><thead><tr><th>#</th><th>Problem</th><th>Submitter</th><th>Score</th><th>Time</th></tr></thead>'
            '<tr><td><a href="/submission/77">77</a></td><td><a href="/contest/123/problem/10">A</a></td>'
            '<td><a class="uoj-username" href="/user/profile/tester">tester</a></td>'
            f'<td>100</td><td><time datetime="{stamp}"></time></td></tr></table>', 'html.parser'))
        wrapper = Practice.__new__(Practice)
        wrapper.api, wrapper.platform = client, 'qoj'
        records = wrapper.submissions(contest, 2000)
        self.assertEqual([(e['id'],e['time'],e['verdict'],e['practice']) for e in records],
                         [('77',-1000,'CORRECT',True)])
        self.assertEqual(2000 + records[0]['time'], 1000)
        view = PracticeDashboard(contest, 100, records)
        self.assertEqual((view.me.cells['A'].tries, view.me.cells['A'].solved), (1, True))
        self.assertIsNone(view.me.points)


if __name__ == '__main__':
    unittest.main()
