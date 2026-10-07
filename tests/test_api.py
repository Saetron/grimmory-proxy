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
    for bid in [301, 302, 303]:
        await db.delete_read_progress(user_id, bid)

    # Setup library, series with 3 books
    await db.upsert_libraries([{"id": 30, "name": "Manga Lib", "paths": []}])
    await db.upsert_series_batch([{
        "id": "30-manga1",
        "library_id": 30,
        "name": "One Piece",
        "slug": "one-piece",
        "books_count": 3,
    }])
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

        # 4. Verify user data was captured in SQLite users table
        user_record = await db.get_user_by_id(test_user_id)
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




