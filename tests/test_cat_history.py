import asyncio
import base64
import io
import random
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.services.cat_history import (
    CatFingerprint,
    CatHistoryError,
    CatImageHistory,
    fingerprint_cat_gif,
)


def animation(size=48, comment=b"", changed=False):
    frames = []
    for index in range(4):
        frame = Image.new("RGB", (size, size), (15, 30, 45))
        draw = ImageDraw.Draw(frame)
        x = (index + 1) * size // 8
        draw.rectangle((x, size // 4, x + size // 3, 3 * size // 4), fill=(220, 110, 30))
        if changed and index > 0:
            draw.rectangle((0, 0, size // 2, size // 2), fill="green")
        frames.append(frame)
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=100, loop=0, comment=comment)
    return output.getvalue()


@pytest.mark.asyncio
async def test_history_survives_restart_and_more_than_64_later_images(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = fingerprint_cat_gif(animation())
    assert await CatImageHistory(path).claim(first, "giphy:first")
    for index in range(70):
        fingerprint = CatFingerprint(
            f"raw-{index}", f"decoded-{index}", random.Random(index).randbytes(280),
        )
        assert await CatImageHistory(path).claim(fingerprint, f"giphy:{index}")
    restarted = CatImageHistory(path)
    assert not await restarted.claim(first, "cataas:alias")
    assert await restarted.known_sources(["giphy:first", "cataas:alias"]) == {
        "giphy:first", "cataas:alias",
    }


@pytest.mark.asyncio
async def test_metadata_reencoding_and_resizing_are_not_new_images(tmp_path):
    history = CatImageHistory(tmp_path / "history.sqlite3")
    original = fingerprint_cat_gif(animation(comment=b"original"))
    reencoded = fingerprint_cat_gif(animation(comment=b"different metadata"))
    resized = fingerprint_cat_gif(animation(size=96))
    assert original.sha256 != reencoded.sha256 != resized.sha256
    assert await history.claim(original, "giphy:original")
    assert not await history.claim(reencoded, "giphy:reencoded")
    assert not await history.claim(resized, "giphy:resized")
    assert await history.known_sources(["giphy:reencoded", "giphy:resized"]) == {
        "giphy:reencoded", "giphy:resized",
    }


@pytest.mark.asyncio
async def test_same_first_frame_with_different_animation_is_still_new(tmp_path):
    history = CatImageHistory(tmp_path / "history.sqlite3")
    assert await history.claim(fingerprint_cat_gif(animation()))
    assert await history.claim(fingerprint_cat_gif(animation(changed=True)))


@pytest.mark.asyncio
async def test_independent_connections_atomically_reserve_one_image(tmp_path):
    fingerprint = fingerprint_cat_gif(animation())
    path = tmp_path / "history.sqlite3"
    claims = await asyncio.gather(*[
        CatImageHistory(path).claim(fingerprint, f"giphy:alias-{index}") for index in range(12)
    ])
    assert sum(claims) == 1


def test_separate_processes_cannot_both_reserve_the_same_image(tmp_path):
    code = (
        "import asyncio,base64,sys; "
        "from app.services.cat_history import CatImageHistory,fingerprint_cat_gif; "
        "print(int(asyncio.run(CatImageHistory(sys.argv[1]).claim("
        "fingerprint_cat_gif(base64.b64decode(sys.argv[2]))))))"
    )
    path = str(tmp_path / "history.sqlite3")
    content = base64.b64encode(animation()).decode()
    processes = [subprocess.Popen(
        [sys.executable, "-c", code, path, content],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ) for _ in range(2)]
    try:
        outputs = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), outputs
        assert sorted(output.strip() for output, _ in outputs) == ["0", "1"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.asyncio
async def test_corrupt_or_unwritable_history_stops_reservations(tmp_path):
    path = tmp_path / "history.sqlite3"
    path.write_text("broken database", encoding="utf-8")
    with pytest.raises(CatHistoryError):
        await CatImageHistory(path).claim(fingerprint_cat_gif(animation()))


@pytest.mark.parametrize("content", [b"GIF89a-invalid", b"not a GIF"])
def test_invalid_content_cannot_get_a_fingerprint(content):
    with pytest.raises(RuntimeError, match="动态 GIF"):
        fingerprint_cat_gif(content)


def test_astrbot_resolves_history_inside_project_instead_of_its_runtime_cwd(tmp_path, monkeypatch):
    from app.astrbot_runtime import project_settings

    monkeypatch.delenv('CAT_HISTORY_PATH', raising=False)
    (tmp_path / '.env').write_text('CAT_HISTORY_PATH=./data/history.sqlite3\n', encoding='utf-8')
    assert Path(project_settings(tmp_path).cat_history_path) == (
        tmp_path / 'data/history.sqlite3'
    ).resolve()
