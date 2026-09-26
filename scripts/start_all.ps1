[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Split-Path -Parent $projectRoot
$envPath = Join-Path $projectRoot ".env"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$bootstrapScript = Join-Path $PSScriptRoot "bootstrap_windows.ps1"
$lifecycleScript = Join-Path $PSScriptRoot "run_bot.ps1"
$logRoot = Join-Path $projectRoot "logs"
$botOutputLog = Join-Path $logRoot "bot.out.log"
$botErrorLog = Join-Path $logRoot "bot.error.log"
$napCatDesktop = "C:\Program Files\NapCatQQ Desktop\NapCatQQ-Desktop.exe"
$napCatRoot = Join-Path $desktopRoot "NapCat.Shell"
$napCatBoot = Join-Path $napCatRoot "NapCatWinBootMain.exe"
$napCatHook = Join-Path $napCatRoot "NapCatWinBootHook.dll"
$qqExecutableCandidates = @(
    "C:\Program Files\Tencent\QQNT\QQ.exe",
    "C:\Program Files\Tencent\QQ\QQ.exe",
    "C:\Program Files (x86)\Tencent\QQ\QQ.exe"
)
$qqExecutable = $qqExecutableCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

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

function Apply-GptSovitsWeights {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Settings,
        [Parameter(Mandatory = $true)][string]$VoiceHost,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $configured = ([string]$Settings["GPT_SOVITS_SOVITS_WEIGHTS"]).Trim()
    if (-not $configured) {
        return
    }
    $weightsPath = if ([System.IO.Path]::IsPathRooted($configured)) {
        [System.IO.Path]::GetFullPath($configured)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $configured))
    }
    if (-not (Test-Path -LiteralPath $weightsPath -PathType Leaf)) {
        Write-Host "[VOICE] Configured SoVITS weights not found: $weightsPath" -ForegroundColor Yellow
        return
    }
    $encodedPath = [Uri]::EscapeDataString($weightsPath)
    $endpoint = "http://$VoiceHost`:$Port/set_sovits_weights?weights_path=$encodedPath"
    try {
        $response = Invoke-RestMethod -Uri $endpoint -Method Get -TimeoutSec 180
        Write-Host "[VOICE] Loaded SoVITS weights: $weightsPath" -ForegroundColor Green
    }
    catch {
        Write-Host "[VOICE] Failed to load SoVITS weights: $weightsPath ($($_.Exception.Message))" -ForegroundColor Yellow
    }
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

function Start-GptSovitsSidecar {
    param([Parameter(Mandatory = $true)][hashtable]$Settings)

    $provider = ([string]$Settings["VOICE_PROVIDER"]).Trim().ToLowerInvariant()
    $voiceEnabled = ([string]$Settings["VOICE_ENABLED"]).Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
    $autoStart = ([string]$Settings["GPT_SOVITS_AUTO_START"]).Trim().ToLowerInvariant()
    if (-not $autoStart) {
        $autoStart = "auto"
    }
    $shouldStart = $autoStart -eq "true" -or
        ($autoStart -eq "auto" -and $voiceEnabled -and $provider -in @("gpt_sovits", "gpt-sovits"))
    if (-not $shouldStart) {
        Write-Host "[VOICE] GPT-SoVITS sidecar is disabled (set GPT_SOVITS_AUTO_START=true or enable the GPT provider)." -ForegroundColor DarkGray
        return $false
    }

    $voiceHost = ([string]$Settings["GPT_SOVITS_HOST"]).Trim()
    if (-not $voiceHost) {
        $voiceHost = "127.0.0.1"
    }
    $voicePort = 9880
    $configuredVoicePort = 0
    if ([int]::TryParse(([string]$Settings["GPT_SOVITS_PORT"]).Trim(), [ref]$configuredVoicePort) -and $configuredVoicePort -gt 0) {
        $voicePort = $configuredVoicePort
    }
    $voiceUrl = ([string]$Settings["VOICE_API_URL"]).Trim()
    if ($voiceUrl) {
        try {
            $parsedVoiceUrl = [Uri]$voiceUrl
            if ($parsedVoiceUrl.Host) {
                $voiceHost = $parsedVoiceUrl.Host
            }
            if ($parsedVoiceUrl.Port -gt 0) {
                $voicePort = $parsedVoiceUrl.Port
            }
        }
        catch {
            Write-Host "[VOICE] VOICE_API_URL is invalid; using http://127.0.0.1:9880." -ForegroundColor Yellow
        }
    }

    $localHosts = @("127.0.0.1", "localhost", "::1")
    if ($voiceHost -notin $localHosts) {
        Write-Host "[VOICE] Using remote GPT-SoVITS at $voiceUrl; local sidecar will not start." -ForegroundColor Cyan
        return $true
    }
    if (-not $voiceUrl) {
        # The Python app inherits this value when start_all launches its lifecycle supervisor.
        $env:VOICE_API_URL = "http://127.0.0.1:$voicePort"
    }

    if (Test-LocalPort -Port $voicePort) {
        Write-Host "[VOICE] GPT-SoVITS already listening on $voicePort." -ForegroundColor Green
        Apply-GptSovitsWeights -Settings $Settings -VoiceHost $voiceHost -Port $voicePort
        return $true
    }

    $rootValue = ([string]$Settings["GPT_SOVITS_ROOT"]).Trim()
    if (-not $rootValue) {
        $rootValue = "..\qq-chatrobot-voice\GPT-SoVITS"
    }
    $gptRoot = if ([System.IO.Path]::IsPathRooted($rootValue)) {
        [System.IO.Path]::GetFullPath($rootValue)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $rootValue))
    }
    $apiScript = Join-Path $gptRoot "api_v2.py"
    $configValue = ([string]$Settings["GPT_SOVITS_TTS_CONFIG"]).Trim()
    if (-not $configValue) {
        $configValue = "GPT_SoVITS\configs\tts_infer.yaml"
    }
    $ttsConfig = if ([System.IO.Path]::IsPathRooted($configValue)) {
        [System.IO.Path]::GetFullPath($configValue)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $gptRoot $configValue))
    }
    if (-not (Test-Path -LiteralPath $gptRoot -PathType Container)) {
        Write-Host "[VOICE] GPT-SoVITS root not found: $gptRoot" -ForegroundColor Yellow
        return $false
    }
    if (-not (Test-Path -LiteralPath $apiScript -PathType Leaf)) {
        Write-Host "[VOICE] api_v2.py not found: $apiScript" -ForegroundColor Yellow
        return $false
    }
    if (-not (Test-Path -LiteralPath $ttsConfig -PathType Leaf)) {
        Write-Host "[VOICE] GPT-SoVITS config not found: $ttsConfig" -ForegroundColor Yellow
        return $false
    }

    $pythonValue = ([string]$Settings["GPT_SOVITS_PYTHON"]).Trim()
    if ($pythonValue) {
        $gptPython = if ([System.IO.Path]::IsPathRooted($pythonValue)) {
            [System.IO.Path]::GetFullPath($pythonValue)
        }
        else {
            # GPT_SOVITS_PYTHON is relative to the bot project root, like GPT_SOVITS_ROOT.
            [System.IO.Path]::GetFullPath((Join-Path $projectRoot $pythonValue))
        }
    }
    else {
        $gptPython = Join-Path $gptRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $gptPython -PathType Leaf)) {
            $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
            $gptPython = if ($pathPython) { $pathPython.Source } else { "" }
        }
    }
    if (-not $gptPython -or -not (Test-Path -LiteralPath $gptPython -PathType Leaf)) {
        Write-Host "[VOICE] GPT-SoVITS Python not found. Set GPT_SOVITS_PYTHON in .env." -ForegroundColor Yellow
        return $false
    }

    $existing = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "^(python|pythonw)(\.exe)?$" -and
            $_.CommandLine -and
            $_.CommandLine -match '(?i)api_v2\.py'
        })
    if ($existing.Count -gt 0) {
        Write-Host "[VOICE] GPT-SoVITS process exists; waiting for port $voicePort..." -ForegroundColor Cyan
    }
    else {
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        $voiceOutLog = Join-Path $logRoot "gpt_sovits.out.log"
        $voiceErrorLog = Join-Path $logRoot "gpt_sovits.error.log"
        Write-Host "[VOICE] Starting GPT-SoVITS on $voiceHost`:$voicePort..." -ForegroundColor Yellow
        Start-Process `
            -FilePath $gptPython `
            -ArgumentList @("api_v2.py", "-a", $voiceHost, "-p", [string]$voicePort, "-c", $ttsConfig) `
            -WorkingDirectory $gptRoot `
            -RedirectStandardOutput $voiceOutLog `
            -RedirectStandardError $voiceErrorLog `
            -WindowStyle Hidden
    }

    if (Wait-LocalPort -Port $voicePort -TimeoutSeconds 90) {
        Write-Host "[VOICE] GPT-SoVITS ready: http://$voiceHost`:$voicePort/tts" -ForegroundColor Green
        Apply-GptSovitsWeights -Settings $Settings -VoiceHost $voiceHost -Port $voicePort
        return $true
    }
    Write-Host "[VOICE] GPT-SoVITS did not become ready. Check logs\gpt_sovits.error.log; bot will still start." -ForegroundColor Yellow
    return $false
}

try {
    Write-Host "=== QQ ChatRobot One-Click Start ===" -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Project config was not found: $envPath"
    }
    if (-not (Test-Path -LiteralPath $bootstrapScript)) {
        throw "Windows bootstrap script was not found: $bootstrapScript"
    }

    Write-Host "[CHECK] Python environment..." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrapScript
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python environment setup failed. Run scripts\bootstrap_windows.ps1 for details."
    }
    $useNapCatDesktop = Test-Path -LiteralPath $napCatDesktop
    if (-not $useNapCatDesktop) {
        if (-not (Test-Path -LiteralPath $napCatBoot)) {
            throw "Neither NapCatQQ Desktop nor the legacy NapCat boot program was found."
        }
        if (-not (Test-Path -LiteralPath $napCatHook)) {
            throw "NapCat hook was not found: $napCatHook"
        }
        if (-not $qqExecutable) {
            throw "QQ executable was not found. Install QQNT or NapCatQQ Desktop first."
        }
    }

    $settings = Get-DotEnvValues -Path $envPath
    $qqId = [string]$settings["ONEBOT_SELF_ID"]
    if ($qqId -notmatch '^\d{5,12}$') {
        throw "ONEBOT_SELF_ID is invalid in .env."
    }

    $voiceSidecarReady = Start-GptSovitsSidecar -Settings $settings

    $napCatWebReady = Test-LocalPort -Port 6099
    $oneBotReady = Test-LocalPort -Port 3000
    $botReady = Test-LocalPort -Port 8000

    if (-not $useNapCatDesktop -and -not $napCatWebReady -and -not (Test-IsAdministrator)) {
        Write-Host "Administrator access is required. Requesting UAC confirmation..." -ForegroundColor Yellow
        Request-Elevation
        exit 0
    }

    if ($oneBotReady) {
        Write-Host "[RUNNING] OneBot HTTP: 3000" -ForegroundColor Green
    }
    elseif ($useNapCatDesktop) {
        $desktopProcess = @(Get-Process -Name "NapCatQQ-Desktop" -ErrorAction SilentlyContinue)
        if ($desktopProcess.Count -eq 0) {
            Write-Host "[STARTING] NapCatQQ Desktop..." -ForegroundColor Yellow
            Start-Process -FilePath $napCatDesktop -WindowStyle Minimized
        }
        else {
            Write-Host "[RUNNING] NapCatQQ Desktop" -ForegroundColor Green
        }
        Write-Host "[ACCOUNT] Expected bot QQ: $qqId" -ForegroundColor Cyan
    }
    else {
        if ($napCatWebReady) {
            Write-Host "[RUNNING] Legacy NapCat WebUI: 6099" -ForegroundColor Green
        }
        else {
            $existingNapCat = @(Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue)
            if ($existingNapCat.Count -gt 0) {
                throw "A stale NapCat process exists while port 6099 is unavailable. Stop stale NapCat/QQ processes and retry."
            }

            Write-Host "[STARTING] Legacy NapCat with QQ quick login..." -ForegroundColor Yellow
            $napCatArguments = ('"{0}" "{1}" {2}' -f $qqExecutable, $napCatHook, $qqId)
            Start-Process `
                -FilePath $napCatBoot `
                -ArgumentList $napCatArguments `
                -WorkingDirectory $napCatRoot `
                -WindowStyle Hidden
        }
    }

    if ($oneBotReady -or (Wait-LocalPort -Port 3000 -TimeoutSeconds 90)) {
        Write-Host "[READY] OneBot HTTP: 3000" -ForegroundColor Green
    }
    else {
        Write-Host "[SETUP REQUIRED] OneBot 3000 is not ready. Add/login QQ $qqId in NapCatQQ Desktop and enable HTTP Server port 3000." -ForegroundColor Yellow
    }

    if (-not (Test-Path -LiteralPath $lifecycleScript)) {
        throw "Bot lifecycle supervisor was not found: $lifecycleScript"
    }

    # Always keep one lifecycle supervisor alive. It adopts an already-running
    # Uvicorn process, or starts one when OneBot is ready, and stops it when
    # NapCat/OneBot is closed.
    $lifecycleRunning = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @("powershell.exe", "pwsh.exe") -and
            $_.CommandLine -and
            $_.CommandLine -match '(?i)run_bot\.ps1'
        })
    if ($lifecycleRunning.Count -gt 0) {
        Write-Host "[RUNNING] Bot lifecycle supervisor" -ForegroundColor Green
    }
    else {
        Write-Host "[STARTING] Bot lifecycle supervisor..." -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $lifecycleScript) `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden
        Start-Sleep -Seconds 2
    }

    if (Test-LocalPort -Port 8000) {
        Write-Host "[RUNNING] Bot API: 8000" -ForegroundColor Green
    }
    elseif (Test-LocalPort -Port 3000) {
        if (Wait-LocalPort -Port 8000 -TimeoutSeconds 30) {
            Write-Host "[STARTED] Bot API: 8000" -ForegroundColor Green
        }
        else {
            throw "Bot lifecycle supervisor did not start the API within 30 seconds. Check $botErrorLog"
        }
    }
    else {
        Write-Host "[WAITING] NapCat/OneBot 3000 is unavailable; the supervisor will start Bot API 8000 when NapCat is opened." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Startup complete. Re-running this script will not duplicate NapCat." -ForegroundColor Cyan
    if (Test-LocalPort -Port 6099) {
        Write-Host "NapCat WebUI: http://127.0.0.1:6099"
    }
    elseif ($useNapCatDesktop) {
        Write-Host "NapCat management: NapCatQQ Desktop"
    }
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
