"""使用 Seed-VC 缓存的 RMVPE 模型，为整首歌曲规划八度偏移。"""

import argparse
import gc
import json
import math
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Separated original vocal")
    parser.add_argument("--target-hz", required=True, help="Target voice median F0 in Hz")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="Use cached model weights only")
    return parser.parse_args(argv)


def _configure_model_environment(offline: bool) -> None:
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ["HF_ENDPOINT"] = os.environ.get("SINGING_HF_ENDPOINT", "https://huggingface.co")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"


def _plan(args: argparse.Namespace) -> dict:
    _configure_model_environment(args.offline)
    try:
        target_hz = float(args.target_hz)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("target_median_hz must be a number from 80 to 1000 Hz") from exc
    if not math.isfinite(target_hz) or not 80 <= target_hz <= 1000:
        raise ValueError("target_median_hz must be from 80 to 1000 Hz")
    seed_root = args.seed_root.resolve(strict=True)
    if not (seed_root / "modules" / "rmvpe.py").is_file() or not (seed_root / "hf_utils.py").is_file():
        raise ValueError("--seed-root is not a Seed-VC checkout")
    source = args.source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("--source must be an audio file")

    # 确保本仓库的 app 包排在 Seed-VC 的 app.py 前面。
    seed_root_string = str(seed_root)
    add_seed_path = seed_root_string not in sys.path
    if add_seed_path:
        sys.path.insert(1, seed_root_string)
    previous_cwd = Path.cwd()
    torch_module = None
    device = "cpu"
    rmvpe = None
    try:
        os.chdir(seed_root)  # Seed-VC 会把官方检查点缓存到 ./checkpoints。
        import librosa
        import numpy as np
        import torch
        from hf_utils import load_custom_model_from_hf
        from modules.rmvpe import RMVPE

        from app.services.singing_register import choose_octave_shift
        from scripts.check_singing_quality import _infer_f0

        torch_module = torch
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        source_16k, _ = librosa.load(source, sr=16000, mono=True)
        source_16k = np.asarray(source_16k, dtype=np.float32).reshape(-1)
        if not source_16k.size:
            raise ValueError("--source audio is empty")
        rmvpe_path = load_custom_model_from_hf("lj1995/VoiceConversionWebUI", "rmvpe.pt", None)
        rmvpe = RMVPE(rmvpe_path, is_half=False, device=device)
        source_f0 = _infer_f0(rmvpe, source_16k)
        return choose_octave_shift(source_f0, target_hz)
    finally:
        if rmvpe is not None:
            del rmvpe
        gc.collect()
        if torch_module is not None and device.startswith("cuda") and torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
        os.chdir(previous_cwd)
        if add_seed_path:
            sys.path.remove(seed_root_string)


def _safe_error(exc: Exception) -> dict[str, str | bool]:
    """返回机器可读且不包含路径/token 的失败报告。"""
    report: dict[str, str | bool] = {
        "accepted": False,
        "error_type": type(exc).__name__,
    }
    safe_validation_errors = {
        "target_median_hz must be a number from 80 to 1000 Hz",
        "target_median_hz must be from 80 to 1000 Hz",
        "--seed-root is not a Seed-VC checkout",
        "--source must be an audio file",
        "--source audio is empty",
        "source_f0 must be a one-dimensional F0 sequence",
        "source_f0 must be a nonempty one-dimensional F0 sequence",
        "source_f0 contains no finite voiced frames",
        "source_f0 voiced values are outside the supported range",
    }
    if isinstance(exc, (TypeError, ValueError)) and str(exc) in safe_validation_errors:
        report["error"] = str(exc)
    else:
        report["error"] = "Unable to plan the source pitch; check the audio and cached RMVPE runtime."
    return report


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        report = _plan(args)
        report["accepted"] = True
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - emit a compact JSON failure report for callers
        report = _safe_error(exc)
        exit_code = 2

    try:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        report = {"accepted": False, "error": "Unable to write the pitch plan report.",
                  "error_type": type(exc).__name__}
        exit_code = 2
    print(json.dumps(report, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
