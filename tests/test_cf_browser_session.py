from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from requests.cookies import RequestsCookieJar
from robobrowser import RoboBrowser

from vp_helper import browser_rpc
from vp_helper.browser_session import BrowserSession, BrowserRoutingSession
from vp_helper.http import WebSession
from vp_helper.models import VPError
from vp_helper.storage import state_directory, write_json


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://codeforces.com/contest/2275'
HEADER = '<div id="header"><a href="/profile/tester">tester</a></div>'
FORM = HEADER + '''<form class="submit-form" method="post" action="?csrf_token=fixture-csrf">
<input name="csrf_token" value="fixture-csrf"><input name="ftaa" value="fixture-ftaa">
<input name="bfaa" value="fixture-bfaa"><input name="action" value="submitSolutionFormSubmitted">
<select name="submittedProblemIndex"><option value="B" selected>B</option></select>
<select name="programTypeId"><option value="73" selected>GNU G++20</option></select>
<input type="file" name="sourceFile"><textarea name="source"></textarea></form>'''


def response(url, text=HEADER, *, status=200, cookies=None):
    return browser_rpc.BrowserResponse(status, url, text, {'Content-Type': 'text/html'},
                                       cookies or RequestsCookieJar())


def native(name):
    spec = importlib.util.spec_from_file_location('fixture_browser_' + name, ROOT / 'third_party/cf_submit' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict('sys.modules', {'colours': Mock()}):
        spec.loader.exec_module(module)
    return module


class CFBrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {
            'XDG_CONFIG_HOME': str(self.directory / 'config'), 'XDG_STATE_HOME': str(self.directory / 'state')}))
        self.account = {'handle': 'tester', 'cookie_source': 'chrome', 'cookie': 'JSESSIONID=fixture-old',
                        'browser_profile': str(state_directory() / 'browser-login/chrome' / ('a' * 32 + '.json')),
                        'browser_host': 'codeforces.com', 'user_agent': 'fixture browser UA',
                        'api_key': 'never-send-api-key', 'api_secret': 'never-send-api-secret'}

    def test_real_robobrowser_open_default_none_timeout_uses_actual_rpc_queue(self):
        for source in ('chrome', 'edge'):
            with self.subTest(source=source):
                profile = state_directory() / 'browser-login' / source / ('a' * 32 + '.json')
                write_json(profile, {'source': source, 'profile_id': profile.stem,
                                     'capabilities': [browser_rpc.CAPABILITY], 'updated_at': time.time()})
                account = {**self.account, 'cookie_source': source, 'browser_profile': str(profile)}
                browser = RoboBrowser(parser='lxml', session=BrowserSession(account))
                errors = []
                def open_page():
                    try:
                        browser.open('https://codeforces.com/')
                    except Exception as exc:
                        errors.append(exc)
                worker = threading.Thread(target=open_page, daemon=True)
                worker.start()
                job = None
                until = time.monotonic() + 2
                while job is None and worker.is_alive() and time.monotonic() < until:
                    job = browser_rpc.poll(source, profile.stem)
                    if job is None:
                        time.sleep(.01)
                self.assertEqual(errors, [])
                self.assertIsNotNone(job)
                self.assertEqual(job['method'], 'GET')
                self.assertAlmostEqual(job['expires_at'] - job['created_at'], 45, delta=.1)
                self.assertFalse(job['data']); self.assertIsNone(job['file'])
                browser_rpc.complete(source, profile.stem, job['id'], {
                    'status': 200, 'url': 'https://codeforces.com/', 'text': HEADER,
                    'headers': {'Content-Type': 'text/html'}})
                worker.join(2)
                self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
                self.assertEqual(browser.url, 'https://codeforces.com/')
                self.assertEqual(browser.parsed.select_one('#header a').get_text(), 'tester')
                self.assertIsNone(browser_rpc.poll(source, profile.stem))

    def test_html_uses_browser_from_first_get_and_api_remains_on_raw_transport(self):
        web = WebSession('https://codeforces.com', self.account)
        self.addCleanup(web.session.close)
        self.assertIsInstance(web.session, BrowserRoutingSession)
        html = response(URL + '/submit')
        api = response('https://codeforces.com/api/user.status', '{"status":"OK","result":[]}')
        with patch('vp_helper.browser_rpc.request', return_value=html) as rpc, \
             patch.object(web.session.raw, 'request', return_value=api) as raw, \
             patch.object(web, '_refresh_browser') as refresh:
            self.assertIs(web.get(URL + '/submit', params={'locale': 'en'}), html)
            self.assertIs(web.get(api.url, params={'apiKey': 'never-send-api-key', 'apiSig': 'fixture-signature'}), api)
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(rpc.call_args.args[2], URL + '/submit?locale=en')
        self.assertNotIn('apiKey', str(rpc.call_args.kwargs))
        raw.assert_called_once()
        self.assertIn('apiKey', raw.call_args.kwargs['params'])
        refresh.assert_not_called()

    def test_browser_get_challenge_is_not_refreshed_or_retried_through_curl(self):
        web = WebSession('https://codeforces.com', self.account, auto_refresh=False)
        self.addCleanup(web.session.close)
        challenge = response(URL + '/submit', '<title>Just a moment...</title>', status=403)
        with patch('vp_helper.browser_rpc.request', return_value=challenge) as rpc, \
             patch.object(web.session.raw, 'request') as raw, \
             patch.object(web, '_refresh_browser') as refresh:
            with self.assertRaisesRegex(VPError, 'browser verification'):
                web.get(URL + '/submit')
        rpc.assert_called_once(); raw.assert_not_called(); refresh.assert_not_called()

    def test_invalid_browser_post_never_falls_back_to_raw_transport(self):
        web = WebSession('https://codeforces.com', self.account, auto_refresh=False)
        self.addCleanup(web.session.close)
        with patch('vp_helper.browser_rpc.request') as rpc, patch.object(web.session.raw, 'request') as raw:
            for target in ['https://other.example/submit', URL + '/api/submit', URL + '/submit?apiKey=secret']:
                with self.assertRaises(browser_rpc.BrowserRPCError):
                    web.session.post(target, data={'source': 'sensitive code'})
        rpc.assert_not_called(); raw.assert_not_called()

    def test_native_session_selects_browser_and_old_extension_fails_before_upload(self):
        module = native('cf_login')
        for source in ('chrome', 'edge'):
            with self.subTest(source=source), patch('vp_helper.browser_rpc.available', return_value=True):
                session = module.make_session(account={**self.account, 'cookie_source': source})
                self.assertIsInstance(session, BrowserSession)
        with patch('vp_helper.browser_rpc.available', return_value=False), patch('vp_helper.browser_rpc.request') as rpc:
            with self.assertRaisesRegex(browser_rpc.BrowserRPCUnavailable, 'reload.*extension'):
                module.make_session(account=self.account)
        rpc.assert_not_called()

    def test_real_robobrowser_file_form_uses_one_browser_post_after_confirmation(self):
        source = self.directory / 'B.cpp'; source.write_text('// 中文\nint main() {}\n', encoding='utf8')
        session = BrowserSession(self.account)
        browser = RoboBrowser(parser='lxml', session=session)
        posts = []
        def request(account, method, url, **options):
            if method == 'POST':
                posts.append((url, options))
                return response(URL + '/my')
            return response(url, FORM)
        module = native('cf_submit')
        previous = Path.cwd(); os.chdir(self.directory); self.addCleanup(os.chdir, previous)
        with patch('vp_helper.browser_rpc.request', side_effect=request), \
             patch('builtins.input', return_value='y'), patch('sys.stdout', io.StringIO()) as output:
            browser.open(URL + '/submit/B')
            self.assertTrue(module.submit_problem(browser, '2275', 'cpp', str(source), -1, expected_problem='B'))
        self.assertEqual(len(posts), 1)
        target, options = posts[0]
        self.assertEqual(target, URL + '/submit/B?csrf_token=fixture-csrf')
        self.assertEqual(options['files'], {'sourceFile': ('B.cpp', source.read_bytes())})
        self.assertEqual(options['data']['csrf_token'], 'fixture-csrf')
        self.assertEqual(options['data']['submittedProblemIndex'], 'B')
        self.assertNotIn('headers', options)
        self.assertNotIn('fixture-csrf', output.getvalue())

    def test_native_browser_post_timeout_prints_judging_link_without_retry(self):
        source = self.directory / 'B.cpp'; source.write_text('int main() {}\n')
        browser = RoboBrowser(parser='lxml', session=BrowserSession(self.account))
        module = native('cf_submit')
        previous = Path.cwd(); os.chdir(self.directory); self.addCleanup(os.chdir, previous)
        def request(account, method, url, **options):
            if method == 'POST':
                raise browser_rpc.BrowserRPCError('fixture transport timeout', outcome_unknown=True)
            return response(url, FORM)
        with patch('vp_helper.browser_rpc.request', side_effect=request) as rpc, \
             patch('builtins.input', return_value='y'), patch('sys.stdout', io.StringIO()) as output:
            browser.open(URL + '/submit/B')
            with self.assertRaisesRegex(VPError, 'outcome unknown.*not repeated'):
                module.submit_problem(browser, '2275', 'cpp', str(source), -1, expected_problem='B')
        self.assertEqual([call.args[1] for call in rpc.call_args_list], ['GET', 'POST'])
        self.assertIn(URL + '/my', output.getvalue())

    def test_account_switch_before_browser_post_preserves_safe_error_and_does_not_retry(self):
        source = self.directory / 'B.cpp'; source.write_text('int main() {}\n')
        browser = RoboBrowser(parser='lxml', session=BrowserSession(self.account))
        module = native('cf_submit')
        previous = Path.cwd(); os.chdir(self.directory); self.addCleanup(os.chdir, previous)
        def request(account, method, url, **options):
            if method == 'POST':
                raise browser_rpc.BrowserRPCError('The ordinary Codeforces tab is logged into another account. No code was submitted.')
            return response(url, FORM)
        with patch('vp_helper.browser_rpc.request', side_effect=request) as rpc, \
             patch('builtins.input', return_value='y'), patch('sys.stdout', io.StringIO()) as output:
            browser.open(URL + '/submit/B')
            with self.assertRaisesRegex(VPError, 'another account. No code was submitted'):
                module.submit_problem(browser, '2275', 'cpp', str(source), -1, expected_problem='B')
        self.assertEqual([call.args[1] for call in rpc.call_args_list], ['GET', 'POST'])
        self.assertIn(URL + '/my', output.getvalue())

    def test_cf_verification_supplies_profile_and_target_metadata(self):
        from vp_helper import browser_login
        candidate = browser_login.BrowserCookies(Path(self.account['browser_profile']), 'JSESSIONID=fixture', 'chrome', 'fixture UA', 'www.codeforces.com')
        with patch('vp_helper.browser_rpc.request', return_value=response('https://www.codeforces.com/')) as rpc:
            verified = browser_login._verify('cf', candidate, 'tester')
        self.assertEqual(verified['browser_profile'], str(candidate.profile))
        credentials = rpc.call_args.args[0]
        self.assertEqual(credentials['browser_host'], 'www.codeforces.com')
        self.assertEqual(credentials['handle'], 'tester')
        with patch('vp_helper.browser_rpc.request', side_effect=browser_rpc.BrowserRPCUnavailable('Reload the updated extension')):
            with self.assertRaisesRegex(browser_rpc.BrowserRPCUnavailable, 'Reload'):
                browser_login._verify('cf', candidate, 'tester')

    def test_local_queue_interop_preserves_file_and_claims_post_once(self):
        profile = Path(self.account['browser_profile'])
        write_json(profile, {'source': 'chrome', 'profile_id': profile.stem,
                             'capabilities': [browser_rpc.CAPABILITY], 'updated_at': time.time()})
        source = b'// local protocol fixture\nint main() {}\n'
        result = []; errors = []
        def send():
            try:
                result.append(BrowserSession(self.account).post(URL + '/submit/B',
                    data=[('csrf_token', 'fixture-csrf'), ('submittedProblemIndex', 'B')],
                    files=[('sourceFile', ('B.cpp', io.BytesIO(source)))], timeout=3))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=send); worker.start()
        job = None
        deadline = time.monotonic() + 2
        while job is None and time.monotonic() < deadline:
            job = browser_rpc.poll('chrome', profile.stem)
            if job is None: time.sleep(.01)
        self.assertIsNotNone(job)
        self.assertEqual(job['file'], {'name': 'B.cpp', 'content': source.decode()})
        self.assertEqual(job['data']['submittedProblemIndex'], ['B'])
        self.assertNotIn('api_key', job); self.assertNotIn('api_secret', job)
        self.assertIsNone(browser_rpc.poll('chrome', profile.stem))
        browser_rpc.complete('chrome', profile.stem, job['id'], {
            'status': 200, 'url': URL + '/my', 'text': HEADER, 'headers': {'Content-Type': 'text/html'}})
        worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(len(result), 1); self.assertEqual(result[0].url, URL + '/my')
        self.assertIsNone(browser_rpc.poll('chrome', profile.stem))


if __name__ == '__main__': unittest.main()
