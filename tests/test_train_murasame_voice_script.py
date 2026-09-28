from pathlib import Path


def test_mixed_training_mode_uses_a_new_dataset_and_owns_its_cache_stamp():
    script = Path("scripts/train_murasame_voice.ps1").read_text(encoding="utf-8")

    assert '[ValidateSet("ja", "zh", "en", "mixed")]' in script
    assert '$ExpectedLanguage -eq "mixed" -and $DatasetName -eq "murasame_voice_dataset_ja"' in script
    assert '$ExpectedLanguage -eq "mixed"' in script
    assert '$manifestHash = "language=mixed;audio=$audioFingerprint;$manifestHash"' in script


def test_mixed_training_binds_cache_to_audio_bytes_and_refuses_pretrained_fallback():
    script = Path("scripts/train_murasame_voice.ps1").read_text(encoding="utf-8")

    assert "$audioFingerprintRows = [System.Collections.Generic.List[string]]::new()" in script
    assert "Get-FileHash -LiteralPath $actualAudioPath -Algorithm SHA256" in script
    assert '"language=mixed;audio=$audioFingerprint;$manifestHash"' in script
    assert "function Assert-MixedResumeCheckpoint" in script
    assert "RESUME_EPOCH=" in script
    assert "refusing upstream pretrained fallback" in script
    assert '$Epochs -le $resumeEpoch' in script


def test_each_training_run_gets_its_own_log_directory():
    script = Path("scripts/train_murasame_voice.ps1").read_text(encoding="utf-8")

    assert 'Get-Date -Format "yyyyMMdd-HHmmss-fff"' in script
    assert '[guid]::NewGuid().ToString("N").Substring(0, 6)' in script
    assert '"logs\\murasame-training\\" + $DatasetName + "\\" + $runStamp' in script
    assert '[TRAIN] Per-run logs:' in script


def test_sovits_wrapper_logs_native_stderr_without_treating_warnings_as_failure():
    script = Path("scripts/train_murasame_voice.ps1").read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Continue"' in script
    assert '& $python -s "GPT_SoVITS\\s2_train.py" --config $configPath 2>&1' in script
    assert "$ErrorActionPreference = $originalErrorActionPreference" in script
    assert "$trainerExitCode = $LASTEXITCODE" in script
    assert "if ($trainerExitCode -ne 0)" in script


def test_character_training_requires_unique_name_and_explicit_mixed_base_opt_in():
    script = Path("scripts/train_murasame_voice.ps1").read_text(encoding="utf-8")

    assert '[string]$ManifestStem = "murasame"' in script
    assert '[string]$ExperimentName = ""' in script
    assert '[switch]$TrainMixedFromBase' in script
    assert 'if ($ManifestStem -ne "murasame" -and -not $ExperimentName)' in script
    assert 'if ($ExpectedLanguage -eq "mixed" -and -not $TrainMixedFromBase)' in script
    assert 'Base-model training requires a new dataset without existing checkpoints' in script
