"""Generate the dragon-maid introduction narration with the configured local voice.

This uses the project's existing GPU reservation and voice routing. Completed
segments are resumable and never send messages to a platform.
"""
from __future__ import annotations

import asyncio
import argparse
import base64
import hashlib
import json
import shutil
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings  # noqa: E402
from app.services.voice import synthesize_voice  # noqa: E402

WORK = ROOT / "artifacts/dragon-maid-video"


def write_status(data: dict) -> None:
    target = WORK / "voice-status.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def valid_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (OSError, ValueError, wave.Error, ZeroDivisionError):
        return 0.0


async def main() -> None:
    scenes = json.loads((WORK / "story.json").read_text(encoding="utf-8"))
    audio_dir = WORK / "voice"
    audio_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings().model_copy(update={"voice_timeout_seconds": 120})
    manifest_path = WORK / "voice-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    started = time.monotonic()
    for index, scene in enumerate(scenes):
        text = scene["text"]
        key = str(index)
        digest = hashlib.sha256(text.encode()).hexdigest()
        target = audio_dir / f"scene-{index:02}.wav"
        if scene.get("audio_file"):
            source = (WORK / scene["audio_file"]).resolve(strict=True)
            source.relative_to(WORK)
            if valid_duration(source) <= 0.5:
                raise RuntimeError("invalid supplied audio segment")
            shutil.copy2(source, target)
            manifest[key] = {"file": target.name, "duration": valid_duration(target),
                             "text_sha256": digest, "source": scene["audio_file"]}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"scene {index + 1}/{len(scenes)} supplied audio ready", flush=True)
            continue
        previous = manifest.get(key, {})
        duration = valid_duration(target)
        if previous.get("text_sha256") == digest and duration > 0.5:
            print(f"scene {index + 1}/{len(scenes)} resumed ({duration:.1f}s)", flush=True)
            continue
        write_status({"phase": "generating", "scene": index + 1, "total": len(scenes),
                      "elapsed_seconds": round(time.monotonic() - started, 1)})
        for attempt in range(12):
            try:
                result = await synthesize_voice(
                    text, settings, profile_name="murasame", target_language="zh", bot_self_id="3503565007",
                )
                break
            except RuntimeError as error:
                if "正在翻唱或训练" not in str(error) or attempt == 11:
                    raise
                print("voice GPU busy; waiting before retry", flush=True)
                await asyncio.sleep(10)
        raw = base64.b64decode(result.removeprefix("base64://"), validate=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(raw)
        if valid_duration(temporary) <= 0.5:
            raise RuntimeError("invalid generated voice segment")
        temporary.replace(target)
        duration = valid_duration(target)
        manifest[key] = {"file": target.name, "duration": duration, "text_sha256": digest}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"scene {index + 1}/{len(scenes)} ready ({duration:.1f}s)", flush=True)
    duration = sum(item["duration"] for item in manifest.values())
    write_status({"phase": "complete", "scene": len(scenes), "total": len(scenes),
                  "duration_seconds": round(duration, 1), "elapsed_seconds": round(time.monotonic() - started, 1)})
    print(f"narration complete: {duration:.1f}s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=WORK)
    args = parser.parse_args()
    WORK = args.work.resolve(strict=True)
    try:
        asyncio.run(main())
    except Exception as exc:
        write_status({"phase": "failed", "error_type": type(exc).__name__})
        raise
