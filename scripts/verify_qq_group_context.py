"""Use the configured Luna on synthetic overlapping group topics, without QQ sends."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from astrbot.api.message_components import Plain, Reply
    from astrbot.core.provider.entities import ProviderRequest
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_group_chat import GroupChatRouter
    from app.astrbot_qq_persona import apply_qq_persona

    config = json.loads((ROOT / "data/astrbot/instance/data/config/abconf_qqchat_dafeiyu.json").read_text(encoding="utf-8-sig"))
    provider_id = config["agent_runner"]["config"]["model"]["provider_id"]
    if provider_id != "qqchat-chatgpt-luna":
        raise RuntimeError("Verification must use the approved QQ chat provider")
    selected = next(item for item in config["provider"] if item["id"] == provider_id)
    provider = register_chatgpt_provider()(selected, config["provider_settings"])
    output = []

    def event(text, user, message_id, components=None):
        extras = {}
        return NS(message_str=text, get_message_str=lambda: text, get_platform_name=lambda: "aiocqhttp",
                  get_platform_id=lambda: "isolated-group-context-test", get_extra=extras.get,
                  set_extra=extras.__setitem__, should_call_llm=lambda _: None,
                  is_at_or_wake_command=False, stop_event=lambda: None,
                  message_obj=NS(message=components or [Plain(text)], raw_message={
                      "post_type": "message", "message_type": "group", "self_id": "test-bot",
                      "group_id": "synthetic-group", "user_id": user, "message_id": message_id,
                      "sender": {"user_id": user, "nickname": "同名群友"},
                      "message": [{"type": "text", "data": {"text": text}}],
                  }))

    try:
        for scene in (sys.argv[1:] or ("separate_members", "unclear_device", "quoted_switch")):
            if scene not in {"separate_members", "unclear_device", "quoted_switch"}:
                raise ValueError("Unknown synthetic scene")
            router = GroupChatRouter({"group_chat_groups": ["synthetic-group"], "group_chat_addressed_only": True})
            router.observe_event(event("我那台电脑跑本地模型，之后想加DDR4内存条", "alice", "pc"))
            if scene != "unclear_device":
                router.observe_event(event("我在看老款Switch，打算买来玩游戏", "bob", "switch"))
            else:
                router.observe_event(event("600左右入老机算好价吗", "bob", "unclear"))
            components = None
            current_user = "bob"
            text = "肥鱼，外加还得买内存卡吧"
            if scene == "quoted_switch":
                current_user = "charlie"
                text = "鲸鱼娘，按我引用的这条消息，我还需要另买内存卡吗"
                components = [Reply(id="switch", sender_id="bob", sender_nickname="同名群友",
                                    chain=[Plain("我在看老款Switch，打算买来玩游戏")]), Plain(text)]
            current = event(text, current_user, "current", components)
            if router.observe_event(current) != "name":
                raise AssertionError("Explicit nickname did not wake the current turn")
            request = ProviderRequest(prompt=text, system_prompt="", contexts=[
                {"role": "user", "content": "之前有人说过跑本地模型"},
                {"role": "assistant", "content": "600元老主机要看CPU和主板是否支持DDR4"},
            ])
            router.prepare_request(current, request)
            apply_qq_persona(NS(), current, request)
            prompt = request.prompt + "\n" + "\n".join(part.text for part in request.extra_user_content_parts)
            response = await asyncio.wait_for(provider.text_chat(
                prompt=prompt, system_prompt=request.system_prompt, contexts=request.contexts), 90)
            result = {"scene": scene, "provider_id": provider_id,
                      "text": response.completion_text, "posted_to_qq": False}
            output.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await provider.terminate()
        path = ROOT / "data/dafeiyu/group-context-live-model-verification.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        merged = {item["scene"]: item for item in previous}
        merged.update({item["scene"]: item for item in output})
        path.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
