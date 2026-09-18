[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logRoot = Join-Path $projectRoot "logs"
$outputLog = Join-Path $logRoot "bot.out.log"
$errorLog = Join-Path $logRoot "bot.error.log"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Set-Location -LiteralPath $projectRoot

while ($true) {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        exit 0
    }

    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $outputLog `
        -RedirectStandardError $errorLog `
        -PassThru `
        -WindowStyle Hidden
    $process.WaitForExit()
    Add-Content -LiteralPath $errorLog -Value "$(Get-Date -Format o) Bot process exited; restarting in 5 seconds."
    Start-Sleep -Seconds 5
}
