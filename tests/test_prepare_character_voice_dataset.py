import json
from pathlib import Path

import pytest

from scripts.prepare_character_voice_dataset import prepare_dataset


def _row(file: str, language: str, text: str, *, reviewed: bool = True) -> str:
    return json.dumps(
        {"file": file, "language": language, "text": text, "human_verified": reviewed},
        ensure_ascii=False,
    )


def test_prepare_mixed_character_dataset_from_reviewed_jsonl(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "zh.wav").write_bytes(b"Chinese voice")
    (source / "ja.wav").write_bytes(b"Japanese voice")
    transcript = tmp_path / "reviewed.jsonl"
    transcript.write_text(
        _row("zh.wav", "zh", "你好。") + "\n" + _row("ja.wav", "ja", "こんにちは。") + "\n",
        encoding="utf-8",
    )
    count, manifest = prepare_dataset(
        transcript, source, tmp_path / "aimisi", speaker="aimisi", manifest_stem="aimisi"
    )
    assert count == 2
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("|aimisi|zh|你好。")
    assert lines[1].endswith("|aimisi|ja|こんにちは。")
    assert (tmp_path / "aimisi" / "audio" / "aimisi_0001.wav").is_file()


def test_rejects_escaping_path_and_does_not_publish_dataset(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "outside.wav").write_bytes(b"wrong voice")
    transcript = tmp_path / "reviewed.jsonl"
    transcript.write_text(_row("../outside.wav", "zh", "你好。") + "\n", encoding="utf-8")
    output = tmp_path / "dataset"
    with pytest.raises(ValueError, match="escapes source_root"):
        prepare_dataset(transcript, source, output, speaker="aimisi", manifest_stem="aimisi")
    assert not output.exists()


def test_rejects_missing_transcript_and_duplicate_audio(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.wav").write_bytes(b"voice")
    transcript = tmp_path / "reviewed.jsonl"
    transcript.write_text(_row("one.wav", "zh", " ") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed transcript"):
        prepare_dataset(
            transcript, source, tmp_path / "dataset", speaker="aimisi", manifest_stem="aimisi"
        )
    transcript.write_text(
        _row("one.wav", "zh", "你好。") + "\n" + _row("one.wav", "zh", "你好。") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate audio"):
        prepare_dataset(
            transcript, source, tmp_path / "dataset", speaker="aimisi", manifest_stem="aimisi"
        )


def test_asr_draft_is_not_silently_used_for_training(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.wav").write_bytes(b"voice")
    transcript = tmp_path / "asr-draft.jsonl"
    transcript.write_text(
        _row("one.wav", "zh", "可能误听的文本", reviewed=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="human_verified"):
        prepare_dataset(
            transcript, source, tmp_path / "dataset", speaker="aimisi", manifest_stem="aimisi"
        )
