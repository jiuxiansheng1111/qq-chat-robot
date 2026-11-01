"""Optional local Minecraft speech: configured TTS, then existing RVC when needed.

RVC changes an existing recording's voice; it cannot turn text into speech by itself.
No model is downloaded and no QQ message is sent by this module.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

from app.config import Settings
from app.services.voice import (
    _profile_value_for_language,
    _project_relative_path,
    _reject_near_silent_wav,
    detect_speech_language,
    profile_inference_overrides,
    profile_supported_languages,
    profile_text_split_method,
    selected_gpt_sovits_weight,
    selected_gpt_weight,
    synthesize_voice,
    validate_voice_text_language,
    voice_profile_authorized,
    voice_profiles,
)

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 90
CHARACTER_PROFILES = {
    "tomori": "mygo_tomori", "anon": "mygo_anon", "rana": "mygo_rana",
    "soyo": "mygo_soyo", "taki": "mygo_taki", "sakiko": "mujica_sakiko",
    "uika": "mujica_uika", "umiri": "mujica_umiri",
}


class VoiceUnavailable(RuntimeError):
    """Safe user-facing speech failure, without provider details or configuration."""


def _tts_job(text: str, settings: Settings, profile_id: str, runtime) -> dict:
    """Reuse the existing profile validators and weight routers; serialize no credentials."""
    profile = voice_profiles(settings)[profile_id]
    language = detect_speech_language(text, str(profile.get("target_language") or profile.get("language") or "zh"))
    supported = profile_supported_languages(profile)
    if supported is not None and language not in supported:
        raise VoiceUnavailable("当前基础音色尚未验收此语言，文字回复已保留。")
    if language == "yue" and (supported is None or language not in supported):
        raise VoiceUnavailable("当前基础音色尚未验收粤语，文字回复已保留。")
    reference = _project_relative_path(_profile_value_for_language(profile, "ref_audio_path", language))
    if not Path(reference).is_file():
        raise VoiceUnavailable("已有基础音色参考录音不可用，文字回复已保留。")
    prompt = _profile_value_for_language(profile, "prompt_text", language, allow_empty=True)
    prompt_language = detect_speech_language(prompt, _profile_value_for_language(profile, "prompt_lang", language)
                                            or str(profile.get("language") or "zh"))
    validate_voice_text_language(text, language)
    payload = {"text": text, "text_lang": "all_yue" if language == "yue" else language,
               "ref_audio_path": reference, "prompt_text": prompt, "prompt_lang": prompt_language,
               "seed": int(settings.voice_seed), "top_k": max(1, int(settings.voice_top_k)),
               "temperature": max(0.1, float(settings.voice_temperature)),
               "repetition_penalty": max(1.0, float(settings.voice_repetition_penalty)),
               "streaming_mode": False, "media_type": "wav"}
    method = profile_text_split_method(profile)
    if method:
        payload["text_split_method"] = method
    payload.update(profile_inference_overrides(profile))
    return {"gpt_root": str(runtime.root), "config": str(runtime.config), "payload": payload,
            "gpt_weights": selected_gpt_weight(settings, profile_id),
            "sovits_weights": selected_gpt_sovits_weight(settings, profile_id, language)}


def validate_pcm_wav(raw: bytes) -> float:
    """The game accepts bounded, uncompressed 16-bit mono/stereo WAV only."""
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise VoiceUnavailable("语音为空或超过 8 MiB，文字回复已保留。")
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            channels, width, rate, frames = (
                audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            )
            if (audio.getcomptype() != "NONE" or channels not in (1, 2) or width != 2
                    or not 8000 <= rate <= 48000 or not 0 < frames <= rate * MAX_AUDIO_SECONDS):
                raise VoiceUnavailable("语音格式不受支持，需要 90 秒内的 16 位 PCM WAV。")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise VoiceUnavailable("语音数据不完整，文字回复已保留。")
            return frames / rate
    except (EOFError, wave.Error) as exc:
        raise VoiceUnavailable("语音接口未返回 PCM WAV，文字回复已保留。") from exc


def _cached_audio(cache, key: str) -> bytes | None:
    raw = cache.read(key)
    if raw is not None:
        try:
            validate_pcm_wav(raw)
        except VoiceUnavailable:
            cache.remove(key)
            return None
    return raw


async def voice_capabilities(settings: Settings | None) -> dict:
    """Only public capability flags; never serialize Settings or file paths."""
    if settings is None:
        return {"configured": False, "tts_tcp_ready": False, "status": "语音配置不可用；文字聊天可用。"}
    profiles = voice_profiles(settings)
    base = profiles.get(settings.voice_profile_default, {})
    from app.minecraft_voice_runtime import tts_runtime

    try:
        tts_runtime(Path(__file__).resolve().parents[1])
        worker_ready = True
    except (OSError, ValueError, RuntimeError):
        worker_ready = False
    try:
        endpoint = urlsplit(settings.voice_api_url)
        endpoint_port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    except ValueError:
        return {"configured": False, "tts_tcp_ready": False, "status": "语音地址配置无效；文字聊天可用。"}
    local = endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
    listening = False
    if local:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(
                endpoint.hostname, endpoint_port,
            ), timeout=0.5)
            writer.close()
            await writer.wait_closed()
            listening = True
        except (OSError, ValueError, TimeoutError):
            pass
    model_files = {}
    from app.services.rvc_singing import rvc_profile

    for character, profile in CHARACTER_PROFILES.items():
        try:
            rvc = rvc_profile(settings, profile)
            model_files[character] = rvc is not None
        except RuntimeError:
            model_files[character] = False
    return {
        "configured": bool(settings.voice_api_url and base),
        "runtime_mode": "sequential_one_shot_workers",
        "tts_worker_runtime_ready": worker_ready,
        "requires_persistent_tts": False,
        "audio_cache_limit_bytes": 8 * 1024 * 1024,
        "qq_voice_enabled": settings.voice_enabled,
        "game_voice": "explicit_client_opt_in",
        "tts_endpoint_local": local,
        "tts_tcp_ready": listening if local else None,
        "inference_verified": False,
        "status": "端口、模型存在只作诊断；实际合成和音色以请求结果为准。",
        "max_chars": min(360, max(20, int(settings.voice_max_chars))),
        "characters": [
            {"id": character, "configured": profile in profiles,
             "route": "tts" if profiles.get(profile, {}).get("tts_enabled") is not False
             and profile in profiles else "tts_then_rvc",
             "rvc_files_present": model_files[character],
             "base_tts_configured": bool(base) and base.get("tts_enabled") is not False}
            for character, profile in CHARACTER_PROFILES.items()
        ],
    }


async def synthesize_minecraft_voice(
    character: str, text: str, settings: Settings, project_root: Path,
) -> tuple[bytes, str]:
    # This request is sent only after the player's optional game switch is enabled.
    # Keep QQ's global voice setting unchanged; reuse a per-request in-memory copy.
    settings = settings.model_copy(update={"voice_enabled": True})
    text = " ".join(text.split())
    if not text or len(text) > min(360, max(20, int(settings.voice_max_chars))):
        raise VoiceUnavailable("本条回复超过语音长度上限，已保留完整文字。")
    profile_id = CHARACTER_PROFILES[character]
    profiles = voice_profiles(settings)
    target = profiles.get(profile_id)
    if target is None:
        raise VoiceUnavailable("此角色尚未配置语音，文字回复已保留。")
    # Minecraft is not the authorized OneBot account. Restricted profiles stay restricted.
    if not voice_profile_authorized(settings, profile_id, None):
        raise VoiceUnavailable("此角色音色仅授权指定 QQ 账号，不能用于游戏语音。")
    convert = target.get("tts_enabled") is False
    rvc = None
    if convert:
        from app.services.rvc_singing import rvc_profile

        try:
            rvc = rvc_profile(settings, profile_id)
        except RuntimeError as exc:
            raise VoiceUnavailable("角色 RVC 模型或运行环境未就绪，文字回复已保留。") from exc
        if rvc is None:
            raise VoiceUnavailable("此角色没有文字朗读音色或 RVC 模型。")
        base_id = settings.voice_profile_default
        base = profiles.get(base_id)
        if not base or base.get("tts_enabled") is False:
            raise VoiceUnavailable("RVC 不能直接读文字；请在 ChatRobot 配置可用的默认 TTS 音色。")
        if not voice_profile_authorized(settings, base_id, None):
            raise VoiceUnavailable("默认 TTS 音色未授权用于游戏，文字回复已保留。")
    else:
        base_id = profile_id
    from app.minecraft_voice_runtime import (
        AudioCache,
        WorkerFailure,
        cache_key,
        file_signature,
        run_worker,
        save_audit,
        tts_runtime,
    )
    from app.services.audio_chunks import locate_audio_tool
    from app.services.singing_gpu import async_gpu_lock

    started = time.monotonic()
    audit = {"character": character, "runtime_mode": "sequential_one_shot_workers",
             "tts_cache_hit": False, "final_cache_hit": False, "llm_called": False,
             "qq_settings_modified": False, "status": "started"}
    try:
        provider = str(settings.voice_provider).casefold()
        local_gpt = provider in {"gpt_sovits", "gpt-sovits"} and urlsplit(settings.voice_api_url).hostname in {"localhost", "127.0.0.1", "::1"}
        runtime = tts_runtime(project_root) if local_gpt else None
        job = _tts_job(text, settings, base_id, runtime) if runtime else None
        cache = AudioCache(project_root)
        base_key = cache_key({"version": 2, "base_profile": base_id, "job": job, "text": text,
                              "profile": profiles[base_id], "provider": provider,
                              "voice_name": settings.voice_name, "voice_model": settings.voice_model,
                              "config": file_signature(runtime.config) if runtime else None,
                              "python": file_signature(runtime.python) if runtime else None,
                              "worker": file_signature(project_root / "scripts" / "minecraft_tts_worker.py") if runtime else None,
                              "gpt": file_signature(job["gpt_weights"]) if job else None,
                              "sovits": file_signature(job["sovits_weights"]) if job else None,
                              "reference": file_signature(job["payload"]["ref_audio_path"]) if job else None})
        final_key = cache_key({"base": base_key, "profile": profile_id, "seed": settings.singing_seed,
                               "worker": file_signature(project_root / "scripts" / "run_rvc_model.py") if rvc else None,
                               "model": file_signature(rvc.model) if rvc else None,
                               "index": file_signature(rvc.index) if rvc else None,
                               "index_rate": rvc.index_rate if rvc else None,
                               "speaker": rvc.speaker_id if rvc else None})
        raw = _cached_audio(cache, final_key)
        if raw is not None:
            validate_pcm_wav(raw)
            audit.update({"final_cache_hit": True, "status": "generated", "bytes": len(raw)})
            return raw, "tts_then_rvc" if convert else "tts"
        ffmpeg = locate_audio_tool("ffmpeg", settings.singing_ffmpeg_path, project_root)
        # TemporaryDirectory owns only this request's generated audio, never player data.
        with tempfile.TemporaryDirectory(prefix="ic2newera-voice-", ignore_cleanup_errors=True) as directory:
            work = Path(directory)
            source = work / "tts.wav"
            # No TTS and RVC model coexist: wait for one worker's full exit before the next.
            # The existing cross-process lock also serializes training/singing jobs.
            async with async_gpu_lock():
                raw = _cached_audio(cache, base_key) if runtime else None
                audit["tts_cache_hit"] = raw is not None
                if raw is None and runtime:
                    request = work / "tts-request.json"
                    request.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
                    await run_worker([runtime.python, project_root / "scripts" / "minecraft_tts_worker.py",
                                      "--request", request, "--output", source],
                                     cwd=runtime.root, project_root=project_root, stage="minecraft_tts_worker",
                                     timeout_seconds=110, audit=audit)
                    if source.stat().st_size > MAX_AUDIO_BYTES:
                        raise VoiceUnavailable("基础语音超过大小上限，文字回复已保留。")
                    raw = source.read_bytes()
                    validate_pcm_wav(raw)
                    _reject_near_silent_wav(raw)
                    cache.write(base_key, raw)
                elif raw is None:
                    # Existing remote/non-GPT providers stay available without changing QQ.
                    generated = await synthesize_voice(text, settings, profile_name=base_id)
                    if not generated.startswith("base64://"):
                        raise VoiceUnavailable("语音服务返回方式不受支持。")
                    raw = base64.b64decode(generated[9:], validate=True)
                validate_pcm_wav(raw)
                if not convert:
                    cache.write(final_key, raw)
                    audit.update({"status": "generated", "bytes": len(raw)})
                    return raw, "tts"
                assert rvc is not None
                source.write_bytes(raw)
                args = [str(rvc.python), str(project_root / "scripts" / "run_rvc_model.py"),
                        "--rvc-root", str(rvc.root), "--model", str(rvc.model),
                        "--source", str(source), "--output", str(work),
                        "--index-rate", str(rvc.index_rate), "--speaker-id", str(rvc.speaker_id),
                        "--seed", str(settings.singing_seed)]
                if rvc.index:
                    args.extend(["--index", str(rvc.index)])
                await run_worker(args, cwd=rvc.root, project_root=project_root,
                                 stage="minecraft_rvc_worker", timeout_seconds=100, audit=audit)
                output = work / "speech.wav"
                await run_worker([
                    ffmpeg, "-v", "error", "-nostdin", "-y", "-i", work / "rvc_vocals.wav",
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", output,
                ], cwd=work, project_root=project_root, stage="minecraft_voice_pcm",
                    timeout_seconds=15, audit=audit, model_loading=False)
                if output.stat().st_size > MAX_AUDIO_BYTES:
                    raise VoiceUnavailable("角色音频超过大小上限，文字回复已保留。")
                raw = output.read_bytes()
        validate_pcm_wav(raw)
        _reject_near_silent_wav(raw)
        cache.write(final_key, raw)
        audit.update({"status": "generated", "bytes": len(raw), "duration_seconds": validate_pcm_wav(raw)})
        return raw, "tts_then_rvc"
    except asyncio.CancelledError:
        audit["status"] = "cancelled"
        raise
    except VoiceUnavailable:
        audit["status"] = "unavailable"
        raise
    except WorkerFailure as exc:
        audit["status"] = "unavailable"
        raise VoiceUnavailable(str(exc)) from exc
    except TimeoutError as exc:
        audit["status"] = "timeout"
        raise VoiceUnavailable("语音工作进程超时，文字回复已保留。") from exc
    except Exception as exc:
        audit["status"] = "unavailable"
        raise VoiceUnavailable("角色语音未完成，请检查已有模型/运行环境；文字回复已保留。") from exc
    finally:
        audit["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_audit(project_root, audit)
