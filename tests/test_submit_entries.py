"""Exercise installed dispatchers and upstream parsers without network I/O."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import time
import unittest

from vp_helper.storage import write_json


REPOSITORY = Path(__file__).resolve().parents[1]


class InstalledEntries(unittest.TestCase):
    def test_qoj_linux_dispatcher_language_redirect_and_cf_absolute_path_parser(self):
        for platform in ("qoj", "gym"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = root / 'bundle'
                for submitter in ('qoj', 'cf_submit'):
                    shutil.copytree(REPOSITORY / 'third_party' / submitter, bundle / 'third_party' / submitter)
                dispatcher_platform = 'darwin'
                if platform == 'qoj' and os.name == 'posix':
                    # Linux executes the installed script's shebang directly.
                    dispatcher_platform = 'linux'
                    script = bundle / 'third_party/qoj/submit'
                    script.write_text('#!' + sys.executable + '\n' + script.read_text().split('\n', 1)[1])
                # Exercise this checkout's real dispatcher and bundled parsers,
                # independently of an unrelated installed XCPC release.
                entry = root / 'dispatcher.py'
                entry.write_text('import sys\nfrom types import SimpleNamespace\n'
                    'from vp_helper import submit\n'
                    'submit.sys=SimpleNamespace(argv=sys.argv,prefix=' + repr(str(bundle / 'venv')) + ',executable=sys.executable,platform=' + repr(dispatcher_platform) + ')\n'
                    'submit.main()\n')
                source = root/"A.cpp"
                source.write_text("int main() {}\n")
                state_path = root/"state/vp/bt-test.json"
                url = "https://qoj.ac/contest/123" if platform == "qoj" else "https://codeforces.com/gym/100123"
                state = {"mode":"practice", "url":url, "start_time":time.time()-10, "handle":"tester",
                         "contest":{"platform":platform,"problems":{"A":"10"},"rules":{"duration":1}}}
                write_json(state_path,state)
                config = {"platform":platform, "mode":"practice" if platform=="qoj" else "vp", "name":"fixture",
                          "base_url":url,"contest_id":100123,"problems":"A","problem_ids":{"A":"10"},"offset":10,
                          "handle":"tester","cookie_source":"manual","cookie":"synthetic=local","user_agent":"fixture",
                          "vp_context":{"state_path":str(state_path),"url":url,"start_time":state["start_time"],"handle":"tester"}}
                write_json(root/"contest.json",config)
                # Child-process test fixtures replace transport (QOJ) or the
                # final submit callback (CF), while keeping the real entry,
                # interpreter, configuration resolver and argument parser.
                (root/"sitecustomize.py").write_text('''
import os, sys
from pathlib import Path
from types import SimpleNamespace
if os.environ["VP_FIXTURE_PLATFORM"] == "gym":
    sys.path.insert(0,os.environ["VP_FIXTURE_CF_DIR"])
    import cf_login, cf_submit
    cf_login.get_secret=lambda password: ("tester","synthetic") if password else "tester"
    cf_login.login=lambda: SimpleNamespace()
    def submit_files(browser,handle,contest,problem,ext,lang,sources,show,guru):
        print("OFFLINE_CF_PARSER",contest,problem,sources[0])
    cf_submit.submit_files=submit_files
else:
    from curl_cffi import requests
    from requests.cookies import RequestsCookieJar
    class Response:
        status_code=200
        headers={}
        def __init__(self,url):
            self.url=url + '/statement/zh_cn' if '/problem/' in url and '/statement/' not in url else url
            self.text='<form id="form-answer" method="post" action="'+self.url+'"><input name="_token" value="fixture"></form>'
        def raise_for_status(self): pass
    class Session:
        def __init__(self,**kwargs): self.headers={}; self.cookies=RequestsCookieJar()
        def get(self,url,**kwargs): return Response(url)
        def close(self): pass
        def post(self,url,**kwargs):
            assert url.endswith('/problem/10/statement/zh_cn'), url
            response=Response(url)
            response.status_code=302
            response.headers={"Location":"/submission/12345"}
            return response
    requests.Session=Session
''')
                env = {**os.environ,"PYTHONPATH":str(root)+os.pathsep+str(REPOSITORY),"XDG_STATE_HOME":str(root/"state"),"XDG_CONFIG_HOME":str(root/"config"),
                       "VP_FIXTURE_PLATFORM":platform,"VP_FIXTURE_CF_DIR":str(bundle/'third_party/cf_submit')}
                if platform=='qoj':
                    declined=subprocess.run([sys.executable,str(entry),str(source)],input='n\n',cwd=root,env=env,
                                            capture_output=True,text=True,timeout=30)
                    self.assertEqual(declined.returncode,0,declined.stdout+declined.stderr)
                    self.assertIn('Do you want to continue?',declined.stdout)
                    self.assertIn('Aborted.',declined.stdout)
                    self.assertNotIn('Check judging results here:',declined.stdout)
                    self.assertFalse(state_path.with_suffix('.receipts').exists())
                result = subprocess.run([sys.executable,str(entry),str(source)],input='y\n',
                                        cwd=root,env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                if platform=="qoj":
                    receipt=json.loads((state_path.with_suffix(".receipts")/"12345.json").read_text())
                    self.assertTrue(receipt["practice"])
                    self.assertIn("Submission #12345",result.stdout)
                    self.assertIn('Check judging results here: https://qoj.ac/submission/12345',result.stdout)
                else:
                    self.assertIn("OFFLINE_CF_PARSER 100123 A",result.stdout)
                    self.assertIn(str(source),result.stdout)
