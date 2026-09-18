import asyncio
import sys

import httpx

from app.config import Settings
from app.llm.manager import LLMManager
from app.plugins.media import random_image, random_nailong_image, random_real_pig_image


async def check_llm(settings: Settings) -> tuple[str, str]:
    if not settings.llm_api_key:
        return "SKIP", "LLM_API_KEY 未填写"
    manager = LLMManager(settings)
    try:
        await manager.ask(
            [
                {"role": "system", "content": "这是连通性测试。"},
                {"role": "user", "content": "只回复 OK"},
            ]
        )
        stats = manager.snapshot()
        provider = next(
            (name for name, item in stats.items() if item["successes"] > 0),
            settings.llm_provider,
        )
        return "OK", f"provider={provider}"
    except Exception as exc:  # noqa: BLE001 - CLI must report provider failures safely
        return "FAIL", f"{type(exc).__name__}: {exc}"
    finally:
        await manager.aclose()


async def check_image(
    name: str,
    url: str,
    api_key: str,
    settings: Settings,
) -> tuple[str, str, str]:
    if not url:
        return name, "SKIP", "URL 未填写"
    try:
        image = await random_image(url, api_key, settings)
        result_type = "url" if image.startswith("https://") else "base64"
        return name, "OK", f"result={result_type}"
    except Exception as exc:  # noqa: BLE001 - CLI must report provider failures safely
        return name, "FAIL", f"{type(exc).__name__}: {exc}"


async def check_onebot(settings: Settings) -> tuple[str, str]:
    if not settings.onebot_api_base or not settings.onebot_self_id:
        return "SKIP", "ONEBOT_API_BASE 或 ONEBOT_SELF_ID 未填写"
    headers = {}
    if settings.onebot_access_token:
        headers["Authorization"] = f"Bearer {settings.onebot_access_token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{settings.onebot_api_base.rstrip('/')}/get_login_info",
                headers=headers,
                json={},
            )
        if response.status_code >= 400:
            return "FAIL", f"HTTP {response.status_code}"
        data = response.json().get("data") or {}
        actual_id = str(data.get("user_id", ""))
        if actual_id and actual_id != settings.onebot_self_id:
            return "FAIL", "适配器登录 QQ 与 ONEBOT_SELF_ID 不一致"
        return "OK", "get_login_info"
    except Exception as exc:  # noqa: BLE001 - CLI must report local adapter failures safely
        return "FAIL", f"{type(exc).__name__}: {exc}"


async def check_pig(settings: Settings) -> tuple[str, str, str]:
    try:
        image = await random_real_pig_image(settings.pig_api_url, settings)
        return "pig", "OK", f"result=real-photo, source={image.source_url}"
    except Exception as exc:  # noqa: BLE001 - CLI must report provider failures safely
        return "pig", "FAIL", f"{type(exc).__name__}: {exc}"


async def check_nailong(settings: Settings) -> tuple[str, str, str]:
    try:
        image = await random_nailong_image(settings)
        return "nailong", "OK", f"result={'base64' if image.startswith('base64://') else 'url'}"
    except Exception as exc:  # noqa: BLE001 - CLI must report provider failures safely
        return "nailong", "FAIL", f"{type(exc).__name__}: {exc}"


async def main() -> int:
    settings = Settings()
    requested = set(sys.argv[1:]) or {"llm", "cat", "pig", "nailong", "onebot"}
    valid = {"llm", "cat", "pig", "nailong", "onebot"}
    unknown = requested - valid
    if unknown:
        print(f"unknown={','.join(sorted(unknown))}")
        return 2

    checks = {
        "llm": lambda: check_llm(settings),
        "cat": lambda: check_image("cat", settings.cat_api_url, "", settings),
        "pig": lambda: check_pig(settings),
        "nailong": lambda: check_nailong(settings),
        "onebot": lambda: check_onebot(settings),
    }
    names = [
        name
        for name in ("llm", "cat", "pig", "nailong", "onebot")
        if name in requested
    ]
    raw_results = await asyncio.gather(*(checks[name]() for name in names))
    results = []
    for name, result in zip(names, raw_results, strict=True):
        results.append(result if len(result) == 3 else (name, *result))
    for name, status, detail in results:
        print(f"{name}={status} ({detail})")
    return 1 if any(status == "FAIL" for _, status, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
