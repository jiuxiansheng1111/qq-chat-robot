import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.singing import (
    SingingPipelineError,
    _validate_model_audio,
    archive_singing_job,
    cleanup_singing_job,
    convert_vocals,
    generate_singing_cover,
    parse_singing_command,
    prepare_voice_reference,
    singing_job_directory,
)
from app.services.singing_jobs import SingingError, SingingJob, SingingJobManager

PROFILES = {"murasame": {"label": "小丛雨"}, "yoshino": {"label": "芳乃"}}


def test_singing_requires_explicit_request_and_preserves_song_title():
    assert parse_singing_command("唱歌 晴天", PROFILES, addressed=False) is None
    assert parse_singing_command("/singular", PROFILES, addressed=False) is None
    command = parse_singing_command("/翻唱 芳乃 朋友的酒DJ版", PROFILES, addressed=False)
    assert (command.action, command.profile_id, command.query) == ("sing", "yoshino", "朋友的酒DJ版")
    command = parse_singing_command("唱歌 芳乃的歌", PROFILES, addressed=True)
    assert command.query == "芳乃的歌"
    assert command.profile_id is None
    for control in ("/唱歌状态", "取消翻唱", "唱歌音色"):
        assert parse_singing_command(control, PROFILES, addressed=True).action in {"status", "cancel", "voices"}


def test_qq_chunk_limit_is_validated_even_when_config_is_wrong():
    with pytest.raises(ValidationError):
        Settings(singing_chunk_seconds=60)
    with pytest.raises(ValidationError):
        Settings(singing_separation_model="../escape")


@pytest.mark.asyncio
async def test_trained_tts_provides_voice_reference_without_reading_song_lyrics(tmp_path, monkeypatch):
    settings = Settings(voice_profiles_json='{"murasame":{"label":"小丛雨","supported_languages":["zh"]}}')
    synth = AsyncMock(return_value="base64://" + base64.b64encode(b"test-wave").decode())
    monkeypatch.setattr("app.services.singing.synthesize_voice", synth)
    path = await prepare_voice_reference("murasame", tmp_path, settings, "bot")
    assert path.read_bytes() == b"test-wave"
    args, kwargs = synth.call_args
    assert "声音" in args[0]
    assert kwargs["target_language"] == "zh"
    assert kwargs["bot_self_id"] == "bot"


@pytest.mark.asyncio
async def test_generation_failure_cleans_the_job_without_leaving_files(tmp_path, monkeypatch):
    root = tmp_path / "singing"
    root.mkdir()
    monkeypatch.setattr("app.services.singing.SINGING_DATA_ROOT", root)
    monkeypatch.setattr("app.services.singing.runtime_paths", lambda _: (Path("python"), tmp_path, Path("ffmpeg"), Path("ffprobe")))
    monkeypatch.setattr("app.services.singing.resolve_singing_song", AsyncMock(side_effect=SingingPipelineError("没有完整原曲")))
    with pytest.raises(SingingPipelineError, match="完整原曲"):
        await generate_singing_cover("歌曲", "murasame", "a" * 32, Settings(), AsyncMock(), bot_self_id="bot")
    assert not list((root / "jobs").iterdir())
    with pytest.raises(ValueError):
        await generate_singing_cover("歌曲", "murasame", "../outside", Settings(), AsyncMock(), bot_self_id="bot")


def test_job_archive_is_bounded_and_cleanup_does_not_touch_other_directories(tmp_path, monkeypatch):
    root = tmp_path / "singing"
    monkeypatch.setattr("app.services.singing.SINGING_DATA_ROOT", root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.wav").write_bytes(b"important")
    for number in range(3):
        job_id = f"{number:032x}"
        directory = singing_job_directory(job_id)
        directory.mkdir(parents=True)
        (directory / "cover.wav").write_bytes(b"wave")
        (directory / "original.mp3").write_bytes(b"original")
        archive_singing_job(job_id, keep=2)
        cleanup_singing_job(job_id)
        assert not directory.exists()
    assert len(list((root / "results").iterdir())) == 2
    assert not list((root / "results").rglob("original.mp3"))
    with pytest.raises(ValueError):
        cleanup_singing_job("../outside")
    assert (outside / "keep.wav").read_bytes() == b"important"


@pytest.mark.asyncio
async def test_model_output_cannot_exceed_duration_or_file_size_limits(tmp_path, monkeypatch):
    audio = tmp_path / "output.wav"
    audio.write_bytes(b"a" * 2000)
    probe = AsyncMock(return_value='{"format":{"duration":"10000"}}')
    monkeypatch.setattr("app.services.singing.run_audio_command", probe)
    with pytest.raises(SingingPipelineError, match="时长"):
        await _validate_model_audio(audio, Path("ffprobe"), 311, Settings())
    with pytest.raises(SingingPipelineError, match="大小"):
        await _validate_model_audio(audio, Path("ffprobe"), 311, Settings(singing_max_source_bytes=500))
    assert probe.await_count == 1
    probe.return_value = '{"format":{"duration":"311.8"}}'
    await _validate_model_audio(audio, Path("ffprobe"), 311.8, Settings())


@pytest.mark.asyncio
async def test_cached_model_mode_is_passed_only_to_the_singing_subprocess(tmp_path, monkeypatch):
    settings = Settings(singing_hf_offline=True)
    monkeypatch.setattr("app.services.singing.runtime_paths", lambda _: (Path("python"), tmp_path, Path("ffmpeg"), Path("ffprobe")))
    monkeypatch.setattr("app.services.singing._checkpoint_for_profile", lambda _: None)
    arguments = []

    async def run(args, **kwargs):
        arguments.extend(args)
        (tmp_path / "converted" / "result.wav").write_bytes(b"a" * 2000)
        return ""

    monkeypatch.setattr("app.services.singing.run_audio_command", run)
    result = await convert_vocals(tmp_path / "source.wav", tmp_path / "reference.wav", tmp_path / "converted", "murasame", settings)
    assert result.name == "result.wav"
    assert "--offline" in arguments


@pytest.mark.asyncio
async def test_command_admission_returns_before_long_gpu_work_and_cancel_stops_it(monkeypatch):
    from app import main
    from app.services.onebot_routing import set_current_onebot_self_id

    monkeypatch.setattr(main.settings, "singing_enabled", True)
    monkeypatch.setattr(main, "voice_profiles", lambda _: PROFILES)
    monkeypatch.setattr(main, "voice_profile_authorized", lambda *args: True)
    monkeypatch.setattr(main, "bot_mentioned", lambda _: True)
    monkeypatch.setattr(main, "runtime_paths", lambda _: None)
    send = AsyncMock()
    monkeypatch.setattr(main, "send_group_message", send)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def worker(job, profile_id, bot_self_id):
        assert profile_id == "yoshino"
        assert bot_self_id == "bot-2"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(main, "_run_singing_job", worker)
    manager = SingingJobManager(cooldown_seconds=0)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(singing_jobs=manager)))
    set_current_onebot_self_id("bot-2")
    try:
        handled = await main._handle_singing_command(request, {}, "唱歌 芳乃 晴天", "group", "user", "murasame")
        assert handled
        assert send.await_count == 1
        await asyncio.wait_for(started.wait(), 1)
        assert await main._handle_singing_command(request, {}, "取消唱歌", "group", "user", "murasame")
        await asyncio.wait_for(stopped.wait(), 1)
        assert manager.status(("bot-2", "group", "user")).status == "cancelled"
    finally:
        await manager.aclose()
        set_current_onebot_self_id("")


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_second", [False, True])
async def test_worker_sends_segments_in_order_and_stops_after_qq_rejects(
    tmp_path, monkeypatch, reject_second
):
    from app import main

    chunks = []
    for index in range(3):
        path = tmp_path / f"chunk_{index}.wav"
        path.write_bytes(f"wave-{index}".encode())
        chunks.append(SimpleNamespace(path=path))
    cover = SimpleNamespace(label="丛雨", song=SimpleNamespace(track=SimpleNamespace(title="歌曲")), chunks=chunks)
    monkeypatch.setattr(main, "generate_singing_cover", AsyncMock(return_value=cover))
    monkeypatch.setattr(main, "send_group_message", AsyncMock())
    records = AsyncMock(side_effect=[True, False] if reject_second else [True, True, True])
    monkeypatch.setattr(main, "send_group_record", records)
    monkeypatch.setattr(main.settings, "singing_segment_pause_seconds", 0)
    archived = []
    cleaned = []
    monkeypatch.setattr(main, "archive_singing_job", archived.append)
    monkeypatch.setattr(main, "cleanup_singing_job", cleaned.append)
    job = SingingJob("b" * 32, "歌曲", ("bot", "group", "user"), "running", "", None, 0)
    if reject_second:
        with pytest.raises(SingingError, match="已发送 1 段"):
            await main._run_singing_job(job, "murasame", "bot")
    else:
        await main._run_singing_job(job, "murasame", "bot")
    expected_count = 2 if reject_second else 3
    assert records.await_count == expected_count
    for index, call in enumerate(records.await_args_list):
        assert call.args[0] == "group"
        assert base64.b64decode(call.args[1].removeprefix("base64://")) == f"wave-{index}".encode()
    assert archived == [job.id]
    assert cleaned == [job.id]
