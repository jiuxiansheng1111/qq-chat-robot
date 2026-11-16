"""Bounded QQ adapter for video-watch-plus; no general terminal/computer tool."""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app.services.bounded_http import request_json
from app.services.http_routing import outbound_http_client

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/"integrations/video-watch-plus"
STATE=ROOT/"data/video-watch-plus"


async def attachment_file(url,directory,settings):
    from app.astrbot_video import _MAX_VIDEO_BYTES, _validated_video_url
    from app.services.bounded_http import request_bytes
    valid=await _validated_video_url(url,settings)
    async with outbound_http_client(settings=settings,timeout=30) as client:
        payload=await request_bytes(client,"GET",valid,max_bytes=_MAX_VIDEO_BYTES,timeout_seconds=30)
    path=directory/"uploaded.mp4"
    path.write_bytes(payload)
    return path


async def skill_run(script,args,settings,timeout=40):
    from app.astrbot_video import _binary
    env={**os.environ,"VW_CONFIG":str(STATE/"vw.config.json"),"VW_FFMPEG":_binary(settings,"ffmpeg"),
         "PYTHONIOENCODING":"utf-8"}
    python=Path(settings.singing_python)
    if not python.is_absolute():python=ROOT/python
    process=await asyncio.create_subprocess_exec(str(python),str(SKILL/script),*map(str,args),env=env,
        stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        output,_error=await asyncio.wait_for(process.communicate(),timeout)
    except BaseException:
        if process.returncode is None:
            if os.name=="nt":
                killer=await asyncio.create_subprocess_exec("taskkill.exe","/PID",str(process.pid),"/T","/F",
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                await killer.wait()
            else:process.kill()
        await process.communicate()
        raise
    if process.returncode or len(output)>2*1024*1024:
        raise ValueError("视频技能量测未完成，不能把猜测当作读取结果")
    return output.decode("utf-8",errors="replace")


async def measure(path,directory,settings):
    await skill_run("vendor/vw.py",["probe",path,"--out",directory],settings)
    shotlist=directory/"shotlist.json"
    await skill_run("vendor/vw.py",["plan",directory/"timeline.json","--skeleton","10","--max","16","--burst","1","--out",shotlist],settings)
    timeline=json.loads((directory/"timeline.json").read_text(encoding="utf-8"))
    plan=json.loads(shotlist.read_text(encoding="utf-8"))
    audio={"status":"unavailable"}
    if timeline.get("info",{}).get("audio_codec"):
        await skill_run("scripts/audio_probe.py",[path,"--json",directory/"audio_probe.json"],settings)
        audio=json.loads((directory/"audio_probe.json").read_text(encoding="utf-8"))
    return {"source":"video-watch-plus","cuts":timeline.get("cuts",[])[:80],
        "freezes":timeline.get("freezes",[])[:30],"silences":timeline.get("silences",[])[:30],
        "content_kind":timeline.get("content_kind"),"max_unsampled_gap_seconds":plan.get("max_gap"),
        "sampled_seconds":[s["t"] for s in plan.get("shots",[])][:16],
        "audio_probe":audio}


async def _download_cdn(client,url,target,headers):
    host=(urlsplit(url).hostname or "").lower()
    if not host.endswith(".bilivideo.com") or urlsplit(url).scheme!="https":
        raise ValueError("公开视频没有返回受支持的官方CDN地址")
    offset,total=0,None
    with target.open("wb") as stream:
        while total is None or offset<total:
            response=await client.get(url,headers={**headers,"Range":f"bytes={offset}-{offset+1048575}"},timeout=20)
            response.raise_for_status()
            match=re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",response.headers.get("content-range",""))
            if not match:
                if offset==0 and response.status_code==200 and len(response.content)<=100*1024*1024:
                    stream.write(response.content)
                    return
                raise ValueError("公开视频分段响应缺少可核对的长度")
            begin,end,total=map(int,match.groups())
            if begin!=offset or end-begin+1!=len(response.content) or total>100*1024*1024:
                raise ValueError("公开视频超过大小上限或下载不完整")
            stream.write(response.content)
            offset=end+1


async def bilibili_file(url,directory,settings):
    """Anonymous official playback only; no account cookies, VIP or unblocking."""
    from app.astrbot_video import _binary, _run
    headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.bilibili.com/"}
    async with outbound_http_client(settings=settings,timeout=20) as client:
        if urlsplit(url).hostname=="b23.tv":
            response=await client.head(url,headers=headers,follow_redirects=False)
            url=response.headers.get("location", "")
            if (urlsplit(url).hostname or "").lower() not in {"www.bilibili.com","bilibili.com","m.bilibili.com"}:
                raise ValueError("短链接未跳转至受支持的B站视频")
        bvid=re.search(r"(BV[A-Za-z0-9]+)",url)
        if not bvid:raise ValueError("请提供完整的B站BV视频链接")
        view=await request_json(client,"GET","https://api.bilibili.com/x/web-interface/view",
            params={"bvid":bvid[1]},headers=headers,max_bytes=512*1024,timeout_seconds=20)
        info=view.get("data",{}) if view.get("code")==0 else {}
        if not info.get("cid"):
            raise ValueError("未取得可读取的公开视频")
        query=parse_qs(urlsplit(url).query)
        try:page=int(query.get("p",[1])[0])
        except (ValueError,TypeError):raise ValueError("视频分P编号无效") from None
        pages=info.get("pages",[])
        if pages:
            if not 1<=page<=len(pages):raise ValueError("不存在这个视频分P")
            info={**info,"cid":pages[page-1]["cid"],"duration":pages[page-1]["duration"]}
        if not 0<float(info.get("duration",0))<=1800:
            raise ValueError("暂时只读取30分钟以内的视频分P")
        play=await request_json(client,"GET","https://api.bilibili.com/x/player/playurl",
            params={"bvid":bvid[1],"cid":info["cid"],"qn":32,"fnval":16},headers=headers,
            max_bytes=1024*1024,timeout_seconds=20)
        if play.get("code")!=0:raise ValueError("平台未提供公开可读取的完整视频")
        if play.get("data",{}).get("is_preview") or float(play.get("data",{}).get("timelength",0))<float(info["duration"])*1000-3000:
            raise ValueError("平台只提供预览片段，不能冒充读取了完整视频")
        dash=play.get("data",{}).get("dash",{})
        for kind in ("video","audio"):
            choices=sorted(dash.get(kind,[]),key=lambda x:x.get("bandwidth",float("inf")))
            if not choices:
                if kind=="video":raise ValueError("平台没有返回可读取的公开视频画面")
                continue
            source=choices[0].get("baseUrl") or choices[0].get("base_url") or ""
            if source.startswith("http://"):source="https://"+source[7:]
            await _download_cdn(client,source,directory/(kind+".m4s"),headers)
    merged=directory/"public.mp4"
    args=[_binary(settings,"ffmpeg"),"-nostdin","-v","error","-protocol_whitelist","file,pipe",
          "-i",str(directory/"video.m4s")]
    if (directory/"audio.m4s").exists():args += ["-i",str(directory/"audio.m4s")]
    await _run(*args,"-c","copy","-y",str(merged),timeout=20)
    query=parse_qs(urlsplit(url).query)
    try:focus=float(query.get("t",[0])[0])
    except (ValueError,TypeError):focus=0
    if not math.isfinite(focus):focus=0
    offset=max(0,min(float(info["duration"])-1,focus)-15) if focus>0 else 0
    if offset:
        clipped=directory/"focused.mp4"
        await _run(_binary(settings,"ffmpeg"),"-nostdin","-v","error","-ss",str(offset),"-i",str(merged),
            "-t","90","-c:v","mpeg4","-q:v","3","-c:a","aac","-threads","2","-y",str(clipped),timeout=30)
        merged=clipped
    return merged,offset,float(info["duration"])


async def read_detail(context,event,settings,time_seconds=0,region="0,0,1,1",source=0):
    """Only a current or quoted native video can be cropped; never a tool path."""
    from app.astrbot_video import _binary, _model_summary, _probe_video, _run, collect_video_sources
    if isinstance(time_seconds,bool) or not math.isfinite(float(time_seconds)) or not 0<=float(time_seconds)<=1800:
        raise ValueError("时间点需为0至1800秒")
    bounds=[float(value) for value in str(region).split(",")]
    if (len(bounds)!=4 or not all(math.isfinite(n) for n in bounds) or min(bounds)<0
            or bounds[2]<=0 or bounds[3]<=0 or bounds[0]+bounds[2]>1 or bounds[1]+bounds[3]>1):
        raise ValueError("区域需为0至1的x,y,宽,高，且不越过画面")
    sources=collect_video_sources(event,SimpleNamespace(extra_user_content_parts=[]))
    if isinstance(source,bool) or not 0<=source<len(sources):raise ValueError("本轮没有这个真实视频附件或链接")
    item=sources[source]
    with tempfile.TemporaryDirectory(prefix="qq-video-detail-") as folder:
        directory=Path(folder)
        offset=0
        if item.page:
            # Read the requested point from the original public video, not a model path.
            from urllib.parse import urlencode, urlunsplit
            parsed=urlsplit(item.url)
            query=parse_qs(parsed.query)
            query["t"]=[str(float(time_seconds))]
            url=urlunsplit(parsed._replace(query=urlencode(query,doseq=True)))
            path,offset,_=await bilibili_file(url,directory,settings)
        elif item.path:path=Path(item.path)
        elif item.url:path=await attachment_file(item.url,directory,settings)
        else:raise ValueError("没有取得可放大的实际视频")
        duration=await _probe_video(path,settings)
        point=float(time_seconds)-offset
        if not 0<=point<duration:raise ValueError("时间点不在本次实际视频范围内")
        raw=await _run(_binary(settings,"ffprobe"),"-v","error","-select_streams","v:0",
            "-show_entries","stream=width,height","-of","json",str(path))
        dimensions=json.loads(raw)["streams"][0]
        x,y,w,h=bounds
        pixel=[int(x*dimensions["width"]),int(y*dimensions["height"]),
               max(40,int(w*dimensions["width"])),max(20,int(h*dimensions["height"]))]
        await skill_run("vendor/vw.py",["read","--media",path,"--times",str(point),"--region",",".join(map(str,pixel)),
            "--zoom","2","--panel","large","--pack","1","--out",directory/"read"],settings,timeout=45)
        panels=sorted((directory/"read/panels").glob("*.png"))
        if not panels:raise ValueError("该区域没有可读取的画面")
        cfg=context.get_config(umo=event.unified_msg_origin)
        pid=cfg.get("provider_settings",{}).get("qqchat_video_provider_id","qqchat-zhipu-vision-backup")
        provider=context.get_provider_by_id(pid)
        if provider is None:raise ValueError("视频视觉接口当前不可用")
        content=[{"type":"text","text":f"原始视频第{float(time_seconds):.3f}秒的实际局部放大图，只确认清楚可读的文字与画面，不猜未提供的部分。"}]
        for panel in panels[:12]:
            content.append({"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(panel.read_bytes()).decode("ascii")}})
        result=await _model_summary(provider,content)
        return {"status":"ready","time_seconds":float(time_seconds),"region":bounds,
                "unread_panels":max(0,len(panels)-12),**result}
