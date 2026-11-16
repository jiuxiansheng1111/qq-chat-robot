"""Render the dragon-maid introduction from prepared narration and artwork."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "artifacts/dragon-maid-video"
ASSETS = WORK
FFMPEG = ROOT / "data/singing/runtime/bin/ffmpeg.exe"
FFPROBE = ROOT / "data/singing/runtime/bin/ffprobe.exe"


def duration(path: Path) -> float:
    result = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                            check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def ass_time(seconds: float) -> str:
    hundredths = max(0, round(seconds * 100))
    hours, rem = divmod(hundredths, 360000)
    minutes, rem = divmod(rem, 6000)
    sec, cent = divmod(rem, 100)
    return f"{hours}:{minutes:02}:{sec:02}.{cent:02}"


def subtitle_chunks(text: str) -> list[str]:
    clauses = re.findall(r"[^，。！？；：]+[，。！？；：]?", text)
    result = []
    current = ""
    for clause in clauses:
        if current and len(current) + len(clause) > 24:
            result.append(current)
            current = ""
        current += clause
    if current:
        result.append(current)
    return result or [text]


def safe_ass(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")


def write_ass(target: Path, scene: dict, length: float, index: int) -> None:
    text = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,Microsoft YaHei,34,&H00FFFFFF,&H000000FF,&H00150D23,&H99000000,-1,0,0,0,100,100,0,0,1,2.6,1.2,2,54,54,32,1
Style: Chapter,Microsoft YaHei,32,&H00FFFFFF,&H000000FF,&H00150D23,&H99000000,-1,0,0,0,100,100,0,0,1,2.2,0.6,7,0,0,0,1
Style: Small,Microsoft YaHei,19,&H00EAD8FF,&H000000FF,&H00150D23,&H99000000,0,0,0,0,100,100,0,0,1,1.4,0.4,7,0,0,0,1
Style: Card,Microsoft YaHei,29,&H00FFFFFF,&H000000FF,&H00150D23,&H99000000,0,0,0,0,100,100,0,0,1,1.5,0.4,7,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    chapter = safe_ass(scene["chapter"])
    title = chapter
    text += f"Dialogue: 1,0:00:00.10,{ass_time(length)},Chapter,,0,0,0,,{{\\pos(760,68)}}{title}\n"
    text += f"Dialogue: 1,0:00:00.10,{ass_time(length)},Small,,0,0,0,,{{\\pos(760,112)}}龙女仆 · 今天也有新发现\n"
    for line_index, line in enumerate(scene.get("card", [])):
        text += f"Dialogue: 1,0:00:00.10,{ass_time(length)},Card,,0,0,0,,{{\\pos(760,{215 + line_index * 62})}}{safe_ass(line)}\n"
    if scene.get("source_caption"):
        text += f"Dialogue: 1,0:00:00.10,{ass_time(length)},Small,,0,0,0,,{{\\pos(760,554)}}{safe_ass(scene['source_caption'])}\n"
    parts = subtitle_chunks(scene.get("subtitle_text", scene["text"]))
    total = sum(max(1, len(part)) for part in parts)
    elapsed = 0.0
    for number, part in enumerate(parts):
        end = length if number == len(parts) - 1 else elapsed + length * len(part) / total
        text += f"Dialogue: 2,{ass_time(elapsed)},{ass_time(end)},Main,,0,0,0,,{safe_ass(part).rstrip('，。；：')}\n"
        elapsed = end
    target.write_text(text, encoding="utf-8-sig")


def visual_filter(shot: str, ass_path: Path, length: float, has_card: bool = False,
                  image_file: Path | None = None, image_crop: list[int] | None = None) -> tuple[list[str], str]:
    card_box = "drawbox=x=730:y=175:w=515:h=415:color=0x211737@0.82:t=fill," if has_card else ""
    common = (
        "zoompan=z='min(zoom+0.00004,1.035)':x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=24,"
        "drawbox=x=730:y=43:w=515:h=100:color=0x211737@0.65:t=fill,"
        f"{card_box}"
        "drawbox=x=0:y=617:w=1280:h=103:color=0x171329@0.48:t=fill,"
        f"fade=t=in:st=0:d=0.22,fade=t=out:st={max(0,length-0.22):.3f}:d=0.22,"
        f"ass='{ass_path.as_posix()}',format=yuv420p"
    )
    if shot == "full":
        inputs = ["-f", "lavfi", "-i", "color=c=0xece5f6:s=1280x720:r=24",
                  "-loop", "1", "-framerate", "24", "-i", str(ASSETS / "chibi-approved.png")]
        return inputs, f"[1:v]scale=-1:720[front];[0:v][front]overlay=x=70:y=0:shortest=1,{common}[v]"
    if shot == "asset":
        if image_file is None:
            raise ValueError("asset shot requires image_file")
        source = image_file
        crop = ""
        if image_crop is not None:
            if len(image_crop) != 4 or any(type(value) is not int or value < 0 for value in image_crop) or not all(image_crop[2:]):
                raise ValueError("image_crop must be nonnegative x/y and positive width/height")
            x,y,width,height=image_crop
            crop=f"crop={width}:{height}:{x}:{y},"
        base = crop + "scale=700:540:force_original_aspect_ratio=decrease,pad=720:555:(ow-iw)/2:(oh-ih)/2:color=0xeee8f3,pad=1280:720:0:35:color=0x312544,"
    elif shot.startswith("expression"):
        number = int(shot[-1])
        x, y = (number % 2) * 627, (number // 2) * 627
        source = ASSETS / "chibi-expressions.png"
        base = f"crop=627:627:{x}:{y},scale=720:720,pad=1280:720:0:0:color=0x312544,"
    else:
        source = ASSETS / ("portrait-approved.png" if shot == "portrait" else "chibi-studio.png")
        base = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
    return ["-loop", "1", "-framerate", "24", "-i", str(source)], f"[0:v]{base}{common}[v]"


def render_scene(index: int, scene: dict, wav: Path, target: Path) -> None:
    length = duration(wav)
    subdir = WORK / "captions"
    subdir.mkdir(parents=True, exist_ok=True)
    subtitle = subdir / f"scene-{index:02}.ass"
    write_ass(subtitle, scene, length, index)
    relative_subtitle = subtitle.relative_to(ROOT)
    image_file = None
    if scene.get("image_file"):
        image_file = (WORK / scene["image_file"]).resolve(strict=True)
        image_file.relative_to(WORK)
    inputs, filters = visual_filter(scene["shot"], relative_subtitle, length, bool(scene.get("card")),image_file,scene.get("image_crop"))
    audio_index = 2 if scene["shot"] == "full" else 1
    command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
               "-i", str(wav), "-filter_complex", filters, "-map", "[v]", "-map", f"{audio_index}:a:0",
               "-t", f"{length:.3f}", "-c:v", "libopenh264", "-b:v", "2400k", "-maxrate", "3200k", "-bufsize", "6400k",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", str(target)]
    subprocess.run(command, cwd=ROOT, check=True)


def clip_key(scene: dict, wav: Path) -> str:
    digest = hashlib.sha256(b"dragon-render-v4-expression-assets")
    digest.update(json.dumps(scene, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    digest.update(wav.read_bytes())
    if scene.get("image_file"):
        image_file=(WORK/scene["image_file"]).resolve(strict=True)
        image_file.relative_to(WORK)
        digest.update(image_file.read_bytes())
    return digest.hexdigest()


def make_music(path: Path, length: float) -> None:
    """Generate a quiet, original four-chord instrumental bed, no sampled music."""
    rate = 22050
    chords = [(220.00, 277.18, 329.63), (196.00, 246.94, 293.66),
              (174.61, 220.00, 261.63), (196.00, 246.94, 329.63)]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for second in range(math.ceil(length)):
            t = np.arange(rate, dtype=np.float64) / rate + second
            chord = chords[(second // 4) % len(chords)]
            transition = np.minimum(1.0, np.minimum((t % 4) * 2, (4 - t % 4) * 2))
            bed = sum(np.sin(2 * np.pi * freq * t) for freq in chord) * transition * 0.012
            note = chord[(second // 2) % 3] * 2
            bell = np.sin(2 * np.pi * note * t) * np.exp(-(t % 2) * 3) * 0.015
            envelope = np.minimum(1.0, np.minimum(t / 2, np.maximum(0, (length - t) / 2)))
            samples = np.clip((bed + bell) * envelope, -1, 1)
            wav.writeframes((samples * 32767).astype("<i2").tobytes())


def main() -> None:
    global WORK, ASSETS
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--assets", type=Path, default=ASSETS)
    args = parser.parse_args()
    WORK = args.work.resolve(strict=True)
    ASSETS = args.assets.resolve(strict=True)
    WORK.relative_to(ROOT)  # Captions use project-relative FFmpeg paths.
    scenes = json.loads((WORK / "story.json").read_text(encoding="utf-8"))
    if args.preview:
        render_scene(0, scenes[0], WORK / "voice-validated.wav", WORK / "preview.mp4")
        print("preview ready", flush=True)
        return
    manifest = json.loads((WORK / "voice-manifest.json").read_text(encoding="utf-8"))
    clips = WORK / "clips"
    clips.mkdir(exist_ok=True)
    rendered = []
    mute_ranges = []
    elapsed = 0.0
    cache_file = WORK / "clips-manifest.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    for index, scene in enumerate(scenes):
        if str(index) not in manifest:
            raise RuntimeError(f"narration missing: {index}")
        wav = WORK / "voice" / manifest[str(index)]["file"]
        target = clips / f"scene-{index:02}.mp4"
        key = clip_key(scene, wav)
        if target.exists() and cache.get(str(index)) == key:
            print(f"reused {index+1}/{len(scenes)}", flush=True)
        else:
            render_scene(index, scene, wav, target)
            cache[str(index)] = key
            cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"rendered {index+1}/{len(scenes)}", flush=True)
        rendered.append(target)
        clip_duration = duration(target)
        if scene.get("audio_file"):
            mute_ranges.append((elapsed, elapsed + clip_duration))
        elapsed += clip_duration
    listing = WORK / "clips.txt"
    listing.write_text("".join(f"file 'clips/{p.name}'\n" for p in rendered), encoding="utf-8")
    combined = WORK / "narrated.mp4"
    subprocess.run([str(FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(combined)], check=True)
    bed = WORK / "music.wav"
    make_music(bed, duration(combined))
    final = WORK / "dragon-maid-introduction.mp4"
    quiet = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in mute_ranges)
    bed_filter = f"volume='if({quiet},0,0.30)':eval=frame" if quiet else "volume=0.30"
    subprocess.run([str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(combined), "-i", str(bed),
                    "-filter_complex", f"[1:a]{bed_filter}[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[a]",
                    "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(final)], check=True)
    print(f"video ready: {duration(final):.1f}s, {final.stat().st_size} bytes", flush=True)


if __name__ == "__main__":
    main()
