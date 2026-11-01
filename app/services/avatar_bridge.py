"""Forward an already-sent live reply to the local puppet, without another model call."""
from __future__ import annotations

import logging
import httpx


async def forward_live_reply(event_id: str, room_id: int, text: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
            response=await client.post('http://127.0.0.1:6187/api/live-reply',
                headers={'Origin':'http://127.0.0.1:6187'},
                json={'event_id':str(event_id)[:180],'room_id':int(room_id),'text':str(text)[:240]})
            return response.status_code==200 and bool(response.json().get('queued'))
    except (httpx.HTTPError,ValueError,TypeError):
        logging.getLogger(__name__).debug('Desktop avatar not available for this live reply')
        return False
