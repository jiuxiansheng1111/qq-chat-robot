import json
from pathlib import Path

import pytest

from scripts.compare_singing_recheck import compare_phase_reports


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _phase(matrix_root: Path, name: str, *, cer: float, energy: float, lyrics: str):
    phase = matrix_root / name
    report = phase / "murasame" / "ja" / "report.json"
    _write_json(report, {
        "status": "passed",
        "profile_id": "murasame",
        "language": "ja",
        "song_id": "same-song",
        "lyrics": "data/sources/ja/excerpt.lrc",
        "vocals": "data/sources/ja/vocals.wav",
        "source_start_seconds": 10.0,
        "source_end_seconds": 30.0,
        "voice_pitch_shift_semitones": 12,
        "quality": {
            "accepted": True,
            "pitch_median_cents": 8.0,
            "vocal_energy_recall": energy,
            "max_missing_vocal_seconds": 0.25,
            "pitch_residual_step_p95_cents": 28.0,
            "pitch_residual_isolated_jump_count": 0,
        },
    })
    _write_json(phase / "manifest.json", {
        "items": {
            "murasame:ja": {
                "status": "passed",
                "report": str(report.relative_to(matrix_root)),
            }
        }
    })
    _write_json(phase / "lyrics_qa.json", {
        "items": [{
            "profile_id": "murasame",
            "language": "ja",
            "status": "completed",
            "converted_vs_lyrics": {"cer": cer},
            "source_vs_lyrics": {"cer": 0.1},
            "transcript_consistency": {"cer": 0.05},
            # 测试确保报告不会复制原始歌词/转写正文。
            "example_transcript": lyrics,
        }]
    })


def test_phase_comparison_reports_audio_and_lyrics_deltas_without_text(tmp_path):
    matrix_root = tmp_path / "run"
    _phase(matrix_root, "baseline", cer=0.20, energy=0.96, lyrics="私密歌词")
    _phase(matrix_root, "candidate", cer=0.10, energy=0.98, lyrics="私密歌词")

    report = compare_phase_reports(matrix_root)

    assert report["automatic_acceptance"] is False
    assert report["summary"]["comparable"] == 1
    item = report["items"][0]
    assert item["comparable"] is True
    assert item["deltas"]["converted_vs_lyrics_cer"]["delta_candidate_minus_baseline"] == -0.1
    assert item["deltas"]["vocal_energy_recall"]["delta_candidate_minus_baseline"] == pytest.approx(0.02)
    serialized = json.dumps(report, ensure_ascii=False)
    assert "私密歌词" not in serialized
    assert "example_transcript" not in serialized


def test_phase_comparison_marks_different_song_as_incomparable(tmp_path):
    matrix_root = tmp_path / "run"
    _phase(matrix_root, "baseline", cer=0.20, energy=0.96, lyrics="歌词")
    _phase(matrix_root, "candidate", cer=0.10, energy=0.98, lyrics="歌词")
    candidate_path = matrix_root / "candidate" / "murasame" / "ja" / "report.json"
    data = json.loads(candidate_path.read_text(encoding="utf-8"))
    data["song_id"] = "different-song"
    candidate_path.write_text(json.dumps(data), encoding="utf-8")

    report = compare_phase_reports(matrix_root)

    assert report["items"][0]["same_source_and_lyrics"] is False
    assert report["items"][0]["comparable"] is False


def test_missing_lyrics_qa_keeps_acoustic_comparison_available(tmp_path):
    matrix_root = tmp_path / "run"
    _phase(matrix_root, "baseline", cer=0.2, energy=0.96, lyrics="lyrics")
    _phase(matrix_root, "candidate", cer=0.1, energy=0.98, lyrics="lyrics")
    (matrix_root / "candidate" / "lyrics_qa.json").unlink()

    report = compare_phase_reports(matrix_root)

    assert report["items"][0]["comparable"] is True
    assert report["items"][0]["deltas"]["vocal_energy_recall"]["candidate"] == 0.98
    assert report["items"][0]["deltas"]["converted_vs_lyrics_cer"]["candidate"] is None


def test_matching_saved_input_audio_verifies_source_when_window_metadata_differs(tmp_path):
    matrix_root = tmp_path / "run"
    _phase(matrix_root, "baseline", cer=0.20, energy=0.96, lyrics="歌词")
    _phase(matrix_root, "candidate", cer=0.10, energy=0.98, lyrics="歌词")
    for phase in ("baseline", "candidate"):
        audio = matrix_root / phase / "murasame" / "ja" / "vocal_before.wav"
        audio.write_bytes(b"identical model input")
        report_path = matrix_root / phase / "murasame" / "ja" / "report.json"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        data["vocal_before"] = str(audio.relative_to(matrix_root))
        if phase == "candidate":
            data["source_end_seconds"] = 35.0
        report_path.write_text(json.dumps(data), encoding="utf-8")

    report = compare_phase_reports(matrix_root)

    item = report["items"][0]
    assert item["comparable"] is True
    assert item["source_evidence"]["input_audio_hashes_match"] is True
    assert item["source_evidence"]["reported_window_matches"] is False
    assert item["source_evidence"]["verification_basis"] == "phase_input_audio_sha256"


def test_different_saved_input_audio_is_incomparable_even_with_same_metadata(tmp_path):
    matrix_root = tmp_path / "run"
    _phase(matrix_root, "baseline", cer=0.20, energy=0.96, lyrics="歌词")
    _phase(matrix_root, "candidate", cer=0.10, energy=0.98, lyrics="歌词")
    for phase, content in (("baseline", b"first audio"), ("candidate", b"second audio")):
        audio = matrix_root / phase / "murasame" / "ja" / "vocal_before.wav"
        audio.write_bytes(content)
        report_path = matrix_root / phase / "murasame" / "ja" / "report.json"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        data["vocal_before"] = str(audio.relative_to(matrix_root))
        report_path.write_text(json.dumps(data), encoding="utf-8")

    report = compare_phase_reports(matrix_root)

    item = report["items"][0]
    assert item["comparable"] is False
    assert item["source_evidence"]["input_audio_hashes_match"] is False
    assert "input_audio_hash_mismatch" in item["incomparable_reasons"]
