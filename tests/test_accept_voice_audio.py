import wave
from pathlib import Path

import pytest

from scripts.accept_voice_audio import (
    apply_language_gates,
    overall_gate,
    run_case,
)


class _FixedAsr:
    def __init__(self, text: str):
        self.text = text

    def generate(self, *, input: str):
        return [{"text": self.text}]


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(32_000)
        output.writeframes(b"\0\0" * 320)
    return path


def test_overall_gate_passes_only_when_all_three_languages_pass():
    result = overall_gate(
        {"zh_asr_gate": True, "en_asr_gate": True, "ja_asr_gate": True}
    )

    assert result["overall_asr_gate"] is True
    assert result["missing_languages"] == []
    assert result["failed_languages"] == []


def test_overall_gate_fails_for_missing_or_failed_language():
    result = overall_gate({"zh_asr_gate": True, "en_asr_gate": False})

    assert result["overall_asr_gate"] is False
    assert result["missing_languages"] == ["ja"]
    assert result["failed_languages"] == ["en", "ja"]


def test_japanese_explicit_orthographic_candidate_preserves_raw_cer_and_passes_gate(
    tmp_path: Path,
):
    row = run_case(
        _FixedAsr("<|ja|>今日はうれしいです。"),
        _wav(tmp_path / "ja.wav"),
        {
            "file": "ja.wav",
            "language": "ja",
            "text": "今日は嬉しいです。",
            "accepted_transcriptions": ["今日はうれしいです。"],
        },
    )

    assert row["reference"] == "今日は嬉しいです。"
    assert row["cer"] > 0
    assert row["gate_reference"] == "今日はうれしいです。"
    assert row["matched_accepted_transcription"] == "今日はうれしいです。"
    assert row["gate_cer"] == 0

    summary: dict[str, object] = {}
    apply_language_gates(summary, [row], {"ja_cer_max": 0.05})
    assert summary["ja_aggregate_cer"] > 0
    assert summary["ja_aggregate_gate_cer"] == 0
    assert summary["ja_asr_gate"] is True


def test_chinese_gate_remains_based_on_original_cer_and_rejects_alternatives(
    tmp_path: Path,
):
    case = {"file": "zh.wav", "language": "zh", "text": "你好", "accepted_transcriptions": ["您好"]}
    with pytest.raises(ValueError, match="only for Japanese"):
        run_case(_FixedAsr("<|zh|>您好"), _wav(tmp_path / "zh.wav"), case)


def test_english_gate_rejects_bad_single_sample_despite_passing_aggregate_wer():
    summary: dict[str, object] = {}
    report = [
        {
            "expected_language": "en",
            "reference_words": 5,
            "word_errors": 2,
            "wer": 0.4,
            "language_match": True,
        },
        {
            "expected_language": "en",
            "reference_words": 15,
            "word_errors": 0,
            "wer": 0,
            "language_match": True,
        },
    ]

    apply_language_gates(
        summary,
        report,
        {"en_wer_max": 0.10, "en_single_wer_max": 0.10},
    )

    assert summary["en_aggregate_wer"] == 0.10
    assert summary["en_asr_gate"] is False
