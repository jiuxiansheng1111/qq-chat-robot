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
    $live = @(foreach ($identity in $State.Processes) {
        if (-not (Get-AstrbotProcessInfo -ProcessId ([int]$identity.ProcessId))) { continue }
        if (-not (Test-AstrbotRecordedIdentity -Identity $identity -LauncherPath $LauncherPath `
                -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port)) {
            throw "PID $($identity.ProcessId) 仍存在但身份不匹配；保留运行记录，未处理进程。"
        }
        $identity
    })
    return $live
}

function Remove-RunState {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Layout)
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $archive = New-AstrbotArchiveDirectory -Layout $Layout
        Copy-Item -LiteralPath $Path -Destination (Join-Path $archive 'astrbot-owned-run.json')
        Remove-Item -LiteralPath $Path -Force
    }
}

function New-AstrbotMutex {
    param([string]$ProjectRoot, [string]$Purpose)
    $hash = Get-AstrbotCommandHash -CommandLine ([IO.Path]::GetFullPath($ProjectRoot).ToLowerInvariant())
    return [Threading.Mutex]::new($false, "Local\QQChatRobot.AstrBot.$Purpose.$hash")
}

function Enter-AstrbotMutex {
    param($Mutex, [int]$TimeoutMilliseconds = 0)
    try { return $Mutex.WaitOne($TimeoutMilliseconds) }
    catch [Threading.AbandonedMutexException] { return $true }
}

function Test-AstrbotManualStop {
    param($Layout)
    $path = Join-Path $Layout.Root 'manual-stop.json'
    Assert-AstrbotManagedPath -Layout $Layout -Path $path
    return Test-Path -LiteralPath $path -PathType Leaf
}

function Set-AstrbotManualStop {
    param($Layout)
    $path = Join-Path $Layout.Root 'manual-stop.json'
    Write-AstrbotRunState -Path $path -Layout $Layout -State ([pscustomobject]@{
        StoppedUtc = [datetime]::UtcNow.ToString('o')
        Reason = 'Explicit run_astrbot.ps1 -Stop'
    })
}

function Clear-AstrbotManualStop {
    param($Layout)
    $path = Join-Path $Layout.Root 'manual-stop.json'
    Assert-AstrbotManagedPath -Layout $Layout -Path $path
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
}

function New-AstrbotArchiveDirectory {
    param($Layout)
    $name = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $directory = Join-Path $Layout.Logs "archive\$name"
    Ensure-AstrbotDirectory -Layout $Layout -Path $directory
    return $directory
}

function Preserve-AstrbotServiceLogs {
    param($Layout)
    $archive = $null
    foreach ($name in @('astrbot-service.stdout.log', 'astrbot-service.stderr.log')) {
        $path = Join-Path $Layout.Logs $name
        Assert-AstrbotManagedPath -Layout $Layout -Path $path
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            if (-not $archive) { $archive = New-AstrbotArchiveDirectory -Layout $Layout }
            Move-Item -LiteralPath $path -Destination (Join-Path $archive $name)
        }
    }
}

function Test-AstrbotDashboardResponse {
    # Explicitly bypass system proxies for this local readiness probe.
    $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:6185/')
    $request.Proxy = $null
    $request.Timeout = 4000
    $request.ReadWriteTimeout = 4000
    $response = $null
    try {
        $response = $request.GetResponse()
        return [int]$response.StatusCode -eq 200
    } catch { return $false }
    finally { if ($response) { $response.Dispose() } }
}

function Get-AstrbotHealth {
    param($Layout)
    if (Test-AstrbotManualStop -Layout $Layout) {
        return [pscustomobject]@{ Status = 'paused'; Reason = 'Manual stop'; Restart = $false }
    }
    $launcherPath = [IO.Path]::GetFullPath((Join-Path $Layout.UvToolBin 'astrbot.exe'))
    $toolPythonPath = [IO.Path]::GetFullPath((Join-Path $Layout.UvToolDir 'astrbot\Scripts\python.exe'))
    $managedPythonPath = Get-ManagedPythonPath -Layout $Layout
    $identityArgs = @{ LauncherPath = $launcherPath; ToolPythonPath = $toolPythonPath;
        ManagedPythonPath = $managedPythonPath; Port = 6185 }
    $statePath = Join-Path $Layout.Root 'astrbot-owned-run.json'
    $state = Read-AstrbotRunState -Path $statePath -Layout $Layout -AstrBotVersion '4.28.2' -Port 6185
    if ($state) {
        Assert-StatePaths -State $state -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
            -ManagedPythonPath $managedPythonPath
    }
    $listener = Get-ValidatedListener -Port 6185
    if (-not $listener) {
        $live = if ($state) { @(Get-LiveRecordedIdentities -State $state @identityArgs) } else { @() }
        if ($live.Count -eq 0) {
            return [pscustomobject]@{ Status = 'down'; Reason = 'AstrBot process exited'; Restart = $false }
        }
        $native = Get-StateIdentity -State $state -Kind 'managed-python'
        if ($native -and -not (Get-AstrbotProcessInfo -ProcessId ([int]$native.ProcessId))) {
            return [pscustomobject]@{ Status = 'down'; Reason = 'AstrBot listener exited; wrappers remain'; Restart = $true }
        }
        return [pscustomobject]@{ Status = 'unhealthy'; Reason = 'Dashboard 6185 unavailable'; Restart = $true }
    }
    if (-not $state -or $state.Phase -cne 'running' -or
        -not (Test-StateListener -State $state -ListenerProcessId $listener.ProcessId @identityArgs)) {
        throw 'Dashboard 6185 ownership mismatch; recovery will not touch this process.'
    }
    $configPath = Join-Path $Layout.Instance 'data\cmd_config.json'
    Assert-AstrbotManagedPath -Layout $Layout -Path $configPath
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $qqEnabled = @($config.platform | Where-Object {
        $_.id -ceq 'qq-chatrobot-local' -and $_.type -ceq 'aiocqhttp' -and $_.enable -eq $true
    }).Count -gt 0
    if ($qqEnabled) {
        $ws = Get-ValidatedListener -Port 6199
        if (-not $ws) {
            return [pscustomobject]@{ Status = 'unhealthy'; Reason = 'QQ reverse WebSocket 6199 unavailable'; Restart = $true }
        }
        if ($ws.ProcessId -ne $listener.ProcessId) {
            throw 'Port 6199 belongs to another process; recovery will not stop it.'
        }
    }
    if (-not (Test-AstrbotDashboardResponse)) {
        return [pscustomobject]@{ Status = 'unhealthy'; Reason = 'Dashboard HTTP did not respond'; Restart = $true }
    }
    return [pscustomobject]@{ Status = 'healthy'; Reason = 'AstrBot ready'; Restart = $false }
}

function Get-AstrbotRecoveryDecision {
    param($Health, [datetime]$Now, [datetime]$UnhealthySince, [datetime]$NextAttempt)
    if ($Health.Status -in @('healthy', 'paused')) { return 'wait' }
    if ($Now -lt $NextAttempt) { return 'wait' }
    if ($Health.Status -eq 'down') { return 'start' }
    if ($Health.Status -eq 'unhealthy' -and ($Now - $UnhealthySince).TotalSeconds -ge 60) {
        return 'restart'
    }
    return 'wait'
}

function New-AstrbotListenerWatch {
    param($Layout)
    $state = Read-AstrbotRunState -Path (Join-Path $Layout.Root 'astrbot-owned-run.json') `
        -Layout $Layout -AstrBotVersion '4.28.2' -Port 6185
    if (-not $state -or $state.Phase -cne 'running') { return $null }
    $launcherPath = Join-Path $Layout.UvToolBin 'astrbot.exe'
    $toolPythonPath = Join-Path $Layout.UvToolDir 'astrbot\Scripts\python.exe'
    $managedPythonPath = Get-ManagedPythonPath -Layout $Layout
    Assert-StatePaths -State $state -LauncherPath $launcherPath -ToolPythonPath $toolPythonPath `
        -ManagedPythonPath $managedPythonPath
    $identity = Get-StateIdentity -State $state -Kind 'managed-python'
    if (-not (Test-AstrbotRecordedIdentity -Identity $identity -LauncherPath $launcherPath `
        -ToolPythonPath $toolPythonPath -ManagedPythonPath $managedPythonPath -Port 6185)) { return $null }
    $process = Get-Process -Id $identity.ProcessId -ErrorAction Stop
    try {
        # Retain this exact Windows handle, so exit status remains available after the PID disappears.
        $null = $process.Handle
        $created = [datetime]::Parse($identity.CreatedUtc).ToUniversalTime()
        if (-not $process.Path.Equals($identity.ExecutablePath, [StringComparison]::OrdinalIgnoreCase) -or
            [math]::Abs(($process.StartTime.ToUniversalTime() - $created).TotalSeconds) -gt 2) {
            throw 'AstrBot listener identity changed while opening the exit watch.'
        }
        return [pscustomobject]@{ Process = $process; Identity = $identity }
    } catch { $process.Dispose(); throw }
}

function Get-AstrbotExitDescription {
    param([int]$ExitCode)
    $unsigned = [BitConverter]::ToUInt32([BitConverter]::GetBytes($ExitCode), 0)
    return "ExitCode=$ExitCode (0x$($unsigned.ToString('X8')))"
}

function Start-AstrbotSupervisor {
    param([string]$ProjectRoot)
    $mutex = New-AstrbotMutex -ProjectRoot $ProjectRoot -Purpose 'Supervisor'
    $available = $false
    try { $available = Enter-AstrbotMutex -Mutex $mutex }
    finally {
        if ($available) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
    if (-not $available) { return }
    # Only start a scheduled task whose action belongs to this checkout.
    $task = Get-ScheduledTask -TaskName 'QQChatRobot API' -ErrorAction SilentlyContinue
    $expected = '"' + (Join-Path $ProjectRoot 'scripts\run_bot_hidden.vbs') + '"'
    if ($task -and $task.Settings.Enabled -and $task.State -ne 'Running' -and
        $task.Actions.Count -eq 1 -and $task.Actions[0].Arguments -ceq $expected -and
        $task.Actions[0].WorkingDirectory -eq $ProjectRoot -and
        $task.Actions[0].Execute -eq 'wscript.exe') {
        Start-ScheduledTask -TaskName 'QQChatRobot API'
        return
    }
    # The supervisor's own mutex makes concurrent entry points harmless even without installed tasks.
    $scriptPath = Join-Path $ProjectRoot 'scripts\supervise_astrbot.ps1'
    $arguments = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $scriptPath) |
        ForEach-Object { ConvertTo-AstrbotWindowsArgument -Value $_ }) -join ' '
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
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
