import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vp_helper import __version__, commands
from vp_helper.models import VPError
from vp_helper.storage import config_directory, state_directory, write_json


class VersionInfoTests(unittest.TestCase):
    def test_version_reports_config_on_every_platform_without_disclosing_secrets_or_network(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
                'XDG_CONFIG_HOME': temporary + '/config', 'XDG_STATE_HOME': temporary + '/state'}):
            root = Path(temporary)
            secrets = ['private-api-key', 'private-api-secret', 'private-cookie',
                       'private-cloud-password', 'private-cloud-token', 'unknown-private-value']
            write_json(config_directory() / 'config.json', {
                'workspace_directory': str(root / 'contests'),
                'cf': {'handle': 'Contestant', 'api_key': secrets[0], 'api_secret': secrets[1],
                       'cookie': secrets[2], 'unknown_field': secrets[5]},
                'qoj': {'handle': 'qoj-user', 'firefox_profile': str(root / 'firefox')},
                'cwnuoj': {'handle': 'hydro-user'}, 'nowcoder': {'handle': 'nk-user'}})
            write_json(config_directory() / 'cloud-account.json', {
                'serverUrl': 'https://vp.cwnupaa.com', 'account': 'login-user',
                'password': secrets[3], 'token': secrets[4], 'expiresAt': '2030-01-01T00:00:00Z',
                'user': {'useraccount': 'cloud-user', 'username': '昵称', 'realname': '姓名',
                         'numberid': '20260001', 'email': 'user@example.test'}})
            write_json(state_directory() / 'browser-login/chrome/fixture.json', {'cookies': secrets})
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for system in ('linux', 'darwin', 'win32'):
                with self.subTest(system=system), patch('sys.platform', system), \
                        patch('sys.argv', ['xcpc', '--version']), patch('sys.stdout', new_callable=io.StringIO) as output, \
                        patch('vp_helper.updater.installation', return_value={
                            'install_root': str(root / 'app'), 'bin_directory': str(root / 'bin'),
                            'source_revision': 'a' * 40, 'global': True}), \
                        patch('vp_helper.version_info.shutil.which', side_effect=lambda name: str(root / 'bin' / name)), \
                        patch('requests.request', side_effect=AssertionError('Must stay offline')):
                    commands.main()
                    text = output.getvalue()
                    for expected in (__version__, str(root / 'app'), str(root / 'contests'),
                                     'Contestant', 'cloud-user', '20260001', 'user@example.test',
                                     'QOJ', 'CWNUOJ', '牛客', '隐藏'):
                        self.assertIn(expected, text)
                    for secret in secrets:
                        self.assertNotIn(secret, text)
                    self.assertIn('整台电脑（所有用户）', text)
                    self.assertIn('Chrome / Edge 扩展' if system != 'linux' else 'Firefox 普通窗口', text)
                    if system != 'linux':
                        self.assertIn('Chrome 已同步配置数：1', text)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_uninstalled_missing_or_corrupted_settings_still_show_version(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
                'XDG_CONFIG_HOME': temporary + '/config', 'XDG_STATE_HOME': temporary + '/state'}), \
                patch('vp_helper.updater.installation', side_effect=VPError('No managed install')), \
                patch('vp_helper.storage.desktop', return_value=Path(temporary) / 'Desktop'):
            for corrupt in (False, True):
                if corrupt:
                    path = config_directory() / 'config.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('invalid private settings')
                with self.subTest(corrupt=corrupt), patch('sys.argv', ['xcpc', '--version']), \
                        patch('sys.stdout', new_callable=io.StringIO) as output:
                    commands.main()
                    self.assertTrue(output.getvalue().startswith('XCPC ' + __version__))
                    self.assertIn('源码或非托管安装', output.getvalue())
                    self.assertIn('未登录', output.getvalue())
                    self.assertNotIn('invalid private settings', output.getvalue())


if __name__ == '__main__':
    unittest.main()
