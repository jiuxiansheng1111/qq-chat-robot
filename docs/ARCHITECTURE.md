# QQ 群机器人设计说明

## 设计目标

这个项目不把 QQ 协议细节散落在业务代码中，而是把系统拆成“接入、事件、领域服务、插件、基础设施”五层。这样可以在不改业务插件的情况下替换 QQ 接入实现，也便于从单机版本扩展到多群、多实例部署。

## 模块边界

### 接入层

负责 WebSocket/HTTP 连接、鉴权、心跳、重连、OneBot 事件转换和发送动作。它不应包含“天气”“签到”“AI 回复”等业务判断。

### 事件层

负责把事件统一成内部模型，例如：

```python
class GroupMessage:
    message_id: str
    group_id: str
    user_id: str
    text: str
    sender_role: str
    timestamp: int
```

事件层还应过滤机器人自身消息，避免自触发循环。

### 领域服务层

包含权限、群设置、冷却、黑名单、审计、用户积分等稳定能力。插件只调用这些服务，不直接读写数据库表。

### 插件层

每个插件包含：触发器、参数校验、权限声明、执行逻辑和回复格式。插件要能独立禁用，并且失败时返回用户可理解的提示。

### 基础设施层

包含数据库、Redis、外部 API 客户端、日志和指标。外部请求统一配置超时、重试次数和熔断策略。

外部 API 通过 Provider 接口接入。Provider 把不同供应商的响应转换成统一领域对象，例如 `MediaResult`、`WeatherResult` 或 `AIReply`，这样猫图、小猪图片、天气和 AI 服务都可以替换而不影响插件。

## 外部 API 与媒体管线

```text
插件 → Provider → APIClient → MediaService → OneBot 消息
```

- `Provider`：理解某个供应商的 API 响应。
- `APIClient`：统一鉴权、超时、有限重试和错误转换。
- `MediaService`：下载、缓存、类型检查、大小限制和 QQ 消息转换。
- `OneBot 消息`：只负责最终发送。

不要在插件里直接请求任意 URL 或拼接 API 密钥。对图片 API 要防止超大文件、恶意重定向、内网地址访问和不受控制的 API 费用。

## 插件扩展模型

插件可以由指令、关键词、定时任务或管理操作触发。每个插件声明名称、触发器、权限、冷却时间、配置项和帮助文本。帮助菜单、群级开关和调用统计可以由这些元数据自动生成。

建议将供应商实现独立放在 `providers/`：

```text
plugins/cat_image.py
plugins/pig_image.py
providers/cat_api.py
providers/pig_api.py
services/media.py
```

## 数据模型建议

```text
groups
  id, group_id, enabled, timezone, created_at, updated_at

group_members
  id, group_id, user_id, role, blocked, created_at

plugin_settings
  id, group_id, plugin_name, enabled, config_json

command_audit
  id, group_id, user_id, command, success, reason, created_at
```

不要把完整原始聊天内容作为默认数据模型。若某个功能确实需要上下文，应使用短期、有上限、可清理的存储。

## 指令生命周期

```text
parse → authorize → validate → rate_limit → execute → render → audit
```

任一步失败都应停止后续执行。审计记录应包含成功/失败、失败原因和耗时，但避免记录不必要的敏感原文。

## 故障策略

- QQ 接入断开：指数退避重连，避免高频重连。
- 外部 API 超时：返回简短失败提示，不连续重试。
- 数据库不可用：只允许健康检查和低风险只读功能。
- 重复事件：基于 `message_id` 做短期幂等去重。
- 发送失败：记录错误并限次重试，避免消息风暴。

## 部署建议

单机小规模：

```text
onebot + bot + sqlite
```

生产小规模：

```text
onebot + bot + postgres + redis
```

多实例：

```text
onebot → bot instances → redis/pubsub → postgres
```

多实例部署时必须使用共享限流、分布式幂等和统一配置，否则同一条消息可能被重复处理。

## 版本演进

### 第一阶段：可用

实现 `/help`、群开关、日志、基本权限和重连；本地连通性使用健康检查接口验证。

### 第二阶段：可维护

加入插件注册表、数据库迁移、配置热更新、测试和管理指令。

### 第三阶段：可运营

加入指标、告警、审计查询、外部 API 配额、灰度启用和多实例部署。
