"""Persistent, shared reservations for cat GIFs; media bytes are never stored here."""

from __future__ import annotations

import asyncio
import hashlib
import io
import sqlite3
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

_SAMPLE_POSITIONS = (0, 0.25, 0.5, 0.75, 0.999999)
_FRAME_SIGNATURE_BYTES = 8 + 48


class CatHistoryError(RuntimeError):
    """Deduplication storage is unavailable; sending must stop."""


class NoNewCatImage(RuntimeError):
    """The source has no usable image that has not already been reserved."""


@dataclass(frozen=True)
class CatFingerprint:
    sha256: str
    decoded_sha256: str
    visual_signature: bytes


def fingerprint_cat_gif(content: bytes) -> CatFingerprint:
    """Compare decoded animation frames as well as the original file bytes."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            frame_count = getattr(image, "n_frames", 1)
            if image.format != "GIF" or frame_count < 2:
                raise ValueError("not an animated GIF")
            if frame_count > 2048 or image.width * image.height > 4_000_000:
                raise ValueError("animation exceeds processing limits")
            ends = []
            elapsed = 0
            for index in range(frame_count):
                image.seek(index)
                elapsed += max(20, int(image.info.get("duration", 100)))
                ends.append(elapsed)
            decoded = hashlib.sha256()
            signature = bytearray()
            for position in _SAMPLE_POSITIONS:
                index = min(frame_count - 1, bisect_right(ends, elapsed * position))
                image.seek(index)
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
                decoded.update(rgb.resize((32, 32), Image.Resampling.LANCZOS).tobytes())
                pixels = list(rgb.convert("L").resize(
                    (9, 8), Image.Resampling.LANCZOS,
                ).getdata())
                dhash = 0
                for row in range(8):
                    for column in range(8):
                        offset = row * 9 + column
                        dhash = (dhash << 1) | (pixels[offset] > pixels[offset + 1])
                signature.extend(dhash.to_bytes(8, "big"))
                signature.extend(rgb.resize((4, 4), Image.Resampling.LANCZOS).tobytes())
            return CatFingerprint(
                hashlib.sha256(content).hexdigest(), decoded.hexdigest(), bytes(signature),
            )
    except (OSError, ValueError, EOFError, Image.DecompressionBombError) as exc:
        raise RuntimeError("猫图不是可处理的动态 GIF") from exc


def _visually_same(first: bytes, second: bytes) -> bool:
    expected = len(_SAMPLE_POSITIONS) * _FRAME_SIGNATURE_BYTES
    if len(first) != expected or len(second) != expected:
        raise CatHistoryError("猫图历史指纹损坏，已停止发送")
    for offset in range(0, expected, _FRAME_SIGNATURE_BYTES):
        first_hash = int.from_bytes(first[offset:offset + 8], "big")
        second_hash = int.from_bytes(second[offset:offset + 8], "big")
        if (first_hash ^ second_hash).bit_count() > 8:
            return False
        colors_a = first[offset + 8:offset + _FRAME_SIGNATURE_BYTES]
        colors_b = second[offset + 8:offset + _FRAME_SIGNATURE_BYTES]
        if sum(abs(a - b) for a, b in zip(colors_a, colors_b)) > 48 * 8:
            return False
    return True


class CatImageHistory:
    def __init__(self, path: str | Path):
        if not str(path).strip() or str(path) == ":memory:":
            raise CatHistoryError("猫图历史必须使用持久化文件")
        self.path = Path(path).expanduser().resolve()

    @classmethod
    def from_settings(cls, settings) -> CatImageHistory:
        return cls(getattr(settings, "cat_history_path", "./data/cat_image_history.sqlite3"))

    @contextmanager
    def _connection(self):
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cat_images (
                    id INTEGER PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    decoded_sha256 TEXT UNIQUE NOT NULL,
                    visual_signature BLOB NOT NULL,
                    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cat_image_keys (
                    key TEXT PRIMARY KEY,
                    image_id INTEGER NOT NULL REFERENCES cat_images(id)
                );
            """)
            yield db
        except (sqlite3.Error, OSError) as exc:
            raise CatHistoryError("猫图去重历史无法读写，已停止发送") from exc
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _keys(fingerprint: CatFingerprint, source_id: str = "") -> list[str]:
        keys = [f"sha256:{fingerprint.sha256}", f"decoded:{fingerprint.decoded_sha256}"]
        if source_id:
            keys.append(f"source:{source_id}")
        return keys

    def _find(self, db, fingerprint: CatFingerprint, source_id: str = "") -> int | None:
        keys = self._keys(fingerprint, source_id)
        placeholders = ",".join("?" for _ in keys)
        row = db.execute(
            f"SELECT image_id FROM cat_image_keys WHERE key IN ({placeholders}) LIMIT 1", keys,
        ).fetchone()
        if row is not None:
            return row[0]
        for image_id, signature in db.execute("SELECT id, visual_signature FROM cat_images"):
            if _visually_same(fingerprint.visual_signature, signature):
                return image_id
        return None

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with self._connection():
            pass

    async def known_sources(self, source_ids: list[str]) -> set[str]:
        return await asyncio.to_thread(self._known_sources, source_ids)

    def _known_sources(self, source_ids: list[str]) -> set[str]:
        if not source_ids:
            return set()
        keys = [f"source:{value}" for value in source_ids]
        with self._connection() as db:
            placeholders = ",".join("?" for _ in keys)
            return {row[0].removeprefix("source:") for row in db.execute(
                f"SELECT key FROM cat_image_keys WHERE key IN ({placeholders})", keys,
            )}

    async def contains(self, fingerprint: CatFingerprint) -> bool:
        return await asyncio.to_thread(self._contains, fingerprint)

    def _contains(self, fingerprint: CatFingerprint) -> bool:
        with self._connection() as db:
            return self._find(db, fingerprint) is not None

    async def claim(self, fingerprint: CatFingerprint, source_id: str = "") -> bool:
        """Reserve before delivery; concurrent processes cannot return the same GIF."""
        return await asyncio.to_thread(self._claim, fingerprint, source_id)

    def _claim(self, fingerprint: CatFingerprint, source_id: str) -> bool:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            image_id = self._find(db, fingerprint, source_id)
            fresh = image_id is None
            if fresh:
                image_id = db.execute(
                    "INSERT INTO cat_images (sha256, decoded_sha256, visual_signature) "
                    "VALUES (?, ?, ?)",
                    (fingerprint.sha256, fingerprint.decoded_sha256, fingerprint.visual_signature),
                ).lastrowid
            # Remember aliases too, so a duplicate GIPHY ID is not downloaded repeatedly.
            db.executemany(
                "INSERT OR IGNORE INTO cat_image_keys (key, image_id) VALUES (?, ?)",
                [(key, image_id) for key in self._keys(fingerprint, source_id)],
            )
            db.execute("COMMIT")
            return fresh
