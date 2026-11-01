"""Enable the subscription chat provider without changing media providers."""

from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.astrbot_chatgpt_provider import PROVIDER_TYPE
from app.chatgpt_subscription import API_BASE, DEFAULT_MODEL, SubscriptionError

SOURCE_ID = "qqchat-chatgpt-subscription"
MODEL_ID = "qqchat-chatgpt-luna"


def subscription_config(config: dict, directory: Path, model: str = DEFAULT_MODEL) -> dict:
    result = copy.deepcopy(config)
    sources = result.setdefault("provider_sources", [])
    models = result.setdefault("provider", [])
    if not isinstance(sources, list) or not isinstance(models, list):
        raise SubscriptionError("AstrBot 模型列表格式无效")
    source = {
        "id": SOURCE_ID, "name": "QQChat ChatGPT subscription",
        "provider": "openai", "type": PROVIDER_TYPE, "provider_type": "chat_completion",
        "enable": True, "key": ["oauth-managed-locally"], "api_base": API_BASE,
        "timeout": 120, "proxy": "", "custom_headers": {},
        "chatgpt_credentials_dir": str(directory.resolve()),
    }
    entry = {
        "id": MODEL_ID, "name": "QQChat GPT-6 Luna · ChatGPT 订阅",
        "type": PROVIDER_TYPE, "provider_type": "chat_completion",
        "provider_source_id": SOURCE_ID, "model": model, "enable": True,
        "modalities": ["text", "tool_use"],
        "custom_extra_body": {"reasoning": {"effort": "low"}},
    }
    for entries, replacement in ((sources, source), (models, entry)):
        existing = next((item for item in entries if item.get("id") == replacement["id"]), None)
        if existing is not None:
            if existing.get("type") != PROVIDER_TYPE:
                raise SubscriptionError("ChatGPT 模型 ID 已被其他提供商占用")
            entries[entries.index(existing)] = replacement
        else:
            entries.append(replacement)
    runner = result.setdefault("agent_runner", {"runner_type": "local", "config": {}})
    if runner.get("runner_type") != "local":
        raise SubscriptionError("请先在 AstrBot 中选择 local 聊天运行器")
    selection = runner.setdefault("config", {}).setdefault("model", {})
    selection.update(provider_id=MODEL_ID, fallback_provider_ids=[], request_max_retries=1)
    return result


async def verify_subscription(subscription, model: str = DEFAULT_MODEL) -> dict:
    models = await subscription.list_models()
    if not any(item["slug"] == model for item in models):
        available = ", ".join(item["slug"] for item in models)
        raise SubscriptionError(f"此账号暂未提供 {model}；当前模型：{available}")
    response = await subscription.respond({
        "model": model, "input": [{"role": "user", "content": "Reply with exactly OK."}],
        "reasoning": {"effort": "low"}, "store": False, "stream": True,
    })
    if not any(
        part.get("type") == "output_text" and str(part.get("text", "")).strip()
        for item in response.get("output", []) if item.get("type") == "message"
        for part in item.get("content", [])
    ):
        raise SubscriptionError("ChatGPT 测试推理未返回可用文字，未切换机器人模型")
    return {"model": model, "completed": True}


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activate_subscription(project_root: Path, directory: Path, model: str = DEFAULT_MODEL) -> Path:
    """Called only after verified, completed inference, with a recoverable backup."""
    root = project_root.resolve()
    path = root / "data/astrbot/instance/data/cmd_config.json"
    original = json.loads(path.read_text(encoding="utf-8-sig"))
    updated = subscription_config(original, directory, model)
    launcher = root / "scripts/run_astrbot.ps1"
    if os.name != "nt" or not launcher.is_file():
        raise SubscriptionError("此一键切换入口需要已安装的 Windows AstrBot 实例")
    backup_dir = root / "data/astrbot/config-backups" / (
        "chatgpt-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ") + "-" + secrets.token_hex(3)
    )
    backup_dir.mkdir(parents=True, mode=0o700)
    backup = backup_dir / path.name
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)

    def run(*flags: str) -> None:
        command = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(launcher), *flags,
        ]
        log = backup_dir / ("lifecycle-" + ("stop" if "-Stop" in flags else "start") + ".log")
        try:
            # The launcher starts a long-lived supervisor. PIPE handles can be
            # inherited by that child and keep communicate() waiting forever.
            # A regular file lets us wait for the launcher itself to finish.
            with log.open("wb") as stream:
                result = subprocess.run(
                    command, cwd=root, stdout=stream, stderr=stream, timeout=180, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except subprocess.TimeoutExpired as exc:
            raise SubscriptionError("AstrBot 启停超时，请检查本机服务日志") from exc
        if result.returncode:
            # The lifecycle scripts may report machine-specific paths. Keep the
            # output locally; no config, API key or arbitrary traceback in chat.
            raise SubscriptionError(f"AstrBot 切换步骤失败，详情保存在 {log}")

    run("-Stop")
    try:
        # Catch panel edits made while preflight was running, before overwriting.
        latest = json.loads(path.read_text(encoding="utf-8-sig"))
        original = latest
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        updated = subscription_config(latest, directory, model)
        _atomic_json(path, updated)
        run("-ReuseExistingConfig")
    except BaseException:
        _atomic_json(path, original)
        try:
            run("-ReuseExistingConfig")
        except SubscriptionError:
            pass
        raise
    return backup
