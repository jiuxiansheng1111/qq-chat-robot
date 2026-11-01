[CmdletBinding()]
param([string]$NapCatConfigDirectory = "", [switch]$VoiceOnly)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$startupHasher = [Security.Cryptography.SHA256]::Create()
try {
    $startupMutexHash = [BitConverter]::ToString($startupHasher.ComputeHash(
        [Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($projectRoot).ToLowerInvariant())
    )).Replace('-', '')
}
finally {
    $startupHasher.Dispose()
}
$script:startupMutex = [Threading.Mutex]::new($false, "Local\QQChatRobot.StartAll.$startupMutexHash")
$script:startupMutexHeld = $false
try {
    $script:startupMutexHeld = $script:startupMutex.WaitOne(0)
}
catch [Threading.AbandonedMutexException] {
    $script:startupMutexHeld = $true
}
function Release-StartAllMutex {
    if ($script:startupMutex) {
        try {
            if ($script:startupMutexHeld) {
                $script:startupMutex.ReleaseMutex()
                $script:startupMutexHeld = $false
            }
        }
        finally {
            $script:startupMutex.Dispose()
            $script:startupMutex = $null
        }
    }
}
if (-not $script:startupMutexHeld) {
    Write-Host '[启动] 已有启动或训练维护任务正在进行，本次不重复启动。' -ForegroundColor Cyan
    Release-StartAllMutex
    exit 0
}
$desktopRoot = Split-Path -Parent $projectRoot
$envPath = Join-Path $projectRoot ".env"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$bootstrapScript = Join-Path $PSScriptRoot "bootstrap_windows.ps1"
$lifecycleScript = Join-Path $PSScriptRoot "run_bot.ps1"
$logRoot = Join-Path $projectRoot "data\logs"
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



function Get-LoopbackListenerOwnerId {
    param([Parameter(Mandatory = $true)][int]$Port)

    if (-not (Get-Command -Name Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw "无法核对端口 $Port 的监听进程（Get-NetTCPConnection 不可用）。"
    }
    try {
        $owners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
            Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1') } |
            Select-Object -ExpandProperty OwningProcess -Unique)
    }
    catch {
        throw "无法核对端口 $Port 的监听进程：$($_.Exception.Message)"
    }
    if ($owners.Count -ne 1) {
        throw "端口 $Port 没有唯一的本机监听进程，详情请查看启动日志。"
    }
    return [int]$owners[0]
}




. (Join-Path $PSScriptRoot 'voice_service.ps1')

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
    if ($NapCatConfigDirectory) {
        $arguments += '-NapCatConfigDirectory'
        $arguments += ('"{0}"' -f (Join-Path ([IO.Path]::GetFullPath($NapCatConfigDirectory)) '.'))
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
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
    if ($VoiceOnly) {
        $settings = Get-DotEnvValues -Path $envPath
        if (-not (Start-GptSovitsSidecar -Settings $settings)) {
            Release-StartAllMutex
            exit 1
        }
        Release-StartAllMutex
        exit 0
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

    $napCatWebReady = Test-LocalPort -Port 6099
    $oneBotReady = Test-LocalPort -Port 3000
    $botReady = Test-LocalPort -Port 8000

    if (-not $useNapCatDesktop -and -not $napCatWebReady -and -not (Test-IsAdministrator)) {
        Write-Host "Administrator access is required. Requesting UAC confirmation..." -ForegroundColor Yellow
        Release-StartAllMutex
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
        Write-Host "[需登录] OneBot HTTP 3000 尚未就绪。请在 NapCatQQ Desktop 添加并登录机器人 QQ 账号，并启用 HTTP Server 端口 3000。" -ForegroundColor Yellow
    }

    . (Join-Path $PSScriptRoot 'bot_runtime_mode.ps1')
    if ((Get-QQChatRobotRuntime -ProjectRoot $projectRoot) -eq 'astrbot') {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run_astrbot.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'AstrBot 启动失败，请检查 data/astrbot/logs。' }
        if (-not (Wait-LocalPort -Port 6185 -TimeoutSeconds 5)) {
            throw 'AstrBot 面板端口 6185 未就绪，请检查 data/astrbot/logs。'
        }
        if (-not (Wait-LocalPort -Port 6199 -TimeoutSeconds 30)) {
            throw 'AstrBot OneBot 反向 WS 端口 6199 未就绪，请检查 data/astrbot/logs。'
        }
        $dashboardOwner = Get-LoopbackListenerOwnerId -Port 6185
        $reverseWsOwner = Get-LoopbackListenerOwnerId -Port 6199
        if ($dashboardOwner -ne $reverseWsOwner) {
            throw 'AstrBot 面板 6185 与 OneBot 反向 WS 6199 不属于同一进程，请检查 data/astrbot/logs。'
        }
        Write-Host '[READY] AstrBot 面板已就绪：http://127.0.0.1:6185' -ForegroundColor Green
        Write-Host '[READY] AstrBot OneBot 反向 WS 已就绪：127.0.0.1:6199' -ForegroundColor Green
        . (Join-Path $PSScriptRoot 'astrbot_windows_common.ps1')
        . (Join-Path $PSScriptRoot 'astrbot_process.ps1')
        . (Join-Path $PSScriptRoot 'astrbot_lifecycle.ps1')
        Start-AstrbotSupervisor -ProjectRoot $projectRoot
        Write-Host '[READY] AstrBot 自动恢复监控已启动。' -ForegroundColor Green

        # QQ 登录后，检查它是否还在向旧端口上报消息。
        $napcatConnectionScript = Join-Path $PSScriptRoot 'connect_astrbot_qq.py'
        if ($NapCatConfigDirectory) {
            $napcatConfigDir = $NapCatConfigDirectory
        }
        elseif ($useNapCatDesktop) {
            $napcatConfigDir = Join-Path $env:ProgramData 'NapCatQQ Desktop\components\NapCatQQ\config'
        }
        else {
            $napcatConfigDir = ''
        }
        $napcatWebuiConfig = if ($napcatConfigDir) { Join-Path $napcatConfigDir 'webui.json' } else { '' }
        if ($napcatWebuiConfig -and (Test-LocalPort -Port 3000) -and (Test-LocalPort -Port 6099) -and
            (Test-Path -LiteralPath $napcatWebuiConfig -PathType Leaf)) {
            try {
                $connectionArguments = @('--project-root', $projectRoot, '--napcat-config-dir', $napcatConfigDir, '--apply')
                if ($useNapCatDesktop) {
                    $desktopConfigRoot = ([IO.DirectoryInfo]$napcatConfigDir).Parent.Parent.Parent.FullName
                    $desktopBotConfig = Join-Path $desktopConfigRoot 'config\bot.json'
                    if (Test-Path -LiteralPath $desktopBotConfig -PathType Leaf) {
                        $connectionArguments += @('--napcat-desktop-config', $desktopBotConfig)
                    }
                    else {
                        Write-Host '[QQ] 未找到桌面版账号配置，本次仅恢复正在运行的连接。' -ForegroundColor Yellow
                    }
                }
                & $pythonPath $napcatConnectionScript @connectionArguments
                if ($LASTEXITCODE -ne 0) { throw 'QQ 连接配置未通过校验。' }
                Write-Host '[READY] QQ 消息上报配置已确认。' -ForegroundColor Green
            }
            catch {
                Write-Host '[QQ] 自动恢复连接失败，请查看上方原因；AstrBot 已启动。' -ForegroundColor Yellow
            }
        }
        else {
            if (-not $napcatConfigDir) {
                Write-Host '[QQ] 旧版 NapCat 需用 -NapCatConfigDirectory 指定配置目录后再恢复连接。' -ForegroundColor Yellow
            }
            else {
                Write-Host '[QQ] 等待 NapCat 登录后，重新点击启动入口可恢复消息连接。' -ForegroundColor Yellow
            }
        }

        try {
            $voiceSidecarReady = Start-GptSovitsSidecar -Settings $settings
        }
        catch {
            Write-Host "[语音] GPT-SoVITS 启动失败：$($_.Exception.Message)。AstrBot 已正常启动。日志：$logRoot\gpt_sovits.error.log" -ForegroundColor Yellow
        }
        Release-StartAllMutex
        exit 0
    }

    # 先启动 QQ 和聊天机器人，再等待首次加载较慢的语音模型。
    $voiceSidecarReady = Start-GptSovitsSidecar -Settings $settings

    if (-not (Test-Path -LiteralPath $lifecycleScript)) {
        throw "Bot lifecycle supervisor was not found: $lifecycleScript"
    }

    # 始终保留一个生命周期监控进程。它会接管已有的 Uvicorn，
    # 或在 OneBot 就绪时启动 Uvicorn，并在
    # NapCat/OneBot 关闭时停止它。
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
    Write-Host ("[启动失败] {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host "日志目录：$logRoot；AstrBot 日志目录：$(Join-Path $projectRoot 'data\astrbot\logs')"
    Release-StartAllMutex
    exit 1
}
finally {
    Release-StartAllMutex
}
