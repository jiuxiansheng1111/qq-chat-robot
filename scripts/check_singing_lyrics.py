"""用 Whisper 对照原唱、转换人声和歌词。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_ROOT = (
    PROJECT_ROOT / "data" / "singing" / "runtime" / "seed-vc" / "checkpoints"
    / "hf_cache" / "models--openai--whisper-small" / "snapshots"
)
LANGUAGES = {"zh": "chinese", "ja": "japanese", "en": "english"}
LRC_TIME = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
LRC_METADATA = re.compile(r"\[[a-zA-Z]{1,3}:.*?\]")


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: str) -> str: ...


def normalize_text(text: str) -> str:
    """统一字符格式并去掉标点和空白。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, reference_char in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_char in enumerate(hypothesis, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (reference_char != hypothesis_char),
            ))
        previous = current
    return previous[-1]


def read_lrc(path: Path) -> str:
    """读歌词并去掉时间戳和 LRC 元数据。"""
    text = path.read_text(encoding="utf-8-sig")
    lines = []
    for line in text.splitlines():
        lyric = LRC_TIME.sub("", line)
        lyric = LRC_METADATA.sub("", lyric)
        if lyric.strip():
            lines.append(lyric)
    result = "\n".join(lines)
    if not normalize_text(result):
        raise ValueError("LRC has no lyric characters")
    return result


def _cer(reference: str, hypothesis: str) -> dict[str, float | int | None]:
    reference_chars = normalize_text(reference)
    hypothesis_chars = normalize_text(hypothesis)
    errors = edit_distance(reference_chars, hypothesis_chars)
    return {
        "cer": errors / len(reference_chars) if reference_chars else None,
        "character_errors": errors,
        "reference_characters": len(reference_chars),
        "hypothesis_characters": len(hypothesis_chars),
    }


def compare_transcripts(
    source_transcript: str, converted_transcript: str, lyrics: str
) -> dict[str, Any]:
    """计算原唱、转换人声和歌词之间的 CER。"""
    return {
        "transcript_consistency": _cer(source_transcript, converted_transcript),
        "source_vs_lyrics": _cer(lyrics, source_transcript),
        "converted_vs_lyrics": _cer(lyrics, converted_transcript),
    }


def analyze_item(
    source_path: Path,
    converted_path: Path,
    lyrics_path: Path,
    language: str,
    transcriber: Transcriber,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """转写两段人声，只返回指标，不保留转写文本。"""
    if language not in LANGUAGES:
        raise ValueError("language must be zh, ja, or en")
    lyrics = read_lrc(lyrics_path)
    source_text = transcriber.transcribe(source_path, language)
    converted_text = transcriber.transcribe(converted_path, language)
    row: dict[str, Any] = {
        "status": "completed",
        "language": language,
        "asr_model": "openai/whisper-small",
        "diagnostic_only": True,
        "transcription_text_saved": False,
        "asr_note": "唱歌转写及日语表记差异会影响 CER；仅供检查，不作为通过门槛。",
        "source_audio_file": source_path.name,
        "converted_audio_file": converted_path.name,
        "lyrics_file": lyrics_path.name,
        **compare_transcripts(source_text, converted_text, lyrics),
    }
    if metadata:
        for key in ("profile_id", "song_id", "song_title", "song_artist"):
            if key in metadata:
                row[key] = metadata[key]
    return row


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _matrix_path(value: str | Path, matrix_root: Path) -> Path:
    path = Path(value)
    candidates = [path] if path.is_absolute() else [PROJECT_ROOT / path, matrix_root / path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_within(resolved, matrix_root) and resolved.is_file():
            return resolved
    raise FileNotFoundError("matrix input is missing or outside its run directory")


def _load_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JSON report is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("JSON report must be an object")
    return value


def collect_matrix_items(matrix_root: Path, phase: str) -> list[dict[str, Any]]:
    """按复验清单找到通过的样本和音频。"""
    if phase not in {"baseline", "candidate"}:
        raise ValueError("phase must be baseline or candidate")
    root = matrix_root.resolve(strict=True)
    phase_root = (root / phase).resolve(strict=True)
    if not _is_within(phase_root, root):
        raise ValueError("phase directory is outside the matrix run")
    manifest = _load_json(phase_root / "manifest.json")
    items = manifest.get("items")
    if not isinstance(items, dict):
        raise TypeError("phase manifest items must be an object")

    resolved_items: list[dict[str, Any]] = []
    for item_key, entry in sorted(items.items()):
        if not isinstance(entry, dict):
            continue
        profile_id, separator, key_language = str(item_key).partition(":")
        language = entry.get("language") or (key_language if separator else None)
        base: dict[str, Any] = {
            "profile_id": profile_id if separator else entry.get("profile_id"),
            "language": language,
            "status": "failed",
        }
        if entry.get("status") != "passed" or language not in LANGUAGES:
            base["error_type"] = "matrix_item_not_passed_or_language_unknown"
            resolved_items.append(base)
            continue
        try:
            report_path = _matrix_path(entry["report"], root)
            report = _load_json(report_path)
            if report.get("status") != "passed":
                raise ValueError("item report was not passed")
            base.update({
                "profile_id": report.get("profile_id", base["profile_id"]),
                "language": language,
                "song_id": report.get("song_id"),
                "source_path": _matrix_path(report["vocals"], root),
                "converted_path": _matrix_path(report["vocal_after"], root),
                "lyrics_path": _matrix_path(report["lyrics"], root),
                "status": "ready",
            })
        except (KeyError, OSError, TypeError, ValueError):
            base["error_type"] = "matrix_item_inputs_missing_or_invalid"
        resolved_items.append(base)
    return resolved_items


def run_matrix(
    matrix_root: Path,
    phase: str,
    transcriber: Transcriber,
) -> dict[str, Any]:
    root = matrix_root.resolve(strict=True)
    entries = collect_matrix_items(root, phase)
    rows: list[dict[str, Any]] = []
    for item in entries:
        if item["status"] != "ready":
            rows.append({key: value for key, value in item.items() if not key.endswith("_path")})
            continue
        metadata = {key: item.get(key) for key in ("profile_id", "song_id")}
        try:
            row = analyze_item(
                item["source_path"], item["converted_path"], item["lyrics_path"],
                item["language"], transcriber, metadata=metadata,
            )
            row["status"] = "completed"
        except Exception as exc:  # noqa: BLE001 - 每个样本独立记录，避免丢失整轮指标。
            row = {
                "status": "failed",
                "profile_id": item.get("profile_id"),
                "song_id": item.get("song_id"),
                "language": item.get("language"),
                "error_type": type(exc).__name__,
            }
        rows.append(row)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "completed":
            grouped[str(row["language"])].append(row)
    summary: dict[str, Any] = {}
    for language, group in sorted(grouped.items()):
        metrics = {}
        for key in ("transcript_consistency", "source_vs_lyrics", "converted_vs_lyrics"):
            values = [row[key]["cer"] for row in group if row[key]["cer"] is not None]
            metrics[key] = {
                "mean_cer": sum(values) / len(values) if values else None,
                "samples": len(values),
            }
        summary[language] = metrics
    failed_count = sum(row.get("status") != "completed" for row in rows)
    return {
        "schema_version": 1,
        "status": "completed_with_failures" if failed_count else "completed",
        "phase": phase,
        "diagnostic_only": True,
        "asr_model": "openai/whisper-small",
        "transcription_text_saved": False,
        "summary": {"completed": len(rows) - failed_count, "failed": failed_count},
        "summary_by_language": summary,
        "items": rows,
    }


def _find_model_snapshot(model_path: Path | None) -> Path:
    if model_path is not None:
        candidate = model_path.resolve(strict=True)
        if (candidate / "config.json").is_file() and any(
            (candidate / name).is_file() for name in ("model.safetensors", "pytorch_model.bin")
        ):
            return candidate
        raise FileNotFoundError("Whisper model config or weights are missing")
    candidates = sorted(
        path for path in DEFAULT_SNAPSHOT_ROOT.glob("*")
        if path.is_dir() and (path / "config.json").is_file()
        and any((path / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))
    )
    if not candidates:
        raise FileNotFoundError("local whisper-small model snapshot was not found")
    return candidates[-1]


class WhisperTranscriber:
    """加载 Whisper；本地缺词表时只补官方公开文件。"""

    def __init__(self, model_path: Path | None = None, device: str = "auto", offline: bool = False):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        snapshot = _find_model_snapshot(model_path)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        self.device = device
        self.torch = torch
        self._transcription_cache: dict[tuple[str, int, int, str], str] = {}
        try:
            self.processor = WhisperProcessor.from_pretrained(
                str(snapshot), local_files_only=True, token=False,
            )
        except (OSError, ValueError) as exc:
            if offline:
                raise RuntimeError("local Whisper tokenizer files are incomplete; offline mode is set") from exc
            self.processor = WhisperProcessor.from_pretrained(
                "openai/whisper-small", local_files_only=False, token=False,
            )
        self.model = WhisperForConditionalGeneration.from_pretrained(
            str(snapshot), local_files_only=True, token=False,
        ).to(device).eval()

    def transcribe(self, audio_path: Path, language: str) -> str:
        if language not in LANGUAGES:
            raise ValueError("language must be zh, ja, or en")
        resolved_path = Path(audio_path).resolve(strict=True)
        stat = resolved_path.stat()
        cache_key = (str(resolved_path), stat.st_size, stat.st_mtime_ns, language)
        cached = self._transcription_cache.get(cache_key)
        if cached is not None:
            return cached
        import librosa

        audio, _ = librosa.load(resolved_path, sr=16000, mono=True)
        if not len(audio):
            raise ValueError("audio is empty")
        if len(audio) > 30 * 16000:
            raise ValueError("audio exceeds Whisper's 30 second input window")
        inputs = self.processor(
            audio, sampling_rate=16000, return_tensors="pt", return_attention_mask=True,
        )
        features = inputs.input_features.to(self.device)
        prompt_builder = getattr(self.processor, "get_decoder_prompt_ids", None)
        if prompt_builder is None:
            prompt_builder = self.processor.tokenizer.get_decoder_prompt_ids
        forced_decoder_ids = prompt_builder(language=LANGUAGES[language], task="transcribe")
        with self.torch.inference_mode():
            predicted = self.model.generate(
                features,
                attention_mask=inputs.attention_mask.to(self.device),
                forced_decoder_ids=forced_decoder_ids,
                do_sample=False,
                num_beams=1,
                max_new_tokens=256,
            )
        transcription = str(self.processor.batch_decode(predicted, skip_special_tokens=True)[0])
        self._transcription_cache[cache_key] = transcription
        return transcription


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Whisper 检查翻唱咬字")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--matrix-root", type=Path, help="含 baseline/ 和 candidate/ 的复验目录")
    mode.add_argument("--source", type=Path, help="原唱人声音频")
    parser.add_argument("--phase", choices=("baseline", "candidate"))
    parser.add_argument("--converted", type=Path, help="转换后人声音频")
    parser.add_argument("--lyrics", type=Path, help="LRC 歌词")
    parser.add_argument("--language", choices=tuple(LANGUAGES))
    parser.add_argument("--output", type=Path, help="JSON 检查报告")
    parser.add_argument("--model-path", type=Path, help="本机 whisper-small 模型目录")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--offline", action="store_true", help="不联网补缺失的官方词表文件")
    args = parser.parse_args(argv)
    if args.matrix_root is not None:
        if args.phase is None or any(
            value is not None for value in (args.converted, args.lyrics, args.language)
        ):
            parser.error("--matrix-root requires --phase and does not accept single-item audio options")
    elif any(value is None for value in (args.converted, args.lyrics, args.language, args.output)):
        parser.error("single-item mode requires --source/--converted/--lyrics/--language/--output")
    elif args.phase is not None:
        parser.error("--phase is only used with --matrix-root")
    return args


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from app.services.singing_gpu import gpu_lock

        with gpu_lock():
            transcriber = WhisperTranscriber(args.model_path, args.device, args.offline)
            if args.matrix_root is not None:
                report = run_matrix(args.matrix_root, args.phase, transcriber)
                output = args.output or (args.matrix_root / args.phase / "lyrics_qa.json")
            else:
                report = analyze_item(
                    args.source, args.converted, args.lyrics, args.language, transcriber,
                )
                report.update({"schema_version": 1, "status": "completed"})
                output = args.output
        _write_json(output, report)
    except Exception as exc:  # noqa: BLE001 - 不打印模型内部转写或环境变量。
        print(f"歌词检查失败：{type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({
        "report": str(output),
        "status": report.get("status"),
        "items": len(report.get("items", [])) if "items" in report else 1,
        "diagnostic_only": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
