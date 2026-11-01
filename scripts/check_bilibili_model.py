"""Exercise the installed BiliBot LLM/schema without any Bilibili or QQ sends."""

from __future__ import annotations

import asyncio
import argparse
import importlib
import json
import os
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ASTRBOT_ROOT"] = str(ROOT / "data/astrbot/instance")


async def main(*, proactive_only: bool = False) -> None:
    import astrbot.api  # noqa: F401
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.star.context import Context

    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from scripts.configure_bilibili_bot import (
        PLUGIN_NAME, PROVIDER_ID, REPLY_STYLE, prepared_config,
    )

    plugin_path = ROOT / "data/astrbot/instance/data/plugins" / PLUGIN_NAME
    # Import the actual provider mixin and public-output protocol, without
    # constructing a bot or its pollers, tools, login or send transports.
    for name, path in (("bilibot_qa", plugin_path), ("bilibot_qa.core", plugin_path / "core")):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    llm = importlib.import_module("bilibot_qa.core.llm")
    protocol = importlib.import_module("bilibot_qa.core.output_protocol")
    cfg = json.loads((ROOT / "data/astrbot/instance/data/cmd_config.json").read_text(
        encoding="utf-8-sig"))
    plugin_config = json.loads((ROOT / "data/astrbot/instance/data/config" /
                               f"{PLUGIN_NAME}_config.json").read_text(encoding="utf-8-sig"))
    if proactive_only:
        schema = json.loads((plugin_path / "_conf_schema.json").read_text(encoding="utf-8"))
        plugin_config = prepared_config(schema, plugin_config)
    entry = next(value for value in cfg["provider"] if value["id"] == PROVIDER_ID)
    assert entry["model"] == "gpt-6-luna"
    register_chatgpt_provider()
    manager = object.__new__(ProviderManager)
    manager.provider_sources_config = cfg["provider_sources"]
    manager.provider_settings = cfg["provider_settings"]
    manager.provider_insts, manager.inst_map = [], {}
    manager.default_chat_provider_id, manager.curr_provider_inst = entry["id"], None
    await manager.load_provider(entry)
    provider = await manager.get_provider_by_id(entry["id"])
    context = object.__new__(Context)
    context.provider_manager = manager
    bot = llm.LLMMixin()
    bot.config, bot.context = plugin_config, context
    database = ROOT / "data/astrbot/instance/data/data_v4.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        persona = connection.execute("SELECT system_prompt FROM personas WHERE persona_id=?",
                                     ("qqchat-local-persona",)).fetchone()[0]
    cases = ["今天又被游戏虐了", "你是谁呀", "你是真人吗", "我打不开游戏，总是黑屏，有什么排查建议？"]
    results = []
    try:
        if proactive_only:
            proactive = importlib.import_module("bilibot_qa.core.proactive")
            content_protocol = importlib.import_module("bilibot_qa.core.content_protocol")
            generated = []
            generation_call = bot._generation_llm_call

            async def capture_generation(*args, **kwargs):
                text = await generation_call(*args, **kwargs)
                generated.append(text)
                return text

            async def system_prompt():
                return persona + "\n" + plugin_config["CUSTOM_SYSTEM_PROMPT"]

            bot._get_system_prompt = system_prompt
            bot._generation_llm_call = capture_generation
            # Only the upstream content generator is exercised: no account,
            # discovery, send transport or polling task is constructed.
            reply = await proactive.ProactiveMixin._generate_proactive_comment(
                bot,
                {"title": "测试素材：游戏里用木箱挡住机关", "up_name": "离线验收"},
                "这是合成验收素材，不是真实视频。角色先被机关推下台，第二次把木箱"
                "放在脚边挡住推杆，站在箱子后安全通过。只根据这段描述评论。",
            )
            value = content_protocol.parse_proactive_comment(generated[-1] if generated else "")
            if reply and len(reply) > 20:
                raise AssertionError(f"Proactive comment exceeded 20 characters: {reply}")
            results.append({"input": "合成游戏片段", "model_decision": value["decision"],
                            "decision": "comment" if reply else "skip",
                            "suppressed_by_length_guard": len(value["text"]) > 20,
                            "reply": reply or "", "characters": len(reply or "")})
        for user_text in ([] if proactive_only else cases):
            text = await bot._llm_call(
                "这是离线验收，不发送消息。你在B站收到一条评论：" + user_text + "\n"
                + REPLY_STYLE + "\n" + protocol.reply_schema_instruction(tools=[]),
                system_prompt=persona, max_tokens=600,
            )
            if not text:
                raise RuntimeError("Installed plugin did not receive a completed reply")
            value = protocol.parse_reply_envelope(text, channel="comment", allowed_tools=set(),
                                                  allow_tool_request=False)
            reply = value.get("reply", "")
            if user_text == cases[0] and len(reply) > 20:
                raise AssertionError("Casual reply exceeded 20 characters")
            if user_text == cases[1] and not ("ChatGPT" in reply and "龙女仆" in reply):
                raise AssertionError("Incorrect public identity")
            results.append({"input": user_text, "decision": value["decision"], "reply": reply,
                            "characters": len(reply)})
    finally:
        await provider.terminate()
    output = ROOT / "artifacts/bilibili-ready"
    output.mkdir(parents=True, exist_ok=True)
    report = {"model": "gpt-6-luna", "sent_bilibili": False, "sent_qq": False, "cases": results}
    report_name = "proactive-model-check.json" if proactive_only else "model-check.json"
    (output / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proactive-only", action="store_true")
    asyncio.run(main(proactive_only=parser.parse_args().proactive_only))
