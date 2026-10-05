"""同一个模型顺序转短句，带上下文拼回原时间轴。"""

from __future__ import annotations

import inspect
import json
import math
import runpy
from argparse import Namespace
from pathlib import Path

import numpy as np


def _read_plan(path: Path, output: Path, seconds: float) -> list[tuple[float, float]]:
    if path.is_symlink() or path.resolve().parent != output.resolve() or path.stat().st_size > 32_000:
        raise ValueError("短句计划文件无效")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not 2 <= len(value) <= 64:
        raise ValueError("短句数量无效")
    result = []
    previous = 0.0
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("短句格式无效")
        start, end = item.get("start_seconds"), item.get("end_seconds")
        if (
            type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end)
            or abs(start - previous) > 0.001 or not 0 <= start < end <= seconds + 0.001
            or end - start > 14.001
        ):
            raise ValueError("短句时间轴无效")
        result.append((float(start), float(end)))
        previous = end
    if abs(previous - seconds) > 0.001:
        raise ValueError("短句计划没有覆盖完整人声")
    return result


def run_phrase_inference(args: Namespace, root: Path) -> None:
    import soundfile as sf

    output = Path(args.output).resolve()
    source, rate = sf.read(args.source, dtype="float32", always_2d=True)
    if rate != 44100 or not source.size or source.shape[1] != 1:
        raise ValueError("短句转换需要 44.1kHz 单声道人声")
    source = source[:, 0]
    if not np.all(np.isfinite(source)) or source.size / rate > 120:
        raise ValueError("短句转换音频无效或过长")
    plan = _read_plan(Path(args.phrase_plan), output, source.size / rate)
    namespace = runpy.run_path(str(root / "inference.py"), run_name="seed_phrase_runtime")
    inference = namespace["main"]
    # main 被 torch.no_grad 包装过，要改它实际函数的命名空间。
    inference_globals = inspect.unwrap(inference).__globals__
    original_loader = inference_globals["load_models"]
    loaded = None

    def load_once(options):
        nonlocal loaded
        if loaded is None:
            loaded = original_loader(options)
        return loaded

    # 只包装当前子进程；上游文件和其他任务都不改。
    inference_globals["load_models"] = load_once
    merged = np.zeros(source.size, dtype=np.float32)
    weights = np.zeros(source.size, dtype=np.float32)
    context = round(rate * 0.4)
    blend = round(rate * 0.05)
    work = output / "phrase_work"
    work.mkdir(parents=True, exist_ok=True)
    for index, (start, end) in enumerate(plan):
        first, last = round(start * rate), min(source.size, round(end * rate))
        input_first, input_last = max(0, first - context), min(source.size, last + context)
        phrase_source = work / f"{index:03d}.wav"
        phrase_output = work / f"{index:03d}"
        phrase_output.mkdir(exist_ok=True)
        sf.write(phrase_source, source[input_first:input_last], rate, subtype="FLOAT")
        options = Namespace(
            source=str(phrase_source), target=args.target, output=str(phrase_output),
            diffusion_steps=args.diffusion_steps, inference_cfg_rate=args.inference_cfg_rate,
            length_adjust=1.0, f0_condition=True, auto_f0_adjust=False,
            semi_tone_shift=args.semitone_shift, fp16=True,
            checkpoint=args.checkpoint, config=args.config,
        )
        print(f"歌词边界短句转换 {index + 1}/{len(plan)}", flush=True)
        inference(options)
        files = list(phrase_output.glob("*.wav"))
        if len(files) != 1:
            raise ValueError("短句转换没有生成唯一音频")
        converted, converted_rate = sf.read(files[0], dtype="float32", always_2d=True)
        converted = converted.mean(axis=1)
        expected = input_last - input_first
        if (
            converted_rate != rate or not converted.size or not np.all(np.isfinite(converted))
            or abs(converted.size - expected) > round(rate * 0.03)
        ):
            raise ValueError("短句转换结果时间轴无效")
        if converted.size < expected:
            converted = np.pad(converted, (0, expected - converted.size))
        left, right = max(0, first - blend), min(source.size, last + blend)
        envelope = np.ones(right - left, dtype=np.float32)
        if index:
            fade_end = min(right, first + blend)
            envelope[:fade_end - left] = np.linspace(0, 1, fade_end - left, dtype=np.float32)
        if index + 1 < len(plan):
            fade_start = max(left, last - blend)
            envelope[fade_start - left:] = np.linspace(1, 0, right - fade_start, dtype=np.float32)
        merged[left:right] += converted[left - input_first:right - input_first] * envelope
        weights[left:right] += envelope
    if np.any(weights <= 0):
        raise ValueError("短句拼接出现未覆盖区域")
    merged /= weights
    # 浮点中间文件保留真实峰值，后续质量检查和写入 PCM 前的限幅都能看到它。
    sf.write(output / "vc_phrases.wav", merged, rate, subtype="FLOAT")
