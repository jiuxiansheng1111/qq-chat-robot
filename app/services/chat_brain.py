"""Select the actual AstrBot chat provider for the QQ profile from the local account panel."""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.services.bounded_http import request_json

LUNA = "qqchat-chatgpt-luna"
PROFILE = "qqchat-dafeiyu"
WEB_SESSION = "webchat:FriendMessage:qqchat-web-assistant"
MOBILE_ONEBOT_ID = "qq-chatrobot-phone"
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
MODALITIES = {
    "auto": ["text", "tool_use"], "text": ["text"], "tools": ["text", "tool_use"],
    "image": ["text", "image"], "image_tools": ["text", "image", "tool_use"],
}


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


async def ollama_window(config):
    base = config["api_base"].removesuffix("/v1")
    secret = config["key"][0]
    headers = {"Authorization":"Bearer "+secret} if secret != "ollama" else {}
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            data = await request_json(client,"GET",base+"/api/ps",headers=headers,max_bytes=512000,timeout_seconds=3)
        wanted = config["model"]
        names = {wanted,wanted+":latest"} if ":" not in wanted.rsplit("/",1)[-1] else {wanted}
        for item in data.get("models",[]):
            if item.get("name") in names or item.get("model") in names:
                window = item.get("context_length")
                if type(window) is int and 0<window<=2**31-1: return window
    except (httpx.HTTPError, TimeoutError, ValueError, AttributeError, TypeError):
        pass
    return 0


async def ollama_details(client, base, model, headers):
    return await request_json(client, "POST", base.removesuffix("/v1") + "/api/show",
                              json={"model": model}, headers=headers, max_bytes=1024000, timeout_seconds=4)


async def ollama_modalities(config):
    secret = config["key"][0]
    headers = {"Authorization": "Bearer " + secret} if secret != "ollama" else {}
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            details = await ollama_details(client, config["api_base"], config["model"], headers)
        caps = details.get("capabilities")
        if isinstance(caps, list):
            return ["text"] + [name for capability, name in (("vision", "image"), ("tools", "tool_use")) if capability in caps]
    except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
        pass
    return ["text"]


def runner_with_provider(runner, provider_id):
    updated = copy.deepcopy(runner)
    config = updated["config"]
    old = config["model"].get("provider_id")
    config["model"]["provider_id"] = provider_id
    if old and config.get("compression", {}).get("provider_id") == old:
        config["compression"]["provider_id"] = provider_id
    return updated


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

    async def select_model(self, provider_id):
        try:
            profile = self.profile()
        except BrainError:
            manager = getattr(self.context, "astrbot_config_mgr", None)
            if manager is None or PROFILE in manager.confs:
                raise
        else:
            current = profile["agent_runner"]
            # On a phone, the built-in WebChat is also a user-facing entry to this agent.
            router = None
            if os.environ.get("QQCHAT_TERMUX_BIN") and profile.get("qqchat_brain_profile"):
                manager = self.context.astrbot_config_mgr
                profile_id = next(k for k, value in manager.confs.items() if value is profile)
                router = manager.ucr
                prior_routes = dict(router.umop_to_conf_id)
                if prior_routes.get("webchat::") == profile_id:
                    router = None
            try:
                if router is not None:
                    await router.update_routing_data({"webchat::":profile_id,**{k:v for k,v in prior_routes.items() if k!="webchat::"}})
                await profile.save_config_async({"agent_runner": runner_with_provider(current, provider_id)})
            except BaseException:
                profile["agent_runner"] = current
                if router is not None:
                    await router.update_routing_data(prior_routes)
                raise
            return
        base = self.context.get_config()
        if base.get("agent_runner", {}).get("runner_type") != "local":
            raise BrainError("请先在 AstrBot 设置里启用本地 Agent 运行器。")
        platforms = [p["id"] + "::" for p in base.get("platform", []) if p.get("enable") and p.get("id")
                     and p.get("type") in {"aiocqhttp","onebot","onebot_v11"}]
        initial = copy.deepcopy(dict(base))
        initial["qqchat_brain_profile"] = True
        initial["agent_runner"] = runner_with_provider(initial["agent_runner"], provider_id)
        profile_id = await manager.create_conf(config=initial, name="大肥鱼 · QQ 和网页助手")
        routing = dict(manager.ucr.umop_to_conf_id)
        target_routes = [WEB_SESSION, *(platforms or [MOBILE_ONEBOT_ID + "::"])]
        if os.environ.get("QQCHAT_TERMUX_BIN"):
            target_routes.append("webchat::")
        try:
            await manager.ucr.update_routing_data({**dict.fromkeys(target_routes, profile_id),
                                                   **{k:v for k,v in routing.items() if k not in target_routes}})
        except BaseException:
            try:
                await manager.ucr.update_routing_data(routing)
            finally:
                await manager.delete_conf(profile_id)
            raise

    def saved(self):
        if not self.context:
            return []
        config = self.context.get_config()
        sources = {p["id"]: p for p in config.get("provider_sources", []) if p.get("id")}
        providers = [{**sources.get(p.get("provider_source_id"), {}), **p} for p in config.get("provider", [])]
        return [p for p in providers
                if p.get("qqchat_preset") in PRESETS and str(p.get("id", "")).startswith("qqchat-brain-")]

    def status(self):
        options = [{"id": key, "label": value[0], "base": value[1], "protocol": value[2]} for key, value in PRESETS.items()]
        try:
            active = self.profile()["agent_runner"]["config"]["model"]["provider_id"]
        except (BrainError, KeyError):
            active = ""
        saved = [{"id": p["id"], "preset": p["qqchat_preset"], "base": p["api_base"], "model": p["model"],
                  "context":p.get("qqchat_context_requested",0),"effective_context":p.get("max_context_tokens",0),
                  "mode": p.get("qqchat_model_mode", "auto"),
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
        if not isinstance(protocol, str) or protocol not in TYPES or not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
            raise BrainError("请填写服务端实际提供的模型名，并选择接口协议。")
        model = model.strip()
        requested = data.get("context")
        if requested in (None,""): requested = 0
        if type(requested) is not int or not 0<=requested<=2**31-1:
            raise BrainError("上下文长度请填写服务支持的正整数，或留空自动读取。")
        key = data.get("key", "")
        if not isinstance(key, str) or len(key) > 2048 or any(ord(c) < 32 for c in key):
            raise BrainError("密钥格式不正确。")
        # Reuse a saved secret only for the exact selected service and endpoint.
        previous = next((p for p in self.saved() if p["id"] == data.get("saved") and p["qqchat_preset"] == preset
                         and p["api_base"] == base and p["type"] == TYPES[protocol]), None)
        mode = data.get("mode", (previous or {}).get("qqchat_model_mode", "auto"))
        if not isinstance(mode, str) or mode not in MODALITIES:
            raise BrainError("请选择模型支持的能力。")
        if not key and previous:
            key = previous["key"][0]
        if not key:
            if preset != "ollama" and not local_endpoint(base):
                raise BrainError("请填写这个接口的 API 密钥。")
            key = "ollama" if preset == "ollama" else "local"
        if (previous and model == previous["model"] and key == previous["key"][0]
                and requested == previous.get("qqchat_context_requested", 0) and mode == previous.get("qqchat_model_mode", "auto")):
            return previous["id"], None
        pid = "qqchat-brain-" + uuid4().hex
        return pid, {"id": pid, "type": TYPES[protocol], "provider_type": "chat_completion", "enable": True,
                     "provider": preset, "qqchat_preset": preset, "model": model, "api_base": base, "key": [key],
                     "modalities": MODALITIES[mode].copy(), "qqchat_model_mode": mode, "timeout": 90, "qqchat_context_requested":requested,
                     "max_context_tokens":requested}

    async def models(self, data):
        base = endpoint(data.get("base") or PRESETS["ollama"][1])
        previous = next((p for p in self.saved() if p["id"] == data.get("saved") and p["qqchat_preset"] == "ollama"
                         and p["api_base"] == base), None)
        key = data.get("key") or (previous["key"][0] if previous else "")
        if not isinstance(key, str) or len(key) > 2048 or any(ord(c) < 32 for c in key):
            raise BrainError("密钥格式不正确。")
        headers = {"Authorization": "Bearer " + key} if key and key != "ollama" else {}
        async with httpx.AsyncClient(trust_env=False, timeout=8) as client:
            response = await request_json(client,"GET",base+"/models",headers=headers,max_bytes=1024000,timeout_seconds=8)
            rows = response.get("data", []) if isinstance(response,dict) else []
            names = list(dict.fromkeys(r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)))
            # Some Ollama installs keep catalogue entries for models whose data is missing.
            async def check(name):
                async with gate:
                    try:
                        await ollama_details(client, base, name, headers)
                        return True
                    except httpx.HTTPStatusError as exc:
                        return False if exc.response.status_code == 404 else None
                    except (httpx.HTTPError, TimeoutError, ValueError):
                        return None
            gate = asyncio.Semaphore(5)
            checks = await asyncio.gather(*(check(name) for name in names[:20]))
            native = True in checks
            if not native and False in checks:
                # An OpenAI-compatible server can list models without any Ollama-native API.
                try:
                    version = await request_json(client, "GET", base.removesuffix("/v1") + "/api/version",
                                                 headers=headers, max_bytes=8192, timeout_seconds=3)
                    native = isinstance(version, dict) and isinstance(version.get("version"), str)
                except (httpx.HTTPError, TimeoutError, ValueError):
                    pass
        missing = [name for name, ok in zip(names, checks) if ok is False] if native else []
        return {"models": [name for name in names if name not in missing], "unavailable": missing}

    async def change(self, data, *, apply):
        async with self.lock:
            if self.context is None:
                raise BrainError("请从正在运行的大肥鱼本机账号页测试模型。")
            manager = self.context.provider_manager
            pid, candidate = self.candidate(data)
            created = discard_saved = False
            try:
                if candidate:
                    # Probe before saving credentials or changing the running QQ profile.
                    if candidate["qqchat_preset"] == "ollama" and candidate["qqchat_model_mode"] == "auto":
                        candidate["modalities"] = await ollama_modalities(candidate)
                    created = True
                    await manager.load_provider(candidate)
                provider = self.context.get_provider_by_id(pid)
                if not callable(getattr(provider, "text_chat", None)):
                    raise BrainError("提供方未加载，请检查适配器或订阅登录状态。")
                async with asyncio.timeout(95):
                    reply = await provider.text_chat(prompt="只回复一个简短的中文问候。", request_max_retries=1)
                answer = getattr(reply, "completion_text", "")
                if not isinstance(answer, str) or not answer.strip():
                    raise BrainError("模型没有返回文字，原选择已保留。")
                window = (candidate or {}).get("max_context_tokens",0)
                if candidate and candidate["qqchat_preset"]=="ollama" and not window:
                    window = candidate["max_context_tokens"] = await ollama_window(candidate)
                if apply:
                    if candidate:
                        await manager.terminate_provider(pid)
                        created = False
                        discard_saved = True
                        await manager.create_provider(candidate)
                        if not self.context.get_provider_by_id(pid):
                            raise BrainError("保存后未能加载提供方。")
                    await self.select_model(pid)
                    discard_saved = False
                if not candidate:
                    window = next((p.get("max_context_tokens",0) for p in self.saved() if p["id"]==pid),0)
                return {"applied": apply, "model": provider.get_model(), "reply": answer.strip()[:160],"context":window}
            except BrainError:
                raise
            except Exception as exc:  # noqa: BLE001 -- native adapters use different SDK errors; never display their payloads
                status = getattr(exc, "status_code", None)
                hint = f"（HTTP {status}）" if isinstance(status, int) else ""
                raise BrainError(f"测试或保存未完成{hint}，请检查地址、模型名和登录凭据。") from None
            finally:
                if discard_saved and any(p.get("id")==pid for p in self.saved()):
                    await manager.delete_provider(provider_id=pid)
                if created:
                    await manager.terminate_provider(pid)
