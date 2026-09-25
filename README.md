# QQ ChatRobot

一个面向 QQ 群聊的可扩展机器人框架。项目使用 **NapCat / OneBot 11** 接入 QQ，以 **FastAPI** 处理事件，支持智谱、Groq 等 OpenAI 兼容 LLM，并内置联网搜索、音乐点歌、长期记忆、群聊风格学习、JWT 管理 API、插件系统、限流和随机图片。

> 当前版本：`0.1.0`。适合个人机器人、群聊助手和二次开发。建议使用专门的机器人 QQ，并在正式使用前阅读本文的安全说明。

## 功能特性

### 群聊能力

- `@机器人 问题` 或 `/ai 问题` 触发 AI 对话。
- `@机器人 随机猫咪`、`/猫` 获取随机猫图。
- `@机器人 随机猪猪`、`/小猪` 获取随机真实小猪照片。
- `@机器人 随机奶龙`、`/奶龙` 获取随机奶龙表情包。
- `@机器人 丛雨图片`、`@机器人 丛雨表情` 从本地授权素材目录发送图片/GIF；`@机器人 生成图片 描述` 调用已配置的图片生成服务。
- `启动语音` 后先显示音色菜单，再用 `选择音色 <ID>`（`切换音色 <ID>` 也可以）选择；`关闭语音` 关闭个人语音回复。`丛雨语音` 仅发送本地授权语音片段，语音服务默认关闭，不会自动训练或克隆音色。
- `音色列表`、`切换音色 <ID>` 选择后台配置的中文/日语或其他角色音色；音色档案只保存 provider 的 voice/model/language 参数。
- `@机器人 今日奥特曼` 从 129 位角色与独立形态中每日抽取一位并保存收藏；形态池只保留 **TV 正剧/电视特别篇实际登场的形态**，并额外保留雷杰多、赛迦、梦比优斯无限、银河维克特利、格罗布、令迦等真正由多位奥特战士合体/融合而成的形态。街机、游戏、舞台剧和单纯电影专属的非合体形态不收录。`@机器人 我的奥特曼` 可查看累计次数、收集数量和本命奥特曼。
- `@机器人 奥特曼图鉴` 查看全部收录名单；直接发送 `@机器人 奥特之父`、`@机器人 贝利亚`、`@机器人 闪耀赛罗` 等正式名或常用简称，可查看真实图片与详细资料，但不会增加收藏次数。二次元角色同样支持简称，例如 `芳乃`、`桐人`、`桐谷和人`。
- 简称、错别字或同名候选出现歧义时不会静默选错：机器人会列出候选角色，回复“是/确认”或序号后才发送对应资料和图片；回复“取消”可放弃本次查询。
- `@机器人 搜索 关键词` 使用免 Key 的 Bing RSS 联网搜索，并让 LLM 基于搜索结果总结。
- 普通 `@机器人 问题` 遇到最新资料或模型无法可靠确认时，可自动回退到联网搜索并附来源；本地通过 `AUTO_WEB_SEARCH_ENABLED` 控制。
- `@机器人 点歌 歌名` 使用网易云搜索并发送可点击的原唱音乐卡片；歌手名可选，支持别名和中英文输入，不会影响普通 @ 对话。
- 成员可以明确要求机器人长期记住个人信息，并随时查看或全部删除。
- 匿名统计群消息长度、标点和少量安全口头语，让回复逐渐贴近群聊氛围；不保存普通群聊原文。
- 随机夺舍会从当天发言达到 15 条的群友中抽取一名“本群今日随机成员”，可反复重抽；指向夺舍可以不限次数地切换到被 @ 的群成员。
- `/help` 或 `@机器人 help` 查看动态帮助菜单。
- 用户可自行开启、关闭、清除和查询短期对话记忆。
- 群管理员可以启停机器人、维护群成员黑名单和控制群级插件开关。
- 通过角色提示词和少量示例定义机器人语气，无需直接训练模型。

### LLM 与并发

- 支持智谱和 Groq，可扩展其他 OpenAI Chat Completions 兼容服务。
- 主 Provider 失败后自动尝试备用 Provider。
- 默认最多同时处理 10 个 LLM 请求，并提供有限队列和排队超时。
- 包含 Provider 失败统计、429 统计、平均延迟和健康查询。
- 用户级与群级双层限流；Redis 不可用时自动回退到本地限流。

### 安全与可靠性

- OneBot HTTP Server 与事件 Webhook 使用独立 Token；Webhook 支持 NapCat `X-Signature` HMAC-SHA1 校验。
- 管理 API 使用 JWT Access Token 与可轮换、可撤销的 Refresh Token。
- 管理员密码哈希存储，生产环境校验弱密码和弱 JWT 密钥。
- 基于 OneBot `message_id` 的短期幂等去重。
- 图片类型、响应大小、请求超时和有限重试检查。
- SQLite 持久化；Redis 可选用于分布式限流和事件去重。
- pytest、ruff、GitHub Actions CI、Dockerfile 与 Docker Compose。

## 工作流程

```text
QQ群消息
   │
   ▼
NapCat / OneBot 11
   │  HTTP Client 事件上报
   ▼
POST /onebot/webhook
   │
   ├─ 鉴权 / 去重 / 群开关 / 黑名单 / 限流
   ├─ 内置指令与自定义插件
   ├─ 智谱 → Groq 备用
   └─ 网易云 / Bing RSS / CATAAS / Wikimedia Commons / nailong-memes
   │
   ▼
NapCat HTTP Server → QQ群回复
```

更完整的分层说明见 [架构设计](docs/ARCHITECTURE.md)。

## 指令一览

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `@机器人 <内容>` | 所有人 | 直接与 LLM 对话 |
| `@机器人 随机猫咪` | 所有人 | 随机猫图 |
| `@机器人 随机猪猪` | 所有人 | 随机发送真实小猪照片 |
| `@机器人 随机奶龙` | 所有人 | 随机发送奶龙静态图或 GIF |
| `@机器人 丛雨图片` / `丛雨表情` | 所有人 | 发送 `data/murasame_assets` 下的授权图片、GIF 或表情 |
| `@机器人 生成图片 描述` | 所有人 | 使用已配置的 OpenAI 兼容图片生成接口 |
| `启动语音` / `关闭语音` | 所有人 | 启动后先显示音色菜单，再选择当前用户的语音回复音色 |
| `音色列表` / `选择音色 <ID>` | 所有人 | 选择后台配置的不同角色/语言音色 |
| `丛雨语音` | 所有人 | 发送本地 `voices/` 下的授权语音片段 |
| `@机器人 今日奥特曼` | 所有人 | 每日抽取一位奥特战士/独立形态并写入个人收藏 |
| `@机器人 我的奥特曼` | 所有人 | 查看累计获得次数、已收集数量与本命奥特曼 |
| `@机器人 奥特曼图鉴` | 所有人 | 查看当前完整角色/形态池 |
| `@机器人 <奥特曼名称/常用简称>` | 所有人 | 查看指定角色或形态的图片与详细资料，不增加收藏次数 |
| `@机器人 翻译 外语内容` | 所有人 | 翻译成简体中文，并给出罗马字/等价搜索写法 |
| `@机器人 搜索 关键词` | 所有人 | 联网检索并附上结果来源 |
| `@机器人 点歌 歌手名 + 歌名` | 所有人 | 搜索并发送网易云原唱音乐卡片 |
| `@机器人 点歌 歌名` | 所有人 | 只输入歌名也可搜索，不区分大小写 |
| `@机器人 播放视频 关键词` | 所有人 | 搜索 B站视频，优先发送内容高相关且同档中播放量更高的小卡片 |
| `/help` | 所有人 | 查看精简帮助菜单 |
| `/记忆开启` | 所有人 | 开启当前群内的个人短期记忆 |
| `/记忆关闭` | 所有人 | 关闭并清除短期记忆 |
| `/记忆删除` | 所有人 | 清除当前短期记忆 |
| `/记忆状态` | 所有人 | 查询记忆状态 |
| `@机器人 记住：内容` | 所有人 | 保存本群内属于自己的长期记忆 |
| `@机器人 你记得什么` | 所有人 | 查看自己的长期记忆 |
| `@机器人 忘记我` | 所有人 | 删除自己的全部长期记忆 |
| `@机器人 记住，内容` | 所有人 | 保存本群共享关系或群梗，切换夺舍对象后仍保留 |
| `@机器人 群记忆` | 所有人 | 查看本群共享记忆 |
| `@机器人 删除记忆 关键词` | 所有人 | 删除所有包含相同文字的群共享记忆 |
| `@机器人 删除语气` | 所有人 | 删除当前夺舍对象持久化的语气样本与摘要 |
| `@机器人 删除语气 @群成员` | 所有人 | 删除指定成员的持久化语气样本与摘要 |
| `@机器人 清除群记忆` | 管理员 | 清空本群共享记忆 |
| `@机器人 随机夺舍` | 所有人 | 从当天发言达到 15 条的成员中重新抽取一名随机成员 |
| `@机器人 夺舍 @群成员` | 所有人 | 指向夺舍：不限次数切换真实群成员，不受活跃度门槛限制 |
| `@机器人 夺舍状态` | 所有人 | 查看今天是否正在夺舍以及抽中的真实群名片 |
| `@机器人 退出` | 被抽中者/管理员 | 退出当前夺舍，之后仍可立即重新夺舍 |
| `/bot on`、`/bot off` | 群主/管理员 | 启用或停用本群机器人 |
| `/blacklist add QQ号` | 群主/管理员 | 加入本群黑名单 |
| `/blacklist remove QQ号` | 群主/管理员 | 移出本群黑名单 |

旧式 `/ai <问题>`、`/猫`、`/小猪`、`/点歌 <关键词>`、`/翻译 <内容>`、`/视频 <关键词>` 命令仍然兼容，但推荐直接 @ 机器人使用。
@ 可以放在命令之前、之后或文本段中间；机器人会先移除自己的 @ 段，再解析命令。数组消息与 CQ 字符串消息均支持。

## 环境要求

- Python `3.11+`，推荐 Python `3.12`。
- Windows 10/11：可使用仓库提供的一键启动脚本。
- 最新版 NT 架构 QQ 与 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)。
- 至少一个可用的 LLM API Key。
- 可选：Docker Desktop、Redis。网易云点歌、B站视频搜索、猫 GIF、真实猪图和奶龙图库均无需额外 API Key。

## 快速开始

### 1. 获取代码

```powershell
git clone https://github.com/jiuxiansheng1111/qq-chat-robot.git
cd qq-chatrobot
```

### 2. 创建 Python 环境

Windows 可以直接使用项目自带的自愈脚本；它会优先使用 Python 3.12，没有时自动使用 Python 3.11，并在缺少 `.venv` 或依赖时自动创建和安装：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -IncludeDev
```

也可以直接双击 `测试项目.bat`，它会先自动修复 Python 环境，再执行 pytest、ruff 和 compileall。

### 3. 创建配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`。本地运行时建议至少填写：

```env
APP_ENV=development
REDIS_URL=

JWT_SECRET_KEY=请替换为至少32字符的随机字符串
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请替换为强密码

ONEBOT_API_BASE=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=与NapCat_HTTP_Server一致
ONEBOT_WEBHOOK_TOKEN=与NapCat_HTTP_Client一致
ONEBOT_SELF_ID=机器人QQ号

LLM_PROVIDER=zhipu
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.7-flash
LLM_API_KEY=你的智谱API_Key

LLM_FALLBACK_PROVIDER=groq
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=openai/gpt-oss-20b
GROQ_API_KEY=你的Groq_API_Key
```

说明：免费模型的可用性和限额会变化。若 `glm-4.7-flash` 返回 `429 / 1305`，通常表示该模型公共资源繁忙，可以稍后重试、切换其他可用模型，或依赖 Groq 备用 Provider。

### 4. 配置 NapCat / OneBot

在 NapCat WebUI 中创建以下两个网络配置：

| 类型 | 配置 |
| --- | --- |
| HTTP Server | Host `127.0.0.1`，Port `3000`，消息格式 `array`，Token 与 `ONEBOT_ACCESS_TOKEN` 相同 |
| HTTP Client | URL `http://127.0.0.1:8000/onebot/webhook`，消息格式 `array`，Token 与 `ONEBOT_WEBHOOK_TOKEN` 相同 |

注意：

- NapCat WebUI 默认是 `http://127.0.0.1:6099`。
- `ONEBOT_API_BASE` 应指向 OneBot HTTP Server `3000`，不是 WebUI `6099`。
- 本地默认使用 HTTP，不要写成 `https://127.0.0.1:3000`。
- HTTP Server Token 与 HTTP Client Token 是两套独立 Token，不要混用。

详细步骤见 [OneBot 接入指南](docs/ONEBOT_SETUP.md)。

### 5. 启动机器人

只启动 FastAPI：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 一键启动 NapCatQQ Desktop、OneBot 和 FastAPI：

```text
双击 start-qq-chatrobot.bat
```

启动脚本现在会自动检查 `.venv` 和运行依赖：虚拟环境不存在时自动用本机 Python 3.11/3.12 创建，依赖缺失时自动安装，不再因为 `.venv\Scripts\python.exe` 不存在直接退出。

机器人 API 由 `scripts\run_bot.ps1` 生命周期守护脚本管理：NapCat 的 OneBot HTTP Server（默认 `127.0.0.1:3000`）可用时自动启动或接管 Uvicorn；NapCat/OneBot 关闭后会自动停止 Uvicorn，重新打开 NapCat 后会自动使用当前项目代码启动 Uvicorn。不要用手动的 Uvicorn 命令替代一键脚本，否则不会启用这个生命周期联动。

为了避免重启电脑或机器人进程退出后出现 `ECONNREFUSED 127.0.0.1:8000`，管理员身份双击：

```text
安装机器人开机自启.bat
```

它会注册并立即运行 `QQChatRobot API` 计划任务，登录 Windows 后自动启动生命周期守护脚本；只有 NapCat/OneBot 可用时才启动并守护 8000 服务，NapCat 关闭时会一并停止 Uvicorn；同时注册每分钟检查一次的 `QQChatRobot Health Check`，主守护任务意外退出时也会自动拉起。
NapCat/QQ 因可能需要登录确认，仍使用 `start-qq-chatrobot.bat` 启动。

一键脚本会读取 `.env` 中的 `ONEBOT_SELF_ID`，检查端口避免重复启动 NapCat，并优先启动默认安装位置的 NapCatQQ Desktop：

```text
C:\Program Files\NapCatQQ Desktop\NapCatQQ-Desktop.exe
```

项目不再依赖桌面上的旧 `NapCat.Shell` 文件夹。若安装到了其他目录，请修改 `scripts/start_all.ps1` 中的 `$napCatDesktop`。

首次登录或触发 QQ 安全验证时，仍需在手机 QQ 上确认。详见 [Windows 一键启动](docs/ONE_CLICK_START.md)。

### 6. 检查状态

```text
http://127.0.0.1:8000/health/live
http://127.0.0.1:8000/health/ready
http://127.0.0.1:8000/health/llm
http://127.0.0.1:8000/docs
```

也可以运行连通性脚本：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_connectivity
```

## 图片功能

### 随机猫图

使用 `/猫` 或 `@机器人 随机猫咪`。机器人通过 [CATAAS](https://cataas.com/) 的 `/cat/gif` 端点获取动态猫图，下载后会校验 GIF 文件头再发送，不需要 API Key。后台任务会持续维持 2 张 GIF 缓存；遇到上游临时 500 会短暂重试，缓存不足时每 3 秒继续补充，不再因一次失败永久停止预热。上游请求使用 12 秒短超时，GIF 上传到 QQ 仍可能根据文件大小耗时数秒。

### 随机真实小猪照片

使用 `/小猪` 或 `@机器人 随机猪猪`。机器人会从经过筛选的 Wikimedia Commons 真实摄影文件池中随机抽取，抽完一轮后重新洗牌，因此不会连续发送同一张照片。该功能无需 API Key。

### 随机奶龙表情包

使用 `/奶龙` 或 `@机器人 随机奶龙`。图片来自 MIT 许可的 [nailong-memes](https://github.com/GGGeeeooorrrgggeee/nailong-memes) 图库；项目只保存轻量文件清单，每次按需下载一张并转为 QQ 可发送的 base64，不需要 API Key。奶龙角色及第三方素材的相关权利仍归各自权利人所有。

## 奥特曼收藏与图鉴

奥特曼功能使用同一份 `ULTRAMAN_ROSTER` 作为**图鉴、今日抽取和收藏统计**的数据源，因此只要把新角色/形态加入角色池，就会自动进入 `@机器人 今日奥特曼` 的随机范围，不需要维护第二份抽卡名单。当前共收录 **129 位角色与独立形态**。

### 当前重点收录

- **欧布系列（只收录电视作品实际登场）**：重光形态、暴炎形态、疾风形态、暗耀形态、原生形态，以及《奥特格斗欧布》等电视作品中实际登场的煌闪形态、智勇形态。旧译名“斯佩修姆哉佩利敖 / 燃烧炸弹 / 闪电攻击者 / 艾梅利姆头镖”继续作为搜索别名。街机专属扩展融合形态不进入角色池。
- **捷德系列（只收录 TV 登场）**：原始、刚燃、机敏、豪勇、尊皇，以及在《泽塔奥特曼》TV 中登场的银河初升。电影专属终极形态与街机/游戏专属融合升华形态不收录。
- **梦比优斯系列**：勇者形态、燃烧勇者、凤凰勇者均保留；梦比优斯奥特曼·无限形态虽然首次登场于电影，但属于多位奥特战士真正融合而成的合体形态，因此按合体规则保留。
- **真正的合体 / 融合奥特战士**：超级奥特曼泰罗、雷杰多、赛迦、梦比优斯奥特曼·无限形态、银河维克特利奥特曼、罗布、格罗布、泰迦奥特曼·三重斯特利姆形态、令迦、真理特利迦等。这里的“合体”指多位奥特战士本体或力量真正合为一体，不把街机/游戏中单纯借卡片组合出的形态算进去。
- **近年 TV 形态**：继续收录泽塔、特利迦、德凯、布莱泽、亚刻的 TV 强化形态，并采用授权中文商品页中的“法多兰盔甲”“索利斯装甲”“露娜装甲”等名称；《欧米伽奥特曼》收录雷金斯装甲、特里加隆装甲、瓦尔根斯装甲，以及 2026 年的加梅顿装甲。旧译名继续作为兼容别名。

> 收录原则：**TV 正剧 / 电视特别篇实际登场的形态 + 真正的奥特战士合体形态**。明确排除街机、游戏、舞台剧，以及仅在电影中登场且不属于合体形态的独立强化形态。

### 使用方式

```text
@机器人 今日奥特曼
@机器人 我的奥特曼
@机器人 奥特曼图鉴
@机器人 欧布原生
@机器人 捷德尊皇
@机器人 梦比优斯凤凰勇者
@机器人 雷基尼斯装甲
@机器人 格罗布
```

`今日奥特曼` 每个 QQ 用户每天只会保存一个结果；当天重复抽取会返回已经保存的那一位，不会重复写入数据库。`我的奥特曼` 会按历史记录统计累计获得次数、不同角色/形态数量和出现次数最多的本命奥特曼。图鉴查询不会写入收藏。

角色图片现在按“**圆谷/官方专图 → 形态专用百科文件 → 百度百科对应形态代表图 → 维基百科 PageImages / 条目内具体形态文件 → 明确失败**”的顺序解析。奥特曼图片与 B站视频功能完全分离，**绝不使用 B站视频封面充当奥特曼角色图**。独立形态也不会静默复用本体普通形态图：百科兜底必须让条目标题、图片标签、文件名或紧邻上下文明确命中具体形态名；例如“捷德奥特曼·尊皇形态”会优先读取 TAMASHII WEB 的官方单体图，旧的萌娘百科双人横幅只在官方图不可用时回退。横幅来源在图鉴卡中会完整保留并放到弱化背景上，不再从中间硬裁。仍找不到可靠图时只发资料并说明暂无可靠图片。

## 机器人语气与记忆

- `prompts/persona.txt`：System Prompt 角色卡。
- `prompts/examples.jsonl`：少量高质量语气示例。
- `MAX_CONTEXT_MESSAGES`：短期上下文长度。
- `/记忆开启`：用户明确开启后才保存内存中的短期上下文。
- `@机器人 记住：内容`：明确保存最多 20 条、每条最多 300 字的长期记忆，持久化在 SQLite 中。
- 密码、Token、密钥、身份证、银行卡、验证码和 Cookie 等敏感内容会被拒绝保存。
- 普通群聊风格学习只记录匿名统计值与固定安全口头语计数。经授权后，机器人会按群和成员保存最近 100 条公开文本样本，用于夺舍后的上下文和语气学习；图片只保存 OneBot 可重新发送的引用，不保存图片内容。可以使用“删除语气”清除指定成员的样本、图片引用和摘要。
- 随机夺舍使用当天发言达到 15 条的真实群成员，并在结果中显示“本群今日随机成员”；指向夺舍通过第二个 @ 直接指定成员，不检查发言数。
- 两种模式都使用真实群名片（没有群名片时使用 QQ 昵称）。机器人会以该名字自我介绍，但始终标注为娱乐扮演，不会声称自己是真人或代表该成员。
- 被选中的成员或群管理员可发送 `@机器人 退出`；退出只结束当前状态，当天仍可重新抽取或重新指定。
- `@机器人 你现在是谁` 等身份问题会直接读取 SQLite 中的当天夺舍状态，不经过 LLM，因此服务重启后仍能稳定返回正确成员名。
- 机器人默认名为“小丛雨”。普通 AI 对话会注入最高优先级的夺舍身份，并在发送前校验名字，避免模型重新自称默认名字。
- 小丛雨默认使用自然、平实的群聊口吻；“乐乐”和颜文字仅在合适时偶尔使用，不会作为固定尾缀。夺舍时以目标成员历史风格摘要和近期真实句式为主，支持在当前状态中声明目标成员别名。
- 开启 `恋爱模式` 后会从自然、轻微害羞的正常聊天开始；同一群、同一用户在 45 分钟内持续进行成功的 AI 对话后，才会逐步变得更亲近或偶尔娇羞。技术、事实、求助、悲伤和争执话题始终以正常、清楚、可靠的语气回答，不再使用“杂鱼~杂鱼~”固定口癖。会先读取最近群聊背景来调整认真程度和玩笑浓度；用户表达难过、焦虑或请求安慰时，回复至少 50 个汉字，说明情绪原因，给出温柔的正向安慰和一个可执行的小步骤。
- 夺舍期间会持久化最近 20 轮被 @ 对话作为群内共享角色上下文，并记录每句话的说话者群名片，方便正确推理人物和辈分；切换目标或退出时自动清除。
- 每次随机或指向夺舍都会重新读取目标成员近期群历史，并结合数据库中最近公开片段刷新语气摘要；普通对话最多等待 3 秒让学习完成。历史接口临时不可用时，会使用已保存的群内样本回退。
- 夺舍风格会统计目标成员近期消息中图片或表情的使用频率，也会缓存少量可由 OneBot 重新发送的图片引用。只有在成员明确发送 `@机器人 模仿他/她说话` 时，机器人会在文字回复后随机发送一张该夺舍对象近期实际发过的图片；普通对话不会自动发送。机器人不保存或识别图片内容。目标成员实际用过的少量群聊怪话可偶尔自然使用，但不会据此编造人物关系。
- 使用逗号形式 `@机器人 记住，内容` 可保存最多 50 条群共享事实，切换夺舍对象后仍保留；以“你是、你叫、你的”开头时，会自动绑定为保存当时的机器人或夺舍显示名，避免以后切换对象后主语错乱。冒号形式 `记住：内容` 仍是个人长期记忆。
- `@机器人 say my name` 或 `@机器人 我叫什么名字` 会返回提问者自己的群名片，不会返回夺舍对象。

完整规则和示例见 [夺舍模式说明](docs/POSSESSION.md)。

建议先调整角色卡和示例，不要默认把群聊内容用于训练。训练或持久保存聊天记录前，应取得必要授权并移除 QQ 号、姓名、手机号等个人信息。

## 自定义插件

在 `app/plugins/` 新建模块，并把模块路径加入 `.env`：

```env
PLUGIN_MODULES=app.plugins.hello,app.plugins.weather
```

最小插件示例：

```python
from app.plugins.registry import PluginSpec, registry


async def weather(context):
    city = context.args or "北京"
    return f"正在查询 {city} 的天气"


registry.register(
    PluginSpec(
        name="weather",
        commands=("/天气",),
        description="/天气 城市 —— 查询天气",
        handler=weather,
    )
)
```

插件上下文包含群号、用户 QQ、原始 OneBot 事件、管理员状态和命令参数。更多内容见 [插件开发](docs/PLUGIN_DEVELOPMENT.md)。

## JWT 管理 API

```text
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
GET  /api/auth/me

GET  /api/admin/groups/{group_id}
PUT  /api/admin/groups/{group_id}
GET  /api/admin/groups/{group_id}/blacklist/{user_id}
PUT  /api/admin/groups/{group_id}/blacklist/{user_id}
GET  /api/admin/groups/{group_id}/plugins/{plugin_name}
PUT  /api/admin/groups/{group_id}/plugins/{plugin_name}
```

默认管理员由 `.env` 中的 `ADMIN_USERNAME` 与 `ADMIN_PASSWORD` 在首次启动时创建或同步。完整流程见 [JWT 鉴权](docs/AUTH_JWT.md) 和 [管理 API](docs/ADMIN_API.md)。

## Docker 部署

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

当前 Compose 启动机器人服务和 Redis，不包含 Windows NapCat。NapCat 在宿主机运行时，Docker 中的机器人通常应配置：

```env
ONEBOT_API_BASE=http://host.docker.internal:3000
REDIS_URL=redis://redis:6379/0
```

NapCat HTTP Client 仍可通过宿主机映射端口向 `http://127.0.0.1:8000/onebot/webhook` 上报事件。生产部署建议使用反向代理、TLS、持久卷和受限网络。详见 [Docker 部署](docs/DOCKER_DEPLOYMENT.md)。

## 测试与质量检查

普通测试不会访问外部 API，也不会向 QQ 群发送消息。推荐直接双击 `测试项目.bat`，或执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m compileall -q app tests
```

pytest 的临时目录固定在项目内的 `.test-tmp/pytest`，避免 Windows 用户临时目录 ACL 异常导致 `PermissionError: [WinError 5]`。

当前本地非 live 测试基线：`85 passed, 15 skipped`。其中外部真实服务测试默认跳过；需要显式添加 `--live` 才会调用已配置的 LLM、OneBot 等外部服务。

真实接口健康检查会读取 `.env` 并调用外部服务，可能消耗少量额度：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\live --live -v
```

可以用 `-k zhipu`、`-k groq`、`-k cat`、`-k pig` 或 `-k onebot` 单独检查服务。详见 [自动化测试](docs/TESTING.md)。

## 项目结构

```text
qq-chatrobot/
├─ app/
│  ├─ api/          # JWT 与管理 API
│  ├─ core/         # 鉴权、限流、幂等与安全工具
│  ├─ db/           # SQLite 数据层
│  ├─ llm/          # Provider、并发、降级与短期记忆
│  ├─ plugins/      # 插件注册表与内置插件
│  ├─ config.py
│  └─ main.py       # FastAPI 与 OneBot Webhook
├─ docs/            # 架构、鉴权、部署和接入文档
├─ prompts/         # 角色卡和语气示例
├─ scripts/         # 连通性检查与 Windows 启动脚本
├─ tests/           # 单元测试和真实服务测试
├─ docker-compose.yml
├─ Dockerfile
└─ pyproject.toml
```

## 安全说明

- `.env`、数据库、虚拟环境和缓存目录已加入 `.gitignore`，上传前仍应执行 `git status` 复查。
- 不要提交 API Key、QQ Cookie、WebUI 登录密钥、JWT 密钥或 OneBot Token。
- NapCat WebUI 和 OneBot Server 建议只监听 `127.0.0.1`；需要远程访问时使用防火墙、反向代理与 TLS。
- `APP_ENV=production` 时必须使用至少 32 字符的随机 `JWT_SECRET_KEY` 和至少 12 字符的管理员强密码。
- 免费 API 不提供永久 SLA，应设置超时、限流、备用服务和费用告警。
- QQ 自动化和第三方协议适配可能带来账号风险，请遵守 QQ、NapCat 及相关 API 服务的规则。

## 常见问题

### NapCat 3000 正常，但机器人调用 send_group_msg / get_group_msg_history 返回 502

如果 PowerShell 直接访问 `http://127.0.0.1:3000/get_login_info` 正常，而机器人日志中的 Python/httpx 请求却对 `send_group_msg` 或 `get_group_msg_history` 返回 502，常见原因是 Clash、系统代理或环境代理把本机 OneBot 请求也接管了。

机器人现在对所有 OneBot/NapCat HTTP 请求强制使用 `trust_env=False`，即这些 `127.0.0.1:3000` 请求不会读取 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 等代理环境，也不会经过代理；B站、联网搜索等外网请求不受这个改动影响。

### 没有报错，但机器人突然不回复

新版将限流拆成两层：所有入站消息只受高额度的防刷保护；真正进入通用 LLM 聊天时才使用较严格的 AI 限流。像 `/help`、群记忆关系直答等本地操作不会消耗 AI 聊天额度。触发任何限流时，机器人会在冷却周期内至少提示一次，而不是静默丢弃。

默认值可在 `.env` 调整：

```dotenv
INGRESS_USER_RATE_LIMIT_PER_MINUTE=60
INGRESS_GROUP_RATE_LIMIT_PER_MINUTE=300
LLM_USER_RATE_LIMIT_PER_MINUTE=20
LLM_GROUP_RATE_LIMIT_PER_MINUTE=120
RATE_LIMIT_NOTICE_COOLDOWN_SECONDS=10
```

另外，普通文本发送现在会检查 NapCat/OneBot 的返回状态；如果 OneBot 返回 `status=failed`，服务端会记录真实错误，不再把“HTTP 请求发出去了”误当成“QQ群消息发送成功”。

### 本地 `https://127.0.0.1:3000` 无法访问

NapCat 默认提供普通 HTTP，请使用 `http://127.0.0.1:3000`。`6099` 是 WebUI，`3000` 是 OneBot API。

### `provided QQ path is invalid`

通常是旧版 Shell 启动方式未找到新版 NTQQ。建议卸载旧 Shell 启动方式，安装最新版 QQ 和 NapCatQQ Desktop，再从 Desktop 客户端添加并启动机器人账号。

### 提示账号已登录，无法重复登录

不要同时启动多个 NapCat，也不要让机器人 QQ 同时登录普通 QQ 客户端。完全退出相关 QQ/NapCat 进程后，只启动一次。

### 智谱返回 `429 / 1305`

这通常表示当前免费模型访问量过大。稍后重试、降低并发、切换其他可用模型，或确保 Groq 备用 Provider 已配置。

### 能收到消息但机器人不回复

检查 NapCat HTTP Server `3000` 是否启用、`ONEBOT_ACCESS_TOKEN` 是否一致，以及 `ONEBOT_SELF_ID` 是否为实际登录的机器人 QQ。

### 完全收不到群消息

检查 NapCat HTTP Client 是否启用、URL 是否为 `/onebot/webhook`、消息格式是否为 `array`，以及 Client Token 是否与 `ONEBOT_WEBHOOK_TOKEN` 一致。

### NapCat 提示 `ECONNREFUSED 127.0.0.1:8000`

这表示 NapCat 正常尝试上报消息，但 FastAPI 没有在 `8000` 端口监听。先双击项目内的 `安装机器人开机自启.bat` 注册并启动守护任务，再访问 `http://127.0.0.1:8000/health/live`，应返回 `{"status":"ok"}`。NapCat 本身未运行时，再双击桌面的 `一键启动QQ机器人.bat`。

### 联网搜索需要申请 API Key 吗

当前实现使用 Bing RSS 搜索入口，不需要额外 Key。显式 `@机器人 搜索 关键词` 始终搜索；开启 `AUTO_WEB_SEARCH_ENABLED=true` 后，普通 @ 问题涉及明显时效信息，或模型明确表示需要查证时，也会自动搜索。发送给 Bing 的是最多 120 字的精炼查询，不会把长期记忆和完整对话上下文发送给搜索服务。搜索服务可能调整访问策略，因此代码保留了超时和失败提示；LLM 只根据返回的摘要总结，并在回复末尾附上来源链接。

### 奥特曼形态图片为什么会跟丢

发布前可以跑完整图片体检（逐个解析当前图鉴图片、检查尺寸/空白图并标出完全重复项）：

```powershell
python -m scripts.audit_ultraman_images --report data/ultraman-image-audit.json --require-all
python -m scripts.audit_anime_character_images --report data/anime-character-image-audit.json --require-all
```

像“欧布奥特曼 暗耀形态”这类常用中文叫法现在会映射到图鉴中的对应形态，并直接进入图片卡片流程；不会再掉回普通聊天。机器人还会记住当前用户刚查看的奥特曼形态，因此紧接着发送“图片呢”“对的我要看图片”“给我看看图”等短追问时，会重新发送刚才那个具体形态的图片，而不是让 LLM 回答“没有存图功能”。

欧布这个形态在图鉴中正式显示为“欧布奥特曼·暗耀形态”。日文原名是“サンダーブレスター（Thunder Breastar）”；旧版本误写的“雷霆肩章”只保留为兼容别名，不再对外展示。

整套形态名现已做过一次统一校对：对外优先使用授权中文资料中的常用名称，日文原名、英文名和旧版本中文名进入别名索引。例如赛罗的 Beyond 对外显示为“无限形态”，银河使用“斯特利姆形态”，艾克斯使用“超越形态”，欧布使用“重光 / 暴炎 / 疾风 / 暗耀 / 煌闪 / 智勇”，亚刻使用“索利斯 / 露娜 / 银河装甲”。数据库启动时会自动迁移旧收藏名称，不丢历史抽取记录。

图片方面，奥特曼图鉴与 B站视频搜索彻底分离：**奥特曼角色/形态图片绝不使用 B站视频封面兜底**。所有独立形态都禁止静默回退到普通本体图；帝纳斯、诺亚、雷杰多、贝利亚早期形态、托雷基亚早期形态等共享相关角色页面的特殊角色也纳入同一保护。没有可靠的对应图片时会明确提示暂无可靠图片，宁可不发，也不会拿视频封面或其他形态冒充。

### 奥特曼百科图片怎么选

对于圆谷角色页没有独立形态图的情况，机器人会继续查百科，而不是直接判定“无图”。百度百科优先匹配具体形态条目或母条目中带有明确形态标签的图片；维基百科则优先匹配条目内部文件名明确对应形态的图片，其次才使用标题本身就是该形态的 PageImages 代表图。这样可以处理“闪耀迪迦”“捷德银河初升”“捷德尊皇”等有百科代表图、但圆谷通用角色页可能只展示基础形态的情况。

为了避免误图，母条目的普通头图不会被当作强化形态图。图片下载后还会检查响应类型、尺寸和文件大小；不满足条件就继续尝试下一来源。已确认的常见问题条目会优先直达百科页，减少每次查询的等待时间。

### 二次元角色图片与萌娘百科

二次元角色图片会保存可追溯的来源条目、原图地址和匹配证据；发送图片时，若有可公开访问的来源页，图片说明会附上“图片来源”链接。缓存命中也会保留这些信息，旧缓存则明确标记为 `legacy/unknown`。图片解析采用分层兜底：先尝试萌娘百科角色页（开启后），再尝试 VNDB/Bangumi/AniList，之后才进入 Wikipedia、官方网页和百度/Bing 搜索；不会因为单个来源超时就直接判定角色没有图片。同一来源组内会比较已下载候选的实际像素尺寸，优先选择更高清的图片，而不是简单采用最先返回的缩略图。

角色资料也会先读取萌娘百科角色摘要和联网检索结果，再交给 LLM 做有依据的改写，输出约 200～320 字的“角色简介”和“角色背景”。LLM 只整理已提供的资料，不凭空补写设定；LLM 不可用时仍会使用经过压缩改写的公开摘要，并附资料来源链接。

萌娘百科来源默认关闭。萌娘百科的用户协议对机器人抓取及站外图片使用有额外限制；只有在你已获得部署场景所需许可后，才在 `.env` 设置 `MOEGIRL_IMAGE_PROVIDER_ENABLED=true`。开启后，机器人只会通过其公开 MediaWiki API 严格核验角色条目标题和作品证据，不会调用被禁用的 `imageinfo` 接口。

如果要扩充 Galgame/二次元角色，不建议让机器人启动时盲目抓整站；可在确认拥有相应使用许可后，按你选择的分类或关键词执行：

```powershell
python -m scripts.import_moegirl_characters --i-have-permission `
  --category "Galgame角色" --limit 1000
python -m scripts.import_moegirl_characters --i-have-permission `
  --search "千恋万花 角色" --series "《千恋＊万花》" --limit 50
```

导入结果会合并到 `app/data/anime_characters_extra.json`，重启机器人后进入“角色图鉴”；简称仍会走候选确认，不会静默发错角色。

### B站视频搜索怎么选结果

使用 `@机器人 播放视频 关键词`。机器人会同时参考 B站“综合排序”和“最多播放”结果，再用标题、简介、标签与关键词做二次相关度计算；内容相关度优先，在相近相关度档位中再优先播放量更高的视频。B站视频卡片**必须使用该视频自己的封面**，没有封面的候选不会被选中。正常情况下发送 OneBot `share` 小卡片，卡片包含视频封面、标题、UP 主、播放量、时长与链接；如果 QQ/NapCat 当前不接受分享卡片，会自动降级为标题 + 链接文本。

B站网页搜索存在风控，代码会先访问 B站首页获取匿名 Cookie，并在分类搜索失败时尝试综合搜索兜底。该功能不需要 API Key，但平台接口策略变化时可能暂时不可用。

### 点歌为什么有时只能打开页面

机器人优先发送网易云 `163` 音乐卡片，NapCat 或 QQ 不支持该卡片时会降级为网易云页面链接。网易云搜索入口是非官方网页接口，平台调整接口时可能暂时不可用；完整播放仍受平台版权、地区和账号权限影响。详见 [音乐点歌](docs/MUSIC.md)。

## 文档索引

- [架构设计](docs/ARCHITECTURE.md)
- [OneBot 接入指南](docs/ONEBOT_SETUP.md)
- [Windows 一键启动](docs/ONE_CLICK_START.md)
- [LLM 集成与并发](docs/LLM_INTEGRATION.md)
- [JWT 鉴权](docs/AUTH_JWT.md)
- [管理 API](docs/ADMIN_API.md)
- [插件开发](docs/PLUGIN_DEVELOPMENT.md)
- [自动化测试](docs/TESTING.md)
- [夺舍模式说明](docs/POSSESSION.md)
- [音乐点歌](docs/MUSIC.md)
- [丛雨图片、表情和语音素材](docs/MURASAME_MEDIA.md)
- [Docker 部署](docs/DOCKER_DEPLOYMENT.md)

## 开源许可证

本项目采用 [MIT License](LICENSE)。你可以自由使用、复制、修改和分发本项目，但需要保留原始版权声明与许可证文本。本项目按“原样”提供，不附带任何形式的担保。

## 后续计划

- 图片缓存、备用图片 Provider 与每日配额。
- 管理后台、日志审计、Prometheus 指标和告警。
- PostgreSQL 数据层与数据库迁移。
- 插件热加载、定时任务和群级可视化配置。

欢迎通过 Issue 或 Pull Request 提交建议与改进。
