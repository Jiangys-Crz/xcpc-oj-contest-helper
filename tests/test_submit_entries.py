"""Exercise installed dispatchers and upstream parsers without network I/O."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from vp_helper.storage import write_json


ENTRY = Path(sys.prefix) / "bin/vp-submit"


@unittest.skipUnless(ENTRY.is_file(), "requires the installed bundled Ubuntu submitters")
class InstalledEntries(unittest.TestCase):
    def test_actual_qoj_practice_script_and_cf_absolute_path_parser(self):
        for platform in ("qoj", "gym"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
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
if os.environ["VP_FIXTURE_PLATFORM"] == "gym":
    sys.path.insert(0,str(Path(sys.prefix).parent/"third_party/cf_submit"))
    import cf_login, cf_submit
    cf_login.get_secret=lambda password: ("tester","synthetic") if password else "tester"
    cf_login.login=lambda: object()
    def submit_files(browser,handle,contest,problem,ext,lang,sources,show,guru):
        print("OFFLINE_CF_PARSER",contest,problem,sources[0])
    cf_submit.submit_files=submit_files
else:
    from curl_cffi import requests
    class Response:
        status_code=200
        headers={}
        text='<input name="_token" value="fixture">'
        def __init__(self,url): self.url=url
        def raise_for_status(self): pass
    class Session:
        def __init__(self,**kwargs): self.headers={}
        def get(self,url,**kwargs): return Response(url)
        def post(self,url,**kwargs):
            response=Response(url)
            response.status_code=302
            response.headers={"Location":"/submission/12345"}
            return response
    requests.Session=Session
''')
                env = {**os.environ,"PYTHONPATH":str(root),"XDG_STATE_HOME":str(root/"state"),
                       "VP_FIXTURE_PLATFORM":platform}
                if platform=='qoj':
                    declined=subprocess.run([str(ENTRY),str(source)],input='n\n',cwd=root,env=env,
                                            capture_output=True,text=True,timeout=30)
                    self.assertEqual(declined.returncode,0,declined.stdout+declined.stderr)
                    self.assertIn('Do you want to continue?',declined.stdout)
                    self.assertIn('Aborted.',declined.stdout)
                    self.assertNotIn('Check judging results here:',declined.stdout)
                    self.assertFalse(state_path.with_suffix('.receipts').exists())
                result = subprocess.run([str(ENTRY),str(source)],input='y\n',
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
