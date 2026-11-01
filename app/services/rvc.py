"""说话与翻唱共用的 RVC 模型配置和音频转换。"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.audio_chunks import AudioChunkError, locate_audio_tool, run_audio_command
from app.services.singing_gpu import async_gpu_lock
from app.services.singing_resources import singing_resource_problem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RVCProfile:
    root: Path
    python: Path
    model: Path
    index: Path | None
    index_rate: float
    speaker_id: int


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def configured_rvc_profile(settings: Settings, profile: dict[str, object]) -> RVCProfile:
    """只接受角色自己的模型和索引，不借用其他角色的权重。"""
    if not settings.singing_rvc_root:
        raise RuntimeError("RVC 环境尚未配置，请管理员检查。")
    root = _path(settings.singing_rvc_root)
    python = (
        _path(settings.singing_rvc_python) if settings.singing_rvc_python
        else root / ".venv" / "Scripts" / "python.exe"
    )
    if not python.is_file() or not (root / "infer" / "vc" / "modules.py").is_file():
        raise RuntimeError("RVC 环境暂不可用，请管理员检查。")
    model_value = str(profile.get("rvc_model_path") or "")
    if not model_value:
        raise RuntimeError("这个角色尚未配置 RVC 模型。")
    model = _path(model_value)
    if model.suffix.lower() != ".pth" or not model.is_file():
        raise RuntimeError("这个角色的 RVC 模型文件暂不可用。")
    index_value = str(profile.get("rvc_index_path") or "")
    index = _path(index_value) if index_value else None
    if index is not None and (index.suffix.lower() != ".index" or not index.is_file()):
        raise RuntimeError("这个角色配置的 RVC 索引文件暂不可用。")
    rate = profile.get("rvc_index_rate", 0.75 if index else 0.0)
    if type(rate) not in (int, float) or not math.isfinite(rate) or not 0 <= rate <= 1:
        raise RuntimeError("这个角色的 RVC 检索比例配置无效。")
    speaker = profile.get("rvc_speaker_id", 0)
    if type(speaker) is not int or not 0 <= speaker <= 109:
        raise RuntimeError("这个角色的 RVC 说话人编号配置无效。")
    return RVCProfile(root, python, model, index, float(rate) if index else 0.0, speaker)


async def convert_rvc_speech(raw: bytes, model: RVCProfile, settings: Settings) -> bytes:
    """排队使用显卡；取消、超时或完成后释放子进程和临时音频。"""
    ffmpeg = locate_audio_tool("ffmpeg", settings.singing_ffmpeg_path)
    temporary_root = PROJECT_ROOT / "data" / "voice" / "rvc"
    temporary_root.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    environment.update({key: "2" for key in (
        "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    )})
    try:
        async with asyncio.timeout(settings.voice_rvc_timeout_seconds):
            async with async_gpu_lock():
                issue = singing_resource_problem(PROJECT_ROOT, starting=True, stage="rvc_speech")
                if issue:
                    raise RuntimeError(issue.replace("翻唱", "语音转换"))
                with tempfile.TemporaryDirectory(prefix="speech-", dir=temporary_root) as name:
                    work = Path(name)
                    source, normalized = work / "source.wav", work / "normalized.wav"
                    source.write_bytes(raw)
                    await run_audio_command([
                        ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source,
                        "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", normalized,
                    ], env=environment, timeout_seconds=settings.voice_rvc_timeout_seconds)
                    output = work / "converted"
                    args = [
                        model.python, PROJECT_ROOT / "scripts" / "run_rvc_model.py",
                        "--rvc-root", model.root, "--model", model.model,
                        "--source", normalized, "--output", output,
                        "--index-rate", str(model.index_rate),
                        "--speaker-id", str(model.speaker_id),
                        "--seed", str(settings.voice_seed),
                    ]
                    if model.index:
                        args += ["--index", model.index]
                    await run_audio_command(
                        args, cwd=model.root, env=environment,
                        timeout_seconds=settings.voice_rvc_timeout_seconds,
                    )
                    converted, result = output / "rvc_vocals.wav", work / "speech.wav"
                    await run_audio_command([
                        ffmpeg, "-v", "error", "-nostdin", "-y", "-i", converted,
                        "-af", "alimiter=limit=0.98:latency=1:level=false",
                        "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", result,
                    ], env=environment, timeout_seconds=settings.voice_rvc_timeout_seconds)
                    if not result.is_file() or not 44 < result.stat().st_size <= settings.media_max_bytes:
                        raise RuntimeError("RVC 语音为空或超过发送大小限制，已回退文字。")
                    return result.read_bytes()
    except TimeoutError as exc:
        raise RuntimeError("RVC 语音转换等待或处理超时，已回退文字，请稍后再试。") from exc
    except AudioChunkError as exc:
        logger.warning("RVC speech conversion failed: %s", exc.diagnostic_tail)
        raise RuntimeError("RVC 语音转换失败，已回退文字，请稍后再试。") from exc
