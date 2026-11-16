"""Fetch the reviewed SurfVoice voice assets into an isolated project directory."""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

import httpx

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/"data/surfvoice"
ASSETS=[
    ("voice-local-voicepack.tar.gz",2551407,"76b0974be2cd82afda9505590b0a7f782829198d6328d652b2fdc674b0253a44"),
    ("voice-local-models.tar.gz",58395536,"11ab078049e61e94483085d3418d217fdf81c5edd66c2bb5a66aac1aeeb3e111"),
    ("voice-local-vendor.tar.gz",1841046244,"468721372969f25e6d6a789fbb45f632bc4da0bbe5feb92b48ed575e852f6604"),
]


def setup():
    DEST.mkdir(parents=True,exist_ok=True)
    with httpx.Client(follow_redirects=True,timeout=60,trust_env=False) as client:
        for name,size,expected in ASSETS:
            if "--models-only" in sys.argv and name!="voice-local-models.tar.gz":
                continue
            archive=DEST/name
            if not archive.exists() or archive.stat().st_size!=size:
                partial=archive.with_suffix(archive.suffix+".partial")
                start=partial.stat().st_size if partial.exists() else 0
                url="https://github.com/Mieksido/surfvoicelocal/releases/download/v0.1.0/"+name
                with client.stream("GET",url,headers={"Range":f"bytes={start}-"}) as response:
                    response.raise_for_status()
                    if start and response.status_code!=206:
                        raise RuntimeError("Server did not support a verified resumed download")
                    count=start
                    with partial.open("ab" if start else "wb") as stream:
                        for chunk in response.iter_bytes(1024*1024):
                            stream.write(chunk)
                            count+=len(chunk)
                            if count>size:
                                raise RuntimeError("Archive exceeds declared size")
                if partial.stat().st_size!=size:
                    raise RuntimeError("Incomplete voice archive")
                partial.replace(archive)
            with archive.open("rb") as stream:
                digest=hashlib.file_digest(stream,"sha256").hexdigest()
            if digest!=expected:
                raise RuntimeError("Voice archive SHA256 does not match upstream manifest")
            print(json.dumps({"verified":name,"bytes":size}),flush=True)
            output=DEST/"unpacked"/"models" if name=="voice-local-models.tar.gz" else DEST/"unpacked"
            with tarfile.open(archive,"r:gz") as source:
                members=source.getmembers()
                for item in members:
                    path=PurePosixPath(item.name)
                    target=(output/Path(*path.parts)).resolve()
                    if (path.is_absolute() or ".." in path.parts or item.issym() or item.islnk()
                            or not target.is_relative_to(output.resolve())
                            or not (item.isfile() or item.isdir())):
                        raise RuntimeError("Unsafe path in voice archive")
                source.extractall(output,members=members,filter="data")
            print(json.dumps({"unpacked":name,"files":len(members)}),flush=True)


if __name__=="__main__":
    setup()
