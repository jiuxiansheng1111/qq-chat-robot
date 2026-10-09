#requires -Version 5.1
[CmdletBinding()]
param([switch]$Stop, [switch]$Restart, [switch]$Recovery)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'astrbot_windows_common.ps1')
. (Join-Path $PSScriptRoot 'astrbot_process.ps1')
. (Join-Path $PSScriptRoot 'astrbot_lifecycle.ps1')

$dashboardPort = 6185
$bridgePort = 3010
$astrbotVersion = '4.28.2'


try {
    if ($Stop -and $Restart) { throw '-Stop and -Restart cannot be combined.' }
    $layout = Get-AstrbotLayout
    $lifecycleMutex = New-AstrbotMutex -ProjectRoot $layout.ProjectRoot -Purpose 'Lifecycle'
    $lifecycleMutexHeld = Enter-AstrbotMutex -Mutex $lifecycleMutex -TimeoutMilliseconds 120000
    if (-not $lifecycleMutexHeld) { throw 'AstrBot lifecycle operation is already running.' }
    if ($Recovery -and (Test-AstrbotManualStop -Layout $layout)) {
        Write-Host 'AstrBot was intentionally stopped; automatic recovery is paused.'
        exit 0
    }
    $launcherPath = [IO.Path]::GetFullPath((Join-Path $layout.UvToolBin 'astrbot.exe'))
    $toolPythonPath = [IO.Path]::GetFullPath((Join-Path $layout.UvToolDir 'astrbot\Scripts\python.exe'))
    $managedPythonPath = Get-ManagedPythonPath -Layout $layout
    $manifestPath = Join-Path $layout.Root 'install-state.json'
    $statePath = Join-Path $layout.Root 'astrbot-owned-run.json'
    $legacyStatePath = Join-Path $layout.Root 'astrbot-listen.pid'
    $configureScript = Join-Path $layout.ProjectRoot 'scripts\configure_astrbot.py'
    $bridgeScript = Join-Path $layout.ProjectRoot 'scripts\start_netease_member.ps1'
    $stdoutPath = Join-Path $layout.Logs 'astrbot-service.stdout.log'
    $stderrPath = Join-Path $layout.Logs 'astrbot-service.stderr.log'

    foreach ($path in @($layout.Instance, $launcherPath, $toolPythonPath, $managedPythonPath,
            $manifestPath, $layout.Logs, $statePath, $legacyStatePath)) {
        Assert-AstrbotManagedPath -Layout $layout -Path $path
    }
    foreach ($path in @($configureScript, $bridgeScript)) {
        Assert-AstrbotProjectPath -Layout $layout -Path $path
    }
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $toolPythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $managedPythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $layout.Instance 'data') -PathType Container)) {
        throw 'AstrBot 尚未安装。请先运行 scripts/install_astrbot.ps1。'
    }
    if (Test-Path -LiteralPath $legacyStatePath -PathType Leaf) {
        throw '发现旧版 AstrBot PID 记录；为避免误停进程，请先由维护者核对后清理旧记录。'
    }

    $state = Read-AstrbotRunState -Path $statePath -Layout $layout `
        -AstrBotVersion $astrbotVersion -Port $dashboardPort
    if ($state) {
        Assert-StatePaths -State $state -LauncherPath $launcherPath `
            -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath
    }

    $listener = if ($Stop -or $Restart) { $null } else { Get-ValidatedListener -Port $dashboardPort }
    if ($Stop -or $Restart) {
        if (-not $state -and $Stop) {
            Set-AstrbotManualStop -Layout $layout
            Write-Host 'AstrBot automatic recovery is paused; no recorded process to stop.'
            exit 0
        }
        if ($state) {
            $live = @(Get-LiveRecordedIdentities -State $state -LauncherPath $launcherPath `
                -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort)
            if ($Stop) { Set-AstrbotManualStop -Layout $layout }
            if ($live.Count -eq 0 -and $Stop) {
                Remove-RunState -Path $statePath -Layout $layout
                Write-Host '记录中的 AstrBot 进程已退出；清理了本次 PID 记录。'
                exit 0
            }

            # 先全量核验，再按子进程到启动器的顺序逐个停止。
            $rank = @{ 'launcher' = 0; 'tool-python' = 1; 'managed-python' = 2 }
            $ordered = @($live | Sort-Object { $rank[[string]$_.Kind] } -Descending)
            foreach ($identity in $ordered) {
                Stop-AstrbotRecordedProcess -Identity $identity -LauncherPath $launcherPath `
                    -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort
            }
            $deadline = (Get-Date).AddSeconds(15)
            do {
                Start-Sleep -Milliseconds 250
                $remaining = @(Get-LiveRecordedIdentities -State $state -LauncherPath $launcherPath `
                    -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort)
            } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
            if ($remaining.Count -gt 0) { throw 'AstrBot 进程尚未全部退出；保留运行记录。' }
            Remove-RunState -Path $statePath -Layout $layout
            Write-Host 'AstrBot 已停止。本操作只处理了运行记录中的 AstrBot 进程。'
        }
        if ($Stop) { exit 0 }
        $state = $null
        $listener = Get-ValidatedListener -Port $dashboardPort
    }

    if ($listener) {
        if (-not $state -or $state.Phase -cne 'running' -or
            -not (Test-StateListener -State $state -ListenerProcessId $listener.ProcessId `
                -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
                -ManagedPythonPath $managedPythonPath -Port $dashboardPort)) {
            throw "端口 $dashboardPort 有未被本次启动记录认领的监听进程（PID $($listener.ProcessId)）；未作改动。"
        }
        if (-not $Recovery) {
            Clear-AstrbotManualStop -Layout $layout
            Start-AstrbotSupervisor -ProjectRoot $layout.ProjectRoot
        }
        Write-Host "AstrBot 已在后台运行（监听 PID $($listener.ProcessId)）。"
        Write-Host "本机面板：http://127.0.0.1:$dashboardPort"
        exit 0
    }

    if ($state) {
        $live = @(Get-LiveRecordedIdentities -State $state -LauncherPath $launcherPath `
            -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort)
        if ($live.Count -gt 0) {
            throw "本次启动的 AstrBot 进程仍在运行但面板未监听（PID $($live.ProcessId -join ', ')）；不会重复启动。"
        }
        Remove-RunState -Path $statePath -Layout $layout
    }

    $untracked = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $kind = Get-AstrbotProcessKind -ProcessInfo $_ -LauncherPath $launcherPath `
            -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort
        $null -ne $kind
    })
    if ($untracked.Count -gt 0) {
        throw "发现没有本次 PID 记录的 AstrBot 进程（PID $($untracked.ProcessId -join ', ')）；不会重复启动或停止它。"
    }

    if (-not $Recovery) { Clear-AstrbotManualStop -Layout $layout }
    Preserve-AstrbotServiceLogs -Layout $layout
    Set-AstrbotUvEnvironment -Layout $layout
    $env:ASTRBOT_ROOT = $layout.Instance
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    Invoke-AstrbotLoggedCommand -FilePath $toolPythonPath -ArgumentList @($configureScript) `
        -WorkingDirectory $layout.ProjectRoot -LogPrefix 'configure-astrbot' -LogDirectory $layout.Logs | Out-Null

    # 只检查或启动 3010 桥接服务；不会打开登录页、启动旧 Uvicorn 或触碰 QQ/TTS。
    & $bridgeScript -Port $bridgePort

    $process = Start-Process -FilePath $launcherPath -ArgumentList @('run', '--port', [string]$dashboardPort) `
        -WorkingDirectory $layout.Instance -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden
    $launchDeadline = (Get-Date).AddSeconds(5)
    $launcherIdentity = $null
    do {
        $launcherInfo = Get-AstrbotProcessInfo -ProcessId $process.Id
        if ($launcherInfo) {
            $launcherIdentity = New-AstrbotProcessIdentity -ProcessInfo $launcherInfo `
                -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
                -ManagedPythonPath $managedPythonPath -Port $dashboardPort
            if ($launcherIdentity -and $launcherIdentity.ProcessId -eq $process.Id) { break }
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $launchDeadline)
    if (-not $launcherIdentity) {
        throw '无法核对本次启动的 AstrBot launcher 身份；未尝试停止任何进程。'
    }

    $state = New-AstrbotRunState -Port $dashboardPort -AstrBotVersion $astrbotVersion `
        -LauncherIdentity $launcherIdentity
    Write-AstrbotRunState -Path $statePath -Layout $layout -State $state

    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        $listener = Get-ValidatedListener -Port $dashboardPort
        if ($listener) {
            $listenerInfo = Get-AstrbotProcessInfo -ProcessId $listener.ProcessId
            $chain = if ($listenerInfo) {
                Get-AstrbotVerifiedChain -ListenerInfo $listenerInfo -LauncherPath $launcherPath `
                    -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort
            } else { $null }
            if (-not $chain) {
                throw "6185 监听进程（PID $($listener.ProcessId)）不属于本次 AstrBot 三层进程链；未停止任何进程。"
            }
            if ($chain.Launcher.ProcessId -ne $launcherIdentity.ProcessId -or
                $chain.Launcher.CreatedUtc -cne $launcherIdentity.CreatedUtc -or
                $chain.Launcher.CommandHash -cne $launcherIdentity.CommandHash) {
                throw '6185 监听进程与本次 AstrBot launcher 不匹配；未停止任何进程。'
            }
            $state.Phase = 'running'
            $state.Processes = @($chain.Launcher, $chain.ToolPython, $chain.Listener)
            $state.ListenerProcessId = [int]$chain.Listener.ProcessId
            Write-AstrbotRunState -Path $statePath -Layout $layout -State $state
            if (-not $Recovery) { Start-AstrbotSupervisor -ProjectRoot $layout.ProjectRoot }
            Write-Host "AstrBot 已在后台启动（监听 PID $($chain.Listener.ProcessId)）。"
            Write-Host "本机面板：http://127.0.0.1:$dashboardPort"
            Write-Host "日志：$stdoutPath、$stderrPath"
            exit 0
        }

        $state.Processes = @(Get-OwnedChildren -LauncherIdentity $launcherIdentity `
            -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
            -ManagedPythonPath $managedPythonPath -Port $dashboardPort)
        Write-AstrbotRunState -Path $statePath -Layout $layout -State $state
        Start-Sleep -Milliseconds 500
    }
    throw "AstrBot 45 秒内未开始监听。运行记录已保留；可用 `scripts\run_astrbot.ps1 -Stop` 停止记录中的进程。日志：$stdoutPath、$stderrPath"
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}

finally {
    if ($lifecycleMutex) {
        if ($lifecycleMutexHeld) { $lifecycleMutex.ReleaseMutex() }
        $lifecycleMutex.Dispose()
    }
}
