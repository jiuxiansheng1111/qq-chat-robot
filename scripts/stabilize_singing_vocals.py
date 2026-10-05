"""用原唱轻重整理转换人声，不生成新发音、不混回原唱。"""

import argparse
import json
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.singing_mix import stabilize_vocal_envelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--converted", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source, rate = sf.read(args.source, dtype="float32", always_2d=True)
    converted, converted_rate = sf.read(args.converted, dtype="float32", always_2d=True)
    if source.shape[1] != 1 or converted.shape[1] != 1 or rate != converted_rate:
        raise ValueError("人声整理需要同采样率的单声道音频")
    if source.shape[0] / rate > 120 or converted.shape[0] / rate > 120:
        raise ValueError("人声整理单段超过时长上限")
    adjusted, report = stabilize_vocal_envelope(source[:, 0], converted[:, 0], rate)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, adjusted, rate, subtype="FLOAT")
    output.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
