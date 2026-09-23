import pytest

import app.services.slang as slang
from app.services.web_search import SearchResult


def test_unknown_slang_heuristic_targets_acronyms_not_normal_words():
    assert slang.needs_slang_check("CCB")
    assert slang.needs_slang_check("ccb")
    assert slang.needs_slang_check("欧金金")
    assert not slang.needs_slang_check("hello")
    assert not slang.needs_slang_check("今天一起吃饭吗")


@pytest.mark.asyncio
async def test_unknown_slang_llm_can_mark_insult():
    class FakeLLM:
        async def ask(self, messages):
            return '{"category":"INSULT"}'

    verdict = await slang.classify_unknown_slang("CCB", FakeLLM())
    assert verdict is not None
    assert verdict.delta == -5


@pytest.mark.asyncio
async def test_unknown_slang_can_use_web_evidence_after_unknown(monkeypatch):
    calls = 0

    class FakeLLM:
        async def ask(self, messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                return '{"category":"UNKNOWN"}'
            assert "联网搜索摘要" in messages[-1]["content"]
            return '{"category":"SEVERE"}'

    async def fake_search(query: str, limit: int = 3, timeout: float = 4):
        return [
            SearchResult(
                title="网络词解释",
                url="https://example.com/slang",
                snippet="该缩写在这个语境中用于辱骂他人。",
            )
        ]

    monkeypatch.setattr(slang, "search_web", fake_search)
    verdict = await slang.classify_unknown_slang("CCB", FakeLLM())
    assert verdict is not None
    assert verdict.delta == -10
    assert calls == 2
