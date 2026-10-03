import pytest

from scripts.run_singing_model import _parse_args


def _base_args(*extra):
    return [
        "--seed-root", "seed",
        "--ffmpeg", "ffmpeg.exe",
        "--source", "source.wav",
        "--target", "target.wav",
        "--output", "output.wav",
        *extra,
    ]


def test_semitone_shift_defaults_to_zero_and_accepts_bounded_values():
    assert _parse_args(_base_args()).semitone_shift == 0
    assert _parse_args(_base_args("--semitone-shift", "3")).semitone_shift == 3
    assert _parse_args(_base_args("--semitone-shift", "-6")).semitone_shift == -6


@pytest.mark.parametrize("shift", ["-7", "7"])
def test_semitone_shift_rejects_values_outside_experimental_bound(shift):
    with pytest.raises(SystemExit):
        _parse_args(_base_args("--semitone-shift", shift))
