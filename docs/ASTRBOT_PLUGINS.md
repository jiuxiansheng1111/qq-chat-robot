# AstrBot 图片插件

## 当前安装

本机私有 AstrBot 实例使用官方索引列出的 [画境拾珍](https://github.com/shitianyaa/astrbot_plugin_get_px) v3.5.1。AstrBot 4.28.2 已成功加载该插件；本机已配置 Pixiv 登录，凭据保留在私有配置中，Lolicon 的请求固定为普通分级 `r18=0`。

- 源码仓库：[画境拾珍主仓库](https://github.com/shitianyaa/astrbot_plugin_get_px)
- 固定 commit：`866d6cd70d0575a66f16742a581dc91d26849e1b`
- 下载地址：[固定 commit ZIP](https://codeload.github.com/shitianyaa/astrbot_plugin_get_px/zip/866d6cd70d0575a66f16742a581dc91d26849e1b)
- ZIP SHA-256：`5EDB30E3B55ED34E940F3419EE5291D0E767A7AD81554C38EBD897A5EE0FF469`
- MIT 许可证；上游 v3.5.1 tag object 未签名。

从仓库根目录为新实例安装固定源码：

```powershell
python scripts/install_astrbot_plugins.py
```

安装器只下载上述 commit，核对完整 ZIP SHA-256 和成员路径；解包暂存位于被 `.gitignore` 忽略的 `data/astrbot/plugin-review`，运行文件写入 `data/astrbot/instance/data/plugins/astrbot_plugin_get_px`。它会跳过上游的 `.github`、`docs`、`scripts`、`tests` 等开发目录；不会执行上游脚本、安装依赖、写 AstrBot 配置或启动服务。源 ZIP 保留在 review 目录。目标插件目录已存在时会报错且不覆盖。

随后在 AstrBot 的 Python 环境运行已有的 `scripts/configure_astrbot.py`。首次配置默认开启签到、关闭自然语言发图；已有实例保留面板中的两个开关，启动不会重新关闭它们。Pixiv token 默认为空。不要把账号、token、模型、`.env` 或 AstrBot 私有配置上传到 GitHub。

安装器不安装上游未锁定的 `requirements.txt`。当前 AstrBot 4.28.2 / Python 3.12.15 环境已经安装 `aiohttp 3.14.3`、`Pillow 12.3.0`、`pixivpy-async 1.2.14`、`lunar-python 1.4.8`，`pip check` 通过，框架插件加载验证通过。其他环境应在 AstrBot 自己的 Python 环境核对并安装这些依赖，不要因此修改本项目旧机器人环境的依赖上限。

## 发图与角色收藏

- 默认命令是 `/p 初音ミク 1` 按标签找图、`/p 3` 随机发图；在面板改名后按新名称发送，例如 `/来份二次元图片 初音ミク 1`。统一菜单读取当前名称。
- QQ 群可直接说 `来一份二次元图片`，也支持 `来份二次元图片`、`来一张二次元图片`，无需斜杠。本项目将这些请求交给插件原有随机 `/p` 处理器，并在上游停止事件后完成图片发送。它只接受短命令式请求，不接管提问、否定句或讨论句；AstrBot 未启用该插件时 helper 不转发。
- QQ 群也支持 `来份色图`、`来一份涩图`、`来张泳装图`、`来张短裙图`、`我想看黑丝女仆图` 等短句，每次一张。通用请求不随机附加服装要求；服装仅按偏好检索和排序，组合词采用 OR 搜索。入口复用原插件的图片源、指令权限、群白名单、限流和去重，不启用上游宽泛自然语言触发，不改 B 站私信行为。
- 题材插画保留上游 `r18=0`、年龄/学生校服元数据、安全词、黑名单及实际画面检查，允许普通成年动漫画风、泳装、丝袜、短裙和女仆装。普通成人制服不误作学生校服；大眼画风或未知精确年龄本身不算幼态。仍拒绝未成年、幼态、年龄不明、私密部位裸露和露骨性行为，视觉判断不能保证零误判。题材不再作为画面安全门槛。最多检查六个候选，每次最多一张，整次不超过90秒；不使用收费模型，检查未完成不发送，QQ发送超时不自动重发。
- 免费识图链为 `glm-4.6v-flash` → `glm-4.1v-thinking-flash` → `glm-4v-flash`，只在官方端点使用这三个已确认免费模型。`1305` 模型过载冷却5分钟，`1302` 账户模型限流冷却1分钟，网络超时冷却30秒，每个模型每轮只尝试一次；本模块识图并发为1。`400/1301` 和内容过滤结果直接拒绝该图，不换模型绕过。有效JSON前有说明或附加解释字段可以解析，但七个判断必须齐全且为布尔值，冲突或不完整结果不放行。
- 图片主源现在使用项目统一的代理/直连回退线路，连接失败后直接切到已登录Pixiv，并短暂冷却主源，避免同次请求立刻重试失败的线路。错误提示区分来源超时、识图未响应、繁忙及本批无合适图片。图片已发送后的记账失败仅记录日志，不再补发一条“图片失败”。`scripts/check_qq_anime_images.py --generic --model glm-4.1v-thinking-flash` 可只在本地检验，不连接或发送QQ消息。
- `/随机二次元角色`、`/今日二次元角色`、`/我的二次元角色`、`/角色图鉴` 和奥特曼相关角色、等级与收藏仍由本项目原功能负责，继续使用既有 roster、SQLite 的 `daily_anime_character` / `daily_ultraman` 表及 `app/data/anime_characters_extra.json`。这些数据不写入画境拾珍。

插件无 Pixiv token 时仍会访问默认 Lolicon API 获取图片；请求标签会发送给该服务，并下载它返回的图片地址。Lolicon 调用固定为 `r18=0`，插件还检查限制标记、安全词及本地黑名单。初始化会请求 GitHub 节假日数据；不要开放内置 R18 策略。

插件的图片索引、缓存和签到数据放在 AstrBot 插件数据目录，临时下载文件发送后会清理。其签到功能可能访问上游节假日、一言及页面资源接口。

## 签到和统一菜单

当前恢复的插件用于取图；签到和上游宽泛的自然触发保持关闭。QQ 的明确发图短句由本项目入口处理。以下签到功能需在插件面板开启后才可用：

- `签到`：每日签到；`/签到我的 状态`：金币、好感和连签。
- `/签到排行 月榜`：查看群排行，也支持今日、连签和累计。
- `/签到商店 查看`：商店；`/签到商店 主题列表`：可用主题。
- `来一份二次元图片`：当前已接通的 QQ 随机取图入口。

图片菜单按插件名称显示常用功能；`文字版菜单` 或 `画境拾珍菜单` 列出当前平台、权限及开关下的完整命令。管理子命令仅向有权限的用户展示。改名、停用指令或关闭签到后，菜单随之变化。它声明支持 QQ OneBot；微信菜单不冒充支持该插件。

QQ群回复中的国外来源链接改成文字名称。已审阅版本的搜图和签到背景说明原本就只列标题、作者或作品 ID，不展示作品直链。

## 来源

- [AstrBot 官方插件索引](https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/main/plugins.json)
- [画境拾珍主仓库](https://github.com/shitianyaa/astrbot_plugin_get_px)
- [v3.5.1 发布说明](https://github.com/shitianyaa/astrbot_plugin_get_px/releases/tag/v3.5.1)
- [固定 commit](https://github.com/shitianyaa/astrbot_plugin_get_px/commit/866d6cd70d0575a66f16742a581dc91d26849e1b)
- [AstrBot 4.28.2 源码：get_registered_star](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/context.py#L317-L321)；[StarMetadata 的 activated/star_cls 定义](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/star.py#L16-L49)


## 群报允许列表（本机配置）

群报只处理本机 `data/calendar_report_groups.json` 中允许的群。该文件是 JSON 字符串数组，例如 `["1000000001"]`；请在本机填写需要开放的群号。文件缺失、格式错误或为空时默认不开放。该文件不会提交到 Git。修改后重启对应机器人进程生效。
