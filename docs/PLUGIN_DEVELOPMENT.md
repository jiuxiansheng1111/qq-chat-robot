# 插件与外部 API 开发指南

## 插件职责

插件负责声明触发方式、解析参数、检查权限与冷却、调用业务服务，以及把结果转换成 QQ 消息。插件不应直接处理数据库连接、API 密钥、底层 HTTP 重试或 OneBot 原始协议。

## 图片插件模板

```python
from nonebot import on_command

image_command = on_command(
    "小猪", aliases={"pig", "猪图"}, priority=10, block=True
)

@image_command.handle()
async def handle_image():
    result = await pig_provider.random_pig()
    image = await media_service.to_qq_image(result)
    await image_command.finish(image)
```

## Provider 适配器

```python
class PigImageProvider:
    def __init__(self, client, url, api_key):
        self.client = client
        self.url = url
        self.api_key = api_key

    async def random_pig(self):
        data = await self.client.get_json(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        return MediaResult(
            url=data["image_url"],
            mime_type=data.get("mime_type", "image/jpeg"),
        )
```

不同供应商的字段都在 provider 内转换为统一的 `MediaResult`、`WeatherResult` 或 `AIReply`，插件无需知道供应商格式。

## 图片发送管线

```text
API 返回 URL
  → URL 安全校验
  → 下载并限制大小
  → 检查真实文件类型
  → 缓存
  → 转换为 OneBot 图片消息
  → 发送并记录结果
```

如果接口返回 Base64，也要检查解码后的大小和真实媒体类型，不能只相信扩展名或响应头。

## 失败与降级

每个 provider 建议配置主服务、备用服务、超时、最大重试次数、每群冷却、每用户冷却和每日总调用额度。例如 `/猫` 可按“主猫图 API → 备用猫图 API → 统一失败提示”的顺序执行。

## 自由功能注册表

```python
PLUGIN_META = {
    "cat_image": {
        "commands": ["猫", "cat", "猫图"],
        "description": "随机发送一张猫图",
        "admin_only": False,
        "cooldown_seconds": 10,
    },
    "pig_image": {
        "commands": ["小猪", "pig", "猪图"],
        "description": "随机发送一张小猪图片",
        "admin_only": False,
        "cooldown_seconds": 10,
    },
}
```

`/help`、群级开关和调用统计都可以从注册表自动生成。
