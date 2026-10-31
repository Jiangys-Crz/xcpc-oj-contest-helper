from contextlib import ExitStack
import json
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vp_helper import browser_bridge as bridge, browser_rpc as rpc
from vp_helper.storage import read_json, state_directory, write_json

PROFILE='01234567-89ab-cdef-0123-456789abcdef'
OTHER='abcdef01-2345-6789-abcd-ef0123456789'
URL='https://codeforces.com/gym/106554/submit/H'


class BrowserRPCTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')}))
        bridge.store_snapshot({'source':'edge','profile_id':PROFILE,'user_agent':'Fixture Edge','cookies':[], 'capabilities':[rpc.CAPABILITY]})
        self.account={'cookie_source':'edge','browser_profile':str(state_directory()/'browser-login/edge'/f'{PROFILE}.json'),'handle':'MyHandle'}

    def start_request(self,method='GET',url=URL,**kwargs):
        values={}
        def run():
            try:values['response']=rpc.request(self.account,method,url,**kwargs)
            except Exception as exc:values['error']=exc
        thread=threading.Thread(target=run);thread.start();self.addCleanup(thread.join,3)
        until=time.monotonic()+1
        while not list(rpc._identity('edge',PROFILE).glob('*.json')) and time.monotonic()<until:time.sleep(.005)
        return thread,values

    def result(self,**change):
        return {'status':200,'url':'https://codeforces.com/gym/106554/my','text':'<div id="header">Fixture</div>',
                'headers':{'Content-Type':'text/html','Date':'Fri, 30 Oct 2026 12:00:00 GMT','Set-Cookie':'must-not-return'},
                'cookies':[{'domain':'.codeforces.com','name':'39ce7','value':'fresh-fixture','path':'/'},
                           {'domain':'qoj.ac','name':'private','value':'must-not-return','path':'/'}],**change}

    def test_capability_and_exact_snapshot_profile_are_required_before_dispatch(self):
        self.assertTrue(rpc.available(self.account))
        path=Path(self.account['browser_profile']);saved=read_json(path);saved['capabilities']=[];write_json(path,saved)
        with self.assertRaises(rpc.BrowserRPCUnavailable):rpc.request(self.account,'POST',URL,data={})
        self.assertFalse(list((state_directory()/'browser-rpc').glob('**/*.json')))
        saved['capabilities']=[rpc.CAPABILITY];write_json(path,saved)
        self.assertFalse(rpc.available({**self.account,'browser_profile':'/tmp/unrelated.json'}))

    def test_profile_bound_once_claimed_get_returns_bounded_response_and_fresh_cf_cookies(self):
        thread,values=self.start_request()
        self.assertIsNone(rpc.poll('edge',OTHER));self.assertIsNone(rpc.poll('chrome',PROFILE))
        job=rpc.poll('edge',PROFILE);self.assertEqual(job['expected_handle'],'MyHandle')
        self.assertIsNone(rpc.poll('edge',PROFILE))
        rpc.complete('edge',OTHER,job['id'],self.result());self.assertTrue(thread.is_alive())
        rpc.complete('edge',PROFILE,job['id'],self.result());thread.join(2)
        response=values['response'];self.assertEqual(response.status_code,200);self.assertTrue(response.ok)
        self.assertIn('Fixture',response.text);self.assertEqual(response.cookies.get('39ce7'),'fresh-fixture')
        self.assertNotIn('must-not-return',repr(response));self.assertNotIn('Set-Cookie',response.headers)
        self.assertFalse(list(rpc._identity('edge',PROFILE).glob('*.json')))

    def test_post_claim_times_out_with_unknown_outcome_and_is_never_replayed(self):
        thread,values=self.start_request('POST',data={'submittedProblemIndex':'H','source':'fixture-code'},timeout=1)
        job=rpc.poll('edge',PROFILE);self.assertEqual(job['data']['source'],['fixture-code'])
        self.assertEqual((rpc._identity('edge',PROFILE)/(job['id']+'.json')).stat().st_mode&0o777,0o600)
        thread.join(2);self.assertIsInstance(values['error'],rpc.BrowserRPCError);self.assertTrue(values['error'].outcome_unknown)
        self.assertIsNone(rpc.poll('edge',PROFILE))
        self.assertNotIn('fixture-code',''.join(p.read_text() for p in rpc._identity('edge',PROFILE).glob('*.json')))

    def test_no_tab_account_mismatch_and_verification_are_safe_prepost_errors(self):
        for code in ('no-tab','account-mismatch','verification'):
            thread,values=self.start_request('POST',data={'submittedProblemIndex':'H','source':'secret-fixture'})
            job=rpc.poll('edge',PROFILE);rpc.complete('edge',PROFILE,job['id'],{'error':code,'sent':False})
            thread.join(2);self.assertFalse(values['error'].outcome_unknown);self.assertNotIn('secret-fixture',str(values['error']))

    def test_arbitrary_hosts_methods_apis_fields_and_conflicting_form_are_rejected(self):
        for method,url in [('GET','https://evil.test/'),('GET','https://codeforces.com/api/user.info'),('POST','https://codeforces.com/contestRegistration/123/virtual/true'),('PUT',URL),('GET',URL+'?arbitrary=1')]:
            with self.subTest(url=url),self.assertRaises(rpc.BrowserRPCError):rpc.request(self.account,method,url)
        for data in ({'script':'arbitraryJS'},{'contestId':'999','submittedProblemIndex':'H'},{'submittedProblemIndex':'A'},{'source':'x'*rpc.MAX_REQUEST}):
            with self.assertRaises(rpc.BrowserRPCError):rpc.request(self.account,'POST',URL,data=data)
        with self.assertRaises(rpc.BrowserRPCError):rpc.request(self.account,'POST',URL,files={'sourceFile':('../private.cpp','code')})
        with self.assertRaises(rpc.BrowserRPCError):rpc.request(self.account,'POST',URL,files={'sourceFile':('a.cpp',b'\xff')})
        self.assertFalse(list((state_directory()/'browser-rpc').glob('**/*.json')))

    def test_anonymous_get_discovery_is_allowed_but_anonymous_post_never_enqueues(self):
        self.account['handle']=''
        thread,values=self.start_request('GET','https://codeforces.com/?locale=en')
        job=rpc.poll('edge',PROFILE);rpc.complete('edge',PROFILE,job['id'],self.result(url='https://codeforces.com/'))
        thread.join(2);self.assertEqual(values['response'].status_code,200)
        with self.assertRaises(rpc.BrowserRPCUnavailable):rpc.request(self.account,'POST',URL,data={})

    def test_native_unicode_roundtrip_is_utf8_and_exact_frame_size_is_bounded(self):
        payload={'text':'汉'*200000}
        stream=io.BytesIO();bridge.send_message(stream,payload)
        self.assertLess(len(stream.getvalue()),1024*1024)
        stream.seek(0);self.assertEqual(bridge.read_message(stream),payload)
        with self.assertRaisesRegex(ValueError,'supported size'):
            bridge.send_message(io.BytesIO(),{'text':'x'*(1024*1024)})

    def native_input(self,messages):
        stream=io.BytesIO()
        for message in messages:bridge.send_message(stream,message)
        stream.seek(0)
        return stream

    def test_native_port_uses_first_snapshot_identity_and_ignores_poll_supplied_profile(self):
        from vp_helper.chromium_login import extension_id
        messages=[{'type':'snapshot','source':'edge','profile_id':PROFILE,'cookies':[]},
                  {'type':'cf-request-poll','source':'chrome','profile_id':OTHER},
                  {'type':'cf-request-result','source':'chrome','profile_id':OTHER,'id':'a'*32,'result':{'error':'no-tab'}}]
        with patch.object(bridge.sys,'argv',['host','chrome-extension://'+extension_id()+'/']), \
             patch.object(bridge.sys,'stdin',SimpleNamespace(buffer=self.native_input(messages))), \
             patch.object(bridge.sys,'stdout',SimpleNamespace(buffer=io.BytesIO())), \
             patch.object(rpc,'poll',return_value=None) as poll,patch.object(rpc,'complete') as complete:
            bridge.main()
        poll.assert_called_once_with('edge',PROFILE)
        complete.assert_called_once_with('edge',PROFILE,'a'*32,{'error':'no-tab'})

    def test_persistent_native_port_cannot_switch_profile_or_poll_before_snapshot(self):
        from vp_helper.chromium_login import extension_id
        for messages in ([{'type':'cf-request-poll'}],
                         [{'type':'snapshot','source':'edge','profile_id':PROFILE,'cookies':[]},
                          {'type':'snapshot','source':'chrome','profile_id':OTHER,'cookies':[]}]):
            with patch.object(bridge.sys,'argv',['host','chrome-extension://'+extension_id()+'/']), \
                 patch.object(bridge.sys,'stdin',SimpleNamespace(buffer=self.native_input(messages))), \
                 patch.object(bridge.sys,'stdout',SimpleNamespace(buffer=io.BytesIO())),self.assertRaises(SystemExit):
                bridge.main()
        self.assertFalse((state_directory()/'browser-login/chrome'/(OTHER+'.json')).exists())


if __name__=='__main__':unittest.main()
