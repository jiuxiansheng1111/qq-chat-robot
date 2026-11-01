"""One game speech job, existing GPT-SoVITS weights, then exit to release models."""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
import wave
from pathlib import Path

_STARTED = time.monotonic()
_PHASE_INDEX = 0


def _phase(name: str) -> None:
    """Own-process readings only: no text, paths, settings, or credentials."""
    global _PHASE_INDEX
    _PHASE_INDEX += 1
    event = {"minecraft_voice_phase": name, "phase_index": _PHASE_INDEX,
             "elapsed_seconds": round(time.monotonic() - _STARTED, 3),
             "monotonic_seconds": time.monotonic()}
    if os.name == "nt":
        import ctypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (field, ctypes.c_size_t) for field in ("peak_working", "working", "peak_paged", "paged",
                                                     "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        read = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if read(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
            event.update({"working_set_bytes": counters.working, "private_bytes": counters.private})
    print(json.dumps(event), flush=True)


def _instrument(owner, method: str, name: str) -> None:
    original = getattr(owner, method)

    @functools.wraps(original)
    def measured(self, *args, **kwargs):
        _phase(name + "_begin")
        result = original(self, *args, **kwargs)
        _phase(name + "_complete")
        return result

    setattr(owner, method, measured)


def _configure_half_precision_loaders(torch, *, device: str, final_half: bool) -> dict:
    """Use public HF loader options only when the pipeline's final models are already FP16.

    The installed Transformers defaults to FP32 when dtype is omitted, even
    for these local FP16 checkpoints. Scope these adapters to this one worker;
    neither dependency files nor QQ's resident model objects are modified.
    """
    result = {"hf_load_dtype": "default", "hf_direct_cuda": False}
    if not final_half or not device.startswith("cuda"):
        return result
    from transformers import AutoModelForMaskedLM, HubertModel
    from transformers.utils import is_accelerate_available

    direct = is_accelerate_available()
    for owner in (AutoModelForMaskedLM, HubertModel):
        original = owner.from_pretrained

        def adapted(*args, _load=original, **kwargs):
            kwargs["dtype"] = torch.float16
            kwargs["local_files_only"] = True
            if direct:
                kwargs["device_map"] = {"": device}
            return _load(*args, **kwargs)

        # `original` is already bound to its HF class; a static adapter avoids
        # passing that class twice and keeps each closure's original loader.
        owner.from_pretrained = staticmethod(functools.wraps(original)(adapted))
    result.update({"hf_load_dtype": "float16", "hf_direct_cuda": direct})
    _phase("hf_fp16_direct_cuda" if direct else "hf_fp16_cpu_staging")
    return result


def main() -> None:
    _phase("worker_start")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.request.stat().st_size > 65536:
        raise ValueError("Speech request exceeds size limit")
    job = json.loads(args.request.read_text(encoding="utf-8"))
    root = Path(job["gpt_root"]).resolve(strict=True)
    source_config = Path(job["config"]).resolve(strict=True)
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "GPT_SoVITS"))
    # Existing GPT-SoVITS switch: use its light pypinyin front end instead of
    # loading a separate 635 MB G2PW ONNX model/session for a short game reply.
    # Model weights/LLM stay unchanged; polyphonic-word readings can differ.
    os.environ["is_g2pw"] = "false"
    # We import our own offline adapter, never edit third-party source files.
    from run_gpt_sovits import _install_offline_language_detection

    _install_offline_language_detection()
    _phase("language_adapter_complete")
    import numpy as np
    _phase("torch_import_begin")
    import torch
    _phase("torch_import_complete")
    import yaml
    _phase("tts_import_begin")
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
    from TTS_infer_pack.TextPreprocessor import TextPreprocessor

    _phase("tts_import_complete")
    for method, name in (("init_t2s_weights", "load_t2s"), ("init_vits_weights", "load_vits"),
                         ("init_bert_weights", "load_bert"), ("init_cnhuhbert_weights", "load_cnhubert")):
        _instrument(TTS, method, name)
    _instrument(TextPreprocessor, "get_phones_and_bert", "frontend")

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    data = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Invalid existing TTS configuration")
    custom = dict(data.get("custom") or data.get("v2") or {})
    # Select the already configured profile before loading anything: no initial
    # unrelated character model followed by a second set of switched weights.
    for field, job_field in (("t2s_weights_path", "gpt_weights"), ("vits_weights_path", "sovits_weights")):
        selected = job.get(job_field) or custom.get(field)
        path = Path(str(selected or ""))
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise FileNotFoundError(f"Configured {field} is missing")
        custom[field] = str(path.resolve())
    for field in ("bert_base_path", "cnhuhbert_base_path"):
        path = Path(str(custom.get(field) or ""))
        path = path if path.is_absolute() else root / path
        if not path.is_dir():
            raise FileNotFoundError(f"Configured local {field} is missing")
        custom[field] = str(path.resolve())
    # This per-job device choice never writes the original YAML or changes QQ.
    _phase("cuda_probe_begin")
    custom["device"] = "cuda" if torch.cuda.is_available() else "cpu"
    custom["is_half"] = custom["device"] == "cuda"
    data["custom"] = custom
    output = args.output.resolve()
    private_config = output.with_suffix(".tts-config.yaml")
    private_config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    config = TTS_Config(str(private_config))
    _phase("configuration_complete")
    loader_report = _configure_half_precision_loaders(
        torch, device=str(config.device), final_half=config.is_half,
    )
    payload = job["payload"]
    # One short fragment at a time avoids batched semantic/audio allocations.
    payload.update({"batch_size": 1, "parallel_infer": False, "split_bucket": False,
                    "use_cuda_graph": False, "streaming_mode": False,
                    "return_fragment": True, "fixed_length_chunk": False})
    frames = 0
    sample_rate = None
    with torch.inference_mode():
        pipeline = TTS(config)
        _phase("all_models_loaded")
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            _phase("synthesis_begin")
            for rate, fragment in pipeline.run(payload):
                if not 8000 <= rate <= 48000 or (sample_rate is not None and sample_rate != rate):
                    raise ValueError("Unexpected TTS sample rate")
                if sample_rate is None:
                    audio.setframerate(rate)
                sample_rate = rate
                fragment = np.asarray(fragment)
                if fragment.ndim != 1 or not np.all(np.isfinite(fragment)):
                    raise ValueError("Invalid TTS audio")
                if fragment.dtype.kind == "f":
                    fragment = np.clip(fragment * 32768, -32768, 32767).astype("<i2")
                else:
                    fragment = fragment.astype("<i2", copy=False)
                frames += fragment.size
                if frames > rate * 90 or frames * 2 > 8 * 1024 * 1024:
                    raise ValueError("TTS audio exceeds game limits")
                audio.writeframes(fragment.tobytes())
                del fragment
        if not frames:
            raise ValueError("TTS returned empty audio")
        _phase("synthesis_complete")
    print(json.dumps({"backend": "gpt_sovits_one_shot", "frames": frames,
                      "sample_rate": sample_rate, "device": custom["device"],
                      "half_precision": custom["is_half"], "fragment_mode": True,
                      "g2pw_enabled": False, **loader_report}), flush=True)


if __name__ == "__main__":
    main()
