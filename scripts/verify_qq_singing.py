"""Check actual Luna capability answers/tool selection without sending QQ messages."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/dafeiyu/singing-model-probe"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = str(OUT / "isolated-native")


async def verify_audio():
    from astrbot.api.message_components import Record
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    folder = ROOT / "data/singing/results/dafeiyu-20261103"
    sample = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    audio = folder / "chunks" / sample["chunks"][0]["file"]
    payload = await AiocqhttpMessageEvent._parse_onebot_json(NS(chain=[Record(file=str(audio.resolve()))]))
    binary = base64.b64decode(payload[0]["data"]["file"].removeprefix("base64://"))
    result = {"kind": payload[0]["type"], "bytes": len(binary), "sha256": hashlib.sha256(binary).hexdigest(),
              "matches_generated_audio": binary == audio.read_bytes(), "quality_passed": sample["quality"]["accepted"],
              "duration_seconds": sample["chunks"][0]["duration_seconds"], "posted_to_qq": False}
    (ROOT / "data/dafeiyu/singing-audio-transport-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["matches_generated_audio"] and result["quality_passed"] and result["kind"] == "record"
    print(json.dumps(result), flush=True)


async def main():
    from astrbot.api.message_components import Plain  # noqa: F401 -- initialize SDK before provider imports
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.provider.entities import ProviderRequest
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_runtime import project_settings
    from app.astrbot_singing import prepare_audio_capabilities
    from app.services import qq_voice, usage_ledger

    candidate = qq_voice.pack_config()
    candidate["profile"]["singing_enabled"] = True
    qq_voice.pack_config = lambda: candidate
    usage_ledger.LEDGER_PATH = OUT / "usage.sqlite3"
    cfg = json.loads((ROOT / "data/astrbot/instance/data/config/abconf_qqchat_dafeiyu.json").read_text(encoding="utf-8-sig"))
    selected = next(p for p in cfg["provider"] if p["id"] == "qqchat-chatgpt-luna")
    provider = register_chatgpt_provider()(selected, cfg["provider_settings"])
    tools = ToolSet(tools=[
        FunctionTool(name="qqchat_sing", description="根据用户明确点歌真正演唱并发送歌声。查询能力时直接回答，缺歌名先问。接单后后台生成，当前不发中间回执，最终送出歌声。",
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "歌名与歌手或歌曲链接"},
                "voice": {"type": "string", "description": "留空用当前音色"},
                "mode": {"type": "string", "enum": ["full", "clip"], "description": "整首full，唱几句clip"},
                "action": {"type": "string", "enum": ["sing", "status", "cancel", "voices"]}}, "required": []}),
        FunctionTool(name="qqchat_send_voice", description="普通语音朗读，不能用来冒充演唱", parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}),
    ])
    settings = qq_voice.qq_voice_settings(project_settings(ROOT))
    async def profile(*args):
        return settings.voice_profile_default
    core = NS(settings=settings, app=NS(state=NS(singing_jobs=object(), db=NS(voice_profile=profile))))
    rows = []
    try:
        for text in ("你会唱歌吗", "刚才不是说没配语音吗，那现在能唱吗", "鲸鱼娘，给我唱一段年少有为，李荣浩那首"):
            event = NS(get_platform_name=lambda: "aiocqhttp", message_obj=NS(raw_message={"self_id": settings.onebot_self_id, "group_id": "fixture", "user_id": "fixture"}))
            req = ProviderRequest(prompt=text, system_prompt=(ROOT / "prompts/qq/dafeiyu.txt").read_text(encoding="utf-8"), func_tool=tools)
            await prepare_audio_capabilities(core, event, req)
            response = await provider.text_chat(prompt=text, system_prompt=req.system_prompt, func_tool=tools, contexts=[], request_max_retries=1)
            rows.append({"prompt": text, "reply": response.completion_text, "tools": response.tools_call_name, "arguments": response.tools_call_args, "posted_to_qq": False})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    finally:
        await provider.terminate()
    report = {"model": "gpt-6-luna", "samples": rows, "actual_usage": usage_ledger.ledger().report(owner_only=False)}
    (ROOT / "data/dafeiyu/singing-model-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all(not row["tools"] for row in rows[:2]), "A capability question unexpectedly triggered an action"
    assert "qqchat_sing" in rows[-1]["tools"] and "qqchat_send_voice" not in rows[-1]["tools"]


if __name__ == "__main__":
    asyncio.run(verify_audio() if "--audio-only" in sys.argv else main())
