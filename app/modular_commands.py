"""Select an installed legacy command family before entering the shared dispatcher."""
from __future__ import annotations


def matches(enabled, raw, runtime):
    from app import main as bot

    if raw.get("message_type") == "private":
        command = bot.private_command(raw, runtime._core.app.state)
        return command is not None and command[0] in enabled
    text = bot.message_text(raw)
    if not text:
        return False
    addressed = raw.get("wechat_private") or bot.bot_mentioned(raw) or text.startswith("/")
    if addressed:
        text = bot.canonicalize_short_command(bot.normalize_menu_intent(text), addressed=True)
    spec, _ = bot.registry.resolve(text)
    if spec is not None and spec.name in enabled:
        return True
    if "music" in enabled:
        music = bot.parse_music_action(text)
        if music and (addressed or music.action in {"select", "lyrics_candidate", "cancel"}):
            return True
    if {"character_catalog", "anime_character", "ultraman"}.intersection(enabled):
        key = (str(raw.get("group_id", "")), str(raw.get("user_id", "")))
        pending = getattr(runtime._core.app.state, "pending_character_confirmations", {}).get(key)
        if pending and (text.isdigit() or text in {"是", "确认", "取消"}):
            return True
        if addressed and (bot.extract_catalog_lookup(text) is not None or bot.resolve_anime_character_matches(text)
                          or bot.resolve_ultraman_matches(text) or bot.asks_for_ultraman_image_followup(text)):
            return True
    if "voice" in enabled and (text in bot.VOICE_ON_COMMANDS | bot.VOICE_OFF_COMMANDS | bot.VOICE_STATUS_COMMANDS
                               | bot.VOICE_CHARACTER_LIST_COMMANDS or text.startswith(bot.VOICE_CHARACTER_SELECT_PREFIXES)):
        return True
    if "daily_news" in enabled and text in bot.DAILY_NEWS_COMMANDS:
        return True
    if "possession" in enabled and (bot.is_targeted_possession_command(raw, text) or
                                   text in bot.RANDOM_POSSESSION_COMMANDS | bot.POSSESSION_STATUS_COMMANDS
                                   | bot.POSSESSION_EXIT_COMMANDS | bot.POSSESSION_STYLE_CLEAR_COMMANDS):
        return bool(addressed)
    if not addressed:
        return False
    extractors = {"translation": bot.extract_translation_query, "bilibili_video": bot.extract_bilibili_video_query,
                  "web_search": bot.extract_search_query, "weather": bot.extract_weather_location}
    if any(key in enabled and extract(raw, text) is not None for key, extract in extractors.items()):
        return True
    if "story" in enabled and bot.parse_story_request(text):
        return True
    if "image_generation" in enabled and any(text == term or text.startswith(term + " ") for term in bot.IMAGE_GENERATION_COMMANDS):
        return True
    if "character_images" in enabled and text in bot.MURASAME_IMAGE_COMMANDS | bot.MURASAME_EMOJI_COMMANDS:
        return True
    if "romance_mode" in enabled and text in (bot.ROMANCE_MODE_COMMANDS | bot.AFFECTION_VIEW_COMMANDS
                                               | bot.AFFECTION_RESET_COMMANDS | bot.AFFECTION_HISTORY_COMMANDS):
        return True
    if "group_admin" in enabled and text.lstrip("/").startswith(("bot ", "blacklist ", "机器人开启", "机器人关闭")):
        return True
    if "memory" in enabled:
        if (text in bot.LONG_MEMORY_LIST_COMMANDS | bot.LONG_MEMORY_CLEAR_COMMANDS | bot.GROUP_MEMORY_LIST_COMMANDS
                | bot.GROUP_MEMORY_CLEAR_COMMANDS | bot.MEMBER_IDENTITY_LIST_COMMANDS | bot.MEMBER_IDENTITY_CLEAR_COMMANDS
                | bot.MEMBER_IDENTITY_ADMIN_CLEAR_COMMANDS or text.startswith("/memory ")):
            return True
        if any(extract(raw, text) is not None for extract in (bot.extract_long_memory, bot.extract_group_memory,
                                                              bot.extract_group_memory_deletion)):
            return True
    if "singing" in enabled:
        profiles = bot.voice_profiles(runtime._core.settings)
        if bot.parse_singing_command(text, profiles, addressed=True):
            return True
    return "rps" in enabled and bot.resolve_rps_logic(text) is not None
