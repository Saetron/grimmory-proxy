import asyncio
from contextlib import asynccontextmanager
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid
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

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    key TEXT UNIQUE NOT NULL,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_books_series_id ON books(series_id);
CREATE INDEX IF NOT EXISTS idx_books_library_id ON books(library_id);
CREATE INDEX IF NOT EXISTS idx_books_page_count ON books(page_count);
CREATE INDEX IF NOT EXISTS idx_books_created ON books(created);
CREATE INDEX IF NOT EXISTS idx_books_deleted ON books(deleted);
CREATE INDEX IF NOT EXISTS idx_read_progress_user ON read_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_read_progress_book ON read_progress(book_id);
CREATE INDEX IF NOT EXISTS idx_read_progress_date ON read_progress(read_date);
CREATE INDEX IF NOT EXISTS idx_read_progress_user_comp ON read_progress(user_id, completed);
CREATE INDEX IF NOT EXISTS idx_r2_progression_user ON r2_progression(user_id);
CREATE INDEX IF NOT EXISTS idx_r2_progression_book ON r2_progression(book_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys(key);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_series_library_id ON series(library_id);
CREATE INDEX IF NOT EXISTS idx_series_name ON series(name);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._pool: Optional[aiosqlite.Connection] = None

    @asynccontextmanager
    async def get_db(self):
        async with aiosqlite.connect(self.db_path, timeout=60.0) as db:
            await db.execute("PRAGMA busy_timeout = 60000")
            yield db

    async def connect(self) -> None:
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass
        async with aiosqlite.connect(self.db_path, timeout=60.0) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA busy_timeout = 60000")
            await db.execute("PRAGMA synchronous = NORMAL")
            await db.execute("PRAGMA foreign_keys = ON")
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
        logger.info(f"Database initialized at {self.db_path} (WAL mode enabled, busy_timeout=60s)")

    def get_connection(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.db_path, timeout=60.0)

    # ---------------- Library Operations ----------------

    async def upsert_libraries(self, libraries: List[Dict[str, Any]]) -> None:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM libraries ORDER BY name ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_library(self, library_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM libraries WHERE id = ?", (library_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---------------- Series Operations ----------------

    async def upsert_series_batch(self, series_list: List[Dict[str, Any]]) -> None:
        if not series_list:
            return
        chunk_size = 1000
        for i in range(0, len(series_list), chunk_size):
            chunk = series_list[i : i + chunk_size]
            for attempt in range(5):
                try:
                    async with self.get_db() as db:
                        await db.executemany(
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
                            [
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
                                )
                                for s in chunk
                            ],
                        )
                        await db.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 4:
                        await asyncio.sleep(0.1 * (2 ** attempt))
                    else:
                        raise
            await asyncio.sleep(0)

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
        user_id: Optional[int] = None,
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

        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM series {where_clause}", params)
            count_row = await count_cursor.fetchone()
            total = count_row["total"] if count_row else 0

            if user_id is not None:
                if unpaged:
                    query = f"""
                        SELECT s.*,
                               (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 1) as books_read_count,
                               (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) as books_in_progress_count
                        FROM (
                            SELECT * FROM series {where_clause}
                            ORDER BY {order_col} {order_direction}
                        ) s
                    """
                    cursor = await db.execute(query, [user_id, user_id] + params)
                else:
                    query = f"""
                        SELECT s.*,
                               (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 1) as books_read_count,
                               (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) as books_in_progress_count
                        FROM (
                            SELECT * FROM series {where_clause}
                            ORDER BY {order_col} {order_direction}
                            LIMIT ? OFFSET ?
                        ) s
                    """
                    cursor = await db.execute(query, [user_id, user_id] + params + [limit, offset])
            else:
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

    async def get_series_by_id(self, series_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            if user_id is not None:
                query = """
                    SELECT s.*,
                           (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 1) as books_read_count,
                           (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) as books_in_progress_count
                    FROM series s WHERE s.id = ?
                """
                cursor = await db.execute(query, (user_id, user_id, series_id))
            else:
                cursor = await db.execute("SELECT * FROM series WHERE id = ?", (series_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def cleanup_empty_series(self) -> None:
        async with self.get_db() as db:
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

    async def find_series_by_id_or_slug(self, identifier: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id FROM series WHERE id = ?", (identifier,))
            row = await cursor.fetchone()
            if not row:
                cursor = await db.execute("SELECT id FROM series WHERE id LIKE ? ORDER BY id ASC LIMIT 1", (f"{identifier}-%",))
                row = await cursor.fetchone()
            if not row:
                cursor = await db.execute("SELECT id FROM series WHERE slug = ? OR slug LIKE ? ORDER BY id ASC LIMIT 1", (identifier, f"{identifier}-%"))
                row = await cursor.fetchone()
            if not row:
                cursor = await db.execute("SELECT id FROM series WHERE id LIKE ? ORDER BY id ASC LIMIT 1", (f"%-{identifier}",))
                row = await cursor.fetchone()
            if not row:
                cursor = await db.execute("SELECT id FROM series WHERE name = ? COLLATE NOCASE ORDER BY id ASC LIMIT 1", (identifier,))
                row = await cursor.fetchone()
            if not row and "-" in identifier and identifier.split("-")[0].isdigit():
                slug_part = identifier.split("-", 1)[1]
                cursor = await db.execute("SELECT id FROM series WHERE slug = ? OR slug LIKE ? OR id LIKE ? ORDER BY id ASC LIMIT 1", (slug_part, f"{slug_part}-%", f"%-{slug_part}"))
                row = await cursor.fetchone()
            if row:
                actual_id = row["id"]
                return await self.get_series_by_id(actual_id, user_id=user_id)
            return None

    async def get_books_by_series(self, series_id: str) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            s_cursor = await db.execute(
                "SELECT id FROM series WHERE id = ? OR slug = ? OR id LIKE ? OR id LIKE ?",
                (series_id, series_id, f"{series_id}-%", f"%-{series_id}"),
            )
            s_rows = await s_cursor.fetchall()
            target_series_ids = {r["id"] for r in s_rows}
            target_series_ids.add(series_id)

            placeholders = ",".join("?" for _ in target_series_ids)
            query = f"""
                SELECT id, series_id, library_id, name, number, book_type, page_count, raw_json
                FROM books
                WHERE (series_id IN ({placeholders}) OR series_id LIKE ? OR series_id LIKE ?)
                  AND deleted = 0
                ORDER BY number ASC
            """
            params = list(target_series_ids) + [f"{series_id}-%", f"%-{series_id}"]
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Books Operations ----------------

    async def upsert_books_batch(self, books: List[Dict[str, Any]]) -> None:
        if not books:
            return
        chunk_size = 1000
        for i in range(0, len(books), chunk_size):
            chunk = books[i : i + chunk_size]
            for attempt in range(5):
                try:
                    async with self.get_db() as db:
                        await db.executemany(
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
                            [
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
                                    b.get("raw_json") if isinstance(b.get("raw_json"), str) else json.dumps(b.get("raw_json", {})),
                                )
                                for b in chunk
                            ],
                        )
                        await db.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 4:
                        await asyncio.sleep(0.1 * (2 ** attempt))
                    else:
                        raise
            await asyncio.sleep(0)

    async def update_book_page_count(self, book_id: int, page_count: int) -> None:
        for attempt in range(5):
            try:
                async with self.get_db() as db:
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
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    wait_time = 0.1 * (2 ** attempt)
                    logger.warning(f"Database locked in update_book_page_count, retrying in {wait_time:.2f}s (attempt {attempt + 1}/5)...")
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def get_book_by_id(
        self, book_id: int, include_deleted: bool = False, user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        clause = "WHERE b.id = ?" if include_deleted else "WHERE b.id = ? AND b.deleted = 0"
        async with self.get_db() as db:
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

    async def get_adjacent_book(
        self, book_id: int, direction: str = "next", user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Find the next or previous book in the same series.
        direction: 'next' or 'previous'
        """
        target = await self.get_book_by_id(book_id, include_deleted=True)
        if not target:
            return None

        series_id = target["series_id"]
        curr_num = target.get("number", 1.0)
        curr_id = target["id"]

        user_select = ""
        user_join = ""
        params: List[Any] = []

        if user_id is not None:
            user_select = ", rp.page as user_page, rp.completed as user_completed, rp.read_date as user_read_date"
            user_join = "LEFT JOIN read_progress rp ON rp.book_id = b.id AND rp.user_id = ?"
            params.append(user_id)

        params.extend([series_id, curr_num, curr_num, curr_id])

        if direction.lower() == "next":
            order_clause = "ORDER BY b.number ASC, b.id ASC"
            comp_clause = "(b.number > ? OR (b.number = ? AND b.id > ?))"
        else:
            order_clause = "ORDER BY b.number DESC, b.id DESC"
            comp_clause = "(b.number < ? OR (b.number = ? AND b.id < ?))"

        query = f"""
            SELECT b.*, s.name as series_name{user_select}
            FROM books b
            JOIN series s ON b.series_id = s.id
            {user_join}
            WHERE b.series_id = ? AND b.deleted = 0 AND {comp_clause}
            {order_clause}
            LIMIT 1
        """

        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def mark_book_deleted(self, book_id: int) -> None:
        async with self.get_db() as db:
            await db.execute("UPDATE books SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (book_id,))
            await db.execute("DELETE FROM book_pages WHERE book_id = ?", (book_id,))
            await db.commit()
        await self.cleanup_empty_series()

    async def mark_books_deleted(self, book_ids: List[int]) -> int:
        if not book_ids:
            return 0
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        for attempt in range(5):
            try:
                async with self.get_db() as db:
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
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    wait_time = 0.1 * (2 ** attempt)
                    logger.warning(f"Database locked in upsert_read_progress, retrying in {wait_time:.2f}s (attempt {attempt + 1}/5)...")
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def delete_read_progress(self, user_id: int, book_id: int) -> None:
        async with self.get_db() as db:
            await db.execute("DELETE FROM read_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
            await db.commit()

    async def delete_read_progress_batch(self, user_id: int, book_ids: List[int]) -> None:
        if not book_ids:
            return
        async with self.get_db() as db:
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
        chunk_size = 1000
        for i in range(0, len(records), chunk_size):
            chunk = records[i : i + chunk_size]
            for attempt in range(5):
                try:
                    async with self.get_db() as db:
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
                                for r in chunk
                            ],
                        )
                        await db.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 4:
                        await asyncio.sleep(0.1 * (2 ** attempt))
                    else:
                        raise
            await asyncio.sleep(0)

    async def get_book_read_progress(self, user_id: int, book_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM read_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_user_read_progress(self, user_id: int) -> Dict[int, Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM read_progress WHERE user_id = ?", (user_id,))
            rows = await cursor.fetchall()
            return {r["book_id"]: dict(r) for r in rows}

    # ---------------- User Operations ----------------

    async def upsert_user(
        self,
        user_id: int,
        username: str,
        token: Optional[str] = None,
        is_admin: bool = False,
        assigned_libraries: Optional[List[int]] = None,
    ) -> None:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE token = ?", (token,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_users(self) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users ORDER BY last_connected_at DESC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_users_with_tokens(self) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE token IS NOT NULL AND token != '' ORDER BY last_connected_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_user_progress_sync_time(self, user_id: int) -> None:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        for attempt in range(5):
            try:
                async with self.get_db() as db:
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
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    wait_time = 0.1 * (2 ** attempt)
                    logger.warning(f"Database locked in upsert_r2_progression, retrying in {wait_time:.2f}s (attempt {attempt + 1}/5)...")
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def delete_r2_progression(self, user_id: int, book_id: int) -> None:
        async with self.get_db() as db:
            await db.execute(
                "DELETE FROM r2_progression WHERE user_id = ? AND book_id = ?",
                (user_id, book_id),
            )
            await db.commit()

    async def delete_r2_progression_batch(self, user_id: int, book_ids: List[int]) -> None:
        if not book_ids:
            return
        async with self.get_db() as db:
            chunk_size = 500
            for i in range(0, len(book_ids), chunk_size):
                chunk = book_ids[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                await db.execute(
                    f"DELETE FROM r2_progression WHERE user_id = ? AND book_id IN ({placeholders})",
                    [user_id] + chunk,
                )
            await db.commit()

    # ---------------- API Key Operations ----------------

    async def create_api_key(
        self, user_id: int, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        key_id = str(uuid.uuid4())
        key_val = secrets.token_urlsafe(32)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with self.get_db() as db:
            await db.execute(
                """
                INSERT INTO api_keys (id, user_id, key, comment, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (key_id, user_id, key_val, comment),
            )
            await db.commit()
        return {
            "id": key_id,
            "key": key_val,
            "comment": comment,
            "created": now_str,
            "lastUsed": None,
        }

    async def get_api_keys(self, user_id: int) -> List[Dict[str, Any]]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "key": r["key"],
                    "comment": r["comment"],
                    "created": r["created_at"],
                    "lastUsed": r["last_used_at"],
                }
                for r in rows
            ]

    async def get_user_id_by_api_key(self, key: str) -> Optional[int]:
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id FROM api_keys WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row:
                user_id = row["user_id"]
                await db.execute(
                    "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE key = ?",
                    (key,),
                )
                await db.commit()
                return user_id
            return None

    async def delete_api_key(self, user_id: int, key_id: str) -> bool:
        async with self.get_db() as db:
            cursor = await db.execute(
                "DELETE FROM api_keys WHERE user_id = ? AND id = ?",
                (user_id, key_id),
            )
            await db.commit()
            return cursor.rowcount > 0

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
        sort: Optional[Union[str, List[str]]] = None,
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
        if user_id is not None:
            eff_read_date = "rp.read_date"
            is_completed = "(rp.completed = 1)"
            is_in_prog = "(rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL))"
            join_rp_clause = "LEFT JOIN read_progress rp ON (b.id = rp.book_id AND rp.user_id = ?)"
            rp_join_params = [user_id]
        else:
            eff_read_date = "COALESCE(rp.read_date, json_extract(b.raw_json, '$.dateFinished'), json_extract(b.raw_json, '$.cbxProgress.lastRead'), json_extract(b.raw_json, '$.epubProgress.lastRead'), json_extract(b.raw_json, '$.pdfProgress.lastRead'))"
            is_completed = "(rp.completed = 1 OR (rp.book_id IS NULL AND json_extract(b.raw_json, '$.readStatus') = 'READ'))"
            is_in_prog = "((rp.book_id IS NOT NULL AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) OR (rp.book_id IS NULL AND (json_extract(b.raw_json, '$.readStatus') IN ('READING', 'IN_PROGRESS') OR (json_extract(b.raw_json, '$.cbxProgress.percentage') > 0 AND json_extract(b.raw_json, '$.cbxProgress.percentage') < 99) OR (json_extract(b.raw_json, '$.epubProgress.percentage') > 0 AND json_extract(b.raw_json, '$.epubProgress.percentage') < 99) OR (json_extract(b.raw_json, '$.pdfProgress.percentage') > 0 AND json_extract(b.raw_json, '$.pdfProgress.percentage') < 99))))"
            join_rp_clause = "LEFT JOIN read_progress rp ON (1 = 0)"
            rp_join_params = []
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

        eff_release_date = "COALESCE(b.released, json_extract(b.raw_json, '$.metadata.released'), json_extract(b.raw_json, '$.released'), json_extract(b.raw_json, '$.metadata.releaseDate'), json_extract(b.raw_json, '$.releaseDate'), json_extract(b.raw_json, '$.metadata.publishedDate'))"

        # Build multi-column sort expressions
        sort_specs: List[str] = []
        if sort:
            if isinstance(sort, list):
                sort_specs.extend([str(s) for s in sort if s])
            elif isinstance(sort, str):
                sort_specs.append(sort)
        elif sort_by:
            sort_specs.append(f"{sort_by},{sort_dir}")

        allowed_sorts = {
            "number": "b.number",
            "numbersort": "b.number",
            "name": "b.name",
            "title": "b.name",
            "titlesort": "b.name",
            "series": "COALESCE(s.sort_title, s.name, b.series_id)",
            "seriestitle": "COALESCE(s.sort_title, s.name, b.series_id)",
            "seriessort": "COALESCE(s.sort_title, s.name, b.series_id)",
            "series.name": "COALESCE(s.sort_title, s.name, b.series_id)",
            "series.title": "COALESCE(s.sort_title, s.name, b.series_id)",
            "created": "b.created",
            "createddate": "b.created",
            "lastmodified": "b.last_modified",
            "lastmodifieddate": "b.last_modified",
            "filelastmodified": "b.last_modified",
            "release": eff_release_date,
            "releasedate": eff_release_date,
            "readdate": eff_read_date,
            "readprogress.readdate": eff_read_date,
            "readprogress": eff_read_date,
        }

        order_clauses: List[str] = []
        requires_read_date = False
        requires_release_date = False

        for spec in sort_specs:
            parts = [p.strip() for p in spec.split(",") if p.strip()]
            if not parts:
                continue

            direction = "ASC"
            if parts[-1].lower() in ("asc", "desc"):
                direction = "DESC" if parts[-1].lower() == "desc" else "ASC"
                fields = parts[:-1]
            else:
                fields = parts

            for field in fields:
                clean_field = field.lower().replace("metadata.", "").strip()
                col_expr = allowed_sorts.get(clean_field)
                if not col_expr:
                    col_expr = allowed_sorts.get(clean_field.replace("sort", ""))
                if not col_expr:
                    col_expr = "b.number"

                if col_expr == eff_read_date:
                    requires_read_date = True
                if col_expr == eff_release_date:
                    requires_release_date = True

                order_clauses.append(f"{col_expr} {direction}")

        if not order_clauses:
            order_clauses.append("b.number ASC")

        if requires_read_date and not read_status:
            # When sorting by read date without explicit read_status, only include books with read progress
            conditions.append(f"{eff_read_date} IS NOT NULL")

        if requires_release_date:
            # Ignore books without release data in Grimmory in the new releases list
            conditions.append(f"({eff_release_date} IS NOT NULL AND {eff_release_date} != '')")

        order_by_sql = ", ".join(order_clauses)
        where_clause = f"WHERE {' AND '.join(conditions)}"

        async with self.get_db() as db:
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
                    ORDER BY {order_by_sql}
                """
                cursor = await db.execute(query, rp_join_params + params)
            else:
                query = f"""
                    SELECT {select_cols} FROM books b
                    LEFT JOIN series s ON b.series_id = s.id
                    {join_rp_clause}
                    {where_clause}
                    ORDER BY {order_by_sql}
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

        if user_id is not None:
            join_rp_clause = "LEFT JOIN read_progress rp ON (b.id = rp.book_id AND rp.user_id = ?)"
            rp_params = [user_id]
        else:
            join_rp_clause = "LEFT JOIN read_progress rp ON (1 = 0)"
            rp_params = []

        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            query = f"""
                SELECT b.*, s.name as series_name,
                       rp.page as user_page, rp.completed as user_completed, rp.read_date as user_read_date
                FROM books b
                LEFT JOIN series s ON b.series_id = s.id
                {join_rp_clause}
                WHERE b.deleted = 0 {lib_filter}
                ORDER BY b.series_id, b.number ASC
            """
            cursor = await db.execute(query, rp_params + lib_params)
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
                        if isinstance(raw, str):
                            raw = json.loads(raw)
                    except Exception:
                        pass
                    if not isinstance(raw, dict):
                        raw = {}

                if user_id is not None:
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
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_books_for_page_calc(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM books WHERE deleted = 0 ORDER BY id ASC"
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Page Dimension / Detail Cache ----------------

    async def upsert_book_pages(self, book_id: int, pages: List[Dict[str, Any]]) -> None:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM book_pages WHERE book_id = ? ORDER BY page_number ASC",
                (book_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Sync & Stats State ----------------

    async def set_state(self, key: str, value: str) -> None:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
            cursor = await db.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_stats(self) -> Dict[str, Any]:
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        async with self.get_db() as db:
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
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM page_calc_jobs ORDER BY id DESC LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None
