import json
from pathlib import Path

import pytest

from scripts.prepare_character_voice_dataset import prepare_dataset


def _row(
    file: str,
    language: str,
    text: str,
    *,
    reviewed: bool = True,
    source_verified: bool = False,
) -> str:
    return json.dumps(
        {
            "file": file,
            "language": language,
            "text": text,
            "human_verified": reviewed,
            "source_text_verified": source_verified,
        },
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


def test_source_verified_pilot_requires_explicit_opt_in_and_preserves_provenance(
    tmp_path: Path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.wav").write_bytes(b"voice")
    transcript = tmp_path / "source-verified.jsonl"
    transcript.write_text(
        _row(
            "one.wav",
            "zh",
            "有公开语音槽位逐字稿且 ASR 已交叉核对。",
            reviewed=False,
            source_verified=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="human_verified"):
        prepare_dataset(
            transcript, source, tmp_path / "strict", speaker="aimisi", manifest_stem="aimisi"
        )

    output = tmp_path / "pilot"
    with pytest.warns(UserWarning, match="PILOT / 未听审"):
        count, manifest = prepare_dataset(
            transcript,
            source,
            output,
            speaker="aimisi",
            manifest_stem="aimisi",
            allow_source_verified_pilot=True,
        )

    provenance = json.loads(
        (output / "aimisi.provenance.json").read_text(encoding="utf-8")
    )
    assert count == 1
    assert manifest.is_file()
    assert provenance["dataset_mode"] == "PILOT_NOT_HUMAN_REVIEWED"
    assert "PILOT / 未听审" in provenance["warning"]
    assert provenance["clips"] == [
        {
            "file": "audio/aimisi_0001.wav",
            "source_text_verified": True,
            "human_verified": False,
        }
    ]


def test_pilot_rejects_unverified_or_non_boolean_human_status(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.wav").write_bytes(b"voice")
    transcript = tmp_path / "invalid-pilot.jsonl"
    transcript.write_text(
        _row("one.wav", "zh", "未经来源核对的文本", reviewed=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_text_verified"):
        prepare_dataset(
            transcript,
            source,
            tmp_path / "pilot",
            speaker="aimisi",
            manifest_stem="aimisi",
            allow_source_verified_pilot=True,
        )

    transcript.write_text(
        json.dumps(
            {
                "file": "one.wav",
                "language": "zh",
                "text": "人类审核状态不是布尔值。",
                "source_text_verified": True,
                "human_verified": "false",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="human_verified=false"):
        prepare_dataset(
            transcript,
            source,
            tmp_path / "invalid-human-status",
            speaker="aimisi",
            manifest_stem="aimisi",
            allow_source_verified_pilot=True,
        )
