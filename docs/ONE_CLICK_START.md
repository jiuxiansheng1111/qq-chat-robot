# Windows 一键启动

项目入口：

```text
qq-chatrobot\start-qq-chatrobot.bat
```

双击后，脚本会：

1. 从项目 `.env` 读取 `ONEBOT_SELF_ID`。
2. 检查 NapCat WebUI `6099`，避免重复启动多个 NapCat。
3. 请求管理员权限并使用机器人 QQ 快速启动 NapCat。
4. 等待 OneBot HTTP Server `3000`。
5. 启动 FastAPI 机器人服务 `8000`。
6. 输出最终状态。

## 防止 8000 服务意外消失

管理员身份双击项目根目录的 `安装机器人开机自启.bat`。它会注册 Windows 任务
`QQChatRobot API`：登录 Windows 后自动启动 FastAPI，并在进程意外退出后等待 5 秒重启。
机器人日志写入 `logs/bot.out.log` 和 `logs/bot.error.log`。

该任务只负责机器人 API `8000`。NapCat/QQ 仍由 `start-qq-chatrobot.bat` 启动，因为首次登录、
设备锁或风控时可能需要手机确认。看到 `ECONNREFUSED 127.0.0.1:8000` 时，先访问
`http://127.0.0.1:8000/health/live`；若不能打开，可再次双击安装脚本修复并立即启动任务。

首次运行或 QQ 风控触发时，仍可能需要在手机 QQ 上确认登录。此时打开：

```text
http://127.0.0.1:6099
```

脚本默认要求项目目录与 `NapCat.Shell` 位于同一个父目录：

```text
父目录/
├─ qq-chatrobot/
└─ NapCat.Shell/
```

脚本不会自动结束普通 QQ。重启后请先运行机器人入口，再打开需要使用的普通 QQ，避免同一个账号重复登录。

也可以从项目目录运行：

```powershell
.\start-qq-chatrobot.bat
```
