"""Build an isolated character voice dataset from reviewed per-clip transcripts.

The source audio and the JSONL transcript file are local, authorized inputs.
Never infer text from a filename or silently merge speakers/languages. Keep the
result below an ignored local directory; do not commit audio or transcripts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_LANGUAGES = frozenset({"zh", "ja", "en"})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a"})
_MAX_AUDIO_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _safe_name(value: str, label: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} must contain only ASCII letters, digits, _ or -")
    return value


def read_reviewed_rows(manifest: Path, source_root: Path) -> list[tuple[Path, str, str]]:
    """Resolve each JSONL source under source_root; reject unsafe/incomplete rows."""
    root = source_root.resolve(strict=True)
    rows: list[tuple[Path, str, str]] = []
    seen: set[Path] = set()
    total_bytes = 0
    for number, line in enumerate(manifest.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"row {number}: blank JSONL line")
        item = json.loads(line)
        if not isinstance(item, dict):
            raise TypeError(f"row {number}: expected an object")
        relative = item.get("file")
        language = item.get("language")
        transcript = item.get("text")
        if item.get("human_verified") is not True:
            raise ValueError(f"row {number}: human_verified=true is required")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError(f"row {number}: file must be relative to source_root")
        if language not in _LANGUAGES:
            raise ValueError(f"row {number}: language must be zh, ja or en")
        if not isinstance(transcript, str) or not transcript.strip():
            raise ValueError(f"row {number}: reviewed transcript is required")
        text = transcript.strip()
        if any(separator in text for separator in ("|", "\n", "\r")):
            raise ValueError(f"row {number}: transcript contains a manifest delimiter")
        audio = (root / relative).resolve(strict=True)
        if not audio.is_relative_to(root) or not audio.is_file():
            raise ValueError(f"row {number}: audio escapes source_root or is not a file")
        if audio.suffix.lower() not in _AUDIO_SUFFIXES:
            raise ValueError(f"row {number}: unsupported audio suffix")
        if audio in seen:
            raise ValueError(f"row {number}: duplicate audio")
        seen.add(audio)
        size = audio.stat().st_size
        if size <= 0 or size > _MAX_AUDIO_BYTES:
            raise ValueError(f"row {number}: invalid audio size")
        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            raise ValueError("dataset exceeds audio size limit")
        rows.append((audio, language, text))
    if not rows:
        raise ValueError("reviewed transcript manifest is empty")
    return rows


def prepare_dataset(
    transcript_jsonl: Path,
    source_root: Path,
    output_root: Path,
    *,
    speaker: str,
    manifest_stem: str,
) -> tuple[int, Path]:
    """Copy reviewed clips into a new, atomically published local dataset."""
    _safe_name(speaker, "speaker")
    _safe_name(manifest_stem, "manifest_stem")
    rows = read_reviewed_rows(transcript_jsonl, source_root)
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {output_root}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".voice-dataset-", dir=output_root.parent))
    try:
        audio_root = staged / "audio"
        audio_root.mkdir()
        lines = []
        for number, (source, language, text) in enumerate(rows, 1):
            name = f"{speaker}_{number:04d}{source.suffix.lower()}"
            shutil.copy2(source, audio_root / name)
            final_path = (output_root / "audio" / name).resolve()
            lines.append(f"{final_path}|{speaker}|{language}|{text}")
        (staged / f"{manifest_stem}.list").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        staged.replace(output_root)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return len(rows), output_root / f"{manifest_stem}.list"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript_jsonl", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speaker", required=True)
    parser.add_argument("--manifest-stem", required=True)
    args = parser.parse_args()
    count, manifest = prepare_dataset(
        args.transcript_jsonl,
        args.source_root,
        args.output,
        speaker=args.speaker,
        manifest_stem=args.manifest_stem,
    )
    print(f"prepared {count} reviewed clips: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
