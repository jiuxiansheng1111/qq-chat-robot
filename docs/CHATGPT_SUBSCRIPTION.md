# 使用 ChatGPT Pro 订阅进行 QQ 聊天

本机通过 OpenAI 官方 **Sign in with ChatGPT** 授权，使用订阅内的
Codex / Work 用量调用 Responses API。首选模型是 `gpt-6-luna`，以账号实际模型列表为准。
模型或权限不可用时停止切换，不会自动换到其他计费路径。

只新增聊天提供商并切换 AstrBot 的默认聊天选择。图片生成、图片理解、语音、翻唱、
搜索与其他插件的原有 API 配置保留。本地工具调用仍交由 AstrBot 执行。

## 连接

双击项目根目录的 `连接ChatGPT订阅.bat`，在本机连接页点击
**Continue with ChatGPT**，在 OpenAI 官方页面登录并确认订阅用量权限。

或在项目目录运行：

```powershell
data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe scripts\connect_chatgpt.py login --activate
```

打开 `http://127.0.0.1:1455`。不要把 ChatGPT 密码、浏览器 Cookie、Codex 的
`auth.json` 或访问令牌粘贴到群聊或代码中。

授权后程序会检查实际模型列表，并完成一次很短的 Luna 文字推理；这会使用少量订阅用量。
只有收到 `response.completed` 且含有效文字才会备份配置、重启 AstrBot 并启用聊天模型。
配置备份位于 `data/astrbot/config-backups/chatgpt-*/cmd_config.json`。
切换后的提供商 ID 是 `qqchat-chatgpt-luna`。在 AstrBot 的模型菜单中可以切回原有聊天模型。
显式为会话选过其他模型的会话，仍可在模型菜单中自行切换。

连接工具只监听本机回环地址，不开放公网服务；可在完成后关闭登录窗口。
机器人启动时会自动读取授权，并在到期前刷新。Windows 凭据使用当前用户的 DPAPI
加密，保存在已被 Git 忽略的 `data/chatgpt/credentials.bin`；不能搬到另一个 Windows
用户下直接复用。Unix 使用仅本人可读写的凭据文件。

## 查看与退出

使用同一个 Python 路径，将末尾命令替换为：

```text
scripts\connect_chatgpt.py status
scripts\connect_chatgpt.py models
scripts\connect_chatgpt.py verify
scripts\connect_chatgpt.py activate
scripts\connect_chatgpt.py logout
```

`verify` 和 `activate` 会执行一次真实推理。`login --new-account` 添加其他账号；
`login --profile <注册ID>` 重新授权已保存账号。`status` 只显示连接状态和账号标签，
不显示凭据。退出前应先在 AstrBot 切回原有聊天模型。

在 ChatGPT **设置 → 用量** 管理此应用用量，在 **安全和登录 → Sign in with ChatGPT**
断开授权。连接不增加订阅额度。授权无效、额度耗尽、响应流中断或模型不可用时明确报错。

官方资料：

- [订阅用量接入](https://developers.openai.com/siwc/token-sharing-open-source)
- [注册与登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [模型与推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [当前接口限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
