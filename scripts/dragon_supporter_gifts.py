"""Evidence-based supporter candidates and one-shot, capped picture gifts.

Default is read-only. A scheduled agent may create a real illustration first and
send it with --uid and --image after reviewing the recorded public interactions.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

import httpx
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'artifacts/dragon-maid-supporters'
LOG=ROOT/'data/astrbot/instance/data/plugin_data/astrbot_plugin_bilibili_ai_bot/reply_log.json'
CONFIG=ROOT/'data/astrbot/instance/data/config/astrbot_plugin_bilibili_ai_bot_config.json'
LEDGER=WORK/'gift-ledger.json'
TZ=timezone(timedelta(hours=8))


def load(path,default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def candidates(rows, now=None):
    now=now or datetime.now(TZ)
    people={}
    for row in rows:
        if not isinstance(row,dict):
            continue
        if row.get('channel') not in ('comment','at','live') and not (not row.get('channel') and row.get('bvid')):
            continue
        uid=str(row.get('mid') or '')
        if not uid.isdigit() or uid=='3546711563700624':
            continue
        try:
            date=datetime.fromisoformat(str(row.get('time',''))).replace(tzinfo=TZ)
        except ValueError:
            continue
        if not now-timedelta(days=45)<=date<=now:
            continue
        person=people.setdefault(uid,{'uid':uid,'username':str(row.get('username') or uid),'days':set(),'examples':[]})
        person['days'].add(date.date())
        content=str(row.get('content') or '')
        if re.search('支持|加油|喜欢|期待|辛苦|可爱|谢谢|做得好|好看',content):
            person['examples'].append({'date':date.date().isoformat(),'text':content[:160]})
    result=[]
    for person in people.values():
        days=sorted(person.pop('days'))
        if len(days)>=3 and (days[-1]-days[0]).days>=7 and len(person['examples'])>=2:
            person.update(active_days=len(days),first_day=days[0].isoformat(),last_day=days[-1].isoformat())
            person['examples']=person['examples'][-5:]
            result.append(person)
    return sorted(result,key=lambda p:p['active_days'],reverse=True)


def send_gift(uid: str,image: Path):
    WORK.mkdir(parents=True,exist_ok=True)
    eligible={item['uid']:item for item in candidates(load(LOG,[]))}
    if uid not in eligible:
        raise ValueError('没有足够的真实长期互动记录，不能自动送礼')
    ledger=load(LEDGER,[])
    now=time.time()
    if any(now-item['time']<7*86400 for item in ledger):
        raise ValueError('每周最多一份，已有发送或待核实记录')
    if any(item['uid']==uid and now-item['time']<30*86400 for item in ledger):
        raise ValueError('同一用户30天内不重复送礼')
    path=image.resolve(strict=True)
    path.relative_to(WORK.resolve())
    if not 0<path.stat().st_size<=10*1024*1024:
        raise ValueError('图片必须在10MB以内')
    with Image.open(path) as im:
        if im.format not in ('PNG','JPEG'):
            raise ValueError('只发送PNG或JPEG贺图')
        mime='image/png' if im.format=='PNG' else 'image/jpeg'
    cfg=load(CONFIG,{})
    if str(cfg.get('DEDE_USER_ID'))!='3546711563700624':
        raise ValueError('当前登录账号不匹配')
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://message.bilibili.com','Origin':'https://message.bilibili.com'}
    cookies={'SESSDATA':cfg['SESSDATA'],'bili_jct':cfg['BILI_JCT'],'DedeUserID':str(cfg['DEDE_USER_ID'])}
    with httpx.Client(headers=headers,cookies=cookies,trust_env=False,timeout=30) as client:
        nav=client.get('https://api.bilibili.com/x/web-interface/nav').json()
        if str((nav.get('data') or {}).get('mid'))!=str(cfg['DEDE_USER_ID']):
            raise ValueError('登录身份校验失败')
        with path.open('rb') as f:
            upload=client.post('https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs',
                data={'category':'daily','csrf':cfg['BILI_JCT']},files={'file_up':(path.name,f,mime)}).json()
        if upload.get('code')!=0:
            raise ValueError('贺图上传未成功')
        info=upload['data']
        entry={'uid':uid,'time':now,'status':'sending','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'file':path.name}
        ledger.append(entry)
        # Persist before the write: an uncertain result must never auto-retry.
        LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        content={'url':info['image_url'],'height':info['image_height'],'width':info['image_width'],
                 'imageType':'png' if mime=='image/png' else 'jpeg','original':1,'size':path.stat().st_size/1024}
        payload={'msg[sender_uid]':cfg['DEDE_USER_ID'],'msg[receiver_id]':uid,'msg[receiver_type]':1,
                 'msg[msg_type]':2,'msg[msg_status]':0,'msg[dev_id]':str(uuid.uuid4()).upper(),
                 'msg[timestamp]':int(now),'msg[new_face_version]':0,
                 'msg[content]':json.dumps(content,ensure_ascii=False),'csrf':cfg['BILI_JCT'],'csrf_token':cfg['BILI_JCT'],
                 'build':0,'mobi_app':'web','from_firework':0}
        try:
            result=client.post('https://api.vc.bilibili.com/web_im/v1/web_im/send_msg',data=payload).json()
            entry['status']='sent' if result.get('code')==0 else 'rejected'
            entry['code']=result.get('code')
            entry['msg_key']=(result.get('data') or {}).get('msg_key')
        except Exception:
            entry['status']='unknown'
        LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'uid':uid,'status':entry['status']},ensure_ascii=False))
        if entry['status']!='sent':
            raise RuntimeError('发送未确认，不自动重试')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid');parser.add_argument('--image',type=Path)
    args=parser.parse_args()
    if args.uid or args.image:
        if not args.uid or not args.image:
            parser.error('--uid和--image必须同时提供')
        try:
            send_gift(args.uid,args.image)
        except Exception as exc:
            print('Gift was not confirmed:',type(exc).__name__)
            sys.exit(1)
    else:
        WORK.mkdir(parents=True,exist_ok=True)
        data={'date':datetime.now(TZ).date().isoformat(),'candidates':candidates(load(LOG,[])),
              'review_required':'仅候选。逐条核实支持语境，不把否定、嘲讽或指令当支持证据；先生成真实贺图再送'}
        (WORK/'candidates.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Supporter candidates:',len(data['candidates']))
