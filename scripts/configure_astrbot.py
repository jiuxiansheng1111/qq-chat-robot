"""配置仓库内的 AstrBot 私有实例，不启动任何服务。"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_ASTRBOT_VERSION = "4.28.2"
INITIAL_PASSWORD_ENV = "ASTRBOT_DASHBOARD_INITIAL_PASSWORD"
PLATFORM_ID = "qq-chatrobot-local"
WECHAT_PLATFORM_ID = "qq-chatrobot-wechat"
WECHAT_PLATFORM_TYPE = "weixin_oc"
# AstrBot 4.28.2 default.py 的“个人微信” config_template 默认值。
WECHAT_PLATFORM_DEFAULTS: dict[str, str | int] = {
    "weixin_oc_base_url": "https://ilinkai.weixin.qq.com",
    "weixin_oc_bot_type": "3",
    "weixin_oc_qr_poll_interval": 1,
    "weixin_oc_long_poll_timeout_ms": 35_000,
    "weixin_oc_api_timeout_ms": 120_000,
}
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 6185
ONEBOT_HOST = "127.0.0.1"
ONEBOT_PORT = 6199


class ConfigurationError(RuntimeError):
    """配置不安全或不符合当前 AstrBot 版本。"""


@dataclass(frozen=True)
class AstrBotApi:
    version: str
    config_class: Any
    generate_dashboard_password: Callable[[], str]
    validate_dashboard_password: Callable[[str], None]
    hash_dashboard_password: Callable[[str], str]
    hash_md5_dashboard_password: Callable[[str], str]
    verify_dashboard_password: Callable[[str, str], bool]


def _load_astrbot_api() -> AstrBotApi:
    import astrbot
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.utils.auth_password import (
        generate_dashboard_password,
        hash_dashboard_password,
        hash_md5_dashboard_password,
        validate_dashboard_password,
        verify_dashboard_password,
    )

    return AstrBotApi(
        version=str(astrbot.__version__),
        config_class=AstrBotConfig,
        generate_dashboard_password=generate_dashboard_password,
        validate_dashboard_password=validate_dashboard_password,
        hash_dashboard_password=hash_dashboard_password,
        hash_md5_dashboard_password=hash_md5_dashboard_password,
        verify_dashboard_password=verify_dashboard_password,
    )


def _full_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _assert_managed_path(project_root: Path, path: Path) -> None:
    root = _full_path(project_root)
    target = _full_path(path)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(f"路径超出项目目录：{target}") from exc

    cursor = root
    if _is_reparse_point(cursor):
        raise ConfigurationError(f"项目目录是链接：{cursor}")
    for part in relative.parts:
        cursor /= part
        if _is_reparse_point(cursor):
            raise ConfigurationError(f"路径包含链接：{cursor}")


def _assert_child(parent: Path, child: Path) -> None:
    parent = _full_path(parent)
    child = _full_path(child)
    if parent == child:
        raise ConfigurationError(f"路径必须位于 AstrBot 私有目录的下级：{child}")
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ConfigurationError(f"路径不在 AstrBot 私有目录内：{child}") from exc


def _ensure_directory(project_root: Path, path: Path) -> None:
    _assert_managed_path(project_root, path)
    root = _full_path(project_root)
    cursor = root
    relative = _full_path(path).relative_to(root)
    for part in relative.parts:
        cursor /= part
        if cursor.exists():
            if not cursor.is_dir():
                raise ConfigurationError(f"目录位置已被文件占用：{cursor}")
        else:
            cursor.mkdir()
        _assert_managed_path(project_root, cursor)


def _write_private_secret(path: Path, secret: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(secret + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8-sig", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_existing_config(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"AstrBot 配置无法读取：{path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("AstrBot 配置根节点必须是对象")
    return value


def _find_or_create_platform(
    config: dict[str, Any], token_factory: Callable[[], str]
) -> dict[str, Any]:
    platforms = config.get("platform")
    if not isinstance(platforms, list):
        raise ConfigurationError("platform 配置必须是列表")

    named = [
        item
        for item in platforms
        if isinstance(item, dict) and item.get("id") == PLATFORM_ID
    ]
    if len(named) > 1:
        raise ConfigurationError("发现重复的本地 OneBot 配置")
    if named:
        platform = named[0]
        if platform.get("type") != "aiocqhttp":
            raise ConfigurationError("本地 OneBot 配置 ID 已被其他平台占用")
        if any(
            isinstance(item, dict)
            and item is not platform
            and item.get("type") == "aiocqhttp"
            for item in platforms
        ):
            raise ConfigurationError(
                "已有其他 OneBot 配置；为避免冲突，请先在 AstrBot 中核对平台列表"
            )
    else:
        if any(
            isinstance(item, dict) and item.get("type") == "aiocqhttp"
            for item in platforms
        ):
            raise ConfigurationError(
                "已有其他 OneBot 配置；为避免冲突，请先在 AstrBot 中核对平台列表"
            )
        platform = {
            "id": PLATFORM_ID,
            "type": "aiocqhttp",
            "enable": False,
        }
        platforms.append(platform)

    token = platform.get("ws_reverse_token")
    if not isinstance(token, str) or not token:
        token = token_factory()
    platform.update(
        {
            "id": PLATFORM_ID,
            "type": "aiocqhttp",
            "enable": bool(platform.get("enable", False)),
            "ws_reverse_host": ONEBOT_HOST,
            "ws_reverse_port": ONEBOT_PORT,
            "ws_reverse_token": token,
        }
    )
    return platform


def _find_or_create_wechat_platform(
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    platforms = config.get("platform")
    if not isinstance(platforms, list):
        raise ConfigurationError("platform 配置必须是列表")

    named = [
        item
        for item in platforms
        if isinstance(item, dict) and item.get("id") == WECHAT_PLATFORM_ID
    ]
    if len(named) > 1:
        raise ConfigurationError("发现重复的个人微信配置 ID")

    created = not named
    if named:
        platform = named[0]
        if platform.get("type") != WECHAT_PLATFORM_TYPE:
            raise ConfigurationError("个人微信配置 ID 已被其他平台占用")
    else:
        platform = {
            "id": WECHAT_PLATFORM_ID,
            "type": WECHAT_PLATFORM_TYPE,
            "enable": False,
            **WECHAT_PLATFORM_DEFAULTS,
        }
        platforms.append(platform)

    # 只补缺少的非凭据默认项；账号、token、enable 和用户调过的值都原样保留。
    platform.setdefault("enable", False)
    for key, value in WECHAT_PLATFORM_DEFAULTS.items():
        platform.setdefault(key, value)
    return platform, created


def _find_get_px_schema(project_root: Path, instance_root: Path) -> Path | None:
    plugin_root = instance_root / "data" / "plugins" / "astrbot_plugin_get_px"
    schema_path = plugin_root / "_conf_schema.json"
    _assert_managed_path(project_root, plugin_root)
    if not plugin_root.exists():
        return None
    if not plugin_root.is_dir():
        raise ConfigurationError("get_px 插件目录位置不是目录")
    _assert_managed_path(project_root, schema_path)
    if not schema_path.is_file():
        raise ConfigurationError("get_px 插件已安装，但缺少配置 schema")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("get_px 插件配置 schema 无法读取") from exc
    expected_types = {
        "checkin_enabled": "bool",
        "auto_trigger_enabled": "bool",
        "pixiv_refresh_token": "string",
    }
    if not isinstance(schema, dict) or any(
        not isinstance(schema.get(name), dict)
        or schema[name].get("type") != expected_type
        for name, expected_type in expected_types.items()
    ):
        raise ConfigurationError("get_px 插件配置项与已审核版本不符")
    config_dir = instance_root / "data" / "config"
    config_path = config_dir / "astrbot_plugin_get_px_config.json"
    _assert_managed_path(project_root, config_dir)
    _assert_managed_path(project_root, config_path)
    return schema_path


def _configure_get_px(
    project_root: Path,
    instance_root: Path,
    schema_path: Path | None,
) -> Path | None:
    if schema_path is None:
        return None
    config_dir = instance_root / "data" / "config"
    config_path = config_dir / "astrbot_plugin_get_px_config.json"
    for path in (config_dir, config_path):
        _assert_managed_path(project_root, path)

    _ensure_directory(project_root, config_dir)
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("get_px 私有配置无法读取") from exc
        if not isinstance(config, dict):
            raise ConfigurationError("get_px 私有配置根节点必须是对象")
    else:
        config = {}
    config["checkin_enabled"] = False
    config["auto_trigger_enabled"] = False
    config.setdefault("pixiv_refresh_token", "")
    if (
        not config_path.exists()
        or json.loads(config_path.read_text(encoding="utf-8-sig")) != config
    ):
        _write_json_atomically(config_path, config)
    return config_path


def configure_instance(
    private_root: Path,
    instance_root: Path,
    *,
    api: AstrBotApi | None = None,
    token_factory: Callable[[], str] | None = None,
) -> dict[str, str | bool | int | None]:
    """写好本地登录凭据和 OneBot 配置；不启动 AstrBot 或 QQ 服务。"""
    private_root = _full_path(private_root)
    instance_root = _full_path(instance_root)
    project_root = private_root.parent.parent
    config_path = instance_root / "data" / "cmd_config.json"
    password_path = private_root / "dashboard-login.txt"

    _assert_child(private_root, instance_root)
    _assert_managed_path(project_root, private_root)
    _assert_managed_path(project_root, instance_root)
    _assert_managed_path(project_root, config_path)
    _assert_managed_path(project_root, password_path)
    _ensure_directory(project_root, private_root)
    _ensure_directory(project_root, instance_root / "data")
    get_px_schema_path = _find_get_px_schema(project_root, instance_root)

    prior_config = _read_existing_config(config_path)
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        password_is_new = False
        if not password:
            raise ConfigurationError("面板登录密码文件为空")
    else:
        prior_dashboard = (prior_config or {}).get("dashboard", {})
        if isinstance(prior_dashboard, dict) and (
            prior_dashboard.get("password") or prior_dashboard.get("pbkdf2_password")
        ):
            raise ConfigurationError(
                "已有面板密码但私有密码文件缺失；为避免覆盖，请先手动恢复该文件"
            )
        password = ""
        password_is_new = True

    old_root = os.environ.get("ASTRBOT_ROOT")
    old_password = os.environ.get(INITIAL_PASSWORD_ENV)
    try:
        os.environ["ASTRBOT_ROOT"] = str(instance_root)
        if api is None:
            api = _load_astrbot_api()
        if api.version != EXPECTED_ASTRBOT_VERSION:
            raise ConfigurationError(
                f"需要 AstrBot {EXPECTED_ASTRBOT_VERSION}，当前是 {api.version}"
            )

        if password_is_new:
            password = api.generate_dashboard_password()
            api.validate_dashboard_password(password)
        os.environ[INITIAL_PASSWORD_ENV] = password

        config = api.config_class(config_path=str(config_path))
        if not isinstance(config, dict):
            raise ConfigurationError("AstrBot 配置对象格式异常")

        dashboard = config.get("dashboard")
        if not isinstance(dashboard, dict):
            raise ConfigurationError("dashboard 配置必须是对象")
        dashboard["enable"] = True
        dashboard["host"] = DASHBOARD_HOST
        dashboard["port"] = DASHBOARD_PORT
        dashboard.setdefault("username", "astrbot")
        md5_hash = api.hash_md5_dashboard_password(password)
        stored_pbkdf2 = dashboard.get("pbkdf2_password")
        if not (
            isinstance(stored_pbkdf2, str)
            and stored_pbkdf2.startswith("pbkdf2_sha256$")
            and api.verify_dashboard_password(stored_pbkdf2, password)
        ):
            dashboard["pbkdf2_password"] = api.hash_dashboard_password(password)
        if dashboard.get("password") != md5_hash:
            dashboard["password"] = md5_hash
        dashboard["password_storage_upgraded"] = True
        dashboard.setdefault("password_change_required", True)

        platform = _find_or_create_platform(
            config, token_factory or (lambda: secrets.token_urlsafe(32))
        )
        wechat_platform, wechat_platform_created = _find_or_create_wechat_platform(
            config
        )
        if password_is_new:
            _write_private_secret(password_path, password)
        config.save_config()
        get_px_config_path = _configure_get_px(
            project_root, instance_root, get_px_schema_path
        )
    finally:
        if old_root is None:
            os.environ.pop("ASTRBOT_ROOT", None)
        else:
            os.environ["ASTRBOT_ROOT"] = old_root
        if old_password is None:
            os.environ.pop(INITIAL_PASSWORD_ENV, None)
        else:
            os.environ[INITIAL_PASSWORD_ENV] = old_password

    saved = _read_existing_config(config_path)
    assert saved is not None
    saved_dashboard = saved.get("dashboard", {})
    saved_platforms = saved.get("platform", [])
    assert isinstance(saved_dashboard, dict)
    assert isinstance(saved_platforms, list)
    saved_entry = next(
        (
            item
            for item in saved_platforms
            if isinstance(item, dict) and item.get("id") == PLATFORM_ID
        ),
        None,
    )
    saved_wechat_entry = next(
        (
            item
            for item in saved_platforms
            if isinstance(item, dict) and item.get("id") == WECHAT_PLATFORM_ID
        ),
        None,
    )
    if (
        saved_dashboard.get("host") != DASHBOARD_HOST
        or saved_dashboard.get("port") != DASHBOARD_PORT
        or not isinstance(saved_entry, dict)
        or saved_entry.get("type") != "aiocqhttp"
        or saved_entry.get("ws_reverse_host") != ONEBOT_HOST
        or saved_entry.get("ws_reverse_port") != ONEBOT_PORT
        or saved_entry.get("ws_reverse_token") != platform.get("ws_reverse_token")
        or not isinstance(saved_wechat_entry, dict)
        or saved_wechat_entry.get("type") != WECHAT_PLATFORM_TYPE
        or any(
            saved_wechat_entry.get(key) != value
            for key, value in wechat_platform.items()
        )
    ):
        raise ConfigurationError("写入后的 AstrBot 配置校验失败")

    return {
        "config_path": str(config_path),
        "dashboard_url": f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}",
        "password_file": str(password_path),
        "platform_id": PLATFORM_ID,
        "platform_enabled": bool(saved_entry.get("enable")),
        "wechat_platform_id": WECHAT_PLATFORM_ID,
        "wechat_platform_enabled": bool(saved_wechat_entry.get("enable")),
        "wechat_platform_created": wechat_platform_created,
        "get_px_plugin_installed": get_px_config_path is not None,
        "get_px_config_path": str(get_px_config_path) if get_px_config_path else None,
        "get_px_checkin_enabled": False if get_px_config_path else None,
        "get_px_auto_trigger_enabled": False if get_px_config_path else None,
        "onebot_host": ONEBOT_HOST,
        "onebot_port": ONEBOT_PORT,
        "dashboard_password_created": password_is_new,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="配置 AstrBot 私有实例，不启动服务")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    project_root = _full_path(args.project_root)
    private_root = project_root / "data" / "astrbot"
    instance_root = private_root / "instance"
    try:
        result = configure_instance(private_root, instance_root)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 1

    print("AstrBot 本地配置已写好，服务未启动。")
    print(f"管理面板：{result['dashboard_url']}")
    print(f"面板密码文件：{result['password_file']}")
    print(
        f"OneBot：{result['onebot_host']}:{result['onebot_port']}，"
        f"当前{'已启用' if result['platform_enabled'] else '未启用'}"
    )
    if result["get_px_plugin_installed"]:
        print("get_px 签到与自动触发已关闭。")
    else:
        print("未安装可选 get_px，跳过插件配置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
