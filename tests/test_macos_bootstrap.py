"""Exercise the macOS shell bootstrap using isolated, fake system commands."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent.parent / 'install-macos.sh'


class MacOSBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xcpc mac bootstrap ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'commands'
        self.assets = self.root / 'fixtures'
        self.home = self.root / 'home'
        self.downloads = self.root / 'temporary downloads'
        self.apple = self.root / 'Apple Homebrew'
        self.intel = self.root / 'Intel Homebrew'
        for directory in (self.bin, self.assets, self.home, self.downloads,
                          self.apple / 'bin', self.intel / 'bin'):
            directory.mkdir(parents=True)
        self.prefix = self.apple
        self.script = self.root / 'install-macos.sh'
        # Redirect only Homebrew's two fixed locations. No host Homebrew, Python,
        # Git, tmux, shell profiles, or network is accessible to these fixtures.
        text = SOURCE.read_text().replace('/opt/homebrew/bin/brew', shlex.quote(str(self.apple / 'bin/brew')))
        text = text.replace('/usr/local/bin/brew', shlex.quote(str(self.intel / 'bin/brew')))
        self.script.write_text(text)
        (self.root / 'install.py').write_text('# intercepted by the fake Python\n')
        for command in ('dirname', 'mktemp', 'rm', 'cp'):
            real = shutil.which(command)
            self.assertIsNotNone(real)
            (self.bin / command).symlink_to(real)
        self.command(self.bin / 'uname', "printf '%s\\n' Darwin\n")
        self.command(self.bin / 'id', "[ \"$1\" = -u ]\nprintf '%s\\n' \"${FIXTURE_UID:-1000}\"\n")
        self.command(self.bin / 'sudo', '''
printf '%s\\0' "$@" >> "$FIXTURE_SUDO_LOG"
if [ "$1" = -u ]; then
    shift 2
    [ "$1" = -H ] && shift
    FIXTURE_UID=1000
    export FIXTURE_UID
    unset SUDO_USER
fi
[ "$1" = -- ] && shift
exec "$@"
''')
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.home),
            'TMPDIR': str(self.downloads), 'SHELL': '/bin/sh', 'LC_ALL': 'C',
            'XCPC_MIRROR': 'official',
            'FIXTURE_SUDO_LOG': str(self.root/'sudo-calls'),
        }
        self.python_fixture = self.python(self.assets / 'python3.13')
        self.command(self.assets / 'git', "[ \"$1\" = --version ]\n")
        self.command(self.assets / 'tmux', "[ \"$1\" = -V ]\n")

    def tearDown(self):
        self.assertEqual(list(self.home.iterdir()), [], 'bootstrap modified shell settings')
        self.assertEqual(list(self.downloads.iterdir()), [], 'downloaded installer was not cleaned up')

    @staticmethod
    def command(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nset -eu\n' + body)
        path.chmod(0o755)
        return path

    def python(self, path, probe_status=0, shared_status=0):
        return self.command(path, f'''
if [ "${{1:-}}" = -I ]; then
    printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'python-probes'))}
    case "$*" in *XCPC_GLOBAL_PYTHON_CHECK*) exit {shared_status} ;; esac
    exit {probe_status}
fi
printf '%s\\0' "$0" "$@" > {shlex.quote(str(self.root / 'installer-call'))}
printf '%s' "$PATH" > {shlex.quote(str(self.root / 'installer-path'))}
exit "${{FIXTURE_INSTALL_EXIT:-0}}"
''')

    def tools(self, directory=None, python_name='python3.13', git=True, tmux=True):
        directory = directory or self.bin
        if python_name:
            self.python(directory / python_name)
        for command, enabled in (('git', git), ('tmux', tmux)):
            if enabled:
                shutil.copy2(self.assets / command, directory / command)

    def brew(self, destination=None, *, install_status=0, supply_tools=True):
        destination = destination or self.bin / 'brew'
        return self.command(destination, f'''
printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'brew-calls'))}
case "$1" in
    --prefix) printf '%s\\n' {shlex.quote(str(self.prefix))} ;;
    install)
        umask > {shlex.quote(str(self.root / 'brew-install-umask'))}
        if [ {install_status} -ne 0 ]; then exit {install_status}; fi
        shift
        for formula in "$@"; do
            case "$formula" in
                python@3.13) name=python3.13 ;;
                git|tmux) name=$formula ;;
                *) exit 91 ;;
            esac
            if {str(supply_tools).lower()}; then
                cp {shlex.quote(str(self.assets))}/"$name" {shlex.quote(str(self.prefix / 'bin'))}/"$name"
            fi
        done
        ;;
    *) exit 92 ;;
esac
''')

    def download(self, *, curl_status=0, installer_status=0):
        self.brew(self.assets / 'brew')
        installer = self.command(self.assets / 'homebrew-install.sh', f'''
printf '%s\\n' "${{NONINTERACTIVE-unset}}" > {shlex.quote(str(self.root / 'homebrew-ran'))}
umask > {shlex.quote(str(self.root / 'homebrew-install-umask'))}
if [ {installer_status} -ne 0 ]; then exit {installer_status}; fi
cp {shlex.quote(str(self.assets / 'brew'))} {shlex.quote(str(self.prefix / 'bin/brew'))}
''')
        self.command(self.bin / 'curl', f'''
printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'curl-calls'))}
if [ {curl_status} -ne 0 ]; then exit {curl_status}; fi
while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then
        shift
        cp {shlex.quote(str(installer))} "$1"
        exit 0
    fi
    shift
done
exit 93
''')

    def run_installer(self, *arguments):
        return subprocess.run(['/bin/sh', str(self.script), *arguments],
                              env=self.env, text=True, capture_output=True, timeout=20,
                              preexec_fn=lambda: os.umask(0o077))

    def calls(self, name):
        file = self.root / name
        return file.read_text().splitlines() if file.exists() else []

    def installer_call(self):
        raw = (self.root / 'installer-call').read_bytes().split(b'\0')
        return [os.fsdecode(value) for value in raw[:-1]]

    def test_help_needs_no_prerequisites_and_never_downloads(self):
        self.download()
        for arguments in (('--help',), ('-h',), ('--offline', '--help')):
            with self.subTest(arguments=arguments):
                result = self.run_installer(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--target-root', result.stdout)
        self.assertEqual(self.calls('curl-calls'), [])
        self.assertEqual(self.calls('brew-calls'), [])
        self.assertFalse((self.root / 'installer-call').exists())

    def test_existing_tools_prefer_python_313_and_preserve_arguments(self):
        self.tools()
        self.python(self.bin / 'python3')
        self.download()
        arguments = ('--target-root', str(self.root / '应用 directory'),
                     '--target-bin', str(self.root / 'custom commands'))
        result = self.run_installer(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installer_call(), [str(self.bin / 'python3.13'),
                                              str(self.root / 'install.py'), *arguments])
        self.assertEqual(self.calls('curl-calls'), [])
        self.assertEqual(self.calls('brew-calls'), [])
        self.assertFalse((self.root/'sudo-calls').exists())

    def test_default_install_elevates_only_the_shared_application_stage(self):
        self.tools()
        self.env['XCPC_SOURCE_REVISION']='a'*40
        result=self.run_installer('--offline')
        self.assertEqual(result.returncode,0,result.stderr)
        arguments=self.installer_call()
        self.assertIn('/Library/Application Support/XCPC/app',arguments)
        self.assertIn('/usr/local/bin',arguments)
        elevated=(self.root/'sudo-calls').read_bytes().split(b'\0')
        self.assertEqual(elevated[:2],[b'--',b'/usr/bin/env'])
        self.assertIn(('XCPC_SOURCE_REVISION='+'a'*40).encode(),elevated)
        self.assertIn(str(self.root/'install.py').encode(),elevated)
        self.assertFalse((self.root/'brew-calls').exists())
        self.assertIn('vp_helper.chromium_login',(self.root/'python-probes').read_text())
        self.assertNotIn(b'vp_helper.chromium_login',elevated)

    def test_explicit_skip_browser_registration_is_honored(self):
        self.tools()
        result=self.run_installer('--skip-browser-registration')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('vp_helper.chromium_login',(self.root/'python-probes').read_text())

    def test_failed_shared_install_does_not_register_a_browser_bridge(self):
        self.tools()
        self.env['FIXTURE_INSTALL_EXIT']='17'
        result=self.run_installer()
        self.assertEqual(result.returncode,17,result.stderr)
        self.assertNotIn('vp_helper.chromium_login',(self.root/'python-probes').read_text())

    def test_private_user_python_is_replaced_with_shared_homebrew_python(self):
        self.tools(python_name=None)
        self.python(self.bin/'python3.13',shared_status=1)
        self.brew()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls('brew-calls'),['--prefix','install python@3.13'])
        self.assertEqual(self.installer_call()[0],str(self.prefix/'bin/python3.13'))

    def test_explicit_private_targets_keep_the_existing_user_runtime_without_sudo(self):
        self.tools(python_name=None)
        self.python(self.bin/'python3.13',shared_status=1)
        arguments=('--target-root',str(self.home/'XCPC app'),'--target-bin',str(self.home/'XCPC commands'))
        result=self.run_installer(*arguments)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.installer_call()[2:],list(arguments))
        self.assertFalse((self.root/'sudo-calls').exists())

    def test_private_git_and_tmux_are_replaced_by_shared_homebrew_tools(self):
        self.tools(git=False,tmux=False)
        private=self.home/'bin'
        private.mkdir()
        self.tools(private,python_name=None)
        self.env['PATH']=str(private)+os.pathsep+str(self.bin)
        self.brew()
        try:
            result=self.run_installer()
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.calls('brew-calls'),['--prefix','install git tmux'])
        finally:
            shutil.rmtree(private)

    def test_mixed_private_and_shared_targets_are_rejected_before_setup(self):
        for arguments in (
            ('--target-root','/Library/Application Support/XCPC/app','--target-bin',str(self.home/'bin')),
            ('--target-root',str(self.home/'app'),'--target-bin','/usr/local/bin'),
        ):
            with self.subTest(arguments=arguments):
                result=self.run_installer(*arguments)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('mixed shared/private',result.stderr)
                self.assertFalse((self.root/'sudo-calls').exists())
                self.assertFalse((self.root/'installer-call').exists())

    def test_sudo_launch_prepares_homebrew_as_the_invoking_user(self):
        self.env.update(FIXTURE_UID='0',SUDO_USER='fixture-admin')
        self.download()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        calls=(self.root/'sudo-calls').read_bytes().split(b'\0')
        self.assertEqual(calls[:4],[b'-u',b'fixture-admin',b'-H',b'--'])
        self.assertEqual(self.calls('brew-calls'),['--prefix','install python@3.13 git tmux'])

    def test_root_without_an_invoking_user_does_not_run_homebrew_setup(self):
        self.env['FIXTURE_UID']='0'
        self.download()
        result=self.run_installer()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('normal administrator account',result.stderr)
        self.assertEqual(self.calls('brew-calls'),[])
        self.assertEqual(self.calls('curl-calls'),[])

    def test_existing_python_312_can_run_offline_without_homebrew(self):
        self.tools(python_name='python3.12')
        result = self.run_installer('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.installer_call()[0]).name, 'python3.12')
        self.assertEqual(self.installer_call()[-1], '--offline')
        self.assertEqual(self.calls('brew-calls'), [])

    def test_unusable_python_candidates_fall_back_to_python_312(self):
        self.tools(python_name='python3.12')
        self.python(self.bin / 'python3.13', probe_status=1)
        self.python(self.bin / 'python3', probe_status=1)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.installer_call()[0]).name, 'python3.12')
        probes = self.calls('python-probes')
        native=[probe for probe in probes if 'sys.version_info < (3, 12)' in probe]
        self.assertEqual(len(native), 3)
        self.assertTrue(all('import venv, ensurepip' in probe for probe in native))

    def test_offline_reports_all_missing_tools_without_downloads(self):
        self.download()
        result = self.run_installer('--offline')
        self.assertNotEqual(result.returncode, 0)
        for text in ('Offline', 'Python 3.12+', 'venv', 'Git', 'tmux', 'without --offline'):
            self.assertIn(text, result.stderr)
        self.assertEqual(self.calls('curl-calls'), [])
        self.assertEqual(self.calls('brew-calls'), [])
        self.assertFalse((self.root / 'installer-call').exists())

    def test_offline_homebrew_is_only_queried(self):
        self.brew()
        self.tools(python_name=None, tmux=False)
        result = self.run_installer('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.12+', result.stderr)
        self.assertIn('tmux', result.stderr)
        self.assertEqual(self.calls('brew-calls'), ['--prefix'])

    def check_fixed_homebrew_location(self, prefix):
        self.prefix = prefix
        self.brew(prefix / 'bin/brew')
        self.tools(prefix / 'bin')
        result = self.run_installer('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installer_call()[0], str(prefix / 'bin/python3.13'))
        path = (self.root / 'installer-path').read_text().split(os.pathsep)
        self.assertIn(str(prefix / 'bin'), path)
        self.assertIn(str(prefix / 'opt/python@3.13/libexec/bin'), path)
        self.assertEqual(self.calls('brew-calls'), ['--prefix'])

    def test_apple_silicon_homebrew_is_discovered_outside_path(self):
        self.check_fixed_homebrew_location(self.apple)

    def test_intel_homebrew_is_discovered_outside_path(self):
        self.check_fixed_homebrew_location(self.intel)

    def test_install_only_missing_python_and_retain_existing_tools(self):
        self.brew()
        self.tools(python_name=None)
        self.python(self.bin / 'python3', probe_status=1)
        result = self.run_installer('--target-bin', 'custom commands')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('brew-calls'), ['--prefix', 'install python@3.13'])
        self.assertEqual(self.installer_call()[0], str(self.prefix / 'bin/python3.13'))
        self.assertEqual(self.installer_call()[-2:], ['--target-bin', 'custom commands'])

    def test_reuse_python_and_install_only_missing_git_and_tmux(self):
        self.brew()
        self.tools(python_name='python3.12', git=False, tmux=False)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('brew-calls'), ['--prefix', 'install git tmux'])
        self.assertEqual(self.installer_call()[0], str(self.bin / 'python3.12'))

    def test_fresh_setup_downloads_official_homebrew_and_continues(self):
        self.download()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('password', result.stdout)
        self.assertEqual(len(self.calls('curl-calls')), 1)
        self.assertIn('https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh',
                      self.calls('curl-calls')[0])
        self.assertEqual(self.calls('homebrew-ran'), ['unset'])
        self.assertEqual(int((self.root/'homebrew-install-umask').read_text().strip(),8),0o022)
        self.assertEqual(int((self.root/'brew-install-umask').read_text().strip(),8),0o022)
        self.assertEqual(self.calls('brew-calls'), ['--prefix', 'install python@3.13 git tmux'])
        self.assertEqual(self.installer_call()[0], str(self.prefix / 'bin/python3.13'))

    def test_download_failure_stops_and_cleans_temporary_file(self):
        self.download(curl_status=22)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Could not download', result.stderr)
        self.assertFalse((self.root / 'homebrew-ran').exists())
        self.assertFalse((self.root / 'installer-call').exists())

    def test_homebrew_failure_stops_and_cleans_temporary_file(self):
        self.download(installer_status=7)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Homebrew setup did not finish', result.stderr)
        self.assertEqual(self.calls('brew-calls'), [])
        self.assertFalse((self.root / 'installer-call').exists())

    def test_brew_formula_failure_does_not_launch_application_installer(self):
        self.brew(install_status=17)
        result = self.run_installer()
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse((self.root / 'installer-call').exists())

    def test_successful_brew_must_actually_make_prerequisites_usable(self):
        self.brew(supply_tools=False)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not make these tools usable', result.stderr)
        self.assertFalse((self.root / 'installer-call').exists())

    def test_other_platform_does_not_start_prerequisite_setup(self):
        self.command(self.bin / 'uname', "printf '%s\\n' Linux\n")
        self.download()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ubuntu uses bash install.sh', result.stderr)
        self.assertEqual(self.calls('curl-calls'), [])
        self.assertEqual(self.calls('brew-calls'), [])

    def mirror_brew(self, *, fail_mirror=False):
        original=self.brew(self.assets/'brew-original')
        return self.command(self.bin/'brew',f'''
if [ "$1" = install ]; then
    printf '%s|%s|%s|%s|%s\\n' "${{HOMEBREW_API_DOMAIN-unset}}" "${{HOMEBREW_BOTTLE_DOMAIN-unset}}" "${{HOMEBREW_PIP_INDEX_URL-unset}}" "${{HOMEBREW_BREW_GIT_REMOTE-unset}}" "${{HOMEBREW_CORE_GIT_REMOTE-unset}}" >> {shlex.quote(str(self.root/'brew-environment'))}
    if {str(fail_mirror).lower()} && [ "${{HOMEBREW_API_DOMAIN-unset}}" = https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api ]; then exit 23; fi
fi
exec {shlex.quote(str(original))} "$@"
''')

    def test_china_defaults_scope_homebrew_downloads_without_rewriting_existing_git_remotes(self):
        self.env.pop('XCPC_MIRROR')
        self.mirror_brew()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls('brew-environment'),[
            'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api|'
            'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles|'
            'https://pypi.tuna.tsinghua.edu.cn/simple|unset|unset'])
        elevated=(self.root/'sudo-calls').read_bytes().split(b'\0')
        self.assertIn(b'XCPC_MIRROR=china',elevated)
        self.assertNotIn(b'HOMEBREW_API_DOMAIN',elevated)

    def test_china_mirror_failure_retries_original_sources_and_keeps_custom_settings(self):
        self.env['XCPC_MIRROR']='china'
        self.env['HOMEBREW_BOTTLE_DOMAIN']='https://custom.example/bottles'
        self.env['HOMEBREW_BREW_GIT_REMOTE']='https://custom.example/brew.git'
        self.mirror_brew(fail_mirror=True)
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('retrying with the original sources',result.stderr)
        self.assertEqual(self.calls('brew-environment'),[
            'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api|https://custom.example/bottles|'
            'https://pypi.tuna.tsinghua.edu.cn/simple|https://custom.example/brew.git|unset',
            'unset|https://custom.example/bottles|unset|https://custom.example/brew.git|unset'])

    def test_official_mode_does_not_inject_any_mirror_settings(self):
        self.mirror_brew()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls('brew-environment'),['unset|unset|unset|unset|unset'])
        elevated=(self.root/'sudo-calls').read_bytes().split(b'\0')
        self.assertIn(b'XCPC_MIRROR=official',elevated)

    def test_all_explicit_homebrew_download_settings_are_preserved(self):
        self.env['XCPC_MIRROR']='china'
        self.env.update(HOMEBREW_API_DOMAIN='https://custom.example/api',
            HOMEBREW_BOTTLE_DOMAIN='',HOMEBREW_PIP_INDEX_URL='https://custom.example/python')
        self.mirror_brew()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls('brew-environment'),['https://custom.example/api||https://custom.example/python|unset|unset'])

    def mirror_bootstrap(self, *, fail_clone=False):
        self.env['XCPC_MIRROR']='china'
        self.download()
        installer=self.assets/'homebrew-install.sh'
        installer.write_text(installer.read_text().replace('umask >',
            f'''printf '%s|%s|%s|%s\\n' "${{HOMEBREW_API_DOMAIN-unset}}" "${{HOMEBREW_BOTTLE_DOMAIN-unset}}" "${{HOMEBREW_BREW_GIT_REMOTE-unset}}" "${{HOMEBREW_CORE_GIT_REMOTE-unset}}" >> {shlex.quote(str(self.root/'bootstrap-environment'))}
umask >'''))
        self.command(self.bin/'git',f'''
if [ "$1" = --version ]; then exit 0; fi
printf '%s\\n' "$*" >> {shlex.quote(str(self.root/'git-clone-calls'))}
if {str(fail_clone).lower()}; then exit 18; fi
while [ "$#" -gt 1 ]; do shift; done
mkdir -p "$1"
cp {shlex.quote(str(installer))} "$1/install.sh"
''')
        (self.bin/'mkdir').symlink_to(shutil.which('mkdir'))

    def test_fresh_homebrew_uses_documented_tuna_clone_and_scoped_bootstrap_remotes(self):
        self.mirror_bootstrap()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.calls('curl-calls'),[])
        self.assertIn('clone --depth=1 https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/install.git',
            self.calls('git-clone-calls')[0])
        self.assertEqual(self.calls('bootstrap-environment'),[
            'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api|'
            'https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles|'
            'https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git|'
            'https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/homebrew-core.git'])

    def test_failed_homebrew_mirror_bootstrap_falls_back_to_official_installer(self):
        self.mirror_bootstrap(fail_clone=True)
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('bootstrap mirror failed',result.stderr)
        self.assertEqual(len(self.calls('curl-calls')),1)
        self.assertEqual(self.calls('bootstrap-environment'),['unset|unset|unset|unset'])

    def test_invalid_mirror_mode_stops_before_network_or_elevation(self):
        self.env['XCPC_MIRROR']='https://not-an-enum.example'
        result=self.run_installer()
        self.assertEqual(result.returncode,2)
        self.assertIn('XCPC_MIRROR must be china or official',result.stderr)
        self.assertFalse((self.root/'sudo-calls').exists())


if __name__ == '__main__':
    unittest.main()
