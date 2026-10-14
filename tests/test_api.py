from datetime import datetime, timezone, timedelta
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_actuator_info():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/actuator/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "build" in data
        assert data["build"]["version"] == "1.12.0"


@pytest.mark.asyncio
async def test_unauthorized_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without credentials, /api/v1/users/me should return 401 with WWW-Authenticate header
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401
        assert "Basic" in resp.headers.get("www-authenticate", "")

        resp = await client.get("/api/v1/libraries")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_page_renders():
    from app.config import settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/login")
        assert resp.status_code == 200
        assert "Admin Sign In" in resp.text
        assert f"v{settings.app_version}" in resp.text
        assert "/static/app.js" not in resp.text


@pytest.mark.asyncio
async def test_admin_login_flow(monkeypatch):
    from app.main import db
    from app.models.grimmory import GrimmoryLoginResponse, GrimmoryUser, GrimmoryPermissions
    from app.clients import grimmory as grimmory_module

    await db.connect()

    async def mock_login(username, password):
        return GrimmoryLoginResponse(accessToken="tok_admin_test_flow", expires=3600)

    async def mock_get_current_user(token):
        return GrimmoryUser(
            id=1,
            username="admin",
            permissions=GrimmoryPermissions(admin=True),
            assignedLibraries=[],
        )

    async def mock_get_library_books(library_id, token=None):
        return []

    async def mock_get_all_books(token=None):
        return []

    async def mock_get_magic_shelves(token=None):
        return []

    monkeypatch.setattr(grimmory_module.grimmory_client, "login", mock_login)
    monkeypatch.setattr(grimmory_module.grimmory_client, "get_current_user", mock_get_current_user)
    monkeypatch.setattr(grimmory_module.grimmory_client, "get_library_books", mock_get_library_books)
    monkeypatch.setattr(grimmory_module.grimmory_client, "get_all_books", mock_get_all_books)
    monkeypatch.setattr(grimmory_module.grimmory_client, "get_magic_shelves", mock_get_magic_shelves)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/login", data={"username": "admin", "password": "password"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin"
        set_cookie = resp.headers.get("set-cookie", "")
        assert "admin_session=tok_admin_test_flow" in set_cookie
        assert "samesite=lax" in set_cookie.lower()
        assert "path=/" in set_cookie.lower()

        # Follow redirect with the cookie
        client.cookies.set("admin_session", "tok_admin_test_flow")
        resp_admin = await client.get("/admin")
        assert resp_admin.status_code == 200
        assert "Bridge Overview" in resp_admin.text

        # Test logout clears cookies and redirects to /admin/login
        resp_logout = await client.get("/admin/logout")
        assert resp_logout.status_code == 302
        assert resp_logout.headers["location"] == "/admin/login"
        logout_cookie = resp_logout.headers.get("set-cookie", "")
        assert "admin_session" in logout_cookie


@pytest.mark.asyncio
async def test_thumbnail_cache_size_ttl(tmp_path):
    from app.services.cache import DiskThumbnailCache
    cache = DiskThumbnailCache(base_dir=str(tmp_path))

    # Empty cache
    count, size = cache.get_cache_size()
    assert count == 0
    assert size == 0

    # Save a thumbnail
    cache.save_thumbnail("book", "1", b"fake_jpeg_data")
    count, size = cache.get_cache_size()
    assert count == 1
    assert size == len(b"fake_jpeg_data")

    # Calling within TTL uses cached values without re-scanning
    count2, size2 = cache.get_cache_size()
    assert count2 == 1
    assert size2 == len(b"fake_jpeg_data")


@pytest.mark.asyncio
async def test_root_api_returns_version():
    from app.config import settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/", headers={"accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == settings.app_version


def test_load_version_logic(monkeypatch):
    from app.config import _load_version
    # Test env override
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert _load_version() == "9.9.9"

    monkeypatch.delenv("APP_VERSION", raising=False)
    # Test file read from VERSION (the release workflow bumps it, so don't hard-code the value)
    from pathlib import Path
    expected = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    ver = _load_version()
    assert ver == expected



@pytest.mark.asyncio
async def test_admin_api_requires_admin_session(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    # 1. Non-admin user session
    non_admin = UserSession(
        user_id=2,
        username="regular_user",
        token="token_regular",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(non_admin)

    # 2. Admin user session
    admin = UserSession(
        user_id=1,
        username="admin_user",
        token="token_admin",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-admin attempting to trigger page calculation should get 403 Forbidden
        headers_regular = {"Authorization": "Bearer token_regular"}
        resp = await client.post("/admin/api/calculate-pages", json={"missing_only": True}, headers=headers_regular)
        assert resp.status_code == 403
        assert "Admin" in resp.text

        # Admin attempting to trigger page calculation should succeed
        headers_admin = {"Authorization": "Bearer token_admin"}
        resp_admin = await client.post("/admin/api/calculate-pages", json={"missing_only": True}, headers=headers_admin)
        assert resp_admin.status_code == 200
        assert resp_admin.json()["status"] in ["started", "already_running", "no_work"]


@pytest.mark.asyncio
async def test_series_library_filter_and_access_restrictions():
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    # Populate 2 libraries, series and books
    await db.upsert_libraries([
        {"id": 14, "name": "Stories", "paths": []},
        {"id": 20, "name": "Light Novel", "paths": []},
    ])
    await db.upsert_series_batch([
        {"id": "14-story1", "library_id": 14, "name": "Story 1", "slug": "story1", "books_count": 1},
        {"id": "20-novel1", "library_id": 20, "name": "Novel 1", "slug": "novel1", "books_count": 2},
    ])
    await db.upsert_books_batch([
        {"id": 101, "series_id": "14-story1", "library_id": 14, "name": "Story Book 1", "number": 1.0},
        {"id": 201, "series_id": "20-novel1", "library_id": 20, "name": "Novel Book 1", "number": 1.0},
        {"id": 202, "series_id": "20-novel1", "library_id": 20, "name": "Novel Book 2", "number": 2.0},
    ])

    # 1. Restricted user assigned ONLY to library 20
    test_user = UserSession(
        user_id=200,
        username="sample_member",
        token="token_sample_member",
        is_admin=False,
        assigned_library_ids=[20],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(test_user)
    headers = {"Authorization": "Bearer token_sample_member"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Query GET /api/v1/series without library filter:
        # Must ONLY return series from Library 20! Must NOT leak Library 14!
        resp = await client.get("/api/v1/series", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalElements"] == 1
        assert data["content"][0]["id"] == "20-novel1"

        # B. Query POST /api/v1/series/list with condition libraryId=20
        resp_post = await client.post(
            "/api/v1/series/list",
            json={"condition": {"libraryId": {"operator": "is", "value": "20"}}},
            headers=headers,
        )
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert data_post["totalElements"] == 1
        assert data_post["content"][0]["id"] == "20-novel1"

        # C. Query GET /api/v1/series?library_id=14 (unauthorized library):
        # Must return 0 series
        resp_unauth = await client.get("/api/v1/series?library_id=14", headers=headers)
        assert resp_unauth.status_code == 200
        assert resp_unauth.json()["totalElements"] == 0

        # D. Query GET /api/v1/series/20-novel1/books?unpaged=true
        # Must return both books in the series with unpaged metadata
        resp_books = await client.get("/api/v1/series/20-novel1/books?unpaged=true", headers=headers)
        assert resp_books.status_code == 200
        books_data = resp_books.json()
        assert books_data["totalElements"] == 2
        assert len(books_data["content"]) == 2
        assert books_data["pageable"]["unpaged"] is True
        assert books_data["content"][0]["seriesTitle"] == "Novel 1"

        # E. Query GET /api/v1/series/14-story1/books (unauthorized series in Library 14):
        # Must return 404
        resp_forbidden_series = await client.get("/api/v1/series/14-story1/books", headers=headers)
        assert resp_forbidden_series.status_code == 404

        # F. Query GET /api/v1/books?seriesId=20-novel1 (camelCase query parameter)
        resp_books_q = await client.get("/api/v1/books?seriesId=20-novel1", headers=headers)
        assert resp_books_q.status_code == 200
        assert resp_books_q.json()["totalElements"] == 2

        # G. Query POST /api/v1/books/list with condition seriesId
        resp_books_post = await client.post(
            "/api/v1/books/list",
            json={"condition": {"seriesId": {"operator": "is", "value": "20-novel1"}}},
            headers=headers,
        )
        assert resp_books_post.status_code == 200
        post_book_data = resp_books_post.json()
        assert post_book_data["totalElements"] == 2
        first_b = post_book_data["content"][0]
        assert first_b["media"]["epubIsKepub"] is False
        assert isinstance(first_b["metadata"]["isbn"], str)
        assert isinstance(first_b["created"], str) and len(first_b["created"]) > 0
        assert isinstance(first_b["lastModified"], str) and len(first_b["lastModified"]) > 0

        # H. Query GET /api/v1/series/20-novel1/collections (must return 200 with empty list, not 404)
        resp_collections = await client.get("/api/v1/series/20-novel1/collections", headers=headers)
        assert resp_collections.status_code == 200
        assert resp_collections.json() == []

        # I. Query GET /api/v1/series/updated?library_id=20 and ?library_id=14
        resp_updated_20 = await client.get("/api/v1/series/updated?library_id=20", headers=headers)
        assert resp_updated_20.status_code == 200
        assert resp_updated_20.json()["totalElements"] == 1
        assert resp_updated_20.json()["content"][0]["id"] == "20-novel1"

        resp_updated_14 = await client.get("/api/v1/series/updated?library_id=14", headers=headers)
        assert resp_updated_14.status_code == 200
        assert resp_updated_14.json()["totalElements"] == 0

        # J. Query GET /api/v1/books/ondeck?library_id=20 and ?library_id=14
        # Since no books have been started in series 20, on-deck returns 0
        resp_ondeck_20 = await client.get("/api/v1/books/ondeck?library_id=20", headers=headers)
        assert resp_ondeck_20.status_code == 200
        assert resp_ondeck_20.json()["totalElements"] == 0

        resp_ondeck_14 = await client.get("/api/v1/books/ondeck?library_id=14", headers=headers)
        assert resp_ondeck_14.status_code == 200
        assert resp_ondeck_14.json()["totalElements"] == 0


@pytest.mark.asyncio
async def test_ondeck_keep_reading_and_released_sorting():
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    user_id = 300
    user = UserSession(
        user_id=user_id,
        username="reader_user",
        token="token_reader_user",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_reader_user"}

    # Clean residual progress from previous runs if any
    for bid in [301, 302, 303, 304]:
        await db.delete_read_progress(user_id, bid)

    # Setup library, series with 3 books plus an unreleased series
    await db.upsert_libraries([{"id": 30, "name": "Manga Lib", "paths": []}])
    await db.upsert_series_batch([
        {
            "id": "30-manga1",
            "library_id": 30,
            "name": "One Piece",
            "slug": "one-piece",
            "books_count": 3,
        },
        {
            "id": "30-manga-unreleased",
            "library_id": 30,
            "name": "Unreleased Series",
            "slug": "unreleased-series",
            "books_count": 1,
        },
    ])
    await db.upsert_books_batch([
        {
            "id": 301,
            "series_id": "30-manga1",
            "library_id": 30,
            "name": "One Piece Vol 1",
            "number": 1.0,
            "released": "2020-01-01",
            "created": "2023-01-01T00:00:00Z",
        },
        {
            "id": 302,
            "series_id": "30-manga1",
            "library_id": 30,
            "name": "One Piece Vol 2",
            "number": 2.0,
            "released": "2022-05-15",
            "created": "2023-01-02T00:00:00Z",
        },
        {
            "id": 303,
            "series_id": "30-manga1",
            "library_id": 30,
            "name": "One Piece Vol 3",
            "number": 3.0,
            "released": "2024-10-01",
            "created": "2023-01-03T00:00:00Z",
        },
        {
            "id": 304,
            "series_id": "30-manga-unreleased",
            "library_id": 30,
            "name": "One Piece Vol 4 (No release date)",
            "number": 4.0,
            "released": None,
            "created": "2025-01-01T00:00:00Z",
        },
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unstarted series: On Deck and Read More should be empty
        ondeck_resp = await client.get("/api/v1/books/ondeck?library_id=30", headers=headers)
        assert ondeck_resp.status_code == 200
        assert ondeck_resp.json()["totalElements"] == 0

        read_more_resp = await client.post(
            "/api/v1/books/list?page=0&sort=readProgress.readDate,desc",
            json={"condition": {"libraryId": {"operator": "is", "value": "30"}}},
            headers=headers,
        )
        assert read_more_resp.status_code == 200
        assert read_more_resp.json()["totalElements"] == 0

        # 2. Start reading Vol 1 (in-progress)
        await db.upsert_read_progress(
            user_id=user_id,
            book_id=301,
            page=45,
            completed=False,
            read_date="2026-10-01T12:00:00Z",
        )

        # On Deck should now return Vol 1 (in-progress book)
        ondeck_resp = await client.get("/api/v1/books/ondeck?library_id=30", headers=headers)
        assert ondeck_resp.status_code == 200
        assert ondeck_resp.json()["totalElements"] == 1
        assert ondeck_resp.json()["content"][0]["id"] == "301"

        # Read more (Keep Reading) should return Vol 1
        read_more_resp = await client.post(
            "/api/v1/books/list?page=0&sort=readProgress.readDate,desc",
            json={"condition": {"libraryId": {"operator": "is", "value": "30"}}},
            headers=headers,
        )
        assert read_more_resp.status_code == 200
        assert read_more_resp.json()["totalElements"] == 1
        assert read_more_resp.json()["content"][0]["id"] == "301"
        assert read_more_resp.json()["content"][0]["readProgress"]["page"] == 45
        assert read_more_resp.json()["content"][0]["readProgress"]["completed"] is False

        # 3. Finish Vol 1 (completed)
        await db.upsert_read_progress(
            user_id=user_id,
            book_id=301,
            page=200,
            completed=True,
            read_date="2026-10-02T15:00:00Z",
        )

        # On Deck should now return Vol 2 (next unread book)
        ondeck_resp = await client.get("/api/v1/books/ondeck?library_id=30", headers=headers)
        assert ondeck_resp.status_code == 200
        assert ondeck_resp.json()["totalElements"] == 1
        assert ondeck_resp.json()["content"][0]["id"] == "302"

        # 4. Finish Vol 2 and Vol 3
        await db.upsert_read_progress(
            user_id=user_id,
            book_id=302,
            page=200,
            completed=True,
            read_date="2026-10-03T15:00:00Z",
        )
        await db.upsert_read_progress(
            user_id=user_id,
            book_id=303,
            page=200,
            completed=True,
            read_date="2026-10-04T15:00:00Z",
        )

        # On Deck should now be empty (entire series read)
        ondeck_resp = await client.get("/api/v1/books/ondeck?library_id=30", headers=headers)
        assert ondeck_resp.status_code == 200
        assert ondeck_resp.json()["totalElements"] == 0

        # Read more should return all 3 books sorted by readDate descending (303, 302, 301)
        read_more_resp = await client.post(
            "/api/v1/books/list?page=0&sort=readProgress.readDate,desc",
            json={"condition": {"libraryId": {"operator": "is", "value": "30"}}},
            headers=headers,
        )
        assert read_more_resp.status_code == 200
        assert read_more_resp.json()["totalElements"] == 3
        ids = [b["id"] for b in read_more_resp.json()["content"]]
        assert ids == ["303", "302", "301"]

        # 5. Test release date sorting via POST /api/v1/books/list?sort=metadata.releaseDate,desc
        rel_resp = await client.post(
            "/api/v1/books/list?page=0&sort=metadata.releaseDate,desc",
            json={"condition": {"libraryId": {"operator": "is", "value": "30"}}},
            headers=headers,
        )
        assert rel_resp.status_code == 200
        assert rel_resp.json()["totalElements"] == 3
        rel_ids = [b["id"] for b in rel_resp.json()["content"]]
        # Vol 3 (2024-10-01) > Vol 2 (2022-05-15) > Vol 1 (2020-01-01)
        assert rel_ids == ["303", "302", "301"]
        assert rel_resp.json()["content"][0]["metadata"]["releaseDate"] == "2024-10-01"

        # 6. Test GET /api/v1/books/released?library_id=30
        released_endpoint = await client.get("/api/v1/books/released?library_id=30", headers=headers)
        assert released_endpoint.status_code == 200
        assert released_endpoint.json()["totalElements"] == 3
        assert [b["id"] for b in released_endpoint.json()["content"]] == ["303", "302", "301"]


@pytest.mark.asyncio
async def test_removed_book_graceful_handling(monkeypatch):
    import httpx
    from app.main import db, page_calculator
    from app.clients.grimmory import grimmory_client
    from app.services.sync import SyncService

    await db.connect()
    # Insert a book that will simulate being removed upstream
    await db.upsert_libraries([{"id": 99, "name": "Temp Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "99-deleted-series", "library_id": 99, "name": "Deleted Series", "slug": "deleted-series", "books_count": 1}])
    await db.upsert_books_batch([{
        "id": 99999,
        "series_id": "99-deleted-series",
        "library_id": 99,
        "name": "Ghost Book",
        "number": 1.0,
        "page_count": 0,
        "deleted": 0,
    }])

    # 1. Verify book is active in DB
    book = await db.get_book_by_id(99999)
    assert book is not None
    assert book["deleted"] == 0

    # 2. Mock Grimmory download returning 404
    async def mock_download_404(book_id, token=None):
        req = httpx.Request("GET", f"http://test/api/v1/books/{book_id}/download")
        resp = httpx.Response(404, request=req)
        raise httpx.HTTPStatusError("Client error '404 '", request=req, response=resp)

    monkeypatch.setattr(grimmory_client, "download_book_bytes", mock_download_404)

    # 3. Calculate pages for the book - should mark deleted gracefully
    result = await page_calculator.calculate_and_save_book({"id": 99999, "name": "Ghost Book", "book_type": "EPUB"})
    assert result == "removed"

    # 4. Verify book is marked deleted and orphaned series was cleaned up
    book_after = await db.get_book_by_id(99999)
    assert book_after is None  # get_book_by_id filters deleted by default
    book_raw = await db.get_book_by_id(99999, include_deleted=True)
    assert book_raw["deleted"] == 1

    series_after = await db.get_series_by_id("99-deleted-series")
    assert series_after is None  # empty series cleaned up


@pytest.mark.asyncio
async def test_user_connect_captures_data_and_checks_grimmory_read_states(monkeypatch):
    import asyncio
    import base64
    from app.main import db
    from app.clients.grimmory import grimmory_client
    from app.models.grimmory import GrimmoryLoginResponse, GrimmoryPermissions, GrimmoryUser
    from app.services.user_sync import user_sync_service

    await db.connect()

    # 1. Populate test books in Library 40
    await db.upsert_libraries([{"id": 40, "name": "Grimmory Test Lib", "paths": []}])
    await db.upsert_series_batch([{
        "id": "40-series1",
        "library_id": 40,
        "name": "Sync Series",
        "slug": "sync-series",
        "books_count": 3,
    }])
    await db.upsert_books_batch([
        {
            "id": 401,
            "series_id": "40-series1",
            "library_id": 40,
            "name": "Sync Book 1",
            "number": 1.0,
            "page_count": 250,
            "book_type": "EPUB",
        },
        {
            "id": 402,
            "series_id": "40-series1",
            "library_id": 40,
            "name": "Sync Book 2",
            "number": 2.0,
            "page_count": 120,
            "book_type": "CBZ",
        },
        {
            "id": 403,
            "series_id": "40-series1",
            "library_id": 40,
            "name": "Sync Book 3",
            "number": 3.0,
            "page_count": 80,
            "book_type": "PDF",
        },
    ])

    test_user_id = 400
    for bid in [401, 402, 403]:
        await db.delete_read_progress(test_user_id, bid)

    # 2. Mock Grimmory authentication and upstream book fetch
    mock_login_resp = GrimmoryLoginResponse(accessToken="mock_token_alice_123", expires=3600)
    mock_user_resp = GrimmoryUser(
        id=test_user_id,
        username="alice",
        permissions=GrimmoryPermissions(admin=False),
        assignedLibraries=[40],
    )

    async def mock_login(username, password):
        assert username == "alice"
        assert password == "secret123"
        return mock_login_resp

    async def mock_get_current_user(token):
        assert token == "mock_token_alice_123"
        return mock_user_resp

    # Grimmory returns live reading states for alice
    mock_grimmory_books = [
        {
            "id": 401,
            "readStatus": "READ",
            "dateFinished": "2026-10-05T12:00:00Z",
            "metadata": {"pageCount": 250},
        },
        {
            "id": 402,
            "readStatus": "READING",
            "cbxProgress": {"page": 60, "percentage": 50.0, "lastRead": "2026-10-06T15:30:00Z"},
            "metadata": {"pageCount": 120},
        },
        {
            "id": 403,
            "readStatus": "UNREAD",
            "metadata": {"pageCount": 80},
        },
    ]

    async def mock_get_library_books(library_id, token=None):
        assert token == "mock_token_alice_123"
        return mock_grimmory_books

    async def mock_get_all_books(token=None):
        assert token == "mock_token_alice_123"
        return mock_grimmory_books

    async def mock_get_magic_shelves(token=None):
        return []

    monkeypatch.setattr(grimmory_client, "login", mock_login)
    monkeypatch.setattr(grimmory_client, "get_current_user", mock_get_current_user)
    monkeypatch.setattr(grimmory_client, "get_library_books", mock_get_library_books)
    monkeypatch.setattr(grimmory_client, "get_all_books", mock_get_all_books)
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", mock_get_magic_shelves)

    # 3. User connects to the server with Basic Auth (e.g. via Komic)
    basic_creds = base64.b64encode(b"alice:secret123").decode("utf-8")
    headers = {"Authorization": f"Basic {basic_creds}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial connection: GET /api/v1/users/me
        resp_me = await client.get("/api/v1/users/me", headers=headers)
        assert resp_me.status_code == 200
        assert resp_me.json()["id"] == "400"

        # Allow background sync task to complete
        for _ in range(40):
            await asyncio.sleep(0.05)
            user_record = await db.get_user_by_id(test_user_id)
            if user_record and user_record.get("last_sync_progress_at") is not None:
                break
        assert user_record is not None
        assert user_record["username"] == "alice"
        assert user_record["token"] == "mock_token_alice_123"
        assert user_record["last_connected_at"] is not None
        assert user_record["last_sync_progress_at"] is not None

        # 5. Verify read states were captured and stored in read_progress table
        # Book 401 is completed (READ)
        p401 = await db.get_book_read_progress(test_user_id, 401)
        assert p401 is not None
        assert p401["completed"] == 1
        assert p401["page"] == 250
        assert "2026-10-05" in p401["read_date"]

        # Book 402 is in-progress (READING, page 60)
        p402 = await db.get_book_read_progress(test_user_id, 402)
        assert p402 is not None
        assert p402["completed"] == 0
        assert p402["page"] == 60
        assert "2026-10-06" in p402["read_date"]

        # Book 403 is unread (not in read_progress)
        p403 = await db.get_book_read_progress(test_user_id, 403)
        assert p403 is None

        # 6. Verify On Deck immediately returns Book 402 (in-progress book from Grimmory)
        resp_ondeck = await client.get("/api/v1/books/ondeck?library_id=40", headers=headers)
        assert resp_ondeck.status_code == 200
        ondeck_content = resp_ondeck.json()["content"]
        assert len(ondeck_content) == 1
        assert ondeck_content[0]["id"] == "402"
        assert ondeck_content[0]["readProgress"]["page"] == 60
        assert ondeck_content[0]["readProgress"]["completed"] is False

        # 7. Verify Keep Reading / Read More shelf returns [402, 401]
        resp_read_more = await client.post(
            "/api/v1/books/list?page=0&sort=readProgress.readDate,desc",
            json={"condition": {"libraryId": {"operator": "is", "value": "40"}}},
            headers=headers,
        )
        assert resp_read_more.status_code == 200
        assert resp_read_more.json()["totalElements"] == 2
        read_more_ids = [b["id"] for b in resp_read_more.json()["content"]]
        assert read_more_ids == ["402", "401"]


@pytest.mark.asyncio
async def test_claim_and_oauth_endpoints():
    from datetime import datetime, timezone, timedelta
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    session = UserSession(
        user_id=1,
        username="claim_user",
        token="token_claim",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated claim check
        resp = await client.get("/api/v1/claim")
        assert resp.status_code == 200
        assert resp.json() == {"isClaimed": True}

        # 2. Claim attempt returns 400
        resp_post = await client.post("/api/v1/claim")
        assert resp_post.status_code == 400
        assert "already been claimed" in resp_post.json()["detail"]

        # 3. OAuth2 providers check
        resp_oauth = await client.get("/api/v1/oauth2/providers")
        assert resp_oauth.status_code == 200
        assert resp_oauth.json() == []

        # 4. User API keys
        resp_keys_unauth = await client.get("/api/v2/users/me/api-keys")
        assert resp_keys_unauth.status_code == 401

        resp_keys_auth = await client.get("/api/v2/users/me/api-keys", headers={"Authorization": "Bearer token_claim"})
        assert resp_keys_auth.status_code == 200
        assert resp_keys_auth.json() == []


@pytest.mark.asyncio
async def test_client_settings_endpoints():
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()

    admin = UserSession(
        user_id=10,
        username="settings_admin",
        token="token_s_admin",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    user = UserSession(
        user_id=20,
        username="settings_user",
        token="token_s_user",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(admin)
    AuthService.cache_session(user)

    admin_headers = {"Authorization": "Bearer token_s_admin"}
    user_headers = {"Authorization": "Bearer token_s_user"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Global settings - unauthorized fetch only returns allowUnauthorized=True
        resp = await client.get("/api/v1/client-settings/global/list")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

        # 2. Admin saves global settings
        patch_resp = await client.patch(
            "/api/v1/client-settings/global",
            json={
                "webui.theme": {"value": "dark", "allowUnauthorized": True},
                "server.internal_key": {"value": "secret", "allowUnauthorized": False},
            },
            headers=admin_headers,
        )
        assert patch_resp.status_code == 204

        # Non-admin cannot save global settings
        patch_user = await client.patch(
            "/api/v1/client-settings/global",
            json={"webui.theme": {"value": "light", "allowUnauthorized": True}},
            headers=user_headers,
        )
        assert patch_user.status_code == 403

        # 3. Unauthenticated GET receives only allowUnauthorized=True
        unauth_get = await client.get("/api/v1/client-settings/global/list")
        assert unauth_get.status_code == 200
        unauth_data = unauth_get.json()
        assert "webui.theme" in unauth_data
        assert unauth_data["webui.theme"]["value"] == "dark"
        assert "server.internal_key" not in unauth_data

        # Admin GET receives all global settings
        admin_get = await client.get("/api/v1/client-settings/global/list", headers=admin_headers)
        assert admin_get.status_code == 200
        admin_data = admin_get.json()
        assert "webui.theme" in admin_data
        assert "server.internal_key" in admin_data

        # 4. User saves and retrieves user settings
        user_patch = await client.patch(
            "/api/v1/client-settings/user",
            json={"reader.mode": {"value": "continuous"}},
            headers=user_headers,
        )
        assert user_patch.status_code == 204

        user_get = await client.get("/api/v1/client-settings/user/list", headers=user_headers)
        assert user_get.status_code == 200
        assert user_get.json().get("reader.mode") == {"value": "continuous", "allowUnauthorized": False}

        # 5. User deletes user setting
        user_del = await client.request(
            "DELETE",
            "/api/v1/client-settings/user",
            json=["reader.mode"],
            headers=user_headers,
        )
        assert user_del.status_code == 204

        user_get_after = await client.get("/api/v1/client-settings/user/list", headers=user_headers)
        assert user_get_after.status_code == 200
        assert "reader.mode" not in user_get_after.json()


@pytest.mark.asyncio
async def test_r2_progression_endpoints_and_grimmory_sync(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock
    from app.clients.grimmory import grimmory_client
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    # Insert test book
    await db.upsert_libraries([{"id": 50, "name": "Novels", "paths": []}])
    await db.upsert_series_batch([{"id": "50-series1", "library_id": 50, "name": "Prog Series", "slug": "prog-series"}])
    await db.upsert_books_batch([{
        "id": 501,
        "series_id": "50-series1",
        "library_id": 50,
        "name": "Volume 1",
        "number": 1.0,
        "page_count": 200,
    }])

    user = UserSession(
        user_id=50,
        username="r2_reader",
        token="token_r2",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_r2"}

    # Mock Grimmory update_read_progress call
    mock_grimmory_update = AsyncMock(return_value=True)
    monkeypatch.setattr(grimmory_client, "update_read_progress", mock_grimmory_update)
    mock_grimmory_reset = AsyncMock(return_value=True)
    monkeypatch.setattr(grimmory_client, "reset_read_progress", mock_grimmory_reset)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Non-existent book returns 404
        resp_404 = await client.get("/api/v1/books/999999/progression", headers=headers)
        assert resp_404.status_code == 404

        # 2. Book with no stored progression returns 204 No Content
        resp_initial = await client.get("/api/v1/books/501/progression", headers=headers)
        assert resp_initial.status_code == 204

        # 3. Client saves Readium R2 progression via PUT
        r2_payload = {
            "modified": "2026-10-07T19:35:00Z",
            "device": {"id": "komic-app", "name": "Komic Reader"},
            "locator": {
                "href": "OEBPS/chapter3.xhtml",
                "type": "application/xhtml+xml",
                "title": "Chapter 3",
                "locations": {
                    "progression": 0.45,
                    "totalProgression": 0.45,
                    "position": 90,
                },
            },
        }
        resp_put = await client.put(
            "/api/v1/books/501/progression",
            json=r2_payload,
            headers=headers,
        )
        assert resp_put.status_code == 204

        # 4. Verify Grimmory client was called with epub, cbx, and pdf progress
        assert mock_grimmory_update.called
        req_sent = mock_grimmory_update.call_args[0][0]
        assert req_sent.bookId == 501
        assert req_sent.epubProgress.percentage == 45.0
        assert req_sent.epubProgress.href == "OEBPS/chapter3.xhtml"
        assert req_sent.cbxProgress.page == 90
        assert req_sent.cbxProgress.percentage == 45.0

        # 5. Subsequent GET /api/v1/books/501/progression returns stored JSON
        resp_get = await client.get("/api/v1/books/501/progression", headers=headers)
        assert resp_get.status_code == 200
        saved_prog = resp_get.json()
        assert saved_prog["device"]["name"] == "Komic Reader"
        assert saved_prog["locator"]["href"] == "OEBPS/chapter3.xhtml"
        assert saved_prog["locator"]["locations"]["position"] == 90

        # 6. Verify local read_progress was updated for Komga home screen
        resp_rp = await client.get("/api/v1/books/501/read-progress", headers=headers)
        assert resp_rp.status_code == 200
        assert resp_rp.json()["page"] == 90
        assert resp_rp.json()["completed"] is False

        # 7. DELETE progression removes both R2 and read_progress
        resp_del = await client.delete("/api/v1/books/501/progression", headers=headers)
        assert resp_del.status_code == 204
        assert mock_grimmory_reset.called

        resp_get_after = await client.get("/api/v1/books/501/progression", headers=headers)
        assert resp_get_after.status_code == 204


@pytest.mark.asyncio
async def test_user_sync_handles_null_progress_fields(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock
    from app.clients.grimmory import grimmory_client
    from app.main import db
    from app.models.internal import UserSession
    from app.services.user_sync import user_sync_service

    await db.connect()
    user = UserSession(
        user_id=88,
        username="testuser",
        token="token_testuser",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    # Mock Grimmory returning null values in cbxProgress, pdfProgress, epubProgress
    mock_books = [
        {
            "id": 8801,
            "title": "Novel with null progress",
            "readStatus": "READING",
            "metadata": {"pageCount": 150},
            "cbxProgress": {"percentage": None, "page": None, "lastRead": None},
            "pdfProgress": {"percentage": None, "page": None, "lastRead": None},
            "epubProgress": {"percentage": None, "href": None, "lastRead": None},
            "readProgress": None,
        },
        {
            "id": 8802,
            "title": "Completed novel",
            "readStatus": "READ",
            "dateFinished": "2026-10-07T12:00:00Z",
            "metadata": {"pageCount": 200},
            "cbxProgress": None,
            "pdfProgress": None,
            "epubProgress": None,
        },
    ]

    monkeypatch.setattr(grimmory_client, "get_library_books", AsyncMock(return_value=[]))
    monkeypatch.setattr(grimmory_client, "get_all_books", AsyncMock(return_value=mock_books))
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", AsyncMock(return_value=[]))

    # Synchronizing read states should succeed without raising TypeError
    success = await user_sync_service.capture_and_sync_user(user, force=True)
    assert success is True

    # Verify book 8802 was saved as completed
    p8802 = await db.get_book_read_progress(88, 8802)
    assert p8802 is not None
    assert p8802["completed"] == 1
    assert p8802["page"] == 200


@pytest.mark.asyncio
async def test_api_key_lifecycle_and_auth():
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    user = UserSession(
        user_id=77,
        username="kmreader_user",
        token="token_kmreader",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    # Also save user to database so API key lookup can resolve it
    await db.upsert_user(
        user_id=77,
        username="kmreader_user",
        token="token_kmreader",
        is_admin=False,
        assigned_libraries=[],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login check with remember-me=true sets cookies
        resp_me = await client.get("/api/v2/users/me?remember-me=true", headers={"Authorization": "Bearer token_kmreader"})
        assert resp_me.status_code == 200
        assert "remember-me" in resp_me.cookies or "SESSION" in resp_me.cookies or "KOMGA-SESSION" in resp_me.cookies

        # 2. KMreader creates an API key
        resp_create = await client.post(
            "/api/v2/users/me/api-keys",
            json={"comment": "KMreader iPad"},
            headers={"Authorization": "Bearer token_kmreader"},
        )
        assert resp_create.status_code == 201
        key_data = resp_create.json()
        assert key_data["comment"] == "KMreader iPad"
        api_key = key_data["key"]
        key_id = key_data["id"]

        # 3. GET /api/v2/users/me/api-keys returns the created key
        resp_list = await client.get("/api/v2/users/me/api-keys", headers={"Authorization": "Bearer token_kmreader"})
        assert resp_list.status_code == 200
        keys_list = resp_list.json()
        assert any(k["id"] == key_id for k in keys_list)

        # 4. Use X-API-Key header to authenticate
        resp_x_api = await client.get("/api/v1/libraries", headers={"X-API-Key": api_key})
        assert resp_x_api.status_code == 200

        # 5. Use X-Auth-Token header to authenticate
        resp_x_auth = await client.get("/api/v1/libraries", headers={"X-Auth-Token": api_key})
        assert resp_x_auth.status_code == 200

        # 6. DELETE the API key
        resp_del = await client.delete(f"/api/v2/users/me/api-keys/{key_id}", headers={"Authorization": "Bearer token_kmreader"})
        assert resp_del.status_code == 204

        # 7. Using deleted API key fails with 401
        client.cookies.clear()
        resp_revoked = await client.get("/api/v1/libraries", headers={"X-API-Key": api_key})
        assert resp_revoked.status_code == 401


@pytest.mark.asyncio
async def test_sse_events_endpoint():
    from datetime import datetime, timezone, timedelta
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import Request
    from app.models.internal import UserSession
    from app.routers.komga_auth import sse_events
    from app.services.auth import AuthService

    user = UserSession(
        user_id=1,
        username="sse_user",
        token="token_sse",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # SSE endpoint requires auth
        resp_unauth = await client.get("/sse/v1/events")
        assert resp_unauth.status_code == 401

    # Test SSE response and generator directly to avoid ASGITransport infinite loop
    mock_request = MagicMock(spec=Request)
    mock_request.is_disconnected = AsyncMock(return_value=False)
    sse_resp = await sse_events(mock_request, user=user)
    assert sse_resp.status_code == 200
    assert sse_resp.media_type == "text/event-stream"
    assert sse_resp.headers.get("Cache-Control") == "no-cache"

    gen = sse_resp.body_iterator
    first_chunk = await anext(gen)
    assert ":keepalive" in first_chunk
    await gen.aclose()


@pytest.mark.asyncio
async def test_read_progress_isolation_between_users():
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    # Create library, series and book 601
    await db.upsert_libraries([{"id": 60, "name": "Shared Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "60-series1", "library_id": 60, "name": "Shared Series", "slug": "shared-series"}])
    await db.upsert_books_batch([{
        "id": 601,
        "series_id": "60-series1",
        "library_id": 60,
        "name": "Shared Book",
        "number": 1.0,
        "page_count": 100,
        "raw_json": '{"readStatus": "READ", "cbxProgress": {"page": 100, "percentage": 100.0}}',
    }])

    # saetron (user 1) has read the book
    saetron = UserSession(
        user_id=1,
        username="saetron",
        token="token_saetron",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    # testuser (user 2) has NOT read the book
    testuser = UserSession(
        user_id=2,
        username="testuser",
        token="token_testuser",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(saetron)
    AuthService.cache_session(testuser)

    await db.upsert_read_progress(user_id=1, book_id=601, page=100, completed=True, read_date="2026-10-07T10:00:00Z")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. testuser queries book 601 by ID -> readProgress must be None!
        resp_testuser = await client.get("/api/v1/books/601", headers={"Authorization": "Bearer token_testuser"})
        assert resp_testuser.status_code == 200
        book_testuser = resp_testuser.json()
        assert book_testuser["readProgress"] is None

        # 2. testuser queries book list -> readProgress must be None!
        resp_list_testuser = await client.post(
            "/api/v1/books/list?page=0",
            json={"condition": {"libraryId": {"operator": "is", "value": "60"}}},
            headers={"Authorization": "Bearer token_testuser"},
        )
        assert resp_list_testuser.status_code == 200
        content_testuser = resp_list_testuser.json()["content"]
        assert len(content_testuser) == 1
        assert content_testuser[0]["readProgress"] is None

        # 3. saetron queries book 601 -> readProgress must be completed!
        resp_saetron = await client.get("/api/v1/books/601", headers={"Authorization": "Bearer token_saetron"})
        assert resp_saetron.status_code == 200
        book_saetron = resp_saetron.json()
        assert book_saetron["readProgress"] is not None
        assert book_saetron["readProgress"]["completed"] is True
        assert book_saetron["readProgress"]["page"] == 100


@pytest.mark.asyncio
async def test_download_fallback_on_403(monkeypatch):
    import httpx
    from datetime import datetime, timezone, timedelta
    from app.clients.grimmory import grimmory_client
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    await db.upsert_libraries([{"id": 70, "name": "Novels", "paths": []}])
    await db.upsert_series_batch([{"id": "70-series1", "library_id": 70, "name": "Novel Series", "slug": "novel-series"}])
    await db.upsert_books_batch([{
        "id": 701,
        "series_id": "70-series1",
        "library_id": 70,
        "name": "Novel 701",
        "number": 1.0,
        "page_count": 100,
    }])

    testuser = UserSession(
        user_id=2,
        username="testuser",
        token="token_restricted",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(testuser)

    # Mock grimmory_client:
    # If Authorization header has token_restricted -> return 403 Forbidden
    # If Authorization header has sync token -> return 200 OK with bytes
    async def mock_get_sync_token(*args, **kwargs):
        return "admin_sync_token"
    monkeypatch.setattr(grimmory_client, "get_sync_token", mock_get_sync_token)

    real_send = grimmory_client._http_client.send

    async def mock_send(request: httpx.Request, *args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if "token_restricted" in auth:
            return httpx.Response(status_code=403, request=request)
        return httpx.Response(status_code=200, content=b"PK\x03\x04mock_epub_data", headers={"Content-Type": "application/epub+zip"}, request=request)

    monkeypatch.setattr(grimmory_client._http_client, "send", mock_send)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/books/701/file", headers={"Authorization": "Bearer token_restricted"})
        assert resp.status_code == 200
        assert resp.content == b"PK\x03\x04mock_epub_data"


@pytest.mark.asyncio
async def test_periodic_user_sync_and_data_persistence(monkeypatch):
    from unittest.mock import AsyncMock
    from app.main import db
    from app.services.user_sync import user_sync_service

    await db.connect()

    # 1. Store users with tokens in SQLite
    await db.upsert_user(
        user_id=801,
        username="user_periodic_a",
        token="token_p_a",
        is_admin=False,
        assigned_libraries=[70],
    )
    await db.upsert_user(
        user_id=802,
        username="user_periodic_b",
        token="token_p_b",
        is_admin=False,
        assigned_libraries=[70],
    )

    # 2. Verify get_user_by_token and get_users_with_tokens
    user_a = await db.get_user_by_token("token_p_a")
    assert user_a is not None
    assert user_a["id"] == 801
    assert user_a["username"] == "user_periodic_a"

    active_users = await db.get_users_with_tokens()
    active_ids = [u["id"] for u in active_users]
    assert 801 in active_ids
    assert 802 in active_ids

    # 3. Test sync_all_active_users invokes sync for both users
    sync_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(user_sync_service, "capture_and_sync_user", sync_mock)

    await user_sync_service.sync_all_active_users()

    # Verify capture_and_sync_user was called for each active user
    called_user_ids = [call.args[0].user_id for call in sync_mock.call_args_list]
    assert 801 in called_user_ids
    assert 802 in called_user_ids

    # 4. Verify request handling uses SQLite cache directly without blocking sync
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request uses token_p_a: resolves via db.get_user_by_token
        resp = await client.get("/api/v1/libraries", headers={"Authorization": "Bearer token_p_a"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_webui_sync_logs_and_clear():
    from datetime import datetime, timezone, timedelta
    from app.main import sync_service
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    # Create admin session
    admin = UserSession(
        user_id=1,
        username="admin_logger",
        token="token_admin_log",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(admin)

    # Populate sample sync logs
    sync_service.status.logs = ["[12:00:00] Synchronized 3 libraries", "[12:00:01] Fetched 100 books"]
    sync_service.status.is_running = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer token_admin_log"}

        # 1. Status API includes sync_job with logs
        resp_status = await client.get("/admin/api/status", headers=headers)
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert "sync_job" in data
        assert len(data["sync_job"]["logs"]) == 2
        assert "Synchronized 3 libraries" in data["sync_job"]["logs"][0]

        # 2. Clear logs endpoint
        resp_clear = await client.post("/admin/api/sync/clear-logs", headers=headers)
        assert resp_clear.status_code == 200
        assert resp_clear.json() == {"status": "cleared"}

        # 3. Status API now reflects empty logs
        resp_status_after = await client.get("/admin/api/status", headers=headers)
        assert resp_status_after.status_code == 200
        assert resp_status_after.json()["sync_job"]["logs"] == []


@pytest.mark.asyncio
async def test_series_and_book_read_progress_endpoints(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService
    from app.clients.grimmory import grimmory_client

    await db.connect()
    user_id = 880
    user = UserSession(
        user_id=user_id,
        username="series_reader",
        token="token_series_reader",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_series_reader"}

    # Mock grimmory client calls
    async def mock_update_read_progress(req, token):
        return True

    async def mock_reset_read_progress(bids, token):
        return True

    monkeypatch.setattr(grimmory_client, "update_read_progress", mock_update_read_progress)
    monkeypatch.setattr(grimmory_client, "reset_read_progress", mock_reset_read_progress)

    # 1. Setup test library, series, and 3 books
    await db.upsert_libraries([{"id": 88, "name": "Series Read Lib", "paths": []}])
    await db.upsert_series_batch([
        {
            "id": "88-test-series",
            "library_id": 88,
            "name": "My Little Sister",
            "slug": "my-little-sister",
            "books_count": 3,
        }
    ])
    await db.upsert_books_batch([
        {
            "id": 881,
            "series_id": "88-test-series",
            "library_id": 88,
            "name": "Vol 1",
            "number": 1.0,
            "page_count": 100,
        },
        {
            "id": 882,
            "series_id": "88-test-series",
            "library_id": 88,
            "name": "Vol 2",
            "number": 2.0,
            "page_count": 100,
        },
        {
            "id": 883,
            "series_id": "88-test-series",
            "library_id": 88,
            "name": "Vol 3",
            "number": 3.0,
            "page_count": 100,
        },
    ])
    for bid in [881, 882, 883]:
        await db.delete_read_progress(user_id, bid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial series check: 0 read, 3 unread
        resp_s = await client.get("/api/v1/series/88-test-series", headers=headers)
        assert resp_s.status_code == 200
        s_data = resp_s.json()
        assert s_data["booksCount"] == 3
        assert s_data["booksReadCount"] == 0
        assert s_data["booksUnreadCount"] == 3

        # 2. Test reading to page 100 of 100 (without completed=true) marks book completed automatically per Komga spec
        resp_p = await client.patch(
            "/api/v1/books/881/read-progress",
            json={"page": 100},
            headers=headers,
        )
        assert resp_p.status_code == 204

        resp_b1 = await client.get("/api/v1/books/881", headers=headers)
        assert resp_b1.status_code == 200
        assert resp_b1.json()["readProgress"]["completed"] is True
        assert resp_b1.json()["readProgress"]["page"] == 100

        # Series now reflects 1 read, 2 unread
        resp_s_after1 = await client.get("/api/v1/series/88-test-series", headers=headers)
        assert resp_s_after1.json()["booksReadCount"] == 1
        assert resp_s_after1.json()["booksUnreadCount"] == 2

        # 3. Test POST /api/v1/series/{series_id}/read-progress marks entire series as read
        resp_s_read = await client.post("/api/v1/series/88-test-series/read-progress", headers=headers)
        assert resp_s_read.status_code == 204

        resp_s_after_all = await client.get("/api/v1/series/88-test-series", headers=headers)
        assert resp_s_after_all.json()["booksReadCount"] == 3
        assert resp_s_after_all.json()["booksUnreadCount"] == 0

        # Query books list with readStatus=READ returns all 3 books
        resp_read_books = await client.post(
            "/api/v1/books/list?page=0&size=20",
            json={"condition": {"allOf": [{"readStatus": {"operator": "is", "value": "READ"}}]}},
            headers=headers,
        )
        assert resp_read_books.status_code == 200
        read_book_ids = [b["id"] for b in resp_read_books.json()["content"] if b["id"] in ["881", "882", "883"]]
        assert len(read_book_ids) == 3

        # 4. Test DELETE /api/v1/series/{series_id}/read-progress marks entire series as unread
        resp_s_unread = await client.delete("/api/v1/series/88-test-series/read-progress", headers=headers)
        assert resp_s_unread.status_code == 204

        resp_s_after_del = await client.get("/api/v1/series/88-test-series", headers=headers)
        assert resp_s_after_del.json()["booksReadCount"] == 0
        assert resp_s_after_del.json()["booksUnreadCount"] == 3

        # 5. Test Tachiyomi / Mihon v2 series endpoints
        # PUT /api/v2/series/{series_id}/read-progress/tachiyomi sets read books up to number
        resp_tachi_put = await client.put(
            "/api/v2/series/88-test-series/read-progress/tachiyomi",
            json={"lastBookNumberSortRead": 2.0},
            headers=headers,
        )
        assert resp_tachi_put.status_code == 204

        resp_tachi_get = await client.get("/api/v2/series/88-test-series/read-progress/tachiyomi", headers=headers)
        assert resp_tachi_get.status_code == 200
        tachi_data = resp_tachi_get.json()
        assert tachi_data["booksCount"] == 3
        assert tachi_data["booksReadCount"] == 2
        assert tachi_data["booksUnreadCount"] == 1

        # 6. Test POST /api/v1/books/{book_id}/read-progress with completed=false resets progress to UNREAD
        resp_reset_b = await client.post(
            "/api/v1/books/881/read-progress",
            json={"completed": False},
            headers=headers,
        )
        assert resp_reset_b.status_code == 204

        resp_b1_reset = await client.get("/api/v1/books/881", headers=headers)
        assert resp_b1_reset.json()["readProgress"] is None


@pytest.mark.asyncio
async def test_grimmory_url_redirects(monkeypatch):
    from app.config import settings
    from app.main import db

    await db.connect()
    # Insert series and book to test DB resolution
    await db.upsert_libraries([{"id": 86, "name": "Eighty Six Library"}])
    await db.upsert_series_batch([
        {
            "id": "86-86-eighty-six-alter-ae04b64f",
            "library_id": 86,
            "name": "86-EIGHTY-SIX Alter",
            "slug": "86-eighty-six-alter-ae04b64f",
            "books_count": 1,
        }
    ])
    await db.upsert_books_batch([
        {
            "id": 12345,
            "series_id": "86-86-eighty-six-alter-ae04b64f",
            "library_id": 86,
            "name": "86 Vol 1",
            "book_type": "EPUB",
        },
        {
            "id": 12346,
            "series_id": "86-86-eighty-six-alter-ae04b64f",
            "library_id": 86,
            "name": "86 Manga 1",
            "book_type": "CBZ",
        },
    ])

    monkeypatch.setattr(settings, "grimmory_public_url", "http://public-grimmory:9090")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        # 1. /series/{id} maps composite ID to Grimmory's /series/:seriesName scheme
        resp_s = await client.get("/series/86-86-eighty-six-alter-ae04b64f?tab=books")
        assert resp_s.status_code == 307
        assert resp_s.headers["location"] == "http://public-grimmory:9090/series/86-EIGHTY-SIX%20Alter?tab=books"

        # 1b. /series empty redirects to series browser
        resp_s_empty = await client.get("/series")
        assert resp_s_empty.status_code == 307
        assert resp_s_empty.headers["location"] == "http://public-grimmory:9090/series"

        # 2. /book/{id} redirects to Grimmory's /book/:bookId scheme
        resp_b = await client.get("/book/12345")
        assert resp_b.status_code == 307
        assert resp_b.headers["location"] == "http://public-grimmory:9090/book/12345"

        # 2b. /book/{id}/read maps to reader component (/ebook-reader/book/:id or /cbx-reader/book/:id)
        resp_read_epub = await client.get("/book/12345/read")
        assert resp_read_epub.status_code == 307
        assert resp_read_epub.headers["location"] == "http://public-grimmory:9090/ebook-reader/book/12345"

        resp_read_cbz = await client.get("/book/12346/read")
        assert resp_read_cbz.status_code == 307
        assert resp_read_cbz.headers["location"] == "http://public-grimmory:9090/cbx-reader/book/12346"

        # 3. /library/{id} redirects to Grimmory's /library/:libraryId/books scheme
        resp_l = await client.get("/library/14")
        assert resp_l.status_code == 307
        assert resp_l.headers["location"] == "http://public-grimmory:9090/library/14/books"

        resp_l_empty = await client.get("/library")
        assert resp_l_empty.status_code == 307
        assert resp_l_empty.headers["location"] == "http://public-grimmory:9090/all-books"

        # 4. /shelf/{id} and /collection/{id} redirect to /shelf/:shelfId/books
        resp_shelf = await client.get("/collection/7")
        assert resp_shelf.status_code == 307
        assert resp_shelf.headers["location"] == "http://public-grimmory:9090/shelf/7/books"

        # 5. /authors redirects to /authors
        resp_authors = await client.get("/authors")
        assert resp_authors.status_code == 307
        assert resp_authors.headers["location"] == "http://public-grimmory:9090/authors"


@pytest.mark.asyncio
async def test_multi_column_and_spring_sorting():
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    user_id = 999
    token = "token_sort_test"
    user = UserSession(
        user_id=user_id,
        username="sort_tester",
        token=token,
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": f"Bearer {token}"}

    # Upsert series and books with specific titles and numbers
    await db.upsert_series_batch([
        {"id": "s-alpha", "library_id": 1, "name": "Alpha Series", "slug": "alpha", "books_count": 2},
        {"id": "s-beta", "library_id": 1, "name": "Beta Series", "slug": "beta", "books_count": 2},
    ])
    await db.upsert_books_batch([
        {"id": 9001, "series_id": "s-beta", "library_id": 1, "name": "Beta Vol 2", "number": 2.0},
        {"id": 9002, "series_id": "s-beta", "library_id": 1, "name": "Beta Vol 1", "number": 1.0},
        {"id": 9003, "series_id": "s-alpha", "library_id": 1, "name": "Alpha Vol 2", "number": 2.0},
        {"id": 9004, "series_id": "s-alpha", "library_id": 1, "name": "Alpha Vol 1", "number": 1.0},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test sort=series,metadata.numberSort,asc
        resp = await client.post(
            "/api/v1/books/list?page=0&size=10&sort=series,metadata.numberSort,asc",
            headers=headers,
        )
        assert resp.status_code == 200
        books = [b for b in resp.json()["content"] if b["id"] in ["9001", "9002", "9003", "9004"]]
        expected_order = ["9004", "9003", "9002", "9001"] # Alpha 1, Alpha 2, Beta 1, Beta 2
        actual_order = [b["id"] for b in books]
        assert actual_order == expected_order

        # Test GET /api/v1/books with sort=series,asc&sort=metadata.numberSort,desc
        resp_desc = await client.get(
            "/api/v1/books?sort=series,asc&sort=metadata.numberSort,desc",
            headers=headers,
        )
        assert resp_desc.status_code == 200
        books_desc = [b for b in resp_desc.json()["content"] if b["id"] in ["9001", "9002", "9003", "9004"]]
        expected_desc = ["9003", "9004", "9001", "9002"] # Alpha 2, Alpha 1, Beta 2, Beta 1
        assert [b["id"] for b in books_desc] == expected_desc


@pytest.mark.asyncio
async def test_read_progress_page_preservation_and_sync_protection():
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService
    from app.services.user_sync import user_sync_service

    await db.connect()
    user_id = 777
    token = "token_read_test"
    user = UserSession(
        user_id=user_id,
        username="read_tester",
        token=token,
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": f"Bearer {token}"}

    # Book with 0 page_count in DB (page count not calculated yet)
    await db.upsert_books_batch([
        {"id": 7771, "series_id": "s-alpha", "library_id": 1, "name": "Uncalculated Book", "number": 1.0, "page_count": 0},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Update R2 progression to position 5
        resp_prog = await client.put(
            "/api/v1/books/7771/progression",
            json={"locator": {"href": "part1.html", "locations": {"position": 5}}},
            headers=headers,
        )
        assert resp_prog.status_code == 204

        # Verify page is 5 and NOT marked completed
        resp_b = await client.get("/api/v1/books/7771", headers=headers)
        assert resp_b.status_code == 200
        rp = resp_b.json()["readProgress"]
        assert rp is not None
        assert rp["page"] == 5
        assert rp["completed"] is False

        # 2. Test user_sync_service does not erase local progress when Grimmory returns UNREAD
        local_before = await db.get_book_read_progress(user_id, 7771)
        assert local_before is not None
        assert local_before["page"] == 5
        assert local_before["completed"] == 0

        # Simulate user sync where remote Grimmory response has readStatus="UNREAD"
        # Monkeypatch get_library_books & get_all_books to return UNREAD
        from app.clients.grimmory import grimmory_client
        async def mock_all_books(token=None):
            return [{"id": 7771, "readStatus": "UNREAD", "page_count": 0}]
        monkeypatch_all = pytest.MonkeyPatch()
        monkeypatch_all.setattr(grimmory_client, "get_all_books", mock_all_books)
        monkeypatch_all.setattr(grimmory_client, "get_library_books", lambda lib_id, token=None: mock_all_books())
        monkeypatch_all.setattr(grimmory_client, "get_magic_shelves", lambda token=None: [])

        await user_sync_service.capture_and_sync_user(user, force=True)

        # Local progress should STILL exist and be page 5!
        local_after = await db.get_book_read_progress(user_id, 7771)
        assert local_after is not None
        assert local_after["page"] == 5
        assert local_after["completed"] == 0
        monkeypatch_all.undo()


@pytest.mark.asyncio
async def test_admin_read_sync_endpoints():
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    admin = UserSession(
        user_id=1,
        username="admin_user",
        token="token_admin_sync",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(admin)
    headers = {"Authorization": "Bearer token_admin_sync"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Status returns read_sync_job
        resp_status = await client.get("/admin/api/status", headers=headers)
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert "read_sync_job" in data
        assert "read_sync_running" in data

        # 2. Trigger read sync
        resp_sync = await client.post("/admin/api/read-sync", headers=headers)
        assert resp_sync.status_code == 200
        assert resp_sync.json()["status"] in ["started", "already_running"]

        # 3. Clear read sync logs
        resp_clear = await client.post("/admin/api/read-sync/clear-logs", headers=headers)
        assert resp_clear.status_code == 200
        assert resp_clear.json()["status"] == "cleared"


@pytest.mark.asyncio
async def test_book_adjacency_and_page_thumbnail():
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService
    from app.clients.grimmory import grimmory_client

    await db.connect()
    # Create library 80, series 80-series, and 3 consecutive books
    await db.upsert_libraries([{"id": 80, "name": "Adj Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "80-series", "library_id": 80, "name": "Adj Series", "slug": "adj-series"}])
    await db.upsert_books_batch([
        {"id": 801, "series_id": "80-series", "library_id": 80, "name": "Book 1", "number": 1.0, "page_count": 20},
        {"id": 802, "series_id": "80-series", "library_id": 80, "name": "Book 2", "number": 2.0, "page_count": 25},
        {"id": 803, "series_id": "80-series", "library_id": 80, "name": "Book 3", "number": 3.0, "page_count": 30},
    ])

    user = UserSession(
        user_id=1,
        username="adj_user",
        token="token_adj",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_adj"}

    # Mock page thumbnail stream
    import io
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    fake_img_bytes = buf.getvalue()

    async def mock_stream_page(book_id, page_num, token=None):
        return fake_img_bytes

    grimmory_client.stream_page_image = mock_stream_page

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Book 801 (first book)
        resp_next = await client.get("/api/v1/books/801/next", headers=headers)
        assert resp_next.status_code == 200
        assert resp_next.json()["id"] == "802"

        resp_prev = await client.get("/api/v1/books/801/previous", headers=headers)
        assert resp_prev.status_code == 404

        # Book 802 (middle book)
        resp_next = await client.get("/api/v1/books/802/next", headers=headers)
        assert resp_next.status_code == 200
        assert resp_next.json()["id"] == "803"

        resp_prev = await client.get("/api/v1/books/802/previous", headers=headers)
        assert resp_prev.status_code == 200
        assert resp_prev.json()["id"] == "801"

        # Book 803 (last book)
        resp_next = await client.get("/api/v1/books/803/next", headers=headers)
        assert resp_next.status_code == 404

        resp_prev = await client.get("/api/v1/books/803/previous", headers=headers)
        assert resp_prev.status_code == 200
        assert resp_prev.json()["id"] == "802"

        # Page thumbnail
        resp_thumb = await client.get("/api/v1/books/801/pages/1/thumbnail", headers=headers)
        assert resp_thumb.status_code == 200
        assert resp_thumb.headers.get("content-type") == "image/jpeg"
        assert len(resp_thumb.content) > 0


@pytest.mark.asyncio
async def test_auto_mark_completed_when_reading_to_end():
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService
    from app.clients.grimmory import grimmory_client

    await db.connect()
    await db.upsert_libraries([{"id": 85, "name": "Read Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "85-series", "library_id": 85, "name": "Read Series", "slug": "read-series"}])
    await db.upsert_books_batch([
        {"id": 850, "series_id": "85-series", "library_id": 85, "name": "Book 850", "number": 1.0, "page_count": 20},
        {"id": 851, "series_id": "85-series", "library_id": 85, "name": "Book 851", "number": 2.0, "page_count": 100},
        {"id": 852, "series_id": "85-series", "library_id": 85, "name": "Book 852", "number": 3.0, "page_count": 50},
    ])

    user = UserSession(
        user_id=1,
        username="reader_user",
        token="token_reader",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_reader"}

    # Mock Grimmory update_read_progress to succeed
    async def mock_update_read_progress(req, token=None):
        return True

    grimmory_client.update_read_progress = mock_update_read_progress

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Reading mid-way: page 5 of 20 with completed=False -> completed should be False
        resp = await client.patch("/api/v1/books/850/read-progress", json={"page": 5, "completed": False}, headers=headers)
        assert resp.status_code == 204
        prog = await client.get("/api/v1/books/850/read-progress", headers=headers)
        assert prog.status_code == 200
        assert prog.json()["page"] == 5
        assert prog.json()["completed"] is False

        # 2. Reading to last page: page 20 of 20 with completed=False -> automatically marked as completed!
        resp = await client.patch("/api/v1/books/850/read-progress", json={"page": 20, "completed": False}, headers=headers)
        assert resp.status_code == 204
        prog = await client.get("/api/v1/books/850/read-progress", headers=headers)
        assert prog.status_code == 200
        assert prog.json()["page"] == 20
        assert prog.json()["completed"] is True

        # 3. Reading past 95%: page 96 of 100 with completed=False -> automatically marked as completed!
        resp = await client.patch("/api/v1/books/851/read-progress", json={"page": 96, "completed": False}, headers=headers)
        assert resp.status_code == 204
        prog = await client.get("/api/v1/books/851/read-progress", headers=headers)
        assert prog.status_code == 200
        assert prog.json()["completed"] is True

        # 4. Readium R2 progression >= 95%: 96% -> automatically marked as completed!
        resp = await client.put(
            "/api/v1/books/852/progression",
            json={"locations": {"totalProgression": 0.96}},
            headers=headers,
        )
        assert resp.status_code == 204
        prog = await client.get("/api/v1/books/852/read-progress", headers=headers)
        assert prog.status_code == 200
        assert prog.json()["completed"] is True


@pytest.mark.asyncio
async def test_connected_users_and_purge():
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()
    # 1. Setup admin and target user in database
    await db.upsert_libraries([{"id": 80, "name": "Adj Lib", "paths": []}])
    await db.upsert_user(user_id=990, username="admin_boss", token="tok_admin_boss", is_admin=True)
    await db.upsert_user(user_id=991, username="test_reader", token="tok_test_reader", is_admin=False, assigned_libraries=[80])

    # Cache sessions
    admin_session = UserSession(
        user_id=990,
        username="admin_boss",
        token="tok_admin_boss",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    reader_session = UserSession(
        user_id=991,
        username="test_reader",
        token="tok_test_reader",
        is_admin=False,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    AuthService.cache_session(admin_session)
    AuthService.cache_session(reader_session)

    # Insert read progress for user 991
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.upsert_read_progress(user_id=991, book_id=850, page=12, completed=False, read_date=now_iso)

    admin_headers = {"Authorization": "Bearer tok_admin_boss"}
    reader_headers = {"Authorization": "Bearer tok_test_reader"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Non-admin attempting GET /admin/api/users gets 401/403
        resp_unauth = await client.get("/admin/api/users", headers=reader_headers)
        assert resp_unauth.status_code in (401, 403)

        # B. Admin GET /admin/api/users succeeds
        resp_users = await client.get("/admin/api/users", headers=admin_headers)
        assert resp_users.status_code == 200
        data = resp_users.json()
        assert "users" in data
        user_ids = [u["id"] for u in data["users"]]
        assert 990 in user_ids
        assert 991 in user_ids

        # Verify enriched fields for user 991
        target_info = next(u for u in data["users"] if u["id"] == 991)
        assert target_info["username"] == "test_reader"
        assert target_info["progress_count"] >= 1
        assert "Adj Lib" in target_info["libraries_display"]

        # C. Self-purge prevention
        resp_self = await client.post("/admin/api/users/990/purge", headers=admin_headers)
        assert resp_self.status_code == 400
        assert "own active admin user session" in resp_self.json()["detail"]

        # D. Non-admin purge attempt fails
        resp_non_admin = await client.post("/admin/api/users/991/purge", headers=reader_headers)
        assert resp_non_admin.status_code == 403

        # E. Purging nonexistent user fails with 404
        resp_404 = await client.post("/admin/api/users/999999/purge", headers=admin_headers)
        assert resp_404.status_code == 404

        # F. Successful purge of user 991
        resp_purge = await client.post("/admin/api/users/991/purge", headers=admin_headers)
        assert resp_purge.status_code == 200
        purge_data = resp_purge.json()
        assert purge_data["success"] is True
        assert purge_data["deleted"]["user_deleted"] == 1
        assert purge_data["deleted"]["progress_deleted"] >= 1

        # G. Verify database is purged
        assert await db.get_user_by_id(991) is None
        assert len(await db.get_all_user_read_progress(991)) == 0

        # H. Verify in-memory session is evicted
        assert AuthService.get_cached_session("tok_test_reader") is None

        # I. Verify dashboard HTML renders connected users section
        resp_dash = await client.get("/admin", headers=admin_headers)
        assert resp_dash.status_code == 200
        assert "Connected Users & Readers" in resp_dash.text
        assert "/static/app.js" in resp_dash.text


@pytest.mark.asyncio
async def test_grimmory_read_status_and_page_progress_sync():
    import asyncio
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService
    from app.clients.grimmory import grimmory_client
    import io
    from PIL import Image

    await db.connect()
    await db.upsert_libraries([{"id": 90, "name": "Status Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "90-pdf", "library_id": 90, "name": "PDF Series", "slug": "pdf"}])
    await db.upsert_books_batch([
        {"id": 15889, "series_id": "90-pdf", "library_id": 90, "name": "Test Comic 15889", "number": 1.0, "page_count": 20},
    ])

    user = UserSession(
        user_id=10,
        username="reader_sync",
        token="token_reader_sync",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer token_reader_sync"}

    captured_progress_reqs = []
    captured_status_calls = []
    captured_reset_calls = []

    async def mock_update_read_progress(req, token=None):
        captured_progress_reqs.append((req, token))
        return True

    async def mock_update_read_status(book_ids, status, token=None):
        captured_status_calls.append((book_ids, status, token))
        return True

    async def mock_reset_read_progress(book_ids, token=None):
        captured_reset_calls.append((book_ids, token))
        return True

    grimmory_client.update_read_progress = mock_update_read_progress
    grimmory_client.update_read_status = mock_update_read_status
    grimmory_client.reset_read_progress = mock_reset_read_progress

    # Mock page image
    img = Image.new("RGB", (50, 50), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    fake_img = buf.getvalue()

    async def mock_stream_page(b_id, p_num, token=None):
        return fake_img

    grimmory_client.stream_page_image = mock_stream_page

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test finding series by "series-pdf" slug identifier
        series_found = await db.find_series_by_id_or_slug("series-pdf")
        assert series_found is not None
        assert series_found["id"] == "90-pdf"

        # 2. Reading page 8 of book 15889 via GET /api/v1/books/{id}/pages/{page_num}
        resp_page = await client.get("/api/v1/books/15889/pages/8", headers=headers)
        assert resp_page.status_code == 200

        # Yield to let background task run
        await asyncio.sleep(0.1)

        # Check that page 8 progress was recorded locally
        prog = await db.get_book_read_progress(10, 15889)
        assert prog is not None
        assert prog["page"] == 8
        assert prog["completed"] == 0

        # Check that Grimmory was notified with progressValid=True and status READING
        assert any(r[0].bookId == 15889 and r[0].cbxProgress.page == 8 and r[0].progressValid is True for r in captured_progress_reqs)
        assert any(s[0] == [15889] and s[1] == "READING" for s in captured_status_calls)

        # 3. Explicit read completion via PATCH /api/v1/books/{id}/read-progress
        resp_patch = await client.patch("/api/v1/books/15889/read-progress", json={"completed": True}, headers=headers)
        assert resp_patch.status_code == 204
        assert any(s[0] == [15889] and s[1] == "READ" for s in captured_status_calls)

        # 4. Explicit reset via DELETE /api/v1/books/{id}/read-progress
        resp_del = await client.delete("/api/v1/books/15889/read-progress", headers=headers)
        assert resp_del.status_code == 204
        assert any(r[0] == [15889] for r in captured_reset_calls)


@pytest.mark.asyncio
async def test_grimmory_client_token_401_retry():
    import httpx
    from app.clients.grimmory import GrimmoryClient
    from app.models.grimmory import GrimmoryReadProgressRequest
    from unittest.mock import AsyncMock

    client = GrimmoryClient(base_url="http://mock-grimmory")
    client.get_sync_token = AsyncMock(return_value="valid_sync_token")
    client._sync_token = "valid_sync_token"

    # 1. Background / sync user call (token=None): 401 on first try with expired token, 200 on retry with sync token
    resp_401 = httpx.Response(401, request=httpx.Request("POST", "http://mock-grimmory"))
    resp_200 = httpx.Response(200, request=httpx.Request("POST", "http://mock-grimmory"))

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[resp_401, resp_200])
    client._http_client = mock_http

    req = GrimmoryReadProgressRequest(bookId=101, progressValid=True)
    success = await client.update_read_progress(req, token=None)

    assert success is True
    assert mock_http.post.call_count == 2
    # Verify second call used the valid sync token
    assert mock_http.post.call_args_list[1][1]["headers"]["Authorization"] == "Bearer valid_sync_token"

    # 2. Regular user call (token="user_token"): 401 must NOT fall back to sync token or update sync user progress
    mock_http_user = AsyncMock()
    mock_http_user.post = AsyncMock(return_value=resp_401)
    client._http_client = mock_http_user

    user_req = GrimmoryReadProgressRequest(bookId=102, progressValid=True)
    user_success = await client.update_read_progress(user_req, token="user_token")

    assert user_success is False
    assert mock_http_user.post.call_count == 1
    # Verify user token was passed and no sync token was leaked or called
    assert mock_http_user.post.call_args_list[0][1]["headers"]["Authorization"] == "Bearer user_token"


@pytest.mark.asyncio
async def test_komga_metadata_endpoints_and_filtering():
    import json
    from datetime import datetime, timezone, timedelta
    from app.main import db
    from app.models.internal import UserSession
    from app.services.auth import AuthService

    await db.connect()

    # Seed libraries, series, books with rich metadata
    await db.upsert_libraries([
        {"id": 55, "name": "Manga Library", "paths": []},
    ])

    series_json = json.dumps({
        "authors": [{"name": "Eiichiro Oda", "role": "writer"}, {"name": "Boichi", "role": "penciller"}],
        "genres": ["Action", "Adventure"],
        "tags": ["Pirates", "Shonen"],
        "publisher": "Shueisha",
        "summary": "Epic pirate adventures",
        "releaseDate": "1997-07-22",
    })
    await db.upsert_series_batch([
        {
            "id": "55-onepiece",
            "library_id": 55,
            "name": "One Piece",
            "slug": "onepiece",
            "books_count": 2,
            "raw_json": series_json,
        }
    ])

    book1_json = json.dumps({
        "authors": [{"name": "Eiichiro Oda", "role": "writer"}],
        "tags": ["East Blue", "Pirates"],
        "genres": ["Action"],
        "publisher": "Shueisha",
        "summary": "Romance Dawn",
        "releaseDate": "1997-12-24",
    })
    book2_json = json.dumps({
        "authors": [{"name": "Boichi", "role": "penciller"}],
        "tags": ["Spinoff", "Pirates"],
        "genres": ["Adventure"],
        "publisher": "Shueisha",
        "summary": "Ace Novel Manga",
        "releaseDate": "2020-09-16",
    })

    await db.upsert_books_batch([
        {
            "id": 5501,
            "series_id": "55-onepiece",
            "library_id": 55,
            "name": "One Piece Vol 1",
            "number": 1.0,
            "raw_json": book1_json,
        },
        {
            "id": 5502,
            "series_id": "55-onepiece",
            "library_id": 55,
            "name": "One Piece: Ace 1",
            "number": 2.0,
            "raw_json": book2_json,
        },
    ])

    test_user = UserSession(
        user_id=550,
        username="manga_fan",
        token="token_manga_fan",
        is_admin=True,
        assigned_library_ids=[],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(test_user)
    headers = {"Authorization": "Bearer token_manga_fan"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test GET /api/v1/genres (List[str]) and /api/v2/genres (PageableDto[str])
        resp_genres_v1 = await client.get("/api/v1/genres", headers=headers)
        assert resp_genres_v1.status_code == 200
        genres_v1 = resp_genres_v1.json()
        assert isinstance(genres_v1, list)
        assert "Action" in genres_v1
        assert "Adventure" in genres_v1

        resp_genres_v2 = await client.get("/api/v2/genres", headers=headers)
        assert resp_genres_v2.status_code == 200
        genres_v2 = resp_genres_v2.json()
        assert "content" in genres_v2
        assert "Action" in genres_v2["content"]

        # 2. Test GET /api/v1/series/genres
        resp_series_genres = await client.get("/api/v1/series/genres", headers=headers)
        assert resp_series_genres.status_code == 200
        assert "Action" in resp_series_genres.json()

        # 3. Test GET /api/v1/publishers & /api/v2/publishers
        resp_pub_v1 = await client.get("/api/v1/publishers", headers=headers)
        assert resp_pub_v1.status_code == 200
        assert "Shueisha" in resp_pub_v1.json()

        resp_pub_v2 = await client.get("/api/v2/publishers", headers=headers)
        assert resp_pub_v2.status_code == 200
        assert "Shueisha" in resp_pub_v2.json()["content"]

        # 4. Test GET /api/v1/tags, /api/v2/tags, /api/v1/tags/book, /api/v1/tags/series
        resp_tags_v1 = await client.get("/api/v1/tags", headers=headers)
        assert resp_tags_v1.status_code == 200
        tags_v1 = resp_tags_v1.json()
        assert "Pirates" in tags_v1
        assert "Shonen" in tags_v1

        resp_book_tags = await client.get("/api/v1/tags/book", headers=headers)
        assert resp_book_tags.status_code == 200
        assert "East Blue" in resp_book_tags.json()

        resp_series_tags = await client.get("/api/v1/tags/series", headers=headers)
        assert resp_series_tags.status_code == 200
        assert "Shonen" in resp_series_tags.json()

        # 5. Test GET /api/v1/authors & /api/v2/authors & /api/v1/authors/roles
        resp_authors_v1 = await client.get("/api/v1/authors", headers=headers)
        assert resp_authors_v1.status_code == 200
        authors_v1 = resp_authors_v1.json()
        assert isinstance(authors_v1, list)
        assert any(a["name"] == "Eiichiro Oda" and a["role"] == "writer" for a in authors_v1)

        resp_authors_roles = await client.get("/api/v1/authors/roles", headers=headers)
        assert resp_authors_roles.status_code == 200
        roles = resp_authors_roles.json()
        assert "writer" in roles
        assert "penciller" in roles

        # 6. Test Series DTO metadata mapping
        resp_series_one = await client.get("/api/v1/series/55-onepiece", headers=headers)
        assert resp_series_one.status_code == 200
        s_dto = resp_series_one.json()
        assert s_dto["metadata"]["publisher"] == "Shueisha"
        assert "Action" in s_dto["metadata"]["genres"]
        assert "Shonen" in s_dto["metadata"]["tags"]
        assert any(a["name"] == "Eiichiro Oda" for a in s_dto["booksMetadata"]["authors"])

        # 7. Test Book DTO metadata mapping
        resp_book_one = await client.get("/api/v1/books/5501", headers=headers)
        assert resp_book_one.status_code == 200
        b_dto = resp_book_one.json()
        assert any(a["name"] == "Eiichiro Oda" and a["role"] == "writer" for a in b_dto["metadata"]["authors"])
        assert "East Blue" in b_dto["metadata"]["tags"]
        assert b_dto["metadata"]["isbn"] is None or isinstance(b_dto["metadata"]["isbn"], str)

        # 8. Test Filtering Series by genre and tag
        resp_filter_series_genre = await client.get("/api/v1/series?genre=Action", headers=headers)
        assert resp_filter_series_genre.status_code == 200
        assert resp_filter_series_genre.json()["totalElements"] == 1

        resp_filter_series_genre_nomatch = await client.get("/api/v1/series?genre=Horror", headers=headers)
        assert resp_filter_series_genre_nomatch.status_code == 200
        assert resp_filter_series_genre_nomatch.json()["totalElements"] == 0

        # 9. Test Filtering Books by author and tag
        resp_filter_books_author = await client.get("/api/v1/books?author=Boichi", headers=headers)
        assert resp_filter_books_author.status_code == 200
        assert resp_filter_books_author.json()["totalElements"] == 1
        assert resp_filter_books_author.json()["content"][0]["id"] == "5502"

        resp_filter_books_tag = await client.get("/api/v1/books?tag=East Blue", headers=headers)
        assert resp_filter_books_tag.status_code == 200
        assert resp_filter_books_tag.json()["totalElements"] == 1
        assert resp_filter_books_tag.json()["content"][0]["id"] == "5501"

        # 10. Test POST /api/v1/series/list and /api/v1/books/list with Komic-style author SearchCondition
        komic_series_query = {
            "condition": {
                "allOf": [
                    {"author": {"value": {"role": "writer", "name": "Eiichiro Oda"}, "operator": "is"}}
                ]
            }
        }
        resp_post_series_author = await client.post("/api/v1/series/list", json=komic_series_query, headers=headers)
        assert resp_post_series_author.status_code == 200
        post_series_data = resp_post_series_author.json()
        assert post_series_data["totalElements"] == 1
        assert post_series_data["content"][0]["id"] == "55-onepiece"

        komic_books_query = {
            "condition": {
                "allOf": [
                    {"author": {"value": {"role": "penciller", "name": "Boichi"}, "operator": "is"}}
                ]
            }
        }
        resp_post_books_author = await client.post("/api/v1/books/list", json=komic_books_query, headers=headers)
        assert resp_post_books_author.status_code == 200
        post_books_data = resp_post_books_author.json()
        assert post_books_data["totalElements"] == 1
        assert post_books_data["content"][0]["id"] == "5502"

        # Non-matching author query
        komic_nomatch_query = {
            "condition": {
                "allOf": [
                    {"author": {"value": {"role": "writer", "name": "Unknown Author"}, "operator": "is"}}
                ]
            }
        }
        resp_post_nomatch = await client.post("/api/v1/series/list", json=komic_nomatch_query, headers=headers)
        assert resp_post_nomatch.status_code == 200
        assert resp_post_nomatch.json()["totalElements"] == 0


@pytest.mark.asyncio
async def test_grimmory_favorites_collection_user_gating_and_renaming(monkeypatch):
    import base64
    from app.main import db
    from app.clients.grimmory import grimmory_client
    from app.models.grimmory import GrimmoryLoginResponse, GrimmoryPermissions, GrimmoryUser

    await db.connect()
    grimmory_client.clear_user_favorites_cache()

    # Populate test library and books
    await db.upsert_libraries([{"id": 40, "name": "Favorites Lib", "paths": []}])
    await db.upsert_series_batch([{
        "id": "40-series1",
        "library_id": 40,
        "name": "Sync Series",
        "slug": "sync-series",
        "books_count": 3,
    }])
    await db.upsert_books_batch([
        {"id": 401, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 1", "number": 1.0, "page_count": 250},
        {"id": 402, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 2", "number": 2.0, "page_count": 120},
        {"id": 403, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 3", "number": 3.0, "page_count": 80},
    ])

    # Setup Alice (user 400) and Bob (user 500)
    async def mock_login(username, password):
        if username == "alice":
            return GrimmoryLoginResponse(accessToken="tok_alice_fav", expires=3600)
        elif username == "bob":
            return GrimmoryLoginResponse(accessToken="tok_bob_fav", expires=3600)
        raise ValueError("Invalid user")

    async def mock_get_current_user(token):
        if token == "tok_alice_fav":
            return GrimmoryUser(id=400, username="alice", permissions=GrimmoryPermissions(admin=False), assignedLibraries=[40])
        elif token == "tok_bob_fav":
            return GrimmoryUser(id=500, username="bob", permissions=GrimmoryPermissions(admin=False), assignedLibraries=[40])
        raise ValueError("Invalid token")

    async def mock_get_shelves(token=None):
        if token == "tok_alice_fav":
            return [{"id": 11, "name": "Favorites", "books": [{"id": 401}, {"id": 402}]}]
        elif token == "tok_bob_fav":
            return [{"id": 22, "name": "Favorites", "books": [{"id": 403}]}]
        return []

    async def mock_get_magic_shelves(token=None):
        return []

    monkeypatch.setattr(grimmory_client, "login", mock_login)
    monkeypatch.setattr(grimmory_client, "get_current_user", mock_get_current_user)
    monkeypatch.setattr(grimmory_client, "get_shelves", mock_get_shelves)
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", mock_get_magic_shelves)

    alice_headers = {"Authorization": f"Basic {base64.b64encode(b'alice:pw').decode('utf-8')}"}
    bob_headers = {"Authorization": f"Basic {base64.b64encode(b'bob:pw').decode('utf-8')}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Alice queries collections -> sees ONLY "Favorites - alice" (favorites-400)
        resp_a = await client.get("/api/v1/collections", headers=alice_headers)
        assert resp_a.status_code == 200
        data_a = resp_a.json()
        assert data_a["totalElements"] == 1
        coll_a = data_a["content"][0]
        assert coll_a["id"] == "favorites-400"
        assert coll_a["name"] == "Favorites - alice"
        assert coll_a["seriesIds"] == ["fav-400-40-series1"]

        # 2. Bob queries collections -> sees ONLY "Favorites - bob" (favorites-500)
        resp_b = await client.get("/api/v1/collections", headers=bob_headers)
        assert resp_b.status_code == 200
        data_b = resp_b.json()
        assert data_b["totalElements"] == 1
        coll_b = data_b["content"][0]
        assert coll_b["id"] == "favorites-500"
        assert coll_b["name"] == "Favorites - bob"
        assert coll_b["seriesIds"] == ["fav-500-40-series1"]

        # 3. User Gating checks: Alice cannot access Bob's collection or series
        assert (await client.get("/api/v1/collections/favorites-500", headers=alice_headers)).status_code == 404
        assert (await client.get("/api/v1/collections/favorites-500/series", headers=alice_headers)).status_code == 404
        assert (await client.get("/api/v1/collections/favorites-500/books", headers=alice_headers)).status_code == 404
        assert (await client.get("/api/v1/series/fav-500-40-series1", headers=alice_headers)).status_code == 404
        assert (await client.get("/api/v1/series/fav-500-40-series1/books", headers=alice_headers)).status_code == 404
        assert (await client.get("/api/v1/series/fav-500-40-series1/thumbnail", headers=alice_headers)).status_code == 404

        # Bob cannot access Alice's collection or series
        assert (await client.get("/api/v1/collections/favorites-400", headers=bob_headers)).status_code == 404
        assert (await client.get("/api/v1/collections/favorites-400/series", headers=bob_headers)).status_code == 404
        assert (await client.get("/api/v1/series/fav-400-40-series1", headers=bob_headers)).status_code == 404
        assert (await client.get("/api/v1/series/fav-400-40-series1/books", headers=bob_headers)).status_code == 404


@pytest.mark.asyncio
async def test_grimmory_favorites_collection_empty_exclusion(monkeypatch):
    import base64
    from app.main import db
    from app.clients.grimmory import grimmory_client
    from app.models.grimmory import GrimmoryLoginResponse, GrimmoryPermissions, GrimmoryUser

    await db.connect()
    grimmory_client.clear_user_favorites_cache()

    async def mock_login(username, password):
        return GrimmoryLoginResponse(accessToken="tok_charlie_fav", expires=3600)

    async def mock_get_current_user(token):
        return GrimmoryUser(id=600, username="charlie", permissions=GrimmoryPermissions(admin=False), assignedLibraries=[40])

    # Charlie has an empty Favorites shelf or no Favorites shelf at all
    async def mock_get_shelves(token=None):
        return [{"id": 33, "name": "Other Shelf", "books": []}]

    async def mock_get_magic_shelves(token=None):
        return []

    monkeypatch.setattr(grimmory_client, "login", mock_login)
    monkeypatch.setattr(grimmory_client, "get_current_user", mock_get_current_user)
    monkeypatch.setattr(grimmory_client, "get_shelves", mock_get_shelves)
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", mock_get_magic_shelves)

    charlie_headers = {"Authorization": f"Basic {base64.b64encode(b'charlie:pw').decode('utf-8')}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Stipulation 2: If a user has no favorites collection, don't show it to them at all
        resp = await client.get("/api/v1/collections", headers=charlie_headers)
        assert resp.status_code == 200
        assert resp.json()["totalElements"] == 0
        assert len(resp.json()["content"]) == 0

        # Direct access to favorites-600 must return 404
        assert (await client.get("/api/v1/collections/favorites-600", headers=charlie_headers)).status_code == 404
        assert (await client.get("/api/v1/collections/favorites-600/series", headers=charlie_headers)).status_code == 404


@pytest.mark.asyncio
async def test_grimmory_favorites_collection_scoped_books_population(monkeypatch):
    import base64
    from app.main import db
    from app.clients.grimmory import grimmory_client
    from app.models.grimmory import GrimmoryLoginResponse, GrimmoryPermissions, GrimmoryUser

    await db.connect()
    grimmory_client.clear_user_favorites_cache()

    # Populate test library and 3 books in "40-series1"
    await db.upsert_libraries([{"id": 40, "name": "Favorites Lib", "paths": []}])
    await db.upsert_series_batch([{
        "id": "40-series1",
        "library_id": 40,
        "name": "Sync Series",
        "slug": "sync-series",
        "books_count": 3,
    }])
    await db.upsert_books_batch([
        {"id": 401, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 1", "number": 1.0, "page_count": 250},
        {"id": 402, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 2", "number": 2.0, "page_count": 120},
        {"id": 403, "series_id": "40-series1", "library_id": 40, "name": "Sync Book 3", "number": 3.0, "page_count": 80},
    ])

    async def mock_login(username, password):
        return GrimmoryLoginResponse(accessToken="tok_alice_fav2", expires=3600)

    async def mock_get_current_user(token):
        return GrimmoryUser(id=400, username="alice", permissions=GrimmoryPermissions(admin=False), assignedLibraries=[40])

    # Alice has books 401 and 402 as favorites (book 403 is NOT favorited)
    async def mock_get_shelves(token=None):
        return [{"id": 11, "name": "Favorites", "books": [{"id": 401}, {"id": 402}]}]

    async def mock_get_magic_shelves(token=None):
        return []

    async def mock_stream_thumbnail(book_id, token=None):
        return b"fake_thumb_bytes"

    monkeypatch.setattr(grimmory_client, "login", mock_login)
    monkeypatch.setattr(grimmory_client, "get_current_user", mock_get_current_user)
    monkeypatch.setattr(grimmory_client, "get_shelves", mock_get_shelves)
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", mock_get_magic_shelves)
    monkeypatch.setattr(grimmory_client, "stream_thumbnail", mock_stream_thumbnail)

    alice_headers = {"Authorization": f"Basic {base64.b64encode(b'alice:pw').decode('utf-8')}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Collection series count reflects only favorited books (booksCount = 2, not 3)
        resp_series = await client.get("/api/v1/collections/favorites-400/series", headers=alice_headers)
        assert resp_series.status_code == 200
        s_data = resp_series.json()
        assert s_data["totalElements"] == 1
        s_item = s_data["content"][0]
        assert s_item["id"] == "fav-400-40-series1"
        assert s_item["booksCount"] == 2

        # 2. Opening the series via the collection (GET /api/v1/series/fav-400-40-series1/books):
        # ONLY populates books marked as favorites (401 and 402). 403 is EXCLUDED!
        resp_fav_books = await client.get("/api/v1/series/fav-400-40-series1/books", headers=alice_headers)
        assert resp_fav_books.status_code == 200
        fb_data = resp_fav_books.json()
        assert fb_data["totalElements"] == 2
        book_ids = [b["id"] for b in fb_data["content"]]
        assert book_ids == ["401", "402"]
        assert "403" not in book_ids
        for b in fb_data["content"]:
            assert b["seriesId"] == "fav-400-40-series1"

        # 3. Opening series with collection_id query param:
        resp_coll_param = await client.get("/api/v1/series/40-series1/books?collection_id=favorites-400", headers=alice_headers)
        assert resp_coll_param.status_code == 200
        cp_data = resp_coll_param.json()
        assert cp_data["totalElements"] == 2
        assert [b["id"] for b in cp_data["content"]] == ["401", "402"]

        # 4. Opening books via collection books endpoint:
        resp_coll_books = await client.get("/api/v1/collections/favorites-400/books", headers=alice_headers)
        assert resp_coll_books.status_code == 200
        cb_data = resp_coll_books.json()
        assert cb_data["totalElements"] == 2
        assert [b["id"] for b in cb_data["content"]] == ["401", "402"]

        # 5. Opening GET /api/v1/books?collection_id=favorites-400:
        resp_books_coll = await client.get("/api/v1/books?collection_id=favorites-400", headers=alice_headers)
        assert resp_books_coll.status_code == 200
        bc_data = resp_books_coll.json()
        assert bc_data["totalElements"] == 2
        assert [b["id"] for b in bc_data["content"]] == ["401", "402"]

        # 6. Normal library browsing (GET /api/v1/series/40-series1/books):
        # MUST return ALL 3 books (401, 402, and 403)!
        resp_all_books = await client.get("/api/v1/series/40-series1/books", headers=alice_headers)
        assert resp_all_books.status_code == 200
        ab_data = resp_all_books.json()
        assert ab_data["totalElements"] == 3
        all_ids = [b["id"] for b in ab_data["content"]]
        assert all_ids == ["401", "402", "403"]

        # 7. Series collections:
        resp_s_colls = await client.get("/api/v1/series/fav-400-40-series1/collections", headers=alice_headers)
        assert resp_s_colls.status_code == 200
        assert len(resp_s_colls.json()) == 1
        assert resp_s_colls.json()[0]["id"] == "favorites-400"

        # 8. Collection thumbnail:
        resp_thumb = await client.get("/api/v1/collections/favorites-400/thumbnail", headers=alice_headers)
        assert resp_thumb.status_code == 200
        assert resp_thumb.content == b"fake_thumb_bytes"


@pytest.mark.asyncio
async def test_user_read_stats_isolation_restricted_libraries_and_no_sync_leak(monkeypatch):
    import json
    from app.main import db
    from app.models.internal import UserSession
    from app.services.user_sync import user_sync_service
    from app.clients.grimmory import grimmory_client
    from unittest.mock import AsyncMock

    await db.connect()

    # 1. Seed libraries
    await db.upsert_libraries([
        {"id": 1, "name": "Manga", "paths": []},
        {"id": 2, "name": "Comics", "paths": []},
        {"id": 3, "name": "Light Novel", "paths": []},
    ])

    # 2. Seed books: 100 in Manga, 100 in Comics, 9 in Light Novel (IDs 1001-1009)
    books = []
    series_list = [
        {"id": "1-manga", "library_id": 1, "name": "Manga Series", "slug": "manga", "sort_title": "Manga Series", "books_count": 100, "raw_json": {}},
        {"id": "2-comics", "library_id": 2, "name": "Comics Series", "slug": "comics", "sort_title": "Comics Series", "books_count": 100, "raw_json": {}},
        {"id": "3-ln", "library_id": 3, "name": "Light Novel Series", "slug": "ln", "sort_title": "Light Novel Series", "books_count": 9, "raw_json": {}},
    ]
    await db.upsert_series_batch(series_list)

    for i in range(1, 101):
        books.append({
            "id": i, "series_id": "1-manga", "library_id": 1, "name": f"Manga {i}",
            "number": float(i), "book_type": "CBZ", "page_count": 20, "deleted": 0, "raw_json": {},
        })
    for i in range(101, 201):
        books.append({
            "id": i, "series_id": "2-comics", "library_id": 2, "name": f"Comic {i}",
            "number": float(i - 100), "book_type": "CBZ", "page_count": 20, "deleted": 0, "raw_json": {},
        })
    for i in range(1001, 1010):
        books.append({
            "id": i, "series_id": "3-ln", "library_id": 3, "name": f"LN Book {i}",
            "number": float(i - 1000), "book_type": "EPUB", "page_count": 250, "deleted": 0, "raw_json": {},
        })
    await db.upsert_books_batch(books)

    # 3. Create users
    # User 1: saetron (admin, all libraries)
    await db.upsert_user(user_id=5001, username="saetron", token="saetron_token", is_admin=True, assigned_libraries=[])
    # User 2: testuser (regular user, only Light Novel / library 3)
    await db.upsert_user(user_id=5002, username="testuser", token="testuser_token", is_admin=False, assigned_libraries=[3])

    # Seed read progress for saetron (read all 209 books)
    saetron_progress = [
        {"user_id": 5001, "book_id": b["id"], "page": 20, "completed": True, "read_date": "2026-10-14T07:23:11Z"}
        for b in books
    ]
    await db.upsert_read_progress_batch(saetron_progress)

    # Simulate the bug where saetron's read progress was previously erroneously synced to testuser
    bogus_testuser_progress = [
        {"user_id": 5002, "book_id": b["id"], "page": 20, "completed": True, "read_date": "2026-10-14T07:23:11Z"}
        for b in books
    ]
    await db.upsert_read_progress_batch(bogus_testuser_progress)

    # 4. Run cleanup
    pruned = await db.cleanup_unauthorized_read_progress(user_id=5002)
    assert pruned == 200  # 100 manga + 100 comics pruned from testuser

    # Verify get_users_detailed:
    users_detailed = await db.get_users_detailed()
    users_by_id = {u["id"]: u for u in users_detailed}

    saetron_info = users_by_id[5001]
    testuser_info = users_by_id[5002]

    assert saetron_info["progress_count"] == 209
    assert saetron_info["completed_count"] == 209
    assert saetron_info["libraries_display"] == "All Libraries"

    assert testuser_info["progress_count"] == 9  # Only the 9 LN books remain
    assert testuser_info["progress_count"] < 1000
    assert testuser_info["libraries_display"] == "Light Novel"

    # 5. Test user sync for testuser with mock Grimmory
    # On Grimmory, testuser has only read 1 of the LN books (book 1001)
    get_all_books_called = False
    get_library_books_calls = []

    async def mock_get_lib_books(lib_id, token=None):
        get_library_books_calls.append((lib_id, token))
        if lib_id == 3:
            return [
                {"id": 1001, "readStatus": "READ", "page_count": 250, "dateFinished": "2026-10-14T07:30:00Z"},
                {"id": 1002, "readStatus": "UNREAD", "page_count": 250},
            ]
        return []

    async def mock_get_all_books(token=None):
        nonlocal get_all_books_called
        get_all_books_called = True
        return []

    monkeypatch.setattr(grimmory_client, "get_library_books", mock_get_lib_books)
    monkeypatch.setattr(grimmory_client, "get_all_books", mock_get_all_books)
    monkeypatch.setattr(grimmory_client, "get_magic_shelves", AsyncMock(return_value=[]))

    testuser_session = UserSession(
        user_id=5002,
        username="testuser",
        token="testuser_valid_token",
        is_admin=False,
        assigned_library_ids=[3],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    res = await user_sync_service.capture_and_sync_user(testuser_session, force=True)
    assert res is True
    # get_all_books MUST NOT be called for restricted library users
    assert get_all_books_called is False
    # Only library 3 was queried
    assert get_library_books_calls == [(3, "testuser_valid_token")]

    # 6. Test that if testuser's token expires (returns 401), sync does NOT fall back to sync token
    # and does NOT leak saetron's books
    from app.clients.grimmory import GrimmoryClient
    client_test = GrimmoryClient(base_url="http://mock-grimmory")
    client_test.get_sync_token = AsyncMock(return_value="saetron_sync_token")
    client_test._sync_token = "saetron_sync_token"

    import httpx
    resp_401 = httpx.Response(401, request=httpx.Request("GET", "http://mock-grimmory/api/v1/libraries/3/book"))
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=resp_401)
    client_test._http_client = mock_http

    lib_books_res = await client_test.get_library_books(3, token="testuser_expired_token")
    assert lib_books_res == []
    # Verify it called Grimmory only once (did NOT retry with saetron_sync_token)
    assert mock_http.get.call_count == 1
    assert mock_http.get.call_args_list[0][1]["headers"]["Authorization"] == "Bearer testuser_expired_token"









