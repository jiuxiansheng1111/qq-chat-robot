# 丛雨图片、表情和角色语音

机器人不会自动下载或训练丛雨素材。将已获授权的文件放入配置的
`MURASAME_ASSET_DIR`（默认 `./data/murasame_assets`）下：

- `images/`：发送“丛雨图片”，支持 PNG/JPG/WebP/GIF。
- `emotes/`：发送“丛雨表情”，支持 PNG/JPG/WebP/GIF。

`启动语音`是每位用户独立的开关。群内发送“可用角色”查看菜单，发送
`选择角色 小丛雨` 选择角色；`关闭语音` 可随时关闭。当前“小丛雨”由同一角色档案处理
中文、日语和英语，按文字自动识别语言，不再提供单独发送本地语音素材的指令。

后续新增角色时，每个角色都使用一个统一档案；中 / 日 / 英支持通过 `languages` 标注，例如：

```json
{"murasame":{"label":"小丛雨","voice":"murasame","language":"auto","languages":"中 / 日 / 英","target_language":"auto","prompt_lang":"zh","prompt_text":"不要把我当小孩子","ref_audio_path":"data/murasame_voice_dataset/audio/murasame_0001.mp3"}}
```

群里只显示角色名称，不显示内部 ID 或部署字段。项目不会自动把录音
训练成音色，也不会在后台隐式训练。若要在本机显式训练，请先确认录音授权，
再运行 `powershell -ExecutionPolicy Bypass -File .\scripts\train_murasame_voice.ps1`；
该脚本会复用已完成的预处理并在 CPU 上生成本地 SoVITS 权重。训练模型和录音都在
`.gitignore` 覆盖的 `data/` 下，不会提交到 GitHub。

## GPT-SoVITS 本地后端

GPT-SoVITS 的 `api_v2.py` 默认监听 `9880`。本项目会把
`target_language=auto` 的文本自动识别为中文、日语或英语，并始终使用同一条
`ref_audio_path`，让三种语言保持同一个“小丛雨”参考音色。配置示例：

```env
VOICE_ENABLED=true
VOICE_PROVIDER=gpt_sovits
VOICE_API_URL=http://127.0.0.1:9880
GPT_SOVITS_AUTO_START=auto
GPT_SOVITS_ROOT=../qq-chatrobot-voice/GPT-SoVITS
GPT_SOVITS_PYTHON=../qq-chatrobot-voice/GPT-SoVITS/.venv/Scripts/python.exe
GPT_SOVITS_TTS_CONFIG=GPT_SoVITS/configs/tts_infer.yaml
# 质量训练完成并通过语音检查后再填写；留空使用基础模型。
GPT_SOVITS_SOVITS_WEIGHTS=./data/murasame_voice_dataset/SoVITS_weights_quality/murasame_voice_e10_s1660.pth
```

执行 `scripts\\start_all.ps1` 时，如果本地目录、独立 Python 环境和模型配置都存在，
脚本会自动启动 `api_v2.py` 并等待 `9880/tts` 就绪；缺少依赖时只记录警告，不会阻止
QQ 机器人启动。参考音频和训练模型均只保存在本机。

录音不会被机器人自动拿去训练或克隆音色。若要接入音色模型，先确认录音者、
角色相关授权，再把已经部署好的 TTS 服务配置为 OpenAI 兼容的
`/v1/audio/speech` 接口。

可优先从柚子社《千恋＊万花》[官方 MOVIE 页面](https://www.yuzu-soft.com/products/senren/movie.html) 查看公开的角色歌曲/短剧和
系统语音入口，再按其授权条款取得可用素材；不要把第三方转载视频直接转成
群机器人音频。
