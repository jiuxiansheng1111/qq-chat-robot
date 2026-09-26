import io
import struct
import wave

import pytest

from app.config import Settings
from app.services.voice import (
    _reject_near_silent_wav,
    detect_speech_language,
    resolve_character_profile,
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
    assert profile["target_language"] == "auto"
    assert profile["prompt_lang"] == "zh"
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
