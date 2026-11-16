# 大肥鱼 QQ 人格适配

来源：https://github.com/zexuanw958-svg/dafeiyu-config-pack

审阅版本：`8c01b0361b8a4baff5be3d2bc67ed55108953086`，MIT 许可见同目录 LICENSE。

该项目是 AstrBot 配置包，可复用其人格；本地只适配人格，不运行整包安装器，不安装它的主动消息、签名、语音或记忆插件。

QQ 的唯一人格编辑入口是 `prompts/qq/dafeiyu.txt`，含身份、语气、功能边界和示例。与上游相比，去掉了会误报实际模型厂商的设定，移除了无需请求自行改签名等动作；详细牌谱和教程允许完整输出。

`scripts/configure_qq_persona.py --apply` 在停机时创建独立 QQ 配置，新增“大肥鱼”人格并迁移 QQ 默认会话。全局 `qqchat-local-persona` 仍被 B 站使用，因此不删除；原 `prompts/persona.txt` 和 `prompts/examples.jsonl` 不再作为 QQ 默认人格。其他平台、模型、工具权限和媒体接口不变。

QQ 请求末端清理 Companion 已标识的旧语气段，再使用新人格。用户原文、工具结果、记忆事实不改写；选择旧角色音色不会更换大肥鱼身份。功能继续使用 AstrBot 已注册工具和原有命令服务，工作模型与聊天人格分开，不导入其他角色提示词。

应用前自动备份数据库与已有 QQ 专用配置。修改提示词后重新执行同步并重启即可。
