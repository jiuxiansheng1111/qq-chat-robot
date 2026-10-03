from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import train_singing_voices as training
from train_singing_voices import (
    _safe_run_name,
    build_training_config,
    build_training_environment,
    latest_run_checkpoint,
    parse_training_manifest,
    resolve_inference_assets,
    save_candidate_registry,
    validate_checkpoint_signature,
    validate_run_architecture,
)


def test_parse_training_manifest_resolves_basename_and_deduplicates(tmp_path: Path) -> None:
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    (audio_root / "clip_a.wav").write_bytes(b"wav")
    manifest = tmp_path / "train.list"
    manifest.write_text(
        "clip_a.wav|speaker|ja|one\nclip_a.wav|speaker|ja|duplicate\n",
        encoding="utf-8",
    )

    assert parse_training_manifest(manifest, [audio_root]) == [
        (audio_root / "clip_a.wav").resolve()
    ]


def test_parse_training_manifest_rejects_missing_audio(tmp_path: Path) -> None:
    manifest = tmp_path / "train.list"
    manifest.write_text("missing.wav|speaker|ja|text\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="cannot resolve"):
        parse_training_manifest(manifest, [tmp_path / "audio"])


def test_registry_adds_candidate_and_preserves_accepted_registry(tmp_path: Path) -> None:
    registry = tmp_path / "candidates.json"
    first = {"run_name": "murasame-svc-round1", "status": "candidate_requires_human_review"}
    save_candidate_registry(registry, "murasame", first)
    assert json.loads(registry.read_text(encoding="utf-8"))["murasame"] == first

    round_two = {**first, "steps": 300}
    save_candidate_registry(registry, "murasame", round_two)
    assert json.loads(registry.read_text(encoding="utf-8"))["murasame"] == round_two

    other_run = {"run_name": "murasame-svc-round2", "status": "candidate_requires_human_review"}
    with pytest.raises(FileExistsError, match="already has a registry entry"):
        save_candidate_registry(registry, "murasame", other_run)


def test_candidate_registry_does_not_touch_accepted_voice_registry(tmp_path: Path) -> None:
    accepted_registry = tmp_path / "voices.json"
    accepted = {"murasame": {"status": "accepted", "checkpoint": "accepted.pth"}}
    accepted_registry.write_text(json.dumps(accepted), encoding="utf-8")

    save_candidate_registry(
        tmp_path / "candidates.json",
        "murasame",
        {"run_name": "murasame-svc-round1", "status": "candidate_requires_human_review"},
    )

    assert json.loads(accepted_registry.read_text(encoding="utf-8")) == accepted


def test_run_names_are_ascii_and_path_safe() -> None:
    assert _safe_run_name("mako-svc-round_2") == "mako-svc-round_2"
    with pytest.raises(ValueError):
        _safe_run_name("../mako")


def test_training_environment_overrides_global_hf_mirror_only_for_child() -> None:
    parent_env = {"HF_ENDPOINT": "https://hf-mirror.com"}
    child = build_training_environment(parent_env)
    assert child["HF_ENDPOINT"] == "https://huggingface.co"
    assert child["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
    assert parent_env == {"HF_ENDPOINT": "https://hf-mirror.com"}


def test_training_environment_honors_explicit_endpoint_override() -> None:
    child = build_training_environment(
        {"HF_ENDPOINT": "https://hf-mirror.com", "SINGING_HF_ENDPOINT": "https://mirror.example"}
    )
    assert child["HF_ENDPOINT"] == "https://mirror.example"


def _model_config(hidden: int = 768, *, timbre: bool = False) -> dict:
    model = {
        "DiT": {"hidden_dim": hidden, "f0_condition": True},
        "length_regulator": {
            "channels": hidden,
            "in_channels": hidden,
            "f0_condition": True,
        },
        "speech_tokenizer": {"name": "openai/whisper-small"},
    }
    if timbre:
        model["timbre_shifter"] = {"ckpt_path": "local/training/path"}
    return {
        "preprocess_params": {"sr": 44100, "spect_params": {"hop_length": 512}},
        "model_params": model,
        "pretrained_model": "older-checkpoint.pth",
    }


def test_training_config_keeps_inference_architecture_and_merges_training_only_fields(
    tmp_path: Path,
) -> None:
    # A deliberately different preset catches accidental use of its model dimensions.
    inference = tmp_path / "inference.yml"
    preset = tmp_path / "preset.yml"
    inference.write_text(yaml.safe_dump(_model_config(hidden=512)), encoding="utf-8")
    preset_config = _model_config(hidden=768, timbre=True)
    preset_config["loss_params"] = {"base_lr": 0.0001}
    preset.write_text(yaml.safe_dump(preset_config), encoding="utf-8")
    config = build_training_config(inference, preset, tmp_path / "logs")
    assert config["model_params"]["DiT"]["hidden_dim"] == 512
    assert config["model_params"]["length_regulator"]["channels"] == 512
    assert config["model_params"]["timbre_shifter"] == {"ckpt_path": "local/training/path"}
    assert config["loss_params"] == {"base_lr": 0.0001}
    assert config["pretrained_model"] == ""
    assert config["log_dir"] == str(tmp_path / "logs")


def test_cached_v2_assets_are_paired_in_one_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "checkpoints" / "models--Plachta--Seed-VC" / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    config = snapshot / training.INFERENCE_CONFIG
    weights = snapshot / training.INFERENCE_CHECKPOINT
    config.write_text("model_params: {}", encoding="utf-8")
    weights.write_bytes(b"cached")
    assert resolve_inference_assets(tmp_path) == (config, weights)


def test_run_architecture_rejects_old_model_or_fallback_pretrained(tmp_path: Path) -> None:
    inference = tmp_path / "inference.yml"
    run = tmp_path / "run.yml"
    inference.write_text(yaml.safe_dump(_model_config()), encoding="utf-8")
    old = _model_config(hidden=512, timbre=True)
    old["pretrained_model"] = ""
    run.write_text(yaml.safe_dump(old), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        validate_run_architecture(run, inference)
    new = _model_config(hidden=768, timbre=True)
    run.write_text(yaml.safe_dump(new), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        validate_run_architecture(run, inference)
    new["pretrained_model"] = ""
    run.write_text(yaml.safe_dump(new), encoding="utf-8")
    assert validate_run_architecture(run, inference) == new


def test_checkpoint_signature_rejects_wrong_tensor_shapes(
    monkeypatch, tmp_path: Path
) -> None:
    checkpoint = tmp_path / "small.pth"
    checkpoint.write_bytes(b"test checkpoint")
    net = {
        "cfm": {
            "module.estimator.transformer.layers.0.attention.wqkv.weight":
                SimpleNamespace(shape=(6, 2))
        },
        "length_regulator": {"module.model.0.weight": SimpleNamespace(shape=(2, 2, 3))},
    }
    calls = []

    def fake_load(path, **kwargs):
        calls.append((path, kwargs))
        return {"net": net}

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(load=fake_load))
    matching = _model_config(hidden=2, timbre=True)
    validate_checkpoint_signature(checkpoint, matching)
    with pytest.raises(ValueError, match="incompatible"):
        validate_checkpoint_signature(checkpoint, _model_config(hidden=3, timbre=True))
    assert calls == [
        (checkpoint, {"map_location": "cpu", "weights_only": False, "mmap": True}),
        (checkpoint, {"map_location": "cpu", "weights_only": False, "mmap": True}),
    ]


def test_resume_uses_newest_final_or_intermediate_weights(tmp_path: Path) -> None:
    final = tmp_path / "ft_model.pth"
    intermediate = tmp_path / "DiT_epoch_00001_step_00050.pth"
    intermediate.write_bytes(b"first")
    final.write_bytes(b"final")
    assert latest_run_checkpoint(tmp_path) == final
    import os

    newer = final.stat().st_mtime_ns + 1_000_000_000
    os.utime(intermediate, ns=(newer, newer))
    assert latest_run_checkpoint(tmp_path) == intermediate


def test_first_round_passes_explicit_v2_weights_and_exact_step_count(
    monkeypatch, tmp_path: Path
) -> None:
    singing_root = tmp_path / "singing"
    seed_root = singing_root / "runtime" / "seed-vc"
    preset = seed_root / training.PRESET_RELATIVE
    preset.parent.mkdir(parents=True)
    preset.write_text(yaml.safe_dump(_model_config(timbre=True)), encoding="utf-8")
    (seed_root / "train.py").write_text("", encoding="utf-8")
    inference = tmp_path / "inference.yml"
    inference.write_text(yaml.safe_dump(_model_config()), encoding="utf-8")
    pretrained = tmp_path / "v2.pth"
    pretrained.write_bytes(b"v2")
    runtime_python = tmp_path / "python.exe"
    runtime_python.write_bytes(b"python")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    manifest = tmp_path / "train.list"
    manifest.write_text("clip.wav|speaker|ja|text", encoding="utf-8")
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"clip")
    monkeypatch.setattr(training, "SINGING_ROOT", singing_root)
    monkeypatch.setattr(training, "SEED_ROOT", seed_root)
    monkeypatch.setattr(training, "_read_settings", lambda: ({"yoshino": {}}, {}))
    monkeypatch.setattr(
        training, "discover_training_sources",
        lambda *args, **kwargs: (manifest, [clip], reference),
    )
    monkeypatch.setattr(training, "resolve_inference_assets", lambda *args: (inference, pretrained))
    monkeypatch.setattr(training, "validate_checkpoint_signature", lambda *args: None)
    monkeypatch.setattr(training, "_soundfile_info", lambda path: (5.0, 44100, 1))
    monkeypatch.setattr(
        training,
        "prepare_audio_dataset",
        lambda *args, **kwargs: {"clips": [{"seconds": 5.0}]},
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        checkpoint = singing_root / "checkpoints" / "yoshino" / "round1" / "ft_model.pth"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"output")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(training.subprocess, "run", fake_run)
    assert training.main([
        "--profile", "yoshino", "--run-name", "round1", "--python", str(runtime_python),
        "--ffmpeg", str(ffmpeg), "--max-steps", "100",
    ]) == 0
    command = captured["command"]
    assert command[command.index("--pretrained-ckpt") + 1] == str(pretrained.resolve())
    assert command[command.index("--max-steps") + 1] == "100"
    assert command[command.index("--save-every") + 1] == "50"
