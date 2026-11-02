import asyncio
import hashlib
import io
import logging
import os
from typing import Any, Dict, Optional

from app.clients.grimmory import grimmory_client
from app.models.grimmory import (
    GrimmoryCbxProgress,
    GrimmoryEpubProgress,
    GrimmoryPdfProgress,
    GrimmoryReadProgressRequest,
)
from app.services.mapper import parse_raw_json

logger = logging.getLogger("grimmory_proxy.koreader_utils")


def compute_koreader_hash(file_or_path: Any) -> Optional[str]:
    """
    Computes KOReader partial MD5 checksum for a file path or bytes object.
    Samples 1024-byte chunks at 12 seek positions (-1..10):
    i = -1 -> offset 0
    i = 0..10 -> offset 1024 << (2 * i)
    """
    step, size = 1024, 1024
    hasher = hashlib.md5()
    try:
        if isinstance(file_or_path, (bytes, bytearray)):
            bio = io.BytesIO(file_or_path)
            for i in range(-1, 11):
                offset = 0 if i == -1 else step << (2 * i)
                bio.seek(offset)
                chunk = bio.read(size)
                if not chunk:
                    break
                hasher.update(chunk)
            return hasher.hexdigest()
        elif isinstance(file_or_path, str) and os.path.isfile(file_or_path):
            with open(file_or_path, "rb") as f:
                for i in range(-1, 11):
                    offset = 0 if i == -1 else step << (2 * i)
                    f.seek(offset)
                    chunk = f.read(size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()
        return None
    except Exception:
        return None


def resolve_koreader_doc_hash(book_record: Dict[str, Any]) -> str:
    """
    Resolves the KOReader document hash for a book.
    Prioritizes computed koreader_hash column, then partial MD5 from file_path,
    then primaryFile hash, and falls back to stringified book ID.
    """
    if book_record.get("koreader_hash"):
        return str(book_record["koreader_hash"])

    file_path = book_record.get("file_path")
    if file_path:
        computed = compute_koreader_hash(file_path)
        if computed:
            return computed

    raw = parse_raw_json(book_record.get("raw_json"))

    pfile = raw.get("primaryFile") or {}
    for k in ("hash", "md5", "koreaderHash"):
        val = pfile.get(k) or raw.get(k)
        if val:
            return str(val)

    return str(book_record.get("id") or "")


def format_koreader_progress(book_type: Optional[str], page: int, total_pages: int, percentage: float) -> str:
    """
    Formats the progress string according to KOReader expectations.
    For paginated media (CBZ, CBR, PDF), uses page number.
    For EPUB, uses /body/DocFragment[X].0 locator.
    """
    b_type = (book_type or "EPUB").upper()
    if b_type in ("CBZ", "CBR", "PDF", "DIVINA"):
        return str(max(1, page))
    return f"/body/DocFragment[{max(1, page)}].0"


def build_grimmory_progress_request(
    book_id: int,
    page: int,
    percentage: float,
    completed: bool,
    timestamp: str,
    href: Optional[str] = None,
    cfi: Optional[str] = None,
) -> GrimmoryReadProgressRequest:
    """Builds a standardized GrimmoryReadProgressRequest payload."""
    return GrimmoryReadProgressRequest(
        bookId=book_id,
        cbxProgress=GrimmoryCbxProgress(page=page, percentage=percentage, lastRead=timestamp),
        pdfProgress=GrimmoryPdfProgress(page=page, percentage=percentage, lastRead=timestamp),
        epubProgress=GrimmoryEpubProgress(percentage=percentage, href=href, cfi=cfi, lastRead=timestamp),
        dateFinished=timestamp if completed else None,
        readStatus="READ" if completed else "READING",
        lastRead=timestamp,
        progressValid=True,
    )


def schedule_koreader_progress_sync(
    record: Optional[Dict[str, Any]],
    book_id: int,
    page: int,
    page_count: int,
    percentage: float,
    token: str,
) -> None:
    """Asynchronously forwards reading progress to the upstream Grimmory KOReader endpoint."""
    try:
        doc_hash = resolve_koreader_doc_hash(record or {"id": book_id})
        koreader_prog_str = format_koreader_progress(
            record.get("book_type") if record else None, page, page_count, percentage
        )
        asyncio.create_task(
            grimmory_client.update_koreader_progress(
                doc_hash,
                koreader_prog_str,
                percentage / 100.0,
                token=token,
            )
        )
    except Exception as e:
        logger.debug(f"Could not forward KOReader progress for book {book_id}: {e}")
