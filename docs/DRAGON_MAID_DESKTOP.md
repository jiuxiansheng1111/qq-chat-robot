# 龙女仆桌面皮套

启动 `scripts/start_dragon_desktop.ps1`，或桌面上的“龙女仆桌面”快捷方式。需要本机 `.venv` 的 PySide6 6.12.0 和 WebEngine；已安装。透明角色窗口不会开启摄像头、麦克风或直播。

- 左键按住角色拖动；滚轮放大/缩小。位置和窗口尺寸自动保存在 `artifacts/dragon-maid-live2d/desktop-position.json`
- 右键可打开控制台、放大/缩小、切换置顶和桌面语音，或退出；双击打开控制台
- 控制台 `http://127.0.0.1:6187/`，切换8种表情、自动眨眼、呼吸摆动、点头和语音口型
- 点击“说给你听”或“示例语音”会在当前网页试听；这段语音的桌面/OBS副本自动静音，画面和口型保持同步，避免两处重复播放
- 普通自动语音优先从桌面发声；没有桌面窗口时从OBS透明源发声
- 麦克风口型需用户点击授权，只传音量，不录音或上传麦克风音轨
- OBS 浏览器来源为 `http://127.0.0.1:6187/avatar`，1920×1080，60 FPS，透明背景

这是保留原画的自定义WebGL动态皮套，不是 `.moc3`、Live2D Cubism 或VTube Studio模型。动画底层使用 `assets/base-v3-blank.png`，由内置生图工具在已批准原画上移除五官，保留脸型、头发、服装和轮廓；五官由同角色面部图集叠加，避免原画眼睛和眉毛透出产生重影。完整原画 `base-v2.png` 仍保留。

表情修正：八种表情分别校准左右眼和嘴巴的源坐标，使用统一眼窝锚点，眼睛比前版约小一成。嘴巴按各表情独立定位，说话口型使用更小的局部范围；切换和眨眼不再因双层半透明暴露底图。`/static/expression-review.html` 为八种表情的静态校对页，独立预览不连接实时控制，不会播放语音。底层编辑提示词保存于 `artifacts/dragon-maid-live2d/blank-face-edit-prompt.txt`。

## B站弹幕联动

已在 BiliBot 的成功弹幕回复后接入 `app/services/avatar_bridge.py`，向本机 `/api/live-reply` 投递同一条回复。桌面端排队生成语音、同步口型并根据内容切换微笑；同一事件去重，不再调用一次聊天模型、不二次发弹幕。超过45秒的旧回复丢弃；有新回复时合并等待队列。默认沿用BiliBot已有回复冷却和模型选择。

主人明确要求开播后再配，因此当前 `LIVE_DANMAKU_ROOM_ID=0`、`ENABLE_LIVE_DANMAKU_REPLY=false` 保持未启用。开播后在原BiliBot中设置实际房间并开始监听即可。当前未做真实直播间弹幕验收。

## 感谢与每日反馈

B站附加人设已允许按语境少量使用 `[doge]`、`[笑哭]`、`[捂脸]`、`[比心]`、`[星星眼]`，并要求感谢建立在真实记录上，不编造打赏或支持时长，不口头假称已送图片。

每日22:30“龙女仆每日反馈”自动化收集项目运行状况、互动反馈及相关新消息，在当前聊天给主人汇总。原21:30每日视频不变。

`scripts/dragon_supporter_gifts.py` 默认只整理候选；至少跨7天、3个活跃日，且有公开支持语境，才进入人工核对候选。每日自动化可为符合条件的用户先生成真实感谢小画，再用明确UID和图片路径执行发送。每周至多一份，同一用户30天内一次；发送前记账，未知结果禁止自动重试。初次检查暂无满足条件的长期记录，没有发出礼物。

## 2026-10-29 独立语音启动

桌面“龙女仆桌面”入口现在依次检查语音引擎、形象服务，再显示角色。无需先打开 QQ、NapCat 或聊天机器人。模型和音色沿用现有本地 GPT-SoVITS，不重复下载或复制模型。

- 独立语音入口：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_voice.ps1`
- 桌面入口：`scripts/start_dragon_desktop.ps1`，原快捷方式继续有效。
- QQ 启动与桌面启动共用 `scripts/voice_service.ps1`；同一项目用互斥锁防止并发重复启动。已有服务须通过 GPT-SoVITS API 校验才复用。
- 关闭 QQ 不会停止独立语音。关闭桌面形象后语音服务继续可供其他本机客户端使用。
- 翻唱占用 GPU 时不会争抢；释放后的语音恢复也改用独立入口。
- 没有新增开机计划任务；语音随用户点击桌面形象启动。启动失败详见 `artifacts/dragon-maid-live2d/voice-startup.log` 与 `data/logs/gpt_sovits.error.log`。
- 这里只独立进程的启动生命周期，现有代码、音色配置和模型仍保存在本项目目录内。

## 维护

`scripts/start_dragon_avatar.ps1 -Stop` 只停止有身份记录的本机服务。普通关闭桌面窗口不会关闭QQ/B站机器人。网页依赖服务6187，QQ/B站主服务仍在6185。

原画和图集位于 `artifacts/dragon-maid-live2d/assets/`，生成提示词在相邻 `generation-prompts.json`。Qt提供的 `web/dragon-avatar/qwebchannel.js` 保留原始 LGPL/GPL 许可声明；桌面脚本通过 PySide6 动态使用Qt，未改动Qt库。
