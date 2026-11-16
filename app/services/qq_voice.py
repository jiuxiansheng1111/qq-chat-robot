"""An optional QQ-only voice pack; all original platform defaults stay intact."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
PROFILE_PATH=ROOT/"data/surfvoice/qq-voice.json"
_START_LOCK=asyncio.Lock()


async def ensure_qq_voice_service(settings):
    from app.services.voice_memory import _powershell, is_local_gpt_sovits
    if os.name!="nt" or not is_local_gpt_sovits(settings):
        return
    async def ready():
        url=urlsplit(settings.voice_api_url)
        try:
            _,writer=await asyncio.wait_for(asyncio.open_connection(url.hostname,url.port or 80),1)
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError,TimeoutError):
            return False
    if await ready():
        return
    async with _START_LOCK:
        if not await ready():
            await _powershell("start_voice.ps1",[],timeout=180)


def pack_config():
    if not PROFILE_PATH.is_file() or PROFILE_PATH.stat().st_size>24000:
        return None
    try:
        data=json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        if data.get("enabled") is not True or not isinstance(data.get("profile"),dict):
            return None
        # Runtime tools cannot provide or change these paths.
        for value in (data["profile"]["ref_audio_path"],data["gpt_weight"],*data["sovits_weights"].values()):
            path=Path(value).resolve()
            if not path.is_relative_to((ROOT/"data/surfvoice").resolve()) or not path.is_file():
                return None
        return data
    except (OSError,ValueError,TypeError,KeyError):
        return None


def qq_voice_settings(settings):
    data=pack_config()
    if data is None:
        return settings
    profiles=json.loads(settings.voice_profiles_json or "{}")
    profiles["dafeiyu"]=data["profile"]
    sovits=json.loads(settings.voice_sovits_weights_by_profile_json or "{}")
    sovits["dafeiyu"]=data["sovits_weights"]
    gpt=json.loads(settings.voice_gpt_weights_by_profile_json or "{}")
    gpt["dafeiyu"]=data["gpt_weight"]
    return settings.model_copy(update={"voice_profiles_json":json.dumps(profiles,ensure_ascii=False),
        "voice_sovits_weights_by_profile_json":json.dumps(sovits,ensure_ascii=False),
        "voice_gpt_weights_by_profile_json":json.dumps(gpt,ensure_ascii=False),"voice_profile_default":"dafeiyu"})
