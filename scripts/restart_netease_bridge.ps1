#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedScript = [IO.Path]::GetFullPath((Join-Path $projectRoot 'integrations\netease\bridge.cjs'))
$pattern = '(?i)(?:^|\s)"?' + [regex]::Escape($expectedScript) + '"?(?:\s|$)'
$listeners = @(Get-NetTCPConnection -LocalPort 3010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($bridgeProcessId in $listeners) {
    $owned = Get-CimInstance Win32_Process -Filter "ProcessId = $bridgeProcessId"
    if (-not $owned -or $owned.Name -ine 'node.exe' -or $owned.CommandLine -notmatch $pattern) {
        throw '3010 is not this workspace NetEase bridge; refusing to stop it.'
    }
    $started = (Get-Process -Id $bridgeProcessId).StartTime
    $verified = Get-CimInstance Win32_Process -Filter "ProcessId = $bridgeProcessId"
    if ($verified.CommandLine -cne $owned.CommandLine -or (Get-Process -Id $bridgeProcessId).StartTime -ne $started) {
        throw 'Bridge process identity changed; refusing to stop it.'
    }
    Stop-Process -Id $bridgeProcessId -Force
}
& (Join-Path $PSScriptRoot 'start_netease_member.ps1') -Port 3010
