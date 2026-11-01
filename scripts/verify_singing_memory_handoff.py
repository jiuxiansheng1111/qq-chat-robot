"""Local clip validation of the actual failed request; never send OneBot messages."""
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC,datetime
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from app.config import Settings
from app.services.singing import generate_singing_cover
from app.services.singing_resources import _read_memory_status


async def main():
    output=PROJECT/'data/maintenance/singing-memory-20261025'
    output.mkdir(parents=True,exist_ok=True)
    settings=Settings(_env_file=PROJECT/'.env').model_copy(update={'singing_clip_seconds':20.0})
    job_id=uuid.uuid4().hex
    samples=[];events=[];finished=asyncio.Event()
    def snapshot():
        value=_read_memory_status()
        if value:
            samples.append({'at':time.time(),'commit_used_gib':round((value.total_commit-value.available_commit)/2**30,3),
                'commit_limit_gib':round(value.total_commit/2**30,3),
                'commit_percent':round((1-value.available_commit/value.total_commit)*100,2),
                'available_physical_gib':round(value.available_phys/2**30,3)})
    async def monitor():
        while not finished.is_set():
            snapshot()
            try:await asyncio.wait_for(finished.wait(),.25)
            except TimeoutError:pass
    async def progress(text):
        event={'at':datetime.now(UTC).isoformat(),'stage':text}
        events.append(event)
        (output/'progress.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(event,ensure_ascii=False),flush=True)
    task=asyncio.create_task(monitor());error=None;cover=None
    try:
        cover=await generate_singing_cover('后来的我们','murasame',job_id,settings,progress,
            bot_self_id=settings.onebot_self_id,mode='clip',on_chunk=None)
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
    finally:
        snapshot();finished.set();await task
        stop_log=PROJECT/'data/logs/voice-memory-stop_gpt_sovits_for_singing.log'
        handoff=json.loads(stop_log.read_text(encoding='utf-8-sig')) if stop_log.exists() else {}
        result={'finished_at':datetime.now(UTC).isoformat(),'job_id':job_id,'song':'后来的我们',
            'profile_id':'murasame','mode':'clip','requested_seconds':20,'onebot_messages_sent':0,
            'passed':cover is not None and handoff.get('stopped') is True,'error':error,'samples':samples,
            'voice_memory_handoff':handoff,
            'peak_commit_percent':max((s['commit_percent'] for s in samples),default=0),
            'peak_commit_used_gib':max((s['commit_used_gib'] for s in samples),default=0),
            'minimum_available_physical_gib':min((s['available_physical_gib'] for s in samples),default=0),
            'cover':str(cover.full_path) if cover else None}
        (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False),flush=True)
    if error:raise RuntimeError(error['message'])
    assert result['voice_memory_handoff'].get('stopped') is True, 'The managed voice process was not released'


if __name__=='__main__':asyncio.run(main())
