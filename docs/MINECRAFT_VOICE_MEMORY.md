# 游戏语音按需运行与内存边界

2026-10-17 后续实现替代此前“常驻 GPT-SoVITS + RVC”游戏路线。游戏语音请求不再依赖 9880 侧车，也不改变 QQ 的常驻语音、`.env`、模型 API 或 LLM 配置。

## 实际执行顺序

1. 复用当前角色授权、默认 TTS 音色、语言、参考录音和 GPT/SoVITS 权重路由。只把这些语音字段与本条文本写入本次临时作业，不包含凭据或 LLM 设置。
2. 获取既有跨进程 GPU 锁，启动 `scripts/minecraft_tts_worker.py`。它直接调用已有 GPT-SoVITS 的 `TTS_Config` / `TTS.run`；构造前选定真实权重，避免先加载其他人物再切模型。游戏专用进程使用可用 CUDA 半精度、单 batch、关闭并行及 CUDA graph，逐片段写 PCM16 WAV；原 YAML 不变。
3. 等待 TTS **整个进程退出**，释放其模型及 CUDA 上下文，再启动原 `run_rvc_model.py`，只加载本次角色的 RVC/索引，结束后同样退出。
4. 轻量 FFmpeg 转为游戏支持的 PCM16 WAV；语音桥按原协议返回。八个角色不同时常驻模型。

只有本机 GPT-SoVITS 采用这条一次性路径；原有远程/其他 TTS provider 仍沿用原接口。角色没有 TTS 时才做“基础 TTS → RVC”，不会把 RVC 当作文字合成器。

## 缓存和保护

`app/minecraft_voice_runtime.py` 只在 `data/minecraft_voice/audio_cache/` 缓存小型已生成 WAV。总计最多8MiB、32条，单条最多2MiB；不缓存模型。同一句话在八个角色间可复用基础 TTS WAV，但每个最终角色有独立缓存键。键包含配置、音色/采样参数、权重/索引/参考录音的文件签名、worker版本和随机种子；不使用原文作为文件名。坏缓存会删除并重新生成。

加载 TTS 或 RVC 前仍要求至少4GiB可用物理内存；运行期间保留1.5GiB、85%提交量与10GiB磁盘保护。没有为了通过而降低模型加载阈值。只有不加载模型的 PCM 转换使用运行期保护。任务取消、断开、资源保护或超时会终止**本次创建的子进程树**，等待退出后清理临时文件；不关闭玩家应用或其他服务。运行器不把密钥传入 worker 环境。

健康接口区分 `tts_worker_runtime_ready`（文件路径存在）、`tts_tcp_ready`（旧侧车端口诊断）与实际推理成功。`requires_persistent_tts=false` 时，无常驻端口不代表按需功能不可用。`data/minecraft_voice/last-request.json` 记录最近请求的阶段耗时、worker退出状态、最小可用RAM、CUDA/半精度报告、缓存命中与结果；不记录文本或模型凭据。

首次分阶段真实调用仍在TTS阶段触发运行保护，尚未进入RVC。源码进一步发现中文前端默认另载635,212,732字节的G2PW ONNX模型。游戏worker现在只设置该版本现有开关 `is_g2pw=false`，使用其已有的轻量pypinyin路径；训练权重和LLM不变，中文多音字读音可能不同。未同时更改模型加载dtype/mmap，先通过阶段读数确认额外前端模型是否为实际峰值来源。worker提供无秘密的torch/TTS导入、各公开模型初始化、前端和合成阶段读数，保存在同一审计的 `phases`。

Windows运行器改用 `Popen` 和本次专属文件日志，异步线程负责启动/读日志/等待，主循环只作非阻塞poll及保护检查，避免Proactor管道析构警告。取消和失败收尾等待本次PID树结束、关闭日志句柄；收尾异常不能覆盖原始资源不足提示。

进一步只读核查确认现有 Transformers 4.57.6 在未显式指定dtype时按FP32加载，即使两份本地BERT/HuBERT配置均写float16，原TTS随后才 `.to(cuda).half()`。游戏worker现在仅在CUDA且最终本已half时，对这两个公开 `from_pretrained` 入口显式传 `dtype=torch.float16`、`local_files_only=True`。本环境已有Accelerate 1.15.0，高于当前Transformers要求的0.26.0，因此同时使用单GPU `device_map` 直接放入CUDA；若环境未具备Accelerate则只用FP16加载后沿用原搬运步骤，不安装依赖。没有修改权重、原配置、LLM、全局默认dtype或第三方文件，也没有修改GPT/SoVITS的 `torch.load`/mmap。报告新增 `hf_load_dtype`/`hf_direct_cuda`；源码确定的冗余中间精度已去除，实际峰值改善仍待有足够空闲内存时测量。

`scripts/start_minecraft_voice.ps1` 默认只检查已有运行路径并写入无密钥的 `runtime.json`，不启动模型；默认目录下不运行此脚本也可自动定位。显式 `-PersistentSidecar` 才启动旧诊断侧车。游戏按需功能无需它。离线 lite 语言检测使用现成捆绑模型，并禁止其下载大模型。

## 验证状态

2026-10-17后续实际运行已通过八角色HTTP合成与生产Java播放类的本机输出；每份4.18秒PCM16、44.1kHz单声道，八份哈希不同。首条完整冷启动52.859秒，后七条复用基础TTS各12.718–13.828秒。TTS阶段驻留约2.51GiB，整轮工作进程组最大驻留约2.89GiB（立希RVC）；结束进程0。最终缓存命中0.109秒，不加载模型；缓存共3.07MiB。7个原配置/LLM文件哈希全部不变，未调用LLM或发送QQ。证据与离线试听位于 `artifacts/minecraft-voice-20261017/`。没有启动Minecraft，游戏页面/实体完整联调与听感仍需实际确认。

该实现已通过 Python 静态编译、Ruff 和 PowerShell 解析；未新增或运行测试套件。实现协作者没有并发启动模型或服务。插件重载与八角色真实 HTTP 音频生成由主集成串行执行，具体结果应记录在该阶段交付报告；源码和静态检查本身不等于八角色音色或游戏播放验收。
