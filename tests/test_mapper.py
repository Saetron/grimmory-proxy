from app.models.komga import PageDto, ReadProgressDto
from app.services.mapper import KomgaMapper, format_file_size, get_media_type_and_profile


def test_format_file_size():
    assert format_file_size(0) == "0 B"
    assert format_file_size(500) == "500.0 KB"
    assert format_file_size(2048) == "2.0 MB"


def test_media_type_and_profile():
    mt, mp = get_media_type_and_profile("CBZ")
    assert mt == "application/x-cbz"
    assert mp == "DIVINA"

    mt, mp = get_media_type_and_profile("EPUB")
    assert mt == "application/epub+zip"
    assert mp == "EPUB"

    mt, mp = get_media_type_and_profile("PDF")
    assert mt == "application/pdf"
    assert mp == "PDF"


def test_to_library_dto():
    record = {"id": 14, "name": "Manga", "root": "/books/Manga"}
    dto = KomgaMapper.to_library_dto(record)
    assert dto.id == "14"
    assert dto.name == "Manga"
    assert dto.root == "/books/Manga"
    assert dto.unavailable is False


def test_to_book_dto_with_user_progress():
    record = {
        "id": 101,
        "series_id": "1-berserk",
        "library_id": 1,
        "name": "Berserk Vol. 1",
        "number": 1.0,
        "book_type": "CBZ",
        "file_size_kb": 25000,
        "page_count": 220,
        "deleted": 0,
        "created": "2026-01-01T00:00:00Z",
        "last_modified": "2026-01-01T00:00:00Z",
    }
    user_prog = ReadProgressDto(page=45, completed=False)
    dto = KomgaMapper.to_book_dto(record, user_progress=user_prog)
    assert dto.id == "101"
    assert dto.seriesId == "1-berserk"
    assert dto.media.pagesCount == 220
    assert dto.media.mediaProfile == "DIVINA"
    assert dto.readProgress is not None
    assert dto.readProgress.page == 45
    assert dto.readProgress.completed is False


def test_to_divina_manifest():
    record = {
        "id": 1,
        "series_id": "1-test",
        "library_id": 1,
        "name": "Test Book",
        "page_count": 2,
    }
    book = KomgaMapper.to_book_dto(record)
    pages = [
        PageDto(number=1, fileName="p1.jpg", mediaType="image/jpeg", width=1200, height=1600),
        PageDto(number=2, fileName="p2.jpg", mediaType="image/jpeg", width=1200, height=1600),
    ]
    manifest = KomgaMapper.to_divina_manifest(book, pages, base_url="http://localhost:8080")
    assert manifest["metadata"]["title"] == "Test Book"
    assert manifest["metadata"]["numberOfPages"] == 2
    assert len(manifest["readingOrder"]) == 2
    assert manifest["readingOrder"][0]["href"] == "http://localhost:8080/api/v1/books/1/pages/1"
