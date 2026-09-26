[CmdletBinding()]
param(
    # Two epochs was only a smoke test and produced a near-silent checkpoint.
    # Ten epochs is the minimum useful CPU fine-tune for this six-minute set.
    [int]$Epochs = 10,
    [int]$BatchSize = 1,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$DatasetName = "murasame_voice_dataset_ja",
    # "mixed" accepts only per-row ja/zh labels and requires both languages.
    # It must use a new dataset root so the single-language cache is untouched.
    [ValidateSet("ja", "zh", "en", "mixed")]
    [string]$ExpectedLanguage = "ja",
    # Leave empty to select a free ASCII SUBST drive without disturbing an
    # existing user mapping.  A value such as T: can be supplied explicitly.
    [string]$ProjectDrive = "",
    [string]$VoiceDrive = ""
)

$ErrorActionPreference = "Stop"
$qualityRunName = "quality"
$projectRoot = Split-Path -Parent $PSScriptRoot
$voiceRootActual = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\qq-chatrobot-voice\GPT-SoVITS"))
$datasetActual = Join-Path $projectRoot ("data\" + $DatasetName)

if ($ExpectedLanguage -eq "mixed" -and $DatasetName -eq "murasame_voice_dataset_ja") {
    throw "Mixed ja+zh training requires a new dataset root. For example: -DatasetName murasame_voice_dataset_ja_zh -ExpectedLanguage mixed"
}

if (-not ("VoiceSubst.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace VoiceSubst {
    public static class NativeMethods {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern uint QueryDosDevice(
            string deviceName,
            StringBuilder targetPath,
            int maxCharacters
        );
    }
}
'@
}

function Normalize-DriveName {
    param([string]$Drive)
    $candidate = $Drive.Trim().ToUpperInvariant()
    if ($candidate -notmatch "^[A-Z]:$") { throw "Drive must have the form X:, got: $Drive" }
    return $candidate
}

function Get-SubstTarget {
    param([string]$Drive)
    $deviceName = Normalize-DriveName $Drive
    $buffer = New-Object System.Text.StringBuilder 32768
    $written = [VoiceSubst.NativeMethods]::QueryDosDevice($deviceName, $buffer, $buffer.Capacity)
    if ($written -eq 0) { return $null }
    $target = $buffer.ToString()
    # A SUBST drive has a \??\C:\... target. Physical and network drives use
    # device names such as \Device\HarddiskVolume... and are never repurposed.
    if ($target.StartsWith("\??\")) { return $target.Substring(4).TrimEnd("\") }
    return $null
}

function Test-ExpectedSubstTarget {
    param([string]$Drive, [string]$Target)
    $mappedTarget = Get-SubstTarget $Drive
    if (-not $mappedTarget) { return $false }
    $mappedFull = [System.IO.Path]::GetFullPath($mappedTarget).TrimEnd("\")
    $targetFull = [System.IO.Path]::GetFullPath($Target).TrimEnd("\")
    return [string]::Equals($mappedFull, $targetFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Select-SubstDrive {
    param([string]$RequestedDrive, [string]$Target, [string[]]$Candidates)
    if ($RequestedDrive.Trim()) { return Normalize-DriveName $RequestedDrive }
    foreach ($candidate in $Candidates) {
        $drive = Normalize-DriveName $candidate
        if (Test-ExpectedSubstTarget $drive $Target) { return $drive }
        if ((Get-SubstTarget $drive) -or (Test-Path -LiteralPath ($drive + "\"))) { continue }
        return $drive
    }
    throw "No free ASCII SUBST drive is available. Pass -ProjectDrive or -VoiceDrive with a free drive letter."
}

function Ensure-SubstAlias {
    param(
        [Parameter(Mandatory = $true)][string]$Drive,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$RequiredPath
    )

    $Drive = Normalize-DriveName $Drive
    $targetFull = [System.IO.Path]::GetFullPath($Target).TrimEnd("\")
    # `subst` is the one API that reveals the physical target of an existing
    # drive alias.  Merely finding a familiar subdirectory is unsafe: an old
    # checkout can have the same dataset name.
    $matchedTarget = Get-SubstTarget $Drive
    if ($matchedTarget) {
        $mappedFull = [System.IO.Path]::GetFullPath($matchedTarget).TrimEnd("\")
        if (-not [string]::Equals($mappedFull, $targetFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Drive is already mapped to $matchedTarget, not $Target. Do not overwrite the drive mapping; choose a free drive or remove the stale mapping yourself."
        }
        Register-SubstPSDrive $Drive
        if (-not (Test-Path -LiteralPath $RequiredPath)) {
            throw "$Drive maps to the expected root but does not contain the required path: $RequiredPath"
        }
        return
    }
    if (Test-Path -LiteralPath ($Drive + "\")) {
        throw "$Drive is already assigned to a non-SUBST drive. Do not overwrite it; choose a free drive or remove the mapping yourself."
    }
    & subst $Drive $Target | Out-Null
    $substExitCode = $LASTEXITCODE
    Register-SubstPSDrive $Drive
    if ($substExitCode -ne 0 -or -not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Could not map $Drive to $Target"
    }
}

function Register-SubstPSDrive {
    param([string]$Drive)
    $normalizedDrive = Normalize-DriveName $Drive
    $name = $normalizedDrive.Substring(0, 1)
    if (-not (Get-PSDrive -Name $name -ErrorAction SilentlyContinue)) {
        # PowerShell 5.1 caches FileSystem drives at process start.  A SUBST
        # created moments ago exists to native programs but may not yet be a
        # PowerShell provider drive, which would make Join-Path fail.
        New-PSDrive -Name $name -PSProvider FileSystem -Root ($normalizedDrive + "\") -Scope Script -ErrorAction Stop | Out-Null
    }
}

# Python's Windows/PowerShell path handling can corrupt this user's CJK
# username when a long Unicode path is serialized into the training JSON.
# SUBST gives the external trainer ASCII-only aliases, while the real files
# remain in their normal locations.
if (-not (Test-Path -LiteralPath $datasetActual)) { throw "Dataset directory not found: $datasetActual" }
$projectAlias = Select-SubstDrive -RequestedDrive $ProjectDrive -Target $projectRoot -Candidates @("R:", "T:", "U:", "W:", "X:", "Y:", "Z:")
$voiceAlias = Select-SubstDrive -RequestedDrive $VoiceDrive -Target ([System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\qq-chatrobot-voice"))) -Candidates @("V:", "S:", "Q:", "N:", "M:", "L:", "K:")
Ensure-SubstAlias -Drive $projectAlias -Target $projectRoot -RequiredPath ($projectAlias + "\data\" + $DatasetName)
Ensure-SubstAlias -Drive $voiceAlias -Target ([System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\qq-chatrobot-voice"))) -RequiredPath ($voiceAlias + "\GPT-SoVITS\.venv_cpu\Scripts\python.exe")
Register-SubstPSDrive $projectAlias
Register-SubstPSDrive $voiceAlias

$voiceRoot = Join-Path $voiceAlias "GPT-SoVITS"
$python = Join-Path $voiceRoot ".venv_cpu\Scripts\python.exe"
$dataset = Join-Path $projectAlias ("data\" + $DatasetName)
$sourceListPath = Join-Path $datasetActual "murasame.list"
$listPath = Join-Path $dataset "murasame.train.list"
$experimentName = "murasame_voice_$ExpectedLanguage"
$bertDir = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\chinese-roberta-wwm-ext-large"
$hubertDir = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\chinese-hubert-base"
$s2g = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s2G2333k.pth"
$s2d = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s2D2333k.pth"
$runStamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 6)
$logDir = Join-Path $projectRoot ("logs\murasame-training\" + $DatasetName + "\" + $runStamp)
$null = New-Item -ItemType Directory -Force -Path $logDir
Write-Host "[TRAIN] Per-run logs: $logDir"

if (-not (Test-Path -LiteralPath $python)) { throw "CPU GPT-SoVITS Python not found: $python" }
if (-not (Test-Path -LiteralPath $sourceListPath)) { throw "Dataset manifest not found: $sourceListPath" }
foreach ($requiredPath in @($bertDir, $hubertDir, $s2g, $s2d)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Required GPT-SoVITS dependency not found: $requiredPath" }
}

function Assert-MixedResumeCheckpoint {
    param([string]$CheckpointDirectory)

    # Upstream s2_train.py wraps both checkpoint loads in a bare `except`.
    # Any missing, mismatched, or corrupt checkpoint then falls through to the
    # base pretrained weights at epoch 1.  Mixed runs are explicitly a
    # continuation of the e10 Japanese run, so fail before any expensive
    # preprocessing or an accidental from-scratch train.
    $resumeCheck = @'
import pathlib
import sys
import torch

root = pathlib.Path(sys.argv[1])

def latest(pattern: str) -> pathlib.Path:
    paths = list(root.glob(pattern))
    if not paths:
        raise RuntimeError(f"missing {pattern} checkpoint in {root}")
    # Match GPT-SoVITS utils.latest_checkpoint_path(), which sorts all digits
    # in the complete path rather than trusting file modification times.
    return max(paths, key=lambda path: int("".join(filter(str.isdigit, str(path)))))

def load(label: str) -> tuple[pathlib.Path, dict, int]:
    path = latest(f"{label}_*.pth")
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    iteration = checkpoint.get("iteration")
    if not isinstance(iteration, int) or iteration < 1:
        raise RuntimeError(f"{path} has no valid saved epoch")
    if checkpoint.get("optimizer") is None:
        raise RuntimeError(f"{path} lacks optimizer state and is not a resumable training checkpoint")
    if "model" not in checkpoint:
        raise RuntimeError(f"{path} lacks model state")
    return path, checkpoint, iteration

g_path, _g_checkpoint, g_epoch = load("G")
d_path, _d_checkpoint, d_epoch = load("D")
if g_epoch != d_epoch:
    raise RuntimeError(f"G/D checkpoint epochs differ: {g_path}={g_epoch}, {d_path}={d_epoch}")
print(f"RESUME_EPOCH={g_epoch}")
'@
    $resumeCheckOutput = @(& $python -c $resumeCheck $CheckpointDirectory)
    if ($LASTEXITCODE -ne 0) {
        throw "Mixed training requires valid paired G/D e10 resume checkpoints in $CheckpointDirectory; refusing upstream pretrained fallback."
    }
    $epochLines = @($resumeCheckOutput | Where-Object { $_ -match "^RESUME_EPOCH=\d+$" })
    if ($epochLines.Count -ne 1) {
        throw "Could not determine mixed resume epoch from checkpoint validation."
    }
    return [int]$epochLines[0].Substring("RESUME_EPOCH=".Length)
}

if ($ExpectedLanguage -eq "mixed") {
    $resumeEpoch = Assert-MixedResumeCheckpoint (Join-Path $dataset "logs_s2_v2")
    if ($Epochs -le $resumeEpoch) {
        throw "Epochs ($Epochs) must be greater than mixed resume epoch ($resumeEpoch)."
    }
}

# Validate the source manifest before canonicalizing its paths.  Checking only
# basenames would let a stale manifest from an old dataset borrow same-named
# files from the new one, destroying audio/transcript provenance.
& $python (Join-Path $projectRoot "scripts\validate_voice_manifest.py") $sourceListPath $datasetActual `
    --expected-language $ExpectedLanguage --minimum-items 10
if ($LASTEXITCODE -ne 0) { throw "Source dataset manifest validation failed" }
$sourceManifestHash = (Get-FileHash -LiteralPath $sourceListPath -Algorithm SHA256).Hash

# The preparer writes ordinary absolute paths.  Convert only the training copy
# to R: so GPT-SoVITS never receives the Windows user's CJK profile path.
$audioFingerprintRows = [System.Collections.Generic.List[string]]::new()
$aliasRows = foreach ($sourceLine in Get-Content -LiteralPath $sourceListPath -Encoding UTF8) {
    if (-not $sourceLine.Trim()) { continue }
    $parts = $sourceLine -split "\|", 4
    if ($parts.Count -ne 4) { throw "Invalid source manifest row: $sourceLine" }
    $audioName = [System.IO.Path]::GetFileName($parts[0].Trim())
    if (-not $audioName) { throw "Source manifest row has no audio filename: $sourceLine" }
    $actualAudioPath = Join-Path $datasetActual ("audio\" + $audioName)
    if (-not (Test-Path -LiteralPath $actualAudioPath -PathType Leaf)) {
        throw "Source manifest references audio outside this dataset: $($parts[0])"
    }
    # The source list records paths and transcripts, but a curation pass can
    # replace a re-cut WAV without changing either.  For mixed training, bind
    # preprocessing caches to the actual bytes as well as the manifest.
    if ($ExpectedLanguage -eq "mixed") {
        $audioHash = (Get-FileHash -LiteralPath $actualAudioPath -Algorithm SHA256).Hash
        $audioFingerprintRows.Add("$audioName|$audioHash") | Out-Null
    }
    (Join-Path $dataset ("audio\" + $audioName)) + "|" + $parts[1] + "|" + $parts[2] + "|" + $parts[3]
}
if (-not $aliasRows) { throw "Dataset manifest contains no rows: $sourceListPath" }
[System.IO.File]::WriteAllLines($listPath, [string[]]$aliasRows, [System.Text.UTF8Encoding]::new($false))

& $python (Join-Path $projectRoot "scripts\validate_voice_manifest.py") $listPath $dataset `
    --expected-language $ExpectedLanguage --minimum-items 10
if ($LASTEXITCODE -ne 0) { throw "Dataset manifest validation failed" }
$manifestCount = (Get-Content -LiteralPath $listPath -Encoding UTF8 | Where-Object { $_.Trim() }).Count

function Get-NonEmptyLineCount {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return 0 }
    return @((Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_.Trim() })).Count
}

function Assert-GeneratedRowCount {
    param([string]$Path, [int]$Expected, [string]$Name, [int]$HeaderRows = 0)
    $actual = [Math]::Max(0, (Get-NonEmptyLineCount $Path) - $HeaderRows)
    if ($actual -ne $Expected) {
        throw "$Name has $actual row(s); expected exactly $Expected. Use a new dataset directory instead of mixing partial preprocessing output."
    }
}

$semanticHeader = [string]::Join([char]9, @("item_name", "semantic_audio"))

# Preprocessing outputs are content-addressed only by convention in upstream
# GPT-SoVITS.  Refuse to combine caches with a changed manifest: that creates a
# superficially successful but acoustically invalid checkpoint.
$trainingManifestHash = (Get-FileHash -LiteralPath $listPath -Algorithm SHA256).Hash
# The mixed cache additionally binds its language policy.  Keep the legacy
# single-language stamp byte-for-byte compatible so this new entry point does
# not invalidate the existing Japanese preprocessing cache.
$manifestHash = "source=$sourceManifestHash;training=$trainingManifestHash"
if ($ExpectedLanguage -eq "mixed") {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $fingerprintBytes = [System.Text.Encoding]::UTF8.GetBytes(
            [string]::Join("`n", [string[]]$audioFingerprintRows)
        )
        $audioFingerprint = -join ($sha256.ComputeHash($fingerprintBytes) | ForEach-Object {
            $_.ToString("x2")
        })
    } finally {
        $sha256.Dispose()
    }
    $manifestHash = "language=mixed;audio=$audioFingerprint;$manifestHash"
}
$manifestStamp = Join-Path $dataset "preprocess_manifest.sha256"
$cachePaths = @(
    (Join-Path $dataset "2-name2text.txt"),
    (Join-Path $dataset "4-cnhubert"),
    (Join-Path $dataset "6-name2semantic.tsv")
)
$hasCache = $cachePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (Test-Path -LiteralPath $manifestStamp) {
    $stampedHash = (Get-Content -LiteralPath $manifestStamp -Encoding UTF8 -Raw).Trim()
    if ($stampedHash -ne $manifestHash -and $hasCache) {
        throw "Manifest changed after preprocessing. Use a new dataset directory; stale caches will not be reused."
    }
} elseif ($hasCache) {
    throw "Untracked preprocessing cache found. Use a new dataset directory to avoid mixing old labels."
}
[System.IO.File]::WriteAllText($manifestStamp, $manifestHash, [System.Text.UTF8Encoding]::new($false))

$env:PYTHONPATH = "$voiceRoot;$voiceRoot\GPT_SoVITS"
$env:is_half = "False"
# The full G2PW package is downloaded separately.  Training preprocessing can
# use the bundled pypinyin path and will resume without re-downloading it.
$env:is_g2pw = "false"

function Invoke-Stage {
    param([string]$Name, [string]$Script, [hashtable]$Environment)
    $log = Join-Path $logDir "$Name.log"
    foreach ($item in $Environment.GetEnumerator()) { Set-Item "Env:$($item.Key)" $item.Value }
    Push-Location $voiceRoot
    try {
        & $python $Script *>&1 | Tee-Object -FilePath $log
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "$Name failed; see $log" }
}

if (-not (Test-Path (Join-Path $dataset "2-name2text.txt"))) {
    $textPart = Join-Path $dataset "2-name2text-0.txt"
    if ((Test-Path -LiteralPath $textPart) -and (Get-NonEmptyLineCount $textPart) -lt $manifestCount) {
        throw "Partial text preprocessing output found: $textPart. Use a new dataset directory."
    }
    Invoke-Stage "01-text" "GPT_SoVITS\prepare_datasets\1-get-text.py" @{
        inp_text=$listPath; inp_wav_dir=""; exp_name=$experimentName; i_part="0"; all_parts="1";
        opt_dir=$dataset; bert_pretrained_dir=$bertDir; version="v2"
    }
    if (-not (Test-Path -LiteralPath $textPart)) { throw "Text preprocessing did not produce: $textPart" }
    Copy-Item $textPart (Join-Path $dataset "2-name2text.txt") -Force
}
Assert-GeneratedRowCount (Join-Path $dataset "2-name2text.txt") $manifestCount "Text preprocessing"

function Get-MissingHubertFeatures {
    $featureRoot = Join-Path $dataset "4-cnhubert"
    $missing = foreach ($manifestRow in Get-Content -LiteralPath $listPath -Encoding UTF8) {
        if (-not $manifestRow.Trim()) { continue }
        $parts = $manifestRow -split "\|", 4
        $audioName = [System.IO.Path]::GetFileName($parts[0].Trim())
        $featurePath = Join-Path $featureRoot ($audioName + ".pt")
        if (-not (Test-Path -LiteralPath $featurePath -PathType Leaf)) { $audioName }
    }
    return @($missing)
}

$missingHubertFeatures = @(Get-MissingHubertFeatures)
if ($missingHubertFeatures.Count -gt 0) {
    Invoke-Stage "02-hubert" "GPT_SoVITS\prepare_datasets\2-get-hubert-wav32k.py" @{
        inp_text=$listPath; inp_wav_dir=""; exp_name=$experimentName; i_part="0"; all_parts="1";
        opt_dir=$dataset; cnhubert_base_dir=$hubertDir
    }
}
$missingHubertFeatures = @(Get-MissingHubertFeatures)
if ($missingHubertFeatures.Count -gt 0) {
    throw "Hubert preprocessing did not produce features for $($missingHubertFeatures.Count) manifest item(s), e.g. $($missingHubertFeatures[0])"
}

$semanticPath = Join-Path $dataset "6-name2semantic.tsv"
if (Test-Path -LiteralPath $semanticPath) {
    $existingHeader = @(Get-Content -LiteralPath $semanticPath -Encoding UTF8 -TotalCount 1)[0]
    if ($existingHeader -cne $semanticHeader) {
        throw "Semantic preprocessing has an invalid TSV header. Use a new dataset directory; do not reuse this cache."
    }
}
$semanticCount = if (Test-Path -LiteralPath $semanticPath) {
    [Math]::Max(0, (Get-Content -LiteralPath $semanticPath -Encoding UTF8).Count - 1)
} else { 0 }
if ($semanticCount -lt $manifestCount) {
    Invoke-Stage "03-semantic" "GPT_SoVITS\prepare_datasets\3-get-semantic.py" @{
        inp_text=$listPath; exp_name=$experimentName; i_part="0"; all_parts="1"; opt_dir=$dataset;
        pretrained_s2G=$s2g; s2config_path="GPT_SoVITS/configs/s2.json"
    }
    $semanticPart = Join-Path $dataset "6-name2semantic-0.tsv"
    if (-not (Test-Path -LiteralPath $semanticPart)) { throw "Semantic preprocessing did not produce: $semanticPart" }
    Assert-GeneratedRowCount $semanticPart $manifestCount "Semantic preprocessing"
    $semanticRows = @($semanticHeader) + @(
        Get-Content -LiteralPath $semanticPart -Encoding UTF8
    )
    [System.IO.File]::WriteAllLines(
        $semanticPath,
        [string[]]$semanticRows,
        [System.Text.UTF8Encoding]::new($false)
    )
}
Assert-GeneratedRowCount $semanticPath $manifestCount "Semantic preprocessing" 1

$base = Get-Content (Join-Path $voiceRoot "GPT_SoVITS\configs\s2.json") -Raw | ConvertFrom-Json
$base.train | Add-Member -NotePropertyName pretrained_s2G -NotePropertyValue "" -Force
$base.train | Add-Member -NotePropertyName pretrained_s2D -NotePropertyValue "" -Force
$base.train | Add-Member -NotePropertyName gpu_numbers -NotePropertyValue "0" -Force
$base.train | Add-Member -NotePropertyName if_save_latest -NotePropertyValue $true -Force
$base.train | Add-Member -NotePropertyName if_save_every_weights -NotePropertyValue $true -Force
$base.train | Add-Member -NotePropertyName save_every_epoch -NotePropertyValue 1 -Force
$base.data | Add-Member -NotePropertyName exp_dir -NotePropertyValue "" -Force
$base.model | Add-Member -NotePropertyName version -NotePropertyValue "v2" -Force
$base | Add-Member -NotePropertyName name -NotePropertyValue $experimentName -Force
$base | Add-Member -NotePropertyName version -NotePropertyValue "v2" -Force
$base | Add-Member -NotePropertyName save_weight_dir -NotePropertyValue "" -Force
$base.train.epochs = $Epochs
$base.train.batch_size = $BatchSize
$base.train.fp16_run = $false
$base.train.pretrained_s2G = $s2g
$base.train.pretrained_s2D = $s2d
$base.train.gpu_numbers = "0"
$base.train.if_save_latest = $true
$base.train.if_save_every_weights = $true
$base.train.save_every_epoch = 1
$base.train.grad_ckpt = $true
$base.model.version = "v2"
$base.data.exp_dir = $dataset
# GPT-SoVITS s2_train.py does not consistently honor s2_ckpt_dir: resume and
# checkpoint saves are hard-coded to <exp_dir>/logs_s2_<model.version>.  Keep
# the configured TensorBoard directory aligned with that upstream location and
# create it before training, otherwise the first CPU checkpoint fails after a
# full epoch when utils.my_save tries to move the temporary .pth file.
$s2CheckpointDir = Join-Path $dataset ("logs_s2_" + $base.model.version)
$base.s2_ckpt_dir = $s2CheckpointDir
$base.save_weight_dir = Join-Path $dataset "SoVITS_weights_$qualityRunName"
$base.name = $experimentName
$base.version = "v2"
$null = New-Item -ItemType Directory -Force -Path $s2CheckpointDir
$null = New-Item -ItemType Directory -Force -Path $base.save_weight_dir
$configPath = Join-Path $dataset "tmp_s2_cpu.json"
$configJson = $base | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $configJson, [System.Text.UTF8Encoding]::new($false))

Write-Host "[TRAIN] Starting SoVITS CPU fine-tune ($Epochs epoch(s), batch $BatchSize)."
if ($Epochs -lt 10) {
    Write-Warning "Epochs below 10 is a smoke test and is not expected to produce usable speech."
}
if ($ExpectedLanguage -eq "mixed") {
    Write-Host "[TRAIN] Verified mixed resume checkpoint at epoch $resumeEpoch; upstream should start epoch $($resumeEpoch + 1)."
}
Push-Location $voiceRoot
try {
    & $python "-s" "GPT_SoVITS\s2_train.py" "--config" $configPath *>&1 |
        Tee-Object -FilePath (Join-Path $logDir "04-sovits.log")
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) { throw "SoVITS training failed; see $logDir\04-sovits.log" }
Write-Host "[TRAIN] SoVITS fine-tune finished."
