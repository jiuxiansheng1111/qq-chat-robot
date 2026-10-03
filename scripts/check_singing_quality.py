"""Offline Seed-VC singing quality check; run with the singing runtime Python.

Example:
    data/singing/runtime/.venv/Scripts/python.exe scripts/check_singing_quality.py \
      --seed-root data/singing/runtime/seed-vc --source source_vocals.wav \
      --converted converted_vocals.wav --reference target_voice.wav \
      --output data/singing/jobs/example/quality.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check converted singing against source and voice")
    parser.add_argument("--seed-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Separated original vocal")
    parser.add_argument("--converted", type=Path, required=True, help="Converted vocal")
    parser.add_argument("--reference", type=Path, required=True, help="Conversion prompt / target speaker recording")
    parser.add_argument(
        "--identity-reference", type=Path,
        help="Optional independent target identity recording; adds a diagnostic similarity score",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--max-pitch-cents", type=float, default=100)
    parser.add_argument(
        "--expected-semitone-shift", type=int, choices=range(-6, 7), default=0,
        help="Expected source-to-converted pitch shift for controlled transposition checks",
    )
    parser.add_argument("--min-within-semitone", type=float, default=0.70)
    parser.add_argument("--min-voiced-recall", type=float, default=0.70)
    parser.add_argument("--max-duration-error", type=float, default=0.03)
    parser.add_argument("--min-rms", type=float, default=1e-4)
    parser.add_argument("--max-clipping", type=float, default=0.01)
    parser.add_argument("--min-voice-similarity", type=float, default=0.35)
    parser.add_argument("--min-source-voiced-frames", type=int, default=100)
    parser.add_argument("--offline", action="store_true", help="Use previously downloaded model weights only")
    parser.add_argument(
        "--require-pass", action="store_true", help="Exit 1 when thresholds fail (report is still written)"
    )
    return parser.parse_args()


def _audio_windows(audio, sample_rate: int, seconds: int = 8, limit: int = 12):
    """Use distributed 8 s windows and skip silence for speaker comparison."""
    import numpy as np

    window_size = seconds * sample_rate
    if audio.size < window_size:
        return [audio] if audio.size >= 2 * sample_rate else []
    last_start = audio.size - window_size
    count = min(limit, max(2, 1 + last_start // (4 * sample_rate)))
    starts = np.linspace(0, last_start, num=count, dtype=np.int64)
    windows = [audio[start : start + window_size] for start in dict.fromkeys(starts)]
    return [window for window in windows if float(np.sqrt(np.mean(window**2))) > 1e-4]


def _infer_f0(model, audio, sample_rate: int = 16000, chunk_seconds: int = 20):
    """Chunk long songs to keep RMVPE memory bounded; frames are 10 ms."""
    import numpy as np

    chunk_size = sample_rate * chunk_seconds
    pieces = []
    for start in range(0, audio.size, chunk_size):
        chunk = np.asarray(audio[start : start + chunk_size], dtype=np.float32)
        f0 = np.asarray(model.infer_from_audio(chunk, thred=0.03), dtype=np.float64)
        expected_frames = int(np.ceil(chunk.size / 160))
        pieces.append(np.pad(f0[:expected_frames], (0, max(0, expected_frames - f0.size))))
    return np.concatenate(pieces) if pieces else np.array([], dtype=np.float64)


def _speaker_embedding(model, audio, device: str):
    import numpy as np
    import torch
    import torchaudio

    vectors = []
    for window in _audio_windows(audio, 16000):
        wave = torch.from_numpy(np.asarray(window, dtype=np.float32)).unsqueeze(0)
        features = torchaudio.compliance.kaldi.fbank(
            wave, num_mel_bins=80, dither=0, sample_frequency=16000
        )
        features -= features.mean(dim=0, keepdim=True)
        with torch.no_grad():
            vector = model(features.unsqueeze(0).to(device)).squeeze(0).cpu().numpy()
        vectors.append(vector)
    if not vectors:
        return np.zeros(192, dtype=np.float64)
    return np.mean(np.stack(vectors), axis=0)


def _evaluate(args: argparse.Namespace) -> dict:
    # Seed's older checkpoints use torch.load without weights_only. This is
    # restricted to the official model files loaded by its own modules.
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ["HF_ENDPOINT"] = os.environ.get("SINGING_HF_ENDPOINT", "https://huggingface.co")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    seed_root = args.seed_root.resolve(strict=True)
    if not (seed_root / "modules" / "rmvpe.py").is_file() or not (
        seed_root / "hf_utils.py"
    ).is_file():
        raise ValueError("--seed-root is not a Seed-VC checkout")
    source = args.source.resolve(strict=True)
    converted = args.converted.resolve(strict=True)
    reference = args.reference.resolve(strict=True)
    identity_reference = (
        args.identity_reference.resolve(strict=True)
        if args.identity_reference is not None else None
    )
    if identity_reference == reference and args.identity_reference is not None:
        raise ValueError("--identity-reference must be a separate file from --reference")
    input_paths = (source, converted, reference) + (
        (identity_reference,) if identity_reference is not None else ()
    )
    if not all(path.is_file() for path in input_paths):
        raise ValueError("All input paths must be audio files")
    # Keep this project's ``app`` package ahead of Seed-VC's ``app.py``.
    sys.path.insert(1, str(seed_root))
    os.chdir(seed_root)  # hf_utils caches under ./checkpoints, matching Seed-VC.

    import librosa
    import numpy as np
    import torch
    from hf_utils import load_custom_model_from_hf
    from modules.campplus.DTDNN import CAMPPlus
    from modules.rmvpe import RMVPE

    from app.services.singing_quality import compute_quality_report, quality_failures

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    rmvpe_path = load_custom_model_from_hf("lj1995/VoiceConversionWebUI", "rmvpe.pt", None)
    rmvpe = RMVPE(rmvpe_path, is_half=False, device=device)
    campplus_path = load_custom_model_from_hf("funasr/campplus", "campplus_cn_common.bin", None)
    campplus = CAMPPlus(feat_dim=80, embedding_size=192)
    campplus.load_state_dict(torch.load(campplus_path, map_location="cpu"))
    campplus.eval().to(device)

    source_16k, _ = librosa.load(source, sr=16000, mono=True)
    converted_16k, _ = librosa.load(converted, sr=16000, mono=True)
    reference_16k, _ = librosa.load(reference, sr=16000, mono=True)
    identity_reference_16k = (
        librosa.load(identity_reference, sr=16000, mono=True)[0]
        if identity_reference is not None else None
    )
    converted_native, _ = librosa.load(converted, sr=None, mono=True)
    if (
        not source_16k.size or not converted_16k.size or not reference_16k.size
        or (identity_reference_16k is not None and not identity_reference_16k.size)
    ):
        raise ValueError("One of the audio files is empty")

    source_f0 = _infer_f0(rmvpe, source_16k)
    converted_f0 = _infer_f0(rmvpe, converted_16k)
    reference_embedding = _speaker_embedding(campplus, reference_16k, device)
    identity_reference_embedding = (
        _speaker_embedding(campplus, identity_reference_16k, device)
        if identity_reference_16k is not None else None
    )
    converted_embedding = _speaker_embedding(campplus, converted_16k, device)
    report = compute_quality_report(
        source_f0,
        converted_f0,
        np.asarray(converted_native),
        source_16k.size / 16000,
        converted_16k.size / 16000,
        reference_embedding,
        converted_embedding,
        identity_reference_embedding=identity_reference_embedding,
        expected_semitone_shift=args.expected_semitone_shift,
    )
    failures = quality_failures(
        report,
        max_pitch_median_cents=args.max_pitch_cents,
        min_pitch_within_semitone_ratio=args.min_within_semitone,
        min_voiced_recall=args.min_voiced_recall,
        max_duration_error_ratio=args.max_duration_error,
        min_converted_rms=args.min_rms,
        max_clipping_ratio=args.max_clipping,
        min_voice_similarity=args.min_voice_similarity,
        min_source_voiced_frames=args.min_source_voiced_frames,
    )
    return {
        "accepted": not failures,
        "failures": failures,
        "voice_similarity_basis": "conversion_prompt",
        **report,
    }


def main() -> int:
    args = _parse_args()
    output = args.output.resolve()
    try:
        report = _evaluate(args)
    except Exception as exc:  # noqa: BLE001 - preserve a machine-readable failure report
        report = {"accepted": False, "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 2
    else:
        exit_code = 1 if args.require_pass and not report["accepted"] else 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
