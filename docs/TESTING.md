# 自动化测试与健康检查

项目测试分为两层，所有命令均在项目根目录运行。

## 本地测试

不访问外部 API，不消耗额度，也不会向 QQ 发送消息：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 真实接口健康检查

真实调用 `.env` 中配置的服务，可能消耗少量 API 额度：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\live --live -v
```

每个接口是独立测试项，包括：

- 本地 `/health/live`、`/health/ready`、`/health/llm`
- 管理员 JWT 登录、查询、刷新和注销
- OneBot Webhook 鉴权
- 智谱 LLM
- Groq 备用 LLM（未配置时跳过）
- CATAAS 动态猫 GIF
- Wikimedia Commons 真实小猪照片
- GitHub 开源图库奶龙表情包
- OneBot `get_login_info`

## 只检查一个服务

```powershell
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k zhipu
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k groq
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k cat
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k pig
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k nailong
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k onebot
.\.venv\Scripts\python.exe -m pytest tests\live --live -v -k jwt
```

测试失败时 pytest 会直接显示失败的测试名称。测试代码不会打印 API Key、JWT、Token、模型回复或图片内容。
