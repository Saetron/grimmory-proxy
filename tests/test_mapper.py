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


def test_kotlin_dto_serialization_compliance():
    # 1. BookDto serialization compliance
    book_rec = {
        "id": 42,
        "series_id": "20-test-series",
        "library_id": 20,
        "name": "Book 42",
        "number": 1.0,
        "page_count": 100,
        # created and last_modified are None to test defaults
        "created": None,
        "last_modified": None,
        "raw_json": '{"readStatus": "READ", "dateFinished": "2026-02-15T12:00:00Z"}',
    }
    book_dto = KomgaMapper.to_book_dto(book_rec)
    book_json = book_dto.model_dump()

    # Moshi/Kotlin non-null checks:
    assert isinstance(book_json["created"], str) and len(book_json["created"]) > 0
    assert isinstance(book_json["lastModified"], str) and len(book_json["lastModified"]) > 0
    assert isinstance(book_json["fileLastModified"], str) and len(book_json["fileLastModified"]) > 0
    assert isinstance(book_json["media"]["epubIsKepub"], bool)
    assert book_json["media"]["epubIsKepub"] is False
    assert isinstance(book_json["metadata"]["isbn"], str)
    assert isinstance(book_json["metadata"]["created"], str) and len(book_json["metadata"]["created"]) > 0
    assert isinstance(book_json["metadata"]["lastModified"], str) and len(book_json["metadata"]["lastModified"]) > 0
    assert isinstance(book_json["readProgress"]["readDate"], str) and len(book_json["readProgress"]["readDate"]) > 0
    assert isinstance(book_json["readProgress"]["created"], str) and len(book_json["readProgress"]["created"]) > 0
    assert isinstance(book_json["readProgress"]["lastModified"], str) and len(book_json["readProgress"]["lastModified"]) > 0

    # 2. SeriesDto serialization compliance
    series_rec = {
        "id": "20-test-series",
        "library_id": 20,
        "name": "Test Series",
        "books_count": 5,
        "created": None,
        "last_modified": None,
    }
    series_dto = KomgaMapper.to_series_dto(series_rec)
    series_json = series_dto.model_dump()

    assert isinstance(series_json["created"], str) and len(series_json["created"]) > 0
    assert isinstance(series_json["lastModified"], str) and len(series_json["lastModified"]) > 0
    assert isinstance(series_json["fileLastModified"], str) and len(series_json["fileLastModified"]) > 0
    assert isinstance(series_json["metadata"]["created"], str) and len(series_json["metadata"]["created"]) > 0
    assert isinstance(series_json["metadata"]["lastModified"], str) and len(series_json["metadata"]["lastModified"]) > 0
    assert isinstance(series_json["booksMetadata"]["created"], str) and len(series_json["booksMetadata"]["created"]) > 0
    assert isinstance(series_json["booksMetadata"]["lastModified"], str) and len(series_json["booksMetadata"]["lastModified"]) > 0

    # 3. LibraryDto serialization compliance
    lib_rec = {"id": 20, "name": "Novels", "root": "/data/novels"}
    lib_dto = KomgaMapper.to_library_dto(lib_rec)
    lib_json = lib_dto.model_dump()

    assert "seriesCover" in lib_json
    assert lib_json["seriesCover"] == "FIRST"


def test_metadata_extraction_and_mapping():
    # 1. Book with comprehensive metadata
    book_raw = {
        "id": 100,
        "series_id": "1-one-piece",
        "library_id": 1,
        "name": "One Piece Vol. 1",
        "number": 1.0,
        "page_count": 200,
        "raw_json": {
            "metadata": {
                "title": "Romance Dawn",
                "description": "Luffy sets sail to become the Pirate King.",
                "authors": [
                    {"name": "Eiichiro Oda", "role": "writer"},
                    "Akira Toriyama",
                ],
                "publisher": "Viz Media",
                "genres": ["Action", "Adventure", "Comedy"],
                "tags": ["Pirates", "Shonen", "Devil Fruit"],
                "isbn13": "9781569319017",
                "released": "1997-12-24",
            }
        },
    }
    book_dto = KomgaMapper.to_book_dto(book_raw)
    assert book_dto.metadata.title == "Romance Dawn"
    assert book_dto.metadata.summary == "Luffy sets sail to become the Pirate King."
    assert len(book_dto.metadata.authors) == 2
    assert book_dto.metadata.authors[0].name == "Eiichiro Oda"
    assert book_dto.metadata.authors[0].role == "writer"
    assert book_dto.metadata.authors[1].name == "Akira Toriyama"
    assert book_dto.metadata.authors[1].role == "writer"
    assert book_dto.metadata.tags == ["Pirates", "Shonen", "Devil Fruit"]
    assert book_dto.metadata.isbn == "9781569319017"
    assert book_dto.metadata.releaseDate == "1997-12-24"

    # 2. Series with aggregated metadata
    series_raw = {
        "id": "1-one-piece",
        "library_id": 1,
        "name": "One Piece",
        "books_count": 105,
        "raw_json": {
            "name": "One Piece",
            "publisher": "Shueisha",
            "genres": ["Action", "Adventure", "Fantasy"],
            "tags": ["Pirates", "Grand Line"],
            "authors": [
                {"name": "Eiichiro Oda", "role": "writer"},
                {"name": "Takeshi Maekawa", "role": "assistant"},
            ],
            "summary": "Follow Monkey D. Luffy and his Straw Hat crew.",
            "releaseDate": "1997-12-24",
            "language": "ja",
        },
    }
    series_dto = KomgaMapper.to_series_dto(series_raw)
    assert series_dto.metadata.publisher == "Shueisha"
    assert series_dto.metadata.genres == ["Action", "Adventure", "Fantasy"]
    assert series_dto.metadata.tags == ["Pirates", "Grand Line"]
    assert series_dto.metadata.summary == "Follow Monkey D. Luffy and his Straw Hat crew."
    assert series_dto.metadata.language == "ja"

    assert len(series_dto.booksMetadata.authors) == 2
    assert series_dto.booksMetadata.authors[0].name == "Eiichiro Oda"
    assert series_dto.booksMetadata.authors[0].role == "writer"
    assert series_dto.booksMetadata.authors[1].name == "Takeshi Maekawa"
    assert series_dto.booksMetadata.authors[1].role == "assistant"
    assert series_dto.booksMetadata.tags == ["Pirates", "Grand Line"]
    assert series_dto.booksMetadata.releaseDate == "1997-12-24"
    assert series_dto.booksMetadata.summary == "Follow Monkey D. Luffy and his Straw Hat crew."


