"""Opt-in multilingual character voice output."""

import asyncio
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

# A GPT-SoVITS sidecar has one active GPT and SoVITS model. The lock covers
# both mutable weight switches and the following /tts request.
_GPT_SOVITS_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_SUPPORTED_GPT_SOVITS_WEIGHT_LANGUAGES = frozenset({"zh", "ja", "en", "ko", "yue"})
_SUPPORTED_PROFILE_LANGUAGES = frozenset({"zh", "ja", "en", "yue"})
_PROFILE_LANGUAGE_LABELS = {"zh": "中文", "ja": "日语", "en": "英语", "yue": "粤语"}
_SUPPORTED_GPT_SOVITS_TEXT_SPLIT_METHODS = frozenset(
    {f"cut{index}" for index in range(6)}
)

_LANGUAGE_DIRECTIVE_RE = re.compile(
    r"(?:^|[，,。.!！?？；;：:\s])(?:小?丛雨[,，]?)?"
    r"(?P<early_negation>不要|别|不需要|无需|禁止)?"
    r"(?:你)?(?:请你帮我|请你|麻烦你|帮我|替我|我想让你|我希望你|希望你|让你|我想|想要)?"
    r"(?:可不可以|能不能|能否|可以|能)?(?:请|麻烦)?"
    r"(?:"
    r"(?P<negation>不要|别|不需要|无需|禁止)?(?:再)?"
    r"(?:改用|换成|用|说|讲|读)(?:标准)?"
    r"(?P<language>中文|汉语|普通话|日语|日文|英语|英文|粤语|广东话)"
    r"(?:回答|回复|朗读|说|讲)?|"
    r"(?P<language_first>中文|汉语|普通话|日语|日文|英语|英文|粤语|广东话)"
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
    "粤语": "yue",
    "广东话": "yue",
}
_ENGLISH_DIRECTIVE_RE = re.compile(r"\bin\s+english\b", re.IGNORECASE)
_JAPANESE_DIRECTIVE_RE = re.compile(r"日本語で")


def voice_profiles(settings: Settings) -> dict[str, dict[str, object]]:
    """Return character voice profiles without exposing internal IDs to users."""
    try:
        payload = json.loads(settings.voice_profiles_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    profiles: dict[str, dict[str, object]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            profile_id = str(key).strip()
            if not profile_id or not re_safe_profile_id(profile_id):
                continue
            profile: dict[str, object] = {
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
            # Keep language-specific values raw until synthesis. This lets the
            # menu remain usable while a malformed voice-only setting fails
            # closed at the point it could otherwise produce a wrong voice.
            for field in (
                "ref_audio_path_by_language",
                "prompt_text_by_language",
                "prompt_lang_by_language",
                "supported_languages",
                "text_split_method",
                "seed",
                "top_k",
                "temperature",
                "parallel_infer",
                "split_bucket",
            ):
                if field in value:
                    profile[field] = value[field]
            profiles[profile_id] = profile
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


def profile_supported_languages(profile: dict[str, object]) -> tuple[str, ...] | None:
    """Return an explicit accepted-language allowlist, or preserve legacy behavior.

    A missing field intentionally means the profile predates per-language
    acceptance and retains its existing routing behavior. A present field is
    strict: malformed or unsupported values must not allow an unaccepted TTS
    request through to the sidecar.
    """
    if "supported_languages" not in profile:
        return None
    raw_languages = profile["supported_languages"]
    if not isinstance(raw_languages, list) or not raw_languages:
        raise RuntimeError("所选角色的 supported_languages 配置无效，已回退文字回复")
    if (
        any(not isinstance(language, str) or language not in _SUPPORTED_PROFILE_LANGUAGES for language in raw_languages)
        or len(set(raw_languages)) != len(raw_languages)
    ):
        raise RuntimeError(
            "所选角色的 supported_languages 仅可包含不重复的 zh、ja、en、yue，已回退文字回复"
        )
    return tuple(raw_languages)


def profile_text_split_method(profile: dict[str, object]) -> str | None:
    """Return an optional sidecar-supported split method without changing legacy payloads."""
    if "text_split_method" not in profile:
        return None
    raw_method = profile["text_split_method"]
    if not isinstance(raw_method, str):
        raise RuntimeError(  # noqa: TRY004 - this is a user-safe synthesis failure
            "所选角色的 text_split_method 配置无效，已回退文字回复"
        )
    method = raw_method.strip()
    if not method:
        return None
    if method not in _SUPPORTED_GPT_SOVITS_TEXT_SPLIT_METHODS:
        raise RuntimeError(
            "所选角色的 text_split_method 仅可为 cut0 至 cut5，已回退文字回复"
        )
    return method


def profile_inference_overrides(profile: dict[str, object]) -> dict[str, int | float | bool]:
    """Validate optional per-character GPT-SoVITS sampling overrides."""
    overrides: dict[str, int | float | bool] = {}
    if "seed" in profile:
        seed = profile["seed"]
        if type(seed) is not int or not (-1 <= seed < 2**32):
            raise RuntimeError("所选角色的 seed 必须是 -1 至 2^32-1 的整数，已回退文字回复")
        overrides["seed"] = seed
    if "top_k" in profile:
        top_k = profile["top_k"]
        if type(top_k) is not int or not (1 <= top_k <= 100):
            raise RuntimeError("所选角色的 top_k 必须是 1 至 100 的整数，已回退文字回复")
        overrides["top_k"] = top_k
    if "temperature" in profile:
        temperature = profile["temperature"]
        try:
            numeric_temperature = float(temperature)
        except (OverflowError, TypeError, ValueError):
            numeric_temperature = float("nan")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(numeric_temperature)
            or not (0.1 <= numeric_temperature <= 2.0)
        ):
            raise RuntimeError("所选角色的 temperature 必须在 0.1 至 2.0 之间，已回退文字回复")
        overrides["temperature"] = numeric_temperature
    for field in ("parallel_infer", "split_bucket"):
        if field in profile:
            value = profile[field]
            if type(value) is not bool:
                raise RuntimeError(f"所选角色的 {field} 必须是布尔值，已回退文字回复")
            overrides[field] = value
    return overrides


def character_languages(profile: dict[str, object]) -> str:
    """Return a compact display label for the character's supported languages."""
    try:
        supported_languages = profile_supported_languages(profile)
    except RuntimeError:
        # Keep a role visible in the selector without claiming malformed
        # configuration can synthesize a language.
        return "语音暂不可用"
    if supported_languages is not None:
        return " / ".join(_PROFILE_LANGUAGE_LABELS[language] for language in supported_languages)
    configured = str(profile.get("languages") or "").strip()
    if configured:
        return configured
    language = str(profile.get("target_language") or profile.get("language") or "auto")
    return {
        "auto": "中 / 日 / 英",
        "zh": "中文",
        "ja": "日语",
        "en": "英语",
        "ko": "韩语",
        "yue": "粤语",
    }.get(language, language)


def character_label(profile_id: str, profile: dict[str, object]) -> str:
    return str(profile.get("label") or profile_id)


def voice_profile_authorized(settings: Settings, profile_id: str, bot_self_id: str | None) -> bool:
    """Keep account-scoped character licenses off other OneBot accounts."""
    restricted = {
        item.strip()
        for item in settings.voice_primary_account_profile_ids.split(",")
        if item.strip()
    }
    if profile_id not in restricted:
        return True
    primary_id = str(settings.onebot_self_id or "").strip()
    return bool(primary_id and str(bot_self_id or "").strip() == primary_id)


def voice_profile_menu(settings: Settings, bot_self_id: str | None = None) -> str:
    lines = ["可用角色"]
    cantonese_available = False
    for profile_id, profile in voice_profiles(settings).items():
        if not voice_profile_authorized(settings, profile_id, bot_self_id):
            continue
        lines.append(f"- {character_label(profile_id, profile)}（{character_languages(profile)}）")
        try:
            cantonese_available = cantonese_available or (
                "yue" in (profile_supported_languages(profile) or ())
            )
        except RuntimeError:
            # Invalid per-role language configuration is already displayed as
            # unavailable by character_languages; do not advertise it here.
            continue
    lines.append("发送“选择角色 角色名”切换")
    if cantonese_available:
        lines.append("明确说“用粤语回答”可切换本轮语音，默认中文")
    return "\n".join(lines)


def resolve_character_profile(
    settings: Settings, selection: str, bot_self_id: str | None = None
) -> str | None:
    """Resolve a user-facing character name, preserving old internal IDs as aliases."""
    requested = re.sub(r"\s+", "", str(selection or "")).casefold()
    if not requested:
        return None
    profiles = {
        profile_id: profile
        for profile_id, profile in voice_profiles(settings).items()
        if voice_profile_authorized(settings, profile_id, bot_self_id)
    }
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


def _profile_value_for_language(
    profile: dict[str, object],
    field: str,
    target_language: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Choose an optional language-specific profile value with legacy fallback."""
    mapping_field = f"{field}_by_language"
    legacy = str(profile.get(field) or "").strip()
    if mapping_field not in profile:
        return legacy
    raw_mapping = profile[mapping_field]
    if not isinstance(raw_mapping, dict) or not raw_mapping:
        raise RuntimeError(f"所选角色的 {mapping_field} 配置无效，已回退文字回复")
    values: dict[str, str] = {}
    for language, value in raw_mapping.items():
        code = str(language or "").strip().casefold()
        if code not in _SUPPORTED_GPT_SOVITS_WEIGHT_LANGUAGES or not isinstance(value, str):
            raise RuntimeError(f"所选角色的 {mapping_field} 配置无效，已回退文字回复")
        candidate = value.strip()
        if not candidate and not allow_empty:
            raise RuntimeError(f"所选角色的 {mapping_field} 包含空值，已回退文字回复")
        values[code] = candidate
    return values.get(target_language, legacy)


def gpt_sovits_language_weights(settings: Settings) -> dict[str, str]:
    """Read the optional per-language SoVITS weight map from settings."""
    raw = str(settings.voice_sovits_weights_by_language_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("GPT-SoVITS 语言权重 JSON 配置无效，已回退文字回复") from exc
    if not isinstance(payload, dict):
        raise TypeError("GPT-SoVITS 语言权重必须是 JSON 对象，已回退文字回复")
    weights: dict[str, str] = {}
    for language, configured_path in payload.items():
        code = str(language or "").strip().casefold()
        path = str(configured_path or "").strip()
        if code not in _SUPPORTED_GPT_SOVITS_WEIGHT_LANGUAGES or not path:
            raise RuntimeError("GPT-SoVITS 语言权重配置包含无效语言或路径，已回退文字回复")
        resolved_path = _project_relative_path(path)
        if not Path(resolved_path).is_file():
            raise RuntimeError(
                f"{code} 语言配置的 SoVITS 权重不可用，已回退文字回复"
            )
        weights[code] = resolved_path
    return weights


def gpt_sovits_profile_language_weights(settings: Settings) -> dict[str, dict[str, str]]:
    """Read optional profile-specific GPT-SoVITS weights from settings."""
    raw = str(settings.voice_sovits_weights_by_profile_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("GPT-SoVITS 角色权重 JSON 配置无效，已回退文字回复") from exc
    if not isinstance(payload, dict):
        raise TypeError("GPT-SoVITS 角色权重必须是 JSON 对象，已回退文字回复")
    profiles: dict[str, dict[str, str]] = {}
    for profile_id, language_map in payload.items():
        profile_key = str(profile_id or "").strip()
        if not re_safe_profile_id(profile_key) or not isinstance(language_map, dict):
            raise RuntimeError("GPT-SoVITS 角色权重配置包含无效角色或语言映射，已回退文字回复")
        weights: dict[str, str] = {}
        for language, configured_path in language_map.items():
            code = str(language or "").strip().casefold()
            path = str(configured_path or "").strip()
            if code not in _SUPPORTED_GPT_SOVITS_WEIGHT_LANGUAGES or not path:
                raise RuntimeError("GPT-SoVITS 角色权重配置包含无效语言或路径，已回退文字回复")
            resolved_path = _project_relative_path(path)
            if not Path(resolved_path).is_file():
                raise RuntimeError(
                    f"{profile_key}/{code} 配置的 SoVITS 权重不可用，已回退文字回复"
                )
            weights[code] = resolved_path
        if not weights:
            raise RuntimeError("GPT-SoVITS 角色权重配置不能为空，已回退文字回复")
        profiles[profile_key] = weights
    return profiles


def selected_gpt_sovits_weight(
    settings: Settings, profile_id: str, target_language: str
) -> str | None:
    """Choose a weight without allowing a default profile model to leak.

    The older language map remains an explicit default-profile route. Any
    non-default profile needs its own entry in the nested profile map.
    """
    profile_weights = gpt_sovits_profile_language_weights(settings)
    default_profile = settings.voice_profile_default
    if profile_id != default_profile:
        if profile_weights:
            selected_weight = profile_weights.get(profile_id, {}).get(target_language)
            if selected_weight:
                return selected_weight
        if gpt_sovits_language_weights(settings) or profile_weights:
            raise RuntimeError(
                f"{profile_id}/{target_language} 未配置专属 SoVITS 权重，已回退文字回复"
            )
        return None

    # A profile-specific default entry may override the legacy default map.
    selected_weight = profile_weights.get(profile_id, {}).get(target_language)
    if selected_weight:
        return selected_weight
    language_weights = gpt_sovits_language_weights(settings)
    selected_weight = language_weights.get(target_language)
    if language_weights and not selected_weight:
        raise RuntimeError(
            f"{target_language} 语言未配置 SoVITS 权重，已回退文字回复"
        )
    if profile_weights and not language_weights:
        raise RuntimeError(
            f"{profile_id}/{target_language} 未配置专属 SoVITS 权重，已回退文字回复"
        )
    return selected_weight


def selected_gpt_weight(settings: Settings, profile_id: str) -> str | None:
    """Resolve an explicit GPT route, never retaining another role's model."""
    raw = str(settings.voice_gpt_weights_by_profile_json or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("GPT 角色权重 JSON 配置无效，已回退文字回复") from exc
    if not isinstance(payload, dict):
        raise TypeError("GPT 角色权重必须是 JSON 对象，已回退文字回复")
    if not payload:
        return None
    for key, value in payload.items():
        if not re_safe_profile_id(key) or not isinstance(value, str) or not value.strip():
            raise RuntimeError("GPT 角色权重包含无效角色或路径，已回退文字回复")
    configured_path = payload.get(profile_id)
    if not configured_path:
        raise RuntimeError(f"{profile_id} 未配置专属 GPT 权重，已回退文字回复")
    resolved_path = _project_relative_path(configured_path.strip())
    if not Path(resolved_path).is_file():
        raise RuntimeError(f"{profile_id} 配置的 GPT 权重不可用，已回退文字回复")
    return resolved_path


def _gpt_sovits_control_endpoint(tts_endpoint: str) -> str:
    """Return api_v2's weight-switch endpoint for a normalized /tts URL."""
    return tts_endpoint.rsplit("/tts", 1)[0] + "/set_sovits_weights"


def _gpt_sovits_lock(endpoint: str) -> asyncio.Lock:
    # pytest creates a fresh event loop per test. Locks are loop-local while
    # the active-weight cache deliberately belongs to the process/sidecar.
    key = (id(asyncio.get_running_loop()), endpoint)
    lock = _GPT_SOVITS_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GPT_SOVITS_LOCKS[key] = lock
    return lock


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


def _gpt_sovits_tts_failure_message(response: httpx.Response) -> str:
    """Make a sidecar 4xx actionable without exposing a raw traceback to chat."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = response.text
    if isinstance(payload, dict):
        detail = str(payload.get("Exception") or payload.get("message") or "")
    else:
        detail = str(payload or "")
    if "averaged_perceptron_tagger_eng" in detail:
        return (
            "GPT-SoVITS 英语前端缺少 NLTK 资源 averaged_perceptron_tagger_eng；"
            "请使用 GPT-SoVITS 的 Python 执行："
            "python -m nltk.downloader averaged_perceptron_tagger_eng"
        )
    compact_detail = " ".join(detail.split())[:500]
    suffix = f"：{compact_detail}" if compact_detail else ""
    return f"GPT-SoVITS /tts 返回 HTTP {response.status_code}{suffix}"


def _raise_for_gpt_sovits_tts_response(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(_gpt_sovits_tts_failure_message(response)) from exc


async def synthesize_voice(
    text: str,
    settings: Settings,
    profile_name: str | None = None,
    target_language: str | None = None,
    bot_self_id: str | None = None,
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
    selected_profile_id = profile_name or settings.voice_profile_default
    if selected_profile_id not in profiles:
        selected_profile_id = (
            settings.voice_profile_default
            if settings.voice_profile_default in profiles
            else next(iter(profiles))
        )
    if not voice_profile_authorized(settings, selected_profile_id, bot_self_id):
        raise RuntimeError("该角色音色仅获准在指定机器人账号使用，已回退文字回复")
    profile = profiles[selected_profile_id]
    if provider in {"gpt_sovits", "gpt-sovits"}:
        if not endpoint.endswith("/tts"):
            endpoint += "/tts"
        target_language = detect_speech_language(
            clean,
            target_language
            or str(profile.get("target_language") or "")
            or str(profile.get("language") or "")
            or "zh",
        )
        supported_languages = profile_supported_languages(profile)
        if target_language == "yue" and (
            supported_languages is None or target_language not in supported_languages
        ):
            language_label = _PROFILE_LANGUAGE_LABELS[target_language]
            raise RuntimeError(f"所选角色尚未验收 {language_label} 语音，已回退文字回复")
        if supported_languages is not None and target_language not in supported_languages:
            language_label = _PROFILE_LANGUAGE_LABELS.get(target_language, target_language)
            raise RuntimeError(
                f"所选角色尚未验收 {language_label} 语音，已回退文字回复"
            )
        reference = _project_relative_path(
            _profile_value_for_language(profile, "ref_audio_path", target_language)
        )
        if not Path(reference).is_file():
            raise RuntimeError("所选角色的语音暂不可用，请稍后再试。")
        prompt_text = _profile_value_for_language(
            profile, "prompt_text", target_language, allow_empty=True
        )
        prompt_lang = detect_speech_language(
            prompt_text,
            _profile_value_for_language(profile, "prompt_lang", target_language)
            or str(profile.get("language") or "")
            or "zh",
        )
        validate_voice_text_language(clean, target_language)
        selected_weight = selected_gpt_sovits_weight(
            settings, selected_profile_id, target_language
        )
        selected_gpt = selected_gpt_weight(settings, selected_profile_id)
        text_split_method = profile_text_split_method(profile)
        inference_overrides = profile_inference_overrides(profile)
        payload = {
            "text": clean,
            # GPT-SoVITS uses all_yue for speech spoken entirely in Cantonese;
            # plain yue denotes Cantonese-English mixed input in its front end.
            "text_lang": "all_yue" if target_language == "yue" else target_language,
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
        if text_split_method is not None:
            payload["text_split_method"] = text_split_method
        payload.update(inference_overrides)
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
        if provider in {"gpt_sovits", "gpt-sovits"}:
            # Keep model switching and its synthesis request indivisible.
            async with _gpt_sovits_lock(endpoint):
                if selected_gpt:
                    switch = await client.get(
                        endpoint.rsplit("/tts", 1)[0] + "/set_gpt_weights",
                        headers=headers,
                        params={"weights_path": selected_gpt},
                    )
                    switch.raise_for_status()
                # api_v2 exposes no stable process identity or active-weight
                # read endpoint. Reassert a configured route for every
                # synthesis: a restarted sidecar or external weight change
                # otherwise makes an in-process cache unsafe.
                if selected_weight:
                    switch = await client.get(
                        _gpt_sovits_control_endpoint(endpoint),
                        headers=headers,
                        params={"weights_path": selected_weight},
                    )
                    switch.raise_for_status()
                response = await client.post(endpoint, headers=headers, json=payload)
        else:
            response = await client.post(endpoint, headers=headers, json=payload)
        if provider in {"gpt_sovits", "gpt-sovits"}:
            _raise_for_gpt_sovits_tts_response(response)
        else:
            response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith(("audio/", "application/octet-stream")):
            raise RuntimeError("TTS 接口返回的内容不是音频")
        raw = response.content
    if not raw or len(raw) > settings.media_max_bytes:
        raise RuntimeError("TTS 返回为空或超过 MEDIA_MAX_BYTES")
    _reject_near_silent_wav(raw)
    return "base64://" + base64.b64encode(raw).decode("ascii")
