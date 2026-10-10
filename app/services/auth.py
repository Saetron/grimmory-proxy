import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
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
# Cache keyed-hash(username, password) -> token for quick Basic Auth re-use
_credential_tokens: Dict[str, str] = {}
_last_user_touch: Dict[int, float] = {}

# Per-process random key for hashing cached credentials
_CREDENTIAL_CACHE_KEY = secrets.token_bytes(32)
_MAX_CACHED_SESSIONS = 5000

# Failed-login throttling (per client IP + username)
_failed_logins: Dict[str, List[float]] = {}
_MAX_FAILED_LOGINS = 10
_LOGIN_WINDOW_SECONDS = 300
_MAX_THROTTLE_ENTRIES = 10000


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
        if len(_active_sessions) >= _MAX_CACHED_SESSIONS:
            AuthService._prune_expired()
        _active_sessions[session.token] = session

    @staticmethod
    def _prune_expired() -> None:
        now = datetime.now(timezone.utc)
        for token in [t for t, s in list(_active_sessions.items()) if s.expires_at <= now]:
            _active_sessions.pop(token, None)
        for key in [k for k, t in list(_credential_tokens.items()) if t not in _active_sessions]:
            _credential_tokens.pop(key, None)

    @staticmethod
    def _credential_key(username: str, password: str) -> str:
        """Keyed hash so plaintext passwords are never held as dictionary keys."""
        msg = f"{username}\x00{password}".encode("utf-8")
        return hmac.new(_CREDENTIAL_CACHE_KEY, msg, hashlib.sha256).hexdigest()

    @staticmethod
    def _throttle_key(username: str, client_ip: str) -> str:
        return f"{client_ip}|{username.lower()}"

    @classmethod
    def _check_throttle(cls, key: str) -> None:
        now = time.monotonic()
        attempts = [t for t in _failed_logins.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if attempts:
            _failed_logins[key] = attempts
        else:
            _failed_logins.pop(key, None)
        if len(attempts) >= _MAX_FAILED_LOGINS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)},
            )

    @classmethod
    def _record_failure(cls, key: str) -> None:
        if len(_failed_logins) >= _MAX_THROTTLE_ENTRIES:
            now = time.monotonic()
            for k in [k for k, v in list(_failed_logins.items()) if not v or now - v[-1] >= _LOGIN_WINDOW_SECONDS]:
                _failed_logins.pop(k, None)
        _failed_logins.setdefault(key, []).append(time.monotonic())

    @classmethod
    def purge_user_sessions(cls, user_id: int, username: Optional[str] = None) -> int:
        """
        Evicts all in-memory sessions and credential caches associated with the specified user ID.
        """
        tokens_to_remove = {
            token for token, session in list(_active_sessions.items())
            if session.user_id == user_id
        }
        for token in tokens_to_remove:
            _active_sessions.pop(token, None)

        # Credential cache entries are hashed, so match them via the token they resolve to.
        for key in [k for k, t in list(_credential_tokens.items()) if t in tokens_to_remove]:
            _credential_tokens.pop(key, None)
        return len(tokens_to_remove)

    @classmethod
    async def touch_user_activity(cls, user_id: int) -> None:
        """
        Updates the last connected timestamp for the user in the database (throttled to at most once per 60s).
        """
        import time
        now = time.time()
        last = _last_user_touch.get(user_id, 0.0)
        if now - last > 60.0:
            _last_user_touch[user_id] = now
            try:
                from app.main import db
                await db.touch_user_last_connected(user_id)
            except Exception:
                pass

    @classmethod
    async def authenticate_credentials(
        cls, username: str, password: str, client_ip: str = "unknown"
    ) -> UserSession:
        cache_key = cls._credential_key(username, password)
        cached_token = _credential_tokens.get(cache_key)
        if cached_token:
            cached_session = cls.get_cached_session(cached_token)
            if cached_session:
                return cached_session

        throttle_key = cls._throttle_key(username, client_ip)
        cls._check_throttle(throttle_key)

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

            # Store user data in SQLite once upon login
            try:
                await user_sync_service.save_user_data(session)
            except Exception as e:
                logger.warning(f"Error saving user login record for {username}: {e}")

            # Capture initial read states from Grimmory upon login
            try:
                await user_sync_service.capture_and_sync_user(session)
            except Exception as e:
                logger.warning(f"Error checking Grimmory read states for user {username}: {e}")

            return session
        except Exception as e:
            cls._record_failure(throttle_key)
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

        # Check SQLite if user was previously stored with this token
        try:
            from app.main import db
            user_row = await db.get_user_by_token(token)
            if user_row:
                assigned_libs = []
                try:
                    assigned_libs = json.loads(user_row.get("assigned_libraries") or "[]")
                except Exception:
                    pass
                session = UserSession(
                    user_id=user_row["id"],
                    username=user_row["username"],
                    token=user_row["token"] or token,
                    is_admin=bool(user_row["is_admin"]),
                    assigned_library_ids=assigned_libs,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                )
                cls.cache_session(session)
                return session
        except Exception as e:
            logger.debug(f"Error checking SQLite for token: {e}")

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
            try:
                await user_sync_service.save_user_data(session)
            except Exception:
                pass
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
                return await cls.authenticate_credentials(
                    username, password, client_ip=request.client.host if request.client else "unknown"
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.debug(f"Error parsing Basic Auth: {e}")

        # 2. Check Bearer Token Header
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            session = await cls.get_session_from_token(token)
            if session:
                await cls.touch_user_activity(session.user_id)
                return session

        # 3. Check API Key Headers or Query Parameter
        api_key = (
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or request.headers.get("X-Auth-Token")
            or request.headers.get("x-auth-token")
            or request.headers.get("api-key")
            or request.query_params.get("api_key")
        )
        if api_key:
            from app.main import db
            user_id = await db.get_user_id_by_api_key(api_key)
            if user_id is not None:
                user_row = await db.get_user_by_id(user_id)
                if user_row:
                    assigned_libs = []
                    try:
                        assigned_libs = json.loads(user_row["assigned_libraries"] or "[]")
                    except Exception:
                        pass
                    await cls.touch_user_activity(user_row["id"])
                    return UserSession(
                        user_id=user_row["id"],
                        username=user_row["username"],
                        token=user_row["token"] or "",
                        is_admin=bool(user_row["is_admin"]),
                        assigned_library_ids=assigned_libs,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                    )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
                headers={"WWW-Authenticate": 'Basic realm="Komga"'},
            )

        # 4. Check Session & Remember-Me Cookies (Komga / Browser WebUI / KMreader)
        for cookie_name in ["KOMGA-SESSION", "SESSION", "remember-me", "access_token", "admin_session"]:
            cookie_token = request.cookies.get(cookie_name)
            if cookie_token:
                session = await cls.get_session_from_token(cookie_token)
                if session:
                    await cls.touch_user_activity(session.user_id)
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
