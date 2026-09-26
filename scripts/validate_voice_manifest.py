"""Validate a GPT-SoVITS manifest before expensive preprocessing or training."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SUPPORTED_LANGUAGES = frozenset({"zh", "ja", "en", "mixed"})
_MIXED_LANGUAGES = frozenset({"zh", "ja"})
_SUPPORTED_AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a"})


def validate_manifest(
    manifest: Path,
    dataset_root: Path,
    *,
    expected_language: str,
    minimum_items: int = 1,
) -> int:
    if expected_language not in _SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported expected language: {expected_language!r}")
    if minimum_items < 1:
        raise ValueError("minimum_items must be at least 1")
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    audio_root = dataset_root / "audio"
    if not audio_root.is_dir():
        raise FileNotFoundError(audio_root)

    lines = [line.strip() for line in manifest.read_text(encoding="utf-8-sig").splitlines()]
    if len(lines) < minimum_items:
        raise ValueError(f"manifest has {len(lines)} item(s); expected at least {minimum_items}")

    seen_audio: set[str] = set()
    language_counts: dict[str, int] = {}
    kana_transcripts: dict[str, int] = {}
    for line_number, line in enumerate(lines, start=1):
        parts = line.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"line {line_number}: expected audio|speaker|language|text")
        audio_value, speaker, language, transcript = (part.strip() for part in parts)
        if not audio_value or not speaker or not transcript:
            raise ValueError(f"line {line_number}: audio, speaker and text are required")
        if "|" in transcript or "\n" in transcript or "\r" in transcript:
            raise ValueError(f"line {line_number}: transcript contains a manifest delimiter")
        allowed_languages = (
            _MIXED_LANGUAGES if expected_language == "mixed" else {expected_language}
        )
        if language not in allowed_languages:
            raise ValueError(
                f"line {line_number}: language {language!r} does not match "
                f"{expected_language!r}"
            )
        language_counts[language] = language_counts.get(language, 0) + 1
        if _KANA_RE.search(transcript):
            kana_transcripts[language] = kana_transcripts.get(language, 0) + 1
        if language == "zh" and not _CJK_RE.search(transcript):
            raise ValueError(
                f"line {line_number}: Chinese transcript must contain CJK text"
            )
        audio_path = Path(audio_value)
        if not audio_path.is_file():
            raise FileNotFoundError(f"line {line_number}: audio does not exist: {audio_path}")
        if audio_path.suffix.lower() not in _SUPPORTED_AUDIO_SUFFIXES:
            raise ValueError(f"line {line_number}: unsupported audio suffix")
        try:
            # Resolve symlinks before checking the parent so a link inside
            # audio/ cannot silently point at an older dataset.
            same_audio_directory = audio_path.resolve().parent.samefile(audio_root.resolve())
        except OSError:
            same_audio_directory = False
        if not same_audio_directory:
            raise ValueError(
                f"line {line_number}: audio must be directly inside {audio_root}"
            )
        audio_key = str(audio_path.resolve()).casefold()
        if audio_key in seen_audio:
            raise ValueError(f"line {line_number}: duplicate audio path")
        seen_audio.add(audio_key)
    # Japanese may contain kanji-only fragments, but a real multi-sentence
    # Japanese corpus should not be overwhelmingly kana-free.  This catches
    # the previous failure mode where Chinese filename descriptions were
    # mislabeled as Japanese transcripts.
    japanese_count = language_counts.get("ja", 0)
    japanese_with_kana = kana_transcripts.get("ja", 0)
    if japanese_count and japanese_with_kana * 2 < japanese_count:
        raise ValueError(
            "Japanese manifest failed script sanity check: fewer than half of "
            "the transcripts contain kana"
        )
    if expected_language == "mixed":
        missing = sorted(_MIXED_LANGUAGES - language_counts.keys())
        if missing:
            raise ValueError(
                "mixed manifest must contain at least one transcript for each "
                f"language: missing {', '.join(missing)}"
            )
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--expected-language", required=True, choices=sorted(_SUPPORTED_LANGUAGES))
    parser.add_argument("--minimum-items", type=int, default=1)
    args = parser.parse_args()
    count = validate_manifest(
        args.manifest,
        args.dataset_root,
        expected_language=args.expected_language,
        minimum_items=max(1, args.minimum_items),
    )
    print(f"validated {count} manifest items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
