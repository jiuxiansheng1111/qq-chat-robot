"""Project-local video-watch-plus dependencies; no global PATH or environment edits."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def setup():
    from app.astrbot_runtime import project_settings
    from app.astrbot_video import _binary
    settings=project_settings(ROOT)
    target=ROOT/"data/video-watch-plus"
    target.mkdir(parents=True,exist_ok=True)
    async def download():
        from app.services.http_routing import outbound_http_client
        async with outbound_http_client(settings=settings,follow_redirects=True,timeout=60) as client:
            files=[("ImageMagick.7z","https://github.com/ImageMagick/ImageMagick/releases/download/7.1.2-35/ImageMagick-7.1.2-35-portable-Q16-x64.7z",12106737),
                   ]
            for name,url,limit in files:
                path=target/name
                if path.exists():continue
                async with client.stream("GET",url) as response:
                    response.raise_for_status()
                    count=0
                    with path.open("wb") as output:
                        async for chunk in response.aiter_bytes(64*1024):
                            count+=len(chunk)
                            if count>limit:raise RuntimeError("Unexpected dependency size")
                            output.write(chunk)
    asyncio.run(download())
    archive=target/"ImageMagick.7z"
    with archive.open("rb") as stream:
        actual=hashlib.file_digest(stream,"sha256").hexdigest()
    if actual!="e7c629f3530ccb9d4918a15f0e928254feda28e84a7fa794b25873d152c075c1":
        raise RuntimeError("ImageMagick SHA256 differs from official release digest")
    # Listing is checked before extracting any paths from the official archive.
    extractor=str(Path(os.environ["SystemRoot"])/"System32/tar.exe")
    args=[extractor,"-tf",str(archive)]
    listing=subprocess.run(args,capture_output=True,text=True,errors="replace",check=True).stdout
    for line in listing.splitlines():
        p=PureWindowsPath(line)
        if p.is_absolute() or ".." in p.parts:
            raise RuntimeError("Unsafe archive member")
    detailed=subprocess.run([extractor,"-tvf",str(archive)],capture_output=True,text=True,errors="replace",check=True).stdout
    if any(line.startswith(("l","h")) for line in detailed.splitlines()):
        raise RuntimeError("Archive links are unsupported")
    output=target/"ImageMagick"
    if not output.resolve().is_relative_to(target.resolve()):raise RuntimeError("Invalid tool directory")
    output.mkdir(parents=True,exist_ok=True)
    subprocess.run([extractor,"-xf",str(archive),"-C",str(output)],
                   stdout=subprocess.DEVNULL,check=True)
    magick=next(output.rglob("magick.exe"))
    config={"ffmpeg":_binary(settings,"ffmpeg"),"ffprobe":_binary(settings,"ffprobe"),"magick":str(magick),
            "font":str(Path(os.environ["SystemRoot"])/"Fonts/consola.ttf"),"outdir":str(target/"work")}
    (target/"vw.config.json").write_text(json.dumps(config,indent=2),encoding="utf-8")
    with archive.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
    print(json.dumps({"installed":"ImageMagick 7.1.2-35","archive_sha256":digest,"config":str(target/"vw.config.json")}),flush=True)


if __name__=="__main__":
    setup()
