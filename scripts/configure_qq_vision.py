"""Install the QQ caption adapter; preserve existing providers and other platform defaults."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.astrbot_vision import NATIVE_IMAGE_CAPTION_PROMPT
from app.astrbot_vision_provider import BACKUP_MODEL, PRIMARY_MODEL, PROVIDER_ID, PROVIDER_TYPE
from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically
from scripts.sync_astrbot_identity import _running


def planned_configs(base, qq):
    base, qq = copy.deepcopy(base), copy.deepcopy(qq)
    providers = {p["id"]: p for p in base["provider"]}
    primary, backup = providers["qqchat-zhipu-vision"], providers["qqchat-zhipu-vision-backup"]
    if (primary.get("model"), backup.get("model")) != (PRIMARY_MODEL, BACKUP_MODEL):
        raise ValueError("Existing free vision models do not match the reviewed configuration")
    if primary.get("provider_source_id") != backup.get("provider_source_id"):
        raise ValueError("Vision providers must already use the same configured source")
    selected = {
        **copy.deepcopy(primary),
        "id": PROVIDER_ID,
        "type": PROVIDER_TYPE,
        "enable": True,
        "modalities": ["text", "image"],
    }
    for config in (base, qq):
        existing = next((p for p in config["provider"] if p["id"] == PROVIDER_ID), None)
        if existing and existing.get("type") != PROVIDER_TYPE:
            raise ValueError("Refusing to replace another provider")
        config["provider"] = [p for p in config["provider"] if p["id"] != PROVIDER_ID] + [
            copy.deepcopy(selected)
        ]
    qq["provider_settings"]["default_image_caption_provider_id"] = PROVIDER_ID
    qq["provider_settings"]["image_caption_prompt"] = NATIVE_IMAGE_CAPTION_PROMPT.replace(
        "通常150～350字；", "长度按图片信息量决定，长报告保留关键数字和代表性原文；"
    )
    return base, qq


def configure(root, *, apply=False, is_running=_running):
    root = Path(root).resolve(strict=True)
    data = root / "data/astrbot/instance/data"
    paths = [data / "cmd_config.json", data / "config/abconf_qqchat_dafeiyu.json"]
    for path in paths:
        _assert_managed_path(root, path)
    original = [json.loads(p.read_text(encoding="utf-8-sig")) for p in paths]
    updated = planned_configs(*original)
    changed = [i for i in range(2) if original[i] != updated[i]]
    result = {
        "applied": False,
        "changed_files": [paths[i].name for i in changed],
        "caption_provider": PROVIDER_ID,
    }
    if not apply or not changed:
        return result
    if is_running():
        raise RuntimeError(
            "Stop AstrBot using its existing manager before applying QQ vision settings"
        )
    backup = (
        root
        / "data/astrbot/identity-backups"
        / ("qq-vision-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    )
    _assert_managed_path(root, backup)
    backup.mkdir(parents=True)
    for i in changed:
        shutil.copy2(paths[i], backup / paths[i].name)
    try:
        for i in changed:
            _write_json_atomically(paths[i], updated[i])
    except Exception:
        for i in changed:
            shutil.copy2(backup / paths[i].name, paths[i])
        raise
    return {**result, "applied": True, "backup_dir": str(backup)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            configure(Path(__file__).resolve().parents[1], apply=args.apply), ensure_ascii=False
        )
    )
