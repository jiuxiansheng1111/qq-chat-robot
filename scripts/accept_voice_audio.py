"""Blindly score generated voice samples with a local FunASR SenseVoice model.

This is an opt-in acceptance helper. It reads WAV files and a small manifest,
loads an already-downloaded ASR model, and writes its report to stdout. It does
not contact a service, generate audio, or modify the project data.

Manifest format (UTF-8 JSON):

    [
      {"file": "acceptance_zh.wav", "language": "zh", "text": "你好呀，今天过得怎么样？"},
      {
        "file": "acceptance_ja.wav",
        "language": "ja",
        "text": "今日は嬉しいです。",
        "accepted_transcriptions": ["今日はうれしいです。"]
      }
    ]

``accepted_transcriptions`` is optional and permitted only for Japanese cases.
Each item is a manually reviewed, equivalent orthographic rendering of the
same utterance; it is never inferred from the ASR output.  The report always
retains the original reference and CER, and separately identifies the chosen
gate reference and gate CER.

Run it with the GPT-SoVITS Python environment, which already contains FunASR:

    .venv_cpu\\Scripts\\python.exe scripts/accept_voice_audio.py \\
      --model-path C:\\Users\\...\\.cache\\modelscope\\hub\\iic\\SenseVoiceSmall
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import unicodedata
import wave
from pathlib import Path
from typing import Any

DEFAULT_CASES = [
    {
        "file": "acceptance_zh.wav",
        "language": "zh",
        "text": "你好呀，今天过得怎么样？",
    },
    {
        "file": "acceptance_en.wav",
        "language": "en",
        "text": "Hello, how are you today?",
    },
    {
        "file": "acceptance_ja.wav",
        "language": "ja",
        "text": "こんにちは、元気ですか？",
    },
]
LANGUAGE_TAG = re.compile(r"<\|([^|]+)\|>")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
LANGUAGE_GATES = {"zh": "zh_asr_gate", "en": "en_asr_gate", "ja": "ja_asr_gate"}


def edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_char in enumerate(reference, start=1):
        current = [row]
        for column, hyp_char in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[-1]


def normalize_characters(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def normalize_words(text: str) -> list[str]:
    return WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def accepted_transcriptions(case: dict[str, Any]) -> list[str]:
    """Return explicit Japanese spelling alternatives, rejecting hidden relaxations."""
    alternatives = case.get("accepted_transcriptions", [])
    if alternatives is None:
        return []
    if case["language"].casefold() != "ja":
        if alternatives:
            raise ValueError("accepted_transcriptions is supported only for Japanese cases")
        return []
    if not isinstance(alternatives, list):
        raise TypeError("accepted_transcriptions must be a JSON array of non-empty strings")
    if any(not isinstance(item, str) or not item.strip() for item in alternatives):
        raise ValueError("accepted_transcriptions must contain only non-empty strings")
    if len(set(alternatives)) != len(alternatives):
        raise ValueError("accepted_transcriptions must not contain duplicates")
    return alternatives


def gate_character_score(
    reference: str,
    recognized: str,
    alternatives: list[str],
) -> tuple[str, int, int, float]:
    """Select the best score from an explicit reference plus its listed alternatives."""
    candidates = [reference, *alternatives]
    scores = []
    hypothesis_chars = normalize_characters(recognized)
    for position, candidate in enumerate(candidates):
        candidate_chars = normalize_characters(candidate)
        errors = edit_distance(candidate_chars, hypothesis_chars)
        rate = errors / max(1, len(candidate_chars))
        scores.append((rate, errors, position, candidate, len(candidate_chars)))
    rate, errors, _position, candidate, characters = min(scores)
    return candidate, errors, characters, rate


def audio_metrics(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as audio:
        frame_count = audio.getnframes()
        channels = audio.getnchannels()
        sample_rate = audio.getframerate()
        sample_width = audio.getsampwidth()
        raw = audio.readframes(frame_count)

    result: dict[str, Any] = {
        "seconds": round(frame_count / sample_rate, 3) if sample_rate else None,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
    }
    if sample_width != 2 or not raw:
        return result

    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw[: len(raw) // 2 * 2])
    peak = max((abs(sample) for sample in samples), default=0)
    rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples)))
    samples_per_frame = max(1, round(sample_rate * 0.02)) * channels
    frame_rms = []
    for offset in range(0, len(samples), samples_per_frame):
        chunk = samples[offset : offset + samples_per_frame]
        frame_rms.append(math.sqrt(sum(sample * sample for sample in chunk) / max(1, len(chunk))))
    silence_limit = max(peak * 0.01, 4)
    silence_ratio = sum(value < silence_limit for value in frame_rms) / max(1, len(frame_rms))
    very_low_energy_ratio = sum(value < 100 for value in frame_rms) / max(1, len(frame_rms))
    near_digital_silence_ratio = sum(value < 20 for value in frame_rms) / max(1, len(frame_rms))
    result.update(
        {
            "peak_pcm16": peak,
            "rms_pcm16": round(rms, 1),
            "near_silence_20ms_pct": round(silence_ratio * 100, 1),
            "very_low_energy_20ms_pct_below_100_pcm16": round(very_low_energy_ratio * 100, 1),
            "near_digital_silence_20ms_pct_below_20_pcm16": round(near_digital_silence_ratio * 100, 1),
        }
    )
    return result


def run_case(model: Any, audio_path: Path, case: dict[str, Any]) -> dict[str, Any]:
    generated = model.generate(input=str(audio_path))
    raw_text = str(generated[0].get("text", "")) if generated else ""
    tags = [tag.casefold() for tag in LANGUAGE_TAG.findall(raw_text)]
    language_tags = [tag for tag in tags if tag in {"zh", "en", "ja", "ko", "yue"}]
    recognized = LANGUAGE_TAG.sub("", raw_text).strip()

    expected = case["text"]
    expected_language = case["language"].casefold()
    reference_chars = normalize_characters(expected)
    hypothesis_chars = normalize_characters(recognized)
    char_errors = edit_distance(reference_chars, hypothesis_chars)
    char_rate = char_errors / max(1, len(reference_chars))
    alternatives = accepted_transcriptions(case)
    gate_reference, gate_errors, gate_characters, gate_rate = gate_character_score(
        expected,
        recognized,
        alternatives,
    )

    row: dict[str, Any] = {
        "file": audio_path.name,
        "file_path": str(audio_path.resolve()),
        "expected_language": expected_language,
        "detected_language": language_tags[0] if language_tags else None,
        "language_tags": tags,
        "reference": expected,
        "recognized": recognized,
        "raw_asr_text": raw_text,
        "character_errors": char_errors,
        "reference_characters": len(reference_chars),
        "cer": round(char_rate, 4),
        "accepted_transcriptions": alternatives,
        "gate_reference": gate_reference,
        "matched_accepted_transcription": (
            gate_reference if gate_reference != expected else None
        ),
        "gate_character_errors": gate_errors,
        "gate_reference_characters": gate_characters,
        "gate_cer": round(gate_rate, 4),
        "language_match": bool(language_tags and language_tags[0] == expected_language),
        "audio": audio_metrics(audio_path),
    }
    if expected_language == "en":
        reference_words = normalize_words(expected)
        hypothesis_words = normalize_words(recognized)
        word_errors = word_distance(reference_words, hypothesis_words)
        row.update(
            {
                "word_errors": word_errors,
                "reference_words": len(reference_words),
                "wer": round(word_errors / max(1, len(reference_words)), 4),
            }
        )
    return row


def word_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_word in enumerate(reference, start=1):
        current = [row]
        for column, hyp_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (ref_word != hyp_word),
                )
            )
        previous = current
    return previous[-1]


def overall_gate(summary: dict[str, Any]) -> dict[str, Any]:
    """Require all three languages for automation-grade acceptance."""
    missing = [language for language, gate in LANGUAGE_GATES.items() if gate not in summary]
    failed = [
        language
        for language, gate in LANGUAGE_GATES.items()
        if summary.get(gate) is not True
    ]
    return {
        "required_languages": list(LANGUAGE_GATES),
        "missing_languages": missing,
        "failed_languages": failed,
        "overall_asr_gate": not missing and not failed,
    }


def apply_language_gates(
    summary: dict[str, Any], report: list[dict[str, Any]], thresholds: dict[str, float]
) -> None:
    """Populate language gates; only Japanese may use explicit gate CER alternatives."""
    zh_rows = [row for row in report if row["expected_language"] == "zh"]
    if zh_rows:
        zh_ref_chars = sum(row["reference_characters"] for row in zh_rows)
        zh_errors = sum(row["character_errors"] for row in zh_rows)
        summary["zh_aggregate_cer"] = round(zh_errors / max(1, zh_ref_chars), 4)
        summary["zh_all_languages_match"] = all(row["language_match"] for row in zh_rows)
        summary["zh_asr_gate"] = bool(
            summary["zh_aggregate_cer"] <= thresholds["zh_aggregate_cer_max"]
            and summary["zh_all_languages_match"]
            and all(row["cer"] <= thresholds["zh_single_cer_max"] for row in zh_rows)
        )
    en_rows = [row for row in report if row["expected_language"] == "en"]
    if en_rows:
        en_words = sum(row["reference_words"] for row in en_rows)
        en_errors = sum(row["word_errors"] for row in en_rows)
        summary["en_aggregate_wer"] = round(en_errors / max(1, en_words), 4)
        summary["en_asr_gate"] = bool(
            summary["en_aggregate_wer"] <= thresholds["en_wer_max"]
            and all(row["language_match"] for row in en_rows)
            and all(row["wer"] <= thresholds["en_single_wer_max"] for row in en_rows)
        )
    ja_rows = [row for row in report if row["expected_language"] == "ja"]
    if ja_rows:
        ja_ref_chars = sum(row["reference_characters"] for row in ja_rows)
        ja_errors = sum(row["character_errors"] for row in ja_rows)
        ja_gate_chars = sum(row["gate_reference_characters"] for row in ja_rows)
        ja_gate_errors = sum(row["gate_character_errors"] for row in ja_rows)
        summary["ja_aggregate_cer"] = round(ja_errors / max(1, ja_ref_chars), 4)
        summary["ja_aggregate_gate_cer"] = round(
            ja_gate_errors / max(1, ja_gate_chars), 4
        )
        summary["ja_asr_gate"] = bool(
            summary["ja_aggregate_gate_cer"] <= thresholds["ja_cer_max"]
            and all(row["language_match"] for row in ja_rows)
            and all(row["gate_cer"] <= thresholds["ja_cer_max"] for row in ja_rows)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True, help="Local SenseVoiceSmall model directory")
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "logs",
        help="Directory containing manifest WAV files",
    )
    parser.add_argument("--manifest", type=Path, help="UTF-8 JSON case manifest; defaults to three baseline cases")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional UTF-8 JSON report path; WAV inputs are left unchanged",
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Additional case as FILE|LANGUAGE|EXPECTED_TEXT; may be repeated",
    )
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    parser.add_argument("--max-zh-cer", type=float, default=0.05)
    parser.add_argument("--max-single-zh-cer", type=float, default=0.10)
    parser.add_argument("--max-en-wer", type=float, default=0.10)
    parser.add_argument(
        "--max-single-en-wer",
        type=float,
        default=0.10,
        help="Maximum WER for each English sample; defaults to the aggregate limit",
    )
    parser.add_argument("--max-ja-cer", type=float, default=0.05)
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Exit nonzero unless zh, en, and ja are all present and pass their ASR gates",
    )
    args = parser.parse_args()

    # Keep caller-provided drive aliases intact. ModelScope's Windows path
    # handling can break when a checkpoint lives below a CJK username.
    model_path = Path(args.model_path).expanduser()
    if not model_path.is_dir():
        parser.error(f"Local ASR model directory does not exist: {model_path}")
    if args.manifest:
        cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    elif args.case:
        cases = []
    else:
        cases = list(DEFAULT_CASES)
    if args.case:
        for item in args.case:
            parts = item.split("|", 2)
            if len(parts) != 3 or parts[1].casefold() not in {"zh", "en", "ja"}:
                parser.error("--case must be FILE|zh|TEXT, FILE|en|TEXT, or FILE|ja|TEXT")
            cases.append({"file": parts[0], "language": parts[1].casefold(), "text": parts[2]})

    from funasr import AutoModel

    model = AutoModel(model=str(model_path), device=args.device, disable_update=True)
    report = []
    for case in cases:
        audio_path = (args.audio_dir / case["file"]).resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        report.append(run_case(model, audio_path, case))

    summary: dict[str, Any] = {"samples": len(report), "items": report}
    summary["thresholds"] = {
        "zh_aggregate_cer_max": args.max_zh_cer,
        "zh_single_cer_max": args.max_single_zh_cer,
        "en_wer_max": args.max_en_wer,
        "en_single_wer_max": args.max_single_en_wer,
        "ja_cer_max": args.max_ja_cer,
    }
    apply_language_gates(summary, report, summary["thresholds"])
    summary["human_blind_listen_still_required"] = True
    if args.fail_on_gate:
        summary.update(overall_gate(summary))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    # ASCII-only output keeps transcript characters lossless in legacy
    # Windows consoles; readers can still decode the JSON escapes.
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 1 if args.fail_on_gate and not summary["overall_asr_gate"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
