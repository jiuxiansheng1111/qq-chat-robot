"""安全预览或切换本机 NapCat OneBot 连接到 AstrBot。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import socket
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ASTRBOT_PLATFORM_ID = "qq-chatrobot-local"
ASTRBOT_PLATFORM_TYPE = "aiocqhttp"
ASTRBOT_HOST = "127.0.0.1"
ASTRBOT_WS_PORT = 6199
ASTRBOT_WS_URL = "ws://127.0.0.1:6199/ws"
OLD_WEBHOOK_HOST = "127.0.0.1"
OLD_WEBHOOK_PORT = 8000
OLD_WEBHOOK_PATH = "/onebot/webhook"
NAPCAT_MARKER = "astrbot-qqchat"
NAPCAT_CONFIG_DEFAULT = Path(
    r"C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config"
)
WEBUI_LOGIN_PATH = "/api/auth/login"
GET_CONFIG_PATH = "/api/OB11Config/GetConfig"
SET_CONFIG_PATH = "/api/OB11Config/SetConfig"


class ConnectError(RuntimeError):
    """预检或安全校验失败。"""


@dataclass(frozen=True)
class OneBotAccount:
    name: str
    api_base: str
    access_token: str = field(repr=False)
    expected_self_id: str = field(default="", repr=False)


@dataclass(frozen=True)
class WebUiSettings:
    base_url: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class ConnectPlan:
    project_root: Path
    napcat_config_dir: Path
    account_name: str = field(repr=False)
    self_id: str = field(repr=False)
    webui: WebUiSettings = field(repr=False)
    credential: str = field(repr=False)
    old_config: dict[str, Any] = field(repr=False)
    new_config: dict[str, Any] = field(repr=False)
    http_clients_disabled: int
    websocket_client_added: bool
    port_6199_listening: bool
    port_8000_listening: bool


def _full_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _has_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _assert_project_private_path(project_root: Path, path: Path) -> None:
    root = project_root.resolve()
    candidate = _full_path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ConnectError("私有数据路径超出项目目录。") from exc
    cursor = root
    if _has_reparse_point(cursor):
        raise ConnectError("项目目录包含链接路径。")
    for part in relative.parts:
        cursor /= part
        if _has_reparse_point(cursor):
            raise ConnectError("私有数据路径包含链接或重解析点。")


def _ensure_private_dir(project_root: Path, path: Path) -> None:
    _assert_project_private_path(project_root, path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConnectError("无法创建项目私有备份目录。") from exc
    if not path.is_dir():
        raise ConnectError("私有备份路径被非目录占用。")
    _assert_project_private_path(project_root, path)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if _has_reparse_point(path) or not path.is_file():
        raise ConnectError(f"找不到安全的{label}文件。")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConnectError(f"无法读取{label}文件。") from None
    if not isinstance(value, dict):
        raise ConnectError(f"{label}文件根节点格式不正确。")
    return value


def _local_url_base(value: str, *, scheme: str = "http") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectError("本机服务地址未配置。")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ConnectError("本机服务地址格式不正确。") from None
    host = (parsed.hostname or "").lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme.lower() != scheme
        or host not in local_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise ConnectError("只允许使用带端口的本机 HTTP 服务地址。")
    if parsed.path and parsed.path != "/":
        path = parsed.path.rstrip("/")
    else:
        path = ""
    netloc_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc_host}:{port}{path}"


def _contains_two_factor_requirement(value: Any) -> bool:
    markers = ("2fa", "twofactor", "multifactor", "mfa", "totp")
    truthy = {"true", "yes", "required", "enabled", "on", "1"}
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            is_secret_field = "secret" in normalized
            if not is_secret_field and any(marker in normalized for marker in markers):
                if item is True or (
                    isinstance(item, str) and item.strip().lower() in truthy
                ):
                    return True
                if isinstance(item, list) and any(
                    isinstance(entry, str)
                    and any(marker in entry.lower().replace("_", "") for marker in markers)
                    for entry in item
                ):
                    return True
            if _contains_two_factor_requirement(item):
                return True
    elif isinstance(value, list):
        return any(_contains_two_factor_requirement(item) for item in value)
    return False


def load_webui_settings(path: Path) -> WebUiSettings:
    """读取 NapCat 本地 WebUI 地址，不返回或输出 token。"""
    config = _read_json(path, "NapCat WebUI 配置")
    if _contains_two_factor_requirement(config):
        raise ConnectError("NapCat WebUI 配置要求双重验证，已停止。")
    settings = config.get("webui", config)
    if not isinstance(settings, dict):
        raise ConnectError("NapCat WebUI 配置结构无法识别。")
    normalized_settings = {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): value
        for key, value in settings.items()
    }
    if normalized_settings.get("disablewebui") is True:
        raise ConnectError("NapCat WebUI 已禁用。")
    enabled_keys = {"enable", "enabled", "enablewebui", "webuienabled", "webuienable"}
    if any(normalized_settings.get(key) is False for key in enabled_keys):
        raise ConnectError("NapCat WebUI 已禁用。")
    host = settings.get("host")
    port = settings.get("port")
    token = settings.get("token")
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        raise ConnectError("NapCat WebUI 配置缺少本机 host/port。")
    normalized_host = host.strip().strip("[]").lower()
    client_hosts = {
        "localhost": "localhost",
        "127.0.0.1": "127.0.0.1",
        "::1": "::1",
        "0.0.0.0": "127.0.0.1",
        "::": "::1",
    }
    if normalized_host not in client_hosts:
        raise ConnectError("NapCat WebUI 地址不是本机或通配绑定。")
    if not 1 <= port <= 65535:
        raise ConnectError("NapCat WebUI 端口无效。")
    client_host = client_hosts[normalized_host]
    url_host = f"[{client_host}]" if ":" in client_host else client_host
    base_url = _local_url_base(f"http://{url_host}:{port}")
    if not isinstance(token, str) or not token.strip():
        raise ConnectError("NapCat WebUI token 未配置。")
    return WebUiSettings(base_url=base_url, token=token.strip())


def load_astrbot_ws_token(project_root: Path) -> str:
    """核对私有 AstrBot OneBot 平台后取得反向 WebSocket token。"""
    root = project_root.resolve()
    path = root / "data" / "astrbot" / "instance" / "data" / "cmd_config.json"
    _assert_project_private_path(root, path)
    config = _read_json(path, "AstrBot 私有配置")
    platforms = config.get("platform")
    if not isinstance(platforms, list):
        raise ConnectError("AstrBot 平台配置不是列表。")
    matches = [
        item
        for item in platforms
        if isinstance(item, dict) and item.get("id") == ASTRBOT_PLATFORM_ID
    ]
    if len(matches) != 1:
        raise ConnectError("AstrBot 本地 OneBot 平台缺失或重复。")
    platform = matches[0]
    if (
        platform.get("type") != ASTRBOT_PLATFORM_TYPE
        or platform.get("enable") is not True
        or platform.get("ws_reverse_host") != ASTRBOT_HOST
        or platform.get("ws_reverse_port") != ASTRBOT_WS_PORT
    ):
        raise ConnectError("AstrBot 本地 OneBot 平台未按预期启用。")
    token = platform.get("ws_reverse_token")
    if not isinstance(token, str) or not token.strip():
        raise ConnectError("AstrBot 本地 OneBot token 未配置。")
    return token.strip()


def _parse_env(path: Path) -> dict[str, str]:
    if _has_reparse_point(path) or not path.is_file():
        raise ConnectError("找不到项目根目录的 .env 配置。")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ConnectError("无法读取项目 .env 配置。") from None
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        key = key.strip().upper()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_onebot_accounts(env_path: Path, selection: str = "auto") -> list[OneBotAccount]:
    if selection not in {"auto", "primary", "secondary"}:
        raise ConnectError("账号选择参数无效。")
    env = _parse_env(env_path)
    primary_base = env.get("ONEBOT_API_BASE", "").strip()
    candidates: list[OneBotAccount] = []
    for name, suffix in (("primary", ""), ("secondary", "_2")):
        if selection not in {"auto", name}:
            continue
        base = env.get(f"ONEBOT_API_BASE{suffix}", "").strip()
        token = env.get(f"ONEBOT_ACCESS_TOKEN{suffix}", "").strip()
        expected = env.get(f"ONEBOT_SELF_ID{suffix}", "").strip()
        if name == "secondary" and not any((base, token, expected)):
            if selection == "auto":
                continue
            raise ConnectError("secondary OneBot API 地址和 token 未配置。")
        if name == "secondary" and not base:
            base = primary_base
        if not base and not token:
            continue
        if not base or not token:
            raise ConnectError(f"{name} OneBot API 地址或 token 配置不完整。")
        if expected and not expected.isdigit():
            raise ConnectError(f"{name} OneBot self_id 配置格式不正确。")
        candidates.append(
            OneBotAccount(
                name=name,
                api_base=_local_url_base(base),
                access_token=token,
                expected_self_id=expected,
            )
        )
    if not candidates:
        raise ConnectError("没有可用于识别 NapCat 账号的 OneBot API 配置。")
    return candidates


def _port_listening(host: str, port: int, *, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _new_http_client(factory: Callable[..., Any] | None = None):
    if factory is None:
        try:
            import httpx
        except ImportError:
            raise ConnectError("请在 AstrBot Python 环境安装 httpx 后重试。") from None
        factory = httpx.Client
    return factory(timeout=10, trust_env=False)


def _response_body(response: Any) -> dict[str, Any]:
    try:
        if int(response.status_code) >= 400:
            raise ConnectError("本机服务拒绝了预检请求。")
        value = response.json()
    except ConnectError:
        raise
    except Exception:  # noqa: BLE001 - 屏蔽底层 HTTP/JSON 响应异常，避免泄露本机细节。
        raise ConnectError("本机服务返回了无法读取的响应。") from None
    if not isinstance(value, dict):
        raise ConnectError("本机服务响应格式不正确。")
    if value.get("success") is False:
        raise ConnectError("本机服务请求未成功。")
    if value.get("status") in {"failed", "error"}:
        raise ConnectError("本机服务请求未成功。")
    code = value.get("code")
    if code is not None and str(code) not in {"0", "200"}:
        raise ConnectError("本机服务请求未成功。")
    if _contains_two_factor_requirement(value):
        raise ConnectError("NapCat WebUI 请求了双重验证，已停止。")
    return value


def _webui_login(client: Any, settings: WebUiSettings) -> str:
    digest = hashlib.sha256((settings.token + ".napcat").encode("utf-8")).hexdigest()
    try:
        response = client.post(
            settings.base_url + WEBUI_LOGIN_PATH,
            json={"hash": digest},
        )
        body = _response_body(response)
    except ConnectError:
        raise
    except Exception:  # noqa: BLE001 - 屏蔽 HTTP 客户端异常，避免泄露凭据或 URL。
        raise ConnectError("无法连接到本机 NapCat WebUI。") from None
    data = body.get("data")
    credential = data.get("Credential") if isinstance(data, dict) else None
    if not isinstance(credential, str) or not credential.strip():
        raise ConnectError("NapCat WebUI 未返回登录凭据。")
    return credential.strip()


def _get_onebot_self_id(client: Any, account: OneBotAccount) -> str:
    url = account.api_base.rstrip("/") + "/get_login_info"
    try:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {account.access_token}"},
            json={},
        )
        body = _response_body(response)
    except ConnectError:
        raise
    except Exception:  # noqa: BLE001 - 屏蔽 HTTP 客户端异常，避免泄露 OneBot token。
        raise ConnectError(f"无法读取 {account.name} OneBot 登录身份。") from None
    if body.get("retcode", 0) not in (0, "0") or body.get("status", "ok") != "ok":
        raise ConnectError(f"{account.name} OneBot 登录身份不可用。")
    data = body.get("data")
    user_id = data.get("user_id") if isinstance(data, dict) else None
    self_id = str(user_id or "").strip()
    if not self_id.isdigit():
        raise ConnectError(f"无法识别 {account.name} OneBot 登录身份。")
    if account.expected_self_id and self_id != account.expected_self_id:
        raise ConnectError(f"{account.name} OneBot 身份与 .env 配置不一致。")
    return self_id


def _read_account_config(config_dir: Path, self_id: str) -> dict[str, Any]:
    if not self_id.isdigit():
        raise ConnectError("OneBot 返回的 self_id 不安全。")
    path = config_dir / f"onebot11_{self_id}.json"
    if _has_reparse_point(path) or not path.is_file():
        raise ConnectError("OneBot 登录身份未对应到 NapCat 私有账号配置。")
    config = _read_json(path, "NapCat OneBot 账号配置")
    if not isinstance(config.get("network"), dict):
        raise ConnectError("NapCat OneBot 账号配置结构无法识别。")
    embedded_ids = _embedded_account_ids(config)
    if embedded_ids and any(value != self_id for value in embedded_ids):
        raise ConnectError("NapCat 私有配置与 OneBot 登录身份不一致。")
    return config


def _embedded_account_ids(value: Any) -> set[str]:
    id_keys = {"selfid", "uin", "accountid", "userid"}
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in id_keys and isinstance(item, (int, str)):
                candidate = str(item).strip()
                if candidate.isdigit():
                    found.add(candidate)
            found.update(_embedded_account_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_embedded_account_ids(item))
    return found


def _is_old_webhook_client(item: dict[str, Any]) -> bool:
    value = item.get("url")
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "http"
        and (parsed.hostname or "").lower() == OLD_WEBHOOK_HOST
        and port == OLD_WEBHOOK_PORT
        and parsed.path.rstrip("/") == OLD_WEBHOOK_PATH
        and parsed.username is None
        and parsed.password is None
    )


def build_napcat_config(
    original: dict[str, Any], astrbot_token: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """只禁用旧 webhook 客户端并加入/复用本脚本标记的反向 WS。"""
    if not isinstance(astrbot_token, str) or not astrbot_token.strip():
        raise ConnectError("AstrBot 反向 WebSocket token 为空。")
    updated = copy.deepcopy(original)
    network = updated.get("network")
    if not isinstance(network, dict):
        raise ConnectError("NapCat OneBot 配置缺少 network 对象。")

    http_clients = network.setdefault("httpClients", [])
    ws_clients = network.setdefault("websocketClients", [])
    if not isinstance(http_clients, list) or not isinstance(ws_clients, list):
        raise ConnectError("NapCat OneBot 客户端配置必须是数组。")
    if any(not isinstance(item, dict) for item in (*http_clients, *ws_clients)):
        raise ConnectError("NapCat OneBot 客户端数组包含未知格式。")

    disabled = 0
    for item in http_clients:
        if _is_old_webhook_client(item) and item.get("enable") is not False:
            item["enable"] = False
            disabled += 1

    marked = [item for item in ws_clients if item.get("name") == NAPCAT_MARKER]
    if len(marked) > 1:
        raise ConnectError("发现多个同名 AstrBot WebSocket 客户端，拒绝修改。")
    same_url = [item for item in ws_clients if item.get("url") == ASTRBOT_WS_URL]
    if any(item.get("name") != NAPCAT_MARKER for item in same_url):
        raise ConnectError("AstrBot WebSocket 地址已被其他配置占用，拒绝覆盖。")
    added = not marked
    if marked:
        target = marked[0]
        if target.get("url") != ASTRBOT_WS_URL:
            raise ConnectError("同名 WebSocket 客户端指向未知地址，拒绝覆盖。")
    else:
        target = {}
        ws_clients.append(target)

    target.update(
        {
            "name": NAPCAT_MARKER,
            "enable": True,
            "url": ASTRBOT_WS_URL,
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "heartInterval": 30000,
            "reconnectInterval": 5000,
            "token": astrbot_token.strip(),
            "debug": False,
        }
    )
    return updated, {
        "http_clients_disabled": disabled,
        "websocket_client_added": added,
        "websocket_client_reused": not added,
    }


def _extract_config(response_body: dict[str, Any]) -> dict[str, Any]:
    data = response_body.get("data")
    if isinstance(data, dict) and "config" in data:
        data = data["config"]
    elif data is None and "config" in response_body:
        data = response_body["config"]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise ConnectError("NapCat 返回的网络配置不是有效 JSON。") from None
    if not isinstance(data, dict):
        raise ConnectError("NapCat 未返回 OneBot 网络配置。")
    return copy.deepcopy(data)


def _webui_headers(credential: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def _webui_get_config(client: Any, settings: WebUiSettings, credential: str) -> dict[str, Any]:
    try:
        response = client.post(
            settings.base_url + GET_CONFIG_PATH,
            headers=_webui_headers(credential),
        )
        return _extract_config(_response_body(response))
    except ConnectError:
        raise
    except Exception:  # noqa: BLE001 - 屏蔽 HTTP 客户端异常和本机响应正文。
        raise ConnectError("无法读取 NapCat OneBot 网络配置。") from None


def _webui_set_config(
    client: Any,
    settings: WebUiSettings,
    credential: str,
    config: dict[str, Any],
) -> None:
    body = {"config": json.dumps(config, ensure_ascii=False, separators=(",", ":"))}
    try:
        response = client.post(
            settings.base_url + SET_CONFIG_PATH,
            headers=_webui_headers(credential),
            json=body,
        )
        _response_body(response)
    except ConnectError:
        raise
    except Exception:  # noqa: BLE001 - 屏蔽 HTTP 客户端异常和本机响应正文。
        raise ConnectError("无法更新 NapCat OneBot 网络配置。") from None


def _same_json(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
            right, sort_keys=True, ensure_ascii=False
        )
    except (TypeError, ValueError):
        return False


def preflight(
    project_root: Path,
    napcat_config_dir: Path,
    *,
    account: str = "auto",
    port_probe: Callable[[str, int], bool] = _port_listening,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectPlan:
    """只读核对并生成变更预览；本函数不写配置、不发送消息。"""
    root = project_root.resolve()
    config_dir = napcat_config_dir.expanduser().resolve()
    if not config_dir.is_dir() or _has_reparse_point(napcat_config_dir):
        raise ConnectError("NapCat 配置目录不存在或包含链接路径。")
    port_6199 = port_probe("127.0.0.1", ASTRBOT_WS_PORT)
    port_8000 = port_probe("127.0.0.1", OLD_WEBHOOK_PORT)
    if not port_6199:
        raise ConnectError("AstrBot 反向 WebSocket 端口 6199 尚未监听。")

    astrbot_token = load_astrbot_ws_token(root)
    webui = load_webui_settings(config_dir / "webui.json")
    accounts = load_onebot_accounts(root / ".env", selection=account)
    with _new_http_client(client_factory) as client:
        credential = _webui_login(client, webui)
        active: dict[str, tuple[OneBotAccount, str]] = {}
        errors = 0
        for candidate in accounts:
            try:
                self_id = _get_onebot_self_id(client, candidate)
                if not (config_dir / f"onebot11_{self_id}.json").is_file():
                    errors += 1
                    continue
                active.setdefault(self_id, (candidate, self_id))
            except ConnectError:
                errors += 1
        if not active:
            raise ConnectError("没有可与 NapCat 私有账号配置匹配的 OneBot 登录身份。")
        if len(active) > 1:
            raise ConnectError("检测到多个已登录账号；请用 --account primary 或 --account secondary 指定。")
        candidate, self_id = next(iter(active.values()))
        _read_account_config(config_dir, self_id)
        old_config = _webui_get_config(client, webui, credential)
        new_config, summary = build_napcat_config(old_config, astrbot_token)

    return ConnectPlan(
        project_root=root,
        napcat_config_dir=config_dir,
        account_name=candidate.name,
        self_id=self_id,
        webui=webui,
        credential=credential,
        old_config=old_config,
        new_config=new_config,
        http_clients_disabled=summary["http_clients_disabled"],
        websocket_client_added=summary["websocket_client_added"],
        port_6199_listening=port_6199,
        port_8000_listening=port_8000,
    )


def _new_backup_dir(project_root: Path) -> Path:
    parent = project_root / "data" / "astrbot" / "napcat-backups"
    _ensure_private_dir(project_root, parent)
    name = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = parent / name
    _assert_project_private_path(project_root, target)
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError:
        raise ConnectError("备份目录时间戳冲突，请稍后重试。") from None
    return target


def _write_json_private(
    path: Path, value: dict[str, Any], *, allow_update: bool = False
) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        if path.is_symlink() or (_has_reparse_point(path)):
            raise ConnectError("备份或报告路径包含链接，拒绝写入。")
        if path.exists() and not allow_update:
            raise ConnectError("备份或报告文件已存在，拒绝覆盖。")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _active_mode_path(project_root: Path) -> Path:
    path = project_root / "data" / "astrbot" / "active-mode.txt"
    _assert_project_private_path(project_root, path)
    return path


def _read_active_mode(project_root: Path) -> bytes | None:
    path = _active_mode_path(project_root)
    if not path.exists() and not path.is_symlink():
        return None
    if _has_reparse_point(path) or not path.is_file():
        raise ConnectError("AstrBot 启动模式标记不是安全的普通文件。")
    try:
        return path.read_bytes()
    except OSError:
        raise ConnectError("无法读取私有启动模式标记。") from None


def _write_active_mode(project_root: Path) -> None:
    path = _active_mode_path(project_root)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(b"astrbot\n")
            stream.flush()
            os.fsync(stream.fileno())
        if _has_reparse_point(path):
            raise ConnectError("私有启动模式标记路径包含链接。")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_active_mode(project_root: Path, original: bytes | None) -> None:
    path = _active_mode_path(project_root)
    if original is None:
        if _has_reparse_point(path):
            raise ConnectError("无法安全移除本次创建的启动模式标记。")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.restore.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        if _has_reparse_point(path):
            raise ConnectError("无法安全恢复原启动模式标记。")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _base_report(plan: ConnectPlan) -> dict[str, Any]:
    return {
        "port_6199_listening": bool(plan.port_6199_listening),
        "port_8000_listening": bool(plan.port_8000_listening),
        "account_identity_matched": True,
        "http_clients_disabled": int(plan.http_clients_disabled),
        "websocket_client_added": bool(plan.websocket_client_added),
        "websocket_client_reused": not plan.websocket_client_added,
        "apply_started": True,
        "config_verified": False,
        "active_mode_marker_written": False,
        "active_mode_marker_restored": False,
        "rollback_attempted": False,
        "rollback_verified": False,
    }


def apply_plan(
    plan: ConnectPlan,
    *,
    client_factory: Callable[..., Any] | None = None,
    port_probe: Callable[[str, int], bool] = _port_listening,
) -> dict[str, Any]:
    """备份、热更新并校验；验证失败时尝试还原原配置。"""
    if not plan.port_6199_listening:
        raise ConnectError("AstrBot 反向 WebSocket 端口 6199 尚未监听。")
    if plan.port_8000_listening:
        raise ConnectError("旧 webhook 端口 8000 仍在监听；停止旧服务后再试。")
    if not port_probe("127.0.0.1", ASTRBOT_WS_PORT):
        raise ConnectError("AstrBot 反向 WebSocket 端口 6199 已停止监听。")
    if port_probe("127.0.0.1", OLD_WEBHOOK_PORT):
        raise ConnectError("旧 webhook 端口 8000 仍在监听；没有应用更改。")

    backup_dir = _new_backup_dir(plan.project_root)
    backup_path = backup_dir / "onebot-config.json"
    report_path = backup_dir / "report.json"
    _assert_project_private_path(plan.project_root, backup_path)
    _assert_project_private_path(plan.project_root, report_path)
    _write_json_private(backup_path, plan.old_config)
    original_mode = _read_active_mode(plan.project_root)
    report = _base_report(plan)
    _write_json_private(report_path, report)

    marker_attempted = False
    try:
        with _new_http_client(client_factory) as client:
            _webui_set_config(client, plan.webui, plan.credential, plan.new_config)
            actual = _webui_get_config(client, plan.webui, plan.credential)
            if not _same_json(actual, plan.new_config):
                raise ConnectError("NapCat 未确认应用预期配置。")
        report["config_verified"] = True
        marker_attempted = True
        _write_active_mode(plan.project_root)
        report["active_mode_marker_written"] = True
        _write_json_private(report_path, report, allow_update=True)
        return report
    except Exception:  # noqa: BLE001 - 任一应用阶段异常都必须进入恢复流程。
        report["rollback_attempted"] = True
        if marker_attempted:
            try:
                _restore_active_mode(plan.project_root, original_mode)
                report["active_mode_marker_restored"] = True
            except Exception:  # noqa: BLE001 - 恢复标记失败需继续尝试恢复 NapCat 配置。
                report["active_mode_marker_restored"] = False
        try:
            with _new_http_client(client_factory) as client:
                _webui_set_config(client, plan.webui, plan.credential, plan.old_config)
                restored = _webui_get_config(client, plan.webui, plan.credential)
                report["rollback_verified"] = _same_json(restored, plan.old_config)
        except Exception:  # noqa: BLE001 - 记录恢复失败而不输出私有服务细节。
            report["rollback_verified"] = False
        _write_json_private(report_path, report, allow_update=True)
        if report["rollback_verified"]:
            if report["active_mode_marker_restored"] or not marker_attempted:
                raise ConnectError(
                    "新配置校验失败，已恢复旧配置；私有备份与报告已保存。"
                ) from None
            raise ConnectError(
                "NapCat 配置已恢复，但启动模式标记未能恢复；请检查私有模式文件。"
            ) from None
        raise ConnectError(
            "新配置校验失败，自动恢复未能确认；请使用私有备份人工检查。"
        ) from None


def _print_preview(plan: ConnectPlan) -> None:
    ws_action = "新增" if plan.websocket_client_added else "复用"
    print("预检通过：AstrBot 端口监听、NapCat 本机 WebUI 与登录身份均已确认。")
    print(f"计划：禁用旧 webhook 客户端 {plan.http_clients_disabled} 项；{ws_action} AstrBot WebSocket 客户端 1 项。")
    print(f"端口：6199 已监听；8000 {'仍在监听' if plan.port_8000_listening else '未监听'}。")
    print("只预览，不会写配置或发送消息；QQ 身份和任何 token 均不显示。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="默认只预检 NapCat 到 AstrBot 的 OneBot 切换计划。"
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--napcat-config-dir", type=Path, default=NAPCAT_CONFIG_DEFAULT)
    parser.add_argument("--account", choices=("auto", "primary", "secondary"), default="auto")
    parser.add_argument("--apply", action="store_true", help="应用预览配置并写私有备份")
    args = parser.parse_args(argv)
    try:
        plan = preflight(
            args.project_root,
            args.napcat_config_dir,
            account=args.account,
        )
        _print_preview(plan)
        if not args.apply:
            if plan.port_8000_listening:
                print("8000 仍在监听；停旧服务后再运行 --apply。", file=sys.stderr)
                return 2
            print("确认计划后再添加 --apply。")
            return 0
        if plan.port_8000_listening:
            raise ConnectError("旧 webhook 端口 8000 仍在监听；没有应用更改。")
        report = apply_plan(plan)
    except (OSError, ValueError, ConnectError) as exc:
        print(f"切换未执行：{exc}", file=sys.stderr)
        return 1
    print(
        "切换完成："
        f"旧 webhook 停用 {report['http_clients_disabled']} 项；"
        f"配置已验证={report['config_verified']}；"
        f"回滚尝试={report['rollback_attempted']}。"
    )
    print("AstrBot 私有启动模式标记已更新。")
    print("私有备份与仅含状态计数的报告保存在 data/astrbot/napcat-backups。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
