import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import numpy as np
import pytest
from pydantic import ValidationError

from app.config import Settings as AppSettings
from app.services.singing import (
    SingingCover,
    SingingPipelineError,
    _accompaniment_pitch_shift,
    _automatic_pitch_target,
    _plan_automatic_pitch,
    _validate_model_audio,
    _vocal_mix_gain,
    _voice_pitch_shift_for_profile,
    archive_singing_job,
    check_cover_quality,
    cleanup_singing_job,
    convert_vocals,
    generate_singing_cover,
    parse_singing_command,
    prepare_voice_reference,
    singing_job_directory,
)
from app.services.singing_jobs import SingingError, SingingJob, SingingJobManager
from scripts.run_singing_model import _parse_args as _parse_singing_model_args
from scripts.run_singing_model import _repair_isolated_f0_spikes
from scripts.transpose_singing_audio import transpose_audio_file

PROFILES = {"murasame": {"label": "小丛雨"}, "yoshino": {"label": "芳乃"}}


class Settings(AppSettings):
    # Local selected voices and presets must not change these mock scenarios.
    model_config: ClassVar[dict] = {**AppSettings.model_config, "env_file": None}


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
    assert Settings().singing_chunk_seconds == 115
    assert Settings(singing_chunk_seconds=115).singing_chunk_seconds == 115
    with pytest.raises(ValidationError):
        Settings(singing_chunk_seconds=116)
    with pytest.raises(ValidationError):
        Settings(singing_separation_model="../escape")


def test_singing_inference_seed_and_f0_repair_are_reproducible_and_opt_in(monkeypatch):
    monkeypatch.delenv("SINGING_SEED", raising=False)
    monkeypatch.delenv("SINGING_REPAIR_F0_SPIKES", raising=False)
    args = _parse_singing_model_args([
        "--seed-root", "seed", "--ffmpeg", "ffmpeg", "--source", "source.wav",
        "--target", "target.wav", "--output", "out",
    ])
    assert args.seed == 20261004
    assert args.repair_f0_spikes is False

    monkeypatch.setenv("SINGING_SEED", "42")
    monkeypatch.setenv("SINGING_REPAIR_F0_SPIKES", "true")
    args = _parse_singing_model_args([
        "--seed-root", "seed", "--ffmpeg", "ffmpeg", "--source", "source.wav",
        "--target", "target.wav", "--output", "out",
    ])
    assert args.seed == 42
    assert args.repair_f0_spikes is True

    f0 = np.array([220.0, 222.0, 440.0, 221.0, 0.0, 220.0, 0.0, 221.0])
    repaired = _repair_isolated_f0_spikes(f0)
    assert repaired[2] == pytest.approx(np.sqrt(222 * 221))
    assert repaired[5] == 220.0 and repaired[7] == 221.0  # 不跨静音修复

    vibrato = 220 * 2 ** (0.3 * np.sin(np.arange(9) * 2 * np.pi / 8) / 12)
    assert np.array_equal(_repair_isolated_f0_spikes(vibrato), vibrato)


@pytest.mark.parametrize(
    ("voice_shift", "accompaniment_shift"),
    [(0, 0), (3, 3), (9, -3), (12, 0), (-9, 3), (-12, 0)],
)
def test_accompaniment_shift_uses_nearest_equivalent_key(voice_shift, accompaniment_shift):
    assert _accompaniment_pitch_shift(voice_shift) == accompaniment_shift


def test_profile_pitch_shift_defaults_to_zero_for_other_roles_and_accepts_bounds():
    settings = Settings(singing_semitone_shift_by_profile_json='{"murasame":-12,"yoshino":12}')
    assert _voice_pitch_shift_for_profile(settings, "murasame") == -12
    assert _voice_pitch_shift_for_profile(settings, "yoshino") == 12
    assert _voice_pitch_shift_for_profile(settings, "other") == 0


@pytest.mark.parametrize(
    "value",
    [
        "invalid",
        "[]",
        "null",
        '{"murasame":true}',
        '{"murasame":3.0}',
        '{"murasame":"3"}',
        '{"murasame":13}',
        '{"other":-13}',
    ],
)
def test_invalid_profile_pitch_shift_config_raises_safe_error(value):
    settings = Settings(singing_semitone_shift_by_profile_json=value)
    with pytest.raises(SingingPipelineError, match="音区配置无效"):
        _voice_pitch_shift_for_profile(settings, "murasame")


def test_singing_cover_pitch_shift_defaults_to_original_pitch():
    cover = SingingCover(None, "murasame", "丛雨", Path("cover.wav"), (), {})
    assert cover.pitch_shift_semitones == 0


def test_automatic_pitch_targets_are_per_role_and_manual_zero_has_priority():
    settings = Settings(singing_target_median_f0_by_profile_json='{"murasame":400}')
    assert _automatic_pitch_target(settings, "murasame") == 400
    assert _automatic_pitch_target(settings, "yoshino") is None
    settings.singing_semitone_shift_by_profile_json = '{"murasame":0}'
    assert _automatic_pitch_target(settings, "murasame") is None


@pytest.mark.parametrize("value", ["null", "invalid", '{"murasame":true}', '{"murasame":79}', '{"murasame":1001}', '{"murasame":"400"}', '{"murasame":NaN}'])
def test_automatic_pitch_rejects_bad_targets(value):
    settings = Settings(singing_target_median_f0_by_profile_json=value)
    with pytest.raises(SingingPipelineError, match="自动音区配置无效"):
        _automatic_pitch_target(settings, "murasame")


def test_b_style_vocal_mix_restores_level_with_bounded_constant_gain():
    settings = Settings()
    assert _vocal_mix_gain({"converted_rms": 0.0716252832}, settings) == pytest.approx(2.792310076)
    assert _vocal_mix_gain({"converted_rms": 0.001}, settings) == 4
    assert _vocal_mix_gain({"converted_rms": 2}, settings) == 1.0
    for rms in (None, 0, float("nan"), True):
        with pytest.raises(SingingPipelineError, match="歌声音量"):
            _vocal_mix_gain({"converted_rms": rms}, settings)


@pytest.mark.asyncio
async def test_automatic_pitch_cli_passes_target_and_offline_then_rejects_bad_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.singing.runtime_paths", lambda _: (Path("python"), tmp_path, Path("ffmpeg"), Path("ffprobe")))
    output = tmp_path / "pitch_plan.json"
    calls = []
    async def run(args, **kwargs):
        calls.append([str(item) for item in args])
        output.write_text('{"expected_semitone_shift":12,"source_median_f0_hz":200}', encoding="utf-8")
    monkeypatch.setattr("app.services.singing.run_audio_command", run)
    plan = await _plan_automatic_pitch(tmp_path / "vocals.wav", 400, output, Settings(singing_hf_offline=True))
    assert plan["mode"] == "automatic_octave"
    assert plan["expected_semitone_shift"] == 12
    assert calls[0][calls[0].index("--target-hz") + 1] == "400"
    assert "--offline" in calls[0]
    monkeypatch.setattr("app.services.singing.run_audio_command", AsyncMock())
    output.write_text('{"expected_semitone_shift":3}', encoding="utf-8")
    with pytest.raises(SingingPipelineError, match="可用的八度"):
        await _plan_automatic_pitch(tmp_path / "vocals.wav", 400, output, Settings())


@pytest.mark.asyncio
async def test_trained_tts_reference_remains_available_for_other_profiles(tmp_path, monkeypatch):
    settings = Settings(
        voice_profiles_json='{"yoshino":{"label":"芳乃","supported_languages":["zh"]}}',
        voice_primary_account_profile_ids="",
        singing_prefer_recorded_reference=False,
    )
    synth = AsyncMock(return_value="base64://" + base64.b64encode(b"test-wave").decode())
    monkeypatch.setattr("app.services.singing.synthesize_voice", synth)
    path = await prepare_voice_reference("yoshino", tmp_path, settings, "bot")
    assert path.read_bytes() == b"test-wave"
    args, kwargs = synth.call_args
    assert "声音" in args[0]
    assert kwargs["target_language"] == "zh"
    assert kwargs["bot_self_id"] == "bot"


@pytest.mark.asyncio
async def test_murasame_uses_original_game_recording_as_singing_reference(tmp_path, monkeypatch):
    original = tmp_path / "murasame-ja.wav"
    original.write_bytes(b"game-recording")
    settings = Settings(
        voice_profiles_json=json.dumps(
            {"murasame": {"label": "小丛雨", "supported_languages": ["zh"], "ref_audio_path": str(original)}}
        ),
        singing_prefer_recorded_reference=True,
    )
    synth = AsyncMock()
    monkeypatch.setattr("app.services.singing.synthesize_voice", synth)
    reference = await prepare_voice_reference("murasame", tmp_path, settings, "bot")
    assert reference == original
    assert not synth.await_count


@pytest.mark.asyncio
async def test_singing_reference_override_does_not_modify_chat_reference(tmp_path, monkeypatch):
    original = tmp_path / "original.wav"
    original.write_bytes(b"original")
    cute = tmp_path / "cute.wav"
    cute.write_bytes(b"cute")
    profiles = {"murasame": {"label": "丛雨", "ref_audio_path": str(original)}}
    settings = Settings(
        voice_profiles_json=json.dumps(profiles),
        singing_reference_audio_by_profile_json=json.dumps({"murasame": str(cute)}),
    )
    synth = AsyncMock()
    monkeypatch.setattr("app.services.singing.synthesize_voice", synth)
    reference = await prepare_voice_reference("murasame", tmp_path, settings, "bot")
    assert reference == cute
    assert json.loads(settings.voice_profiles_json)["murasame"]["ref_audio_path"] == str(original)
    assert not synth.await_count


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["[1]", "invalid", '{"murasame":123}'])
async def test_bad_reference_overrides_raise_safe_configuration_errors(tmp_path, value):
    settings = Settings(
        voice_profiles_json='{"murasame":{"label":"丛雨"}}',
        singing_reference_audio_by_profile_json=value,
    )
    with pytest.raises(SingingPipelineError, match="配置|路径"):
        await prepare_voice_reference("murasame", tmp_path, settings, "bot")


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
    result = await convert_vocals(
        tmp_path / "source.wav", tmp_path / "reference.wav",
        tmp_path / "converted", "murasame", settings,
    )
    assert result.name == "result.wav"
    assert "--offline" in arguments
    assert arguments[arguments.index("--semitone-shift") + 1] == "0"


@pytest.mark.asyncio
async def test_convert_and_quality_commands_pass_profile_pitch_shift(tmp_path, monkeypatch):
    settings = Settings()
    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    monkeypatch.setattr(
        "app.services.singing.runtime_paths",
        lambda _: (Path("python"), seed_root, Path("ffmpeg"), Path("ffprobe")),
    )
    monkeypatch.setattr("app.services.singing._checkpoint_for_profile", lambda _: None)
    calls = []

    async def run(args, **kwargs):
        calls.append([str(item) for item in args])
        if "run_singing_model.py" in calls[-1][1]:
            output = tmp_path / "converted"
            output.mkdir(exist_ok=True)
            (output / "result.wav").write_bytes(b"a" * 2000)
        else:
            report = {
                "accepted": True,
                "failures": [],
                "pitch_median_cents": 0,
                "pitch_within_semitone_ratio": 1,
                "voiced_recall": 1,
                "voice_similarity": 1,
                "duration_ratio": 1,
                "source_voiced_frames": 200,
                "converted_rms": 0.1,
                "clipping_ratio": 0,
                "vocal_energy_recall": 1,
                "max_missing_vocal_seconds": 0,
            }
            Path(calls[-1][calls[-1].index("--output") + 1]).write_text(
                json.dumps(report), encoding="utf-8"
            )
        return ""

    monkeypatch.setattr("app.services.singing.run_audio_command", run)
    converted = await convert_vocals(
        tmp_path / "source.wav", tmp_path / "reference.wav",
        tmp_path / "converted", "murasame", settings, semitone_shift=3,
    )
    report = await check_cover_quality(
        tmp_path / "source.wav", converted, tmp_path / "reference.wav",
        tmp_path / "quality.json", settings, expected_semitone_shift=3,
    )
    assert report["accepted"] is True
    model_args, quality_args = calls
    assert model_args[model_args.index("--semitone-shift") + 1] == "3"
    assert quality_args[quality_args.index("--expected-semitone-shift") + 1] == "3"


def test_cpu_accompaniment_transposer_preserves_stereo_rate_and_exact_length(tmp_path, monkeypatch):
    source = tmp_path / "stereo.wav"
    source.write_bytes(b"input")
    samples = np.arange(30, dtype=np.float32).reshape(15, 2) / 40
    calls = []
    written = {}

    def pitch_shift(channel, **kwargs):
        calls.append(kwargs)
        return np.pad(channel + 0.01, (0, 2))

    fake_librosa = SimpleNamespace(effects=SimpleNamespace(pitch_shift=pitch_shift))
    fake_soundfile = SimpleNamespace(
        read=lambda *args, **kwargs: (samples.copy(), 44100),
        write=lambda path, data, rate, **kwargs: written.update(
            path=Path(path), data=np.asarray(data).copy(), rate=rate, kwargs=kwargs
        ),
    )
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)
    output = tmp_path / "out" / "stereo-shifted.wav"

    transpose_audio_file(source, output, -3)

    assert written["path"] == output
    assert written["rate"] == 44100
    assert written["kwargs"]["subtype"] == "PCM_16"
    assert written["data"].shape == samples.shape
    unscaled = samples + 0.01
    gain = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / np.sqrt(
        np.mean(unscaled.astype(np.float64) ** 2)
    )
    np.testing.assert_allclose(written["data"], unscaled * gain)
    assert len(calls) == 2
    assert all(call["sr"] == 44100 and call["n_steps"] == -3 for call in calls)
    assert all(call["res_type"] == "soxr_hq" for call in calls)


def test_cpu_accompaniment_transposer_uses_uniform_peak_guard(tmp_path, monkeypatch):
    source = tmp_path / "stereo.wav"
    source.write_bytes(b"input")
    samples = np.full((4, 2), 0.5, dtype=np.float32)
    written = {}
    fake_librosa = SimpleNamespace(
        effects=SimpleNamespace(
            pitch_shift=lambda channel, **kwargs: np.array([1.0, 0.0, 0.0, 0.0])
        )
    )
    fake_soundfile = SimpleNamespace(
        read=lambda *args, **kwargs: (samples.copy(), 44100),
        write=lambda path, data, rate, **kwargs: written.update(
            data=np.asarray(data).copy(), rate=rate
        ),
    )
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    transpose_audio_file(source, tmp_path / "shifted.wav", 2)

    assert written["data"].shape == samples.shape
    assert float(np.max(np.abs(written["data"]))) == pytest.approx(0.99)
    assert written["data"][0, 0] == written["data"][0, 1]
    assert np.count_nonzero(written["data"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_generation_retries_with_same_shift_and_mixes_equivalent_accompaniment(
    tmp_path, monkeypatch, automatic,
):
    data_root = tmp_path / "singing-data"
    monkeypatch.setattr("app.services.singing.SINGING_DATA_ROOT", data_root)
    seed_root = tmp_path / "seed-vc"
    seed_root.mkdir()
    monkeypatch.setattr(
        "app.services.singing.runtime_paths",
        lambda _: (Path("python"), seed_root, Path("ffmpeg"), Path("ffprobe")),
    )
    song = SimpleNamespace(
        track=SimpleNamespace(song_id="song-id", title="song", duration_seconds=1),
        lyrics_text="[00:00.00]lyric",
        lyric_lines=(),
    )
    async def download(_, job_dir, __):
        source = job_dir / "song.mp3"
        source.write_bytes(b"source" * 500)
        return source

    reference = tmp_path / "murasame-ja.wav"
    reference.write_bytes(b"reference")
    monkeypatch.setattr("app.services.singing.resolve_singing_song", AsyncMock(return_value=song))
    monkeypatch.setattr("app.services.singing.download_singing_source", download)
    monkeypatch.setattr(
        "app.services.singing.prepare_voice_reference", AsyncMock(return_value=reference)
    )
    monkeypatch.setattr("app.services.singing.voice_profiles", lambda _: {"murasame": {"label": "丛雨"}})
    monkeypatch.setattr(
        "app.services.singing.wav_rms", lambda _: 0.1,
    )
    monkeypatch.setattr(
        "app.services.singing.plan_paused_sections",
        lambda *args: (SimpleNamespace(
            start_seconds=0.0, end_seconds=1.0, duration_seconds=1.0, reason="test",
        ),),
    )

    model_shifts = []

    async def convert(source, ref, output_dir, profile_id, settings, *, steps=None, semitone_shift=0):
        model_shifts.append((steps, semitone_shift))
        output_dir.mkdir(parents=True, exist_ok=True)
        converted = output_dir / "vocals.wav"
        converted.write_bytes(b"converted" * 250)
        return converted

    quality_shifts = []

    async def check(source, converted, ref, output, settings, *, expected_semitone_shift=0):
        quality_shifts.append((output.name, expected_semitone_shift))
        if len(quality_shifts) == 1:
            raise SingingPipelineError("first quality attempt requests retry")
        report = {
            "accepted": True,
            "failures": [],
            "pitch_median_cents": 0,
            "converted_rms": 0.05,
            "vocal_energy_recall": 1,
            "max_missing_vocal_seconds": 0,
        }
        output.write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr("app.services.singing.convert_vocals", convert)
    monkeypatch.setattr("app.services.singing.check_cover_quality", check)
    transposer_args = []
    mix_accompaniments = []
    mix_filters = []

    async def run_command(args, **kwargs):
        values = [str(value) for value in args]
        if Path(values[0]).name == "ffprobe":
            return '{"format":{"duration":"1.0"}}'
        script = Path(values[1]).name if len(values) > 1 else ""
        if script == "run_singing_separation.py":
            output_dir = Path(values[values.index("--output") + 1])
            stem_dir = output_dir / "htdemucs_ft" / "song"
            stem_dir.mkdir(parents=True)
            (stem_dir / "vocals.wav").write_bytes(b"v" * 2000)
            (stem_dir / "no_vocals.wav").write_bytes(b"a" * 2000)
        elif script == "transpose_singing_audio.py":
            transposer_args.extend(values)
            output = Path(values[values.index("--output") + 1])
            output.write_bytes(b"shifted" * 300)
        elif Path(values[0]).name == "ffmpeg":
            if "-filter_complex" in values:
                input_indices = [index for index, value in enumerate(values) if value == "-i"]
                mix_accompaniments.append(Path(values[input_indices[1] + 1]))
                mix_filters.append(values[values.index("-filter_complex") + 1])
            Path(values[-1]).write_bytes(b"cover" * 500)
        else:
            raise AssertionError(f"Unexpected audio command: {values}")
        return ""

    monkeypatch.setattr("app.services.singing.run_audio_command", run_command)
    planner = AsyncMock(return_value={"mode": "automatic_octave", "expected_semitone_shift": 12, "source_median_f0_hz": 200})
    monkeypatch.setattr("app.services.singing._plan_automatic_pitch", planner)
    settings = Settings(
        singing_semitone_shift_by_profile_json='{}' if automatic else '{"murasame":9}',
        singing_target_median_f0_by_profile_json='{"murasame":400}',
    )
    cover = await generate_singing_cover(
        "song", "murasame", "a" * 32, settings, AsyncMock(), bot_self_id="bot"
    )

    selected_shift = 12 if automatic else 9
    assert model_shifts == [(None, selected_shift), (50, selected_shift)]
    assert quality_shifts == [("quality.json", selected_shift), ("quality_retry.json", selected_shift)]
    if automatic:
        assert planner.await_count == 1
        assert not transposer_args
        assert mix_accompaniments[0] == (
            data_root / "jobs" / ("a" * 32) / "parts" / "001" / "background.wav"
        )
    else:
        assert not planner.await_count
        assert transposer_args[transposer_args.index("--semitones") + 1] == "-3"
        assert mix_accompaniments == [
            data_root / "jobs" / ("a" * 32) / "parts" / "001" / "accompaniment_shifted.wav"
        ]
    assert "[0:a]aformat=channel_layouts=stereo,highpass=f=60" in mix_filters[0]
    assert "volume=4.0000000000,acompressor=threshold=0.32" in mix_filters[0]
    assert "[1:a]aformat=channel_layouts=stereo,volume=0.8500000000[background]" in mix_filters[0]
    assert "amix=inputs=2:duration=longest:normalize=0" in mix_filters[0]
    assert "volume=0.9300000000,alimiter=limit=0.95" in mix_filters[0]
    assert cover.pitch_shift_semitones == selected_shift
    assert cover.quality["voice_pitch_shift_semitones"] == selected_shift
    assert cover.quality["accompaniment_pitch_shift_semitones"] == (0 if automatic else -3)
    assert "pitch_median_cents" not in cover.quality
    assert len(cover.quality["segments"]) == 1
    job_dir = data_root / "jobs" / ("a" * 32)
    segment_dir = job_dir / "parts" / "001"
    saved_retry_report = json.loads(
        (segment_dir / "quality_retry.json").read_text(encoding="utf-8")
    )
    assert saved_retry_report["vocal_energy_recall"] == 1
    saved_part_report = json.loads(
        (segment_dir / "report.json").read_text(encoding="utf-8")
    )
    assert saved_part_report["voice_pitch_shift_semitones"] == selected_shift
    assert saved_part_report["accompaniment_pitch_shift_semitones"] == (0 if automatic else -3)
    saved_report = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
    assert saved_report["voice_pitch_shift_semitones"] == selected_shift
    assert len(saved_report["segments"]) == 1
    assert saved_report["segments"][0]["pitch_median_cents"] == 0
    assert saved_report["pitch_plan"]["mode"] == ("automatic_octave" if automatic else "fixed")


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
@pytest.mark.parametrize("pitch_shift", [0, 12])
async def test_worker_sends_segments_in_order_and_stops_after_qq_rejects(
    tmp_path, monkeypatch, reject_second, pitch_shift
):
    from app import main

    chunks = []
    for index in range(3):
        path = tmp_path / f"chunk_{index}.wav"
        path.write_bytes(f"wave-{index}".encode())
        chunks.append(SimpleNamespace(path=path))
    cover = SimpleNamespace(
        label="丛雨", song=SimpleNamespace(track=SimpleNamespace(title="歌曲")),
        chunks=chunks, pitch_shift_semitones=pitch_shift,
    )
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
    first_message = main.send_group_message.await_args_list[0].args[1]
    assert ("人声升高一个八度，伴奏保持原调" in first_message) == bool(pitch_shift)
    for index, call in enumerate(records.await_args_list):
        assert call.args[0] == "group"
        assert base64.b64decode(call.args[1].removeprefix("base64://")) == f"wave-{index}".encode()
    assert archived == [job.id]
    assert cleaned == [job.id]
