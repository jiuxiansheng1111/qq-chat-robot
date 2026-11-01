# Bilibili 接入记录

## 龙女仆每日视频（2026-10-28）

用户确认每天北京时间 **21:30 开始**整理与制作，成片按实际制作耗时投稿。内容约定见 `prompts/dragon_maid_video.txt`：当天真实经历、实际功能或语音展示、一个自己的发现、对主人的感想或温和吐槽，最后一两句贴合当天的鼓励。介绍侧重能力和用途，不局限于群聊，不反复强调“非官方”，不每天重做自我介绍。保留投稿平台 AI 生成声明。

用户提前要求制作时，先在对应日期目录保存 `episode-state.json`（`origin: manual`、`scheduled_skip: true`）。当天21:30检查到该标记便跳过自动制作，即使手动工作尚未完成也不新开或重启一份；第二天恢复正常安排。已有回执、制作进行中或投稿结果不明时也不重复生成。仅讨论以后可能提前做不算实际请求。

每天集中讲一个新重点，设计灵感、表情设计、语音、功能实践和热点点评分期轮换，先检查近七期避免重复。热点须在当次制作时联网核对官方来源和可靠报道，区分事件日期与报道日期，来源、查证时间和事实/个人评价分别记入 `evidence.json`，简介附实际使用的链接。2026-10-28用户已明确提前安排“限定短句试唱＋S16的AL出局回顾”，设计灵感留到后续一期。

已发布介绍片作为历史成片保留，新模板修改不会自动更换线上视频。每天使用独立目录 `artifacts/dragon-maid-daily/YYYY-MM-DD`，避免覆盖首支视频和混用投稿回执。目录包含 `story.json`（沿用 chapter/shot/text 结构）、`metadata.json`（title/description/tags）、`cover.jpg` 和本期配音、字幕、成片及回执。画面复用用户已批准的 `artifacts/dragon-maid-video` 形象。脚本默认路径仍兼容首支视频，新日记必须指定 `--work`：

```powershell
& '.\.venv\Scripts\python.exe' scripts/dragon_intro_voice.py --work artifacts/dragon-maid-daily/YYYY-MM-DD
& '.\.venv\Scripts\python.exe' scripts/dragon_intro_render.py --work artifacts/dragon-maid-daily/YYYY-MM-DD --assets artifacts/dragon-maid-video
& '.\.venv\Scripts\python.exe' scripts/dragon_intro_upload.py --work artifacts/dragon-maid-daily/YYYY-MM-DD --dry-run
& '.\.venv\Scripts\python.exe' scripts/dragon_intro_upload.py --work artifacts/dragon-maid-daily/YYYY-MM-DD
```

产物名暂沿用 `dragon-maid-introduction.mp4`，但保存在每期独立目录；标题与简介必须来自本期 `metadata.json`。运行前先确认本机语音服务、运行环境依赖；不要因依赖缺失重新安装或覆盖全局环境。配音可断点续做，渲染缓存包含模板版本；每期上传前必须核对当日回执和创作中心现有稿件，查看首中尾画面、听实际配音并确认片长。上传成功后记录 aid/bvid，再读取公开稿件状态；审核中不能说观众已经可见。

活动来源：`data/astrbot/instance/data/plugin_data/astrbot_plugin_bilibili_ai_bot/` 中的当日观看、回复、主动操作记录及 SQLite 账本。仅提取自己的活动和公开内容，区分尝试、成功提交、公开可见；不要把私信或群聊原文用于公开视频。普通聊天回复仍走 Luna，视频定时制作通过当前 Codex 聊天执行，不改变聊天与直播模型配置。需要电脑在线和 Codex 运行；并非关机可用的云端托管。

配置日期：2026-10-27（北京时间）。对外称号为 **ChatGPT 的龙女仆**。

## 登录和运行

打开 [AstrBot 的 Bilibot 页面](http://127.0.0.1:6185/#/plugin-page/astrbot_plugin_bilibili_ai_bot/bilibot)，进入「账号连接」，生成二维码，用要托管的 B 站账号扫码。二维码过期后重新生成即可。

插件已安装并启用；未登录时不会启动 B 站后台互动。扫码登录成功后插件会保存登录状态并自动启动。首次轮询跳过历史通知，只处理后续新消息。账号注册、验证和扫码由账号持有人完成。

这套服务在当前电脑本地运行；需要保持电脑联网且 AstrBot 运行。它不是关机后仍在工作的云端服务。需要长期暂停时，在 AstrBot 插件管理中禁用 Bilibot；只关闭页面不会停止后台任务。

## 模型和功能

| 用途 | 配置 | 当前状态 |
| --- | --- | --- |
| 评论回复、私信回复、视频内容理解、主动评论 | `qqchat-chatgpt-luna` / `gpt-6-luna` | 已配置 |
| 未来直播互动 | `qqchat-chatgpt-live-sol` / `gpt-6.1-sol` | 提供商已验证、已预留 |
| 游戏直播、OBS 推流、游戏操作 | 等待游戏和直播安排 | 未启用 |

使用现有 ChatGPT 订阅连接；默认聊天模型仍是 Luna。视频通过字幕和抽帧理解，不把视频文件直接当作模型支持的输入。启动脚本会将项目内已有的 FFmpeg/FFprobe 和 AstrBot 环境的 yt-dlp 目录加入子进程的 PATH。

评论和私信会根据上下文自然回答。普通闲聊以 20 字符以内为目标；明确提问、解释、求助和总结可以完整展开，评论回复仍受插件 180 字符限制，私信为 1200 字符。自我介绍可以自然修饰；明确询问是否为 AI 时如实说明。

## 评论频率

- 评论回复与主动评论共用 **北京时间每天最多 100 条**的额度。这是上限，不是发送任务。
- 主动评论每天最多 **10 条**，每个视频最多 1 条；每天分两轮浏览，每轮最多 8 个、合计最多 16 个视频。额度是上限，不会为了凑数乱评；优先相关 AI 技术、游戏技巧和有趣游戏内容。
- 没有具体内容就跳过。主动评论超出 20 字符时不发送，不截断。
- 评论轮询和回复冷却均为 60 秒；保留重复、低价值和广告过滤。
- 主动浏览每天两个时间段，23:00～08:00 为主动活动休息时间。休息期间继续监听新评论和私信；私信独立按需轮询，最多 60 次回复/天。
- 私信跳过 B 站的固定互关提示“我们已互相关注，开始聊天吧～”，不会因此再发一次招呼；正常私信和视频分享仍会处理。
- 绑定的主人可直接在 B 站私信发 `控制菜单`、`运行状态`、`暂停主动活动`、`恢复主动活动`、`关闭评论回复`、`开启评论回复`、`关闭主动评论`、`开启主动评论`、`主动评论上限 5`（1～10条/天）。入口按真实发送 UID 鉴权，设置持久化；不受模型是否理解指令影响，暂停主动后仍保留私信控制。
- 点赞、投币、收藏、关注、发动态、自动拉黑及直播弹幕互动均未开启。

共享额度存放在插件 SQLite 中，重启保留。评论动作必须先获得持久化额度和开始发送的确认；数据库不可用时不发送。发送结果不明的动作保留额度，不盲目重试。

## 安装与维护

上游：[chenluQwQ/astrbot_plugin_bilibili_ai_bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot)，版本 1.7.2，固定提交 `1d12480b147b8aa43e7f39e455b23a7818d3e157`。

安装位置：`data/astrbot/instance/data/plugins/astrbot_plugin_bilibili_ai_bot`。

本地补丁 `deploy/bilibili/comment-budget.patch` 增加共享评论上限、发送前持久化检查、主动评论短句检查、互关提示过滤、主人私信控制，以及休息时段保留入站回复监听。更新插件前需保留并重新检查该补丁；直接覆盖上游版本会丢失这些本地行为。

配置准备脚本为 `scripts/configure_bilibili_bot.py`，默认只预览，`--apply` 才修改。它检查固定版本和已启用的 Luna，保留已有登录字段，修改前备份；必须先停止 AstrBot。配置备份在 `data/astrbot/config-backups`，人格备份在 `data/astrbot/identity-backups`。

从项目目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_astrbot.ps1 -Stop
& '.\data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe' -m scripts.configure_bilibili_bot --apply
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_astrbot.ps1 -ReuseExistingConfig
```

`-ReuseExistingConfig` 保留现有模型、权限和插件配置。不要用重新生成配置替代它。

## 验收范围

`artifacts/bilibili-ready/model-check.json` 记录实际 Luna 回复协议和人设检查；`proactive-model-check.json` 记录主动评论生成器检查。检查只使用合成素材，不登录 B 站，也不向 B 站或 QQ 发送测试消息。共享额度、并发、重启、发送状态异常等通过本地测试验证。

上游 `test_proactive_flow_snapshot.py` 有两个快照在当前 Windows 时区下失败：快照使用纽约时间的年初 epoch，代码使用本机北京时间。已在未修改的固定提交中复现相同差异；本次没有修改该时间逻辑。共享额度的北京时间日期边界使用独立的显式 UTC+8 计算，并已有测试。

2026-10-27 已确认账号登录、后台运行和私信监听初始化成功，运行时没有失败记录。休息时段入站监听修复的三项测试通过，已单独重载 Bilibot 生效。实际评论／私信回复仍需收到初始化之后的新消息才能验收；不能把开关已开启等同于真实发送已成功。

未来直播候选为 [astrbot_plugin_live_stream_companion](https://github.com/menglimi/astrbot_plugin_live_stream_companion)。目前没有安装或启动直播；待确定游戏、直播间、画面和声音后，再把已预留的 GPT-6.1 Sol 接入直播流程。
