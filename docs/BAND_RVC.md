# MyGO!!!!! 与 Ave Mujica 角色接入

这 10 个角色使用用户提供的 RVC 模型进行翻唱，使用独立角色卡进行文字聊天。RVC 不直接生成文本语音，因此没有将它们配置成 GPT-SoVITS 朗读音色。

## 使用

- `可用角色`、`唱歌音色`：查看角色。
- `选择角色 千早爱音`、`选择角色 丰川祥子`：切换当前角色。
- `爱音出来`、`祥子，陪我聊聊`：召唤角色进行文字聊天。
- `@机器人 翻唱片段 爱音 歌名`：约 20 秒片段。
- `@机器人 翻唱完整 祥子 歌名`：整首分段翻唱。
- `@机器人 唱歌状态`、`@机器人 取消唱歌`：沿用原任务控制。

图片菜单、文字菜单与角色列表均使用正式角色名，简称用于角色选择和召唤。模型文件名不作为角色名别名。

| 乐队 | 显示名称 | 模型文件 | 索引 |
| --- | --- | --- | --- |
| MyGO!!!!! | 高松灯 | mygo_deng.pth | mygo_deng.index |
| MyGO!!!!! | 千早爱音 | mygo_aiyi.pth | mygo_aiyi.index |
| MyGO!!!!! | 要乐奈 | mygo_lenai.pth | mygo_lenai.index |
| MyGO!!!!! | 长崎素世 | mygo_sushi.pth | mygo_sushi.index |
| MyGO!!!!! | 椎名立希 | mygo_lixi.pth | mygo_lixi.index |
| Ave Mujica | 三角初华 | mujica_chuhua.pth | 暂缺 |
| Ave Mujica | 若叶睦 | mujica_nuoyemu.pth | 暂缺 |
| Ave Mujica | 八幡海铃 | mujica_hailin.pth | 暂缺 |
| Ave Mujica | 祐天寺若麦 | mujica_miaomeng.pth | 暂缺 |
| Ave Mujica | 丰川祥子 | mujica_xiangzi.pth | 暂缺 |

## 运行方式

其他本机安装可使用 `.venv/Scripts/python.exe scripts/register_band_rvc.py --rvc-root ../qq-chatrobot-voice/RVC-upstream-20261012 --apply` 注册。脚本先备份 `.env`，只更新角色列表和 RVC 环境路径；省略 `--apply` 时仅显示名单。

`SINGING_RVC_ROOT` 指向独立 RVC 源码目录，`SINGING_RVC_PYTHON` 指向其 Python。角色配置中的 `singing_provider=rvc` 与 `rvc_model_path` 决定模型；`rvc_index_path` 与 `rvc_index_rate` 决定索引。索引缺失的角色明确设置空路径与比例 0，不借用其他角色索引。

模型通过 `scripts/run_rvc_model.py` 在独立子进程加载。长段在同一子进程内按最多 20 秒、带前后上下文的小段转换，并按原曲采样位置交叉淡化，避免 RVC 内部分段累积时长偏差。机器人原有 GPU 排队、物理内存保护、取消和超时、分段发送、原唱分离、音高检查、人声缺失检查和混音继续使用。每次加载所选模型，子进程结束后释放显存。

没有目标参考录音的 RVC 角色不计算音色身份相似度，报告 `voice_similarity=null` 和 `not_evaluated_no_target_recording`。音高、时长、人声覆盖、音量与削波检查仍然执行；原 Seed-VC 角色保留默认音色相似度门槛。可在 `SINGING_REFERENCE_AUDIO_BY_PROFILE_JSON` 为对应角色配置本人参考录音，以启用该检查。

## 原有内容

本机新增角色前的 `.env` 备份保存在 `data/maintenance/20261012/band_robot_before/`。旧 7 个角色的模型、录音、配置与语音服务开关保留，模型文件不纳入 Git。

角色卡依据官方角色名称与基本设定，用原创文字描述聊天表达：[MyGO!!!!! 官方角色](https://anime.bang-dream.com/bandorichan/character/mygo/)、[Ave Mujica 官方角色](https://anime.bang-dream.com/bandorichan/character/avemujica/)。
