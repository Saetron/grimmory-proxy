import pytest
from app.database import Database


@pytest.mark.asyncio
async def test_database_crud(tmp_path):
    db_file = tmp_path / "test.db"
    db = Database(str(db_file))
    await db.connect()

    # 1. Upsert library
    await db.upsert_libraries([{"id": 1, "name": "Novels", "paths": [{"path": "/books/novels"}]}])
    libs = await db.get_libraries()
    assert len(libs) == 1
    assert libs[0]["name"] == "Novels"

    # 2. Upsert series
    await db.upsert_series_batch([{
        "id": "1-overlord",
        "library_id": 1,
        "name": "Overlord",
        "slug": "overlord",
        "books_count": 1,
    }])
    series_list, total_s = await db.get_series_list()
    assert total_s == 1
    assert series_list[0]["id"] == "1-overlord"

    # 3. Upsert books
    await db.upsert_books_batch([{
        "id": 10,
        "series_id": "1-overlord",
        "library_id": 1,
        "name": "Overlord Vol. 1",
        "number": 1.0,
        "book_type": "EPUB",
        "page_count": 0,
    }])
    books, total_b = await db.get_books_list()
    assert total_b == 1
    assert books[0]["id"] == 10
    assert books[0]["page_count"] == 0

    # 4. Check missing pages
    missing = await db.get_books_missing_pages()
    assert len(missing) == 1

    # 5. Update page count
    await db.update_book_page_count(10, 350)
    book = await db.get_book_by_id(10)
    assert book["page_count"] == 350

    missing_after = await db.get_books_missing_pages()
    assert len(missing_after) == 0

    # 6. Check stats
    stats = await db.get_stats()
    assert stats["books_count"] == 1
    assert stats["books_with_pages"] == 1
    assert stats["books_missing_pages"] == 0


@pytest.mark.asyncio
async def test_concurrent_writes_and_wal_mode(tmp_path):
    import asyncio
    db_file = tmp_path / "test_concurrent.db"
    db = Database(str(db_file))
    await db.connect()

    await db.upsert_libraries([{"id": 1, "name": "Lib 1", "paths": []}])
    await db.upsert_series_batch([{"id": "s1", "library_id": 1, "name": "Series 1", "slug": "series-1"}])

    # Run 50 concurrent writes simultaneously to test lock-free concurrency
    async def write_progression(i: int):
        await db.upsert_r2_progression(user_id=1, book_id=i, progression_json=f'{{"page": {i}}}')
        await db.upsert_read_progress(user_id=1, book_id=i, page=i, completed=False, read_date="2026-01-01T00:00:00Z")

    tasks = [write_progression(i) for i in range(1, 51)]
    await asyncio.gather(*tasks)

    # Verify all were written successfully
    prog = await db.get_r2_progression(user_id=1, book_id=25)
    assert prog is not None
    assert prog["page"] == 25

