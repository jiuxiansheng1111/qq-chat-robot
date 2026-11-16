"""统一菜单只列当前可用的调用和设置入口。"""

from types import SimpleNamespace

import pytest

from app.astrbot_menu import build_unified_menu_groups
from app.astrbot_menu_files import _MenuAudience


@pytest.mark.parametrize("text,expected", [
    ("网易云帮助", ("网易云音乐",)), ("请给我视频说明", ("视频与看图",)),
    ("/唱歌用法", ("语音与唱歌",)), ("恶魔模式帮助", ()),
    ("听听菜单这首歌", ()), ("不要给我视频帮助", ()), ("他说‘视频帮助’是什么意思", ()),
])
def test_help_routes_to_actual_topic_without_intercepting_chitchat(text, expected):
    from app.astrbot_menu import menu_request_kind
    from app.services.qq_feature_menu import requested_topic, select_text_groups

    assert requested_topic(text) == expected
    if expected:
        assert menu_request_kind(text) == "text"
        groups = ((expected[0], ("实际说明",)), ("其他功能", ("另一项",)))
        assert select_text_groups(groups, text) == groups[:1]


def test_disabled_join_does_not_advertise_a_room_connection():
    from app.services.qq_feature_menu import core_groups

    _, groups = core_groups(settings=SimpleNamespace(netease_member_enabled=False))
    music = dict(groups)["网易云音乐"]
    assert any("真实音轨" in line for line in music)
    assert not any("邀请" in line or "房间留言" in line for line in music)


@pytest.mark.parametrize("sharing_conflict", [True, False])
async def test_menu_preparation_recovers_only_transient_windows_file_locks(monkeypatch, tmp_path, sharing_conflict):
    from app import astrbot_menu_files as files
    source = tmp_path / "cache.jpg"
    source.write_bytes(b"isolated fixture")
    monkeypatch.setattr("app.astrbot_menu.build_unified_menu_groups", lambda *args: ((), ()))
    async def prepare(*args, **kwargs):
        return source
    monkeypatch.setattr("app.services.help_menu.prepare_help_menu_file", prepare)
    publications = []
    failed = False
    def publish(path, name):
        nonlocal failed
        if not failed:
            failed = True
            error = PermissionError("synthetic file conflict")
            error.winerror = 32 if sharing_conflict else 5
            raise error
        publications.append(name)
    monkeypatch.setattr(files, "_publish_menu_file", publish)
    context = SimpleNamespace(get_config=dict)
    if sharing_conflict:
        assert await files.prepare_default_menu_files(context, object()) == 8
        assert len(publications) == 8 and "menu-qq-private.jpg" in publications
    else:
        with pytest.raises(PermissionError):
            await files.prepare_default_menu_files(context, object())
        assert not publications


def command(plugin, name, *, original="", description="", display_name=""):
    return {
        "names": [name], "primary": name, "plugin": SimpleNamespace(reserved=False),
        "plugin_name": plugin, "display_name": display_name or plugin,
        "description": description, "module_path": plugin, "handler_name": "",
        "is_group": False, "native_ids": set(), "original_command_name": original,
    }


def test_passive_plugins_and_unavailable_management_do_not_fill_the_menu(monkeypatch):
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(object(), audience)
    titles = {title for title, *_ in images}
    assert not {"主动消息", "自主学习", "学习设置", "表情包", "日常陪伴", "管理", "AstrBot 聊天"} & titles
    assert not any("插件页面" in line or "完整指令" in line for _, lines in texts for line in lines)


def test_plugin_menu_keeps_common_actions_and_settings_with_short_labels(monkeypatch):
    items = [
        command("astrbot_plugin_lifesim", "人生开启", description="very long internal manual\nimplementation details"),
        command("astrbot_plugin_lifesim", "人生加点"),
        command("astrbot_plugin_lifesim", "人生世界观"),
        command("astrbot_plugin_private_companion", "陪伴"),
        command("astrbot_plugin_private_companion", "陪伴群"),
        command("astrbot_plugin_self_learning", "start_learning"),
        command("astrbot_plugin_self_learning", "clean_rag_cache"),
    ]
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: items)
    audience = _MenuAudience("aiocqhttp", admin=True, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(object(), audience)
    combined = "\n".join(line for _, lines in texts for line in lines)
    assert "/人生开启" in combined
    assert "/人生加点" in combined
    assert "/陪伴群：群聊状态与开关" in combined
    assert "/陪伴：" not in combined
    assert "/start_learning" in combined
    assert "clean_rag_cache" not in combined
    assert "人生世界观" not in combined
    assert "internal manual" not in combined
    assert not any("完整指令" in line or "插件学习面板" in line for _, lines, _ in images for line in lines)


def test_renamed_commands_keep_current_names_and_empty_plugin_groups_disappear(monkeypatch):
    items = [command("astrbot_plugin_lifesim", "开始我的人生", original="人生开启")]
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: items)
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    _, texts = build_unified_menu_groups(object(), audience)
    assert any(title == "模拟人生" and any("/开始我的人生" in line for line in lines) for title, lines in texts)
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    images, texts = build_unified_menu_groups(object(), audience)
    assert "模拟人生" not in {title for title, *_ in images}
    assert "模拟人生" not in {title for title, _ in texts}


def test_compact_menu_keeps_core_features_and_short_text(monkeypatch):
    from app.services.help_menu import COMPACT_MENU_SUBTITLE, COMPACT_MENU_TITLE, concise_text_menu

    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(object(), audience)
    text = concise_text_menu(texts, title=COMPACT_MENU_TITLE, subtitle=COMPACT_MENU_SUBTITLE)
    assert len(images) == 8
    assert all(len(lines) <= 3 for _, lines, _ in images)
    assert len(text) < 4200
    assert "发一起听邀请" in "\n".join(line for _, lines, _ in images for line in lines)
    assert "双方需不同网易云账号" in text and "具体时间和区域追问" in text
    assert "MyGO" not in text and "Ave Mujica" not in text


def test_compact_menu_image_fits_one_phone_view(monkeypatch, tmp_path):
    from app.services.help_menu import COMPACT_MENU_SUBTITLE, COMPACT_MENU_TITLE, _draw_menu

    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    images, _ = build_unified_menu_groups(object(), audience)
    image = _draw_menu(tmp_path / "missing.png", images, COMPACT_MENU_TITLE, COMPACT_MENU_SUBTITLE)
    assert image.width == 1080
    assert image.height <= 1450


def plugin_context(*, active=True):
    names = (
        "astrbot_plugin_lifesim", "astrbot_plugin_private_companion",
        "astrbot_plugin_self_learning", "astrbot_plugin_proactive_chat",
        "astrbot_plugin_qq_group_daily_analysis",
        "astrbot_plugin_image_companion",
    )
    return SimpleNamespace(get_all_stars=lambda: [
        SimpleNamespace(name=name, activated=active, star_cls=object(), reserved=False, support_platforms=[])
        for name in names
    ])


def test_image_menu_includes_all_enabled_plugin_functions_and_settings(monkeypatch, tmp_path):
    from app.services.help_menu import COMPACT_MENU_SUBTITLE, COMPACT_MENU_TITLE, _draw_menu

    items = [
        command("astrbot_plugin_lifesim", "人生开启"),
        command("astrbot_plugin_private_companion", "陪伴群"),
        command("astrbot_plugin_private_companion", "陪伴"),
        command("astrbot_plugin_self_learning", "learning_status"),
        command("astrbot_plugin_self_learning", "start_learning"),
        command("astrbot_plugin_self_learning", "stop_learning"),
        command("astrbot_plugin_qq_group_daily_analysis", "群分析"),
        command("astrbot_plugin_qq_group_daily_analysis", "群漫画"),
        command("astrbot_plugin_qq_group_daily_analysis", "分析设置"),
        command("astrbot_plugin_image_companion", "画图工作流"),
    ]
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: items)
    audience = _MenuAudience("aiocqhttp", admin=True, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(plugin_context(), audience)
    image_text = "\n".join(line for _, lines, _ in images for line in lines)
    text = "\n".join(line for _, lines in texts for line in lines)
    for entry in ("模拟人生：/人生开启", "日常陪伴：/陪伴群", "学习设置：/learning_status",
                  "主动消息：", "群聊分析：/群分析", "群报设置：/分析设置"):
        assert entry in image_text
    assert "面板设置" in image_text
    assert "生图：/陪伴 生图 画面描述" in image_text and "自拍：/陪伴 自拍" in image_text
    assert "画图工作流" not in image_text and "画图工作流" not in text
    assert "/start_learning" in text and "/stop_learning" in text
    assert len(images) == 11
    assert all(len(lines) <= 3 for _, lines, _ in images)
    image = _draw_menu(tmp_path / "missing.png", images, COMPACT_MENU_TITLE, COMPACT_MENU_SUBTITLE)
    assert image.height <= 1900


def test_members_see_plugin_features_without_admin_commands_and_disabled_plugins_disappear(monkeypatch):
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(plugin_context(), audience)
    combined = "\n".join(line for _, lines, _ in images for line in lines)
    combined += "\n".join(line for _, lines in texts for line in lines)
    assert "学习设置" in combined and "群聊分析" in combined and "主动消息" in combined
    assert "管理员设置" in combined
    assert "/群分析" not in combined and "/start_learning" not in combined

    images, texts = build_unified_menu_groups(plugin_context(active=False), audience)
    assert len(images) == 8
    assert not any(title.startswith("插件") for title, *_ in images)
    assert not any("主动消息" in line or "学习设置" in line for _, lines in texts for line in lines)


def test_group_photo_menu_labels_private_only_entry(monkeypatch):
    items = [command("astrbot_plugin_private_companion", "陪伴")]
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: items)
    context = plugin_context()
    companion = SimpleNamespace(require_private_opt_in=True, config={})
    original_stars = context.get_all_stars()
    for star in original_stars:
        if star.name == "astrbot_plugin_private_companion":
            star.star_cls = companion
    context.get_all_stars = lambda: original_stars
    audience = _MenuAudience("aiocqhttp", admin=True, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(context, audience)
    combined = "\n".join(line for _, lines, _ in images for line in lines)
    assert "私聊生图：/陪伴 生图 描述" in combined
    assert "私聊自拍：/陪伴 自拍" in combined
    assert any("陪伴生图（私聊）" in line for _, lines in texts for line in lines)


def test_proactive_status_command_merges_into_existing_plugin_card(monkeypatch):
    items = [command("astrbot_plugin_qqchat", "主动消息")]
    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: items)
    audience = _MenuAudience("aiocqhttp", admin=True, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(plugin_context(), audience)
    text = "\n".join(line for _, lines in texts for line in lines)
    assert text.count("/主动消息：") == 1
    assert "/主动消息：查看本群主动消息设置" in text
    assert len([title for title, *_ in images if title == "插件 · 陪伴互动"]) == 1


def test_astrbot_menu_preserves_mahjong_and_new_base_features(monkeypatch):
    from app.services import help_menu

    monkeypatch.setattr("app.astrbot_menu._active_commands", lambda *_: [])
    audience = _MenuAudience("aiocqhttp", admin=False, group=True, plugins=["*"])
    images, texts = build_unified_menu_groups(object(), audience)
    mahjong = next(lines for title, lines, _ in images if title == "雀魂牌谱分析")
    assert any("帮我分析牌谱" in line for line in mahjong)
    assert any("mjai" in line for line in mahjong)
    assert any("链接读取整场" in line for line in mahjong)
    text = "\n".join(line for _, lines in texts for line in lines)
    assert "雀魂牌谱" in text and "/牌谱分析 帮助" in text

    monkeypatch.setattr(help_menu, "_IMAGE_GROUPS", (*help_menu._IMAGE_GROUPS[:-1],
                        ("新增功能", ("实际入口",), (80, 100, 120)), help_menu._IMAGE_GROUPS[-1]))
    monkeypatch.setattr(help_menu, "_TEXT_GROUPS", (*help_menu._TEXT_GROUPS[:-1],
                        ("新增功能", ("实际入口说明",)), help_menu._TEXT_GROUPS[-1]))
    images, texts = build_unified_menu_groups(object(), audience)
    assert "新增功能" in {title for title, *_ in images}
    assert any("实际入口说明" in line for _, lines in texts for line in lines)
