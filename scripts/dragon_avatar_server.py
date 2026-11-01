"""Local transparent anime puppet for OBS. Does not start a stream or send messages."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
import sys
import time
import uuid
import wave

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / "artifacts/dragon-maid-live2d"
WEB = ROOT / "web/dragon-avatar"
MEDIA = WORK / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
ALLOWED = {"http://127.0.0.1:6187", "http://localhost:6187"}
EXPRESSIONS = ("neutral", "smile", "angry", "pout", "cry", "shy", "tsundere", "surprise")
app = FastAPI(title="龙女仆 · 2D 实时皮套", docs_url=None, redoc_url=None)
clients: set[WebSocket] = set()
client_roles: dict[WebSocket, str] = {}
live_seen: dict[str, float] = {}
live_pending: dict | None = None
live_worker: asyncio.Task | None = None
state = {"expression": "neutral", "motion": 0.65, "zoom": 1.0, "nod_at": 0,
         "mouth": 0.0, "mouth_at": 0, "voice": None}
voice_lock = asyncio.Lock()


def envelope(path: Path) -> tuple[float, list[float]]:
    with wave.open(str(path), "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        if width != 2 or not rate or channels not in (1, 2):
            raise ValueError("需要 16 位单声道或立体声 WAV")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
    samples = samples.reshape(-1, channels).mean(axis=1) / 32768
    if not len(samples) or len(samples) / rate > 120:
        raise ValueError("音频需在 0–120 秒内")
    hop = max(1, rate // 50)
    amplitudes = np.array([np.sqrt(np.mean(x * x)) for x in
                           (samples[n:n + hop] for n in range(0, len(samples), hop))])
    scale = max(0.025, float(np.percentile(amplitudes, 90)))
    values = np.clip((amplitudes - 0.008) / scale, 0, 1)
    return len(samples) / rate, np.round(values, 3).tolist()


def snapshot() -> dict:
    if state["voice"] and time.time() * 1000 > state["voice"]["start"] + state["voice"]["duration"] * 1000:
        state["voice"] = None
    return {"type": "state", **state, "server_time": time.time() * 1000}


async def broadcast() -> None:
    payload = snapshot()
    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_json(payload), 1)
        except Exception:
            clients.discard(ws)
            client_roles.pop(ws,None)
    await asyncio.gather(*(send(ws) for ws in tuple(clients)))


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.headers.get("host") not in ("127.0.0.1:6187", "localhost:6187", "testserver"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "仅供本机使用"}, status_code=403)
    if request.method != "GET" and request.headers.get("origin") not in ALLOWED:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "请从本机控制台操作"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def control():
    return FileResponse(WEB / "index.html")


@app.get("/avatar")
async def avatar():
    return FileResponse(WEB / "avatar.html")


@app.get("/api/status")
async def status():
    return {"service": "dragon-maid-2d-v1", "clients": len(clients),
            "voice_busy": voice_lock.locked(), "expressions": EXPRESSIONS, **snapshot()}


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    preview: bool = False


async def publish_audio(path: Path, *, preview: bool = False) -> dict:
    duration, values = await asyncio.to_thread(envelope, path)
    state["voice"] = {"id": path.stem, "url": f"/media/{path.name}", "duration": duration,
                      "start": time.time() * 1000 + 800, "envelope": values, "step": 20,
                      "output": "browser" if preview else ("desktop" if "desktop" in client_roles.values() else "obs")}
    await broadcast()
    return {"duration": duration, "id": path.stem, "url": state["voice"]["url"], "start": state["voice"]["start"]}


class LiveReply(BaseModel):
    event_id: str = Field(min_length=1, max_length=180)
    room_id: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=240)


def configured_live_room() -> int:
    path=ROOT/'data/astrbot/instance/data/config/astrbot_plugin_bilibili_ai_bot_config.json'
    try:
        config=json.loads(path.read_text(encoding='utf-8-sig'))
        return int(config.get('LIVE_DANMAKU_ROOM_ID') or 0) if config.get('ENABLE_LIVE_DANMAKU_REPLY') else 0
    except (OSError,ValueError,TypeError):
        return 0


async def read_live_replies():
    global live_pending
    while live_pending:
        pending=live_pending
        live_pending=None
        if time.time()-pending['received']>45:
            continue
        while voice_lock.locked() or (state['voice'] and state['voice']['start']+state['voice']['duration']*1000>time.time()*1000):
            await asyncio.sleep(.3)
            if live_pending:
                pending=live_pending;live_pending=None
            if time.time()-pending['received']>45:
                break
        if time.time()-pending['received']>45:
            continue
        text=re.sub(r'\[[^\]\r\n]{1,16}\]','',pending['text']).strip()
        if not text:
            continue
        state['expression']='smile' if any(word in text for word in ('谢谢','感谢','欢迎','开心')) else 'neutral'
        await broadcast()
        try:
            await speak(Speech(text=text))
        except HTTPException:
            logging.getLogger(__name__).warning('Live reply voice unavailable; text was already handled by BiliBot')


@app.post('/api/live-reply')
async def live_reply(body: LiveReply):
    global live_pending, live_worker
    if configured_live_room()!=body.room_id:
        raise HTTPException(409,'直播间尚未配置或不是当前直播间')
    if not any(role in ('desktop','obs') for role in client_roles.values()):
        return {'queued':False,'reason':'no_avatar'}
    now=time.time()
    for key in tuple(live_seen):
        if now-live_seen[key]>600:
            live_seen.pop(key,None)
    key=f'{body.room_id}:{body.event_id}'
    if key in live_seen:
        return {'queued':False,'reason':'duplicate'}
    live_seen[key]=now
    live_pending={**body.model_dump(),'received':now}
    if live_worker is None or live_worker.done():
        live_worker=asyncio.create_task(read_live_replies())
    return {'queued':True}


@app.post("/api/speak")
async def speak(body: Speech):
    if not body.text.strip():
        raise HTTPException(422, "先写一句想说的话")
    if voice_lock.locked():
        raise HTTPException(409, "上一句还在生成，稍等一下")
    async with voice_lock:
        target = MEDIA / (uuid.uuid4().hex + ".wav")
        try:
            from app.config import get_settings
            from app.services.voice import synthesize_voice
            settings = get_settings().model_copy(update={"voice_timeout_seconds": 90})
            result = await asyncio.wait_for(synthesize_voice(
                body.text.strip(), settings, profile_name="murasame", target_language="zh",
                bot_self_id="3503565007"), timeout=100)
            raw = base64.b64decode(result.removeprefix("base64://"), validate=True)
            target.write_bytes(raw)
            return await publish_audio(target, preview=body.preview)
        except Exception as exc:
            target.unlink(missing_ok=True)
            logging.getLogger(__name__).warning("Avatar speech failed: %s", type(exc).__name__)
            raise HTTPException(503, "语音服务暂不可用，可以先用麦克风或示例语音") from None


@app.post("/api/demo")
async def demo(preview: bool = False):
    # A prerecorded line from the user's already-published introduction; no model call.
    import shutil
    source = ROOT / "artifacts/dragon-maid-video/voice/scene-00.wav"
    if not source.is_file():
        raise HTTPException(404, "示例语音文件不存在")
    target = MEDIA / (uuid.uuid4().hex + ".wav")
    await asyncio.to_thread(shutil.copyfile, source, target)
    return await publish_audio(target, preview=preview)


@app.websocket("/ws")
async def socket(ws: WebSocket):
    if ws.headers.get("origin") not in ALLOWED or ws.headers.get("host") not in ("127.0.0.1:6187", "localhost:6187"):
        await ws.close(code=1008)
        return
    await ws.accept()
    clients.add(ws)
    client_roles[ws]=ws.query_params.get('role','control')
    await ws.send_json(snapshot())
    try:
        async for raw in ws.iter_text():
            if len(raw) > 1024:
                await ws.close(code=1009)
                break
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "expression" and message.get("value") in EXPRESSIONS:
                    state["expression"] = message["value"]
                elif message.get("type") in ("motion", "zoom", "mouth"):
                    key = message["type"]
                    value = float(message["value"])
                    if not np.isfinite(value):
                        continue
                    state[key] = max(0.6 if key == "zoom" else 0, min(1.7 if key == "zoom" else 1, value))
                    if key == "mouth":
                        state["mouth_at"] = time.time() * 1000
                elif message.get("type") == "nod":
                    state["nod_at"] = time.time() * 1000
                elif message.get("type") == "stop_voice":
                    state["voice"] = None
                else:
                    continue
                await broadcast()
            except (ValueError, TypeError, KeyError):
                continue
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
        client_roles.pop(ws,None)


app.mount("/static", StaticFiles(directory=WEB), name="static")
app.mount("/assets", StaticFiles(directory=WORK / "assets"), name="assets")
app.mount("/media", StaticFiles(directory=MEDIA), name="media")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=6187, access_log=False)
