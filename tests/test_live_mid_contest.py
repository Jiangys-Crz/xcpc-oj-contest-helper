"""Entering a running contest must attach to its original official clock."""
from copy import deepcopy
import io
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

from vp_helper import cli, live
from vp_helper.models import Contest, Rules
from vp_helper.storage import read_json, write_json
from vp_helper.tui import LiveDashboard
from test_workflows import Isolated


class MidContestEntryTests(Isolated):
    def test_first_entry_and_reconnect_keep_official_clock_for_all_live_platforms(self):
        self.clock = 2800  # The original contest started thirty minutes ago.
        urls = {'cf': 'https://codeforces.com/contest/1234',
                'cwnuoj': 'https://oj.cwnupaa.com/contest/' + 'a'*24,
                'nowcoder': 'https://ac.nowcoder.com/acm/contest/123'}
        for platform, url in urls.items():
            with self.subTest(platform=platform):
                contest = Contest(platform, url.rsplit('/', 1)[-1], url, platform+' live', 'legacy',
                                  {'A':'10'}, Rules(7200), original_start=1000,
                                  extra={'phase':'CODING', 'scoring':'ICPC', 'uid':42,
                                         'domain':'system', 'end_time':8200})
                snapshot = {'own':None, 'stats':{}, 'phase':'CODING', 'frozen':False, 'at':self.clock}
                account = {'handle':'tester', 'cookie':'synthetic'}
                client = Mock(handle='tester', account=account)
                client.prepare.side_effect = lambda: (deepcopy(contest), deepcopy(snapshot))
                client.contest_name.return_value = contest.name

                def configure(api, current, directory):
                    write_json(directory/'contest.json', {'platform':platform, 'mode':'live',
                               'base_url':url, 'name':current.name, 'problems':list(current.problems),
                               'problem_ids':current.problems, 'handle':'tester'})

                with patch.object(cli,'load_account',return_value={platform:account}), \
                        patch.object(live,'live_adapter',return_value=client), \
                        patch.object(live,'configure_submission',side_effect=configure), \
                        patch.object(live,'desktop',return_value=self.root/'desktop'), \
                        patch.object(cli,'open_state') as opened, \
                        patch('vp_helper.scheduling.prompt_start') as prompt:
                    cli.start(url, live=True)
                    path, state = opened.call_args.args
                    self.assertEqual((state['mode'], state['status']), ('live','running'))
                    self.assertEqual(state['start_time'],1000)
                    self.assertEqual(state['contest']['original_start'],1000)
                    self.assertEqual(state['contest']['rules']['duration'],7200)
                    self.assertEqual(state['start_time']+7200,8200)
                    source = Path(state['directory'])/'A.cpp'
                    source.write_text('preserved solution')
                    state['submissions']=[{'id':'1','team':'me','problem':'A','time':600,
                                           'verdict':'CORRECT','practice':False}]
                    write_json(path,state)
                    self.clock = 3100
                    cli.start(url, live=True)
                    resumed_path, resumed = opened.call_args.args
                    self.assertEqual(resumed_path,path)
                    self.assertEqual(resumed['start_time'],1000)
                    self.assertEqual(resumed['submissions'][0]['time'],600)
                    self.assertEqual(source.read_text(),'preserved solution')
                    self.assertEqual(read_json(source.parent/'contest.json')['vp_context']['start_time'],1000)
                    client.prepare.assert_called_once()
                    client.start.assert_not_called()
                    client.web.post.assert_not_called()
                    prompt.assert_not_called()
                output = io.StringIO()
                Console(file=output,width=160,color_system=None).print(
                    LiveDashboard(contest,self.clock-contest.original_start,snapshot,[]))
                self.assertIn('00:35:00 / 02:00:00',output.getvalue())
                self.clock = 2800
