"""接通现有 RVC 说话/翻唱，保留独立 TTS 基础音色和可恢复的本地配置。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.services.rvc import configured_rvc_profile


def update_rvc_profiles(
    settings: Settings, *, trained: dict[str, dict[str, object]] | None = None,
) -> dict[str, str]:
    profiles = json.loads(settings.voice_profiles_json)
    base_id = settings.voice_rvc_base_profile if settings.voice_rvc_base_profile_json else "roka"
    base = (
        json.loads(settings.voice_rvc_base_profile_json)
        if settings.voice_rvc_base_profile_json else dict(profiles[base_id])
    )
    base["tts_enabled"] = True
    if base.get("voice_provider") == "rvc":
        raise ValueError("RVC base profile must describe the original TTS voice")
    if trained:
        for profile_id, conversion in trained.items():
            if profile_id not in profiles:
                raise ValueError("Training result does not match a known character")
            profiles[profile_id].update(conversion)
            profiles[profile_id]["singing_provider"] = "rvc"
    available = []
    for profile_id, profile in profiles.items():
        is_rvc = profile.get("singing_provider") == "rvc"
        profile["tts_enabled"] = is_rvc
        profile["singing_enabled"] = is_rvc
        if is_rvc:
            configured_rvc_profile(settings, profile)
            available.append(profile_id)
            profile.update({
                "voice_provider": "rvc", "tts_base_profile": base_id,
                "target_language": "zh", "language": "zh",
                "supported_languages": base.get("supported_languages", ["zh"]),
                "languages": "语音 / 翻唱",
            })
    if not available:
        raise ValueError("No usable RVC voice was found")
    default = settings.voice_profile_default
    if default not in available:
        default = available[0]
    return {
        "VOICE_ENABLED": "true", "SINGING_ENABLED": "true",
        "GPT_SOVITS_AUTO_START": "true", "VOICE_PROFILE_DEFAULT": default,
        "VOICE_RVC_BASE_PROFILE": base_id,
        "VOICE_RVC_BASE_PROFILE_JSON": json.dumps(base, ensure_ascii=False, separators=(",", ":")),
        "VOICE_PROFILES_JSON": json.dumps(profiles, ensure_ascii=False, separators=(",", ":")),
    }


def write_settings(path: Path, values: dict[str, str]) -> Path:
    backup = PROJECT_ROOT / "data" / "rvc" / "backups" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, backup / ".env")
    text = path.read_text(encoding="utf-8-sig")
    for key, value in values.items():
        pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=.*$", re.MULTILINE)
        encoded = json.dumps(value, ensure_ascii=False) if key.endswith("_JSON") else value
        replacement = key + "=" + encoded
        first = True

        def replace(_match):
            nonlocal first
            if not first:
                return ""
            first = False
            return replacement

        text = pattern.sub(replace, text) if pattern.search(text) else text + "\n" + replacement + "\n"
    temporary = path.with_name(".env.rvc.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        loaded = Settings(_env_file=temporary)
        if json.loads(loaded.voice_profiles_json) != json.loads(values["VOICE_PROFILES_JSON"]):
            raise ValueError("RVC configuration did not survive serialization")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--trained-models", type=Path)
    args = parser.parse_args()
    settings = Settings(_env_file=PROJECT_ROOT / ".env")
    trained = json.loads(args.trained_models.read_text(encoding="utf-8")) if args.trained_models else None
    values = update_rvc_profiles(settings, trained=trained)
    backup = write_settings(PROJECT_ROOT / ".env", values) if args.apply else None
    profiles = json.loads(values["VOICE_PROFILES_JSON"])
    print(json.dumps({
        "enabled_rvc": [key for key, value in profiles.items() if value.get("voice_provider") == "rvc"],
        "pending_training": [key for key, value in profiles.items() if not value.get("tts_enabled")],
        "default": values["VOICE_PROFILE_DEFAULT"], "applied": args.apply,
        "backup": str(backup) if backup else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
