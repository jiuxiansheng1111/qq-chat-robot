# AstrBot 进程身份与本任务 PID 记录。

function Get-AstrbotCommandHash {
    param([Parameter(Mandatory = $true)][string]$CommandLine)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($CommandLine)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-AstrbotProcessKind {
    param(
        [Parameter(Mandatory = $true)]$ProcessInfo,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    if (-not $ProcessInfo.ExecutablePath -or -not $ProcessInfo.CommandLine) { return $null }
    try { $actual = [IO.Path]::GetFullPath([string]$ProcessInfo.ExecutablePath) }
    catch { return $null }

    $launcher = [IO.Path]::GetFullPath($LauncherPath)
    $toolPython = [IO.Path]::GetFullPath($ToolPythonPath)
    $managedPython = [IO.Path]::GetFullPath($ManagedPythonPath)
    $command = [string]$ProcessInfo.CommandLine
    $options = [Text.RegularExpressions.RegexOptions]::IgnoreCase
    $portArg = [regex]::Escape([string]$Port)

    if ($actual.Equals($launcher, [StringComparison]::OrdinalIgnoreCase)) {
        $pattern = '^\s*"?' + [regex]::Escape($launcher) + '"?\s+run\s+--port\s+' + $portArg + '(?:\s|$)'
        if ([regex]::IsMatch($command, $pattern, $options)) { return 'launcher' }
        return $null
    }

    $kind = if ($actual.Equals($toolPython, [StringComparison]::OrdinalIgnoreCase)) {
        'tool-python'
    } elseif ($actual.Equals($managedPython, [StringComparison]::OrdinalIgnoreCase)) {
        'managed-python'
    } else { return $null }

    $pattern = '^\s*"?' + [regex]::Escape($actual) + '"?\s+"?' +
        [regex]::Escape($launcher) + '"?\s+run\s+--port\s+' + $portArg + '(?:\s|$)'
    if ([regex]::IsMatch($command, $pattern, $options)) { return $kind }
    return $null
}

function New-AstrbotProcessIdentity {
    param(
        [Parameter(Mandatory = $true)]$ProcessInfo,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $kind = Get-AstrbotProcessKind -ProcessInfo $ProcessInfo -LauncherPath $LauncherPath `
        -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    if (-not $kind) { return $null }
    try { $exe = [IO.Path]::GetFullPath([string]$ProcessInfo.ExecutablePath) }
    catch { return $null }
    if ($null -eq $ProcessInfo.CreationDate) { return $null }
    $created = ([datetime]$ProcessInfo.CreationDate).ToUniversalTime().ToString('o')
    return [pscustomobject]@{
        ProcessId = [int]$ProcessInfo.ProcessId
        ParentProcessId = [int]$ProcessInfo.ParentProcessId
        Kind = [string]$kind
        CreatedUtc = $created
        ExecutablePath = $exe
        CommandHash = Get-AstrbotCommandHash -CommandLine ([string]$ProcessInfo.CommandLine)
    }
}

function Get-AstrbotProcessInfo {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Test-AstrbotRecordedIdentity {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $info = Get-AstrbotProcessInfo -ProcessId ([int]$Identity.ProcessId)
    if (-not $info) { return $false }
    $current = New-AstrbotProcessIdentity -ProcessInfo $info -LauncherPath $LauncherPath `
        -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    if (-not $current) { return $false }
    return $current.ProcessId -eq [int]$Identity.ProcessId -and
        $current.ParentProcessId -eq [int]$Identity.ParentProcessId -and
        $current.Kind -ceq [string]$Identity.Kind -and
        $current.CreatedUtc -ceq [string]$Identity.CreatedUtc -and
        $current.ExecutablePath.Equals([string]$Identity.ExecutablePath, [StringComparison]::OrdinalIgnoreCase) -and
        $current.CommandHash -ceq [string]$Identity.CommandHash
}

function Get-AstrbotVerifiedChain {
    param(
        [Parameter(Mandatory = $true)]$ListenerInfo,
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$ToolPythonPath,
        [Parameter(Mandatory = $true)][string]$ManagedPythonPath,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $listener = New-AstrbotProcessIdentity -ProcessInfo $ListenerInfo -LauncherPath $LauncherPath `
        -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    if (-not $listener -or $listener.Kind -cne 'managed-python') { return $null }

    $toolInfo = Get-AstrbotProcessInfo -ProcessId $listener.ParentProcessId
    if (-not $toolInfo) { return $null }
    $tool = New-AstrbotProcessIdentity -ProcessInfo $toolInfo -LauncherPath $LauncherPath `
        -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    if (-not $tool -or $tool.Kind -cne 'tool-python' -or $tool.ProcessId -ne $listener.ParentProcessId) { return $null }

    $launcherInfo = Get-AstrbotProcessInfo -ProcessId $tool.ParentProcessId
    if (-not $launcherInfo) { return $null }
    $launcher = New-AstrbotProcessIdentity -ProcessInfo $launcherInfo -LauncherPath $LauncherPath `
        -ToolPythonPath $ToolPythonPath -ManagedPythonPath $ManagedPythonPath -Port $Port
    if (-not $launcher -or $launcher.Kind -cne 'launcher' -or $launcher.ProcessId -ne $tool.ParentProcessId) { return $null }

    try {
        $launcherStart = [datetime]::Parse([string]$launcher.CreatedUtc).ToUniversalTime()
        $toolStart = [datetime]::Parse([string]$tool.CreatedUtc).ToUniversalTime()
        $listenerStart = [datetime]::Parse([string]$listener.CreatedUtc).ToUniversalTime()
    } catch { return $null }
    if ($toolStart -lt $launcherStart -or $listenerStart -lt $toolStart) { return $null }
    return [pscustomobject]@{ Launcher = $launcher; ToolPython = $tool; Listener = $listener }
}

function Get-AstrbotListeners {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    } catch {
        if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::ObjectNotFound) {
            return @()
        }
        throw "无法安全检查本机端口 $Port；请在支持 Get-NetTCPConnection 的 PowerShell 中重试。"
    }
}

function Read-AstrbotRunState {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$AstrBotVersion,
        [Parameter(Mandatory = $true)][int]$Port
    )
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch { throw "AstrBot 运行记录无法读取：$Path" }
    if ($state.SchemaVersion -ne 1 -or $state.AstrBotVersion -ne $AstrBotVersion -or
        $state.Port -ne $Port -or -not $state.Launcher -or -not $state.Processes) {
        throw 'AstrBot 运行记录格式或版本不匹配；为安全起见未处理任何进程。'
    }
    $allowed = @('launcher', 'tool-python', 'managed-python')
    foreach ($identity in @($state.Processes)) {
        if (-not $identity.ProcessId -or $identity.Kind -notin $allowed -or
            -not $identity.CreatedUtc -or -not $identity.CommandHash -or -not $identity.ExecutablePath) {
            throw 'AstrBot 运行记录含无效进程项；为安全起见未处理任何进程。'
        }
    }
    return $state
}

function Write-AstrbotRunState {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)]$State
    )
    Assert-AstrbotManagedPath -Layout $Layout -Path $Path
    $parent = Split-Path -Parent $Path
    Ensure-AstrbotDirectory -Layout $Layout -Path $parent
    $temporary = "$Path.tmp-$PID-$([guid]::NewGuid().ToString('N'))"
    Assert-AstrbotManagedPath -Layout $Layout -Path $temporary
    try {
        $json = ConvertTo-Json -InputObject $State -Depth 8
        [IO.File]::WriteAllText($temporary, $json + "`n", [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function New-AstrbotRunState {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$AstrBotVersion,
        [Parameter(Mandatory = $true)]$LauncherIdentity
    )
    return [pscustomobject]@{
        SchemaVersion = 1
        AstrBotVersion = $AstrBotVersion
        Port = $Port
        Phase = 'starting'
        StartedUtc = [datetime]::UtcNow.ToString('o')
        Launcher = $LauncherIdentity
        Processes = @($LauncherIdentity)
        ListenerProcessId = $null
    }
}
