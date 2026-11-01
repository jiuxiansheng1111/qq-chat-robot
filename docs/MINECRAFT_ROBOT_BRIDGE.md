# Minecraft 人形机器人对话与可选语音桥

此功能把本地 Minecraft 人形机器人的玩家提问交给当前项目 AstrBot 中已配置的主聊天模型，先返回文字。玩家可另外开启游戏内语音，将回复交给同项目已有语音模型朗读。服务由 `astrbot_plugin_qq_chatrobot` 在插件正常加载时启动，卸载时关闭；导入项目模块不会监听端口。实现位于 `app/minecraft_bridge.py`，模型沿用 `app.main` 运行时的 `LLMManager.ask_primary` 和 AstrBot 公开 provider `text_chat` 接口。

## 连接与激活

- 默认地址：`http://127.0.0.1:8012`
- 游戏对话：`POST /minecraft/bridge/chat`
- 可选语音：`POST /minecraft/bridge/voice`
- 健康状态：`GET /minecraft/bridge/health`
- 仅绑定 IPv4 loopback。请求必须由本机 TCP 对端发出；服务不读取或信任 `X-Forwarded-For` 等转发头。
- 需要 aiohttp。当前 AstrBot 运行环境已有该依赖；旧项目 `.venv` 不提供此服务。
- 源码通过项目已有 Junction 插件目录供 AstrBot 加载。下一次正常加载插件（通常是下次正常重启 AstrBot）后才会启用；本次没有重启正在运行的实例。
- 若 8012 已被其他程序占用，插件记录 `port_unavailable` 并保留现有监听者，QQ 聊天仍可运行。需要在 AstrBot 本地日志确认冲突后协调端口。

## POST 请求

请求头使用 `Content-Type: application/json`，整个请求体最多 20 KiB。只允许以下字段，不接受额外字段：

```json
{
  "protocol": 1,
  "character": "tomori",
  "player_uuid": "00000000-0000-0000-0000-000000000001",
  "player_name": "Player",
  "robot_uuid": "00000000-0000-0000-0000-000000000002",
  "messages": [
    {"role": "user", "content": "你好，你能做什么？"}
  ],
  "context": {
    "dimension": "minecraft:overworld",
    "mode": "STAY",
    "health": 20,
    "cooking_available": false
  }
}
```

`character` 支持 `tomori`、`anon`、`rana`、`soyo`、`taki`、`sakiko`、`uika`、`umiri`。玩家名称最多 64 字符。`messages` 必须含 1 至 12 条消息，每条角色只能是 `user` 或 `assistant`，内容最多 1200 字符，最后一条必须是 `user`。维度字符串最多 128 字符，`mode` 仅支持 `STAY`、`FOLLOW`、`WANDER`，`health` 是 0 至 100 的有限数值；当前 `cooking_available` 必须为 `false`。

成功响应：

```json
{"protocol":1,"character":"tomori","reply":"你好……我可以陪你聊天，也能认真回答你的问题。"}
```

`reply` 最多 6000 字符。错误响应含稳定的 `error` 代码；模型超时返回 HTTP 504，主模型不可用或桥接并发已满返回 HTTP 503。桥接不尝试 AstrBot 备用模型。健康响应只报告协议、后端类型、服务状态和支持的角色，不暴露模型配置或密钥。

## 行为边界

聊天模型只返回文字建议；语音接口只朗读已有回复，不再调用聊天模型。桥不会执行游戏命令、移动、烹饪、文件操作或其他动作，也不向 QQ 发送消息。烹饪暂未接入；机器人只能给出文字建议。模型调用仅由游戏客户端提交玩家消息触发，使用本机已配置的 AstrBot 主模型，不添加或导出凭据。对话上下文由游戏客户端随请求提供，服务端不持久化机器人或玩家会话。

`protocol: 1` 的消息上下文、`context` 字段和 `cooking_available` 标志为将来增加烹饪建议/交互边界预留；当前没有任何烹饪调用或游戏动作接口。

## 可选语音（2026-10-17 源码实现）

本地项目入口为 `C:/Users/罗斌/Desktop/qq-chatrobot-all/qq-chatrobot`；语音环境位于相邻的 `qq-chatrobot-voice`。[公开项目仓库](https://github.com/jiuxiansheng1111/qq-chat-robot)已核实可访问，本轮桥接改动仅保存在本地，未上传。

`app/minecraft_voice.py` 复用已有 `voice_profiles` / `synthesize_voice`，保留既有主模型、TTS 权重、参考录音、语言与账号授权。当前八位乐队角色是 RVC 音色，`tts_enabled=false`；RVC 只能转换已有声音。因此游戏路线明确为“现有默认 TTS（当前丛雨）→目标角色 RVC”，不是直接把 RVC 当成 TTS。角色如果将来配置独立 TTS，则直接使用该角色 TTS。没有模型/参考录音/基础 TTS/CUDA 或资源保护不允许时，返回明确错误，游戏保留完整文字；不偷偷改用其他角色当作目标角色。

游戏的语音开关默认关闭，保存在游戏实例 `config/ic2newera-robot-voice.json`。显式语音请求使用当前 Settings 的内存副本启用 TTS；QQ 的 `VOICE_ENABLED`、LLM、`.env` 与账号开关均不变。受限于特定 OneBot 账号的音色不会在 Minecraft 中解除限制。

游戏语音现使用按需进程，默认不需要常驻 GPT-SoVITS。可选检查已有运行路径（无须开启 QQ 总语音）：

```powershell
cd C:\Users\罗斌\Desktop\qq-chatrobot-all\qq-chatrobot
powershell -ExecutionPolicy Bypass -File scripts\start_minecraft_voice.ps1
```

该脚本默认只检查路径并写入不含凭据的 `data/minecraft_voice/runtime.json`，不启动模型。每条游戏请求按顺序运行一次性 TTS、RVC；前一进程完全退出后才载入下一阶段。小音频缓存最多8MiB，不常驻八套模型。可显式传 `-GptRoot` / `-Python` 指定已有环境；`-PersistentSidecar` 才启用旧的常驻侧车诊断方式，游戏无需它。离线 lite 语言检测只使用已安装的小模型，不下载模型、不改第三方源码。主环境先前补充的 `opencc-python-reimplemented==0.1.7` 为481kB依赖。内存实现和当前验证边界见 [游戏语音内存说明](MINECRAFT_VOICE_MEMORY.md)。

`POST /minecraft/bridge/voice` 请求严格为：

```json
{"protocol":1,"character":"anon","text":"你好，我是爱音。"}
```

文本最多 360 字符，且不超过现有 `voice_max_chars`；超长回复保留完整文字，不截断假装全部朗读。成功返回 `audio/wav` 二进制，PCM16、单/双声道、8–48 kHz、最多 90 秒与 8 MiB；带 `X-IC2NE-Protocol: 1`、`X-IC2NE-Character`、`X-IC2NE-Voice-Route`（`tts` / `tts_then_rvc`）和 `Cache-Control: no-store`。失败返回 JSON `error` / 中文 `message`，HTTP 503 表示未就绪/忙碌，504 表示超时。一次只处理一条语音；断开请求会取消作业并清理本次生成的临时文件，RVC 共用已有 GPU 锁与资源保护。

健康响应新增 `voice.configured`、`tts_endpoint_local`、`tts_tcp_ready`、每角色 `rvc_files_present` 和 `base_tts_configured`。端口可连接、文件存在与完成推理分开；健康接口始终不把这些诊断标志表述为音色/推理已验收，也不暴露路径或凭据。

Minecraft 客户端用 Java Sound 在后台播放内存中的 WAV，音量取请求时游戏“主音量×语音”设置；当前为本机非空间语音，只由发出请求的玩家听到。取消、关闭、关闭语音或离开机器人页面时停止请求与播放。Java 音频设备不支持或桥仍是旧版只有文字端点时会显示中文提示，文字不受影响。更新插件源码须在用户正常重新加载插件后生效，本轮没有重启现有 QQ/AstrBot。

## 当前实证与尚未验收

用户于后续完成重载与释放内存后，八角色真实HTTP音频均成功（`http-224755`），并用已安装游戏JAR中的 `RobotVoicePlayer` 完成全部八份本机输出。首条冷启动52.859秒，后七条复用基础TTS各12.718–13.828秒；最终缓存命中0.109秒。结束无语音工作进程，音频缓存约3.07MiB，7个原配置/LLM文件哈希不变。实际阶段证明CUDA半精度、轻量中文前端与TTS/RVC顺序退出均生效。聚合回执和内嵌试听在 `artifacts/minecraft-voice-20261017/eight-character-acceptance.json`、`voice-samples.html`。未启动Minecraft，游戏内实体/页面与多人联调仍待实际游玩确认。下文失败状态为此前历史证据。

后续内存优化已实际加载：通过 `scripts/run_astrbot.ps1 -Restart -ReuseExistingConfig` 跳过配置生成，重启原有受管AstrBot实例加载插件。8012健康响应确认按需worker和八角色配置；重启前后7个原配置/LLM相关文件哈希一致。最新HTTP真实请求仍因空闲3.67GiB低于原4GiB启动保护返回503，未启动模型。新版关闭G2PW、BERT/HuBERT直接FP16载入与阶段日志已实现，但八角色最终音频和游戏播放仍未验收。证据在本仓库 `artifacts/minecraft-voice-20261017/`；以下为前一轮基础TTS的历史记录。

2026-10-17 已完成 Python 编译、Ruff 和 PowerShell 解析检查，未新增/运行测试。真实调用本机 GPT-SoVITS 已生成短句“你好，我是爱音。”的**丛雨基础 TTS 音色**：1.42 秒、90,924 字节、PCM16 mono 32 kHz，暖启动合成 2.047 秒。它不是爱音角色音频。完整爱音路线在 TTS 后被现有资源保护阻止：可用物理内存约 0.37 GiB，低于 RVC 启动 4 GiB。没有降低保护阈值；关闭本次临时 TTS 侧车后可用约 3.59 GiB，仍不足启动 RVC。

证据在 IC2 项目 `build-isolated/robot-voice-20261017/`：`sample-base-murasame.wav`、`base-generation-audit.json`、`generation-audit.json`、侧车日志。没有调用 LLM、发送 QQ、修改 `.env` 或全局开关、启动/关闭 Minecraft、修改存档或下载模型。临时侧车已关闭。角色转换完整音色、HTTP 新端点在现有插件中的激活、Java 音频设备与游戏内播放均仍待真实环境验收；不能把源码接通或基础 TTS 样本称为全部通过。
