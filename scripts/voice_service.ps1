# Shared voice engine lifecycle. Import only; never starts QQ.
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


function ConvertTo-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append([char]34)
    $backslashCount = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashCount++
            continue
        }
        if ($character -eq [char]34) {
            [void]$builder.Append([string]::new([char]92, (2 * $backslashCount) + 1))
            [void]$builder.Append([char]34)
            $backslashCount = 0
            continue
        }
        if ($backslashCount -gt 0) {
            [void]$builder.Append([string]::new([char]92, $backslashCount))
            $backslashCount = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashCount -gt 0) {
        [void]$builder.Append([string]::new([char]92, 2 * $backslashCount))
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
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


function Start-GptSovitsCore {
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
        # start_all 启动生命周期监控时，Python 应用会继承此值。
        $env:VOICE_API_URL = "http://127.0.0.1:$voicePort"
    }
    # 路由模式下，机器人会在每个请求的锁内选择模型。
    # 无论旁路服务是否已在运行，都不要用旧的
    # 单一启动权重覆盖路由模式的选择。
    $routedWeightsConfigured =
        ([string]$Settings["VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON"]).Trim() -or
        ([string]$Settings["VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON"]).Trim()

    if (Test-LocalPort -Port $voicePort) {
        Assert-VoiceApi -VoiceHost $voiceHost -Port $voicePort
        Write-Host "[VOICE] GPT-SoVITS already listening on $voicePort." -ForegroundColor Green
        # 路由模式下由机器人切换模型，并与语音合成串行执行。
        # 配置了角色映射时也会走路由；此处应用旧启动权重，
        # 可能覆盖正在处理的 Yoshino/Mako 请求。
        if (-not $routedWeightsConfigured) {
            Apply-GptSovitsWeights -Settings $Settings -VoiceHost $voiceHost -Port $voicePort
        }
        return $true
    }

    $gpuProbe = Join-Path $PSScriptRoot 'check_gpu_busy.py'
    if (Test-Path -LiteralPath $gpuProbe -PathType Leaf) {
        $gpuState = (& $pythonPath $gpuProbe | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Could not check the shared GPU reservation.' }
        if ($gpuState -eq 'busy') {
            Write-Host '[VOICE] Singing/training owns the GPU; speech will resume after it finishes.' -ForegroundColor Cyan
            return $false
        }
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
    $launcherScript = Join-Path $PSScriptRoot "run_gpt_sovits.py"
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
    if (-not (Test-Path -LiteralPath $launcherScript -PathType Leaf)) {
        Write-Host "[VOICE] GPT-SoVITS memory launcher not found: $launcherScript" -ForegroundColor Yellow
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
            # GPT_SOVITS_PYTHON 与 GPT_SOVITS_ROOT 一样，相对机器人项目根目录。
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

    $targetPortPattern = '(?i)(?:^|\s)"?-p"?\s+"?' + [regex]::Escape([string]$voicePort) + '"?(?:\s|$)'
    $explicitPortPattern = '(?i)(?:^|\s)"?-p"?\s+"?\d+"?(?:\s|$)'
    $existing = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "^(python|pythonw)(\.exe)?$" -and
            $_.CommandLine -and
            ($_.CommandLine -like ('*' + $apiScript + '*')) -and
            (
                $_.CommandLine -match $targetPortPattern -or
                # 未提供 -p 参数时，api_v2 默认使用 9880。
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
        # fast-langdetect 在 Windows 上的模型加载器无法重新打开临时文件，
        # 如果用户目录包含非 ASCII 字符。只给旁路服务
        # 设置 ASCII 临时目录，不改系统级设置。
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
            $voiceArgumentValues = @(
                $launcherScript,
                "--gpt-root", $gptRoot,
                "--api-script", $apiScript,
                "--", "-a", $voiceHost, "-p", [string]$voicePort, "-c", $ttsConfig
            )
            $voiceArgumentList = ($voiceArgumentValues | ForEach-Object {
                ConvertTo-NativeArgument -Value ([string]$_)
            }) -join " "
            Start-Process `
                -FilePath $gptPython `
                -ArgumentList $voiceArgumentList `
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

    if (Wait-LocalPort -Port $voicePort -TimeoutSeconds 120) {
        Assert-VoiceApi -VoiceHost $voiceHost -Port $voicePort
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


function Assert-VoiceApi {
    param([string]$VoiceHost, [int]$Port)
    $hostPart = if ($VoiceHost -eq '::1') { '[::1]' } else { $VoiceHost }
    $schema = Invoke-RestMethod -Uri "http://${hostPart}:$Port/openapi.json" -TimeoutSec 5
    if (-not $schema.paths.'/tts' -or -not $schema.paths.'/set_sovits_weights') {
        throw "Port $Port does not expose the expected GPT-SoVITS API; no weights changed."
    }
}

function Start-GptSovitsSidecar {
    param([Parameter(Mandatory = $true)][hashtable]$Settings)
    # One startup gate for the desktop, QQ and restoration after a cover.
    $voiceHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $voiceHash = [BitConverter]::ToString($voiceHasher.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($projectRoot).ToLowerInvariant())
        )).Replace('-', '')
    } finally { $voiceHasher.Dispose() }
    $voiceMutex = [Threading.Mutex]::new($false, "Local\DragonMaid.Voice.$voiceHash")
    $voiceHeld = $false
    try {
        try { $voiceHeld = $voiceMutex.WaitOne(150000) }
        catch [Threading.AbandonedMutexException] { $voiceHeld = $true }
        if (-not $voiceHeld) { throw 'Voice startup is still busy; please retry shortly.' }
        return (Start-GptSovitsCore -Settings $Settings)
    } finally {
        if ($voiceHeld) { $voiceMutex.ReleaseMutex() }
        $voiceMutex.Dispose()
    }
}
