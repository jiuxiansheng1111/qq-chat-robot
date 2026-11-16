import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import unquote

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("image_only", [False, True])
async def test_actual_whale_gif_reaches_native_qq_payload(monkeypatch, tmp_path, image_only):
    from test_astrbot_native_onebot import (
        _find_project_root,
        _load_astrbot_types,
        _make_group_event,
    )

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-whale-gif"))
    types = _load_astrbot_types(root, monkeypatch)
    from astrbot.core.message.message_event_result import ResultContentType
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest

    from app.astrbot_memes import QQMemes, load_catalog

    async def record_send_flag(self, _message):
        # Keep the real aiocqhttp serializer/transport call, without starting
        # unrelated global history database jobs in this isolated test.
        self._has_send_oper = True

    monkeypatch.setattr(AstrMessageEvent, "send", record_send_flag)

    pack = root / "data/astrbot/instance/data/plugin_data/meme_manager/packs/blue-fat-fish-meme-pack"
    if not pack.is_dir():
        pytest.skip("The separately installed whale pack is not available")
    event, api, _raw = _make_group_event(types)
    event.message_obj.group_id = "12345"
    adapter = QQMemes(pack_root=pack)
    context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True, star_cls=SimpleNamespace()))
    request = ProviderRequest(prompt="今天面试通过了", system_prompt="当前QQ人格")
    assert adapter.prepare(context, event, request)
    response = LLMResponse(role="assistant", completion_text=("" if image_only else "这下可以庆祝了😅\n") + '<dafeiyu-meme id="0064"/>')
    adapter.process_response(event, response)
    assert "dafeiyu-meme" not in response.completion_text and "😅" not in response.completion_text
    result = event.chain_result(response.result_chain.chain) if image_only else event.plain_result(response.completion_text)
    result.set_result_content_type(ResultContentType.LLM_RESULT)
    event.set_result(result)
    assert adapter.decorate(event)
    assert not adapter.decorate(event)  # repeated hooks cannot attach a second image
    assert len([part for part in result.chain if isinstance(part, types.Image)]) == 1

    async def send_group_msg(**params):
        return await api.call_action("send_group_msg", **params)

    event.bot.send_group_msg = send_group_msg
    await event.send(result)
    adapter.mark_sent(event)
    assert len(api.calls) == 1 and api.calls[0][0] == "send_group_msg"
    image = next(part for part in api.calls[0][1]["message"] if part["type"] == "image")
    sent_file = image["data"]["file"]
    original = load_catalog(pack)["0064"].path
    if sent_file.startswith("base64://"):
        assert hashlib.sha256(base64.b64decode(sent_file[9:])).hexdigest() == hashlib.sha256(original.read_bytes()).hexdigest()
    else:
        assert original.name in unquote(sent_file)
    assert adapter.sent
