from contextlib import ExitStack
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import browser_login as login, commands
from vp_helper.models import VPError


def cookies(profile, rows):
    profile.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(profile/"cookies.sqlite")
    connection.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, originAttributes TEXT, lastAccessed INTEGER)")
    connection.executemany("INSERT INTO moz_cookies VALUES (?,?,?,'/',9999999999,'',?)", rows)
    connection.commit()
    connection.close()


class FirefoxLoginTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(login.Path,"home",return_value=self.root))
        self.stack.enter_context(patch.object(login.os,"geteuid",return_value=1000))
        self.stack.enter_context(patch.dict(os.environ,{"XDG_CONFIG_HOME":str(self.root/"config")}))
        self.stack.enter_context(patch.object(login,"_cache",{}))

    def test_new_xdg_profile_and_classic_snap_profile_are_both_discovered(self):
        xdg = self.root/"config/mozilla/firefox"
        snap = self.root/"snap/firefox/common/.mozilla/firefox"
        for base in (xdg,snap):
            cookies(base/"fixture.default",[("sid","synthetic","oj.cwnupaa.com",1)])
            (base/"profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=fixture.default\nDefault=1\n")
        profiles = login.firefox_profiles({})
        self.assertIn(xdg/"fixture.default",profiles)
        self.assertIn(snap/"fixture.default",profiles)

    def test_snap_cookie_profile_is_found_when_profiles_ini_does_not_list_it(self):
        profile = self.root/"snap/firefox/common/.mozilla/firefox/fixture.default"
        cookies(profile,[("sid","synthetic","oj.cwnupaa.com",1)])
        self.assertIn(profile,login.firefox_profiles({}))
        self.assertEqual(login.browser_cookies("cwnuoj",{})[0].cookie,"sid=synthetic")

    def test_flatpak_new_and_old_roots_and_profiles_subdirectory(self):
        bases = [self.root/".var/app/org.mozilla.firefox/config/mozilla/firefox",
                 self.root/".var/app/org.mozilla.firefox/.mozilla/firefox"]
        for base in bases:
            profile = base/"Profiles/fixture.default"
            cookies(profile,[("sid","synthetic","oj.cwnupaa.com",1)])
        for base in bases:
            self.assertIn(base/"Profiles/fixture.default",login.firefox_profiles({}))

    def test_cached_profile_does_not_hide_migrated_same_account(self):
        old = self.root/".mozilla/firefox/old.default"
        new = self.root/"config/mozilla/firefox/new.default"
        cookies(old,[("sid","old-synthetic","oj.cwnupaa.com",1)])
        cookies(new,[("sid","new-synthetic","oj.cwnupaa.com",1)])
        account = {"handle":"tester","firefox_profile":str(old)}
        verified = {"handle":"tester","cookie":"sid=new-synthetic","cookie_source":"firefox","firefox_profile":str(new)}
        with patch.object(login,"_verify",side_effect=lambda platform,candidate,expected: verified if candidate.profile==new else None):
            self.assertTrue(login.refresh_browser_account("cwnuoj",account))
        self.assertEqual(account["firefox_profile"],str(new))

    def test_strict_or_initial_explicit_profile_remains_selected(self):
        profile = self.root/"only-this-profile"
        other = self.root/"config/mozilla/firefox/other.default"
        cookies(other,[("sid","synthetic","oj.cwnupaa.com",1)])
        for account in ({"firefox_profile":str(profile)},
                        {"firefox_profile":str(profile),"handle":"tester","firefox_profile_strict":True}):
            self.assertEqual(login.firefox_profiles(account),[profile])

    def test_site_cookie_is_not_replaced_by_newer_parent_cookie(self):
        profile = self.root/"config/mozilla/firefox/fixture.default"
        cookies(profile,[("sid","site-synthetic","oj.cwnupaa.com",1),
                         ("sid","parent-synthetic",".cwnupaa.com",100),
                         ("sso","parent-sso",".cwnupaa.com",100)])
        session = [("sid","session-parent",".cwnupaa.com")]
        with patch.object(login,"_session_cookie_rows",return_value=session):
            candidates=login.browser_cookies("cwnuoj",{})
        self.assertIn("sid=site-synthetic",candidates[0].cookie)
        self.assertNotIn("sid=parent-synthetic",candidates[0].cookie)
        self.assertNotIn("sid=session-parent",candidates[0].cookie)
        self.assertIn("sso=parent-sso",candidates[0].cookie)

    def test_live_sqlite_wal_cookie_is_read_without_closing_firefox(self):
        profile = self.root/"config/mozilla/firefox/fixture.default"
        cookies(profile,[])
        connection=sqlite3.connect(profile/"cookies.sqlite")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO moz_cookies VALUES ('sid','wal-synthetic','oj.cwnupaa.com','/',9999999999,'',1)")
            connection.commit()
            self.assertIn("sid=wal-synthetic",login.browser_cookies("cwnuoj",{})[0].cookie)
        finally:
            connection.close()

    def test_parent_login_can_be_verified_when_host_cookie_is_an_old_guest(self):
        profile=self.root/"config/mozilla/firefox/fixture.default"
        cookies(profile,[("sid","old-guest","oj.cwnupaa.com",1),
                         ("sid","valid-parent",".cwnupaa.com",100)])
        account={"handle":"tester"}
        verified={"handle":"tester","cookie":"sid=valid-parent","cookie_source":"firefox","firefox_profile":str(profile)}
        with patch.object(login,"_verify",side_effect=lambda platform,candidate,expected: verified if candidate.cookie=="sid=valid-parent" else None):
            self.assertTrue(login.refresh_browser_account("cwnuoj",account))
        self.assertEqual(account["cookie"],"sid=valid-parent")

    def test_diagnostics_distinguish_no_profile_and_no_site_cookie(self):
        self.assertIn("未找到",login.login_diagnostics("cwnuoj",{})[0])
        cookies(self.root/"config/mozilla/firefox/fixture.default",[])
        self.assertIn("未读到"," ".join(login.login_diagnostics("cwnuoj",{})))

    def test_diagnostics_explain_http_failure_without_exposing_cookie(self):
        profile=self.root/"config/mozilla/firefox/fixture.default"
        cookies(profile,[("sid","PRIVATE_SYNTHETIC_TOKEN","oj.cwnupaa.com",1)])
        web=Mock()
        web.check.side_effect=VPError("oj.cwnupaa.com returned HTTP 503")
        with patch("vp_helper.http.WebSession",return_value=web):
            message="\n".join(login.login_diagnostics("cwnuoj",{}))
        self.assertIn("HTTP 503",message)
        self.assertNotIn("PRIVATE_SYNTHETIC_TOKEN",message)
        self.assertNotIn("sid=",message)

    def test_doctor_command_is_read_only_and_does_not_echo_credentials(self):
        output=io.StringIO()
        with patch("sys.argv",["xcpc","doctor","paa"]),patch("sys.stdout",output), \
                patch.object(login,"login_diagnostics",return_value=["safe-result"]) as diagnose:
            commands.main()
        self.assertIn("safe-result",output.getvalue())
        diagnose.assert_called_once_with("cwnuoj",{})
        self.assertFalse((self.root/"config/vp/config.json").exists())

    def test_vp_and_bt_share_verified_cached_firefox_session(self):
        from vp_helper import cli
        from vp_helper.storage import config_directory, read_json, write_json
        path=config_directory()/'config.json'
        original={'qoj':{'handle':'tester','cookie':'sid=synthetic','cookie_source':'firefox',
                         'firefox_profile':str(self.root/'old-profile')}}
        write_json(path,original)
        with patch.object(cli,'refresh_browser_account',return_value=False), \
                patch.object(login,'_verify',return_value={'handle':'tester','cookie':'sid=renewed',
                                                         'cookie_source':'firefox','firefox_profile':'.'}):
            for live in (False,True):
                config=cli._load_account(path,'qoj',live=live)
                self.assertTrue(config['qoj']['_login_verified'])
                self.assertEqual(config['qoj']['handle'],'tester')
                self.assertEqual(config['qoj']['firefox_profile'],str(self.root/'old-profile'))
        self.assertNotIn('_login_verified',read_json(path)['qoj'])

    def test_unverified_cached_cookie_is_not_treated_as_logged_in(self):
        from vp_helper import cli
        from vp_helper.storage import config_directory, write_json
        path=config_directory()/'config.json'
        write_json(path,{'qoj':{'handle':'tester','cookie':'sid=expired','_login_verified':True}})
        with patch.object(cli,'refresh_browser_account',return_value=False), \
                patch.object(cli,'verify_stored_session',return_value=False), \
                patch.object(cli,'login_diagnostics',return_value=['safe login failure']):
            with self.assertRaisesRegex(VPError,'safe login failure'):
                cli._load_account(path,'qoj',live=True)

    def test_qoj_navigation_identity_is_shared_across_layouts_and_account_checked(self):
        for tag in ('<nav>','<div class="navbar">','<ul class="nav-pills">'):
            self.assertEqual(login.identity_from_page('qoj',tag+'<a href="/user/profile/tester">me</a>', 'tester'),
                             {'handle':'tester'})
        with self.assertRaises(VPError):
            login.identity_from_page('qoj','<nav><a href="/user/profile/someone_else">me</a></nav>','tester')
        with self.assertRaises(VPError):
            login.identity_from_page('qoj','<main><a href="/user/profile/tester">ranking user</a></main>','tester')

    def test_verified_root_session_allows_qoj_bt_without_duplicate_nav_guard(self):
        from bs4 import BeautifulSoup
        from vp_helper import cli, practice
        client=Mock(handle='tester',account={'handle':'tester','_login_verified':True})
        client._page.return_value=BeautifulSoup('<table><tr><td>A</td><td><a href="/contest/123/problem/10">A</a></td></tr></table>','html.parser')
        with patch.object(cli,'adapter',return_value=client):
            contest=practice.Practice('https://qoj.ac/contest/123',{}).prepare()
        self.assertEqual(contest.problems,{'A':'10'})
        client.start.assert_not_called()

    def test_verified_root_session_can_prepare_cwnuoj_and_nowcoder_practice(self):
        from vp_helper import practice
        from vp_helper.hydro import HydroLive
        from vp_helper.nowcoder import NowcoderLive
        import test_hydro_live as hydro
        import test_nowcoder_live as nk
        with patch('time.time',return_value=9000):
            for kind,cls,url,account,data in (
                ('cwnuoj',HydroLive,hydro.URL,{'handle':'tester','uid':42,'_login_verified':True},hydro.details(attend=0)),
                ('nowcoder',NowcoderLive,nk.URL,{'handle':'42','uid':42,'_login_verified':True},{**nk.details(now=9000),'isSignUp':False})):
                client=cls(url,account)
                client.identity=Mock(side_effect=AssertionError('Already verified by shared login loader'))
                client.details=Mock(return_value=data)
                if kind=='nowcoder':
                    client.problem_list=Mock()
                with patch('vp_helper.live.live_adapter',return_value=client):
                    contest=practice.Practice(url,{kind:account}).prepare()
                self.assertTrue(contest.extra['practice'])
                self.assertTrue(client._identified)
