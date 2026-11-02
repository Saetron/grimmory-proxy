import pytest
from unittest.mock import AsyncMock, patch
import httpx
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient

from app.main import app, db, page_calculator
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.page_calculator import PageCalculator


def test_corrupt_cbz_raises_value_error():
    with pytest.raises(ValueError, match="Corrupt CBZ archive"):
        PageCalculator.count_cbz_pages(b"corrupted not a zip content")

    with pytest.raises(ValueError, match="Empty book file"):
        PageCalculator.count_cbz_pages(b"")


def test_corrupt_epub_raises_value_error():
    with pytest.raises(ValueError, match="Corrupt EPUB archive"):
        PageCalculator.count_epub_pages(b"corrupted not a zip content")

    with pytest.raises(ValueError, match="Empty book file"):
        PageCalculator.count_epub_pages(b"")


def test_corrupt_pdf_raises_value_error():
    with pytest.raises(ValueError, match="Corrupt or unreadable PDF"):
        PageCalculator.count_pdf_pages(b"not a pdf")

    with pytest.raises(ValueError, match="Empty book file"):
        PageCalculator.count_pdf_pages(b"")


@pytest.mark.asyncio
async def test_database_page_calc_errors_crud(tmp_path):
    from app.database import Database
    db_file = str(tmp_path / "test_errors.db")
    local_db = Database(db_path=db_file)
    await local_db.connect()

    # Initially empty
    errors = await local_db.get_page_calc_errors()
    assert len(errors) == 0

    # Record error
    await local_db.record_page_calc_error(
        book_id=101,
        book_title="Faulty Comic",
        series_name="Comics Series",
        library_id=1,
        book_type="CBZ",
        file_path="/media/comics/faulty.cbz",
        error_message="Corrupt CBZ archive (BadZipFile): File is not a zip file",
        job_id=1,
    )

    errors = await local_db.get_page_calc_errors()
    assert len(errors) == 1
    assert errors[0]["book_id"] == 101
    assert errors[0]["book_title"] == "Faulty Comic"
    assert errors[0]["file_path"] == "/media/comics/faulty.cbz"
    assert "BadZipFile" in errors[0]["error_message"]

    # Updating with another error updates existing record (ON CONFLICT)
    await local_db.record_page_calc_error(
        book_id=101,
        book_title="Faulty Comic",
        series_name="Comics Series",
        library_id=1,
        book_type="CBZ",
        file_path="/media/comics/faulty.cbz",
        error_message="Updated error message",
        job_id=2,
    )
    errors = await local_db.get_page_calc_errors()
    assert len(errors) == 1
    assert errors[0]["error_message"] == "Updated error message"

    # Delete specific book error
    await local_db.delete_page_calc_error(101)
    errors = await local_db.get_page_calc_errors()
    assert len(errors) == 0

    # Test clear_page_calc_errors
    await local_db.record_page_calc_error(
        book_id=102,
        book_title="Book 2",
        series_name="Series 2",
        library_id=1,
        book_type="EPUB",
        file_path="/media/book2.epub",
        error_message="Some error",
    )
    await local_db.record_page_calc_error(
        book_id=103,
        book_title="Book 3",
        series_name="Series 3",
        library_id=1,
        book_type="PDF",
        file_path="/media/book3.pdf",
        error_message="PDF error",
    )
    assert len(await local_db.get_page_calc_errors()) == 2
    await local_db.clear_page_calc_errors()
    assert len(await local_db.get_page_calc_errors()) == 0


@pytest.mark.asyncio
async def test_calculate_and_save_book_records_and_clears_error(tmp_path):
    from app.database import Database
    db_file = str(tmp_path / "test_calc.db")
    local_db = Database(db_path=db_file)
    await local_db.connect()

    calc = PageCalculator(db=local_db)

    # 1. Simulate failure during calculation
    with patch.object(calc, "inspect_book_pages", side_effect=ValueError("Corrupt CBZ archive: Bad zip")):
        book = {
            "id": 42,
            "name": "Broken Book",
            "book_type": "CBZ",
            "series_name": "Test Series",
            "library_id": 1,
            "file_path": "/data/broken.cbz",
        }
        res = await calc.calculate_and_save_book(book)
        assert res == "error"

        # Check logs
        assert any("Broken Book" in line and "ERROR" in line for line in calc.logs)

        # Check DB error record
        errors = await local_db.get_page_calc_errors()
        assert len(errors) == 1
        assert errors[0]["book_id"] == 42
        assert errors[0]["file_path"] == "/data/broken.cbz"
        assert "Bad zip" in errors[0]["error_message"]

    # 2. Simulate success on retry - should remove previous error from DB
    from app.models.komga import PageDto
    with patch.object(calc, "inspect_book_pages", return_value=(10, [PageDto(number=1, fileName="p1.jpg")])), \
         patch("app.clients.grimmory.grimmory_client.update_book_metadata", new_callable=AsyncMock):
        res = await calc.calculate_and_save_book(book)
        assert res == "updated"

        # Check DB error record was deleted
        errors = await local_db.get_page_calc_errors()
        assert len(errors) == 0
        assert any("Calculated 10 pages for book 42" in line for line in calc.logs)


@pytest.mark.asyncio
async def test_admin_api_page_calc_errors_endpoints():
    await db.connect()

    # Pre-populate an error
    await db.record_page_calc_error(
        book_id=999,
        book_title="Damaged PDF",
        series_name="Manuals",
        library_id=1,
        book_type="PDF",
        file_path="/books/damaged.pdf",
        error_message="Corrupt or unreadable PDF document",
    )

    page_calculator.log("Initial test page calc log for API")

    admin_session = UserSession(
        user_id=888,
        username="error_test_admin",
        token="tok_error_tester",
        is_admin=True,
        assigned_library_ids=[1],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    AuthService.cache_session(admin_session)
    cookies = {"admin_session": "tok_error_tester"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /admin/api/page-calc/errors
        res = await client.get("/admin/api/page-calc/errors", cookies=cookies)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        assert any(e["book_id"] == 999 for e in data["errors"])

        # 2. GET /admin/api/status should include page_calc_errors and page_calc_logs
        res_status = await client.get("/admin/api/status", cookies=cookies)
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert "page_calc_errors" in status_data
        assert any(e["book_id"] == 999 for e in status_data["page_calc_errors"])
        assert "page_calc_logs" in status_data

        # 3. GET /admin/api/logs should include page_calc_logs
        res_logs = await client.get("/admin/api/logs", cookies=cookies)
        assert res_logs.status_code == 200
        logs_data = res_logs.json()
        assert "page_calc_logs" in logs_data

        # 4. GET /stats should render the Problematic Files section
        res_stats = await client.get("/stats", cookies=cookies)
        assert res_stats.status_code == 200
        assert "Problematic Files & Calculation Errors" in res_stats.text
        assert "Damaged PDF" in res_stats.text

        # 5. POST /admin/api/page-calc/errors/clear
        res_clear = await client.post("/admin/api/page-calc/errors/clear", cookies=cookies)
        assert res_clear.status_code == 200
        assert res_clear.json()["status"] == "cleared"

        # Verify cleared
        res_after = await client.get("/admin/api/page-calc/errors", cookies=cookies)
        assert res_after.json()["total"] == 0
