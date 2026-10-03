"""在 CPU 上调整伴奏音轨音高，不改变速度或时长。"""

import argparse
from pathlib import Path

SAMPLE_RATE = 44100


def transpose_audio_file(source: Path, output: Path, semitones: int) -> None:
    import librosa
    import numpy as np
    import soundfile as sf

    if type(semitones) is not int or not -6 <= semitones <= 6:
        raise ValueError("Accompaniment shift must be an integer from -6 to 6 semitones")
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if source == output:
        raise ValueError("Input and output paths must be different")

    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if sample_rate != SAMPLE_RATE:
        raise ValueError("Accompaniment must be 44100 Hz")
    if not audio.size or not np.all(np.isfinite(audio)):
        raise ValueError("Accompaniment audio is empty or invalid")

    shifted_channels = []
    for channel_index in range(audio.shape[1]):
        shifted = np.asarray(
            librosa.effects.pitch_shift(
                audio[:, channel_index],
                sr=sample_rate,
                n_steps=semitones,
                res_type="soxr_hq",
            ),
            dtype=np.float32,
        ).reshape(-1)
        if shifted.size < audio.shape[0]:
            shifted = np.pad(shifted, (0, audio.shape[0] - shifted.size))
        else:
            shifted = shifted[: audio.shape[0]]
        shifted_channels.append(shifted)

    result = np.stack(shifted_channels, axis=1)
    if not np.all(np.isfinite(result)):
        raise ValueError("Pitch shift produced invalid audio")
    input_rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    output_rms = float(np.sqrt(np.mean(np.square(result, dtype=np.float64))))
    if input_rms > 0 and output_rms > 1e-12:
        result *= input_rms / output_rms
    peak = float(np.max(np.abs(result)))
    if peak > 0.99:
        result *= 0.99 / peak
    # 这是对混音整体做等比例恢复，不是 EQ 或音色处理。
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, result, sample_rate, subtype="PCM_16")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semitones", type=int, choices=range(-6, 7), required=True)
    args = parser.parse_args()
    transpose_audio_file(args.input, args.output, args.semitones)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
