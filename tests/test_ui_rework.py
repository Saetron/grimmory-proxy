import pytest
import logging
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient

from app.main import app, db
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.log_buffer import RingBufferLogHandler, proxy_log_handler


def test_ring_buffer_caps_at_200_lines():
    """Verify that RingBufferLogHandler retains at most 200 lines."""
    handler = RingBufferLogHandler(capacity=200)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test_buffer_logger")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    for i in range(250):
        test_logger.info(f"Log line {i}")

    logs = handler.get_logs()
    assert len(logs) == 200
    assert "Log line 50" in logs[0]
    assert "Log line 249" in logs[-1]

    handler.clear()
    assert len(handler.get_logs()) == 0


@pytest.mark.asyncio
async def test_ui_rework_pages_and_book_search():
    """Verify all reworked UI pages and book search API."""
    await db.connect()
    user_id = 901
    user = UserSession(
        user_id=user_id,
        username="ui_tester_admin",
        token="tok_ui_tester",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer tok_ui_tester"}
    cookies = {"admin_session": "tok_ui_tester"}

    # Setup dummy library, series, and books
    await db.upsert_libraries([{"id": 901, "name": "Manga Archive", "paths": []}])
    await db.upsert_series_batch([
        {"id": "901-berserk", "library_id": 901, "name": "Berserk", "slug": "berserk", "books_count": 2},
        {"id": "901-stormlight", "library_id": 901, "name": "The Stormlight Archive", "slug": "stormlight", "books_count": 1},
    ])
    await db.upsert_books_batch([
        {"id": 90101, "series_id": "901-berserk", "library_id": 901, "name": "Berserk Vol 1", "number": 1.0, "page_count": 210, "book_type": "CBZ"},
        {"id": 90102, "series_id": "901-berserk", "library_id": 901, "name": "Berserk Vol 2", "number": 2.0, "page_count": 220, "book_type": "CBZ"},
        {"id": 90103, "series_id": "901-stormlight", "library_id": 901, "name": "The Way of Kings", "number": 1.0, "page_count": 1007, "book_type": "EPUB", "file_size_kb": 4096},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
        # 1. Main Dashboard (/admin & /dashboard)
        resp_dash = await client.get("/admin")
        assert resp_dash.status_code == 200
        assert "Query Books on Proxy" in resp_dash.text
        assert "book-search-input" in resp_dash.text
        assert "Stream page 1 in browser" not in resp_dash.text
        assert "calculateSingleBookPages" in resp_dash.text
        assert "syncBookProgress" in resp_dash.text
        assert "Calc Pages" in resp_dash.text
        assert "Pull" in resp_dash.text
        assert "Push" in resp_dash.text

        # 2. Stats Page (/stats & /admin/stats)
        resp_stats = await client.get("/stats")
        assert resp_stats.status_code == 200
        assert "Library Metrics & Page Enrichment" in resp_stats.text
        assert "Page Calculation Engine" in resp_stats.text

        # 3. Users Page (/users & /admin/users)
        resp_users = await client.get("/users")
        assert resp_users.status_code == 200
        assert "Connected Users & Readers" in resp_users.text
        assert "ui_tester_admin" in resp_users.text

        # 4. Logs Page (/logs & /admin/logs)
        resp_logs = await client.get("/logs")
        assert resp_logs.status_code == 200
        assert "Live System & Background Sync Logs" in resp_logs.text
        assert "200 lines" in resp_logs.text

        # 5. Book Search API (/admin/api/books/search) by book title, series name, and ID
        resp_search = await client.get("/admin/api/books/search?query=Berserk", headers=headers)
        assert resp_search.status_code == 200
        search_data = resp_search.json()
        assert search_data["total"] >= 2
        assert any(b["id"] == 90101 for b in search_data["books"])
        assert any(b["name"] == "Berserk Vol 1" for b in search_data["books"])

        resp_series_search = await client.get("/admin/api/books/search?query=Stormlight", headers=headers)
        assert resp_series_search.status_code == 200
        series_search_data = resp_series_search.json()
        assert series_search_data["total"] == 1
        assert series_search_data["books"][0]["id"] == 90103
        assert series_search_data["books"][0]["file_size"] == 4096

        resp_id_search = await client.get("/admin/api/books/search?query=90103", headers=headers)
        assert resp_id_search.status_code == 200
        assert resp_id_search.json()["total"] == 1
        assert resp_id_search.json()["books"][0]["name"] == "The Way of Kings"

        # 6. Logs API (/admin/api/logs and /admin/api/logs/clear)
        resp_api_logs = await client.get("/admin/api/logs", headers=headers)
        assert resp_api_logs.status_code == 200
        logs_data = resp_api_logs.json()
        assert "sync_logs" in logs_data
        assert "read_sync_logs" in logs_data
        assert "proxy_logs" in logs_data

        resp_clear_logs = await client.post("/admin/api/logs/clear", json={"type": "all"}, headers=headers)
        assert resp_clear_logs.status_code == 200
        assert resp_clear_logs.json()["success"] is True
        # 7. Single Book Page Calculation & Progress Sync Endpoints
        from unittest.mock import AsyncMock, patch

        with patch("app.main.page_calculator.calculate_and_save_book", new=AsyncMock(return_value="updated")):
            resp_calc = await client.post("/admin/api/books/90101/calculate-pages", headers=headers)
            assert resp_calc.status_code == 200
            assert resp_calc.json()["status"] == "success"
            assert resp_calc.json()["book_id"] == 90101

            # Via /admin/api/calculate-pages with book_id
            resp_calc_via_body = await client.post("/admin/api/calculate-pages", json={"book_id": 90101}, headers=headers)
            assert resp_calc_via_body.status_code == 200
            assert resp_calc_via_body.json()["status"] == "success"

        resp_calc_404 = await client.post("/admin/api/books/999999/calculate-pages", headers=headers)
        assert resp_calc_404.status_code == 404

        # Read Progress Push
        with patch("app.clients.grimmory.grimmory_client.update_read_progress", new=AsyncMock(return_value=True)), \
             patch("app.clients.grimmory.grimmory_client.update_read_status", new=AsyncMock(return_value=True)), \
             patch("app.clients.grimmory.grimmory_client.update_koreader_progress", new=AsyncMock(return_value=True)):
            resp_push = await client.post(
                "/admin/api/books/90101/progress/push",
                json={"completed": True, "page": 210},
                headers=headers,
            )
            assert resp_push.status_code == 200
            assert resp_push.json()["status"] == "success"
            assert resp_push.json()["completed"] is True
            assert resp_push.json()["action"] == "push"

        # Read Progress Pull
        mock_grimmory_book = {
            "id": 90101,
            "readStatus": "READING",
            "cbxProgress": {"page": 55, "percentage": 25.0, "lastRead": "2026-11-02T10:00:00Z"},
        }
        with patch("app.clients.grimmory.grimmory_client.get_book", new=AsyncMock(return_value=mock_grimmory_book)):
            resp_pull = await client.post("/admin/api/books/90101/progress/pull", headers=headers)
            assert resp_pull.status_code == 200
            assert resp_pull.json()["status"] == "success"
            assert resp_pull.json()["completed"] is False
            assert resp_pull.json()["page"] == 55
            assert resp_pull.json()["action"] == "pull"

