# 丛雨图片、表情和语音素材

机器人不会自动下载或训练丛雨素材。将已获授权的文件放入配置的
`MURASAME_ASSET_DIR`（默认 `./data/murasame_assets`）下：

- `images/`：发送“丛雨图片”，支持 PNG/JPG/WebP/GIF。
- `emotes/`：发送“丛雨表情”，支持 PNG/JPG/WebP/GIF。
- `voices/<音色ID>/`：发送“丛雨语音”，支持 MP3/WAV/OGG/AMR/SILK/M4A；也可放在 `voices/` 作为默认素材。

`启动语音`是每位用户独立的开关；启动后机器人会先显示音色菜单，再用
`选择音色 <ID>`（或 `切换音色 <ID>`）选择音色；`关闭语音` 可随时关闭。服务端还必须配置
`VOICE_ENABLED=true` 和 `VOICE_API_URL` 才会把普通文字回复转成语音。
如果只想发送本地片段，不需要配置 TTS。

多角色/中日音色通过 `VOICE_PROFILES_JSON` 配置，例如：

```json
{"default":{"label":"默认中文","voice":"cn_voice","language":"zh"},"murasame":{"label":"小丛雨（中文/English 同一音色）","voice":"murasame","language":"auto","target_language":"auto","prompt_lang":"zh","prompt_text":"不要把我当小孩子","ref_audio_path":"data/murasame_voice_dataset/audio/murasame_0001.mp3"},"murasame_ja":{"label":"小丛雨（日语）","voice":"murasame_ja","language":"ja","target_language":"ja","prompt_lang":"ja","prompt_text":"","ref_audio_path":"data/murasame_assets/voices/murasame/MUR_SYS_01.wav"}}
```

群里发送“音色列表”查看菜单，发送“选择音色 murasame_ja”（或“切换音色 murasame_ja”）切换。这里的
`voice`、`model` 和语言字段由已部署的 TTS 服务解释；只有确认服务支持额外
语言字段时才设置 `VOICE_SUPPORTS_LANGUAGE_FIELDS=true`。项目不会自动把录音
训练成音色，也不会声称已经完成训练。若要训练，请先确认录音授权，并在
外部服务完成训练后把服务提供的 voice/model ID 写入配置。

## GPT-SoVITS 本地后端

GPT-SoVITS 的 `api_v2.py` 默认监听 `9880`。本项目会把
`target_language=auto` 的文本自动标记为中文或英语，并始终使用同一条
`ref_audio_path`，让中英文保持同一个“小丛雨”参考音色。配置示例：

```env
VOICE_ENABLED=true
VOICE_PROVIDER=gpt_sovits
VOICE_API_URL=http://127.0.0.1:9880
```

GPT-SoVITS 服务需要先独立启动并加载模型；参考音频和训练模型均只保存在本机。

录音不会被机器人自动拿去训练或克隆音色。若要接入音色模型，先确认录音者、
角色相关授权，再把已经部署好的 TTS 服务配置为 OpenAI 兼容的
`/v1/audio/speech` 接口。

可优先从柚子社《千恋＊万花》[官方 MOVIE 页面](https://www.yuzu-soft.com/products/senren/movie.html) 查看公开的角色歌曲/短剧和
系统语音入口，再按其授权条款取得可用素材；不要把第三方转载视频直接转成
群机器人音频。
