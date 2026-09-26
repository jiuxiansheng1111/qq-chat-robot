# 丛雨图片、表情和角色语音

机器人不会自动下载或训练丛雨素材。将已获授权的文件放入配置的
`MURASAME_ASSET_DIR`（默认 `./data/murasame_assets`）下：

- `images/`：发送“丛雨图片”，支持 PNG/JPG/WebP/GIF。
- `emotes/`：发送“丛雨表情”，支持 PNG/JPG/WebP/GIF。

`启动语音`是每位用户独立的开关。群内发送“可用角色”查看菜单，发送
`选择角色 小丛雨` 选择角色；`关闭语音` 可随时关闭。当前“小丛雨”由同一角色档案处理
中文、日语和英语，但默认使用中文。用户明确说“请用英语回答”或“请用日语回答”时才会
切换相应语言；AI、版本号、链接和回复中零星的外语词不会改变语言。语音 record 成功发送后
不会重复发送同一条文字；语音生成、音频校验或 OneBot 发送失败时才回退文字。

目前这些规则只表示语言选择和发送回退已接通，**不表示任何微调音色模型已验收通过**。
当前稳定配置是基础 v2 权重、7.55 秒 #1 日语参考音频与空 `prompt_text`；该组合曾测得中文两句聚合
CER 为 1/29（3.45%，语言 `zh`）、英语 WER 为 0。历史日语长句 ASR 的原始 CER 为 0.0606，原因是
`嬉しい` / `うれしい` 的同音正字法差异；因此旧基础版和 e10 的历史日语 gate 均为 `false`，不能称三种
gate 都已通过。更新后的验收清单可以仅为人工复核过的同一日语台词显式列出
`accepted_transcriptions`，报告会保留原始 reference/CER，并另外显示匹配候选与 gate CER；它不会自动
消除、推断或放宽其他发音差异。这只验收了基础配置和发送链路；`GPT_SOVITS_SOVITS_WEIGHTS` 仍须保持为空，新的微调权重仅可在
下方完整验收通过后配置。

后续新增角色时，每个角色都使用一个统一档案；中 / 日 / 英支持通过 `languages` 标注，例如：

```json
{"murasame":{"label":"小丛雨","voice":"murasame","language":"zh","languages":"中 / 日 / 英","target_language":"zh","prompt_lang":"ja","prompt_text":"","ref_audio_path":"data/murasame_voice_dataset_ja/audio/murasame_0001.mp3"}}
```

`prompt_lang` 和 `prompt_text` 必须与 `ref_audio_path` 指向的**同一条参考音频**一致；不能把
中文录音标为日语，也不能凭文件名猜台词。当前稳定的本机 GPT-SoVITS v2 配置刻意使用空
`prompt_text`（无文本提示模式）：把 #1 的日语 ASR 文本直接填入提示，曾使中文 CER 升至约 70%，
所以不要仅因“有转写”就替换这个空值。部分 SoVITS V3/vocoder 后端会拒绝空文本；如改用该类后端，
必须填入经验证的参考转写并重新完成中/英/日三语验收。

群里只显示角色名称，不显示内部 ID 或部署字段。项目不会自动把录音
训练成音色，也不会在后台隐式训练。若要在本机显式训练，请先确认录音授权，
先用真实的、按 ZIP 内音频顺序排列的转写文件准备数据集（每行对应一条音频），再运行训练：

```powershell
python .\scripts\prepare_murasame_voice_dataset.py .\authorized_voice.zip `
  --output .\data\murasame_voice_dataset_ja --language ja `
  --transcripts .\authorized_transcripts_ja.txt
python .\scripts\validate_voice_manifest.py `
  .\data\murasame_voice_dataset_ja\murasame.list `
  .\data\murasame_voice_dataset_ja --expected-language ja --minimum-items 10
powershell -ExecutionPolicy Bypass -File .\scripts\train_murasame_voice.ps1
```

准备脚本拒绝覆盖已有数据集、空/错位转写和过大的压缩包；训练脚本会生成仅供训练使用的
ASCII 临时盘符路径清单（优先 `R:`，若已被占用则自动选择空闲盘符）、校验清单语言与音频路径，
并拒绝复用与清单不匹配或不完整的预处理缓存。需要固定盘符时可传入
`-ProjectDrive T:` 或 `-VoiceDrive Q:`；脚本绝不会覆盖已有映射。
每次启动会把预处理和训练日志保存到按数据集、运行时间独立命名的 `logs/murasame-training/` 子目录，避免覆盖前次日志。
训练模型和录音都在 `.gitignore` 覆盖的 `data/` 下，不会提交到 GitHub。

## GPT-SoVITS 本地后端

GPT-SoVITS 的 `api_v2.py` 默认监听 `9880`。本项目默认以 `text_lang=zh` 合成；用户明确
要求英语或日语时才将本条请求切换到 `en` 或 `ja`，并始终使用同一条已正确标注的
`ref_audio_path`。这只是语言分流配置，并不保证新权重或音色质量已经通过验收。配置示例：

```env
VOICE_ENABLED=true
VOICE_PROVIDER=gpt_sovits
VOICE_API_URL=http://127.0.0.1:9880
GPT_SOVITS_AUTO_START=auto
GPT_SOVITS_ROOT=../qq-chatrobot-voice/GPT-SoVITS
GPT_SOVITS_PYTHON=../qq-chatrobot-voice/GPT-SoVITS/.venv/Scripts/python.exe
GPT_SOVITS_TTS_CONFIG=GPT_SoVITS/configs/tts_infer.yaml
# 在下方验收通过前必须留空；通过后再填入已验收数据集生成的实际权重路径。
GPT_SOVITS_SOVITS_WEIGHTS=
```

执行 `scripts\\start_all.ps1` 时，如果本地目录、独立 Python 环境和模型配置都存在，
脚本会自动启动 `api_v2.py` 并等待 `9880/tts` 就绪；缺少依赖时只记录警告，不会阻止
QQ 机器人启动。参考音频和训练模型均只保存在本机。

## 启用微调权重前的验收

训练完成后，分别用 `zh`、`en`、`ja` 生成固定测试句并保存为 WAV；运行本地 ASR 检查脚本，
同时进行人工盲听。ASR 是门槛，不代替人工验收：脚本输出中的每个语言 gate 都应为 `true`，
且盲听确认发音、语言和音色均可接受，才可把新权重写入 `.env`。

```powershell
# FunASR 在中文用户名路径下可能无法定位模型。本机已映射 S:；若未映射且该盘符空闲，可运行：
# subst S: $env:USERPROFILE
..\qq-chatrobot-voice\GPT-SoVITS\.venv_cpu\Scripts\python.exe .\scripts\accept_voice_audio.py `
  --model-path "S:\.cache\modelscope\models\iic--SenseVoiceSmall\snapshots\master" `
  --audio-dir .\logs `
  --manifest .\logs\voice_acceptance_manifest.json `
  --output .\logs\voice_acceptance_report.json `
  --fail-on-gate
```

验收清单应对应待验收权重生成的固定中文、英文、日文短句及较长句 WAV；所有 WAV 保留在本机 `logs`，不提交。命令要求中、英、日三类样本全部存在且 ASR gate 均通过，否则以非零状态退出。旧基础权重对照另用
`logs\voice_acceptance_baseline_manifest.json` 和独立报告路径，避免把对照音频混入候选权重 gate。
英语 gate 同时检查聚合 WER 和每条 WAV 的 WER，两个默认上限均为 0.10；这样一条短句的明显错误不能被
较长句的零错误聚合掩盖。可按验收方案通过 `--max-en-wer` 和 `--max-single-en-wer` 分别显式调整。
对于已由人工核对、仅存在同音正字法差异的日语句子，清单可在对应行显式加入候选，例如
`{"accepted_transcriptions":["今日はうれしいです。"]}`。原始 `text`、原始 CER、实际采用的
候选和 gate CER 都会写入报告；不要为中文、英文或任何未审核的日语差异添加该字段。
如果模型缓存实际在其他目录，传入该本地目录即可；脚本不会下载模型、生成音频或修改配置。

录音不会被机器人自动拿去训练或克隆音色。若要接入音色模型，先确认录音者、
角色相关授权，再把已经部署好的 TTS 服务配置为 OpenAI 兼容的
`/v1/audio/speech` 接口。

可优先从柚子社《千恋＊万花》[官方 MOVIE 页面](https://www.yuzu-soft.com/products/senren/movie.html) 查看公开的角色歌曲/短剧和
系统语音入口，再按其授权条款取得可用素材；不要把第三方转载视频直接转成
群机器人音频。
