# 大肥鱼手机版

[下载安卓安装包](https://github.com/jiuxiansheng1111/qq-chat-robot/raw/refs/heads/main/mobile/android/downloads/Dafeiyu.apk)

在 iQOO 11 上安装后，直接输入关键词点「联网搜索」，也可以说「帮我搜一下深圳天气」。点结果链接可在浏览器打开。发一个 HTTPS 网页地址可以读正文；连接模型后，大肥鱼也能总结这页。

直接问「深圳天气」可以查当地实况、体感、今明两天的气温和降水概率；「我的天气城市是深圳」会在手机上记住这个城市。还没有设置城市时，问「今天的天气」会尝试按手机网络估算位置。手机里装有可用的离线中文语音时，最新一条回复下会出现「朗读回复」。

点右上角「模型」，选择 DeepSeek、智谱、OpenAI、Claude、Gemini 或自定义服务，填写自己账号可用的模型名和密钥。手机直接请求该服务。对话保存在手机，密钥由 Android 密钥库保护。选择 Ollama 时，`127.0.0.1` 指手机上实际运行的服务。若凭据只填在电脑上，首次安装需要在手机上单独填写。

希望离开应用后也能用，在「模型」里打开「允许后台运行和开机恢复」，并允许通知。之后可以从通知给大肥鱼发消息。系统重启并解锁后，应用会尝试恢复服务。OriginOS 的自启动和后台耗电权限由手机设置控制。首次安装后请至少打开一次应用。云端对话和搜索均需要手机联网。[iQOO 11 官方参数](https://www.vivo.com.cn/vivo/param/iqoo11) · [Android 后台限制](https://developer.android.com/training/monitoring-device-state/doze-standby)

APK 包含文字多轮对话、联网搜索、网页阅读、搜索来源、通知输入和本机记录。桌面项目的 QQ、狼人杀、Minecraft、语音等扩展使用 AstrBot 环境，有单独的安卓部署方式。QQ 还需要独立在线的协议端。[手机版完整插件环境](../../docs/网页助手与设备联动.md)

构建需要 JDK 17、Android SDK Platform 35 和 Build Tools 35.0.0。运行 `python mobile/android/build.py` 生成安装包。发布签名保存在本机忽略目录 `data/mobile-signing`，下次更新使用同一签名。Android 测试分别覆盖发布包实际联网搜索、通知所在的前台服务、手机内两轮对话、熄屏和重启后的记录恢复。
