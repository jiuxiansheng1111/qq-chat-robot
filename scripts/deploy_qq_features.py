"""Managed deployment of the user-authorized QQ changes, preserving Bilibili."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from scripts.cleanup_qq_memory import _write_store, cleanup, manual_memory_digest
from scripts.configure_astrbot import _assert_managed_path
from scripts.configure_qq_persona import configure
from scripts.configure_qq_participation import configure as configure_participation
from scripts.configure_qq_feature_ownership import configure as configure_feature_ownership
from scripts.configure_qq_owner_memory import configure as configure_owner_memory
from scripts.configure_qq_vision import configure as configure_vision

DATA=ROOT/"data/astrbot/instance/data"


def windows_env():
    # Python launched from PowerShell 7 inherits its module path. Windows
    # PowerShell 5 then cannot autoload Get-FileHash without its native modules.
    env=dict(os.environ)
    env["PSModulePath"]=str(Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/Modules")+";"+str(
        Path(os.environ.get("ProgramFiles","C:/Program Files"))/"WindowsPowerShell/Modules")
    return env


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def lifecycle(output,phase,flag):
    with (output/(phase+".log")).open("wb") as stream:
        process=subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",
            str(ROOT/"scripts/run_astrbot.ps1"),flag],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
            timeout=180,creationflags=subprocess.CREATE_NO_WINDOW,check=False,env=windows_env())
    print(json.dumps({"phase":phase,"exit_code":process.returncode}),flush=True)
    return process.returncode


def legacy_hash():
    with sqlite3.connect((DATA/"data_v4.db").as_uri()+"?mode=ro",uri=True) as db:
        row=db.execute("SELECT * FROM personas WHERE persona_id='qqchat-local-persona'").fetchone()
    return hashlib.sha256(json.dumps(row,ensure_ascii=False).encode()).hexdigest()


def restart_owned_bridge():
    # A daemon can inherit the parent's pipe handles. Use a file so waiting for
    # PowerShell to exit never also waits for the long-lived Node service.
    with (ROOT/"logs/netease-restart.log").open("wb") as stream:
        subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",
            str(ROOT/"scripts/restart_netease_bridge.ps1")],cwd=ROOT,check=True,
            stdout=stream,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW,env=windows_env())


def _run_astrbot_health_probe() -> subprocess.CompletedProcess[bytes]:
    command = """$ErrorActionPreference='Stop'
. './scripts/astrbot_windows_common.ps1'
. './scripts/astrbot_process.ps1'
. './scripts/astrbot_lifecycle.ps1'
Get-AstrbotHealth -Layout (Get-AstrbotLayout) | ConvertTo-Json -Compress
"""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command], cwd=ROOT, capture_output=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW, env=windows_env(),
    )


def qq_connection_status():
    """Read the actual QQ transport; a responsive dashboard is insufficient."""
    from scripts.connect_astrbot_qq import (
        _get_onebot_self_id,
        _new_http_client,
        _response_body,
        load_onebot_accounts,
    )

    result={"identity_matches":False,"napcat_online":False,"reverse_ws_connected":False}
    try:
        account=load_onebot_accounts(ROOT/".env",selection="primary")[0]
        with _new_http_client() as client:
            _get_onebot_self_id(client,account)
            result["identity_matches"]=True
            body=_response_body(client.post(account.api_base.rstrip("/")+"/get_status",
                json={},headers={"Authorization":f"Bearer {account.access_token}"}))
            status=body.get("data",{})
            result["napcat_online"]=status.get("online") is True and status.get("good") is True
        probe=_run_astrbot_health_probe()
        if probe.returncode==0:
            health=json.loads(probe.stdout.decode("utf-8-sig"))
            result["reverse_ws_connected"]=health.get("Status")=="healthy"
    except Exception:
        # Diagnostics must not echo a credential, account ID, or private response.
        pass
    return result


def deploy():
    output=ROOT/"data/dafeiyu/deployments"/datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True)
    owned=[DATA/"config"/name for name in (
        "astrbot_plugin_qq_chatrobot_config.json","astrbot_plugin_private_companion_config.json",
        "meme_manager_config.json","abconf_qqchat_dafeiyu.json")]
    meme=DATA/"plugin_data/meme_manager"
    owned += [meme/"registry.json",meme/"selection_rules.json"]
    protected=[ROOT/".env",ROOT/"prompts/persona.txt",ROOT/"prompts/examples.jsonl",DATA/"cmd_config.json"]
    protected += [p for p in (ROOT/"data/mahjong/account").glob("credentials*") if p.is_file()]
    fingerprints={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    original=legacy_hash()
    bili_path=DATA/"config/astrbot_plugin_bilibili_ai_bot_config.json"
    bili_before=read(bili_path)
    _write_store(output/"bili-before.json",bili_before)
    for index,path in enumerate(owned):
        if path.exists():
            shutil.copy2(path,output/(str(index)+"-"+path.name))
    stage=ROOT/"data/dafeiyu/meme-stage/data/plugin_data/meme_manager"
    pack="blue-fat-fish-meme-pack"
    if not (stage/"packs"/pack/"manifest.json").is_file():
        raise RuntimeError("Stage the real meme pack before deploying")
    if lifecycle(output,"stop","-Stop") and lifecycle(output,"stop-retry","-Stop"):
        raise RuntimeError("Managed stop failed; no configuration changed")
    result={}
    try:
        manual_before=manual_memory_digest(ROOT)
        result["memory"]=cleanup(ROOT,apply=True)
        result["feature_ownership"]=configure_feature_ownership(ROOT,apply=True)
        result["persona"]=configure(ROOT,apply=True)
        result["vision"]=configure_vision(ROOT,apply=True)
        # The vision configurator only adds its dedicated provider registration to
        # the global file; existing defaults/providers remain unchanged.
        fingerprints[str(DATA/"cmd_config.json")]=hashlib.sha256((DATA/"cmd_config.json").read_bytes()).hexdigest()
        result["group_participation"]=configure_participation(ROOT,apply=True)
        if (ROOT/"data/dafeiyu/owner-memory-config.json").is_file():
            result["owner_context"]=configure_owner_memory(ROOT,apply=True)
        profile=DATA/"config/abconf_qqchat_dafeiyu.json"
        value=read(profile)
        value.setdefault("provider_settings",{})["qqchat_local_soundtry"]=True
        value["provider_settings"]["qqchat_video_provider_id"]="qqchat-zhipu-vision-backup"
        _write_store(profile,value)
        path=DATA/"config/astrbot_plugin_qq_chatrobot_config.json"
        value=read(path)
        from app.astrbot_listening import ALIASES
        value["group_chat_bot_names"]=list(dict.fromkeys([*value.get("group_chat_bot_names",[]),*ALIASES]))
        value["group_chat_trigger_keywords"]=list(dict.fromkeys([*value.get("group_chat_trigger_keywords",[]),
            "大肥鱼","一起听","听歌","音频分析","雀魂分析"]))
        _write_store(path,value)
        path=DATA/"config/meme_manager_config.json"
        value=read(path)
        gen=value.setdefault("generation",{})
        gen.setdefault("emotion",{}).update(probability=35,max_memes_per_message=1)
        gen.setdefault("message",{}).update(enable_mixed=True,mixed_probability=100)
        gen.setdefault("prompt",{})["quantity_guidance_enabled"]=False
        # Native QQ uses normal chat hooks. Do not turn other plugin LLMs into
        # public meme senders, which could affect unrelated platform services.
        _write_store(path,value)
        target=meme/"packs"/pack
        _assert_managed_path(ROOT,target)
        if not target.exists():
            shutil.copytree(stage/"packs"/pack,target)
        registry=read(meme/"registry.json")
        entry=next(p for p in read(stage/"registry.json")["installed_packs"] if p["id"]==pack)
        registry["installed_packs"]=[p for p in registry["installed_packs"] if p["id"]!=pack]+[entry]
        _write_store(meme/"registry.json",registry)
        rules=read(meme/"selection_rules.json")
        rules["rules"]=[r for r in rules["rules"] if r.get("id")!="qq-dafeiyu"]
        rules["rules"].insert(0,{"id":"qq-dafeiyu","scope":"persona","target":"大肥鱼","pack_ids":[pack]})
        _write_store(meme/"selection_rules.json",rules)
        assert manual_memory_digest(ROOT)==manual_before,"Manual memory changed"
        assert legacy_hash()==original,"Bili persona changed"
        assert {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}==fingerprints,"Protected configuration changed"
        assert read(bili_path)==bili_before,"Bili config changed during deployment"
        restart_owned_bridge()
        result["protected_files_unchanged"]=True
        result["manual_memories_preserved"]=manual_before[1]
    finally:
        if lifecycle(output,"start","-ReuseExistingConfig"):
            raise RuntimeError("Managed start failed; inspect deployment start.log")
    bili_after=read(bili_path)
    result["bili_config_changed_keys_on_start"]=[k for k in bili_before.keys()|bili_after.keys() if bili_before.get(k)!=bili_after.get(k)]
    result["bili_persona_unchanged"]=legacy_hash()==original
    result["restarted"]=True
    result["qq_connection"]=qq_connection_status()
    result["output_dir"]=str(output)
    _write_store(output/"result.json",result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not all(result["qq_connection"].values()):
        raise RuntimeError("AstrBot已启动，但QQ消息连接未就绪；检查NapCat运行与登录，不能视为部署验收通过")


if __name__=="__main__":
    deploy()
