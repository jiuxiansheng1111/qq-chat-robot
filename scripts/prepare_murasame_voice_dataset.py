"""Prepare a local GPT-SoVITS dataset from a user-provided voice zip.

The source archive is treated as local, user-authorized material. Audio and
generated manifests are written below data/ and are intentionally ignored by
Git. Zip member names from older Windows archives are decoded as GBK when
needed, then used as the transcript after trailing take numbers are removed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MAX_MEMBER_BYTES = 100 * 1024 * 1024


def decode_member_name(name: str) -> str:
    """Recover Chinese names from archives written with a legacy code page."""
    for encoding in ("utf-8", "gbk", "big5"):
        try:
            raw = name.encode("cp437")
            decoded = raw.decode(encoding)
            if any("\u4e00" <= char <= "\u9fff" for char in decoded):
                return decoded
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return name


def transcript_from_name(name: str) -> str:
    stem = Path(decode_member_name(name)).stem
    stem = re.sub(r"[\s_-]*\d+$", "", stem).strip()
    return stem


def prepare_dataset(
    source_zip: Path,
    output_root: Path,
    *,
    speaker: str,
    language: str,
) -> tuple[int, Path]:
    if not source_zip.is_file():
        raise FileNotFoundError(source_zip)

    audio_root = output_root / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "murasame.list"
    lines: list[str] = []
    audio_index = 0

    with zipfile.ZipFile(source_zip) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            suffix = Path(member.filename).suffix.lower()
            if suffix not in SUPPORTED_AUDIO:
                continue
            if member.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"archive member is too large: {member.filename}")

            transcript = transcript_from_name(member.filename)
            if not transcript:
                continue

            audio_index += 1
            target = audio_root / f"murasame_{audio_index:04d}{suffix}"
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            lines.append(
                f"{target.resolve()}|{speaker}|{language}|{transcript}"
            )

    if not lines:
        raise ValueError("no supported audio with usable transcript names found")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines), manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a local GPT-SoVITS voice dataset from a zip archive."
    )
    parser.add_argument("source_zip", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/murasame_voice_dataset"),
    )
    parser.add_argument("--speaker", default="murasame")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    count, manifest = prepare_dataset(
        args.source_zip,
        args.output,
        speaker=args.speaker,
        language=args.language,
    )
    print(f"prepared {count} audio files")
    print(f"manifest: {manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
