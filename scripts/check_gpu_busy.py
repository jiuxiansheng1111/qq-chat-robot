"""Lightweight startup probe; never import CUDA or load a model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.singing_gpu import gpu_is_busy

if __name__ == "__main__":
    print("busy" if gpu_is_busy() else "free")
