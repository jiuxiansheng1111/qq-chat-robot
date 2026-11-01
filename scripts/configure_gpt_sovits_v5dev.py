"""把本机语音路由切换到 V5Dev；原始模型、音频和配置备份均保留。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.config import Settings


def set_values(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        encoded = json.dumps(value, ensure_ascii=False) if key.endswith("_JSON") else value
        replacement = key + "=" + encoded
        pattern = re.compile(r"^[ \t]*" + re.escape(key) + r"[ \t]*=.*$", re.MULTILINE)
        matches = list(pattern.finditer(text))
        if not matches:
            text = text.rstrip("\r\n") + "\n" + replacement + "\n"
            continue
        for position, match in reversed(list(enumerate(matches))):
            text = text[:match.start()] + (replacement if position == 0 else "") + text[match.end():]
    return text


def reference_transcript(reference: Path, manifests: list[Path]) -> tuple[str, str] | None:
    """同名音频可能属于不同语言清单，必须匹配完整路径。"""
    for manifest in manifests:
        for row in manifest.read_text(encoding="utf-8-sig").splitlines():
            fields = row.split("|", 3)
            if len(fields) != 4 or not fields[3].strip():
                continue
            audio = Path(fields[0])
            if not audio.is_absolute():
                audio = manifest.parent / audio
            if audio.resolve() == reference.resolve():
                return fields[2], fields[3].strip()
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = PROJECT.parent / "qq-chatrobot-voice" / "GPT-SoVITS"
    weights = root / "GPT_SoVITS" / "pretrained_models"
    gpt, sovits = weights / "s1v3.ckpt", weights / "gsv-v5-pretrained" / "s2Gv5dev.pth"
    for path in (gpt, sovits, weights / "gsv-v5-pretrained" / "vocoder.pth"):
        if not path.is_file():
            raise FileNotFoundError(f"Required V5Dev model is missing: {path.name}")
    env = PROJECT / ".env"
    settings = Settings(_env_file=env)
    profiles = json.loads(settings.voice_profiles_json)
    for profile_id, profile in profiles.items():
        if profile.get("tts_enabled") is False:
            continue
        reference = Path(str(profile.get("ref_audio_path") or ""))
        if not reference.is_absolute():
            reference = PROJECT / reference
        transcript = reference_transcript(reference, sorted(
            (PROJECT / "data").glob(f"{profile_id}_voice_dataset*/{profile_id}.list")
        ))
        if transcript:
            profile["prompt_lang"], profile["prompt_text"] = transcript
        elif not str(profile.get("prompt_text") or "").strip():
            raise ValueError(f"V5Dev requires the exact reference transcript for {profile_id}")
    routed = {
        key: {language: sovits.as_posix() for language in profile.get("supported_languages", ["zh"])}
        for key, profile in profiles.items() if profile.get("tts_enabled") is not False
    }
    if not routed:
        raise ValueError("No existing reference voices are configured")
    yaml_path = root / "GPT_SoVITS" / "configs" / "tts_infer_v5dev.yaml"
    config = {"custom": {
        "version": "v5dev", "device": "cuda:0", "is_half": True,
        "t2s_weights_path": gpt.as_posix(), "vits_weights_path": sovits.as_posix(),
        "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
        "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
    }}
    values = {
        "VOICE_ENABLED": "true", "GPT_SOVITS_AUTO_START": "true",
        "VOICE_PROFILES_JSON": json.dumps(profiles, ensure_ascii=False, separators=(",", ":")),
        "GPT_SOVITS_PYTHON": "../qq-chatrobot-voice/GPT-SoVITS/.venv/Scripts/python.exe",
        "GPT_SOVITS_TTS_CONFIG": "GPT_SoVITS/configs/tts_infer_v5dev.yaml",
        "GPT_SOVITS_SOVITS_WEIGHTS": sovits.as_posix(),
        "VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON": "{}",
        "VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON": json.dumps(routed, ensure_ascii=False, separators=(",", ":")),
        "VOICE_GPT_WEIGHTS_BY_PROFILE_JSON": json.dumps({key: gpt.as_posix() for key in routed}, ensure_ascii=False, separators=(",", ":")),
        "VOICE_TIMEOUT_SECONDS": "120",
    }
    text = set_values(env.read_text(encoding="utf-8-sig"), values)
    result = {"version": "v5dev", "device": "cuda:0", "reference_profiles": list(routed), "applied": args.apply}
    if args.apply:
        backup = PROJECT / "data" / "maintenance" / ("v5dev-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(env, backup / ".env")
        default_yaml = yaml_path.with_name("tts_infer.yaml")
        shutil.copy2(default_yaml, backup / "tts_infer.yaml")
        if yaml_path.exists():
            shutil.copy2(yaml_path, backup / yaml_path.name)
        temporary = env.with_name(".env.v5dev.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            checked = Settings(_env_file=temporary)
            if json.loads(checked.voice_sovits_weights_by_profile_json) != routed:
                raise ValueError("V5Dev model routing did not survive serialization")
            if checked.voice_enabled is not True or checked.voice_timeout_seconds != 120:
                raise ValueError("V5Dev effective settings were overridden")
            yaml_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            default_yaml.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            temporary.replace(env)
        finally:
            temporary.unlink(missing_ok=True)
        result["backup"] = str(backup)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
