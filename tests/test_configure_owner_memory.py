import json
import sqlite3
from pathlib import Path

import pytest

from scripts.configure_qq_owner_memory import cleanup_unused_learning, configure


@pytest.fixture
def workspace(tmp_path):
    data=tmp_path/"data"
    target=data/"astrbot/instance/data/config/abconf_qqchat_dafeiyu.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"agent_runner":{"runner_type":"local","config":{
        "model":{"provider_id":"qqchat-chatgpt-luna"},"compression":{"max_turns":100,"overflow_strategy":"truncate_by_turns"}}},
        "unrelated":{"keep":"value"}}),encoding="utf-8")
    (data/"dafeiyu").mkdir()
    (data/"dafeiyu/luna-context-capability.json").write_text(json.dumps({"model":"gpt-6-luna",
        "limits":{"context_window":272000,"max_context_window":872000}}),encoding="utf-8")
    (target.parent/"bili.json").write_text("unchanged",encoding="utf-8")
    return tmp_path,target


def test_qq_context_only_uses_verified_default_and_requires_managed_stop(workspace):
    root,target=workspace
    original=target.read_bytes()
    result=configure(root)
    assert not result["applied"] and result["native_compress_near"]==223040
    assert target.read_bytes()==original
    with pytest.raises(RuntimeError,match="停止"):
        configure(root,apply=True,is_running=lambda:True)
    applied=configure(root,apply=True,is_running=lambda:False)
    assert applied["applied"]
    cfg=json.loads(target.read_text(encoding="utf-8-sig"))
    assert cfg["agent_runner"]["config"]["compression"]["max_turns"]==-1
    assert cfg["agent_runner"]["config"]["compression"]["overflow_strategy"]=="llm_compress"
    assert cfg["unrelated"]=={"keep":"value"}
    assert (target.parent/"bili.json").read_text()=="unchanged"
    policy=json.loads((root/"data/dafeiyu/qq-context-policy.json").read_text(encoding="utf-8-sig"))
    assert policy["max_context_tokens"]==272000 and not policy["experimental_context_enabled"]


def test_cleanup_only_obsolete_qq_learning_not_wechat_manual_notes_or_heard_songs(workspace):
    root,_=workspace
    path=root/"data/qqchat.db"
    with sqlite3.connect(path) as db:
        for table in ("possession_style_messages","possession_style_profiles","group_style_stats","group_style_terms"):
            db.execute(f'CREATE TABLE "{table}"(group_id TEXT, content TEXT)')
            db.executemany(f'INSERT INTO "{table}" VALUES(?,?)',[("12345","unused imitation"),("wechat:room","preserve")])
        for table in ("long_term_memories","group_memories","music_listens"):
            db.execute(f'CREATE TABLE "{table}"(content TEXT)')
            db.execute(f'INSERT INTO "{table}" VALUES(?)',("original manual source",))
    preview=cleanup_unused_learning(root)
    assert not preview["applied"] and all(n==1 for n in preview["qq_only_counts"].values())
    result=cleanup_unused_learning(root,apply=True,is_running=lambda:False)
    assert Path(result["backup_dir"]).joinpath("qqchat.db").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT group_id FROM possession_style_messages").fetchall()==[("wechat:room",)]
        assert db.execute("SELECT content FROM music_listens").fetchall()==[("original manual source",)]
        assert db.execute("SELECT content FROM long_term_memories").fetchall()==[("original manual source",)]


@pytest.mark.parametrize("window",[0,8192])
def test_selected_model_preserved_without_luna_metadata(workspace,window):
    root,target=workspace
    cfg=json.loads(target.read_text(encoding="utf-8"))
    cfg["agent_runner"]["config"]["model"]["provider_id"]="qqchat-brain-zhipu"
    cfg["agent_runner"]["config"]["compression"]["fallback_max_tokens"]=272000
    target.write_text(json.dumps(cfg),encoding="utf-8")
    (root/"data/dafeiyu/luna-context-capability.json").unlink()
    source={"id":"selected-source","model":"glm-test","max_context_tokens":window}
    provider={"id":"qqchat-brain-zhipu","provider_source_id":"selected-source","enable":True}
    (target.parent.parent/"cmd_config.json").write_text(json.dumps({"provider":[provider],"provider_sources":[source]}),encoding="utf-8")
    assert configure(root,apply=True,is_running=lambda:False)["model"]=="glm-test"
    updated=json.loads(target.read_text(encoding="utf-8-sig"))["agent_runner"]["config"]
    assert updated["model"]["provider_id"]==updated["compression"]["provider_id"]=="qqchat-brain-zhipu"
    assert updated["compression"].get("fallback_max_tokens",0)==window
    assert not json.loads((root/"data/dafeiyu/qq-context-policy.json").read_text(encoding="utf-8-sig"))["enabled"]
