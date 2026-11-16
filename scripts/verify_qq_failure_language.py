"""Verify Luna failure phrasing without executing or sending any QQ action."""
from __future__ import annotations
import asyncio,json,os,sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'data/dafeiyu/failure-language-probe';OUT.mkdir(parents=True,exist_ok=True)
os.environ['ASTRBOT_ROOT']=str(OUT/'isolated-astrbot')


async def main():
    from astrbot.api.message_components import Plain  # noqa: F401 -- bootstrap native SDK before provider imports
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_failure_reply import Failure,describe_failure,fallback
    from app.services import usage_ledger
    usage_ledger.LEDGER_PATH=OUT/'usage.sqlite3'
    cfg=json.loads((ROOT/'data/astrbot/instance/data/config/abconf_qqchat_dafeiyu.json').read_text(encoding='utf-8-sig'))
    selected=next(p for p in cfg['provider'] if p['id']=='qqchat-chatgpt-luna')
    provider=register_chatgpt_provider()(selected,cfg['provider_settings'])
    async def get_provider(*args):return provider
    context=NS(get_using_provider_async=get_provider)
    rows=[]
    try:
        for feature,kind in [('图片','unavailable'),('歌曲','timeout'),('图片','uncertain')]:
            extras={}
            event=NS(unified_msg_origin='isolated-failure:FriendMessage:12345',get_platform_name=lambda:'aiocqhttp',
                get_extra=extras.get,set_extra=extras.__setitem__)
            info=Failure(feature,kind)
            text=await describe_failure(context,event,info)
            rows.append({'feature':feature,'outcome':kind,'text':text,'model_wording_used':text!=fallback(info),'posted_to_qq':False})
    finally:await provider.terminate()
    report={'model':'gpt-6-luna','samples':rows,'actual_usage':usage_ledger.ledger().report(owner_only=False)}
    (ROOT/'data/dafeiyu/failure-language-live-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':asyncio.run(main())
