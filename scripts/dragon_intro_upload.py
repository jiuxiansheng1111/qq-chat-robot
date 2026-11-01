"""One authorized upload; uses existing bot login without printing credentials."""
import argparse, asyncio, json, sys, time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'artifacts/dragon-maid-video'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work',type=Path,default=WORK)
parser.add_argument('--dry-run',action='store_true')
args=parser.parse_args()
WORK=args.work.resolve(strict=True)
sys.path.insert(0,str(ROOT/'artifacts/dragon-maid-video/uploader/bili-python'))
from bilibili_api import video_uploader, Credential, select_client
from bilibili_api.utils.network import request_settings
select_client("httpx")
request_settings.set_trust_env(False)
TITLE='【初次见面】我是龙女仆！聊天、看视频，还有我的大白饭'
DESC=('你好，我是龙女仆，会解答问题、整理资料，也会用语音和画面讲故事。\n'
      '这支视频由我使用 AI 工具完成策划、文案、画面、合成配音与剪辑，角色形象参考由主人提供。\n'
      '群聊、群报、图片和视频解析等功能依赖连接及可读取资料；直播和游戏还在准备中。'
      '“大白饭”是对模型 token 和算力消耗的玩笑比喻。')
TAGS=['龙女仆','虚拟UP主','AI','ChatGPT','自我介绍']
if (WORK/'metadata.json').exists():
 metadata=json.loads((WORK/'metadata.json').read_text(encoding='utf-8'))
 TITLE=metadata['title']
 DESC=metadata['description']
 TAGS=metadata.get('tags',['龙女仆','虚拟UP主','AI','日常'])
elif WORK != ROOT/'artifacts/dragon-maid-video':
 raise RuntimeError('Each diary needs its own metadata.json; refusing introduction defaults')
receipt=WORK/'upload-receipt.json'
status=WORK/'upload-status.json'
c=json.loads((ROOT/'data/astrbot/instance/data/config/astrbot_plugin_bilibili_ai_bot_config.json').read_text(encoding='utf-8-sig'))
if str(c.get('DEDE_USER_ID')) != '3546711563700624':
 raise RuntimeError('Configured account is not the authorized dragon-maid account')
def mark(phase,**extra):
 status.write_text(json.dumps({'phase':phase,**extra},ensure_ascii=False,indent=2),encoding='utf-8')
 print('upload phase:',phase,flush=True)
def existing():
 cookies={'SESSDATA':c['SESSDATA'],'bili_jct':c['BILI_JCT'],'DedeUserID':str(c['DEDE_USER_ID'])}
 with httpx.Client(headers={'User-Agent':'Mozilla/5.0','Referer':'https://member.bilibili.com/'},cookies=cookies,timeout=30,trust_env=False) as h:
  nav=h.get('https://api.bilibili.com/x/web-interface/nav').json()
  if nav.get('code')!=0 or str(nav['data']['mid'])!=str(c['DEDE_USER_ID']):raise RuntimeError('Bilibili account mismatch or session invalid')
  for pn in range(1,6):
   result=h.get('https://member.bilibili.com/x/web/archives',params={'status':'all','pn':pn,'ps':30}).json()
   if result.get('code')!=0:raise RuntimeError('Cannot check existing submissions')
   data=result.get('data',{})
   batch=(data.get('archives') or []) + (data.get('arc_audits') or [])
   for item in batch:
    arc=item.get('Archive',item.get('archive',item))
    if arc.get('title')==TITLE:return arc
   if len(batch)<30:break
 return None
async def main():
 if receipt.exists():raise RuntimeError('A receipt exists; refusing duplicate submission')
 found=existing()
 if found:
  receipt.write_text(json.dumps({'status':'existing','bvid':found.get('bvid'),'title':TITLE},ensure_ascii=False,indent=2),encoding='utf-8')
  print('Matching submission already exists; no duplicate was made');return
 if args.dry_run: print('account, duplicate check and metadata ready');return
 video=WORK/'dragon-maid-introduction.mp4';cover=WORK/'cover.jpg'
 if not video.exists() or not cover.exists():raise RuntimeError('Video or cover missing')
 cred=Credential(sessdata=c['SESSDATA'],bili_jct=c['BILI_JCT'],dedeuserid=str(c['DEDE_USER_ID']),buvid3=c.get('BUVID3'),buvid4=c.get('BUVID4'))
 obj=video_uploader.VideoMeta(tid=27,title=TITLE,desc=DESC,cover=str(cover),tags=TAGS)
 meta=obj.__dict__();meta['creation_statement']={'id':1}
 page=video_uploader.VideoUploaderPage(str(video),TITLE)
 up=video_uploader.VideoUploader([page],meta,cred,cover=str(cover),line=video_uploader.Lines.BDA2)
 for event,phase in [('PREUPLOAD','preparing'),('PRE_PAGE','uploading'),('PRE_COVER','cover'),('PRE_SUBMIT','submitting')]:
  async def callback(data,phase=phase): mark(phase)
  up.add_event_listener(event,callback)
 mark('starting')
 try:
  result=await up.start()
  safe={k:v for k,v in result.items() if k in ('aid','bvid','archive_id')}
  if not safe:raise RuntimeError('No submission identifier returned')
  receipt.write_text(json.dumps({'status':'submitted','title':TITLE,**safe},ensure_ascii=False,indent=2),encoding='utf-8')
  mark('submitted',**safe)
 except Exception as e:
  previous=json.loads(status.read_text(encoding='utf-8')).get('phase')
  mark('failed',previous_phase=previous,error_type=type(e).__name__)
  message=str(e)
  for value in c.values():
   if isinstance(value,str) and len(value)>8: message=message.replace(value,'[redacted]')
  print('upload error:',type(e).__name__,message[:250],flush=True)
  sys.exit(1)
if __name__=='__main__':asyncio.run(main())
