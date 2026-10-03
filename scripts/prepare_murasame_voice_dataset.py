"""从用户提供的音色压缩包准备本地 GPT-SoVITS 数据集。

输入压缩包视为用户本地授权材料。音频和生成的清单都写到 data/ 下，且被 Git 忽略。压缩包文件名不代表转写：当前压缩包用中文描述日语录音，因此必须提供顺序正确的真实转写文件；只有其他压缩包的文件名确实是转写时，操作者才可明确启用不安全的文件名模式。
"""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_AUDIO_BYTES = 512 * 1024 * 1024
MAX_AUDIO_MEMBERS = 2_000


def decode_member_name(name: str) -> str:
    """从使用旧代码页写入的压缩包中恢复中文文件名。"""
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


def load_ordered_transcripts(path: Path) -> list[str]:
    """按压缩包成员顺序，为每个音频读取一份 UTF-8 转写。"""
    if not path.is_file():
        raise FileNotFoundError(path)
    transcripts = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    if not transcripts or any(not line for line in transcripts):
        raise ValueError("transcript file must contain one non-empty line per audio clip")
    if any("|" in line for line in transcripts):
        raise ValueError("transcripts cannot contain '|' because GPT-SoVITS uses it as a delimiter")
    return transcripts


def prepare_dataset(
    source_zip: Path,
    output_root: Path,
    *,
    speaker: str,
    language: str,
    transcripts: Sequence[str] | None = None,
    use_filenames_as_transcripts: bool = False,
) -> tuple[int, Path]:
    if not source_zip.is_file():
        raise FileNotFoundError(source_zip)
    if language not in {"zh", "ja", "en"}:
        raise ValueError(f"unsupported language: {language!r}")
    if not speaker.strip() or "|" in speaker or "\n" in speaker or "\r" in speaker:
        raise ValueError("speaker must be non-empty and cannot contain a manifest delimiter")

    audio_root = output_root / "audio"
    manifest_path = output_root / "murasame.list"
    if manifest_path.exists() or audio_root.exists():
        raise FileExistsError(
            f"refusing to overwrite existing dataset output: {output_root}; "
            "use a new --output directory"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    with zipfile.ZipFile(source_zip) as archive:
        audio_members = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            and Path(member.filename).suffix.lower() in SUPPORTED_AUDIO
        ]
        if not audio_members:
            raise ValueError("no supported audio files found")
        if len(audio_members) > MAX_AUDIO_MEMBERS:
            raise ValueError(f"archive contains too many audio members: {len(audio_members)}")
        declared_total = sum(member.file_size for member in audio_members)
        if any(member.file_size > MAX_MEMBER_BYTES for member in audio_members):
            raise ValueError("archive contains an audio member larger than the allowed limit")
        if declared_total > MAX_TOTAL_AUDIO_BYTES:
            raise ValueError("archive audio exceeds the total allowed extraction limit")
        if use_filenames_as_transcripts and transcripts is not None:
            raise ValueError("choose transcripts or filename mode, not both")
        if use_filenames_as_transcripts:
            resolved_transcripts = [
                transcript_from_name(member.filename) for member in audio_members
            ]
        elif transcripts is not None:
            resolved_transcripts = [str(value).strip() for value in transcripts]
        else:
            raise ValueError(
                "real transcripts are required; archive filenames are not used implicitly"
            )
        if len(resolved_transcripts) != len(audio_members):
            raise ValueError(
                "transcript count does not match audio count: "
                f"{len(resolved_transcripts)} != {len(audio_members)}"
            )
        if any(not value for value in resolved_transcripts):
            raise ValueError("every audio clip must have a non-empty transcript")
        if any("|" in value or "\n" in value or "\r" in value for value in resolved_transcripts):
            raise ValueError("transcripts cannot contain manifest delimiters or line breaks")

        # 所有文件准备完成后再发布。压缩包损坏或磁盘写满时，
        # 不要留下看起来还能复用的半成品数据集。
        staging_root = Path(tempfile.mkdtemp(prefix=".voice-prepare-", dir=output_root))
        staging_audio = staging_root / "audio"
        staging_audio.mkdir()
        actual_total = 0
        try:
            for audio_index, (member, transcript) in enumerate(
                zip(audio_members, resolved_transcripts, strict=True), start=1
            ):
                suffix = Path(member.filename).suffix.lower()
                target = staging_audio / f"murasame_{audio_index:04d}{suffix}"
                with archive.open(member) as source, target.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        actual_total += len(chunk)
                        if actual_total > MAX_TOTAL_AUDIO_BYTES:
                            raise ValueError("archive audio exceeds the total allowed extraction limit")
                        destination.write(chunk)
                if target.stat().st_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member is too large: {member.filename}")
                lines.append(
                    f"{(audio_root / target.name).resolve()}|{speaker.strip()}|{language}|{transcript}"
                )
            staging_manifest = staging_root / "murasame.list"
            staging_manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            staging_audio.replace(audio_root)
            staging_manifest.replace(manifest_path)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
    return len(lines), manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a local GPT-SoVITS voice dataset from a zip archive."
    )
    parser.add_argument("source_zip", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/murasame_voice_dataset_ja"),
    )
    parser.add_argument("--speaker", default="murasame")
    parser.add_argument("--language", required=True, choices=("zh", "ja", "en"))
    transcript_source = parser.add_mutually_exclusive_group(required=True)
    transcript_source.add_argument(
        "--transcripts",
        type=Path,
        help="UTF-8 file with one genuine transcript per audio clip, in ZIP order",
    )
    transcript_source.add_argument(
        "--use-filenames-as-transcripts",
        action="store_true",
        help="unsafe opt-in; only use when filenames are exact spoken text",
    )
    args = parser.parse_args()

    transcripts = (
        load_ordered_transcripts(args.transcripts) if args.transcripts is not None else None
    )

    count, manifest = prepare_dataset(
        args.source_zip,
        args.output,
        speaker=args.speaker,
        language=args.language,
        transcripts=transcripts,
        use_filenames_as_transcripts=args.use_filenames_as_transcripts,
    )
    print(f"prepared {count} audio files")
    print(f"manifest: {manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
