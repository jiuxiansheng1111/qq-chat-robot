"""将用户本地 RVC 模型注册为乐队角色；原角色配置保留。"""
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
from app.services.band_characters import band_profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rvc-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write local .env after preserving a backup")
    args = parser.parse_args()
    root = args.rvc_root.resolve(strict=True)
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError("RVC Python environment is missing")
    env = PROJECT_ROOT / ".env"
    existing = json.loads(Settings(_env_file=env).voice_profiles_json)
    added = band_profiles(str(root))
    for entry in added.values():
        if not Path(entry["rvc_model_path"]).is_file():
            raise FileNotFoundError("A required RVC model is missing")
        if entry["rvc_index_path"] and not Path(entry["rvc_index_path"]).is_file():
            raise FileNotFoundError("A configured RVC index is missing")
    updated = {**existing, **added}
    if args.apply:
        backup = PROJECT_ROOT / "data" / "maintenance" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S") / "band_rvc"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(env, backup / ".env")
        # dotenv 解码外层字符串，再由 JSON 解码路径；避免路径反斜线被提前吞掉。
        values = {
            "VOICE_PROFILES_JSON": json.dumps(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), ensure_ascii=False),
            "SINGING_RVC_ROOT": root.as_posix(),
            "SINGING_RVC_PYTHON": python.as_posix(),
        }
        text = env.read_text(encoding="utf-8")
        for key, value in values.items():
            pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=.*$", re.MULTILINE)
            replacement = key + "=" + value
            text = pattern.sub(lambda _, replacement=replacement: replacement, text) if pattern.search(text) else text + "\n" + replacement + "\n"
        temporary = env.with_name(".env.band-rvc.tmp")
        temporary.write_text(text, encoding="utf-8")
        loaded = json.loads(Settings(_env_file=temporary).voice_profiles_json)
        if loaded != updated:
            raise ValueError("Configuration serialization changed character profiles")
        temporary.replace(env)
    print(json.dumps({"characters": [x["label"] for x in added.values()], "total": len(updated), "written": args.apply}, ensure_ascii=False))


if __name__ == "__main__":
    main()
