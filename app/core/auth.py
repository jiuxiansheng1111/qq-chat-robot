import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.core.security import hash_password, verify_password
from app.db.database import Database

bearer = HTTPBearer(auto_error=False)


class AuthService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    async def ensure_admin(self) -> None:
        row = await self.db.fetchone(
            "SELECT username, password_hash FROM users WHERE username = ?",
            (self.settings.admin_username,),
        )
        if not row:
            await self.db.execute(
                "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
                (self.settings.admin_username, hash_password(self.settings.admin_password), "owner"),
            )
        elif not verify_password(self.settings.admin_password, row[1]):
            await self.db.execute(
                "UPDATE users SET password_hash = ?, role = 'owner' WHERE username = ?",
                (hash_password(self.settings.admin_password), self.settings.admin_username),
            )

    def _jwt(self, username: str, role: str, token_type: str, expires: timedelta, jti: str) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": username, "role": role, "type": token_type, "jti": jti,
            "iat": now, "exp": now + expires,
            "iss": self.settings.jwt_issuer, "aud": self.settings.jwt_audience,
        }
        return jwt.encode(payload, self.settings.jwt_secret_key, algorithm="HS256")

    async def login(self, username: str, password: str) -> dict:
        row = await self.db.fetchone(
            "SELECT username, password_hash, role FROM users WHERE username = ?", (username,)
        )
        if not row or not verify_password(password, row[1]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        access = self._jwt(username, row[2], "access", timedelta(minutes=self.settings.access_token_expire_minutes), str(uuid.uuid4()))
        refresh_jti = str(uuid.uuid4())
        refresh = self._jwt(username, row[2], "refresh", timedelta(days=self.settings.refresh_token_expire_days), refresh_jti)
        expires_at = int(time.time()) + self.settings.refresh_token_expire_days * 86400
        await self.db.execute(
            "INSERT INTO refresh_tokens(jti, username, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (refresh_jti, username, hashlib.sha256(refresh.encode()).hexdigest(), expires_at),
        )
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": self.settings.access_token_expire_minutes * 60}

    def decode(self, token: str, expected_type: str) -> dict:
        try:
            payload = jwt.decode(token, self.settings.jwt_secret_key, algorithms=["HS256"], audience=self.settings.jwt_audience, issuer=self.settings.jwt_issuer)
            if payload.get("type") != expected_type:
                raise ValueError("wrong token type")
            return payload
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Token 无效或已过期") from exc

    async def refresh(self, token: str) -> dict:
        payload = self.decode(token, "refresh")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = await self.db.fetchone(
            "SELECT jti, username, expires_at, revoked_at FROM refresh_tokens WHERE jti = ? AND token_hash = ?",
            (payload["jti"], token_hash),
        )
        if not row or row[3] or row[2] <= int(time.time()):
            raise HTTPException(status_code=401, detail="Refresh Token 已撤销或过期")
        user = await self.db.fetchone("SELECT role FROM users WHERE username = ?", (row[1],))
        new_jti = str(uuid.uuid4())
        access = self._jwt(row[1], user[0], "access", timedelta(minutes=self.settings.access_token_expire_minutes), str(uuid.uuid4()))
        new_refresh = self._jwt(row[1], user[0], "refresh", timedelta(days=self.settings.refresh_token_expire_days), new_jti)
        await self.db.execute("UPDATE refresh_tokens SET revoked_at = ?, replaced_by = ? WHERE jti = ?", (int(time.time()), new_jti, row[0]))
        await self.db.execute(
            "INSERT INTO refresh_tokens(jti, username, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (new_jti, row[1], hashlib.sha256(new_refresh.encode()).hexdigest(), int(time.time()) + self.settings.refresh_token_expire_days * 86400),
        )
        return {"access_token": access, "refresh_token": new_refresh, "token_type": "bearer", "expires_in": self.settings.access_token_expire_minutes * 60}

    async def logout(self, token: str) -> None:
        payload = self.decode(token, "refresh")
        await self.db.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE jti = ?", (int(time.time()), payload["jti"]))


def current_user(settings: Settings):
    async def dependency(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> dict:
        if not credentials:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
        try:
            payload = jwt.decode(credentials.credentials, settings.jwt_secret_key, algorithms=["HS256"], audience=settings.jwt_audience, issuer=settings.jwt_issuer)
            if payload.get("type") != "access":
                raise ValueError
            return payload
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Access Token 无效或已过期") from exc
    return dependency
