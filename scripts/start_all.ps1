[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Split-Path -Parent $projectRoot
$envPath = Join-Path $projectRoot ".env"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$napCatRoot = Join-Path $desktopRoot "NapCat.Shell"
$napCatLauncher = Join-Path $napCatRoot "launcher.bat"

function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutMilliseconds = 700
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            return $false
        }
        $client.EndConnect($result)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-LocalPort -Port $Port) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-DotEnvValues {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $values[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-Elevation {
    $arguments = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        ('"{0}"' -f $PSCommandPath)
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs
}

try {
    Write-Host "=== QQ ChatRobot One-Click Start ===" -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Project config was not found: $envPath"
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Project Python environment was not found: $pythonPath"
    }
    if (-not (Test-Path -LiteralPath $napCatLauncher)) {
        throw "NapCat launcher was not found: $napCatLauncher"
    }

    $settings = Get-DotEnvValues -Path $envPath
    $qqId = [string]$settings["ONEBOT_SELF_ID"]
    if ($qqId -notmatch '^\d{5,12}$') {
        throw "ONEBOT_SELF_ID is invalid in .env."
    }

    $napCatWebReady = Test-LocalPort -Port 6099
    $botReady = Test-LocalPort -Port 8000

    if (-not $napCatWebReady -and -not (Test-IsAdministrator)) {
        Write-Host "Administrator access is required. Requesting UAC confirmation..." -ForegroundColor Yellow
        Request-Elevation
        exit 0
    }

    if ($napCatWebReady) {
        Write-Host "[RUNNING] NapCat WebUI: 6099" -ForegroundColor Green
    }
    else {
        $existingNapCat = @(Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue)
        if ($existingNapCat.Count -gt 0) {
            throw "A stale NapCat process exists while port 6099 is unavailable. Stop stale NapCat/QQ processes and retry."
        }

        Write-Host "[STARTING] NapCat with QQ quick login..." -ForegroundColor Yellow
        $napCatCommand = 'call "{0}" {1}' -f $napCatLauncher, $qqId
        Start-Process `
            -FilePath "cmd.exe" `
            -ArgumentList @("/k", $napCatCommand) `
            -WorkingDirectory $napCatRoot `
            -WindowStyle Minimized

        if (-not (Wait-LocalPort -Port 6099 -TimeoutSeconds 60)) {
            throw "NapCat WebUI did not start within 60 seconds. Check the minimized NapCat window."
        }
        Write-Host "[STARTED] NapCat WebUI: 6099" -ForegroundColor Green
    }

    if (Wait-LocalPort -Port 3000 -TimeoutSeconds 60) {
        Write-Host "[READY] OneBot HTTP: 3000" -ForegroundColor Green
    }
    else {
        Write-Host "[LOGIN REQUIRED] OneBot 3000 is not ready. Open http://127.0.0.1:6099 if QQ confirmation is required." -ForegroundColor Yellow
    }

    if ($botReady -or (Test-LocalPort -Port 8000)) {
        Write-Host "[RUNNING] Bot API: 8000" -ForegroundColor Green
    }
    else {
        Write-Host "[STARTING] Bot API..." -ForegroundColor Yellow
        Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Minimized

        if (-not (Wait-LocalPort -Port 8000 -TimeoutSeconds 30)) {
            throw "Bot API did not start within 30 seconds. Check the project config and logs."
        }
        Write-Host "[STARTED] Bot API: 8000" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Startup complete. Re-running this script will not duplicate NapCat." -ForegroundColor Cyan
    Write-Host "NapCat WebUI: http://127.0.0.1:6099"
    Write-Host "Bot health check: http://127.0.0.1:8000/health/ready"
    Start-Sleep -Seconds 4
}
catch {
    Write-Host ""
    Write-Host ("Startup failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host "Press Enter to close this window."
    Read-Host | Out-Null
    exit 1
}
