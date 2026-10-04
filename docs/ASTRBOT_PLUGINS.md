# AstrBot 插件建议

目录审阅日期：2026-10-04。遍历当前市场的 2,368 条名称、描述、仓库、分类、标签和平台声明；重点候选另外阅读 README 和元数据。没有逐一审计所有插件源码，也没有安装本页候选。

本机 AstrBot 4.28.2 优先请求市场 API，失败才使用 GitHub 后备目录。后备有 1,329 条、1,322 个不同仓库；两源合并去重为 2,420 个，但运行时不会合并两个目录。只有 669 条市场记录填写平台声明，其中还有无效值，兼容性需要逐个核实。

## 按当前需求排序

| 功能 | 建议 | 接入安排 |
| --- | --- | --- |
| 随机二次元图片 | 继续使用已安装的 `astrbot_plugin_get_px` | 已有转发入口和动态菜单，保留角色图鉴、抽取和收藏 |
| 图片与 GIF 处理 | [图片工具箱](https://github.com/lirundong093-glitch/astrbot_plugin_pic_toolbox) | 可补翻转、调速、摸头等功能；声明支持 OneBot，微信待验证；先核图片大小、帧数与并发限制 |
| 点歌、歌词、歌单 | [Zhalslar 点歌插件](https://github.com/Zhalslar/astrbot_plugin_music) | 复用候选列表等交互，搜索与会员音源接本机桥；保留歌曲别名，不直接覆盖现有音源逻辑 |
| 游戏资料 | [Steam 查价](https://github.com/penguin-madagascar/astrbot_plugin_steam_price_heybox) | 可选的查价、史低、地区比价；不替代现有角色图鉴和收藏 |
| 重复命令 | [anti_repeat](https://github.com/yuanshen-scaramouche/star) | 备选；先核处理顺序与现有限流，避免两次拦截；平台未明确声明 |

## 暂时不叠加的功能

- 故事：项目已复用 AstrBot 的模型提供商实现，不需要另一套故事插件。
- 长期记忆：已有记忆功能。[简单长期记忆](https://github.com/piexian/astrbot_plugin_simple_long_memory) 需要知识库与 embedding 配置，先核重复功能和资源开销。
- 本地 embedding、GPU 生图、语音、翻唱：保持暂停，等音频任务恢复后再处理。
- 微信登录或桌面转发：当前个人微信已接通，不叠加第二条登录链路；Gewechat、WechatPadPro 专用插件不能直接按当前适配器通用。

启用的新插件只有在当前会话、平台和权限下可用，才会进入图片/文字菜单。未安装的推荐不会显示成已上线功能。账号、cookie、密钥及个人素材继续留在本机。

市场来源：[官方 API](https://api.soulter.top/astrbot/plugins)、[官方后备目录](https://github.com/AstrBotDevs/AstrBot_Plugins_Collection/blob/main/plugin_cache_original.json)。
