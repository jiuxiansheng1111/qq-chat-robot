# 本机三语唱歌复验

`scripts/recheck_singing_languages.py` 使用唱歌功能现有的网易云音源、音源分离、Seed-VC 转换、质量检查和混音流程。本工具不连接 QQ 发送消息。默认依次处理 7 个角色和 3 首约 20 秒的有效歌词片段：

| 语言 | 曲目 | 网易云歌曲 ID |
| --- | --- | --- |
| 中文 | 朋友的酒DJ版 / 泽亦轩 | `1939837729` |
| 日语 | 春泥棒 / ヨルシカ | `1810759765` |
| 英语 | Shape of You / Ed Sheeran | `451703096` |

先跑当前生效权重的 baseline：

```powershell
python scripts/recheck_singing_languages.py --phase baseline --run-id 20261004-review
```

训练候选准备好后，用相同 `--run-id` 跑 candidate。它会复用 baseline 下完整且歌曲 ID、分离模型均匹配的原曲、歌词片段和分离 stem：

```powershell
python scripts/recheck_singing_languages.py --phase candidate --run-id 20261004-review
```

每个 phase 都写入独立目录，不会覆盖已有 phase。候选模式要求 `data/singing/candidates.json` 中存在对应角色的 checkpoint 和 config，且文件可读取；缺条目或文件时该角色报告失败，不会回退到 accepted/base 权重。候选权重只在本次进程中覆盖模型映射；`.env`、`voices.json` 和候选清单都不会被本工具改写。角色参考录音仍按当前设置解析，包括已选用的丛雨 B 参考录音。

源片段会先过滤曲名、署名等 LRC 元数据，再检查分离后原人声的 RMS 和有声覆盖。音量不足的窗口不会进入角色复验，也不会以 `instrumental` 结果算作通过。脚本会按 30 秒间隔串行尝试后续的 20 秒歌词窗口；触发音频重选时，`selection_reason` 会标记 LRC 时间轴待 ASR 核验，候选窗的能量记录在 source 清单中。

phase 完成后可以只重跑失败项；中文源修正时用 `--refresh-language zh`，它会强制重跑该 phase 的全部 7 个中文项，即使旧报告状态是 passed。旧中文源目录会移到 `sources/zh-previous-N`，原曲文件从旧目录复用；日语和英语的结果保留。phase 仍在运行时会拒绝刷新，需等原进程结束。

```powershell
python scripts/recheck_singing_languages.py --phase baseline --run-id trilingual-20261004 --retry-failed --refresh-language zh
```

需要比较单项 35/50 步时，指定角色、语言和相同步骤。两次结果分别落在 `candidate-steps35` 与 `candidate-steps50`，共用同一源目录；本项 QA 重试也保持所选步数。音区沿用该角色现有固定或自动配置：

```powershell
python scripts/recheck_singing_languages.py --phase candidate --run-id 20261004-review --profile murasame --language zh --steps 35
python scripts/recheck_singing_languages.py --phase candidate --run-id 20261004-review --profile murasame --language zh --steps 50
```

报告位于 `data/singing/acceptance/<run-id>/<phase>/`。`manifest.json` 会逐项更新进度、状态和失败原因；每个角色/语言组合各有 `report.json`。目录内保留分离后原唱、转换后人声、混音、QQ 音频格式试听文件和质量结果。源目录保存被选中的 LRC 片段与 source 清单，供复核歌词、能量、时间区间和重选原因。

运行前应确认项目 `.env` 中的唱歌运行时路径有效、CUDA/Seed-VC 与 Demucs 运行环境已准备好，以及需要会员歌曲时本机网易云会员桥可用。脚本仅驱动本机唱歌流程，不启动 QQ 或训练任务；训练仍由现有 `scripts/train_all_singing_voices.py` 执行。
