"""Read real image sources and verify the native send payload, without QQ sends."""
from __future__ import annotations
import asyncio,base64,hashlib,io,json,os,sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/"data/dafeiyu/image-failure-probe"
OUT.mkdir(parents=True,exist_ok=True)
os.environ["ASTRBOT_ROOT"]=str(OUT/"isolated-astrbot")


async def main():
    from unittest.mock import AsyncMock
    from PIL import Image as PILImage
    from astrbot.api.message_components import Plain,At,Image
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.utils.metrics import Metric
    Metric.upload=AsyncMock()
    from app.astrbot_flash_provider import register_flash_provider
    register_flash_provider()
    from app.astrbot_runtime import project_settings
    from app.services.http_routing import configure_outbound_http
    from app.services.anime_image_policy import send_styled_anime_image
    from app.astrbot_plugin_images import AnimeImageRequest
    await configure_outbound_http(project_settings(ROOT))
    from app.services import http_routing
    original_http=http_routing.outbound_http_client
    def traced_http(**kwargs):
        client=original_http(**kwargs)
        original_get=client.get
        async def get(*args,**options):
            try:return await original_get(*args,**options)
            except Exception as exc:
                import re
                print(json.dumps({"gallery_transport_error":type(exc).__name__,
                    "cause":re.sub(r"https?://\S+","[URL]",str(exc))[:180]},ensure_ascii=False),flush=True)
                raise
        client.get=get
        return client
    http_routing.outbound_http_client=traced_http
    sys.path.insert(0,str(ROOT/"data/astrbot/instance/data/plugins"))
    from astrbot_plugin_get_px.main import GetPxPlugin
    from astrbot_plugin_get_px.pixiv.index import ImageIndexStore
    sys.path.insert(0,str(ROOT/"tests"))
    from test_astrbot_native_onebot import _make_group_event
    from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    types=NS(Plain=Plain,At=At,Image=Image,AstrBotMessage=AstrBotMessage,MessageMember=MessageMember,
        MessageType=MessageType,PlatformMetadata=PlatformMetadata,AiocqhttpMessageEvent=AiocqhttpMessageEvent)
    live=ROOT/"data/astrbot/instance/data/config"
    config=json.loads((live/"abconf_qqchat_dafeiyu.json").read_text(encoding="utf-8-sig"))
    vid=config["provider_settings"]["default_image_caption_provider_id"]
    manager=object.__new__(ProviderManager)
    manager.provider_sources_config=config["provider_sources"]
    manager.provider_settings=config["provider_settings"]
    manager.provider_insts=[];manager.inst_map={};manager.default_chat_provider_id=vid;manager.curr_provider_inst=None
    await manager.load_provider(next(p for p in config["provider"] if p["id"]==vid))
    provider=await manager.get_provider_by_id(vid)
    context=NS(get_config=lambda **kw:config,get_provider_by_id=lambda _:provider)
    plugin=GetPxPlugin(context,json.loads((live/"astrbot_plugin_get_px_config.json").read_text(encoding="utf-8-sig")))
    plugin.data_dir=OUT/"isolated-getpx";plugin.data_dir.mkdir(parents=True,exist_ok=True)
    plugin._init_client()
    plugin.image_index=await asyncio.to_thread(ImageIndexStore,plugin.data_dir,retention_days=1)
    event,api,raw=_make_group_event(types)
    event.message_obj.group_id="12345"
    event.message_str="来份色图"
    raw["message"]=[{"type":"text","data":{"text":event.message_str}}]
    captured=[]
    async def intercept(**params):
        for segment in params.get("message",[]):
            if segment.get("type")=="image":
                value=segment["data"]["file"]
                if value.startswith("base64://"):
                    data=base64.b64decode(value[9:])
                    with PILImage.open(io.BytesIO(data)) as im:
                        dimensions=im.size;im.verify()
                    captured.append({"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"dimensions":dimensions})
        return {"status":"ok","retcode":0,"data":{}}
    event.bot.send_group_msg=intercept
    messages=[]
    try:
        async for result in send_styled_anime_image(event,context,plugin,AnimeImageRequest(styled=True)):
            messages.append(result.get_plain_text())
        report={"posted_to_qq":False,"real_sources":True,"real_visual_check":True,
            "vision_model":provider.get_model(),"images_serialized":captured,"terminal_texts":messages,
            "passed":bool(captured)}
    except Exception as exc:
        report={"posted_to_qq":False,"passed":False,"error_type":type(exc).__name__}
    finally:
        await plugin.terminate()
        await provider.terminate()
    (ROOT/"data/dafeiyu/image-flow-live-verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=="__main__":asyncio.run(main())
