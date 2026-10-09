$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$scripts = Join-Path $repo 'scripts'
foreach ($name in @('astrbot_lifecycle.ps1', 'supervise_astrbot.ps1', 'run_astrbot.ps1',
        'run_bot.ps1', 'check_bot_task.ps1', 'install_autostart.ps1', 'start_all.ps1')) {
    $tokens = $null
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile((Join-Path $scripts $name), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw "$name parse failed: $($errors[0].Message)" }
}
. (Join-Path $scripts 'astrbot_windows_common.ps1')
. (Join-Path $scripts 'astrbot_process.ps1')
. (Join-Path $scripts 'astrbot_lifecycle.ps1')

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -cne $Expected) { throw "$Message (actual: $Actual; expected: $Expected)" }
}
function Assert-Throws {
    param([scriptblock]$Action, [string]$Message)
    $thrown = $false
    try { & $Action } catch { $thrown = $true }
    if (-not $thrown) { throw $Message }
}

$root = Join-Path $PSScriptRoot ('.supervisor-test-' + [guid]::NewGuid().ToString('N'))
$layout = [pscustomobject]@{
    Root = $root; ProjectRoot = $repo; Logs = (Join-Path $root 'logs')
    Instance = (Join-Path $root 'instance'); UvToolBin = (Join-Path $root 'runtime\bin')
    UvToolDir = (Join-Path $root 'runtime\tool-envs'); UvPythonDir = (Join-Path $root 'runtime\python')
}
try {
    Ensure-AstrbotDirectory -Layout $layout -Path (Join-Path $layout.Instance 'data')
    $launcher = Join-Path $layout.UvToolBin 'astrbot.exe'
    $tool = Join-Path $layout.UvToolDir 'astrbot\Scripts\python.exe'
    $script:nativePath = Join-Path $layout.UvPythonDir 'test\python.exe'
    function Get-ManagedPythonPath { param($Layout); return $script:nativePath }
    $script:processes = @{}
    $ids = @()
    $argsByPath = @{ LauncherPath = $launcher; ToolPythonPath = $tool; ManagedPythonPath = $script:nativePath; Port = 6185 }
    $paths = @($launcher, $tool, $script:nativePath)
    for ($index = 0; $index -lt 3; $index++) {
        $command = '"' + $paths[$index] + '" '
        if ($index -gt 0) { $command += '"' + $launcher + '" ' }
        $command += 'run --port 6185'
        $info = [pscustomobject]@{ ProcessId = 10001 + $index; ParentProcessId = 10000 + $index
            ExecutablePath = $paths[$index]; CommandLine = $command
            CreationDate = ([datetime]'2026-10-09T01:00:00Z').AddSeconds($index) }
        $script:processes[[int]$info.ProcessId] = $info
        $ids += New-AstrbotProcessIdentity -ProcessInfo $info @argsByPath
    }
    $state = New-AstrbotRunState -Port 6185 -AstrBotVersion '4.28.2' -LauncherIdentity $ids[0]
    $state.Phase = 'running'; $state.Processes = $ids; $state.ListenerProcessId = 10003
    Write-AstrbotRunState -Path (Join-Path $root 'astrbot-owned-run.json') -Layout $layout -State $state
    $configPath = Join-Path $layout.Instance 'data\cmd_config.json'
    function Set-TestPlatform { param([bool]$Enabled)
        @{ platform = @(@{ id = 'qq-chatrobot-local'; type = 'aiocqhttp'; enable = $Enabled }) } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
    }
    function Get-AstrbotProcessInfo { param([int]$ProcessId); return $script:processes[$ProcessId] }
    $script:dashboardOwner = 10003; $script:wsOwner = 10003; $script:responsive = $true
    function Get-AstrbotListeners { param([int]$Port)
        $owner = if ($Port -eq 6185) { $script:dashboardOwner } else { $script:wsOwner }
        if ($owner) { return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = $owner } }
        return @()
    }
    function Test-AstrbotDashboardResponse { return $script:responsive }
    Set-TestPlatform $true
    Assert-Equal (Get-AstrbotHealth -Layout $layout).Status 'healthy' 'Healthy instance rejected'
    $script:watchProcess = [pscustomobject]@{
        Handle = [IntPtr]123; Path = $script:nativePath
        StartTime = $script:processes[10003].CreationDate; Disposed = $false
    }
    $script:watchProcess | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    function Get-Process { param([int]$Id); Assert-Equal $Id 10003 'Wrong process opened for exit watch'; return $script:watchProcess }
    $watch = New-AstrbotListenerWatch -Layout $layout
    Assert-Equal $watch.Identity.ProcessId 10003 'Wrong recorded listener watched'
    Assert-Equal $watch.Process.Handle ([IntPtr]123) 'Listener handle not retained'
    $script:watchProcess.Path = 'C:\foreign\python.exe'
    Assert-Throws { New-AstrbotListenerWatch -Layout $layout } 'Changed process path accepted by exit watch'
    Assert-Equal $script:watchProcess.Disposed $true 'Rejected process handle was not disposed'
    $script:watchProcess.Path = $script:nativePath; $script:watchProcess.Disposed = $false
    $script:watchProcess.StartTime = $script:watchProcess.StartTime.AddMinutes(1)
    Assert-Throws { New-AstrbotListenerWatch -Layout $layout } 'Reused PID accepted by exit watch'
    Assert-Equal $script:watchProcess.Disposed $true 'Reused PID handle was not disposed'
    $script:watchProcess.StartTime = $script:processes[10003].CreationDate
    Remove-Item Function:\Get-Process
    Assert-Equal (Get-AstrbotExitDescription -ExitCode 0) 'ExitCode=0 (0x00000000)' 'Zero exit code incorrectly formatted'
    Assert-Equal (Get-AstrbotExitDescription -ExitCode -1073741819) 'ExitCode=-1073741819 (0xC0000005)' 'Windows crash code lost its unsigned representation'
    $script:wsOwner = 0
    $health = Get-AstrbotHealth -Layout $layout
    Assert-Equal $health.Status 'unhealthy' 'Missing QQ listener ignored'
    $now = [datetime]::UtcNow
    Assert-Equal (Get-AstrbotRecoveryDecision -Health $health -Now $now -UnhealthySince $now.AddSeconds(-59) `
        -NextAttempt ([datetime]::MinValue)) 'wait' 'Startup grace was not respected'
    Assert-Equal (Get-AstrbotRecoveryDecision -Health $health -Now $now -UnhealthySince $now.AddSeconds(-60) `
        -NextAttempt ([datetime]::MinValue)) 'restart' 'Persistent QQ listener failure not recovered'
    Set-TestPlatform $false
    Assert-Equal (Get-AstrbotHealth -Layout $layout).Status 'healthy' 'Disabled QQ adapter causes restart loop'
    Set-TestPlatform $true
    $script:wsOwner = 20000
    Assert-Throws { Get-AstrbotHealth -Layout $layout } 'Foreign port 6199 owner accepted'
    $script:wsOwner = 10003
    $script:dashboardOwner = 20000
    Assert-Throws { Get-AstrbotHealth -Layout $layout } 'Foreign dashboard owner accepted'
    $script:dashboardOwner = 10003
    $originalCommand = $script:processes[10003].CommandLine
    $script:processes[10003].CommandLine = 'foreign command'
    Assert-Throws { Get-AstrbotHealth -Layout $layout } 'Reused PID was accepted'
    $script:processes[10003].CommandLine = $originalCommand
    $script:responsive = $false
    Assert-Equal (Get-AstrbotHealth -Layout $layout).Status 'unhealthy' 'Hung HTTP response was ignored'
    $script:responsive = $true
    $script:dashboardOwner = 0; $script:wsOwner = 0
    $script:processes.Remove(10003)
    Assert-Equal (New-AstrbotListenerWatch -Layout $layout) $null 'Exited listener was opened for a new watch'
    $health = Get-AstrbotHealth -Layout $layout
    Assert-Equal $health.Status 'down' 'Listener exit not detected'
    Assert-Equal $health.Restart $true 'Remaining wrappers were not scheduled for cleanup'
    Assert-Equal (Get-AstrbotRecoveryDecision -Health $health -Now $now -UnhealthySince $now `
        -NextAttempt ([datetime]::MinValue)) 'start' 'Process exit should recover without a 60-second delay'
    Assert-Equal (Get-AstrbotRecoveryDecision -Health $health -Now $now -UnhealthySince $now `
        -NextAttempt $now.AddSeconds(30)) 'wait' 'Retry backoff ignored'
    Set-AstrbotManualStop -Layout $layout
    Assert-Equal (Get-AstrbotHealth -Layout $layout).Status 'paused' 'Manual stop did not persist'
    Clear-AstrbotManualStop -Layout $layout
    Assert-Equal (Test-AstrbotManualStop -Layout $layout) $false 'Explicit start did not clear stop marker'
    Ensure-AstrbotDirectory -Layout $layout -Path $layout.Logs
    $log = Join-Path $layout.Logs 'astrbot-service.stdout.log'
    Set-Content -LiteralPath $log -Value 'fault evidence' -Encoding UTF8
    Preserve-AstrbotServiceLogs -Layout $layout
    $archived = @(Get-ChildItem -LiteralPath (Join-Path $layout.Logs 'archive') -Filter '*.log' -Recurse)
    Assert-Equal $archived.Count 1 'Previous log was not archived'
    Assert-Equal (Get-Content -LiteralPath $archived[0].FullName).Trim() 'fault evidence' 'Archived log changed'
    Assert-Equal (Test-Path -LiteralPath $log) $false 'Old stdout log was left for truncation'
    $mutex = New-AstrbotMutex -ProjectRoot $repo -Purpose ('Test-' + [guid]::NewGuid().ToString('N'))
    try {
        Assert-Equal (Enter-AstrbotMutex -Mutex $mutex) $true 'Mutex acquisition failed'
        $mutex.ReleaseMutex()
        Assert-Equal (Enter-AstrbotMutex -Mutex $mutex) $true 'Mutex was not reusable'
        $mutex.ReleaseMutex()
    } finally { $mutex.Dispose() }
    $result = Invoke-AstrbotLoggedCommand -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Milliseconds 200; exit 0') `
        -WorkingDirectory $repo -LogPrefix 'exit-success' -LogDirectory $layout.Logs -TimeoutSeconds 5
    Assert-Equal $result.ExitCode 0 'PowerShell child exit code was lost'
    Assert-Throws {
        Invoke-AstrbotLoggedCommand -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-Command', 'exit 7') `
            -WorkingDirectory $repo -LogPrefix 'exit-failure' -LogDirectory $layout.Logs -TimeoutSeconds 5
    } 'Failing child command was accepted'
    $child = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Milliseconds 500; exit 7') `
        -PassThru -WindowStyle Hidden
    try {
        $retained = Get-Process -Id $child.Id
        try {
            $null = $retained.Handle
            if (-not $retained.WaitForExit(5000)) { throw 'Exit watch child did not finish.' }
            Assert-Equal $retained.HasExited $true 'Retained handle did not observe child exit'
            Assert-Equal (Get-AstrbotExitDescription -ExitCode $retained.ExitCode) 'ExitCode=7 (0x00000007)' `
                'Exit status was lost after the child disappeared'
        } finally { $retained.Dispose() }
    } finally {
        if (-not $child.HasExited) { $child.Kill() }
        $child.Dispose()
    }
    Assert-Throws {
        Invoke-AstrbotLoggedCommand -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 10') `
            -WorkingDirectory $repo -LogPrefix 'exit-timeout' -LogDirectory $layout.Logs -TimeoutSeconds 1
    } 'Hung recovery command did not time out'
    Write-Host 'AstrBot supervisor checks passed: ownership, retained exit status, readiness, grace, retry, manual stop and log preservation.'
} finally {
    $expectedPrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\.supervisor-test-'
    if (-not ([IO.Path]::GetFullPath($root)).StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Test cleanup target escaped the tests directory.'
    }
    Remove-AstrbotManagedPath -Layout $layout -Path $root -Recurse
}
