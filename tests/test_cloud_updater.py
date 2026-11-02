import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import updater, commands
from vp_helper.models import VPError


class CloudUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.environment=patch.dict(os.environ,{'XDG_CACHE_HOME':str(self.root/'cache')})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.revision='a'*40
        self.data={'install_root':str(self.root/'app'),'bin_directory':str(self.root/'bin'),'local':True,'source_revision':'b'*40,'python':'','submit_path':''}

    def archive(self,members=None):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz') as archive:
            for name,kind in members or [('VP/install.py','file'),('VP/install.sh','file')]:
                item=tarfile.TarInfo(name)
                if kind=='file':
                    content=b'# fixture source\n';item.size=len(content);archive.addfile(item,io.BytesIO(content))
                else:
                    item.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE
                    item.linkname='../../outside';archive.addfile(item)
        return out.getvalue()

    def responses(self,blob,**overrides):
        manifest={'version':'0.9.0','revision':self.revision,'sha256':hashlib.sha256(blob).hexdigest(),
                  'sourceUrl':updater.CLOUD_SERVER+'/downloads/terminal/'+self.revision+'.tar.gz',**overrides}
        first=Mock();first.json.return_value={'data':manifest}
        second=Mock();second.iter_content.return_value=[blob]
        return first,second

    def test_cloud_archive_installs_to_original_paths_with_verified_revision(self):
        with patch.object(updater,'_download',side_effect=self.responses(self.archive())) as fetch, \
                patch.object(updater.subprocess,'run') as install:
            updater.cloud_update(self.data)
        self.assertEqual(fetch.call_count,2)
        cmd=install.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--target-root')+1],self.data['install_root'])
        self.assertEqual(cmd[cmd.index('--target-bin')+1],self.data['bin_directory'])
        self.assertEqual(install.call_args.kwargs['env']['XCPC_SOURCE_REVISION'],self.revision)
        self.assertFalse(list((self.root/'cache').rglob('cloud-source-*')))

    def test_checksum_failure_never_runs_installer(self):
        with patch.object(updater,'_download',side_effect=self.responses(self.archive(),sha256='0'*64)), \
                patch.object(updater.subprocess,'run') as install,self.assertRaisesRegex(VPError,'SHA256'):
            updater.cloud_update(self.data)
        install.assert_not_called()

    def test_manifest_cannot_redirect_client_to_third_party_or_plaintext(self):
        for target in ('https://foreign.example/evil.tar.gz','http://vp.cwnupaa.com/a.tar.gz',updater.CLOUD_SERVER+'/downloads/terminal/other.tar.gz'):
            with patch.object(updater,'_download',side_effect=self.responses(self.archive(),sourceUrl=target)) as fetch, \
                    self.assertRaisesRegex(VPError,'来源'):
                updater.cloud_update(self.data)
            self.assertEqual(fetch.call_count,1)

    def test_unsafe_tar_members_are_rejected_before_writing_any_source(self):
        for name,kind in [('/absolute','file'),('../outside','file'),('VP/../../outside','file'),('VP/link','symlink'),('VP/link','hardlink'),('C:\\outside','file')]:
            blob=self.archive([('VP/allowed','file'),(name,kind)])
            archive=self.root/'unsafe.tar.gz';archive.write_bytes(blob)
            destination=self.root/'source'
            with self.assertRaisesRegex(VPError,'不安全'):updater.safe_extract(archive,destination)
            self.assertFalse(destination.exists())
            self.assertFalse((self.root/'outside').exists())

    def test_same_revision_does_not_download_archive_or_install(self):
        with patch.object(updater,'_download',side_effect=self.responses(self.archive())) as fetch, \
                patch.object(updater.subprocess,'run') as install:
            updater.cloud_update({**self.data,'source_revision':self.revision})
        self.assertEqual(fetch.call_count,1);install.assert_not_called()

    def test_server_failures_never_silently_switch_to_github(self):
        with patch.object(updater,'installation',return_value=self.data), \
                patch.object(updater,'cloud_update',side_effect=updater.CloudUnavailable('offline')), \
                patch.object(updater,'ensure_git') as git:
            with self.assertRaisesRegex(VPError,'offline'):updater.update()
        git.assert_not_called()
        with patch.object(updater,'installation',return_value=self.data), \
                patch.object(updater,'cloud_update',side_effect=VPError('bad checksum')), \
                patch.object(updater,'ensure_git') as git:
            with self.assertRaisesRegex(VPError,'bad checksum'):updater.update(cloud=True)
        git.assert_not_called()

    def test_malformed_manifest_and_non_archive_body_fail_closed(self):
        for malformed in ([],None,{'revision':123,'sha256':[]},'text'):
            response=Mock();response.json.return_value={'data':malformed}
            with patch.object(updater,'_download',return_value=response), \
                    patch.object(updater.subprocess,'run') as install,self.assertRaises(VPError):
                updater.cloud_update(self.data)
            install.assert_not_called()
        with patch.object(updater,'_download',side_effect=self.responses(b'not a gzip archive')), \
                patch.object(updater.subprocess,'run') as install,self.assertRaisesRegex(VPError,'格式'):
            updater.cloud_update(self.data)
        install.assert_not_called()

    def test_explicit_gitee_command_preserves_dispatch(self):
        with patch('sys.argv',['xcpc','update','--gitee']),patch.object(updater,'update') as run:
            commands.main()
        run.assert_called_once_with(cloud=True)

    def test_default_update_uses_the_server_without_invoking_git(self):
        with patch.object(updater,'installation',return_value=self.data), \
                patch.object(updater,'cloud_update') as cloud, \
                patch.object(updater,'ensure_git') as git:
            updater.update()
        git.assert_not_called()
        cloud.assert_called_once_with(self.data)
        self.assertEqual(updater.REPOSITORY,'https://github.com/Jiangys-Crz/xcpc-oj-contest-helper.git')

    def test_explicit_github_update_does_not_contact_the_server(self):
        with patch.object(updater,'installation',return_value=self.data), \
                patch.object(updater,'cloud_update') as cloud, \
                patch.object(updater,'ensure_git',side_effect=VPError('GitHub reached')):
            with self.assertRaisesRegex(VPError,'GitHub reached'):
                updater.update(cloud=False)
        cloud.assert_not_called()

    def test_new_update_options_dispatch_without_browser_login_setup(self):
        for options,kwargs in (([],{}),(['--github'],{'cloud':False}),(['--cloud'],{'cloud':True})):
            with self.subTest(options=options), patch('sys.argv',['xcpc','update',*options]), \
                    patch.object(updater,'update') as run, \
                    patch('vp_helper.chromium_login.ensure_user_bridge') as browser:
                commands.main()
            run.assert_called_once_with(**kwargs)
            browser.assert_not_called()
