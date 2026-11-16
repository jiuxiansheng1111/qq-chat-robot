"""Install only SoundTry's local model assets; reuse the existing audio runtime."""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/soundtry"
MANIFEST = json.loads((ROOT / "integrations/soundtry/deps.manifest.json").read_text(encoding="utf-8"))


def download(url, path, *, expected=None, md5=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not expected or path.stat().st_size == expected) and (not md5 or hashlib.md5(path.read_bytes()).hexdigest() == md5):
        print(json.dumps({"file":path.name,"present":True}),flush=True)
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    if temporary.exists() and expected and temporary.stat().st_size == expected and (not md5 or hashlib.md5(temporary.read_bytes()).hexdigest() == md5):
        temporary.replace(path)
        return
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent":"qqchat-soundtry/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
                total = int(response.headers.get("Content-Length", 0))
                received, last = 0, 0
                while block := response.read(1024 * 1024):
                    stream.write(block)
                    received += len(block)
                    if received - last >= 128 * 1024 * 1024:
                        print(json.dumps({"file":path.name,"downloaded_mib":received//1048576}),flush=True)
                        last = received
            if total and temporary.stat().st_size != total:
                raise ValueError("Incomplete model download")
            if expected and temporary.stat().st_size != expected:
                raise ValueError("Unexpected model size")
            if md5 and hashlib.md5(temporary.read_bytes()).hexdigest() != md5:
                raise ValueError("Model checksum differs from upstream manifest")
            temporary.replace(path)
            print(json.dumps({"file":path.name,"ready":True}),flush=True)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


if __name__ == "__main__":
    model = MANIFEST["models"]["medium"]
    for name in model["files"]:
        download(model["baseUrl"] + "/" + name, STATE / "models/medium" / name,
                 expected=1527906378 if name == "model.bin" else None)
    asset = MANIFEST["assets"][0]
    download(asset["url"], STATE / asset["file"], expected=asset["expectBytes"],md5=asset["md5"])
    print(json.dumps({"soundtry_models_ready":True}),flush=True)
