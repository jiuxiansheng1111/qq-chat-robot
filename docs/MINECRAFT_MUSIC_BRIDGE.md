# Minecraft 手机点歌桥（协议 1）

本后端复用 ChatRobot 已注入的主模型 `ask_primary`、网易云公开搜索与歌曲元数据，不修改任何 LLM 配置，不调用 QQ 发送接口。音乐准备使用匿名官方完整音源；明确的会员/付费标记、试听、空 URL、受限响应、非官方音频域名均拒绝，不回退到会员桥。

## 部署边界

ChatRobot 与 Minecraft **服务端**在同一主机运行。服务端请求固定的 `http://127.0.0.1:8012`，只经 Minecraft 已有玩家连接把结果和 OGG 数据发给请求玩家。朋友只需安装 Minecraft 模组，不需要各装 Python。独立服务器同样在服务器主机部署 ChatRobot；内网穿透/端口映射只开放 Minecraft 游戏端口，不开放 8012。

本后端保持 IPv4 loopback 监听，并用真实 TCP 对端地址检查每条音乐路由，不信任转发头。它不提供远程 HTTP 认证或公网站点。玩家权限、请求限频、分块传输、只给请求玩家回包和播放控制由 Minecraft 服务端/客户端实现；后端不维护每个玩家的当前歌曲、上一首/下一首或暂停位置。

插件仍通过原运行方式启用，不能把 `available` 健康字段理解成每首歌均可播放。`music.audio_availability` 为 `checked_per_track_on_prepare`；每首歌准备时都验证公开完整音源，平台授权和可用性可能变化。公开歌单可读也不代表其中歌曲都可播放。

## HTTP 接口

所有 POST 使用 `Content-Type: application/json`，顶层含整数 `"protocol": 1`，拒绝未知顶层字段。音乐 JSON 正文最多 128 KiB；原聊天/语音请求仍各自限制 20 KiB。响应不包含密钥、平台下载 URL、磁盘绝对路径。

Track 的必要字段如下。`id` 为 `netease:<1 至 20 位数字>` 或 `query:<64 位小写十六进制 SHA256>`；`title` 最多 120 字符且非空，`artist` 最多 100 字符。公开检索和歌单响应还可带 `duration_seconds`、`page_url`。

```json
{"id":"netease:29947399","title":"Pretty","artist":"Blurry Lights"}
```

以下是协议示例，模型检索文字和资产编号属于示例；不能把示例编号当作已缓存文件使用。

### POST `/minecraft/bridge/music/search`

`query` 为 1 至 240 字符；`playlist` 可选、最多 100 个 Track。主 LLM 可看到用户偏好歌单，但只负责理解和规划检索。实际候选由已有网易云搜索返回，或来自用户提交的偏好歌单。候选最多 10 项。模型无法通过返回任意 URL 让后端访问它。

```json
{"protocol":1,"query":"放一首我歌单里轻松一点的歌","playlist":[{"id":"netease:29947399","title":"Pretty","artist":"Blurry Lights"}]}
```

```json
{"protocol":1,"interpretation":"从喜欢的歌中选择一首轻松的曲目。","candidates":[{"id":"netease:29947399","title":"Pretty","artist":"Blurry Lights","duration_seconds":314,"page_url":"https://music.163.com/#/song?id=29947399"}],"suggested_id":"netease:29947399"}
```

`interpretation` 最多 320 字符。`suggested_id` 为空表示需要玩家选择，始终不会返回候选以外的建议编号。原主模型暂不可用或返回非法结构时明确报错，不改模型、不启用其他模型回退。搜索并发最多 2，整体超时 90 秒，主模型调用最多 50 秒。

### POST `/minecraft/bridge/music/playlist/import`

`value` 是 1 至 16384 字符的字符串；`source` 可选，取 `auto`（默认）、`netease`、`text`、`json`。

官方公开歌单支持 HTTPS 链接、`#/playlist?id=...` 形式或纯数字 ID；不处理登录、会员桥或第三方音频链接。

```json
{"protocol":1,"value":"https://music.163.com/playlist?id=3778678","source":"netease"}
```

逐行文本每行 `歌曲名 / 歌手` 或 `歌曲名 - 歌手`，也可只写歌名；允许官方 HTTPS 歌曲链接。空行和 `#` 开头的注释忽略。

```json
{"protocol":1,"value":"Pretty / Blurry Lights\n另一个歌名 / 歌手名","source":"text"}
```

JSON 字符串支持数组，或含 `name` 和 `tracks` 的对象；条目可以是歌曲文字、`{title,artist}` 或完整 Track。

```json
{"protocol":1,"source":"json","value":"{\"name\":\"喜欢的歌\",\"tracks\":[{\"title\":\"Pretty\",\"artist\":\"Blurry Lights\"}]}"}
```

```json
{"protocol":1,"name":"喜欢的歌","tracks":[{"id":"query:98edfe4c831f4de3aeeaf6526bcb4dc921548b2e57776daf6f13d82fcc7d1e04","title":"Pretty","artist":"Blurry Lights"}],"total":1,"imported_count":1,"truncated":false,"duplicates_skipped":0,"message":"手工条目在播放准备时才搜索确认；最多导入100首。"}
```

最多 100 首，按原顺序保留。手工条目按规范化后的歌曲/歌手生成稳定 SHA256 ID，重复 ID 去重，导入时不为每首歌发起搜索。超过 100 个不同条目时 `truncated:true`。公开歌单按官方 `trackIds` 顺序读取最多 100 首，并在需要时批量补齐元数据；`total` 为平台报告总数或实际条目数。公开导入整体超时 60 秒。歌单持久化由游戏负责，后端不替换或上传用户歌单。

### POST `/minecraft/bridge/music/prepare`

```json
{"protocol":1,"track":{"id":"netease:29947399","title":"Pretty","artist":"Blurry Lights"}}
```

已解析的网易云编号直接查询官方权威元数据，不根据客户端题名另搜歌曲。未解析 `query:` 条目才按题名/歌手搜索，并复用原有严格歌曲匹配；匹配不到时要求重新搜索选择候选，不随机播放。

```json
{"protocol":1,"asset_id":"a6f997222f574eab8a45cbd9fb205709","title":"Pretty","artist":"Blurry Lights","duration_seconds":314.24746,"format":"ogg_vorbis","audio_path":"/minecraft/bridge/music/audio/a6f997222f574eab8a45cbd9fb205709","size_bytes":3713427}
```

音源、下载结果实际时长、转码后格式都核对后才发布资产。每首最长 12 分钟；OGG Vorbis 目标 96 kbps、44100 Hz、双声道（格式校验最多双声道）；编码资产不超过 16 MiB。原始下载最多 100 MiB，并受原工程更严格的下载大小上限约束。临时文件保存在音乐缓存旁，完成/取消后清理。

并发准备最多 2；超额返回 503 `music_busy`。有效的已缓存网易云编号可直接复用，不占新的编码槽。整体超时 170 秒。客户端关闭本次 HTTP 连接会取消准备；已启动的 FFmpeg/ffprobe 使用现有 owned-worker runner 回收本次子进程树，不结束其他用户进程。

### GET `/minecraft/bridge/music/audio/{asset_id}`

只接受本服务生成的 32 位小写十六进制 opaque ID，返回 `Content-Type: audio/ogg`。服务端用 aiohttp `FileResponse` 流式发送，支持 Range；不会读整首音频到 Python 内存。客户端只使用返回的相对 `audio_path`，不把外部 URL/文件路径交给 GET 接口。

缓存位于 `data/minecraft_music/assets`，OGG 加小型 JSON 元数据总计最多 256 MiB，创建起 6 小时 TTL。空间不足时淘汰最旧的未在发送的资产；流式发送持有租约。过期/无效资产访问返回 404，过期文件在访问或后续缓存扫描时清理。缓存只是本机临时数据，不构成音乐发布或长期歌库。超过 TTL 后须重新准备，此时重新检查平台公开权限。

## 错误处理

音乐 JSON 错误均含 `protocol`、`error`、中文 `message`。典型状态如下：

| HTTP | error | 含义 |
| --- | --- | --- |
| 400 / 413 / 415 | `invalid_*` / `request_too_large` / `invalid_content_type` | 请求内容、长度或媒体类型无效 |
| 403 | `loopback_only` | 非本机 TCP 对端 |
| 404 | `track_unavailable` / `asset_expired` / `invalid_asset` | 曲目或缓存不存在，需重新选择/准备 |
| 409 | `public_audio_unavailable` / `track_unresolved` / `duration_limit` | 完整公开音源不可用、无法准确匹配或超过时长 |
| 502 | `model_response_invalid` | 主 LLM 返回结构无效 |
| 503 | `music_busy` / `cache_busy` / `music_unavailable` | 正忙、缓存无法腾出空间或环境不可用 |
| 504 | `music_timeout` | 本次操作超时 |

失败时游戏应保留歌单与原文字结果，显示中文错误，允许重新选择曲目。暂停/继续/上下首属于客户端现有播放控制；切歌和退出界面应取消旧请求并用票据丢弃迟到结果，不能让后到的旧资产覆盖新歌。

## 本次实现与检查记录（2026-10-18）

源码：`app/minecraft_music.py`、`app/minecraft_music_audio.py`；接入：`app/minecraft_bridge.py`。`main.py` 的窄热重载列表加入 `app.minecraft_music_audio`、`app.minecraft_music`，位于 `app.minecraft_bridge` 之前，沿用原 LLM 对象。

静态检查使用项目既有 Python/Ruff；针对性本地检查使用已有 AstrBot Python 的 aiohttp，无依赖下载。复现命令（仓库根目录）：

```powershell
.venv/Scripts/python.exe -m ruff check app/minecraft_music.py app/minecraft_music_audio.py app/minecraft_bridge.py main.py scripts/check_minecraft_music_bridge.py
.venv/Scripts/python.exe -m py_compile app/minecraft_music.py app/minecraft_music_audio.py app/minecraft_bridge.py main.py scripts/check_minecraft_music_bridge.py
& 'data/astrbot/runtime/tool-envs/astrbot/Scripts/python.exe' -X utf8 scripts/check_minecraft_music_bridge.py --output artifacts/minecraft-music-local-check
```

上述脚本只打开它自己随机分配的 loopback 端口，用明确标注的 LLM/平台替身验证结构和错误边界，用真实 FFmpeg、ffprobe、aiohttp 文件发送和取消回收验证传输链；不调用真实 LLM，不加载 `.env`，不发送 QQ，不操作原服务。可选的真实公开来源检查如下，仅在平台当前返回完整公开音源时下载和编码，不访问会员桥：

```powershell
& 'data/astrbot/runtime/tool-envs/astrbot/Scripts/python.exe' -X utf8 scripts/check_minecraft_music_bridge.py --output artifacts/minecraft-music-public-check --public-track 29947399 --public-playlist 3778678
```

已完成的证据：

- `artifacts/minecraft-music-20261018/local-bridge-check-final/results.json`：本地针对性检查通过；2 秒合成源真实转 OGG 后为 9896 字节，ffprobe 为 Vorbis / 44100 Hz / 双声道，HTTP 整文件与 Range 正常，租约释放、超时/繁忙、断开取消、TTL 与淘汰、子进程退出已检查。
- `artifacts/minecraft-music-20261018/local-bridge-check/public-sources.json`：真实官方公开歌单 `3778678`“热歌榜”总计 200 首，顺序导入 100，`truncated:true`；真实歌曲 `29947399` 为 Blurry Lights 的《Pretty》，完整公开音源转码后 3713427 字节、314.24746 秒，准备耗时 8.313 秒。保存音频为 `public-pretty-29947399.ogg`，可用本机播放器/ffprobe复查。
- `artifacts/minecraft-music-20261018/local-bridge-check/results.json`：歌曲 `1357628744` 官方元数据可读，但匿名播放接口没有完整公开音源，返回明确 `public_audio_unavailable`，未绕过授权。

首次本地检查定位并修复了手动调用 `FileResponse.prepare()` 造成 aiohttp 再次 prepare、破坏后续 keep-alive 请求的问题。最终使用框架生命周期管理发送，租约跟随真实文件发送阶段。

本次没有重启/重载用户的 AstrBot/ChatRobot，没有调用真实主 LLM，没有启动/关闭 Minecraft。插件重载后的自然语言真模型调用、Minecraft 实际播放、独立服务器/多人和穿透验收由主项目集成阶段继续核对，不能用本机 HTTP 成功替代这些验收。

本轮已获授权尝试正规插件重载，并只读核实 AstrBot 管理源码：`POST http://127.0.0.1:6185/api/plugin/reload`，正文为 `{"name":"astrbot_plugin_qqchat"}`，必须通过 `require_dashboard_user` 验证。现有初始面板密码在正规登录接口返回 401；可用 Computer Use 清单为空，未发现可安全复用的已登录面板。因此没有发起重载、没有重置管理员密码或伪造会话，原服务保持运行；原 8012 健康检查也确认音乐路由尚未加载。`artifacts/minecraft-music-20261018/live-service-check/reload.json` 记录 5 个现有配置/LLM 源码文件哈希均未改变，`readonly-status.json` 记录原服务仍可用及公开源保护复核。真实主 LLM 检索→原 8012 prepare→GET 的最后验收须待用户提供已授权登录会话后进行。
