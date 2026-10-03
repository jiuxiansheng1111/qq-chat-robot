import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_singing_lyrics import (
    WhisperTranscriber,
    _parse_args,
    analyze_item,
    collect_matrix_items,
    compare_transcripts,
    normalize_text,
    read_lrc,
    run_matrix,
)


class FixedTranscriber:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def transcribe(self, audio_path: Path, language: str) -> str:
        self.calls.append((audio_path, language))
        return self.outputs.pop(0)


def _write(path: Path, value: str = "audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def test_lrc_parser_removes_timestamp_and_metadata_without_losing_words(tmp_path):
    lyrics = _write(
        tmp_path / "lyrics.lrc",
        "[ti:Demo]\n[00:00.00]你好，世界！\n[00:02.10]再见。\n",
    )
    parsed = read_lrc(lyrics)
    assert normalize_text(parsed) == "你好世界再见"


def test_cer_metrics_cover_source_conversion_and_both_lyric_comparisons():
    report = compare_transcripts("你好世界", "你好世界呀", "你好，世界")
    assert report["transcript_consistency"] == {
        "cer": pytest.approx(0.25),
        "character_errors": 1,
        "reference_characters": 4,
        "hypothesis_characters": 5,
    }
    assert report["source_vs_lyrics"]["cer"] == 0
    assert report["converted_vs_lyrics"]["cer"] == pytest.approx(0.25)


def test_analyze_item_requests_both_languages_and_never_returns_transcript_text(tmp_path):
    source = _write(tmp_path / "source.wav")
    converted = _write(tmp_path / "converted.wav")
    lyrics = _write(tmp_path / "lyrics.lrc", "[00:00.0]今日は嬉しいです。\n")
    transcriber = FixedTranscriber(["今日は嬉しいです", "今日はうれしいです"])

    report = analyze_item(source, converted, lyrics, "ja", transcriber)

    assert transcriber.calls == [(source, "ja"), (converted, "ja")]
    assert report["source_vs_lyrics"]["cer"] == 0
    assert report["converted_vs_lyrics"]["cer"] > 0
    assert report["transcription_text_saved"] is False
    serialized = json.dumps(report, ensure_ascii=False)
    assert "今日は" not in serialized


def test_matrix_reader_and_batch_runner_match_phase_manifest_schema(tmp_path):
    matrix_root = tmp_path / "run-1"
    phase = matrix_root / "baseline"
    item = phase / "murasame" / "zh"
    source = _write(item / "vocal_before.wav")
    converted = _write(item / "vocal_after.wav")
    lyrics = _write(matrix_root / "sources" / "zh" / "excerpt.lrc", "[00:00.0]你好世界\n")
    item_report = item / "report.json"
    item_report.write_text(json.dumps({
        "status": "passed",
        "profile_id": "murasame",
        "language": "zh",
        "song_id": "song-zh",
        "vocals": str(source.relative_to(matrix_root)),
        "vocal_after": str(converted.relative_to(matrix_root)),
        "lyrics": str(lyrics.relative_to(matrix_root)),
    }), encoding="utf-8")
    (phase / "manifest.json").write_text(json.dumps({
        "phase": "baseline",
        "items": {
            "murasame:zh": {
                "status": "passed",
                "report": str(item_report.relative_to(matrix_root)),
            }
        },
    }), encoding="utf-8")

    items = collect_matrix_items(matrix_root, "baseline")
    assert len(items) == 1 and items[0]["status"] == "ready"
    report = run_matrix(matrix_root, "baseline", FixedTranscriber(["你好世界", "你好"] ))

    assert report["phase"] == "baseline"
    assert report["diagnostic_only"] is True
    assert report["items"][0]["profile_id"] == "murasame"
    assert report["items"][0]["source_vs_lyrics"]["cer"] == 0
    assert report["items"][0]["converted_vs_lyrics"]["cer"] == pytest.approx(0.5)


def test_matrix_rejects_paths_that_escape_run_root(tmp_path):
    matrix_root = tmp_path / "run-2"
    phase = matrix_root / "candidate"
    phase.mkdir(parents=True)
    external = _write(tmp_path / "outside.json", "{}")
    (phase / "manifest.json").write_text(json.dumps({
        "items": {"murasame:en": {"status": "passed", "report": str(external)}}
    }), encoding="utf-8")

    result = collect_matrix_items(matrix_root, "candidate")

    assert result[0]["status"] == "failed"
    assert result[0]["error_type"] == "matrix_item_inputs_missing_or_invalid"


def test_cli_accepts_single_and_matrix_modes_and_validates_required_options():
    single = _parse_args([
        "--source", "before.wav", "--converted", "after.wav", "--lyrics", "song.lrc",
        "--language", "en", "--output", "report.json",
    ])
    assert single.language == "en"
    matrix = _parse_args(["--matrix-root", "run", "--phase", "candidate"])
    assert matrix.phase == "candidate"
    with pytest.raises(SystemExit):
        _parse_args(["--source", "before.wav"])


def test_whisper_caches_source_transcript_by_path_stat_and_language(tmp_path, monkeypatch):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"source")
    load_calls = []

    def fake_load(path, *, sr, mono):
        load_calls.append((Path(path), sr, mono))
        return [0.1, 0.2], sr

    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(load=fake_load))

    class FakeFeatures:
        def to(self, device):
            return self

    class FakeProcessor:
        tokenizer = None

        def __call__(self, audio, *, sampling_rate, return_tensors):
            return SimpleNamespace(input_features=FakeFeatures())

        def get_decoder_prompt_ids(self, *, language, task):
            return [(language, task)]

        def batch_decode(self, predicted, *, skip_special_tokens):
            return ["private transcript"]

    class FakeModel:
        def __init__(self):
            self.calls = []

        def generate(self, features, **kwargs):
            self.calls.append(kwargs["forced_decoder_ids"])
            return ["tokens"]

    transcriber = object.__new__(WhisperTranscriber)
    transcriber.device = "cpu"
    transcriber.torch = SimpleNamespace(inference_mode=nullcontext)
    transcriber.processor = FakeProcessor()
    transcriber.model = FakeModel()
    transcriber._transcription_cache = {}

    assert transcriber.transcribe(audio_path, "zh") == "private transcript"
    initial_stat = audio_path.stat()
    initial_key = (
        str(audio_path.resolve()), initial_stat.st_size, initial_stat.st_mtime_ns, "zh"
    )
    assert transcriber.transcribe(audio_path, "zh") == "private transcript"
    assert initial_key in transcriber._transcription_cache
    assert len(load_calls) == 1
    assert len(transcriber.model.calls) == 1

    # 相同文件换语言或换内容时都要重新识别。
    transcriber.transcribe(audio_path, "ja")
    before_touch = audio_path.stat()
    audio_path.write_bytes(b"change")
    os.utime(audio_path, ns=(before_touch.st_atime_ns, before_touch.st_mtime_ns + 1_000_000_000))
    transcriber.transcribe(audio_path, "zh")
    audio_path.write_bytes(b"changed source")
    transcriber.transcribe(audio_path, "zh")
    assert len(load_calls) == 4
    assert len(transcriber.model.calls) == 4

    next_run = object.__new__(WhisperTranscriber)
    next_run.device = transcriber.device
    next_run.torch = transcriber.torch
    next_run.processor = transcriber.processor
    next_run.model = FakeModel()
    next_run._transcription_cache = {}
    next_run.transcribe(audio_path, "zh")
    assert len(next_run.model.calls) == 1
