#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use install.sh on Ubuntu or macOS.' }
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if (Get-Command xcpc -CommandType Application -ErrorAction SilentlyContinue) {
    & xcpc update
    if ($LASTEXITCODE -eq 0) { return }
    Write-Host 'Preparing the standard installer to repair the existing command.'
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
    Write-Host 'Installation complete. Open a new terminal and run: xcpc login'
} finally {
    $env:XCPC_SOURCE_REVISION = $xcpcPreviousRevision
    Remove-Item -LiteralPath $xcpcTemporary -Recurse -Force -ErrorAction SilentlyContinue
}
