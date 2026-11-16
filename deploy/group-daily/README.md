# NapCat 群报历史分页修复

`history-pagination.patch` 应用到已安装的
`data/astrbot/instance/data/plugins/astrbot_plugin_qq_group_daily_analysis`。

NapCat 的无锚点 `get_group_msg_history` 调用 `getAioFirstViewLatestMsgs`，结果可能是稀疏的 QQ 首屏缓存。直接以其中最早消息翻页会漏掉中间历史。本补丁仅将此列表用于寻找最新锚点，再从该锚点读取连续历史；其他 OneBot 驱动行为保持原样。

本地 `app/astrbot_group_reports.py` 的手动群报还优先使用触发消息 ID，按北京时间筛选日期。只有非文字消息时仅返回实际统计，不生成话题和成员评价；缓存版本 2 会拒绝旧的漏读报告。

回归验证：`python -m pytest tests/test_astrbot_report_history.py tests/test_astrbot_group_reports.py -q`。覆盖稀疏缓存、真实锚点、标准驱动、日期和无文字情况。

插件更新后应重新检查补丁。修改插件分页代码后单独重载日报插件 `astrbot_plugin_qq_group_daily_analysis`；本地群报逻辑由 `astrbot_plugin_qqchat` 重载。

## 在测试环境复现群漫画

自动测试从群报插件 v5.8.4 的提交 `ca0e3017b833472b872e7d18df63ed6ff72d6358` 开始，依次应用头像、历史分页和 `comic-integration.patch`。漫画补丁接入实际部署的近期话题筛选、三格漫画生成和发送确认逻辑。CI 使用临时会话及测试消息验证这些入口。
