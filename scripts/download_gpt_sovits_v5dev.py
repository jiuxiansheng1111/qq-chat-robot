"""下载并核对官方 GPT-SoVITS V5Dev 所需权重，不删除现有模型。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2] / "qq-chatrobot-voice" / "GPT-SoVITS"
REVISION = "8f8857563c040d542de108a2c18b0425abd6c9c5"
FILES = {
    "gsv-v5-pretrained/s2Gv5dev.pth": (
        766385517, "16c11fc588a9229a479316a11fc0f518087e79773f5271666a90b39d50460407",
    ),
    "gsv-v5-pretrained/vocoder.pth": (
        57761163, "b7543c83222aa2105bb4efb56845086bc5f2eeba61243d723ffa062052b3221f",
    ),
    "s1v3.ckpt": (
        155284856, "87133414860ea14ff6620c483a3db5ed07b44be42e2c3fcdad65523a729a745a",
    ),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    records = []
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(120, connect=30)) as client:
        for name, (size, expected) in FILES.items():
            target = ROOT / "GPT_SoVITS" / "pretrained_models" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size != size or digest(target) != expected:
                    raise RuntimeError(f"Existing file does not match official hash: {name}")
                print(f"Verified existing {name}", flush=True)
            else:
                temporary = target.with_name(target.name + ".download")
                url = f"https://huggingface.co/lj1995/GPT-SoVITS/resolve/{REVISION}/{name}?download=true"
                for attempt in range(3):
                    current = temporary.stat().st_size if temporary.exists() else 0
                    if current == size:
                        break
                    headers = {"Range": f"bytes={current}-"} if current else {}
                    try:
                        with client.stream("GET", url, headers=headers) as response:
                            response.raise_for_status()
                            if response.status_code != 206:
                                current = 0
                            mode = "ab" if current else "wb"
                            reported = current // (64 * 1024**2)
                            with temporary.open(mode) as stream:
                                for chunk in response.iter_bytes(1024**2):
                                    stream.write(chunk)
                                    current += len(chunk)
                                    if current // (64 * 1024**2) > reported:
                                        reported = current // (64 * 1024**2)
                                        print(f"{name}: {current / size:.0%}", flush=True)
                        if current != size:
                            raise RuntimeError(f"Incomplete download: {name}")
                        break
                    except httpx.HTTPError as exc:
                        print(f"Retry {attempt + 1}/3 for {name}: {type(exc).__name__}", flush=True)
                        if attempt == 2:
                            raise
                        time.sleep(2)
                if temporary.stat().st_size != size or digest(temporary) != expected:
                    raise RuntimeError(f"Official SHA256 verification failed: {name}")
                temporary.replace(target)
                print(f"SHA256 OK: {name}", flush=True)
            records.append({"file": str(target), "bytes": size, "sha256": expected})
    record = ROOT / ".local-setup" / "v5dev-weights.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"revision": REVISION, "files": records}, indent=2), encoding="utf-8")
    print("All V5Dev weights are verified.", flush=True)


if __name__ == "__main__":
    main()
