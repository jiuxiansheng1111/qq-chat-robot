import json
import struct
import wave
from argparse import Namespace
from pathlib import Path

import pytest

from app.config import Settings
from app.services import singing
from app.services.music import NeteaseTrack
from app.services.singing_excerpt import _clean_lyrics
from app.services.singing_sources import LyricLine, SingingSong
from scripts import recheck_singing_languages as recheck


def _write_pcm16(path: Path, value: int, duration: int = 20, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack("<h", value) * duration * rate)


def test_steps_requires_one_explicit_item():
    with pytest.raises(SystemExit):
        recheck._parse_args(["--steps", "50"])
    args = recheck._parse_args([
        "--steps", "35", "--phase", "candidate", "--profile", "murasame",
        "--language", "ja",
    ])
    assert (args.steps, args.profile, args.language) == (35, "murasame", "ja")


def test_refresh_language_requires_retry_and_can_select_zh():
    with pytest.raises(SystemExit):
        recheck._parse_args(["--refresh-language", "zh"])
    args = recheck._parse_args([
        "--run-id", "review", "--retry-failed", "--refresh-language", "zh",
    ])
    assert args.refresh_languages == ["zh"]


def test_chinese_title_and_adaptation_credit_are_source_metadata():
    lines = (
        LyricLine(0.1, "朋友的酒-泽亦轩 remix"),
        LyricLine(0.8, "改编词、曲：泽亦轩"),
        LyricLine(60.0, "昨日一去不复回哦耶"),
        LyricLine(67.4, "朋友的酒最珍贵"),
    )
    cleaned = _clean_lyrics(
        lines, 100, ignore_texts=("朋友的酒（DJ版）", "泽亦轩"),
        song_title="朋友的酒（DJ版）", song_artists=("泽亦轩",),
    )
    assert cleaned == lines[2:]


def test_reusable_source_checks_every_file_and_nested_stem_paths(tmp_path):
    source_dir = tmp_path / "zh"
    stem_dir = source_dir / "separated" / "htdemucs_ft" / "excerpt"
    stem_dir.mkdir(parents=True)
    paths = {
        "source": source_dir / "original.mp3",
        "excerpt": source_dir / "excerpt.wav",
        "vocals": stem_dir / "vocals.wav",
        "accompaniment": stem_dir / "no_vocals.wav",
        "lyrics": source_dir / "excerpt.lrc",
    }
    for field, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1200 if field != "lyrics" else b"[00:00.000]hello\n")
    recheck._write_json(source_dir / "source.json", {
        "status": "ready",
        "language": "zh",
        "song_id": recheck.LANGUAGES["zh"]["song_id"],
        "title": "Friends",
        "artist": "Singer",
        "start_seconds": 12,
        "end_seconds": 32,
        "lyric_count": 3,
        "separation_model": "htdemucs_ft",
        "vocal_activity": {"accepted": True, "rms": 0.05},
        "files": {
            "source": "original.mp3",
            "excerpt": "excerpt.wav",
            "vocals": "separated/htdemucs_ft/excerpt/vocals.wav",
            "accompaniment": "separated/htdemucs_ft/excerpt/no_vocals.wav",
            "lyrics": "excerpt.lrc",
        },
    })

    settings = Settings(_env_file=None)
    loaded = recheck._load_reusable_source(source_dir, "zh", settings)
    assert loaded is not None
    assert loaded.vocals == paths["vocals"]
    assert loaded.seconds == 20

    paths["accompaniment"].unlink()
    assert recheck._load_reusable_source(source_dir, "zh", settings) is None


def test_missing_candidate_fails_instead_of_returning_base(tmp_path):
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(recheck.RecheckError, match="缺少 murasame 条目"):
        recheck._load_candidate_entry(registry, "murasame")


@pytest.mark.asyncio
async def test_source_is_cut_and_separated_once_then_reused(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    external_source = tmp_path / "downloaded.mp3"
    external_source.write_bytes(b"x" * 2000)
    track = NeteaseTrack(
        song_id=recheck.LANGUAGES["en"]["song_id"],
        title="Shape of You",
        artists=("Ed Sheeran",),
        album="album",
        cover_url="",
        duration_seconds=180,
    )
    lines = tuple(
        LyricLine(time_seconds=value, text=f"line {value}")
        for value in (10, 15, 20, 25, 30, 35)
    )
    song = SingingSong(track, "", lines, None, "https://music.126.net/source", 2000)
    calls = {"resolve": 0, "download": 0, "separation": 0}

    async def resolve(_query, _settings):
        calls["resolve"] += 1
        return song

    async def download(_song, _directory, _settings):
        calls["download"] += 1
        return external_source

    async def command(args, **_kwargs):
        string_args = [str(value) for value in args]
        if "-show_entries" in string_args:
            return json.dumps({"format": {"duration": "180"}})
        if "run_singing_separation.py" in " ".join(string_args):
            calls["separation"] += 1
            output = Path(string_args[string_args.index("--output") + 1])
            stem = Path(string_args[string_args.index("--source") + 1]).stem
            model = string_args[string_args.index("--model") + 1]
            directory = output / model / stem
            _write_pcm16(directory / "vocals.wav", 0 if calls["separation"] == 1 else 6000)
            _write_pcm16(directory / "no_vocals.wav", 4000)
            return ""
        output_path = Path(string_args[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"e" * 1200)
        return ""

    monkeypatch.setattr(recheck, "resolve_singing_song", resolve)
    monkeypatch.setattr(recheck, "download_singing_source", download)
    monkeypatch.setattr(
        singing, "runtime_paths",
        lambda _: (tmp_path / "python.exe", tmp_path, tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"),
    )
    monkeypatch.setattr(singing, "run_audio_command", command)

    async def validate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(singing, "_validate_model_audio", validate)
    source_dir = tmp_path / "sources" / "en"
    prepared = await recheck._prepare_source("en", source_dir, settings)
    assert 15 <= prepared.seconds <= 25
    assert prepared.lyric_count >= 2
    assert prepared.source == source_dir / "original.mp3"
    assert calls == {"resolve": 1, "download": 1, "separation": 2}
    assert prepared.start_seconds == 30
    assert prepared.vocal_activity["accepted"] is True
    assert prepared.lrc_timeline_unverified is True
    assert "LRC时轴待ASR核验" in prepared.selection_reason

    reused = await recheck._prepare_source("en", source_dir, settings)
    assert reused == prepared
    assert calls == {"resolve": 1, "download": 1, "separation": 2}


@pytest.mark.asyncio
async def test_candidate_checkpoint_is_in_memory_and_restored(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    vocals = tmp_path / "vocals.wav"
    accompaniment = tmp_path / "accompaniment.wav"
    reference = tmp_path / "reference.wav"
    _write_pcm16(vocals, 6000)
    _write_pcm16(accompaniment, 5000)
    _write_pcm16(reference, 5000)
    source = recheck.PreparedSource(
        language="zh", song_id="1939837729", title="song", artist="artist",
        source=tmp_path / "original.mp3", excerpt=tmp_path / "excerpt.wav",
        vocals=vocals, accompaniment=accompaniment, lyrics=tmp_path / "excerpt.lrc",
        start_seconds=10, end_seconds=30, lyric_count=4, separation_model="htdemucs_ft",
        vocal_activity={"accepted": True, "rms": 0.05},
    )
    candidate = (tmp_path / "candidate.pth", tmp_path / "candidate.yml")
    seen = []
    original_resolver = singing._checkpoint_for_profile

    async def fake_render(*args, **kwargs):
        seen.append(singing._checkpoint_for_profile("murasame"))
        mixed = tmp_path / "mixed.wav"
        record = tmp_path / "record.wav"
        return mixed, record, {"accepted": True, "kind": "instrumental", "converted_rms": 0.0}

    monkeypatch.setattr(singing, "_automatic_pitch_target", lambda *_: None)
    monkeypatch.setattr(singing, "_voice_pitch_shift_for_profile", lambda *_: 0)
    monkeypatch.setattr(singing, "_render_singing_section", fake_render)

    report = await recheck._run_item(
        "murasame", "zh", source, tmp_path / "phase" / "murasame", reference, reference,
        candidate, settings, tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe", None,
    )
    assert report["status"] == "failed"
    assert "instrumental" in report["error"]
    assert seen == [candidate]
    assert singing._checkpoint_for_profile is original_resolver


@pytest.mark.asyncio
async def test_silent_original_vocals_cannot_reach_renderer(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    vocals = tmp_path / "vocals.wav"
    accompaniment = tmp_path / "accompaniment.wav"
    reference = tmp_path / "reference.wav"
    _write_pcm16(vocals, 0)
    _write_pcm16(accompaniment, 4000)
    _write_pcm16(reference, 4000)
    source = recheck.PreparedSource(
        language="zh", song_id="1939837729", title="song", artist="artist",
        source=tmp_path / "original.mp3", excerpt=tmp_path / "excerpt.wav",
        vocals=vocals, accompaniment=accompaniment, lyrics=tmp_path / "excerpt.lrc",
        start_seconds=10, end_seconds=30, lyric_count=3, separation_model="htdemucs_ft",
    )
    called = False

    async def render(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("renderer must not receive an instrumental source")

    async def save_preview(*_args, **_kwargs):
        return None

    monkeypatch.setattr(singing, "_render_singing_section", render)
    monkeypatch.setattr(recheck, "_save_failed_previews", save_preview)
    report = await recheck._run_item(
        "murasame", "zh", source, tmp_path / "phase" / "murasame", reference, reference,
        None, settings, tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe", None,
    )
    assert report["status"] == "failed"
    assert report["vocal_activity"]["accepted"] is False
    assert called is False


@pytest.mark.asyncio
async def test_phase_keeps_going_after_one_source_failure(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    checkpoint = tmp_path / "candidate.pth"
    config = tmp_path / "candidate.yml"
    checkpoint.write_bytes(b"weights")
    config.write_text("{}", encoding="utf-8")
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({"murasame": {
        "status": "candidate_requires_human_review",
        "accepted": False,
        "checkpoint": str(checkpoint),
        "config": str(config),
    }}), encoding="utf-8")
    data_root = tmp_path / "singing"
    monkeypatch.setattr(recheck, "SINGING_DATA_ROOT", data_root)
    monkeypatch.setattr(recheck, "_candidate_registry_path", lambda: registry)
    monkeypatch.setattr(
        singing, "runtime_paths",
        lambda _: (tmp_path / "python.exe", tmp_path, tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"),
    )

    async def prepare(language, directory, _settings, original_source=None):
        if language == "zh":
            raise RuntimeError("lyrics source unavailable")
        return recheck.PreparedSource(
            language=language, song_id=recheck.LANGUAGES[language]["song_id"],
            title="song", artist="artist", source=tmp_path / f"{language}.mp3",
            excerpt=tmp_path / f"{language}.wav", vocals=tmp_path / f"{language}-vocals.wav",
            accompaniment=tmp_path / f"{language}-instrumental.wav",
            lyrics=tmp_path / f"{language}.lrc", start_seconds=10, end_seconds=30,
            lyric_count=3, separation_model="htdemucs_ft",
        )

    async def make_reference(profile_id, profile_dir, _settings, _ffmpeg):
        path = profile_dir / "reference.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reference")
        return path, path

    async def run_item(profile_id, language, source, profile_dir, *_args):
        directory = profile_dir / language
        directory.mkdir(parents=True, exist_ok=True)
        report = {"status": "passed", "profile_id": profile_id, "language": language}
        recheck._write_json(directory / "report.json", report)
        return report

    monkeypatch.setattr(recheck, "_prepare_source", prepare)
    monkeypatch.setattr(recheck, "_make_reference", make_reference)
    monkeypatch.setattr(recheck, "_run_item", run_item)
    args = Namespace(phase="candidate", run_id="smoke", profile="murasame", language=None, steps=None)

    phase = await recheck.run_phase(args, settings)
    manifest = json.loads((phase / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"]["murasame:zh"]["status"] == "failed"
    assert "lyrics source unavailable" in manifest["items"]["murasame:zh"]["error"]
    assert manifest["items"]["murasame:ja"]["status"] == "passed"
    assert manifest["items"]["murasame:en"]["status"] == "passed"
    assert manifest["summary"] == {"passed": 2, "failed": 1, "total": 3}


@pytest.mark.asyncio
async def test_refresh_language_reruns_passed_items_and_preserves_other_languages(
    tmp_path, monkeypatch
):
    settings = Settings(_env_file=None)
    data_root = tmp_path / "singing"
    output_root = data_root / "acceptance" / "refresh-smoke"
    phase_root = output_root / "baseline"
    old_source_dir = output_root / "sources" / "zh"
    old_source_dir.mkdir(parents=True)
    old_original = old_source_dir / "original.mp3"
    old_original.write_bytes(b"o" * 2048)
    recheck._write_json(old_source_dir / "source.json", {
        "language": "zh",
        "song_id": recheck.LANGUAGES["zh"]["song_id"],
        "files": {"source": "original.mp3"},
    })
    items = {}
    for profile_id in recheck.PROFILES:
        for language in LANGUAGES_FOR_TEST:
            report_path = phase_root / profile_id / language / "report.json"
            report_path.parent.mkdir(parents=True)
            recheck._write_json(report_path, {
                "status": "passed", "profile_id": profile_id, "language": language,
                "source": recheck._relative(old_original) if language == "zh" else None,
            })
            items[f"{profile_id}:{language}"] = {
                "status": "passed", "report": str(report_path),
            }
    recheck._write_json(phase_root / "manifest.json", {
        "status": "passed", "run_id": "refresh-smoke", "phase": "baseline",
        "steps_override": None, "items": items,
    })

    checkpoint = (tmp_path / "accepted.pth", tmp_path / "accepted.yml")
    monkeypatch.setattr(recheck, "SINGING_DATA_ROOT", data_root)
    monkeypatch.setattr(
        singing, "runtime_paths",
        lambda _: (tmp_path / "python.exe", tmp_path, tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"),
    )
    monkeypatch.setattr(singing, "_checkpoint_for_profile", lambda _profile: checkpoint)
    monkeypatch.setattr(recheck, "voice_profiles", lambda _settings: set(recheck.PROFILES))
    seen_originals = []
    ran_items = []

    async def prepare(language, directory, _settings, original_source=None):
        assert language == "zh"
        seen_originals.append(original_source)
        return recheck.PreparedSource(
            language="zh", song_id=recheck.LANGUAGES["zh"]["song_id"], title="song",
            artist="artist", source=old_original, excerpt=tmp_path / "excerpt.wav",
            vocals=tmp_path / "vocals.wav", accompaniment=tmp_path / "accompaniment.wav",
            lyrics=tmp_path / "excerpt.lrc", start_seconds=60, end_seconds=80,
            lyric_count=3, separation_model="htdemucs_ft",
            selection_reason="metadata filtered", vocal_activity={"accepted": True},
        )

    async def make_reference(profile_id, profile_dir, _settings, _ffmpeg):
        reference = profile_dir / "reference.wav"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(b"reference")
        return reference, reference

    async def run_item(profile_id, language, _source, profile_dir, *_args):
        ran_items.append(f"{profile_id}:{language}")
        report_dir = profile_dir / language
        report_dir.mkdir(parents=True, exist_ok=True)
        report = {"status": "passed", "profile_id": profile_id, "language": language}
        recheck._write_json(report_dir / "report.json", report)
        return report

    monkeypatch.setattr(recheck, "_prepare_source", prepare)
    monkeypatch.setattr(recheck, "_make_reference", make_reference)
    monkeypatch.setattr(recheck, "_run_item", run_item)
    args = recheck._parse_args([
        "--phase", "baseline", "--run-id", "refresh-smoke", "--retry-failed",
        "--refresh-language", "zh",
    ])
    phase = await recheck.run_phase(args, settings)

    archive = output_root / "sources" / "zh-previous-1"
    assert seen_originals == [archive / "original.mp3"]
    assert seen_originals[0].is_file()
    assert set(ran_items) == {f"{profile}:zh" for profile in recheck.PROFILES}
    assert (archive / "original.mp3").read_bytes() == b"o" * 2048
    manifest = json.loads((phase / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_archives"]["zh"] == recheck._relative(archive)
    for profile_id in recheck.PROFILES:
        assert manifest["items"][f"{profile_id}:zh"]["status"] == "passed"
        assert manifest["items"][f"{profile_id}:ja"]["status"] == "passed"
        assert manifest["items"][f"{profile_id}:en"]["status"] == "passed"
        previous = phase / profile_id / "zh" / "attempts" / "attempt-001.json"
        archived_report = json.loads(previous.read_text(encoding="utf-8"))
        assert archived_report["source"] == recheck._relative(archive / "original.mp3")


LANGUAGES_FOR_TEST = ("zh", "ja", "en")
