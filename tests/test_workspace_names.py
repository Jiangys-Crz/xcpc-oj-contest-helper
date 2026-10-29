from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper import cli, live, practice
from vp_helper.codeforces_live import CodeforcesLive
from vp_helper.gym import Gym
from vp_helper.hydro import HydroLive
from vp_helper.models import Contest, Rules
from vp_helper.nowcoder import NowcoderLive
from vp_helper.qoj import QOJ
from vp_helper.storage import read_json, state_directory, write_json
from vp_helper.workspaces import rename_existing
from test_workflows import Isolated


class WorkspaceNamesTests(Isolated):
    def test_rename_preserves_a_new_verdict_received_during_name_refresh(self):
        path,stale=cli.prepare_start('https://qoj.ac/contest/123')
        current=read_json(path)
        current.update(status='archived',ended_at=1100,practice_view=True,
                       submissions=[{'id':'7','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}])
        write_json(path,current)
        contest=Contest.from_dict(stale['contest'])
        contest.name='Actual updated contest title'
        rename_existing(path,stale,contest)
        saved=read_json(path)
        self.assertEqual(saved['submissions'],current['submissions'])
        self.assertEqual(saved['status'],'archived')
        self.assertTrue(saved['practice_view'])
        self.assertEqual(saved['ended_at'],1100)

    def test_all_platforms_refresh_legacy_name_and_restore_all_files_and_records(self):
        platforms=[('qoj','https://qoj.ac/contest/123','vp',QOJ),
                   ('cf','https://codeforces.com/contest/123','vp',Gym),
                   ('gym','https://codeforces.com/gym/100123','vp',Gym),
                   ('cwnuoj','https://oj.cwnupaa.com/contest/'+'a'*24,'live',HydroLive),
                   ('nowcoder','https://ac.nowcoder.com/acm/contest/123','live',NowcoderLive)]
        self.clock=2000
        for platform,url,mode,kind in platforms:
            with self.subTest(platform=platform):
                title='Actual 比赛 '+platform
                account={**self.api.account,'uid':42}
                client=kind(url,account)
                client.prepare=Mock(side_effect=AssertionError('restoring must not start or prepare a new contest'))
                if platform=='qoj':
                    client._page=Mock(return_value=BeautifulSoup('<h1>QOJ</h1><h2>'+title+'</h2>','html.parser'))
                elif platform in ('cf','gym'):
                    client.api=Mock(return_value={'contest':{'name':title}})
                elif platform=='cwnuoj':
                    client.details=Mock(return_value={'tdoc':{'docId':client.id,'domainId':'system','title':title}})
                else:
                    client.details=Mock(return_value={'contestId':client.id,'name':title})
                directory=self.root/'desktop'/('legacy-'+platform)
                (directory/'nested').mkdir(parents=True)
                files={'A.cpp':b'original source','b.py':b'print(42)','Statements.pdf':b'%PDF-existing',
                       'nested/data.bin':bytes(range(256))}
                for filename,value in files.items():
                    (directory/filename).write_bytes(value)
                contest=Contest(platform,client.id,url,platform+' '+client.id,platform,{'A':'10'},Rules(100),
                                original_start=1000,extra={'uid':42,'phase':'FINISHED','scoring':'ICPC'})
                path=state_directory()/('live-'+platform+'-test.json' if mode=='live' else 'vp-'+platform+'-test.json')
                state={'mode':mode,'url':url,'start_time':1000,'handle':'tester','directory':str(directory),
                       'contest':contest.to_dict(),'status':'complete','tmux_session':platform+'-test',
                       'submissions':[{'id':'1','team':'me','problem':'A','time':60,'verdict':'CORRECT','practice':False}]}
                write_json(path,state)
                def configure(api,con,folder,selected_path,saved,**kwargs):
                    write_json(folder/'contest.json',{'platform':platform,'name':con.name,
                               'vp_context':{'state_path':str(selected_path)}})
                with patch.object(cli,'load_account',return_value={platform:account}), \
                     patch.object(cli,'adapter',return_value=client), \
                     patch.object(live,'live_adapter',return_value=client), \
                     patch.object(cli,'configure_contest',side_effect=configure), \
                     patch.object(cli,'open_state'):
                    if platform=='cf':
                        with patch.object(CodeforcesLive,'contest_name',return_value=title):
                            cli.start(url)
                            practice.prepare(url)
                    else:
                        cli.start(url,live=mode=='live')
                        practice.prepare(url)
                    self.assertEqual(read_json(path)['directory'],str(directory.parent/title))
                    self.assertEqual(read_json(path)['contest']['name'],title)
                    self.assertEqual(read_json(path)['submissions'],state['submissions'])
                    self.assertTrue(read_json(path)['practice_view'])
                    for filename,value in files.items():
                        self.assertEqual((directory.parent/title/filename).read_bytes(),value)
                    self.assertFalse(directory.exists())
                    client.prepare.assert_not_called()
