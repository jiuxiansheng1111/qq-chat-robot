[CmdletBinding()]
param([Parameter(Mandatory=$true)][int]$Port,
      [Parameter(Mandatory=$true)][string]$GptRoot,
      [switch]$InspectOnly)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$gptRootPath=[IO.Path]::GetFullPath($GptRoot)
$launcher=Join-Path $PSScriptRoot 'run_gpt_sovits.py'
$api=Join-Path $gptRootPath 'api_v2.py'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid voice port.' }
$listeners=@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @('127.0.0.1','::1') } |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($listeners.Count -eq 0) {
    @{stopped=$false;reason='not_running'} | ConvertTo-Json -Compress
    exit 0
}
if ($listeners.Count -ne 1) { throw 'Ambiguous voice listener; no process stopped.' }
$owned=Get-Process -Id $listeners[0]
try {
    $null=$owned.Handle
    $identity=Get-CimInstance Win32_Process -Filter ('ProcessId='+$owned.Id)
    $command=[string]$identity.CommandLine
    $launcherPattern='(?i)(?:^|\s)"?'+[regex]::Escape($launcher)+'"?(?:\s|$)'
    $apiPattern='(?i)(?:^|\s)"?'+[regex]::Escape($api)+'"?(?:\s|$)'
    $rootPattern='(?i)(?:^|\s)"?--gpt-root"?\s+"?'+[regex]::Escape($gptRootPath)+'"?(?:\s|$)'
    if ($identity.Name -notmatch '^pythonw?\.exe$' -or $command -notmatch $launcherPattern -or
        $command -notmatch $apiPattern -or $command -notmatch $rootPattern) {
        @{stopped=$false;reason='unmanaged_listener'} | ConvertTo-Json -Compress
        exit 0
    }
    if ([Math]::Abs(($owned.StartTime.ToUniversalTime()-$identity.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
        throw 'Voice process creation identity changed; no process stopped.'
    }
    $privateBytes=$owned.PrivateMemorySize64
    if ($InspectOnly) {
        @{stopped=$false;managed=$true;reason='inspect_only';private_bytes=$privateBytes} | ConvertTo-Json -Compress
        exit 0
    }
    $owned.Kill()
    if (-not $owned.WaitForExit(10000)) { throw 'Voice process did not exit.' }
    @{stopped=$true;managed=$true;reason='yield_to_singing';private_bytes_before=$privateBytes} | ConvertTo-Json -Compress
}
finally { $owned.Dispose() }
