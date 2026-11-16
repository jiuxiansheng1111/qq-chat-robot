"""Build one reproducible, file-installable AstrBot ZIP per feature from project source."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin_packages"
REPO = "https://github.com/jiuxiansheng1111/qq-chat-robot"
ID_PREFIX = "astrbot_plugin_qqchat_"
GROUP_NAMES = ("基础与管理", "日常聊天与工具", "音乐、语音与视频", "游戏")
LOGIC = {"weather": "app/astrbot_weather.py", "cards": "app/astrbot_card_lookup.py", "search": "app/astrbot_guides.py"}

# Files imported on demand belong in the feature ZIP. Eager imports shared by
# several capabilities (including the webhook API) belong to the runtime library.
OWNED = {
    "weather": ("app/astrbot_weather_alerts.py",),
    "listen_first": ("app/astrbot_listen_first.py",),
    "corrections": ("app/astrbot_corrections.py",),
    "watchlist": ("app/astrbot_watchlist.py",),
    "home_control": ("app/astrbot_home_control.py",),
    "web_assistant": ("app/astrbot_web_assistant.py",),
    "desktop_control": ("app/astrbot_desktop_control.py",),
    "item_finder": ("app/astrbot_item_finder.py",),
    "project_resume": ("app/astrbot_project_resume.py",),
    "fridge": ("app/astrbot_fridge.py",),
    "dinner_split": ("app/astrbot_dinner_split.py",),
    "study": ("app/astrbot_study.py",),
    "time_capsule": ("app/astrbot_time_capsule.py",),
    "steam_library": ("app/astrbot_steam_library.py",),
    "group_radio": ("app/astrbot_group_radio.py",),
    "card_quiz": ("app/astrbot_card_quiz.py",),
    "meetups": ("app/astrbot_meetups.py",),
    "followups": ("app/astrbot_followups.py",),
    "accounts": ("app/services/account_hub.py", "app/services/local_listener.py", "app/services/mahjong/account_panel.py",
                 "integrations/accounts.html", "integrations/majsoul/account.html", "scripts/connect_chatgpt.py"),
    "group_reports": ("app/astrbot_group_reports.py", "integrations/group-daily-analysis/*"),
    "memes": ("app/astrbot_memes.py", "scripts/setup_qq_memes.py"),
    "video": ("app/astrbot_video_skill.py", "integrations/video-watch-plus/*", "scripts/setup_video_watch.py"),
    "catchup": ("app/astrbot_catchup.py",),
    "minecraft": ("app/minecraft_bridge.py", "app/minecraft_music*.py", "app/minecraft_voice*.py",
                  "integrations/minecraft-player/player.cjs", "integrations/minecraft-player/survival.cjs",
                  "scripts/minecraft_tts_worker.py", "docs/多人游戏陪伴.md"),
    "listening": ("integrations/netease/*", "scripts/*netease*.ps1"),
    "singing": ("scripts/plan_singing_pitch.py", "scripts/run_singing_*.py", "scripts/run_rvc_model.py",
                "scripts/singing_phrase_inference.py", "scripts/singing-requirements.txt", "scripts/setup_singing.ps1",
                "scripts/check_singing_*.py", "scripts/stabilize_singing_vocals.py", "scripts/transpose_singing_audio.py",
                "scripts/stop_gpt_sovits_for_singing.ps1"),
    "sound": ("integrations/soundtry/*", "scripts/setup_soundtry.py"),
    "voice": ("integrations/surfvoice/*", "integrations/dafeiyu/*", "scripts/setup_surfvoice.py"),
}
CORE_DATA = ("app/*.py", "app/*.json", "prompts/*", "assets/help-menu-background.png", "pyproject.toml",
             "docs/mahjong/knowledge/*", "docs/mahjong/LIQI_LICENSE.txt", "integrations/minecraft-player/account.cjs",
             "integrations/minecraft-player/package*.json")
HOST_METHODS = ("_get_companion_turns", "_get_group_router", "_get_qq_memes",
                "_get_reply_guard", "_get_reply_repair", "_legacy_event_allowed", "on_onebot_message", "on_wechat_message")
HEADER = '''from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star
_LOGGER = logging.getLogger(__name__)
'''
HELPERS = ("_get_result_or_none", "_is_stopped", "_result_has_reply")


def tracked_sources():
    # Ignored runtime products are never eligible for distribution.
    result = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
    return sorted({name for name in result.decode("utf-8").split("\0") if name
                   and not {".git", "node_modules", "__pycache__", ".venv"}.intersection(Path(name).parts)
                   and not (ROOT / name).is_symlink() and (ROOT / name).is_file()})


def source_bytes(path):
    payload = (ROOT / path).read_bytes()
    if Path(path).suffix.lower() in {".py", ".ps1", ".md", ".txt", ".json", ".js", ".cjs", ".mjs", ".ts",
                                     ".html", ".css", ".toml", ".yaml", ".patch", ".jsonl"} or "LICENSE" in path or Path(path).name == "NOTICE":
        return payload.replace(b"\r\n", b"\n")
    return payload


def section(lines, node, *, decorators=True):
    start = node.lineno
    if decorators and getattr(node, "decorator_list", []):
        start = min(d.lineno for d in node.decorator_list)
    return "".join(lines[start-1:node.end_lineno]).rstrip() + "\n"


def originals():
    src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    lines = src.splitlines(keepends=True)
    nodes = ast.parse(src).body
    plugin = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == "QQChatPlugin")
    helpers = "\n".join(section(lines, n) for n in nodes if getattr(n, "name", "") in HELPERS)
    return lines, {f.name: f for f in plugin.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}, helpers


def bound_method(lines, node):
    """Keep AstrBot's inspected signature and docstring on the actual feature module."""
    src = section(lines, node)
    first = node.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    start = min(d.lineno for d in node.decorator_list)
    offset = first.end_lineno - start + 1 if has_doc else first.lineno - start
    parts = src.splitlines(keepends=True)
    tool = node.name.startswith("qqchat_")
    fallback = """return '{"status":"unavailable","message":"请先启用大肥鱼共享运行时"}'""" if tool else "return"
    binding = "        host = self.host()\n        if host is None:\n            " + fallback + "\n"
    # Only this command delegates to another native method on the feature itself.
    if node.name != "anime_image_command":
        binding += "        self = host\n"
    parts.insert(offset, binding)
    return "".join(parts)


def plugin_source(slug, entry, original):
    lines, methods, helpers = original
    body = [HEADER, "from .feature import Feature\n", helpers, "\nclass Plugin(Feature, Star):\n",
            f"    slug = {slug!r}\n    label = {entry['title']!r}\n    summary = {entry['description']!r}\n",
            f"    turns = {tuple(entry.get('turns', ()))!r}\n    legacy = {tuple(entry.get('legacy', ()))!r}\n"]
    custom = SOURCE / "features" / (slug + ".py")
    if custom.exists():
        source = custom.read_text(encoding="utf-8")
        hooks = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Hooks")
        body.extend("\n" + section(source.splitlines(keepends=True), item) for item in hooks.body)
    body.extend("\n" + bound_method(lines, methods[name]) for name in entry.get("methods", ()))
    result = "".join(body)
    if slug in LOGIC:
        result = result.replace("from " + LOGIC[slug][:-3].replace("/", ".") + " import ", "from .logic import ")
    compile(result, slug + "/main.py", "exec")
    return result


def host_support(original):
    lines, methods, helpers = original
    result = HEADER + helpers + "\nclass HostSupport:\n" + "\n".join(
        section(lines, methods[name], decorators=False).replace("_find_project_root()", "self.root") for name in HOST_METHODS)
    compile(result, "support.py", "exec")
    return result


def owner(path):
    for slug, patterns in OWNED.items():
        if any(fnmatchcase(path, pat) for pat in patterns):
            return slug
    if path not in LOGIC.values() and any(fnmatchcase(path, pat) for pat in CORE_DATA):
        return "core"
    return None


def write_zip(path, entries):
    temporary = path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(path.stem + "/" + name, (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build(output):
    catalog = tomllib.loads((SOURCE / "catalog.toml").read_text(encoding="utf-8"))
    version = catalog.pop("version")
    original = originals()
    files = {slug: {} for slug in catalog}
    for path in tracked_sources():
        slug = owner(path)
        if slug is not None:
            files[slug]["runtime/" + path] = source_bytes(path)
    output.mkdir(parents=True, exist_ok=True)
    digests = []
    for slug, entry in catalog.items():
        name = ID_PREFIX + slug
        metadata = dict(name=name, display_name="大肥鱼 · " + entry["title"], desc=entry["description"],
                        version="v" + version, author="jiuxiansheng1111", astrbot_version=">=4.28.2",
                        support_platforms=["aiocqhttp", "weixin_oc", "wecom", "wecom_ai_bot"] if entry.get("legacy") or slug == "core" else ["aiocqhttp"])
        # JSON is valid YAML. File-installed features deliberately have no repo update
        # URL: updating any one feature to the monorepo root would install the bundle.
        data = files[slug]
        data["metadata.yaml"] = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        data["LICENSE"] = source_bytes("LICENSE")
        data["README.md"] = (f"# {metadata['display_name']}\n\n{entry['description']}\n\n"
                             + ("在 AstrBot 的插件页选择「从文件安装」，启用后再装需要的功能。\n" if slug == "core" else
                                "先安装同版本的大肥鱼共享运行时，再从 AstrBot 插件页安装此 ZIP。\n")
                             + f"\n[安装与配置]({REPO}/blob/main/plugin_packages/README.md)\n")
        if slug == "core":
            data.update({"main.py": source_bytes("plugin_packages/core.py"), "support.py": host_support(original),
                         "runtime_files.py": source_bytes("plugin_packages/runtime_files.py"),
                         "_conf_schema.json": source_bytes("_conf_schema.json"),
                         "requirements.txt": source_bytes("requirements.txt") + b"\nuvicorn>=0.34,<1.0\n"})
        else:
            data.update({"main.py": plugin_source(slug, entry, original), "feature.py": source_bytes("plugin_packages/feature_base.py")})
            if slug in LOGIC:
                data["logic.py"] = source_bytes(LOGIC[slug])
        path = output / (name + ".zip")
        write_zip(path, data)
        digests.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "SHA256SUMS").write_bytes(("\n".join(sorted(digests, key=lambda s: s.split()[1])) + "\n").encode())
    return catalog


def check(output):
    """Compare committed archives to a fresh build, allowing different deflate versions."""
    with tempfile.TemporaryDirectory(prefix="qqchat-rebuild-") as temp:
        rebuilt = Path(temp)
        catalog = build(rebuilt)
        names = {p.name for p in rebuilt.glob("*.zip")}
        if names != {p.name for p in output.glob("*.zip")}:
            raise ValueError("安装包数量或文件名与目录不符")
        manifest = []
        for name in sorted(names):
            path = output / name
            manifest.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
            with zipfile.ZipFile(path) as published, zipfile.ZipFile(rebuilt / name) as fresh:
                if published.namelist() != fresh.namelist() or any(
                    published.read(member) != fresh.read(member) for member in fresh.namelist()
                ):
                    raise ValueError(f"{name} 与当前源代码不一致")
        if (output / "SHA256SUMS").read_text(encoding="ascii").splitlines() != manifest:
            raise ValueError("SHA256SUMS 与安装包不一致")
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE / "downloads")
    parser.add_argument("--check", action="store_true", help="verify existing ZIPs against the current source")
    args = parser.parse_args()
    if args.check:
        print(f"Verified {len(check(args.output))} AstrBot packages against source.")
    else:
        print(f"Built {len(build(args.output))} AstrBot packages.")
