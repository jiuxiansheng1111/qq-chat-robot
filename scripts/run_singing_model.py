"""使用现有角色参考音，在歌唱模式下运行上游 Seed-VC。"""

import argparse
import functools
import os
import random
import runpy
import sys
from pathlib import Path

import numpy as np


def _repair_isolated_f0_spikes(f0):
    """只修复有声段中孤立尖跳，不跨静音平滑。"""
    values = np.asarray(f0, dtype=np.float64).reshape(-1).copy()
    if values.size < 3:
        return values
    voiced = np.isfinite(values) & (values > 1)
    padded = np.concatenate(([False], voiced, [False])).astype(np.int8)
    starts = np.flatnonzero(np.diff(padded) == 1)
    ends = np.flatnonzero(np.diff(padded) == -1)
    for start, end in zip(starts, ends):
        for index in range(start + 1, end - 1):
            left, center, right = values[index - 1:index + 2]
            neighbor_delta = abs(1200 * np.log2(left / right))
            center_delta = abs(1200 * np.log2(center / np.sqrt(left * right)))
            if neighbor_delta <= 50 and center_delta >= 100:
                values[index] = np.sqrt(left * right)
    return values


def _install_f0_spike_repair(seed_root: Path) -> None:
    """包装 Seed-VC 使用的 RMVPE 接口，只在显式启用时生效。"""
    if str(seed_root) not in sys.path:
        sys.path.insert(0, str(seed_root))
    from modules.rmvpe import RMVPE

    original = RMVPE.infer_from_audio

    @functools.wraps(original)
    def infer_with_spike_repair(self, audio, thred=0.03):
        return _repair_isolated_f0_spikes(original(self, audio, thred=thred))

    RMVPE.infer_from_audio = infer_with_spike_repair


def _parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-root", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--diffusion-steps", type=int, default=35)
    parser.add_argument("--inference-cfg-rate", type=float, default=0.7)
    parser.add_argument(
        "--seed", type=int, default=os.environ.get("SINGING_SEED", "20261004"),
        help="Inference RNG seed; default 20261004 (also configurable via SINGING_SEED)",
    )
    repair_f0_default = os.environ.get("SINGING_REPAIR_F0_SPIKES", "").strip().casefold()
    parser.add_argument(
        "--repair-f0-spikes", action=argparse.BooleanOptionalAction,
        default=repair_f0_default in {"1", "true", "yes", "on"},
        help="Repair only isolated one-frame F0 spikes in voiced runs; off by default",
    )
    parser.add_argument(
        "--semitone-shift", type=int, choices=range(-12, 13), default=0,
        help="Pitch shift from -12 to +12 semitones; default 0 (12 is one octave)",
    )
    parser.add_argument("--checkpoint")
    parser.add_argument("--config")
    parser.add_argument("--offline", action="store_true", help="Use previously downloaded model weights only")
    return parser.parse_args(argv)


def main():
    args = _parse_args()
    root = Path(args.seed_root).resolve()
    os.environ["PATH"] = str(Path(args.ffmpeg).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    # 上游发布的检查点基于 torch 2.4。
    # 在已安装的 torch 2.7 中加载可信上游模型时，须用旧版加载器。
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_ENDPOINT"] = os.environ.get("SINGING_HF_ENDPOINT", "https://huggingface.co")
    os.environ["PYTHONUTF8"] = "1"
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    os.chdir(root)
    sys.path.insert(0, str(root))
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("The singing runtime requires a CUDA GPU; check setup_singing.ps1.")
    random.seed(args.seed)
    np.random.seed(args.seed % (2 ** 32))
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    if args.repair_f0_spikes:
        _install_f0_spike_repair(root)
    sys.argv = [
        str(root / "inference.py"), "--source", args.source, "--target", args.target,
        "--output", args.output, "--diffusion-steps", str(args.diffusion_steps),
        "--inference-cfg-rate", str(args.inference_cfg_rate), "--length-adjust", "1.0",
        "--f0-condition", "True", "--auto-f0-adjust", "False", "--semi-tone-shift",
        str(args.semitone_shift),
        "--fp16", "True",
    ]
    if bool(args.checkpoint) != bool(args.config):
        raise ValueError("checkpoint and config must be specified together")
    if args.checkpoint:
        sys.argv.extend(["--checkpoint", args.checkpoint, "--config", args.config])
    runpy.run_path(str(root / "inference.py"), run_name="__main__")


if __name__ == "__main__":
    main()
