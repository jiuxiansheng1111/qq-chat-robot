#requires -Version 5.1
[CmdletBinding()]
param([switch]$Stop)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'astrbot_windows_common.ps1')
. (Join-Path $PSScriptRoot 'astrbot_process.ps1')

$dashboardPort = 6185
$bridgePort = 3010
$astrbotVersion = '4.28.2'

function Get-ManagedPythonPath {
    param([Parameter(Mandatory = $true)]$Layout)
    $candidates = @(
        Get-ChildItem -LiteralPath $Layout.UvPythonDir -Directory -ErrorAction Stop |
            Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 } |
            ForEach-Object { Join-Path $_.FullName 'python.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    )
    if ($candidates.Count -ne 1) {
        throw "AstrBot 独立 Python 路径数量异常（$($candidates.Count)），已停止。"
    }
    return [IO.Path]::GetFullPath([string]$candidates[0])
}

function Assert-StatePaths {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath
    )
    $expectedPaths = @{
        'launcher' = [IO.Path]::GetFullPath($LauncherPath)
        'tool-python' = [IO.Path]::GetFullPath($ToolPythonPath)
        'managed-python' = [IO.Path]::GetFullPath($ManagedPythonPath)
    }
    if ($State.Phase -notin @('starting', 'running')) {
        throw 'AstrBot 运行记录状态无效；未处理任何进程。'
    }
    $identities = @($State.Processes)
    if ($State.Phase -ceq 'starting' -and $identities.Count -lt 1) {
        throw 'AstrBot 启动记录为空；未处理任何进程。'
    }
    if ($State.Phase -ceq 'running' -and $identities.Count -ne 3) {
        throw 'AstrBot 运行记录没有完整的三层进程链；未处理任何进程。'
    }
    if ($identities.Count -gt 3) {
        throw 'AstrBot 运行记录包含额外进程；未处理任何进程。'
    }
    foreach ($identity in $identities) {
        $expected = $expectedPaths[[string]$identity.Kind]
        if (-not $expected -or -not ([IO.Path]::GetFullPath([string]$identity.ExecutablePath)).Equals(
                $expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'AstrBot 运行记录含有不属于 data/astrbot/runtime 的程序路径；未处理任何进程。'
        }
    }
    $launcher = @($identities | Where-Object { $_.Kind -ceq 'launcher' })
    $tool = @($identities | Where-Object { $_.Kind -ceq 'tool-python' })
    $native = @($identities | Where-Object { $_.Kind -ceq 'managed-python' })
    if ($launcher.Count -ne 1 -or
        [int]$State.Launcher.ProcessId -ne [int]$launcher[0].ProcessId -or
        [int]$State.Launcher.ParentProcessId -ne [int]$launcher[0].ParentProcessId -or
        $State.Launcher.CommandHash -cne $launcher[0].CommandHash -or
        $State.Launcher.CreatedUtc -cne $launcher[0].CreatedUtc -or
        $State.Launcher.ExecutablePath -cne $launcher[0].ExecutablePath) {
        throw 'AstrBot 启动器身份与运行记录不一致；未处理任何进程。'
    }
    if ($tool.Count -gt 1 -or $native.Count -gt 1 -or
        ($identities.Count -ge 2 -and $tool.Count -ne 1) -or
        ($identities.Count -eq 3 -and $native.Count -ne 1) -or
        ($identities.Count -lt 3 -and $native.Count -gt 0)) {
        throw 'AstrBot 运行记录包含重复或不完整的进程链；未处理任何进程。'
    }
    if ($tool.Count -eq 1 -and [int]$tool[0].ParentProcessId -ne [int]$launcher[0].ProcessId) {
        throw 'AstrBot 中间进程的父 PID 与运行记录不符；未处理任何进程。'
    }
    if ($native.Count -eq 1 -and [int]$native[0].ParentProcessId -ne [int]$tool[0].ProcessId) {
        throw 'AstrBot 监听进程的父 PID 与运行记录不符；未处理任何进程。'
    }
    if ($State.Phase -ceq 'running') {
        if ($tool.Count -ne 1 -or $native.Count -ne 1 -or
            [int]$State.ListenerProcessId -ne [int]$native[0].ProcessId) {
            throw 'AstrBot 三层进程链的 PID 关系不一致；未处理任何进程。'
        }
    }
}

function Get-StateIdentity {
    param([Parameter(Mandatory = $true)]$State, [Parameter(Mandatory = $true)][string]$Kind)
    return @($State.Processes | Where-Object { $_.Kind -ceq $Kind }) | Select-Object -First 1
}

function Test-StateListener {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][int]$ListenerProcessId,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $native = Get-StateIdentity -State $State -Kind 'managed-python'
    $tool = Get-StateIdentity -State $State -Kind 'tool-python'
    if (-not $native -or -not $tool -or
        [int]$State.ListenerProcessId -ne $ListenerProcessId -or
        [int]$native.ProcessId -ne $ListenerProcessId -or
        [int]$native.ParentProcessId -ne [int]$tool.ProcessId) { return $false }
    if (-not (Test-AstrbotRecordedIdentity -Identity $native -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) { return $false }

    # 只要仍存活的中间进程也必须与记录完全吻合；已经退出的祖先进程不影响其子进程归属。
    $toolInfo = Get-AstrbotProcessInfo -ProcessId ([int]$tool.ProcessId)
    if ($toolInfo) {
        if (-not (Test-AstrbotRecordedIdentity -Identity $tool -LauncherPath $LauncherPath `
                -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) { return $false }
    }
    $launcher = Get-StateIdentity -State $State -Kind 'launcher'
    $launcherInfo = Get-AstrbotProcessInfo -ProcessId ([int]$launcher.ProcessId)
    if ($launcherInfo -and (Get-AstrbotProcessKind -ProcessInfo $launcherInfo -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) {
        if (-not (Test-AstrbotRecordedIdentity -Identity $launcher -LauncherPath $LauncherPath `
                -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) { return $false }
    }
    return $true
}

function Get-OwnedChildren {
    param(
        [Parameter(Mandatory = $true)]$LauncherIdentity,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $toolInfo = @($all | Where-Object {
        [int]$_.ParentProcessId -eq [int]$LauncherIdentity.ProcessId
    } | ForEach-Object {
        New-AstrbotProcessIdentity -ProcessInfo $_ -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    } | Where-Object { $_ -and $_.Kind -ceq 'tool-python' }) | Select-Object -First 1
    if (-not $toolInfo) { return @($LauncherIdentity) }
    $nativeInfo = @($all | Where-Object {
        [int]$_.ParentProcessId -eq [int]$toolInfo.ProcessId
    } | ForEach-Object {
        New-AstrbotProcessIdentity -ProcessInfo $_ -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    } | Where-Object { $_ -and $_.Kind -ceq 'managed-python' }) | Select-Object -First 1
    if (-not $nativeInfo) { return @($LauncherIdentity, $toolInfo) }
    return @($LauncherIdentity, $toolInfo, $nativeInfo)
}

function Get-ValidatedListener {
    param([Parameter(Mandatory = $true)][int]$Port)
    $connections = @(Get-AstrbotListeners -Port $Port)
    if ($connections.Count -eq 0) { return $null }
    $badAddress = @($connections | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') })
    if ($badAddress.Count -gt 0) {
        throw "端口 $Port 不是只监听本机回环地址；未处理监听进程。"
    }
    $owners = @($connections | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
    if ($owners.Count -ne 1) { throw "端口 $Port 有多个监听进程；未处理任何进程。" }
    return [pscustomobject]@{ ProcessId = $owners[0]; Connections = $connections }
}

function Get-LiveRecordedIdentities {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    return @($State.Processes | Where-Object {
        Test-AstrbotRecordedIdentity -Identity $_ -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    })
}

function Remove-RunState {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Layout)
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    if (Test-Path -LiteralPath $Path -PathType Leaf) { Remove-Item -LiteralPath $Path -Force }
}

function Stop-AstrbotRecordedProcess {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $processId = [int]$Identity.ProcessId
    if (-not (Get-AstrbotProcessInfo -ProcessId $processId)) { return }
    if (-not (Test-AstrbotRecordedIdentity -Identity $Identity -LauncherPath $LauncherPath `
            -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) {
        throw "PID $processId 的启动时间或命令已变化；未停止该进程。"
    }
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        $processPath = [IO.Path]::GetFullPath([string]$process.Path)
        $recordedPath = [IO.Path]::GetFullPath([string]$Identity.ExecutablePath)
        $recordedStart = [datetime]::Parse([string]$Identity.CreatedUtc).ToUniversalTime()
        $processStart = $process.StartTime.ToUniversalTime()
        if (-not $processPath.Equals($recordedPath, [StringComparison]::OrdinalIgnoreCase) -or
            [math]::Abs(($recordedStart - $processStart).TotalSeconds) -gt 2) {
            throw "PID $processId 已不是运行记录中的 AstrBot 进程；未停止该进程。"
        }
        # Get-Process 已打开并校验目标句柄，再通过句柄停止，避免 PID 复用误伤。
        $process.Kill()
    } catch {
        if (-not (Get-AstrbotProcessInfo -ProcessId $processId)) { return }
        throw
    }
}

try {
    $layout = Get-AstrbotLayout
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

    $listener = if ($Stop) { $null } else { Get-ValidatedListener -Port $dashboardPort }
    if ($Stop) {
        if (-not $state) { throw '没有本次启动器的进程记录；不会停止任何进程。' }
        $live = @(Get-LiveRecordedIdentities -State $state -LauncherPath $launcherPath `
            -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port $dashboardPort)
        if ($live.Count -eq 0) {
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
        exit 0
    }

    if ($listener) {
        if (-not $state -or $state.Phase -cne 'running' -or
            -not (Test-StateListener -State $state -ListenerProcessId $listener.ProcessId `
                -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
                -ManagedPythonPath $managedPythonPath -Port $dashboardPort)) {
            throw "端口 $dashboardPort 有未被本次启动记录认领的监听进程（PID $($listener.ProcessId)）；未作改动。"
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
