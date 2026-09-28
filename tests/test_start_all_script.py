from pathlib import Path


def test_routed_sidecar_never_gets_legacy_startup_weight_on_existing_or_fresh_start():
    script = Path("scripts/start_all.ps1").read_text(encoding="utf-8")

    assert "$routedWeightsConfigured" in script
    assert 'Settings["VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON"]' in script
    assert 'Settings["VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON"]' in script
    assert script.count("if (-not $routedWeightsConfigured)") == 2
    ready_branch = script.index("if (Wait-LocalPort -Port $voicePort -TimeoutSeconds 90)")
    assert script.index("if (-not $routedWeightsConfigured)", ready_branch) < script.index(
        "Apply-GptSovitsWeights", ready_branch
    )


def test_explicit_startup_weight_must_synchronize_instead_of_only_warning():
    script = Path("scripts/start_all.ps1").read_text(encoding="utf-8")

    assert 'throw "Configured SoVITS weights not found: $weightsPath"' in script
    assert "Failed to synchronize configured SoVITS weights" in script
    assert "Invoke-RestMethod -Uri $endpoint -Method Get -TimeoutSec 180 -ErrorAction Stop" in script


def test_sidecar_uses_ascii_temp_without_changing_parent_environment():
    script = Path("scripts/start_all.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $env:PUBLIC "GPTSoVITS_temp"' in script
    assert "$env:TEMP = $voiceTempRoot" in script
    assert "$env:TMP = $voiceTempRoot" in script
    assert "$env:TEMP = $previousTemp" in script
    assert "$env:TMP = $previousTmp" in script


def test_sidecar_process_on_another_port_does_not_block_configured_port_start():
    script = Path("scripts/start_all.ps1").read_text(encoding="utf-8")

    assert "$targetPortPattern" in script
    assert "$explicitPortPattern" in script
    assert "CommandLine -match $targetPortPattern" in script
    assert "CommandLine -notmatch $explicitPortPattern" in script
    assert '"[VOICE] GPT-SoVITS for port $voicePort exists; waiting for it..."' in script
