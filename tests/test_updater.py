from contextlib import ExitStack
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vp_helper import commands, updater
from vp_helper.models import VPError
from vp_helper.storage import write_json


@unittest.skipUnless(shutil.which("git"), "requires Git")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ,{"XDG_CACHE_HOME":str(self.root/"cache")}))
        self.prefix=self.root/"installed app/.vp-install"
        self.prefix.mkdir(parents=True)
        old=self.prefix/"releases/old"
        old.mkdir(parents=True)
        (self.prefix/"current").symlink_to(old)
        self.data={"install_root":str(self.prefix),"bin_directory":str(self.prefix.parent/"bin"),
                   "local":True,"source_revision":"","python":sys.executable,"submit_path":""}
        self.stack.enter_context(patch.object(updater,"installation",side_effect=lambda:dict(self.data)))
        self.repo=self.root/"original checkout"
        self.repo.mkdir()
        self.git("init","-b","main")
        self.git("config","user.name","XCPC Test")
        self.git("config","user.email","fixture@example.invalid")
        self.git("config","commit.gpgsign","false")
        self.script=self.repo/"install.sh"
        self.script.write_text('#!/bin/bash\nset -e\nwhile (( $# )); do\nif [[ "$1" == --target-root ]]; then target="$2"; fi\nshift\ndone\nprintf updated > "$target/updated"\n')
        self.git("add","install.sh")
        self.git("commit","-m","fixture")
        (self.repo/"uncommitted-user-notes").write_text("preserve this")
        self.preferences=self.root/"user-preferences.json"
        self.preferences.write_text('{"workspace_directory":"unchanged"}')

    def git(self,*args):
        return subprocess.check_output(["git","-C",str(self.repo),*args],stderr=subprocess.DEVNULL,text=True).strip()

    def test_update_uses_managed_checkout_and_preserves_original_files(self):
        updater.update(repository=str(self.repo))
        self.assertEqual((self.prefix/"updated").read_text(),"updated")
        self.assertEqual((self.repo/"uncommitted-user-notes").read_text(),"preserve this")
        self.assertEqual(self.preferences.read_text(),'{"workspace_directory":"unchanged"}')
        sources=list((self.root/"cache/vp/updates").glob("*/sources/*"))
        self.assertEqual(len(sources),1)
        self.assertNotEqual(sources[0],self.repo)
        self.assertEqual((sources[0]/"install.sh").read_bytes(),self.script.read_bytes())

    def test_latest_revision_skips_reinstallation(self):
        updater.update(repository=str(self.repo))
        self.data["source_revision"]=self.git("rev-parse","HEAD")
        (self.prefix/"updated").write_text("not reinstalled")
        out=io.StringIO()
        with patch("sys.stdout",out):
            updater.update(repository=str(self.repo))
        self.assertIn("已是最新版本",out.getvalue())
        self.assertEqual((self.prefix/"updated").read_text(),"not reinstalled")

    def test_install_failure_does_not_switch_current_release(self):
        self.script.write_text("#!/bin/bash\nexit 7\n")
        self.git("add","install.sh")
        self.git("commit","-m","failing fixture")
        before=(self.prefix/"current").resolve()
        with self.assertRaisesRegex(VPError,"安装未完成"):
            updater.update(repository=str(self.repo))
        self.assertEqual((self.prefix/"current").resolve(),before)
        self.assertTrue(list((self.root/"cache/vp/updates").glob("*/last-update.log")))

    def test_local_changes_to_managed_cache_are_not_discarded(self):
        updater.update(repository=str(self.repo))
        source=next((self.root/"cache/vp/updates").glob("*/sources/*"))
        (source/"install.sh").write_text("local cached edits")
        with self.assertRaisesRegex(VPError,"本地改动"):
            updater.update(repository=str(self.repo))
        self.assertEqual((source/"install.sh").read_text(),"local cached edits")

    def test_cache_origin_is_verified(self):
        updater.update(repository=str(self.repo))
        with self.assertRaisesRegex(VPError,"仓库地址不匹配"):
            updater.update(repository=str(self.root/"another repo"))

    def test_legacy_gitee_cache_moves_to_github_without_overwriting_old_edits(self):
        updater.update(repository=str(self.repo))
        bare=next((self.root/"cache/vp/updates").glob("*/repository.git"))
        old_source=next((bare.parent/"sources").iterdir())
        (old_source/"install.sh").write_text("local cached edit")
        self.script.write_text(self.script.read_text()+"# GitHub release\n")
        self.git("add","install.sh")
        self.git("commit","-m","next release")
        subprocess.run(["git","--git-dir",str(bare),"remote","set-url","origin",updater.LEGACY_REPOSITORY],check=True)
        original_git=updater._git
        def local_fetch(args,*,cwd=None):
            if "fetch" in args:
                self.assertEqual(original_git(["--git-dir",str(bare),"remote","get-url","origin"]),updater.REPOSITORY)
                args=[*args[:-2],str(self.repo),"main"]
            return original_git(args,cwd=cwd)
        with patch.object(updater,"_git",side_effect=local_fetch),patch.object(updater,"cloud_update") as cloud:
            updater.update()
        cloud.assert_not_called()
        self.assertEqual(original_git(["--git-dir",str(bare),"remote","get-url","origin"]),updater.REPOSITORY)
        self.assertEqual((old_source/"install.sh").read_text(),"local cached edit")
        self.assertEqual((self.repo/"uncommitted-user-notes").read_text(),"preserve this")
        self.assertEqual(self.preferences.read_text(),'{"workspace_directory":"unchanged"}')
        self.assertEqual((self.prefix/"updated").read_text(),"updated")

    def test_default_github_update_does_not_migrate_an_unrecognized_origin(self):
        updater.update(repository=str(self.repo))
        bare=next((self.root/"cache/vp/updates").glob("*/repository.git"))
        unexpected="https://gitee.com/unrelated/project.git"
        subprocess.run(["git","--git-dir",str(bare),"remote","set-url","origin",unexpected],check=True)
        with self.assertRaisesRegex(VPError,"仓库地址不匹配"):
            updater.update()
        self.assertEqual(updater._git(["--git-dir",str(bare),"remote","get-url","origin"]),unexpected)

    def test_installer_command_preserves_mode_targets_and_custom_submitter(self):
        command=updater.installer_command(self.repo,self.data)
        self.assertIn("--local",command)
        self.assertEqual(command[command.index("--target-root")+1],str(self.prefix))
        self.assertEqual(command[command.index("--target-bin")+1],self.data["bin_directory"])
        data={**self.data,"local":False,"submit_path":"/tmp/custom submit"}
        command=updater.installer_command(self.repo,data)
        self.assertNotIn("--local",command)
        self.assertEqual(command[-1],"/tmp/custom submit")

    def test_xcpc_update_dispatches_to_updater(self):
        with patch("sys.argv",["xcpc","update"]),patch.object(updater,"main") as run:
            commands.main()
        run.assert_called_once()

    def test_global_native_updates_use_os_installers_for_admin_elevation(self):
        data = {**self.data, 'portable': True, 'global': True, 'local': False}
        for system, executable, script in (
                ('darwin', 'sh', 'install-macos.sh'), ('win32', 'powershell.exe', 'install.ps1')):
            with self.subTest(system=system), patch.object(updater.sys, 'platform', system):
                command = updater.installer_command(self.repo, data)
                self.assertEqual(command[0], executable)
                self.assertIn(str(self.repo / script), command)
                self.assertEqual(command[command.index('--target-root') + 1], data['install_root'])
                self.assertEqual(command[command.index('--target-bin') + 1], data['bin_directory'])


class InstallationMetadataTests(unittest.TestCase):
    def test_metadata_matches_the_running_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            release=root/"releases/fixture"
            prefix=release/"venv"
            prefix.mkdir(parents=True)
            data={"install_root":str(root),"bin_directory":str(root/"bin"),"local":True}
            write_json(release/"installation.json",data)
            with patch("vp_helper.updater.sys.prefix",str(prefix)):
                self.assertEqual(updater.installation(),data)
            write_json(release/"installation.json",{**data,"install_root":str(root/"other")})
            with patch("vp_helper.updater.sys.prefix",str(prefix)),self.assertRaises(VPError):
                updater.installation()
