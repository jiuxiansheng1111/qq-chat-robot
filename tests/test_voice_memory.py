import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import singing, singing_gpu, voice_memory


@pytest.fixture
def local_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_memory, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(voice_memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(singing_gpu, "GPU_LOCK_PATH", tmp_path / "gpu.lock")
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(voice_memory.asyncio, "open_connection", AsyncMock(return_value=(None, writer)))
    return SimpleNamespace(voice_provider="gpt_sovits", voice_api_url="http://127.0.0.1:9880")


@pytest.mark.asyncio
async def test_cover_releases_voice_once_and_restores_after_gpu_unlock_even_on_failure(local_voice, monkeypatch):
    events = []

    async def powershell(script, arguments, **kwargs):
        if script.startswith("stop_"):
            assert singing_gpu.gpu_is_busy()
            events.append("stop")
            return '{"stopped":true,"private_bytes_before":6442450944}'
        assert not singing_gpu.gpu_is_busy()
        assert script == "start_voice.ps1" and arguments == []
        events.append("restore")
        return "ready"

    async def generate(*args, release_voice_memory, **kwargs):
        events.append("reference_ready")
        async with singing_gpu.async_gpu_lock(blocking=False):
            pass
        await release_voice_memory()
        await release_voice_memory()
        events.append("model")
        raise singing.SingingPipelineError("conversion failed")

    monkeypatch.setattr(voice_memory, "_powershell", powershell)
    monkeypatch.setattr(singing, "singing_resource_problem", lambda *a, **k: None)
    monkeypatch.setattr(singing, "_generate_singing_cover_unlocked", generate)
    with pytest.raises(singing.SingingPipelineError, match="conversion failed"):
        await singing.generate_singing_cover("song", "role", "0" * 32, local_voice, AsyncMock(), bot_self_id="test")
    assert events == ["reference_ready", "stop", "model", "restore"]


@pytest.mark.asyncio
async def test_cancellation_after_stop_still_restores_voice(local_voice, monkeypatch):
    stopped = asyncio.Event()
    calls = []

    async def command(script, args, **kwargs):
        calls.append(script)
        if script.startswith("stop_"):
            stopped.set()
            return '{"stopped":true}'
        return "ready"

    monkeypatch.setattr(voice_memory, "_powershell", command)

    async def work():
        async with voice_memory.release_voice_memory_for_singing(local_voice) as session, singing_gpu.async_gpu_lock():
            await session.pause()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await stopped.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["stop_gpt_sovits_for_singing.ps1", "start_voice.ps1"]
    assert not singing_gpu.gpu_is_busy()


@pytest.mark.asyncio
async def test_foreign_listener_and_remote_service_are_not_stopped_or_restarted(local_voice, monkeypatch):
    command = AsyncMock(return_value='{"stopped":false,"reason":"unmanaged_listener"}')
    monkeypatch.setattr(voice_memory, "_powershell", command)
    async with voice_memory.release_voice_memory_for_singing(local_voice) as session:
        await session.pause()
    assert command.await_count == 1
    command.reset_mock()
    local_voice.voice_api_url = "http://voice.example:9880"
    async with voice_memory.release_voice_memory_for_singing(local_voice) as session:
        await session.pause()
    command.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_pressure_reclaims_speech_before_rechecking_recorded_cover(local_voice, monkeypatch, tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"recording")
    local_voice.singing_prefer_recorded_reference = True
    monkeypatch.setattr(singing, "voice_profiles", lambda _: {"role": {"ref_audio_path": str(reference)}})
    readings = iter(["low physical memory", None])
    stages = []
    def check(*args, stage, **kwargs):
        stages.append(stage)
        return next(readings)
    command = AsyncMock(side_effect=['{"stopped":true}', 'ready'])
    monkeypatch.setattr(voice_memory, "_powershell", command)
    monkeypatch.setattr(singing, "singing_resource_problem", check)
    async def generate(*args, release_voice_memory, **kwargs):
        assert command.await_count == 1
        await release_voice_memory()
        return "cover"
    monkeypatch.setattr(singing, "_generate_singing_cover_unlocked", generate)
    assert await singing.generate_singing_cover("song", "role", "0"*32, local_voice, AsyncMock(), bot_self_id="test") == "cover"
    assert stages == ["startup", "startup_after_voice_release"]
    assert command.await_count == 2


@pytest.mark.asyncio
async def test_remaining_startup_pressure_still_blocks_cover_after_release(local_voice, monkeypatch, tmp_path):
    reference=tmp_path/'voice.wav';reference.write_bytes(b'recording')
    local_voice.singing_prefer_recorded_reference=True
    monkeypatch.setattr(singing, 'voice_profiles', lambda _: {'role':{'ref_audio_path':str(reference)}})
    command=AsyncMock(side_effect=['{"stopped":true}','ready'])
    monkeypatch.setattr(voice_memory, '_powershell', command)
    monkeypatch.setattr(singing, 'singing_resource_problem', lambda *a, **k:'still low')
    generate=AsyncMock()
    monkeypatch.setattr(singing, '_generate_singing_cover_unlocked', generate)
    with pytest.raises(singing.SingingPipelineError,match='still low'):
        await singing.generate_singing_cover('song','role','0'*32,local_voice,AsyncMock(),bot_self_id='test')
    generate.assert_not_awaited()
    assert command.await_count==2
