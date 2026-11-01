"""本地端到端检查 QQ 题材插画；不连接 QQ、不发送任何消息，不输出凭据。"""
from __future__ import annotations

import asyncio
import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data/astrbot/instance/data/plugins"))


async def main():
    from astrbot.api import logger
    from astrbot_plugin_get_px.pixiv.client import PixivClient
    from astrbot_plugin_get_px.pixiv.downloader import ImageDownloader
    from astrbot_plugin_get_px.pixiv.filters import FiltersMixin
    from astrbot_plugin_get_px.pixiv.lolicon import LoliconClient
    from astrbot_plugin_get_px.pixiv.search import SearchMixin
    from openai import AsyncOpenAI
    import httpx
    from app.services.anime_image_policy import safe_image_metadata, verify_image, fetch_theme_candidates, matches_theme_metadata
    from app.services import anime_image_policy

    visual_trace = []
    original_parse = anime_image_policy.parse_visual_check
    def diagnostic_parse(text, **kwargs):
        try:
            parsed = anime_image_policy._visual_decision(text)
            if isinstance(parsed, dict):
                visual_trace.append({k: v for k, v in parsed.items() if k in anime_image_policy._CHECK_KEYS and type(v) is bool})
        except (ValueError, TypeError, AttributeError):
            visual_trace.append({"invalid_json": True})
        return original_parse(text, **kwargs)
    anime_image_policy.parse_visual_check = diagnostic_parse

    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("tags", nargs="*", default=["水着"])
    parser.add_argument("--model", choices=list(anime_image_policy._FREE_VISION_MODELS))
    parser.add_argument("--generic", action="store_true")
    parser.add_argument("--keep-candidates", action="store_true")
    parser.add_argument("--character", default="")
    args = parser.parse_args()
    c = json.loads((ROOT / "data/astrbot/instance/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    pc = json.loads((ROOT / "data/astrbot/instance/data/config/astrbot_plugin_get_px_config.json").read_text(encoding="utf-8-sig"))
    vc = next(p for p in c["provider"] if p["id"] == c["provider_settings"]["default_image_caption_provider_id"])
    source = next(p for p in c["provider_sources"] if p["id"] == vc["provider_source_id"])
    keys = source["key"]
    key = keys[0] if isinstance(keys, list) else keys
    http = httpx.AsyncClient(proxy=source.get("proxy") or None, trust_env=False, timeout=20)
    client = AsyncOpenAI(api_key=key, base_url=source["api_base"], http_client=http, max_retries=0)
    model = args.model or vc["model"]
    provider = SimpleNamespace(client=client, get_model=lambda: model)

    class Probe(SearchMixin, FiltersMixin):
        image_index = None
    plugin = Probe()
    plugin.client = PixivClient(pc["pixiv_refresh_token"], request_timeout=20)
    plugin.lolicon_client = LoliconClient(api_url=pc.get("lolicon_api_url") or "https://api.lolicon.app/setu/v2", request_timeout=20)
    downloader = ImageDownloader(pc.get("lolicon_image_proxy_origins", ""))
    event = SimpleNamespace(get_group_id=lambda: "local-diagnostic", get_sender_id=lambda: "local")
    report = {"model": model, "sent_to_qq": False, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "themes": []}
    output = ROOT / "artifacts/qq-anime-image-check"
    output.mkdir(parents=True, exist_ok=True)
    try:
        for tag in (["general"] if args.generic else (args.tags or ["水着"])):
            item = {"tag": tag, "stage": "fetch", "results": []}
            report["themes"].append(item)
            try:
                themes = () if args.generic else tuple({"水着": "泳装", "ミニスカート": "短裙", "ストッキング": "丝袜", "メイド": "女仆"}[x] for x in tag.split())
                if args.character:
                    candidates = await plugin.lolicon_client.search(args.character, count=10)
                    raw_count, source_key = len(candidates), "lolicon:character-diagnostic"
                else:
                    candidates, raw_count, source_key = await fetch_theme_candidates(plugin, event, themes)
                item.update(raw_count=raw_count, source=source_key.split(":", 1)[0], mature_query=":adult-fashion:" in source_key)
                candidates = [x for x in candidates if safe_image_metadata(x)]
                candidates.sort(key=lambda x:matches_theme_metadata(x,themes),reverse=True)
                item["metadata_count"] = len(candidates)
                candidates = await plugin._filter_blacklisted_illusts(candidates)
                item["safe_tags_count"] = len(candidates)
                for index, artwork in enumerate(candidates[:3]):
                    path = ""
                    checked = {"id": str(artwork.get("id")), "stage": "download"}
                    item["results"].append(checked)
                    try:
                        path, quality, size = await downloader.download_for_send(artwork, "large", timeout=15, downgrade_limit_bytes=3 * 1024 * 1024, log_context="local diagnostic")
                        checked.update(stage="vision", bytes=size, quality=quality)
                        if args.keep_candidates:
                            target = output / ("candidate-" + str(artwork.get("id", index)).replace(":", "-") + Path(path).suffix)
                            shutil.copyfile(path, target)
                            checked["local_candidate"] = str(target)
                        visual_trace.clear()
                        passed = await verify_image(provider, path, themes)
                        checked.update(stage="done", passed=passed)
                        checked["checks"] = list(visual_trace)
                        if passed:
                            dest = output / f"approved-{index}.png"
                            shutil.copyfile(path, dest)
                            checked["local_preview"] = str(dest)
                            break
                    except Exception as exc:
                        checked["error_type"] = type(exc).__name__
                        if isinstance(getattr(exc, "status_code", None), int):
                            checked["http_status"] = exc.status_code
                        body = getattr(exc, "body", {})
                        if isinstance(body, dict):
                            error = body.get("error", body)
                            if isinstance(error, dict):
                                checked["upstream_code"] = str(error.get("code", ""))[:16]
                                message = str(error.get("message", ""))
                                checked["category"] = next((word for word in ("余额", "额度", "并发", "繁忙", "限流", "实名") if word in message), "other")
                        if checked.get("http_status") == 429:
                            break
                    finally:
                        if path:
                            Path(path).unlink(missing_ok=True)
                item["stage"] = "done"
            except Exception as exc:
                item["error_type"] = type(exc).__name__
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (output / f"report-{model}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(item, ensure_ascii=False), flush=True)
    finally:
        await plugin.client.close()
        await plugin.lolicon_client.close()
        await client.close()
    print("No QQ messages sent", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
