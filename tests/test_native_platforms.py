"""Synthetic browser snapshots and OS boundaries; never read personal logins."""
from contextlib import ExitStack
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from vp_helper import browser_bridge as bridge, browser_login as login
from vp_helper import chromium_login, cli, portable, storage, updater, windows_terminal
from vp_helper import bootstrap, bridge_bootstrap
from vp_helper import platform_clock
from vp_helper.models import VPError


PROFILE = '01234567-89ab-cdef-0123-456789abcdef'


def snapshot(**changes):
    return dict(type='snapshot', source='chrome', profile_id=PROFILE, user_agent='Chrome synthetic', cookies=[
        dict(name='sid', value='synthetic-session', domain='qoj.ac', path='/'),
        dict(name='sso', value='parent-session', domain='.nowcoder.com', path='/'),
    ]) | changes


class Isolated(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME':str(self.root/'config'),
                                                        'XDG_STATE_HOME':str(self.root/'state')}))


class ExtensionLoginTests(Isolated):
    def setUp(self):
        super().setUp()
        assets = Path(__file__).resolve().parent.parent/'browser_extension'
        self.stack.enter_context(patch.object(chromium_login,'extension_source',return_value=assets))

    def test_frame_roundtrip_and_invalid_messages(self):
        output = io.BytesIO()
        bridge.send_message(output, snapshot())
        output.seek(0)
        self.assertEqual(bridge.read_message(output), snapshot())
        self.assertIsNone(bridge.read_message(output))
        for data in (b'\x01', struct.pack('<I',0), struct.pack('<I',1024*1024+1), struct.pack('<I',10)+b'{}'):
            with self.assertRaises(ValueError):
                bridge.read_message(io.BytesIO(data))

    def test_host_filters_unrelated_private_partitioned_expired_and_unsafe_cookies(self):
        value = snapshot()
        value['cookies'] += [dict(name='bad', value='excluded', domain=domain, path='/')
                             for domain in ('example.com','evil.qoj.ac','qoj.ac.evil.test')]
        value['cookies'] += [dict(name='excluded', value='excluded', domain='qoj.ac', path='/', **change)
                             for change in ({'partitionKey':{'topLevelSite':'https://other.example'}},
                                            {'incognito':True}, {'expirationDate':1}, {'expirationDate':float('nan')})]
        value['cookies'] += [dict(name='bad\r\nHeader', value='excluded',domain='qoj.ac',path='/'),
                             dict(name='path-cookie',value='excluded',domain='qoj.ac',path='/private')]
        bridge.store_snapshot(value)
        candidates = chromium_login.cookies('qoj', {})
        self.assertEqual(candidates[0].cookie, 'sid=synthetic-session')
        self.assertEqual(candidates[0].source, 'chrome')
        self.assertEqual(candidates[0].user_agent, 'Chrome synthetic')
        self.assertNotIn('synthetic-session', repr(candidates[0]))
        self.assertEqual(chromium_login.cookies('nowcoder', {})[0].cookie, 'sso=parent-session')
        self.assertEqual(chromium_login.cookies('cwnuoj', {}), [])
        if os.name == 'posix':
            self.assertEqual(candidates[0].profile.stat().st_mode & 0o777, 0o600)

    def test_parent_sso_alternative_and_multiple_browser_profiles_are_kept_separate(self):
        bridge.store_snapshot(snapshot(cookies=[
            dict(name='sid',value='guest',domain='oj.cwnupaa.com',path='/'),
            dict(name='sid',value='signed-in',domain='.cwnupaa.com',path='/')]))
        bridge.store_snapshot(snapshot(source='edge',cookies=[dict(name='sid',value='edge-user',domain='qoj.ac',path='/')]))
        self.assertEqual({c.cookie for c in chromium_login.cookies('cwnuoj', {})}, {'sid=guest','sid=signed-in'})
        self.assertEqual({c.source for c in chromium_login.cookies('qoj', {})}, {'edge'})

    def test_www_and_first_party_partitioned_logins_for_every_judge(self):
        cases = [('cf','codeforces.com'),('gym','codeforces.com'),('qoj','qoj.ac'),
                 ('qoj','contest.ucup.ac'),('cwnuoj','oj.cwnupaa.com'),('nowcoder','ac.nowcoder.com')]
        for platform,host in cases:
            with self.subTest(platform=platform,host=host):
                site='https://'+'.'.join(host.split('.')[-2:])
                for domain in (host,'www.'+host):
                    bridge.store_snapshot(snapshot(cookies=[dict(name='sid',value='signed-in',domain=domain,path='/',
                        partitionKey={'topLevelSite':site,'hasCrossSiteAncestor':False}),
                        dict(name='sid',value='guest',domain=host,path='/'),
                        dict(name='cross-site',value='excluded',domain=domain,path='/',
                             partitionKey={'topLevelSite':site,'hasCrossSiteAncestor':True})]))
                    candidates=chromium_login.cookies(platform,{})
                    self.assertIn('sid=signed-in',{c.cookie for c in candidates})
                    self.assertTrue(all('excluded' not in c.cookie for c in candidates))
                    self.assertTrue(all(c.host==host for c in candidates))

    def test_partial_site_read_failure_is_diagnosed_without_exposing_credentials(self):
        bridge.store_snapshot(snapshot(cookies=[],read_errors=['codeforces.com','unrelated.example']))
        with patch.object(chromium_login,'enabled',return_value=True):
            message='\n'.join(login.login_diagnostics('cf',{}))
        self.assertIn('未授权',message)
        self.assertIn('Cookie：0',message)
        self.assertIn(PROFILE[-8:],message)
        self.assertNotIn('unrelated.example',message)

    def test_native_www_redirect_preserves_verified_cookie_and_ucup_stays_separate(self):
        web=Mock()
        web.session.cookies=requests.cookies.RequestsCookieJar()
        web.session.cookies.set('sid','www-cookie',domain='www.codeforces.com',path='/')
        web.session.get.return_value=Mock(url='https://www.codeforces.com/',text='<div id="header"><a href="/profile/tester">tester</a></div>')
        candidate=login.BrowserCookies(self.root,'sid=www-cookie','chrome','Synthetic Chromium','codeforces.com')
        with patch('vp_helper.http.WebSession',return_value=web):
            result=login._verify('cf',candidate,'tester')
        self.assertEqual(result['cookie'],'sid=www-cookie')
        self.assertEqual(result['browser_host'],'codeforces.com')
        bridge.store_snapshot(snapshot(cookies=[dict(name='sid',value='ucup-only',domain='www.contest.ucup.ac',path='/')]))
        self.assertEqual(chromium_login.cookies('qoj',{'_browser_target':'qoj.ac'}),[])
        self.assertEqual(chromium_login.cookies('qoj',{'_browser_target':'contest.ucup.ac'})[0].cookie,'sid=ucup-only')

    def test_extension_manifest_declares_every_imported_host_and_no_unrelated_site(self):
        from vp_helper.browser_sites import HOSTS
        manifest=storage.read_json(chromium_login.extension_source()/'manifest.json')
        self.assertEqual(set(manifest['host_permissions']),{'https://'+host+'/*' for host in HOSTS})

    def test_ucup_web_client_refreshes_only_the_target_judge_cookies(self):
        from vp_helper.http import WebSession
        account={'handle':'tester','cookie_source':'chrome','browser_host':'qoj.ac','cookie':'sid=qoj'}
        def refresh(platform,selected,**kwargs):
            self.assertEqual(platform,'qoj')
            self.assertEqual(selected['_browser_target'],'contest.ucup.ac')
            selected.update(cookie='sid=ucup',browser_host='contest.ucup.ac')
            return True
        with patch.object(login,'refresh_browser_account',side_effect=refresh):
            web=WebSession('https://contest.ucup.ac',account)
            self.assertTrue(web._auto_refresh)
            self.assertTrue(web._refresh_browser())
            self.assertNotIn('_browser_target',account)
            self.assertEqual(account['cookie'],'sid=ucup')
            web.session.close()

    def test_submit_keeps_qoj_and_ucup_sessions_scoped_to_the_contest_url(self):
        bridge.store_snapshot(snapshot(cookies=[dict(name='sid',value='qoj',domain='qoj.ac',path='/'),
            dict(name='sid',value='ucup',domain='www.contest.ucup.ac',path='/')]))
        config={'handle':'tester','cookie_source':'chrome','base_url':'https://contest.ucup.ac/contest/123'}
        def verify(platform,candidate,expected,**kwargs):
            return {'handle':expected,'cookie':candidate.cookie,'browser_host':candidate.host,'cookie_source':'chrome'}
        with patch.object(chromium_login,'enabled',return_value=True),patch.object(login,'_verify',side_effect=verify):
            login.refresh_submission_login(self.root/'contest.json',config,'qoj')
        self.assertEqual(config['cookie'],'sid=ucup')
        self.assertEqual(config['browser_host'],'contest.ucup.ac')
        self.assertNotIn('_browser_target',storage.read_json(self.root/'contest.json'))

    def test_www_contest_links_normalize_to_the_same_saved_contest(self):
        from vp_helper.parsing import contest_url
        urls=['https://codeforces.com/contest/123','https://codeforces.com/gym/100123',
              'https://qoj.ac/contest/123','https://contest.ucup.ac/contest/123',
              'https://oj.cwnupaa.com/contest/'+'a'*24,'https://ac.nowcoder.com/acm/contest/123']
        for url in urls:
            self.assertEqual(contest_url(url.replace('https://','https://www.')),contest_url(url))

    def test_logout_clears_snapshot_and_never_falls_back_to_cached_account(self):
        bridge.store_snapshot(snapshot())
        bridge.store_snapshot(snapshot(cookies=[]))
        with patch.object(chromium_login, 'enabled', return_value=True):
            account = {'handle':'tester','cookie':'sid=old-synthetic','cookie_source':'chrome'}
            with patch.object(login, '_verify') as verify:
                self.assertFalse(login.verify_stored_session('qoj', account))
                self.assertFalse(login.refresh_browser_account('qoj', account))
                verify.assert_not_called()
            with self.assertRaisesRegex(VPError, 'Chrome / Edge'):
                login.refresh_submission_login(self.root/'contest.json',account,'qoj')
            self.assertEqual(account['cookie'],'sid=old-synthetic')

    def test_malformed_snapshot_cannot_select_another_directory(self):
        for changes in ({'source':'firefox'}, {'profile_id':'../../outside'}, {'user_agent':'bad\nheader'}):
            with self.assertRaises(ValueError):
                bridge.store_snapshot(snapshot(**changes))

    def test_native_protocol_acknowledges_without_echoing_secrets(self):
        source, target = io.BytesIO(), io.BytesIO()
        bridge.send_message(source,snapshot())
        source.seek(0)
        with patch('sys.argv',['bridge','chrome-extension://'+chromium_login.extension_id()+'/']), \
                patch('sys.stdin',SimpleNamespace(buffer=source)), patch('sys.stdout',SimpleNamespace(buffer=target)):
            bridge.main()
        self.assertNotIn(b'synthetic-session',target.getvalue())
        target.seek(0)
        self.assertEqual(bridge.read_message(target), {'type':'stored'})
        with patch('sys.argv',['bridge','chrome-extension://unrelated/']), self.assertRaises(SystemExit):
            bridge.main()

    def test_extension_identity_verification_and_account_mismatch(self):
        bridge.store_snapshot(snapshot())
        web = Mock()
        web.session.cookies = requests.cookies.RequestsCookieJar()
        web.session.cookies.set('sid','synthetic-session',domain='qoj.ac',path='/')
        web.session.get.return_value = Mock(url='https://qoj.ac/',text='<nav><a href="/user/profile/tester">tester</a></nav>')
        with patch.object(chromium_login,'enabled',return_value=True), patch('vp_helper.http.WebSession',return_value=web):
            account = {'handle':'tester'}
            self.assertTrue(login.refresh_browser_account('qoj',account,force=True))
            self.assertEqual(account['cookie_source'],'chrome')
            self.assertEqual(account['user_agent'],'Chrome synthetic')
            self.assertIn('browser_profile',account)
            self.assertNotIn('firefox_profile',account)
            another = {'handle':'someone-else','cookie':'keep-this'}
            self.assertFalse(login.refresh_browser_account('qoj',another,force=True))
            self.assertEqual(another, {'handle':'someone-else','cookie':'keep-this'})
            diagnostics = '\n'.join(login.login_diagnostics('qoj',account))
            self.assertIn('已验证', diagnostics)
            self.assertNotIn('synthetic-session', diagnostics)

    def test_manual_login_is_preserved(self):
        with patch.object(chromium_login, 'enabled', return_value=True), patch.object(login,'browser_cookies') as reader:
            account = {'cookie_source':'manual','cookie':'manual-synthetic'}
            self.assertFalse(login.refresh_browser_account('qoj',account))
            login.refresh_submission_login(self.root/'contest.json',account,'qoj')
            reader.assert_not_called()

    def test_macos_registration_and_extension_id_survive_repeat_registration(self):
        release = self.root/'app/releases/first'
        storage.write_json(release/'installation.json', {'portable':True,'install_root':str(release.parent.parent),'python':sys.executable})
        with patch('sys.platform','darwin'), patch('sys.prefix',str(release/'venv')), patch.object(Path,'home',return_value=self.root):
            extension = chromium_login.register_bridge()
            first_id = chromium_login.extension_id()
            self.assertTrue((extension/'background.js').is_file())
            for browser in ('Google/Chrome','Microsoft Edge'):
                manifest = storage.read_json(self.root/'Library/Application Support'/browser/'NativeMessagingHosts/com.xcpc.browser.json')
                self.assertEqual(manifest['allowed_origins'], ['chrome-extension://'+first_id+'/'])
                self.assertTrue(Path(manifest['path']).is_file())
            self.assertEqual(chromium_login.register_bridge(), extension)
            self.assertEqual(chromium_login.extension_id(), first_id)
        with patch('sys.platform','linux'), patch('shutil.copytree') as copy:
            self.assertIsNone(chromium_login.register_bridge())
            copy.assert_not_called()

    def test_windows_registers_both_per_user_browsers_without_browser_executable(self):
        release = self.root/'app/releases/first'
        storage.write_json(release/'installation.json', {'portable':True,'install_root':str(release.parent.parent)})
        registry, maker = Mock(), Mock()
        registry.CreateKey.return_value.__enter__ = Mock(return_value='key')
        registry.CreateKey.return_value.__exit__ = Mock(return_value=False)
        with patch('sys.platform','win32'), patch('sys.prefix',str(release/'venv')), \
                patch.dict(sys.modules, {'winreg':registry,'pip._vendor.distlib.scripts':SimpleNamespace(ScriptMaker=Mock(return_value=maker))}):
            extension = chromium_login.register_bridge()
        self.assertTrue(extension.is_dir())
        maker.make.assert_called_once_with('host = vp_helper.bridge_bootstrap:main')
        paths = [call.args[1] for call in registry.CreateKey.call_args_list]
        self.assertTrue(any('Google\\Chrome' in p for p in paths))
        self.assertTrue(any('Microsoft\\Edge' in p for p in paths))
        self.assertTrue(all(call.args[0] is registry.HKEY_CURRENT_USER for call in registry.CreateKey.call_args_list))


class NativeBoundaryTests(Isolated):
    def test_native_monitors_and_submitters_share_same_boot_clock_anchor(self):
        for platform, value in (('darwin','boot-session-uuid'),('win32','639000000000000000')):
            with patch('sys.platform',platform),patch.object(platform_clock.subprocess,'check_output',return_value=value) as query:
                first=platform_clock.boot_id.__wrapped__()
                with patch('os.getpid',return_value=99999):
                    self.assertEqual(platform_clock.boot_id.__wrapped__(),first)
                self.assertNotIn('process-',first)
                self.assertLessEqual(query.call_args.kwargs['timeout'],5)

    def test_old_command_and_native_host_follow_current_release_after_update(self):
        first, latest = self.root/'app/releases/first', self.root/'app/releases/latest'
        metadata={'portable':True,'install_root':str(first.parent.parent),'bin_directory':str(self.root/'bin')}
        storage.write_json(first/'installation.json',metadata)
        storage.write_json(first.parent.parent/'current.json',{'release':str(latest)})
        with patch('sys.prefix',str(first/'venv')),patch('sys.platform','win32'), \
                patch('sys.argv',['submit.exe','A.cpp']),patch('os.execv') as execute:
            bootstrap.main()
            self.assertEqual(execute.call_args.args[1], [str(latest/'venv/Scripts/python.exe'),'-m','vp_helper.submit','A.cpp'])
        origin='chrome-extension://'+PROFILE+'/'
        with patch('sys.prefix',str(first/'venv')),patch('sys.argv',['host.exe',origin]),patch('os.execv') as execute:
            bridge_bootstrap.main()
            self.assertEqual(execute.call_args.args[1], [str(latest/'venv/Scripts/python.exe'),'-u','-m','vp_helper.browser_bridge',origin])
        storage.write_json(first.parent.parent/'current.json',{'release':str(self.root/'unrelated')})
        with patch('sys.prefix',str(first/'venv')),patch('sys.argv',['submit.exe']),self.assertRaises(SystemExit):
            bootstrap.main()

    def test_native_storage_defaults_and_xdg_override(self):
        with patch.dict(os.environ,{},clear=True), patch.object(Path,'home',return_value=self.root):
            with patch('sys.platform','darwin'):
                self.assertEqual(storage.config_directory(),self.root/'Library/Application Support/XCPC')
                self.assertEqual(storage.state_directory(),storage.config_directory()/'State')
                self.assertEqual(storage.desktop(),self.root/'Desktop')
            with patch('sys.platform','win32'), patch.dict(os.environ,{'APPDATA':str(self.root/'Roaming'),'LOCALAPPDATA':str(self.root/'Local')}):
                self.assertEqual(storage.config_directory(),self.root/'Roaming/XCPC')
                self.assertEqual(storage.state_directory(),self.root/'Local/XCPC/State')
            with patch('sys.platform','linux'):
                self.assertEqual(storage.config_directory(),self.root/'.config/vp')
                self.assertEqual(storage.state_directory(),self.root/'.local/state/vp')

    def test_windows_byte_lock_retries_busy_and_propagates_real_errors(self):
        import errno
        windows = SimpleNamespace(LK_NBLCK=1,LK_UNLCK=2,locking=Mock(side_effect=[OSError(errno.EACCES,'busy'),None,None]))
        path = self.root/'monitor.lock'
        with patch('sys.platform','win32'),patch.dict(sys.modules,{'msvcrt':windows}),patch('time.sleep') as sleep:
            with storage.file_lock(path):
                pass
            sleep.assert_called_once()
            self.assertEqual(windows.locking.call_count,3)
            windows.locking.side_effect=OSError(errno.EACCES,'busy')
            with self.assertRaises(VPError):
                with storage.file_lock(path,nonblocking=True):
                    pass
            windows.locking.side_effect=OSError(errno.EIO,'broken')
            with self.assertRaises(OSError):
                with storage.file_lock(path):
                    pass
            with self.assertRaises(OSError):
                with storage.file_lock(path,nonblocking=True):
                    pass

    def test_windows_terminal_uses_own_monitor_and_gives_shell_more_space(self):
        path = self.root/'contest-state.json'
        state = {'directory':str(self.root/'Contest with spaces')}
        with patch.object(windows_terminal.shutil,'which',return_value='wt.exe'), \
                patch.object(windows_terminal.subprocess,'run') as run, patch('sys.stdout',io.StringIO()):
            windows_terminal.open_dashboard(path,state)
        args = run.call_args.args[0]
        self.assertEqual(args[0:3], ['wt.exe','-w','xcpc-contest-state'])
        self.assertIn('vp_helper.cli',args)
        self.assertIn('vp_helper.windows_terminal',args)
        self.assertEqual(args.count(str(path)),2)
        self.assertEqual(args[args.index('-s')+1],'0.35')
        self.assertEqual(args[args.index('-d')+1],state['directory'])
        storage.write_json(path.with_suffix('.windows-monitor'),{'pid':42,'token':123})
        with patch.object(windows_terminal,'process_token',return_value=456) as token, \
                patch.object(windows_terminal.shutil,'which',return_value='wt.exe'), \
                patch.object(windows_terminal.subprocess,'run'),patch('sys.stdout',io.StringIO()):
            windows_terminal.open_dashboard(path,state | {'_reload_monitor':True})
        self.assertEqual(token.call_count,1)  # PID reuse must not kill an unrelated process.

    def test_terminal_fallback_and_reopening_existing_monitor(self):
        path = self.root/'state.json'
        state={'directory':str(self.root)}
        storage.write_json(path.with_suffix('.windows-monitor'),{'pid':42,'token':123})
        with patch.object(windows_terminal,'process_token',return_value=123), \
                patch.object(windows_terminal.shutil,'which') as find, patch('sys.stdout',io.StringIO()):
            windows_terminal.open_dashboard(path,state)
            find.assert_not_called()
        path.with_suffix('.windows-monitor').unlink()
        with patch.object(windows_terminal.shutil,'which',return_value=None), \
                patch.object(windows_terminal.subprocess,'CREATE_NEW_CONSOLE',16,create=True), \
                patch.object(windows_terminal.subprocess,'Popen') as popen, \
                patch.object(windows_terminal,'command_shell') as shell,patch('sys.stdout',io.StringIO()):
            windows_terminal.open_dashboard(path,state)
            self.assertEqual(popen.call_args.kwargs['creationflags'],16)
            shell.assert_called_once_with(path)

    def test_native_submit_discovery_and_no_symlinks(self):
        release=self.root/'releases/current'
        binary=self.root/'commands/submit.exe'
        binary.parent.mkdir()
        binary.touch()
        storage.write_json(release/'installation.json',{'portable':True,'bin_directory':str(binary.parent)})
        with patch('sys.prefix',str(release/'venv')),patch('sys.platform','win32'):
            self.assertTrue(portable.bundled_submit(str(binary)))
            self.assertEqual(portable.installed_command('vp-submit'),release/'venv/Scripts/vp-submit.exe')
            portable.bind_submit(binary)
            self.assertFalse((storage.state_directory()/'submit').exists())

    def test_native_update_selects_native_installer(self):
        data={'portable':True,'install_root':str(self.root/'app'),'bin_directory':str(self.root/'commands'),'python':sys.executable}
        command=updater.installer_command(self.root/'source',data)
        self.assertEqual(command[1],str(self.root/'source/install.py'))
        self.assertNotIn('bash',command)
        with patch('sys.platform','win32'),patch.object(cli.sys.stdin,'isatty',return_value=True),patch.object(cli.shutil,'which') as find:
            cli.check_terminal()
            find.assert_not_called()


if __name__ == '__main__':
    unittest.main()
