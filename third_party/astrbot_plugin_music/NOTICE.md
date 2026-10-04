# 来源说明

- 上游：`Zhalslar/astrbot_plugin_music`
- 固定快照：commit `5a6c97d04cf0801893d464fe03cb221d7ab76060`
- 上游许可证：MIT，全文见本目录 `LICENSE`
- 借鉴文件：`core/song_renderer.py` 的候选卡片布局与 `core/lyrics_renderer.py` 的歌词排版
- 本地改动：改用项目的 `NeteaseTrack`；只接受调用方传入的封面字节；限制封面字节和像素数；不使用 BeautifulSoup、上游播放器、下载器或发送器；歌词清理和分页设有明确上限；字体使用 `help_menu._find_font_path()`
