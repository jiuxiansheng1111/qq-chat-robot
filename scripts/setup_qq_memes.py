"""Stage the official blue-whale meme pack with meme_manager's own importer."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def setup():
    sys.path.insert(0,str(ROOT))
    os.environ["ASTRBOT_ROOT"]=str(ROOT/"data/dafeiyu/meme-stage")
    plugin=ROOT/"data/astrbot/instance/data/plugins/meme_manager"
    package=types.ModuleType("qq_meme_setup")
    package.__path__=[str(plugin)]
    sys.modules[package.__name__]=package
    storage=importlib.import_module("qq_meme_setup.backend.packs.storage")
    # The native importer still validates and installs the pack. Only transport
    # is adapted to this project's existing proxy routing after direct TLS failed.
    def download(repo,ref,target_zip_path,*args,**kwargs):
        async def fetch():
            from app.astrbot_runtime import project_settings
            from app.services.http_routing import outbound_http_client
            async with outbound_http_client(settings=project_settings(ROOT),timeout=90) as client:
                url=f"https://codeload.github.com/{repo}/zip/refs/tags/{ref}"
                async with client.stream("GET",url) as response:
                    response.raise_for_status()
                    count=0
                    with target_zip_path.open("wb") as stream:
                        async for chunk in response.aiter_bytes(64*1024):
                            count+=len(chunk)
                            if count>storage.MAX_ARCHIVE_COMPRESSED_BYTES:
                                raise ValueError("表情包压缩文件过大")
                            stream.write(chunk)
        asyncio.run(fetch())
    storage._download_github_archive=download
    source={"type":"github","repo":"anka-afk/blue-fat-fish-meme-pack","ref":"v1.0.0","subpath":"."}
    result=storage.install_pack_from_github_source(source,expected_pack_id="blue-fat-fish-meme-pack",preserve_existing_selection=True)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":
    setup()
