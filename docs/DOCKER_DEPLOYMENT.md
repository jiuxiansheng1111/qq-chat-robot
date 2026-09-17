# Docker 开发与部署

## 容器划分

推荐将职责拆成以下容器：

```text
onebot       QQ 协议接入
bot          NoneBot 业务机器人
api          管理 API 与 JWT 鉴权，可与 bot 合并
postgres     生产数据库
redis        限流、缓存、refresh session
```

开发环境可以只运行 `bot + onebot + sqlite`；生产环境建议使用 `postgres + redis`。

## Dockerfile

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && uv pip install --system .

COPY src ./src
COPY bot.py ./

RUN mkdir -p /app/data && chown -R app:app /app
USER app

CMD ["python", "bot.py"]
```

如果项目不使用 `uv`，可以改为 `COPY requirements.txt ./` 和 `pip install -r requirements.txt`。

## docker-compose.dev.yml

```yaml
services:
  bot:
    build: .
    env_file: .env.dev
    volumes:
      - ./src:/app/src
      - ./data:/app/data
    depends_on:
      onebot:
        condition: service_started
    restart: unless-stopped

  onebot:
    image: your-compliant-onebot-image:latest
    environment:
      ONEBOT_ACCESS_TOKEN: ${ONEBOT_ACCESS_TOKEN}
    ports:
      - "127.0.0.1:8080:8080"
    restart: unless-stopped
```

开发启动：

```bash
docker compose -f docker-compose.dev.yml up --build
```

## docker-compose.prod.yml

```yaml
services:
  bot:
    image: ${BOT_IMAGE:-qq-group-bot:latest}
    env_file: .env.prod
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - bot-data:/app/data

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  bot-data:
  postgres-data:
  redis-data:
```

生产启动：

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f bot
```

## .env.prod 示例

```env
BOT_IMAGE=registry.example.com/qq-group-bot:1.0.0
DATABASE_URL=postgresql+asyncpg://bot:change-me@postgres:5432/qqbot
REDIS_URL=redis://redis:6379/0
POSTGRES_DB=qqbot
POSTGRES_USER=bot
POSTGRES_PASSWORD=change-me
JWT_SECRET_KEY=replace-with-a-long-random-secret
ONEBOT_WS_URL=ws://onebot:8080/onebot/v11/ws
ONEBOT_ACCESS_TOKEN=change-me
```

真实环境请使用 `.env` 文件权限控制、Docker Secret 或云厂商 Secret Manager。不要把生产 `.env` 提交到 Git。

## 镜像发布

```bash
docker build -t registry.example.com/qq-group-bot:1.0.0 .
docker push registry.example.com/qq-group-bot:1.0.0
```

建议使用固定版本标签，不要在生产环境依赖 `latest`。升级时先启动新版本，确认数据库迁移、健康检查和日志正常后再切换流量。

## 健康检查与备份

- 提供 `GET /health/live`：进程存活即可返回 200。
- 提供 `GET /health/ready`：数据库、Redis 和 OneBot 连接满足要求才返回 200。
- 定期备份 PostgreSQL，并测试恢复流程。
- Redis 用于缓存和 refresh session 时，生产环境启用持久化并限制网络访问。
- 生产容器不直接暴露 PostgreSQL 和 Redis 到公网。

## 部署安全清单

- 使用非 root 用户运行 bot 容器。
- 生产容器启用只读根文件系统和最小 Linux capability。
- 只开放必要端口，OneBot、Redis、PostgreSQL 使用内部网络。
- 固定基础镜像版本，并定期扫描漏洞。
- 通过环境变量或 Secret 注入 JWT 密钥、QQ token 和第三方 API 密钥。
- 部署前执行数据库迁移和插件配置校验。
