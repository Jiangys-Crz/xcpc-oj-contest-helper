$ErrorActionPreference = 'Stop'
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    $Use313 = $false
    try {
        & py -3.13 -c 'import sys; assert sys.version_info >= (3,12)' 2>$null
        $Use313 = $LASTEXITCODE -eq 0
    } catch { $Use313 = $false }
    if ($Use313) { & py -3.13 "$PSScriptRoot\install.py" @args }
    else { & py -3 "$PSScriptRoot\install.py" @args }
} else {
    & python "$PSScriptRoot\install.py" @args
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Bin = Join-Path $env:LOCALAPPDATA 'XCPC\bin'
$env:Path = "$Bin;$env:Path"
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($UserPath -split ';') -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable('Path', "$Bin;$UserPath", 'User')
}
Write-Host 'Installation complete. Open a new terminal, then run xcpc --help.'
