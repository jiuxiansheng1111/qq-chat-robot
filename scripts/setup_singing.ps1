[CmdletBinding()]
param(
    [string]$GpuPython = "..\qq-chatrobot-voice\GPT-SoVITS\.venv\Scripts\python.exe",
    [switch]$SkipPythonDependencies,
    [switch]$SkipFfmpeg
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$singingRoot = Join-Path $projectRoot "data\singing"
$seedRoot = Join-Path $singingRoot "runtime\seed-vc"
$runtimeRoot = Join-Path $singingRoot "runtime"
$runtimePython = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$binRoot = Join-Path $runtimeRoot "bin"
$requirements = Join-Path $PSScriptRoot "singing-requirements.txt"

if (-not (Test-Path -LiteralPath (Join-Path $seedRoot "train.py"))) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { throw "Git is required to fetch Seed-VC into $seedRoot." }
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    & $git.Source clone https://github.com/Plachtaa/seed-vc.git $seedRoot
    if ($LASTEXITCODE -ne 0) { throw "Cloning the official Seed-VC repository failed." }
}
if (-not (Test-Path -LiteralPath (Join-Path $seedRoot ".git"))) {
    throw "Seed-VC source exists but is not a Git checkout: $seedRoot"
}
$seedCommit = (& git -C $seedRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $seedCommit -notmatch "^[0-9a-f]{40}$") {
    throw "Could not resolve the checked-out Seed-VC commit."
}
$seedPinPath = Join-Path $runtimeRoot "seed-vc.commit"
if (Test-Path -LiteralPath $seedPinPath) {
    $savedSeedCommit = (Get-Content -LiteralPath $seedPinPath -Raw).Trim()
    if ($savedSeedCommit -ne $seedCommit) {
        throw "Seed-VC commit changed from $savedSeedCommit to $seedCommit; review the update and remove $seedPinPath to accept it."
    }
} else {
    Set-Content -LiteralPath $seedPinPath -Value $seedCommit -Encoding ascii
}
Write-Host "Seed-VC commit: $seedCommit"
$preset = Join-Path $seedRoot "configs\presets\config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"
if (-not (Test-Path -LiteralPath $preset)) {
    throw "Seed-VC singing preset is missing: $preset"
}
if (-not [System.IO.Path]::IsPathRooted($GpuPython)) {
    $GpuPython = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $GpuPython))
}
if (-not (Test-Path -LiteralPath $GpuPython)) {
    throw "CUDA Python was not found: $GpuPython"
}

$gpuProbe = & $GpuPython -c "import json,torch; print(json.dumps({'cuda': torch.cuda.is_available(), 'torch': torch.__version__, 'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the selected CUDA Python." }
$gpuInfo = $gpuProbe | ConvertFrom-Json
if (-not $gpuInfo.cuda) { throw "Selected Python has no CUDA-enabled PyTorch. Choose the GPT-SoVITS .venv, not .venv_cpu." }
Write-Host ("CUDA runtime: torch {0}, {1}" -f $gpuInfo.torch, $gpuInfo.gpu)

if (-not (Test-Path -LiteralPath $runtimePython)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $runtimePython) -Force | Out-Null
    & $GpuPython -m venv (Join-Path $runtimeRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the isolated singing runtime venv." }
}

# 把现有 CUDA 环境的包加入新运行时的导入路径，
# 不改写该环境。安装仍只写入本地。
$sharedSiteJson = & $GpuPython -c "import json,sysconfig; print(json.dumps(sysconfig.get_paths()['purelib'], ensure_ascii=True))"
if ($LASTEXITCODE -ne 0 -or -not $sharedSiteJson) { throw "Could not locate CUDA environment site-packages." }
$runtimeSiteJson = & $runtimePython -c "import json,sysconfig; print(json.dumps(sysconfig.get_paths()['purelib'], ensure_ascii=True))"
if ($LASTEXITCODE -ne 0 -or -not $runtimeSiteJson) { throw "Could not locate isolated runtime site-packages." }
$runtimeSite = $runtimeSiteJson | ConvertFrom-Json
$bridgePath = Join-Path $runtimeSite "shared_gpu_runtime.pth"
$bridgeLine = "import sys; sys.path.append($sharedSiteJson)"
if (Test-Path -LiteralPath $bridgePath) {
    if ((Get-Content -LiteralPath $bridgePath -Raw).Trim() -ne $bridgeLine) {
        throw "Existing shared_gpu_runtime.pth points elsewhere; inspect it before changing the GPU runtime bridge."
    }
} else {
    Set-Content -LiteralPath $bridgePath -Value $bridgeLine -Encoding ascii
}
$legacyBridge = Join-Path $runtimeSite "shared_cuda_torch.pth"
if (Test-Path -LiteralPath $legacyBridge) {
    if ((Get-Content -LiteralPath $legacyBridge -Raw).Trim() -ne $bridgeLine) {
        throw "Legacy shared_cuda_torch.pth points elsewhere; inspect it before removing the duplicate bridge."
    }
    Remove-Item -LiteralPath $legacyBridge
}

if (-not $SkipPythonDependencies) {
    & $runtimePython -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Installing singing runtime requirements failed." }
}

if (-not $SkipFfmpeg) {
    New-Item -ItemType Directory -Path $binRoot -Force | Out-Null
    $revision = "bc7576fa9b21e8a880e37c469c23e4d3e2d76cfb"
    $base = "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/$revision"
    $expectedHashes = @{
        "ffmpeg.exe" = "b6a4d917a444790f4c06ada640c1c0c95aecde2f8953ed8d0dfb19352500bfcd"
        "ffprobe.exe" = "2da5b980a9a14a808f423d181c4ed51c2b8af11b1366699f3f7eab0609926f8f"
    }
    foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
        $target = Join-Path $binRoot $name
        $downloaded = $false
        if (-not (Test-Path -LiteralPath $target)) {
            $temporary = "$target.download"
            Invoke-WebRequest -Uri "$base/$name" -OutFile $temporary
            Move-Item -LiteralPath $temporary -Destination $target
            $downloaded = $true
        }
        if ($downloaded) {
            $actualHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualHash -ne $expectedHashes[$name]) {
                Remove-Item -LiteralPath $target
                throw "$name SHA-256 did not match the pinned artifact metadata: $actualHash"
            }
        }
        $version = & $target -version 2>&1 | Select-Object -First 1
        $toolName = [System.IO.Path]::GetFileNameWithoutExtension($name)
        if ($LASTEXITCODE -ne 0 -or $version -notmatch ("^{0} version" -f [Regex]::Escape($toolName))) {
            throw "Audio tool failed its version check: $target"
        }
    }
}

$cudaCheck = & $runtimePython -c "import torch, yaml, librosa, soundfile; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
if ($LASTEXITCODE -ne 0) { throw "Singing runtime import check failed." }
Write-Host "Singing runtime ready. No project .env values were changed."
Write-Host "Runtime Python: $runtimePython"
Write-Host "ffmpeg tools: $binRoot"
