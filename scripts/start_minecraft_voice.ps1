[CmdletBinding()]
param(
    [string]$GptRoot = '',
    [string]$Python = '',
    [ValidateRange(1024,65535)][int]$Port = 9880,
    [switch]$PersistentSidecar
)

$ErrorActionPreference = 'Stop'
$voiceProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GptRoot) { $GptRoot = Join-Path $voiceProjectRoot '..\qq-chatrobot-voice\GPT-SoVITS' }
$GptRoot = [IO.Path]::GetFullPath($GptRoot)
if (-not $Python) { $Python = Join-Path $GptRoot '.venv\Scripts\python.exe' }
$Python = [IO.Path]::GetFullPath($Python)
$voiceLauncher = Join-Path $PSScriptRoot 'run_gpt_sovits.py'
$voiceApi = Join-Path $GptRoot 'api_v2.py'
$voiceSourceConfig = Join-Path $GptRoot 'GPT_SoVITS\configs\tts_infer.yaml'
foreach ($voiceRequired in @($Python, $voiceLauncher, $voiceApi, $voiceSourceConfig)) {
    if (-not (Test-Path -LiteralPath $voiceRequired -PathType Leaf)) {
        throw "Existing GPT-SoVITS file is missing: $voiceRequired"
    }
}
$voiceData = Join-Path $voiceProjectRoot 'data\minecraft_voice'
New-Item -ItemType Directory -Path $voiceData -Force | Out-Null
$voiceRuntime = @{gpt_root=$GptRoot; python=$Python; config=$voiceSourceConfig} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $voiceData 'runtime.json'), $voiceRuntime, [Text.UTF8Encoding]::new($false))
if (-not $PersistentSidecar) {
    Write-Host 'Game voice runtime paths are ready. Explicit game requests start one TTS worker, wait for its exit, then start one RVC worker.'
    Write-Host 'No persistent TTS sidecar is required. Existing QQ/LLM settings and processes are unchanged.'
    Write-Host "Small audio cache and request audit: $voiceData"
    return
}
# Kept only for explicit manual diagnostics/other existing TTS clients.
# Game speech uses one-shot workers and does not need this resident process.
$voiceSocket = [Net.Sockets.TcpClient]::new()
try {
    if ($voiceSocket.ConnectAsync('127.0.0.1', $Port).Wait(800) -and $voiceSocket.Connected) {
        Write-Host "Port $Port already has a listener; leaving that process unchanged. Check the bridge health before use."
        return
    }
} catch { } finally { $voiceSocket.Dispose() }

$voiceConfig = Join-Path $voiceData 'tts_infer.yaml'
if (-not (Test-Path -LiteralPath $voiceConfig)) {
    Copy-Item -LiteralPath $voiceSourceConfig -Destination $voiceConfig
}
$voiceTemp = Join-Path $env:PUBLIC 'GPTSoVITS_temp'
New-Item -ItemType Directory -Path $voiceTemp -Force | Out-Null
$voiceEnvironmentNames = @('TEMP','TMP','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','PYTHONUTF8','OMP_NUM_THREADS','MKL_NUM_THREADS')
$voicePreviousEnvironment = @{}
foreach ($voiceName in $voiceEnvironmentNames) {
    $voicePreviousEnvironment[$voiceName] = [Environment]::GetEnvironmentVariable($voiceName, 'Process')
}
try {
    $env:TEMP = $voiceTemp; $env:TMP = $voiceTemp
    $env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'; $env:PYTHONUTF8 = '1'
    $env:OMP_NUM_THREADS = '2'; $env:MKL_NUM_THREADS = '2'
    $voiceArguments = @($voiceLauncher, '--gpt-root', $GptRoot, '--api-script', $voiceApi, '--offline-language-lite',
        '--', '-a', '127.0.0.1', '-p', [string]$Port, '-c', $voiceConfig)
    # All path arguments are local filesystem paths; embedded quotes are invalid on Windows.
    $voiceQuotedArguments = ($voiceArguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $voiceProcess = Start-Process -FilePath $Python -ArgumentList $voiceQuotedArguments `
        -WorkingDirectory $GptRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $voiceData 'tts.stdout.log') `
        -RedirectStandardError (Join-Path $voiceData 'tts.stderr.log')
    $voiceProcess.Id | Set-Content -LiteralPath (Join-Path $voiceData 'launcher.pid')
    Write-Host "Local TTS is starting on 127.0.0.1:$Port (launcher PID $($voiceProcess.Id))."
    Write-Host 'Only existing models are used; downloads are disabled. QQ settings and LLM are unchanged.'
    Write-Host "Logs: $voiceData. A started process does not confirm successful speech synthesis."
} finally {
    foreach ($voiceName in $voiceEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($voiceName, $voicePreviousEnvironment[$voiceName], 'Process')
    }
}
