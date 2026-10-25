# 雀魂四麻本地文字复盘

本功能接入现有 `app.main` 消息分发和插件注册，不替换角色、不改变其他功能。默认是可解释规则基线，没有训练策略模型，没有策略权重，不调用外部LLM，不上传牌谱。系统提示词与主题教材会实际读取、检查SHA256并构建只含当时视角的教练上下文；读不到时明确降级，不假称调用成功。

## 使用

沿用原机器人的群权限、黑名单、插件开关、限流和消息去重。可发真实OneBot `at`段加文字，也可明确输入：

```text
@机器人 帮我分析牌谱
分析牌谱
分析雀魂牌谱
帮我分析一下这个对局
帮我复盘这局雀魂牌谱
```

没有牌谱、座位或赛制时，机器人只提示补充。可在5分钟内由同一机器人账号、同一会话、同一QQ用户补充，超时需要重新提出。普通“分析牌谱很重要”和“不要分析牌谱”不触发；否定请求会清除自己的待补充状态。QQ私聊沿用项目原有支持范围，本次不放宽消息策略。

完整命令与固定座位：

```text
帮我分析牌谱 座位=北 赛制=半庄 文件=sample-rinshan.json
/牌谱分析 座位=3 赛制=半庄 文件=sample-rinshan.json
/牌谱分析 详情 <复盘ID> 1
/牌谱分析 详情 <复盘ID> 1 2
/牌谱分析 导出 <复盘ID>
```

座位是开局固定座位：0东、1南、2西、3北，不是当前局自风。标准mjai若初始actor编号有旋转，导入器会按庄家/局号重排，并在报告列出原actor映射；不会把分享链接中的账号ID当作座位。

文件放在本项目 `data/mahjong/inbox/`，扩展名 `.json/.jsonl/.mjson`，最多2MiB；导入箱应由本机可信流程管理。同消息或后续消息中的OneBot文件段可以提供已落入导入箱的同名文件。**尚不支持只凭QQ附件URL/file_id自动下载，也不处理仅有上传notice的客户端事件**；任意URL、本机路径、UNC路径、目录穿越和项目外junction都不会作为可信牌谱读取。小于64KiB的导出也可使用 `JSON={...}`。

默认群回复为短摘要；按节点分页展开，每次最多两个候选。完整Markdown报告保存在 `data/mahjong/reports/`。复盘缓存30分钟，最多8份，按机器人账号+群/会话+用户隔离。源文件不会复制进普通聊天记忆。

## 支持哪些真实格式

1. **标准mjai JSON事件数组或JSONL/.mjson**：按gimite/mjai及mjai-reviewer/convlog基础方言，支持 `start_game/start_kyoku/tsumo/dahai/chi/pon/daiminkan/ankan/kakan/dora/reach/reach_accepted/hora/ryukyoku/end_kyoku/end_game`。牌编码为 `1m..9m`、`E/S/W/N/P/F/C`，赤五 `5mr/5pr/5sr`；暗杠读取标准 `consumed[4]`。必须明确东风/半庄。未知事件、缺起手、缺点数、非法顺序等不静默丢弃；流局原因没有提供时不猜成荒牌。
2. **本地tenhou.net/6兼容JSON**：含 `name[4]`、`rule`、`log`，每局17项。可接受可信本地转换器从雀魂生成的兼容文件，但本项目没有登录、获取cookie或运行浏览器下载脚本。时间线采用有限回溯；无法唯一还原或超出子集时拒绝，要求标准mjai。杠宝牌公开时机采用转换约定，不能当作所有雀魂房规的认证。
3. `qq-mahjong-review/v1`是内部标准化/测试格式，可供已验证的本地适配器使用，**不是雀魂原生导出格式**。原生protobuf、RecordAction解码JSON与天凤原始XML尚未直接支持。

第三方本地转换流程的来源可参照 [mjai-reviewer的雀魂本地日志指南](https://github.com/Equim-chan/mjai-reviewer/blob/master/mjsoul.adoc)及[本地tenhou.net/6输入说明](https://github.com/Equim-chan/mjai-reviewer#usage)。指南中浏览器脚本的当前客户端兼容性本任务未实测，未安装执行；本功能也不会自动打开或执行这些链接。

**官方分享链接当前只校验，不抓取、不解析。** 仅接受指定官方HTTPS域名与合法 `paipu` 标识，限制链接长度并拒绝用户名密码、IP、自定义端口和其他域名；没有任何HTTP/DNS请求，也就没有重定向或DNS重绑定的抓取入口。不会使用第三方在线牌谱服务兜底，不索取密码。未来若实现抓取，必须另行做公开IP固定、域名、压缩前后大小和总超时限制，不能直接复用用户URL。

## 计算与信息隔离

`records.decision_input`先删除对手暗手、对手摸牌身份、牌山、结果、任意扩展字段。`analysis.analyze_visible`仅接受 `VisibleReplay`，逐事件生成不可变 `DecisionView`。策略接口只收到这一时点的本人手牌、牌河、副露、公开指示牌、立直、点数等；没有未来事件列表。候选和建议冻结后，`review._postgame`才读取终局与公开暗手，不能回写决策。

确定计算包括标准形/七对子/国士向听、形状待牌/降向听受入、去重后的可见未见枚数、当前宝牌与赤牌、基本门清立直/吃碰/食替/杠条件，以及已声明荒牌听牌人数对应的条件罚符。被鸣走的河牌不与副露重复扣除，赤五与普通五按同种牌合计四张。

每次本人舍牌、杠牌及有选择的可鸣机会记录实际动作和候选。鸣牌包含鸣后合法舍牌。暗杠/大明杠/加杠均保留不杠分支；立直后暗杠采用等听张且所有和形分解中刻子固定的保守结构检查。解释分别列速度/进张、役和价值、逐家安全、顺位净点差、流局条件收益、优点和代价。早/中/晚的巡数分箱仅作描述，同时使用公开剩余牌数与威胁，不独立决定安危。

**当前没有完整役种番符计分器、全部临时振听、和牌优先级/抢杠/四杠终止时机裁判、连庄止/延长/飞人/同点排序和房间自定义规则裁判。** 不宣称穷举所有合法和牌动作，不给确定胜率/放铳率或完整期望收益，不承诺价值和安全同时最优。规则相关罚点按实际适用条件区分；荒牌未听罚符、立直供托、途中流局、流局满贯、线下错和处罚不会混用。声明荒牌但时间线未耗尽牌山时，仅作条件重算并警告缺事件。

## 本地教材和教练提示词

- 主教材：`docs/mahjong/LOCAL_GUIDE.md`，独立主题文字，完整牌形例、适用条件和反例都在正文；来源只在附录，读取不联网。《长期意识》按用户要求暂缓。
- 专业教练补充：`prompts/mahjong_coach.md`，与原角色语气分工，不覆盖原角色文件。
- 追溯索引：`docs/mahjong/knowledge/index.json`，含内容哈希、来源、核验状态与核验说明。
- `coach.coach_request`构建系统提示及当时可见JSON+本地主题片段；原始牌谱字段不会成为系统指令。没有自动接入云端AI；合法本地模型可以通过 `LocalStrategyEngine` 的只读时点接口由程序显式注入，需提供版本、权重SHA256、许可证和目标，未声称其已校准。动作偏好不能称作胜率。

本机教材目前是文字版。Library官方helper已在下载后因Windows缺少 `os.setxattr` 失败，三张截图没有伪装成已入库；正文含全部必要文字复现且没有坏图。主对话另提供完整图文包。

更新已验证主题教材后可重新登记，不进行模型训练：

```powershell
python scripts/import_mahjong_knowledge.py docs/mahjong/LOCAL_GUIDE.md --title "日麻攻防与场况本地解析" --kind teaching --source "本地核验说明" --verified-by "核验人及日期" --verified
```

未核验视频须保持pending；视频资料即使核验也不自动替代主主题教材，更不会自动抓取外部URL。

## 本地运行和验收

从项目目录执行，无需启动机器人：

```powershell
python scripts/review_mahjong.py tests/fixtures/mahjong/upstream/rinshan.json --seat 3 --match hanchan --output report.md --visible-json visible.json
python scripts/test_mahjong_offline.py tests/test_mahjong_review.py tests/test_mahjong_imports.py tests/test_mahjong_webhook.py tests/test_plugins.py tests/test_astrbot_runtime.py tests/test_onebot_routing.py tests/test_webhook.py -q
python -m ruff check app/services/mahjong app/plugins/mahjong.py scripts/review_mahjong.py scripts/import_mahjong_knowledge.py scripts/test_mahjong_offline.py tests/test_mahjong_review.py tests/test_mahjong_imports.py tests/test_mahjong_webhook.py
```

测试脚本阻断真实socket连接，包含本机OneBot；仅允许Windows asyncio内部socketpair。若受限沙箱在socketpair初始化阻塞，应按既有授权在可创建内部socketpair的执行环境运行该离线脚本，不改系统网络。测试临时数据库和输出使用隔离目录，无真实QQ发送、云模型调用或雀魂账号登录。

真实图片菜单渲染测试需要系统中文字体（如微软雅黑或思源黑体）。缺少字体的环境会明确跳过该实绘制项，不能据此宣称已验证图片；文字菜单和其余回归仍执行。本机Windows已进行实际渲染、视觉检查和缓存失效测试。

真实格式fixtures来自mjai-reviewer上游Apache-2.0仓库；它们验证格式与重建，不是本用户真实雀魂端到端测试。`rinshan/chankan/four_reach`覆盖四座位；`ryukyoku`用于超出当前重建子集时拒绝的回归。详细归属见测试fixture目录说明。

开发验收不部署、不重启在线进程、不发送真实QQ消息；在线进程不会因代码落盘自动启用新功能。Git提交与推送按用户后续明确授权仅包含本次功能、教材和菜单，不包含运行数据、凭据或其他用户改动。
