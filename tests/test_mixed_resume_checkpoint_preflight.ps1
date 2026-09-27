[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$trainingScript = Join-Path $projectRoot "scripts\train_murasame_voice.ps1"
$validator = Join-Path $projectRoot "scripts\validate_mixed_resume_checkpoint.py"

# Validate that Windows PowerShell 5.1 can parse the training entry point
# before checking the paired checkpoint contract. This script never trains.
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $trainingScript,
    [ref]$tokens,
    [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -ne 0) {
    throw "PowerShell parser rejected train_murasame_voice.ps1: $($parseErrors[0].Message)"
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot "..\qq-chatrobot-voice\GPT-SoVITS\.venv_cpu\Scripts\python.exe"
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "GPT-SoVITS Python not found: $PythonPath"
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "qqchat-mixed-resume-smoke-" + [guid]::NewGuid().ToString("N")
)
try {
    $null = New-Item -ItemType Directory -Path $temporaryRoot
    # Passing source on stdin is safe in Windows PowerShell 5.1: quote-heavy
    # f-strings reach CPython verbatim, unlike the prior `python -c $string`.
    $makeCheckpoints = @'
import pathlib
import sys
import torch

root = pathlib.Path(sys.argv[1])
for label in ("G", "D"):
    torch.save({"iteration": 14, "optimizer": {}, "model": {}}, root / f"{label}_1400.pth")
'@
    $makeCheckpoints | & $PythonPath - $temporaryRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create smoke-test checkpoints." }

    $output = @(& $PythonPath $validator $temporaryRoot 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Paired checkpoint validation smoke check failed: $output" }
    if (@($output | Where-Object { $_ -eq "RESUME_EPOCH=14" }).Count -ne 1) {
        throw "Paired checkpoint validation returned unexpected output: $output"
    }
    Write-Host "PASS: PowerShell parser and paired mixed-checkpoint preflight"
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
