import asyncio
import io
import json
import struct
import wave
from typing import ClassVar

import httpx
import pytest

from app.config import Settings
from app.services.voice import (
    _raise_for_gpt_sovits_tts_response,
    _reject_near_silent_wav,
    detect_speech_language,
    gpt_sovits_language_weights,
    requested_speech_language,
    resolve_character_profile,
    selected_gpt_sovits_weight,
    selected_gpt_weight,
    synthesize_voice,
    validate_voice_text_language,
    voice_profile_authorized,
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
    assert settings.voice_sovits_weights_by_language_json == ""
    assert settings.voice_sovits_weights_by_profile_json == ""
    assert settings.voice_gpt_weights_by_profile_json == ""


def test_character_menu_hides_internal_profile_ids_and_supports_character_name():
    settings = Settings(_env_file=None)
    menu = voice_profile_menu(settings)
    assert menu == "可用角色\n- 小丛雨（中 / 日 / 英）\n发送“选择角色 角色名”切换"
    assert "murasame" not in menu
    assert "音色" not in menu
    assert resolve_character_profile(settings, "小丛雨") == "murasame"
    assert resolve_character_profile(settings, "murasame") == "murasame"


@pytest.mark.asyncio
async def test_account_scoped_voice_profile_is_hidden_and_cannot_synthesize():
    settings = Settings(
        _env_file=None,
        onebot_self_id="primary-test-id",
        onebot_self_id_2="secondary-test-id",
        voice_enabled=True,
        voice_api_url="http://127.0.0.1:1",
        voice_primary_account_profile_ids="yoshino,mako",
        voice_profiles_json=json.dumps({
            "murasame": {"label": "小丛雨"},
            "yoshino": {"label": "芳乃"},
            "mako": {"label": "茉子"},
        }),
    )
    assert voice_profile_authorized(settings, "yoshino", "primary-test-id")
    assert not voice_profile_authorized(settings, "yoshino", "secondary-test-id")
    assert not voice_profile_authorized(settings, "yoshino", None)
    assert "芳乃" in voice_profile_menu(settings, "primary-test-id")
    assert "芳乃" not in voice_profile_menu(settings, "secondary-test-id")
    assert resolve_character_profile(settings, "芳乃", "secondary-test-id") is None
    assert resolve_character_profile(settings, "芳乃", "primary-test-id") == "yoshino"
    with pytest.raises(RuntimeError, match="指定机器人账号"):
        await synthesize_voice(
            "你好", settings, profile_name="yoshino", bot_self_id="secondary-test-id"
        )


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
        ("请用粤语回答", "yue"),
        ("用广东话说一遍", "yue"),
        ("不要用粤语", "zh"),
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


def test_gpt_sovits_english_nltk_failure_has_actionable_diagnostic():
    response = httpx.Response(
        400,
        json={
            "message": "tts failed",
            "Exception": "Resource 'averaged_perceptron_tagger_eng' not found.",
        },
        request=httpx.Request("POST", "http://voice.test/tts"),
    )

    with pytest.raises(RuntimeError, match="averaged_perceptron_tagger_eng") as exc_info:
        _raise_for_gpt_sovits_tts_response(response)

    assert "nltk.downloader" in str(exc_info.value)


class _FakeTtsResponse:
    headers: ClassVar[dict[str, str]] = {"content-type": "audio/wav"}
    content: ClassVar[bytes] = b"audio"

    def raise_for_status(self):
        return None


class _RecordingGptSovitsClient:
    events: ClassVar[list[tuple[str, str]]] = []
    payloads: ClassVar[list[dict]] = []
    active_weight: ClassVar[str] = ""

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _url, **kwargs):
        self.__class__.active_weight = kwargs["params"]["weights_path"]
        self.__class__.events.append(("switch", self.__class__.active_weight))
        return _FakeTtsResponse()

    async def post(self, _url, **_kwargs):
        # Give a competing task a chance to run. Without the sidecar lock, a
        # second switch can occur before this request records its active model.
        await asyncio.sleep(0)
        self.__class__.events.append(("tts", self.__class__.active_weight))
        self.__class__.payloads.append(_kwargs["json"])
        return _FakeTtsResponse()


def _mapped_gpt_sovits_settings(tmp_path, endpoint="http://voice.test:19880"):
    reference = tmp_path / "reference.wav"
    e12 = tmp_path / "e12.pth"
    e13 = tmp_path / "e13.pth"
    for path in (reference, e12, e13):
        path.write_bytes(b"test")
    return Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url=endpoint,
        voice_profiles_json=json.dumps(
            {
                "murasame": {
                    "label": "小丛雨",
                    "ref_audio_path": str(reference),
                    "language": "zh",
                    "target_language": "zh",
                    "prompt_lang": "ja",
                    "languages": "中 / 日 / 英",
                }
            }
        ),
        voice_sovits_weights_by_language_json=json.dumps(
            {"zh": str(e13), "ja": str(e12), "en": str(e12)}
        ),
    )


def test_gpt_sovits_language_weight_map_uses_e13_for_chinese_and_e12_for_ja_en(tmp_path):
    settings = _mapped_gpt_sovits_settings(tmp_path)
    weights = gpt_sovits_language_weights(settings)
    assert weights["zh"].endswith("e13.pth")
    assert weights["ja"].endswith("e12.pth")
    assert weights["en"] == weights["ja"]


def test_profile_specific_weights_keep_default_and_other_roles_separate(tmp_path):
    settings = _mapped_gpt_sovits_settings(tmp_path)
    yoshino = tmp_path / "yoshino.pth"
    yoshino.write_bytes(b"test")
    settings.voice_sovits_weights_by_profile_json = json.dumps(
        {"yoshino": {"zh": str(yoshino)}}
    )

    assert selected_gpt_sovits_weight(settings, "murasame", "zh").endswith("e13.pth")
    assert selected_gpt_sovits_weight(settings, "yoshino", "zh") == str(yoshino.resolve())
    with pytest.raises(RuntimeError, match="mako/zh"):
        selected_gpt_sovits_weight(settings, "mako", "zh")
    with pytest.raises(RuntimeError, match="yoshino/ja"):
        selected_gpt_sovits_weight(settings, "yoshino", "ja")


@pytest.mark.asyncio
async def test_language_specific_reference_and_prompt_values_override_legacy_fields(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.events = []
    _RecordingGptSovitsClient.payloads = []
    _RecordingGptSovitsClient.active_weight = ""
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path, "http://voice.test:19883")
    japanese_reference = tmp_path / "japanese-reference.wav"
    japanese_reference.write_bytes(b"test")
    legacy_reference = tmp_path / "reference.wav"
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {
                "label": "小丛雨",
                "ref_audio_path": str(legacy_reference),
                "ref_audio_path_by_language": {"ja": str(japanese_reference)},
                "prompt_text": "legacy prompt",
                "prompt_text_by_language": {"ja": "これは日本語の参照文です。"},
                "prompt_lang": "zh",
                "prompt_lang_by_language": {"ja": "ja"},
            }
        }
    )

    await synthesize_voice("こんにちは", settings, target_language="ja")
    japanese_payload = _RecordingGptSovitsClient.payloads[-1]
    assert japanese_payload["ref_audio_path"] == str(japanese_reference.resolve())
    assert japanese_payload["prompt_text"] == "これは日本語の参照文です。"
    assert japanese_payload["prompt_lang"] == "ja"

    await synthesize_voice("Hello there", settings, target_language="en")
    fallback_payload = _RecordingGptSovitsClient.payloads[-1]
    assert fallback_payload["ref_audio_path"] == str(legacy_reference.resolve())
    assert fallback_payload["prompt_text"] == "legacy prompt"
    assert fallback_payload["prompt_lang"] == "zh"


@pytest.mark.asyncio
async def test_supported_languages_controls_menu_and_blocks_unaccepted_tts(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path, "http://voice.test:19887")
    settings.voice_profile_default = "yoshino"
    reference = tmp_path / "reference.wav"
    settings.voice_profiles_json = json.dumps(
        {
            "yoshino": {
                "label": "芳乃",
                "ref_audio_path": str(reference),
                "languages": "中 / 日 / 英 / 粤",
                "supported_languages": ["zh", "en", "yue"],
            }
        }
    )

    assert voice_profile_menu(settings) == (
        "可用角色\n- 芳乃（中文 / 英语 / 粤语）\n发送“选择角色 角色名”切换\n"
        "明确说“用粤语回答”可切换本轮语音，默认中文"
    )
    await synthesize_voice("Hello there", settings, target_language="en")
    request_count = len(_RecordingGptSovitsClient.payloads)

    with pytest.raises(RuntimeError, match="尚未验收 日语"):
        await synthesize_voice("こんにちは", settings, target_language="ja")

    assert len(_RecordingGptSovitsClient.payloads) == request_count


@pytest.mark.asyncio
async def test_legacy_profiles_must_explicitly_accept_cantonese_before_synthesis(
    monkeypatch, tmp_path
):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    reference = tmp_path / "legacy-reference.wav"
    reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19890",
        voice_profiles_json=json.dumps(
            {"murasame": {"label": "小丛雨", "ref_audio_path": str(reference)}}
        ),
    )

    with pytest.raises(RuntimeError, match="尚未验收 粤语"):
        await synthesize_voice(
            "今日天气真好，我哋一齐出去行下啦。",
            settings,
            profile_name="murasame",
            target_language="yue",
        )

    assert _RecordingGptSovitsClient.payloads == []


@pytest.mark.asyncio
async def test_cantonese_profile_routes_cantonese_text_with_chinese_voice_reference(
    monkeypatch, tmp_path
):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    reference = tmp_path / "cantonese-reference.wav"
    reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19891",
        voice_profiles_json=json.dumps(
            {
                "aimisi": {
                    "label": "爱弥斯",
                    "ref_audio_path": str(reference),
                    "prompt_lang": "zh",
                    "prompt_text": "希望这份力量，也能让隧者更长久地保护拉海洛。",
                    "supported_languages": ["zh", "ja", "en", "yue"],
                }
            }
        ),
    )

    menu = voice_profile_menu(settings)
    assert "爱弥斯（中文 / 日语 / 英语 / 粤语）" in menu
    assert "明确说“用粤语回答”可切换本轮语音，默认中文" in menu
    await synthesize_voice(
        "今日天气真好，我哋一齐出去行下啦。",
        settings,
        profile_name="aimisi",
        target_language="yue",
    )

    payload = _RecordingGptSovitsClient.payloads[-1]
    assert payload["text_lang"] == "all_yue"
    assert payload["prompt_lang"] == "zh"
    assert payload["ref_audio_path"] == str(reference.resolve())


@pytest.mark.asyncio
async def test_profile_text_split_method_overrides_only_the_configured_role(monkeypatch, tmp_path):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    murasame_reference = tmp_path / "murasame.wav"
    mako_reference = tmp_path / "mako.wav"
    murasame_reference.write_bytes(b"test")
    mako_reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19889",
        voice_profiles_json=json.dumps(
            {
                "murasame": {
                    "label": "小丛雨",
                    "ref_audio_path": str(murasame_reference),
                    "text_split_method": "cut3",
                },
                "mako": {"label": "茉子", "ref_audio_path": str(mako_reference)},
            }
        ),
    )

    await synthesize_voice("这是小丛雨的长句。", settings, profile_name="murasame")
    assert _RecordingGptSovitsClient.payloads[-1]["text_split_method"] == "cut3"

    await synthesize_voice("这是茉子的默认长句。", settings, profile_name="mako")
    assert "text_split_method" not in _RecordingGptSovitsClient.payloads[-1]


@pytest.mark.asyncio
async def test_invalid_profile_text_split_method_fails_closed_before_tts(monkeypatch, tmp_path):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19890",
        voice_profiles_json=json.dumps(
            {
                "murasame": {
                    "label": "小丛雨",
                    "ref_audio_path": str(reference),
                    "text_split_method": "cut9",
                }
            }
        ),
    )

    with pytest.raises(RuntimeError, match="仅可为 cut0 至 cut5"):
        await synthesize_voice("这条请求不能到达 TTS。", settings)

    assert _RecordingGptSovitsClient.payloads == []


@pytest.mark.asyncio
async def test_invalid_supported_languages_remains_visible_but_fails_closed(monkeypatch, tmp_path):
    from app.services import voice

    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19888",
        voice_profiles_json=json.dumps(
            {
                "yoshino": {
                    "label": "芳乃",
                    "ref_audio_path": str(reference),
                    "supported_languages": ["zh", "ko"],
                }
            }
        ),
    )

    assert voice_profile_menu(settings) == "可用角色\n- 芳乃（语音暂不可用）\n发送“选择角色 角色名”切换"
    with pytest.raises(RuntimeError, match="仅可包含不重复的 zh、ja、en、yue"):
        await synthesize_voice("你好", settings, profile_name="yoshino", target_language="zh")


@pytest.mark.asyncio
async def test_invalid_language_specific_profile_value_falls_back_to_text(monkeypatch, tmp_path):
    from app.services import voice

    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path, "http://voice.test:19884")
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {
                "label": "小丛雨",
                "ref_audio_path": str(tmp_path / "reference.wav"),
                "ref_audio_path_by_language": {"not-a-language": "bad.wav"},
            }
        }
    )

    with pytest.raises(RuntimeError, match="ref_audio_path_by_language 配置无效"):
        await synthesize_voice("你好", settings, target_language="zh")


@pytest.mark.asyncio
async def test_unknown_profile_uses_the_only_available_profile_without_key_error(monkeypatch, tmp_path):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    reference = tmp_path / "yoshino-reference.wav"
    reference.write_bytes(b"test")
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        voice_provider="gpt_sovits",
        voice_api_url="http://voice.test:19885",
        voice_profile_default="murasame",
        voice_profiles_json=json.dumps(
            {"yoshino": {"label": "芳乃", "ref_audio_path": str(reference)}}
        ),
    )

    await synthesize_voice("你好", settings, profile_name="removed-profile", target_language="zh")

    assert _RecordingGptSovitsClient.payloads[-1]["ref_audio_path"] == str(reference.resolve())


@pytest.mark.asyncio
async def test_profile_specific_weight_switches_without_using_default_weight(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.events = []
    _RecordingGptSovitsClient.active_weight = ""
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path, "http://voice.test:19882")
    yoshino = tmp_path / "yoshino.pth"
    yoshino.write_bytes(b"test")
    reference = tmp_path / "reference.wav"
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {"label": "小丛雨", "ref_audio_path": str(reference)},
            "yoshino": {"label": "芳乃", "ref_audio_path": str(reference)},
        }
    )
    settings.voice_sovits_weights_by_profile_json = json.dumps(
        {"yoshino": {"zh": str(yoshino)}}
    )

    await synthesize_voice("你好", settings, profile_name="yoshino", target_language="zh")

    assert _RecordingGptSovitsClient.events[0] == ("switch", str(yoshino.resolve()))
    assert _RecordingGptSovitsClient.events[1] == ("tts", str(yoshino.resolve()))


@pytest.mark.asyncio
async def test_gpt_sovits_weight_is_reasserted_after_possible_sidecar_restart(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.events = []
    _RecordingGptSovitsClient.active_weight = ""
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path)

    await synthesize_voice("你好", settings, target_language="zh")
    await synthesize_voice("再见", settings, target_language="zh")

    assert [event[0] for event in _RecordingGptSovitsClient.events] == [
        "switch",
        "tts",
        "switch",
        "tts",
    ]


@pytest.mark.asyncio
async def test_gpt_sovits_switch_and_synthesis_are_serialized(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.events = []
    _RecordingGptSovitsClient.active_weight = ""
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path, "http://voice.test:19881")

    await asyncio.gather(
        synthesize_voice("你好", settings, target_language="zh"),
        synthesize_voice("こんにちは", settings, target_language="ja"),
    )

    events = _RecordingGptSovitsClient.events
    assert [kind for kind, _weight in events] == ["switch", "tts", "switch", "tts"]
    assert events[0][1] == events[1][1]
    assert events[2][1] == events[3][1]
    assert events[0][1] != events[2][1]


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("[]", TypeError),
        ("{", RuntimeError),
        ('{"murasame": 4}', RuntimeError),
        ('{"murasame": "missing.ckpt"}', RuntimeError),
    ],
)
def test_gpt_profile_routing_fails_closed(tmp_path, raw, error):
    settings = _mapped_gpt_sovits_settings(tmp_path)
    settings.voice_gpt_weights_by_profile_json = raw
    with pytest.raises(error):
        selected_gpt_weight(settings, "murasame")


@pytest.mark.parametrize("raw", ["", "{}"])
def test_empty_gpt_profile_routing_preserves_legacy_shared_model(tmp_path, raw):
    settings = _mapped_gpt_sovits_settings(tmp_path)
    settings.voice_gpt_weights_by_profile_json = raw

    assert selected_gpt_weight(settings, "murasame") is None


@pytest.mark.asyncio
async def test_gpt_and_sovits_role_switches_are_one_transaction(monkeypatch, tmp_path):
    from app.services import voice

    events = []

    class Client(_RecordingGptSovitsClient):
        async def get(self, url, **kwargs):
            events.append((url.rsplit("/", 1)[1], kwargs["params"]["weights_path"]))
            await asyncio.sleep(0)
            return _FakeTtsResponse()

        async def post(self, url, **kwargs):
            events.append(("tts", kwargs["json"]["text"]))
            return _FakeTtsResponse()

    monkeypatch.setattr(voice.httpx, "AsyncClient", Client)
    settings = _mapped_gpt_sovits_settings(tmp_path)
    profiles = json.loads(settings.voice_profiles_json)
    profiles["rena"] = dict(profiles["murasame"], label="蕾娜")
    settings.voice_profiles_json = json.dumps(profiles)
    gpt_map = {}
    for role in profiles:
        checkpoint = tmp_path / f"{role}.ckpt"
        checkpoint.write_bytes(b"checkpoint")
        gpt_map[role] = str(checkpoint)
    settings.voice_gpt_weights_by_profile_json = json.dumps(gpt_map)
    sovits_map = {}
    for role in profiles:
        checkpoint = tmp_path / f"{role}-sovits.pth"
        checkpoint.write_bytes(b"checkpoint")
        sovits_map[role] = {"zh": str(checkpoint)}
    settings.voice_sovits_weights_by_profile_json = json.dumps(sovits_map)
    await asyncio.gather(
        synthesize_voice("你好", settings, profile_name="murasame"),
        synthesize_voice("你好", settings, profile_name="rena"),
    )
    assert [event[0] for event in events] == ["set_gpt_weights", "set_sovits_weights", "tts"] * 2
    assert events[0][1] == gpt_map["murasame"]
    assert events[1][1] == sovits_map["murasame"]["zh"]
    assert events[3][1] == gpt_map["rena"]
    assert events[4][1] == sovits_map["rena"]["zh"]


@pytest.mark.asyncio
async def test_missing_gpt_profile_route_fails_before_any_switch_or_synthesis(monkeypatch, tmp_path):
    from app.services import voice

    calls = []

    class Client(_RecordingGptSovitsClient):
        async def get(self, url, **kwargs):
            calls.append(("get", url))
            return _FakeTtsResponse()

        async def post(self, url, **kwargs):
            calls.append(("post", url))
            return _FakeTtsResponse()

    monkeypatch.setattr(voice.httpx, "AsyncClient", Client)
    settings = _mapped_gpt_sovits_settings(tmp_path)
    reference = tmp_path / "rena.wav"
    reference.write_bytes(b"reference")
    rena_sovits = tmp_path / "rena.pth"
    rena_sovits.write_bytes(b"checkpoint")
    rena_gpt = tmp_path / "murasame.ckpt"
    rena_gpt.write_bytes(b"checkpoint")
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {"label": "小丛雨", "ref_audio_path": str(reference)},
            "rena": {"label": "蕾娜", "ref_audio_path": str(reference)},
        }
    )
    settings.voice_sovits_weights_by_profile_json = json.dumps(
        {"rena": {"zh": str(rena_sovits)}}
    )
    settings.voice_gpt_weights_by_profile_json = json.dumps(
        {"murasame": str(rena_gpt)}
    )

    with pytest.raises(RuntimeError, match="rena 未配置专属 GPT 权重"):
        await synthesize_voice("你好", settings, profile_name="rena")

    assert calls == []


@pytest.mark.asyncio
async def test_profile_sampling_overrides_are_local_to_each_role(monkeypatch, tmp_path):
    from app.services import voice

    voice._GPT_SOVITS_LOCKS.clear()
    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path)
    reference = tmp_path / "pilot.wav"
    reference.write_bytes(b"reference")
    settings.voice_seed = 123
    settings.voice_top_k = 13
    settings.voice_temperature = 0.7
    settings.voice_sovits_weights_by_profile_json = json.dumps(
        {"pilot": {"zh": str(tmp_path / "e13.pth")}}
    )
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {"label": "小丛雨", "ref_audio_path": str(reference)},
            "pilot": {
                "label": "试听角色",
                "ref_audio_path": str(reference),
                "seed": 20260929,
                "top_k": 5,
                "temperature": 1.0,
                "parallel_infer": False,
                "split_bucket": False,
                "text_split_method": "cut5",
            },
        }
    )

    await synthesize_voice("你好", settings, profile_name="pilot")
    pilot_payload = _RecordingGptSovitsClient.payloads[-1]
    await synthesize_voice("你好", settings, profile_name="murasame")
    legacy_payload = _RecordingGptSovitsClient.payloads[-1]

    assert {
        key: pilot_payload[key]
        for key in ("seed", "top_k", "temperature", "parallel_infer", "split_bucket", "text_split_method")
    } == {
        "seed": 20260929,
        "top_k": 5,
        "temperature": 1.0,
        "parallel_infer": False,
        "split_bucket": False,
        "text_split_method": "cut5",
    }
    assert legacy_payload["seed"] == 123
    assert legacy_payload["top_k"] == 13
    assert legacy_payload["temperature"] == 0.7
    assert "parallel_infer" not in legacy_payload
    assert "split_bucket" not in legacy_payload
    assert "text_split_method" not in legacy_payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", True),
        ("seed", -2),
        ("seed", 2**32),
        ("top_k", False),
        ("top_k", 0),
        ("top_k", 101),
        ("temperature", True),
        ("temperature", 0.09),
        ("temperature", 2.01),
        ("temperature", "1.0"),
        ("temperature", float("inf")),
        ("parallel_infer", 0),
        ("split_bucket", "false"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_profile_sampling_override_fails_before_tts(
    monkeypatch, tmp_path, field, value
):
    from app.services import voice

    _RecordingGptSovitsClient.payloads = []
    monkeypatch.setattr(voice.httpx, "AsyncClient", _RecordingGptSovitsClient)
    settings = _mapped_gpt_sovits_settings(tmp_path)
    reference = tmp_path / "invalid-param.wav"
    reference.write_bytes(b"reference")
    settings.voice_profiles_json = json.dumps(
        {
            "murasame": {
                "label": "小丛雨",
                "ref_audio_path": str(reference),
                field: value,
            }
        }
    )

    with pytest.raises(RuntimeError, match=field):
        await synthesize_voice("你好", settings)

    assert _RecordingGptSovitsClient.payloads == []


@pytest.mark.asyncio
async def test_gpt_switch_failure_prevents_synthesis(monkeypatch, tmp_path):
    from app.services import voice

    class Client(_RecordingGptSovitsClient):
        async def get(self, url, **kwargs):
            assert url.endswith("/set_gpt_weights")
            return httpx.Response(500, request=httpx.Request("GET", url))

        async def post(self, *_args, **_kwargs):
            pytest.fail("Synthesis must not run with the previous GPT checkpoint")

    monkeypatch.setattr(voice.httpx, "AsyncClient", Client)
    settings = _mapped_gpt_sovits_settings(tmp_path)
    checkpoint = tmp_path / "role.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    settings.voice_gpt_weights_by_profile_json = json.dumps({"murasame": str(checkpoint)})
    with pytest.raises(httpx.HTTPStatusError):
        await synthesize_voice("你好", settings)
