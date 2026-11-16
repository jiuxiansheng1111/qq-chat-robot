# QQ 大肥鱼本地声线

用户授权接入 SurfCore 配套的本地语音包。来源：[Mieksido/surfvoicelocal](https://github.com/Mieksido/surfvoicelocal)，资源固定为官方 `v0.1.0` 发布，代码许可见 LICENSE；模型及录音沿用各自上游许可。

`scripts/setup_surfvoice.py` 下载官方资源、校验 SHA256、检查归档路径后解压到 `data/surfvoice`。不会运行上游批处理安装器，不改已有 GPT-SoVITS、唱歌、生图或 B 站接口。下载体积大；文件在本机，不进版本库。

`app/services/qq_voice.py` 读取本机验收后的 `data/surfvoice/qq-voice.json`，仅为 QQ 创建配置副本，默认选择大肥鱼声线，原 Settings、原音色及模型映射保留。未完成配置或资源缺失时不会把其他声音冒充成大肥鱼专属声线。语音只是朗读通道，聊天人格始终读 `prompts/qq/dafeiyu.txt`。

QQ 的 `qqchat_send_voice` 支持私聊和群聊；已有语音模式、音色选择与唱歌功能继续使用原接口。语音失败保留完整文字，不报告虚假的发送成功。

2026-11-03 已使用现有 Seed-VC 翻唱引擎和此参考录音验证约 22 秒真实歌声，原音高、声线、有声覆盖和削波门槛全部通过，随后启用 `profile.singing_enabled`。QQ 点唱走 `qqchat_sing` 和原串行队列，既有明确命令也转入同一流程，普通朗读仍走 `qqchat_send_voice`。只询问唱歌能力时不生成歌曲，收到明确点唱后才在后台生成并发送音频。能力判断不依赖 AstrBot 原生 TTS Provider 是否配置，也不向 QQ 聊天暴露引擎或服务设置。

官方归档未包含 README 提到的 `logs/weights_*` 微调检查点。当前适配采用随包分发的 v2 基础 GPT/SoVITS 模型和官方 `voicepack/age12/01.wav` 参考录音，通过已安装的 GPT-SoVITS 接口合成；不冒充缺失的微调版。已实际合成约 6 秒语音，并用本地 ASR 核对朗读内容。服务未运行时按原管理脚本按需启动；不另起 SurfCore 或 voxd，不改变其他音色的默认模型映射。
