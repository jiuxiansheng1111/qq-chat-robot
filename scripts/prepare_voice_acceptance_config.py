"""Prepare a GPT-SoVITS TTS config for an isolated local acceptance sidecar.

Pass the printed output path to ``api_v2.py -c`` for a non-production port.
GPT-SoVITS persists /set_sovits_weights changes into that selected config, so
this tool deliberately refuses to overwrite the source/live config.  Supplying
``--sovits-weights`` writes that existing weight path only into the new copy.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


def _custom_block_bounds(lines: list[str]) -> tuple[int, int]:
    custom_indices = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"custom:\s*(?:#.*)?(?:\r?\n)?", line)
    ]
    if len(custom_indices) != 1:
        raise ValueError("source config must contain exactly one top-level custom block")

    start = custom_indices[0]
    for index in range(start + 1, len(lines)):
        # A non-indented, non-comment line begins the next top-level YAML block.
        if re.match(r"^[^\s#]", lines[index]):
            return start + 1, index
    return start + 1, len(lines)


def _custom_scalar_lines(lines: list[str], field: str) -> list[int]:
    start, end = _custom_block_bounds(lines)
    pattern = re.compile(rf"^(?P<indent>[ \t]+){re.escape(field)}:\s*.*(?:\r?\n)?$")
    return [index for index in range(start, end) if pattern.fullmatch(lines[index])]


def source_custom_sovits_weights(source: Path) -> str:
    """Return the source custom SoVITS value for an explicit inheritance warning."""
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = _custom_scalar_lines(lines, "vits_weights_path")
    if len(matches) != 1:
        raise ValueError("source config custom.vits_weights_path must appear exactly once")
    return lines[matches[0]].split(":", 1)[1].strip()


def _yaml_double_quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _with_custom_sovits_weights(config_text: str, weights: Path) -> str:
    """Replace only the inline custom.vits_weights_path scalar, fail-closed."""
    lines = config_text.splitlines(keepends=True)
    matches = _custom_scalar_lines(lines, "vits_weights_path")
    if len(matches) != 1:
        raise ValueError("source config custom.vits_weights_path must appear exactly once")

    index = matches[0]
    match = re.fullmatch(
        r"(?P<indent>[ \t]+)vits_weights_path:\s*.*(?P<newline>\r?\n)?",
        lines[index],
    )
    if match is None:
        raise ValueError("source config custom.vits_weights_path must be an inline scalar")
    newline = match.group("newline") or ""
    indentation = match.group("indent")
    lines[index] = f"{indentation}vits_weights_path: {_yaml_double_quoted(str(weights))}{newline}"

    # YAML emitters can wrap a quoted Windows path onto indented continuation
    # lines. They belong to the replaced scalar, not to the next custom key.
    continuation_end = index + 1
    while continuation_end < len(lines):
        continuation = lines[continuation_end]
        if not continuation.strip():
            break
        continuation_indent = len(continuation) - len(continuation.lstrip(" \t"))
        if continuation_indent <= len(indentation):
            break
        continuation_end += 1
    del lines[index + 1 : continuation_end]
    return "".join(lines)


def copy_isolated_tts_config(
    source: Path,
    output: Path,
    sovits_weights: Path | None = None,
) -> Path:
    source = source.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if source == output:
        raise ValueError("acceptance TTS config must not overwrite the source/live config")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing acceptance config: {output}")

    config_text: str | None = None
    if sovits_weights is not None:
        sovits_weights = sovits_weights.expanduser().resolve(strict=True)
        if not sovits_weights.is_file():
            raise ValueError(f"SoVITS weights must be a file: {sovits_weights}")
        config_text = _with_custom_sovits_weights(
            source.read_text(encoding="utf-8"), sovits_weights
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if config_text is None:
        shutil.copyfile(source, output)
    else:
        output.write_text(config_text, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Existing source tts_infer.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New acceptance-only config path; it must not already exist",
    )
    parser.add_argument(
        "--sovits-weights",
        type=Path,
        help="Existing .pth to write to the acceptance copy's custom.vits_weights_path",
    )
    args = parser.parse_args()
    try:
        source = args.source.expanduser().resolve(strict=True)
        inherited_weights = (
            source_custom_sovits_weights(source) if args.sovits_weights is None else None
        )
        output = copy_isolated_tts_config(source, args.output, args.sovits_weights)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    print(output)
    if args.sovits_weights is None:
        print(
            "WARNING: no --sovits-weights was supplied; the acceptance config inherits "
            f"the source custom.vits_weights_path: {inherited_weights}",
            file=sys.stderr,
        )
    else:
        print(
            f"Acceptance-only custom.vits_weights_path: {args.sovits_weights.expanduser().resolve()}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
