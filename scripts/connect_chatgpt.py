"""Sign in locally, verify GPT-6 Luna, then optionally switch AstrBot chat."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
import webbrowser
from contextlib import asynccontextmanager, suppress
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.chatgpt_subscription import (
    DEFAULT_MODEL,
    DEFAULT_STORE,
    ChatGPTSubscription,
    SubscriptionError,
)
from app.chatgpt_subscription_config import (
    _atomic_json,
    activate_subscription,
    verify_subscription,
)


def _page(title: str, message: str, *, sign_in: bool = False) -> str:
    link = '<p><a class="button" href="/login">Continue with ChatGPT</a></p>' if sign_in else ""
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · QQChatRobot</title>
<style>body{{font:18px/1.8 system-ui,sans-serif;color:#202622;background:#f4f7f5;
max-width:660px;margin:10vh auto;padding:32px}}h1{{font-size:30px}}.button{{display:inline-block;
background:#202622;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none}}
small{{color:#58645d}}a{{color:#176546}}</style>
<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{link}
<p><small>模型：GPT-6 Luna。使用已有的 ChatGPT Codex / Work 用量，受账号额度限制。
图片生成、语音、翻唱和其他服务沿用原配置。</small></p>
<p><a href="https://chatgpt.com/#settings/Usage">管理 ChatGPT 用量</a></p></html>'''


def login_app(subscription: ChatGPTSubscription, args) -> FastAPI:
    state = {"status": "waiting", "model": args.model, "activated": False}
    try:
        previous = json.loads((args.store / "last-login.json").read_text(encoding="utf-8"))
        if previous.get("status") == "ready" and previous.get("model") == args.model:
            state.update(previous)
    except (OSError, ValueError, AttributeError):
        pass
    pending = None
    callback_lock = asyncio.Lock()

    def save_status():
        args.store.mkdir(parents=True, exist_ok=True)
        _atomic_json(args.store / "last-login.json", state)

    def saved_status():
        try:
            result = json.loads((args.store / "last-login.json").read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else state
        except (OSError, ValueError):
            return state

    @asynccontextmanager
    async def lifespan(app):
        save_status()
        if args.open_browser:
            # The lifespan hook completes just before Uvicorn starts accepting.
            asyncio.get_running_loop().call_later(
                1, webbrowser.open, f"http://127.0.0.1:{args.port}",
            )
        yield
        await subscription.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # A loopback listener plus Host checking prevents DNS-rebinding access.
        if request.url.hostname != "127.0.0.1":
            return HTMLResponse("Local access only", status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        status = subscription.status()
        result = saved_status()
        if result.get("status") == "ready" and result.get("activated") and status["plan_usage_enabled"]:
            return _page("连接成功", "Luna 已完成真实调用验证，QQ 聊天模型已切换。可以关闭此页面。")
        account = f"当前连接：{status['email']}。" if status["connected"] else ""
        return _page(
            "连接 QQ 机器人的 ChatGPT 订阅",
            account + "点击下方按钮，在 OpenAI 官方页面登录并允许使用订阅用量。"
            "授权后先检查 Luna 并测试一次回复，成功后才切换聊天模型。",
            sign_in=True,
        )

    @app.get("/login")
    async def login():
        nonlocal pending
        async with callback_lock:
            pending = await subscription.prepare_login(
                f"http://127.0.0.1:{args.port}/auth/callback",
                new_account=args.new_account, profile_id=args.profile,
            )
            state.update(status="authorizing", error="")
            save_status()
            return RedirectResponse(pending.authorization_url(), status_code=303)

    @app.get("/auth/callback", response_class=HTMLResponse)
    async def callback(request: Request):
        async with callback_lock:
            if pending is None:
                return HTMLResponse(_page("登录无效", "请从本机连接页重新登录。", sign_in=True), 400)
            try:
                # Reject duplicate fields instead of silently picking an OAuth code.
                pairs = request.query_params.multi_items()
                if len({key for key, _ in pairs}) != len(pairs):
                    raise SubscriptionError("ChatGPT 登录回调参数重复")
                await subscription.finish_login(pending, dict(pairs))
                state["status"] = "verifying"
                save_status()
                await verify_subscription(subscription, args.model)
                if args.activate:
                    state["status"] = "activating"
                    save_status()
                    backup = await asyncio.to_thread(
                        activate_subscription, PROJECT_ROOT, args.store, args.model,
                    )
                    state.update(activated=True, backup=str(backup))
                state.update(status="ready", error="")
                save_status()
                message = (
                    "已完成真实调用验证，QQ 机器人的聊天模型已切换到 GPT-6 Luna。可以关闭此页面。"
                    if args.activate else "已完成真实调用验证；订阅连接已保存，尚未切换机器人模型。"
                )
                return _page("连接成功", message)
            except (SubscriptionError, httpx.HTTPError, TimeoutError, OSError) as exc:
                error = str(exc) if isinstance(exc, SubscriptionError) else type(exc).__name__
                state.update(status="failed", error=error)
                save_status()
                return HTMLResponse(_page("连接尚未完成", error, sign_in=True), 400)

    @app.get("/status")
    async def status():
        return JSONResponse({**saved_status(), **subscription.status()})

    return app


async def command(args) -> None:
    subscription = ChatGPTSubscription(args.store)
    try:
        if args.command == "status":
            print(json.dumps(subscription.status(), ensure_ascii=False, indent=2))
        elif args.command == "models":
            print(json.dumps([
                {"slug": item["slug"], "name": item.get("display_name", item["slug"])}
                for item in await subscription.list_models()
            ], ensure_ascii=False, indent=2))
        elif args.command == "verify":
            print(json.dumps(await verify_subscription(subscription, args.model), ensure_ascii=False))
        elif args.command == "logout":
            await subscription.logout()
            print("ChatGPT 本机授权已退出。")
        elif args.command == "activate":
            await verify_subscription(subscription, args.model)
            backup = await asyncio.to_thread(
                activate_subscription, PROJECT_ROOT, args.store, args.model,
            )
            args.store.mkdir(parents=True, exist_ok=True)
            _atomic_json(args.store / "last-login.json", {
                "status": "ready", "model": args.model, "activated": True,
                "backup": str(backup), "error": "",
            })
            print(f"GPT-6 Luna 聊天已启用。原配置备份：{backup}")
        else:
            app = login_app(subscription, args)
            config = uvicorn.Config(
                app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning",
            )
            print(f"本机连接页：http://127.0.0.1:{args.port}", flush=True)
            print("请在页面点击 Continue with ChatGPT；账号和密码仅在 OpenAI 官方页面输入。", flush=True)
            server = uvicorn.Server(config)

            async def close_after_success():
                while not server.should_exit:
                    await asyncio.sleep(2)
                    try:
                        saved = json.loads((args.store / "last-login.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if saved.get("status") == "ready":
                        await asyncio.sleep(30)
                        server.should_exit = True

            watcher = asyncio.create_task(close_after_success())
            try:
                await server.serve()
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
    finally:
        await subscription.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "status", "models", "verify", "activate", "logout"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--port", type=int, default=1455)
    parser.add_argument("--activate", action="store_true", help="验证成功后自动切换 AstrBot 聊天")
    parser.add_argument("--new-account", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--profile", default="", help="重新登录已保存账号的注册 ID")
    args = parser.parse_args()
    try:
        asyncio.run(command(args))
    except (SubscriptionError, httpx.HTTPError, TimeoutError, OSError) as exc:
        error = str(exc) if isinstance(exc, SubscriptionError) else type(exc).__name__
        print(f"连接未完成：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
