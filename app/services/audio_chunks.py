"""准备时长合规且连续的 WAV 音频，用于 OneBot/QQ 发送。"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class AudioChunkError(RuntimeError):
    """音频处理失败，诊断尾日志只留在服务器。"""

    def __init__(self, message: str, *, diagnostic_tail: str = "") -> None:
        super().__init__(message)
        self.diagnostic_tail = diagnostic_tail


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    size_bytes: int


def _tool_path(name: str, explicit: str | Path | None, source: Path) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    executable = name + suffix
    if explicit is not None:
        requested = Path(explicit).expanduser()
        candidates = [requested]
        if not requested.is_absolute():
            candidates.append(Path(__file__).resolve().parents[2] / requested)
        for candidate in candidates:
            if candidate.is_dir():
                candidate /= executable
            if candidate.is_file():
                return str(candidate.resolve())
        found = shutil.which(str(explicit))
        if found:
            return found
        raise AudioChunkError(f"{name} executable was not found")

    found = shutil.which(name)
    if found:
        return found

    # 查找常见的便携 GPT-SoVITS 目录布局，也包括同级仓库。
    anchors = [source.parent, Path.cwd(), Path(__file__).resolve().parent]
    for anchor in list(anchors):
        anchors.extend(list(anchor.parents)[:3])
    seen: set[Path] = set()
    for anchor in anchors:
        if anchor in seen:
            continue
        seen.add(anchor)
        for folder in (
            anchor,
            anchor / "ffmpeg",
            anchor / "ffmpeg" / "bin",
            anchor / "GPT-SoVITS",
            anchor / "GPT-SoVITS" / "ffmpeg",
            anchor / "GPT-SoVITS" / "runtime" / "ffmpeg",
            anchor / "GPT-SoVITS" / "runtime" / "ffmpeg" / "bin",
        ):
            candidate = folder / executable
            if candidate.is_file():
                return str(candidate.resolve())
    raise AudioChunkError(f"{name} executable was not found")


def locate_audio_tool(
    name: str, configured: str | Path = "", source: str | Path | None = None
) -> Path:
    """从 PATH 或附近的便携 GPT-SoVITS 目录查找可执行文件。"""
    if not name or any(character in name for character in ("/", "\\", "..")):
        raise ValueError("name must be an executable basename")
    return Path(_tool_path(name, configured or None, Path(source or Path.cwd()).resolve()))


async def _stop_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), 5)
        except (OSError, TimeoutError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _run_process(
    args: Sequence[str | Path], timeout_seconds: float, cwd: str | Path | None = None
) -> bytes:
    if not args or timeout_seconds <= 0:
        raise ValueError("args must be nonempty and timeout_seconds positive")
    try:
        process = await asyncio.create_subprocess_exec(
            *(str(arg) for arg in args),
            cwd=str(cwd) if cwd is not None else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise AudioChunkError("Audio tool could not start") from exc

    async def consume_stdout() -> bytes:
        assert process.stdout is not None
        kept = bytearray()
        while data := await process.stdout.read(65536):
            if len(kept) < 8192:
                kept.extend(data[: 8192 - len(kept)])
        return bytes(kept)

    async def consume_stderr() -> bytes:
        assert process.stderr is not None
        tail = bytearray()
        while data := await process.stderr.read(65536):
            tail.extend(data)
            if len(tail) > 8192:
                del tail[:-8192]
        return bytes(tail)

    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(consume_stdout(), consume_stderr(), process.wait()), timeout_seconds
        )
    except (asyncio.CancelledError, TimeoutError):
        await _stop_process_tree(process)
        raise
    if process.returncode != 0:
        # 留一小段错误日志排查，别直接发到群里。
        prefix = f"exit_code={process.returncode}\n"
        raise AudioChunkError(
            "Audio tool failed to process the file",
            diagnostic_tail=prefix + stderr.decode("utf-8", errors="replace")[-(8192 - len(prefix)):],
        )
    return stdout


async def run_audio_command(
    args: Sequence[str | Path], *, cwd: str | Path | None = None, timeout_seconds: float = 120.0
) -> str:
    """安全运行本地音频命令，最多返回 8 KiB 标准输出。"""
    return (await _run_process(args, timeout_seconds, cwd)).decode("utf-8", errors="replace")


async def _probe_seconds(path: Path, ffprobe: str, timeout_seconds: float) -> float:
    raw = await _run_process(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout_seconds,
    )
    try:
        duration = float(raw.strip())
    except ValueError as exc:
        raise AudioChunkError("Audio duration could not be measured") from exc
    if not math.isfinite(duration) or duration < 0:
        raise AudioChunkError("Audio duration is invalid")
    return duration


def _boundary(
    audio: wave.Wave_read,
    start: int,
    limit: int,
    rate: int,
    preferred_boundaries: Sequence[float],
) -> int:
    """在时长上限附近挑选能量较低的 100 毫秒窗口，并保持采样帧完整。"""
    window = max(1, rate // 10)
    earliest = max(start + window, limit - 8 * rate)
    preferred = [
        round(second * rate)
        for second in preferred_boundaries
        if earliest <= round(second * rate) <= limit
    ]
    if preferred:
        return max(preferred)
    best_at = limit
    best_score = float("inf")
    for at in range(earliest, limit, window):
        audio.setpos(at)
        pcm = audio.readframes(min(window, limit - at))
        if not pcm:
            break
        # 不用第三方库读取 16 位小端 PCM。
        samples = memoryview(pcm).cast("h")
        energy = sum(abs(sample) for sample in samples) / len(samples)
        distance = (limit - at) / rate
        score = energy + distance * 12
        if score < best_score:
            best_score, best_at = score, at + len(samples) // 2
    return min(max(best_at, start + 1), limit)


def _write_chunks(
    normalized: Path,
    output_dir: Path,
    max_chunk_seconds: float,
    max_total_seconds: float,
    max_output_bytes: int,
    sample_rate: int,
    preferred_boundaries: Sequence[float],
) -> list[AudioChunk]:
    chunks: list[AudioChunk] = []
    made: list[Path] = []
    total_bytes = 0
    try:
        with wave.open(str(normalized), "rb") as audio:
            rate = audio.getframerate()
            frame_count = audio.getnframes()
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or rate != sample_rate:
                raise AudioChunkError("Normalized audio has an unexpected format")
            if frame_count == 0 or frame_count / rate > max_total_seconds + 1 / rate:
                raise AudioChunkError("Audio is empty or exceeds the total duration limit")

            hard_frames = math.floor(max_chunk_seconds * rate)
            if hard_frames < 1:
                raise ValueError("max_chunk_seconds is too small")
            start = 0
            stem = uuid.uuid4().hex
            while start < frame_count:
                limit = min(start + hard_frames, frame_count)
                end = (
                    limit if limit == frame_count
                    else _boundary(audio, start, limit, rate, preferred_boundaries)
                )
                if end <= start:
                    raise AudioChunkError("Could not locate a valid audio boundary")
                path = output_dir / f"{stem}_{len(chunks) + 1:03d}.wav"
                made.append(path)
                audio.setpos(start)
                with wave.open(str(path), "wb") as chunk:
                    chunk.setnchannels(1)
                    chunk.setsampwidth(2)
                    chunk.setframerate(rate)
                    remaining = end - start
                    while remaining:
                        count = min(remaining, rate)
                        data = audio.readframes(count)
                        if len(data) != count * 2:
                            raise AudioChunkError("Audio ended unexpectedly")
                        chunk.writeframesraw(data)
                        remaining -= count
                size = path.stat().st_size
                total_bytes += size
                if total_bytes > max_output_bytes:
                    raise AudioChunkError("Audio exceeds the output size limit")
                chunks.append(AudioChunk(path, start / rate, end / rate, (end - start) / rate, size))
                start = end
    except BaseException:
        for path in made:
            path.unlink(missing_ok=True)
        raise
    return chunks


async def split_audio_for_qq(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    ffmpeg_path: str | Path | None = None,
    ffprobe_path: str | Path | None = None,
    max_chunk_seconds: float = 55.0,
    max_total_seconds: float = 600.0,
    max_input_bytes: int = 100_000_000,
    max_output_bytes: int = 64_000_000,
    sample_rate: int = 24_000,
    timeout_seconds: float = 120.0,
    preferred_boundaries: Sequence[float] = (),
) -> list[AudioChunk]:
    """把 WAV/MP3 转为连续的单声道 WAV 片段，每段都短于两分钟。

    返回文件由调用方负责清理。出错或取消时会删除未完成文件。
    只在本地处理，不会把 shell 命令或输出展示给用户。
    """
    source = Path(input_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".wav", ".mp3"}:
        raise AudioChunkError("Input must be an existing WAV or MP3 file")
    if source.stat().st_size > max_input_bytes:
        raise AudioChunkError("Audio exceeds the input size limit")
    if not (0 < max_chunk_seconds <= 115 and max_chunk_seconds <= max_total_seconds):
        raise ValueError("max_chunk_seconds must be between 0 and 115 seconds")
    if sample_rate not in {24_000, 32_000}:
        raise ValueError("sample_rate must be 24000 or 32000")
    if min(max_input_bytes, max_output_bytes) <= 0 or timeout_seconds <= 0:
        raise ValueError("Byte limits and timeout must be positive")
    if any(not math.isfinite(second) or second < 0 for second in preferred_boundaries):
        raise ValueError("preferred_boundaries must contain nonnegative finite seconds")
    ffmpeg = _tool_path("ffmpeg", ffmpeg_path, source)
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    ffprobe = (
        str(sibling) if ffprobe_path is None and sibling.is_file()
        else _tool_path("ffprobe", ffprobe_path, source)
    )

    probed = await _probe_seconds(source, ffprobe, timeout_seconds)
    if probed > max_total_seconds + 0.01:
        raise AudioChunkError("Audio exceeds the total duration limit")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="audio_chunks_", dir=target) as temp:
        normalized = Path(temp) / "normalized.wav"
        await _run_process(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(source), "-vn", "-map", "0:a:0", "-ac", "1",
                "-ar", str(sample_rate), "-c:a", "pcm_s16le",
                "-t", str(max_total_seconds + 1), str(normalized),
            ],
            timeout_seconds,
        )
        chunks = _write_chunks(
            normalized, target, max_chunk_seconds, max_total_seconds, max_output_bytes,
            sample_rate, preferred_boundaries,
        )
    try:
        for chunk in chunks:
            measured = await _probe_seconds(chunk.path, ffprobe, timeout_seconds)
            if measured > max_chunk_seconds + 0.0001:
                raise AudioChunkError("An audio chunk exceeds its duration limit")
    except BaseException:
        for chunk in chunks:
            chunk.path.unlink(missing_ok=True)
        raise
    return chunks
