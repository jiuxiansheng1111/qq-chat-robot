import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_rvc_model import conversion_sections, convert_sections


@pytest.mark.parametrize("seconds", [20.01, 108.588, 114.996, 120])
def test_long_rvc_sections_preserve_every_source_sample_and_seams(tmp_path, monkeypatch, seconds):
    rate = 1000
    frames = round(seconds * rate)
    source = np.sin(np.arange(frames, dtype=np.float32) * 0.03)
    plan = conversion_sections(seconds)
    lengths = []
    fragments = {}

    def write(path, fragment, fragment_rate, subtype):
        assert fragment_rate == rate and subtype == "FLOAT"
        fragments[path] = fragment.copy()

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=write))

    def convert(path, expected_frames):
        fragment = fragments[path]
        assert len(fragment) == expected_frames
        lengths.append(len(fragment) / rate)
        return fragment

    output = convert_sections(source, rate, plan, convert, tmp_path / "work")
    assert len(plan) > 1
    assert max(lengths) <= 20.8
    assert output.shape == source.shape
    np.testing.assert_allclose(output, source, atol=2e-7)


@pytest.mark.parametrize("seconds", [0, 121, float("nan"), float("inf")])
def test_invalid_rvc_section_duration_is_rejected(seconds):
    with pytest.raises(ValueError):
        conversion_sections(seconds)
