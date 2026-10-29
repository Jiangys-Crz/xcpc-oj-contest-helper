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
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.home),
            'TMPDIR': str(self.downloads), 'SHELL': '/bin/sh', 'LC_ALL': 'C',
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

    def python(self, path, probe_status=0):
        return self.command(path, f'''
if [ "${{1:-}}" = -I ]; then
    printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'python-probes'))}
    exit {probe_status}
fi
printf '%s\\0' "$0" "$@" > {shlex.quote(str(self.root / 'installer-call'))}
printf '%s' "$PATH" > {shlex.quote(str(self.root / 'installer-path'))}
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
                              env=self.env, text=True, capture_output=True, timeout=20)

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
        self.assertEqual(len(probes), 3)
        self.assertTrue(all('sys.version_info < (3, 12)' in probe and
                            'import venv, ensurepip' in probe for probe in probes))

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


if __name__ == '__main__':
    unittest.main()
