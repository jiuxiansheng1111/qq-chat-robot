"""Use configured Luna on synthetic scenes; never post a message to a real QQ chat."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_memes import QQMemes, load_catalog

    data = ROOT / "data/astrbot/instance/data"
    config = json.loads((data / "config/abconf_qqchat_dafeiyu.json").read_text(encoding="utf-8-sig"))
    provider_id = config["agent_runner"]["config"]["model"]["provider_id"]
    selected = next(provider for provider in config["provider"] if provider["id"] == provider_id)
    if provider_id != "qqchat-chatgpt-luna":
        raise RuntimeError("This verification must use the approved QQ chat provider")
    provider = register_chatgpt_provider()(selected, config["provider_settings"])
    context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True, star_cls=SimpleNamespace()))
    catalog = load_catalog()
    results = []
    try:
        for scene, text in [
            ("celebration", "我今天面试通过了，终于能松口气了，给我配一张庆祝的大肥鱼表情吧"),
            ("comfort", "今天有点受挫，想被安慰一下，也给我配一张大肥鱼摸摸头的表情吧"),
        ]:
            extras = {}
            event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp", get_message_str=lambda text=text: text,
                                    get_extra=extras.get, set_extra=extras.__setitem__, unified_msg_origin="qq-meme-test:" + scene)
            request = SimpleNamespace(system_prompt=(ROOT / "prompts/qq/dafeiyu.txt").read_text(encoding="utf-8"))
            adapter = QQMemes()
            if not adapter.prepare(context, event, request):
                raise RuntimeError("The actual enabled whale pack is unavailable")
            response = await asyncio.wait_for(provider.text_chat(prompt=text, system_prompt=request.system_prompt), timeout=90)
            adapter.process_response(event, response)
            identifier = event.get_extra("qqchat_whale_meme_pick")
            image = catalog.get(identifier)
            result = {"scene": scene, "text": response.completion_text, "selected_id": identifier,
                      "selected_name": image.name if image else None, "actual_gif_exists": bool(image and image.path.is_file()),
                      "provider_id": provider_id, "posted_to_qq": False}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await provider.terminate()
        output = ROOT / "data/dafeiyu/meme-live-model-verification.json"
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
