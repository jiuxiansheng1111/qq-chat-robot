import io
import struct
import wave

import pytest

from app.config import Settings
from app.services.voice import (
    _reject_near_silent_wav,
    detect_speech_language,
    requested_speech_language,
    resolve_character_profile,
    synthesize_voice,
    validate_voice_text_language,
    voice_profile_menu,
    voice_profiles,
)


def test_voice_profile_is_one_multilingual_murasame_character():
    settings = Settings(_env_file=None)
    profiles = voice_profiles(settings)
    assert list(profiles) == ["murasame"]
    profile = profiles["murasame"]
    assert profile["label"] == "小丛雨"
    assert profile["languages"] == "中 / 日 / 英"
    assert profile["target_language"] == "zh"
    assert profile["prompt_lang"] == "ja"
    assert profile["prompt_text"] == ""
    assert profile["ref_audio_path"].endswith("murasame_0001.mp3")


def test_character_menu_hides_internal_profile_ids_and_supports_character_name():
    settings = Settings(_env_file=None)
    menu = voice_profile_menu(settings)
    assert menu == "可用角色\n- 小丛雨（中 / 日 / 英）\n发送“选择角色 角色名”切换"
    assert "murasame" not in menu
    assert "音色" not in menu
    assert resolve_character_profile(settings, "小丛雨") == "murasame"
    assert resolve_character_profile(settings, "murasame") == "murasame"


def test_detect_speech_language_for_chinese_and_english():
    assert detect_speech_language("你好，今天开心吗？") == "zh"
    assert detect_speech_language("Hello, are you okay?") == "en"
    assert detect_speech_language("こんにちは、元気ですか？") == "ja"
    assert detect_speech_language("今日はいい天気ですね") == "ja"
    assert detect_speech_language("Hello 你好") == "zh"
    assert detect_speech_language("这个接口支持 AI 和 v2 配置") == "zh"
    assert detect_speech_language("请看 https://example.test/api") == "zh"
    assert detect_speech_language("https://example.test/api") == "zh"
    assert detect_speech_language("getUserName") == "zh"
    assert detect_speech_language("模型は V2") == "zh"
    assert detect_speech_language("任意文本", preferred="en") == "en"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("你好，今天过得怎么样", "zh"),
        ("Hello, how are you?", "zh"),
        ("介绍一下英语学习方法", "zh"),
        ("介绍一下日语学习方法", "zh"),
        ("你会说英语吗？", "zh"),
        ("日语怎么说谢谢？", "zh"),
        ("请用英语回答", "en"),
        ("帮我用英语说一遍", "en"),
        ("请你用日语回答", "ja"),
        ("我想让你用日语回答", "ja"),
        ("我希望你用英语回答", "en"),
        ("小丛雨帮我用英语回答", "en"),
        ("麻烦你用日语回复", "ja"),
        ("能否用英文回复", "en"),
        ("你可以用英语回答吗？", "en"),
        ("能不能用日语说一遍？", "ja"),
        ("in English, please", "en"),
        ("用日语说一遍", "ja"),
        ("日本語で答えて", "ja"),
        ("不要用英语", "zh"),
        ("不要帮我用英语回答", "zh"),
        ("请你不要用日语回答", "zh"),
        ("介绍一下如何用日语回答问题", "zh"),
        ("不要用英语，改用日语回答", "ja"),
        ("先用英语回答，换成中文回复", "zh"),
    ],
)
def test_requested_speech_language_defaults_to_chinese_without_explicit_request(
    prompt: str, expected: str
):
    assert requested_speech_language(prompt) == expected


def test_voice_text_language_rejects_clear_frontend_mismatch():
    validate_voice_text_language("你好，今天的 AI 工具很好用", "zh")
    validate_voice_text_language("Hello, 小丛雨。", "en")
    with pytest.raises(RuntimeError, match="主要为英语"):
        validate_voice_text_language("This is a long English answer for you.", "zh")
    with pytest.raises(RuntimeError, match="主要为英语"):
        validate_voice_text_language("Hello, how are you?", "zh")
    with pytest.raises(RuntimeError, match="主要为英语"):
        validate_voice_text_language("苟修金，吾辈来说：This is a long English answer for you.", "zh")
    with pytest.raises(RuntimeError, match="主要为日语"):
        validate_voice_text_language("こんにちは、元気にしていますか？", "zh")
    with pytest.raises(RuntimeError, match="不像英语"):
        validate_voice_text_language("今天的天气很好，我们一起出去走走怎么样？", "en")


@pytest.mark.asyncio
async def test_long_voice_reply_falls_back_instead_of_silently_truncating():
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://127.0.0.1:9880",
        voice_max_chars=20,
    )
    with pytest.raises(RuntimeError, match="回退完整文字"):
        await synthesize_voice("这是一条不能被悄悄截断的完整回复。" * 3, settings)


def _pcm_wav(amplitude: int) -> bytes:
    samples = struct.pack("<" + "h" * 16000, *([amplitude] * 16000))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples)
    return stream.getvalue()


def test_near_silent_wav_is_rejected_before_sending():
    with pytest.raises(RuntimeError, match="接近静音"):
        _reject_near_silent_wav(_pcm_wav(80))
    _reject_near_silent_wav(_pcm_wav(1200))
