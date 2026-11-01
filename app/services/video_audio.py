"""为真实视频附件提取内嵌字幕或语音转写，不把画面推断冒充听辨。"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from app.config import Settings
from app.services.audio_chunks import AudioChunkError, locate_audio_tool
from app.services.http_routing import outbound_http_client

logger = logging.getLogger(__name__)
MAX_VIDEO_BYTES = 100 * 1024 * 1024
# 应用侧处理预算；Groq 附件限制为25MB，智谱单次限制为30秒。
# https://console.groq.com/docs/speech-to-text
# https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512
MAX_AUDIO_SECONDS = 300
MAX_TRANSCRIPT_CHARS = 14_000
MAX_WAV_BYTES = 10 * 1024 * 1024
_SAMPLE_RATE = 16_000
_ZHIPU_SEGMENT_SECONDS = 28
_ZHIPU_MAX_SEGMENTS = 6
_FORMATS = "mov,matroska,webm,avi,flv,mpegts,mpeg,ogg"
_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
_slots = asyncio.Semaphore(2)
_TIMESTAMP = re.compile(r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass(frozen=True)
class VideoAudioResult:
    """将来源、处理范围和失败状态与转写正文一并交给视频摘要。"""

    transcript: str = ""
    status: Literal[
        "ok", "partial", "no_audio", "no_speech", "unavailable", "invalid", "failed"
    ] = "unavailable"
    source: Literal["embedded_subtitles", "groq_asr", "zhipu_asr", "none"] = "none"
    detail: str = "尚未获得视频语音或字幕内容"
    duration_seconds: float = 0.0
    truncated: bool = False
    processed_seconds: float = 0.0


@dataclass(frozen=True)
class _ASRProvider:
    name: Literal["groq", "zhipu"]
    endpoint: str
    model: str
    key: str = field(repr=False)


class _VideoAudioError(RuntimeError):
    """视频音轨处理未能完成，错误文字不包含凭据或上游正文。"""


def _providers(
    settings: Settings | None, config: Mapping[str, object] | None, *, allow_paid: bool = False
) -> list[_ASRProvider]:
    keys: dict[str, str] = {}
    sources = config.get("provider_sources", []) if config is not None else []
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, Mapping) or source.get("enable") is False:
                continue
            provider_id = source.get("id")
            base = str(source.get("api_base", "")).rstrip("/")
            expected = {
                "qqchat-groq": ("groq", "https://api.groq.com/openai/v1"),
                "qqchat-zhipu": ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
            }.get(str(provider_id))
            if not expected or base != expected[1]:
                continue
            if expected[0] == "zhipu" and allow_paid is not True:
                continue
            value = source.get("key", source.get("api_key", ""))
            values = value if isinstance(value, list) else [value]
            key = next(
                (item.strip() for item in values if isinstance(item, str) and item.strip()), ""
            )
            if key:
                keys[expected[0]] = key
    if settings is not None:
        if (
            settings.groq_api_key
            and settings.groq_base_url.rstrip("/") == "https://api.groq.com/openai/v1"
        ):
            keys.setdefault("groq", settings.groq_api_key)
        if (
            allow_paid is True
            and settings.llm_api_key
            and settings.llm_base_url.rstrip("/") == "https://open.bigmodel.cn/api/paas/v4"
        ):
            keys.setdefault("zhipu", settings.llm_api_key)
    return [
        _ASRProvider(name, endpoint, model, keys[name])
        for name, endpoint, model in (
            (
                "groq",
                "https://api.groq.com/openai/v1/audio/transcriptions",
                "whisper-large-v3-turbo",
            ),
            ("zhipu", "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions", "glm-asr-2512"),
        )
        if name in keys
    ]


def _trusted_video(path: Path, trusted_root: Path | None) -> Path:
    if trusted_root is None or not isinstance(path, Path):
        raise _VideoAudioError("仅接受已由真实视频附件校验的本地文件")
    if path.is_symlink():
        raise _VideoAudioError("视频附件不能是文件链接")
    resolved = path.resolve(strict=True)
    root = trusted_root.resolve(strict=True)
    if not root.is_dir() or not resolved.is_relative_to(root) or not resolved.is_file():
        raise _VideoAudioError("视频附件不在已校验下载目录内")
    if not 0 < resolved.stat().st_size <= MAX_VIDEO_BYTES:
        raise _VideoAudioError("视频附件为空或超过100MiB处理上限")
    return resolved


async def _command(
    args: Sequence[str | Path], *, timeout: float = 15, max_output: int = 262_144
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *(str(value) for value in args),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    async def read(stream: asyncio.StreamReader | None, budget: int) -> bytes:
        if stream is None:
            return b""
        kept = bytearray()
        while block := await stream.read(65536):
            kept.extend(block[: max(0, budget - len(kept))])
        return bytes(kept)

    try:
        stdout, _, _ = await asyncio.wait_for(
            asyncio.gather(
                read(process.stdout, max_output), read(process.stderr, 4096), process.wait()
            ),
            timeout,
        )
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        raise _VideoAudioError("本地视频音轨处理未完成")
    return stdout


def _input_options(path: Path) -> list[str | Path]:
    # 拒绝网络协议和播放列表/外部引用格式，始终把附件当作本地媒体容器。
    return ["-protocol_whitelist", "file,pipe", "-format_whitelist", _FORMATS, "-i", path]


def _seconds(value: object) -> float:
    try:
        number = float(str(value))
    except ValueError:
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _label(seconds: float) -> str:
    minutes, second = divmod(int(seconds), 60)
    return f"{minutes:02}:{second:02}"


def _subtitle_text(raw: str) -> tuple[str, bool]:
    lines = []
    used = 0
    clipped = False
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        parts = block.splitlines()
        position = next((index for index, line in enumerate(parts) if "-->" in line), -1)
        if position < 0:
            continue
        stamps = _TIMESTAMP.findall(parts[position])
        if not stamps:
            continue
        start = sum(int(value) * scale for value, scale in zip(stamps[0][:3], (3600, 60, 1)))
        if start >= MAX_AUDIO_SECONDS:
            clipped = True
            continue
        text = html.unescape(
            re.sub(r"<[^>]+>|\{\\[^}]+\}", "", " ".join(parts[position + 1 :]))
        ).strip()
        if not text:
            continue
        line = f"[{_label(start)}] {text}"
        if used + len(line) > MAX_TRANSCRIPT_CHARS:
            clipped = True
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines), clipped


def _wave_payload(path: Path) -> tuple[bytes, float, bool]:
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != _SAMPLE_RATE
        ):
            raise _VideoAudioError("音轨转换格式无效")
        frames = audio.readframes(_SAMPLE_RATE * MAX_AUDIO_SECONDS)
    import numpy as np

    samples = np.frombuffer(frames, dtype="<i2")
    silent = not len(samples) or float(np.max(np.abs(samples.astype(np.int32)))) < 32
    return frames, len(frames) / (_SAMPLE_RATE * 2), silent


def _wav(frames: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(_SAMPLE_RATE)
        audio.writeframes(frames)
    return output.getvalue()


async def _request_asr(
    provider: _ASRProvider, audio: bytes, settings: Settings | None
) -> Mapping[str, object]:
    if len(audio) > MAX_WAV_BYTES:
        raise _VideoAudioError("转写音轨超过上传预算")
    data = {"model": provider.model}
    if provider.name == "groq":
        data.update({"response_format": "verbose_json", "temperature": "0"})
    else:
        data["stream"] = "false"
    async with (
        outbound_http_client(settings=settings, timeout=30, follow_redirects=False) as client,
        client.stream(
            "POST",
            provider.endpoint,
            headers={"Authorization": f"Bearer {provider.key}"},
            files={"file": ("audio.wav", audio, "audio/wav")},
            data=data,
        ) as response,
    ):
        if response.status_code != 200:
            raise _VideoAudioError(f"语音识别返回 HTTP {response.status_code}")
        payload = bytearray()
        async for block in response.aiter_bytes():
            payload.extend(block)
            if len(payload) > 512 * 1024:
                raise _VideoAudioError("语音识别响应超过预算")
    result = json.loads(payload)
    if not isinstance(result, Mapping):
        raise _VideoAudioError("语音识别响应无效")
    return result


def _groq_text(payload: Mapping[str, object]) -> str:
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        texts = []
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            # Whisper 的静音置信度可排除常见的静音幻听。
            if _seconds(segment.get("no_speech_prob")) >= 0.8:
                continue
            text = segment.get("text")
            if isinstance(text, str) and text.strip():
                text = text.strip()
                texts.append(f"[{_label(_seconds(segment.get('start')))}] {text}")
        return "\n".join(texts)
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


async def _transcribe(
    provider: _ASRProvider,
    frames: bytes,
    duration: float,
    original_duration: float,
    settings: Settings | None,
) -> VideoAudioResult:
    if provider.name == "groq":
        text = _groq_text(await _request_asr(provider, _wav(frames), settings))
        clipped = (
            len(text) > MAX_TRANSCRIPT_CHARS
            or not original_duration
            or original_duration > duration + 0.5
        )
        return VideoAudioResult(
            transcript=text[:MAX_TRANSCRIPT_CHARS],
            status="partial" if clipped else "ok",
            source="groq_asr",
            detail=f"自动语音转写，仅覆盖视频前 {_label(duration)}",
            duration_seconds=original_duration,
            truncated=clipped,
            processed_seconds=duration,
        )
    chunk_size = _SAMPLE_RATE * 2 * _ZHIPU_SEGMENT_SECONDS
    chunks = [frames[index : index + chunk_size] for index in range(0, len(frames), chunk_size)][
        :_ZHIPU_MAX_SEGMENTS
    ]
    parallel = asyncio.Semaphore(2)

    async def one(index: int, chunk: bytes) -> tuple[str, float, bool]:
        try:
            async with parallel:
                result = await _request_asr(provider, _wav(chunk), settings)
            text = result.get("text")
            if isinstance(text, str) and text.strip():
                return (
                    f"[{_label(index * _ZHIPU_SEGMENT_SECONDS)}] {text.strip()}",
                    len(chunk) / (_SAMPLE_RATE * 2),
                    True,
                )
            return "", 0.0, True
        except (httpx.HTTPError, _VideoAudioError, ValueError):
            pass
        return "", 0.0, False

    results = await asyncio.gather(*(one(index, chunk) for index, chunk in enumerate(chunks)))
    if not any(item[2] for item in results):
        raise _VideoAudioError("分段语音识别未完成")
    text = "\n".join(item[0] for item in results if item[0])
    covered = sum(item[1] for item in results)
    clipped = (
        len(text) > MAX_TRANSCRIPT_CHARS
        or not original_duration
        or covered < original_duration - 0.5
    )
    return VideoAudioResult(
        transcript=text[:MAX_TRANSCRIPT_CHARS],
        status="partial" if clipped else "ok",
        source="zhipu_asr",
        detail=f"分段语音转写，成功识别 {_label(covered)}；最多处理前02:48，时间标签标明各片段起点",
        duration_seconds=original_duration,
        truncated=clipped,
        processed_seconds=covered,
    )


async def transcribe_video_audio(
    video_path: Path,
    settings: Settings | None = None,
    *,
    trusted_root: Path | None = None,
    provider_config: Mapping[str, object] | None = None,
    allow_paid: bool = False,
) -> VideoAudioResult:
    """提取真实附件中的文字字幕，或有限范围内的第一条音轨。

    Args:
        video_path: 真实 QQ 视频附件已下载并校验的路径，禁止来自消息文本。
        settings: 本机工具路径和已有模型凭据，可为空。
        trusted_root: 调用者已核验的附件下载目录；为空时拒绝读取。
        provider_config: AstrBot 当前会话配置，只在内存复用官方来源密钥。
        allow_paid: 默认关闭收费 ASR；只有明确为 True 时才允许智谱收费后备。

    Returns:
        含转写来源、已处理时长、截断标记和失败边界的结构化结果。
    """
    try:
        video = _trusted_video(video_path, trusted_root)
    except (OSError, _VideoAudioError):
        return VideoAudioResult(status="invalid", detail="视频不是已核验目录内的有效附件")
    try:
        bundled_tools = Path(__file__).resolve().parents[2] / "data/singing/runtime/bin"
        tool_suffix = ".exe" if os.name == "nt" else ""
        ffmpeg_setting = (
            settings.singing_ffmpeg_path if settings else bundled_tools / ("ffmpeg" + tool_suffix)
        )
        ffprobe_setting = (
            settings.singing_ffprobe_path if settings else bundled_tools / ("ffprobe" + tool_suffix)
        )
        ffmpeg = locate_audio_tool(
            "ffmpeg", ffmpeg_setting if Path(ffmpeg_setting).is_file() or settings else "", video
        )
        ffprobe = locate_audio_tool(
            "ffprobe", ffprobe_setting if Path(ffprobe_setting).is_file() or settings else "", video
        )
    except AudioChunkError:
        return VideoAudioResult(detail="本机缺少视频音轨处理工具；没有取得语音或字幕")
    duration = 0.0
    try:
        async with asyncio.timeout(80), _slots:
            probe = json.loads(
                await _command(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-format_whitelist",
                        _FORMATS,
                        "-show_entries",
                        "format=duration:stream=index,codec_type,codec_name:stream_tags=language",
                        "-of",
                        "json",
                        video,
                    ]
                )
            )
            streams = probe.get("streams", [])
            if not isinstance(streams, list) or not any(
                stream.get("codec_type") == "video"
                for stream in streams
                if isinstance(stream, dict)
            ):
                return VideoAudioResult(status="invalid", detail="附件不是受支持的视频媒体容器")
            duration = _seconds(probe.get("format", {}).get("duration"))
            subtitles = [
                stream
                for stream in streams
                if isinstance(stream, dict)
                and stream.get("codec_type") == "subtitle"
                and stream.get("codec_name") in _SUBTITLE_CODECS
            ]
            subtitles.sort(
                key=lambda item: (
                    str(item.get("tags", {}).get("language", "")) not in {"chi", "zho", "zh"}
                )
            )
            for stream in subtitles[:2]:
                try:
                    raw = await _command(
                        [
                            ffmpeg,
                            "-v",
                            "error",
                            "-nostdin",
                            "-threads",
                            "2",
                            *_input_options(video),
                            "-t",
                            str(MAX_AUDIO_SECONDS),
                            "-map",
                            f"0:{int(stream['index'])}",
                            "-f",
                            "srt",
                            "pipe:1",
                        ]
                    )
                    text, clipped = _subtitle_text(raw.decode("utf-8", errors="replace"))
                    if text:
                        clipped = clipped or duration > MAX_AUDIO_SECONDS or not duration
                        return VideoAudioResult(
                            transcript=text,
                            status="partial" if clipped else "ok",
                            source="embedded_subtitles",
                            detail="取自视频内嵌文字字幕；尚未对照音轨逐句核验",
                            duration_seconds=duration,
                            truncated=clipped,
                            processed_seconds=min(duration or MAX_AUDIO_SECONDS, MAX_AUDIO_SECONDS),
                        )
                except (_VideoAudioError, ValueError, TimeoutError):
                    continue
            if not any(
                stream.get("codec_type") == "audio"
                for stream in streams
                if isinstance(stream, dict)
            ):
                return VideoAudioResult(
                    status="no_audio",
                    detail="视频没有可提取音轨或文字字幕",
                    duration_seconds=duration,
                )
            providers = _providers(settings, provider_config, allow_paid=allow_paid)
            if not providers:
                return VideoAudioResult(
                    detail=(
                        "没有可用的语音识别服务；未听辨音轨"
                        if allow_paid is True
                        else "免费语音识别服务暂不可用，收费后备已关闭；未听辨音轨"
                    ),
                    duration_seconds=duration,
                )
            with tempfile.TemporaryDirectory(prefix="qqchat-video-audio-") as folder:
                audio_path = Path(folder) / "audio.wav"
                await _command(
                    [
                        ffmpeg,
                        "-v",
                        "error",
                        "-nostdin",
                        "-threads",
                        "2",
                        *_input_options(video),
                        "-t",
                        str(MAX_AUDIO_SECONDS),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-sn",
                        "-dn",
                        "-ac",
                        "1",
                        "-ar",
                        str(_SAMPLE_RATE),
                        "-c:a",
                        "pcm_s16le",
                        "-fs",
                        str(MAX_WAV_BYTES),
                        "-y",
                        audio_path,
                    ],
                    timeout=20,
                )
                frames, processed, silent = await asyncio.to_thread(_wave_payload, audio_path)
            if silent:
                return VideoAudioResult(
                    status="no_speech",
                    detail="音轨为静音或音量过低，没有可靠语音文字",
                    duration_seconds=duration,
                )
            for provider in providers:
                try:
                    result = await _transcribe(provider, frames, processed, duration, settings)
                    if result.transcript:
                        return result
                    return VideoAudioResult(
                        status="no_speech",
                        source=result.source,
                        detail="语音识别没有返回可靠文字；不能据此描述口播内容",
                        duration_seconds=duration,
                        processed_seconds=result.processed_seconds,
                        truncated=result.truncated,
                    )
                except (httpx.HTTPError, _VideoAudioError, ValueError) as exc:
                    logger.warning("视频语音转写未完成：%s/%s", provider.name, type(exc).__name__)
            return VideoAudioResult(
                status="failed",
                detail="现有语音识别服务未完成；未取得音轨文字",
                duration_seconds=duration,
            )
    except (OSError, _VideoAudioError, ValueError, TimeoutError, wave.Error) as exc:
        logger.warning("视频音轨处理未完成：%s", type(exc).__name__)
        return VideoAudioResult(
            status="failed",
            detail="字幕或音轨处理未完成；不能据此描述口播内容",
            duration_seconds=duration,
        )
