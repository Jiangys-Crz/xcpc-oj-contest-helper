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
    $probeCode = 'import sys; sys.exit(1) if sys.version_info < (3, 12) else None; import venv, ensurepip; ensurepip.version()'
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
    param([string]$Name, [switch]$ProbeVersion)
    foreach ($path in @(Get-XcpcApplicationPaths $Name)) {
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
    return [pscustomobject]@{
        Python = Find-XcpcPython
        Git = Find-XcpcTool 'git.exe' -ProbeVersion
        Terminal = Find-XcpcTool 'wt.exe'
    }
}

function Get-XcpcCommandBin {
    param([string[]]$InstallerArgs)
    $bin = Join-Path $env:LOCALAPPDATA 'XCPC\bin'
    for ($index = 0; $index -lt $InstallerArgs.Count; $index++) {
        if ($InstallerArgs[$index] -eq '--target-bin') {
            if ($index + 1 -ge $InstallerArgs.Count -or $InstallerArgs[$index + 1].StartsWith('--')) {
                throw '--target-bin requires a directory, for example --target-bin "C:\XCPC commands".'
            }
            $index++
            $bin = $InstallerArgs[$index]
        } elseif ($InstallerArgs[$index].StartsWith('--target-bin=')) {
            $bin = $InstallerArgs[$index].Substring('--target-bin='.Length)
        }
    }
    if ([string]::IsNullOrWhiteSpace($bin)) { throw '--target-bin requires a nonempty directory.' }
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($bin)
}

function Add-XcpcCommandPath {
    param([string]$Bin)
    $userPath = Get-XcpcEnvironmentPath 'User'
    $updated = Join-XcpcPath @($Bin, $userPath)
    if ($updated -cne $userPath) { Set-XcpcUserPath $updated }
    $env:Path = Join-XcpcPath @($Bin, $env:Path)
}

function Invoke-XcpcWindowsSetup {
    param([string[]]$InstallerArgs = @())
    if ($InstallerArgs -contains '--help' -or $InstallerArgs -contains '-h') {
        Write-Host 'Usage: .\install.ps1 [--target-root DIRECTORY] [--target-bin DIRECTORY] [--offline]'
        Write-Host 'Installs missing Python 3.13, Git, and Windows Terminal, then installs XCPC for this user.'
        Write-Host '  --offline  Use bundled wheels; fail if prerequisites are missing, without downloading them.'
        Write-Host '  --help     Show this help without installing or downloading anything.'
        return 0
    }
    try {
        if ($env:OS -ne 'Windows_NT') { throw 'This setup script requires native Windows. On macOS use bash install-macos.sh; on Ubuntu use bash install.sh.' }
        $bin = Get-XcpcCommandBin $InstallerArgs
        Update-XcpcProcessPath
        $prerequisites = Get-XcpcPrerequisites
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
                $status = Invoke-XcpcNative $winget $wingetArgs
                if ($status -ne 0) {
                    throw "WinGet could not install $($package.Name) (exit $status). Resolve the installer message above, then rerun .\install.ps1."
                }
                Update-XcpcProcessPath
            }
            $prerequisites = Get-XcpcPrerequisites
            $missing = @($packages | Where-Object { -not $prerequisites.($_.Key) })
            if ($missing.Count) {
                $names = ($missing | ForEach-Object { $_.Name }) -join ', '
                throw "Installed prerequisites are still unavailable: $names. Open a new PowerShell window and rerun .\install.ps1; check that their commands are on PATH."
            }
        }
        $python = $prerequisites.Python
        $pythonArgs = @($python.Prefix) + @((Join-Path $PSScriptRoot 'install.py')) + $InstallerArgs
        $status = Invoke-XcpcNative $python.Executable $pythonArgs
        if ($status -ne 0) { return $status }
        if ($InstallerArgs -notcontains '--skip-path-configuration') { Add-XcpcCommandPath $bin }
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
