"""把原角色提示导入 AstrBot 的私有人格数据库。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings


PERSONA_ID = "qqchat-local-persona"
ASTRBOT_DB_NAME = "data_v4.db"


async def migrate_persona(
    settings: Settings,
    dbpath: str | Path,
    replace_owned: bool = False,
) -> dict[str, Any]:
    """导入原 system prompt；默认保留同 ID 的现有人格。"""
    path = Path(dbpath).expanduser()
    if not path.is_absolute():
        raise ValueError("dbpath 必须是 AstrBot 数据库的绝对路径")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("AstrBot 数据库文件不存在") from exc
    if not path.is_file() or path.name.casefold() != ASTRBOT_DB_NAME:
        raise ValueError("dbpath 必须指向现有的 data_v4.db")
    if path.parent.name.casefold() != "data":
        raise ValueError("data_v4.db 必须位于 AstrBot 实例的 data 目录中")

    prompt = settings.persona_prompt()
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("原角色 system prompt 为空")

    # 首次导入 AstrBot 核心前，把它的根目录指到目标实例。
    env_names = ("ASTRBOT_ROOT", "ASTRBOT_RESET_DASHBOARD_PASSWORD")
    saved_env = {name: os.environ.get(name) for name in env_names}
    db = None
    try:
        os.environ["ASTRBOT_ROOT"] = str(path.parent.parent)
        os.environ.pop("ASTRBOT_RESET_DASHBOARD_PASSWORD", None)
        # 延迟导入，避免在非 AstrBot 环境中导入其运行时。
        from astrbot.core.db.sqlite import SQLiteDatabase

        db = SQLiteDatabase(str(path))
        existing = await db.get_persona_by_id(PERSONA_ID)
        if existing is None:
            await db.insert_persona(
                persona_id=PERSONA_ID,
                system_prompt=prompt,
            )
            return {
                "id": PERSONA_ID,
                "created": True,
                "updated": False,
                "retained": False,
            }

        if not replace_owned:
            return {
                "id": PERSONA_ID,
                "created": False,
                "updated": False,
                "retained": True,
            }

        # 只同步提示词，保留现有人格的开场对话、工具、Skills 等字段。
        await db.update_persona(
            persona_id=PERSONA_ID,
            system_prompt=prompt,
        )
        return {
            "id": PERSONA_ID,
            "created": False,
            "updated": True,
            "retained": False,
        }
    finally:
        try:
            if db is not None:
                await db.engine.dispose()
        finally:
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
