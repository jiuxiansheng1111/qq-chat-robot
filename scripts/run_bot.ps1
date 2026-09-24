[CmdletBinding()]
param()

# Long-running lifecycle supervisor for the FastAPI bot.
# It follows NapCat's OneBot HTTP Server instead of keeping Uvicorn alive
# while QQ/NapCat is closed:
#   OneBot 3000 available   -> start/adopt Uvicorn
#   OneBot 3000 unavailable -> stop Uvicorn and wait

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$bootstrapScript = Join-Path $PSScriptRoot "bootstrap_windows.ps1"
$envPath = Join-Path $projectRoot ".env"
$logRoot = Join-Path $projectRoot "logs"
$outputLog = Join-Path $logRoot "bot.out.log"
$errorLog = Join-Path $logRoot "bot.error.log"
$watchdogLog = Join-Path $logRoot "watchdog.log"
$uvicornStartMutex = [Threading.Mutex]::new($false, "Global\QQChatRobot-Uvicorn-Start")

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Set-Location -LiteralPath $projectRoot

function Write-LifecycleLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -LiteralPath $watchdogLog -Value $line
    Write-Host $line
}

function Get-OneBotEndpoint {
    $fallback = [Uri]"http://127.0.0.1:3000"
    if (-not (Test-Path -LiteralPath $envPath)) {
        return $fallback
    }

    $line = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*ONEBOT_API_BASE\s*=' } |
        Select-Object -First 1
    if (-not $line -or $line -notmatch '^\s*ONEBOT_API_BASE\s*=\s*(.*?)\s*$') {
        return $fallback
    }

    $raw = $matches[1].Trim().Trim('"').Trim("'")
    if (-not $raw) {
        return $fallback
    }

    try {
        $parsed = [Uri]$raw
        if (-not $parsed.Host -or $parsed.Port -le 0) {
            return $fallback
        }
        return $parsed
    }
    catch {
        Write-LifecycleLog "Invalid ONEBOT_API_BASE '$raw'; falling back to 127.0.0.1:3000."
        return $fallback
    }
}

function Test-TcpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$TargetHost,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMilliseconds = 700
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-UvicornProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -and
            $_.CommandLine -match '(?i)-m\s+uvicorn\s+app\.main:app'
        })
}

function Stop-Uvicorn {
    $processes = @(Get-UvicornProcesses)
    if ($processes.Count -eq 0) {
        return
    }

    foreach ($processInfo in $processes) {
        Stop-Process -Id ([int]$processInfo.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    Write-LifecycleLog "Stopped Uvicorn process(es): $($processes.ProcessId -join ', ')."
}

function Start-Uvicorn {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python environment not found: $pythonPath"
    }
    $mutexAcquired = $false
    try {
        $mutexAcquired = $uvicornStartMutex.WaitOne(0)
        if (-not $mutexAcquired) {
            return
        }
        if (@(Get-UvicornProcesses).Count -gt 0) {
            return
        }

        Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput $outputLog `
            -RedirectStandardError $errorLog `
            -WindowStyle Hidden

        Start-Sleep -Seconds 2
        Write-LifecycleLog "Started Uvicorn from the current project files."
    }
    finally {
        if ($mutexAcquired) {
            $uvicornStartMutex.ReleaseMutex()
        }
    }
}

$oneBotEndpoint = Get-OneBotEndpoint
$oneBotHost = $oneBotEndpoint.Host
$oneBotPort = $oneBotEndpoint.Port
$lastOneBotState = $null

try {
    if (-not (Test-Path -LiteralPath $bootstrapScript)) {
        throw "Windows bootstrap script not found: $bootstrapScript"
    }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrapScript *>> $watchdogLog
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python environment setup returned exit code $LASTEXITCODE"
    }

    Write-LifecycleLog "Lifecycle supervisor watching OneBot $oneBotHost`:$oneBotPort."

    while ($true) {
        $oneBotReady = Test-TcpEndpoint -TargetHost $oneBotHost -Port $oneBotPort

        if (-not $oneBotReady) {
            if ($lastOneBotState -ne $false) {
                Write-LifecycleLog "NapCat/OneBot is unavailable; Uvicorn will stay stopped."
            }
            $lastOneBotState = $false
            Stop-Uvicorn
            Start-Sleep -Seconds 5
            continue
        }

        if ($lastOneBotState -ne $true) {
            Write-LifecycleLog "NapCat/OneBot is available; ensuring Uvicorn is running."
        }
        $lastOneBotState = $true

        if (@(Get-UvicornProcesses).Count -eq 0) {
            Start-Uvicorn
            Start-Sleep -Seconds 2
        }

        $unhealthySince = $null
        while ($true) {
            Start-Sleep -Seconds 10

            if (-not (Test-TcpEndpoint -TargetHost $oneBotHost -Port $oneBotPort)) {
                Write-LifecycleLog "NapCat/OneBot stopped; stopping Uvicorn."
                Stop-Uvicorn
                break
            }

            if (@(Get-UvicornProcesses).Count -eq 0) {
                break
            }

            try {
                $health = Invoke-WebRequest `
                    -Uri "http://127.0.0.1:8000/health/live" `
                    -UseBasicParsing `
                    -TimeoutSec 5 `
                    -ErrorAction Stop
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
                Write-LifecycleLog "Bot health failed for 45 seconds; restarting Uvicorn."
                Stop-Uvicorn
                break
            }
        }
    }
}
catch {
    Write-LifecycleLog "Lifecycle supervisor failed: $($_.Exception.Message)"
    throw
}
finally {
    # If this supervisor is deliberately stopped, do not leave an orphaned
    # Uvicorn process behind.
    Stop-Uvicorn
}
