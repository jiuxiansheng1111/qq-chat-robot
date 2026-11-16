"""Configure the phone-side AstrBot OneBot receiver, retaining unrelated settings."""
import argparse
import ipaddress
import os
import secrets
from pathlib import Path

from app.services.chat_brain import MOBILE_ONEBOT_ID
from scripts.configure_astrbot import ONEBOT_PORT, _read_existing_config, _write_json_atomically


def receiver(path, address=None):
    config = _read_existing_config(path)
    if config is None: raise ValueError("请先执行 prepare，生成手机自己的 AstrBot 实例。")
    platforms = config.setdefault("platform",[])
    if not isinstance(platforms,list): raise ValueError("platform 配置格式有误。")
    existing = next((p for p in platforms if isinstance(p,dict) and p.get("id")==MOBILE_ONEBOT_ID),None)
    if existing and existing.get("type")!="aiocqhttp": raise ValueError("手机连接的名字已被其他平台使用。")
    if address is not None:
        ip = ipaddress.ip_address(address)
        private_v4 = ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","100.64.0.0/10")
        local = ip.is_loopback or (ip.version==4 and any(ip in ipaddress.ip_network(net) for net in private_v4))
        if not local and not (ip.version==6 and ip in ipaddress.ip_network("fc00::/7")):
            raise ValueError("请填写手机的局域网、私有 VPN 或回环地址。")
        if any(isinstance(p,dict) and p is not existing and p.get("enable") and p.get("ws_reverse_port")==ONEBOT_PORT
               for p in platforms): raise ValueError("6199 已分给另一个连接，请在 AstrBot 面板查看那个连接。")
        if existing is None:
            existing = {"id":MOBILE_ONEBOT_ID,"type":"aiocqhttp"}
            platforms.append(existing)
        existing.update(enable=True,ws_reverse_host=str(ip),ws_reverse_port=ONEBOT_PORT,
                        ws_reverse_token=existing.get("ws_reverse_token") or secrets.token_urlsafe(32))
        _write_json_atomically(path,config)
    if not existing: raise ValueError("请先执行 onebot 配置手机的接收地址。")
    host = existing["ws_reverse_host"]
    host = "["+host+"]" if ":" in host else host
    return f"ws://{host}:{existing['ws_reverse_port']}/ws", existing["ws_reverse_token"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path",type=Path)
    parser.add_argument("--address")
    parser.add_argument("--show-token",action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    url,token = receiver(args.path,args.address)
    print("OneBot 反向 WebSocket 地址",url)
    if args.show_token: print("配对令牌",token)
    else: print("令牌已保存在手机。执行 pairing 可在本机终端查看。")


if __name__=="__main__":
    main()
