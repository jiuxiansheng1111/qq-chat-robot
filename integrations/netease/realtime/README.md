# 网易云一起听房间文字

复用原账号桥的登录，只为一起听增加网易云信 ChatRoom 连接。账号、Cookie、原取歌和心跳接口保持，云信原生库在独立隐藏子进程中运行，故障不拖住原取歌接口。

安装：在项目根运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_netease_room_chat.ps1 -NativeSdk`。依赖放在项目内 `data/netease/nim-runtime`，使用官方 `node-nim@10.10.13`；原生 SDK 从网易云信官方发布源获取。Node.js 至少 22。

原桥新增带 Bearer 鉴权的 `POST /api/listen/chat`，仅允许当前已加入房间的 connect/status/messages/send/disconnect，SDK 的真实发送回调成功才返回 sent。QQ 工具再核对邀请来源的完整会话和用户，不能指定任意房间。普通 QQ 聊天不会被转发到网易云房间；房间新留言在自己的会话里由现有 Luna/大肥鱼回应。

消息只在子进程内存中暂存最多 100 条，一次取最多 50 条；过滤自身回声、重复消息和换房后旧回复。文字最多 500 个字符，超时发送不自动重试。退出只停止自己的聊天连接，不调用关闭所有人的一起听 end 接口。取消和换房会使仍在登录的旧连接失效。

`vendor` 的 ChatRoom 协议与 SDK 适配源自 [yanceydaisy/cove-resonance](https://github.com/yanceydaisy/cove-resonance) 的 `src/netease/realtimeTransport.ts`、`nimTicketBootstrap.ts` 和 `types.ts`，MIT，Copyright (c) 2026 Yancey，许可保留在 `LICENSE.upstream`。本项目调整了本地依赖加载、隐藏进程和临时目录清理边界，未引入上游的另一个模型、MCP 宿主或账号系统。

源文件和编译后的 JS 都保留；当前 JS 由 Node 22 的 `module.stripTypeScriptTypes(source, {mode: "transform"})` 生成。运行时直接加载 JS，不依赖在线编译或额外的 TypeScript 运行环境。云信凭据只经本机 IPC 传递，不进入 QQ 或聊天工具结果。
