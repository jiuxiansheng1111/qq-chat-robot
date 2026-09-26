[CmdletBinding()]
param(
    # Two epochs was only a smoke test and produced a near-silent checkpoint.
    # Ten epochs is the minimum useful CPU fine-tune for this six-minute set.
    [int]$Epochs = 10,
    [int]$BatchSize = 1
)

$ErrorActionPreference = "Stop"
$qualityRunName = "quality"
$projectRoot = Split-Path -Parent $PSScriptRoot
$voiceRootActual = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\qq-chatrobot-voice\GPT-SoVITS"))

# Python's Windows/PowerShell path handling can corrupt this user's CJK
# username when a long Unicode path is serialized into the training JSON.
# SUBST gives the external trainer ASCII-only aliases, while the real files
# remain in their normal locations.
if (-not (Test-Path -LiteralPath "R:\data\murasame_voice_dataset")) {
    & subst R: $projectRoot | Out-Null
}
if (-not (Test-Path -LiteralPath "V:\GPT-SoVITS\.venv_cpu\Scripts\python.exe")) {
    & subst V: ([System.IO.Path]::GetFullPath((Join-Path $projectRoot "..\qq-chatrobot-voice"))) | Out-Null
}
if (-not (Test-Path -LiteralPath "R:\data\murasame_voice_dataset")) { throw "Project alias R: could not be mapped to $projectRoot" }
if (-not (Test-Path -LiteralPath "V:\GPT-SoVITS\.venv_cpu\Scripts\python.exe")) { throw "Voice alias V: could not be mapped to $voiceRootActual" }

$projectAlias = "R:"
$voiceRoot = "V:\GPT-SoVITS"
$python = Join-Path $voiceRoot ".venv_cpu\Scripts\python.exe"
$dataset = Join-Path $projectAlias "data\murasame_voice_dataset"
$listPath = Join-Path $dataset "murasame.list"
$bertDir = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\chinese-roberta-wwm-ext-large"
$hubertDir = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\chinese-hubert-base"
$s2g = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s2G2333k.pth"
$s2d = Join-Path $voiceRoot "GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s2D2333k.pth"
$logDir = Join-Path $projectRoot "logs\murasame-training"
$null = New-Item -ItemType Directory -Force -Path $logDir

if (-not (Test-Path -LiteralPath $python)) { throw "CPU GPT-SoVITS Python not found: $python" }
if (-not (Test-Path -LiteralPath $listPath)) { throw "Dataset manifest not found: $listPath" }

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
    Invoke-Stage "01-text" "GPT_SoVITS\prepare_datasets\1-get-text.py" @{
        inp_text=$listPath; inp_wav_dir=""; exp_name="murasame_voice"; i_part="0"; all_parts="1";
        opt_dir=$dataset; bert_pretrained_dir=$bertDir; version="v2"
    }
    Copy-Item (Join-Path $dataset "2-name2text-0.txt") (Join-Path $dataset "2-name2text.txt") -Force
}

if (-not (Test-Path (Join-Path $dataset "4-cnhubert\murasame_0083.mp3.pt"))) {
    Invoke-Stage "02-hubert" "GPT_SoVITS\prepare_datasets\2-get-hubert-wav32k.py" @{
        inp_text=$listPath; inp_wav_dir=""; exp_name="murasame_voice"; i_part="0"; all_parts="1";
        opt_dir=$dataset; cnhubert_base_dir=$hubertDir
    }
}

if (-not (Test-Path (Join-Path $dataset "6-name2semantic.tsv"))) {
    Invoke-Stage "03-semantic" "GPT_SoVITS\prepare_datasets\3-get-semantic.py" @{
        inp_text=$listPath; exp_name="murasame_voice"; i_part="0"; all_parts="1"; opt_dir=$dataset;
        pretrained_s2G=$s2g; s2config_path="GPT_SoVITS/configs/s2.json"
    }
    $semanticPart = Join-Path $dataset "6-name2semantic-0.tsv"
    @("item_name` tsemantic_audio" -replace "`t ", "`t") + (Get-Content $semanticPart) |
        Set-Content (Join-Path $dataset "6-name2semantic.tsv") -Encoding utf8
}

$base = Get-Content (Join-Path $voiceRoot "GPT_SoVITS\configs\s2.json") -Raw | ConvertFrom-Json
$base.train | Add-Member -NotePropertyName pretrained_s2G -NotePropertyValue "" -Force
$base.train | Add-Member -NotePropertyName pretrained_s2D -NotePropertyValue "" -Force
$base.train | Add-Member -NotePropertyName gpu_numbers -NotePropertyValue "0" -Force
$base.train | Add-Member -NotePropertyName if_save_latest -NotePropertyValue $true -Force
$base.train | Add-Member -NotePropertyName if_save_every_weights -NotePropertyValue $true -Force
$base.train | Add-Member -NotePropertyName save_every_epoch -NotePropertyValue 1 -Force
$base.data | Add-Member -NotePropertyName exp_dir -NotePropertyValue "" -Force
$base.model | Add-Member -NotePropertyName version -NotePropertyValue "v2" -Force
$base | Add-Member -NotePropertyName name -NotePropertyValue "murasame_voice" -Force
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
$base.s2_ckpt_dir = Join-Path $dataset "logs_s2_v2_$qualityRunName"
$base.save_weight_dir = Join-Path $dataset "SoVITS_weights_$qualityRunName"
$base.name = "murasame_voice"
$base.version = "v2"
$null = New-Item -ItemType Directory -Force -Path $base.save_weight_dir
$configPath = Join-Path $dataset "tmp_s2_cpu.json"
$configJson = $base | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $configJson, [System.Text.UTF8Encoding]::new($false))

Write-Host "[TRAIN] Starting SoVITS CPU fine-tune ($Epochs epoch(s), batch $BatchSize)."
if ($Epochs -lt 10) {
    Write-Warning "Epochs below 10 is a smoke test and is not expected to produce usable speech."
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
