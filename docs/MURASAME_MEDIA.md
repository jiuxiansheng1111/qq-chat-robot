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

后续新增角色时，每个角色都使用一个统一档案；中 / 日 / 英支持可通过旧的展示字段 `languages` 标注，
也可用机器可读的 `supported_languages` 限制为已验收语言。后者存在时菜单以它为准，且未列出的语言
不会送往 TTS，而是回退文字；省略它则保持旧档案行为。例如芳乃日语未验收时：

```json
{"yoshino":{"label":"芳乃","voice":"yoshino","languages":"中 / 日 / 英","supported_languages":["zh","en"],"target_language":"zh","prompt_lang":"ja","prompt_text":"","ref_audio_path":"data/voice/yoshino-reference.wav"}}
```

`supported_languages` 只能是非空、无重复的 `zh`、`ja`、`en` 列表；错误配置会 fail-closed，菜单仍显示
角色但标为“语音暂不可用”。

每个角色还可选填 `text_split_method` 来覆盖 GPT-SoVITS 的文本切分方式，只接受 API 已知的 `cut0` 至
`cut5`。例如已通过长句试听的小丛雨可单独填 `"text_split_method":"cut3"`；未填的茉子不会携带
该字段，继续让 sidecar 使用当前默认 `cut5`。非法值会在请求 TTS 前回退文字。

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

其他角色或混合语种素材使用 `prepare_character_voice_dataset.py` 准备独立数据集。
输入是人工核对后的 UTF-8 JSONL，每行须有相对于 `--source-root` 的 `file`、
真实 `language`（`zh`/`ja`/`en`）、逐句 `text` 以及人工核对后的
`"human_verified":true`；文件名、自动听写草稿和音效不能
自动当作正确台词。示例：

```json
{"file":"zh_voice_01.wav","language":"zh","text":"你好，今天过得怎么样？","human_verified":true}
{"file":"ja_voice_01.wav","language":"ja","text":"こんにちは、元気ですか。","human_verified":true}
```

```powershell
python -m scripts.prepare_character_voice_dataset .\reviewed.jsonl `
  --source-root .\authorized_audio --output .\data\character_voice_mixed `
  --speaker character --manifest-stem character
powershell -ExecutionPolicy Bypass -File .\scripts\train_murasame_voice.ps1 `
  -DatasetName character_voice_mixed -ManifestStem character `
  -ExperimentName character_voice_mixed -ExpectedLanguage mixed -TrainMixedFromBase
```

`-TrainMixedFromBase` 仅用于全新角色的中日混合集；旧小丛雨混合集仍须通过配对检查点
续训预检。输出语音必须分别试听、核对中日英语种与说话人后才能接入机器人。

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
# 权重路由在部署并验收每个本地路径前必须留空；e13 目前尚未部署。
VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON=
VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON=
```

执行 `scripts\\start_all.ps1` 时，如果本地目录、独立 Python 环境和模型配置都存在，
脚本会自动启动 `api_v2.py` 并等待 `9880/tts` 就绪；缺少依赖时只记录警告，不会阻止
QQ 机器人启动。参考音频和训练模型均只保存在本机。

验收用的独立 sidecar 必须先复制配置，随后将复制出的路径传给 `api_v2.py -c`，并使用非生产端口。
不要让验收进程直接使用生产 `tts_infer.yaml`：验收与生产应各有启动配置，避免候选权重或人工改动混入生产对照。换权接口改变当前进程中的模型状态，不能据此断言它会写回 YAML。

```powershell
..\qq-chatrobot-voice\GPT-SoVITS\.venv_cpu\Scripts\python.exe .\scripts\prepare_voice_acceptance_config.py `
  --source ..\qq-chatrobot-voice\GPT-SoVITS\GPT_SoVITS\configs\tts_infer.yaml `
  --output .\logs\voice-acceptance\tts_infer.acceptance.yaml `
  --sovits-weights "C:\accepted-weights\murasame_voice_candidate.pth"
# 将上一步打印出的路径传给验收 sidecar：api_v2.py -a 127.0.0.1 -p 9881 -c <output>
```

`--sovits-weights` 必须是已存在的候选 `.pth`，它只会写进新建的验收副本的
`custom.vits_weights_path`，不会改动 source/live YAML。若省略该参数，工具会明确警告验收副本
继承 source 当前的 `custom.vits_weights_path`；它不保证是基础模型，也不应被当作基础模型对照。

GPT-SoVITS 的英语前端还需要 NLTK 的 `averaged_perceptron_tagger_eng` 数据。若英语请求返回该资源
缺失错误，使用**实际启动 sidecar 的 GPT-SoVITS Python**一次性安装它（不切换权重、不改 YAML）：

```powershell
..\qq-chatrobot-voice\GPT-SoVITS\.venv_cpu\Scripts\python.exe -m nltk.downloader averaged_perceptron_tagger_eng
```

安装后先重试英语请求；若进程仍报资源缺失，再按正常流程重启 sidecar。在资源缺失期间，机器人会保留 LLM 的文字回退，而不会静默丢弃回复。

`accept_voice_audio.py` 只分析生成好的 WAV，不能修复已用生产配置生成样本的 sidecar。生产启动若明确
设置 `GPT_SOVITS_SOVITS_WEIGHTS`，则必须成功同步该权重；同步失败会明确停止启动流程，避免以错误模型提供语音。

映射为空时仍使用原来的单一模型。映射中的权重文件在每次合成前检查；缺失、JSON 格式错误或
切换接口失败时，该消息会回退成普通文字。切换和合成会在机器人内串行执行。由于当前 api_v2
没有可读取的进程身份或活动权重接口，已配置路由的每次合成都会在锁内重申对应权重，确保 sidecar
重启或外部换权后首条请求不会串音色。启动脚本在路由模式下无论 sidecar 已存在或刚启动，都不会再用
`GPT_SOVITS_SOVITS_WEIGHTS` 覆盖它；映射为空时才保留该单一初始权重行为。

`VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON` 只适用于默认角色。配置
`VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON` 后，非默认角色必须在其中有对应的角色和语言权重；
缺失时不会借用默认角色权重，而是回退文字。这避免不同角色在单一 sidecar 切换时串用音色。

角色档案也可以按合成语言选择参考音频和提示文本，同时保留旧字段作为缺项回退：

```json
{
  "label": "角色显示名",
  "ref_audio_path": "data/voice/default-reference.wav",
  "ref_audio_path_by_language": {"zh": "data/voice/zh-reference.wav", "ja": "data/voice/ja-reference.wav"},
  "prompt_text": "默认提示文本",
  "prompt_text_by_language": {"zh": "中文提示文本", "ja": "日本語の提示文"},
  "prompt_lang": "zh",
  "prompt_lang_by_language": {"zh": "zh", "ja": "ja"}
}
```

映射只接受 `zh`、`ja`、`en`、`ko`、`yue` 键。某语言没有映射时使用原字段；映射类型、语言键或非空
参考音频／提示语言的值不正确时，该条语音会回退文字。菜单始终只展示角色显示名和支持语言。

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
