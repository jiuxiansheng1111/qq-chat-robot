#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'astrbot_windows_common.ps1')
. (Join-Path $PSScriptRoot 'astrbot_process.ps1')
. (Join-Path $PSScriptRoot 'astrbot_lifecycle.ps1')
. (Join-Path $PSScriptRoot 'bot_runtime_mode.ps1')

$layout = Get-AstrbotLayout
$supervisorMutex = New-AstrbotMutex -ProjectRoot $layout.ProjectRoot -Purpose 'Supervisor'
$held = Enter-AstrbotMutex -Mutex $supervisorMutex
if (-not $held) { $supervisorMutex.Dispose(); exit 0 }
$logPath = Join-Path $layout.Logs 'astrbot-supervisor.log'
Ensure-AstrbotDirectory -Layout $layout -Path $layout.Logs
Assert-AstrbotManagedPath -Layout $layout -Path $logPath

function Write-SupervisorLog {
    param([string]$Message)
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format o) $Message"
}

try {
    Write-SupervisorLog "Supervisor started (PID $PID); checking every 10 seconds."
    $unhealthySince = [datetime]::MinValue
    $nextAttempt = [datetime]::MinValue
    $lastStatus = ''
    $failedAttempts = 0
    $healthySince = [datetime]::MinValue
    while ((Get-QQChatRobotRuntime -ProjectRoot $layout.ProjectRoot) -eq 'astrbot') {
        try {
            # Start/stop and health snapshots use the same lock: don't inspect half-written startup state.
            $lifecycle = New-AstrbotMutex -ProjectRoot $layout.ProjectRoot -Purpose 'Lifecycle'
            $snapshotHeld = $false
            try {
                $snapshotHeld = Enter-AstrbotMutex -Mutex $lifecycle
                if ($snapshotHeld) { $health = Get-AstrbotHealth -Layout $layout }
            } finally {
                if ($snapshotHeld) { $lifecycle.ReleaseMutex() }
                $lifecycle.Dispose()
            }
            if (-not $snapshotHeld) { Start-Sleep -Seconds 10; continue }
            $now = [datetime]::UtcNow
            if ($health.Status -eq 'healthy') {
                $unhealthySince = [datetime]::MinValue
                if ($healthySince -eq [datetime]::MinValue) { $healthySince = $now }
                if (($now - $healthySince).TotalSeconds -ge 300) { $failedAttempts = 0 }
            } elseif ($health.Status -eq 'paused') {
                $unhealthySince = [datetime]::MinValue
                $nextAttempt = [datetime]::MinValue
                $failedAttempts = 0
                $healthySince = [datetime]::MinValue
            } else {
                $healthySince = [datetime]::MinValue
                if ($unhealthySince -eq [datetime]::MinValue) { $unhealthySince = $now }
            }
            $status = "$($health.Status): $($health.Reason)"
            if ($status -ne $lastStatus) { Write-SupervisorLog $status; $lastStatus = $status }
            $decision = Get-AstrbotRecoveryDecision -Health $health -Now $now `
                -UnhealthySince $unhealthySince -NextAttempt $nextAttempt
            if ($decision -ne 'wait') {
                Write-SupervisorLog "Recovery $decision requested: $($health.Reason)."
                $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    (Join-Path $PSScriptRoot 'run_astrbot.ps1'), '-Recovery')
                # If the listener died but launcher wrappers remain, clean up the verified chain too.
                if ($decision -eq 'restart' -or $health.Restart) { $arguments += '-Restart' }
                Invoke-AstrbotLoggedCommand -FilePath 'powershell.exe' -ArgumentList $arguments `
                    -WorkingDirectory $layout.ProjectRoot -LogPrefix 'astrbot-recovery' `
                    -LogDirectory $layout.Logs -TimeoutSeconds 180 | Out-Null
                # Bound repeated crashes as well as startup failures; five stable minutes reset the streak.
                $failedAttempts++
                $delay = [math]::Min(300, 10 * [math]::Pow(2, [math]::Min($failedAttempts - 1, 5)))
                $nextAttempt = [datetime]::UtcNow.AddSeconds($delay)
                $unhealthySince = [datetime]::UtcNow
                Write-SupervisorLog "Recovery command completed; retry cooldown ${delay}s."
            }
        } catch {
            $healthySince = [datetime]::MinValue
            $failedAttempts++
            $delay = [math]::Min(300, 10 * [math]::Pow(2, [math]::Min($failedAttempts - 1, 5)))
            $nextAttempt = [datetime]::UtcNow.AddSeconds($delay)
            $message = "Recovery failed: $($_.Exception.Message)"
            if ($message -ne $lastStatus -or $failedAttempts -le 3) { Write-SupervisorLog $message }
            $lastStatus = $message
        }
        Start-Sleep -Seconds 10
    }
    Write-SupervisorLog 'Runtime switched away from AstrBot; supervisor exiting.'
} finally {
    $supervisorMutex.ReleaseMutex()
    $supervisorMutex.Dispose()
}
