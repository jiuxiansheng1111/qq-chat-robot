from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services import video_audio


def test_asr_keys_only_go_to_their_existing_official_endpoint():
    config = {
        "provider_sources": [
            {"id": "qqchat-groq", "api_base": "https://evil.example/openai/v1", "key": ["secret"]},
            {
                "id": "qqchat-zhipu",
                "api_base": "https://open.bigmodel.cn/api/paas/v4",
                "key": ["official-key"],
            },
        ]
    }
    assert video_audio._providers(None, config) == []
    result = video_audio._providers(None, config, allow_paid=True)
    assert len(result) == 1
    assert result[0].name == "zhipu"
    assert "official-key" not in repr(result[0])
    assert "evil.example" not in result[0].endpoint


def test_free_mode_never_selects_zhipu_asr_from_config_or_settings():
    config = {
        "provider_sources": [
            {
                "id": "qqchat-groq",
                "api_base": "https://api.groq.com/openai/v1",
                "key": ["free-key"],
            },
            {
                "id": "qqchat-zhipu",
                "api_base": "https://open.bigmodel.cn/api/paas/v4",
                "key": ["paid-key"],
            },
        ]
    }
    settings = Settings(
        _env_file=None,
        groq_api_key="free-env-key",
        llm_api_key="dummy-api-key-003",
        llm_base_url="https://open.bigmodel.cn/api/paas/v4",
    )
    assert [item.name for item in video_audio._providers(settings, config)] == ["groq"]
    assert [item.name for item in video_audio._providers(settings, config, allow_paid=True)] == [
        "groq",
        "zhipu",
    ]


@pytest.mark.parametrize("flag", [False, None, "false", "true", 1])
def test_paid_asr_requires_explicit_boolean_true(flag):
    config = {
        "provider_sources": [
            {
                "id": "qqchat-zhipu",
                "api_base": "https://open.bigmodel.cn/api/paas/v4",
                "key": ["paid-key"],
            }
        ]
    }
    assert video_audio._providers(None, config, allow_paid=flag) == []


async def test_text_paths_or_untrusted_roots_cannot_start_media_processing(tmp_path, monkeypatch):
    command = AsyncMock()
    monkeypatch.setattr(video_audio, "_command", command)
    path = tmp_path / "fake-video.mp4"
    path.write_bytes(b"private file")

    missing_root = await video_audio.transcribe_video_audio(path)
    outside_root = await video_audio.transcribe_video_audio(path, trusted_root=tmp_path / "other")
    text_path = await video_audio.transcribe_video_audio(str(path), trusted_root=tmp_path)

    assert {missing_root.status, outside_root.status, text_path.status} == {"invalid"}
    assert not command.called


def test_embedded_subtitle_parser_preserves_timestamps_and_marks_cutoff():
    content = """1
00:00:01,000 --> 00:00:03,000
<i>这是视频自己的字幕。</i>

2
00:05:01,000 --> 00:05:03,000
超出处理范围的正文
"""
    text, clipped = video_audio._subtitle_text(content)
    assert text == "[00:01] 这是视频自己的字幕。"
    assert clipped


def test_groq_silence_segments_are_not_used_as_spoken_evidence():
    text = video_audio._groq_text(
        {
            "text": "mixed",
            "segments": [
                {"start": 0, "text": "欢迎订阅", "no_speech_prob": 0.95},
                {"start": 2, "text": "真实语句", "no_speech_prob": 0.1},
                {"start": 5, "text": None, "no_speech_prob": 0.1},
            ],
        }
    )
    assert text == "[00:02] 真实语句"


async def test_zhipu_chunks_stay_under_official_limit_and_report_long_video_coverage(monkeypatch):
    durations = []

    async def request(_provider, payload, _settings):
        with wave.open(io.BytesIO(payload), "rb") as sound:
            durations.append(sound.getnframes() / sound.getframerate())
        return {"text": "识别到的片段"}

    monkeypatch.setattr(video_audio, "_request_asr", request)
    frames = b"\x01\x01" * 16_000 * 300
    provider = video_audio._ASRProvider(
        "zhipu", "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions", "glm-asr-2512", "key"
    )

    result = await video_audio._transcribe(provider, frames, 300, 600, None)

    assert len(durations) == 6
    assert max(durations) == 28
    assert result.processed_seconds == 168
    assert result.duration_seconds == 600
    assert result.truncated and result.status == "partial"
    assert "[02:20]" in result.transcript


async def test_total_asr_failure_is_not_reported_as_no_speech(monkeypatch):
    monkeypatch.setattr(
        video_audio, "_request_asr", AsyncMock(side_effect=httpx.ReadTimeout("no data"))
    )
    provider = video_audio._ASRProvider(
        "zhipu", "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions", "glm-asr-2512", "key"
    )
    with pytest.raises(video_audio._VideoAudioError):
        await video_audio._transcribe(provider, b"\x01\x01" * 16_000, 1, 1, None)


def _ffmpeg() -> Path:
    suffix = ".exe" if video_audio.os.name == "nt" else ""
    path = Path(__file__).resolve().parents[1] / "data/singing/runtime/bin" / ("ffmpeg" + suffix)
    if not path.is_file():
        pytest.skip("需要本机已安装 ffmpeg")
    return path


async def _video(tmp_path: Path, *, subtitles: bool, audio: bool) -> Path:
    ffmpeg = _ffmpeg()
    destination = tmp_path / "real-video.mp4"
    args = [ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=teal:s=96x64:r=1:d=2"]
    if audio:
        args.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2"])
    if subtitles:
        caption = tmp_path / "caption.srt"
        caption.write_text(
            "1\n00:00:00,100 --> 00:00:01,800\n视频内嵌字幕测试。\n", encoding="utf-8"
        )
        args.extend(["-i", caption, "-c:s", "mov_text"])
    args.extend(["-t", "2", "-c:v", "mpeg4", "-c:a", "aac", "-threads", "1", "-y", destination])
    await video_audio._command(args)
    return destination


async def test_real_video_embedded_subtitles_work_without_asr_key(tmp_path, monkeypatch):
    video = await _video(tmp_path, subtitles=True, audio=True)
    request = AsyncMock(side_effect=AssertionError("字幕优先时不能调用语音API"))
    monkeypatch.setattr(video_audio, "_request_asr", request)
    result = await video_audio.transcribe_video_audio(video, trusted_root=tmp_path)

    assert result.source == "embedded_subtitles"
    assert "视频内嵌字幕测试" in result.transcript
    assert "尚未对照音轨" in result.detail
    assert result.status == "ok"
    assert not request.called


async def test_real_silent_video_has_no_audio_evidence(tmp_path):
    video = await _video(tmp_path, subtitles=False, audio=False)
    result = await video_audio.transcribe_video_audio(video, trusted_root=tmp_path)
    assert result.status == "no_audio"
    assert not result.transcript


async def test_real_audio_extraction_uses_groq_then_zhipu_fallback(tmp_path, monkeypatch):
    video = await _video(tmp_path, subtitles=False, audio=True)
    providers = [
        video_audio._ASRProvider(
            "groq",
            "https://api.groq.com/openai/v1/audio/transcriptions",
            "whisper-large-v3-turbo",
            "groq-key",
        ),
        video_audio._ASRProvider(
            "zhipu",
            "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions",
            "glm-asr-2512",
            "zhipu-key",
        ),
    ]
    monkeypatch.setattr(video_audio, "_providers", lambda *_args, **_kwargs: providers)

    async def request(provider, payload, _settings):
        if provider.name == "groq":
            raise httpx.ReadTimeout("provider unavailable")
        with wave.open(io.BytesIO(payload), "rb") as sound:
            assert sound.getframerate() == 16_000
            assert sound.getnchannels() == 1
        return {"text": "备用转写结果"}

    monkeypatch.setattr(video_audio, "_request_asr", request)
    result = await video_audio.transcribe_video_audio(
        video, Settings(_env_file=None), trusted_root=tmp_path, allow_paid=True
    )

    assert result.source == "zhipu_asr", result.detail
    assert result.transcript.endswith("备用转写结果")
    assert result.status == "ok"


async def test_free_groq_failure_never_falls_back_to_paid_asr(tmp_path, monkeypatch):
    path = await _video(tmp_path, subtitles=False, audio=True)
    request = AsyncMock(side_effect=httpx.ReadTimeout("free quota exhausted"))
    monkeypatch.setattr(video_audio, "_request_asr", request)
    config = {
        "provider_sources": [
            {
                "id": "qqchat-groq",
                "api_base": "https://api.groq.com/openai/v1",
                "key": ["free-key"],
            },
            {
                "id": "qqchat-zhipu",
                "api_base": "https://open.bigmodel.cn/api/paas/v4",
                "key": ["paid-key"],
            },
        ]
    }
    result = await video_audio.transcribe_video_audio(
        path, trusted_root=tmp_path, provider_config=config
    )
    assert result.status == "failed" and not result.transcript
    assert request.call_count == 1
    assert request.call_args.args[0].name == "groq"


async def test_only_paid_key_in_free_mode_returns_unavailable_without_api(tmp_path, monkeypatch):
    path = await _video(tmp_path, subtitles=False, audio=True)
    request = AsyncMock(side_effect=AssertionError("a paid ASR endpoint must not be called"))
    monkeypatch.setattr(video_audio, "_request_asr", request)
    config = {
        "provider_sources": [
            {
                "id": "qqchat-zhipu",
                "api_base": "https://open.bigmodel.cn/api/paas/v4",
                "key": ["paid-key"],
            }
        ]
    }
    result = await video_audio.transcribe_video_audio(
        path, trusted_root=tmp_path, provider_config=config
    )
    assert result.status == "unavailable" and "收费后备已关闭" in result.detail
    assert not request.called
