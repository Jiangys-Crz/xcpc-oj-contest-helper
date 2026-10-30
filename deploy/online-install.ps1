#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

function Join-XcpcOnlinePath {
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

function Get-XcpcOnlineEnvironmentPath {
    param([string]$Scope)
    return [Environment]::GetEnvironmentVariable('Path', $Scope)
}

function Set-XcpcOnlineMachinePath {
    param([string]$Value)
    # .NET also broadcasts the environment change to Windows applications.
    [Environment]::SetEnvironmentVariable('Path', $Value, 'Machine')
}

function Confirm-XcpcOnlineCommand {
    param([string]$Command)
    & $Command --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installed XCPC could not start: $Command" }
}

function Initialize-XcpcOnlineUserBridge {
    param([string]$Bin)
    try {
        $root = Join-Path (Split-Path -Parent $Bin) 'app'
        $pointer = Get-Content -Raw -LiteralPath (Join-Path $root 'current.json') -Encoding UTF8 | ConvertFrom-Json
        $release = [IO.Path]::GetFullPath([string]$pointer.release)
        $releases = [IO.Path]::GetFullPath((Join-Path $root 'releases')).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
        if (-not $release.StartsWith($releases,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid shared release pointer.' }
        & (Join-Path $release 'venv\Scripts\python.exe') -I -m vp_helper.chromium_login | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Browser registration did not complete.' }
    } catch {
        Write-Warning "XCPC is installed; browser setup for this Windows user will retry on first use: $($_.Exception.Message)"
    }
}

function Set-XcpcOnlineCommandPath {
    param([string]$Bin)
    $command = Join-Path $Bin 'xcpc.exe'
    if (-not (Test-Path -LiteralPath $command -PathType Leaf)) { throw "XCPC command was not installed: $command" }
    $machinePath = Get-XcpcOnlineEnvironmentPath 'Machine'
    $updated = Join-XcpcOnlinePath @($Bin, $machinePath)
    if ($updated -cne $machinePath) { Set-XcpcOnlineMachinePath $updated }
    # The native installer runs in a child PowerShell. Refresh this IEX session
    # too; retain process-only entries such as an activated Conda environment.
    $env:Path = [Environment]::ExpandEnvironmentVariables((Join-XcpcOnlinePath @(
        $Bin, $env:Path, $updated, (Get-XcpcOnlineEnvironmentPath 'User'))))
    Confirm-XcpcOnlineCommand $command
    Write-Host "Command directory: $Bin"
}

function Invoke-XcpcOnlineSetup {
    if ($env:OS -ne 'Windows_NT') { throw 'Use install.sh on Ubuntu or macOS.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $xcpcProgramFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } elseif ($env:ProgramFiles) { $env:ProgramFiles } else { [Environment]::GetFolderPath('ProgramFiles') }
    if (-not $xcpcProgramFiles) { throw 'Could not locate the Windows Program Files directory.' }
    $xcpcGlobalBin = Join-Path $xcpcProgramFiles 'XCPC\bin'
    $xcpcGlobalCommand = Join-Path $xcpcGlobalBin 'xcpc.exe'
    $xcpcMachineEntries = @( (Get-XcpcOnlineEnvironmentPath 'Machine') -split ';' | ForEach-Object { $_.Trim().Trim('"').TrimEnd('\','/') } )
    # Legacy per-user installs are preserved. The standard online installer now
    # installs the shared program instead of silently upgrading the private copy.
    if ((Test-Path -LiteralPath $xcpcGlobalCommand -PathType Leaf) -and $xcpcMachineEntries -contains $xcpcGlobalBin.TrimEnd('\','/')) {
        & $xcpcGlobalCommand update
        if ($LASTEXITCODE -eq 0) {
            Set-XcpcOnlineCommandPath $xcpcGlobalBin
            Initialize-XcpcOnlineUserBridge $xcpcGlobalBin
            Write-Host 'Installation complete. Run: xcpc login'
            return
        }
        Write-Host 'Preparing the standard installer to repair the shared command.'
    }
    $xcpcTemporary = Join-Path ([IO.Path]::GetTempPath()) ('xcpc-online-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $xcpcTemporary | Out-Null
    $xcpcPreviousRevision = $env:XCPC_SOURCE_REVISION
    try {
        $xcpcBase = 'https://vp.cwnupaa.com'
        $xcpcManifest = (Invoke-RestMethod -Uri ($xcpcBase + '/api/client/releases/latest') -TimeoutSec 30).data
        if ($xcpcManifest.revision -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' -or $xcpcManifest.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid release manifest.' }
        $xcpcArchive = Join-Path $xcpcTemporary 'source.tar.gz'
        Invoke-WebRequest -UseBasicParsing -Uri ($xcpcBase + '/downloads/terminal/' + $xcpcManifest.revision + '.tar.gz') -OutFile $xcpcArchive -TimeoutSec 600
        if ((Get-FileHash -LiteralPath $xcpcArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $xcpcManifest.sha256) { throw 'Release SHA256 mismatch; nothing was installed.' }
        $xcpcTar = (Get-Command tar.exe -CommandType Application -ErrorAction Stop).Source
        $xcpcNames = @(& $xcpcTar -tzf $xcpcArchive)
        if ($LASTEXITCODE -ne 0) { throw 'Invalid release archive.' }
        foreach ($xcpcName in $xcpcNames) {
            if ($xcpcName -match '(^/|:|(^|/)\.\.(/|$)|\\)') { throw 'Unsafe source path.' }
        }
        $xcpcEntries = @(& $xcpcTar -tvzf $xcpcArchive)
        if ($LASTEXITCODE -ne 0) { throw 'Invalid release archive.' }
        foreach ($xcpcEntry in $xcpcEntries) {
            if ($xcpcEntry -and $xcpcEntry.Substring(0,1) -notin @('-','d')) { throw 'Source archive links are not allowed.' }
        }
        $xcpcSource = Join-Path $xcpcTemporary 'source'
        New-Item -ItemType Directory -Path $xcpcSource | Out-Null
        & $xcpcTar -xzf $xcpcArchive -C $xcpcSource
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the release.' }
        $xcpcInstaller = Join-Path $xcpcSource 'VP\install.ps1'
        if (-not (Test-Path -LiteralPath $xcpcInstaller -PathType Leaf)) { throw 'Incomplete source archive.' }
        $env:XCPC_SOURCE_REVISION = $xcpcManifest.revision
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $xcpcInstaller
        if ($LASTEXITCODE -ne 0) { throw "Installation failed (exit $LASTEXITCODE)." }
        Set-XcpcOnlineCommandPath $xcpcGlobalBin
        Initialize-XcpcOnlineUserBridge $xcpcGlobalBin
        Write-Host 'Installation complete. Run: xcpc login'
    } finally {
        $env:XCPC_SOURCE_REVISION = $xcpcPreviousRevision
        Remove-Item -LiteralPath $xcpcTemporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Dot-sourcing exposes helpers for isolated tests without starting an install.
if ($MyInvocation.InvocationName -ne '.') { Invoke-XcpcOnlineSetup }
