$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$scripts = Join-Path $repo 'scripts'

foreach ($name in @('astrbot_windows_common.ps1', 'astrbot_process.ps1', 'run_astrbot.ps1')) {
    $file = Join-Path $scripts $name
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        throw ($name + ' parse failure at line ' + $errors[0].Extent.StartLineNumber + ': ' + $errors[0].Message)
    }
}

. (Join-Path $scripts 'astrbot_windows_common.ps1')
. (Join-Path $scripts 'astrbot_process.ps1')

$root = 'C:\Users\Test User\Project\data\astrbot\runtime'
$launcher = Join-Path $root 'bin\astrbot.exe'
$toolPython = Join-Path $root 'tool-envs\astrbot\Scripts\python.exe'
$managedPython = Join-Path $root 'python\cpython-3.12.15-windows-x86_64-none\python.exe'
$port = 6185

function New-FakeProcess {
    param([string]$Path, [string]$CommandLine, [int]$Id = 123, [int]$ParentId = 1)
    return [pscustomobject]@{
        ExecutablePath = $Path
        CommandLine = $CommandLine
        ProcessId = $Id
        ParentProcessId = $ParentId
        CreationDate = [datetime]'2026-10-04T01:00:00Z'
    }
}

$launcherInfo = New-FakeProcess -Path $launcher -CommandLine ('"' + $launcher + '" run --port 6185')
$toolInfo = New-FakeProcess -Path $toolPython -CommandLine ('"' + $toolPython + '" "' + $launcher + '" run --port 6185') -Id 124 -ParentId 123
$nativeInfo = New-FakeProcess -Path $managedPython -CommandLine ('"' + $managedPython + '" "' + $launcher + '" run --port 6185') -Id 125 -ParentId 124

$identityArgs = @{ LauncherPath = $launcher; ToolPythonPath = $toolPython; ManagedPythonPath = $managedPython; Port = $port }
if ((Get-AstrbotProcessKind -ProcessInfo $launcherInfo @identityArgs) -cne 'launcher') { throw 'Launcher identity was not accepted.' }
if ((Get-AstrbotProcessKind -ProcessInfo $toolInfo @identityArgs) -cne 'tool-python') { throw 'Tool Python identity was not accepted.' }
if ((Get-AstrbotProcessKind -ProcessInfo $nativeInfo @identityArgs) -cne 'managed-python') { throw 'Managed Python identity was not accepted.' }

$foreignPython = New-FakeProcess -Path 'C:\Python311\python.exe' -CommandLine $nativeInfo.CommandLine
if (Get-AstrbotProcessKind -ProcessInfo $foreignPython @identityArgs) { throw 'A Python executable outside the managed runtime was accepted.' }
$wrongPort = New-FakeProcess -Path $managedPython -CommandLine ('"' + $managedPython + '" "' + $launcher + '" run --port 6186')
if (Get-AstrbotProcessKind -ProcessInfo $wrongPort @identityArgs) { throw 'A process for another port was accepted.' }
$wrongLauncher = New-FakeProcess -Path $managedPython -CommandLine ('"' + $managedPython + '" "C:\temp\astrbot.exe" run --port 6185')
if (Get-AstrbotProcessKind -ProcessInfo $wrongLauncher @identityArgs) { throw 'A process using another launcher was accepted.' }

$script:testProcessById = @{ 123 = $launcherInfo; 124 = $toolInfo; 125 = $nativeInfo }
function Get-AstrbotProcessInfo {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    return $script:testProcessById[$ProcessId]
}

$chain = Get-AstrbotVerifiedChain -ListenerInfo $nativeInfo -LauncherPath $launcher `
    -ToolPythonPath $toolPython -ManagedPythonPath $managedPython -Port $port
if (-not $chain -or $chain.Launcher.ProcessId -ne 123 -or $chain.ToolPython.ProcessId -ne 124 -or $chain.Listener.ProcessId -ne 125) {
    throw 'The expected launcher → tool Python → managed Python chain was not accepted.'
}
$orphan = New-FakeProcess -Path $managedPython -CommandLine $nativeInfo.CommandLine -Id 126 -ParentId 999
if (Get-AstrbotVerifiedChain -ListenerInfo $orphan -LauncherPath $launcher `
        -ToolPythonPath $toolPython -ManagedPythonPath $managedPython -Port $port) {
    throw 'A process without the recorded parent chain was accepted.'
}

$testStateRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.run-state-test'))
$testsRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
if (-not $testStateRoot.StartsWith($testsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Test state directory escaped the test workspace.'
}
if (Test-Path -LiteralPath $testStateRoot) { throw 'Test state directory already exists.' }
New-Item -ItemType Directory -Path $testStateRoot | Out-Null
try {
    $statePath = Join-Path $testStateRoot 'astrbot-owned-run.json'
    $testLayout = [pscustomobject]@{ Root = $testStateRoot }
    $identity = New-AstrbotProcessIdentity -ProcessInfo $launcherInfo @identityArgs
    $state = New-AstrbotRunState -Port $port -AstrBotVersion '4.28.2' -LauncherIdentity $identity
    Write-AstrbotRunState -Path $statePath -Layout $testLayout -State $state
    $saved = Read-AstrbotRunState -Path $statePath -Layout $testLayout -AstrBotVersion '4.28.2' -Port $port
    if ($saved.Phase -cne 'starting' -or @($saved.Processes).Count -ne 1 -or
        [int]$saved.Launcher.ProcessId -ne 123) {
        throw 'The private PID record did not survive a JSON round trip.'
    }
    $state.Phase = 'running'
    $state.Processes = @($chain.Launcher, $chain.ToolPython, $chain.Listener)
    $state.ListenerProcessId = 125
    Write-AstrbotRunState -Path $statePath -Layout $testLayout -State $state
    $saved = Read-AstrbotRunState -Path $statePath -Layout $testLayout -AstrBotVersion '4.28.2' -Port $port
    if ($saved.Phase -cne 'running' -or @($saved.Processes).Count -ne 3 -or
        [int]$saved.ListenerProcessId -ne 125) {
        throw 'The complete AstrBot process chain did not survive a JSON round trip.'
    }
} finally {
    if (Test-Path -LiteralPath $testStateRoot) {
        if (Test-Path -LiteralPath $statePath -PathType Leaf) { Remove-Item -LiteralPath $statePath -Force }
        Remove-Item -LiteralPath $testStateRoot -Force
    }
}

Write-Output ('AstrBot process identity checks passed under PowerShell ' + $PSVersionTable.PSVersion + '.')
