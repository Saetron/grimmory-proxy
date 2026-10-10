import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.internal import UserSession
from app.services import auth as auth_module
from app.services.auth import AuthService
from app.services.cache import DiskThumbnailCache

ROOT = Path(__file__).resolve().parent.parent


def _session(user_id: int, token: str, admin: bool = False) -> UserSession:
    return UserSession(
        user_id=user_id,
        username=f"user{user_id}",
        token=token,
        is_admin=admin,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_thumbnail_cache_path_cannot_escape_base_dir(tmp_path):
    cache = DiskThumbnailCache(str(tmp_path / "thumbs"))
    for evil in ["../../etc/passwd", "..\\..\\evil", "a/b", "x.y", "名前"]:
        path = cache._get_path("series", evil).resolve()
        assert path.parent == (tmp_path / "thumbs").resolve()
        assert path.suffix == ".jpg"
    cache.save_thumbnail("series", "../../escape", b"data")
    assert not (tmp_path / "escape.jpg").exists()
    assert cache.get_thumbnail("series", "../../escape") == b"data"


def test_credential_cache_key_does_not_contain_plaintext():
    key = AuthService._credential_key("alice", "hunter2")
    assert "hunter2" not in key and "alice" not in key
    assert key == AuthService._credential_key("alice", "hunter2")
    assert key != AuthService._credential_key("alice", "hunter3")
    # Separator ambiguity: ("a:b", "c") must differ from ("a", "b:c")
    assert AuthService._credential_key("a:b", "c") != AuthService._credential_key("a", "b:c")


def test_purge_user_sessions_removes_hashed_credentials():
    session = _session(9001, "tok_purge_9001")
    AuthService.cache_session(session)
    key = AuthService._credential_key("user9001", "pw")
    auth_module._credential_tokens[key] = session.token

    assert AuthService.purge_user_sessions(9001, username="user9001") == 1
    assert AuthService.get_cached_session("tok_purge_9001") is None
    assert key not in auth_module._credential_tokens


@pytest.mark.asyncio
async def test_failed_login_throttle(monkeypatch):
    async def fail_login(*args, **kwargs):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(auth_module.grimmory_client, "login", fail_login)
    auth_module._failed_logins.clear()

    for _ in range(auth_module._MAX_FAILED_LOGINS):
        with pytest.raises(HTTPException) as exc:
            await AuthService.authenticate_credentials("victim", "wrong", client_ip="1.2.3.4")
        assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await AuthService.authenticate_credentials("victim", "wrong", client_ip="1.2.3.4")
    assert exc.value.status_code == 429

    # A different client IP is not locked out
    with pytest.raises(HTTPException) as exc:
        await AuthService.authenticate_credentials("victim", "wrong", client_ip="5.6.7.8")
    assert exc.value.status_code == 401
    auth_module._failed_logins.clear()


@pytest.mark.asyncio
async def test_cors_does_not_allow_credentials_for_arbitrary_origin():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/",
            headers={"Origin": "https://evil.example", "Cookie": "admin_session=abc"},
        )
    assert resp.headers.get("access-control-allow-credentials") != "true"


@pytest.mark.asyncio
async def test_admin_endpoints_require_admin():
    AuthService.cache_session(_session(9101, "tok_plain_user"))
    AuthService.cache_session(_session(9102, "tok_admin_user", admin=True))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.post("/admin/api/sync")
        assert anon.status_code in (401, 403)
        normal = await client.post("/admin/api/sync", headers={"Authorization": "Bearer tok_plain_user"})
        assert normal.status_code == 403
        users = await client.get("/admin/api/users", headers={"Authorization": "Bearer tok_admin_user"})
        assert users.status_code == 200


def test_no_inline_js_interpolation_of_usernames():
    """Usernames must never be interpolated into inline onclick handlers (XSS)."""
    for rel in ["app/templates/dashboard.html", "app/static/app.js"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"onclick=\"[^\"]*username", text), rel
