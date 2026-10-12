import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.config import settings
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


def extract_authors_data(data: Dict[str, Any]) -> List[Dict[str, str]]:
    if not isinstance(data, dict):
        return []
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    candidates = []

    # Check meta.get("authors") and data.get("authors")
    for source in [meta.get("authors"), data.get("authors")]:
        if isinstance(source, list):
            candidates.extend(source)
        elif isinstance(source, str) and source.strip():
            for part in source.split(","):
                if part.strip():
                    candidates.append(part.strip())

    # Check singular meta.get("author") and data.get("author")
    for source in [meta.get("author"), data.get("author")]:
        if isinstance(source, str) and source.strip():
            for part in source.split(","):
                if part.strip():
                    candidates.append(part.strip())
        elif isinstance(source, dict):
            candidates.append(source)
        elif isinstance(source, list):
            candidates.extend(source)

    authors: List[Dict[str, str]] = []
    seen = set()
    for item in candidates:
        name = ""
        role = "writer"
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            role = str(item.get("role") or "writer").strip() or "writer"
        if name:
            key = (name.lower(), role.lower())
            if key not in seen:
                seen.add(key)
                authors.append({"name": name, "role": role})
    return authors


def extract_authors(data: Dict[str, Any]) -> List[AuthorDto]:
    return [AuthorDto(name=a["name"], role=a["role"]) for a in extract_authors_data(data)]


def extract_publisher(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for source in [
        meta.get("publisher"),
        data.get("publisher"),
        meta.get("publishers"),
        data.get("publishers"),
        meta.get("imprint"),
        data.get("imprint"),
    ]:
        if isinstance(source, str) and source.strip():
            return source.strip()
        elif isinstance(source, dict) and source.get("name"):
            return str(source["name"]).strip()
        elif isinstance(source, list) and source:
            first = source[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
            elif isinstance(first, dict) and first.get("name"):
                return str(first["name"]).strip()
    return ""


def extract_genres(data: Dict[str, Any]) -> List[str]:
    if not isinstance(data, dict):
        return []
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    candidates = []
    for source in [meta.get("genres"), data.get("genres"), meta.get("genre"), data.get("genre")]:
        if isinstance(source, list):
            candidates.extend(source)
        elif isinstance(source, str) and source.strip():
            parts = re.split(r"[,;]", source)
            candidates.extend([p.strip() for p in parts if p.strip()])

    genres: List[str] = []
    seen = set()
    for item in candidates:
        val = ""
        if isinstance(item, str):
            val = item.strip()
        elif isinstance(item, dict) and item.get("name"):
            val = str(item["name"]).strip()
        if val and val.lower() not in seen:
            seen.add(val.lower())
            genres.append(val)
    return genres


def extract_tags(data: Dict[str, Any]) -> List[str]:
    if not isinstance(data, dict):
        return []
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    candidates = []
    for source in [
        meta.get("tags"),
        data.get("tags"),
        meta.get("tag"),
        data.get("tag"),
        meta.get("keywords"),
        data.get("keywords"),
        meta.get("categories"),
        data.get("categories"),
    ]:
        if isinstance(source, list):
            candidates.extend(source)
        elif isinstance(source, str) and source.strip():
            parts = re.split(r"[,;]", source)
            candidates.extend([p.strip() for p in parts if p.strip()])

    tags: List[str] = []
    seen = set()
    for item in candidates:
        val = ""
        if isinstance(item, str):
            val = item.strip()
        elif isinstance(item, dict) and item.get("name"):
            val = str(item["name"]).strip()
        if val and val.lower() not in seen:
            seen.add(val.lower())
            tags.append(val)
    return tags


def extract_summary(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for source in [
        meta.get("description"),
        meta.get("summary"),
        meta.get("synopsis"),
        data.get("description"),
        data.get("summary"),
        data.get("synopsis"),
    ]:
        if isinstance(source, str) and source.strip():
            return source.strip()
    return ""



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
        summary = extract_summary(raw) or metadata_obj.get("description", "")
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

        isbn = metadata_obj.get("isbn13") or metadata_obj.get("isbn10") or raw.get("isbn13") or raw.get("isbn10") or raw.get("isbn")

        authors_list: List[AuthorDto] = extract_authors(raw)
        tags_list: List[str] = extract_tags(raw)

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

        book_links = []
        for l in metadata_obj.get("links", []):
            if isinstance(l, dict) and "url" in l:
                book_links.append(l)
        if settings.public_grimmory_url:
            book_links.append({
                "label": "Grimmory",
                "url": f"{settings.public_grimmory_url}/book/{book_id}",
            })

        metadata = BookMetadataDto(
            title=title,
            summary=summary,
            number=str(int(number) if number.is_integer() else number),
            numberSort=number,
            releaseDate=release_date,
            authors=authors_list,
            tags=tags_list,
            isbn=isbn or "",
            links=book_links,
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
                cbx_page = _safe_int(cbx.get("page"), 1)
                is_comp = cbx_perc >= 95.0 or (page_count > 0 and cbx_page >= page_count)
                read_progress = ReadProgressDto(
                    page=cbx_page,
                    completed=is_comp,
                    readDate=format_iso_timestamp(cbx.get("lastRead") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )
            elif raw.get("pdfProgress") and isinstance(raw["pdfProgress"], dict):
                pdf = raw["pdfProgress"]
                pdf_perc = _safe_float(pdf.get("percentage"))
                pdf_page = _safe_int(pdf.get("page"), 1)
                is_comp = pdf_perc >= 95.0 or (page_count > 0 and pdf_page >= page_count)
                read_progress = ReadProgressDto(
                    page=pdf_page,
                    completed=is_comp,
                    readDate=format_iso_timestamp(pdf.get("lastRead") or book_modified),
                    created=book_created,
                    lastModified=book_modified,
                )
            elif raw.get("epubProgress") and isinstance(raw["epubProgress"], dict):
                epub = raw["epubProgress"]
                perc = _safe_float(epub.get("percentage"))
                calc_page = max(1, round((perc / 100.0) * page_count)) if page_count > 0 else 1
                is_comp = perc >= 95.0 or (page_count > 0 and calc_page >= page_count)
                read_progress = ReadProgressDto(
                    page=calc_page,
                    completed=is_comp,
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
        books_read_count: Optional[int] = None,
        books_in_progress_count: Optional[int] = None,
    ) -> SeriesDto:
        series_id = str(record["id"])
        library_id = str(record["library_id"])
        name = record.get("name", "Unknown Series")
        count = books_count if books_count is not None else record.get("books_count", 0)

        read_count = books_read_count if books_read_count is not None else (record.get("books_read_count", 0) or 0)
        in_prog_count = books_in_progress_count if books_in_progress_count is not None else (record.get("books_in_progress_count", 0) or 0)
        unread_count = max(0, count - read_count - in_prog_count)

        series_created = format_iso_timestamp(record.get("created"))
        series_modified = format_iso_timestamp(record.get("last_modified"))

        series_links = []
        if settings.public_grimmory_url:
            series_links.append({
                "label": "Grimmory",
                "url": f"{settings.public_grimmory_url}/series/{quote(name, safe='')}",
            })

        # Parse raw_json if available
        raw = {}
        if record.get("raw_json"):
            try:
                raw = json.loads(record["raw_json"]) if isinstance(record["raw_json"], str) else record["raw_json"]
            except Exception:
                pass
        if not isinstance(raw, dict):
            raw = {}

        publisher = record.get("publisher") or raw.get("publisher", "") or extract_publisher(raw)
        genres = raw.get("genres") or extract_genres(raw)
        tags = raw.get("tags") or extract_tags(raw)
        authors = extract_authors(raw)

        series_summary = raw.get("summary") or record.get("summary") or extract_summary(raw) or ""
        books_summary = series_summary or f"Series {name}"
        raw_rel = raw.get("releaseDate") or record.get("released")
        release_date = None
        if raw_rel:
            r_str = str(raw_rel).strip()
            if len(r_str) >= 10 and r_str[:4].isdigit() and r_str[4] == "-" and r_str[7] == "-":
                release_date = r_str[:10]
            elif len(r_str) == 4 and r_str.isdigit():
                release_date = f"{r_str}-01-01"
            else:
                release_date = r_str[:10] if len(r_str) >= 10 else r_str

        language = raw.get("language") or record.get("language") or "en"
        reading_direction = raw.get("readingDirection") or "LEFT_TO_RIGHT"
        series_status = raw.get("status") or "ONGOING"
        age_rating = raw.get("ageRating")

        meta = SeriesMetadataDto(
            status=series_status,
            created=series_created,
            lastModified=series_modified,
            title=name,
            titleSort=record.get("sort_title", name),
            readingDirection=reading_direction,
            publisher=publisher,
            ageRating=age_rating,
            language=language,
            genres=genres,
            tags=tags,
            totalBookCount=count,
            links=series_links,
            summary=series_summary,
        )

        books_meta = BookMetadataAggregationDto(
            authors=authors,
            tags=tags,
            releaseDate=release_date,
            summary=books_summary,
            created=series_created,
            lastModified=series_modified,
        )

        return SeriesDto(
            id=series_id,
            libraryId=library_id,
            name=name,
            url=f"/api/v1/series/{series_id}",
            created=series_created,
            lastModified=series_modified,
            fileLastModified=series_modified,
            booksCount=count,
            booksReadCount=read_count,
            booksUnreadCount=unread_count,
            booksInProgressCount=in_prog_count,
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
