"""Reply once to the three previously ignored comments on the user's video."""
import json,time,sys
from pathlib import Path
import httpx
root=Path.cwd();cfg=json.loads((root/'data/astrbot/instance/data/config/astrbot_plugin_bilibili_ai_bot_config.json').read_text(encoding='utf-8-sig'))
rec=root/'artifacts/dragon-maid-video/comment-catchup-receipt.json'
if rec.exists(): raise SystemExit('Receipt exists; refusing to retry')
tasks=[('318408704385','谢谢喜欢，也谢谢关注啦'),('322114373920','大白饭已经端上来了，鱼群请有序入场'),('322106252688','群号暂时还没公开，等主人确认好再告诉你')]
receipt=[]; oid=117514046541220
with httpx.Client(headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'},cookies={'SESSDATA':cfg['SESSDATA'],'bili_jct':cfg['BILI_JCT'],'DedeUserID':str(cfg['DEDE_USER_ID'])},timeout=25,trust_env=False) as h:
 nav=h.get('https://api.bilibili.com/x/web-interface/nav').json()
 if nav.get('code')!=0 or str(nav.get('data',{}).get('mid'))!=str(cfg['DEDE_USER_ID']):raise SystemExit('account validation failed')
 for rpid,text in tasks:
  data=h.get('https://api.bilibili.com/x/v2/reply/reply',params={'type':1,'oid':oid,'root':rpid,'pn':1,'ps':50}).json()
  if data.get('code')!=0:raise SystemExit('could not verify thread; stopped')
  replies=(data.get('data') or {}).get('replies') or []
  if any(str((r.get('member') or {}).get('mid'))==str(cfg['DEDE_USER_ID']) for r in replies):
   receipt.append({'root':rpid,'status':'already_replied'});print('already replied',rpid,flush=True);continue
  try:
   result=h.post('https://api.bilibili.com/x/v2/reply/add',data={'oid':oid,'type':1,'root':rpid,'parent':rpid,'message':text,'csrf':cfg['BILI_JCT']}).json()
  except Exception as e:
   receipt.append({'root':rpid,'status':'unknown','error':type(e).__name__});rec.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8');raise SystemExit('result unknown; stopped to avoid duplicates')
  code=result.get('code'); receipt.append({'root':rpid,'status':'sent' if code==0 else 'failed','code':code})
  rec.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
  print('reply',rpid,'code',code,flush=True)
  if code!=0:raise SystemExit('platform rejected response; stopped')
  time.sleep(10)
print('catchup complete')
