"""Repair the original managed launchers, without replacing loaded EXEs."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import subprocess
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vp_helper.storage import read_json, write_json

spec=importlib.util.spec_from_file_location('xcpc_installer',Path(__file__).resolve().parent.parent/'install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class NativeInstallRepairTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=(Path(temporary.name)/'app').resolve()
        self.first=self.root/'releases/initial'
        write_json(self.first/'installation.json',{'portable':True,'install_root':str(self.root)})
        self.package=self.first/'venv/Lib/site-packages/vp_helper'
        self.package.mkdir(parents=True)
        self.source=Path(__file__).resolve().parent.parent/'vp_helper'
        for name in ('bootstrap.py','bridge_bootstrap.py'):
            (self.package/name).write_text('raise PermissionError("old forwarder")\n')
        self.exe=self.root/'bin/xcpc.exe'
        self.exe.parent.mkdir()
        self.exe.write_bytes(b'owned fixture EXE; must remain unchanged')

    def symlink(self, target, link, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest('Symlinks are unavailable: ' + type(exc).__name__)

    @unittest.skipUnless(os.name=='posix','POSIX application permissions')
    def test_private_download_modes_become_public_runtime_without_exposing_account_data_or_links(self):
        runtime=self.root/'private-extracted-runtime'
        library=runtime/'venv/lib/package'
        library.mkdir(parents=True)
        code=library/'module.py'
        code.write_text('VALUE = 1\n')
        command=runtime/'venv/bin/xcpc'
        command.parent.mkdir()
        command.write_text('#!/bin/sh\nexit 0\n')
        secret=self.root/'cloud-account.json'
        secret.write_text('private fixture account\n')
        secret.chmod(0o600)
        parent_mode=self.root.stat().st_mode&0o777
        self.symlink(secret,library/'external-file')
        self.symlink(self.root,library/'external-directory',directory=True)
        for folder in (runtime,runtime/'venv',runtime/'venv/lib',library,command.parent):folder.chmod(0o700)
        code.chmod(0o666);command.chmod(0o777)
        installer.make_runtime_readable(runtime)
        for folder in (runtime,runtime/'venv',runtime/'venv/lib',library,command.parent):
            self.assertEqual(folder.stat().st_mode&0o555,0o555)
        self.assertEqual(code.stat().st_mode&0o777,0o644)
        self.assertEqual(command.stat().st_mode&0o777,0o755)
        self.assertEqual(secret.stat().st_mode&0o777,0o600)
        self.assertEqual(self.root.stat().st_mode&0o777,parent_mode)
        self.assertTrue((library/'external-directory').is_symlink())
        self.assertTrue((library/'external-file').is_symlink())

    def test_native_default_targets_are_machine_wide_and_explicit_legacy_targets_stay_local(self):
        args=SimpleNamespace(target_root=None,target_bin=None)
        root,commands,global_install,shared=installer.installation_targets(args,windows=False)
        self.assertEqual(root,Path('/Library/Application Support/XCPC/app'))
        self.assertEqual(commands,Path('/usr/local/bin'));self.assertTrue(global_install)
        machine=self.root.parent/'Program Files'
        personal=self.root.parent/'User AppData'
        with patch.dict(os.environ,{'ProgramW6432':str(machine),'ProgramFiles':str(self.root.parent/'PF32'),'LOCALAPPDATA':str(personal)}):
            root,commands,global_install,shared=installer.installation_targets(args,windows=True)
            self.assertEqual(root,machine/'XCPC/app');self.assertEqual(commands,machine/'XCPC/bin')
            self.assertTrue(global_install)
            args=SimpleNamespace(target_root=str(personal/'old app'),target_bin=str(personal/'old commands'))
            root,commands,global_install,_=installer.installation_targets(args,windows=True)
            self.assertFalse(global_install)
            self.assertEqual(root,personal/'old app');self.assertEqual(commands,personal/'old commands')
        for root,commands in (('/Library/Application Support/XCPC/app',str(self.root/'private bin')),
                              (str(self.root/'private app'),'/usr/local/bin')):
            with self.subTest(root=root,commands=commands),self.assertRaisesRegex(SystemExit,'mix shared and private'):
                installer.installation_targets(SimpleNamespace(target_root=root,target_bin=commands),windows=False)

    @unittest.skipUnless(os.name=='posix','POSIX application permissions')
    def test_global_parent_protection_removes_write_access_without_touching_other_files(self):
        parent=self.root/'shared parent';parent.mkdir();parent.chmod(0o777)
        private=parent/'unrelated-private-file';private.write_text('private fixture\n');private.chmod(0o600)
        installer.protect_runtime_parent(parent)
        self.assertEqual(parent.stat().st_mode&0o777,0o755)
        self.assertEqual(private.stat().st_mode&0o777,0o600)
        if os.geteuid()==0:self.assertEqual(parent.stat().st_uid,0)
        link=self.root/'custom shared symlink';self.symlink(parent,link,directory=True)
        with self.assertRaisesRegex(RuntimeError,'real directories'):installer.protect_runtime_parent(link)

    def test_path_setup_prioritizes_shared_command_over_legacy_user_command(self):
        home=self.root.parent/'home';home.mkdir()
        global_bin=home/'shared bin';global_bin.mkdir()
        private_bin=home/'old user bin';private_bin.mkdir()
        for directory in (global_bin,private_bin):
            entry=directory/'xcpc';entry.write_text('#!/bin/sh\nexit 0\n');entry.chmod(0o755)
        installer.configure_macos_path(global_bin,home=home,shell='/bin/bash')
        environment={**os.environ,'PATH':str(private_bin)+':/usr/bin:/bin:'+str(global_bin)}
        result=subprocess.run(['/bin/sh','-c','. "$1"; command -v xcpc','fixture',str(home/'.bashrc')],
                              env=environment,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),str(global_bin/'xcpc'))

    def test_original_modules_are_repaired_and_can_be_rolled_back(self):
        before={p:p.read_bytes() for p in self.package.glob('*.py')}
        exe=self.exe.read_bytes()
        backups=installer.repair_windows_bootstraps(self.root,[str(self.first),str(self.first)],self.source)
        for name in ('bootstrap.py','bridge_bootstrap.py'):
            self.assertEqual((self.package/name).read_bytes(),(self.source/name).read_bytes())
        self.assertEqual(self.exe.read_bytes(),exe)
        installer.restore_bootstraps(backups)
        for p,data in before.items(): self.assertEqual(p.read_bytes(),data)

    def test_failed_repair_restores_already_patched_modules(self):
        before={p:p.read_bytes() for p in self.package.glob('*.py')}
        real_replace=installer.replace_file
        def fail_once(path,data):
            if path.name=='bridge_bootstrap.py' and b'old forwarder' not in data:
                raise PermissionError('fixture failure')
            return real_replace(path,data)
        with patch.object(installer,'replace_file',side_effect=fail_once):
            with self.assertRaises(PermissionError):
                installer.repair_windows_bootstraps(self.root,[str(self.first)],self.source)
        for p,data in before.items(): self.assertEqual(p.read_bytes(),data)

    def test_foreign_initial_runtime_is_never_patched(self):
        for initial in (self.root.parent/'another-app',self.first):
            if initial==self.first:
                write_json(self.first/'installation.json',{'portable':True,'install_root':str(self.root.parent/'another-app')})
            with self.assertRaises(RuntimeError):
                installer.repair_windows_bootstraps(self.root,[str(initial)],self.source)
        self.assertIn('old forwarder',(self.package/'bootstrap.py').read_text())

    def test_package_symlink_cannot_repair_an_external_directory(self):
        external=self.root.parent/'external-package'
        self.package.rename(external)
        before={p.name:p.read_bytes() for p in external.iterdir()}
        self.symlink(external,self.package,directory=True)
        with self.assertRaises(RuntimeError):
            installer.repair_windows_bootstraps(self.root,[self.first],self.source)
        self.assertTrue(self.package.is_symlink())
        self.assertEqual({p.name:p.read_bytes() for p in external.iterdir()},before)

    def test_cache_symlink_cannot_delete_external_bytecode(self):
        external=self.root.parent/'external-cache'
        external.mkdir()
        cached=external/'bootstrap.cpython-313.pyc'
        cached.write_bytes(b'foreign cached module; must remain unchanged')
        before={p:p.read_bytes() for p in self.package.glob('*.py')}
        self.symlink(external,self.package/'__pycache__',directory=True)
        with self.assertRaises(RuntimeError):
            installer.repair_windows_bootstraps(self.root,[self.first],self.source)
        self.assertTrue((self.package/'__pycache__').is_symlink())
        self.assertEqual(cached.read_bytes(),b'foreign cached module; must remain unchanged')
        for path,data in before.items():
            self.assertEqual(path.read_bytes(),data)

    def test_module_symlinks_are_preserved_and_previous_repairs_are_rolled_back(self):
        before={p:p.read_bytes() for p in self.package.glob('*.py')}
        for name in ('bootstrap.py','bridge_bootstrap.py'):
            with self.subTest(module=name):
                target=self.package/name
                external=self.root.parent/('external-'+name)
                external.write_bytes(b'foreign module; must remain unchanged')
                target.unlink()
                self.symlink(external,target)
                with self.assertRaises(RuntimeError):
                    installer.repair_windows_bootstraps(self.root,[self.first],self.source)
                self.assertTrue(target.is_symlink())
                self.assertEqual(external.read_bytes(),b'foreign module; must remain unchanged')
                for path,data in before.items():
                    if path != target:
                        self.assertEqual(path.read_bytes(),data)
                target.unlink()
                target.write_bytes(before[target])

    def test_only_repaired_modules_have_their_bytecode_removed(self):
        cache=self.package/'__pycache__'
        cache.mkdir()
        stale=[cache/'bootstrap.cpython-313.pyc',cache/'bridge_bootstrap.cpython-313.opt-1.pyc']
        unrelated=cache/'browser_bridge.cpython-313.pyc'
        for path in [*stale,unrelated]:
            path.write_bytes(b'fixture cache')
        installer.repair_windows_bootstraps(self.root,[self.first],self.source)
        self.assertTrue(all(not path.exists() for path in stale))
        self.assertEqual(unrelated.read_bytes(),b'fixture cache')

    def test_module_repair_does_not_follow_an_existing_temporary_symlink(self):
        external=self.root.parent/'external-module'
        external.write_bytes(b'foreign contents; must remain unchanged')
        target=self.package/'bootstrap.py'
        temporary=target.with_name(target.name+'.xcpc-repair')
        self.symlink(external,temporary)
        installer.repair_windows_bootstraps(self.root,[self.first],self.source)
        self.assertEqual(external.read_bytes(),b'foreign contents; must remain unchanged')
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(),(self.source/'bootstrap.py').read_bytes())

    def test_upgrading_owned_old_install_adds_printfile_without_replacing_commands(self):
        for operating_system in ('darwin','win32'):
            with self.subTest(operating_system=operating_system):
                source=self.root.parent/('source-'+operating_system)
                (source/'vp_helper').mkdir(parents=True)
                for name in ('bootstrap.py','bridge_bootstrap.py'):
                    (source/'vp_helper'/name).write_bytes((self.source/name).read_bytes())
                (source/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0"\n')
                (source/'third_party').mkdir();(source/'browser_extension').mkdir()
                windows=operating_system=='win32'
                suffix='.exe' if windows else ''
                commands=self.root.parent/('commands-'+operating_system);commands.mkdir()
                originals={commands/(name+suffix):b'original 0.8.2 entry' for name in ('xcpc','vp','live','bt','submit')}
                for path,data in originals.items():path.write_bytes(data)
                marker=commands/'.xcpc-native-commands.json'
                write_json(marker,{'owner':'xcpc-native','initial_release':str(self.first),'install_root':str(self.root)})
                before=marker.read_bytes()
                def create(directory):
                    scripts=directory/('Scripts' if windows else 'bin');scripts.mkdir(parents=True)
                    (scripts/('python.exe' if windows else 'python')).write_bytes(b'fixture interpreter')
                def run(arguments,**kwargs):
                    if windows and any("m.make('printfile =" in str(a) for a in arguments):
                        (commands/'printfile.exe').write_bytes(b'new entry using new interpreter')
                args=SimpleNamespace(target_root=str(self.root),target_bin=str(commands),offline=True,
                                     skip_browser_registration=True,skip_path_configuration=True)
                with patch('sys.platform',operating_system),patch('sys.stdout',new=io.StringIO()), \
                        patch.object(installer,'__file__',str(source/'install.py')), \
                        patch.object(installer.shutil,'which',return_value=None), \
                        patch('vp_helper.storage.config_directory',return_value=self.root.parent/'fixture-config'), \
                        patch.object(installer.venv,'EnvBuilder',return_value=SimpleNamespace(create=create)), \
                        patch.object(installer,'run',side_effect=run):
                    installer.install(args)
                self.assertTrue((commands/('printfile'+suffix)).is_file())
                self.assertEqual(marker.read_bytes(),before)
                for path,data in originals.items():self.assertEqual(path.read_bytes(),data)
                if not windows:
                    current=read_json(self.root/'current.json')['release']
                    self.assertIn(current,(commands/'printfile').read_text())

    def test_windows_install_failure_before_publication_restores_both_initial_runtimes(self):
        source=self.root.parent/'source-fixture'
        source.mkdir()
        (source/'vp_helper').mkdir()
        for name in ('bootstrap.py','bridge_bootstrap.py'):
            (source/'vp_helper'/name).write_bytes((self.source/name).read_bytes())
        (source/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0"\n')
        (source/'third_party').mkdir()
        (source/'browser_extension').mkdir()
        host_initial=self.root/'releases/host-initial'
        write_json(host_initial/'installation.json',{'portable':True,'install_root':str(self.root)})
        host_package=host_initial/'venv/Lib/site-packages/vp_helper'
        host_package.mkdir(parents=True)
        for name in ('bootstrap.py','bridge_bootstrap.py'):
            (host_package/name).write_text('raise PermissionError("old host forwarder")\n')
        config=self.root.parent/'config-fixture'
        host=config/'browser-bridge/host.exe'
        write_json(host.parent/'host-installation.json',{'release':str(host_initial)})
        host.write_bytes(b'owned host EXE; must remain unchanged')
        for name in ('vp','live','bt','submit'):
            (self.exe.parent/(name+'.exe')).write_bytes(b'owned command EXE: '+name.encode())
        marker=self.exe.parent/'.xcpc-native-commands.json'
        write_json(marker,{'owner':'xcpc-native','initial_release':str(self.first),'install_root':str(self.root)})
        current=self.root/'current.json'
        write_json(current,{'release':str(self.first)})
        external_pointer=self.root.parent/'external-pointer'
        external_pointer.write_bytes(b'foreign pointer; must remain unchanged')
        unchanged=[*self.package.glob('*.py'),*host_package.glob('*.py'),
                   *self.exe.parent.glob('*.exe'),host,marker,current,
                   host.parent/'host-installation.json',external_pointer]
        before={path:path.read_bytes() for path in unchanged}
        releases=set((self.root/'releases').iterdir())
        args=SimpleNamespace(target_root=str(self.root),target_bin=str(self.exe.parent),
                             offline=True,skip_browser_registration=False,skip_path_configuration=True)

        def create_runtime(directory):
            (directory/'Scripts').mkdir(parents=True)
            (directory/'Scripts/python.exe').write_bytes(b'fixture runtime')

        for failure in ('diagnostic','registration','publication'):
            with self.subTest(failure=failure):
                reached=[]
                diagnostics=[]
                if failure=='publication':
                    self.symlink(external_pointer,self.root/'.current-new.json')

                def assert_repaired():
                    for package in (self.package,host_package):
                        for name in ('bootstrap.py','bridge_bootstrap.py'):
                            self.assertEqual((package/name).read_bytes(),(source/'vp_helper'/name).read_bytes())

                def run(arguments, **kwargs):
                    arguments=list(map(str,arguments))
                    stage=None
                    if '-c' in arguments and 'launch_python' in arguments[arguments.index('-c')+1]:
                        diagnostics.append(arguments[0])
                        stage='diagnostic'
                    elif 'vp_helper.chromium_login' in arguments:
                        stage='registration'
                    if stage:
                        assert_repaired()
                    if stage==failure:
                        reached.append(stage)
                        raise subprocess.CalledProcessError(7,arguments)

                real_replace=installer.os.replace

                def replace(source_path,destination):
                    if Path(destination)==current:
                        assert_repaired()
                        if failure=='publication':
                            reached.append('publication')
                            raise PermissionError('fixture pointer publication failure')
                    return real_replace(source_path,destination)

                with patch.object(installer,'__file__',str(source/'install.py')), \
                        patch('sys.platform','win32'), \
                        patch('sys.stdout',new=io.StringIO()),patch('sys.stderr',new=io.StringIO()), \
                        patch.dict(os.environ,{'XDG_CONFIG_HOME':str(config)},clear=False), \
                        patch('vp_helper.storage.config_directory',return_value=config), \
                        patch.object(installer.shutil,'which',return_value=None), \
                        patch.object(installer.venv,'EnvBuilder',return_value=SimpleNamespace(create=create_runtime)), \
                        patch.object(installer,'run',side_effect=run), \
                        patch.object(installer.os,'replace',side_effect=replace):
                    with self.assertRaises((subprocess.CalledProcessError,PermissionError)):
                        installer.install(args)
                self.assertEqual(reached,[failure])
                if failure!='diagnostic':
                    self.assertEqual(set(diagnostics),{
                        str(self.first/'venv/Scripts/python.exe'),str(host_initial/'venv/Scripts/python.exe')})
                self.assertEqual(set((self.root/'releases').iterdir()),releases)
                if failure=='publication':
                    self.assertTrue((self.root/'.current-new.json').is_symlink())
                    self.assertEqual((self.root/'.current-new.json').resolve(),external_pointer)
                else:
                    self.assertFalse((self.root/'.current-new.json').exists())
                self.assertEqual(list(self.root.glob('current.json.xcpc-repair-*')),[])
                for path,data in before.items():
                    self.assertEqual(path.read_bytes(),data,str(path))

    def test_zsh_path_setup_preserves_settings_is_idempotent_and_handles_spaces(self):
        home=self.root.parent/'home'
        home.mkdir()
        config=home/'.zshrc'
        original=b'# user configuration\nexport KEEP_THIS=1\n'
        config.write_bytes(original)
        commands=self.root.parent/'XCPC commands 空间'
        commands.mkdir()
        entry=commands/'xcpc'
        entry.write_text('#!/bin/sh\nexit 0\n')
        entry.chmod(0o755)
        installer.configure_macos_path(commands,home=home,shell='/bin/zsh')
        first=config.read_bytes()
        installer.configure_macos_path(commands,home=home,shell='/bin/zsh')
        self.assertEqual(config.read_bytes(),first)
        self.assertTrue(first.startswith(original))
        self.assertEqual(first.count(b'# >>> XCPC command path >>>'),1)
        result=subprocess.run(['/bin/sh','-c','. "$1"; command -v xcpc','fixture',str(config)],
                              capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),str(entry))

    def test_shell_config_symlink_is_retained(self):
        home=self.root.parent/'home'
        home.mkdir()
        target=home/'actual-zshrc'
        target.write_text('# retained dotfiles\n')
        (home/'.zshrc').symlink_to(target)
        installer.configure_macos_path(home/'bin',home=home,shell='/bin/zsh')
        self.assertTrue((home/'.zshrc').is_symlink())
        self.assertIn('# retained dotfiles',target.read_text())
        self.assertIn('# >>> XCPC command path >>>',target.read_text())

    def test_path_setup_does_not_follow_an_existing_temporary_symlink(self):
        home=self.root.parent/'home'
        home.mkdir()
        config=home/'.zprofile'
        config.write_bytes(b'# original profile\n')
        config.chmod(0o640)
        external=self.root.parent/'external-settings'
        external.write_bytes(b'foreign settings; must remain unchanged')
        self.symlink(external,home/'.zprofile.xcpc-path')
        installer.configure_macos_path(home/'bin',home=home,shell='/bin/zsh')
        self.assertEqual(external.read_bytes(),b'foreign settings; must remain unchanged')
        self.assertFalse(config.is_symlink())
        self.assertIn(b'# original profile\n',config.read_bytes())
        self.assertIn(b'# >>> XCPC command path >>>',config.read_bytes())
        if os.name=='posix':
            self.assertEqual(config.stat().st_mode & 0o777,0o640)

    def test_bash_existing_profile_is_preserved(self):
        home=self.root.parent/'home'
        home.mkdir()
        (home/'.profile').write_text('# existing profile\n')
        installer.configure_macos_path(home/'bin',home=home,shell='/bin/bash')
        self.assertFalse((home/'.bash_profile').exists())
        self.assertIn('# existing profile',(home/'.profile').read_text())

    def test_zsh_path_setup_honors_zdotdir_and_is_idempotent(self):
        home=self.root.parent/'home'
        dotdir=home/'custom zsh dotfiles'
        dotdir.mkdir(parents=True)
        config=dotdir/'.zshrc'
        config.write_text('# custom startup settings\n')
        commands=home/'XCPC commands'
        with patch.object(Path,'home',return_value=home), \
                patch.dict(os.environ,{'ZDOTDIR':str(dotdir)}):
            files=installer.configure_macos_path(commands,shell='/bin/zsh')
            first={file:file.read_bytes() for file in files}
            installer.configure_macos_path(commands,shell='/bin/zsh')
        self.assertEqual(files,[dotdir/'.zprofile',dotdir/'.zshrc'])
        self.assertFalse((home/'.zprofile').exists())
        self.assertFalse((home/'.zshrc').exists())
        self.assertIn('# custom startup settings',config.read_text())
        for file,data in first.items():
            self.assertEqual(file.read_bytes(),data)
            self.assertEqual(data.count(b'# >>> XCPC command path >>>'),1)

    def test_explicit_home_does_not_write_to_environment_zdotdir(self):
        home=self.root.parent/'home'
        home.mkdir()
        foreign=self.root.parent/'other-dotfiles'
        foreign.mkdir()
        config=foreign/'.zshrc'
        config.write_bytes(b'# other shell configuration\n')
        with patch.dict(os.environ,{'ZDOTDIR':str(foreign)}):
            installer.configure_macos_path(home/'bin',home=home,shell='/bin/zsh')
        self.assertTrue((home/'.zprofile').is_file())
        self.assertTrue((home/'.zshrc').is_file())
        self.assertFalse((foreign/'.zprofile').exists())
        self.assertEqual(config.read_bytes(),b'# other shell configuration\n')

    def test_macos_path_setup_covers_bash_and_zsh_even_when_login_shell_differs(self):
        home=self.root.parent/'multiple shells'
        home.mkdir()
        commands=home/'XCPC commands'
        commands.mkdir()
        entry=commands/'xcpc'
        entry.write_text('#!/bin/sh\nexit 0\n')
        entry.chmod(0o755)
        files=installer.configure_macos_path(commands,home=home,shell='/bin/bash',all_shells=True)
        self.assertEqual({file.name for file in files},{'.bash_profile','.bashrc','.zprofile','.zshrc'})
        for shell in ('bash','zsh'):
            binary=shutil.which(shell)
            if not binary:
                continue
            environment={'HOME':str(home),'ZDOTDIR':str(home),'PATH':'/usr/bin:/bin'}
            result=subprocess.run([binary,'-lic','command -v xcpc'],env=environment,
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),str(entry))

    def test_path_setup_creates_missing_custom_zsh_parent_and_fish_conf_directory(self):
        home=self.root.parent/'home'
        home.mkdir()
        dotdir=home/'new dotfiles'
        with patch.object(Path,'home',return_value=home),patch.dict(os.environ,{'ZDOTDIR':str(dotdir)}):
            files=installer.configure_macos_path(home/'bin',shell='/bin/zsh')
        self.assertTrue(all(file.is_file() for file in files))
        files=installer.configure_macos_path(home/"commands with 'quote",home=home,shell='/usr/bin/fish')
        self.assertEqual(files,[home/'.config/fish/conf.d/xcpc-path.fish'])
        self.assertIn("commands with \\'quote",files[0].read_text())
        self.assertIn('set -gx PATH',files[0].read_text())
