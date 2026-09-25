"""Opt-in voice output and local Murasame clip support."""

import asyncio
import base64
import json
import random
import re
import time
from pathlib import Path

import httpx

from app.config import Settings

VOICE_SUFFIXES = frozenset({".mp3", ".wav", ".ogg", ".amr", ".silk", ".m4a"})
_VOICE_PATH_CACHE: dict[str, tuple[float, tuple[Path, ...]]] = {}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def local_voice_paths(settings: Settings, profile_name: str | None = None) -> list[Path]:
    root = Path(settings.voice_local_dir).expanduser()
    if profile_name:
        profile_root = root / profile_name
        if profile_root.exists():
            root = profile_root
    cache_key = str(root.resolve())
    now = time.monotonic()
    cached = _VOICE_PATH_CACHE.get(cache_key)
    if cached and now - cached[0] < 15:
        return list(cached[1])
    if not root.exists():
        paths: list[Path] = []
    else:
        paths = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in VOICE_SUFFIXES
        ]
    _VOICE_PATH_CACHE[cache_key] = (now, tuple(paths))
    return paths


def voice_setup_help(settings: Settings) -> str:
    return (
        "语音功能目前没有可用音频。可以把已获授权的丛雨音频放到：\n"
        f"{Path(settings.voice_local_dir).expanduser()}\n"
        "或配置 VOICE_API_URL/VOICE_API_KEY 后使用兼容 /v1/audio/speech 的 TTS；"
        "GPT-SoVITS 使用 VOICE_PROVIDER=gpt_sovits 和 /tts。"
    )


def voice_profiles(settings: Settings) -> dict[str, dict[str, str]]:
    """Return configured voice profiles without allowing malformed config to crash chat."""
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
                )
            }
    if not profiles:
        profiles["default"] = {
            "label": "默认音色",
            "voice": settings.voice_name,
            "model": settings.voice_model,
            "language": "zh",
            "target_language": "auto",
            "prompt_lang": "zh",
            "prompt_text": "",
            "ref_audio_path": "",
            "instructions": "",
        }
    return profiles


def re_safe_profile_id(value: str) -> bool:
    return all(char.isalnum() or char in {"_", "-"} for char in value) and len(value) <= 48


def voice_profile_menu(settings: Settings) -> str:
    lines = ["✦ 可用音色 ✦"]
    for profile_id, profile in voice_profiles(settings).items():
        language = profile.get("language") or "未指定"
        label = profile.get("label") or profile_id
        lines.append(f"- {profile_id}：{label}（{language}）")
    lines.append(
        "选择：发送“选择音色 音色ID”（或“切换音色 音色ID”）；"
        "语言/音色由管理员在 VOICE_PROFILES_JSON 配置。"
    )
    return "\n".join(lines)


def detect_speech_language(text: str, preferred: str = "auto") -> str:
    """Return the target language code used by multilingual TTS backends."""
    requested = str(preferred or "auto").strip().casefold()
    if requested in {"zh", "en", "ja", "ko", "yue"}:
        return requested
    cjk_count = len(_CJK_RE.findall(text or ""))
    if cjk_count:
        return "zh"
    return "en" if _LATIN_RE.search(text or "") else "zh"


def _project_relative_path(value: str) -> str:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return str(path.resolve())


async def random_local_voice(settings: Settings, profile_name: str | None = None) -> str:
    paths = local_voice_paths(settings, profile_name)
    if not paths:
        raise FileNotFoundError(voice_setup_help(settings))
    path = random.SystemRandom().choice(paths)
    raw = await asyncio.to_thread(path.read_bytes)
    if len(raw) > settings.media_max_bytes:
        raise RuntimeError(f"语音素材超过 MEDIA_MAX_BYTES：{path.name}")
    return "base64://" + base64.b64encode(raw).decode("ascii")


async def synthesize_voice(
    text: str,
    settings: Settings,
    profile_name: str | None = None,
) -> str:
    if not settings.voice_enabled:
        raise RuntimeError("语音功能未开启，请设置 VOICE_ENABLED=true")
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
        raise RuntimeError("VOICE_API_URL 未配置")
    clean = " ".join(str(text or "").split())[: max(20, int(settings.voice_max_chars))]
    if not clean:
        raise ValueError("没有可转换成语音的文字")
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
            raise RuntimeError(f"GPT-SoVITS 参考音频不存在：{reference}")
        prompt_text = profile.get("prompt_text", "")
        prompt_lang = detect_speech_language(
            prompt_text,
            profile.get("prompt_lang") or profile.get("language") or "zh",
        )
        target_language = detect_speech_language(
            clean,
            profile.get("target_language") or profile.get("language") or "auto",
        )
        payload = {
            "text": clean,
            "text_lang": target_language,
            "ref_audio_path": reference,
            "prompt_lang": prompt_lang,
            "prompt_text": prompt_text,
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
    return "base64://" + base64.b64encode(raw).decode("ascii")
