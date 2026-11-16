import json
from types import SimpleNamespace

import pytest

from app.astrbot_memes import QQMemes, clean_response, load_catalog


class Event:
    def __init__(self, text="今天终于把项目做完了", platform="aiocqhttp"):
        self.extras = {}
        self.text = text
        self.platform = platform
        self.unified_msg_origin = "qq-local:GroupMessage:fixture"
        self._has_send_oper = False

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_message_str(self):
        return self.text

    def get_platform_name(self):
        return self.platform


@pytest.fixture
def catalog(tmp_path):
    images = []
    for number, name, category in [(64, "庆祝", "happy"), (91, "摸头", "reply"), (103, "点赞", "happy")]:
        path = tmp_path / "memes" / category / f"{number:04d}_{name}.gif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GIF89a")
        images.append({"source_index": number, "path": str(path.relative_to(tmp_path)), "category": category})
    (tmp_path / "source_index.json").write_text(json.dumps({"images": images}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def context(active=True):
    metadata = SimpleNamespace(activated=active, star_cls=SimpleNamespace())
    return SimpleNamespace(get_registered_star=lambda _name: metadata)


def test_concrete_catalog_replaces_random_category_prompt(catalog):
    adapter, event = QQMemes(pack_root=catalog), Event()
    request = SimpleNamespace(system_prompt="人格\n<!-- meme_manager_prompt:start -->旧分类<!-- meme_manager_prompt:end -->")
    assert adapter.prepare(context(), event, request)
    assert "0064=庆祝" in request.system_prompt and "0091=摸头" in request.system_prompt
    assert "旧分类" not in request.system_prompt
    assert event.get_extra("meme_manager_llm_request_origin") == "qqchat_whale_catalog"
    assert str(catalog) not in request.system_prompt


def test_failed_or_invalid_selection_never_becomes_a_file_path(catalog):
    adapter, event = QQMemes(pack_root=catalog), Event()
    adapter.prepare(context(), event, SimpleNamespace(system_prompt=""))
    response = SimpleNamespace(completion_text='这次庆祝一下\n<dafeiyu-meme id="9999"/>', result_chain=None)
    adapter.process_response(event, response)
    assert response.completion_text == "这次庆祝一下"
    assert not event.get_extra("qqchat_whale_meme_pick")
    data = json.loads((catalog / "source_index.json").read_text(encoding="utf-8"))
    data["images"].append({"source_index": 42, "path": "../outside.gif"})
    (catalog / "source_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert "0042" not in load_catalog(catalog)


def test_user_no_images_and_plugin_disable_are_respected(catalog):
    adapter = QQMemes(pack_root=catalog)
    for text in ["只用文字回复", "不要发表情包", "别发图"]:
        event = Event(text)
        assert not adapter.prepare(context(), event, SimpleNamespace(system_prompt=""))
    assert not adapter.prepare(context(False), Event(), SimpleNamespace(system_prompt=""))
    request = SimpleNamespace(system_prompt="B站原提示")
    assert not adapter.prepare(context(), Event(platform="bilibili"), request)
    assert request.system_prompt == "B站原提示"


def test_cooldown_only_records_real_sends_and_explicit_request_can_bypass(catalog):
    adapter, event = QQMemes(pack_root=catalog, clock=lambda: 1000), Event()
    event.set_extra("qqchat_whale_meme_attached", True)
    event.set_extra("qqchat_whale_meme_pick", "0064")
    adapter.mark_sent(event)
    assert not adapter.sent
    event._has_send_oper = True
    adapter.mark_sent(event)
    followup = SimpleNamespace(system_prompt="")
    assert not adapter.prepare(context(), Event(), followup)
    assert "上次已实际发送" in followup.system_prompt and "庆祝" in followup.system_prompt
    assert adapter.prepare(context(), Event("给我发一张大肥鱼表情包"), SimpleNamespace(system_prompt=""))
    assert adapter.prepare(context(), Event("给我配一张摸摸头的表情吧"), SimpleNamespace(system_prompt=""))


def test_chat_faces_and_control_tags_do_not_touch_code_or_quoted_material():
    original = '这下舒服了😅\n<dafeiyu-meme id="0064"/>\n`😅`\n```xml\n<dafeiyu-meme id="0091"/>\n```\n> 引用😓'
    text, selections = clean_response(original)
    assert text.startswith("这下舒服了\n")
    assert selections == ["0064"]
    assert '`😅`' in text and '> 引用😓' in text
    assert '<dafeiyu-meme id="0091"/>' in text
