import zipfile
from pathlib import Path

import pytest

from scripts.prepare_murasame_voice_dataset import (
    load_ordered_transcripts,
    prepare_dataset,
)


def _voice_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("不要把我当小孩子 01.mp3", b"first")
        archive.writestr("谢谢 02.wav", b"second")
        archive.writestr("readme.txt", b"ignored")
    return path


def test_real_ordered_transcripts_are_written_to_manifest(tmp_path: Path):
    source = _voice_zip(tmp_path / "voice.zip")
    count, manifest = prepare_dataset(
        source,
        tmp_path / "dataset",
        speaker="murasame",
        language="ja",
        transcripts=["子供扱いしないで。", "ありがとう。"],
    )

    assert count == 2
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("|murasame|ja|子供扱いしないで。")
    assert lines[1].endswith("|murasame|ja|ありがとう。")


def test_filename_descriptions_are_never_used_implicitly(tmp_path: Path):
    source = _voice_zip(tmp_path / "voice.zip")
    with pytest.raises(ValueError, match="real transcripts are required"):
        prepare_dataset(
            source,
            tmp_path / "dataset",
            speaker="murasame",
            language="ja",
        )


def test_transcript_count_must_match_audio_count(tmp_path: Path):
    source = _voice_zip(tmp_path / "voice.zip")
    with pytest.raises(ValueError, match="transcript count does not match"):
        prepare_dataset(
            source,
            tmp_path / "dataset",
            speaker="murasame",
            language="ja",
            transcripts=["一件だけ。"],
        )


def test_transcript_file_rejects_blank_alignment_rows(tmp_path: Path):
    transcript_file = tmp_path / "transcripts.txt"
    transcript_file.write_text("一件目。\n\n二件目。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one non-empty line"):
        load_ordered_transcripts(transcript_file)
