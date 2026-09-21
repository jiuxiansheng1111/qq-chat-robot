[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logRoot = Join-Path $projectRoot "logs"
$outputLog = Join-Path $logRoot "bot.out.log"
$errorLog = Join-Path $logRoot "bot.error.log"
$watchdogLog = Join-Path $logRoot "watchdog.log"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Set-Location -LiteralPath $projectRoot

while ($true) {
    $listener = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count -gt 0) {
        $process = Get-Process -Id $listener[0].OwningProcess -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            Start-Sleep -Seconds 2
            continue
        }
        Add-Content -LiteralPath $watchdogLog -Value "$(Get-Date -Format o) Existing process $($process.Id) found on port 8000; monitoring it."
    }
    else {
        $process = Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput $outputLog `
            -RedirectStandardError $errorLog `
            -PassThru `
            -WindowStyle Hidden
    }
    # A wedged process can keep port 8000 open, so supervise health as well.
    $unhealthySince = $null
    while (-not $process.HasExited) {
        Start-Sleep -Seconds 10
        if ($process.HasExited) {
            break
        }
        try {
            $health = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health/live" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                $unhealthySince = $null
                continue
            }
        }
        catch {
            if ($null -eq $unhealthySince) {
                $unhealthySince = Get-Date
            }
        }
        if ($unhealthySince -and ((Get-Date) - $unhealthySince).TotalSeconds -ge 45) {
            Add-Content -LiteralPath $watchdogLog -Value "$(Get-Date -Format o) Health check failed for 45 seconds; stopping bot process."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            break
        }
    }
    $exitCode = if ($process.HasExited) { $process.ExitCode } else { "health-timeout" }
    Add-Content -LiteralPath $watchdogLog -Value "$(Get-Date -Format o) Bot process exited ($exitCode); restarting in 5 seconds."
    Start-Sleep -Seconds 5
}
