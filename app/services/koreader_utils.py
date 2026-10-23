import hashlib
import io
import os
import re
from typing import Any, Dict, Optional


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

    raw = book_record.get("raw_json") or {}
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            raw = {}

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
