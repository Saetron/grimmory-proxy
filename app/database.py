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
    released TEXT,
    created TEXT,
    last_modified TEXT,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (series_id) REFERENCES series (id) ON DELETE CASCADE,
    FOREIGN KEY (library_id) REFERENCES libraries (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    token TEXT,
    is_admin INTEGER DEFAULT 0,
    assigned_libraries TEXT,
    last_connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_sync_progress_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS read_progress (
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    page INTEGER DEFAULT 1,
    completed INTEGER DEFAULT 0,
    read_date TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, book_id),
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS client_settings (
    scope TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    allow_unauthorized INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope, user_id, name)
);

CREATE TABLE IF NOT EXISTS r2_progression (
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    progression_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, book_id),
    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
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
    removed_books INTEGER DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_read_progress_user ON read_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_read_progress_book ON read_progress(book_id);
CREATE INDEX IF NOT EXISTS idx_read_progress_date ON read_progress(read_date);
CREATE INDEX IF NOT EXISTS idx_r2_progression_user ON r2_progression(user_id);
CREATE INDEX IF NOT EXISTS idx_r2_progression_book ON r2_progression(book_id);
CREATE INDEX IF NOT EXISTS idx_series_library_id ON series(library_id);
CREATE INDEX IF NOT EXISTS idx_series_name ON series(name);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
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
            # Automatic schema migration for existing databases
            try:
                await db.execute("ALTER TABLE books ADD COLUMN released TEXT")
            except Exception:
                pass
            try:
                await db.execute("CREATE INDEX IF NOT EXISTS idx_books_released ON books(released)")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE page_calc_jobs ADD COLUMN removed_books INTEGER DEFAULT 0")
            except Exception:
                pass
            # Backfill released column from raw_json if books were previously synced without released column
            try:
                await db.execute("""
                    UPDATE books SET released = (
                        COALESCE(
                            json_extract(raw_json, '$.metadata.released'),
                            json_extract(raw_json, '$.released'),
                            json_extract(raw_json, '$.metadata.releaseDate'),
                            json_extract(raw_json, '$.metadata.publishedDate')
                        )
                    )
                    WHERE (released IS NULL OR released = '') AND raw_json IS NOT NULL
                """)
            except Exception:
                pass
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
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "name",
        sort_dir: str = "asc",
        unpaged: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        params: List[Any] = []

        target_libs = library_ids if library_ids is not None else ([library_id] if library_id is not None else None)
        if target_libs is not None:
            if len(target_libs) == 0:
                return [], 0
            placeholders = ",".join("?" for _ in target_libs)
            conditions.append(f"library_id IN ({placeholders})")
            params.extend(target_libs)

        if search:
            conditions.append("name LIKE ?")
            params.append(f"%{search}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Normalize sort column
        clean_sort = sort_by.lower().replace("metadata.", "").replace("sort", "")
        allowed_sorts = {
            "name": "sort_title",
            "title": "sort_title",
            "created": "created",
            "createddate": "created",
            "lastmodified": "last_modified",
            "lastmodifieddate": "last_modified",
            "books_count": "books_count",
            "bookscount": "books_count",
        }
        order_col = allowed_sorts.get(clean_sort, "sort_title")
        order_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM series {where_clause}", params)
            count_row = await count_cursor.fetchone()
            total = count_row["total"] if count_row else 0

            if unpaged:
                query = f"""
                    SELECT * FROM series {where_clause}
                    ORDER BY {order_col} {order_direction}
                """
                cursor = await db.execute(query, params)
            else:
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

    async def cleanup_empty_series(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Recalculate books_count on series for accuracy
            await db.execute("""
                UPDATE series SET books_count = (
                    SELECT COUNT(*) FROM books WHERE books.series_id = series.id AND books.deleted = 0
                )
            """)
            # Prune empty series
            await db.execute("""
                DELETE FROM series
                WHERE id NOT IN (SELECT DISTINCT series_id FROM books WHERE deleted = 0)
                   OR books_count = 0
            """)
            await db.commit()

    async def find_series_by_id_or_slug(self, identifier: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM series WHERE id = ?", (identifier,))
            row = await cursor.fetchone()
            if row:
                return dict(row)

            cursor = await db.execute("SELECT * FROM series WHERE id LIKE ? ORDER BY id ASC LIMIT 1", (f"{identifier}-%",))
            row = await cursor.fetchone()
            if row:
                return dict(row)

            cursor = await db.execute("SELECT * FROM series WHERE slug = ? OR slug LIKE ? ORDER BY id ASC LIMIT 1", (identifier, f"{identifier}-%"))
            row = await cursor.fetchone()
            if row:
                return dict(row)

            # 4. Suffix match by id (e.g. if identifier is a slug without library prefix)
            cursor = await db.execute("SELECT * FROM series WHERE id LIKE ? ORDER BY id ASC LIMIT 1", (f"%-{identifier}",))
            row = await cursor.fetchone()
            if row:
                return dict(row)

            return None

    # ---------------- Books Operations ----------------

    async def upsert_books_batch(self, books: List[Dict[str, Any]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            for b in books:
                await db.execute(
                    """
                    INSERT INTO books (id, series_id, library_id, name, number, book_type, file_path, file_size_kb, page_count, deleted, released, created, last_modified, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                        released = excluded.released,
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
                        b.get("released"),
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

    async def get_book_by_id(
        self, book_id: int, include_deleted: bool = False, user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        clause = "WHERE b.id = ?" if include_deleted else "WHERE b.id = ? AND b.deleted = 0"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if user_id is not None:
                query = f"""
                    SELECT b.*, rp.page as user_page, rp.completed as user_completed, rp.read_date as user_read_date
                    FROM books b
                    LEFT JOIN read_progress rp ON rp.book_id = b.id AND rp.user_id = ?
                    {clause}
                """
                cursor = await db.execute(query, (user_id, book_id))
            else:
                cursor = await db.execute(f"SELECT b.* FROM books b {clause}", (book_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def mark_book_deleted(self, book_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE books SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (book_id,))
            await db.execute("DELETE FROM book_pages WHERE book_id = ?", (book_id,))
            await db.commit()
        await self.cleanup_empty_series()

    async def mark_books_deleted(self, book_ids: List[int]) -> int:
        if not book_ids:
            return 0
        async with aiosqlite.connect(self.db_path) as db:
            chunk_size = 500
            for i in range(0, len(book_ids), chunk_size):
                chunk = book_ids[i:i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                await db.execute(f"UPDATE books SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", chunk)
                await db.execute(f"DELETE FROM book_pages WHERE book_id IN ({placeholders})", chunk)
            await db.commit()
        await self.cleanup_empty_series()
        return len(book_ids)

    async def get_active_book_ids(self) -> List[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT id FROM books WHERE deleted = 0")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    # ---------------- Read Progress Operations ----------------

    async def upsert_read_progress(
        self,
        user_id: int,
        book_id: int,
        page: int,
        completed: bool,
        read_date: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO read_progress (user_id, book_id, page, completed, read_date, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, book_id) DO UPDATE SET
                    page = excluded.page,
                    completed = excluded.completed,
                    read_date = excluded.read_date,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, book_id, page, 1 if completed else 0, read_date),
            )
            await db.commit()

    async def delete_read_progress(self, user_id: int, book_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM read_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
            await db.commit()

    async def delete_read_progress_batch(self, user_id: int, book_ids: List[int]) -> None:
        if not book_ids:
            return
        async with aiosqlite.connect(self.db_path) as db:
            chunk_size = 500
            for i in range(0, len(book_ids), chunk_size):
                chunk = book_ids[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                await db.execute(
                    f"DELETE FROM read_progress WHERE user_id = ? AND book_id IN ({placeholders})",
                    [user_id] + chunk,
                )
            await db.commit()

    async def upsert_read_progress_batch(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """
                INSERT INTO read_progress (user_id, book_id, page, completed, read_date, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, book_id) DO UPDATE SET
                    page = excluded.page,
                    completed = excluded.completed,
                    read_date = excluded.read_date,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    (
                        r["user_id"],
                        r["book_id"],
                        r.get("page", 1),
                        1 if r.get("completed") else 0,
                        r["read_date"],
                    )
                    for r in records
                ],
            )
            await db.commit()

    async def get_book_read_progress(self, user_id: int, book_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM read_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- User Operations ----------------

    async def upsert_user(
        self,
        user_id: int,
        username: str,
        token: Optional[str] = None,
        is_admin: bool = False,
        assigned_libraries: Optional[List[int]] = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (id, username, token, is_admin, assigned_libraries, last_connected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    username = excluded.username,
                    token = COALESCE(excluded.token, users.token),
                    is_admin = excluded.is_admin,
                    assigned_libraries = excluded.assigned_libraries,
                    last_connected_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    username,
                    token,
                    1 if is_admin else 0,
                    json.dumps(assigned_libraries or []),
                ),
            )
            await db.commit()

    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_user_progress_sync_time(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_sync_progress_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            await db.commit()

    # ---------------- Client Settings Operations ----------------

    async def get_client_settings(
        self, scope: str, user_id: int = 0, allow_unauthorized_only: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        clause = "WHERE scope = ? AND user_id = ?"
        params: List[Any] = [scope, user_id]
        if allow_unauthorized_only:
            clause += " AND allow_unauthorized = 1"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(f"SELECT * FROM client_settings {clause}", params)
            rows = await cursor.fetchall()
            return {
                r["name"]: {
                    "value": r["value"],
                    "allowUnauthorized": bool(r["allow_unauthorized"]),
                }
                for r in rows
            }

    async def save_client_settings(
        self, scope: str, user_id: int, settings: Dict[str, Dict[str, Any]]
    ) -> None:
        if not settings:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """
                INSERT INTO client_settings (scope, user_id, name, value, allow_unauthorized, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, user_id, name) DO UPDATE SET
                    value = excluded.value,
                    allow_unauthorized = excluded.allow_unauthorized,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    (
                        scope,
                        user_id,
                        name,
                        data.get("value", ""),
                        1 if data.get("allowUnauthorized") else 0,
                    )
                    for name, data in settings.items()
                ],
            )
            await db.commit()

    async def delete_client_settings(
        self, scope: str, user_id: int, names: List[str]
    ) -> None:
        if not names:
            return
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" for _ in names)
            await db.execute(
                f"DELETE FROM client_settings WHERE scope = ? AND user_id = ? AND name IN ({placeholders})",
                [scope, user_id] + names,
            )
            await db.commit()

    # ---------------- R2 Progression Operations ----------------

    async def get_r2_progression(
        self, user_id: int, book_id: int
    ) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT progression_json FROM r2_progression WHERE user_id = ? AND book_id = ?",
                (user_id, book_id),
            )
            row = await cursor.fetchone()
            if row and row["progression_json"]:
                try:
                    return json.loads(row["progression_json"])
                except Exception:
                    return None
            return None

    async def upsert_r2_progression(
        self, user_id: int, book_id: int, progression_json: str
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO r2_progression (user_id, book_id, progression_json, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, book_id) DO UPDATE SET
                    progression_json = excluded.progression_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, book_id, progression_json),
            )
            await db.commit()

    async def delete_r2_progression(self, user_id: int, book_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM r2_progression WHERE user_id = ? AND book_id = ?",
                (user_id, book_id),
            )
            await db.commit()

    async def get_books_list(
        self,
        library_id: Optional[int] = None,
        library_ids: Optional[List[int]] = None,
        series_id: Optional[str] = None,
        series_ids: Optional[List[str]] = None,
        search: Optional[str] = None,
        read_status: Optional[List[str]] = None,
        user_id: Optional[int] = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "number",
        sort_dir: str = "asc",
        unpaged: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = ["b.deleted = 0"]
        params: List[Any] = []

        target_libs = library_ids if library_ids is not None else ([library_id] if library_id is not None else None)
        if target_libs is not None:
            if len(target_libs) == 0:
                return [], 0
            placeholders = ",".join("?" for _ in target_libs)
            conditions.append(f"b.library_id IN ({placeholders})")
            params.extend(target_libs)

        target_series = series_ids if series_ids is not None else ([series_id] if series_id is not None else None)
        if target_series is not None:
            if len(target_series) == 0:
                return [], 0
            series_clauses = []
            for s_id in target_series:
                series_clauses.append("(b.series_id = ? OR b.series_id LIKE ? OR b.series_id LIKE ?)")
                params.extend([s_id, f"{s_id}-%", f"%-{s_id}"])
            conditions.append(f"({' OR '.join(series_clauses)})")

        if search:
            conditions.append("b.name LIKE ?")
            params.append(f"%{search}%")

        # Read status expressions
        eff_read_date = "COALESCE(rp.read_date, json_extract(b.raw_json, '$.dateFinished'), json_extract(b.raw_json, '$.cbxProgress.lastRead'), json_extract(b.raw_json, '$.epubProgress.lastRead'), json_extract(b.raw_json, '$.pdfProgress.lastRead'))"
        is_completed = "(rp.completed = 1 OR (rp.book_id IS NULL AND json_extract(b.raw_json, '$.readStatus') = 'READ'))"
        is_in_prog = "((rp.book_id IS NOT NULL AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) OR (rp.book_id IS NULL AND (json_extract(b.raw_json, '$.readStatus') IN ('READING', 'IN_PROGRESS') OR (json_extract(b.raw_json, '$.cbxProgress.percentage') > 0 AND json_extract(b.raw_json, '$.cbxProgress.percentage') < 99) OR (json_extract(b.raw_json, '$.epubProgress.percentage') > 0 AND json_extract(b.raw_json, '$.epubProgress.percentage') < 99) OR (json_extract(b.raw_json, '$.pdfProgress.percentage') > 0 AND json_extract(b.raw_json, '$.pdfProgress.percentage') < 99))))"
        is_unread = f"(NOT {is_completed} AND NOT {is_in_prog})"

        if read_status:
            statuses = {str(s).upper() for s in read_status}
            status_clauses = []
            if "IN_PROGRESS" in statuses:
                status_clauses.append(is_in_prog)
            if "READ" in statuses:
                status_clauses.append(is_completed)
            if "UNREAD" in statuses:
                status_clauses.append(is_unread)
            if status_clauses:
                conditions.append(f"({' OR '.join(status_clauses)})")

        clean_sort = sort_by.lower().replace("metadata.", "").replace("sort", "")
        if clean_sort in ("readprogress.readdate", "readdate", "readprogress") and not read_status:
            # When sorting by read date without explicit read_status, only include books with read progress
            conditions.append(f"{eff_read_date} IS NOT NULL")

        where_clause = f"WHERE {' AND '.join(conditions)}"

        allowed_sorts = {
            "number": "b.number",
            "name": "b.name",
            "title": "b.name",
            "created": "b.created",
            "createddate": "b.created",
            "lastmodified": "b.last_modified",
            "lastmodifieddate": "b.last_modified",
            "filelastmodified": "b.last_modified",
            "release": "COALESCE(b.released, json_extract(b.raw_json, '$.metadata.released'), b.created)",
            "releasedate": "COALESCE(b.released, json_extract(b.raw_json, '$.metadata.released'), b.created)",
            "readdate": eff_read_date,
            "readprogress.readdate": eff_read_date,
        }
        order_col = allowed_sorts.get(clean_sort, "b.number")
        order_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        join_rp_clause = "LEFT JOIN read_progress rp ON (b.id = rp.book_id AND (rp.user_id = ? OR ? IS NULL))"
        rp_join_params = [user_id, user_id]

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_query = f"""
                SELECT COUNT(*) as total FROM books b
                {join_rp_clause}
                {where_clause}
            """
            count_cursor = await db.execute(count_query, rp_join_params + params)
            count_row = await count_cursor.fetchone()
            total = count_row["total"] if count_row else 0

            select_cols = """
                b.*, s.name as series_name,
                rp.page as user_page, rp.completed as user_completed, rp.read_date as user_read_date
            """

            if unpaged:
                query = f"""
                    SELECT {select_cols} FROM books b
                    LEFT JOIN series s ON b.series_id = s.id
                    {join_rp_clause}
                    {where_clause}
                    ORDER BY {order_col} {order_direction}
                """
                cursor = await db.execute(query, rp_join_params + params)
            else:
                query = f"""
                    SELECT {select_cols} FROM books b
                    LEFT JOIN series s ON b.series_id = s.id
                    {join_rp_clause}
                    {where_clause}
                    ORDER BY {order_col} {order_direction}
                    LIMIT ? OFFSET ?
                """
                cursor = await db.execute(query, rp_join_params + params + [limit, offset])

            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total

    async def get_books_ondeck(
        self,
        library_ids: Optional[List[int]] = None,
        user_id: Optional[int] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Calculates On Deck books for the user according to Komga specifications:
        For each series that has been started:
        1. If a book in the series is currently in progress, that in-progress book is On Deck.
        2. Else if at least one book in the series has been completed, the NEXT unread book in that series is On Deck.
        Series that have never been started (or are completely finished) are excluded.
        """
        if library_ids is not None and len(library_ids) == 0:
            return [], 0

        lib_filter = ""
        lib_params = []
        if library_ids:
            placeholders = ",".join("?" for _ in library_ids)
            lib_filter = f"AND b.library_id IN ({placeholders})"
            lib_params = list(library_ids)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"""
                SELECT b.*, s.name as series_name,
                       rp.page as user_page, rp.completed as user_completed, rp.read_date as user_read_date
                FROM books b
                LEFT JOIN series s ON b.series_id = s.id
                LEFT JOIN read_progress rp ON (b.id = rp.book_id AND (rp.user_id = ? OR ? IS NULL))
                WHERE b.deleted = 0 {lib_filter}
                ORDER BY b.series_id, b.number ASC
            """
            cursor = await db.execute(query, [user_id, user_id] + lib_params)
            rows = await cursor.fetchall()

        series_books: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            book_dict = dict(r)
            series_books.setdefault(book_dict["series_id"], []).append(book_dict)

        ondeck_candidates = []

        for s_id, books in series_books.items():
            books.sort(key=lambda x: x.get("number", 1.0))

            in_progress_books = []
            completed_numbers = []
            unread_books = []
            latest_activity_date = None

            for b in books:
                raw = {}
                if b.get("raw_json"):
                    try:
                        raw = json.loads(b["raw_json"]) if isinstance(b["raw_json"], str) else b["raw_json"]
                    except Exception:
                        pass

                has_user_rp = b.get("user_page") is not None or b.get("user_completed") is not None or b.get("user_read_date")
                if has_user_rp:
                    is_comp = bool(b.get("user_completed"))
                    is_prog = not is_comp and ((b.get("user_page") or 0) > 0 or b.get("user_read_date") is not None)
                    r_date = b.get("user_read_date")
                else:
                    is_comp = raw.get("readStatus") == "READ" or raw.get("cbxProgress", {}).get("percentage", 0) >= 99.0 or raw.get("epubProgress", {}).get("percentage", 0) >= 99.0 or raw.get("pdfProgress", {}).get("percentage", 0) >= 99.0
                    cbx_perc = raw.get("cbxProgress", {}).get("percentage", 0)
                    epub_perc = raw.get("epubProgress", {}).get("percentage", 0)
                    pdf_perc = raw.get("pdfProgress", {}).get("percentage", 0)
                    is_prog = not is_comp and (raw.get("readStatus") in ("READING", "IN_PROGRESS") or 0 < cbx_perc < 99.0 or 0 < epub_perc < 99.0 or 0 < pdf_perc < 99.0)
                    r_date = raw.get("dateFinished") or raw.get("cbxProgress", {}).get("lastRead") or raw.get("epubProgress", {}).get("lastRead") or raw.get("pdfProgress", {}).get("lastRead")

                if r_date and (not latest_activity_date or r_date > latest_activity_date):
                    latest_activity_date = r_date

                if is_prog:
                    in_progress_books.append((b, r_date or b.get("last_modified") or ""))
                elif is_comp:
                    completed_numbers.append(b.get("number", 1.0))
                else:
                    unread_books.append(b)

            if in_progress_books:
                # Pick the most recently active in-progress book in the series
                in_progress_books.sort(key=lambda x: x[1], reverse=True)
                chosen = in_progress_books[0][0]
                ondeck_candidates.append({
                    "book": chosen,
                    "activity_date": in_progress_books[0][1] or latest_activity_date or chosen.get("last_modified") or "",
                })
            elif completed_numbers:
                max_completed = max(completed_numbers)
                next_books = [ub for ub in unread_books if ub.get("number", 1.0) > max_completed]
                if not next_books and unread_books:
                    next_books = unread_books
                if next_books:
                    next_book = next_books[0]
                    ondeck_candidates.append({
                        "book": next_book,
                        "activity_date": latest_activity_date or next_book.get("last_modified") or "",
                    })

        ondeck_candidates.sort(key=lambda x: x["activity_date"], reverse=True)
        total = len(ondeck_candidates)
        sliced = [c["book"] for c in ondeck_candidates[offset : offset + limit]]
        return sliced, total

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
        removed: int = 0,
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
                    SET processed_books = ?, updated_books = ?, removed_books = ?, error_count = ?, current_book = ?,
                        status = ?, completed_at = COALESCE(?, completed_at), error_message = ?
                    WHERE id = ?
                    """,
                    (processed, updated, removed, errors, current_book, status, completed_at, error_message, job_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE page_calc_jobs
                    SET processed_books = ?, updated_books = ?, removed_books = ?, error_count = ?, current_book = ?
                    WHERE id = ?
                    """,
                    (processed, updated, removed, errors, current_book, job_id),
                )
            await db.commit()

    async def get_latest_page_calc_job(self) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM page_calc_jobs ORDER BY id DESC LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None
