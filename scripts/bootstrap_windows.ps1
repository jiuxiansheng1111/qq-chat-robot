[CmdletBinding()]
param(
    [switch]$IncludeDev
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

function Find-CompatiblePython {
    $candidates = @(
        @{ File = "py.exe"; Prefix = @("-3.12") },
        @{ File = "py.exe"; Prefix = @("-3.11") },
        @{ File = "python.exe"; Prefix = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.File -ErrorAction SilentlyContinue)) {
            continue
        }

        try {
            $probeArgs = @($candidate.Prefix) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
            & $candidate.File @probeArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    return $null
}

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Find-CompatiblePython
    if ($null -eq $python) {
        throw "Python 3.11+ was not found. Install Python 3.11 or 3.12 and retry."
    }

    Write-Host "[SETUP] Creating .venv with Python 3.11+..." -ForegroundColor Yellow
    $venvArgs = @($python.Prefix) + @("-m", "venv", $venvRoot)
    & $python.File @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "Failed to create Python virtual environment at $venvRoot"
    }
}

$coreCheck = "import fastapi, uvicorn, httpx, aiosqlite, redis, jwt, PIL, pydantic_settings"
$devCheck = "import pytest, pytest_asyncio, ruff"
$check = if ($IncludeDev) { "$coreCheck; $devCheck" } else { $coreCheck }

& $venvPython -c $check 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[SETUP] Installing project dependencies..." -ForegroundColor Yellow
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    $installTarget = if ($IncludeDev) { ".[dev]" } else { "." }
    & $venvPython -m pip install -e $installTarget
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install project dependencies. Check network access to PyPI."
    }

    & $venvPython -c $check
    if ($LASTEXITCODE -ne 0) {
        throw "Dependencies were installed but import verification still failed."
    }
}

Write-Host "[READY] Python environment: $venvPython" -ForegroundColor Green
exit 0
