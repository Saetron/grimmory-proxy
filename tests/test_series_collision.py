import pytest
from app.services.sync import detect_series_collisions, get_primary_author_name


def test_get_primary_author_name():
    # Empty authors list
    assert get_primary_author_name([]) is None

    # Writer role preferred
    authors = [
        {"name": "Artist Person", "role": "artist"},
        {"name": "Writer Person", "role": "writer"},
    ]
    assert get_primary_author_name(authors) == "Writer Person"

    # Fallback to first author if no writer role
    authors_no_writer = [
        {"name": "Sole Creator", "role": "penciller"},
    ]
    assert get_primary_author_name(authors_no_writer) == "Sole Creator"

    # Strips whitespace
    assert get_primary_author_name([{"name": "  Clean Name  ", "role": "writer"}]) == "Clean Name"


def test_detect_series_collisions_single_oneshot():
    # Single oneshot: no collision, author should NOT be appended
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"title": "Goodbye, Eri", "authors": [{"name": "Tatsuki Fujimoto", "role": "writer"}]},
        }
    ]
    collisions = detect_series_collisions(books)
    assert len(collisions) == 0


def test_detect_series_collisions_two_oneshots_different_authors():
    # Two oneshots with title "REC" by different authors: collision!
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": [{"name": "Q-ta Minami", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": [{"name": "Mizuho Kusanagi", "role": "writer"}]},
        },
    ]
    collisions = detect_series_collisions(books)
    assert (10, "rec") in collisions


def test_detect_series_collisions_two_books_same_oneshot():
    # Multiple parts of the same oneshot by the same author: no collision
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"title": "Look Back", "authors": [{"name": "Tatsuki Fujimoto", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 10,
            "metadata": {"title": "Look Back", "authors": [{"name": "Tatsuki Fujimoto", "role": "writer"}]},
        },
    ]
    collisions = detect_series_collisions(books)
    assert len(collisions) == 0


def test_detect_series_collisions_explicit_series_and_oneshot():
    # Explicit series "REC" and standalone oneshot "REC": collision!
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"seriesName": "REC", "title": "REC Vol 1", "authors": [{"name": "Aoi", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": [{"name": "Q-ta Minami", "role": "writer"}]},
        },
    ]
    collisions = detect_series_collisions(books)
    assert (10, "rec") in collisions


def test_detect_series_collisions_oneshot_author_vs_no_author():
    # One oneshot with author, one with no author: collision!
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": [{"name": "Q-ta Minami", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": []},
        },
    ]
    collisions = detect_series_collisions(books)
    assert (10, "rec") in collisions


def test_detect_series_collisions_different_libraries():
    # Same title in different libraries: each library is independent, no collision in either
    books = [
        {
            "id": 1,
            "libraryId": 1,
            "metadata": {"title": "REC", "authors": [{"name": "Q-ta Minami", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 2,
            "metadata": {"title": "REC", "authors": [{"name": "Mizuho Kusanagi", "role": "writer"}]},
        },
    ]
    collisions = detect_series_collisions(books)
    assert len(collisions) == 0


def test_detect_series_collisions_case_insensitivity():
    # "REC" vs "rec" in the same library: collision detected case-insensitively
    books = [
        {
            "id": 1,
            "libraryId": 10,
            "metadata": {"title": "REC", "authors": [{"name": "Author A", "role": "writer"}]},
        },
        {
            "id": 2,
            "libraryId": 10,
            "metadata": {"title": "rec", "authors": [{"name": "Author B", "role": "writer"}]},
        },
    ]
    collisions = detect_series_collisions(books)
    assert (10, "rec") in collisions


@pytest.mark.asyncio
async def test_sync_catalog_series_naming_integration(monkeypatch):
    """
    Test that sync_catalog correctly resolves series names:
    - Appends author ONLY when there is a collision.
    - Leaves series name without author when there is only one.
    """
    from app.main import db, page_calculator
    from app.clients.grimmory import grimmory_client
    from app.services.sync import SyncService

    await db.connect()

    lib_id = 77
    await db.upsert_libraries([{"id": lib_id, "name": "Test Lib 77", "paths": []}])

    mock_books = [
        # 1 & 2: Two oneshots with title "REC" by different authors -> COLLISION!
        {
            "id": 7701,
            "libraryId": lib_id,
            "metadata": {
                "title": "REC",
                "authors": [{"name": "Q-ta Minami", "role": "writer"}],
            },
            "primaryFile": {"fileName": "rec_minami.cbz", "bookType": "CBZ"},
            "addedOn": "2026-01-01T00:00:00Z",
        },
        {
            "id": 7702,
            "libraryId": lib_id,
            "metadata": {
                "title": "REC",
                "authors": [{"name": "Mizuho Kusanagi", "role": "writer"}],
            },
            "primaryFile": {"fileName": "rec_kusanagi.cbz", "bookType": "CBZ"},
            "addedOn": "2026-01-01T00:00:00Z",
        },
        # 3: Single oneshot with unique title -> NO COLLISION, author NOT appended!
        {
            "id": 7703,
            "libraryId": lib_id,
            "metadata": {
                "title": "Goodbye, Eri",
                "authors": [{"name": "Tatsuki Fujimoto", "role": "writer"}],
            },
            "primaryFile": {"fileName": "goodbye_eri.cbz", "bookType": "CBZ"},
            "addedOn": "2026-01-01T00:00:00Z",
        },
        # 4 & 5: Explicit series "Naruto" -> Preserves explicit series name
        {
            "id": 7704,
            "libraryId": lib_id,
            "metadata": {
                "seriesName": "Naruto",
                "title": "Chapter 1",
                "authors": [{"name": "Masashi Kishimoto", "role": "writer"}],
            },
            "primaryFile": {"fileName": "naruto_01.cbz", "bookType": "CBZ"},
            "addedOn": "2026-01-01T00:00:00Z",
        },
        {
            "id": 7705,
            "libraryId": lib_id,
            "metadata": {
                "seriesName": "Naruto",
                "title": "Chapter 2",
                "authors": [{"name": "Masashi Kishimoto", "role": "writer"}],
            },
            "primaryFile": {"fileName": "naruto_02.cbz", "bookType": "CBZ"},
            "addedOn": "2026-01-01T00:00:00Z",
        },
    ]

    async def mock_get_libraries():
        return [{"id": lib_id, "name": "Test Lib 77", "paths": []}]

    async def mock_get_library_books(lid):
        return mock_books

    async def mock_get_all_books():
        return mock_books

    monkeypatch.setattr(grimmory_client, "get_libraries", mock_get_libraries)
    monkeypatch.setattr(grimmory_client, "get_library_books", mock_get_library_books)
    monkeypatch.setattr(grimmory_client, "get_all_books", mock_get_all_books)

    sync_service = SyncService(db, page_calculator)
    await sync_service.sync_all_metadata()

    # Verify series in database:
    # 1. "REC - Q-ta Minami" was disambiguated
    # 2. "REC - Mizuho Kusanagi" was disambiguated
    # 3. "Goodbye, Eri" remained WITHOUT author appended
    # 4. "Naruto" remained "Naruto" with 2 books
    book1 = await db.get_book_by_id(7701)
    book2 = await db.get_book_by_id(7702)
    book3 = await db.get_book_by_id(7703)
    book4 = await db.get_book_by_id(7704)
    book5 = await db.get_book_by_id(7705)

    assert book1 is not None
    assert book2 is not None
    assert book3 is not None
    assert book4 is not None
    assert book5 is not None

    series1 = await db.get_series_by_id(book1["series_id"])
    series2 = await db.get_series_by_id(book2["series_id"])
    series3 = await db.get_series_by_id(book3["series_id"])
    series4 = await db.get_series_by_id(book4["series_id"])

    assert series1["name"] == "REC - Q-ta Minami"
    assert series2["name"] == "REC - Mizuho Kusanagi"
    assert series3["name"] == "Goodbye, Eri"
    assert series4["name"] == "Naruto"

    # Books 1 and 2 are in separate series
    assert book1["series_id"] != book2["series_id"]

    # Books 4 and 5 share the same series
    assert book4["series_id"] == book5["series_id"]
    assert series4["books_count"] == 2
