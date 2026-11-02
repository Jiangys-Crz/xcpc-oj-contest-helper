"""Offline execution of the public bootstrap with real archives and isolated tools."""
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'deploy/online-install.sh'
REVISION = 'a' * 40
BASE = 'https://vp.cwnupaa.com'


@unittest.skipUnless(os.name == 'posix' and Path('/bin/bash').is_file(), 'POSIX bash bootstrap')
class OnlineInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xcpc online installer ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / 'commands'
        self.fixtures = self.root / 'fixtures'
        self.downloads = self.root / 'temporary downloads'
        self.home = self.root / 'home'
        self.machine_bin = self.root / 'shared commands'
        self.machine_root = self.root / 'shared application'
        self.bootstrap = self.root / 'online-install.sh'
        self.bootstrap.write_text(BOOTSTRAP.read_text().replace('/usr/local/bin', str(self.machine_bin)).replace('/opt/vp-helper', str(self.machine_root)))
        for directory in (self.bin, self.fixtures, self.downloads, self.home):
            directory.mkdir()
        self.manifest = self.fixtures / 'manifest.json'
        self.archive = self.fixtures / 'source.tar.gz'
        self.installer_log = self.root / 'installer-call'
        self.curl_log = self.root / 'curl-calls'
        self.xcpc_log = self.root / 'xcpc-calls'
        for name in ('bash', 'sh', 'mktemp', 'sed', 'cut', 'tar', 'awk', 'mkdir', 'rm', 'cp', 'mv', 'dirname', 'readlink'):
            command = shutil.which(name)
            self.assertIsNotNone(command, name)
            (self.bin / name).symlink_to(command)
        self.command('uname', "[ \"$1\" = -s ]\nprintf '%s\\n' \"$FIXTURE_OS\"\n")
        # An explicit false updater blocks discovery of the user's real xcpc.
        self.command('xcpc', '''
printf '%s\\0' "$@" >> "$FIXTURE_XCPC_LOG"
[ "$1" != --help ] || exit "$FIXTURE_HELP_EXIT"
exit "$FIXTURE_UPDATE_EXIT"
''')
        self.cli_template = self.fixtures/'cli-template'
        shutil.copy2(self.bin/'xcpc',self.cli_template)
        self.command('curl', '''
output= url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) shift; output=$1 ;;
        https://*) url=$1 ;;
    esac
    shift
done
[ -n "$output" ] && [ -n "$url" ] || exit 91
printf '%s\\n' "$url" >> "$FIXTURE_CURL_LOG"
case "$url" in
    https://vp.cwnupaa.com/api/client/releases/latest) cp "$FIXTURE_MANIFEST" "$output" ;;
    https://vp.cwnupaa.com/downloads/terminal/"$FIXTURE_REVISION".tar.gz) cp "$FIXTURE_ARCHIVE" "$output" ;;
    *) exit 92 ;;
esac
''')
        # Calculate the real digest. Never use a fixed response to hide a mismatch.
        digest = "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()+'  '+sys.argv[1])"
        self.command('sha256sum', 'exec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(digest) + ' "$@"\n')
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.home), 'TMPDIR': str(self.downloads),
            'SHELL': '/bin/bash', 'LC_ALL': 'C', 'FIXTURE_OS': 'Linux',
            'FIXTURE_ARCHIVE': str(self.archive), 'FIXTURE_MANIFEST': str(self.manifest),
            'FIXTURE_INSTALL_LOG': str(self.installer_log), 'FIXTURE_CURL_LOG': str(self.curl_log),
            'FIXTURE_XCPC_LOG': str(self.xcpc_log), 'FIXTURE_REVISION': REVISION,
            'FIXTURE_GLOBAL_BIN':str(self.machine_bin), 'FIXTURE_CLI_TEMPLATE':str(self.cli_template),
            'FIXTURE_UPDATE_EXIT': '1', 'FIXTURE_INSTALL_EXIT': '0', 'FIXTURE_HELP_EXIT': '0',
        }
        self.make_archive()

    def tearDown(self):
        self.assertEqual(list(self.downloads.iterdir()), [], 'bootstrap left its temporary download directory')
        self.assertFalse(list(self.home.rglob('.xcpc-path.*')), 'bootstrap left a shell configuration temporary file')

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\nset -eu\n' + body)
        path.chmod(0o755)

    def make_archive(self, unsafe=None, *, missing_installer=False):
        installer = '''#!/bin/sh
set -eu
printf '%s\\0' "$XCPC_SOURCE_REVISION" "$0" "$PWD" "$@" > "$FIXTURE_INSTALL_LOG"
[ "$FIXTURE_INSTALL_EXIT" != 0 ] || { mkdir -p "$FIXTURE_GLOBAL_BIN"; cp "$FIXTURE_CLI_TEMPLATE" "$FIXTURE_GLOBAL_BIN/xcpc"; }
exit "$FIXTURE_INSTALL_EXIT"
'''.encode()
        files = {'VP/install.py': b'# No Python installation is executed by this fixture.\n',
                 'VP/install.sh': installer, 'VP/install-macos.sh': installer}
        if missing_installer:
            del files['VP/install.py']
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, value in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                entry.mode = 0o755 if name.endswith('.sh') else 0o644
                archive.addfile(entry, io.BytesIO(value))
            if unsafe:
                name, kind = unsafe
                entry = tarfile.TarInfo(name)
                if kind == 'regular':
                    value = b'must not be extracted outside the staging directory\n'
                    entry.size = len(value)
                    archive.addfile(entry, io.BytesIO(value))
                else:
                    entry.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                    entry.linkname = '../../outside-bootstrap'
                    archive.addfile(entry)
        self.write_manifest()

    def write_manifest(self, **changes):
        data = {'version': '0.9.0', 'revision': REVISION,
                'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                'sourceUrl': BASE + '/downloads/terminal/' + REVISION + '.tar.gz'}
        data.update(changes)
        # The actual endpoint wraps data; whitespace is legal and must work.
        self.manifest.write_text(json.dumps({'data': data}, indent=2))

    def run_bootstrap(self, *arguments, operating_system='Linux'):
        env = self.env | {'FIXTURE_OS': operating_system}
        return subprocess.run(['/bin/bash', str(self.bootstrap), *arguments], cwd=self.root,
                              env=env, text=True, capture_output=True, timeout=20)

    def global_install(self):
        self.machine_bin.mkdir(exist_ok=True)
        shutil.copy2(self.cli_template,self.machine_bin/'xcpc')
        (self.machine_bin/'.xcpc-native-commands.json').write_text('{"owner":"xcpc-native","global":true}')
        self.env['FIXTURE_UPDATE_EXIT']='0'

    def calls(self, file):
        return file.read_text().splitlines() if file.exists() else []

    def installed(self):
        return [os.fsdecode(value) for value in self.installer_log.read_bytes().split(b'\0')[:-1]]

    def test_verified_archive_invokes_the_correct_native_installer_with_source_revision(self):
        for operating_system, filename in (('Linux', 'install.sh'), ('Darwin', 'install-macos.sh')):
            with self.subTest(operating_system=operating_system):
                self.installer_log.unlink(missing_ok=True)
                self.curl_log.unlink(missing_ok=True)
                result = self.run_bootstrap(operating_system=operating_system)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = self.installed()
                self.assertEqual(args[0], REVISION)
                self.assertEqual(Path(args[1]).name, filename)
                self.assertEqual(args[2:], [str(self.root)])
                self.assertEqual(self.calls(self.curl_log), [BASE + '/api/client/releases/latest',
                                                           BASE + '/downloads/terminal/' + REVISION + '.tar.gz'])
                self.assertIn('xcpc login', result.stdout)
                self.assertEqual(list(self.downloads.iterdir()), [])

    def test_sha_mismatch_never_runs_an_installer(self):
        self.write_manifest(sha256='b' * 64)
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256 mismatch', result.stderr)
        self.assertFalse(self.installer_log.exists())

    def test_parent_traversal_and_absolute_archive_paths_are_rejected_before_extraction(self):
        for filename in ('../outside-bootstrap', 'VP/../../outside-bootstrap', str(self.root / 'outside-bootstrap')):
            with self.subTest(filename=filename):
                self.make_archive((filename, 'regular'))
                result = self.run_bootstrap()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.installer_log.exists())
                self.assertFalse((self.root / 'outside-bootstrap').exists())
                self.assertEqual(list(self.downloads.iterdir()), [])

    def test_source_archive_symlinks_and_hardlinks_are_rejected(self):
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                self.make_archive(('VP/linked-source', kind))
                result = self.run_bootstrap()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('links are not allowed', result.stderr)
                self.assertFalse(self.installer_log.exists())
                self.assertEqual(list(self.downloads.iterdir()), [])

    def test_existing_updater_success_never_downloads_or_uses_fresh_default_directories(self):
        self.global_install()
        custom_directory = self.root / 'existing custom install'
        custom_directory.mkdir()
        preserved = custom_directory / 'settings'
        preserved.write_text('keep existing configuration\n')
        result = self.run_bootstrap(operating_system='Darwin')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.xcpc_log.read_bytes(), b'update\0--cloud\0--help\0')
        self.assertFalse(self.installer_log.exists())
        self.assertFalse(self.curl_log.exists())
        self.assertEqual(preserved.read_text(), 'keep existing configuration\n')

    def test_noop_update_repairs_new_bash_and_zsh_terminals_with_stale_shell_environment(self):
        self.global_install()
        self.env['SHELL'] = '/bin/bash'  # macOS Terminal may actually open zsh.
        first = self.run_bootstrap(operating_system='Darwin')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        profiles = ('.bash_profile', '.bashrc', '.zprofile', '.zshrc')
        before = {name:(self.home/name).read_bytes() for name in profiles}
        second = self.run_bootstrap(operating_system='Darwin')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(before, {name:(self.home/name).read_bytes() for name in profiles})
        for shell in ('bash', 'zsh'):
            binary = shutil.which(shell)
            if not binary:
                continue
            env = {'HOME':str(self.home), 'ZDOTDIR':str(self.home), 'PATH':'/usr/bin:/bin'}
            check = subprocess.run([binary, '-lic', 'command -v xcpc'], env=env,
                                   text=True, capture_output=True, timeout=10)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(check.stdout.strip(), str(self.machine_bin/'xcpc'))
        self.assertFalse(self.curl_log.exists())

    def test_legacy_private_macos_command_migrates_to_shared_install_without_deleting_private_files(self):
        standard = self.home/'.local/bin'
        standard.mkdir(parents=True)
        shutil.copy2(self.bin/'xcpc', standard/'xcpc')
        (self.bin/'xcpc').unlink()
        result = self.run_bootstrap(operating_system='Darwin')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.curl_log.exists())
        self.assertTrue((standard/'xcpc').is_file())
        check = subprocess.run(['/bin/bash', '-c', '. "$1"; command -v xcpc', 'fixture', str(self.home/'.zshrc')],
                               env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout.strip(), str(self.machine_bin/'xcpc'))

    def test_path_repair_preserves_shell_symlinks_and_existing_settings(self):
        target = self.home/'saved shell settings'
        target.write_text('export KEEP_ME=1\n')
        (self.home/'.bashrc').symlink_to(target)
        self.global_install()
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home/'.bashrc').is_symlink())
        self.assertIn('export KEEP_ME=1', target.read_text())
        self.assertEqual(target.read_text().count('# >>> XCPC command path >>>'), 1)

    def test_successful_updater_with_broken_command_does_not_report_installation_complete(self):
        self.env.update(FIXTURE_UPDATE_EXIT='0', FIXTURE_HELP_EXIT='17')
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertNotIn('Installation complete', result.stdout)

    def test_invalid_manifest_stops_before_archive_download(self):
        self.write_manifest(revision='not-a-git-revision')
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid release manifest', result.stderr)
        self.assertEqual(self.calls(self.curl_log), [BASE + '/api/client/releases/latest'])
        self.assertFalse(self.installer_log.exists())

    def test_incomplete_archive_and_child_failure_do_not_report_success(self):
        self.make_archive(missing_installer=True)
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Incomplete source archive', result.stderr)
        self.assertFalse(self.installer_log.exists())
        self.make_archive()
        self.env['FIXTURE_INSTALL_EXIT'] = '17'
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 17)
        self.assertNotIn('Installation complete', result.stdout)

    def test_help_needs_no_system_probe_update_or_download(self):
        result = self.run_bootstrap('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('online installer', result.stdout)
        self.assertFalse(self.xcpc_log.exists())
        self.assertFalse(self.curl_log.exists())
        self.assertFalse(self.installer_log.exists())
