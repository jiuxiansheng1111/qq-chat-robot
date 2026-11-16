# SoundTry 的 AstrBot / QQ 接入

上游：https://github.com/CixiSVesper/dsh-soundtry

版本：1.0.2，审阅提交 `53458e1daa248df91ce4f8514ca0e06a9fc421cb`，MIT 许可见 LICENSE。

保留上游 Python 音频分析引擎，通过 QQ 的 `qqchat_sound_analyze` 工具提供综合听取、转写、节奏、旋律、和弦、结构六种模式。原 DSH 插件管理器、网页设置与 Node 宿主不安装。引擎仅增加 SOUNDTRY_STATE_DIR，以便模型和缓存写入项目 data/soundtry。

`scripts/setup_soundtry.py` 下载 medium 和 MDX-Net 权重。复用现有 singing/runtime/.venv，不改动其依赖。medium 的 model.bin 实测服务端大小为 1527906378 字节，上游清单的 expectBytes 为整个模型目录的近似大小；MDX 校验上游 MD5。

只接受本轮真实附件或引用链中的附件，不向模型暴露任意文件路径读取能力。模型通过 mode 和附件序号调用，分析在独立后台进程进行，原聊天可继续。完成后由当前 QQ 聊天模型按大肥鱼人格解释结果；失败时明确结束，不凭空编造听感。最近的分析资料只供同一会话的同一用户后续聊歌。

本地分析固定 CPU/int8，避免与现有 GPU 唱歌任务争抢显存。每次最多分析10分钟、100MiB的附件。后台分析期间保持静默，完成或失败后一次性回复完整结果，单次分析最多12分钟。

未安装已取消的 dsh-narrator。
