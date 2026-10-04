from __future__ import annotations

import argparse
import asyncio
import copy
import importlib
import json
import math
import os
import shutil
import socket
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

EXPECTED_ASTRBOT_VERSION = "4.28.2"
ASTRBOT_PORT = 6185
PERSONA_ID = "qqchat-local-persona"
OWNED_MODELS = {
    "zhipu": {"source_id": "qqchat-zhipu", "model_id": "qqchat-zhipu-chat"},
    "groq": {"source_id": "qqchat-groq", "model_id": "qqchat-groq-chat"},
}
OWNED_SOURCE_IDS = {item["source_id"] for item in OWNED_MODELS.values()}
OWNED_MODEL_IDS = {item["model_id"] for item in OWNED_MODELS.values()}


class MigrationError(RuntimeError):
    """配置无法安全迁移。"""


def _project_root(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _setting(settings: Any, name: str) -> Any:
    if isinstance(settings, dict):
        return settings.get(name)
    return getattr(settings, name, None)


def _require_text(value: Any, label: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise MigrationError(f"{label} 配置格式错误")
    value = value.strip()
    if not value and not optional:
        raise MigrationError(f"{label} 不能为空")
    return value


def _safe_base_url(value: str) -> str:
    """隐藏 URL 中的账号信息、查询参数和片段。"""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "(自定义地址已隐藏)"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "(自定义地址已隐藏)"


def _validate_base_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        _ = parsed.port
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise MigrationError(f"{label} 必须是有效的 HTTP(S) 地址")
    return value


def _finite_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MigrationError(f"{label} 配置格式错误")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise MigrationError(f"{label} 配置超出范围")
    return number


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MigrationError(f"{label} 必须是正整数")
    return value


def _provider_settings(settings: Any, name: str) -> dict[str, Any]:
    if name == "zhipu":
        values = {
            "key": _require_text(_setting(settings, "llm_api_key"), "智谱 API Key", optional=True),
            "api_base": _require_text(_setting(settings, "llm_base_url"), "智谱 API 地址"),
            "model": _require_text(_setting(settings, "llm_model"), "智谱模型名"),
        }
    else:
        values = {
            "key": _require_text(_setting(settings, "groq_api_key"), "Groq API Key", optional=True),
            "api_base": _require_text(_setting(settings, "groq_base_url"), "Groq API 地址"),
            "model": _require_text(_setting(settings, "groq_model"), "Groq 模型名"),
        }
    values["api_base"] = _validate_base_url(values["api_base"], f"{name} API 地址")
    values["timeout"] = _finite_number(
        _setting(settings, "llm_timeout_seconds"), "LLM 超时", minimum=0.001
    )
    values["temperature"] = _finite_number(
        _setting(settings, "llm_temperature"), "LLM 温度", minimum=0
    )
    values["max_tokens"] = _positive_integer(
        _setting(settings, "llm_max_output_tokens"), "LLM 最大输出词元数"
    )
    return values


def _ids_index(items: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise MigrationError(f"{label} 列表包含非对象项目")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise MigrationError(f"{label} 项目缺少有效 ID")
        if item_id in result:
            raise MigrationError(f"{label} 存在重复 ID")
        result[item_id] = item
    return result


def _list_field(config: dict[str, Any], name: str) -> list[Any]:
    value = config.get(name, [])
    if not isinstance(value, list):
        raise MigrationError(f"AstrBot {name} 必须是列表")
    return value


def _validate_owned_types(
    sources: dict[str, dict[str, Any]], models: dict[str, dict[str, Any]]
) -> None:
    for ids in OWNED_MODELS.values():
        source = sources.get(ids["source_id"])
        if source is not None and (
            source.get("provider") != "openai"
            or source.get("type") != "openai_chat_completion"
            or source.get("provider_type") != "chat_completion"
        ):
            raise MigrationError(f"来源 ID {ids['source_id']} 已被其他配置类型占用")
        if source is not None:
            keys = source.get("key")
            timeout = source.get("timeout")
            if (
                not isinstance(source.get("enable"), bool)
                or not isinstance(keys, list)
                or any(not isinstance(key, str) for key in keys)
                or not isinstance(source.get("api_base"), str)
                or isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(float(timeout))
            ):
                raise MigrationError(f"来源 ID {ids['source_id']} 配置结构错误")
        model = models.get(ids["model_id"])
        if model is not None and (
            model.get("type") != "openai_chat_completion"
            or model.get("provider_source_id") != ids["source_id"]
            or model.get("provider_type", "chat_completion") != "chat_completion"
        ):
            raise MigrationError(f"模型 ID {ids['model_id']} 已被其他配置类型占用")
        if model is not None and (
            not isinstance(model.get("enable"), bool)
            or not isinstance(model.get("model"), str)
            or not model.get("model", "").strip()
            or not isinstance(model.get("custom_extra_body"), dict)
        ):
            raise MigrationError(f"模型 ID {ids['model_id']} 配置结构错误")


def _source_config(name: str, values: dict[str, Any]) -> dict[str, Any]:
    ids = OWNED_MODELS[name]
    has_key = bool(values["key"])
    return {
        "id": ids["source_id"],
        "name": f"QQChat {name}",
        "provider": "openai",
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "enable": has_key,
        "key": [values["key"]] if has_key else [],
        "api_base": values["api_base"],
        "timeout": values["timeout"],
        "proxy": "",
        "custom_headers": {},
    }


def _model_config(name: str, values: dict[str, Any]) -> dict[str, Any]:
    ids = OWNED_MODELS[name]
    has_key = bool(values["key"])
    return {
        "id": ids["model_id"],
        "name": f"QQChat {name} chat",
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "provider_source_id": ids["source_id"],
        "model": values["model"],
        "enable": has_key,
        "modalities": ["text"],
        "custom_extra_body": {
            "temperature": values["temperature"],
            "max_tokens": values["max_tokens"],
        },
    }


def _effective_model_enabled(
    model: dict[str, Any], source_index: dict[str, dict[str, Any]]
) -> bool:
    if model.get("enable") is not True:
        return False
    source = source_index.get(model.get("provider_source_id"))
    if not isinstance(source, dict):
        return False
    keys = source.get("key")
    return isinstance(keys, list) and any(isinstance(key, str) and key.strip() for key in keys)


def plan_migration(
    config: dict[str, Any], settings: Any, *, replace_owned: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回迁移后的配置和不含凭据的摘要；不修改传入对象。"""
    if not isinstance(config, dict):
        raise MigrationError("AstrBot 配置根节点必须是对象")
    result = copy.deepcopy(config)
    primary = _require_text(_setting(settings, "llm_provider"), "主 LLM 提供商").lower()
    fallback = _require_text(
        _setting(settings, "llm_fallback_provider"), "备用 LLM 提供商", optional=True
    ).lower()
    if primary not in OWNED_MODELS or (fallback and fallback not in OWNED_MODELS):
        raise MigrationError("当前迁移只支持 zhipu 和 groq 提供商")
    source_values = {name: _provider_settings(settings, name) for name in OWNED_MODELS}

    sources_list = _list_field(result, "provider_sources")
    models_list = _list_field(result, "provider")
    sources = _ids_index(sources_list, "provider_sources")
    models = _ids_index(models_list, "provider")
    _validate_owned_types(sources, models)

    runner = result.get("agent_runner")
    if runner is None:
        runner = {"runner_type": "local", "config": {}}
        result["agent_runner"] = runner
    if not isinstance(runner, dict):
        raise MigrationError("AstrBot agent_runner 必须是对象")
    runner_type = runner.get("runner_type", "local")
    if runner_type != "local":
        raise MigrationError("当前只支持 AstrBot local runner；请先在面板切换")
    runner_config = runner.get("config")
    if runner_config is None:
        runner_config = {}
        runner["config"] = runner_config
    if not isinstance(runner_config, dict):
        raise MigrationError("AstrBot agent_runner.config 必须是对象")
    model_config = runner_config.get("model")
    if model_config is None:
        model_config = {}
        runner_config["model"] = model_config
    if not isinstance(model_config, dict):
        raise MigrationError("AstrBot agent_runner.config.model 必须是对象")
    compression = runner_config.get("compression")
    if compression is None:
        compression = {}
        runner_config["compression"] = compression
    if not isinstance(compression, dict):
        raise MigrationError("AstrBot agent_runner.config.compression 必须是对象")
    persona_config = runner_config.get("persona")
    if persona_config is None:
        persona_config = {}
        runner_config["persona"] = persona_config
    if not isinstance(persona_config, dict):
        raise MigrationError("AstrBot agent_runner.config.persona 必须是对象")
    current_persona_id = persona_config.get("persona_id", "default")
    if not isinstance(current_persona_id, str):
        raise MigrationError("AstrBot 默认人格 ID 格式错误")
    persona_default_owned = current_persona_id in {"", "default", PERSONA_ID}
    if persona_default_owned:
        persona_config["persona_id"] = PERSONA_ID

    current_default = model_config.get("provider_id", "")
    if not isinstance(current_default, str):
        raise MigrationError("AstrBot 默认模型 ID 格式错误")
    current_fallbacks = model_config.get("fallback_provider_ids", [])
    if not isinstance(current_fallbacks, list) or any(
        not isinstance(item, str) for item in current_fallbacks
    ):
        raise MigrationError("AstrBot 备用模型 ID 列表格式错误")
    had_owned_config = any(item_id in sources for item_id in OWNED_SOURCE_IDS) or any(
        item_id in models for item_id in OWNED_MODEL_IDS
    )
    initial_empty_choice = not current_default and not had_owned_config

    actions: dict[str, dict[str, str]] = {}
    for name in OWNED_MODELS:
        source_id = OWNED_MODELS[name]["source_id"]
        model_id = OWNED_MODELS[name]["model_id"]
        new_source = _source_config(name, source_values[name])
        new_model = _model_config(name, source_values[name])
        if source_id in sources:
            if replace_owned:
                sources_list[sources_list.index(sources[source_id])] = new_source
                sources[source_id] = new_source
                source_action = "同步"
            else:
                source_action = "保留"
        else:
            sources_list.append(new_source)
            sources[source_id] = new_source
            source_action = "新增"
        if model_id in models:
            if replace_owned:
                models_list[models_list.index(models[model_id])] = new_model
                models[model_id] = new_model
                model_action = "同步"
            else:
                model_action = "保留"
        else:
            models_list.append(new_model)
            models[model_id] = new_model
            model_action = "新增"
        actions[name] = {"source_action": source_action, "model_action": model_action}

    result["provider_sources"] = sources_list
    result["provider"] = models_list
    runner["runner_type"] = runner_type
    source_index = _ids_index(sources_list, "provider_sources")
    model_index = _ids_index(models_list, "provider")
    preferred_order = [primary]
    if fallback and fallback not in preferred_order:
        preferred_order.append(fallback)
    available_order = [
        OWNED_MODELS[name]["model_id"]
        for name in preferred_order
        if OWNED_MODELS[name]["model_id"] in model_index
        and _effective_model_enabled(model_index[OWNED_MODELS[name]["model_id"]], source_index)
    ]

    may_choose_default = initial_empty_choice or (
        replace_owned and current_default in OWNED_MODEL_IDS
    )
    if may_choose_default:
        chosen_default = available_order[0] if available_order else ""
        model_config["provider_id"] = chosen_default
        model_config["fallback_provider_ids"] = [
            item for item in available_order if item != chosen_default
        ]
    else:
        chosen_default = current_default
        model_config["provider_id"] = current_default
        model_config["fallback_provider_ids"] = current_fallbacks

    if initial_empty_choice:
        model_config["request_max_retries"] = 1
        max_messages = _positive_integer(
            _setting(settings, "max_context_messages"), "最大上下文消息数"
        )
        compression["max_turns"] = max(1, max_messages // 2)
        compression["trim_turns"] = 1
        compression["overflow_strategy"] = "truncate_by_turns"

    summary_models = []
    for name in OWNED_MODELS:
        model_id = OWNED_MODELS[name]["model_id"]
        source_id = OWNED_MODELS[name]["source_id"]
        active_source = source_index[source_id]
        active_model = model_index[model_id]
        merged = {**active_source, **active_model}
        extra_body = merged.get("custom_extra_body", {})
        summary_models.append(
            {
                "provider": name,
                "source_id": source_id,
                "model_id": model_id,
                "model": merged["model"],
                "api_base": _safe_base_url(merged.get("api_base", "")),
                "enabled": _effective_model_enabled(active_model, source_index),
                "temperature": extra_body.get("temperature"),
                "max_tokens": extra_body.get("max_tokens"),
                "timeout_seconds": merged.get("timeout"),
                **actions[name],
            }
        )
    prompt_reader = getattr(settings, "persona_prompt", None)
    persona_prompt = prompt_reader() if callable(prompt_reader) else ""
    if not isinstance(persona_prompt, str) or not persona_prompt.strip():
        raise MigrationError("原角色 system prompt 为空")
    persona_label = _require_text(
        _setting(settings, "persona_name"), "原角色名称", optional=True
    ) or "默认角色"
    summary = {
        "primary_from_env": primary,
        "fallback_from_env": fallback or None,
        "models": summary_models,
        "default_provider_id": chosen_default,
        "default_preserved": not may_choose_default,
        "initial_empty_choice": initial_empty_choice,
        "fallback_provider_ids": model_config.get("fallback_provider_ids", []),
        "persona": {
            "id": PERSONA_ID,
            "label": persona_label,
            "char_count": len(persona_prompt),
            "default_updated": persona_default_owned,
        },
    }
    return result, summary


def read_project_settings(project_root: Path) -> Any:
    """按项目根目录读取 .env，不切换当前目录。"""
    project_root = _project_root(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    module = importlib.import_module("app.astrbot_runtime")
    return module.project_settings(project_root)


def check_astrbot_version(project_root: Path) -> str:
    project_root = _project_root(project_root)
    site_packages = (
        project_root / "data" / "astrbot" / "runtime" / "tool-envs" / "astrbot"
        / "Lib" / "site-packages"
    )
    if not site_packages.is_dir():
        raise MigrationError("缺少项目内 AstrBot 运行环境")
    sys.path.insert(0, str(site_packages))
    try:
        astrbot = importlib.import_module("astrbot")
    except Exception as exc:
        raise MigrationError("无法读取项目内 AstrBot 版本") from exc
    version = str(getattr(astrbot, "__version__", ""))
    if version != EXPECTED_ASTRBOT_VERSION:
        raise MigrationError(f"需要 AstrBot {EXPECTED_ASTRBOT_VERSION}，当前版本不匹配")
    return version


def _configure_helpers(project_root: Path) -> Any:
    project_root = _project_root(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        return importlib.import_module("scripts.configure_astrbot")
    except Exception as exc:
        raise MigrationError("无法载入项目的安全路径与原子写入工具") from exc


def load_target_config(project_root: Path) -> tuple[Path, dict[str, Any]]:
    project_root = _project_root(project_root)
    helpers = _configure_helpers(project_root)
    target = project_root / "data" / "astrbot" / "instance" / "data" / "cmd_config.json"
    helpers._assert_managed_path(project_root, target)
    if not target.is_file():
        raise MigrationError("AstrBot 配置文件不存在，请先完成 AstrBot 初始配置")
    try:
        config = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("AstrBot 配置文件无法安全读取") from exc
    if not isinstance(config, dict):
        raise MigrationError("AstrBot 配置根节点必须是对象")
    return target, config


def _astrbot_is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", ASTRBOT_PORT), timeout=0.25):
            return True
    except OSError:
        return False


def _create_backup_dir(project_root: Path) -> Path:
    project_root = _project_root(project_root)
    helpers = _configure_helpers(project_root)
    backup_root = project_root / "data" / "astrbot" / "config-backups"
    helpers._assert_managed_path(project_root, backup_root)
    helpers._ensure_directory(project_root, backup_root)
    backup_dir: Path | None = None
    for _ in range(10):
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        candidate = backup_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        helpers._assert_managed_path(project_root, candidate)
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        backup_dir = candidate
        break
    if backup_dir is None:
        raise MigrationError("无法创建唯一的配置备份目录")
    return backup_dir


def _sqlite_backup(source_path: Path, backup_path: Path) -> None:
    """用 SQLite 在线备份接口复制数据库，包含 WAL 中的已提交数据。"""
    source = target = None
    try:
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(backup_path)
        source.backup(target)
    except sqlite3.Error as exc:
        raise MigrationError("AstrBot 人格数据库备份失败") from exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def _restore_sqlite(backup_path: Path, target_path: Path) -> None:
    source = target = None
    try:
        source = sqlite3.connect(backup_path)
        target = sqlite3.connect(target_path)
        source.backup(target)
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


async def apply_migration(
    project_root: Path,
    config: dict[str, Any],
    settings: Any,
    *,
    replace_owned: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """先备份，再导入人格，最后原子写配置；失败时尝试恢复两份数据。"""
    project_root = _project_root(project_root)
    if _astrbot_is_running():
        raise MigrationError("检测到 AstrBot 正在 127.0.0.1:6185 监听；请停止后再迁移")
    if not isinstance(config, dict):
        raise MigrationError("待写入的 AstrBot 配置必须是对象")
    helpers = _configure_helpers(project_root)
    persona_module = importlib.import_module("app.astrbot_config_persona")
    if getattr(persona_module, "PERSONA_ID", None) != PERSONA_ID:
        raise MigrationError("人格迁移模块与配置 ID 不一致")
    target = project_root / "data" / "astrbot" / "instance" / "data" / "cmd_config.json"
    database = project_root / "data" / "astrbot" / "instance" / "data" / "data_v4.db"
    for path in (target, database):
        helpers._assert_managed_path(project_root, path)
    if not target.is_file() or not database.is_file():
        raise MigrationError("AstrBot 配置或人格数据库不存在")

    backup_dir = _create_backup_dir(project_root)
    backup_config = backup_dir / "cmd_config.json"
    backup_database = backup_dir / "data_v4.db"
    helpers._assert_managed_path(project_root, backup_config)
    helpers._assert_managed_path(project_root, backup_database)
    try:
        shutil.copy2(target, backup_config)
        _sqlite_backup(database, backup_database)
    except Exception as exc:
        raise MigrationError(f"迁移前备份失败，未修改配置；备份位置：{backup_dir}") from exc

    try:
        persona_result = await persona_module.migrate_persona(
            settings, database, replace_owned=replace_owned
        )
        helpers._write_json_atomically(target, config)
    except Exception as exc:  # noqa: BLE001
        restored_database = False
        restored_config = False
        try:
            _restore_sqlite(backup_database, database)
            restored_database = True
        except sqlite3.Error:
            restored_database = False
        try:
            old_config = json.loads(backup_config.read_text(encoding="utf-8-sig"))
            if isinstance(old_config, dict):
                helpers._write_json_atomically(target, old_config)
                restored_config = True
        except (OSError, ValueError):
            restored_config = False
        state = "已恢复" if restored_database and restored_config else "恢复不完整"
        raise MigrationError(
            f"人格或配置写入失败（{type(exc).__name__}），{state}；备份位置：{backup_dir}"
        ) from None
    if not isinstance(persona_result, dict):
        persona_result = {"id": PERSONA_ID, "retained": False, "created": False, "updated": False}
    return backup_dir, persona_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="预览或迁移 qq-chatrobot 的 LLM 提供商配置到 AstrBot。"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="项目根目录；默认使用脚本所在项目",
    )
    parser.add_argument("--apply", action="store_true", help="备份后写入配置")
    parser.add_argument(
        "--replace-owned",
        action="store_true",
        help="从 .env 重新同步本脚本管理的来源和模型",
    )
    args = parser.parse_args(argv)
    project_root = _project_root(args.project_root)
    try:
        check_astrbot_version(project_root)
        settings = read_project_settings(project_root)
        _, current = load_target_config(project_root)
        planned, summary = plan_migration(current, settings, replace_owned=args.replace_owned)
        output: dict[str, Any] = {
            "mode": "apply" if args.apply else "preview",
            "migration": summary,
        }
        if args.apply:
            backup_dir, persona_result = asyncio.run(
                apply_migration(
                    project_root,
                    planned,
                    settings,
                    replace_owned=args.replace_owned,
                )
            )
            summary["persona"]["action"] = next(
                (
                    action
                    for key, action in (
                        ("created", "新增"),
                        ("updated", "同步"),
                        ("retained", "保留"),
                    )
                    if persona_result.get(key)
                ),
                "完成",
            )
            output["backup"] = str(backup_dir)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as exc:
        print(f"迁移未执行：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"迁移未执行：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
