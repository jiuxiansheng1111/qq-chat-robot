#requires -Version 5.1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logRoot = Join-Path $projectRoot 'data\logs'
try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'The existing voice Python environment is missing.'
    }
    . (Join-Path $PSScriptRoot 'voice_service.ps1')
    $settings = Get-DotEnvValues -Path (Join-Path $projectRoot '.env')
    if (-not (Start-GptSovitsSidecar -Settings $settings)) {
        throw 'Voice is disabled or the GPU is reserved. Please retry when it is available.'
    }
    Write-Host '[READY] Independent voice service is ready. QQ is not required.'
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
