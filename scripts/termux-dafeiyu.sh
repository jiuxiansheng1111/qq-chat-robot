#!/data/data/com.termux/files/usr/bin/bash
# Run inside Termux. The Ubuntu container owns its Python environment and AstrBot data.
set -eu
mode=${1:-status}
container=dafeiyu
boot_file="$HOME/.termux/boot/60-dafeiyu"
case "$mode" in
  prepare)
    pkg install -y proot-distro util-linux curl
    if ! proot-distro login "$container" -- /bin/true >/dev/null 2>&1; then
      proot-distro install ubuntu:24.04 --name "$container"
    fi
    proot-distro login "$container" -- /bin/bash -s <<'INSTALL'
set -eu
umask 077
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3.12-venv git ca-certificates tzdata
python3.12 -m venv /root/dafeiyu-venv
/root/dafeiyu-venv/bin/pip install 'astrbot==4.28.2' 'uvicorn>=0.34,<1'
project=/root/qq-chatrobot
if [ ! -e "$project" ]; then
  git clone --depth 1 https://github.com/jiuxiansheng1111/qq-chat-robot.git "$project"
fi
/root/dafeiyu-venv/bin/pip install -r "$project/requirements.txt"
instance="$project/data/astrbot/instance"
mkdir -p "$instance/data/plugins"
(cd "$instance" && ASTRBOT_ROOT="$instance" /root/dafeiyu-venv/bin/astrbot init --yes)
if [ ! -e "$instance/data/plugins/astrbot_plugin_qqchat" ]; then
  ln -s "$project" "$instance/data/plugins/astrbot_plugin_qqchat"
fi
if [ ! -e "$instance/data/cmd_config.json" ]; then
  printf '%s\n' '{"dashboard":{"host":"127.0.0.1","port":6185}}' > "$instance/data/cmd_config.json"
fi
plugin_config="$instance/data/config/astrbot_plugin_qqchat_config.json"
if [ ! -e "$plugin_config" ]; then
  printf '%s\n' '{"legacy_chat":false}' > "$plugin_config"
fi
if [ ! -e "$project/.env" ]; then
  /root/dafeiyu-venv/bin/python - "$project/.env" <<'PY'
import secrets, sys
from pathlib import Path
Path(sys.argv[1]).write_text("APP_ENV=production\nJWT_SECRET_KEY=" + secrets.token_urlsafe(48)
                            + "\nADMIN_PASSWORD=" + secrets.token_urlsafe(30) + "\n", encoding="utf-8")
PY
fi
INSTALL
    ;;
  browser)
    proot-distro login "$container" -- /bin/bash -s <<'BROWSER'
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y fonts-noto-cjk
/root/dafeiyu-venv/bin/python -m playwright install --with-deps chromium
/root/dafeiyu-venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as driver:
    browser = driver.chromium.launch()
    page = browser.new_page()
    page.set_content("<title>大肥鱼</title><p>网页助手</p>")
    if page.title() != "大肥鱼" or page.locator("p").inner_text() != "网页助手":
        raise SystemExit("浏览器页面读取失败")
    browser.close()
print("Chromium 已启动，网页文字读取成功。")
PY
BROWSER
    ;;
  owner)
    owner_qq=${2:-}
    proot-distro login "$container" -- /root/dafeiyu-venv/bin/python - "$owner_qq" <<'PY'
import json, os, re, sys
from pathlib import Path
if not re.fullmatch(r"[0-9]{5,20}", sys.argv[1]):
    raise SystemExit("请在 owner 后填自己的 QQ 号。")
os.umask(0o077)
target = Path("/root/qq-chatrobot/data/dafeiyu/owner-memory-config.json")
target.parent.mkdir(parents=True, exist_ok=True)
policy = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"archive_owner_private_and_group": False}
policy.update(enabled=True, owner_id=sys.argv[1])
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
temporary.replace(target)
print("已登记可通过 QQ 私聊操作本机的主人。")
PY
    ;;
  run)
    command -v termux-wake-lock >/dev/null && termux-wake-lock
    exec proot-distro login "$container" -- /bin/bash -c 'cd /root/qq-chatrobot/data/astrbot/instance && ASTRBOT_ROOT="$PWD" exec /root/dafeiyu-venv/bin/astrbot run'
    ;;
  enable-boot)
    mkdir -p "$HOME/.termux/boot" "$HOME/.local/bin"
    cp "$0" "$HOME/.local/bin/dafeiyu-termux"
    chmod 700 "$HOME/.local/bin/dafeiyu-termux"
    cat > "$boot_file" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
mkdir -p "$HOME/.local/state/dafeiyu"
exec 9>"$HOME/.local/state/dafeiyu/run.lock"
flock -n 9 || exit 0
while true; do
  "$HOME/.local/bin/dafeiyu-termux" run >"$HOME/.local/state/dafeiyu/last-run.log" 2>&1
  sleep 20
done
BOOT
    chmod 700 "$boot_file"
    ;;
  status)
    curl --silent --show-error --fail --max-time 4 --output /dev/null http://127.0.0.1:6185/
    printf '%s\n' '手机本机的 AstrBot 网页已有响应。'
    curl --silent --show-error --fail --max-time 4 --output /dev/null http://127.0.0.1:6190/accounts
    printf '%s\n' '大肥鱼的账号页和网页助手入口已有响应。'
    ;;
  *) printf '%s\n' '用法：bash termux-dafeiyu.sh prepare|browser|owner QQ号|run|enable-boot|status' >&2; exit 2 ;;
esac
