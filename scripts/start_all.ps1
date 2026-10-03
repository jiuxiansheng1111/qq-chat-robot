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
        throw "Configured SoVITS weights not found: $weightsPath"
    }
    $encodedPath = [Uri]::EscapeDataString($weightsPath)
    $endpoint = "http://$VoiceHost`:$Port/set_sovits_weights?weights_path=$encodedPath"
    try {
        $response = Invoke-RestMethod -Uri $endpoint -Method Get -TimeoutSec 180 -ErrorAction Stop
        Write-Host "[VOICE] Loaded SoVITS weights: $weightsPath" -ForegroundColor Green
    }
    catch {
        throw "Failed to synchronize configured SoVITS weights: $weightsPath ($($_.Exception.Message))"
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
    # Routed mode owns the selected model inside the bot's per-request lock.
    # Never overwrite it with the legacy single startup weight, regardless of
    # whether this sidecar is already listening or is started below.
    $routedWeightsConfigured =
        ([string]$Settings["VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON"]).Trim() -or
        ([string]$Settings["VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON"]).Trim()

    if (Test-LocalPort -Port $voicePort) {
        Write-Host "[VOICE] GPT-SoVITS already listening on $voicePort." -ForegroundColor Green
        # In routed mode the bot owns model changes and serializes them with
        # synthesis. A profile map is routed too; applying the legacy startup
        # weight here could overwrite a Yoshino/Mako request in flight.
        if (-not $routedWeightsConfigured) {
            Apply-GptSovitsWeights -Settings $Settings -VoiceHost $voiceHost -Port $voicePort
        }
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

    $targetPortPattern = '(?i)(?:^|\s)-p\s+' + [regex]::Escape([string]$voicePort) + '(?:\s|$)'
    $explicitPortPattern = '(?i)(?:^|\s)-p\s+\d+(?:\s|$)'
    $existing = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "^(python|pythonw)(\.exe)?$" -and
            $_.CommandLine -and
            $_.CommandLine -match '(?i)api_v2\.py' -and
            (
                $_.CommandLine -match $targetPortPattern -or
                # api_v2 defaults to 9880 when no -p argument is supplied.
                $_.CommandLine -notmatch $explicitPortPattern
            )
        })
    if ($existing.Count -gt 0) {
        Write-Host "[VOICE] GPT-SoVITS for port $voicePort exists; waiting for it..." -ForegroundColor Cyan
    }
    else {
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        $voiceOutLog = Join-Path $logRoot "gpt_sovits.out.log"
        $voiceErrorLog = Join-Path $logRoot "gpt_sovits.error.log"
        # fast-langdetect's Windows model loader cannot reopen temporary files
        # when the user profile path contains non-ASCII characters.  Give only
        # the sidecar an ASCII temp path; do not alter the machine-wide setting.
        $voiceTempRoot = Join-Path $env:PUBLIC "GPTSoVITS_temp"
        if ($voiceTempRoot -match '[^\x00-\x7F]') {
            throw "GPT-SoVITS requires an ASCII temporary directory: $voiceTempRoot"
        }
        New-Item -ItemType Directory -Path $voiceTempRoot -Force | Out-Null
        Write-Host "[VOICE] Starting GPT-SoVITS on $voiceHost`:$voicePort..." -ForegroundColor Yellow
        $previousTemp = $env:TEMP
        $previousTmp = $env:TMP
        try {
            $env:TEMP = $voiceTempRoot
            $env:TMP = $voiceTempRoot
            Start-Process `
                -FilePath $gptPython `
                -ArgumentList @("api_v2.py", "-a", $voiceHost, "-p", [string]$voicePort, "-c", $ttsConfig) `
                -WorkingDirectory $gptRoot `
                -RedirectStandardOutput $voiceOutLog `
                -RedirectStandardError $voiceErrorLog `
                -WindowStyle Hidden
        }
        finally {
            $env:TEMP = $previousTemp
            $env:TMP = $previousTmp
        }
    }

    if (Wait-LocalPort -Port $voicePort -TimeoutSeconds 90) {
        Write-Host "[VOICE] GPT-SoVITS ready: http://$voiceHost`:$voicePort/tts" -ForegroundColor Green
        if (-not $routedWeightsConfigured) {
            Apply-GptSovitsWeights -Settings $Settings -VoiceHost $voiceHost -Port $voicePort
        }
        else {
            Write-Host "[VOICE] Routed weights configured; initial model will be selected by the bot." -ForegroundColor Cyan
        }
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
    $neteaseMemberEnabled = ([string]$settings["NETEASE_MEMBER_ENABLED"]).Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
    if ($neteaseMemberEnabled) {
        $neteasePort = 3010
        $neteaseBridgeUrl = ([string]$settings["NETEASE_MEMBER_BRIDGE_URL"]).Trim()
        $neteaseUrlValid = $true
        if ($neteaseBridgeUrl) {
            $parsedNeteaseUrl = $null
            $neteaseUrlValid = [Uri]::TryCreate($neteaseBridgeUrl, [UriKind]::Absolute, [ref]$parsedNeteaseUrl) -and
                $parsedNeteaseUrl.Scheme -eq "http" -and
                $parsedNeteaseUrl.Host.ToLowerInvariant() -in @("127.0.0.1", "localhost") -and
                -not $parsedNeteaseUrl.UserInfo -and
                $parsedNeteaseUrl.AbsolutePath -eq "/" -and
                -not $parsedNeteaseUrl.Query -and
                -not $parsedNeteaseUrl.Fragment -and
                $parsedNeteaseUrl.Port -ge 1024
            if ($neteaseUrlValid) {
                $neteasePort = $parsedNeteaseUrl.Port
            }
        }

        if (-not $neteaseUrlValid) {
            Write-Host "[NETEASE] NETEASE_MEMBER_ENABLED is true, but NETEASE_MEMBER_BRIDGE_URL must use http://localhost or http://127.0.0.1. The member bridge was not started." -ForegroundColor Yellow
        }
        else {
            $neteaseStartScript = Join-Path $PSScriptRoot "start_netease_member.ps1"
            try {
                if (-not (Test-Path -LiteralPath $neteaseStartScript -PathType Leaf)) {
                    throw "网易云会员启动脚本缺失。"
                }
                & $neteaseStartScript -Port $neteasePort
            }
            catch {
                Write-Host "[NETEASE] 会员桥接服务启动失败：$($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

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
