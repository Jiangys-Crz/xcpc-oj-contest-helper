from contextlib import ExitStack
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import accounts, cli, commands
from vp_helper.codeforces_live import CodeforcesLive, verify_practice
from vp_helper.gym import Gym, signed_parameters
from vp_helper.models import VPError
from vp_helper.storage import config_directory, read_json, write_json


class CodeforcesAPISettingsTests(unittest.TestCase):
    def test_username_prompt_is_chinese_in_initial_setup_and_rebinding(self):
        for initial_setup in (False, True):
            write_json(self.path,{})
            with patch('builtins.input',side_effect=['Tester','new-key']) as prompt, \
                    patch.object(accounts.getpass,'getpass',return_value='new-secret'):
                if initial_setup:
                    accounts.prepare_api_account(self.path,{},'cf')
                else:
                    accounts.configure_api('cf')
            self.assertEqual(prompt.call_args_list[0].args,('请输入 Codeforces 用户名：',))
            self.assertEqual(read_json(self.path)['gym']['handle'],'Tester')

    def test_native_first_use_prompts_api_before_browser_and_shares_cf_gym(self):
        for operating_system in ('darwin','win32'):
            for platform in ('cf','gym'):
                write_json(self.path,{'workspace_directory':'/kept'})
                def verified(judge,account):
                    self.assertEqual(read_json(self.path)['gym']['api_key'],'new-key')
                    self.assertEqual(account['handle'],'tester')
                    account.update(cookie='synthetic',cookie_source='chrome')
                    return True
                with patch('sys.platform',operating_system),patch.object(cli,'refresh_browser_account',side_effect=verified), \
                        patch('builtins.input',side_effect=['tester','new-key']), \
                        patch.object(accounts.getpass,'getpass',return_value='new-secret'):
                    result=cli._load_account(self.path,platform,live=True)
                self.assertEqual(result['gym']['api_secret'],'new-secret')
                self.assertEqual(result['workspace_directory'],'/kept')
                self.assertEqual(accounts.account_key(result,'cf'),accounts.account_key(result,'gym'))

    def test_native_browser_failure_keeps_completed_api_configuration(self):
        for operating_system in ('darwin','win32'):
            write_json(self.path,{})
            with patch('sys.platform',operating_system),patch.object(cli,'refresh_browser_account',return_value=False), \
                    patch.object(cli,'verify_stored_session',return_value=False), \
                    patch.object(cli,'login_diagnostics',return_value=['No browser snapshot']), \
                    patch('builtins.input',side_effect=['tester','new-key']), \
                    patch.object(accounts.getpass,'getpass',return_value='new-secret'):
                with self.assertRaises(VPError):
                    cli._load_account(self.path,'cf')
            self.assertEqual(read_json(self.path)['gym'],{'handle':'tester','api_key':'new-key','api_secret':'new-secret'})

    def test_native_cancelled_setup_does_not_save_partial_credentials_or_check_browser(self):
        write_json(self.path,{'gym':{'handle':'tester','cookie':'keep-cookie','api_key':'half-key'}})
        before=self.path.read_bytes()
        with patch('sys.platform','win32'),patch('builtins.input',return_value='new-key'), \
                patch.object(accounts.getpass,'getpass',side_effect=EOFError),patch.object(cli,'refresh_browser_account') as browser:
            with self.assertRaises(VPError):
                cli._load_account(self.path,'gym')
        self.assertEqual(self.path.read_bytes(),before)
        browser.assert_not_called()

    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'config'),
                                                       'XDG_STATE_HOME':str(self.root/'state')}))
        self.path=config_directory()/'config.json'
        self.stack.enter_context(patch.object(accounts.sys.stdin,'isatty',return_value=True))
        self.output=io.StringIO()
        self.stack.enter_context(patch('sys.stdout',self.output))
        self.stack.enter_context(patch('time.sleep'))
        self.original={'handle':'tester','cookie':'sid=synthetic','firefox_profile':'/synthetic/profile',
                       'api_key':'old-key','api_secret':'old-secret'}

    def configure(self,platform='cf',values=('new-key','new-secret')):
        with patch.object(accounts.getpass,'getpass',side_effect=[values[1]]), \
                patch('builtins.input',side_effect=lambda prompt: 'tester' if '用户名' in prompt else values[0]):
            accounts.configure_api(platform)

    def test_reset_preserves_browser_workspace_and_other_platform_settings(self):
        saved={'workspace_directory':'/preserved/path','gym':self.original,
               'qoj':{'handle':'other','cookie':'synthetic-qoj'}}
        write_json(self.path,saved)
        self.configure()
        new=read_json(self.path)
        self.assertEqual(new['gym'],{**self.original,'api_key':'new-key','api_secret':'new-secret'})
        self.assertEqual(new['workspace_directory'],saved['workspace_directory'])
        self.assertEqual(new['qoj'],saved['qoj'])
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertNotIn('new-secret',self.output.getvalue())
        self.assertNotIn('old-secret',self.output.getvalue())
        self.assertNotIn('new-key',self.output.getvalue())

    def test_same_account_cf_and_gym_are_synced_but_other_account_is_preserved(self):
        for handle in ('Tester','someone_else'):
            write_json(self.path,{'cf':self.original,'gym':{**self.original,'handle':handle}})
            self.configure()
            new=read_json(self.path)
            self.assertEqual(new['cf']['api_secret'],'new-secret')
            self.assertEqual(new['gym']['api_secret'],'new-secret' if handle=='Tester' else 'old-secret')
            self.assertEqual(new['gym']['handle'],handle)

    def test_empty_or_cancelled_pair_does_not_replace_old_keys(self):
        write_json(self.path,{'gym':self.original})
        before=self.path.read_bytes()
        for values in (['new-key',''],['new-key',EOFError()],['new-key',KeyboardInterrupt()]):
            with self.assertRaises((VPError,KeyboardInterrupt)):
                self.configure(values=values)
            self.assertEqual(self.path.read_bytes(),before)

    def test_initial_setup_keeps_handle_and_reuses_cf_for_gym(self):
        write_json(self.path,{'cf':{'handle':''}})
        with patch('builtins.input',return_value='tester'):
            self.configure()
        config=read_json(self.path)
        self.assertEqual(config['cf']['handle'],'tester')
        self.assertEqual(accounts.account_key(config,'gym'),'cf')
        self.assertEqual(cli.adapter('https://codeforces.com/gym/123',config).handle,'tester')

    def test_noninteractive_reset_does_not_echo_or_change_keys(self):
        write_json(self.path,{'gym':self.original})
        before=self.path.read_bytes()
        with patch.object(accounts.sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(VPError,'交互终端'):
                self.configure()
        self.assertEqual(self.path.read_bytes(),before)

    def test_cf_bt_and_live_first_use_require_a_complete_pair(self):
        for is_live in (False,True):
            write_json(self.path,{'gym':{'handle':'tester','cookie':'synthetic'}})
            with patch.object(cli,'refresh_browser_account',return_value=True), \
                    patch('builtins.input',return_value='new-key'), \
                    patch.object(cli.getpass,'getpass',return_value='new-secret') as prompt:
                config=cli._load_account(self.path,'cf',live=is_live)
            self.assertEqual(prompt.call_count,1)
            self.assertEqual(config['gym']['api_key'],'new-key')
            self.assertEqual(config['gym']['api_secret'],'new-secret')

    def test_incomplete_pair_is_replaced_together_and_never_mixed(self):
        write_json(self.path,{'gym':{'handle':'tester','cookie':'synthetic','api_key':'half-old-key'}})
        with patch.object(cli,'refresh_browser_account',return_value=True), \
                patch('builtins.input',return_value='new-key'), \
                patch.object(cli.getpass,'getpass',return_value='new-secret'):
            config=cli._load_account(self.path,'cf',live=True)
        self.assertEqual(config['gym']['api_key'],'new-key')
        self.assertEqual(config['gym']['api_secret'],'new-secret')
        self.assertEqual(accounts.api_credentials({'handle':'different'},config,'cf'),{})

    def test_public_standings_keep_exact_anonymous_query_even_with_api_configured(self):
        write_json(self.path,{'gym':self.original})
        client=CodeforcesLive('https://codeforces.com/contest/123',{'handle':'tester'})
        client.public.get=Mock(return_value=Mock(json=Mock(return_value={'status':'OK','result':{'rows':[]}})))
        client.api('contest.standings',contestId=123,handles='tester',showUnofficial=True)
        self.assertEqual(client.public.get.call_args.kwargs['params'],{'contestId':123})
        self.assertFalse(client.public.get.call_args.kwargs['retry'])
        self.assertEqual(client.public.account,{})
        self.assertFalse(client.public._auto_refresh)

    def test_regular_cf_vp_standings_strip_signatures_and_filters(self):
        client=Gym('https://codeforces.com/contest/2181',dict(self.original))
        client.public.get=Mock(return_value=Mock(json=Mock(return_value={'status':'OK','result':{'rows':[]}})))
        client.api('contest.standings',contestId='2181',showUnofficial=False,participantTypes='CONTESTANT',handles='tester')
        self.assertEqual(client.public.get.call_args.kwargs['params'],{'contestId':'2181'})
        self.assertEqual(client.public.account,{})

    def test_bracketed_paste_is_normalized_for_key_and_secret(self):
        write_json(self.path,{'gym':self.original})
        self.configure(values=('\x1b[200~new-key\x1b[201~','\x1b[200~new-secret\x1b[201~'))
        result=read_json(self.path)['gym']
        self.assertEqual(result['api_key'],'new-key')
        self.assertEqual(result['api_secret'],'new-secret')
        self.assertNotIn('new-secret',self.output.getvalue())
        self.assertIn('Get your Codeforces API key and secret at https://codeforces.com/settings/api (sign in first).',
                      self.output.getvalue())

    def test_live_and_bt_authenticated_queries_pick_up_reset_keys_in_memory(self):
        write_json(self.path,{'gym':self.original})
        client=CodeforcesLive('https://codeforces.com/contest/123',{'handle':'tester','cookie':'synthetic'})
        web=Mock()
        web.get.return_value.json.return_value={'status':'OK','result':[]}
        with patch('vp_helper.gym.WebSession',return_value=web),patch('time.time',return_value=2000), \
                patch('vp_helper.gym.secrets.token_hex',return_value='abcdef'):
            for key,secret in [('old-key','old-secret'),('new-key','new-secret')]:
                if key=='new-key': self.configure()
                client.api('contest.status',contestId=123,handle='tester')
                params=web.get.call_args.kwargs['params']
                expected=signed_parameters('contest.status',{'contestId':123,'handle':'tester'},key,secret,at=2000,nonce='abcdef')
                self.assertEqual(params,expected)
                self.assertNotIn('api_secret',params)
        self.assertNotIn('api_key',read_json(self.root/'state/vp/cf-api.json'))

    def test_invalid_api_response_redacts_secrets_and_gives_reset_command(self):
        write_json(self.path,{'gym':self.original})
        client=Gym('https://codeforces.com/gym/123',dict(self.original))
        client.web=Mock()
        client.web.get.return_value.json.return_value={'status':'FAILED','comment':'bad old-key old-secret sid=synthetic'}
        with self.assertRaises(VPError) as error:
            client.api('contest.status',contestId=123)
        self.assertIn('xcpc api cf',str(error.exception))
        for value in ('old-key','old-secret','sid=synthetic'):
            self.assertNotIn(value,str(error.exception))

    def test_practice_checks_status_instead_of_illegal_filtered_public_standings(self):
        client=Mock(platform='cf',handle='tester',id='123',url='https://codeforces.com/contest/123')
        client.api.return_value=[]
        verify_practice(client,7200)
        client.api.assert_called_once_with('contest.status',contestId='123',handle='tester',**{'from':1,'count':100})

    def test_cli_reset_command_dispatches_without_opening_a_contest(self):
        with patch('sys.argv',['xcpc','api','cf']),patch.object(accounts,'configure_api') as configure:
            commands.main()
        configure.assert_called_once_with('cf')
