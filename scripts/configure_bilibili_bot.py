"""Prepare the audited Bilibili plugin; no login, model calls or Bilibili writes."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically

PLUGIN_NAME = "astrbot_plugin_bilibili_ai_bot"
REVISION = "1d12480b147b8aa43e7f39e455b23a7818d3e157"
REPOSITORY = "https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot"
PROVIDER_ID = "qqchat-chatgpt-luna"

COMMON_STYLE = (
    "对外称号是ChatGPT的龙女仆，自我介绍可以随上下文自然修饰，不用每次背固定台词。"
    "直接介绍自己能做什么、能帮什么忙，不主动反复强调非官方角色或非官方账号；"
    "被明确问到身份、归属或来源时如实简短回答，不冒充官方。"
    "介绍覆盖解答问题、整理资料、图像视频理解、语音表达与配音、内容创作，"
    "只说已验证可用的能力，不局限于QQ群，也不把日常回复变成宣传清单。"
    "普通闲聊单条不超过20字符（含标点），轻松、温和、口语化，有主见，不讨好，"
    "不用emoji、颜文字或括号动作，不强行玩梗、反问、@人，不重复。"
    "明确提问、求助、自我介绍、解释、教程和总结任务可按需要完整回答；"
    "评论回复仍遵守本插件的180字符上限，私信遵守1200字符上限。"
    "一句能说清就不绕圈，没有对方的新消息就不自行续写。"
    "日常评论和私信句末通常不加句号，问句保留问号；长句正常用标点。"
    "模型token消耗在闲聊中可自然叫大白饭，不反复提、不讨要打赏；技术追问准确解释。"
    "明确问AI/真人或技术来源时如实说明基于ChatGPT，实际回复模型为gpt-6-luna；不冒充真人。"
    "无内容可答时使用本次任务协议的跳过选项，不要发送保持安静或任何内部状态。"
    "只用本轮资料与真实工具结果，不编造看过视频、玩过游戏、亲身经历或已经查证的事实。"
    "遵守本次任务指定的输出协议，内部判断不写进公开正文。"
)
REPLY_STYLE = COMMON_STYLE + (
    "本次是收到消息后的回复；遵守回复JSON协议，正文只写在reply字段，"
    "无内容可答时decision=ignore且reply为空字符串。"
)

# Explicitly disable write-producing autonomous features, including their child
# switches, so a future UI toggle does not silently enable additional actions.
DISABLED = (
    "PROACTIVE_LIKE", "PROACTIVE_COIN", "PROACTIVE_FAV",
    "PROACTIVE_FOLLOW", "ENABLE_DYNAMIC", "ENABLE_OWNER_RECOMMEND",
    "ENABLE_CROSS_PLATFORM_ACTIVITY_STATUS", "SPECIAL_FOLLOW_ENABLED",
    "ENABLE_BANGUMI", "BANGUMI_PROACTIVE", "BANGUMI_COMMENT", "BANGUMI_AUTO_FOLLOW",
    "ENABLE_DYNAMIC_WATCH", "ENABLE_DAILY_SUMMARY", "ENABLE_WEEKLY_SUMMARY",
    "ENABLE_PERSONALITY_EVOLUTION", "ENABLE_MEME_LEARNING", "ENABLE_AUTONOMOUS_DAILY_PLAN",
    "ENABLE_LIVE_DANMAKU_REPLY", "ENABLE_AUTO_BLOCK", "PRIVATE_MESSAGE_AUTO_BLOCK",
    "ENABLE_LLM_TOOLS", "SECURITY_ENABLE_PRIVATE_READ_TOOLS", "SECURITY_ENABLE_WRITE_TOOLS",
    "BILI_PRIVATE_SHARE_TOOL_ENABLED", "ENABLE_SAFE_CROSS_PLATFORM_MEMORY",
    "ENABLE_BILI_SHARE_PARSE", "BILI_SHARE_PARSE_AUTO_TRIGGER_ENABLED",
    "BILI_SHARE_PARSE_LLM_TRIGGER_ENABLED", "BILI_SHARE_PARSE_AUTO_WATCH",
    "ENABLE_WEB_SEARCH",
)


def prepared_config(schema: dict, existing: dict | None = None) -> dict:
    """Preserve existing credentials and unrelated fields; set the requested profile."""
    config = {key: copy.deepcopy(value.get("default")) for key, value in schema.items()}
    config.update(copy.deepcopy(existing or {}))
    overrides = {
        **dict.fromkeys(DISABLED, False),
        "LLM_PROVIDER_ID": PROVIDER_ID,
        "SUMMARY_PROVIDER_ID": PROVIDER_ID,
        "GENERATION_PROVIDER_ID": PROVIDER_ID,
        "SCHEDULE_PLAN_PROVIDER_ID": PROVIDER_ID,
        "VIDEO_VISION_PROVIDER_ID": PROVIDER_ID,
        "IMAGE_VISION_PROVIDER_ID": PROVIDER_ID,
        "VIDEO_VISION_FORMAT": "none",
        "USE_ASTRBOT_PERSONA": True,
        "CUSTOM_SYSTEM_PROMPT": COMMON_STYLE,
        "CUSTOM_REPLY_INSTRUCTION": REPLY_STYLE,
        "CUSTOM_PRIVATE_MESSAGE_INSTRUCTION": REPLY_STYLE,
        "ENABLE_REPLY": True,
        "POLL_INTERVAL": 60,
        "REPLY_COOLDOWN": 60,
        "REPLY_PROBABILITY_PERCENT": 100,
        "COMMENT_SHARED_DAILY_LIMIT": 100,
        "ENABLE_PROACTIVE": True,
        "PROACTIVE_COMMENT": True,
        "PROACTIVE_TIMES_COUNT": 2,
        "PROACTIVE_VIDEO_COUNT": 8,
        "PROACTIVE_DAILY_LIMIT": 16,
        "PROACTIVE_COMMENT_COUNT": 1,
        "PROACTIVE_COMMENT_DAILY_LIMIT": 10,
        "PROACTIVE_COMMENT_MIN_SCORE": 8,
        "ENABLE_PROACTIVE_LLM_PREFILTER": True,
        "PROACTIVE_VIDEO_POOLS": ["排行榜:游戏", "最新:单机游戏"],
        "PROACTIVE_SEARCH_QUERY_PROMPT": (
            "只从AI技术演示、游戏技巧与有趣游戏实况这些相关主题选择1至2个具体搜索词。"
            "没有真实兴趣和相关内容就跳过，不为凑次数乱找视频。"
        ),
        "CUSTOM_PROACTIVE_INSTRUCTION": (
            COMMON_STYLE + "只针对已经实际看清的视频细节自然评论一句；"
            "没有独到内容就不评论，不套万能夸奖、互关引流、催更或冒充玩过游戏。"
            "不要复制上一条评论，不在无关视频下刷存在感；无需提自己身份。"
            "只输出主动评论JSON：decision为comment或skip，正文用text字段；"
            "没有想说的内容时decision=skip且text为空。"
        ),
        "SLEEP_START": 23,
        "SLEEP_END": 8,
        "ENABLE_PRIVATE_MESSAGES": True,
        "PRIVATE_MESSAGE_AUTO_REPLY": True,
        "PRIVATE_MESSAGE_REPLY_SCOPE": "all",
        "PRIVATE_MESSAGE_POLL_INTERVAL": 60,
        "PRIVATE_MESSAGE_IDLE_POLL_INTERVAL": 180,
        "PRIVATE_MESSAGE_MAX_PER_POLL": 1,
        "PRIVATE_MESSAGE_MAX_MESSAGE_AGE": 600,
        "INTEREST_APPLY_TO_PRIVATE": False,
        "ENABLE_INTEREST_BASED_REPLY": True,
        "FILTER_LOW_VALUE_MESSAGES": True,
        "FILTER_DUPLICATE_MESSAGES": True,
        "FILTER_AD_MESSAGES": True,
        "AUTONOMOUS_REPLY_DAILY_MAX": 100,
        "AUTONOMOUS_PRIVATE_DAILY_MAX": 60,
        "AUTONOMOUS_DYNAMIC_DAILY_MAX": 0,
        "AUTONOMOUS_PROACTIVE_DAILY_MAX": 16,
        "AUTONOMOUS_PROACTIVE_DAILY_LIMIT": 16,
        "BEHAVIOR_BUDGET_ENABLED": True,
        "MEMORY_ISOLATION_MODE": "isolated",
        "ENABLE_PRIVACY_REDACTION": True,
        "BILI_PROMPT_INJECTION_DEFENSE": True,
    }
    missing = overrides.keys() - schema.keys()
    if missing:
        raise ValueError("Plugin schema changed; review required: " + ", ".join(sorted(missing)))
    config.update(overrides)
    return config


def is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6185), timeout=0.3):
            return True
    except OSError:
        return False


def configure(root: Path, *, apply: bool = False) -> dict:
    root = root.resolve(strict=True)
    plugin = root / "data/astrbot/instance/data/plugins" / PLUGIN_NAME
    path = root / "data/astrbot/instance/data/config" / f"{PLUGIN_NAME}_config.json"
    for location in (plugin, path):
        _assert_managed_path(root, location)
    revision = subprocess.check_output(
        ["git", "-C", str(plugin), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != REVISION:
        raise ValueError("Installed plugin is not the audited revision")
    schema = json.loads((plugin / "_conf_schema.json").read_text(encoding="utf-8"))
    old = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    new = prepared_config(schema, old)
    native = json.loads((root / "data/astrbot/instance/data/cmd_config.json").read_text(
        encoding="utf-8-sig"))
    models = [p for p in native.get("provider", []) if p.get("id") == PROVIDER_ID]
    if len(models) != 1 or models[0].get("model") != "gpt-6-luna" or not models[0].get("enable"):
        raise ValueError("Enabled GPT-6 Luna provider must already exist; no model fallback")
    result = {"plugin": PLUGIN_NAME, "revision": revision, "model": "gpt-6-luna",
              "credentials_present": bool(new.get("SESSDATA")),
              "changed": new != old, "applied": False}
    if not apply or new == old:
        return result
    if is_running():
        raise RuntimeError("Stop AstrBot before writing configuration")
    backup = root / "data/astrbot/config-backups" / (
        "bilibili-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    _assert_managed_path(root, backup)
    backup.mkdir(parents=True)
    if path.exists():
        shutil.copy2(path, backup / path.name)
    _write_json_atomically(path, new)
    _write_json_atomically(backup / "deployment.json", {
        **result, "applied": True, "repository": REPOSITORY,
        "previous_config_existed": bool(old),
    })
    return {**result, "applied": True, "backup_dir": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(Path(__file__).resolve().parents[1], apply=args.apply),
                     ensure_ascii=True))
