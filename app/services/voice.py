"""Opt-in multilingual character voice output."""

import base64
import io
import json
import math
import re
import struct
import wave
from pathlib import Path

import httpx

from app.config import Settings

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")

_LANGUAGE_DIRECTIVE_RE = re.compile(
    r"(?:^|[，,。.!！?？；;：:\s])(?:小?丛雨[,，]?)?"
    r"(?P<early_negation>不要|别|不需要|无需|禁止)?"
    r"(?:你)?(?:请你帮我|请你|麻烦你|帮我|替我|我想让你|我希望你|希望你|让你|我想|想要)?"
    r"(?:可不可以|能不能|能否|可以|能)?(?:请|麻烦)?"
    r"(?:"
    r"(?P<negation>不要|别|不需要|无需|禁止)?(?:再)?"
    r"(?:改用|换成|用|说|讲|读)(?:标准)?"
    r"(?P<language>中文|汉语|普通话|日语|日文|英语|英文)"
    r"(?:回答|回复|朗读|说|讲)?|"
    r"(?P<language_first>中文|汉语|普通话|日语|日文|英语|英文)"
    r"(?:回答|回复|朗读|说|讲)"
    r")",
)
_LANGUAGE_CODES = {
    "中文": "zh",
    "汉语": "zh",
    "普通话": "zh",
    "日语": "ja",
    "日文": "ja",
    "英语": "en",
    "英文": "en",
}
_ENGLISH_DIRECTIVE_RE = re.compile(r"\bin\s+english\b", re.IGNORECASE)
_JAPANESE_DIRECTIVE_RE = re.compile(r"日本語で")


def voice_profiles(settings: Settings) -> dict[str, dict[str, str]]:
    """Return character voice profiles without exposing internal IDs to users."""
    try:
        payload = json.loads(settings.voice_profiles_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    profiles: dict[str, dict[str, str]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            profile_id = str(key).strip()
            if not profile_id or not re_safe_profile_id(profile_id):
                continue
            profiles[profile_id] = {
                field: str(value.get(field) or "").strip()
                for field in (
                    "label",
                    "voice",
                    "model",
                    "language",
                    "target_language",
                    "prompt_lang",
                    "prompt_text",
                    "ref_audio_path",
                    "instructions",
                    "languages",
                )
            }
    if not profiles:
        profiles["default"] = {
            "label": "默认角色",
            "voice": settings.voice_name,
            "model": settings.voice_model,
            "language": "zh",
            "target_language": "zh",
            "prompt_lang": "zh",
            "prompt_text": "",
            "ref_audio_path": "",
            "instructions": "",
            "languages": "中文",
        }
    return profiles


def re_safe_profile_id(value: str) -> bool:
    return all(char.isalnum() or char in {"_", "-"} for char in value) and len(value) <= 48


def character_languages(profile: dict[str, str]) -> str:
    """Return a compact display label for the character's supported languages."""
    configured = profile.get("languages", "").strip()
    if configured:
        return configured
    language = profile.get("target_language") or profile.get("language") or "auto"
    return {
        "auto": "中 / 日 / 英",
        "zh": "中文",
        "ja": "日语",
        "en": "英语",
        "ko": "韩语",
        "yue": "粤语",
    }.get(language, language)


def character_label(profile_id: str, profile: dict[str, str]) -> str:
    return profile.get("label") or profile_id


def voice_profile_menu(settings: Settings) -> str:
    lines = ["可用角色"]
    for profile_id, profile in voice_profiles(settings).items():
        lines.append(f"- {character_label(profile_id, profile)}（{character_languages(profile)}）")
    lines.append("发送“选择角色 角色名”切换")
    return "\n".join(lines)


def resolve_character_profile(settings: Settings, selection: str) -> str | None:
    """Resolve a user-facing character name, preserving old internal IDs as aliases."""
    requested = re.sub(r"\s+", "", str(selection or "")).casefold()
    if not requested:
        return None
    profiles = voice_profiles(settings)
    if selection in profiles:
        return selection
    matches = [
        profile_id
        for profile_id, profile in profiles.items()
        if re.sub(r"\s+", "", character_label(profile_id, profile)).casefold() == requested
    ]
    return matches[0] if len(matches) == 1 else None


def detect_speech_language(text: str, preferred: str = "auto") -> str:
    """Return the target language code used by multilingual TTS backends.

    Automatic detection intentionally defaults to Chinese.  Character replies
    often contain an English product name, acronym, or short quote; treating
    any Latin character as English makes GPT-SoVITS use the wrong frontend.
    A configured non-``auto`` preference is the explicit language override.
    """
    requested = str(preferred or "auto").strip().casefold()
    if requested in {"zh", "en", "ja", "ko", "yue"}:
        return requested
    candidate = str(text or "")
    kana_count = len(_KANA_RE.findall(candidate))
    cjk_count = len(_CJK_RE.findall(candidate))
    # Japanese ordinarily includes several kana; a lone kana in an otherwise
    # Chinese sentence is not enough to override the safe Chinese default.
    if kana_count >= 2 and kana_count >= cjk_count * 0.15:
        return "ja"
    if cjk_count:
        return "zh"
    # Ignore URLs before inspecting Latin words: their host/path components do
    # not indicate the language that should be spoken.
    prose_candidate = re.sub(r"(?:https?://|www\.)\S+", "", candidate, flags=re.IGNORECASE)
    latin_words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", prose_candidate)
    latin_letters = sum(len(word.replace("'", "")) for word in latin_words)
    # Require an actual English-looking utterance.  This keeps "AI", "v2",
    # URLs and identifiers from flipping a Chinese reply to English.
    if latin_letters >= 4 and len(latin_words) >= 2:
        return "en"
    return "zh"


def requested_speech_language(prompt: str) -> str:
    """Resolve an explicit per-message voice language; otherwise use Chinese.

    The generated answer itself is deliberately not used as the language
    switch.  Product names, quotations, or an LLM unexpectedly replying in a
    foreign language must not silently change the user's voice preference.
    """
    candidate = " ".join(str(prompt or "").split())
    if not candidate:
        return "zh"
    directives: list[tuple[int, str]] = []
    for match in _LANGUAGE_DIRECTIVE_RE.finditer(candidate):
        # A negative preference (for example, “不要用英语”) is not a request
        # to synthesize another foreign language.  With no later positive
        # directive, the product rule is to fall back to Chinese.
        if match.group("early_negation") or match.group("negation"):
            continue
        language = match.group("language") or match.group("language_first")
        directives.append((match.start(), _LANGUAGE_CODES[language]))
    directives.extend(
        (match.start(), "en") for match in _ENGLISH_DIRECTIVE_RE.finditer(candidate)
    )
    directives.extend(
        (match.start(), "ja") for match in _JAPANESE_DIRECTIVE_RE.finditer(candidate)
    )
    # If a user corrects themselves in one message, the last positive request
    # wins: “不要用英语，改用日语回答” must resolve to Japanese.
    return max(directives, default=(-1, "zh"), key=lambda item: item[0])[1]


def validate_voice_text_language(text: str, target_language: str) -> None:
    """Reject clear text/frontend mismatches before producing garbled speech.

    This intentionally does not try to classify mixed language or short
    fragments. It only guards the obvious case where the LLM answered in a
    different script from the explicitly selected GPT-SoVITS frontend.
    """
    candidate = re.sub(
        r"(?:https?://|www\.)\S+", "", str(text or ""), flags=re.IGNORECASE
    )
    cjk_count = len(_CJK_RE.findall(candidate))
    kana_count = len(_KANA_RE.findall(candidate))
    latin_count = len(re.findall(r"[A-Za-z]", candidate))
    latin_words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", candidate)
    if target_language == "zh" and kana_count >= 6 and kana_count > cjk_count:
        raise RuntimeError("回复主要为日语，已避免按中文发音并回退文字")
    if (
        target_language == "zh"
        and len(latin_words) >= 2
        and latin_count >= 10
        and latin_count > cjk_count * 2
    ):
        raise RuntimeError("回复主要为英语，已避免按中文发音并回退文字")
    if target_language == "ja" and cjk_count >= 12 and kana_count == 0:
        raise RuntimeError("回复不像日语，已避免错误语音并回退文字")
    if target_language == "en" and cjk_count >= 12 and latin_count < 5:
        raise RuntimeError("回复不像英语，已避免错误语音并回退文字")


def _project_relative_path(value: str) -> str:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return str(path.resolve())


def _reject_near_silent_wav(raw: bytes) -> None:
    """Reject a broken TTS result before it becomes a group voice message.

    A failed/under-trained SoVITS checkpoint can still return HTTP 200 and a
    valid WAV container while containing little more than a short breath.  We
    only inspect PCM WAV responses (other providers may return MP3/OGG), and
    keep the threshold deliberately conservative so quiet speech is retained.
    """
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            frame_count = audio.getnframes()
            sample_rate = audio.getframerate()
            sample_width = audio.getsampwidth()
            channels = audio.getnchannels()
            pcm = audio.readframes(frame_count)
    except (EOFError, ValueError, wave.Error):
        return
    if not sample_rate or not frame_count or sample_width != 2 or channels < 1:
        return
    sample_count = len(pcm) // 2
    if sample_count == 0:
        raise RuntimeError("TTS 返回为空音频，已拒绝发送")
    samples = struct.unpack("<" + "h" * sample_count, pcm[: sample_count * 2])
    rms = math.sqrt(sum(sample * sample for sample in samples) / sample_count)
    peak = max(abs(sample) for sample in samples)
    duration = frame_count / sample_rate
    if duration >= 0.35 and rms < 300 and peak < 3000:
        raise RuntimeError("TTS 返回接近静音，已拒绝发送并回退文字回复")


async def synthesize_voice(
    text: str,
    settings: Settings,
    profile_name: str | None = None,
    target_language: str | None = None,
) -> str:
    if not settings.voice_enabled:
        raise RuntimeError("语音服务暂不可用，请稍后再试。")
    provider = str(settings.voice_provider or "openai_compatible").strip().casefold()
    if provider not in {
        "openai_compatible",
        "openai_compatible_extended",
        "gpt_sovits",
        "gpt-sovits",
    }:
        raise RuntimeError(f"未实现的语音 provider：{settings.voice_provider}")
    endpoint = str(settings.voice_api_url or "").strip().rstrip("/")
    if not endpoint:
        raise RuntimeError("语音服务暂不可用，请稍后再试。")
    clean = " ".join(str(text or "").split())
    if not clean:
        raise ValueError("没有可转换成语音的文字")
    # Sending the first N characters as the entire voice reply silently drops
    # the rest of the answer once text is suppressed.  Preserve the complete
    # answer through the caller's text fallback until chunked TTS is supported.
    if len(clean) > max(20, int(settings.voice_max_chars)):
        raise RuntimeError("回复超过语音长度上限，已回退完整文字")
    headers = {"Content-Type": "application/json"}
    if settings.voice_api_key:
        headers["Authorization"] = f"Bearer {settings.voice_api_key}"
    profiles = voice_profiles(settings)
    profile = profiles.get(profile_name or settings.voice_profile_default) or profiles.get(
        settings.voice_profile_default
    ) or profiles["default"]
    if provider in {"gpt_sovits", "gpt-sovits"}:
        if not endpoint.endswith("/tts"):
            endpoint += "/tts"
        reference = _project_relative_path(profile.get("ref_audio_path", ""))
        if not Path(reference).is_file():
            raise RuntimeError("所选角色的语音暂不可用，请稍后再试。")
        prompt_text = profile.get("prompt_text", "")
        prompt_lang = detect_speech_language(
            prompt_text,
            profile.get("prompt_lang") or profile.get("language") or "zh",
        )
        target_language = detect_speech_language(
            clean,
            target_language
            or profile.get("target_language")
            or profile.get("language")
            or "zh",
        )
        validate_voice_text_language(clean, target_language)
        payload = {
            "text": clean,
            "text_lang": target_language,
            "ref_audio_path": reference,
            "prompt_lang": prompt_lang,
            "prompt_text": prompt_text,
            "seed": int(settings.voice_seed),
            "top_k": max(1, int(settings.voice_top_k)),
            "temperature": max(0.1, float(settings.voice_temperature)),
            "repetition_penalty": max(1.0, float(settings.voice_repetition_penalty)),
            "media_type": "wav",
            "streaming_mode": False,
        }
    else:
        if not endpoint.endswith("/audio/speech"):
            endpoint += "/audio/speech"
        payload = {"input": clean, "voice": profile.get("voice") or settings.voice_name}
        model = profile.get("model") or settings.voice_model
        if model:
            payload["model"] = model
        # Non-standard fields are opt-in because many OpenAI-compatible endpoints
        # reject unknown JSON keys. Enable them only for a provider documented to
        # accept multilingual/instruction fields.
        if settings.voice_supports_language_fields:
            if profile.get("language"):
                payload["language"] = profile["language"]
            if profile.get("instructions"):
                payload["instructions"] = profile["instructions"]
    async with httpx.AsyncClient(
        timeout=min(max(float(settings.voice_timeout_seconds), 5), 120),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith(("audio/", "application/octet-stream")):
            raise RuntimeError("TTS 接口返回的内容不是音频")
        raw = response.content
    if not raw or len(raw) > settings.media_max_bytes:
        raise RuntimeError("TTS 返回为空或超过 MEDIA_MAX_BYTES")
    _reject_near_silent_wav(raw)
    return "base64://" + base64.b64encode(raw).decode("ascii")
