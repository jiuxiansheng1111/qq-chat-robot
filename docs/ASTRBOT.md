# AstrBot 本地接入

AstrBot 负责 QQ 和微信消息接入、插件管理；消息处理、角色聊天、TTS、唱歌、网易云会员、媒体和游戏仍复用本仓库运行时。插件不会启动旧的 FastAPI/Uvicorn Webhook。

## 本机部署

Windows 脚本把 AstrBot 4.28.2 和 Python 3.12 放在忽略目录 `data/astrbot/`，不会替换系统 Python、旧机器人环境或 GPU 环境。AstrBot venv 使用仓库 `requirements.txt` 中兼容 NumPy 2、Pillow 12 的依赖；不会安装 PyTorch 或其他 GPU 包。

在仓库根目录运行：

```powershell
scripts\install_astrbot.ps1
```

本地开发时也可以给 AstrBot 的插件目录建立受保护的 Junction：

```powershell
scripts\install_astrbot.ps1 -LinkProject
```

配置私有实例；此命令不会启动服务：

```powershell
data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe scripts\configure_astrbot.py
```

配置器按 AstrBot 4.28.2 的 `data/cmd_config.json` 格式写入 `data/astrbot/instance/data/cmd_config.json`：管理面板只监听 `127.0.0.1:6185`；随机初始密码写入被忽略的 `data/astrbot/dashboard-login.txt`，配置文件只保存 AstrBot 生成的密码哈希；OneBot v11 反向 WebSocket 绑定 `127.0.0.1:6199`，并生成仅保存在私有配置中的 token。平台初始为禁用，NapCat 配置不会自动修改。

微信预设 `qq-chatrobot-wechat` 初始关闭，已保存的微信账号与登录态不会被配置器覆盖。扫码步骤见 [微信接入](ASTRBOT_WECHAT.md)。

安装可选画境拾珍插件后，配置器也会在 `data/astrbot/instance/data/config/astrbot_plugin_get_px_config.json` 关闭画境拾珍的签到和自然语言自动触发，空 Pixiv token 保持为空；如果本地已有 token，会保留且不显示它。这样“签到”不会被图片插件接管。

## 切换到 AstrBot

先在管理面板确认插件已加载，启用 OneBot 平台，再按 [QQ 切换说明](ASTRBOT_QQ_SWITCH.md) 检查和切换。NapCat 作为反向 WebSocket 客户端连接：

```text
ws://127.0.0.1:6199/ws
```

NapCat 与 AstrBot 使用同一个 `ws_reverse_token`。该 token 留在本机私有配置，不要复制到日志、仓库或公开消息。管理面板初始密码从 `data/astrbot/dashboard-login.txt` 本机查看，首次登录后按面板提示修改。

启动命令：

```powershell
scripts\run_astrbot.ps1
```

确认需要停止时运行：

```powershell
scripts\run_astrbot.ps1 -Stop
```

启动器核对启动器、虚拟环境 Python、实际监听 Python 的父子关系、路径和启动时间，把归属记录放在 `data/astrbot/astrbot-owned-run.json`。重复启动会复用已有实例，其他进程占用端口时会报错。启动时会调用 `scripts/start_netease_member.ps1 -Port 3010`，不打开登录页；已有 GPT-SoVITS 进程保持运行。它不会启动 NapCat、旧 Uvicorn 或旧生命周期看门狗。停止 AstrBot 时只处理已验证的 AstrBot 进程，不按端口误杀其他服务。

切换成功后，`data/astrbot/active-mode.txt` 记录为 `astrbot`，原来的 `start_all.ps1`、`run_bot.ps1` 和恢复检查也会启动 AstrBot。回退时先恢复 NapCat 备份、停用 AstrBot QQ 平台，再将此文件改为 `fastapi`，启动旧入口。

## 消息与模型

插件的 `legacy_chat` 默认开启：群消息继续走现有机器人和自然 @ 功能，不要求在消息前加 `/`；由旧运行时处理的消息会关闭 AstrBot 默认 LLM 流程，避免双重回复。原角色、每个人的记忆开关、音色选择和业务数据继续由本插件管理。

AstrBot 配好本地聊天模型后，本插件也会使用面板里的主模型和备用模型。修改模型、密钥或请求参数后，现有角色聊天会跟着使用新配置；原来的队列和并发限制保留。每条请求仍传原来的角色提示和对应用户的上下文，不会加入 AstrBot 普通聊天的群共享历史。启动时没有配置 AstrBot 模型，继续使用本项目 `.env`；已接通后不要清空默认模型，切换回旧配置需要重启。

## 迁移原模型配置

先停 AstrBot，再从仓库根目录预览或迁移：

```powershell
scripts\run_astrbot.ps1 -Stop
data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe scripts\migrate_astrbot_config.py
data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe scripts\migrate_astrbot_config.py --apply
scripts\run_astrbot.ps1
```

迁移脚本读取本机 `.env`，导入智谱、Groq 的接口地址、密钥、模型、温度、输出长度和请求超时，设置主模型及备用模型。旧上下文按两条消息约一轮换算到 AstrBot，沿用截断方式。原角色提示和语气示例导入私有人格库；原角色切换仍在本插件里生效。

第一次迁移前会在 `data/astrbot/config-backups/` 备份配置和 AstrBot 数据库。再次运行默认保留面板里改过的模型和人格；需要从 `.env` 重新同步本脚本创建的条目时，加 `--replace-owned`。这个选项会覆盖 `qqchat-*` 固定 ID 的对应条目，使用前先看预览。

语音和翻唱继续使用原来的 GPT-SoVITS 服务、训练模型与每用户音色设置，不启用另一套会争用权重的 TTS。网易云登录、图鉴和收藏继续保存在原私有目录。

启动配置器保留已经修改的面板密码；`dashboard-login.txt` 只记录首次生成的密码，改密后不再用它覆盖当前密码。

## 消息入口

NapCat 切换到 AstrBot 后，不要再把相同 OneBot 消息转发到旧 `/onebot/webhook`。同一事件走两条入口会导致重复处理。AstrBot 插件优先级为 `-100`；已经回复、停止或有更高优先级处理结果的事件会跳过。插件保留原始 OneBot 消息段，并通过 AstrBot 客户端执行发送动作。

## 本地文件

以下内容仅留在本机：`.env`、`data/`、AstrBot 登录密码、OneBot token、QQ/网易云会话、模型及媒体文件。不要把 `data/astrbot`、`.env` 或账号状态提交、上传或复制到插件发布包。配置器只访问仓库 `data/astrbot/` 下的 AstrBot 私有实例，并拒绝经过 Junction/符号链接的路径。

安装完成后的检查：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_configure_astrbot.py tests\test_astrbot_plugin.py -q
```

## 版本依据

配置字段按本机固定的 AstrBot 4.28.2 发布版实现核对：

- [AstrBot 4.28.2 默认配置](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/config/default.py)
- [AstrBot 4.28.2 OneBot v11 反向 WebSocket 适配器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)
- [AstrBot CLI 启动参数](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/cli/commands/cmd_run.py)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot OneBot/NapCat 文档](https://docs.astrbot.app/platform/aiocqhttp.html)
