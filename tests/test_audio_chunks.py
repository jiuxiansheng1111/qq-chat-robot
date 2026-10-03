from __future__ import annotations

import array
import asyncio
import os
import sys
import time
import wave
from itertools import pairwise
from pathlib import Path

import pytest

from app.services.audio_chunks import (
    AudioChunkError,
    locate_audio_tool,
    run_audio_command,
    split_audio_for_qq,
)


def _ffmpeg_pair():
    try:
        return (
            locate_audio_tool("ffmpeg", os.environ.get("FFMPEG_PATH", "")),
            locate_audio_tool("ffprobe", os.environ.get("FFPROBE_PATH", "")),
        )
    except AudioChunkError:
        pytest.skip("ffmpeg and ffprobe are unavailable")


def _long_wav(path, seconds: int = 122, rate: int = 24_000) -> bytes:
    # Distinct second markers make gaps or repeated content visible after splitting.
    blocks = []
    for second in range(seconds):
        amplitude = 0 if second % 10 == 9 else 500 + second
        samples = array.array("h", (amplitude if i % 2 else -amplitude for i in range(rate)))
        blocks.append(samples.tobytes())
    raw = b"".join(blocks)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(raw)
    return raw


def test_configured_runtime_tool_resolves_outside_project_cwd(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[1]
    runtime = project_root / "data" / "singing" / "runtime" / "bin" / "ffmpeg.exe"
    if not runtime.is_file():
        pytest.skip("project runtime FFmpeg is unavailable")
    monkeypatch.chdir(tmp_path)
    assert locate_audio_tool("ffmpeg", "./data/singing/runtime/bin/ffmpeg.exe") == runtime


@pytest.mark.asyncio
async def test_real_ffmpeg_chunks_are_contiguous_and_within_limit(tmp_path):
    ffmpeg, ffprobe = _ffmpeg_pair()
    source = tmp_path / "song.wav"
    raw = _long_wav(source)

    chunks = await split_audio_for_qq(
        source,
        tmp_path / "records",
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        preferred_boundaries=[53.1, 105.2],
    )

    assert len(chunks) == 3
    assert chunks[0].end_seconds == pytest.approx(53.1, abs=1 / 24_000)
    assert chunks[1].end_seconds == pytest.approx(105.2, abs=1 / 24_000)
    assert chunks[-1].end_seconds == pytest.approx(122, abs=1 / 24_000)
    assert all(chunk.duration_seconds <= 55 for chunk in chunks)
    assert all(chunk.size_bytes == chunk.path.stat().st_size for chunk in chunks)
    assert all(a.end_seconds == b.start_seconds for a, b in pairwise(chunks))

    combined = bytearray()
    for chunk in chunks:
        with wave.open(str(chunk.path), "rb") as stream:
            assert stream.getnchannels() == 1
            assert stream.getframerate() == 24_000
            combined.extend(stream.readframes(stream.getnframes()))
    assert bytes(combined) == raw


@pytest.mark.asyncio
async def test_size_and_duration_limits_remove_outputs(tmp_path):
    ffmpeg, ffprobe = _ffmpeg_pair()
    source = tmp_path / "song.wav"
    _long_wav(source, seconds=61)
    records = tmp_path / "records"

    with pytest.raises(AudioChunkError, match="duration limit"):
        await split_audio_for_qq(
            source, records, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe, max_total_seconds=60
        )

    with pytest.raises(AudioChunkError, match="output size limit"):
        await split_audio_for_qq(
            source, records, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            max_output_bytes=100,
        )
    assert not list(records.glob("*.wav"))

    chunks = await split_audio_for_qq(
        source, records, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe
    )
    # The quiet second beginning at 49 s is preferred to a hard cut at 55 s.
    assert 49 <= chunks[0].end_seconds < 50
    assert chunks[-1].end_seconds == pytest.approx(61)


@pytest.mark.asyncio
async def test_command_timeout_and_cancellation_stop_promptly():
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await run_audio_command(command, timeout_seconds=0.1)
    assert time.monotonic() - started < 8

    task = asyncio.create_task(run_audio_command(command, timeout_seconds=30))
    await asyncio.sleep(0.1)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 8


@pytest.mark.asyncio
async def test_command_drains_large_output_and_keeps_private_stderr_tail():
    script = (
        "import os; "
        "os.write(1, b'O' * 1_000_000); "
        "os.write(2, b'E' * 1_000_000 + b'ENDMARKER'); "
        "raise SystemExit(7)"
    )
    with pytest.raises(AudioChunkError) as failure:
        await run_audio_command([sys.executable, "-c", script], timeout_seconds=5)
    assert len(failure.value.diagnostic_tail) <= 8192
    assert failure.value.diagnostic_tail.endswith("ENDMARKER")
    assert "ENDMARKER" not in str(failure.value)


@pytest.mark.asyncio
async def test_cancellation_stops_descendant_process(tmp_path):
    started = tmp_path / "started"
    survived = tmp_path / "survived"
    child_script = (
        "from pathlib import Path; import time; "
        f"time.sleep(2); Path({str(survived)!r}).touch()"
    )
    parent_script = (
        "from pathlib import Path; import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        f"Path({str(started)!r}).touch(); time.sleep(30)"
    )
    task = asyncio.create_task(
        run_audio_command([sys.executable, "-c", parent_script], timeout_seconds=30)
    )
    async def wait_for_start():
        while not started.exists():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_start(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.1)
    assert not survived.exists()
