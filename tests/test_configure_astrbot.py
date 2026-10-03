"""AstrBot 私有实例配置器的隔离测试。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import configure_astrbot


class FakeAstrBotConfig(dict):
    def __init__(self, config_path: str):
        self.path = Path(config_path)
        if self.path.exists():
            self.update(json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self.update(
                {
                    "dashboard": {
                        "enable": True,
                        "username": "astrbot",
                        "password": "",
                        "pbkdf2_password": "",
                        "password_storage_upgraded": False,
                        "password_change_required": False,
                        "host": "0.0.0.0",
                        "port": 6185,
                    },
                    "platform": [],
                    "provider": [],
                    "wake_prefix": ["/"],
                }
            )

    def save_config(self):
        self.path.write_text(
            json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def fake_api():
    def validate(password: str) -> None:
        assert len(password) >= 8
        assert re.search(r"[A-Z]", password)
        assert re.search(r"[a-z]", password)
        assert re.search(r"\d", password)

    return SimpleNamespace(
        version=configure_astrbot.EXPECTED_ASTRBOT_VERSION,
        config_class=FakeAstrBotConfig,
        generate_dashboard_password=lambda: "Abcdef1234567890Qrstuvwx",
        validate_dashboard_password=validate,
        hash_dashboard_password=lambda value: (
            "pbkdf2$" + hashlib.sha256(value.encode()).hexdigest()
        ),
        hash_md5_dashboard_password=lambda value: hashlib.md5(
            value.encode()
        ).hexdigest(),
        verify_dashboard_password=lambda stored, value: (
            stored == "pbkdf2$" + hashlib.sha256(value.encode()).hexdigest()
        ),
    )


def setup_get_px(instance_root: Path) -> None:
    schema_path = (
        instance_root
        / "data"
        / "plugins"
        / "astrbot_plugin_get_px"
        / "_conf_schema.json"
    )
    schema_path.parent.mkdir(parents=True)
    schema_path.write_text(
        json.dumps(
            {
                "checkin_enabled": {"type": "bool"},
                "auto_trigger_enabled": {"type": "bool"},
                "pixiv_refresh_token": {"type": "string"},
            }
        ),
        encoding="utf-8",
    )


def test_configure_creates_private_password_and_disabled_local_onebot(tmp_path: Path):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    setup_get_px(instance_root)

    result = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "private-ws-token",
    )

    password = (
        (private_root / "dashboard-login.txt").read_text(encoding="utf-8").strip()
    )
    config_path = instance_root / "data" / "cmd_config.json"
    config_text = config_path.read_text(encoding="utf-8")
    config = json.loads(config_text)
    dashboard = config["dashboard"]
    platform = next(p for p in config["platform"] if p["id"] == "qq-chatrobot-local")
    wechat = next(p for p in config["platform"] if p["id"] == "qq-chatrobot-wechat")

    assert result["dashboard_url"] == "http://127.0.0.1:6185"
    assert result["dashboard_password_created"] is True
    assert dashboard["host"] == "127.0.0.1"
    assert dashboard["port"] == 6185
    assert dashboard["password"] == hashlib.md5(password.encode()).hexdigest()
    assert password not in config_text
    assert platform == {
        "id": "qq-chatrobot-local",
        "type": "aiocqhttp",
        "enable": False,
        "ws_reverse_host": "127.0.0.1",
        "ws_reverse_port": 6199,
        "ws_reverse_token": "private-ws-token",
    }
    assert "private-ws-token" not in (private_root / "dashboard-login.txt").read_text()
    assert result["wechat_platform_created"] is True
    assert result["wechat_platform_enabled"] is False
    assert wechat == {
        "id": "qq-chatrobot-wechat",
        "type": "weixin_oc",
        "enable": False,
        "weixin_oc_base_url": "https://ilinkai.weixin.qq.com",
        "weixin_oc_bot_type": "3",
        "weixin_oc_qr_poll_interval": 1,
        "weixin_oc_long_poll_timeout_ms": 35_000,
        "weixin_oc_api_timeout_ms": 120_000,
    }
    assert "weixin_oc_token" not in wechat
    assert "weixin_oc_account_id" not in wechat
    get_px_config = json.loads(
        (
            instance_root / "data" / "config" / "astrbot_plugin_get_px_config.json"
        ).read_text(encoding="utf-8-sig")
    )
    assert get_px_config["checkin_enabled"] is False
    assert get_px_config["auto_trigger_enabled"] is False
    assert get_px_config["pixiv_refresh_token"] == ""


def test_configure_is_idempotent_and_preserves_enabled_state_and_other_config(
    tmp_path: Path,
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    setup_get_px(instance_root)
    first = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "stable-token",
    )
    config_path = Path(str(first["config_path"]))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    first_hash = config["dashboard"]["pbkdf2_password"]
    config["platform"][0]["enable"] = True
    config["provider"].append({"id": "preserved-provider"})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    get_px_path = (
        instance_root / "data" / "config" / "astrbot_plugin_get_px_config.json"
    )
    get_px_config = json.loads(get_px_path.read_text(encoding="utf-8-sig"))
    get_px_config["checkin_enabled"] = True
    get_px_config["auto_trigger_enabled"] = True
    get_px_config["pixiv_refresh_token"] = "private-token-placeholder"
    get_px_path.write_text(json.dumps(get_px_config), encoding="utf-8")

    second = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "must-not-replace",
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert second["dashboard_password_created"] is False
    assert saved["platform"][0]["enable"] is True
    assert saved["platform"][0]["ws_reverse_token"] == "stable-token"
    assert saved["dashboard"]["pbkdf2_password"] == first_hash
    assert saved["provider"] == [{"id": "preserved-provider"}]
    assert saved["wake_prefix"] == ["/"]
    saved_wechat = next(
        p for p in saved["platform"] if p["id"] == "qq-chatrobot-wechat"
    )
    assert saved_wechat["enable"] is False
    assert second["wechat_platform_created"] is False
    saved_get_px = json.loads(get_px_path.read_text(encoding="utf-8-sig"))
    assert saved_get_px["checkin_enabled"] is False
    assert saved_get_px["auto_trigger_enabled"] is False
    assert saved_get_px["pixiv_refresh_token"] == "private-token-placeholder"


def test_configure_refuses_to_replace_unknown_onebot_platform(tmp_path: Path):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    setup_get_px(instance_root)
    first = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "stable-token",
    )
    config_path = Path(str(first["config_path"]))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["platform"].append(
        {
            "id": "other-onebot",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_port": 6200,
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    before = config_path.read_bytes()

    with pytest.raises(configure_astrbot.ConfigurationError, match="已有其他 OneBot"):
        configure_astrbot.configure_instance(
            private_root, instance_root, api=fake_api()
        )

    assert config_path.read_bytes() == before


def test_configure_refuses_to_regenerate_missing_password_file_for_existing_hash(
    tmp_path: Path,
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    config_path = instance_root / "data" / "cmd_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"dashboard": {"password": "existing-hash"}}), encoding="utf-8"
    )

    with pytest.raises(configure_astrbot.ConfigurationError, match="密码文件缺失"):
        configure_astrbot.configure_instance(
            private_root, instance_root, api=fake_api()
        )

    assert not (private_root / "dashboard-login.txt").exists()


def test_configure_rejects_path_outside_private_root(tmp_path: Path):
    private_root = tmp_path / "data" / "astrbot"
    outside_instance = tmp_path / "elsewhere"

    with pytest.raises(configure_astrbot.ConfigurationError, match="AstrBot 私有目录"):
        configure_astrbot.configure_instance(
            private_root, outside_instance, api=fake_api()
        )


def test_configure_skips_get_px_when_optional_plugin_is_not_installed(tmp_path: Path):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"

    result = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "private-ws-token",
    )

    assert result["get_px_plugin_installed"] is False
    assert result["get_px_config_path"] is None
    assert not (instance_root / "data" / "plugins" / "astrbot_plugin_get_px").exists()
    assert not (instance_root / "data" / "config").exists()


def test_configure_rejects_installed_get_px_without_schema_before_writing_credentials(
    tmp_path: Path,
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    plugin_root = instance_root / "data" / "plugins" / "astrbot_plugin_get_px"
    plugin_root.mkdir(parents=True)

    with pytest.raises(configure_astrbot.ConfigurationError, match="缺少配置 schema"):
        configure_astrbot.configure_instance(
            private_root, instance_root, api=fake_api()
        )

    assert not (private_root / "dashboard-login.txt").exists()
    assert not (instance_root / "data" / "cmd_config.json").exists()


def test_configure_rejects_incompatible_get_px_schema_before_writing_credentials(
    tmp_path: Path,
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    schema_path = (
        instance_root
        / "data"
        / "plugins"
        / "astrbot_plugin_get_px"
        / "_conf_schema.json"
    )
    schema_path.parent.mkdir(parents=True)
    schema_path.write_text(
        json.dumps(
            {
                "checkin_enabled": {"type": "int"},
                "auto_trigger_enabled": {"type": "bool"},
                "pixiv_refresh_token": {"type": "string"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        configure_astrbot.ConfigurationError, match="配置项与已审核版本不符"
    ):
        configure_astrbot.configure_instance(
            private_root, instance_root, api=fake_api()
        )

    assert not (private_root / "dashboard-login.txt").exists()
    assert not (instance_root / "data" / "cmd_config.json").exists()


def test_wechat_preserves_login_and_custom_settings_on_second_configuration(
    tmp_path: Path,
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    setup_get_px(instance_root)
    first = configure_astrbot.configure_instance(
        private_root, instance_root, api=fake_api(), token_factory=lambda: "qq-ws-token"
    )
    config_path = Path(str(first["config_path"]))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    qq = next(p for p in config["platform"] if p["id"] == "qq-chatrobot-local")
    wx = next(p for p in config["platform"] if p["id"] == "qq-chatrobot-wechat")
    qq["enable"] = True
    wx.update(
        {
            "enable": True,
            "weixin_oc_account_id": "account-test-value",
            "weixin_oc_token": "token-test-value",
            "weixin_oc_sync_buf": 73,
            "weixin_oc_api_timeout_ms": 91_000,
        }
    )
    expected_wechat = dict(wx)
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    second = configure_astrbot.configure_instance(
        private_root,
        instance_root,
        api=fake_api(),
        token_factory=lambda: "must-not-replace",
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    saved_qq = next(p for p in saved["platform"] if p["id"] == "qq-chatrobot-local")
    saved_wechat = next(
        p for p in saved["platform"] if p["id"] == "qq-chatrobot-wechat"
    )

    assert saved_qq["enable"] is True
    assert saved_qq["ws_reverse_token"] == "qq-ws-token"
    assert saved_wechat == expected_wechat
    assert second["wechat_platform_enabled"] is True
    assert second["wechat_platform_created"] is False


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        (
            [{"id": "qq-chatrobot-wechat", "type": "qq_official", "enable": True}],
            "个人微信配置 ID 已被其他平台占用",
        ),
        (
            [
                {"id": "qq-chatrobot-wechat", "type": "weixin_oc", "enable": False},
                {"id": "qq-chatrobot-wechat", "type": "weixin_oc", "enable": True},
            ],
            "重复的个人微信配置 ID",
        ),
    ],
)
def test_wechat_conflicting_or_duplicate_id_is_rejected_without_saving(
    tmp_path: Path, entries: list[dict], error: str
):
    private_root = tmp_path / "data" / "astrbot"
    instance_root = private_root / "instance"
    config_path = instance_root / "data" / "cmd_config.json"
    config_path.parent.mkdir(parents=True)
    seeded = FakeAstrBotConfig(str(config_path))
    seeded["platform"] = entries
    seeded.save_config()
    before = config_path.read_bytes()

    with pytest.raises(configure_astrbot.ConfigurationError, match=error):
        configure_astrbot.configure_instance(
            private_root, instance_root, api=fake_api()
        )

    assert config_path.read_bytes() == before
    assert not (private_root / "dashboard-login.txt").exists()


def test_wechat_defaults_match_astrbot_4282_personal_wechat_template():
    assert configure_astrbot.WECHAT_PLATFORM_DEFAULTS == {
        "weixin_oc_base_url": "https://ilinkai.weixin.qq.com",
        "weixin_oc_bot_type": "3",
        "weixin_oc_qr_poll_interval": 1,
        "weixin_oc_long_poll_timeout_ms": 35_000,
        "weixin_oc_api_timeout_ms": 120_000,
    }
