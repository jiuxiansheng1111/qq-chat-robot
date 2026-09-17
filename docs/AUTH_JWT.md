# JWT 鉴权与 Token Refresh 设计

## 适用范围

JWT 主要用于管理后台、管理 API 和未来的 Web 控制台，不建议用 JWT 代替 QQ 群成员身份判断。群内权限仍应根据 OneBot 事件中的群号、用户 QQ 和群角色校验。

## Token 策略

| Token | 建议有效期 | 用途 | 存储方式 |
|---|---:|---|---|
| Access Token | 10–30 分钟 | 调用管理 API | 客户端内存或安全 Cookie |
| Refresh Token | 7–30 天 | 获取新的 Access Token | HttpOnly、Secure Cookie |

Refresh Token 必须支持轮换和撤销。不要把一个长期不变的 refresh token 当作永久登录凭证。

## 推荐流程

```text
POST /api/auth/login
  → 校验用户名和密码
  → 签发 access_token + refresh_token

调用管理 API
  → Authorization: Bearer <access_token>

Access Token 过期
  → POST /api/auth/refresh
  → 校验 refresh token
  → 撤销旧 refresh token
  → 签发新的 access token + refresh token

退出登录
  → POST /api/auth/logout
  → 撤销当前 refresh token
```

## JWT Claims

Access Token 建议包含：

```json
{
  "sub": "admin-user-id",
  "role": "admin",
  "type": "access",
  "jti": "unique-token-id",
  "iat": 1720000000,
  "exp": 1720001800
}
```

Refresh Token 至少包含 `sub`、`type=refresh`、`jti`、`iat` 和 `exp`。服务端将 refresh token 的哈希值、`jti`、用户 ID、过期时间、撤销时间和替换关系存入数据库或 Redis。

## API 设计

```text
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
GET  /api/auth/me
```

登录请求示例：

```json
{
  "username": "admin",
  "password": "change-me"
}
```

登录响应示例：

```json
{
  "access_token": "<short-lived-jwt>",
  "token_type": "bearer",
  "expires_in": 1200
}
```

Refresh Token 推荐通过 HttpOnly Cookie 返回，而不是放在 JSON 响应中。若必须由移动端或脚本客户端保存，应使用安全的系统级密钥存储。

## Refresh Token 轮换

每次 refresh 都执行：

1. 验证签名、类型和过期时间。
2. 根据 `jti` 查询服务端会话。
3. 检查会话是否已经撤销。
4. 撤销旧 token。
5. 创建新 refresh token，并记录 `replaced_by`。
6. 签发新的 access token。

如果一个已经使用过的 refresh token 再次出现，应视为可能的 token 泄露，撤销该用户或该设备的整条 refresh token 链。

## 安全要求

- 密码使用 Argon2id 或 bcrypt 哈希，禁止明文保存。
- JWT 签名密钥从环境变量或 Secret 注入，不能写入代码仓库。
- 生产环境优先使用非对称算法，例如 RS256/EdDSA；所有服务共享公钥，只有认证服务持有私钥。
- 严格校验 `iss`、`aud`、`type`、`exp` 和 `jti`。
- 登录和刷新接口必须限流。
- 浏览器场景使用 HttpOnly、Secure、SameSite Cookie，并考虑 CSRF 防护。
- 日志中不得记录完整 access token、refresh token 或密码。

## 环境变量

```env
JWT_ALGORITHM=HS256
JWT_SECRET_KEY=replace-with-a-long-random-secret
JWT_ISSUER=qq-group-bot
JWT_AUDIENCE=qq-group-bot-admin
ACCESS_TOKEN_EXPIRE_MINUTES=20
REFRESH_TOKEN_EXPIRE_DAYS=14
AUTH_COOKIE_SECURE=false
```

开发环境可以使用 HS256；生产环境应使用 Secret 管理和密钥轮换机制。更换签名密钥会使旧 token 失效，应安排维护窗口。

## 权限分层

```text
viewer  → 查看状态、日志摘要
operator → 启停插件、刷新配置
admin   → 管理群配置、黑名单、API 配额
owner   → 用户、密钥和系统级配置
```

管理 API 的每个路由都应显式声明所需角色，不要只判断“用户已经登录”。
