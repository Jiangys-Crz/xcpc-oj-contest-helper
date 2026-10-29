"""Isolated Ubuntu prerequisite and owner-safe shell PATH setup checks."""
from contextlib import ExitStack, redirect_stdout
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = (ROOT / 'install.sh').read_text()
PATH_CHILD = INSTALLER.split("<<'PY_PATH'\n", 1)[1].split('\nPY_PATH', 1)[0]


class LinuxPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xcpc ubuntu setup ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'commands'
        self.assets = self.root / 'fixtures'
        self.bin.mkdir()
        self.assets.mkdir()
        self.certificate = self.root / 'ca-certificates.crt'
        self.certificate.write_text('fixture certificate\n')
        release = self.root / 'os-release'
        release.write_text('ID=ubuntu\n')
        # Run the real argument parsing and prerequisite setup. Stop before any
        # application files are created; package installs use the fake apt below.
        preflight = INSTALLER.split('install -d -m 755 "$vp_prefix/releases" "$vp_bin"', 1)[0]
        preflight = preflight.replace('/etc/os-release', shlex.quote(str(release)))
        preflight = preflight.replace('/etc/ssl/certs/ca-certificates.crt', shlex.quote(str(self.certificate)))
        self.script = self.root / 'install.sh'
        self.script.write_text(preflight + "printf '%s\\n' prerequisites-ready\n")
        for name in ('dirname', 'cp'):
            command = shutil.which(name)
            self.assertIsNotNone(command)
            (self.bin / name).symlink_to(command)
        self.command(self.bin / 'uname', "printf '%s\\n' aarch64\n")
        self.command(self.bin / 'sudo', '[ "$1" != -- ] || shift\nexec "$@"\n')
        self.python(self.assets / 'python3')
        for name, option in (('git', '--version'), ('tmux', '-V')):
            self.tool(self.assets / name, option)
        self.apt()
        self.env = {'PATH': str(self.bin), 'LC_ALL': 'C'}

    @staticmethod
    def command(path, body):
        path.write_text('#!/bin/sh\nset -eu\n' + body)
        path.chmod(0o755)
        return path

    def python(self, path, *, version_ok=True, venv_ok=True):
        return self.command(path, f'''
printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'python-probes'))}
[ "$1" = -c ] || exit 95
case "$2" in
    *'assert sys.version_info'*) {str(version_ok).lower()} ;;
    *'import venv, ensurepip'*)
        {str(venv_ok).lower()} || [ -f {shlex.quote(str(self.root / 'venv-ready'))} ] ;;
    *'print(f'*) printf '%s\\n' 3.12 ;;
    *) exit 96 ;;
esac
''')

    def tool(self, path, option, *, usable=True):
        return self.command(path, f'''
printf '%s\\n' "$*" >> {shlex.quote(str(self.root / (path.name + '-probes')))}
[ "$1" = {shlex.quote(option)} ] && {str(usable).lower()}
''')

    def tools(self, *, python=True, git=True, tmux=True):
        for name, enabled in (('python3', python), ('git', git), ('tmux', tmux)):
            if enabled:
                shutil.copy2(self.assets / name, self.bin / name)

    def apt(self, *, status=0, supply=True):
        self.command(self.bin / 'apt-get', f'''
printf '%s\\n' "$*" >> {shlex.quote(str(self.root / 'apt-calls'))}
[ "$1" = install ] || exit 0
if [ {status} -ne 0 ]; then exit {status}; fi
if ! {str(supply).lower()}; then exit 0; fi
shift
for package in "$@"; do
    case "$package" in
        python3)
            cp {shlex.quote(str(self.assets / 'python3'))} {shlex.quote(str(self.bin / 'python3'))} ;;
        python3-venv|python3.12-venv)
            : > {shlex.quote(str(self.root / 'venv-ready'))} ;;
        git|tmux)
            cp {shlex.quote(str(self.assets))}/"$package" {shlex.quote(str(self.bin))}/"$package" ;;
        ca-certificates)
            printf '%s\\n' fixture > {shlex.quote(str(self.certificate))} ;;
        -*) ;;
        *) exit 97 ;;
    esac
done
''')

    def run_installer(self, *arguments):
        return subprocess.run(['/bin/bash', str(self.script), '--local', *arguments],
                              capture_output=True, text=True, env=self.env, timeout=20)

    def calls(self, name):
        file = self.root / name
        return file.read_text().splitlines() if file.exists() else []

    def test_help_is_read_only_with_no_prerequisites(self):
        result = self.run_installer('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--offline', result.stdout)
        self.assertEqual(self.calls('apt-calls'), [])
        self.assertEqual(self.calls('python-probes'), [])

    def test_working_prerequisites_are_reused_without_apt(self):
        self.tools()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('prerequisites-ready', result.stdout)
        self.assertEqual(self.calls('apt-calls'), [])
        self.assertTrue(all(option == '--version' for option in self.calls('git-probes')))
        self.assertTrue(all(option == '-V' for option in self.calls('tmux-probes')))

    def test_missing_git_and_tmux_are_installed_together(self):
        self.tools(git=False, tmux=False)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('apt-calls'), ['update -qq', 'install -y --no-install-recommends tmux git'])

    def test_broken_git_and_tmux_are_treated_as_missing(self):
        self.tools()
        self.tool(self.bin / 'git', '--version', usable=False)
        self.tool(self.bin / 'tmux', '-V', usable=False)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('apt-calls'), ['update -qq', 'install -y --no-install-recommends tmux git'])

    def test_missing_python_is_installed_before_other_tools(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('apt-calls'), [
            'update -qq', 'install -y --no-install-recommends python3 python3-venv ca-certificates',
            'update -qq', 'install -y --no-install-recommends tmux git'])

    def test_missing_venv_and_certificates_are_installed(self):
        self.tools()
        self.python(self.bin / 'python3', venv_ok=False)
        self.certificate.unlink()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('apt-calls'), [
            'update -qq', 'install -y --no-install-recommends python3.12-venv ca-certificates'])

    def test_offline_keeps_existing_system_package_setup_semantics(self):
        self.tools(git=False)
        result = self.run_installer('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('apt-calls'), ['update -qq', 'install -y --no-install-recommends git'])

    def test_missing_explicit_interpreter_is_rejected_without_apt(self):
        result = self.run_installer('--python', 'python3')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python interpreter not found', result.stderr)
        self.assertEqual(self.calls('apt-calls'), [])

    def test_old_explicit_interpreter_is_rejected_without_fallback_or_apt(self):
        self.python(self.bin / 'custom python', version_ok=False)
        result = self.run_installer('--python', str(self.bin / 'custom python'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('supplied interpreter', result.stderr)
        self.assertEqual(self.calls('apt-calls'), [])

    def test_apt_failure_stops_setup(self):
        self.tools(git=False)
        self.apt(status=100)
        result = self.run_installer()
        self.assertEqual(result.returncode, 100, result.stderr)
        self.assertNotIn('prerequisites-ready', result.stdout)

    def test_apt_success_requires_functional_tools(self):
        self.tools(git=False)
        self.apt(supply=False)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('still unusable', result.stderr)
        self.assertNotIn('prerequisites-ready', result.stdout)

    def test_apt_success_requires_functional_venv_support(self):
        self.tools()
        self.python(self.bin / 'python3', venv_ok=False)
        self.apt(supply=False)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('venv support is still unusable', result.stderr)
        self.assertNotIn('prerequisites-ready', result.stdout)


class LinuxOwnerPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xcpc owner path ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'owner home'
        self.commands = self.root / 'XCPC commands 空间'
        self.home.mkdir()
        self.commands.mkdir()
        self.account = SimpleNamespace(pw_name='alice', pw_uid=1001, pw_gid=1002,
                                       pw_dir=str(self.home), pw_shell='/bin/bash')

    def configure(self, initial_uid):
        identity = {'uid': initial_uid}
        events = []
        write_bytes = Path.write_bytes

        def write_as_owner(path, data):
            self.assertEqual(identity['uid'], self.account.pw_uid,
                             'shell configuration written before dropping privileges')
            events.append(('write', path.name))
            return write_bytes(path, data)

        def setuid(uid):
            events.append(('setuid', uid))
            identity['uid'] = uid

        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'dont_write_bytecode', True))
            stack.enter_context(patch('sys.argv', ['-', str(ROOT / 'install.py'),
                                                   str(self.commands), self.account.pw_name]))
            stack.enter_context(patch('pwd.getpwnam', return_value=self.account))
            stack.enter_context(patch('os.geteuid', side_effect=lambda: identity['uid']))
            stack.enter_context(patch('os.initgroups', side_effect=lambda *args: events.append(('initgroups', *args))))
            stack.enter_context(patch('os.setgid', side_effect=lambda gid: events.append(('setgid', gid))))
            stack.enter_context(patch('os.setuid', side_effect=setuid))
            stack.enter_context(patch.object(Path, 'write_bytes', new=write_as_owner))
            stack.enter_context(redirect_stdout(io.StringIO()))
            exec(compile(PATH_CHILD, 'install.sh:PY_PATH', 'exec'), {'__name__': '__main__'})
        return events

    def test_sudo_configuration_drops_groups_gid_and_uid_before_writing(self):
        original = b'# keep user settings\nexport KEEP_THIS=1\n'
        (self.home / '.profile').write_bytes(original)
        events = self.configure(initial_uid=0)
        self.assertEqual(events[:3], [('initgroups', 'alice', 1002), ('setgid', 1002), ('setuid', 1001)])
        self.assertTrue((self.home / '.profile').read_bytes().startswith(original))
        self.assertIn(str(self.commands), (self.home / '.profile').read_text())
        self.assertTrue((self.home / '.bashrc').exists())
        self.assertFalse((self.home / '.bash_profile').exists())

    def test_local_shell_configuration_is_idempotent_and_finds_command_with_spaces(self):
        command = self.commands / 'xcpc'
        command.write_text('#!/bin/sh\nexit 0\n')
        command.chmod(0o755)
        self.configure(initial_uid=1001)
        profile = self.home / '.bash_profile'
        first = profile.read_bytes()
        self.configure(initial_uid=1001)
        self.assertEqual(profile.read_bytes(), first)
        result = subprocess.run(['/bin/sh', '-c', '. "$1"; command -v xcpc', 'fixture', str(profile)],
                                env={'PATH': '/usr/bin:/bin'}, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), str(command))

    def test_account_zsh_is_used_instead_of_installer_shell(self):
        self.account.pw_shell = '/bin/zsh'
        events = self.configure(initial_uid=1001)
        self.assertFalse(any(event[0] in ('initgroups', 'setgid', 'setuid') for event in events))
        self.assertTrue((self.home / '.zprofile').exists())
        self.assertTrue((self.home / '.zshrc').exists())
        self.assertFalse((self.home / '.bashrc').exists())

    def test_unprivileged_process_cannot_write_another_accounts_configs(self):
        with self.assertRaises(PermissionError):
            self.configure(initial_uid=2001)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
