"""Conversation-scoped, user-authored notes and durable copies of explicitly attached media."""
from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime

from PIL import Image

from app.astrbot_compare import image_refs
from app.services.social_plans import CHINA, PlanStore

_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
 owner TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, image BLOB, updated REAL NOT NULL,
 PRIMARY KEY (owner,name)
);
"""


async def attached_photo(event):
    quoted, current = image_refs(event)
    refs = current or quoted
    if not refs:
        return None
    from astrbot.core.utils.media_utils import resolve_image_ref_to_base64_data
    result = await resolve_image_ref_to_base64_data(refs[0], strict=True)
    if not result:
        raise ValueError("没读到这张照片，可以重新发一次。")
    data = result.to_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("照片超过 20 MB，可以发一张较小的副本。")
    def validate():
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format not in {"JPEG","PNG","WEBP","GIF"} or picture.width * picture.height > 32_000_000:
                raise ValueError("照片格式或尺寸无法保存。")
            picture.verify()
    await asyncio.to_thread(validate)
    return data


async def send_photo_note(event, text, image=None):
    if image:
        from astrbot.api.message_components import Image as QQImage
        from astrbot.api.message_components import Plain
        await event.send(event.chain_result([Plain(text), QQImage.fromBytes(image)]))
    elif text:
        await event.send(event.plain_result(text))


def notes_store(host, slug):
    return PlanStore(host.runtime.project_root / "data/dafeiyu" / (slug + ".sqlite3"), _NOTES)


async def personal_note(host, event, key, slug, action, name="", description=""):
    store = notes_store(host, slug)
    owner = json.dumps(key)
    now = datetime.now(CHINA)
    image = await attached_photo(event) if action == "save" else None
    def change(db):
        if action == "save":
            db.execute("""INSERT INTO notes VALUES (?,?,?,?,?) ON CONFLICT(owner,name) DO UPDATE SET
                description=excluded.description, image=COALESCE(excluded.image,notes.image), updated=excluded.updated""",
                (owner, name, description, image, now.timestamp()))
            return ("记下了「" + name + "」：" + description + ("，照片也存好了。" if image else "。"), None)
        if action == "forget":
            gone = db.execute("DELETE FROM notes WHERE owner=? AND name=?", (owner, name)).rowcount
            return ("已经删掉「" + name + "」和随附照片。" if gone else "这里没有这条记录。", None)
        if action == "list":
            rows = db.execute("SELECT name FROM notes WHERE owner=? ORDER BY updated DESC LIMIT 40", (owner,)).fetchall()
            return ("这里记着：" + "、".join(row[0] for row in rows) if rows else "你还没有在这个会话里保存过。", None)
        if name:
            row = db.execute("SELECT * FROM notes WHERE owner=? AND name=?", (owner, name)).fetchone()
        else:
            row = db.execute("SELECT * FROM notes WHERE owner=? ORDER BY updated DESC, name LIMIT 1", (owner,)).fetchone()
        if not row:
            return ("这个会话里还没有对应的记录。", None)
        return ("你上次记的是「" + row["name"] + "」：" + row["description"], row["image"])
    result = await asyncio.to_thread(store.turn, event, change, now)
    if result:
        await send_photo_note(event, *result)
    return True
