"""Separate one original song; keep FFmpeg discovery local to this subprocess."""

import argparse
import os
import runpy
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", choices=["htdemucs", "htdemucs_ft"], default="htdemucs_ft")
    args = parser.parse_args()
    os.environ["PATH"] = str(Path(args.ffmpeg).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    sys.argv = ["demucs", "--two-stems=vocals", "--name", args.model, "--device", "cuda",
                "--shifts", "1", "--segment", "7", "-o", args.output, args.source]
    runpy.run_module("demucs.separate", run_name="__main__")


if __name__ == "__main__":
    main()
