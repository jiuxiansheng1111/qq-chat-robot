import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pixiv_login", ROOT / "scripts/login_pixiv.py")
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


@pytest.mark.parametrize("url", [
    "https://example.org/?code=secret", "https://accounts.pixiv.net/?code=secret",
    "pixiv://other/login?code=secret", "pixiv://account/elsewhere?code=secret",
    "pixiv://account/login", "pixiv://account/login?code=a&code=b",
])
def test_only_own_oauth_callback_is_captured(url):
    assert login.extract_code(url) is None


def test_valid_callback_is_decoded_without_logging():
    assert login.extract_code("pixiv://account/login?code=sample-code_123") == "sample-code_123"


def test_save_preserves_settings_and_keeps_previous_config_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "ROOT", tmp_path)
    config = tmp_path / "config.json"
    old = {"pixiv_refresh_token": "old", "lolicon_api_url": "existing", "checkin_enabled": False}
    config.write_text(json.dumps(old), encoding="utf-8")
    login.save_plugin_token("new", config)
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved == {**old, "pixiv_refresh_token": "new"}
    backups = list((tmp_path / "data/astrbot/config-backups").rglob("config.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == old


def test_empty_token_cannot_replace_config(tmp_path):
    config = tmp_path / "config.json"
    config.write_text('{"original":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        login.save_plugin_token("", config)
    assert json.loads(config.read_text(encoding="utf-8")) == {"original": True}


def test_status_exposes_no_credential_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "STATE_DIR", tmp_path)
    login.status("waiting_for_user_login")
    data = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert set(data) == {"phase", "error", "pid", "updated_at"}
