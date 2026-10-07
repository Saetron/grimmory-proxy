import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite

logger = logging.getLogger("grimmory_proxy.database")

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS libraries (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT,
    organization_mode TEXT,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,
    library_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    sort_title TEXT,
    books_count INTEGER DEFAULT 0,
    created TEXT,
    last_modified TEXT,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (library_id) REFERENCES libraries (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    series_id TEXT NOT NULL,
    library_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    number REAL DEFAULT 1.0,
    book_type TEXT,
    file_path TEXT,
    file_size_kb INTEGER DEFAULT 0,
    page_count INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    created TEXT,
    last_modified TEXT,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (series_id) REFERENCES series (id) ON DELETE CASCADE,
    FOREIGN KEY (library_id) REFERENCES libraries (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS book_pages (
    book_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    size_bytes INTEGER DEFAULT 0,
    PRIMARY KEY (book_id, page_number),
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS authors (
    name TEXT PRIMARY KEY,
    count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS page_calc_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    total_books INTEGER DEFAULT 0,
    processed_books INTEGER DEFAULT 0,
    updated_books INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    current_book TEXT,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_books_series_id ON books(series_id);
CREATE INDEX IF NOT EXISTS idx_books_library_id ON books(library_id);
CREATE INDEX IF NOT EXISTS idx_books_page_count ON books(page_count);
CREATE INDEX IF NOT EXISTS idx_books_created ON books(created);
CREATE INDEX IF NOT EXISTS idx_series_library_id ON series(library_id);
CREATE INDEX IF NOT EXISTS idx_series_name ON series(name);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._pool: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    def get_connection(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.db_path)

    # ---------------- Library Operations ----------------

    async def upsert_libraries(self, libraries: List[Dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            for lib in libraries:
                paths = lib.get("paths", [])
                root = paths[0].get("path", "") if paths else ""
                await db.execute(
                    """
                    INSERT INTO libraries (id, name, root, organization_mode, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        root = excluded.root,
                        organization_mode = excluded.organization_mode,
                        raw_json = excluded.raw_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        lib["id"],
                        lib.get("name", f"Library {lib['id']}"),
                        root,
                        lib.get("organizationMode", "BOOK_PER_FILE"),
                        json.dumps(lib),
                    ),
                )
            await db.commit()

    async def get_libraries(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM libraries ORDER BY name ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_library(self, library_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM libraries WHERE id = ?", (library_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- Series Operations ----------------

    async def upsert_series_batch(self, series_list: List[Dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            for s in series_list:
                await db.execute(
                    """
                    INSERT INTO series (id, library_id, name, slug, sort_title, books_count, created, last_modified, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        slug = excluded.slug,
                        sort_title = excluded.sort_title,
                        books_count = excluded.books_count,
                        last_modified = excluded.last_modified,
                        raw_json = excluded.raw_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        s["id"],
                        s["library_id"],
                        s["name"],
                        s["slug"],
                        s.get("sort_title", s["name"]),
                        s.get("books_count", 0),
                        s.get("created"),
                        s.get("last_modified"),
                        json.dumps(s.get("raw_json", {})),
                    ),
                )
            await db.commit()

    async def get_series_list(
        self,
        library_id: Optional[int] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "name",
        sort_dir: str = "asc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        params: List[Any] = []

        if library_id is not None:
            conditions.append("library_id = ?")
            params.append(library_id)

        if search:
            conditions.append("name LIKE ?")
            params.append(f"%{search}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Validate sort column
        allowed_sorts = {
            "name": "sort_title",
            "title": "sort_title",
            "created": "created",
            "lastmodified": "last_modified",
            "books_count": "books_count",
        }
        order_col = allowed_sorts.get(sort_by.lower(), "sort_title")
        order_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM series {where_clause}", params)
            count_row = await count_cursor.fetchone()
            total = count_row["total"] if count_row else 0

            query = f"""
                SELECT * FROM series {where_clause}
                ORDER BY {order_col} {order_direction}
                LIMIT ? OFFSET ?
            """
            cursor = await db.execute(query, params + [limit, offset])
            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total

    async def get_series_by_id(self, series_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM series WHERE id = ?", (series_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- Books Operations ----------------

    async def upsert_books_batch(self, books: List[Dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            for b in books:
                await db.execute(
                    """
                    INSERT INTO books (id, series_id, library_id, name, number, book_type, file_path, file_size_kb, page_count, deleted, created, last_modified, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        series_id = excluded.series_id,
                        library_id = excluded.library_id,
                        name = excluded.name,
                        number = excluded.number,
                        book_type = excluded.book_type,
                        file_path = excluded.file_path,
                        file_size_kb = excluded.file_size_kb,
                        page_count = CASE WHEN excluded.page_count > 0 THEN excluded.page_count ELSE books.page_count END,
                        deleted = excluded.deleted,
                        last_modified = excluded.last_modified,
                        raw_json = excluded.raw_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        b["id"],
                        b["series_id"],
                        b["library_id"],
                        b["name"],
                        b.get("number", 1.0),
                        b.get("book_type"),
                        b.get("file_path"),
                        b.get("file_size_kb", 0),
                        b.get("page_count", 0),
                        b.get("deleted", 0),
                        b.get("created"),
                        b.get("last_modified"),
                        json.dumps(b.get("raw_json", {})),
                    ),
                )
            await db.commit()

    async def update_book_page_count(self, book_id: int, page_count: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Also update pageCount in raw_json
            cursor = await db.execute("SELECT raw_json FROM books WHERE id = ?", (book_id,))
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    if "metadata" in data and isinstance(data["metadata"], dict):
                        data["metadata"]["pageCount"] = page_count
                    updated_json = json.dumps(data)
                    await db.execute(
                        "UPDATE books SET page_count = ?, raw_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (page_count, updated_json, book_id),
                    )
                except Exception:
                    await db.execute(
                        "UPDATE books SET page_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (page_count, book_id),
                    )
            else:
                await db.execute(
                    "UPDATE books SET page_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (page_count, book_id),
                )
            await db.commit()

    async def get_book_by_id(self, book_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM books WHERE id = ?", (book_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_books_list(
        self,
        library_id: Optional[int] = None,
        series_id: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "number",
        sort_dir: str = "asc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = ["deleted = 0"]
        params: List[Any] = []

        if library_id is not None:
            conditions.append("library_id = ?")
            params.append(library_id)

        if series_id is not None:
            conditions.append("series_id = ?")
            params.append(series_id)

        if search:
            conditions.append("name LIKE ?")
            params.append(f"%{search}%")

        where_clause = f"WHERE {' AND '.join(conditions)}"

        allowed_sorts = {
            "number": "number",
            "name": "name",
            "created": "created",
            "lastmodified": "last_modified",
            "filelastmodified": "last_modified",
        }
        order_col = allowed_sorts.get(sort_by.lower(), "number")
        order_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM books {where_clause}", params)
            count_row = await count_cursor.fetchone()
            total = count_row["total"] if count_row else 0

            query = f"""
                SELECT * FROM books {where_clause}
                ORDER BY {order_col} {order_direction}
                LIMIT ? OFFSET ?
            """
            cursor = await db.execute(query, params + [limit, offset])
            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total

    async def get_books_missing_pages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM books WHERE deleted = 0 AND (page_count IS NULL OR page_count <= 0) ORDER BY id ASC"
        if limit:
            query += f" LIMIT {limit}"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_books_for_page_calc(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM books WHERE deleted = 0 ORDER BY id ASC"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Page Dimension / Detail Cache ----------------

    async def upsert_book_pages(self, book_id: int, pages: List[Dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM book_pages WHERE book_id = ?", (book_id,))
            for p in pages:
                await db.execute(
                    """
                    INSERT INTO book_pages (book_id, page_number, file_name, media_type, width, height, size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        p["number"],
                        p.get("fileName", f"page-{p['number']}"),
                        p.get("mediaType", "image/jpeg"),
                        p.get("width", 0),
                        p.get("height", 0),
                        p.get("sizeBytes", 0),
                    ),
                )
            await db.commit()

    async def get_book_pages(self, book_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM book_pages WHERE book_id = ? ORDER BY page_number ASC",
                (book_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Sync & Stats State ----------------

    async def set_state(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sync_state (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            await db.commit()

    async def get_state(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            lib_count = (await (await db.execute("SELECT COUNT(*) as count FROM libraries")).fetchone())["count"]
            series_count = (await (await db.execute("SELECT COUNT(*) as count FROM series")).fetchone())["count"]
            books_count = (await (await db.execute("SELECT COUNT(*) as count FROM books WHERE deleted = 0")).fetchone())["count"]
            books_with_pages = (await (await db.execute("SELECT COUNT(*) as count FROM books WHERE deleted = 0 AND page_count > 0")).fetchone())["count"]
            books_missing_pages = books_count - books_with_pages

            # Formats breakdown
            type_cursor = await db.execute("SELECT book_type, COUNT(*) as count FROM books WHERE deleted = 0 GROUP BY book_type")
            type_rows = await type_cursor.fetchall()
            formats_breakdown = {r["book_type"] or "UNKNOWN": r["count"] for r in type_rows}

            last_sync = await self.get_state("last_sync_time")
            sync_status = await self.get_state("sync_status") or "idle"

            # Check DB file size
            db_size_bytes = 0
            if Path(self.db_path).exists():
                db_size_bytes = Path(self.db_path).stat().st_size

            return {
                "libraries_count": lib_count,
                "series_count": series_count,
                "books_count": books_count,
                "books_with_pages": books_with_pages,
                "books_missing_pages": books_missing_pages,
                "formats_breakdown": formats_breakdown,
                "last_sync_time": last_sync,
                "sync_status": sync_status,
                "database_size_bytes": db_size_bytes,
            }

    # ---------------- Page Calculation Job Management ----------------

    async def create_page_calc_job(self, total_books: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO page_calc_jobs (status, total_books, processed_books, updated_books, error_count, started_at)
                VALUES ('running', ?, 0, 0, 0, ?)
                """,
                (total_books, now),
            )
            await db.commit()
            return cursor.lastrowid or 1

    async def update_page_calc_progress(
        self,
        job_id: int,
        processed: int,
        updated: int,
        errors: int,
        current_book: str = "",
        status: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            completed_at = datetime.now(timezone.utc).isoformat() if status in ["completed", "failed", "stopped"] else None
            if status:
                await db.execute(
                    """
                    UPDATE page_calc_jobs
                    SET processed_books = ?, updated_books = ?, error_count = ?, current_book = ?,
                        status = ?, completed_at = COALESCE(?, completed_at), error_message = ?
                    WHERE id = ?
                    """,
                    (processed, updated, errors, current_book, status, completed_at, error_message, job_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE page_calc_jobs
                    SET processed_books = ?, updated_books = ?, error_count = ?, current_book = ?
                    WHERE id = ?
                    """,
                    (processed, updated, errors, current_book, job_id),
                )
            await db.commit()

    async def get_latest_page_calc_job(self) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM page_calc_jobs ORDER BY id DESC LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None
