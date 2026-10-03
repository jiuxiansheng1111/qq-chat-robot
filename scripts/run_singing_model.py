"""Run upstream Seed-VC in singing mode with the existing character reference."""

import argparse
import os
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-root", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--diffusion-steps", type=int, default=35)
    parser.add_argument("--inference-cfg-rate", type=float, default=0.7)
    parser.add_argument("--checkpoint")
    parser.add_argument("--config")
    parser.add_argument("--offline", action="store_true", help="Use previously downloaded model weights only")
    args = parser.parse_args()
    root = Path(args.seed_root).resolve()
    os.environ["PATH"] = str(Path(args.ffmpeg).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    # Upstream's published checkpoints target torch 2.4. The trusted upstream
    # models require the legacy loader when sharing the installed torch 2.7.
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
    sys.argv = [
        str(root / "inference.py"), "--source", args.source, "--target", args.target,
        "--output", args.output, "--diffusion-steps", str(args.diffusion_steps),
        "--inference-cfg-rate", str(args.inference_cfg_rate), "--length-adjust", "1.0",
        "--f0-condition", "True", "--auto-f0-adjust", "False", "--semi-tone-shift", "0",
        "--fp16", "True",
    ]
    if bool(args.checkpoint) != bool(args.config):
        raise ValueError("checkpoint and config must be specified together")
    if args.checkpoint:
        sys.argv.extend(["--checkpoint", args.checkpoint, "--config", args.config])
    runpy.run_path(str(root / "inference.py"), run_name="__main__")


if __name__ == "__main__":
    main()
