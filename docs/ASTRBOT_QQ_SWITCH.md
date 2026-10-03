# NapCat 切换到 AstrBot

`scripts/connect_astrbot_qq.py` 为本机 NapCat OneBot 连接生成并可应用一份受校验的切换配置。脚本不会启动或停止 QQ、NapCat、AstrBot 或旧机器人，也不会发送聊天消息。

## 预检与应用

从仓库根目录运行。默认配置目录为 `C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config`；可用 `--napcat-config-dir` 指向实际目录。

```powershell
python scripts/connect_astrbot_qq.py --napcat-config-dir "C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config"
```

默认只预检和预览：检查 AstrBot 私有 `instance/data/cmd_config.json` 中已启用的 `qq-chatrobot-local` / `aiocqhttp` reverse-WS 平台及其 `127.0.0.1:6199` 地址、端口监听状态、NapCat WebUI 和登录身份。NapCat WebUI 若绑定 `0.0.0.0` 或 `::`，脚本仍只通过 `127.0.0.1` 或 `[::1]` 访问，不更改 NapCat 的绑定设置；未知公网地址或域名会拒绝。它通过 NapCat WebUI 登录后只读取当前 OneBot 配置；WebUI token 按 NapCat 约定转换为 SHA-256 登录摘要，`httpx` 设置 `trust_env=False`、超时 10 秒，不经过系统代理。QQ ID、WebUI token、OneBot token 和登录凭据都不会显示。

身份验证使用项目根目录 `.env` 的 `ONEBOT_API_BASE` / `ONEBOT_ACCESS_TOKEN` 与可选 `_2` 配置，对本机 OneBot 调用 `get_login_info`，并要求返回的登录身份能对应到 NapCat 私有目录中的 `onebot11_<self_id>.json`。有两个账号都匹配时，必须明确指定：

```powershell
python scripts/connect_astrbot_qq.py --account primary
python scripts/connect_astrbot_qq.py --account secondary
```

只有确认预览计划后才加 `--apply`：

```powershell
python scripts/connect_astrbot_qq.py --napcat-config-dir "C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config" --apply
```

应用会再次预检。AstrBot 的 6199 端口必须已监听；如果旧 webhook 端口 8000 仍监听，脚本拒绝修改。请由运维者先停止旧机器人服务；脚本不杀进程或停服务。

## 配置变更与恢复

- 只将 `httpClients` 中 URL 精确指向 `http://127.0.0.1:8000/onebot/webhook` 的项目旧入口设为 `enable=false`；保留其他 HTTP API 和所有其他网络配置。
- 在 `network.websocketClients` 新增或复用名称 `astrbot-qqchat` 的客户端，连接 `ws://127.0.0.1:6199/ws`，使用 `messagePostFormat=array`、`reportSelfMessage=false`、`heartInterval=30000`、`reconnectInterval=5000`，token 从 AstrBot 私有平台配置读取。若该名称指向别处，或目标 URL 被其他名称占用，脚本拒绝覆盖。
- 修改前把 WebUI 读到的旧 OneBot 配置备份到被 `.gitignore` 忽略的 `data/astrbot/napcat-backups/<时间戳>/onebot-config.json`。应用后通过 `GetConfig` 验证；验证失败会尝试用备份内容恢复，并再读回确认。
- 只有配置读回校验成功后，脚本才原子写入私有 `data/astrbot/active-mode.txt`，内容为 `astrbot`。如果后续步骤失败，会恢复原标记内容；原来没有标记时会清理本次创建的标记。预检不写此文件。
- 同目录的 `report.json` 只记录状态布尔值和计数，不含 token、QQ ID 或配置内容。`onebot-config.json` 本身可能含 NapCat 凭据，只保存在私有目录中，严禁提交或上传。
- 默认预检不写配置、备份、报告或启动模式标记；只有 `--apply` 会修改 NapCat 配置并写私有备份/报告/标记。仓库启动入口可读取该标记并按 AstrBot 模式启动。NapCat `SetConfig` 会热加载配置，脚本不触发 QQ 发信动作。

如果应用和自动恢复都无法确认，请停止切换并使用 `onebot-config.json` 在 NapCat WebUI 中人工恢复。不要将备份复制到公开目录。

## 实现依据

- [NapCat 官方 OneBot 配置 schema](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/onebot/config.ts)定义 `network.httpClients`、`network.websocketClients`，以及 `heartInterval` / `reconnectInterval` 等客户端字段。
- [OneBot 11 `get_login_info`](https://github.com/botuniverse/onebot-11/blob/master/api/public.md#get_login_info)说明登录身份查询接口。
- [OneBot 11 HTTP 通信规范](https://github.com/botuniverse/onebot-11/blob/master/communication/http.md)说明通过 HTTP 请求调用 OneBot action。

测试使用假的 WebUI/OneBot 响应及临时 JSON 配置，不连接真实端口、不发消息，也不改 NapCat 或 AstrBot 配置。
