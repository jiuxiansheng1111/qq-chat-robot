"""Official Sign in with ChatGPT for this locally hosted bot.

Credentials belong to this app, never to the Codex desktop client. Windows
credentials are encrypted with DPAPI; refreshes are serialized across processes.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.llm.providers import LLMError
from app.services.http_routing import outbound_http_client

AUTH_ORIGIN = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH_ORIGIN}/api/accounts/authorize"
TOKEN_URL = f"{AUTH_ORIGIN}/api/accounts/oauth/token"
API_BASE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "chatgpt"
_DPAPI_PREFIX = b"QQCHAT-DPAPI-1\n"


class SubscriptionError(LLMError):
    """A credential-safe diagnostic, suitable for bot/server logs."""


def _safe_identifier(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]", "", str(value or ""))[:100]


def _http_error(response: httpx.Response, action: str) -> SubscriptionError:
    # Upstream messages may echo request content or credentials. Keep only codes
    # and the request ID; never include response.text or the Authorization header.
    code = ""
    try:
        value = response.json().get("error", {})
        code = _safe_identifier(value.get("code") or value.get("type")) if isinstance(
            value, dict
        ) else _safe_identifier(value)
    except (ValueError, AttributeError):
        pass
    request_id = _safe_identifier(response.headers.get("x-request-id"))
    return SubscriptionError(
        f"ChatGPT {action}: HTTP {response.status_code} {code} request_id={request_id}"
    )


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Use the current Windows user's DPAPI key, with no external secrets."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SubscriptionError("ChatGPT 本机凭据加密/解密失败")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


class CredentialStore:
    def __init__(self, directory: Path = DEFAULT_STORE):
        self.directory = Path(directory)
        self.path = self.directory / "credentials.bin"

    def read(self) -> dict:
        if not self.path.exists():
            return {"profiles": {}}
        data = self.path.read_bytes()
        if data.startswith(_DPAPI_PREFIX):
            if os.name != "nt":
                raise SubscriptionError("此凭据仅可由原 Windows 用户读取，请重新登录")
            data = _protect(data[len(_DPAPI_PREFIX):], decrypt=True)
        elif os.name == "nt":
            raise SubscriptionError("ChatGPT 凭据未受本机加密保护，请重新登录")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise SubscriptionError("ChatGPT 本机凭据损坏，请重新登录") from exc
        if not isinstance(result, dict) or not isinstance(result.get("profiles"), dict):
            raise SubscriptionError("ChatGPT 本机凭据格式错误，请重新登录")
        return result

    def write(self, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps(state, ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            data = _DPAPI_PREFIX + _protect(data)
        temporary = self.path.with_name(f".{secrets.token_hex(12)}.tmp")
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @asynccontextmanager
    async def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.directory / "credentials.lock"
        with lock_path.open("a+b") as stream:
            os.chmod(lock_path, 0o600)
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + 45
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise SubscriptionError("ChatGPT 凭据正被另一进程更新，请稍后再试") from exc
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(repr=False)
class LoginAttempt:
    client_id: str
    host_id: str
    redirect_uri: str
    subject: str | None = None
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    created_at: float = field(default_factory=time.monotonic)
    id_token_hint: str = ""
    login_hint: str = ""
    request_consent: bool = False
    consumed: bool = False

    def authorization_url(self) -> str:
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self.verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        params = {
            "client_id": self.client_id, "ext_agent_host_id": self.host_id,
            "response_type": "code", "redirect_uri": self.redirect_uri,
            "scope": SCOPES, "resource": API_BASE, "state": self.state,
            "nonce": self.nonce, "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if self.client_id == "dynamic_agent_client":
            params["agent_name_hint"] = "QQChatRobot"
        if self.id_token_hint:
            params["id_token_hint"] = self.id_token_hint
        if self.login_hint:
            params["login_hint"] = self.login_hint
        if self.request_consent:
            params["prompt"] = "consent"
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def consume_callback(self, params: dict[str, str]) -> tuple[str, str]:
        if self.consumed or time.monotonic() - self.created_at > 600:
            raise SubscriptionError("ChatGPT 登录已过期或已使用，请重新登录")
        if not secrets.compare_digest(params.get("state", ""), self.state):
            raise SubscriptionError("ChatGPT 登录 state 校验失败")
        self.consumed = True
        if params.get("error"):
            raise SubscriptionError(f"ChatGPT 授权未完成：{_safe_identifier(params['error'])}")
        issued = params.get("client_id", "")
        if self.client_id == "dynamic_agent_client":
            if not issued or issued == "dynamic_agent_client":
                raise SubscriptionError("ChatGPT 未返回注册后的 client_id")
        elif issued and issued != self.client_id:
            raise SubscriptionError("ChatGPT 登录返回了不同的 client_id")
        client_id = issued or self.client_id
        if not params.get("code"):
            raise SubscriptionError("ChatGPT 登录未返回授权码")
        return client_id, params["code"]


class ChatGPTSubscription:
    def __init__(
        self, directory: Path = DEFAULT_STORE, *, client: httpx.AsyncClient | None = None,
        timeout: float = 120,
    ):
        self.store = CredentialStore(directory)
        self.client = client or outbound_http_client(timeout=timeout)
        self.timeout = timeout

    async def prepare_login(
        self, redirect_uri: str, *, new_account: bool = False, profile_id: str = "",
    ) -> LoginAttempt:
        if not re.fullmatch(r"http://127\.0\.0\.1:\d+/auth/callback", redirect_uri):
            raise SubscriptionError("ChatGPT 登录必须使用 127.0.0.1 回调")
        async with self.store.locked():
            state = self.store.read()
            state.setdefault("host_id", f"urn:uuid:{uuid.uuid4()}")
            selected = profile_id or state.get("active_client_id", "")
            if profile_id and profile_id not in state["profiles"]:
                raise SubscriptionError("未找到指定的 ChatGPT 账号")
            profile = {} if new_account else state["profiles"].get(selected, {})
            pending = {} if new_account else state.get("pending_registration", {})
            self.store.write(state)
        return LoginAttempt(
            client_id=profile.get("client_id") or pending.get("client_id") or "dynamic_agent_client",
            host_id=state["host_id"], redirect_uri=redirect_uri,
            subject=profile.get("subject"), id_token_hint=profile.get("id_token", ""),
            login_hint=profile.get("email", ""),
            request_consent=bool(profile) and PLAN_SCOPE not in profile.get("scopes", []),
        )

    async def _token_request(self, fields: dict) -> dict:
        response = await self.client.post(TOKEN_URL, data={**fields, "resource": API_BASE})
        if response.is_error:
            raise _http_error(response, "授权")
        try:
            tokens = response.json()
        except ValueError as exc:
            raise SubscriptionError("ChatGPT 授权响应格式错误") from exc
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise SubscriptionError("ChatGPT 未返回访问令牌")
        if str(tokens.get("token_type", "")).lower() != "bearer":
            raise SubscriptionError("ChatGPT 返回了不支持的令牌类型")
        return tokens

    async def _validate_identity(
        self, token: str, client_id: str, *, nonce: str | None, subject: str | None,
    ) -> dict:
        response = await self.client.get(f"{AUTH_ORIGIN}/.well-known/openid-configuration")
        if response.is_error:
            raise _http_error(response, "身份元数据")
        metadata = response.json()
        if metadata.get("issuer") != AUTH_ORIGIN:
            raise SubscriptionError("ChatGPT 身份签发者无效")
        jwks_url = metadata.get("jwks_uri", "")
        if not jwks_url.startswith(f"{AUTH_ORIGIN}/"):
            raise SubscriptionError("ChatGPT 身份密钥地址无效")
        response = await self.client.get(jwks_url)
        if response.is_error:
            raise _http_error(response, "身份密钥")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise SubscriptionError("ChatGPT 身份令牌签名算法无效")
            key_data = next(
                key for key in response.json()["keys"] if key.get("kid") == header.get("kid")
            )
            key = jwt.PyJWK.from_dict(key_data, algorithm="RS256").key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=client_id, issuer=AUTH_ORIGIN,
                options={"require": ["iss", "aud", "exp", "sub", "iat"]},
            )
        except (jwt.PyJWTError, ValueError, KeyError, StopIteration) as exc:
            raise SubscriptionError("ChatGPT 身份令牌校验失败") from exc
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SubscriptionError("ChatGPT 身份令牌 nonce 校验失败")
        if subject is not None and claims["sub"] != subject:
            raise SubscriptionError("ChatGPT 登录账号与所选账号不一致")
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise SubscriptionError("ChatGPT 身份信息无效")
        return claims

    @staticmethod
    def _merge_tokens(profile: dict, tokens: dict) -> dict:
        result = {**profile, **{k: tokens[k] for k in (
            "access_token", "refresh_token", "id_token", "token_type",
        ) if k in tokens}}
        if "scope" in tokens:
            if not isinstance(tokens["scope"], str):
                raise SubscriptionError("ChatGPT 授权权限格式错误")
            result["scopes"] = tokens["scope"].split()
        try:
            expiry = float(tokens.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise SubscriptionError("ChatGPT 授权有效期无效") from exc
        if not 0 < expiry <= 366 * 86400:
            raise SubscriptionError("ChatGPT 授权有效期无效")
        result["expires_at"] = time.time() + expiry
        return result

    async def finish_login(self, attempt: LoginAttempt, params: dict[str, str]) -> dict:
        client_id, code = attempt.consume_callback(params)
        async with self.store.locked():
            state = self.store.read()
            # Keep the issued registration even if this single-use code expires.
            state["pending_registration"] = {"client_id": client_id}
            self.store.write(state)
            tokens = await self._token_request({
                "grant_type": "authorization_code", "client_id": client_id, "code": code,
                "code_verifier": attempt.verifier, "redirect_uri": attempt.redirect_uri,
            })
            claims = await self._validate_identity(
                tokens.get("id_token", ""), client_id, nonce=attempt.nonce, subject=attempt.subject,
            )
            profile = self._merge_tokens({
                "client_id": client_id, "subject": claims["sub"],
                "email": claims.get("email", ""), "scopes": [],
            }, tokens)
            state["profiles"][client_id] = profile
            state["active_client_id"] = client_id
            state.pop("pending_registration", None)
            self.store.write(state)
        return self.status()

    def status(self) -> dict:
        state = self.store.read()
        selected = state.get("active_client_id", "")
        profile = state["profiles"].get(selected, {})
        return {
            "connected": bool(profile.get("access_token")),
            "plan_usage_enabled": PLAN_SCOPE in profile.get("scopes", []),
            "email": profile.get("email", ""),
            "profiles": [{"id": key, "email": value.get("email", ""), "active": key == selected}
                         for key, value in state["profiles"].items()],
        }

    async def _access_token(self, *, rejected_token: str = "") -> str:
        async with self.store.locked():
            state = self.store.read()
            selected = state.get("active_client_id", "")
            profile = state["profiles"].get(selected, {})
            if PLAN_SCOPE not in profile.get("scopes", []):
                raise SubscriptionError("请先在本机登录并允许 QQChatRobot 使用 ChatGPT 订阅用量")
            current = profile.get("access_token", "")
            rejected = bool(rejected_token) and rejected_token == current
            if current and not rejected and profile.get("expires_at", 0) > time.time() + 60:
                return current
            if not profile.get("refresh_token"):
                raise SubscriptionError("ChatGPT 授权已过期，请在本机重新登录")
            tokens = await self._token_request({
                "grant_type": "refresh_token", "client_id": selected,
                "refresh_token": profile["refresh_token"],
            })
            # Persist rotating credentials before any additional network request.
            updated = self._merge_tokens(profile, tokens)
            state["profiles"][selected] = updated
            self.store.write(state)
            if PLAN_SCOPE not in updated.get("scopes", []):
                raise SubscriptionError("ChatGPT 订阅用量权限已撤销，请重新授权")
            return updated["access_token"]

    async def list_models(self) -> list[dict]:
        token = await self._access_token()
        response = await self.client.get(
            f"{API_BASE}/models", headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            token = await self._access_token(rejected_token=token)
            response = await self.client.get(
                f"{API_BASE}/models", headers={"Authorization": f"Bearer {token}"},
            )
        if response.is_error:
            raise _http_error(response, "模型列表")
        models = response.json().get("models")
        if not isinstance(models, list):
            raise SubscriptionError("ChatGPT 模型列表格式错误")
        return [item for item in models if isinstance(item, dict)
                and item.get("visibility") == "list" and item.get("slug")]

    async def respond(self, payload: dict) -> dict:
        if payload.get("store") is not False or payload.get("stream") is not True:
            raise SubscriptionError("ChatGPT 订阅请求必须关闭存储并使用流式响应")
        token = await self._access_token()
        async with asyncio.timeout(self.timeout):
            for attempt in range(2):
                async with self.client.stream(
                    "POST", f"{API_BASE}/responses", json=payload,
                    headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                ) as response:
                    if response.status_code == 401 and attempt == 0:
                        await response.aread()
                        token = await self._access_token(rejected_token=token)
                        continue
                    if response.is_error:
                        await response.aread()
                        raise _http_error(response, "推理")
                    data_lines: list[str] = []
                    output_items: dict[int, dict] = {}
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip(" "))
                        elif not line and data_lines:
                            raw = "\n".join(data_lines)
                            data_lines.clear()
                            result = self._stream_event(raw, output_items)
                            if result is not None:
                                return result
                    if data_lines:
                        result = self._stream_event("\n".join(data_lines), output_items)
                        if result is not None:
                            return result
                    raise SubscriptionError("ChatGPT 响应流中断，未收到 response.completed")
        raise SubscriptionError("ChatGPT 推理未完成")

    @staticmethod
    def _stream_event(raw: str, output_items: dict[int, dict] | None = None) -> dict | None:
        if raw == "[DONE]":
            return None
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise SubscriptionError("ChatGPT 响应流格式错误") from exc
        if not isinstance(event, dict):
            raise SubscriptionError("ChatGPT 响应事件格式错误")
        kind = event.get("type")
        response = event.get("response") or {}
        if kind == "response.output_item.done" and output_items is not None:
            index, item = event.get("output_index"), event.get("item")
            if not isinstance(index, int) or index < 0 or not isinstance(item, dict):
                raise SubscriptionError("ChatGPT 完成的输出项格式错误")
            output_items[index] = item
        if kind in {"response.failed", "response.incomplete", "error"}:
            error = response.get("error") or event.get("error") or event
            code = _safe_identifier(error.get("code")) if isinstance(error, dict) else ""
            if code.startswith("subscription_sharing_usage_"):
                raise SubscriptionError("ChatGPT 订阅用量暂不可用或已达到上限，请稍后再试")
            raise SubscriptionError(f"ChatGPT 推理未完成：{_safe_identifier(kind)} {code}")
        if kind == "response.completed":
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise SubscriptionError("ChatGPT 推理未正常完成")
            # The subscription stream can deliver all output in output_item.done
            # while its final response has output=[]. Reconstruct only completed
            # items, and only after successful response.completed confirmation.
            if not response.get("output") and output_items:
                response = {**response, "output": [output_items[i] for i in sorted(output_items)]}
            return response
        return None

    async def logout(self) -> None:
        async with self.store.locked():
            state = self.store.read()
            profile = state["profiles"].get(state.get("active_client_id"), {})
            refresh = profile.get("refresh_token")
            if refresh:
                response = await self.client.get(
                    f"{AUTH_ORIGIN}/.well-known/openid-configuration",
                )
                if response.is_error:
                    raise _http_error(response, "退出")
                endpoint = response.json().get("revocation_endpoint", "")
                if not endpoint.startswith(f"{AUTH_ORIGIN}/"):
                    raise SubscriptionError("ChatGPT 撤销地址无效")
                response = await self.client.post(endpoint, data={
                    "token": refresh, "token_type_hint": "refresh_token",
                    "client_id": profile["client_id"],
                })
                if response.is_error:
                    raise _http_error(response, "退出")
            for key in ("access_token", "refresh_token", "id_token", "expires_at"):
                profile.pop(key, None)
            profile["scopes"] = []
            self.store.write(state)

    async def aclose(self) -> None:
        await self.client.aclose()
