"""按角色累计原始人声候选；只读音频，未达五分钟时不允许训练。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = Path.home() / "Desktop" / "训练数据"
MINIMUM_SECONDS = 300


def measure(paths: list[Path]) -> dict:
    probe = PROJECT / "data" / "singing" / "runtime" / "bin" / "ffmpeg.exe"
    fingerprints = set()
    unique, failed, duration, active = [], [], 0.0, 0.0
    for path in paths:
        try:
            raw = subprocess.check_output([
                str(probe), "-v", "error", "-nostdin", "-i", str(path),
                "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1",
            ], stderr=subprocess.DEVNULL)
            signature = hashlib.sha256(raw).hexdigest()
            if signature in fingerprints:
                continue
            fingerprints.add(signature)
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            blocks = samples[:samples.size // 320 * 320].reshape(-1, 320)
            if not blocks.size:
                continue
            speech = np.sqrt(np.mean(blocks * blocks, axis=1)) > 10 ** (-50 / 20)
            duration += samples.size / 16000
            active += float(speech.sum()) * 0.02
            unique.append(str(path))
        except (OSError, subprocess.SubprocessError):
            failed.append(str(path))
    return {
        "candidate_files": len(paths), "unique_files": len(unique),
        "raw_seconds": round(duration, 2), "estimated_active_seconds": round(active, 2),
        "minimum_seconds": MINIMUM_SECONDS,
        "duration_gate_passed": active >= MINIMUM_SECONDS,
        "training_started": False, "sources": unique, "failed": failed,
    }


def main() -> None:
    official = SOURCES / "千恋万花官方系统音频_20260928" / "extracted"
    segments = SOURCES / "DuolinGal_training_audio_extract_20260929" / "user_source_m4s_20260929" / "candidate_segments"
    groups = {"murasame_ja": list((SOURCES / "原日语数据集" / "audio").glob("*.mp3"))}
    for name, prefix in (("yoshino", "yoshino"), ("mako", "mako"), ("lena", "rena"), ("roka", "roka"), ("koharu", "koharu")):
        groups[name + "_ja"] = list(segments.glob(prefix + ".wav_*.wav"))
    for name, directory in (("murasame", "MURASAME"), ("yoshino", "YOSHINO"), ("mako", "MAKO"), ("lena", "RENA")):
        groups[name + "_ja"].extend((official / directory).glob("*.wav"))
    for language in ("zh", "ja"):
        groups["aimisi_" + language] = list((SOURCES / "爱弥斯" / "raw" / "aimisi").glob(language + "*_word*.wav"))
    reports = {}
    for name, paths in groups.items():
        reports[name] = measure(sorted(paths))
        print(json.dumps({"profile_language": name, **{
            key: value for key, value in reports[name].items() if key not in ("sources", "failed")
        }}, ensure_ascii=False), flush=True)
    output = PROJECT / "data" / "rvc" / "training" / "source-audit-cumulative.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
