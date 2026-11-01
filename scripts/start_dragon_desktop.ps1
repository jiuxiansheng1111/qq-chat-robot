#requires -Version 5.1
$ErrorActionPreference='Stop'
$dragonRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dragonLogs=Join-Path $dragonRoot 'artifacts\dragon-maid-live2d'
New-Item -ItemType Directory -Path $dragonLogs -Force | Out-Null
# Start the shared speech engine without going through QQ/NapCat or its bootstrap.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_voice.ps1') *> (Join-Path $dragonLogs 'voice-startup.log')
if ($LASTEXITCODE -ne 0) { throw 'Voice could not start; inspect artifacts/dragon-maid-live2d/voice-startup.log' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_dragon_avatar.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Avatar server could not start' }
$dragonPython=Join-Path $dragonRoot '.venv\Scripts\pythonw.exe'
$dragonScript=Join-Path $PSScriptRoot 'dragon_avatar_desktop.py'
Start-Process -FilePath $dragonPython -ArgumentList @('"'+$dragonScript+'"') -WorkingDirectory $dragonRoot -WindowStyle Hidden
