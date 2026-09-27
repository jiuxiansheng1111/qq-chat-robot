"""Fail closed unless GPT-SoVITS has a usable paired S2 resume checkpoint."""

from __future__ import annotations

import pathlib
import sys

import torch


def latest(root: pathlib.Path, pattern: str) -> pathlib.Path:
    paths = list(root.glob(pattern))
    if not paths:
        raise RuntimeError(f"missing {pattern} checkpoint in {root}")
    # Match GPT-SoVITS utils.latest_checkpoint_path(), which sorts every digit
    # in the complete path rather than trusting file modification times.
    return max(paths, key=lambda path: int("".join(filter(str.isdigit, str(path)))))


def load(root: pathlib.Path, label: str) -> tuple[pathlib.Path, int]:
    path = latest(root, f"{label}_*.pth")
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    iteration = checkpoint.get("iteration")
    if not isinstance(iteration, int) or iteration < 1:
        raise RuntimeError(f"{path} has no valid saved epoch")
    if checkpoint.get("optimizer") is None:
        raise RuntimeError(f"{path} lacks optimizer state and is not a resumable training checkpoint")
    if "model" not in checkpoint:
        raise RuntimeError(f"{path} lacks model state")
    return path, iteration


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise RuntimeError("usage: validate_mixed_resume_checkpoint.py CHECKPOINT_DIRECTORY")
    root = pathlib.Path(argv[1])
    g_path, g_epoch = load(root, "G")
    d_path, d_epoch = load(root, "D")
    if g_epoch != d_epoch:
        raise RuntimeError(f"G/D checkpoint epochs differ: {g_path}={g_epoch}, {d_path}={d_epoch}")
    print(f"RESUME_EPOCH={g_epoch}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RuntimeError as exc:
        print(f"checkpoint validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
