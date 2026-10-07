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

logger = logging.getLogger("grimmory_proxy.mapper")


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


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


def format_iso_timestamp(ts: Optional[str] = None) -> str:
    if ts and isinstance(ts, str) and len(ts.strip()) >= 10:
        cleaned = ts.strip().replace(" ", "T")
        if not cleaned.endswith("Z") and "+" not in cleaned:
            cleaned += "Z"
        return cleaned
    return "2026-01-01T00:00:00Z"


class KomgaMapper:
    @staticmethod
    def to_library_dto(record: Dict[str, Any]) -> LibraryDto:
        return LibraryDto(
            id=str(record["id"]),
            name=record.get("name", f"Library {record['id']}"),
            root=record.get("root", ""),
            seriesCover="FIRST",
            seriesCoverSort="FIRST",
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
                if isinstance(raw, str):
                    raw = json.loads(raw)
            except Exception:
                pass
            if not isinstance(raw, dict):
                raw = {}

            # If raw contains pageCount, ensure page_count reflects it
            raw_meta = raw.get("metadata", {})
            if raw_meta.get("pageCount") and page_count <= 0:
                page_count = raw_meta["pageCount"]

        metadata_obj = raw.get("metadata", {})
        title = metadata_obj.get("title") or name
        summary = metadata_obj.get("description", "")
        raw_released = (
            record.get("released")
            or metadata_obj.get("released")
            or raw.get("released")
            or metadata_obj.get("releaseDate")
            or raw.get("releaseDate")
            or metadata_obj.get("publishedDate")
        )
        release_date = None
        if raw_released:
            r_str = str(raw_released).strip()
            if len(r_str) >= 10 and r_str[:4].isdigit() and r_str[4] == "-" and r_str[7] == "-":
                release_date = r_str[:10]
            elif len(r_str) == 4 and r_str.isdigit():
                release_date = f"{r_str}-01-01"
            else:
                release_date = r_str[:10] if len(r_str) >= 10 else r_str

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
            epubIsKepub=False,
        )

        book_created = format_iso_timestamp(record.get("created"))
        book_modified = format_iso_timestamp(record.get("last_modified"))

        metadata = BookMetadataDto(
            title=title,
            summary=summary,
            number=str(int(number) if number.is_integer() else number),
            numberSort=number,
            releaseDate=release_date,
            authors=authors_list,
            tags=tags_list,
            isbn=isbn or "",
            created=book_created,
            lastModified=book_modified,
        )

        # Reading progress from user or record
        read_progress = user_progress
        has_user_query = (
            "user_page" in record
            or "user_completed" in record
            or "user_read_date" in record
        )

        if not read_progress and (
            record.get("user_page") is not None
            or record.get("user_completed") is not None
            or record.get("user_read_date") is not None
        ):
            read_progress = ReadProgressDto(
                page=record.get("user_page") or 1,
                completed=bool(record.get("user_completed")),
                readDate=format_iso_timestamp(record.get("user_read_date") or book_modified),
                created=book_created,
                lastModified=book_modified,
            )
        elif not read_progress and not has_user_query and raw:
            # Check if book raw JSON has progress for the current user
            if raw.get("readStatus") == "READ":
                read_progress = ReadProgressDto(
                    page=page_count or 1,
                    completed=True,
                    readDate=format_iso_timestamp(raw.get("dateFinished") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )
            elif raw.get("cbxProgress") and isinstance(raw["cbxProgress"], dict):
                cbx = raw["cbxProgress"]
                cbx_perc = _safe_float(cbx.get("percentage"))
                read_progress = ReadProgressDto(
                    page=_safe_int(cbx.get("page"), 1),
                    completed=cbx_perc >= 99.0,
                    readDate=format_iso_timestamp(cbx.get("lastRead") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )
            elif raw.get("pdfProgress") and isinstance(raw["pdfProgress"], dict):
                pdf = raw["pdfProgress"]
                pdf_perc = _safe_float(pdf.get("percentage"))
                read_progress = ReadProgressDto(
                    page=_safe_int(pdf.get("page"), 1),
                    completed=pdf_perc >= 99.0,
                    readDate=format_iso_timestamp(pdf.get("lastRead") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )
            elif raw.get("epubProgress") and isinstance(raw["epubProgress"], dict):
                epub = raw["epubProgress"]
                perc = _safe_float(epub.get("percentage"))
                calc_page = max(1, round((perc / 100.0) * page_count)) if page_count > 0 else 1
                read_progress = ReadProgressDto(
                    page=calc_page,
                    completed=perc >= 99.0,
                    readDate=format_iso_timestamp(epub.get("lastRead") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )

        return BookDto(
            id=book_id,
            seriesId=series_id,
            seriesTitle=record.get("series_name") or series_id,
            libraryId=lib_id,
            name=name,
            url=f"/api/v1/books/{book_id}",
            number=int(number),
            created=book_created,
            lastModified=book_modified,
            fileLastModified=book_modified,
            sizeBytes=file_size_kb * 1024,
            size=format_file_size(file_size_kb),
            media=media,
            metadata=metadata,
            readProgress=read_progress,
            deleted=bool(record.get("deleted", 0)),
            fileHash="",
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

        series_created = format_iso_timestamp(record.get("created"))
        series_modified = format_iso_timestamp(record.get("last_modified"))

        meta = SeriesMetadataDto(
            status="ONGOING",
            created=series_created,
            lastModified=series_modified,
            title=name,
            titleSort=record.get("sort_title", name),
            readingDirection="LEFT_TO_RIGHT",
            totalBookCount=count,
        )

        books_meta = BookMetadataAggregationDto(
            summary=f"Series {name}",
            created=series_created,
            lastModified=series_modified,
        )

        unread_count = max(0, count - books_read_count - books_in_progress_count)

        return SeriesDto(
            id=series_id,
            libraryId=library_id,
            name=name,
            url=f"/api/v1/series/{series_id}",
            created=series_created,
            lastModified=series_modified,
            fileLastModified=series_modified,
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
        Komga and Divina-compliant readers use this for reading.
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
