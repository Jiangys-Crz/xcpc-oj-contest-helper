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
POWERSHELL = (os.environ.get('XCPC_TEST_POWERSHELL') or shutil.which('pwsh')
              or shutil.which('powershell'))


@unittest.skipUnless(POWERSHELL, 'PowerShell required (pwsh, powershell, or XCPC_TEST_POWERSHELL)')
class WindowsSetupTests(unittest.TestCase):
    def run_powershell(self, body):
        with tempfile.TemporaryDirectory(prefix='xcpc windows tests ') as temporary:
            script = Path(temporary) / 'scenario.ps1'
            script.write_text(". $env:XCPC_SETUP_SCRIPT\n" + body, encoding='utf-8')
            env = os.environ | {'XCPC_SETUP_SCRIPT': str(SETUP), 'LOCALAPPDATA': temporary,
                                'OS': 'Windows_NT'}
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
        if ($id -eq 'Microsoft.WindowsTerminal') { $script:present += 'Terminal' }
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
            ([], ['Python.Python.3.13', 'Git.Git', 'Microsoft.WindowsTerminal']),
            (['Python', 'Terminal'], ['Git.Git']),
            (['Python', 'Git', 'Terminal'], []),
        ):
            with self.subTest(present=present):
                data = self.run_setup_scenario(present)
                self.assertEqual(data['Status'], 0)
                self.assertEqual([args[3] for args in data['Installs']], expected)
                for args in data['Installs']:
                    self.assertEqual(args[:3], ['install', '--exact', '--id'])
                    self.assertEqual(args[4:6], ['--source', 'winget'])
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
