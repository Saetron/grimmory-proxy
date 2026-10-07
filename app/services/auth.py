import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.models.internal import UserSession
from app.services.user_sync import user_sync_service

logger = logging.getLogger("grimmory_proxy.auth")

security_basic = HTTPBasic(auto_error=False)

# In-memory session cache: token/session_key -> UserSession
_active_sessions: Dict[str, UserSession] = {}
# Cache username:password -> token for quick Basic Auth re-use
_credential_tokens: Dict[str, str] = {}


def parse_assigned_libraries(assigned_raw: Any) -> List[int]:
    result = []
    if isinstance(assigned_raw, list):
        for item in assigned_raw:
            if isinstance(item, int):
                result.append(item)
            elif isinstance(item, str) and item.strip().isdigit():
                result.append(int(item.strip()))
            elif isinstance(item, dict):
                lid = item.get("id") or item.get("libraryId")
                if lid is not None and str(lid).strip().isdigit():
                    result.append(int(str(lid).strip()))
    return result


class AuthService:
    @staticmethod
    def get_cached_session(token: str) -> Optional[UserSession]:
        session = _active_sessions.get(token)
        if session:
            if session.expires_at > datetime.now(timezone.utc):
                return session
            else:
                _active_sessions.pop(token, None)
        return None

    @staticmethod
    def cache_session(session: UserSession) -> None:
        _active_sessions[session.token] = session

    @classmethod
    async def authenticate_credentials(cls, username: str, password: str) -> UserSession:
        cache_key = f"{username}:{password}"
        cached_token = _credential_tokens.get(cache_key)
        if cached_token:
            cached_session = cls.get_cached_session(cached_token)
            if cached_session:
                try:
                    await user_sync_service.capture_and_sync_user(cached_session)
                except Exception as e:
                    logger.debug(f"Error checking read states on cached login: {e}")
                return cached_session

        try:
            login_resp = await grimmory_client.login(username, password)
            token = login_resp.accessToken
            user = await grimmory_client.get_current_user(token)

            assigned_libs = parse_assigned_libraries(user.assignedLibraries)
            session = UserSession(
                user_id=user.id,
                username=user.username,
                token=token,
                is_admin=user.permissions.admin,
                assigned_library_ids=assigned_libs,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=login_resp.expires),
            )
            cls.cache_session(session)
            _credential_tokens[cache_key] = token

            # Capture user connection data and synchronize read states from Grimmory
            try:
                await user_sync_service.capture_and_sync_user(session)
            except Exception as e:
                logger.warning(f"Error checking Grimmory read states for user {username}: {e}")

            return session
        except Exception as e:
            logger.warning(f"Failed authentication for user {username}: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": 'Basic realm="Komga"'},
            )

    @classmethod
    async def get_session_from_token(cls, token: str) -> Optional[UserSession]:
        cached = cls.get_cached_session(token)
        if cached:
            return cached

        try:
            user = await grimmory_client.get_current_user(token)
            assigned_libs = parse_assigned_libraries(user.assignedLibraries)
            session = UserSession(
                user_id=user.id,
                username=user.username,
                token=token,
                is_admin=user.permissions.admin,
                assigned_library_ids=assigned_libs,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            )
            cls.cache_session(session)
            return session
        except Exception as e:
            logger.debug(f"Invalid token: {e}")
            return None

    @classmethod
    async def get_current_user_optional(cls, request: Request) -> Optional[UserSession]:
        # 1. Check HTTP Basic Auth Header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("basic "):
            try:
                b64_creds = auth_header[6:].strip()
                decoded = base64.b64decode(b64_creds).decode("utf-8")
                username, password = decoded.split(":", 1)
                return await cls.authenticate_credentials(username, password)
            except HTTPException:
                raise
            except Exception as e:
                logger.debug(f"Error parsing Basic Auth: {e}")

        # 2. Check Bearer Token Header
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            session = await cls.get_session_from_token(token)
            if session:
                return session

        # 3. Check Session Cookies (Komga / Browser WebUI)
        for cookie_name in ["KOMGA-SESSION", "SESSION", "access_token", "admin_session"]:
            cookie_token = request.cookies.get(cookie_name)
            if cookie_token:
                session = await cls.get_session_from_token(cookie_token)
                if session:
                    return session

        return None

    @classmethod
    async def require_user(cls, request: Request) -> UserSession:
        user = await cls.get_current_user_optional(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="Komga"'},
            )
        try:
            await user_sync_service.capture_and_sync_user(user)
        except Exception as e:
            logger.debug(f"Error checking Grimmory read states for user {user.username}: {e}")
        return user

    @classmethod
    async def require_admin(cls, request: Request) -> UserSession:
        user = await cls.require_user(request)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Only Grimmory Administrators can access this resource",
            )
        return user


auth_service = AuthService()
