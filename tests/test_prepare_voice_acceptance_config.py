import sys
from pathlib import Path

import pytest

from scripts.prepare_voice_acceptance_config import (
    _with_custom_sovits_weights,
    copy_isolated_tts_config,
    main,
)


def test_acceptance_config_is_a_separate_byte_for_byte_copy(tmp_path: Path):
    source = tmp_path / "tts_infer.yaml"
    output = tmp_path / "acceptance" / "tts_infer.acceptance.yaml"
    source.write_text("custom:\n  sovits_path: base.pth\n", encoding="utf-8")

    assert copy_isolated_tts_config(source, output) == output.resolve()
    assert output.read_bytes() == source.read_bytes()


def test_acceptance_config_never_overwrites_live_or_existing_config(tmp_path: Path):
    source = tmp_path / "tts_infer.yaml"
    source.write_text("custom: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        copy_isolated_tts_config(source, source)
    existing = tmp_path / "existing.yaml"
    existing.write_text("do not replace\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        copy_isolated_tts_config(source, existing)


def test_acceptance_config_can_set_only_its_custom_sovits_weights(tmp_path: Path):
    source = tmp_path / "tts_infer.yaml"
    source_text = (
        "custom:\n"
        "  t2s_weights_path: original-gpt.ckpt\n"
        "  vits_weights_path: original-sovits.pth\n"
        "v2:\n"
        "  vits_weights_path: pretrained.pth\n"
    )
    source.write_text(source_text, encoding="utf-8")
    weights = tmp_path / "weights" / "candidate.pth"
    weights.parent.mkdir()
    weights.write_bytes(b"candidate")
    output = tmp_path / "acceptance.yaml"

    copy_isolated_tts_config(source, output, weights)

    assert source.read_text(encoding="utf-8") == source_text
    expected = str(weights.resolve()).replace("\\", "\\\\")
    output_text = output.read_text(encoding="utf-8")
    assert f'  vits_weights_path: "{expected}"' in output_text
    assert "  vits_weights_path: pretrained.pth" in output_text


def test_acceptance_config_rejects_missing_sovits_weights_without_writing(tmp_path: Path):
    source = tmp_path / "tts_infer.yaml"
    source.write_text("custom:\n  vits_weights_path: original.pth\n", encoding="utf-8")
    output = tmp_path / "acceptance.yaml"

    with pytest.raises(FileNotFoundError):
        copy_isolated_tts_config(source, output, tmp_path / "missing.pth")

    assert not output.exists()


def test_custom_sovits_replacement_removes_wrapped_scalar_continuation():
    config = (
        "custom:\n"
        '  vits_weights_path: "C:\\\\long\\\\\n'
        '    wrapped\\\\model.pth"\n'
        "v1:\n"
        "  vits_weights_path: untouched.pth\n"
    )

    result = _with_custom_sovits_weights(config, Path(r"C:\\accepted\\candidate.pth"))

    assert "wrapped" not in result
    assert '  vits_weights_path: "C:\\\\accepted\\\\candidate.pth"' in result
    assert "  vits_weights_path: untouched.pth" in result


def test_cli_warns_when_it_inherits_the_source_sovits_weight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    source = tmp_path / "tts_infer.yaml"
    source.write_text("custom:\n  vits_weights_path: source-model.pth\n", encoding="utf-8")
    output = tmp_path / "acceptance.yaml"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_voice_acceptance_config.py",
            "--source",
            str(source),
            "--output",
            str(output),
        ],
    )

    assert main() == 0

    captured = capsys.readouterr()
    assert str(output.resolve()) in captured.out
    assert "inherits the source custom.vits_weights_path: source-model.pth" in captured.err
