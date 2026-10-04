"""为每个角色准备 Seed-VC 歌唱微调，仅生成候选模型，不自动启用。

只使用所选角色现有训练清单列出的音频。生成的数据集、检查点、配置和候选清单都放在被 Git 忽略的 ``data/singing`` 下，本脚本不会提交它们。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
SINGING_ROOT = DATA_ROOT / "singing"
SEED_ROOT = SINGING_ROOT / "runtime" / "seed-vc"
PRESET_RELATIVE = Path(
    "configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"
)
INFERENCE_CHECKPOINT = "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"
INFERENCE_CONFIG = PRESET_RELATIVE.name
SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_RATE = 44_100
MAX_TRAIN_CLIP_SECONDS = 30.0
TRAIN_CLIP_SECONDS = 25


def parse_training_manifest(
    manifest: Path,
    audio_roots: Iterable[Path],
) -> list[Path]:
    """把用竖线分隔的 GPT-SoVITS/Seed 试验清单解析为音频文件。"""
    roots = [root.resolve() for root in audio_roots]
    sources: list[Path] = []
    seen: set[Path] = set()
    for number, line in enumerate(
        manifest.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        item = line.split("|", 1)[0].strip().strip('"')
        if not item:
            raise ValueError(f"{manifest}:{number}: missing audio path")
        raw = Path(item)
        candidates = [raw] if raw.is_absolute() else [root / raw for root in roots]
        candidates.extend(root / raw.name for root in roots)
        found = next((path.resolve() for path in candidates if path.is_file()), None)
        if found is None:
            raise FileNotFoundError(
                f"{manifest}:{number}: cannot resolve listed audio {item!r}"
            )
        if found not in seen:
            sources.append(found)
            seen.add(found)
    if not sources:
        raise ValueError(f"Training manifest has no audio rows: {manifest}")
    return sources


def _read_settings() -> tuple[dict[str, dict[str, object]], dict[str, dict[str, str]]]:
    # 只导入本地角色/参考音频发现所需的 Settings 字段。
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import get_settings

    settings = get_settings()
    profiles = json.loads(settings.voice_profiles_json or "{}")
    weights = json.loads(settings.voice_sovits_weights_by_profile_json or "{}")
    if not isinstance(profiles, dict) or not isinstance(weights, dict):
        raise TypeError("Voice profiles or SoVITS weight mapping must be an object")
    return profiles, weights


def _manifest_candidates(profile_id: str, weight_map: dict[str, str]) -> list[tuple[Path, list[Path]]]:
    """先找本地清单，再找权重旁的日语试验清单。"""
    found: list[tuple[Path, list[Path]]] = []
    for dataset in sorted(DATA_ROOT.glob(f"{profile_id}_voice_dataset*")):
        manifest = dataset / f"{profile_id}.train.list"
        if manifest.is_file():
            found.append((manifest, [dataset / "audio", dataset]))

    japanese_weight = weight_map.get("ja")
    if japanese_weight:
        weight = Path(japanese_weight)
        # 试验目录：<project>/weights/sovits/<checkpoint> 和
        # <project>/inputs/train_ja.list + <project>/exp/<name>/5-wav32k。
        pilot_root = weight.parent.parent.parent
        manifest = pilot_root / "inputs" / "train_ja.list"
        exp_root = pilot_root / "exp" / pilot_root.name / "5-wav32k"
        if manifest.is_file():
            found.append((manifest, [exp_root, pilot_root / "source-audio"]))

    # 只保留实际存在的清单，去重时不改写路径。
    unique: dict[Path, list[Path]] = {}
    for manifest, roots in found:
        unique.setdefault(manifest.resolve(), roots)
    return [(manifest, roots) for manifest, roots in unique.items()]


def discover_training_sources(
    profile_id: str,
    profiles: dict[str, dict[str, object]],
    weight_maps: dict[str, dict[str, str]],
    *,
    manifest_override: Path | None = None,
    audio_root_override: Path | None = None,
) -> tuple[Path, list[Path], Path]:
    """返回清单、解析后的音频行和已配置的参考音频。"""
    if profile_id not in profiles:
        raise KeyError(f"Unknown voice profile: {profile_id}")
    ref_value = profiles[profile_id].get("ref_audio_path")
    if not isinstance(ref_value, str) or not ref_value.strip():
        raise ValueError(f"Profile {profile_id} has no configured reference audio")
    reference = Path(ref_value)
    if not reference.is_absolute():
        reference = (PROJECT_ROOT / reference).resolve()
    if not reference.is_file():
        raise FileNotFoundError(f"Configured reference audio is missing: {reference}")

    if manifest_override:
        manifest = manifest_override.resolve(strict=True)
        roots = (
            [audio_root_override.resolve(strict=True)]
            if audio_root_override
            else [manifest.parent / "audio", manifest.parent]
        )
        return manifest, parse_training_manifest(manifest, roots), reference

    candidates = _manifest_candidates(profile_id, weight_maps.get(profile_id, {}))
    if not candidates:
        raise FileNotFoundError(
            f"No original training manifest found for {profile_id}. "
            "Pass --manifest to select reviewed local training material; "
            "the reference clip alone is not used as training data."
        )
    # 优先选择包含所配置参考音频的本地角色数据集；
    # 否则优先使用 data-root 下的首个清单，而非外部试验数据。
    ref_parent = reference.parent.resolve()
    local_match = next(
        (
            (manifest, roots)
            for manifest, roots in candidates
            if any(root.resolve() == ref_parent or ref_parent in root.resolve().parents for root in roots)
        ),
        None,
    )
    manifest, roots = local_match or candidates[0]
    return manifest, parse_training_manifest(manifest, roots), reference


def _source_fingerprint(manifest: Path, sources: list[Path]) -> str:
    digest = hashlib.sha256(manifest.read_bytes())
    for source in sources:
        stat = source.stat()
        digest.update(str(source).encode("utf-8"))
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()


def _soundfile_info(path: Path) -> tuple[float, int, int]:
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "soundfile is missing; run scripts/setup_singing.ps1 first."
        ) from exc
    info = sf.info(str(path))
    return float(info.duration), int(info.samplerate), int(info.channels)


def _publish_staged_dataset(staged: Path, output_dir: Path) -> None:
    """同盘发布暂存目录，短暂拒绝访问时重试三次。"""
    for attempt in range(3):
        if output_dir.exists():
            raise FileExistsError(
                f"Dataset appeared while it was being prepared: {output_dir}"
            )
        try:
            staged.rename(output_dir)
            return
        except PermissionError:
            if output_dir.exists():
                raise FileExistsError(
                    f"Dataset appeared while it was being prepared: {output_dir}"
                ) from None
            if attempt == 2:
                raise
            time.sleep(0.25 * (attempt + 1))


def prepare_audio_dataset(
    manifest: Path,
    sources: list[Path],
    output_dir: Path,
    ffmpeg: Path,
    *,
    reference: Path,
    resume: bool = False,
) -> dict[str, object]:
    """把选中的片段转成单声道 44.1 kHz PCM，并切分过长的音频。"""
    fingerprint = _source_fingerprint(manifest, sources)
    metadata_path = output_dir / "singing_dataset.json"
    if output_dir.exists():
        if not resume:
            raise FileExistsError(
                f"Dataset already exists; use --resume only for its original run: {output_dir}"
            )
        if not metadata_path.is_file():
            raise FileExistsError(f"Cannot verify existing dataset provenance: {output_dir}")
        previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        if previous.get("source_fingerprint") != fingerprint:
            raise ValueError("Source manifest/audio changed since this run was prepared")
        clips = list((output_dir / "audio").glob("*.wav"))
        if not clips:
            raise FileNotFoundError(f"Prepared dataset contains no clips: {output_dir}")
        return previous

    if not ffmpeg.is_file():
        raise FileNotFoundError(f"ffmpeg.exe is required: {ffmpeg}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".seedvoice-", dir=output_dir.parent) as temp_name:
        staged = Path(temp_name)
        audio_dir = staged / "audio"
        audio_dir.mkdir()
        accepted: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        next_index = 1
        for source in sources:
            try:
                duration, _, _ = _soundfile_info(source)
            except (OSError, RuntimeError, ValueError) as exc:
                skipped.append({"source": str(source), "reason": f"unreadable: {exc}"})
                continue
            if duration < 1.0:
                skipped.append({"source": str(source), "reason": "shorter than 1 second"})
                continue
            target_pattern = audio_dir / f"clip_{next_index:04d}_%03d.wav"
            if duration > MAX_TRAIN_CLIP_SECONDS:
                command = [
                    str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                    "-c:a", "pcm_s16le", "-f", "segment", "-segment_time",
                    str(TRAIN_CLIP_SECONDS), "-reset_timestamps", "1", str(target_pattern),
                ]
            else:
                target = audio_dir / f"clip_{next_index:04d}.wav"
                command = [
                    str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                    "-c:a", "pcm_s16le", str(target),
                ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode:
                skipped.append(
                    {"source": str(source), "reason": result.stderr.strip()[-500:]}
                )
                continue
            outputs = sorted(audio_dir.glob(f"clip_{next_index:04d}*.wav"))
            usable = 0
            for clip in outputs:
                try:
                    clip_duration, sr, channels = _soundfile_info(clip)
                except (OSError, RuntimeError, ValueError):
                    clip.unlink(missing_ok=True)
                    continue
                if not 1.0 <= clip_duration <= MAX_TRAIN_CLIP_SECONDS or sr != SAMPLE_RATE or channels != 1:
                    clip.unlink(missing_ok=True)
                    continue
                accepted.append(
                    {
                        "file": f"audio/{clip.name}",
                        "source": str(source),
                        "seconds": round(clip_duration, 3),
                    }
                )
                usable += 1
            if usable == 0:
                skipped.append({"source": str(source), "reason": "no usable 1-30 second output"})
            next_index += 1

        if not accepted:
            raise ValueError("No usable 1-30 second training clips were prepared")
        record: dict[str, object] = {
            "format": "seed-vc-svc-training-audio-v1",
            "source_manifest": str(manifest),
            "source_fingerprint": fingerprint,
            "reference_audio": str(reference),
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
            "clips": accepted,
            "skipped": skipped,
        }
        (staged / "singing_dataset.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _publish_staged_dataset(staged, output_dir)
    return record


def save_candidate_registry(registry_path: Path, profile_id: str, candidate: dict[str, object]) -> None:
    """添加候选模型，不覆盖任何已有或已验收的角色模型。"""
    current: dict[str, object] = {}
    if registry_path.exists():
        loaded = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Candidate registry is not an object: {registry_path}")
        current = loaded
    old = current.get(profile_id)
    if old is not None:
        if not isinstance(old, dict) or old.get("run_name") != candidate.get("run_name"):
            raise FileExistsError(
                f"Profile {profile_id} already has a registry entry; review/archive it before registering another candidate."
            )
        if old.get("status") == "accepted":
            raise FileExistsError(f"Refusing to replace accepted singing model for {profile_id}")
    current[profile_id] = candidate
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry_path.with_suffix(registry_path.suffix + ".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(registry_path)


def _safe_run_name(value: str) -> str:
    if not SAFE_RUN_NAME.fullmatch(value):
        raise ValueError("run name must be 1-64 ASCII letters, digits, underscores or hyphens")
    return value


def build_training_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """默认将此子进程的模型下载源固定为官方 Hugging Face。"""
    env = dict(os.environ if base is None else base)
    env["HF_ENDPOINT"] = (
        env.get("SINGING_HF_ENDPOINT", "").strip() or "https://huggingface.co"
    )
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def resolve_inference_assets(seed_root: Path = SEED_ROOT) -> tuple[Path, Path]:
    """从同一个 Hugging Face revision 获取 inference.py 使用的配置和 v2 权重。"""
    cache = seed_root / "checkpoints"
    repo_cache = cache / "models--Plachta--Seed-VC"
    snapshots = repo_cache / "snapshots"
    main_ref = repo_cache / "refs" / "main"
    revision_hint = None
    if main_ref.is_file():
        revision_hint = main_ref.read_text(encoding="ascii").strip()
        selected = snapshots / revision_hint
        config, checkpoint = selected / INFERENCE_CONFIG, selected / INFERENCE_CHECKPOINT
        if config.is_file() and checkpoint.is_file():
            return config, checkpoint
    else:
        pairs = [
            (snapshot / INFERENCE_CONFIG, snapshot / INFERENCE_CHECKPOINT)
            for snapshot in snapshots.glob("*")
            if (snapshot / INFERENCE_CONFIG).is_file()
            and (snapshot / INFERENCE_CHECKPOINT).is_file()
        ]
        if pairs:
            return max(pairs, key=lambda pair: pair[1].stat().st_mtime_ns)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Install the singing runtime before downloading Seed-VC weights") from exc
    endpoint = os.environ.get("SINGING_HF_ENDPOINT", "").strip() or "https://huggingface.co"
    config = Path(
        hf_hub_download(
            repo_id="Plachta/Seed-VC",
            filename=INFERENCE_CONFIG,
            revision=revision_hint,
            cache_dir=str(cache),
            endpoint=endpoint,
        )
    )
    checkpoint = Path(
        hf_hub_download(
            repo_id="Plachta/Seed-VC",
            filename=INFERENCE_CHECKPOINT,
            revision=config.parent.name,
            cache_dir=str(cache),
            endpoint=endpoint,
        )
    )
    if not config.is_file() or not checkpoint.is_file():
        raise FileNotFoundError("Official Seed-VC v2 inference assets are incomplete")
    return config, checkpoint


def build_training_config(inference_config: Path, preset: Path, log_dir: Path) -> dict:
    """保留推理架构，只补上训练所需的元数据。"""
    import yaml

    base = yaml.safe_load(inference_config.read_text(encoding="utf-8"))
    training = yaml.safe_load(preset.read_text(encoding="utf-8"))
    if not isinstance(base, dict) or not isinstance(training, dict):
        raise TypeError("Seed-VC config must be a YAML object")
    config = copy.deepcopy(base)
    for name in (
        "save_freq", "log_interval", "save_interval", "device", "epochs",
        "batch_size", "batch_length", "max_len", "pretrained_encoder",
        "load_only_params", "loss_params",
    ):
        if name not in config and name in training:
            config[name] = copy.deepcopy(training[name])
    model = config.get("model_params")
    fallback_model = training.get("model_params")
    if not isinstance(model, dict) or not isinstance(fallback_model, dict):
        raise TypeError("Seed-VC model parameters are missing")
    if "timbre_shifter" not in model and "timbre_shifter" in fallback_model:
        model["timbre_shifter"] = copy.deepcopy(fallback_model["timbre_shifter"])
    if not model.get("timbre_shifter"):
        raise ValueError("Seed-VC training config lacks timbre_shifter")
    if (
        config.get("preprocess_params", {}).get("sr") != SAMPLE_RATE
        or model.get("DiT", {}).get("f0_condition") is not True
        or model.get("length_regulator", {}).get("f0_condition") is not True
    ):
        raise ValueError("The inference model is not the 44.1 kHz F0 singing architecture")
    # 必须显式传入 --pretrained-ckpt。值为空时，train.py 不会
    # 在缺少检查点的情况下静默下载旧预设模型。
    config["pretrained_model"] = ""
    config["log_dir"] = str(log_dir)
    return config


def validate_run_architecture(run_config: Path, inference_config: Path) -> dict:
    """拒绝旧版运行配置，避免最终只加载少量匹配权重。"""
    import yaml

    run = yaml.safe_load(run_config.read_text(encoding="utf-8"))
    inference = yaml.safe_load(inference_config.read_text(encoding="utf-8"))
    if not isinstance(run, dict) or not isinstance(inference, dict):
        raise TypeError("Seed-VC run or inference config is invalid")
    run_model = copy.deepcopy(run.get("model_params"))
    inference_model = copy.deepcopy(inference.get("model_params"))
    if not isinstance(run_model, dict) or not isinstance(inference_model, dict):
        raise TypeError("Seed-VC model parameters are missing")
    run_model.pop("timbre_shifter", None)
    inference_model.pop("timbre_shifter", None)
    if (
        run_model != inference_model
        or run.get("preprocess_params") != inference.get("preprocess_params")
        or run.get("pretrained_model")
    ):
        raise ValueError("Run config does not match the current v2 inference model; start a new run")
    return run


def validate_checkpoint_signature(checkpoint: Path, config: dict) -> None:
    """在 Seed-VC 宽松加载检查点前，先核对关键张量形状。"""
    import torch

    state = torch.load(checkpoint, map_location="cpu", weights_only=False, mmap=True)
    nets = state.get("net") if isinstance(state, dict) else None
    if not isinstance(nets, dict):
        raise TypeError("Seed-VC checkpoint lacks model weights")
    model = config["model_params"]
    hidden = int(model["DiT"]["hidden_dim"])
    channels = int(model["length_regulator"]["channels"])
    inputs = int(model["length_regulator"]["in_channels"])

    def shape(module: str, name: str) -> tuple[int, ...] | None:
        part = nets.get(module)
        if not isinstance(part, dict):
            return None
        tensor = part.get(name)
        if tensor is None:
            tensor = part.get("module." + name)
        return tuple(tensor.shape) if tensor is not None else None

    expected = {
        ("cfm", "estimator.transformer.layers.0.attention.wqkv.weight"):
            (hidden * 3, hidden),
        ("length_regulator", "model.0.weight"): (channels, inputs, 3),
    }
    mismatches = [
        f"{module}.{name}: {shape(module, name)} != {wanted}"
        for (module, name), wanted in expected.items()
        if shape(module, name) != wanted
    ]
    if mismatches:
        raise ValueError("Checkpoint is incompatible with inference architecture: " + "; ".join(mismatches))


def latest_run_checkpoint(run_dir: Path) -> Path:
    candidates = [run_dir / "ft_model.pth", *run_dir.glob("DiT_epoch_*_step_*.pth")]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise FileNotFoundError(f"No checkpoint exists to resume: {run_dir}")
    return max(existing, key=lambda path: path.stat().st_mtime_ns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train an isolated Seed-VC F0 singing voice candidate")
    parser.add_argument("--profile", required=True, help="voice profile id from VOICE_PROFILES_JSON")
    parser.add_argument("--manifest", type=Path, help="explicit reviewed training manifest (optional)")
    parser.add_argument("--audio-root", type=Path, help="directory holding basename-only manifest audio")
    parser.add_argument("--run-name", help="ASCII run id; reuse it with --resume for another round")
    parser.add_argument(
        "--resume", action="store_true",
        help="warm-start weights from this run's latest checkpoint; optimizer and step count reset",
    )
    parser.add_argument("--max-steps", type=int, choices=(100, 300, 1000), default=100)
    parser.add_argument("--batch-size", type=int, choices=(1,), default=1)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="isolated singing runtime Python")
    parser.add_argument("--ffmpeg", type=Path, default=SINGING_ROOT / "runtime" / "bin" / "ffmpeg.exe")
    parser.add_argument("--dry-run", action="store_true", help="show selected sources and training command without writing data")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profiles, weight_maps = _read_settings()
    profile_id = args.profile
    _safe_run_name(profile_id)
    run_name = _safe_run_name(
        args.run_name or f"{profile_id}-svc-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    )
    manifest, sources, reference = discover_training_sources(
        profile_id,
        profiles,
        weight_maps,
        manifest_override=args.manifest,
        audio_root_override=args.audio_root,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "profile": profile_id,
                    "manifest": str(manifest),
                    "sources": len(sources),
                    "reference": str(reference),
                    "run_name": run_name,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.python.is_file():
        raise FileNotFoundError(f"Singing runtime Python is missing: {args.python}")
    if not args.ffmpeg.is_file():
        raise FileNotFoundError(f"Run scripts/setup_singing.ps1 first; ffmpeg missing: {args.ffmpeg}")
    seed_train = SEED_ROOT / "train.py"
    preset = SEED_ROOT / PRESET_RELATIVE
    if not seed_train.is_file() or not preset.is_file():
        raise FileNotFoundError("Seed-VC source or F0 44-kHz preset is missing; run setup first")
    inference_config, inference_checkpoint = resolve_inference_assets(SEED_ROOT)
    dataset_dir = SINGING_ROOT / "datasets" / profile_id / run_name
    run_root = SINGING_ROOT / "runs" / profile_id / run_name
    training_log_root = SINGING_ROOT / "checkpoints" / profile_id
    run_config = run_root / PRESET_RELATIVE.name
    checkpoint = training_log_root / run_name / "ft_model.pth"
    if args.resume:
        if not run_config.is_file():
            raise FileNotFoundError(f"Run config is missing; cannot safely resume: {run_config}")
        config = validate_run_architecture(run_config, inference_config)
        pretrained_source = latest_run_checkpoint(training_log_root / run_name)
    elif run_root.exists() or (training_log_root / run_name).exists():
        raise FileExistsError("Run already exists; use --resume or choose another --run-name")
    else:
        config = build_training_config(inference_config, preset, training_log_root)
        pretrained_source = inference_checkpoint
    validate_checkpoint_signature(pretrained_source, config)

    reference_seconds, _, _ = _soundfile_info(reference)
    if not 1.0 <= reference_seconds <= MAX_TRAIN_CLIP_SECONDS:
        raise ValueError("Configured reference audio must be 1-30 seconds for Seed-VC")
    dataset = prepare_audio_dataset(
        manifest, sources, dataset_dir, args.ffmpeg, reference=reference, resume=args.resume
    )
    if not args.resume:
        import yaml

        run_root.mkdir(parents=True, exist_ok=False)
        run_config.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Seed-VC 在刚好到达停止步数时不会保存周期检查点，但始终会写出
    # ft_model.pth。每轮 100 步时，中途另存一次以便崩溃恢复。
    save_every = 50 if args.max_steps == 100 else 100
    command = [
        str(args.python.resolve()),
        str(seed_train.resolve()),
        "--config", str(run_config.resolve()),
        "--pretrained-ckpt", str(pretrained_source.resolve()),
        "--dataset-dir", str((dataset_dir / "audio").resolve()),
        "--run-name", run_name,
        "--batch-size", str(args.batch_size),
        "--max-steps", str(args.max_steps),
        "--max-epochs", "1000",
        "--save-every", str(save_every),
        "--num-workers", "0",
        "--gpu", str(args.gpu),
    ]
    env = build_training_environment()
    result = subprocess.run(command, cwd=SEED_ROOT, env=env, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Training returned success but produced no candidate checkpoint: {checkpoint}")

    candidate = {
        "status": "candidate_requires_human_review",
        "run_name": run_name,
        "checkpoint": str(checkpoint.resolve()),
        "config": str(run_config.resolve()),
        "reference_audio": str(reference.resolve()),
        "training_manifest": str(manifest.resolve()),
        "dataset_manifest": str((dataset_dir / "singing_dataset.json").resolve()),
        "f0_condition": True,
        "sample_rate": SAMPLE_RATE,
        "auto_f0_adjust": False,
        "semitone_shift": 0,
        "max_steps_this_round": args.max_steps,
        "pretrained_checkpoint": str(pretrained_source.resolve()),
        "optimizer_resumed": False,
        "source_clip_count": len(dataset["clips"]),
        "source_audio_seconds": round(
            sum(float(row["seconds"]) for row in dataset["clips"]), 2
        ),
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "accepted": False,
    }
    save_candidate_registry(SINGING_ROOT / "candidates.json", profile_id, candidate)
    print(f"Candidate saved for review only: {SINGING_ROOT / 'candidates.json'} [{profile_id}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
