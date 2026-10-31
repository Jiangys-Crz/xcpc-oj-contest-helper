"""Offline native cookie/form rotation across consecutive judge submissions."""
from contextlib import ExitStack
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from requests.cookies import RequestsCookieJar

from vp_helper.http import WebSession, _RequestException
from vp_helper.models import VPError
from vp_helper.session_cookies import honor_judge_response_cookies, import_cookie_header


def response(url, *, body='', status=200, cookies=(), location=None, data=None):
    result = SimpleNamespace(url=url, status_code=status, text=body, history=[],
                             headers={'Content-Type': 'text/html'}, cookies=RequestsCookieJar())
    if location is not None:
        result.headers['Location'] = location
    for name, value, domain, path in cookies:
        result.cookies.set(name, value, domain=domain, path=path)
    result.json = Mock(return_value=data) if data is not None else Mock(side_effect=ValueError())
    return result


class OfflineSession:
    """Use a real requests cookie jar/preparer; replace only the wire exchange."""
    def __init__(self, router):
        self.headers = {}
        self.cookies = RequestsCookieJar()
        self.router = router
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        prepared = requests.Request(method, url, headers=self.headers, cookies=self.cookies).prepare()
        self.calls.append((method, url, prepared.headers.get('Cookie', ''), kwargs))
        reply = self.router(method, url, prepared.headers.get('Cookie', ''), kwargs)
        self.cookies.update(reply.cookies)  # Native transports do this before returning.
        return reply

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def close(self):
        self.closed = True


class JudgeCookieTests(unittest.TestCase):
    def test_requests_and_curl_cookie_containers_preserve_native_rotation(self):
        from curl_cffi.requests import Cookies
        for container in (RequestsCookieJar(), Cookies()):
            with self.subTest(container=type(container).__name__):
                session = SimpleNamespace(cookies=container)
                import_cookie_header(session, 'https://qoj.ac', 'sid=imported')
                update = response('https://qoj.ac/submit', cookies=[('sid', 'fresh', '.qoj.ac', '/')])
                update.cookies = SimpleNamespace(jar=update.cookies)
                honor_judge_response_cookies(session, update)
                jar = getattr(container, 'jar', container)
                self.assertEqual([(c.name, c.value) for c in jar], [('sid', 'fresh')])

    def test_imported_snapshots_rotate_once_without_stale_cookie_headers(self):
        for host, domain in [('qoj.ac', '.qoj.ac'), ('contest.ucup.ac', '.contest.ucup.ac'),
                             ('oj.cwnupaa.com', '.cwnupaa.com'), ('ac.nowcoder.com', '.nowcoder.com')]:
            with self.subTest(host=host):
                session = OfflineSession(None)
                import_cookie_header(session, 'https://' + host, 'sid=fixture-import; keep=fixture-keep')
                session.cookies.set('unrelated', 'fixture-private', domain='other.example', path='/')
                for number in range(2):
                    reply = response('https://' + host + '/submit', cookies=[('sid', f'fresh-{number}', domain, '/')])
                    session.cookies.update(reply.cookies)
                    honor_judge_response_cookies(session, reply)
                    header = requests.Request('POST', 'https://' + host + '/submit', cookies=session.cookies).prepare().headers['Cookie']
                    self.assertEqual(header.count('sid='), 1)
                    self.assertIn('sid=fresh-' + str(number), header)
                    self.assertNotIn('fixture-import', header)
                    self.assertIn('keep=fixture-keep', header)
                    self.assertNotIn('fixture-private', header)
                self.assertNotIn('Cookie', session.headers)

    def test_native_subdomain_and_path_cookies_are_not_merged_with_parent_updates(self):
        session = OfflineSession(None)
        import_cookie_header(session, 'https://ac.nowcoder.com', 'sid=imported')
        # A native host-only cookie has provenance distinct from our import.
        session.cookies.set('sid', 'native-host', domain='ac.nowcoder.com', path='/')
        session.cookies.set('sid', 'other-path', domain='.nowcoder.com', path='/other')
        session.cookies.set('sid', 'other-subdomain', domain='www.nowcoder.com', path='/')
        reply = response('https://ac.nowcoder.com/submit', cookies=[('sid', 'native-parent', '.nowcoder.com', '/')])
        honor_judge_response_cookies(session, reply)
        values = {(c.domain, c.path): c.value for c in session.cookies if c.name == 'sid'}
        self.assertEqual(values, {('ac.nowcoder.com', '/'): 'native-host', ('.nowcoder.com', '/other'): 'other-path',
                                  ('www.nowcoder.com', '/'): 'other-subdomain', ('.nowcoder.com', '/'): 'native-parent'})
        foreign = response('https://other.example/', cookies=[('sid', 'bad', '.nowcoder.com', '/')])
        honor_judge_response_cookies(session, foreign)
        self.assertNotIn('bad', [c.value for c in session.cookies])

    def test_www_redirect_parent_rotation_and_expiry_replace_only_the_recorded_import(self):
        for host, domains in [('qoj.ac', ['.qoj.ac']),
                              ('oj.cwnupaa.com', ['.oj.cwnupaa.com', '.cwnupaa.com']),
                              ('ac.nowcoder.com', ['.ac.nowcoder.com', '.nowcoder.com'])]:
            for domain in domains:
                for expired in (False, True):
                    with self.subTest(host=host, domain=domain, expired=expired):
                        session = OfflineSession(None)
                        import_cookie_header(session, 'https://' + host, 'sid=imported')
                        session.cookies.set('sid', 'keep-path', domain=host, path='/other')
                        update = response('https://www.' + host + '/submit',
                                          cookies=[('sid', 'fresh', domain, '/')])
                        update.history = [response('https://' + host + '/submit')]
                        if expired:
                            for cookie in update.cookies: cookie.expires = 1
                        session.cookies.update(update.cookies)
                        honor_judge_response_cookies(session, update)
                        header = requests.Request('POST', 'https://' + host + '/submit', cookies=session.cookies).prepare().headers.get('Cookie', '')
                        self.assertNotIn('imported', header)
                        self.assertEqual(header.count('sid='), 0 if expired else 1)
                        self.assertIn('keep-path', [c.value for c in session.cookies])

    def test_www_exclusive_update_or_deletion_cannot_replace_the_root_import(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                session = OfflineSession(None)
                import_cookie_header(session, 'https://oj.cwnupaa.com', 'sid=imported')
                update = response('https://www.oj.cwnupaa.com/submit',
                                  cookies=[('sid', 'www-only', 'www.oj.cwnupaa.com', '/')])
                if expired:
                    for cookie in update.cookies: cookie.expires = 1
                session.cookies.update(update.cookies)
                honor_judge_response_cookies(session, update)
                header = requests.Request('POST', 'https://oj.cwnupaa.com/submit', cookies=session.cookies).prepare().headers['Cookie']
                self.assertEqual(header, 'sid=imported')
                self.assertIn(('oj.cwnupaa.com', '/', 'sid'), session._xcpc_imported_cookies)

    def test_expiry_and_other_judge_domains_cannot_revive_or_replace_login(self):
        session = OfflineSession(None)
        import_cookie_header(session, 'https://qoj.ac', 'sid=imported')
        reply = response('https://qoj.ac/submit', cookies=[('sid', '', '.qoj.ac', '/')])
        for cookie in reply.cookies:
            cookie.expires = 1
        honor_judge_response_cookies(session, reply)
        self.assertEqual(list(session.cookies), [])
        reply = response('https://qoj.ac/submit', cookies=[('sid', 'bad', '.contest.ucup.ac', '/')])
        honor_judge_response_cookies(session, reply)
        self.assertEqual(list(session.cookies), [])

    def test_get_retry_receives_rotation_before_next_idempotent_request(self):
        count = 0
        def router(method, url, cookie, options):
            nonlocal count
            count += 1
            if count == 1:
                return response(url, status=503, cookies=[('sid', 'fresh', '.nowcoder.com', '/')])
            self.assertEqual(cookie.count('sid='), 1)
            self.assertIn('sid=fresh', cookie)
            return response(url)
        backend = OfflineSession(router)
        with patch('vp_helper.http._requests.Session', return_value=backend), patch('vp_helper.http.time.sleep'):
            web = WebSession('https://ac.nowcoder.com', {'cookie': 'sid=old'}, auto_refresh=False)
            self.assertEqual(web.get('https://ac.nowcoder.com/submit').status_code, 200)
        self.assertEqual([call[0] for call in backend.calls], ['GET', 'GET'])

    def test_login_refresh_followed_by_transient_status_also_rotates_before_retry(self):
        count = 0
        def router(method, url, cookie, options):
            nonlocal count
            count += 1
            if count == 1:
                return response('https://qoj.ac/login')
            if count == 2:
                return response(url, status=503, cookies=[('sid', 'after-refresh', '.qoj.ac', '/')])
            self.assertEqual(cookie.count('sid='), 1)
            self.assertIn('sid=after-refresh', cookie)
            return response(url)
        backend = OfflineSession(router)
        with patch('vp_helper.http._requests.Session', return_value=backend), patch('vp_helper.http.time.sleep'):
            web = WebSession('https://qoj.ac', {'cookie': 'sid=old'}, auto_refresh=False)
            web._browser_checked = True
            with patch.object(web, '_refresh_browser', return_value=True) as refresh:
                self.assertEqual(web.get('https://qoj.ac/submit').status_code, 200)
            refresh.assert_called_once_with(force=True)
        self.assertEqual(len(backend.calls), 3)


class WwwBrowserRefreshTests(unittest.TestCase):
    hosts = [('qoj.ac', 'qoj'), ('oj.cwnupaa.com', 'cwnuoj'),
             ('ac.nowcoder.com', 'nowcoder'), ('codeforces.com', 'cf')]

    def test_www_auto_refresh_uses_correct_platform_and_actual_browser_target(self):
        for host, platform in self.hosts:
            with self.subTest(host=host):
                actual = 'www.' + host
                backend = OfflineSession(None)
                account = {'handle': 'fixture', 'cookie': 'sid=old', 'cookie_source': 'chrome', 'browser_host': host}
                def verify(kind, selected, *, force=False):
                    self.assertEqual(kind, platform); self.assertEqual(selected['_browser_target'], actual)
                    selected.update(cookie='sid=refreshed', browser_host=actual)
                    return True
                with patch('vp_helper.http._requests.Session', return_value=backend), \
                     patch('vp_helper.browser_login.refresh_browser_account', side_effect=verify) as refresh:
                    web = WebSession('https://' + actual, account)
                    self.assertTrue(web._auto_refresh); self.assertTrue(web._refresh_browser(force=True))
                self.assertEqual(refresh.call_count, 1)
                self.assertNotIn('_browser_target', account)
                self.assertEqual([c.value for c in backend.cookies], ['refreshed'])

    def test_www_constructor_retargets_cross_browser_snapshots_using_platform_alias(self):
        for host, platform in self.hosts:
            with self.subTest(host=host):
                actual = 'www.' + host
                backend = OfflineSession(None)
                account = {'handle': 'fixture', 'cookie': 'sid=old', 'cookie_source': 'edge',
                           'browser_host': 'qoj.ac' if host != 'qoj.ac' else 'codeforces.com'}
                def verify(kind, selected):
                    self.assertEqual(kind, platform); self.assertEqual(selected['_browser_target'], actual)
                    selected.update(cookie='sid=correct-site', browser_host=actual)
                    return True
                with patch('vp_helper.http._requests.Session', return_value=backend), \
                     patch('vp_helper.browser_login.refresh_browser_account', side_effect=verify) as refresh:
                    WebSession('https://' + actual, account)
                refresh.assert_called_once()
                self.assertEqual([c.value for c in backend.cookies], ['correct-site'])

    def test_www_login_redirect_refreshes_once_before_returning_the_current_form(self):
        for host, platform in self.hosts:
            with self.subTest(host=host):
                actual = 'www.' + host
                url = 'https://' + actual + '/problem'
                count = 0
                def router(method, destination, cookie, options):
                    nonlocal count
                    count += 1
                    self.assertEqual(method, 'GET')
                    if count == 1:
                        self.assertIn('sid=verified', cookie)
                        return response('https://' + actual + '/login')
                    self.assertIn('sid=after-login', cookie); self.assertEqual(cookie.count('sid='), 1)
                    return response(destination, body='<form><input name="_token" value="current-form"></form>')
                backend = OfflineSession(router)
                account = {'handle': 'fixture', 'cookie': 'sid=old', 'cookie_source': 'chrome', 'browser_host': actual}
                calls = []
                def verify(kind, selected, *, force=False):
                    self.assertEqual(kind, platform); self.assertEqual(selected['_browser_target'], actual)
                    calls.append(force); selected['cookie'] = 'sid=after-login' if force else 'sid=verified'
                    return True
                with patch('vp_helper.http._requests.Session', return_value=backend), \
                     patch('vp_helper.browser_login.refresh_browser_account', side_effect=verify):
                    web = WebSession('https://' + actual, account)
                    form = web.soup(url)
                self.assertEqual(form.select_one('[name="_token"]')['value'], 'current-form')
                self.assertEqual(calls, [False, True]); self.assertEqual(len(backend.calls), 2)

    def test_external_urls_and_foreign_login_redirects_do_not_refresh_bound_credentials(self):
        for requested, redirected in [('https://other.example/problem', 'https://other.example/login'),
                                       ('https://www.qoj.ac/problem', 'https://other.example/login')]:
            with self.subTest(requested=requested):
                backend = OfflineSession(lambda method, url, cookie, options: response(redirected))
                account = {'handle':'fixture','cookie':'sid=qoj-only','cookie_source':'chrome','browser_host':'www.qoj.ac'}
                with patch('vp_helper.http._requests.Session', return_value=backend), \
                     patch('vp_helper.browser_login.refresh_browser_account') as refresh:
                    web = WebSession('https://www.qoj.ac', account)
                    if requested.startswith('https://www.qoj.ac'):
                        web._browser_checked = True
                    with self.assertRaisesRegex(VPError, 'expired'):
                        web.get(requested)
                refresh.assert_not_called()
                if requested.startswith('https://other.example'):
                    self.assertEqual(backend.calls[0][2], '')
        with patch('vp_helper.browser_login.refresh_browser_account') as refresh:
            web = WebSession('https://www.other.example', {'handle':'fixture','cookie_source':'chrome','browser_host':'qoj.ac'})
            self.addCleanup(web.session.close)
            self.assertFalse(web._auto_refresh)
        refresh.assert_not_called()

    def test_ucup_www_keeps_native_browser_only_auto_refresh_policy(self):
        for source, expected in [('firefox', False), ('chrome', True), ('edge', True)]:
            with self.subTest(source=source):
                web = WebSession('https://www.contest.ucup.ac', {'handle':'fixture','cookie_source':source})
                self.addCleanup(web.session.close)
                self.assertEqual(web._auto_refresh, expected)


class QojContinuousSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config'), 'XDG_STATE_HOME': str(self.root/'state')}))
        self.config = {'name': 'Synthetic contest', 'platform': 'qoj', 'base_url': 'https://qoj.ac/contest/123',
                       'problems': 'A', 'offset': 10, 'user_agent': 'fixture', 'cookie': 'sid=fixture-old',
                       'cookie_source': 'manual', 'handle': 'tester'}
        file = Path(__file__).resolve().parents[1]/'third_party/qoj/submit'
        loader = importlib.machinery.SourceFileLoader('fixture_qoj_repeat', str(file))
        self.module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        with patch('vp_helper.submit_context.submission_configuration', return_value=(self.root/'contest.json', self.config)), \
             patch('vp_helper.browser_login.refresh_submission_login'):
            loader.exec_module(self.module)
        self.url = self.config['base_url'] + '/problem/10'
        self.receipt = self.stack.enter_context(patch.object(self.module, 'record_receipt'))

    def test_repeated_submission_uses_fresh_cookie_and_token_and_never_repeats_rejection(self):
        submitted = []
        transports = []
        for attempt in range(3):
            token = f'fixture-token-{attempt}'
            def router(method, url, cookie, options):
                if method == 'GET' and url.endswith('/submissions'):
                    self.assertIn('sid=fixture-old', cookie)
                    return response(url, cookies=[('sid', 'after-baseline', '.qoj.ac', '/')])
                if method == 'GET':
                    self.assertIn('sid=after-baseline', cookie)
                    self.assertEqual(cookie.count('sid='), 1)
                    return response(url, body='<input name="_token" value="'+token+'">',
                                    cookies=[('sid', 'after-form', '.qoj.ac', '/')])
                self.assertEqual(cookie.count('sid='), 1)
                self.assertIn('sid=after-form', cookie)
                fields = options['data']; self.assertEqual(fields['_token'], token)
                submitted.append(fields)
                if attempt == 2:
                    return response(url, body='<span class="error">Denied '+token+'</span>')
                return response(url, status=302, location='/submission/'+str(1200+attempt),
                                cookies=[('sid', 'after-post', '.qoj.ac', '/')])
            backend = OfflineSession(router); transports.append(backend)
            with patch('vp_helper.http._requests.Session', return_value=backend), \
                 patch.object(self.module, 'post_form', side_effect=lambda session, url, fields: session.post(url, data={name:spec[1] for name,spec in fields.items()})), \
                 patch('sys.stdout', io.StringIO()) as output:
                if attempt < 2:
                    self.module.submit(self.url, 'C++20', '// 中文\nint main(){}', 'A')
                    self.assertIn('Submission #'+str(1200+attempt), output.getvalue())
                else:
                    with self.assertRaisesRegex(SystemExit, 'Denied') as error:
                        self.module.submit(self.url, 'C++20', 'int main(){}', 'A')
                    self.assertNotIn(token, str(error.exception)+output.getvalue())
                    self.assertIn('Check judging results here:', output.getvalue())
            self.assertTrue(backend.closed)
            self.assertNotIn('Cookie', backend.headers)
            self.assertEqual([call[0] for call in backend.calls], ['GET', 'GET', 'POST'])
        self.assertEqual(self.receipt.call_count, 2)
        self.assertEqual(len(submitted), 3)

    def test_timeout_and_missing_token_never_retry_the_post(self):
        def router(method, url, cookie, options):
            if method == 'POST':
                raise _RequestException('fixture timeout')
            return response(url, body='<input name="_token" value="fixture">')
        backend = OfflineSession(router)
        with patch('vp_helper.http._requests.Session', return_value=backend), \
             patch.object(self.module, 'post_form', side_effect=lambda session, url, fields: session.post(url)), patch('sys.stdout', io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'confirmation unavailable'):
                self.module.submit(self.url, 'C++20', 'int main(){}', 'A')
        self.assertEqual([call[0] for call in backend.calls].count('POST'), 1)
        self.receipt.assert_not_called()
        backend = OfflineSession(lambda method, url, cookie, options: response(url))
        with patch('vp_helper.http._requests.Session', return_value=backend), patch('sys.stdout', io.StringIO()):
            with self.assertRaisesRegex(VPError, 'submission token'):
                self.module.submit(self.url, 'C++20', 'int main(){}', 'A')
        self.assertNotIn('POST', [call[0] for call in backend.calls])

    def test_actual_script_entry_confirmation_multipart_and_session_close(self):
        self._run_script_confirmation(False)

    def test_actual_script_entry_explicit_yes_preserves_login_and_single_upload(self):
        self._run_script_confirmation(True)

    def _run_script_confirmation(self, assume_yes):
        source = self.root/'A.cpp'; source.write_text('// 中文\nint main(){}', encoding='utf8')
        self.config.update(problem_ids={'A': '10'})
        (self.root/'contest.json').write_text(json.dumps(self.config))
        class Mime:
            def __init__(self): self.parts = {}; self.closed = False
            def addpart(self, **kwargs): self.parts[kwargs['name']] = kwargs['data']
            def close(self): self.closed = True
        uploads = []
        def router(method, url, cookie, options):
            if method == 'GET' and url.endswith('/submissions'):
                return response(url, cookies=[('sid', 'after-baseline', '.qoj.ac', '/')])
            if method == 'GET':
                self.assertIn('sid=after-baseline', cookie)
                return response(url, body='<input name="_token" value="fresh-token">', cookies=[('sid', 'after-form', '.qoj.ac', '/')])
            self.assertEqual(cookie.count('sid='), 1); self.assertIn('sid=after-form', cookie)
            mime = options['multipart']; uploads.append(mime)
            self.assertEqual(mime.parts['_token'], b'fresh-token')
            self.assertEqual(mime.parts['answer_answer_editor'], source.read_bytes())
            return response(url, status=302, location='/submission/12345')
        backend = OfflineSession(router)
        script = Path(__file__).resolve().parents[1]/'third_party/qoj/submit'
        with patch('vp_helper.http._requests.Session', return_value=backend), patch('curl_cffi.CurlMime', Mime), \
             patch('vp_helper.submit_context.submission_configuration', return_value=(self.root/'contest.json', self.config)), \
             patch('vp_helper.browser_login.refresh_submission_login') as refresh, \
             patch('vp_helper.receipts.record_receipt') as receipt, patch('sys.argv', [str(script), str(source)]+(['-y'] if assume_yes else [])), \
             patch('builtins.input', return_value='y') as decision, patch('sys.stdout', io.StringIO()) as output:
            runpy.run_path(str(script), run_name='__main__')
        refresh.assert_called_once(); receipt.assert_called_once()
        self.assertEqual(len(uploads), 1); self.assertTrue(uploads[0].closed); self.assertTrue(backend.closed)
        if assume_yes:
            decision.assert_not_called();self.assertIn('y [-y]',output.getvalue())
        else:
            self.assertIn('Do you want to continue?', decision.call_args.args[0])
        self.assertIn('Check judging results here: https://qoj.ac/submission/12345', output.getvalue())
        self.assertNotIn('fresh-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
