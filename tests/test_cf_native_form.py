from contextlib import ExitStack
import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from requests.cookies import RequestsCookieJar

from vp_helper.cf_forms import _tta, form_secrets, honor_response_cookies, redact, safe_url, sign_form, submission_error, submission_target, submission_headers
from vp_helper.models import VPError


URL='https://codeforces.com/gym/728585'
TOKEN='fixture-csrf-sensitive'
PAGE=f'''<script>window._ftaa="fixture-ftaa-sensitive"; window._bfaa="fixture-bfaa-sensitive";</script>
<form class="submit-form" method="post" action="?csrf_token={TOKEN}">
<input name="csrf_token" value="{TOKEN}"><input name="ftaa" value=""><input name="_tta" value="stale">
<select name="submittedProblemIndex"><option value="L" selected>L</option></select>
<select name="programTypeId"><option value="73" selected>GNU G++20</option></select>
<input type="file" name="sourceFile"><textarea name="source"></textarea></form>'''


def session():
    cookies=RequestsCookieJar()
    cookies.set('39ce7','fixture-cookie-39ce7',domain='codeforces.com',path='/')
    cookies.set('JSESSIONID','fixture-session-sensitive',domain='codeforces.com',path='/')
    return SimpleNamespace(cookies=cookies)


class Form(dict):
    def __setitem__(self,key,value):
        if key in self:self[key].value=value
        else:super().__setitem__(key,SimpleNamespace(value=value))


class Browser:
    def __init__(self,page=PAGE):
        self.url=URL+'/submit/L'
        self.parsed=BeautifulSoup(page,'html.parser')
        self.session=session()
        self.response=SimpleNamespace(status_code=200)
        self.posts=[]
        self.reject=False

    def get_form(self,**kwargs):
        node=self.parsed.select_one('form.submit-form')
        if node is None:return None
        form=Form()
        for field in node.select('[name]'):
            if field.name=='select':
                options=field.select('option');selected=field.select_one('option[selected]') or options[0]
                dict.__setitem__(form,field['name'],SimpleNamespace(value=selected.get('value',''),options=[o['value'] for o in options],labels=[o.get_text() for o in options]))
            else:form[field['name']]=field.get('value','')
        return form

    def submit_form(self,form,**options):
        values={name:field.value for name,field in form.items()}
        upload=values.get('sourceFile')
        if hasattr(upload,'read'):values['sourceFile']=upload.read()
        self.posts.append((form.action,values,options))
        self.response=SimpleNamespace(status_code=200)
        if self.reject:
            self.url=URL+'/submit/L?csrf_token='+values['csrf_token']
            self.parsed=BeautifulSoup('<span class="error">Source rejected '+values['csrf_token']+'</span>','html.parser')
        else:
            self.url=URL+'/my?locale=en'
            self.parsed=BeautifulSoup('<table></table>','html.parser')


class NativeFormTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config'),'XDG_STATE_HOME':str(self.root/'state')}))
        previous=Path.cwd();os.chdir(self.root);self.addCleanup(os.chdir,previous)
        file=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_submit.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_native',file)
        self.module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'colours':Mock()}):spec.loader.exec_module(self.module)
        self.source=self.root/'L.cpp';self.source.write_text('// 中文注释\nint main() {}\n',encoding='utf8')

    def test_official_js_signatures_include_utf16_and_cookie_unescape(self):
        # Values obtained independently from the published JS function.
        for value,expected in [('abc',870),('fixture-cookie-39ce7',267),('0123456789abcdef',670),('😀A',752),('a%62c',870),('%uD83D%uDE00A',752)]:
            with self.subTest(value=value):self.assertEqual(_tta(value),expected)

    def test_native_page_and_existing_browser_cookie_supply_signing_fields(self):
        page=BeautifulSoup(PAGE,'html.parser');form=page.select_one('form')
        sign_form(page,form,session())
        fields={node['name']:node.get('value') for node in form.select('input[name]')}
        self.assertEqual(fields['ftaa'],'fixture-ftaa-sensitive')
        self.assertEqual(fields['bfaa'],'fixture-bfaa-sensitive')
        self.assertEqual(fields['_tta'],'267')
        self.assertEqual(fields['csrf_token'],TOKEN)

    def test_signature_and_secret_collection_use_actual_host_and_path(self):
        transport=session()
        transport.cookies.set('39ce7','abc',domain='www.codeforces.com',path='/')
        for cookie in transport.cookies:
            if cookie.domain=='www.codeforces.com':cookie.domain_specified=False
        transport.cookies.set('39ce7','0123456789abcdef',domain='.codeforces.com',path='/other')
        for url,expected in [(URL+'/submit',267),('https://www.codeforces.com/gym/728585/submit',870),
                             ('https://codeforces.com/other/submit',670),('https://codeforces.com/otherwise/submit',267)]:
            with self.subTest(url=url):
                page=BeautifulSoup(PAGE,'html.parser');form=page.select_one('form')
                sign_form(page,form,transport,url)
                self.assertEqual(form.select_one('[name="_tta"]')['value'],str(expected))
        values=form_secrets({},transport,URL+'/submit')
        self.assertIn('fixture-cookie-39ce7',values)
        self.assertNotIn('abc',values)
        self.assertNotIn('0123456789abcdef',values)
        self.assertEqual(len([c for c in transport.cookies if c.name=='39ce7']),3)

    def test_real_robobrowser_serialization_preserves_hidden_fields_and_binary_source(self):
        from robobrowser.forms.form import Form as NativeForm
        page=BeautifulSoup(PAGE,'html.parser');node=page.select_one('form')
        sign_form(page,node,session())
        form=NativeForm(node)
        with self.source.open('rb') as source:
            form['sourceFile']=source
            payload=form.serialize().to_requests('post')
            fields=dict(payload['data'])
            self.assertEqual(fields['_tta'],'267')
            self.assertEqual(fields['ftaa'],'fixture-ftaa-sensitive')
            self.assertEqual(fields['bfaa'],'fixture-bfaa-sensitive')
            self.assertEqual(fields['csrf_token'],TOKEN)
            uploaded=dict(payload['files'])['sourceFile']
            self.assertEqual(uploaded.read(),self.source.read_bytes())

    def test_native_problem_suffix_is_allowed_but_foreign_or_wrong_problem_is_rejected(self):
        native=URL+'/submit/L?csrf_token='+TOKEN
        self.assertEqual(submission_target(URL+'/submit/L','?csrf_token='+TOKEN,URL,'L'),native)
        for action in ['https://attacker.invalid/submit','https://codeforces.com/contest/1/submit','/gym/728585/submit/A','https://codeforces.com:444/gym/728585/submit']:
            with self.subTest(action=action),self.assertRaises(VPError):submission_target(URL+'/submit/L',action,URL,'L')
        self.assertNotIn(TOKEN,safe_url(native))

    def test_vp_sends_one_binary_upload_with_native_signatures_after_confirmation(self):
        browser=Browser()
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()) as output:
            self.assertTrue(self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L'))
        self.assertEqual(len(browser.posts),1)
        target,fields,options=browser.posts[0]
        self.assertEqual(target,URL+'/submit/L?csrf_token='+TOKEN)
        self.assertEqual(fields['sourceFile'],self.source.read_bytes())
        self.assertEqual(fields['_tta'],'267')
        self.assertEqual(fields['ftaa'],'fixture-ftaa-sensitive')
        self.assertEqual(fields['bfaa'],'fixture-bfaa-sensitive')
        self.assertEqual(options['timeout'],(10,40))
        self.assertEqual(options['headers']['Sec-Fetch-Site'],'same-origin')
        self.assertEqual(options['headers']['Sec-Fetch-Mode'],'navigate')
        self.assertEqual(options['headers']['Sec-Fetch-Dest'],'document')
        for value in [TOKEN,'fixture-ftaa-sensitive','fixture-bfaa-sensitive','fixture-session-sensitive']:
            self.assertNotIn(value,output.getvalue())

    def test_native_fetch_metadata_preserves_real_www_origin_without_leaking_csrf(self):
        headers=submission_headers('https://www.codeforces.com/gym/728585/submit?csrf_token='+TOKEN,URL+'/submit')
        self.assertEqual(headers['Origin'],'https://www.codeforces.com')
        self.assertEqual(headers['Sec-Fetch-Site'],'same-site')
        self.assertEqual(headers['Referer'],'https://www.codeforces.com/gym/728585/submit')
        self.assertNotIn(TOKEN,str(headers))
        for bad in ['https://other.example/submit','http://codeforces.com/submit','https://user@codeforces.com/submit']:
            with self.assertRaises(VPError):submission_headers(bad,URL+'/submit')

    def test_explicit_yes_skips_only_prompt_and_still_shows_native_preview(self):
        browser=Browser();browser.xcpc_assume_yes=True
        with patch('builtins.input',side_effect=AssertionError('explicit -y must not prompt')),patch('sys.stdout',io.StringIO()) as output:
            self.assertTrue(self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L'))
        self.assertEqual(len(browser.posts),1)
        self.assertIn('Submission information:',output.getvalue())
        self.assertIn('y [-y]',output.getvalue())

    def test_cf_entry_yes_flag_is_explicit_and_keeps_login_verification(self):
        import importlib.machinery
        from unittest.mock import MagicMock
        entry=Path(__file__).resolve().parents[1]/'third_party/cf_submit/submit'
        (self.root/'contest.json').write_text('{"platform":"gym","contest_id":728585}')
        for options,expected in [([],False),(['-y'],True),(['--yes'],True)]:
            browser=SimpleNamespace()
            login=SimpleNamespace(get_secret=Mock(return_value=('tester',None)),login=Mock(return_value=browser))
            submitted=Mock()
            loader=importlib.machinery.SourceFileLoader('fixture_cf_entry_yes',str(entry))
            module=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
            with patch.dict('sys.modules',{'cf_login':login,'cf_submit':SimpleNamespace(submit_files=submitted),
                                         'cf_coach':MagicMock(),'cf_problems':MagicMock(),'cf_standings':MagicMock(),'colours':MagicMock()}), \
                 patch('sys.argv',[str(entry),str(self.source),*options]):
                loader.exec_module(module);module.main()
            login.login.assert_called_once();submitted.assert_called_once()
            self.assertIs(browser.xcpc_assume_yes,expected)

    def test_post_redirect_challenge_is_unknown_and_never_replays_upload(self):
        browser=Browser()
        def upload(form,**options):
            browser.posts.append((form.action,{},options))
            browser.url=URL+'/my'
            browser.parsed=BeautifulSoup('<title>Just a moment...</title>','html.parser')
            browser.response=SimpleNamespace(status_code=403,headers={'cf-mitigated':'challenge'},history=[SimpleNamespace(status_code=302)])
        browser.submit_form=upload
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()) as output:
            with self.assertRaisesRegex(VPError,'outcome is unknown') as error:
                self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L')
        self.assertEqual(len(browser.posts),1)
        self.assertIn('successful upload',str(error.exception));self.assertIn(URL+'/my',output.getvalue())

    def test_native_transport_refreshes_challenged_get_once_but_never_any_post(self):
        file=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_login.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_login_challenge',file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for method,url,should_refresh in [('GET',URL+'/submit/L',True),('POST',URL+'/submit/L',False),('GET','https://other.example/',False)]:
            with self.subTest(method=method,url=url):
                transport=module.make_session();self.addCleanup(transport.close)
                transport._xcpc_refresh=Mock(return_value=True)
                challenge=SimpleNamespace(status_code=403,headers={'Content-Type':'text/html','cf-mitigated':'challenge'},text='<title>Just a moment...</title>',url=url,cookies=[],history=[])
                fresh=SimpleNamespace(status_code=200,headers={},text='form',url=url,cookies=[],history=[])
                with patch.object(module._requests.Session,'request',side_effect=[challenge,fresh]) as wire:
                    result=transport.request(method,url)
                self.assertEqual(wire.call_count,2 if should_refresh else 1)
                self.assertEqual(transport._xcpc_refresh.call_count,int(should_refresh))
                self.assertIs(result,fresh if should_refresh else challenge)

    def test_mid_vp_cookie_and_csrf_rotation_uses_latest_native_values(self):
        from requests import Request
        browser=Browser()
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()):
            self.assertTrue(self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L'))
        # A later native GET rotates both cookies under an equivalent domain.
        updated=RequestsCookieJar()
        updated.set('JSESSIONID','fixture-new-session',domain='.codeforces.com',path='/')
        updated.set('39ce7','fixture-new-signature',domain='.codeforces.com',path='/')
        updated.set('other','keep-this-path',domain='.codeforces.com',path='/other')
        honor_response_cookies(browser.session,SimpleNamespace(cookies=updated,history=[],url=URL+'/submit/L'))
        cookie_header=Request('GET',URL+'/submit/L',cookies=browser.session.cookies).prepare().headers['Cookie']
        self.assertIn('JSESSIONID=fixture-new-session',cookie_header)
        self.assertNotIn('fixture-session-sensitive',cookie_header)
        self.assertEqual(cookie_header.count('JSESSIONID='),1)
        self.assertNotIn('keep-this-path',cookie_header)
        browser.url=URL+'/submit/L'
        browser.parsed=BeautifulSoup(PAGE.replace(TOKEN,'fixture-new-csrf'),'html.parser')
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()):
            self.assertTrue(self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L'))
        self.assertEqual(len(browser.posts),2)
        fields=browser.posts[-1][1]
        self.assertEqual(fields['csrf_token'],'fixture-new-csrf')
        self.assertEqual(fields['_tta'],str(_tta('fixture-new-signature')))
        # A subsequent rejection remains one attempt, with a safe message/link.
        browser.url=URL+'/submit/L';browser.reject=True
        browser.parsed=BeautifulSoup(PAGE.replace(TOKEN,'fixture-new-csrf'),'html.parser')
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()) as output:
            with self.assertRaisesRegex(VPError,'Source rejected') as error:
                self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L')
        self.assertEqual(len(browser.posts),3)
        self.assertNotIn('fixture-new-csrf',output.getvalue()+str(error.exception))
        self.assertIn(URL+'/my',output.getvalue())

    def test_bundled_transport_honors_native_set_cookie_before_next_form(self):
        file=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_login.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_login',file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        if not module._IMPERSONATE:self.skipTest('curl_cffi transport is optional')
        transport=module.make_session()
        self.addCleanup(transport.close)
        transport.cookies.set('JSESSIONID','fixture-old',domain='codeforces.com',path='/')
        updated=RequestsCookieJar();updated.set('JSESSIONID','fixture-new',domain='.codeforces.com',path='/')
        response=SimpleNamespace(cookies=updated,history=[],url=URL+'/submit/L')
        with patch.object(module._requests.Session,'request',return_value=response) as native:
            self.assertIs(transport.get(URL+'/submit/L'),response)
        native.assert_called_once()
        self.assertNotIn('Cookie',transport.headers)
        values=[c.value for c in transport.cookies.jar if c.name=='JSESSIONID']
        self.assertEqual(values,['fixture-new'])

    def test_native_and_shared_transport_use_matching_real_firefox_profile_and_ua(self):
        from vp_helper.http import WebSession
        profile=self.root/'firefox';profile.mkdir()
        binary=self.root/'firefox-binary';(binary/'distribution').mkdir(parents=True)
        (binary/'distribution/distribution.ini').write_text('[Global]\nid=canonical-002\n')
        (profile/'compatibility.ini').write_text('[Compatibility]\nLastVersion=154.0\nLastOSABI=Linux_aarch64-gcc3\nLastPlatformDir='+str(binary)+'\n')
        account={'cookie_source':'firefox','firefox_profile':str(profile),'cookie':'sid=fixture',
                 'user_agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0'}
        file=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_login.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_login_identity',file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        expected='Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0'
        with patch('vp_helper.browser_transport._supported_profiles',return_value=frozenset({'firefox147','firefox154'})):
            native=module.make_session(account['user_agent'],'firefox',account=account)
            web=WebSession('https://codeforces.com',account,auto_refresh=False)
        self.addCleanup(native.close);self.addCleanup(web.session.close)
        self.assertEqual(native.impersonate,'firefox154');self.assertEqual(web.session.impersonate,'firefox154')
        self.assertEqual(native.headers['User-Agent'],expected);self.assertEqual(web.session.headers['User-Agent'],expected)

    def test_firefox_login_verification_passes_candidate_profile_to_transport(self):
        from vp_helper import browser_login
        candidate=browser_login.BrowserCookies(self.root/'firefox','sid=fixture','firefox')
        fake=Mock();fake.session.get.return_value=SimpleNamespace(url='https://codeforces.com/',
            text='<div id="header"><a href="/profile/tester">tester</a></div>',headers={})
        fake.session.cookies=RequestsCookieJar();fake.session.cookies.set('sid','fixture',domain='codeforces.com',path='/')
        with patch('vp_helper.http.WebSession',return_value=fake) as factory:
            verified=browser_login._verify('gym',candidate,'tester')
        self.assertIsNotNone(verified)
        credentials=factory.call_args.args[1]
        self.assertEqual(credentials['cookie_source'],'firefox');self.assertEqual(credentials['firefox_profile'],str(candidate.profile))
        self.assertFalse(factory.call_args.kwargs['auto_refresh'])

    def test_requests_fallback_keeps_files_payload_and_honors_native_rotation(self):
        file=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_login.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_login_fallback',file)
        module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'curl_cffi':None}):spec.loader.exec_module(module)
        self.assertIsNone(module._IMPERSONATE)
        transport=module.make_session();self.addCleanup(transport.close)
        transport.cookies.set('JSESSIONID','fixture-old',domain='codeforces.com',path='/')
        updates=RequestsCookieJar();updates.set('JSESSIONID','fixture-new',domain='.codeforces.com',path='/')
        response=SimpleNamespace(cookies=updates,history=[],url=URL+'/submit')
        files=[('sourceFile',b'fixture-source')]
        with patch.object(module._requests.Session,'request',return_value=response) as native:
            self.assertIs(transport.post(URL+'/submit',files=files),response)
        native.assert_called_once()
        self.assertEqual(native.call_args.kwargs['files'],files)
        self.assertNotIn('multipart',native.call_args.kwargs)
        self.assertEqual([c.value for c in transport.cookies if c.name=='JSESSIONID'],['fixture-new'])

    def test_cookie_rotation_preserves_other_hosts_paths_and_rejects_invalid_scope(self):
        transport=session()
        transport.cookies.set('JSESSIONID','different-host',domain='www.codeforces.com',path='/')
        transport.cookies.set('JSESSIONID','different-path',domain='codeforces.com',path='/other')
        transport.cookies.set('JSESSIONID','different-platform',domain='qoj.ac',path='/')
        updates=RequestsCookieJar()
        updates.set('JSESSIONID','fixture-new',domain='.codeforces.com',path='/')
        updates.set('JSESSIONID','must-not-change-child',domain='.www.codeforces.com',path='/')
        updates.set('bad','must-not-import-path',domain='.codeforces.com',path='invalid')
        updates.set('bad','must-not-import-host',domain='attacker.invalid',path='/')
        honor_response_cookies(transport,SimpleNamespace(cookies=updates,history=[],url=URL+'/submit'))
        actual={(cookie.domain,cookie.path,cookie.name):cookie.value for cookie in transport.cookies}
        self.assertEqual(actual[('.codeforces.com','/','JSESSIONID')],'fixture-new')
        self.assertEqual(actual[('www.codeforces.com','/','JSESSIONID')],'different-host')
        self.assertEqual(actual[('codeforces.com','/other','JSESSIONID')],'different-path')
        self.assertEqual(actual[('qoj.ac','/','JSESSIONID')],'different-platform')
        self.assertFalse(any(cookie.name=='bad' for cookie in transport.cookies))
        self.assertNotIn(('codeforces.com','/','JSESSIONID'),actual)

    def test_shared_http_honors_rotated_cookie_for_bt_and_live(self):
        from vp_helper.http import WebSession
        web=WebSession('https://codeforces.com',{'cookie':'JSESSIONID=fixture-old'},auto_refresh=False)
        self.addCleanup(web.session.close)
        updates=RequestsCookieJar();updates.set('JSESSIONID','fixture-new',domain='.codeforces.com',path='/')
        response=SimpleNamespace(cookies=updates,history=[],url=URL+'/submit',status_code=400,headers={},text='')
        with self.assertRaisesRegex(VPError,'HTTP 400'):web.check(response)
        self.assertEqual([c.value for c in web.session.cookies.jar if c.name=='JSESSIONID'],['fixture-new'])

    def test_cancel_never_opens_or_sends_source(self):
        browser=Browser()
        with patch('builtins.input',return_value='n'),patch('sys.stdout',io.StringIO()):
            self.assertFalse(self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L'))
        self.assertEqual(browser.posts,[])

    def test_rejected_post_reports_safe_native_reason_and_judging_link(self):
        browser=Browser();browser.reject=True
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()) as output:
            with self.assertRaisesRegex(VPError,'Source rejected') as error:self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L')
        self.assertEqual(len(browser.posts),1)
        self.assertNotIn(TOKEN,str(error.exception)+output.getvalue())
        self.assertIn(URL+'/my',output.getvalue())

    def test_unknown_post_outcome_is_not_retried_and_exception_url_is_not_leaked(self):
        browser=Browser();browser.submit_form=Mock(side_effect=self.module.requests.ConnectionError('https://codeforces.com/submit?csrf_token='+TOKEN))
        with patch('builtins.input',return_value='y'),patch('sys.stdout',io.StringIO()):
            with self.assertRaisesRegex(VPError,'not repeated') as error:self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L')
        browser.submit_form.assert_called_once()
        self.assertNotIn(TOKEN,str(error.exception))

    def test_form_without_csrf_stops_before_confirmation_or_post(self):
        browser=Browser(PAGE.replace(f'<input name="csrf_token" value="{TOKEN}">',''))
        with patch('builtins.input') as prompt,self.assertRaisesRegex(VPError,'CSRF'):
            self.module.submit_problem(browser,'728585','cpp',str(self.source),-1,expected_problem='L')
        prompt.assert_not_called();self.assertEqual(browser.posts,[])

    def test_error_detail_handles_browser_verification_and_login_expiry_without_secrets(self):
        for html,url,status,phrase in [('<title>Just a moment...</title>',URL+'/submit',403,'browser verification'),('<form></form>','https://codeforces.com/enter',200,'login expired'),('<form></form>',URL+'/submit',429,'rate limiting')]:
            detail=submission_error(BeautifulSoup(html,'html.parser'),status,url+'?csrf_token='+TOKEN)
            self.assertIn(phrase,detail);self.assertNotIn(TOKEN,detail)
        self.assertNotIn(TOKEN,redact('csrf_token='+TOKEN))


if __name__=='__main__':unittest.main()
