"""Manual Pixiv OAuth login; validate and save the refresh token without printing it.

The user enters credentials in a normal Edge window. No password, CAPTCHA or
security prompt is automated. OAuth flow reference: github.com/piglig/pixiv-token.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data/astrbot/pixiv-login"
CONFIG = ROOT / "data/astrbot/instance/data/config/astrbot_plugin_get_px_config.json"
TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"


def extract_code(url: str) -> str | None:
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc, parsed.path) != ("pixiv", "account", "/login"):
        return None
    values = parse_qs(parsed.query).get("code", [])
    if len(values) != 1 or not values[0] or len(values[0]) > 512:
        return None
    return values[0]


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def status(phase: str, *, error: str = "") -> None:
    atomic_json(STATE_DIR / "status.json", {
        "phase": phase, "error": error, "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def save_plugin_token(token: str, config_path: Path = CONFIG) -> None:
    if not isinstance(token, str) or not token.strip():
        raise ValueError("empty_token")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("invalid_config")
    backup = ROOT / "data/astrbot/config-backups" / (
        "pixiv-login-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-" + secrets.token_hex(3)
    )
    backup.mkdir(parents=True)
    shutil.copyfile(config_path, backup / config_path.name)
    config["pixiv_refresh_token"] = token.strip()
    atomic_json(config_path, config)


async def reload_plugin() -> bool:
    import jwt

    main = ROOT / "data/astrbot/instance/data/cmd_config.json"
    dashboard = json.loads(main.read_text(encoding="utf-8-sig"))["dashboard"]
    token = jwt.encode({"username": dashboard.get("username", "astrbot"),
                        "exp": int(time.time()) + 120}, dashboard["jwt_secret"], algorithm="HS256")
    async with httpx.AsyncClient(trust_env=False, timeout=35) as client:
        response = await client.post(
            "http://127.0.0.1:6185/api/v1/plugins/astrbot_plugin_get_px/reload",
            headers={"Authorization": "Bearer " + token},
        )
        return response.is_success and response.json().get("status") == "ok"


async def validate_plugin_login(token: str) -> None:
    source = ROOT / "data/astrbot/instance/data/plugins/astrbot_plugin_get_px/pixiv/client.py"
    spec = importlib.util.spec_from_file_location("_pixiv_login_validation", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    client = module.PixivClient(refresh_token=token, request_timeout=25)
    try:
        await client.ensure_logged_in()
    finally:
        await client.close()


async def login(timeout: int) -> None:
    from pixivpy_async import AppPixivAPI
    from playwright.async_api import async_playwright

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    login_url = "https://app-api.pixiv.net/web/v1/login?" + urlencode({
        "code_challenge": challenge, "code_challenge_method": "S256", "client": "pixiv-android",
    })
    future = asyncio.get_running_loop().create_future()
    status("opening")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=False)
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1100, "height": 780})
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")

        def capture(event):
            url = (event.get("request") or {}).get("url") or event.get("documentURL") or ""
            code = extract_code(url)
            if code and not future.done():
                future.set_result(code)

        cdp.on("Network.requestWillBeSent", capture)
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
            status("waiting_for_user_login")
            code = await asyncio.wait_for(future, timeout=timeout)
            status("validating")
            # Close the credential page once the user has completed authorization.
            await context.close()
            api = AppPixivAPI()
            async with httpx.AsyncClient(trust_env=False, timeout=25) as http:
                response = await http.post(TOKEN_URL, data={
                    "client_id": api.client_id, "client_secret": api.client_secret,
                    "grant_type": "authorization_code", "code": code,
                    "code_verifier": verifier, "redirect_uri": REDIRECT_URI,
                    "include_policy": "true",
                }, headers={"User-Agent": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)"})
                response.raise_for_status()
                data = response.json()
            token = data.get("refresh_token")
            if not isinstance(token, str) or not token:
                raise ValueError("oauth_token_missing")
            await validate_plugin_login(token)
            save_plugin_token(token)
            try:
                loaded = await reload_plugin()
            except Exception:
                loaded = False
            status("connected" if loaded else "saved_needs_reload")
            complete = await browser.new_page()
            await complete.set_content(
                '<html lang="zh-CN"><meta charset="utf-8"><title>Pixiv 登录完成</title>'
                '<body style="font:22px sans-serif;padding:70px;background:#fafafa">'
                '<h1>Pixiv 已连接</h1><p>登录已验证，凭据已保存到本机 AstrBot 图片插件。</p>'
                '<p>QQ 中可以继续发送“来一份二次元图片”。此窗口可以关闭。</p></body></html>'
            )
            await asyncio.sleep(60)
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    if args.status:
        path = STATE_DIR / "status.json"
        print(path.read_text(encoding="utf-8") if path.is_file() else '{"phase":"not_started"}')
        return 0
    try:
        asyncio.run(login(max(60, min(args.timeout, 3600))))
    except KeyboardInterrupt:
        status("cancelled")
        return 1
    except Exception as error:
        # Exception messages may embed an OAuth response or URL; only record type.
        status("failed", error=type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
