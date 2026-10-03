#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$projectModeTestRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $projectModeTestRoot 'scripts\bot_runtime_mode.ps1')
$modeTestRoot = Join-Path $projectModeTestRoot ('data\astrbot\mode-tests\' + [guid]::NewGuid().ToString('N'))
$modeTestDirectory = Join-Path $modeTestRoot 'data\astrbot'
New-Item -ItemType Directory -Path $modeTestDirectory -Force | Out-Null
$modeTestFile = Join-Path $modeTestDirectory 'active-mode.txt'
if ((Get-QQChatRobotRuntime -ProjectRoot $modeTestRoot) -ne 'fastapi') { throw 'Missing marker should use FastAPI' }
[IO.File]::WriteAllText($modeTestFile, "astrbot`n", [Text.UTF8Encoding]::new($false))
if ((Get-QQChatRobotRuntime -ProjectRoot $modeTestRoot) -ne 'astrbot') { throw 'AstrBot marker was ignored' }
[IO.File]::WriteAllText($modeTestFile, "fastapi`n", [Text.UTF8Encoding]::new($false))
if ((Get-QQChatRobotRuntime -ProjectRoot $modeTestRoot) -ne 'fastapi') { throw 'FastAPI marker was ignored' }
[IO.File]::WriteAllText($modeTestFile, 'unknown', [Text.UTF8Encoding]::new($false))
$modeTestRejected = $false
try { Get-QQChatRobotRuntime -ProjectRoot $modeTestRoot | Out-Null }
catch { $modeTestRejected = $true }
if (-not $modeTestRejected) { throw 'Invalid marker must not start either service' }
Write-Output 'Startup mode checks passed; no service was started.'
