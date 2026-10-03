import json
from argparse import Namespace

import numpy as np
import pytest

from app.services.singing_quality import (
    compute_quality_report,
    cosine_similarity,
    quality_failures,
)
from scripts import check_singing_quality


def _report(
    source_f0, converted_f0, *, audio=None, reference=None, converted=None,
    identity_reference=None, expected_semitone_shift=0, duration=2.0,
):
    if audio is None:
        audio = np.full(32000, 0.1)
    if reference is None:
        reference = np.array([1.0, 0.0])
    if converted is None:
        converted = np.array([1.0, 0.0])
    return compute_quality_report(
        np.asarray(source_f0, dtype=float),
        np.asarray(converted_f0, dtype=float),
        np.asarray(audio, dtype=float),
        2.0,
        duration,
        np.asarray(reference, dtype=float),
        np.asarray(converted, dtype=float),
        identity_reference_embedding=(
            np.asarray(identity_reference, dtype=float)
            if identity_reference is not None else None
        ),
        expected_semitone_shift=expected_semitone_shift,
    )


def test_good_singing_passes_all_checks():
    f0 = np.tile([220.0, 330.0, 440.0, 330.0], 50)
    report = _report(f0, f0)
    assert report["pitch_median_cents"] == 0
    assert report["pitch_within_semitone_ratio"] == 1
    assert report["voiced_recall"] == 1
    assert report["voice_similarity"] == 1
    assert report["duration_ratio"] == 1
    assert quality_failures(report) == []


def test_octave_shift_fails_pitch_even_when_all_frames_are_voiced():
    source = np.tile([220.0, 330.0, 440.0, 330.0], 50)
    report = _report(source, source * 2)
    assert report["pitch_median_cents"] >= 1200
    assert report["pitch_within_semitone_ratio"] < 0.70
    assert "转换后音高偏差过大" in quality_failures(report)


def test_flat_spoken_pitch_fails_melody_retention():
    source = np.tile([220.0, 330.0, 440.0, 330.0], 50)
    report = _report(source, np.full(source.shape, 200.0))
    assert report["pitch_within_semitone_ratio"] < 0.70
    assert "转换后保留的旋律音高比例不足" in quality_failures(report)


def test_missing_voiced_frames_and_accompaniment_fail():
    source = np.full(200, 220.0)
    converted = np.concatenate([np.full(30, 220.0), np.zeros(170)])
    report = _report(source, converted)
    assert report["voiced_recall"] < 0.70
    assert "转换后保留的有声帧比例不足" in quality_failures(report)

    no_vocal = _report(np.zeros(200), np.zeros(200))
    assert no_vocal["source_voiced_frames"] == 0
    assert no_vocal["pitch_median_cents"] is None
    assert "原始人声的有声帧不足" in quality_failures(no_vocal)


def test_voiced_precision_reports_conversion_only_voicing_without_changing_gate():
    source = np.concatenate([np.zeros(50), np.full(100, 220.0), np.zeros(50)])
    converted = np.concatenate([np.full(150, 220.0), np.zeros(50)])
    report = _report(source, converted)
    assert report["voiced_recall"] == 1
    assert report["voiced_precision"] == 100 / 150
    assert "转换后保留的有声帧比例不足" not in quality_failures(report)


def test_expected_pitch_shift_uses_transposed_source_and_reports_median_hz():
    source = np.full(200, 220.0)
    shift = 3
    converted = source * 2 ** (shift / 12)

    unshifted_report = _report(source, converted)
    shifted_report = _report(source, converted, expected_semitone_shift=shift)

    assert unshifted_report["pitch_median_cents"] == pytest.approx(300)
    assert shifted_report["pitch_median_cents"] == pytest.approx(0, abs=1e-8)
    assert shifted_report["source_voiced_median_f0_hz"] == pytest.approx(220)
    assert shifted_report["converted_voiced_median_f0_hz"] == pytest.approx(converted[0])
    assert shifted_report["expected_semitone_shift"] == 3
    assert "转换后音高偏差过大" not in quality_failures(shifted_report)


def test_pitch_residual_diagnostics_separate_source_motion_from_output_spike():
    frames = np.arange(300)
    # 源有自然起伏，转换正常时残差仍为零。
    source = 220 * 2 ** (0.35 * np.sin(2 * np.pi * 5 * frames / 100) / 12)
    converted = source.copy()
    natural = _report(source, converted)
    assert natural["source_pitch_step_p95_cents"] > 0
    assert natural["pitch_residual_step_p95_cents"] == pytest.approx(0, abs=1e-8)
    assert natural["pitch_residual_isolated_jump_count"] == 0
    assert not any("音高" in failure for failure in quality_failures(natural))

    converted[150] *= 2 ** (2 / 12)
    with_spike = _report(source, converted)
    assert with_spike["pitch_residual_isolated_jump_count"] == 1
    assert with_spike["pitch_residual_adjacent_pair_count"] == len(source) - 1
    # 该诊断不会额外变成接受门槛。
    assert "转换后音高抖动过多" not in quality_failures(with_spike)


def test_optional_energy_thresholds_fail_closed_only_when_configured():
    report = {"source_voiced_frames": 200}
    assert quality_failures(report) == [
        "转换后音高偏差过大",
        "转换后保留的旋律音高比例不足",
        "转换后保留的有声帧比例不足",
        "转换前后时长偏差过大",
        "转换后人声音量过低",
        "转换后人声削波过多",
        "转换后与目标音色相似度不足",
    ]
    assert "转换后人声能量覆盖比例不足" in quality_failures(
        report, min_energy_recall=0.90
    )
    assert "转换后连续缺失人声过长" in quality_failures(
        report, max_missing_vocal_seconds=1.2
    )


def test_identity_similarity_uses_held_out_embedding_and_keeps_prompt_score():
    f0 = np.full(200, 220.0)
    report = _report(
        f0, f0, reference=np.array([1.0, 0.0]),
        identity_reference=np.array([0.0, 1.0]), converted=np.array([0.0, 1.0]),
    )
    assert report["voice_similarity"] == 0
    assert report["identity_reference_similarity"] == 1


def test_identity_reference_cli_option_is_optional(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_singing_quality.py",
            "--seed-root", "seed",
            "--source", "source.wav",
            "--converted", "converted.wav",
            "--reference", "prompt.wav",
            "--identity-reference", "held-out.wav",
            "--expected-semitone-shift", "3",
            "--output", "quality.json",
        ],
    )
    args = check_singing_quality._parse_args()
    assert str(args.reference) == "prompt.wav"
    assert str(args.identity_reference) == "held-out.wav"
    assert args.expected_semitone_shift == 3


def test_cli_uses_current_vocal_coverage_threshold_defaults(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_singing_quality.py",
            "--seed-root", "seed",
            "--source", "source.wav",
            "--converted", "converted.wav",
            "--reference", "prompt.wav",
            "--output", "quality.json",
        ],
    )
    args = check_singing_quality._parse_args()
    assert args.min_voiced_recall == pytest.approx(0.88)
    assert args.min_energy_recall == pytest.approx(0.90)
    assert args.max_missing_vocal_seconds == pytest.approx(1.2)


def test_f0_delay_search_aligns_up_to_100_ms():
    source = np.linspace(180.0, 510.0, 200)
    converted = np.concatenate([np.zeros(7), source[:-7]])
    report = _report(source, converted)
    assert report["alignment_delay_ms"] == 70
    assert report["pitch_median_cents"] == 0
    assert report["voiced_recall"] > 0.95


def test_speaker_cosine_and_waveform_checks():
    assert cosine_similarity(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == 0
    assert cosine_similarity(np.array([0.0, 0.0]), np.array([1.0, 0.0])) is None
    f0 = np.full(200, 220.0)
    report = _report(
        f0,
        f0,
        audio=np.concatenate([np.ones(1000), np.zeros(31000)]),
        converted=np.array([0.0, 1.0]),
        duration=2.2,
    )
    failures = quality_failures(report)
    assert "转换前后时长偏差过大" in failures
    assert "转换后人声削波过多" in failures
    assert "转换后与目标音色相似度不足" in failures


def test_silent_output_fails_rms_and_missing_measurements_fail_closed():
    f0 = np.full(200, 220.0)
    report = _report(f0, f0, audio=np.zeros(32000))
    assert "转换后人声音量过低" in quality_failures(report)
    assert len(quality_failures({})) == 8


def test_cli_writes_failed_report_without_nonzero_exit_by_default(monkeypatch, tmp_path):
    output = tmp_path / "quality.json"
    args = Namespace(output=output, require_pass=False)
    monkeypatch.setattr(check_singing_quality, "_parse_args", lambda: args)
    monkeypatch.setattr(
        check_singing_quality,
        "_evaluate",
        lambda parsed: {"accepted": False, "failures": ["转换后音高偏差过大"]},
    )
    assert check_singing_quality.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["failures"] == [
        "转换后音高偏差过大"
    ]
    args.require_pass = True
    assert check_singing_quality.main() == 1
