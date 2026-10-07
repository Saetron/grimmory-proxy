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
