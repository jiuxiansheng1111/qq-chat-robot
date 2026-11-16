import asyncio
import json
import sqlite3

import pytest
from test_owner_memory import event

from app.services import qq_context_policy as policy
from app.services.usage_ledger import UsageLedger, bind_event


def test_actual_counters_missing_values_and_dedupe(tmp_path):
    ledger=UsageLedger(tmp_path/"ledger.sqlite3")
    context={"scope_hash":"scope-a","turn_hash":"turn-a","owner":1,"purpose":"chat"}
    ledger.record({"id":"resp-one","usage":{"input_tokens":1000,"output_tokens":100,"total_tokens":1100,
        "input_tokens_details":{"cached_tokens":800},"output_tokens_details":{"reasoning_tokens":30}}},
        provider="provider",model="gpt-6-luna",context=context)
    ledger.record({"id":"resp-one","usage":{"input_tokens":99999}},provider="provider",model="gpt-6-luna",context=context)
    ledger.record({"id":"resp-two"},provider="provider",model="gpt-6-luna",context=context)
    ledger.record(None,provider="provider",model="gpt-6-luna",context=context,error_type="TimeoutError")
    r=ledger.report(scope_hash="scope-a")
    assert r["calls"]==3 and r["unknown_usage_calls"]==2 and r["failed_calls"]==1
    assert r["known_sums"]["total_tokens"]==1100
    assert r["known_sums"]["cached_tokens"]==800
    assert r["coverage"]["cache_write_tokens"]==0 and r["known_sums"]["cache_write_tokens"] is None
    assert ledger.report(scope_hash="other")["calls"]==0


def test_payload_text_and_identifiers_are_not_stored(tmp_path):
    path=tmp_path/"ledger.sqlite3"
    ledger=UsageLedger(path)
    ledger.record({"id":"opaque-private-response-id","usage":{"input_tokens":-1,"total_tokens":True},
                   "output":[{"text":"fixture-secret-password"}]},provider="provider",model="gpt-6-luna")
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT input_tokens,total_tokens FROM usage_calls").fetchone()
        assert row==(None,None)
    raw=path.read_bytes()
    assert b"fixture-secret" not in raw and b"opaque-private-response-id" not in raw


async def test_qq_window_is_request_local_and_does_not_modify_shared_provider(monkeypatch,tmp_path,owner_enabled):
    config_path=tmp_path/"context-policy.json"
    config_path.write_text(json.dumps({"enabled":True,"model":"gpt-6-luna","max_context_tokens":272000,
        "verified_default_context_window":272000}),encoding="utf-8")
    monkeypatch.setattr(policy,"POLICY_PATH",config_path)
    config=policy.ScopedProviderConfig({"model":"gpt-6-luna","max_context_tokens":128000})
    ready=asyncio.Event()
    async def qq():
        bind_event(event())
        ready.set()
        await asyncio.sleep(0)
        assert config.get("max_context_tokens")==272000
        assert config["max_context_tokens"]==272000
    async def bili():
        await ready.wait()
        bind_event(event(platform="bilibili"))
        await asyncio.sleep(0)
        assert config.get("max_context_tokens")==128000
    await asyncio.gather(qq(),bili())
    assert dict(config)["max_context_tokens"]==128000


@pytest.mark.parametrize("text,expected",[
    ("先吃饭；再听歌。","先吃饭，再听歌"),
    ('引文“甲；乙。”和代码`for(;;)`。','引文“甲；乙。”和代码`for(;;)`'),
    ("```js\nfor(;;){}\n```","```js\nfor(;;){}\n```"),
    ("https://example.com/a;b","https://example.com/a;b"),
    ("先确定是哪个设备？】【。","先确定是哪个设备？"),
])
def test_natural_punctuation_preserves_literals(text,expected):
    from app.services.chat_punctuation import natural_chat_text
    assert natural_chat_text(text)==expected
