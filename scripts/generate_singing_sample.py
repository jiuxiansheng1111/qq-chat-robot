"""生成整首或短版翻唱，保存到本机回听。"""

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


async def generate(args: argparse.Namespace) -> None:
    from app.config import get_settings
    from app.services.singing import (
        cleanup_singing_job,
        generate_singing_cover,
        singing_job_directory,
    )
    from app.services.singing_sources import SINGING_DATA_ROOT

    output = args.output.resolve()
    if not output.is_relative_to(SINGING_DATA_ROOT.resolve()) or output == SINGING_DATA_ROOT.resolve():
        raise ValueError("Output must be a new directory below data/singing")
    if output.exists():
        raise FileExistsError("Output exists; choose another directory to preserve previous listening samples")
    settings = get_settings()
    job_id = uuid.uuid4().hex

    async def progress(message: str) -> None:
        print(message, flush=True)

    try:
        async with asyncio.timeout(settings.singing_job_timeout_seconds):
            cover = await generate_singing_cover(
                args.query, args.profile, job_id, settings, progress,
                bot_self_id=args.bot_self_id or settings.onebot_self_id,
                mode=args.mode,
            )
        output.mkdir(parents=True)
        shutil.copy2(cover.full_path, output / "cover.wav")
        chunks = output / "chunks"
        chunks.mkdir()
        for chunk in cover.chunks:
            shutil.copy2(chunk.path, chunks / chunk.path.name)
        job_dir = singing_job_directory(job_id)
        for name in ("voice_reference.wav", "lyrics.lrc", "selection.json"):
            if (job_dir / name).is_file():
                shutil.copy2(job_dir / name, output / name)
        report = {
            "song": cover.song.track.title,
            "song_id": cover.song.track.song_id,
            "profile": cover.profile_id,
            "mode": cover.mode,
            "source_start_seconds": cover.source_start_seconds,
            "source_end_seconds": cover.source_end_seconds,
            "pitch_shift_semitones": cover.pitch_shift_semitones,
            "quality": cover.quality,
            "chunks": [
                {"file": chunk.path.name, "start_seconds": chunk.start_seconds,
                 "end_seconds": chunk.end_seconds, "duration_seconds": chunk.duration_seconds}
                for chunk in cover.chunks
            ],
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved cover and {len(cover.chunks)} QQ voice segments: {output}", flush=True)
    finally:
        await asyncio.to_thread(cleanup_singing_job, job_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--profile", default="murasame")
    parser.add_argument("--mode", choices=["full", "clip"], default="full")
    parser.add_argument("--bot-self-id", default="")
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(generate(parser.parse_args()))


if __name__ == "__main__":
    main()
