# AstrBot 图片插件

## 当前安装

本机私有 AstrBot 实例使用官方索引列出的 [画境拾珍](https://github.com/shitianyaa/astrbot_plugin_get_px) v3.5.1。AstrBot 4.28.2 已成功加载该插件；配置保持 Pixiv token 为空，Lolicon 的请求固定为普通分级 `r18=0`。

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

随后在 AstrBot 的 Python 环境运行已有的 `scripts/configure_astrbot.py`。该配置脚本会把 `checkin_enabled` 和 `auto_trigger_enabled` 写为 `false`，保留空 Pixiv token。不要把账号、token、模型、`.env` 或 AstrBot 私有配置上传到 GitHub。

安装器不安装上游未锁定的 `requirements.txt`。当前 AstrBot 4.28.2 / Python 3.12.15 环境已经安装 `aiohttp 3.14.3`、`Pillow 12.3.0`、`pixivpy-async 1.2.14`、`lunar-python 1.4.8`，`pip check` 通过，框架插件加载验证通过。其他环境应在 AstrBot 自己的 Python 环境核对并安装这些依赖，不要因此修改本项目旧机器人环境的依赖上限。

## 发图与角色收藏

- 在 AstrBot 中仍可用 `/p 初音ミク 1` 按标签找图、`/p 3` 随机发图。
- 本项目入口将明确的短发图请求（例如“随机二次元图片”“来张二次元图”“来张随机二次元图片”“帮我来一张随机动漫图”）交给插件原有随机 `/p` 处理器。它只接受短命令式请求，不接管提问、否定句或讨论句；AstrBot 未启用该插件时 helper 不转发。
- `/随机二次元角色`、`/今日二次元角色`、`/我的二次元角色`、`/角色图鉴` 和奥特曼相关角色、等级与收藏仍由本项目原功能负责，继续使用既有 roster、SQLite 的 `daily_anime_character` / `daily_ultraman` 表及 `app/data/anime_characters_extra.json`。这些数据不写入画境拾珍。

插件无 Pixiv token 时仍会访问默认 Lolicon API 获取图片；请求标签会发送给该服务，并下载它返回的图片地址。Lolicon 调用固定为 `r18=0`，插件还检查限制标记、安全词及本地黑名单。初始化会请求 GitHub 节假日数据；不要开放内置 R18 策略。

插件的图片索引、缓存和签到数据放在 AstrBot 插件数据目录，临时下载文件发送后会清理。其签到功能可能访问上游节假日、一言及页面资源接口。保持 `checkin_enabled=false` 与 `auto_trigger_enabled=false`；目前只用 `/p` 和本项目的短句路由发图。

## 来源

- [AstrBot 官方插件索引](https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/main/plugins.json)
- [画境拾珍主仓库](https://github.com/shitianyaa/astrbot_plugin_get_px)
- [v3.5.1 发布说明](https://github.com/shitianyaa/astrbot_plugin_get_px/releases/tag/v3.5.1)
- [固定 commit](https://github.com/shitianyaa/astrbot_plugin_get_px/commit/866d6cd70d0575a66f16742a581dc91d26849e1b)
- [AstrBot 4.28.2 源码：get_registered_star](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/context.py#L317-L321)；[StarMetadata 的 activated/star_cls 定义](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/star.py#L16-L49)
