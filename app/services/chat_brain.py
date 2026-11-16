"""Select the actual AstrBot chat provider for the QQ profile from the local account panel."""
from __future__ import annotations

import asyncio
import copy
import ipaddress
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

LUNA = "qqchat-chatgpt-luna"
PROFILE = "qqchat-dafeiyu"
PRESETS = {
    "luna": ("当前订阅 · GPT-6 Luna", "", ""),
    "ollama": ("Ollama 本地模型", "http://127.0.0.1:11434/v1", "chat"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "chat"),
    "zhipu": ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "chat"),
    "openai": ("OpenAI GPT", "https://api.openai.com/v1", "responses"),
    "claude": ("Anthropic Claude", "https://api.anthropic.com", "messages"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "chat"),
    "custom": ("自定义兼容接口", "", "chat"),
}
TYPES = {"chat": "openai_chat_completion", "responses": "openai_responses", "messages": "anthropic_chat_completion"}


class BrainError(ValueError):
    pass


def endpoint(value):
    if not isinstance(value, str) or len(value) > 700 or any(c.isspace() or c in "\\\r\n" for c in value):
        raise BrainError("接口地址格式不正确。")
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
        raise BrainError("请填写服务的 HTTP 或 HTTPS 基础地址，密钥填在单独的输入框里。")
    try:
        port = url.port
    except ValueError as exc:
        raise BrainError("接口端口不正确。") from exc
    if port == 0:
        raise BrainError("接口端口不正确。")
    return value.rstrip("/")


def local_endpoint(url):
    name = urlsplit(url).hostname
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


class ChatBrain:
    def __init__(self, context):
        self.context = context
        self.lock = asyncio.Lock()

    def profile(self):
        manager = getattr(self.context, "astrbot_config_mgr", None)
        confs = getattr(manager, "confs", {})
        conf = confs.get(PROFILE)
        if conf is None:
            conf = next((c for key,c in confs.items() if key != "default" and c.get("qqchat_brain_profile") is True), None)
        if conf is None or conf.get("agent_runner", {}).get("runner_type") != "local":
            raise BrainError("尚未加载大肥鱼的 QQ 聊天配置。")
        return conf

    async def installation_profile(self):
        try:
            return self.profile()
        except BrainError:
            manager = getattr(self.context, "astrbot_config_mgr", None)
            if manager is None or PROFILE in manager.confs:
                raise
        base = self.context.get_config()
        if base.get("agent_runner", {}).get("runner_type") != "local":
            raise BrainError("请先在 AstrBot 设置里启用本地 Agent 运行器。")
        platforms = [p["id"] + "::" for p in base.get("platform", []) if p.get("enable") and p.get("id")
                     and p.get("type") in {"aiocqhttp","onebot","onebot_v11"}]
        if not platforms:
            raise BrainError("请先在 AstrBot 添加并启用 QQ 的 OneBot 连接。")
        initial = copy.deepcopy(dict(base))
        initial["qqchat_brain_profile"] = True
        profile_id = await manager.create_conf(config=initial, name="QQ · 大肥鱼")
        routing = dict(manager.ucr.umop_to_conf_id)
        try:
            await manager.ucr.update_routing_data({**dict.fromkeys(platforms, profile_id),
                                                   **{k:v for k,v in routing.items() if k not in platforms}})
        except BaseException:
            await manager.ucr.update_routing_data(routing)
            await manager.delete_conf(profile_id)
            raise
        return manager.confs[profile_id]

    def saved(self):
        if not self.context:
            return []
        return [p for p in self.context.get_config().get("provider", [])
                if p.get("qqchat_preset") in PRESETS and str(p.get("id", "")).startswith("qqchat-brain-")]

    def status(self):
        options = [{"id": key, "label": value[0], "base": value[1], "protocol": value[2]} for key, value in PRESETS.items()]
        try:
            active = self.profile()["agent_runner"]["config"]["model"]["provider_id"]
        except (BrainError, KeyError):
            active = ""
        saved = [{"id": p["id"], "preset": p["qqchat_preset"], "base": p["api_base"], "model": p["model"],
                  "protocol": next(k for k, v in TYPES.items() if p["type"] == v)} for p in self.saved() if p.get("type") in TYPES.values()]
        return {"options": options, "profiles": saved, "active": active, "luna": LUNA}

    def candidate(self, data):
        preset = data.get("preset")
        if not isinstance(preset, str) or preset not in PRESETS:
            raise BrainError("请选择模型服务。")
        if preset == "luna":
            return LUNA, None
        default_base, default_protocol = PRESETS[preset][1:]
        base = endpoint(data.get("base") or default_base)
        protocol = data.get("protocol") or default_protocol
        model = data.get("model")
        if protocol not in TYPES or not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
            raise BrainError("请填写服务端实际提供的模型名，并选择接口协议。")
        model = model.strip()
        key = data.get("key", "")
        if not isinstance(key, str) or len(key) > 2048 or any(ord(c) < 32 for c in key):
            raise BrainError("密钥格式不正确。")
        # Reuse a saved secret only for the exact selected service and endpoint.
        previous = next((p for p in self.saved() if p["id"] == data.get("saved") and p["qqchat_preset"] == preset
                         and p["api_base"] == base and p["type"] == TYPES[protocol]), None)
        if not key and previous:
            key = previous["key"][0]
        if not key:
            if preset != "ollama" and not local_endpoint(base):
                raise BrainError("请填写这个接口的 API 密钥。")
            key = "ollama" if preset == "ollama" else "local"
        if previous and model == previous["model"] and key == previous["key"][0]:
            return previous["id"], None
        pid = "qqchat-brain-" + uuid4().hex
        return pid, {"id": pid, "type": TYPES[protocol], "provider_type": "chat_completion", "enable": True,
                     "provider": preset, "qqchat_preset": preset, "model": model, "api_base": base, "key": [key],
                     "modalities": ["text", "tool_use"], "timeout": 90}

    async def models(self, data):
        base = endpoint(data.get("base") or PRESETS["ollama"][1])
        async with httpx.AsyncClient(trust_env=False, timeout=8) as client:
            response = await client.get(base + "/models")
            response.raise_for_status()
            rows = response.json().get("data", [])
        return {"models": [r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]}

    async def change(self, data, *, apply):
        async with self.lock:
            if self.context is None:
                raise BrainError("请从正在运行的大肥鱼本机账号页测试模型。")
            manager = self.context.provider_manager
            pid, candidate = self.candidate(data)
            created = False
            try:
                if candidate:
                    # Probe before saving credentials or changing the running QQ profile.
                    await manager.load_provider(candidate)
                    created = True
                provider = self.context.get_provider_by_id(pid)
                if not callable(getattr(provider, "text_chat", None)):
                    raise BrainError("提供方未加载，请检查适配器或订阅登录状态。")
                async with asyncio.timeout(95):
                    reply = await provider.text_chat(prompt="只回复一个简短的中文问候。", request_max_retries=1)
                answer = getattr(reply, "completion_text", "")
                if not isinstance(answer, str) or not answer.strip():
                    raise BrainError("模型没有返回文字，原选择已保留。")
                if apply:
                    profile = await self.installation_profile()
                    if candidate:
                        await manager.terminate_provider(pid)
                        created = False
                        await manager.create_provider(candidate)
                        if not self.context.get_provider_by_id(pid):
                            raise BrainError("保存后未能加载提供方。")
                    runner = profile["agent_runner"]
                    updated = copy.deepcopy(runner)
                    config = updated["config"]
                    old = config["model"].get("provider_id")
                    config["model"]["provider_id"] = pid
                    if old and config.get("compression", {}).get("provider_id") == old:
                        config["compression"]["provider_id"] = pid
                    try:
                        await profile.save_config_async({"agent_runner": updated})
                    except Exception:
                        profile["agent_runner"] = runner
                        raise
                return {"applied": apply, "model": provider.get_model(), "reply": answer.strip()[:160]}
            except BrainError:
                raise
            except Exception as exc:  # noqa: BLE001 -- native adapters use different SDK errors; never display their payloads
                status = getattr(exc, "status_code", None)
                hint = f"（HTTP {status}）" if isinstance(status, int) else ""
                raise BrainError(f"测试或保存未完成{hint}，请检查地址、模型名和登录凭据。") from None
            finally:
                if created:
                    await manager.terminate_provider(pid)
