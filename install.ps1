#Requires -Version 5.1
# Native Windows setup; compatible with Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'

function Invoke-XcpcNative {
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$Quiet)
    $global:LASTEXITCODE = 0
    if ($Quiet) { & $FilePath @ArgumentList *> $null }
    else { & $FilePath @ArgumentList | Out-Host }
    return [int]$LASTEXITCODE
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

function Find-XcpcPython {
    param([switch]$SystemWide)
    $probeCode = 'import sys; sys.exit(1) if sys.version_info < (3, 12) else None; import venv, ensurepip; ensurepip.version()'
    if ($SystemWide) {
        $probeCode += '; import os; from pathlib import Path; prefixes=[os.environ.get(k) for k in ("ProgramW6432","ProgramFiles","ProgramFiles(x86)")]; sys.exit(0 if any(p and Path(sys.base_prefix).resolve().is_relative_to(Path(p).resolve()) for p in prefixes) else 1)'
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

function Invoke-XcpcElevatedSetup {
    param([string[]]$InstallerArgs)
    $scriptPath = Join-Path $PSScriptRoot 'install.ps1'
    $quoted = @($scriptPath) + $InstallerArgs | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }
    $command = '$env:XCPC_SETUP_ELEVATED="1"; & ' + ($quoted -join ' ')
    if ($env:XCPC_SOURCE_REVISION -match '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        $command = '$env:XCPC_SOURCE_REVISION=' + "'" + $env:XCPC_SOURCE_REVISION + "'; " + $command
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Write-Host 'XCPC needs administrator permission to install commands for all Windows users.'
    $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Wait -PassThru
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
    try {
        if ($env:OS -ne 'Windows_NT') { throw 'This setup script requires native Windows. On macOS use bash install-macos.sh; on Ubuntu use bash install.sh.' }
        $layout = Get-XcpcLayout $InstallerArgs
        $bin = $layout.Bin
        if ($layout.Global -and -not (Test-XcpcAdministrator)) {
            $status = Invoke-XcpcElevatedSetup $InstallerArgs
            if ($status -eq 0) {
                Update-XcpcProcessPath
                $env:Path = Join-XcpcPath @($bin, $env:Path)
                if ($InstallerArgs -notcontains '--skip-browser-registration') { Initialize-XcpcUserBridge $layout.Root }
            }
            return $status
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
            $winget = Ensure-XcpcWinGet
            foreach ($package in $missing) {
                Write-Host "Installing $($package.Name)..."
                $wingetArgs = @('install', '--exact', '--id', $package.Id, '--source', 'winget',
                    '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
                if ($layout.Global -and $package.Key -in @('Python','Git')) { $wingetArgs += @('--scope', 'machine') }
                $status = Invoke-XcpcNative $winget $wingetArgs
                if ($status -ne 0) {
                    throw "WinGet could not install $($package.Name) (exit $status). Resolve the installer message above, then rerun .\install.ps1."
                }
                Update-XcpcProcessPath
            }
            $prerequisites = Get-XcpcPrerequisites -SystemWide:$layout.Global
            $missing = @($packages | Where-Object { -not $prerequisites.($_.Key) })
            if ($missing.Count) {
                $names = ($missing | ForEach-Object { $_.Name }) -join ', '
                throw "Installed prerequisites are still unavailable: $names. Open a new PowerShell window and rerun .\install.ps1; check that their commands are on PATH."
            }
        }
        $python = $prerequisites.Python
        $pythonArgs = @($python.Prefix) + @((Join-Path $PSScriptRoot 'install.py')) + $InstallerArgs
        if (-not @($InstallerArgs | Where-Object { $_ -match '^--target-root(?:=|$)' }).Count) { $pythonArgs += @('--target-root', $layout.Root) }
        if (-not @($InstallerArgs | Where-Object { $_ -match '^--target-bin(?:=|$)' }).Count) { $pythonArgs += @('--target-bin', $layout.Bin) }
        $status = Invoke-XcpcNative $python.Executable $pythonArgs
        if ($status -ne 0) { return $status }
        if ($layout.Global) { Set-XcpcGlobalPermissions $layout.Root $layout.Bin }
        if ($InstallerArgs -notcontains '--skip-path-configuration') { Add-XcpcCommandPath $bin -SystemWide:$layout.Global }
        if ($layout.Global -and $env:XCPC_SETUP_ELEVATED -ne '1' -and $InstallerArgs -notcontains '--skip-browser-registration') { Initialize-XcpcUserBridge $layout.Root }
        Write-Host 'Installation complete. Open a new terminal, then run xcpc --help.'
        return 0
    } catch {
        Write-Error "Setup failed: $($_.Exception.Message)" -ErrorAction Continue
        return 1
    }
}

# Dot-sourcing exposes helpers for isolated tests without starting an install.
if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-XcpcWindowsSetup -InstallerArgs $args)
}
