"""Produce one bounded, short vocal quotation for the 2026-10-28 diary.

No QQ/Bilibili sends. Reuses the existing singing lock and quality gates.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.services import singing
from app.services.singing_sources import resolve_singing_song
from app.services.singing_excerpt import SingingExcerpt

OUT = ROOT / 'artifacts/dragon-maid-daily/2026-10-28/song-snippet'

async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'ready.json').exists():
        print('Existing accepted snippet; no duplicate work', flush=True)
        return
    settings = get_settings()
    song = await resolve_singing_song('后来的我们 五月天', settings)
    candidates = [(i, line) for i, line in enumerate(song.lyric_lines[:-1])
                  if line.text.strip().startswith('后来的我们')
                  and 5 < len(''.join(line.text.split())) <= 10]
    if not candidates:
        raise RuntimeError('No safely bounded short phrase found')
    index, line = candidates[0]
    start = max(0, line.time_seconds - 0.12)
    end = song.lyric_lines[index + 1].time_seconds - 0.12
    if not 1 < end - start <= 12:
        raise RuntimeError('Unexpected phrase duration')
    selection = SingingExcerpt(start, end, 'single short phrase, at most 10 Chinese characters')
    # Restrict stored lyrics and generated vocals to this one short phrase.
    restricted_song = replace(song, lyric_lines=(line,), lyrics_text=line.text)
    async def source(_query, _settings):
        return restricted_song
    singing.resolve_singing_song = source
    singing.select_singing_excerpt = lambda *a, **kw: selection
    async def progress(message):
        print(message.replace('完成后立即发送', '仅在本地保存'), flush=True)
    job_id = uuid.uuid4().hex
    (OUT / 'job.json').write_text(json.dumps({'job_id':job_id,'song_id':song.track.song_id,
        'source_start':start,'source_end':end,'lyric':line.text,'status':'preparing'},ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        cover = await singing.generate_singing_cover('后来的我们 五月天', 'murasame', job_id,
            settings, progress, bot_self_id='3503565007', mode='clip', on_chunk=None)
    except Exception as exc:
        job = json.loads((OUT / 'job.json').read_text(encoding='utf-8'))
        job.update(status='failed', error_type=type(exc).__name__)
        (OUT / 'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    shutil.copy2(cover.full_path, OUT / 'dragon-maid-song-snippet.wav')
    (OUT / 'ready.json').write_text(json.dumps({'status':'quality_passed_needs_listening',
        'song':'后来的我们','artist':'五月天','job_id':job_id,'source_start':start,'source_end':end,
        'lyric':line.text,'quality':cover.quality},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Snippet saved locally; listen before including in a video',flush=True)

if __name__ == '__main__':
    asyncio.run(main())
