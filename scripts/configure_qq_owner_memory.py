"""Configure only QQ source-preserving memory and native context compression."""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from datetime import UTC,datetime
from pathlib import Path

from scripts.configure_astrbot import _assert_managed_path,_write_json_atomically
from scripts.sync_astrbot_identity import _running

SUMMARY_INSTRUCTION=(
    "用中文整理可继续聊天的会话摘要。保留每件事的发言者标识、引用对象、时间、话题、主人明确约定与最新修正，"
    "不同群友的经历与设备不能混为一人，旧Bot猜测与真人确认严格分开。"
    "保留重要音乐喜恶的原话、实际听歌证据和本鱼感受的区别。"
    "工具结果只记真实完成/失败状态与必要来源，不把接受任务记为已完成。"
    "未完成事项保留最新结果与下一步，不凭摘要补造细节。"
    "这是短上下文摘要，主人的完整原文仍可按需调用qqchat_recall，重要约定不随摘要删除。"
)


def cleanup_unused_learning(root:Path,*,apply=False,is_running=_running):
    """Only obsolete QQ imitation/style data; retain manual notes and music."""
    root=root.resolve(strict=True)
    path=root/"data/qqchat.db"
    _assert_managed_path(root,path)
    tables=("possession_style_messages","possession_style_profiles","group_style_stats","group_style_terms")
    condition="group_id <> '' AND group_id NOT GLOB '*[^0-9]*'"
    counts={}
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as db:
        for table in tables:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                columns={r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                if "group_id" not in columns:
                    raise ValueError("旧学习表结构不符，未清理")
                counts[table]=db.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {condition}').fetchone()[0]
    result={"applied":False,"qq_only_counts":counts}
    if not apply or not any(counts.values()):
        return result
    if is_running():
        raise RuntimeError("先通过服务管理器停止AstrBot，再清理闲置QQ学习数据")
    backup=root/"data/dafeiyu/owner-memory-deployments"/datetime.now(UTC).strftime("legacy-learning-%Y%m%dT%H%M%S%fZ")
    _assert_managed_path(root,backup)
    backup.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        with sqlite3.connect(backup/path.name) as target:
            db.backup(target)
        for table in counts:
            db.execute(f'DELETE FROM "{table}" WHERE {condition}')
    return {**result,"applied":True,"backup_dir":str(backup)}


def configure(root:Path,*,apply=False,is_running=_running):
    root=root.resolve(strict=True)
    target=root/"data/astrbot/instance/data/config/abconf_qqchat_dafeiyu.json"
    capability=root/"data/dafeiyu/luna-context-capability.json"
    policy_path=root/"data/dafeiyu/qq-context-policy.json"
    for path in (target,capability,policy_path):
        _assert_managed_path(root,path)
    metadata=json.loads(capability.read_text(encoding="utf-8-sig"))
    window=metadata.get("limits",{}).get("context_window",0)
    if metadata.get("model")!="gpt-6-luna" or not isinstance(window,int) or window<16000:
        raise ValueError("缺少当前授权通道的Luna默认窗口确认，未改配置")
    limit=min(300000,window)
    policy={"enabled":True,"model":"gpt-6-luna","max_context_tokens":limit,
            "verified_default_context_window":window,"source":"authenticated model metadata",
            "native_compress_threshold":0.82,"experimental_context_enabled":False}
    before=json.loads(target.read_text(encoding="utf-8-sig"))
    after=copy.deepcopy(before)
    runner=after["agent_runner"]
    if runner.get("runner_type")!="local" or runner["config"]["model"]["provider_id"]!="qqchat-chatgpt-luna":
        raise ValueError("QQ现有原生Luna路由不符，未改配置")
    runner["config"]["compression"].update({"max_turns":-1,"trim_turns":1,
        "overflow_strategy":"llm_compress","instruction":SUMMARY_INSTRUCTION,
        "keep_recent_ratio":0.2,"provider_id":"qqchat-chatgpt-luna","fallback_max_tokens":limit})
    result={"applied":False,"model":"gpt-6-luna","max_turns":-1,"qq_context_window":limit,
            "native_compress_near":int(limit*.82),"public_or_experimental_limit_not_assumed":True}
    if not apply:
        return result
    if is_running():
        raise RuntimeError("先通过服务管理器停止AstrBot，再应用QQ记忆配置")
    backup=root/"data/dafeiyu/owner-memory-deployments"/datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    _assert_managed_path(root,backup)
    backup.mkdir(parents=True)
    shutil.copy2(target,backup/target.name)
    if policy_path.exists():
        shutil.copy2(policy_path,backup/policy_path.name)
    try:
        _write_json_atomically(target,after)
        _write_json_atomically(policy_path,policy)
    except Exception:
        shutil.copy2(backup/target.name,target)
        if (backup/policy_path.name).exists():
            shutil.copy2(backup/policy_path.name,policy_path)
        else:
            policy_path.unlink(missing_ok=True)
        raise
    return {**result,"applied":True,"backup_dir":str(backup)}
