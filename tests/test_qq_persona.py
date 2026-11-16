import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from app.astrbot_identity import QQ_PERSONA_PATH
from app.astrbot_qq_persona import apply_qq_persona
from scripts.configure_qq_persona import PROFILE_FILE, configure


def test_qq_final_prompt_removes_only_managed_personality_keeps_tool_results():
    old = "你是ChatGPT的龙女仆。旧人格的完整内容。"
    context = NS(persona_manager=NS(personas_v3=[{"name": "qqchat-local-persona", "prompt": old}]))
    managed = {"type": "text", "_private_companion_turn_fragments": True, "text":
               '<section title="回复风格约束"><![CDATA[旧20字、龙女仆]]></section>\n'
               '<section title="本轮技术推理约束">必须核对牌谱数据</section>\n'
               '<section title="群聊上下文"><message>叫他龙女仆吗</message></section>'}
    external = {"type": "text", "text": '<section title="回复风格约束">这是用户引用</section>'}
    tools = object()
    history = [{"role": "tool", "tool_call_id": "call1", "content": "真实报告和数字：120"}]
    request = NS(system_prompt=old + "\n只在允许的会话里调用工具", prompt="用户原文：你是谁", contexts=history,
                 extra_user_content_parts=[managed, external], func_tool=tools)
    assert apply_qq_persona(context, NS(get_platform_name=lambda: "aiocqhttp"), request)
    assert old not in request.system_prompt
    assert "大肥鱼" in request.system_prompt and "只在允许的会话里调用工具" in request.system_prompt
    result = request.extra_user_content_parts
    assert "旧20字" not in result[0]["text"]
    assert "必须核对牌谱数据" in result[0]["text"] and "叫他龙女仆吗" in result[0]["text"]
    assert "旧20字" in managed["text"]  # no mutation of plugin's cached source
    assert result[1] is external and request.contexts is history and request.func_tool is tools
    assert request.prompt == "用户原文：你是谁"
    before = request.system_prompt
    assert not apply_qq_persona(context, NS(get_platform_name=lambda: "aiocqhttp"), request)
    assert request.system_prompt == before


@pytest.mark.parametrize("platform", ["bilibili", "weixin_oc", "webchat"])
def test_non_qq_requests_unchanged(platform):
    request = NS(system_prompt="原人格", prompt="原提问", extra_user_content_parts=[])
    before = copy.deepcopy(vars(request))
    assert not apply_qq_persona(NS(), NS(get_platform_name=lambda: platform), request)
    assert vars(request) == before


def test_companion_fallback_placements_only_clean_exact_owned_blocks():
    owned = '<section title="回复风格约束">旧人格</section><section title="事实">保留数据</section>'
    request = NS(system_prompt="系统里A&B合法\n" + owned, prompt="用户A<B\n" + owned,
                 _private_companion_conversation_plan_system_text=owned,
                 _private_companion_conversation_plan_turn_text=owned)
    apply_qq_persona(NS(), NS(get_platform_name=lambda: "onebot"), request)
    assert "旧人格" not in request.system_prompt and "旧人格" not in request.prompt
    assert "系统里A&B合法" in request.system_prompt and request.prompt.startswith("用户A<B")
    assert "保留数据" in request.system_prompt and "保留数据" in request.prompt


@pytest.fixture
def persona_workspace(tmp_path):
    data = tmp_path / "data/astrbot/instance/data"
    (data / "config").mkdir(parents=True)
    prompt = tmp_path / "prompts/qq/dafeiyu.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_bytes(QQ_PERSONA_PATH.read_bytes())
    config = {"platform": [{"id":"qq-test", "type":"aiocqhttp", "enable":True},
                            {"id":"wx-test", "type":"weixin_oc", "enable":True}],
              "agent_runner":{"runner_type":"local", "config":{"persona":{"persona_id":"qqchat-local-persona"}}},
              "provider":[{"model":"gpt-6-luna", "key":"synthetic"}], "wake_prefix":["旧昵称"]}
    (data / "cmd_config.json").write_text(json.dumps(config), encoding="utf-8")
    (data / "config/bili.json").write_text('{"USE_ASTRBOT_PERSONA":true}', encoding="utf-8")
    with sqlite3.connect(data / "data_v4.db") as db:
        db.executescript('''
        CREATE TABLE personas (created_at TEXT,updated_at TEXT,persona_id TEXT PRIMARY KEY,system_prompt TEXT,begin_dialogs TEXT,tools TEXT,skills TEXT,sort_order INTEGER);
        CREATE TABLE preferences (created_at TEXT,updated_at TEXT,scope TEXT,scope_id TEXT,key TEXT,value TEXT);
        CREATE TABLE conversations (platform_id TEXT,persona_id TEXT,content TEXT);
        INSERT INTO personas VALUES ('old','old','qqchat-local-persona','B站旧人格','["旧台词"]','["safe_tool"]','[]',0);
        INSERT INTO conversations VALUES ('qq-test','qqchat-local-persona','聊天记录不能删');
        INSERT INTO conversations VALUES ('wx-test','qqchat-local-persona','微信记录');
        ''')
    return tmp_path, data


def test_deploy_only_routes_qq_and_preserves_bili_model_permissions_and_history(persona_workspace):
    root, data = persona_workspace
    config_before = (data / "cmd_config.json").read_bytes()
    bili_before = (data / "config/bili.json").read_bytes()
    with sqlite3.connect(data / "data_v4.db") as db:
        old = db.execute("SELECT * FROM personas").fetchall()
    assert not configure(root)["applied"]
    with pytest.raises(RuntimeError, match="停止"):
        configure(root, apply=True, is_running=lambda: True)
    assert configure(root, apply=True, is_running=lambda: False)["applied"]
    assert (data / "cmd_config.json").read_bytes() == config_before
    assert (data / "config/bili.json").read_bytes() == bili_before
    profile = json.loads((data / "config" / PROFILE_FILE).read_text(encoding="utf-8-sig"))
    assert profile["provider"] == json.loads(config_before)["provider"]
    assert profile["agent_runner"]["config"]["persona"]["persona_id"] == "大肥鱼"
    assert profile["provider_settings"]["persona_pool"] == ["大肥鱼"]
    with sqlite3.connect(data / "data_v4.db") as db:
        assert db.execute("SELECT * FROM personas WHERE persona_id='qqchat-local-persona'").fetchall() == old
        new = db.execute("SELECT begin_dialogs,tools,skills FROM personas WHERE persona_id='大肥鱼'").fetchone()
        assert new == ('[]', '["safe_tool"]', '[]')
        assert db.execute("SELECT persona_id,content FROM conversations WHERE platform_id='qq-test'").fetchone() == ('大肥鱼', '聊天记录不能删')
        assert db.execute("SELECT persona_id,content FROM conversations WHERE platform_id='wx-test'").fetchone() == ('qqchat-local-persona', '微信记录')
        routes = json.loads(db.execute("SELECT value FROM preferences WHERE key='umop_config_routing'").fetchone()[0])["val"]
        assert routes == {'qq-test::':'qqchat-dafeiyu'}
