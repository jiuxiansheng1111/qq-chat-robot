from app.config import Settings
from app.services.voice import detect_speech_language, voice_profiles


def test_voice_profile_includes_bilingual_murasame_reference():
    settings = Settings(_env_file=None)
    profile = voice_profiles(settings)["murasame"]
    assert profile["target_language"] == "auto"
    assert profile["prompt_lang"] == "zh"
    assert profile["ref_audio_path"].endswith("murasame_0001.mp3")


def test_detect_speech_language_for_chinese_and_english():
    assert detect_speech_language("你好，今天开心吗？") == "zh"
    assert detect_speech_language("Hello, are you okay?") == "en"
    assert detect_speech_language("Hello 你好") == "zh"
