[CmdletBinding()]
param(
    # 两轮训练仅是冒烟测试，生成的检查点几乎没有声音。
    # 这组 6 分钟音频至少需要在 CPU 上微调 10 轮才有实际效果。
    [int]$Epochs = 10,
    [int]$BatchSize = 1,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$DatasetName = "murasame_voice_dataset_ja",
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$ManifestStem = "murasame",
    [ValidatePattern('^[A-Za-z0-9_-]*$')]
    [string]$ExperimentName = "",
    # mixed 模式每行只能标记 ja 或 zh，且两种语言都必须出现。
    # 必须使用新的数据集目录，避免改动单语缓存。
    [ValidateSet("ja", "zh", "en", "mixed")]
    [string]$ExpectedLanguage = "ja",
    # 新角色没有旧的 e10 检查点。显式从基础模型开始 mixed 训练；
    # 旧村雨的 mixed 恢复检查仍保留。
    [switch]$TrainMixedFromBase,
    # 留空时会选择一个空闲的 ASCII SUBST 盘符，
    # 不会占用已有映射。也可以显式指定，例如 T:。
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
if ($ManifestStem -ne "murasame" -and -not $ExperimentName) {
    throw "A non-Murasame manifest requires an explicit unique -ExperimentName."
}
if ($TrainMixedFromBase -and $ExpectedLanguage -ne "mixed") {
    throw "-TrainMixedFromBase only applies to -ExpectedLanguage mixed."
}
if ($TrainMixedFromBase) {
    $existingCheckpointRoot = Join-Path $datasetActual "logs_s2_v2"
    if ((Test-Path -LiteralPath $existingCheckpointRoot) -and
        @(Get-ChildItem -LiteralPath $existingCheckpointRoot -File -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "Base-model training requires a new dataset without existing checkpoints: $existingCheckpointRoot"
    }
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
    # SUBST 盘符的目标格式为 \??\C:\...。物理盘和网络盘使用
    # \Device\HarddiskVolume... 等设备名，不会被重新映射。
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
    # 只有 subst 能显示现有盘符别名对应的物理目标。
    # 仅凭熟悉的子目录名判断并不安全：旧版
    # 检出目录也可能有同名数据集。
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
        # Windows PowerShell 5.1 会在进程启动时缓存文件系统盘符。刚创建的 SUBST
        # 盘符对原生命令可见，但可能还不是 PowerShell
        # Provider 盘符，导致 Join-Path 失败。
        New-PSDrive -Name $name -PSProvider FileSystem -Root ($normalizedDrive + "\") -Scope Script -ErrorAction Stop | Out-Null
    }
}

# Python 在 Windows/PowerShell 下处理路径时，可能会破坏此用户的中文
# 用户名，尤其是把长 Unicode 路径写入训练 JSON 时。
# SUBST 可让外部训练器使用纯 ASCII 路径别名，
# 实际文件仍保留在原位置。
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
$sourceListPath = Join-Path $datasetActual ($ManifestStem + ".list")
$listPath = Join-Path $dataset ($ManifestStem + ".train.list")
$experimentName = if ($ExperimentName) { $ExperimentName } else { "murasame_voice_$ExpectedLanguage" }
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

    # 上游 s2_train.py 用裸 except 包住了两处检查点加载。
    # 检查点缺失、不匹配或损坏时，流程会静默改用
    # 第 1 轮的基础预训练权重。mixed 训练明确是
    # 从日语 e10 训练继续，因此必须在耗时的
    # 预处理前失败，避免意外从头训练。
    # 校验器保存在 ASCII 源文件中。Windows PowerShell 5.1 可能拆坏
    # 通过 -c 传入的含引号 Python 代码（包括 f-string），
    # 把明确的校验失败误报成 Python 语法错误。
    $validatorPath = Join-Path $projectRoot "scripts\validate_mixed_resume_checkpoint.py"
    if (-not (Test-Path -LiteralPath $validatorPath -PathType Leaf)) {
        throw "Mixed resume checkpoint validator is missing: $validatorPath"
    }
    $resumeCheckOutput = @(& $python $validatorPath $CheckpointDirectory 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Mixed training requires valid paired G/D e10 resume checkpoints in $CheckpointDirectory; refusing upstream pretrained fallback."
    }
    $epochLines = @($resumeCheckOutput | Where-Object { $_ -match "^RESUME_EPOCH=\d+$" })
    if ($epochLines.Count -ne 1) {
        throw "Could not determine mixed resume epoch from checkpoint validation."
    }
    return [int]$epochLines[0].Substring("RESUME_EPOCH=".Length)
}

if ($ExpectedLanguage -eq "mixed" -and -not $TrainMixedFromBase) {
    $resumeEpoch = Assert-MixedResumeCheckpoint (Join-Path $dataset "logs_s2_v2")
    if ($Epochs -le $resumeEpoch) {
        throw "Epochs ($Epochs) must be greater than mixed resume epoch ($resumeEpoch)."
    }
}

# 规范化路径前先校验源清单。只检查文件名会让
# 旧数据集的清单可能借用新目录中的同名文件，
# 进而破坏音频与文本的来源记录。
& $python (Join-Path $projectRoot "scripts\validate_voice_manifest.py") $sourceListPath $datasetActual `
    --expected-language $ExpectedLanguage --minimum-items 10
if ($LASTEXITCODE -ne 0) { throw "Source dataset manifest validation failed" }
$sourceManifestHash = (Get-FileHash -LiteralPath $sourceListPath -Algorithm SHA256).Hash

# 预处理工具会写入普通绝对路径。只把训练副本
# 转成 R:，避免 GPT-SoVITS 收到 Windows 用户的中文目录路径。
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
    # 源列表记录了路径和文本，但整理时可能替换重新剪辑的 WAV，
    # 而路径和文本都不变。mixed 训练要把缓存同时
    # 绑定到清单和文件实际内容。
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

# 上游 GPT-SoVITS 只按约定管理预处理内容缓存。
# 清单变化时拒绝混用缓存，否则可能生成
# 表面成功、实际音质有误的检查点。
$trainingManifestHash = (Get-FileHash -LiteralPath $listPath -Algorithm SHA256).Hash
# mixed 缓存还会记录语言策略。保留旧的
# 单语标记，确保字节内容完全不变，避免
# 这个新入口让现有日语预处理缓存失效。
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
# 完整版 G2PW 包会单独下载。训练预处理可先用
# 附带的 pypinyin 路径，之后继续运行也无需重新下载。
$env:is_g2pw = "false"

function Invoke-Stage {
    param([string]$Name, [string]$Script, [hashtable]$Environment)
    $log = Join-Path $logDir "$Name.log"
    foreach ($item in $Environment.GetEnumerator()) { Set-Item "Env:$($item.Key)" $item.Value }
    Push-Location $voiceRoot
    $previousErrorActionPreference = $ErrorActionPreference
    $stageExitCode = 0
    try {
        # 部分上游 Python 依赖会把无害的弃用提示写到 stderr。
        # 在脚本级设置 Stop 时，PowerShell 会把这些
        # 原生 stderr 内容提升为终止错误，子进程
        # 还没结束就中断流程。让此阶段继续运行，
        # 最后根据 Python 的实际退出码判断是否成功。
        $ErrorActionPreference = "Continue"
        & $python $Script *>&1 | Tee-Object -FilePath $log
        $stageExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
    if ($stageExitCode -ne 0) { throw "$Name failed with exit code $stageExitCode; see $log" }
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
# GPT-SoVITS 的 s2_train.py 不总是遵守 s2_ckpt_dir：恢复和
# 保存检查点时会固定使用 <exp_dir>/logs_s2_<model.version>。
# TensorBoard 目录必须与上游路径一致，并在训练前
# 创建；否则 utils.my_save 移动临时 .pth 文件时，
# 可能在完整训练一轮后才失败。
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
if ($ExpectedLanguage -eq "mixed" -and -not $TrainMixedFromBase) {
    Write-Host "[TRAIN] Verified mixed resume checkpoint at epoch $resumeEpoch; upstream should start epoch $($resumeEpoch + 1)."
} elseif ($ExpectedLanguage -eq "mixed") {
    Write-Host "[TRAIN] Explicit new-character mixed run from the pretrained base."
}
Push-Location $voiceRoot
try {
    # Windows PowerShell 5.1 会把原生 stderr 记录为错误。PyTorch
    # 即使检查点已成功保存，也可能输出无害的 c10d 警告，
    # 所以将警告留在日志中，并以 Python 原生命令退出码为准。
    # 直接调用可保留带引号的路径，避免 cmd.exe /s /c
    # 去掉命令字符串中的引号。
    $originalErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $python -s "GPT_SoVITS\s2_train.py" --config $configPath 2>&1 |
            Tee-Object -FilePath (Join-Path $logDir "04-sovits.log")
        $trainerExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $originalErrorActionPreference
    }
} finally {
    Pop-Location
}
if ($trainerExitCode -ne 0) { throw "SoVITS training failed; see $logDir\04-sovits.log" }
Write-Host "[TRAIN] SoVITS fine-tune finished."
