"""Mirror selection is scoped, signed where applicable, and has safe fallbacks."""
import importlib.util
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mirror_installer',ROOT/'install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PythonMirrorTests(unittest.TestCase):
    def run_install(self,outcomes,*,offline=False,environment=None):
        output=io.StringIO()
        with patch.dict(os.environ,environment or {},clear=True),patch.object(installer,'run',side_effect=outcomes) as run,patch('sys.stdout',output):
            try:installer.install_dependencies(['python','-m','pip','install'],[],['fixture'],offline=offline)
            except BaseException as error:return run.call_args_list,output.getvalue(),error,dict(os.environ)
            return run.call_args_list,output.getvalue(),None,dict(os.environ)

    @staticmethod
    def failure():return subprocess.CalledProcessError(1,['pip'])

    def test_bundled_wheels_need_no_network_or_environment_change(self):
        calls,_,error,env=self.run_install([None])
        self.assertIsNone(error);self.assertEqual(len(calls),1)
        self.assertIn('--no-index',calls[0].args[0]);self.assertEqual(env,{})

    def test_offline_failure_never_contacts_a_mirror(self):
        calls,_,error,_=self.run_install([self.failure()],offline=True)
        self.assertIsInstance(error,SystemExit);self.assertEqual(len(calls),1)

    def test_domestic_then_official_without_changing_parent_pip_settings(self):
        calls,output,error,env=self.run_install([self.failure(),self.failure(),None],environment={'PIP_INDEX_URL':'https://private.example/simple','PIP_DEFAULT_TIMEOUT':'42'})
        self.assertIsNone(error)
        self.assertEqual([c.kwargs['env']['PIP_INDEX_URL'] for c in calls[1:]],['https://pypi.tuna.tsinghua.edu.cn/simple','https://pypi.org/simple'])
        self.assertEqual(calls[1].kwargs['env']['PIP_DEFAULT_TIMEOUT'],'42')
        self.assertEqual(env['PIP_INDEX_URL'],'https://private.example/simple')
        self.assertNotIn('private.example',output)

    def test_official_optout_never_tries_domestic(self):
        calls,_,error,_=self.run_install([self.failure(),None],environment={'XCPC_MIRROR':'official'})
        self.assertIsNone(error);self.assertEqual(len(calls),2)
        self.assertEqual(calls[1].kwargs['env']['PIP_INDEX_URL'],'https://pypi.org/simple')

    def test_all_indexes_failing_remains_an_installation_failure(self):
        calls,_,error,_=self.run_install([self.failure(),self.failure(),self.failure()])
        self.assertIsInstance(error,subprocess.CalledProcessError);self.assertEqual(len(calls),3)

    def test_empty_mode_defaults_to_china_and_invalid_modes_never_contact_an_index(self):
        calls,_,error,_=self.run_install([self.failure(),None],environment={'XCPC_MIRROR':''})
        self.assertIsNone(error)
        self.assertEqual(calls[1].kwargs['env']['PIP_INDEX_URL'],'https://pypi.tuna.tsinghua.edu.cn/simple')
        for mode in ('auto','invalid','https://untrusted.example',' official '):
            with self.subTest(mode=mode):
                calls,_,error,_=self.run_install([self.failure()],environment={'XCPC_MIRROR':mode})
                self.assertIsInstance(error,SystemExit)
                self.assertEqual(len(calls),1)

    def test_native_binary_constraints_and_existing_timeout_retries_are_retained(self):
        calls,_,error,env=self.run_install([self.failure(),None],environment={'PIP_DEFAULT_TIMEOUT':'51','PIP_RETRIES':'4'})
        self.assertIsNone(error)
        self.assertIn('--only-binary=cffi,curl-cffi,lxml,selectolax',calls[1].args[0])
        self.assertEqual(calls[1].kwargs['env']['PIP_DEFAULT_TIMEOUT'],'51')
        self.assertEqual(calls[1].kwargs['env']['PIP_RETRIES'],'4')
        self.assertEqual(env,{'PIP_DEFAULT_TIMEOUT':'51','PIP_RETRIES':'4'})


class UbuntuMirrorTests(unittest.TestCase):
    def scenario(self,*,arch='arm64',mode='china',fail_mirror='',fail_system=False,
                 codename='resolute',has_keyring=True,cleanup_fails=False,
                 cleanup_sudo_fails=False,exit_status=None,signal=None,details=False):
        with tempfile.TemporaryDirectory(prefix='xcpc apt mirror ') as temporary:
            root=Path(temporary);commands=root/'bin';commands.mkdir();(root/'tmp').mkdir()
            keyring=root/'ubuntu-keyring.gpg'
            if has_keyring:keyring.write_text('fixture public key')
            source=(ROOT/'install.sh').read_text()
            functions=source.split('vp_apt_temp=\n',1)[1].split('if ! command -v "$vp_python"',1)[0]
            functions=('vp_apt_temp=\n'+functions).replace('-r /usr/share/keyrings/ubuntu-archive-keyring.gpg','-r '+shlex.quote(str(keyring)))
            # Exercise local-install privilege transitions even when CI itself
            # runs as root. No real sudo, APT or system path is reachable here.
            functions=functions.replace('$EUID','$FIXTURE_EUID')
            def command(name,body):
                p=commands/name;p.write_text('#!/bin/sh\nset -eu\n'+body);p.chmod(0o755)
            command('dpkg','printf "%s\\n" '+shlex.quote(arch)+'\n')
            command('sudo',r'''
[ "$1" != -- ] || shift
printf '%s\n' "$*" >> "$SUDO_LOG"
if [ "$1" = rm ]; then
  if [ "$CLEANUP_SUDO_FAIL" = yes ]; then exit 14; fi
  XCPC_CLEANUP_ELEVATED=yes; export XCPC_CLEANUP_ELEVATED
fi
exec "$@"
''')
            command('rm',r'''
printf '%s\n' "$*" >> "$RM_LOG"
if [ "$CLEANUP_FAIL" = yes ] && [ "${XCPC_CLEANUP_ELEVATED-no}" != yes ]; then exit 13; fi
exec REAL_RM "$@"
'''.replace('REAL_RM',shlex.quote(shutil.which('rm'))))
            command('apt-get',r'''
kind=system
for arg in "$@"; do
  case "$arg" in Dir::Etc::sourcelist=*) kind=mirror; file=${arg#*=}; cp "$file" "$OBSERVED" ;; esac
done
printf '%s %s\n' "$kind" "$1" >> "$APT_LOG"
printf '%s\0' "$@" >> "$APT_ARGS"
if [ "$kind" = mirror ] && { [ "$FAIL_MIRROR" = all ] || [ "$FAIL_MIRROR" = "$1" ]; }; then exit 7; fi
if [ "$kind" = system ] && [ "$FAIL_SYSTEM" = yes ]; then exit 8; fi
''')
            script=root/'scenario.sh';script.write_text('set -euo pipefail\nVERSION_CODENAME='+shlex.quote(codename)+'\n'+functions+'vp_apt update -qq\nvp_apt install -y python3\n'+('kill -'+signal+' $$\n' if signal else '')+('exit '+str(exit_status)+'\n' if exit_status is not None else ''))
            env={**os.environ,'PATH':str(commands)+os.pathsep+os.defpath,'TMPDIR':str(root/'tmp'),'XCPC_MIRROR':mode,'FAIL_MIRROR':fail_mirror,'FAIL_SYSTEM':'yes' if fail_system else 'no','APT_LOG':str(root/'calls'),'APT_ARGS':str(root/'args'),'OBSERVED':str(root/'sources'),'FIXTURE_EUID':'1000','SUDO_LOG':str(root/'sudo'),'RM_LOG':str(root/'rm'),'CLEANUP_FAIL':'yes' if cleanup_fails else 'no','CLEANUP_SUDO_FAIL':'yes' if cleanup_sudo_fails else 'no'}
            result=subprocess.run(['/bin/bash',str(script)],env=env,capture_output=True,text=True)
            calls=(root/'calls').read_text().splitlines() if (root/'calls').exists() else []
            observed=(root/'sources').read_text() if (root/'sources').exists() else ''
            remaining=list((root/'tmp').iterdir())
            if not(cleanup_fails and cleanup_sudo_fails):
                self.assertEqual(remaining,[],'Temporary indexes must be removed even on failure')
            if details:
                return result,{'calls':calls,'sources':observed,'sudo':(root/'sudo').read_text().splitlines() if (root/'sudo').exists() else [],
                    'args':(root/'args').read_bytes().split(b'\0')[:-1] if (root/'args').exists() else [],
                    'rm':(root/'rm').read_text().splitlines() if (root/'rm').exists() else [],'remaining':[p.name for p in remaining]}
            return result,calls,observed

    def test_arm64_uses_ports_and_isolated_signed_sources(self):
        result,calls,sources=self.scenario()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(calls,['mirror update','mirror install'])
        self.assertIn('https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/',sources)
        self.assertIn('signed-by=',sources);self.assertNotIn('trusted=yes',sources)
        self.assertIn('resolute-security',sources)

    def test_amd64_uses_ubuntu_repository(self):
        result,_,sources=self.scenario(arch='amd64')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('https://mirrors.tuna.tsinghua.edu.cn/ubuntu/',sources)
        self.assertNotIn('/ubuntu-ports/',sources)

    def test_update_failure_falls_back_to_original_system_sources(self):
        result,calls,_=self.scenario(fail_mirror='update')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(calls,['mirror update','system update','system install'])

    def test_package_failure_refreshes_original_indexes_before_retry(self):
        result,calls,_=self.scenario(fail_mirror='install')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(calls,['mirror update','mirror install','system update','system install'])

    def test_official_optout_uses_existing_sources_only(self):
        result,calls,sources=self.scenario(mode='official')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(calls,['system update','system install']);self.assertEqual(sources,'')

    def test_all_sources_fail_without_reporting_success(self):
        result,calls,_=self.scenario(fail_mirror='all',fail_system=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,['mirror update','system update'])

    def test_isolated_mirror_indexes_and_caches_do_not_touch_existing_sources(self):
        result,data=self.scenario(details=True)
        self.assertEqual(result.returncode,0,result.stderr)
        arguments=[os.fsdecode(arg) for arg in data['args']]
        self.assertIn('Dir::Etc::sourceparts=-',arguments)
        for key in ('Dir::Etc::sourcelist=','Dir::State::lists=','Dir::Cache::archives='):
            paths=[a.removeprefix(key) for a in arguments if a.startswith(key)]
            self.assertTrue(paths)
            self.assertTrue(all('/xcpc-apt.' in p for p in paths),paths)
        self.assertNotIn('AllowInsecureRepositories=true',arguments)
        self.assertNotIn('AllowUnauthenticated=true',arguments)

    def test_unknown_architecture_codename_or_absent_keyring_uses_existing_sources(self):
        for options in ({'arch':'unsupported'},{'codename':'../invalid'},{'codename':''},{'has_keyring':False}):
            with self.subTest(options=options):
                result,calls,sources=self.scenario(**options)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(calls,['system update','system install'])
                self.assertEqual(sources,'')

    def test_invalid_mirror_enum_fails_before_any_apt_or_sudo(self):
        for mode in ('auto','https://untrusted.example',' official '):
            with self.subTest(mode=mode):
                result,data=self.scenario(mode=mode,details=True)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertEqual(data['calls'],[])
                self.assertEqual(data['sudo'],[])
                self.assertEqual(data['rm'],[])

    def test_local_apt_owned_partial_cleanup_uses_sudo_for_only_its_temp_directory(self):
        result,data=self.scenario(cleanup_fails=True,exit_status=23,details=True)
        self.assertEqual(result.returncode,23,result.stderr)
        cleanup=[call for call in data['sudo'] if call.startswith('rm ')]
        self.assertEqual(len(cleanup),1)
        self.assertIn('rm -rf -- ',cleanup[0])
        self.assertIn('/xcpc-apt.',cleanup[0])
        self.assertEqual(len(data['rm']),2)
        self.assertEqual(data['rm'][0],data['rm'][1])
        self.assertEqual(data['remaining'],[])

    def test_unavailable_cleanup_privilege_warns_without_masking_installer_failure(self):
        result,data=self.scenario(cleanup_fails=True,cleanup_sudo_fails=True,exit_status=29,details=True)
        self.assertEqual(result.returncode,29,result.stderr)
        self.assertIn('Could not remove temporary Ubuntu indexes:',result.stderr)
        self.assertEqual(len(data['remaining']),1)
        self.assertTrue(data['remaining'][0].startswith('xcpc-apt.'))

    def test_cancel_or_termination_preserves_signal_status_and_cleans_temporary_indexes(self):
        for signal,status in (('INT',130),('TERM',143)):
            with self.subTest(signal=signal):
                result,data=self.scenario(signal=signal,details=True)
                self.assertEqual(result.returncode,status,result.stderr)
                self.assertEqual(data['remaining'],[])


if __name__=='__main__':unittest.main()
