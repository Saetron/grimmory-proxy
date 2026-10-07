import json
import logging
from typing import Any, Dict, List, Optional

from app.models.komga import (
    AuthorDto,
    BookDto,
    BookMetadataAggregationDto,
    BookMetadataDto,
    CollectionDto,
    LibraryDto,
    MediaDto,
    PageDto,
    ReadListDto,
    ReadProgressDto,
    SeriesDto,
    SeriesMetadataDto,
)

logger = logging.getLogger("komic.mapper")


def format_file_size(size_kb: int) -> str:
    if not size_kb or size_kb <= 0:
        return "0 B"
    size_bytes = size_kb * 1024
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_media_type_and_profile(book_type: str) -> tuple[str, str]:
    btype = (book_type or "EPUB").upper()
    if btype in ["CBX", "CBZ"]:
        return "application/x-cbz", "DIVINA"
    elif btype == "PDF":
        return "application/pdf", "PDF"
    elif btype in ["MOBI", "AZW3", "AZW"]:
        return "application/x-mobipocket-ebook", "EPUB"
    elif btype == "FB2":
        return "application/fictionbook2+zip", "DIVINA"
    elif btype == "AUDIOBOOK":
        return "audio/*", "AUDIOBOOK"
    else:
        return "application/epub+zip", "EPUB"


class KomgaMapper:
    @staticmethod
    def to_library_dto(record: Dict[str, Any]) -> LibraryDto:
        return LibraryDto(
            id=str(record["id"]),
            name=record.get("name", f"Library {record['id']}"),
            root=record.get("root", ""),
            unavailable=False,
        )

    @staticmethod
    def to_book_dto(
        record: Dict[str, Any],
        user_progress: Optional[ReadProgressDto] = None,
    ) -> BookDto:
        book_id = str(record["id"])
        series_id = str(record.get("series_id", "1-unknown"))
        lib_id = str(record.get("library_id", "1"))
        name = record.get("name", f"Book {book_id}")
        number = float(record.get("number", 1.0))
        page_count = record.get("page_count", 0)
        book_type = record.get("book_type", "EPUB")
        file_size_kb = record.get("file_size_kb", 0)

        # Parse raw_json if available
        raw = {}
        if record.get("raw_json"):
            try:
                raw = json.loads(record["raw_json"]) if isinstance(record["raw_json"], str) else record["raw_json"]
            except Exception:
                pass

            # If raw contains pageCount, ensure page_count reflects it
            raw_meta = raw.get("metadata", {})
            if raw_meta.get("pageCount") and page_count <= 0:
                page_count = raw_meta["pageCount"]

        metadata_obj = raw.get("metadata", {})
        title = metadata_obj.get("title") or name
        summary = metadata_obj.get("description", "")
        release_date = metadata_obj.get("publishedDate")
        isbn = metadata_obj.get("isbn13") or metadata_obj.get("isbn10")

        authors_list: List[AuthorDto] = []
        for a in metadata_obj.get("authors", []):
            if isinstance(a, str):
                authors_list.append(AuthorDto(name=a, role="writer"))
            elif isinstance(a, dict) and "name" in a:
                authors_list.append(AuthorDto(name=a["name"], role=a.get("role", "writer")))

        tags_list: List[str] = []
        for t in metadata_obj.get("tags", []):
            if isinstance(t, str):
                tags_list.append(t)
            elif isinstance(t, dict) and "name" in t:
                tags_list.append(t["name"])

        media_type, media_profile = get_media_type_and_profile(book_type)

        media = MediaDto(
            status="READY",
            mediaType=media_type,
            pagesCount=page_count,
            mediaProfile=media_profile,
            epubDivinaCompatible=(media_profile == "DIVINA"),
        )

        metadata = BookMetadataDto(
            title=title,
            summary=summary,
            number=str(int(number) if number.is_integer() else number),
            numberSort=number,
            releaseDate=release_date,
            authors=authors_list,
            tags=tags_list,
            isbn=isbn,
        )

        # Reading progress from user or record
        read_progress = user_progress
        if not read_progress and raw:
            # Check if book raw JSON has progress for the current user
            if raw.get("readStatus") == "READ":
                read_progress = ReadProgressDto(
                    page=page_count or 1,
                    completed=True,
                    readDate=raw.get("dateFinished"),
                )
            elif raw.get("cbxProgress"):
                cbx = raw["cbxProgress"]
                read_progress = ReadProgressDto(
                    page=cbx.get("page", 1),
                    completed=cbx.get("percentage", 0.0) >= 99.0,
                )
            elif raw.get("pdfProgress"):
                pdf = raw["pdfProgress"]
                read_progress = ReadProgressDto(
                    page=pdf.get("page", 1),
                    completed=pdf.get("percentage", 0.0) >= 99.0,
                )
            elif raw.get("epubProgress"):
                epub = raw["epubProgress"]
                perc = epub.get("percentage", 0.0)
                calc_page = max(1, round((perc / 100.0) * page_count)) if page_count > 0 else 1
                read_progress = ReadProgressDto(
                    page=calc_page,
                    completed=perc >= 99.0,
                )

        return BookDto(
            id=book_id,
            seriesId=series_id,
            seriesTitle=record.get("series_name", series_id),
            libraryId=lib_id,
            name=name,
            url=f"/api/v1/books/{book_id}",
            number=int(number),
            created=record.get("created"),
            lastModified=record.get("last_modified"),
            fileLastModified=record.get("last_modified"),
            sizeBytes=file_size_kb * 1024,
            size=format_file_size(file_size_kb),
            media=media,
            metadata=metadata,
            readProgress=read_progress,
            deleted=bool(record.get("deleted", 0)),
            oneshot=False,
        )

    @staticmethod
    def to_series_dto(
        record: Dict[str, Any],
        books_count: Optional[int] = None,
        books_read_count: int = 0,
        books_in_progress_count: int = 0,
    ) -> SeriesDto:
        series_id = str(record["id"])
        library_id = str(record["library_id"])
        name = record.get("name", "Unknown Series")
        count = books_count if books_count is not None else record.get("books_count", 0)

        meta = SeriesMetadataDto(
            status="ONGOING",
            title=name,
            titleSort=record.get("sort_title", name),
            readingDirection="LEFT_TO_RIGHT",
            totalBookCount=count,
        )

        books_meta = BookMetadataAggregationDto(
            summary=f"Series {name}",
            created=record.get("created"),
            lastModified=record.get("last_modified"),
        )

        unread_count = max(0, count - books_read_count - books_in_progress_count)

        return SeriesDto(
            id=series_id,
            libraryId=library_id,
            name=name,
            url=f"/api/v1/series/{series_id}",
            created=record.get("created"),
            lastModified=record.get("last_modified"),
            fileLastModified=record.get("last_modified"),
            booksCount=count,
            booksReadCount=books_read_count,
            booksUnreadCount=unread_count,
            booksInProgressCount=books_in_progress_count,
            metadata=meta,
            booksMetadata=books_meta,
            deleted=False,
            oneshot=(count == 1),
        )

    @staticmethod
    def to_divina_manifest(book: BookDto, pages: List[PageDto], base_url: str = "") -> Dict[str, Any]:
        """
        Builds a Readium Web Publication / Divina manifest.
        Komic and other Divina-compliant readers use this for reading.
        """
        reading_order = []
        for p in pages:
            reading_order.append({
                "type": p.mediaType,
                "href": f"{base_url}/api/v1/books/{book.id}/pages/{p.number}",
                "width": p.width or 1200,
                "height": p.height or 1600,
            })

        return {
            "@context": "https://readium.org/webpub-manifest/context.jsonld",
            "metadata": {
                "@type": "http://schema.org/VisualArtwork",
                "title": book.metadata.title or book.name,
                "identifier": book.id,
                "numberOfPages": len(pages),
                "readingProgression": "ltr",
            },
            "readingOrder": reading_order,
            "resources": [],
            "links": [
                {
                    "rel": "self",
                    "href": f"{base_url}/api/v1/books/{book.id}/manifest/divina",
                    "type": "application/divina+json",
                }
            ],
        }
