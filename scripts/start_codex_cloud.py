"""为 Codex Linux 云端试运行准备独立实例，不改动本机 QQ、账号或模型。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="在当前云端 VM 启动独立 AstrBot")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    instance = project / "data" / "codex-cloud-instance"
    status = {
        "target": "Codex Cloud",
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "astrbot_installed": importlib.util.find_spec("astrbot") is not None,
        "environment_file_present": (project / ".env").exists(),
        "instance": str(instance),
        "always_on_verified": False,
        "qq_connected_verified": False,
    }
    if not args.start:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    if os.name != "posix":
        parser.error("此入口用于 Codex Linux 云端；本机请继续使用原一键启动脚本。")
    if not status["astrbot_installed"]:
        parser.error("请先安装项目及 AstrBot 4.28.2，见 deploy/codex-cloud/README.md。")
    if len(os.environ.get("ASTRBOT_DASHBOARD_INITIAL_PASSWORD", "")) < 12:
        parser.error("请先通过云端环境变量提供至少 12 字符的独立面板初始密码。")
    try:
        with socket.create_connection(("127.0.0.1", 6185), timeout=1):
            parser.error("6185 已有服务监听；保留现有服务，不重复启动。")
    except OSError:
        pass
    instance.mkdir(parents=True, exist_ok=True)
    (instance / ".astrbot").touch(exist_ok=True)
    plugins = instance / "data" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    sources = {"astrbot_plugin_qq_chatrobot": project}
    staged = project / "cloud_plugins"
    if staged.is_dir():
        sources.update({p.name: p for p in staged.iterdir() if p.is_dir()})
    for name, source in sources.items():
        destination = plugins / name
        if destination.is_symlink():
            if destination.resolve() != source.resolve():
                parser.error(f"已有插件链接指向另一位置，保留原状：{name}")
        elif destination.exists():
            parser.error(f"已有同名插件目录，保留原状：{name}")
        else:
            destination.symlink_to(source, target_is_directory=True)
    config_dir = instance / "data" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "astrbot_plugin_qq_chatrobot": {"legacy_chat": False},
        "astrbot_plugin_lifesim": {"provider_id": "qqchat-zhipu-chat"},
        "astrbot_plugin_private_companion": {
            "basic_config": {"bot_name": "小丛雨", "LLM_PROVIDER_ID": "qqchat-zhipu-chat"},
        },
        "astrbot_plugin_self_learning": {
            "Database_Settings": {"db_type": "sqlite"},
            "Runtime_Internal_Settings": {"enable_llm_hooks": True},
            "Model_Configuration": {key: "qqchat-zhipu-chat" for key in (
                "filter_provider_id", "refine_provider_id", "reinforce_provider_id",
            )},
        },
    }
    for name, config in defaults.items():
        plugin_config = config_dir / f"{name}_config.json"
        if not plugin_config.exists():
            plugin_config.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    env = os.environ.copy()
    env["ASTRBOT_ROOT"] = str(instance)
    # 先验证文字与插件服务。模型文件和音频运行时验收后再单独启用。
    env.setdefault("VOICE_ENABLED", "false")
    env.setdefault("SINGING_ENABLED", "false")
    env.setdefault("DATABASE_PATH", str(instance / "data" / "qqchat.db"))
    log_path = instance / "cloud-runtime.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "astrbot.cli", "run", "--port", "6185"],
            cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    (instance / "cloud-runtime.pid").write_text(str(process.pid), encoding="ascii")
    ready = False
    deadline = time.monotonic() + 45
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:6185/", timeout=2) as response:
                ready = response.status == 200
            if ready:
                break
        except OSError:
            pass
        time.sleep(1)
    status.update({"pid": process.pid, "panel_ready": ready, "log": str(log_path)})
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
