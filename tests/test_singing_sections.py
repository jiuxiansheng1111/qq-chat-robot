import wave
from itertools import pairwise

import numpy as np
import pytest

from app.services.singing_sections import plan_paused_sections
from app.services.singing_sources import LyricLine


def pcm(path, levels, sample_rate=1000):
    samples = (np.asarray(levels) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def test_no_silence_uses_last_lyric_boundary_and_adds_pause(tmp_path):
    vocal = tmp_path / 'continuous.wav'
    pcm(vocal, np.full(20_000, .2))
    lines = tuple(LyricLine(float(time), '歌词') for time in (0, 3, 6, 9, 12, 15, 18))
    sections = plan_paused_sections(vocal, lines, 20, 7)
    assert sections[0].end_seconds == 6
    assert sections[0].pause_seconds == .18
    assert sections[-1].end_seconds == 20
    assert all(a.end_seconds == b.start_seconds for a, b in pairwise(sections))
    assert all(section.duration_seconds + section.pause_seconds <= 7 for section in sections)


def test_natural_pause_does_not_add_silence(tmp_path):
    vocal = tmp_path / 'paused.wav'
    levels = np.full(12_000, .2)
    levels[5000:6000] = 0
    pcm(vocal, levels)
    sections = plan_paused_sections(vocal, (), 12, 7)
    assert 5 <= sections[0].end_seconds <= 6
    assert sections[0].pause_seconds == 0


def test_no_lyrics_and_no_pause_reports_missing_boundary(tmp_path):
    vocal = tmp_path / 'continuous.wav'
    pcm(vocal, np.full(12_000, .2))
    with pytest.raises(ValueError, match='逐句歌词'):
        plan_paused_sections(vocal, (), 12, 7)
