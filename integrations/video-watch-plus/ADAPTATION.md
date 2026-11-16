# QQ 适配与验收

上游：[xinhailaopo/video-watch-plus](https://github.com/xinhailaopo/video-watch-plus)，固定版本 `8ca266f9cf9e5ffce7e9d424aa0e937a897b608d`。保留上游 SKILL.md、LICENSE 和 NOTICE。此目录是 QQ 专用功能后端，不把技能全文当成人格提示词，不打开通用电脑或终端工具。

- `scripts/setup_video_watch.py` 复用已安装的 FFmpeg、音频 Python 与本地 Whisper 模型；FFmpeg 的七项必需滤镜已逐项验证。ImageMagick 7.1.2-35 便携版只装到 `data/video-watch-plus`，按官方发布摘要校验 SHA256。Windows 内置 tar 支持该 7z，避免额外安装解包器。不改系统 PATH、用户环境变量或 B 站配置。
- 当前 FFmpeg 有所需滤镜但未带 libx264；仅为上游合成验收脚本补充 `--codec mpeg4` 选项，原基准时间轴不变。已通过 doctor、build_testclip、probe、plan、audio_probe、grid 的实际验收：3/6 秒切点、0/6 秒冻结、3.66 秒静音结束均与真值匹配，纯音没有当成人声。
- `app/astrbot_video_skill.py` 调用上游逐帧量测、采样计划、声学侦察和局部放大。`qqchat_video_detail` 只处理本轮真实或引用的视频，模型不能指定任意本机文件路径。输入区域、时间及读取范围有明确校验。
- QQ 私聊、群聊可读取实际视频附件，也可匿名取得平台公开提供的 B 站画面/音轨；不使用 B 站登录态，不绕过付费或试看限制。分 P 和 `t` 时间参数按实际视频处理。对话与最终解释仍由原 GPT-6 Luna 完成，现有图像生成、唱歌等接口不变。
- 声音先经过声学侦察，再结合本地 SoundTry 的人声门控转写核对。声学判型存在不确定性，不能凭单一物理指标把真实人声硬判为不存在。视频本地转写覆盖至多 60 秒；画面只覆盖列出的抽帧，小字需单独放大。成功读取不等于逐帧看完整段，未覆盖部分明确保留。
- 用户提供的 SurfCore 演示已通过实际 QQ 视频链路读取 56.5–146.5 秒的画面取样及其中前 60 秒音轨，实际原视频为 936 秒。不能记成完整看过 936 秒。

未安装可选的浏览器扩展链路；当前公开视频读取不需要桌面或浏览器操作。B 站机器人仍使用其原有人格、模型、账号和功能。
