"""Exercise the Windows setup script with mocked tools, never real installers.

Set XCPC_TEST_POWERSHELL to a portable pwsh executable when it is not on PATH.
These tests run on any pwsh host; they do not replace a native Windows smoke test.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SETUP = Path(__file__).resolve().parents[1] / 'install.ps1'
ONLINE = SETUP.parent / 'deploy/online-install.ps1'
POWERSHELL = (os.environ.get('XCPC_TEST_POWERSHELL') or shutil.which('pwsh')
              or shutil.which('powershell'))


@unittest.skipUnless(POWERSHELL, 'PowerShell required (pwsh, powershell, or XCPC_TEST_POWERSHELL)')
class WindowsSetupTests(unittest.TestCase):
    def run_powershell(self, body):
        with tempfile.TemporaryDirectory(prefix='xcpc windows tests ') as temporary:
            script = Path(temporary) / 'scenario.ps1'
            script.write_text(". $env:XCPC_SETUP_SCRIPT\n" + body, encoding='utf-8')
            env = os.environ | {'XCPC_SETUP_SCRIPT': str(SETUP), 'LOCALAPPDATA': temporary,
                                'XCPC_MIRROR':'official',
                                'OS': 'Windows_NT', 'ProgramFiles': str(Path(temporary)/'Program Files'), 'ProgramW6432': str(Path(temporary)/'Program Files')}
            result = subprocess.run(
                [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script)],
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = [line.removeprefix('__RESULT__') for line in result.stdout.splitlines()
                    if line.startswith('__RESULT__')]
            self.assertEqual(len(data), 1, result.stdout + result.stderr)
            return json.loads(data[0])

    def test_script_parses_and_help_never_checks_or_installs_prerequisites(self):
        data = self.run_powershell(r'''
$tokens = $null; $errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($env:XCPC_SETUP_SCRIPT, [ref]$tokens, [ref]$errors)
function Get-XcpcCommandBin { throw 'help touched setup' }
function Ensure-XcpcWinGet { throw 'help touched WinGet' }
$status = Invoke-XcpcWindowsSetup -InstallerArgs @('--help')
Write-Output ('__RESULT__' + (@{ Status = $status; Errors = @($errors | ForEach-Object { $_.Message }) } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data, {'Status': 0, 'Errors': []})

    def test_online_wrapper_repairs_machine_and_current_session_paths_after_child_install(self):
        data = self.run_powershell(". '" + str(ONLINE).replace("'", "''") + "'\n" + r'''
$script:machinePath = 'C:\Existing;C:\Other'
$script:writes = 0; $script:confirmed = @()
$env:Path = 'C:\CondaProcessOnly;C:\Existing'
function Get-XcpcOnlineEnvironmentPath { param($Scope); if ($Scope -eq 'User') { return 'C:\User commands' }; return $script:machinePath }
function Set-XcpcOnlineMachinePath { param($Value); $script:machinePath = $Value; $script:writes++ }
function Test-Path { param($LiteralPath, $PathType); return $true }
function Confirm-XcpcOnlineCommand { param($Command); $script:confirmed += $Command }
$bin = Join-Path $env:LOCALAPPDATA 'custom commands'
Set-XcpcOnlineCommandPath $bin
Set-XcpcOnlineCommandPath $bin
Write-Output ('__RESULT__' + (@{ MachinePath = $script:machinePath; ProcessPath = $env:Path; Writes = $script:writes; Bin = $bin; Confirmed = $script:confirmed } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['MachinePath'], data['Bin']+';C:\\Existing;C:\\Other')
        self.assertEqual(data['ProcessPath'], data['Bin']+';C:\\CondaProcessOnly;C:\\Existing;C:\\Other;C:\\User commands')
        self.assertEqual(data['Writes'], 1)
        self.assertEqual(len(data['Confirmed']), 2)

    def test_online_archive_accepts_flat_and_wrapped_layouts_with_one_installer(self):
        data = self.run_powershell(". '" + str(ONLINE).replace("'", "''") + "'\n" + r'''
$source = Join-Path $env:LOCALAPPDATA 'source'
$wrapped = Join-Path $source 'VP'
New-Item -ItemType Directory -Path $source, $wrapped | Out-Null
function Add-FixtureInstallers($Root) {
    foreach ($file in @('install.py', 'install.sh', 'install.ps1')) {
        New-Item -ItemType File -Path (Join-Path $Root $file) | Out-Null
    }
}
Add-FixtureInstallers $source
$flat = Resolve-XcpcOnlineInstaller $source
Add-FixtureInstallers $wrapped
try { $null = Resolve-XcpcOnlineInstaller $source } catch { $ambiguous = $_.Exception.Message }
foreach ($file in @('install.py', 'install.sh', 'install.ps1')) {
    Remove-Item -LiteralPath (Join-Path $source $file)
}
$nested = Resolve-XcpcOnlineInstaller $source
Remove-Item -LiteralPath (Join-Path $wrapped 'install.ps1')
try { $null = Resolve-XcpcOnlineInstaller $source } catch { $missing = $_.Exception.Message }
Write-Output ('__RESULT__' + (@{ Flat=$flat; Wrapped=$nested; Ambiguous=$ambiguous; Missing=$missing } | ConvertTo-Json -Compress))
''')
        self.assertEqual(Path(data['Flat']).parent.name, 'source')
        self.assertEqual(Path(data['Wrapped']).parent.name, 'VP')
        self.assertIn('Multiple source roots', data['Ambiguous'])
        self.assertIn('missing install.ps1', data['Missing'])

    def test_online_noop_global_update_reapplies_the_machine_command_path(self):
        data = self.run_powershell(". '" + str(ONLINE).replace("'", "''") + "'\n" + r'''
$script:repaired = @(); $script:calls = @()
$bin = Join-Path $env:ProgramFiles 'XCPC/bin'
$command = Join-Path $bin 'xcpc.exe'
function Test-Path { param($LiteralPath, $PathType); return $true }
function Get-XcpcOnlineEnvironmentPath { param($Scope); return $bin }
function Invoke-FixtureXcpc { param($Operation); $script:calls += (@($Operation) + @($args) -join ' '); $global:LASTEXITCODE=0 }
Set-Alias -Name $command -Value Invoke-FixtureXcpc
function Set-XcpcOnlineCommandPath { param($Bin); $script:repaired += $Bin }
function Initialize-XcpcOnlineUserBridge {param($Bin);$script:initialized=$Bin}
Invoke-XcpcOnlineSetup
Write-Output ('__RESULT__' + (@{ Calls = $script:calls; Repaired = $script:repaired; Bin=$bin;Initialized=$script:initialized } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Calls'], ['update --cloud'])
        self.assertEqual(data['Repaired'], [data['Bin']])
        self.assertEqual(data['Initialized'],data['Bin'])

    def test_online_legacy_private_command_uses_the_new_global_installer(self):
        data = self.run_powershell(". '" + str(ONLINE).replace("'", "''") + "'\n" + r'''
$script:downloaded=$false
function Test-Path { param($LiteralPath, $PathType); return $false }
function Get-Command { throw 'Legacy private command must not be upgraded by the global installer' }
function Get-XcpcOnlineEnvironmentPath { param($Scope); return 'C:\Windows\System32' }
function Invoke-RestMethod { param($Uri,$TimeoutSec); $script:downloaded=$true; throw 'fixture-download-stop' }
try { Invoke-XcpcOnlineSetup } catch { $message=$_.Exception.Message }
Write-Output ('__RESULT__' + (@{ Downloaded=$script:downloaded; Message=$message } | ConvertTo-Json -Compress))
''')
        self.assertTrue(data['Downloaded'])
        self.assertEqual(data['Message'],'fixture-download-stop')

    def test_python_prefers_313_then_3_then_compatible_python_and_skips_store_alias(self):
        for available, expected, expected_calls in (
            ('-3.13', '-3.13', ['C:\\Python Launcher\\py.exe:-3.13']),
            ('-3', '-3', ['C:\\Python Launcher\\py.exe:-3.13', 'C:\\Python Launcher\\py.exe:-3']),
            ('python', None, ['C:\\Python Launcher\\py.exe:-3.13', 'C:\\Python Launcher\\py.exe:-3',
                              'C:\\Old Python\\python.exe:-I', 'C:\\Python 313\\python.exe:-I']),
        ):
            with self.subTest(available=available):
                data = self.run_powershell(r'''
$script:calls = @(); $script:probes = @()
function Get-Command {
    param($Name, $CommandType, [switch]$All, $ErrorAction)
    if ($Name -eq 'py.exe') { [pscustomobject]@{ Source = 'C:\Python Launcher\py.exe' } }
    if ($Name -eq 'python.exe') {
        [pscustomobject]@{ Source = 'C:\Users\fixture\AppData\Local\Microsoft\WindowsApps\python.exe' }
        [pscustomobject]@{ Source = 'C:\Old Python\python.exe' }
        [pscustomobject]@{ Source = 'C:\Python 313\python.exe' }
    }
}
function Invoke-XcpcNative {
    param($FilePath, $ArgumentList, [switch]$Quiet)
    $script:calls += ($FilePath + ':' + $ArgumentList[0])
    $script:probes += ,@($ArgumentList)
    if ($ArgumentList[0] -eq AVAILABLE -or ($FilePath -eq 'C:\Python 313\python.exe' -and AVAILABLE -eq 'python')) { return 0 }
    return 1
}
$python = Find-XcpcPython
Write-Output ('__RESULT__' + (@{ Executable = $python.Executable; Prefix = @($python.Prefix); Calls = @($script:calls); Probes = @($script:probes) } | ConvertTo-Json -Compress -Depth 10))
'''.replace('AVAILABLE', "'" + available + "'"))
                self.assertEqual(data['Calls'], expected_calls)
                self.assertEqual(data['Prefix'], [expected] if expected else [])
                self.assertEqual(data['Executable'], 'C:\\Python Launcher\\py.exe' if expected else 'C:\\Python 313\\python.exe')
                for probe in data['Probes']:
                    options = probe[1:] if probe[0] in ('-3.13', '-3') else probe
                    self.assertEqual(options[:2], ['-I', '-c'])

    def test_real_probe_code_rejects_old_python_and_missing_venv_or_ensurepip(self):
        data = self.run_powershell(r'''
function Get-XcpcApplicationPaths { param($Name, [switch]$SkipWindowsApps); if ($Name -eq 'python.exe') { return 'python-fixture' } }
function Invoke-XcpcNative { param($FilePath, $ArgumentList, [switch]$Quiet); $script:probe = @($ArgumentList); return 0 }
$null = Find-XcpcPython
Write-Output ('__RESULT__' + (@{ Probe = @($script:probe) } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Probe'][:2], ['-I', '-c'])
        code = data['Probe'][2]
        for version, missing, expected in (
            ((3, 11), '', 1), ((3, 12), '', 0), ((3, 13), '', 0),
            ((3, 13), 'venv', 1), ((3, 13), 'ensurepip', 1),
        ):
            with self.subTest(version=version, missing=missing):
                # Execute the exact code passed to Python, with controlled module
                # availability. No interpreter or environment is installed.
                fixture = f'''import builtins, sys, types
sys.version_info = {version!r}
original_import = builtins.__import__
def fixture_import(name, *args, **kwargs):
    if name == {missing!r}:
        raise ImportError('fixture missing module: ' + name)
    if name in ('venv', 'ensurepip'):
        return types.SimpleNamespace(version=lambda: 'fixture')
    return original_import(name, *args, **kwargs)
builtins.__import__ = fixture_import
'''
                result = subprocess.run([sys.executable, '-I', '-c', fixture + code],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)

    def run_setup_scenario(self, present, arguments=(), status=0, winget_status=0):
        config = json.dumps({'Present': present, 'Arguments': list(arguments), 'Status': status,
                             'WingetStatus': winget_status}).replace("'", "''")
        return self.run_powershell("$config = '" + config + "' | ConvertFrom-Json\n" + r'''
$script:present = @($config.Present)
$script:installs = @(); $script:pythonArgs = @(); $script:pathWrites = @()
$script:refreshes = 0; $script:wingetCalls = 0
function Test-XcpcAdministrator { return $true }
function Set-XcpcGlobalPermissions { param($Root,$Bin) }
function Initialize-XcpcUserBridge { param($Root) }
function Update-XcpcProcessPath { $script:refreshes++ }
function Get-XcpcPrerequisites {
    $python = $null
    if ($script:present -contains 'Python') { $python = [pscustomobject]@{ Executable = 'python-fixture'; Prefix = @('-3.13') } }
    return [pscustomobject]@{ Python = $python; Git = ($script:present -contains 'Git'); Terminal = ($script:present -contains 'Terminal') }
}
function Ensure-XcpcWinGet { $script:wingetCalls++; return 'winget-fixture' }
function Invoke-XcpcNative {
    param($FilePath, $ArgumentList, [switch]$Quiet)
    if ($FilePath -eq 'winget-fixture') {
        $script:installs += ,@($ArgumentList)
        $id = $ArgumentList[3]
        if ($id -eq 'Python.Python.3.13') { $script:present += 'Python' }
        if ($id -eq 'Git.Git') { $script:present += 'Git' }
        if ($id -eq 'Microsoft.WindowsTerminal') { throw 'Windows Terminal is optional and must not be installed' }
        return [int]$config.WingetStatus
    }
    if ($FilePath -ne 'python-fixture') { throw 'unexpected command' }
    $script:pythonArgs = @($ArgumentList)
    return [int]$config.Status
}
function Add-XcpcCommandPath { param($Bin); $script:pathWrites += $Bin }
$status = Invoke-XcpcWindowsSetup -InstallerArgs @($config.Arguments) 2>$null
Write-Output ('__RESULT__' + (@{ Status = $status; Installs = @($script:installs); PythonArgs = @($script:pythonArgs); PathWrites = @($script:pathWrites); Refreshes = $script:refreshes; WingetCalls = $script:wingetCalls } | ConvertTo-Json -Compress -Depth 10))
''')

    def test_installs_only_missing_packages_and_refreshes_path(self):
        for present, expected in (
            ([], ['Python.Python.3.13', 'Git.Git']),
            (['Python', 'Terminal'], ['Git.Git']),
            (['Python', 'Git'], []),
            (['Python', 'Git', 'Terminal'], []),
        ):
            with self.subTest(present=present):
                data = self.run_setup_scenario(present)
                self.assertEqual(data['Status'], 0)
                self.assertEqual([args[3] for args in data['Installs']], expected)
                for args in data['Installs']:
                    self.assertEqual(args[:3], ['install', '--exact', '--id'])
                    self.assertEqual(args[4:6], ['--source', 'winget'])
                    self.assertEqual(args[-2:], ['--scope','machine'] if args[3] in ('Python.Python.3.13','Git.Git') else ['--accept-source-agreements','--disable-interactivity'])
                    self.assertEqual('--force' in args, args[3] in ('Python.Python.3.13','Git.Git'))
                self.assertEqual(data['Refreshes'], 1 + len(expected))
                self.assertEqual(data['WingetCalls'], int(bool(expected)))

    def test_offline_missing_tools_performs_no_download_or_install(self):
        data = self.run_setup_scenario(['Python'], ['--offline'])
        self.assertEqual(data['Status'], 1)
        self.assertEqual(data['Installs'], [])
        self.assertEqual(data['WingetCalls'], 0)
        self.assertEqual(data['PythonArgs'], [])
        self.assertEqual(data['PathWrites'], [])

    def test_arguments_with_spaces_are_forwarded_and_custom_bin_is_used(self):
        for custom in (['--target-bin', 'custom commands'], ['--target-bin=custom commands']):
            with self.subTest(custom=custom):
                arguments = ['--target-root', 'app folder', *custom, '--offline']
                data = self.run_setup_scenario(['Python', 'Git', 'Terminal'], arguments)
                self.assertEqual(data['Status'], 0)
                self.assertEqual(data['PythonArgs'], ['-3.13', str(SETUP.with_name('install.py')), *arguments])
                self.assertEqual(len(data['PathWrites']), 1)
                self.assertEqual(Path(data['PathWrites'][0]).name, 'custom commands')

    def test_python_and_winget_failures_do_not_publish_command_path(self):
        data = self.run_setup_scenario(['Python', 'Git', 'Terminal'], status=23)
        self.assertEqual(data['Status'], 23)
        self.assertEqual(data['PathWrites'], [])
        data = self.run_setup_scenario([], winget_status=9)
        self.assertEqual(data['Status'], 1)
        self.assertEqual(len(data['Installs']), 1)
        self.assertEqual(data['PythonArgs'], [])
        self.assertEqual(data['PathWrites'], [])

    def test_path_persistence_preserves_existing_entries_and_is_idempotent(self):
        data = self.run_powershell(r'''
$script:userPath = 'C:\Existing;C:\Other'
$script:writes = 0
$env:Path = 'C:\ProcessOnly;C:\Existing'
function Get-XcpcEnvironmentPath { param($Scope); return $script:userPath }
function Set-XcpcUserPath { param($Value); $script:userPath = $Value; $script:writes++ }
Add-XcpcCommandPath 'C:\Custom commands'
Add-XcpcCommandPath 'C:\Custom commands'
Write-Output ('__RESULT__' + (@{ UserPath = $script:userPath; ProcessPath = $env:Path; Writes = $script:writes } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['UserPath'], 'C:\\Custom commands;C:\\Existing;C:\\Other')
        self.assertEqual(data['ProcessPath'], 'C:\\Custom commands;C:\\ProcessOnly;C:\\Existing')
        self.assertEqual(data['Writes'], 1)

    def test_refresh_imports_registry_paths_without_losing_process_paths(self):
        data = self.run_powershell(r'''
$env:Path = 'C:\ProcessOnly;C:\Existing'
function Get-XcpcEnvironmentPath {
    param($Scope)
    if ($Scope -eq 'Machine') { return 'C:\Existing;C:\Git\cmd' }
    return 'C:\Python 313;C:\Python 313\Scripts'
}
Update-XcpcProcessPath
Write-Output ('__RESULT__' + (@{ Entries = @($env:Path -split ';') } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Entries'][:5], [r'C:\ProcessOnly', r'C:\Existing',
                                              r'C:\Git\cmd', r'C:\Python 313',
                                              r'C:\Python 313\Scripts'])
        self.assertTrue(data['Entries'][5].replace('\\', '/').endswith('/Microsoft/WindowsApps'))

    def test_machine_git_uses_both_program_files_layouts_and_checks_its_version(self):
        data=self.run_powershell(r'''
$env:ProgramW6432='/machine/Program Files';$env:ProgramFiles=$env:ProgramW6432
${env:ProgramFiles(x86)}='/machine/Program Files (x86)'
$env:USERPROFILE='/users/alice';$env:PUBLIC='/users/Public';$env:LOCALAPPDATA='/users/alice/AppData/Local'
$script:broken=Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
$script:valid=Join-Path ${env:ProgramFiles(x86)} 'Git\cmd\git.exe';$script:calls=@()
function Get-XcpcMachineGitPaths {}
function Get-XcpcApplicationPaths {param($Name);return '/users/alice/Conda/git.exe'}
function Test-Path {param($LiteralPath,$PathType);return $LiteralPath -in @($script:broken,$script:valid)}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=@{File=$FilePath;Arguments=$ArgumentList};if($FilePath -eq $script:valid){return 0};return 1}
$git=Find-XcpcTool 'git.exe' -ProbeVersion -SystemWide
Write-Output ('__RESULT__'+(@{Git=$git;Expected=$script:valid;Calls=$script:calls}|ConvertTo-Json -Compress -Depth 6))
''')
        self.assertEqual(data['Git'],data['Expected'])
        self.assertEqual(len(data['Calls']),2)
        self.assertTrue(all(call['Arguments']==['--version'] for call in data['Calls']))

    def test_machine_git_registration_accepts_custom_shared_path_but_rejects_user_profiles(self):
        data=self.run_powershell(r'''
$env:ProgramW6432='/machine/Program Files';$env:ProgramFiles=$env:ProgramW6432
${env:ProgramFiles(x86)}='/machine/Program Files (x86)'
$env:USERPROFILE='/users/alice';$env:PUBLIC='/users/Public';$env:LOCALAPPDATA='/users/alice/AppData/Local'
$script:custom='/machine/shared Git/cmd/git.exe';$script:calls=@()
function Get-XcpcMachineGitPaths {
 [pscustomobject]@{Executable='/users/bob/Git/cmd/git.exe';Directory='/users/bob/Git'}
 [pscustomobject]@{Executable=$script:custom;Directory='/machine/shared Git'}
}
function Get-XcpcApplicationPaths {param($Name);return '/users/alice/Git/cmd/git.exe'}
function Test-Path {param($LiteralPath,$PathType);return $LiteralPath -in @($script:custom,'/users/bob/Git/cmd/git.exe')}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=,$FilePath;return 0}
$git=Find-XcpcTool 'git.exe' -ProbeVersion -SystemWide
$unregistered=Test-XcpcSharedToolPath '/unregistered/Git/cmd/git.exe'
$boundary=Test-XcpcSharedToolPath '/machine/Program Files-private/Git/cmd/git.exe'
$env:USERPROFILE='/alice';$env:PUBLIC='';$env:LOCALAPPDATA='/alice/AppData/Local'
$rootProfile=Test-XcpcSharedToolPath '/machine/shared Git/cmd/git.exe' '/machine/shared Git'
Write-Output ('__RESULT__'+(@{Git=$git;Calls=$script:calls;Unregistered=$unregistered;Boundary=$boundary;RootProfile=$rootProfile}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Git'],'/machine/shared Git/cmd/git.exe')
        self.assertEqual(data['Calls'],['/machine/shared Git/cmd/git.exe'])
        self.assertFalse(data['Unregistered']);self.assertFalse(data['Boundary'])
        self.assertTrue(data['RootProfile'])

    def test_git_registered_during_install_is_immediately_reused_and_its_path_is_persisted(self):
        data=self.run_powershell(r'''
$env:ProgramW6432='/machine/Program Files';$env:ProgramFiles=$env:ProgramW6432
${env:ProgramFiles(x86)}='/machine/Program Files (x86)'
$env:USERPROFILE='/users/alice';$env:PUBLIC='/users/Public';$env:LOCALAPPDATA='/users/alice/AppData/Local'
$env:Path='/users/alice/Conda;/machine/Existing'
$script:registered=$false;$script:git='/machine/shared Git/cmd/git.exe';$script:installs=@();$script:machinePath='/machine/Existing';$script:writes=0
function Test-XcpcAdministrator {return $true}
function Get-XcpcMachineGitPaths {if($script:registered){[pscustomobject]@{Executable=$script:git;Directory='/machine/shared Git'}}}
function Get-XcpcApplicationPaths {param($Name);if($Name -eq 'git.exe'){return '/users/alice/Conda/git.exe'};if($Name -eq 'wt.exe'){return 'terminal-fixture'}}
function Find-XcpcPython {param([switch]$SystemWide);return [pscustomobject]@{Executable='python-fixture';Prefix=@()}}
function Test-Path {param($LiteralPath,$PathType);return $script:registered -and $LiteralPath -eq $script:git}
function Update-XcpcProcessPath {}
function Ensure-XcpcWinGet {return 'winget-fixture'}
function Get-XcpcEnvironmentPath {param($Scope);if($Scope -ne 'Machine'){throw 'Private PATH must not be persisted'};return $script:machinePath}
function Set-XcpcMachinePath {param($Value);$script:machinePath=$Value;$script:writes++}
function Set-XcpcUserPath {throw 'Private PATH must not be persisted'}
function Invoke-XcpcNative {
 param($FilePath,$ArgumentList,[switch]$Quiet)
 if($FilePath -eq 'winget-fixture'){$script:installs+=,@($ArgumentList);$script:registered=$true;return 0}
 if($FilePath -eq $script:git){return 0}
 if($FilePath -eq 'python-fixture'){$script:installPath=$env:Path;return 0}
 throw 'Private or unverified Git must not be executed'
}
function Set-XcpcGlobalPermissions {param($Root,$Bin)}
function Initialize-XcpcUserBridge {param($Root)}
function Add-XcpcCommandPath {param($Bin,[switch]$SystemWide)}
$status=Invoke-XcpcWindowsSetup
Add-XcpcGitPath $script:git -SystemWide
Write-Output ('__RESULT__'+(@{Status=$status;Installs=$script:installs;Machine=$script:machinePath;Process=$script:installPath;Writes=$script:writes;Directory=(Split-Path -Parent $script:git);Destination=(Join-Path $env:ProgramW6432 'Git')}|ConvertTo-Json -Compress -Depth 6))
''')
        self.assertEqual(data['Status'],0)
        self.assertEqual(len(data['Installs']),1)
        self.assertIn('--force',data['Installs'][0]);self.assertEqual(data['Installs'][0][-2:],['--scope','machine'])
        args=data['Installs'][0];self.assertEqual(args[args.index('--location')+1],data['Destination'])
        self.assertEqual(data['Machine'],'/machine/Existing;'+data['Directory'])
        self.assertEqual(data['Writes'],1)
        self.assertEqual(data['Process'],data['Directory']+';/users/alice/Conda;/machine/Existing')

    def test_missing_git_after_successful_package_command_reports_git_specific_diagnostics(self):
        data=self.run_powershell(r'''
$script:message='';$script:published=$false
function Test-XcpcAdministrator {return $true}
function Update-XcpcProcessPath {}
function Get-XcpcPrerequisites {return [pscustomobject]@{Python=[pscustomobject]@{Executable='python-fixture';Prefix=@()};Git=$null;Terminal='terminal-fixture'}}
function Ensure-XcpcWinGet {return 'winget-fixture'}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);return 0}
function Add-XcpcCommandPath {param($Bin,[switch]$SystemWide);$script:published=$true}
function Write-Error {param($Message,$ErrorAction);$script:message=$Message}
$status=Invoke-XcpcWindowsSetup
Write-Output ('__RESULT__'+(@{Status=$status;Message=$script:message;Published=$script:published}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],1)
        self.assertIn('Git setup did not provide a usable shared git.exe',data['Message'])
        self.assertIn('both HKLM registry views',data['Message'])
        self.assertNotIn('Python must be installed',data['Message'])
        self.assertFalse(data['Published'])

    def test_winget_registration_precedes_official_per_user_repair(self):
        for registered in (True, False):
            with self.subTest(registration_succeeds=registered):
                data = self.run_powershell('$script:registerWorks = $' + str(registered).lower() + '\n' + r'''
$script:ready = $false; $script:events = @()
function Find-XcpcTool { param($Name, [switch]$ProbeVersion); if ($script:ready) { return 'winget-fixture' } }
function Update-XcpcProcessPath { }
function Add-AppxPackage { param([switch]$RegisterByFamilyName, $MainPackage, $ErrorAction); $script:events += 'register'; $script:ready = $script:registerWorks }
function Get-Module { param([switch]$ListAvailable, $Name) }
function Get-PackageProvider { param($Name, [switch]$ListAvailable, $ErrorAction) }
function Install-PackageProvider { param($Name, $MinimumVersion, $Scope, [switch]$Force, $ErrorAction); $script:events += ('provider:' + $Scope) }
function Install-Module { param($Name, $Repository, $Scope, [switch]$Force, [switch]$AllowClobber, $ErrorAction); $script:events += ('module:' + $Name + ':' + $Scope) }
function Import-Module { param($Name, $ErrorAction); $script:events += ('import:' + $Name) }
function Repair-WinGetPackageManager { param([switch]$Latest, [switch]$AllUsers, $ErrorAction); $script:events += ('repair:' + $Latest + ':' + $AllUsers); $script:ready = $true }
$winget = Ensure-XcpcWinGet
Write-Output ('__RESULT__' + (@{ Winget = $winget; Events = @($script:events) } | ConvertTo-Json -Compress))
''')
                self.assertEqual(data['Winget'], 'winget-fixture')
                self.assertEqual(data['Events'], ['register'] if registered else [
                    'register', 'provider:CurrentUser', 'module:Microsoft.WinGet.Client:CurrentUser',
                    'import:Microsoft.WinGet.Client', 'repair:True:False'])

    def test_native_command_returns_exact_exit_status(self):
        data = self.run_powershell(r'''
$executable = (Get-Process -Id $PID).Path
$status = Invoke-XcpcNative $executable @('-NoLogo', '-NoProfile', '-NonInteractive', '-Command', 'exit 23')
Write-Output ('__RESULT__' + (@{ Status = $status } | ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'], 23)

    def test_default_install_is_machinewide_but_explicit_custom_paths_are_retained(self):
        data=self.run_powershell(r'''
$default=Get-XcpcLayout @()
$custom=Get-XcpcLayout @('--target-root','custom app','--target-bin','custom commands')
Write-Output ('__RESULT__'+(@{Default=$default;Custom=$custom;ProgramFiles=$env:ProgramFiles}|ConvertTo-Json -Compress -Depth 5))
''')
        self.assertTrue(data['Default']['Global'])
        self.assertEqual(Path(data['Default']['Root']),Path(data['ProgramFiles'])/'XCPC/app')
        self.assertEqual(Path(data['Default']['Bin']),Path(data['ProgramFiles'])/'XCPC/bin')
        self.assertFalse(data['Custom']['Global'])
        self.assertEqual(Path(data['Custom']['Root']).name,'custom app')
        self.assertEqual(Path(data['Custom']['Bin']).name,'custom commands')

    def test_machine_path_is_persisted_without_writing_a_users_private_path(self):
        data=self.run_powershell(r'''
$script:machinePath='C:\Windows\System32;C:\Shared tools';$script:writes=0
$env:Path='C:\Conda process;C:\Windows\System32'
function Get-XcpcEnvironmentPath {param($Scope);if($Scope -ne 'Machine'){throw 'Private user PATH must not be changed'};return $script:machinePath}
function Set-XcpcMachinePath {param($Value);$script:machinePath=$Value;$script:writes++}
function Set-XcpcUserPath {throw 'Private user PATH must not be changed'}
Add-XcpcCommandPath 'C:\Program Files\XCPC\bin' -SystemWide
Add-XcpcCommandPath 'C:\Program Files\XCPC\bin' -SystemWide
Write-Output ('__RESULT__'+(@{Machine=$script:machinePath;Process=$env:Path;Writes=$script:writes}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Machine'],r'C:\Program Files\XCPC\bin;C:\Windows\System32;C:\Shared tools')
        self.assertEqual(data['Process'],r'C:\Program Files\XCPC\bin;C:\Conda process;C:\Windows\System32')
        self.assertEqual(data['Writes'],1)

    def test_default_install_requests_elevation_before_running_any_installer(self):
        for child_status in (0,23):
            data=self.run_powershell(r'''
$script:events=@()
function Test-XcpcAdministrator {return $false}
function Invoke-XcpcElevatedSetup {param($InstallerArgs);$script:events+='elevate';return CHILD_STATUS}
function Get-XcpcPrerequisites {throw 'Unelevated parent must not install prerequisites'}
function Initialize-XcpcUserBridge {param($Root);$script:events+='bridge'}
function Update-XcpcProcessPath {$script:events+='refresh'}
$status=Invoke-XcpcWindowsSetup
Write-Output ('__RESULT__'+(@{Status=$status;Events=$script:events}|ConvertTo-Json -Compress))
'''.replace('CHILD_STATUS',str(child_status)))
            self.assertEqual(data['Status'],child_status)
            self.assertEqual(data['Events'],['elevate','refresh','bridge'] if child_status==0 else ['elevate'])

    def test_elevation_preserves_unicode_paths_arguments_and_source_revision_without_shell_interpolation(self):
        data=self.run_powershell(r'''
$env:SystemRoot=$env:LOCALAPPDATA;$env:XCPC_SOURCE_REVISION='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
function Start-Process {param($FilePath,$Verb,$ArgumentList,[switch]$Wait,[switch]$PassThru);$script:file=$FilePath;$script:verb=$Verb;$script:arguments=$ArgumentList;return [pscustomobject]@{ExitCode=17}}
$status=Invoke-XcpcElevatedSetup @('--target-root',"C:\应用 app\O'Brien",'--target-bin','C:\commands;literal')
$code=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($script:arguments[-1]))
Write-Output ('__RESULT__'+(@{Status=$status;Verb=$script:verb;Arguments=$script:arguments;Code=$code;File=$script:file}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],17)
        self.assertEqual(data['Verb'],'RunAs')
        self.assertEqual(data['Arguments'][:4],['-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand'])
        self.assertIn("'C:\\应用 app\\O''Brien'",data['Code'])
        self.assertIn("'C:\\commands;literal'",data['Code'])
        self.assertIn("$env:XCPC_SOURCE_REVISION='"+'a'*40+"'",data['Code'])

    def test_shared_python_probe_rejects_an_interpreter_in_a_private_user_profile(self):
        data=self.run_powershell(r'''
function Get-ChildItem {param($LiteralPath,[switch]$Directory,$Filter,$ErrorAction)}
function Get-XcpcApplicationPaths {param($Name,[switch]$SkipWindowsApps);if($Name -eq 'python.exe'){return 'python-fixture'}}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:probe=$ArgumentList[2];return 0}
$null=Find-XcpcPython -SystemWide
Write-Output ('__RESULT__'+(@{Code=$script:probe}|ConvertTo-Json -Compress))
''')
        code=data['Code']
        for prefix,expected in (('/machine/Program Files/Python313',0),('/users/alice/AppData/Local/Python313',1)):
            fixture="import sys,os; sys.base_prefix="+repr(prefix)+"; os.environ['ProgramFiles']='/machine/Program Files'; os.environ.pop('ProgramW6432',None); os.environ.pop('ProgramFiles(x86)',None); "
            result=subprocess.run([sys.executable,'-I','-c',fixture+code],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,expected,result.stderr)

    def test_global_permissions_grant_other_users_read_execute_without_write(self):
        data=self.run_powershell(r'''
$env:SystemRoot=$env:LOCALAPPDATA;$script:calls=@()
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=,@($ArgumentList);return 0}
Set-XcpcGlobalPermissions 'C:\Program Files\XCPC\app' 'C:\Program Files\XCPC\bin'
Write-Output ('__RESULT__'+(@{Calls=$script:calls}|ConvertTo-Json -Compress -Depth 4))
''')
        self.assertEqual(len(data['Calls']),2)
        for arguments in data['Calls']:
            self.assertEqual(arguments[1:],['/grant','*S-1-5-32-545:(OI)(CI)RX','/T','/C'])

    def test_machine_registry_python_is_found_immediately_without_path_or_conda_launcher(self):
        data=self.run_powershell(r'''
$script:calls=@()
function Get-XcpcMachinePythonPaths { [pscustomobject]@{Executable='C:\Python313\python.exe';Directory='C:\Python313'} }
function Test-Path {param($LiteralPath,$PathType);return $LiteralPath -eq 'C:\Python313\python.exe'}
function Get-XcpcApplicationPaths {throw 'Conda and the stale Python launcher must not override a registered machine Python'}
function Get-ChildItem {throw 'Registered Python should be tested without assuming a Program Files directory layout'}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=,@($FilePath,$ArgumentList);return 0}
$python=Find-XcpcPython -SystemWide
Write-Output ('__RESULT__'+(@{Python=$python;Calls=$script:calls}|ConvertTo-Json -Compress -Depth 6))
''')
        self.assertEqual(data['Python'], {'Executable':r'C:\Python313\python.exe','Prefix':[]})
        self.assertEqual(len(data['Calls']),1)
        arguments=data['Calls'][0][1]
        self.assertEqual(arguments[:2],['-I','-c'])
        self.assertEqual(arguments[3],r'C:\Python313')

    def test_machine_registry_proof_accepts_shared_custom_python_and_rejects_user_profiles(self):
        data=self.run_powershell(r'''
function Get-XcpcMachinePythonPaths { [pscustomobject]@{Executable='/machine/shared Python/python.exe';Directory='/machine/shared Python'} }
function Test-Path {param($LiteralPath,$PathType);return $true}
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:code=$ArgumentList[2];return 0}
$null=Find-XcpcPython -SystemWide
Write-Output ('__RESULT__'+(@{Code=$script:code}|ConvertTo-Json -Compress))
''')
        for prefix,registered,expected in (('/machine/shared Python','/machine/shared Python',0),
                                            ('/users/other/Python313','/users/other/Python313',1),
                                            ('/unregistered/Python313','/machine/shared Python',1)):
            fixture=("import sys,os;sys.base_prefix="+repr(prefix)+";sys.argv=['-c',"+repr(registered)+
                     "];os.environ['USERPROFILE']='/users/alice';os.environ['PUBLIC']='/users/Public';"
                     "os.environ['ProgramFiles']='/machine/Program Files';os.environ.pop('ProgramW6432',None);os.environ.pop('ProgramFiles(x86)',None);")
            result=subprocess.run([sys.executable,'-I','-c',fixture+data['Code']],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,expected,result.stderr)

    def test_setup_continues_in_same_process_when_winget_registers_python_but_path_stays_stale(self):
        data=self.run_powershell(r'''
$script:registered=$false;$script:installedWith=$null;$script:wingetArgs=@()
function Test-XcpcAdministrator {return $true}
function Get-XcpcMachinePythonPaths {if($script:registered){[pscustomobject]@{Executable='C:\ProgramData\Python313\python.exe';Directory='C:\ProgramData\Python313'}}}
function Get-XcpcApplicationPaths {param($Name,[switch]$SkipWindowsApps);if($Name -eq 'py.exe'){return 'private-py'};if($Name -eq 'python.exe'){return 'private-conda'}}
function Get-ChildItem {param($LiteralPath,[switch]$Directory,$Filter,$ErrorAction)}
function Test-Path {param($LiteralPath,$PathType);return $true}
function Find-XcpcTool {param($Name,[switch]$ProbeVersion,[switch]$SystemWide);return 'available-'+$Name}
function Ensure-XcpcWinGet {return 'winget-fixture'}
function Update-XcpcProcessPath {}
function Set-XcpcGlobalPermissions {param($Root,$Bin)}
function Add-XcpcCommandPath {param($Bin,[switch]$SystemWide)}
function Initialize-XcpcUserBridge {param($Root)}
function Invoke-XcpcNative {
  param($FilePath,$ArgumentList,[switch]$Quiet)
  if($FilePath -eq 'winget-fixture'){$script:registered=$true;$script:wingetArgs=$ArgumentList;return 0}
  if($FilePath -eq 'C:\ProgramData\Python313\python.exe'){
    if($ArgumentList[0] -ne '-I'){$script:installedWith=$FilePath};return 0
  }
  return 1
}
$status=Invoke-XcpcWindowsSetup
Write-Output ('__RESULT__'+(@{Status=$status;Python=$script:installedWith;Winget=$script:wingetArgs}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],0)
        self.assertEqual(data['Python'],r'C:\ProgramData\Python313\python.exe')
        self.assertIn('--force',data['Winget'])
        self.assertEqual(data['Winget'][-2:],['--scope','machine'])

    def test_online_ordinary_parent_does_not_rewrite_an_existing_machine_path_entry(self):
        data=self.run_powershell(". '"+str(ONLINE).replace("'","''")+"'\n"+r'''
$bin=Join-Path $env:ProgramFiles 'XCPC\bin'
$script:machine='C:\Existing;'+$bin.ToUpperInvariant()+'\;C:\Other'
function Get-XcpcOnlineEnvironmentPath {param($Scope);if($Scope -eq 'Machine'){return $script:machine};return 'C:\User commands'}
function Set-XcpcOnlineMachinePath {throw 'Ordinary parent cannot write the machine PATH'}
function Test-Path {param($LiteralPath,$PathType);return $true}
function Confirm-XcpcOnlineCommand {param($Command);$script:confirmed=$Command}
Set-XcpcOnlineCommandPath $bin
Write-Output ('__RESULT__'+(@{Bin=$bin;ProcessPath=$env:Path;Confirmed=$script:confirmed}|ConvertTo-Json -Compress))
''')
        self.assertTrue(data['ProcessPath'].startswith(data['Bin']+';'))
        self.assertEqual(Path(data['Confirmed']).name,'xcpc.exe')

    def test_declined_elevation_explains_that_the_normal_parent_is_correct(self):
        data=self.run_powershell(r'''
$env:SystemRoot=$env:LOCALAPPDATA
function Test-XcpcAdministrator {return $false}
function Start-Process {throw 'fixture UAC declined'}
$status=Invoke-XcpcWindowsSetup 2>$null
Write-Output ('__RESULT__'+(@{Status=$status;Message=$Error[0].Exception.Message}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],1)
        self.assertIn('ordinary PowerShell remains the correct starting point',data['Message'])

    def test_failed_elevation_does_not_start_another_uac_loop(self):
        data=self.run_powershell(r'''
$env:XCPC_SETUP_ELEVATED='1'
function Test-XcpcAdministrator {return $false}
function Invoke-XcpcElevatedSetup {throw 'Must not request elevation recursively'}
$status=Invoke-XcpcWindowsSetup 2>$null
Write-Output ('__RESULT__'+(@{Status=$status;Message=$Error[0].Exception.Message}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],1)
        self.assertIn('did not grant administrator access',data['Message'])

    def test_native_installer_failure_is_saved_outside_downloads_and_replayed(self):
        # Execute a real native process, not a mocked transcript implementation.
        body = r'''
$env:XCPC_SETUP_LOG=$null
function Test-XcpcAdministrator {return $true}
function Update-XcpcProcessPath {}
function Get-XcpcPrerequisites {
  return [pscustomobject]@{Python=[pscustomobject]@{Executable=FIXTURE_PYTHON;Prefix=@('-c','import sys; print(''PYTHON-INSTALL-FAILED-FIXTURE''); sys.exit(31)')};Git=$true;Terminal=$true}
}
$messages=@(& {$script:status=Invoke-XcpcWindowsSetup} 6>&1 2>$null)
$logs=@(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'XCPC/Logs') -Filter setup.log -Recurse)
Write-Output ('__RESULT__'+(@{Status=$script:status;Logs=@($logs.FullName);Content=(Get-Content -Raw -LiteralPath $logs[0].FullName);Messages=($messages|Out-String);Restored=($null -eq $env:XCPC_SETUP_LOG -or $env:XCPC_SETUP_LOG -eq '')}|ConvertTo-Json -Compress))
'''.replace('FIXTURE_PYTHON', "'"+sys.executable.replace("'","''")+"'")
        data=self.run_powershell(body)
        self.assertEqual(data['Status'],31)
        self.assertEqual(len(data['Logs']),1)
        self.assertIn('XCPC',data['Logs'][0])
        self.assertIn('Logs',data['Logs'][0])
        self.assertIn('PYTHON-INSTALL-FAILED-FIXTURE',data['Content'])
        self.assertIn('PYTHON-INSTALL-FAILED-FIXTURE',data['Messages'])
        self.assertIn('Installation log:',data['Messages'])
        self.assertTrue(data['Restored'])

    def test_uac_parent_replays_the_closed_childs_actual_error(self):
        data=self.run_powershell(r'''
$env:SystemRoot=$env:LOCALAPPDATA
$env:LOCALAPPDATA=Join-Path $env:LOCALAPPDATA "O'Brien profile"
$env:XCPC_SETUP_LOG=$null
function Test-XcpcAdministrator {return $false}
function Start-Process {
  param($FilePath,$Verb,$ArgumentList,[switch]$Wait,[switch]$PassThru)
  $script:code=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($ArgumentList[-1]))
  $script:childLog=$env:XCPC_SETUP_LOG
  Set-Content -LiteralPath $script:childLog -Encoding UTF8 -Value 'Setup failed: NATIVE-CHILD-ACCESS-DENIED-FIXTURE'
  return [pscustomobject]@{ExitCode=1}
}
$messages=@(& {$script:status=Invoke-XcpcWindowsSetup} 6>&1 2>$null)
Write-Output ('__RESULT__'+(@{Status=$script:status;Log=$script:childLog;Code=$script:code;Messages=($messages|Out-String);Exists=(Test-Path -LiteralPath $script:childLog);Restored=($null -eq $env:XCPC_SETUP_LOG -or $env:XCPC_SETUP_LOG -eq '')}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Status'],1)
        self.assertTrue(data['Exists'])
        self.assertIn('NATIVE-CHILD-ACCESS-DENIED-FIXTURE',data['Messages'])
        self.assertIn("$env:XCPC_SETUP_LOG='"+data['Log'].replace("'","''")+"'",data['Code'])
        self.assertTrue(data['Restored'])

    def test_online_failure_reports_inner_log_after_temporary_source_cleanup(self):
        data=self.run_powershell(". '"+str(ONLINE).replace("'","''")+"'\n"+r'''
$env:XCPC_SETUP_LOG='original-caller-value'
function Invoke-XcpcOnlineSetupCore {
  $script:log=$env:XCPC_SETUP_LOG
  $source=Join-Path $env:LOCALAPPDATA 'temporary-source'
  New-Item -ItemType Directory -Path $source | Out-Null
  Set-Content -LiteralPath $env:XCPC_SETUP_LOG -Encoding UTF8 -Value 'Setup failed: WINGET-FAILURE-FIXTURE'
  Remove-Item -LiteralPath $source -Recurse -Force
  throw 'Installation failed (exit 1).'
}
$messages=@(& {try {Invoke-XcpcOnlineSetup} catch {$script:errorMessage=$_.Exception.Message}} 6>&1)
Write-Output ('__RESULT__'+(@{Message=$script:errorMessage;Log=$script:log;Exists=(Test-Path -LiteralPath $script:log);Restored=$env:XCPC_SETUP_LOG;Messages=($messages|Out-String)}|ConvertTo-Json -Compress))
''')
        self.assertEqual(data['Message'],'Installation failed (exit 1).')
        self.assertTrue(data['Exists'])
        self.assertIn('WINGET-FAILURE-FIXTURE',data['Messages'])
        self.assertIn(data['Log'],data['Messages'])
        self.assertEqual(data['Restored'],'original-caller-value')

    @unittest.skipUnless(sys.version_info >= (3,12), 'real Python 3.12+ required for the actual native prerequisite probe')
    def test_real_machine_python_probe_works_with_legacy_and_standard_native_arguments(self):
        # Legacy reproduces Windows PowerShell 5.1's embedded-double-quote
        # stripping even when this regression runs in portable PowerShell 7.
        for mode in ('Legacy','Standard'):
            with self.subTest(mode=mode):
                body=r'''
$PSNativeCommandArgumentPassing=ARGUMENT_MODE
$env:ProgramFiles=FIXTURE_BASE
$env:ProgramW6432=FIXTURE_BASE
$env:USERPROFILE=Join-Path $env:LOCALAPPDATA 'users/fixture'
$env:PUBLIC=Join-Path $env:LOCALAPPDATA 'users/Public'
function Get-XcpcMachinePythonPaths {[pscustomobject]@{Executable=FIXTURE_PYTHON;Directory=(FIXTURE_BASE+[IO.Path]::DirectorySeparatorChar)}}
function Get-XcpcApplicationPaths {throw 'The actual registered Python prerequisite probe failed'}
$python=Find-XcpcPython -SystemWide
Write-Output ('__RESULT__'+(@{Executable=$python.Executable;Prefix=@($python.Prefix)}|ConvertTo-Json -Compress))
'''
                for key,value in (('ARGUMENT_MODE',mode),('FIXTURE_BASE',sys.base_prefix),('FIXTURE_PYTHON',sys.executable)):
                    body=body.replace(key,"'"+value.replace("'","''")+"'")
                data=self.run_powershell(body)
                self.assertEqual(data,{'Executable':sys.executable,'Prefix':[]})

    def test_default_mirror_uses_owned_temporary_winget_source_and_preserves_user_sources(self):
        data=self.run_powershell(r'''
$env:XCPC_MIRROR=$null
$script:calls=@()
function Test-XcpcAdministrator { return $true }
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=,@($ArgumentList);return 0}
$one=New-XcpcWinGetMirror 'winget-fixture'
Remove-XcpcWinGetMirror $one
$two=New-XcpcWinGetMirror 'winget-fixture'
Remove-XcpcWinGetMirror $two
Write-Output ('__RESULT__'+(@{Mode=(Get-XcpcMirrorMode);Names=@($one.Name,$two.Name);Calls=$script:calls}|ConvertTo-Json -Compress -Depth 10))
''')
        self.assertEqual(data['Mode'],'china')
        self.assertNotEqual(*data['Names'])
        for name in data['Names']:
            self.assertRegex(name,r'^xcpc-mirror-[0-9a-f]{32}$')
        for index,name in ((0,data['Names'][0]),(2,data['Names'][1])):
            self.assertEqual(data['Calls'][index],['source','add','--name',name,'--arg',
                'https://mirrors.ustc.edu.cn/winget-source','--type','Microsoft.PreIndexed.Package',
                '--accept-source-agreements','--disable-interactivity'])
            self.assertEqual(data['Calls'][index+1],['source','remove','--name',name,'--disable-interactivity'])

    def test_winget_mirror_unavailable_is_cleaned_and_official_or_nonadmin_never_adds_sources(self):
        data=self.run_powershell(r'''
$script:calls=@();$script:admin=$true
function Test-XcpcAdministrator { return $script:admin }
function Invoke-XcpcNative {param($FilePath,$ArgumentList,[switch]$Quiet);$script:calls+=,@($ArgumentList);if($ArgumentList[1] -eq 'add'){return 5};return 0}
$env:XCPC_MIRROR='china';$failed=New-XcpcWinGetMirror 'winget-fixture'
$env:XCPC_MIRROR='official';$official=New-XcpcWinGetMirror 'winget-fixture'
$env:XCPC_MIRROR='china';$script:admin=$false;$private=New-XcpcWinGetMirror 'winget-fixture'
Write-Output ('__RESULT__'+(@{Failed=($null -eq $failed);Official=($null -eq $official);Private=($null -eq $private);Calls=$script:calls}|ConvertTo-Json -Compress -Depth 10))
''')
        self.assertTrue(data['Failed'] and data['Official'] and data['Private'])
        self.assertEqual([call[:2] for call in data['Calls']],[['source','add'],['source','remove']])
        self.assertEqual(data['Calls'][0][3],data['Calls'][1][3])

    def test_winget_package_fallback_cleans_its_source_even_when_both_downloads_fail(self):
        for fallback_status in (0,9):
            with self.subTest(fallback_status=fallback_status):
                data=self.run_powershell(r'''
$env:XCPC_MIRROR='china';$script:installed=$false;$script:calls=@()
function Test-XcpcAdministrator {return $true}
function Set-XcpcGlobalPermissions {param($Root,$Bin)}
function Initialize-XcpcUserBridge {param($Root)}
function Add-XcpcCommandPath {param($Bin,[switch]$SystemWide)}
function Update-XcpcProcessPath {}
function Ensure-XcpcWinGet {return 'winget-fixture'}
function Get-XcpcPrerequisites {
 return [pscustomobject]@{Python=[pscustomobject]@{Executable='python-fixture';Prefix=@()};Git=$script:installed;Terminal=$true}
}
function Invoke-XcpcNative {
 param($FilePath,$ArgumentList,[switch]$Quiet)
 if($FilePath -eq 'python-fixture'){return 0}
 $script:calls+=,@($ArgumentList)
 if($ArgumentList[0] -eq 'source'){return 0}
 if($ArgumentList[5] -ne 'winget'){return 7}
 $script:installed=$true;return FALLBACK_STATUS
}
$status=Invoke-XcpcWindowsSetup -InstallerArgs @('--skip-browser-registration') 2>$null
Write-Output ('__RESULT__'+(@{Status=$status;Calls=$script:calls}|ConvertTo-Json -Compress -Depth 10))
'''.replace('FALLBACK_STATUS',str(fallback_status)))
                self.assertEqual(data['Status'],0 if fallback_status == 0 else 1)
                calls=data['Calls']
                self.assertEqual([c[0] for c in calls],['source','install','install','source'])
                self.assertEqual(calls[1][5],calls[0][3])
                self.assertEqual(calls[2][5],'winget')
                self.assertEqual(calls[-1],['source','remove','--name',calls[0][3],'--disable-interactivity'])

    def mirrored_python_scenario(self, condition='success', architecture='amd64'):
        config=json.dumps({'Condition':condition,'Architecture':architecture}).replace("'","''")
        return self.run_powershell("$config='"+config+"'|ConvertFrom-Json\n"+r'''
$env:XCPC_MIRROR='china';$script:downloads=@();$script:executed=@();$script:refreshes=0
function Test-XcpcAdministrator {return $true}
function Get-XcpcWindowsArchitecture {return $config.Architecture}
function Update-XcpcProcessPath {$script:refreshes++}
function Find-XcpcPython {param([switch]$SystemWide);if($config.Condition -eq 'probe'){return $null};return [pscustomobject]@{Executable='verified-machine-python'}}
function Invoke-WebRequest {param($Uri,$OutFile,[switch]$UseBasicParsing,$TimeoutSec,$ErrorAction);$script:downloads+=@{Uri=$Uri;File=$OutFile};[IO.File]::WriteAllText($OutFile,'inert fixture')}
function Get-FileHash {param($LiteralPath,$Algorithm);return [pscustomobject]@{Hash=$(if($config.Condition -eq 'hash'){'b'*64}else{'a'*64})}}
function Get-AuthenticodeSignature {
 param($LiteralPath)
 $subject=if($config.Condition -eq 'publisher'){'CN=Unrelated Software, O=Unrelated Software'}else{'CN=Python Software Foundation, O=Python Software Foundation, C=US'}
 return [pscustomobject]@{Status=$(if($config.Condition -eq 'signature'){'NotSigned'}else{'Valid'});SignerCertificate=[pscustomobject]@{Subject=$subject}}
}
function Start-Process {param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru);$script:executed+=@{File=$FilePath;Args=$ArgumentList};return [pscustomobject]@{ExitCode=$(if($config.Condition -eq 'installer'){11}else{0})}}
$key=$config.Architecture
$filename='python-3.13.16'+$(if($key -eq 'win32'){'.exe'}else{'-'+$key+'.exe'})
$artifact=@{url='https://vp.cwnupaa.com/downloads/prerequisites/'+$filename;officialUrl='https://www.python.org/ftp/python/3.13.16/'+$filename;sha256='a'*64}
if($config.Condition -eq 'url'){$artifact.url='https://untrusted.example/'+$filename}
if($config.Condition -eq 'officialUrl'){$artifact.officialUrl='https://untrusted.example/'+$filename}
$manifest=@{version='3.13.16';artifacts=@{$key=$artifact}}
if($config.Condition -eq 'version'){$manifest.version='3.13.16/../../bad'}
if($config.Condition -eq 'architecture'){$manifest.artifacts=@{}}
$manifestFile=Join-Path $env:LOCALAPPDATA 'windows-python.json'
$manifest|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $manifestFile -Encoding UTF8
$success=Install-XcpcMirroredPython -SystemWide -ManifestPath $manifestFile
$downloadRemains=@($script:downloads|Where-Object {Test-Path -LiteralPath $_.File}).Count
Write-Output ('__RESULT__'+(@{Success=$success;Downloads=$script:downloads;Executed=$script:executed;Refreshes=$script:refreshes;DownloadRemains=$downloadRemains}|ConvertTo-Json -Compress -Depth 10))
''')

    def test_verified_domestic_python_matches_architecture_installs_machine_wide_and_cleans_download(self):
        for architecture,filename in (('amd64','python-3.13.16-amd64.exe'),('arm64','python-3.13.16-arm64.exe'),('win32','python-3.13.16.exe')):
            with self.subTest(architecture=architecture):
                data=self.mirrored_python_scenario(architecture=architecture)
                self.assertTrue(data['Success'])
                self.assertEqual(data['Downloads'][0]['Uri'],'https://vp.cwnupaa.com/downloads/prerequisites/'+filename)
                self.assertEqual(data['Executed'][0]['Args'],['/quiet','InstallAllUsers=1','PrependPath=1',
                    'Include_launcher=1','InstallLauncherAllUsers=1','Include_pip=1','Include_test=0'])
                self.assertEqual(data['Refreshes'],1)
                self.assertEqual(data['DownloadRemains'],0)

    def test_untrusted_python_metadata_hash_or_publisher_never_executes_download(self):
        for condition in ('hash','signature','publisher','url','officialUrl','version','architecture'):
            with self.subTest(condition=condition):
                data=self.mirrored_python_scenario(condition)
                self.assertFalse(data['Success'])
                self.assertEqual(data['Executed'],[])
                self.assertEqual(data['DownloadRemains'],0)
                if condition in ('url','officialUrl','version','architecture'):
                    self.assertEqual(data['Downloads'],[])

    def test_failed_python_installer_or_probe_falls_back_and_cleans_download(self):
        for condition in ('installer','probe'):
            with self.subTest(condition=condition):
                data=self.mirrored_python_scenario(condition)
                self.assertFalse(data['Success'])
                self.assertEqual(len(data['Executed']),1)
                self.assertEqual(data['DownloadRemains'],0)

    def test_native_utf8_decoding_restores_original_encoding_in_legacy_and_standard_modes(self):
        for mode in ('Legacy','Standard'):
            with self.subTest(mode=mode):
                body=r'''
$PSNativeCommandArgumentPassing=ARGUMENT_MODE
$original=[Console]::OutputEncoding
[Console]::OutputEncoding=[Text.Encoding]::ASCII
$OutputEncoding=[Text.Encoding]::ASCII
$script:captured=@()
function Out-Host {process {$script:captured += [string]$_}}
$status=Invoke-XcpcNative FIXTURE_PYTHON @('-I','-c','import sys; sys.stdout.buffer.write(bytes([228,184,173,230,150,135,10]))')
$result=@{Status=$status;Characters=@($script:captured[0].ToCharArray()|ForEach-Object {[int]$_});Console=[Console]::OutputEncoding.CodePage;Output=$OutputEncoding.CodePage}
[Console]::OutputEncoding=$original
Write-Output ('__RESULT__'+($result|ConvertTo-Json -Compress))
'''
                for key,value in (('ARGUMENT_MODE',mode),('FIXTURE_PYTHON',sys.executable)):
                    body=body.replace(key,"'"+value.replace("'","''")+"'")
                data=self.run_powershell(body)
                self.assertEqual(data,{'Status':0,'Characters':[20013,25991],'Console':20127,'Output':20127})
