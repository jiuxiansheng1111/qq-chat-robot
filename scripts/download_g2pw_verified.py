"""Download the GPT-SoVITS G2PW archive in verified HTTP ranges."""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import requests


URL = "https://www.modelscope.cn/models/kamiorinn/g2pw/resolve/master/G2PWModel_1.1.zip"
SIZE = 588_857_174
SHA256 = "b116f6930a7ee55eef6576a8d8e14bf40c1106583439e8ae924b901512379c64"
CHUNK = 8 * 1024 * 1024


def download(path: Path) -> None:
    part = path.with_suffix(path.suffix + ".part")
    position = part.stat().st_size if part.exists() else 0
    if position > SIZE:
        part.unlink()
        position = 0
    with part.open("ab") as out:
        while position < SIZE:
            end = min(position + CHUNK, SIZE) - 1
            expected = end - position + 1
            for attempt in range(1, 11):
                try:
                    response = requests.get(
                        URL,
                        headers={"Range": f"bytes={position}-{end}"},
                        stream=True,
                        timeout=(30, 120),
                    )
                    response.raise_for_status()
                    if response.status_code != 206:
                        raise RuntimeError(f"server ignored range: {response.status_code}")
                    received = 0
                    for block in response.iter_content(1024 * 1024):
                        if block:
                            out.write(block)
                            received += len(block)
                    if received != expected:
                        out.truncate(position)
                        raise RuntimeError(f"short range: {received}/{expected}")
                    position = end + 1
                    out.flush()
                    print(f"{position}/{SIZE} ({position / SIZE:.1%})", flush=True)
                    break
                except Exception as exc:  # network connections are flaky here
                    out.truncate(position)
                    if attempt == 10:
                        raise
                    print(f"range {position}-{end} failed ({exc}); retry {attempt}/10", flush=True)
                    time.sleep(min(attempt * 2, 15))
    digest = hashlib.sha256(part.read_bytes()).hexdigest()
    if digest != SHA256:
        raise RuntimeError(f"sha256 mismatch: {digest}")
    path.unlink(missing_ok=True)
    part.replace(path)
    print(f"verified: {path}", flush=True)


if __name__ == "__main__":
    download(Path(sys.argv[1]))
