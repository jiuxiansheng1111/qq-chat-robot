# 管理 API

管理 API 使用 JWT Access Token，所有路由都要求 `admin` 或 `owner` 角色。

## 获取 Access Token

```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "你的管理员密码"
}
```

后续请求携带：

```http
Authorization: Bearer <access_token>
```

## 群配置

```http
GET /api/admin/groups/{group_id}
PUT /api/admin/groups/{group_id}
Content-Type: application/json

{
  "enabled": false
}
```

群关闭后，机器人不响应普通群消息；群管理员仍可通过群指令 `/bot on` 重新开启。

## 黑名单

```http
GET /api/admin/groups/{group_id}/blacklist/{user_id}
PUT /api/admin/groups/{group_id}/blacklist/{user_id}
Content-Type: application/json

{
  "blocked": true
}
```

黑名单判断发生在消息路由之前，被拉黑用户的消息不会进入 LLM、图片 API 或其他插件。

## curl 示例

```bash
TOKEN=$(curl -s http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"change-me-now"}' | jq -r .access_token)

curl -X PUT http://localhost:8000/api/admin/groups/123456 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```
