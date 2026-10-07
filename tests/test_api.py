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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/login")
        assert resp.status_code == 200
        assert "Admin Sign In" in resp.text


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
        assert resp_admin.json()["status"] in ["started", "already_running"]


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
        resp_ondeck_20 = await client.get("/api/v1/books/ondeck?library_id=20", headers=headers)
        assert resp_ondeck_20.status_code == 200
        assert resp_ondeck_20.json()["totalElements"] == 2

        resp_ondeck_14 = await client.get("/api/v1/books/ondeck?library_id=14", headers=headers)
        assert resp_ondeck_14.status_code == 200
        assert resp_ondeck_14.json()["totalElements"] == 0


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



