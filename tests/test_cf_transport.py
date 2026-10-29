from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup

try:
    from curl_cffi.const import CurlHttpVersion
    from curl_cffi.requests.exceptions import SSLError
except ImportError:
    CurlHttpVersion=None
    from requests.exceptions import SSLError

from vp_helper.gym import Gym
from vp_helper.http import WebSession
from vp_helper.models import Contest, Rules, VPError


URL='https://codeforces.com/contest/2181'


class CFTransportTests(unittest.TestCase):
    def response(self,status=200,data=None):
        return Mock(status_code=status,url='https://codeforces.com/api/contest.standings',
                    headers={'Content-Type':'application/json'},json=Mock(return_value=data or {}))

    @unittest.skipUnless(CurlHttpVersion is not None,'requires the installed curl_cffi transport')
    def test_ssl_get_falls_back_to_http1_without_disabling_certificate_checks(self):
        web=WebSession('https://codeforces.com',{},auto_refresh=False)
        web.session=Mock()
        response=self.response()
        web.session.get.side_effect=[SSLError('fixture TLS negotiation'),response,response]
        with patch('vp_helper.http.time.sleep') as sleep:
            self.assertIs(web.get(response.url,retry=False),response)
        sleep.assert_called_once_with(2.1)
        self.assertTrue(web._http1_only)
        self.assertEqual(web.session.get.call_args_list[1].kwargs['http_version'],CurlHttpVersion.V1_1)
        self.assertNotIn('verify',web.session.get.call_args_list[1].kwargs)
        web.get(response.url,retry=False)
        self.assertEqual(web.session.get.call_args.kwargs['http_version'],CurlHttpVersion.V1_1)

    def test_http400_api_json_is_available_to_api_error_handler(self):
        web=WebSession('https://codeforces.com',{},auto_refresh=False)
        response=self.response(400,{'status':'FAILED','comment':'fixture apiKey rejected'})
        self.assertIs(web.check(response),response)
        response.url=URL
        with self.assertRaisesRegex(VPError,'HTTP 400'):
            web.check(response)

    @unittest.skipUnless(CurlHttpVersion is not None,'requires the installed curl_cffi transport')
    def test_post_is_never_resent_after_ssl_failure(self):
        web=WebSession('https://codeforces.com',{},auto_refresh=False)
        web.session=Mock()
        web._http1_only=True
        web.session.post.side_effect=SSLError('fixture TLS failure')
        with self.assertRaises(VPError):
            web.post(URL,{'csrf_token':'fixture'})
        web.session.post.assert_called_once()
        self.assertEqual(web.session.post.call_args.kwargs['http_version'],CurlHttpVersion.V1_1)


class CFVirtualConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.client=Gym(URL,{'handle':'tester'})
        self.client._native_duration=18000
        self.client.api=Mock(return_value=[])
        self.client.web=Mock()
        self.server=datetime(2026,10,29,12,0,tzinfo=timezone.utc).timestamp()

    def page(self,phase,value,handle='tester',date='Thu, 29 Oct 2026 12:00:00 GMT'):
        self.client.web.get.return_value=Mock(
            text=f'<div id="header"><a href="/profile/{handle}">me</a></div>'
                 f'<span class="contest-state-regular countdown before-contest-2181-{phase}">{value}</span>',
            headers={'Date':date})

    def test_future_vp_without_submissions_is_verified_from_native_server_countdown(self):
        self.page('start','00:30:00')
        with patch('time.time',return_value=1000):
            party=self.client._virtual_party()
        self.assertEqual(party['startTimeSeconds'],self.server+1800)
        self.client.api.assert_called_once_with('contest.status',contestId='2181',handle='tester',**{'from':1,'count':100})

    def test_native_registration_sets_future_time_and_verifies_server_countdown(self):
        header='<div id="header"><a href="/profile/tester">me</a></div>'
        form='<form id="registerForm" action="/contestRegistration/2181/virtual/true">' \
             '<input name="csrf_token" value="fixture"><input name="startTime" value="Oct/29/2026 20:00">' \
             '<input name="timezoneOffset" value="-480"></form>'
        self.client.web.soup.side_effect=[BeautifulSoup(header,'html.parser'),BeautifulSoup(form,'html.parser')]
        posted=[]
        def get(*args,**kwargs):
            countdown='<span class="contest-state-regular countdown before-contest-2181-start">00:30:00</span>' if posted else ''
            return Mock(text=header+countdown,headers={'Date':'Thu, 29 Oct 2026 12:00:00 GMT'})
        def post(*args,**kwargs):
            posted.append(True)
            return Mock(content='<div>registered</div>')
        self.client.web.get.side_effect=get
        self.client.web.post.side_effect=post
        contest=Contest('cf','2181',URL,'Native CF fixture','Native CF fixture',{'A':'A'},Rules(18000))
        self.assertEqual(self.client.start(contest,requested_start=self.server+1800,fresh=True),self.server+1800)
        self.client.web.post.assert_called_once()
        self.assertEqual(self.client.web.post.call_args.args[1]['startTime'],'Oct/29/2026 20:30')
        self.assertTrue(all(call.args[0]=='contest.status' for call in self.client.api.call_args_list))

    def test_active_vp_native_finish_countdown_uses_server_time_and_duration(self):
        self.page('finish','04:00:00')
        party=self.client._virtual_party()
        self.assertEqual(party['startTimeSeconds'],self.server-3600)

    def test_missing_server_date_never_uses_local_time_as_native_confirmation(self):
        self.page('start','00:30:00',date='')
        self.assertIsNone(self.client._virtual_party())

    def test_other_account_page_cannot_confirm_this_vp(self):
        self.page('start','00:30:00',handle='other')
        with self.assertRaises(VPError):
            self.client._virtual_party()
