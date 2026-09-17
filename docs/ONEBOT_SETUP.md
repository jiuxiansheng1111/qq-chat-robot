# OneBot 接入指南

本项目使用 OneBot v11 的两条 HTTP 通道：

1. 机器人调用 OneBot HTTP Server，向 QQ 群发送消息。
2. OneBot HTTP Client 把 QQ 收到的事件上报给机器人 Webhook。

## 本机 NapCat 示例

假设 NapCat 和本项目都在同一台 Windows 电脑上运行。

### HTTP 与 HTTPS

NapCat 的 OneBot HTTP Server 默认是普通 HTTP 服务，没有直接配置 TLS 证书，因此本机地址应写成：

```text
http://127.0.0.1:3000
```

不要写成：

```text
https://127.0.0.1:3000
```

`https://` 会先进行 TLS 握手，而该端口只接受普通 HTTP，请求会在进入 OneBot API 前失败。同一台电脑上的 `127.0.0.1` 流量不会经过公网，开发环境无需额外使用 HTTPS。只有跨主机或公网部署时，才建议在 NapCat 前增加 Caddy、Nginx 等反向代理并配置有效证书。

NapCat WebUI 和 OneBot HTTP Server 是两个不同的服务：

```text
http://127.0.0.1:6099  # NapCat WebUI 管理界面
http://127.0.0.1:3000  # OneBot HTTP API
```

### 0. 生成两个 Token

这两个 Token 不是从 QQ、NapCat 或其他网站申请的，而是由你自己创建的随机密钥。它们也不是 NapCat WebUI 登录 Token。

在 PowerShell 中运行下面这段命令一次，会输出一个安全随机 Token：

```powershell
$tokenBytes = New-Object byte[] 32
$tokenRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$tokenRng.GetBytes($tokenBytes)
$tokenRng.Dispose()
[Convert]::ToBase64String($tokenBytes)
```

运行两次并分别保存：

- 第一个作为 `ONEBOT_ACCESS_TOKEN`，同时填写到 NapCat HTTP Server 的 Token。
- 第二个作为 `ONEBOT_WEBHOOK_TOKEN`，同时填写到 NapCat HTTP Client 的 Token。

两者技术上可以相同，但建议分开，便于以后单独更换。不要把 Token 发到聊天、截图或提交到 Git。

### 1. NapCat HTTP Server

启动 NapCat 并登录机器人 QQ 后，打开 WebUI，进入 `网络配置 → 新建 → HTTP 服务端`，新增或启用 HTTP Server：

```text
Host: 127.0.0.1
Port: 3000
Token: 自己生成的一串随机字符，例如 napcat-server-请替换
Message post format: array
```

这里的地址和 Token 对应 `.env`：

```env
ONEBOT_API_BASE=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=与HTTP Server Token完全相同
```

注意：`ONEBOT_API_BASE` 不是 NapCat WebUI 的地址。WebUI 常用于管理，HTTP Server 才是程序调用 `/send_group_msg` 等 OneBot API 的地址。

### 2. NapCat HTTP Client

在 WebUI 中进入 `网络配置 → 新建 → HTTP 客户端`：

```text
URL: http://127.0.0.1:8000/onebot/webhook
Token: 另一串随机字符，例如 napcat-webhook-请替换
Message post format: array
Report self message: false
```

这里的 Token 对应：

```env
ONEBOT_WEBHOOK_TOKEN=与HTTP Client Token完全相同
```

当前 NapCat HTTP Client 会使用 Token 对原始请求体生成 HMAC-SHA1，并发送
`X-Signature: sha1=<digest>`。项目会验证该签名，同时兼容下面两种请求头：

```text
Authorization: Bearer <ONEBOT_WEBHOOK_TOKEN>
X-OneBot-Token: <ONEBOT_WEBHOOK_TOKEN>
```

### 3. 机器人 QQ 号

```env
ONEBOT_SELF_ID=登录NapCat的QQ号
```

它用于识别群里的 `@机器人` 消息。建议使用单独的 QQ 号作为机器人账号。

### 4. 本机最终配置

```env
ONEBOT_API_BASE=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=你的HTTP_Server_Token
ONEBOT_WEBHOOK_TOKEN=你的HTTP_Client_Token
ONEBOT_SELF_ID=你的机器人QQ号
```

## Docker 场景

如果 NapCat 在 Windows 主机运行，而机器人运行在 Docker 中：

```env
ONEBOT_API_BASE=http://host.docker.internal:3000
```

NapCat HTTP Client 的上报地址仍可使用宿主机映射端口：

```text
http://127.0.0.1:8000/onebot/webhook
```

如果 NapCat 和机器人都在同一个 Docker Compose 网络中，应使用对应服务名，例如 `http://onebot:3000`，具体名称以 Compose 文件为准。

## 启动与检查

先启动机器人：

```powershell
cd "你的项目路径\qq-chatrobot"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

本地健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
```

然后启动 NapCat 并登录机器人 QQ，在群里发送：

```text
/help
@机器人 随机猫咪
@机器人 随机猪猪
@机器人 你好
```

## 常见问题

- 收不到群事件：检查 HTTP Client 是否启用、URL 是否为 `/onebot/webhook`、Token 是否一致。
- 能收到但无法回复：检查 HTTP Server 是否启用、端口是否正确、`ONEBOT_ACCESS_TOKEN` 是否一致。
- `@机器人` 不触发：检查 `ONEBOT_SELF_ID` 是否为当前登录的 QQ 号，并使用 `array` 消息格式。
- 返回 401：HTTP Client Token 与 `ONEBOT_WEBHOOK_TOKEN` 不一致。

### Windows 提示 provided QQ path is invalid

这表示 NapCat Shell 没有找到可用的新版 NTQQ。旧版 QQ（例如安装在 `C:\Program Files (x86)\Tencent\QQ\Bin\QQ.exe` 的 9.7 系列）不能作为 NapCat Shell 所需的 QQ 路径。

可选择以下一种方式：

1. 从腾讯官方渠道安装最新 Windows QQ，确认安装的是新版 NTQQ，然后重新运行 NapCat Shell 的 `launcher.bat`；Windows 10 使用 `launcher-win10.bat`。
2. 从 NapCat 官方 Releases 下载 `NapCat.Shell.Windows.OneKey.zip`，解压后运行 `NapCatInstaller.exe`，再进入生成的 Shell 目录运行 `napcat.bat`。OneKey 包已内置 QQ 和 NapCat，不依赖现有 QQ 注册表路径。

不要把旧版 `C:\Program Files (x86)\Tencent\QQ\Bin\QQ.exe` 强行填给启动器，它仍会被判定为无效。
