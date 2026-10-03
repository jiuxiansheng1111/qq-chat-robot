import json
import os
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from app.services.singing_register import choose_octave_shift
from scripts import plan_singing_pitch


@pytest.mark.parametrize(
    ("source_hz", "target_hz", "expected"),
    [(200, 400, 12), (400, 400, 0), (800, 400, -12)],
)
def test_choose_octave_shift_selects_nearest_song_register(source_hz, target_hz, expected):
    result = choose_octave_shift(np.full(20, source_hz), target_hz)

    assert result["expected_semitone_shift"] == expected
    assert result["source_median_hz"] == pytest.approx(source_hz)
    assert result["predicted_median_hz"] == pytest.approx(source_hz * 2 ** (expected / 12))
    assert result["source_voiced_frames"] == 20


def test_choose_octave_shift_prefers_zero_on_equal_log_frequency_distance():
    geometric_midpoint = np.sqrt(200 * 400)

    result = choose_octave_shift([geometric_midpoint], 200)

    assert result["expected_semitone_shift"] == 0


def test_choose_octave_shift_reports_robust_voiced_statistics_and_invalid_frames():
    result = choose_octave_shift([180, 200, 220, 0, -1, np.nan, np.inf], 400)

    assert result["source_median_hz"] == pytest.approx(200)
    assert result["source_p10_hz"] == pytest.approx(184)
    assert result["source_p90_hz"] == pytest.approx(216)
    assert result["source_voiced_frames"] == 3
    assert result["source_total_frames"] == 7
    assert result["source_nonfinite_frames"] == 2
    assert result["source_unvoiced_frames"] == 2
    assert result["expected_semitone_shift"] == 12


@pytest.mark.parametrize("target", [float("nan"), float("inf"), 79.9, 1000.1, True, "400"])
def test_choose_octave_shift_rejects_invalid_targets(target):
    with pytest.raises((TypeError, ValueError), match="target_median_hz"):
        choose_octave_shift([200], target)


@pytest.mark.parametrize("f0", [[], [0, -1, np.nan], [np.inf, -np.inf], [[200, 210]]])
def test_choose_octave_shift_rejects_empty_unvoiced_or_malformed_f0(f0):
    with pytest.raises(ValueError, match="source_f0"):
        choose_octave_shift(f0, 400)


@pytest.mark.parametrize(("cuda_available", "expected_device"), [(False, "cpu"), (True, "cuda:0")])
def test_cli_uses_cached_seed_rmvpe_and_releases_selected_device(
    tmp_path, monkeypatch, capsys, cuda_available, expected_device,
):
    seed_root = tmp_path / "seed-vc"
    (seed_root / "modules").mkdir(parents=True)
    (seed_root / "modules" / "rmvpe.py").write_text("", encoding="utf-8")
    (seed_root / "hf_utils.py").write_text("", encoding="utf-8")
    source = tmp_path / "vocal.wav"
    source.write_bytes(b"mock")
    output = tmp_path / "report" / "plan.json"
    loaded = []
    f0_calls = []
    empty_cache_calls = []

    class FakeRMVPE:
        def __init__(self, checkpoint, *, is_half, device):
            loaded.append((checkpoint, is_half, device))

    torch_module = ModuleType("torch")
    torch_module.cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        empty_cache=lambda: empty_cache_calls.append(True),
    )
    librosa_module = ModuleType("librosa")
    librosa_module.load = lambda *args, **kwargs: (np.ones(32000, dtype=np.float32), 16000)
    hf_module = ModuleType("hf_utils")
    hf_module.load_custom_model_from_hf = lambda *args: "cached-rmvpe.pt"
    modules = ModuleType("modules")
    modules.__path__ = []
    rmvpe_module = ModuleType("modules.rmvpe")
    rmvpe_module.RMVPE = FakeRMVPE

    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "librosa", librosa_module)
    monkeypatch.setitem(sys.modules, "hf_utils", hf_module)
    monkeypatch.setitem(sys.modules, "modules", modules)
    monkeypatch.setitem(sys.modules, "modules.rmvpe", rmvpe_module)
    monkeypatch.setattr(
        "scripts.check_singing_quality._infer_f0",
        lambda model, audio: f0_calls.append((model, audio.size)) or np.full(200, 200.0),
    )

    code = plan_singing_pitch.main([
        "--seed-root", str(seed_root), "--source", str(source), "--target-hz", "400",
        "--output", str(output), "--offline",
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert report["accepted"] is True
    assert report["expected_semitone_shift"] == 12
    assert report["source_median_hz"] == 200
    assert loaded == [("cached-rmvpe.pt", False, expected_device)]
    assert f0_calls[0][1] == 32000
    assert empty_cache_calls == ([True] if cuda_available else [])
    assert json.loads(capsys.readouterr().out)["accepted"] is True
    assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
    assert os.environ["HF_ENDPOINT"] == "https://huggingface.co"
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_cli_invalid_source_writes_safe_json_and_returns_two(tmp_path, monkeypatch, capsys):
    seed_root = tmp_path / "missing-seed"
    seed_root.mkdir()
    output = tmp_path / "report.json"
    code = plan_singing_pitch.main([
        "--seed-root", str(seed_root), "--source", str(tmp_path / "missing.wav"),
        "--target-hz", "400", "--output", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert code == 2
    assert report["accepted"] is False
    assert report["error"] == "--seed-root is not a Seed-VC checkout"
    assert "missing.wav" not in capsys.readouterr().out
