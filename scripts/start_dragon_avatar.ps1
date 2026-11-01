#requires -Version 5.1
[CmdletBinding()]
param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$avatarRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$avatarPython = Join-Path $avatarRoot '.venv\Scripts\python.exe'
$avatarScript = Join-Path $avatarRoot 'scripts\dragon_avatar_server.py'
$avatarWork = Join-Path $avatarRoot 'artifacts\dragon-maid-live2d'
$avatarState = Join-Path $avatarWork 'server-process.json'
if ($Stop) {
    if (-not (Test-Path -LiteralPath $avatarState)) { Write-Output 'Avatar has no recorded process'; exit 0 }
    $record = Get-Content -LiteralPath $avatarState -Raw | ConvertFrom-Json
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
    if ($proc) {
        if ($proc.ExecutablePath -ne $record.exe -or $proc.CreationDate.ToUniversalTime().ToString('o') -ne $record.created -or $proc.CommandLine -notlike "*$avatarScript*") { throw 'Recorded process identity changed; refusing to stop' }
        Stop-Process -Id $proc.ProcessId
    }
    Remove-Item -LiteralPath $avatarState
    Write-Output 'Avatar stopped'; exit 0
}
$listener = Get-NetTCPConnection -LocalPort 6187 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $status = Invoke-RestMethod 'http://127.0.0.1:6187/api/status' -TimeoutSec 3
    if ($status.service -ne 'dragon-maid-2d-v1') { throw 'Port 6187 is used by a different application' }
    Write-Output 'Avatar is already running: http://127.0.0.1:6187/'; exit 0
}
$proc = Start-Process -FilePath $avatarPython -ArgumentList @('"' + $avatarScript + '"') -WorkingDirectory $avatarRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $avatarWork 'server.stdout.log') -RedirectStandardError (Join-Path $avatarWork 'server.stderr.log') -PassThru
$identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)"
@{pid=$proc.Id; exe=$identity.ExecutablePath; created=$identity.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $avatarState -Encoding UTF8
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $status=Invoke-RestMethod 'http://127.0.0.1:6187/api/status' -TimeoutSec 2; if ($status.service -eq 'dragon-maid-2d-v1') { Write-Output 'Avatar ready: http://127.0.0.1:6187/'; Write-Output 'OBS browser source: http://127.0.0.1:6187/avatar'; exit 0 } } catch {}
    if ($proc.HasExited) { throw 'Avatar process exited; inspect artifacts/dragon-maid-live2d/server.stderr.log' }
}
throw 'Avatar startup timed out; inspect artifacts/dragon-maid-live2d/server.stderr.log'
