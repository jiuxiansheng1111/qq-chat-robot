# 大肥鱼手机版

[下载安卓安装包](https://github.com/jiuxiansheng1111/qq-chat-robot/raw/refs/heads/main/mobile/android/downloads/Dafeiyu.apk)

在 iQOO 11 上安装后，直接输入关键词点「联网搜索」，也可以说「帮我搜一下深圳天气」。点结果链接可在浏览器打开。发一个 HTTPS 网页地址可以读正文；连接模型后，大肥鱼也能总结这页。

直接问「深圳天气」可以查当地实况、体感、今明两天的气温和降水概率；「我的天气城市是深圳」会在手机上记住这个城市。还没有设置城市时，问「今天的天气」会尝试按手机网络估算位置。手机里装有可用的离线中文语音时，最新一条回复下会出现「朗读回复」。

点右上角「模型」，选择 DeepSeek、智谱、OpenAI、Claude、Gemini 或自定义服务，填写自己账号可用的模型名和密钥。手机直接请求该服务。对话保存在手机，密钥由 Android 密钥库保护。选择 Ollama 时，`127.0.0.1` 指手机上实际运行的服务。若凭据只填在电脑上，首次安装需要在手机上单独填写。

希望离开应用后也能用，在「模型」里打开「允许后台运行和开机恢复」，并允许通知。之后可以从通知给大肥鱼发消息。系统重启并解锁后，应用会尝试恢复服务。OriginOS 的自启动和后台耗电权限由手机设置控制。首次安装后请至少打开一次应用。云端对话和搜索均需要手机联网。[iQOO 11 官方参数](https://www.vivo.com.cn/vivo/param/iqoo11) · [Android 后台限制](https://developer.android.com/training/monitoring-device-state/doze-standby)

## 手机和电脑收 QQ

1. 手机上打开大肥鱼，点「模型」，选择自己的服务。首次安装默认选智谱。手机需要自己的模型凭据，之后通过手机网络生成回复。
2. **电脑开着时同步收。** 让手机和电脑连到同一可信 Wi-Fi。打开手机版「QQ」，开启「接入 QQ 协议端」和「允许同一私网的电脑连接」，复制配对信息。粘到电脑账号页的「手机同步收 QQ」，点「连接手机」。手机显示「QQ 已连接」后，保留默认的「电脑主答」即可两端收、电脑回。手机局域网地址改变时请重新复制配对信息。[电脑账号页](http://127.0.0.1:6190/accounts#mobile-qq)
3. **电脑关机时用手机 QQ。** 在这台手机的 QQ 或 TIM 登录机器人账号，打开消息通知和消息内容预览。在大肥鱼的「QQ」打开「接收这台手机的 QQ 通知」，点「打开系统通知使用权限」授权。QQ 提供文字快捷回复时，大肥鱼可以回到原会话。没有快捷回复的通知会标记为「已收到，QQ 未提供快捷回复」。静音或隐藏正文的会话取决于 QQ 实际发出的通知。识别为群聊时默认只回应点名，通知未标明私聊时可以把会话的完整名称填入允许列表。[Android 通知快捷回复](https://developer.android.com/develop/ui/views/notifications/build-notification#reply-action)

如果有独立常开的 OneBot 协议端，可以直接连手机版并选「手机主答」，私聊、群里 @、发送回执都走这个连接。启用手机主答时，使用同一帐号的电脑实例应关闭自动回复。「只接收」会关闭手机两种入口的自动回复。最近的消息在手机保存一小时，每次访问或收到新消息时清理，最多 200 条。在 QQ 里查询具体城市的天气不会使用主人的手机位置。[OneBot 反向连接](https://github.com/botuniverse/onebot-11/blob/master/communication/ws-reverse.md)

想在手机上使用狼人杀、Minecraft 等完整桌面插件，可以安装单独的 AstrBot 环境。[手机版完整插件环境](../../docs/网页助手与设备联动.md)

构建需要 JDK 17、Android SDK Platform 35 和 Build Tools 35.0.0。运行 `python mobile/android/build.py` 生成安装包。构建会检查 WebSocket 依赖的哈希并随 APK 附上许可证。发布签名保存在本机忽略目录 `data/mobile-signing`，下次更新使用同一签名。设备测试覆盖联网搜索、多轮对话、QQ 双端收取与单端回复、发送回执、通知快捷回复、熄屏和重启恢复。
