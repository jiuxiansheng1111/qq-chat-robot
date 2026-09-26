from pathlib import Path

import pytest

from scripts.validate_voice_manifest import validate_manifest


def _manifest(tmp_path: Path, transcripts: list[str], language: str = "ja") -> tuple[Path, Path]:
    dataset = tmp_path / "dataset"
    audio = dataset / "audio"
    audio.mkdir(parents=True)
    rows = []
    for index, transcript in enumerate(transcripts, start=1):
        clip = audio / f"clip_{index}.wav"
        clip.write_bytes(b"audio")
        rows.append(f"{clip}|murasame|{language}|{transcript}")
    manifest = dataset / "murasame.list"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest, dataset


def test_japanese_manifest_accepts_real_audio_and_kana(tmp_path: Path):
    manifest, dataset = _manifest(tmp_path, ["ありがとう。", "今日は元気です。"])
    assert validate_manifest(
        manifest, dataset, expected_language="ja", minimum_items=2
    ) == 2


def test_japanese_manifest_rejects_chinese_filename_descriptions(tmp_path: Path):
    manifest, dataset = _manifest(tmp_path, ["不要把我当小孩子", "谢谢你"])
    with pytest.raises(ValueError, match="fewer than half"):
        validate_manifest(manifest, dataset, expected_language="ja")


def test_manifest_rejects_stale_audio_path_from_another_dataset(tmp_path: Path):
    manifest, dataset = _manifest(tmp_path, ["ありがとう。"])
    other = tmp_path / "old" / "audio"
    other.mkdir(parents=True)
    old_clip = other / "clip.wav"
    old_clip.write_bytes(b"old")
    manifest.write_text(f"{old_clip}|murasame|ja|ありがとう。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="directly inside"):
        validate_manifest(manifest, dataset, expected_language="ja")


def test_mixed_manifest_requires_and_accepts_explicit_ja_and_zh_rows(tmp_path: Path):
    dataset = tmp_path / "dataset"
    audio = dataset / "audio"
    audio.mkdir(parents=True)
    japanese = audio / "ja.wav"
    chinese = audio / "zh.wav"
    japanese.write_bytes(b"audio")
    chinese.write_bytes(b"audio")
    manifest = dataset / "murasame.list"
    manifest.write_text(
        f"{japanese}|murasame|ja|今日は元気です。\n"
        f"{chinese}|murasame|zh|今天的天气很好。\n",
        encoding="utf-8",
    )

    assert validate_manifest(manifest, dataset, expected_language="mixed") == 2


def test_mixed_manifest_rejects_missing_language_or_non_chinese_zh_text(tmp_path: Path):
    manifest, dataset = _manifest(tmp_path, ["今日は元気です。"])
    with pytest.raises(ValueError, match="missing zh"):
        validate_manifest(manifest, dataset, expected_language="mixed")

    audio = dataset / "audio"
    chinese = audio / "zh.wav"
    chinese.write_bytes(b"audio")
    manifest.write_text(
        f"{audio / 'clip_1.wav'}|murasame|ja|今日は元気です。\n"
        f"{chinese}|murasame|zh|Hello there\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Chinese transcript must contain CJK"):
        validate_manifest(manifest, dataset, expected_language="mixed")
