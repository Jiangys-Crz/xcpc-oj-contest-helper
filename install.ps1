#Requires -Version 5.1
# Native Windows setup; compatible with Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'

function Invoke-XcpcNative {
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$Quiet)
    $previousConsoleEncoding = [Console]::OutputEncoding
    $previousOutputEncoding = $OutputEncoding
    try {
        # WinGet emits UTF-8. Legacy PowerShell otherwise decodes its Chinese
        # progress/error messages using the previous console code page.
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
        $global:LASTEXITCODE = 0
        if ($Quiet) { & $FilePath @ArgumentList *> $null }
        else { & $FilePath @ArgumentList | Out-Host }
        $status = [int]$LASTEXITCODE
    } finally {
        [Console]::OutputEncoding = $previousConsoleEncoding
        $OutputEncoding = $previousOutputEncoding
    }
    return $status
}

function Get-XcpcApplicationPaths {
    param([string]$Name, [switch]$SkipWindowsApps)
    foreach ($command in @(Get-Command $Name -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $path = $command.Source
        # Do not run Python's Microsoft Store aliases: they may open the Store.
        if ($SkipWindowsApps -and $path -match '(?i)[\\/]Microsoft[\\/]WindowsApps[\\/]') { continue }
        if ($path) { $path }
    }
}

function Get-XcpcMachinePythonPaths {
    # PEP 514 registrations are visible immediately after setup, before PATH
    # reaches this process. Inspect both views even from a 32-bit PowerShell.
    # HKCU is deliberately excluded: the shared app cannot depend on Conda or
    # a Python installation that belongs to one Windows user.
    $found = @()
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $registry = $null; $pythonKey = $null
        try {
            $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
            $pythonKey = $registry.OpenSubKey('SOFTWARE\Python')
            if (-not $pythonKey) { continue }
            foreach ($company in $pythonKey.GetSubKeyNames()) {
                $companyKey = $null
                try {
                    $companyKey = $pythonKey.OpenSubKey($company)
                    foreach ($tag in $companyKey.GetSubKeyNames()) {
                        $install = $null
                        try {
                            $install = $companyKey.OpenSubKey($tag + '\InstallPath')
                            if (-not $install) { continue }
                            $directory = [Environment]::ExpandEnvironmentVariables([string]$install.GetValue('')).Trim().Trim('"')
                            $executable = [Environment]::ExpandEnvironmentVariables([string]$install.GetValue('ExecutablePath')).Trim().Trim('"')
                            if (-not $executable -and $directory) { $executable = Join-Path $directory 'python.exe' }
                            if (-not $directory -and $executable) { $directory = Split-Path -Parent $executable }
                            if ($directory -and $executable) {
                                $priority = if ($company -eq 'PythonCore' -and $tag -match '^3\.13(?:$|-)') { 0 } elseif ($company -eq 'PythonCore') { 1 } else { 2 }
                                $architecturePriority = if ($view -eq [Microsoft.Win32.RegistryView]::Registry64) { 0 } else { 1 }
                                $found += [pscustomobject]@{ Executable=$executable; Directory=$directory; Priority=$priority; ArchitecturePriority=$architecturePriority; Tag=$tag }
                            }
                        } catch { }
                        finally { if ($install) { $install.Dispose() } }
                    }
                } catch { }
                finally { if ($companyKey) { $companyKey.Dispose() } }
            }
        } catch { }
        finally { if ($pythonKey) { $pythonKey.Dispose() }; if ($registry) { $registry.Dispose() } }
    }
    $seen = @{}
    foreach ($candidate in @($found | Sort-Object Priority, ArchitecturePriority, @{Expression='Tag';Descending=$true})) {
        if (-not $seen.ContainsKey($candidate.Executable)) { $seen[$candidate.Executable]=$true; $candidate }
    }
}

function Find-XcpcPython {
    param([switch]$SystemWide)
    $probeCode = 'import sys; sys.exit(1) if sys.version_info < (3, 12) else None; import venv, ensurepip; ensurepip.version()'
    if ($SystemWide) {
        # Windows PowerShell 5.1's legacy native argument parser strips embedded
        # double quotes in a Python -c argument. Use Python single-quoted literals
        # so the exact same probe works there and in PowerShell 7.
        $probeCode += "; import os; from pathlib import Path; base=Path(sys.base_prefix).resolve(); prefixes=[os.environ.get(k) for k in ('ProgramW6432','ProgramFiles','ProgramFiles(x86)')]+sys.argv[1:]; profiles=[Path(p).resolve().parent for p in (os.environ.get('USERPROFILE'),os.environ.get('PUBLIC')) if p]; sys.exit(0 if not any(base.is_relative_to(p) for p in profiles) and any(p and base.is_relative_to(Path(p).resolve()) for p in prefixes) else 1)"
        foreach ($registered in @(Get-XcpcMachinePythonPaths)) {
            if (Test-Path -LiteralPath $registered.Executable -PathType Leaf) {
                try {
                    if ((Invoke-XcpcNative $registered.Executable @('-I','-c',$probeCode,$registered.Directory.TrimEnd('\','/')) -Quiet) -eq 0) {
                        return [pscustomobject]@{ Executable=$registered.Executable; Prefix=@() }
                    }
                } catch { }
            }
        }
        $prefixes = @($env:ProgramW6432, $env:ProgramFiles) | Where-Object { $_ } | Select-Object -Unique
        foreach ($prefix in $prefixes) {
            foreach ($directory in @(Get-ChildItem -LiteralPath $prefix -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
                $candidate = Join-Path $directory.FullName 'python.exe'
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    try { if ((Invoke-XcpcNative $candidate @('-I','-c',$probeCode) -Quiet) -eq 0) { return [pscustomobject]@{ Executable=$candidate; Prefix=@() } } } catch { }
                }
            }
        }
    }
    $launchers = @(Get-XcpcApplicationPaths 'py.exe' -SkipWindowsApps)
    foreach ($selector in @('-3.13', '-3')) {
        foreach ($path in $launchers) {
            try {
                $probe = @($selector, '-I', '-c', $probeCode)
                if ((Invoke-XcpcNative $path $probe -Quiet) -eq 0) {
                    return [pscustomobject]@{ Executable = $path; Prefix = @($selector) }
                }
            } catch { }
        }
    }
    foreach ($path in @(Get-XcpcApplicationPaths 'python.exe' -SkipWindowsApps)) {
        try {
            $probe = @('-I', '-c', $probeCode)
            if ((Invoke-XcpcNative $path $probe -Quiet) -eq 0) {
                return [pscustomobject]@{ Executable = $path; Prefix = @() }
            }
        } catch { }
    }
    return $null
}

function Find-XcpcTool {
    param([string]$Name, [switch]$ProbeVersion, [switch]$SystemWide)
    $paths = @()
    if ($SystemWide -and $Name -eq 'git.exe') {
        foreach ($prefix in @($env:ProgramW6432, $env:ProgramFiles) | Where-Object { $_ } | Select-Object -Unique) {
            $candidate = Join-Path $prefix 'Git\cmd\git.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $paths += $candidate }
        }
    }
    $paths += @(Get-XcpcApplicationPaths $Name)
    foreach ($path in $paths) {
        if ($SystemWide) {
            $shared = $false
            foreach ($prefix in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }) {
                if ($path.StartsWith($prefix.TrimEnd('\','/') + '\', [StringComparison]::OrdinalIgnoreCase)) { $shared = $true }
            }
            if (-not $shared) { continue }
        }
        if (-not $ProbeVersion) { return $path }
        try {
            if ((Invoke-XcpcNative $path @('--version') -Quiet) -eq 0) { return $path }
        } catch { }
    }
    return $null
}

function Get-XcpcEnvironmentPath {
    param([string]$Scope)
    return [Environment]::GetEnvironmentVariable('Path', $Scope)
}

function Set-XcpcUserPath {
    param([string]$Value)
    [Environment]::SetEnvironmentVariable('Path', $Value, 'User')
}

function Set-XcpcMachinePath {
    param([string]$Value)
    # .NET sends WM_SETTINGCHANGE so Explorer and new terminals see the change.
    [Environment]::SetEnvironmentVariable('Path', $Value, 'Machine')
}

function Test-XcpcAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function New-XcpcSetupLog {
    # Keep logs outside the downloaded source, which the online installer removes.
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { [IO.Path]::GetTempPath() }
    $directory = Join-Path $base ('XCPC\Logs\install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    return (Join-Path $directory 'setup.log')
}

function Write-XcpcSetupDiagnostics {
    param([string]$LogPath)
    if (-not $LogPath) { return }
    Write-Host "Installation log: $LogPath"
    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        Write-Host 'Last installer output:'
        Get-Content -LiteralPath $LogPath -Encoding UTF8 -Tail 40 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    } else {
        Write-Host 'No installer log was created; Windows may have rejected administrator authorization before setup started.'
    }
}

function Get-XcpcMirrorMode {
    if (-not $env:XCPC_MIRROR -or $env:XCPC_MIRROR -eq 'china') { return 'china' }
    if ($env:XCPC_MIRROR -eq 'official') { return 'official' }
    throw 'XCPC_MIRROR must be china or official.'
}

function Get-XcpcWindowsArchitecture {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    try { $architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() } catch { }
    switch -Regex ($architecture) { '^ARM64$' { return 'arm64' } '^(AMD64|X64)$' { return 'amd64' } '^(X86|I386)$' { return 'win32' } default { return '' } }
}

function Install-XcpcMirroredPython {
    param([switch]$SystemWide, [string]$ManifestPath = (Join-Path $PSScriptRoot 'runtimes\windows-python.json'))
    if ((Get-XcpcMirrorMode) -eq 'official' -or -not $SystemWide -or -not (Test-XcpcAdministrator)) { return $false }
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { return $false }
    $directory = $null
    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$manifest.version -notmatch '^3\.13\.\d+$') { throw 'Invalid bundled Python version.' }
        $key = Get-XcpcWindowsArchitecture
        if (-not $key) { return $false }
        $artifact = $manifest.artifacts.$key
        if (-not $artifact) { return $false }
        $filename = 'python-' + $manifest.version + $(if ($key -eq 'win32') { '.exe' } else { '-' + $key + '.exe' })
        if ([string]$artifact.url -cne ('https://vp.cwnupaa.com/downloads/prerequisites/' + $filename) -or
            [string]$artifact.officialUrl -cne ('https://www.python.org/ftp/python/' + $manifest.version + '/' + $filename) -or
            [string]$artifact.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid bundled Python artifact metadata.' }
        $directory = Join-Path ([IO.Path]::GetTempPath()) ('xcpc-python-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $directory | Out-Null
        $installer = Join-Path $directory $filename
        Write-Host ('Downloading verified Python ' + $manifest.version + ' from the XCPC China download server...')
        Invoke-WebRequest -Uri $artifact.url -OutFile $installer -UseBasicParsing -TimeoutSec 180 -ErrorAction Stop | Out-Null
        $digest = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        if ($digest -ine $artifact.sha256) { throw 'Python installer SHA256 mismatch; the file was not executed.' }
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch '(?i)(?:^|,\s*)(?:CN|O)=Python Software Foundation(?:,|$)') {
            throw 'Python installer publisher verification failed; the file was not executed.'
        }
        $process = Start-Process -FilePath $installer -ArgumentList @('/quiet','InstallAllUsers=1','PrependPath=1',
            'Include_launcher=1','InstallLauncherAllUsers=1','Include_pip=1','Include_test=0') -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw ('Python setup returned exit ' + $process.ExitCode) }
        Update-XcpcProcessPath
        if (-not (Find-XcpcPython -SystemWide)) { throw 'The installed machine Python could not be verified.' }
        return $true
    } catch {
        Write-Warning ('Python mirror setup unavailable: ' + $_.Exception.Message + ' Falling back to WinGet and the vendor installer.')
        return $false
    } finally {
        if ($directory -and (Test-Path -LiteralPath $directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
    }
}

function New-XcpcWinGetMirror {
    param([string]$Winget)
    if ((Get-XcpcMirrorMode) -eq 'official' -or -not (Test-XcpcAdministrator)) { return $null }
    # A private alias leaves the user's existing repositories untouched. Older
    # WinGet versions or policy restrictions simply keep the original source.
    $name = 'xcpc-mirror-' + [guid]::NewGuid().ToString('N')
    $source = [pscustomobject]@{ Executable=$Winget; Name=$name }
    $status = Invoke-XcpcNative $Winget @('source','add','--name',$name,'--arg','https://mirrors.ustc.edu.cn/winget-source',
        '--type','Microsoft.PreIndexed.Package','--accept-source-agreements','--disable-interactivity')
    if ($status -eq 0) {
        Write-Host 'Using the USTC WinGet index for this installation; package signatures and hashes remain checked.'
        return $source
    }
    # A failed add can leave an incomplete registration. The UUID alias belongs
    # only to this invocation, so removing it cannot remove a user's source.
    Remove-XcpcWinGetMirror $source
    Write-Host 'The temporary mirror index is unavailable; using the existing winget source.'
    return $null
}

function Remove-XcpcWinGetMirror {
    param($Source)
    if (-not $Source) { return }
    $status = Invoke-XcpcNative $Source.Executable @('source','remove','--name',$Source.Name,'--disable-interactivity')
    if ($status -ne 0) { Write-Warning "Could not remove the temporary WinGet source $($Source.Name)." }
}

function Invoke-XcpcElevatedSetup {
    param([string[]]$InstallerArgs)
    $scriptPath = Join-Path $PSScriptRoot 'install.ps1'
    $quoted = @($scriptPath) + $InstallerArgs | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }
    $command = '$env:XCPC_SETUP_ELEVATED="1"; & ' + ($quoted -join ' ')
    if ($env:XCPC_SOURCE_REVISION -match '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        $command = '$env:XCPC_SOURCE_REVISION=' + "'" + $env:XCPC_SOURCE_REVISION + "'; " + $command
    }
    if ($env:XCPC_SETUP_LOG) {
        # UAC may start a separate logon environment. Pass only this per-run
        # output path, with the same literal quoting as installer arguments.
        $command = '$env:XCPC_SETUP_LOG=' + "'" + $env:XCPC_SETUP_LOG.Replace("'", "''") + "'; " + $command
    }
    $command = '$env:XCPC_MIRROR=' + "'" + (Get-XcpcMirrorMode) + "'; " + $command
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Write-Host 'Continue in this PowerShell. Accept the Windows administrator prompt to install XCPC for all users; browser setup returns here afterwards.'
    try {
        $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Wait -PassThru
    } catch {
        throw 'Windows administrator authorization was cancelled or unavailable. Run this installer again and accept the UAC prompt; your ordinary PowerShell remains the correct starting point.'
    }
    return [int]$process.ExitCode
}

function Get-XcpcLayout {
    param([string[]]$InstallerArgs)
    $programFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } elseif ($env:ProgramFiles) { $env:ProgramFiles } else { [Environment]::GetFolderPath('ProgramFiles') }
    if (-not $programFiles) { throw 'Could not locate the Windows Program Files directory.' }
    $standard = Join-Path $programFiles 'XCPC'
    $custom = @($InstallerArgs | Where-Object { $_ -match '^--target-(?:root|bin)(?:=|$)' }).Count -gt 0
    $base = if ($custom) { Join-Path $env:LOCALAPPDATA 'XCPC' } else { $standard }
    $root = Join-Path $base 'app'; $bin = Join-Path $base 'bin'
    for ($index = 0; $index -lt $InstallerArgs.Count; $index++) {
        $argument = $InstallerArgs[$index]
        if ($argument -match '^--target-(root|bin)$') {
            $key = $Matches[1]
            if ($index + 1 -ge $InstallerArgs.Count -or $InstallerArgs[$index + 1].StartsWith('--')) { throw "$argument requires a directory." }
            $index++; $value = $InstallerArgs[$index]
            if ([string]::IsNullOrWhiteSpace($value)) { throw "$argument requires a nonempty directory." }
            if ($key -eq 'root') { $root = $value } else { $bin = $value }
        } elseif ($argument -match '^--target-(root|bin)=(.*)$') {
            $key = $Matches[1]; $value = $Matches[2]
            if ([string]::IsNullOrWhiteSpace($value)) { throw "--target-$key requires a nonempty directory." }
            if ($key -eq 'root') { $root = $value } else { $bin = $value }
        }
    }
    $root = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($root)
    $bin = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($bin)
    $standardRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath((Join-Path $standard 'app'))
    $standardBin = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath((Join-Path $standard 'bin'))
    return [pscustomobject]@{ Root=$root; Bin=$bin; Global=($root.TrimEnd('\','/') -ieq $standardRoot.TrimEnd('\','/') -or $bin.TrimEnd('\','/') -ieq $standardBin.TrimEnd('\','/')) }
}

function Set-XcpcGlobalPermissions {
    param([string]$Root, [string]$Bin)
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    foreach ($directory in @($Root, $Bin)) {
        # Built-in Users get read/execute, never write access to shared commands.
        if ((Invoke-XcpcNative $icacls @($directory, '/grant', '*S-1-5-32-545:(OI)(CI)RX', '/T', '/C') -Quiet) -ne 0) { throw "Could not grant all users read/execute access: $directory" }
    }
}

function Initialize-XcpcUserBridge {
    param([string]$Root)
    try {
        $pointer = Get-Content -Raw -LiteralPath (Join-Path $Root 'current.json') -Encoding UTF8 | ConvertFrom-Json
        $release = [IO.Path]::GetFullPath([string]$pointer.release)
        $releases = [IO.Path]::GetFullPath((Join-Path $Root 'releases')).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
        if (-not $release.StartsWith($releases,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid shared release pointer.' }
        $python = Join-Path $release 'venv\Scripts\python.exe'
        if ((Invoke-XcpcNative $python @('-I','-m','vp_helper.chromium_login')) -ne 0) { throw 'Browser registration did not complete.' }
    } catch {
        # Shared commands remain installed. Normal CLI use retries this user's
        # browser registration without copying another user's login data.
        Write-Warning "XCPC is installed; browser setup for this Windows user will retry on first use: $($_.Exception.Message)"
    }
}

function Join-XcpcPath {
    param([string[]]$Paths)
    $entries = New-Object 'System.Collections.Generic.List[string]'
    foreach ($path in $Paths) {
        foreach ($entry in ($path -split ';')) {
            $entry = $entry.Trim().Trim('"')
            if ($entry -and $entries -notcontains $entry) { $entries.Add($entry) }
        }
    }
    return ($entries -join ';')
}

function Update-XcpcProcessPath {
    # Keep process-only paths (for example an activated environment) as well as
    # paths just added to the registry by Python/Git's installers.
    $paths = @($env:Path, (Get-XcpcEnvironmentPath 'Machine'), (Get-XcpcEnvironmentPath 'User'))
    if ($env:LOCALAPPDATA) { $paths += (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps') }
    $env:Path = [Environment]::ExpandEnvironmentVariables((Join-XcpcPath $paths))
}

function Ensure-XcpcWinGet {
    $winget = Find-XcpcTool 'winget.exe' -ProbeVersion
    if ($winget) { return $winget }
    Write-Host 'Preparing Windows Package Manager (WinGet)...'
    try {
        # App Installer may already exist but not yet be registered for this user.
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop | Out-Null
    } catch { }
    Update-XcpcProcessPath
    $winget = Find-XcpcTool 'winget.exe' -ProbeVersion
    if ($winget) { return $winget }
    try {
        # Official per-user recovery, without changing execution policy or
        # requiring an administrator / an all-users App Installer deployment.
        # https://github.com/microsoft/winget-cli/blob/master/src/PowerShell/Help/Microsoft.WinGet.Client/Repair-WinGetPackageManager.md
        if (-not (Get-Module -ListAvailable -Name Microsoft.WinGet.Client)) {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $nuget = @(Get-PackageProvider -Name NuGet -ListAvailable -ErrorAction SilentlyContinue |
                Where-Object { $_.Version -ge [version]'2.8.5.201' })
            if (-not $nuget) {
                Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force -ErrorAction Stop | Out-Null
            }
            Install-Module -Name Microsoft.WinGet.Client -Repository PSGallery -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop | Out-Null
        }
        Import-Module Microsoft.WinGet.Client -ErrorAction Stop
        Repair-WinGetPackageManager -Latest -ErrorAction Stop | Out-Null
        Update-XcpcProcessPath
        $winget = Find-XcpcTool 'winget.exe' -ProbeVersion
        if ($winget) { return $winget }
        throw 'winget.exe is still unavailable after repair.'
    } catch {
        throw "Unable to prepare WinGet automatically: $($_.Exception.Message) Install or update App Installer from https://apps.microsoft.com/detail/9nblggh4nns1, then rerun this setup. Windows 10 1809 or newer is required."
    }
}

function Get-XcpcPrerequisites {
    param([switch]$SystemWide)
    return [pscustomobject]@{
        Python = Find-XcpcPython -SystemWide:$SystemWide
        Git = Find-XcpcTool 'git.exe' -ProbeVersion -SystemWide:$SystemWide
        Terminal = Find-XcpcTool 'wt.exe'
    }
}

function Get-XcpcCommandBin {
    param([string[]]$InstallerArgs)
    return (Get-XcpcLayout $InstallerArgs).Bin
}

function Add-XcpcCommandPath {
    param([string]$Bin, [switch]$SystemWide)
    $scope = if ($SystemWide) { 'Machine' } else { 'User' }
    $previous = Get-XcpcEnvironmentPath $scope
    $updated = Join-XcpcPath @($Bin, $previous)
    if ($updated -cne $previous) {
        if ($SystemWide) { Set-XcpcMachinePath $updated } else { Set-XcpcUserPath $updated }
    }
    $env:Path = Join-XcpcPath @($Bin, $env:Path)
}

function Invoke-XcpcWindowsSetup {
    param([string[]]$InstallerArgs = @())
    if ($InstallerArgs -contains '--help' -or $InstallerArgs -contains '-h') {
        Write-Host 'Usage: .\install.ps1 [--target-root DIRECTORY] [--target-bin DIRECTORY] [--offline]'
        Write-Host 'Installs missing Python 3.13, Git, and Windows Terminal. Default XCPC commands are installed for all Windows users.'
        Write-Host '  --offline  Use bundled wheels; fail if prerequisites are missing, without downloading them.'
        Write-Host '  --help     Show this help without installing or downloading anything.'
        return 0
    }
    $xcpcPreviousLog = $env:XCPC_SETUP_LOG
    $xcpcTranscript = $false
    $xcpcSetupStatus = 1
    $xcpcWinGetMirror = $null
    try {
        $null = Get-XcpcMirrorMode
        if (-not $env:XCPC_SETUP_LOG) {
            try { $env:XCPC_SETUP_LOG = New-XcpcSetupLog }
            catch { Write-Warning "Could not create an installation log: $($_.Exception.Message)" }
        }
        if ($env:OS -ne 'Windows_NT') { throw 'This setup script requires native Windows. On macOS use bash install-macos.sh; on Ubuntu use bash install.sh.' }
        $layout = Get-XcpcLayout $InstallerArgs
        $bin = $layout.Bin
        if ($layout.Global -and -not (Test-XcpcAdministrator)) {
            if ($env:XCPC_SETUP_ELEVATED -eq '1') { throw 'Windows did not grant administrator access to the installer. Rerun install.ps1 and accept the UAC prompt.' }
            $status = Invoke-XcpcElevatedSetup $InstallerArgs
            if ($status -eq 0) {
                Update-XcpcProcessPath
                $env:Path = Join-XcpcPath @($bin, $env:Path)
                if ($InstallerArgs -notcontains '--skip-browser-registration') { Initialize-XcpcUserBridge $layout.Root }
            }
            $xcpcSetupStatus = $status
            return $status
        }
        # Only the process doing the installation writes the transcript. The
        # ordinary UAC parent reads it after the child closes, avoiding two
        # PowerShell hosts trying to append to the same file concurrently.
        if ($env:XCPC_SETUP_LOG) {
            try { Start-Transcript -LiteralPath $env:XCPC_SETUP_LOG -Append -Force | Out-Null; $xcpcTranscript = $true }
            catch { Write-Warning "Could not start the installation log: $($_.Exception.Message)" }
        }
        if ($layout.Global -and $env:XCPC_SETUP_ELEVATED -ne '1') {
            Write-Host 'Administrator access is already available. Installing shared commands; account and browser settings remain private to the current Windows user.'
        }
        Update-XcpcProcessPath
        $prerequisites = Get-XcpcPrerequisites -SystemWide:$layout.Global
        $packages = @(
            @{ Key = 'Python'; Name = 'Python 3.12+'; Id = 'Python.Python.3.13' },
            @{ Key = 'Git'; Name = 'Git'; Id = 'Git.Git' },
            @{ Key = 'Terminal'; Name = 'Windows Terminal'; Id = 'Microsoft.WindowsTerminal' }
        )
        $missing = @($packages | Where-Object { -not $prerequisites.($_.Key) })
        if ($missing.Count -and $InstallerArgs -contains '--offline') {
            $names = ($missing | ForEach-Object { $_.Name }) -join ', '
            throw "Offline setup is missing: $names. Install these prerequisites first, or rerun .\install.ps1 without --offline while online. No prerequisites were downloaded."
        }
        if ($missing.Count) {
            if ($missing.Key -contains 'Python' -and (Install-XcpcMirroredPython -SystemWide:$layout.Global)) {
                $prerequisites = Get-XcpcPrerequisites -SystemWide:$layout.Global
                $missing = @($packages | Where-Object { -not $prerequisites.($_.Key) })
            }
        }
        if ($missing.Count) {
            $winget = Ensure-XcpcWinGet
            $xcpcWinGetMirror = New-XcpcWinGetMirror $winget
            foreach ($package in $missing) {
                Write-Host "Installing $($package.Name)..."
                $sourceName = if ($xcpcWinGetMirror) { $xcpcWinGetMirror.Name } else { 'winget' }
                $wingetArgs = @('install', '--exact', '--id', $package.Id, '--source', $sourceName,
                    '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
                if ($layout.Global -and $package.Key -eq 'Python') { $wingetArgs += '--force' }
                if ($layout.Global -and $package.Key -in @('Python','Git')) { $wingetArgs += @('--scope', 'machine') }
                $status = Invoke-XcpcNative $winget $wingetArgs
                if ($status -ne 0 -and $xcpcWinGetMirror) {
                    Write-Host "Mirror installation of $($package.Name) failed; retrying with the existing winget source."
                    $sourceIndex = [Array]::IndexOf($wingetArgs, '--source') + 1
                    $wingetArgs[$sourceIndex] = 'winget'
                    $status = Invoke-XcpcNative $winget $wingetArgs
                }
                if ($status -ne 0) {
                    throw "WinGet could not install $($package.Name) (exit $status). Resolve the installer message above, then rerun .\install.ps1."
                }
                Update-XcpcProcessPath
            }
            $prerequisites = Get-XcpcPrerequisites -SystemWide:$layout.Global
            $missing = @($packages | Where-Object { -not $prerequisites.($_.Key) })
            if ($missing.Count) {
                $names = ($missing | ForEach-Object { $_.Name }) -join ', '
                throw "Installed prerequisites are still unavailable: $names. The installer checked machine registrations and executable paths in this process. Python must be installed for all users, including venv and ensurepip; a per-user Conda/Python installation cannot back the shared XCPC commands. Review the package installer output above, then rerun install.ps1."
            }
        }
        $python = $prerequisites.Python
        $pythonArgs = @($python.Prefix) + @((Join-Path $PSScriptRoot 'install.py')) + $InstallerArgs
        if (-not @($InstallerArgs | Where-Object { $_ -match '^--target-root(?:=|$)' }).Count) { $pythonArgs += @('--target-root', $layout.Root) }
        if (-not @($InstallerArgs | Where-Object { $_ -match '^--target-bin(?:=|$)' }).Count) { $pythonArgs += @('--target-bin', $layout.Bin) }
        $status = Invoke-XcpcNative $python.Executable $pythonArgs
        if ($status -ne 0) { $xcpcSetupStatus = $status; return $status }
        if ($layout.Global) { Set-XcpcGlobalPermissions $layout.Root $layout.Bin }
        if ($InstallerArgs -notcontains '--skip-path-configuration') { Add-XcpcCommandPath $bin -SystemWide:$layout.Global }
        if ($layout.Global -and $env:XCPC_SETUP_ELEVATED -ne '1' -and $InstallerArgs -notcontains '--skip-browser-registration') { Initialize-XcpcUserBridge $layout.Root }
        Write-Host 'Installation complete. Open a new terminal, then run xcpc --help.'
        $xcpcSetupStatus = 0
        return 0
    } catch {
        Write-Error "Setup failed: $($_.Exception.Message)" -ErrorAction Continue
        return 1
    } finally {
        if ($xcpcWinGetMirror) { try { Remove-XcpcWinGetMirror $xcpcWinGetMirror } catch { Write-Warning 'Could not remove the temporary WinGet mirror source.' } }
        if ($xcpcTranscript) { try { Stop-Transcript | Out-Null } catch { } }
        if ($xcpcSetupStatus -ne 0) { Write-XcpcSetupDiagnostics $env:XCPC_SETUP_LOG }
        $env:XCPC_SETUP_LOG = $xcpcPreviousLog
    }
}

# Dot-sourcing exposes helpers for isolated tests without starting an install.
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-XcpcWindowsSetup -InstallerArgs $args)
}
