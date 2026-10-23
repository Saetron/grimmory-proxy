import asyncio
from contextlib import asynccontextmanager
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid
import aiosqlite

from app.config import settings
from app.services.memory import release_memory

logger = logging.getLogger("grimmory_proxy.database")

KNOWN_AUTHOR_ROLES = {
    "writer", "penciller", "inker", "colorist", "letterer",
    "cover", "editor", "translator", "assistant",
}

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
    koreader_hash TEXT,
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
    role TEXT DEFAULT 'writer',
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
CREATE INDEX IF NOT EXISTS idx_series_slug ON series(slug);
CREATE INDEX IF NOT EXISTS idx_series_sort_title ON series(sort_title);
CREATE INDEX IF NOT EXISTS idx_books_series_number ON books(series_id, number);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


def _parse_author_filter(a: Any) -> Tuple[Optional[str], Optional[str]]:
    auth_name = None
    auth_role = None
    if isinstance(a, dict):
        auth_name = a.get("name")
        auth_role = a.get("role")
    elif isinstance(a, str):
        if "," in a:
            parts = [p.strip() for p in a.split(",")]
            if len(parts) == 2 and parts[1].lower() in KNOWN_AUTHOR_ROLES:
                auth_name = parts[0]
                auth_role = parts[1].lower()
            else:
                auth_name = a.strip()
        else:
            auth_name = a.strip()
    return auth_name, auth_role


def _build_book_author_clause(auth_name: str, auth_role: Optional[str]) -> Tuple[str, List[str]]:
    if auth_role:
        sql = """(
            EXISTS (
                SELECT 1 FROM json_each(b.raw_json, '$.metadata.authors')
                WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
                  AND (type != 'object' OR json_extract(value, '$.role') IS NULL OR LOWER(json_extract(value, '$.role')) = LOWER(?))
            )
            OR EXISTS (
                SELECT 1 FROM json_each(b.raw_json, '$.authors')
                WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
                  AND (type != 'object' OR json_extract(value, '$.role') IS NULL OR LOWER(json_extract(value, '$.role')) = LOWER(?))
            )
            OR (
                LOWER(COALESCE(json_extract(b.raw_json, '$.metadata.author.name'), json_extract(b.raw_json, '$.metadata.author'), json_extract(b.raw_json, '$.author.name'), json_extract(b.raw_json, '$.author'))) = LOWER(?)
                AND (
                    COALESCE(json_extract(b.raw_json, '$.metadata.author.role'), json_extract(b.raw_json, '$.author.role')) IS NULL
                    OR LOWER(COALESCE(json_extract(b.raw_json, '$.metadata.author.role'), json_extract(b.raw_json, '$.author.role'))) = LOWER(?)
                )
            )
        )"""
        return sql, [auth_name, auth_role, auth_name, auth_role, auth_name, auth_role]
    else:
        sql = """(
            EXISTS (
                SELECT 1 FROM json_each(b.raw_json, '$.metadata.authors')
                WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
            )
            OR EXISTS (
                SELECT 1 FROM json_each(b.raw_json, '$.authors')
                WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
            )
            OR LOWER(COALESCE(json_extract(b.raw_json, '$.metadata.author.name'), json_extract(b.raw_json, '$.metadata.author'), json_extract(b.raw_json, '$.author.name'), json_extract(b.raw_json, '$.author'))) = LOWER(?)
        )"""
        return sql, [auth_name, auth_name, auth_name]


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._pool: Optional[aiosqlite.Connection] = None
        self._write_lock: Optional[asyncio.Lock] = None

    @property
    def write_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._write_lock is None or getattr(self, "_lock_loop", None) != loop:
            self._write_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._write_lock

    @asynccontextmanager
    async def get_db(self):
        async with aiosqlite.connect(self.db_path, timeout=60.0) as db:
            await db.execute("PRAGMA busy_timeout = 60000")
            await db.execute("PRAGMA synchronous = NORMAL")
            await db.execute(f"PRAGMA cache_size = {settings.sqlite_cache_size}")
            await db.execute(f"PRAGMA mmap_size = {settings.sqlite_mmap_size}")
            await db.execute("PRAGMA temp_store = MEMORY")
            yield db

    @asynccontextmanager
    async def write_db(self):
        async with self.write_lock:
            async with self.get_db() as db:
                yield db

    async def _execute_write_with_retry(self, operation, op_name: str = "write") -> Any:
        for attempt in range(5):
            try:
                async with self.write_db() as db:
                    result = await operation(db)
                    await db.commit()
                    return result
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    wait_time = 0.1 * (2 ** attempt)
                    logger.warning(
                        f"Database locked in {op_name}, retrying in {wait_time:.2f}s (attempt {attempt + 1}/5)..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def checkpoint(self, mode: str = "PASSIVE") -> None:
        """
        Runs a WAL checkpoint to flush WAL frames to the database and reclaim memory/disk.
        Modes: 'PASSIVE', 'FULL', 'RESTART', 'TRUNCATE'.
        """
        try:
            async with self.write_db() as db:
                await db.execute(f"PRAGMA wal_checkpoint({mode})")
        except Exception as e:
            logger.debug(f"WAL checkpoint ({mode}) exception: {e}")

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
            await db.execute(f"PRAGMA cache_size = {settings.sqlite_cache_size}")
            await db.execute(f"PRAGMA mmap_size = {settings.sqlite_mmap_size}")
            await db.execute(f"PRAGMA wal_autocheckpoint = {settings.sqlite_wal_autocheckpoint}")
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
                await db.execute("ALTER TABLE authors ADD COLUMN role TEXT DEFAULT 'writer'")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE page_calc_jobs ADD COLUMN removed_books INTEGER DEFAULT 0")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE books ADD COLUMN koreader_hash TEXT")
            except Exception:
                pass
            try:
                await db.execute("CREATE INDEX IF NOT EXISTS idx_books_koreader_hash ON books(koreader_hash)")
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

            # Backfill series raw_json with aggregated authors, genres, tags, publisher if missing
            try:
                chk_cur = await db.execute("SELECT value FROM sync_state WHERE key = 'series_metadata_backfilled_v1'")
                chk_row = await chk_cur.fetchone()
                if not chk_row:
                    needs_cur = await db.execute("""
                        SELECT 1 FROM series
                        WHERE raw_json IS NULL
                           OR raw_json NOT LIKE '%"authors"%'
                           OR raw_json NOT LIKE '%"genres"%'
                        LIMIT 1
                    """)
                    needs_row = await needs_cur.fetchone()
                    if not needs_row:
                        await db.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('series_metadata_backfilled_v1', 'true')")
                    else:
                        cursor = await db.execute("""
                            SELECT s.id, s.raw_json, b.raw_json
                            FROM series s
                            JOIN books b ON b.series_id = s.id
                            WHERE b.deleted = 0 AND b.raw_json IS NOT NULL
                              AND (s.raw_json IS NULL OR s.raw_json NOT LIKE '%"authors"%' OR s.raw_json NOT LIKE '%"genres"%')
                        """)
                        rows = await cursor.fetchall()
                        if rows:
                            from app.services.mapper import (
                                extract_authors_data,
                                extract_genres,
                                extract_publisher,
                                extract_summary,
                                extract_tags,
                                parse_raw_json,
                            )

                            series_books: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}
                            for sid, s_raw_str, b_raw_str in rows:
                                if sid not in series_books:
                                    series_books[sid] = (s_raw_str, [])
                                b_data = parse_raw_json(b_raw_str)
                                if b_data:
                                    series_books[sid][1].append(b_data)

                            series_updates = []
                            backfill_authors = set()
                            for sid, (s_raw_str, b_list) in series_books.items():
                                s_raw = parse_raw_json(s_raw_str)

                                needs_update = not s_raw.get("authors") or not s_raw.get("publisher") or not s_raw.get("genres") or not s_raw.get("tags")
                                if needs_update:
                                    authors_set = set()
                                    authors_list = list(s_raw.get("authors", []))
                                    for a in authors_list:
                                        if isinstance(a, dict) and "name" in a:
                                            authors_set.add((a["name"].lower(), a.get("role", "writer").lower()))
                                    genres_set = {g.lower() for g in s_raw.get("genres", [])}
                                    genres_list = list(s_raw.get("genres", []))
                                    tags_set = {t.lower() for t in s_raw.get("tags", [])}
                                    tags_list = list(s_raw.get("tags", []))
                                    pub = s_raw.get("publisher", "")
                                    summary = s_raw.get("summary", "")
                                    release_date = s_raw.get("releaseDate")

                                    for b_data in b_list:
                                        for a in extract_authors_data(b_data):
                                            backfill_authors.add((a["name"], a["role"]))
                                            if (a["name"].lower(), a["role"].lower()) not in authors_set:
                                                authors_set.add((a["name"].lower(), a["role"].lower()))
                                                authors_list.append(a)
                                        for g in extract_genres(b_data):
                                            if g.lower() not in genres_set:
                                                genres_set.add(g.lower())
                                                genres_list.append(g)
                                        for t in extract_tags(b_data):
                                            if t.lower() not in tags_set:
                                                tags_set.add(t.lower())
                                                tags_list.append(t)
                                        if not pub:
                                            pub = extract_publisher(b_data)
                                        if not summary:
                                            summary = extract_summary(b_data)
                                        b_rel = b_data.get("released") or (b_data.get("metadata", {}).get("released") if isinstance(b_data.get("metadata"), dict) else None)
                                        if b_rel and (not release_date or str(b_rel) < str(release_date)):
                                            release_date = str(b_rel)

                                    s_raw["authors"] = authors_list
                                    s_raw["genres"] = genres_list
                                    s_raw["tags"] = tags_list
                                    if pub:
                                        s_raw["publisher"] = pub
                                    if summary:
                                        s_raw["summary"] = summary
                                    if release_date:
                                        s_raw["releaseDate"] = release_date

                                    series_updates.append((json.dumps(s_raw), sid))

                            if series_updates:
                                await db.executemany("UPDATE series SET raw_json = ? WHERE id = ?", series_updates)
                            if backfill_authors:
                                await db.executemany(
                                    "INSERT INTO authors (name, role, count) VALUES (?, ?, 1) ON CONFLICT(name) DO UPDATE SET count = count + 1, role = COALESCE(excluded.role, authors.role)",
                                    list(backfill_authors),
                                )
                            await db.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('series_metadata_backfilled_v1', 'true')")
            except Exception as e:
                logger.debug(f"Series metadata backfill check: {e}")

            # Prune any invalid read progress records where non-admin users with restricted library access
            # have records for books belonging to libraries they are not assigned to
            try:
                await db.execute("""
                    DELETE FROM read_progress
                    WHERE rowid IN (
                        SELECT rp.rowid
                        FROM read_progress rp
                        JOIN users u ON rp.user_id = u.id
                        JOIN books b ON rp.book_id = b.id
                        WHERE u.is_admin = 0
                          AND u.assigned_libraries IS NOT NULL
                          AND u.assigned_libraries != ''
                          AND u.assigned_libraries != '[]'
                          AND b.library_id NOT IN (
                              SELECT value FROM json_each(u.assigned_libraries)
                          )
                    )
                """)
            except Exception as e:
                logger.debug(f"Unauthorized read progress cleanup migration: {e}")

            await db.commit()
            try:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
        release_memory()
        logger.info(f"Database initialized at {self.db_path} (WAL mode enabled, busy_timeout=60s)")

    def get_connection(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.db_path, timeout=60.0)

    # ---------------- Library Operations ----------------

    async def upsert_libraries(self, libraries: List[Dict[str, Any]]) -> None:
        if not libraries:
            return
        rows = []
        for lib in libraries:
            paths = lib.get("paths", [])
            root = paths[0].get("path", "") if paths else ""
            rows.append((
                lib["id"],
                lib.get("name", f"Library {lib['id']}"),
                root,
                lib.get("organizationMode", "BOOK_PER_FILE"),
                json.dumps(lib),
            ))
        async with self.write_db() as db:
            await db.executemany(
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
                rows,
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
            params = [
                (
                    s["id"],
                    s["library_id"],
                    s["name"],
                    s["slug"],
                    s.get("sort_title", s["name"]),
                    s.get("books_count", 0),
                    s.get("created"),
                    s.get("last_modified"),
                    s.get("raw_json") if isinstance(s.get("raw_json"), str) else json.dumps(s.get("raw_json", {})),
                )
                for s in chunk
            ]

            async def _op(db, rows=params):
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
                    rows,
                )

            await self._execute_write_with_retry(_op, "upsert_series_batch")
            await asyncio.sleep(0)

    async def get_series_list(
        self,
        library_id: Optional[int] = None,
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        genres: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        publishers: Optional[List[str]] = None,
        authors: Optional[List[Any]] = None,
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

        if genres:
            genre_clauses = []
            for g in genres:
                genre_clauses.append("EXISTS (SELECT 1 FROM json_each(raw_json, '$.genres') WHERE LOWER(value) = LOWER(?))")
                params.append(g)
            if genre_clauses:
                conditions.append(f"({' OR '.join(genre_clauses)})")

        if tags:
            tag_clauses = []
            for t in tags:
                tag_clauses.append("EXISTS (SELECT 1 FROM json_each(raw_json, '$.tags') WHERE LOWER(value) = LOWER(?))")
                params.append(t)
            if tag_clauses:
                conditions.append(f"({' OR '.join(tag_clauses)})")

        if publishers:
            pub_clauses = []
            for p in publishers:
                pub_clauses.append("LOWER(json_extract(raw_json, '$.publisher')) = LOWER(?)")
                params.append(p)
            if pub_clauses:
                conditions.append(f"({' OR '.join(pub_clauses)})")

        if authors:
            auth_clauses = []
            for a in authors:
                auth_name, auth_role = _parse_author_filter(a)
                if not auth_name:
                    continue

                book_auth_sql, book_params = _build_book_author_clause(auth_name, auth_role)
                if auth_role:
                    series_auth_sql = f"""(
                        EXISTS (
                            SELECT 1 FROM json_each(raw_json, '$.authors')
                            WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
                              AND (type != 'object' OR json_extract(value, '$.role') IS NULL OR LOWER(json_extract(value, '$.role')) = LOWER(?))
                        )
                        OR series.id IN (
                            SELECT b.series_id FROM books b
                            WHERE b.deleted = 0
                              AND b.library_id = series.library_id
                              AND {book_auth_sql}
                        )
                    )"""
                    auth_clauses.append(series_auth_sql)
                    params.extend([auth_name, auth_role] + book_params)
                else:
                    series_auth_sql = f"""(
                        EXISTS (
                            SELECT 1 FROM json_each(raw_json, '$.authors')
                            WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)
                        )
                        OR series.id IN (
                            SELECT b.series_id FROM books b
                            WHERE b.deleted = 0
                              AND b.library_id = series.library_id
                              AND {book_auth_sql}
                        )
                    )"""
                    auth_clauses.append(series_auth_sql)
                    params.extend([auth_name] + book_params)

            if auth_clauses:
                conditions.append(f"({' OR '.join(auth_clauses)})")

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

    async def get_series_alphabetical_groups(
        self, library_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        where_clause = ""
        params: List[Any] = []
        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            where_clause = f"WHERE library_id IN ({ph})"
            params.extend(library_ids)

        query = f"""
            SELECT UPPER(SUBSTR(name, 1, 1)) AS first_char, COUNT(*) AS cnt
            FROM series
            {where_clause}
            GROUP BY first_char
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        groups: Dict[str, int] = {}
        for ch, cnt in rows:
            first_char = ch if (ch and ch.isalpha()) else "#"
            groups[first_char] = groups.get(first_char, 0) + cnt
        return [{"group": k, "count": v} for k, v in sorted(groups.items())]

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
        async with self.write_db() as db:
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
        # Strip fav-{uid}- prefix if present to locate canonical series
        if identifier.startswith("fav-"):
            parts = identifier.split("-", 2)
            if len(parts) >= 3 and parts[1].isdigit():
                identifier = parts[2]

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
            if not row and identifier.startswith("series-"):
                slug_part = identifier[len("series-"):]
                cursor = await db.execute("SELECT id FROM series WHERE slug = ? OR slug LIKE ? OR id LIKE ? OR name = ? COLLATE NOCASE ORDER BY id ASC LIMIT 1", (slug_part, f"{slug_part}-%", f"%-{slug_part}%", slug_part))
                row = await cursor.fetchone()
            if row:
                actual_id = row["id"]
                return await self.get_series_by_id(actual_id, user_id=user_id)
            return None

    async def count_books_in_series(self, series_id: str, book_ids: Optional[List[int]] = None) -> int:
        conditions = ["b.deleted = 0", "(b.series_id = ? OR b.series_id LIKE ? OR b.series_id LIKE ?)"]
        params: List[Any] = [series_id, f"{series_id}-%", f"%-{series_id}"]
        if book_ids is not None:
            if not book_ids:
                return 0
            placeholders = ",".join("?" for _ in book_ids)
            conditions.append(f"b.id IN ({placeholders})")
            params.extend(book_ids)
        query = f"SELECT COUNT(*) FROM books b WHERE {' AND '.join(conditions)}"
        async with self.get_db() as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_series_for_books(
        self,
        book_ids: List[int],
        library_ids: Optional[List[int]] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not book_ids:
            return []
        placeholders = ",".join("?" for _ in book_ids)
        conditions = [f"b.id IN ({placeholders})", "b.deleted = 0"]
        params: List[Any] = list(book_ids)
        if library_ids is not None:
            if not library_ids:
                return []
            lib_placeholders = ",".join("?" for _ in library_ids)
            conditions.append(f"b.library_id IN ({lib_placeholders})")
            params.extend(library_ids)

        query = f"""
            SELECT DISTINCT b.series_id
            FROM books b
            WHERE {' AND '.join(conditions)}
        """
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            series_ids = [r[0] for r in rows if r[0]]

            if not series_ids:
                return []

            s_placeholders = ",".join("?" for _ in series_ids)
            if user_id is not None:
                s_query = f"""
                    SELECT s.*,
                           (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 1) as books_read_count,
                           (SELECT COUNT(*) FROM books b JOIN read_progress rp ON b.id = rp.book_id WHERE b.series_id = s.id AND b.deleted = 0 AND rp.user_id = ? AND rp.completed = 0 AND (rp.page > 0 OR rp.read_date IS NOT NULL)) as books_in_progress_count
                    FROM series s WHERE s.id IN ({s_placeholders})
                """
                s_cursor = await db.execute(s_query, [user_id, user_id] + series_ids)
            else:
                s_cursor = await db.execute(f"SELECT * FROM series WHERE id IN ({s_placeholders})", series_ids)
            s_rows = await s_cursor.fetchall()
            direct_map = {r["id"]: dict(r) for r in s_rows}

        series_list = []
        seen_ids = set()
        for sid in series_ids:
            s_row = direct_map.get(sid)
            if not s_row:
                s_row = await self.find_series_by_id_or_slug(sid, user_id=user_id)
            if s_row and s_row["id"] not in seen_ids:
                seen_ids.add(s_row["id"])
                series_list.append(s_row)
        return series_list

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
        from app.services.mapper import extract_authors_data, parse_raw_json

        chunk_size = 1000
        for i in range(0, len(books), chunk_size):
            chunk = books[i : i + chunk_size]
            book_rows = []
            author_records = set()

            for b in chunk:
                b_raw = b.get("raw_json")
                raw_str = b_raw if isinstance(b_raw, str) else json.dumps(b_raw or {})
                book_rows.append((
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
                    raw_str,
                ))
                b_data = parse_raw_json(b_raw)
                for a in extract_authors_data(b_data):
                    if a.get("name"):
                        author_records.add((a["name"], a.get("role", "writer")))

            async def _op(db, b_rows=book_rows, a_rows=list(author_records)):
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
                    b_rows,
                )
                if a_rows:
                    try:
                        await db.executemany(
                            """
                            INSERT INTO authors (name, role, count) VALUES (?, ?, 1)
                            ON CONFLICT(name) DO UPDATE SET count = count + 1, role = COALESCE(excluded.role, authors.role)
                            """,
                            a_rows,
                        )
                    except Exception as auth_err:
                        logger.debug(f"Authors table sync in upsert_books_batch: {auth_err}")

            await self._execute_write_with_retry(_op, "upsert_books_batch")
            await asyncio.sleep(0)

    async def update_book_page_count(self, book_id: int, page_count: int) -> None:
        async def _op(db):
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

        await self._execute_write_with_retry(_op, "update_book_page_count")

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
        async with self.write_db() as db:
            await db.execute("UPDATE books SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (book_id,))
            await db.execute("DELETE FROM book_pages WHERE book_id = ?", (book_id,))
            await db.commit()
        await self.cleanup_empty_series()

    async def mark_books_deleted(self, book_ids: List[int]) -> int:
        if not book_ids:
            return 0
        async with self.write_db() as db:
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
        async def _op(db):
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

        await self._execute_write_with_retry(_op, "upsert_read_progress")

    async def delete_read_progress(self, user_id: int, book_id: int) -> None:
        async with self.write_db() as db:
            await db.execute("DELETE FROM read_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
            await db.commit()

    async def delete_read_progress_batch(self, user_id: int, book_ids: List[int]) -> None:
        if not book_ids:
            return
        async with self.write_db() as db:
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
            params = [
                (
                    r["user_id"],
                    r["book_id"],
                    r.get("page", 1),
                    1 if r.get("completed") else 0,
                    r["read_date"],
                )
                for r in chunk
            ]

            async def _op(db, rows=params):
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
                    rows,
                )

            await self._execute_write_with_retry(_op, "upsert_read_progress_batch")
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

    async def get_book_ids_for_libraries(self, library_ids: List[int]) -> Set[int]:
        if not library_ids:
            return set()
        async with self.get_db() as db:
            placeholders = ",".join("?" * len(library_ids))
            cursor = await db.execute(
                f"SELECT id FROM books WHERE library_id IN ({placeholders}) AND deleted = 0",
                library_ids,
            )
            rows = await cursor.fetchall()
            return {r[0] for r in rows}

    async def cleanup_unauthorized_read_progress(self, user_id: Optional[int] = None) -> int:
        """
        Deletes any read_progress records for non-admin users where the book
        belongs to a library that the user does not have access to.
        """
        async with self.write_db() as db:
            user_clause = "AND u.id = ?" if user_id is not None else ""
            params = [user_id] if user_id is not None else []
            cursor = await db.execute(
                f"""
                DELETE FROM read_progress
                WHERE rowid IN (
                    SELECT rp.rowid
                    FROM read_progress rp
                    JOIN users u ON rp.user_id = u.id
                    JOIN books b ON rp.book_id = b.id
                    WHERE u.is_admin = 0
                      AND u.assigned_libraries IS NOT NULL
                      AND u.assigned_libraries != ''
                      AND u.assigned_libraries != '[]'
                      {user_clause}
                      AND b.library_id NOT IN (
                          SELECT value FROM json_each(u.assigned_libraries)
                      )
                )
                """,
                params,
            )
            deleted = cursor.rowcount
            await db.commit()
            return deleted

    # ---------------- User Operations ----------------

    async def upsert_user(
        self,
        user_id: int,
        username: str,
        token: Optional[str] = None,
        is_admin: bool = False,
        assigned_libraries: Optional[List[int]] = None,
    ) -> None:
        assigned_json = json.dumps(assigned_libraries or [])

        async def _op(db):
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
                    assigned_json,
                ),
            )

        await self._execute_write_with_retry(_op, "upsert_user")

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
        async with self.write_db() as db:
            await db.execute(
                "UPDATE users SET last_sync_progress_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            await db.commit()

    async def touch_user_last_connected(self, user_id: int) -> None:
        async with self.write_db() as db:
            await db.execute(
                "UPDATE users SET last_connected_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            await db.commit()

    async def purge_user(self, user_id: int) -> Dict[str, int]:
        """
        Permanently purge a user and all their cached progress, settings, and tokens from SQLite.
        Returns a dictionary of deleted row counts.
        """
        async with self.write_db() as db:
            cursor = await db.execute("DELETE FROM read_progress WHERE user_id = ?", (user_id,))
            progress_deleted = cursor.rowcount

            cursor = await db.execute("DELETE FROM r2_progression WHERE user_id = ?", (user_id,))
            r2_deleted = cursor.rowcount

            cursor = await db.execute("DELETE FROM client_settings WHERE user_id = ?", (user_id,))
            settings_deleted = cursor.rowcount

            cursor = await db.execute("DELETE FROM api_keys WHERE user_id = ?", (user_id,))
            keys_deleted = cursor.rowcount

            cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            user_deleted = cursor.rowcount

            await db.commit()

            return {
                "user_deleted": user_deleted,
                "progress_deleted": progress_deleted,
                "r2_deleted": r2_deleted,
                "settings_deleted": settings_deleted,
                "keys_deleted": keys_deleted,
            }

    async def purge_user_read_progress(self, user_id: int) -> Dict[str, int]:
        """
        Purges cached read progress and R2 progression for a user,
        keeping their user record, credentials, and settings intact.
        """
        async with self.write_db() as db:
            c1 = await db.execute("DELETE FROM read_progress WHERE user_id = ?", (user_id,))
            p_deleted = c1.rowcount
            c2 = await db.execute("DELETE FROM r2_progression WHERE user_id = ?", (user_id,))
            r_deleted = c2.rowcount
            await db.commit()
            return {"progress_deleted": p_deleted, "r2_deleted": r_deleted}

    async def purge_all_read_progress(self) -> Dict[str, int]:
        """
        Purges all read progress and R2 progression records from SQLite for all users.
        """
        async with self.write_db() as db:
            c1 = await db.execute("DELETE FROM read_progress")
            p_deleted = c1.rowcount
            c2 = await db.execute("DELETE FROM r2_progression")
            r_deleted = c2.rowcount
            await db.commit()
            return {"progress_deleted": p_deleted, "r2_deleted": r_deleted}

    async def get_total_read_progress_count(self) -> int:
        """
        Returns the real count of distinct read progress records currently in SQLite.
        """
        async with self.get_db() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM read_progress")
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def find_book_for_koreader(self, document_id_or_hash: str) -> Optional[Dict[str, Any]]:
        """
        Locates a book by KOReader hash, book ID, or primary file hash.
        """
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            if document_id_or_hash.isdigit():
                cur = await db.execute("SELECT * FROM books WHERE id = ? AND deleted = 0", (int(document_id_or_hash),))
                row = await cur.fetchone()
                if row:
                    return dict(row)

            try:
                cur = await db.execute("SELECT * FROM books WHERE koreader_hash = ? AND deleted = 0", (document_id_or_hash,))
                row = await cur.fetchone()
                if row:
                    return dict(row)
            except Exception:
                pass

            cur = await db.execute(
                """
                SELECT * FROM books
                WHERE deleted = 0
                  AND (
                    json_extract(raw_json, '$.hash') = ?
                    OR json_extract(raw_json, '$.primaryFile.hash') = ?
                    OR json_extract(raw_json, '$.primaryFile.md5') = ?
                  )
                LIMIT 1
                """,
                (document_id_or_hash, document_id_or_hash, document_id_or_hash),
            )
            row = await cur.fetchone()
            if row:
                return dict(row)

            return None

    async def get_users_detailed(self) -> List[Dict[str, Any]]:
        """
        Retrieves all users enriched with library access names, progress record counts,
        completion counts, and online status.
        """
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row

            lib_cursor = await db.execute("SELECT id, name FROM libraries")
            lib_rows = await lib_cursor.fetchall()
            lib_map = {r["id"]: r["name"] for r in lib_rows}

            cursor = await db.execute(
                """
                SELECT 
                    u.id,
                    u.username,
                    u.is_admin,
                    u.assigned_libraries,
                    u.last_connected_at,
                    u.last_sync_progress_at,
                    u.updated_at,
                    (CASE WHEN u.token IS NOT NULL AND u.token != '' THEN 1 ELSE 0 END) as has_token,
                    (SELECT COUNT(*) FROM read_progress rp WHERE rp.user_id = u.id) as progress_count,
                    (SELECT COUNT(*) FROM read_progress rp WHERE rp.user_id = u.id AND rp.completed = 1) as completed_count
                FROM users u
                ORDER BY u.last_connected_at DESC
                """
            )
            rows = await cursor.fetchall()
            result = []
            now_utc = datetime.now(timezone.utc)

            for r in rows:
                d = dict(r)
                assigned = []
                try:
                    assigned = json.loads(d.get("assigned_libraries") or "[]")
                except Exception:
                    assigned = []
                d["assigned_libraries"] = assigned

                # Resolve human-readable library permissions
                if d["is_admin"] or not assigned:
                    d["libraries_display"] = "All Libraries"
                else:
                    names = [lib_map[lid] for lid in assigned if lid in lib_map]
                    d["libraries_display"] = ", ".join(names) if names else f"{len(assigned)} Libraries"

                # Check if connected recently (within last 15 minutes)
                is_online = False
                last_conn = d.get("last_connected_at")
                if last_conn:
                    try:
                        clean_ts = str(last_conn).replace("Z", "+00:00")
                        dt = datetime.fromisoformat(clean_ts)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        diff_sec = (now_utc - dt).total_seconds()
                        if 0 <= diff_sec <= 900:
                            is_online = True
                    except Exception:
                        pass
                d["is_online"] = is_online
                result.append(d)
            return result

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
        async with self.write_db() as db:
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
        async with self.write_db() as db:
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
        async def _op(db):
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

        await self._execute_write_with_retry(_op, "upsert_r2_progression")

    async def delete_r2_progression(self, user_id: int, book_id: int) -> None:
        async with self.write_db() as db:
            await db.execute(
                "DELETE FROM r2_progression WHERE user_id = ? AND book_id = ?",
                (user_id, book_id),
            )
            await db.commit()

    async def delete_r2_progression_batch(self, user_id: int, book_ids: List[int]) -> None:
        if not book_ids:
            return
        async with self.write_db() as db:
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
        async with self.write_db() as db:
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
        async with self.write_db() as db:
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
        async with self.write_db() as db:
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
        book_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        read_status: Optional[List[str]] = None,
        genres: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        publishers: Optional[List[str]] = None,
        authors: Optional[List[Any]] = None,
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

        if book_ids is not None:
            if len(book_ids) == 0:
                return [], 0
            placeholders = ",".join("?" for _ in book_ids)
            conditions.append(f"b.id IN ({placeholders})")
            params.extend(book_ids)

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

        if tags:
            tag_clauses = []
            for t in tags:
                tag_clauses.append("(EXISTS (SELECT 1 FROM json_each(b.raw_json, '$.metadata.tags') WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)) OR EXISTS (SELECT 1 FROM json_each(b.raw_json, '$.tags') WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)))")
                params.extend([t, t])
            if tag_clauses:
                conditions.append(f"({' OR '.join(tag_clauses)})")

        if authors:
            auth_clauses = []
            for a in authors:
                auth_name, auth_role = _parse_author_filter(a)
                if not auth_name:
                    continue
                book_auth_sql, book_params = _build_book_author_clause(auth_name, auth_role)
                auth_clauses.append(book_auth_sql)
                params.extend(book_params)

            if auth_clauses:
                conditions.append(f"({' OR '.join(auth_clauses)})")

        if genres:
            genre_clauses = []
            for g in genres:
                genre_clauses.append("(EXISTS (SELECT 1 FROM json_each(b.raw_json, '$.metadata.genres') WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)) OR EXISTS (SELECT 1 FROM json_each(b.raw_json, '$.genres') WHERE LOWER(CASE WHEN type = 'object' THEN json_extract(value, '$.name') ELSE value END) = LOWER(?)))")
                params.extend([g, g])
            if genre_clauses:
                conditions.append(f"({' OR '.join(genre_clauses)})")

        if publishers:
            pub_clauses = []
            for p in publishers:
                pub_clauses.append("LOWER(COALESCE(json_extract(b.raw_json, '$.metadata.publisher.name'), json_extract(b.raw_json, '$.metadata.publisher'), json_extract(b.raw_json, '$.publisher.name'), json_extract(b.raw_json, '$.publisher'))) = LOWER(?)")
                params.append(p)
            if pub_clauses:
                conditions.append(f"({' OR '.join(pub_clauses)})")

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

        active_series_clause = ""
        active_series_params: List[Any] = []
        if user_id is not None:
            join_rp_clause = "LEFT JOIN read_progress rp ON (b.id = rp.book_id AND rp.user_id = ?)"
            rp_params = [user_id]
            # Limit candidate series to those where the user has active progress or finished books
            active_series_clause = """
                AND b.series_id IN (
                    SELECT DISTINCT b2.series_id
                    FROM read_progress rp2
                    JOIN books b2 ON rp2.book_id = b2.id
                    WHERE rp2.user_id = ?
                )
            """
            active_series_params = [user_id]
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
                WHERE b.deleted = 0 {active_series_clause} {lib_filter}
                ORDER BY b.series_id, b.number ASC
            """
            cursor = await db.execute(query, rp_params + active_series_params + lib_params)
            rows = await cursor.fetchall()

        series_books: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            book_dict = dict(r)
            series_books.setdefault(book_dict["series_id"], []).append(book_dict)

        ondeck_candidates = []
        from app.services.mapper import parse_raw_json

        for s_id, books in series_books.items():
            books.sort(key=lambda x: x.get("number", 1.0))

            in_progress_books = []
            completed_numbers = []
            unread_books = []
            latest_activity_date = None

            for b in books:
                if user_id is not None:
                    is_comp = bool(b.get("user_completed"))
                    is_prog = not is_comp and ((b.get("user_page") or 0) > 0 or b.get("user_read_date") is not None)
                    r_date = b.get("user_read_date")
                else:
                    raw = parse_raw_json(b.get("raw_json"))
                    is_comp = raw.get("readStatus") == "READ" or raw.get("cbxProgress", {}).get("percentage", 0) >= 95.0 or raw.get("epubProgress", {}).get("percentage", 0) >= 95.0 or raw.get("pdfProgress", {}).get("percentage", 0) >= 95.0
                    cbx_perc = raw.get("cbxProgress", {}).get("percentage", 0)
                    epub_perc = raw.get("epubProgress", {}).get("percentage", 0)
                    pdf_perc = raw.get("pdfProgress", {}).get("percentage", 0)
                    is_prog = not is_comp and (raw.get("readStatus") in ("READING", "IN_PROGRESS") or 0 < cbx_perc < 95.0 or 0 < epub_perc < 95.0 or 0 < pdf_perc < 95.0)
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
        query = """
            SELECT id, series_id, library_id, name, number, book_type, file_path, file_size_kb, page_count, deleted, released, created, last_modified
            FROM books
            WHERE deleted = 0
              AND (
                page_count IS NULL
                OR page_count <= 0
                OR NOT EXISTS (SELECT 1 FROM book_pages WHERE book_pages.book_id = books.id)
              )
            ORDER BY id ASC
        """
        if limit:
            query += f" LIMIT {limit}"
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_books_for_page_calc(self) -> List[Dict[str, Any]]:
        query = """
            SELECT id, series_id, library_id, name, number, book_type, file_path, file_size_kb, page_count, deleted, released, created, last_modified
            FROM books
            WHERE deleted = 0
            ORDER BY id ASC
        """
        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ---------------- Page Dimension / Detail Cache ----------------

    async def upsert_book_pages(self, book_id: int, pages: List[Dict[str, Any]]) -> None:
        rows = [
            (
                book_id,
                p["number"],
                p.get("fileName", f"page-{p['number']}"),
                p.get("mediaType", "image/jpeg"),
                p.get("width", 0),
                p.get("height", 0),
                p.get("sizeBytes", 0),
            )
            for p in pages
        ]
        async with self.write_db() as db:
            await db.execute("DELETE FROM book_pages WHERE book_id = ?", (book_id,))
            if rows:
                await db.executemany(
                    """
                    INSERT INTO book_pages (book_id, page_number, file_name, media_type, width, height, size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
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
        async with self.write_db() as db:
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

            books_row = await (await db.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN b.page_count > 0 AND EXISTS (SELECT 1 FROM book_pages bp WHERE bp.book_id = b.id) THEN 1 ELSE 0 END) as with_pages
                FROM books b
                WHERE b.deleted = 0
                """
            )).fetchone()
            books_count = books_row["total"] if books_row else 0
            books_with_pages = books_row["with_pages"] or 0 if books_row else 0
            books_missing_pages = max(0, books_count - books_with_pages)

            users_count = (await (await db.execute("SELECT COUNT(*) as count FROM users")).fetchone())["count"]

            # Formats breakdown
            type_cursor = await db.execute("SELECT book_type, COUNT(*) as count FROM books WHERE deleted = 0 GROUP BY book_type")
            type_rows = await type_cursor.fetchall()
            formats_breakdown = {r["book_type"] or "UNKNOWN": r["count"] for r in type_rows}

            state_cursor = await db.execute("SELECT key, value FROM sync_state WHERE key IN ('last_sync_time', 'sync_status')")
            state_rows = await state_cursor.fetchall()
            state_map = {r["key"]: r["value"] for r in state_rows}
            last_sync = state_map.get("last_sync_time")
            sync_status = state_map.get("sync_status") or "idle"

            # Check DB and WAL file sizes
            db_size_bytes = 0
            wal_size_bytes = 0
            db_path_obj = Path(self.db_path)
            if db_path_obj.exists():
                try:
                    db_size_bytes = db_path_obj.stat().st_size
                except OSError:
                    pass
            wal_path_obj = Path(f"{self.db_path}-wal")
            if wal_path_obj.exists():
                try:
                    wal_size_bytes = wal_path_obj.stat().st_size
                except OSError:
                    pass

            return {
                "libraries_count": lib_count,
                "series_count": series_count,
                "books_count": books_count,
                "books_with_pages": books_with_pages,
                "books_missing_pages": books_missing_pages,
                "users_count": users_count,
                "formats_breakdown": formats_breakdown,
                "last_sync_time": last_sync,
                "sync_status": sync_status,
                "database_size_bytes": db_size_bytes,
                "wal_size_bytes": wal_size_bytes,
                "total_storage_bytes": db_size_bytes + wal_size_bytes,
            }

    # ---------------- Page Calculation Job Management ----------------

    async def create_page_calc_job(self, total_books: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self.write_db() as db:
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
        async with self.write_db() as db:
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

    async def cleanup_stale_page_calc_jobs(self) -> None:
        async with self.write_db() as db:
            await db.execute(
                "UPDATE page_calc_jobs SET status = 'stopped', current_book = 'Stopped (server restarted)' WHERE status = 'running'"
            )
            await db.commit()

    # ---------------- Metadata Queries (Genres, Publishers, Tags, Authors) ----------------

    async def get_all_genres(
        self,
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
    ) -> List[str]:
        clauses_s = []
        clauses_b = ["b.deleted = 0"]
        params_s: List[Any] = []
        params_b: List[Any] = []

        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            clauses_s.append(f"s.library_id IN ({ph})")
            params_s.extend(library_ids)
            clauses_b.append(f"b.library_id IN ({ph})")
            params_b.extend(library_ids)

        where_s = f"WHERE {' AND '.join(clauses_s)}" if clauses_s else ""
        where_b = f"WHERE {' AND '.join(clauses_b)}"

        search_clause = ""
        search_params: List[Any] = []
        if search:
            search_clause = "AND genre LIKE ?"
            search_params.append(f"%{search}%")

        query = f"""
            SELECT DISTINCT genre FROM (
                SELECT CASE WHEN g.type = 'object' THEN json_extract(g.value, '$.name') ELSE g.value END as genre
                FROM series s, json_each(s.raw_json, '$.genres') as g
                {where_s}
                UNION
                SELECT CASE WHEN g.type = 'object' THEN json_extract(g.value, '$.name') ELSE g.value END as genre
                FROM books b, json_each(b.raw_json, '$.metadata.genres') as g
                {where_b}
                UNION
                SELECT CASE WHEN g.type = 'object' THEN json_extract(g.value, '$.name') ELSE g.value END as genre
                FROM books b, json_each(b.raw_json, '$.genres') as g
                {where_b}
            )
            WHERE genre IS NOT NULL AND TRIM(genre) != '' {search_clause}
            ORDER BY genre COLLATE NOCASE ASC
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params_s + params_b + params_b + search_params)
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    async def get_all_publishers(
        self,
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
    ) -> List[str]:
        clauses_s = []
        clauses_b = ["b.deleted = 0"]
        params_s: List[Any] = []
        params_b: List[Any] = []

        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            clauses_s.append(f"s.library_id IN ({ph})")
            params_s.extend(library_ids)
            clauses_b.append(f"b.library_id IN ({ph})")
            params_b.extend(library_ids)

        where_s = f"WHERE {' AND '.join(clauses_s)}" if clauses_s else ""
        where_b = f"WHERE {' AND '.join(clauses_b)}"

        search_clause = ""
        search_params: List[Any] = []
        if search:
            search_clause = "AND pub LIKE ?"
            search_params.append(f"%{search}%")

        query = f"""
            SELECT DISTINCT pub FROM (
                SELECT json_extract(s.raw_json, '$.publisher') as pub
                FROM series s
                {where_s}
                UNION
                SELECT COALESCE(
                    json_extract(b.raw_json, '$.metadata.publisher.name'),
                    json_extract(b.raw_json, '$.metadata.publisher'),
                    json_extract(b.raw_json, '$.publisher.name'),
                    json_extract(b.raw_json, '$.publisher')
                ) as pub
                FROM books b
                {where_b}
            )
            WHERE pub IS NOT NULL AND TRIM(pub) != '' {search_clause}
            ORDER BY pub COLLATE NOCASE ASC
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params_s + params_b + search_params)
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    async def get_all_tags(
        self,
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
    ) -> List[str]:
        clauses_s = []
        clauses_b = ["b.deleted = 0"]
        params_s: List[Any] = []
        params_b: List[Any] = []

        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            clauses_s.append(f"s.library_id IN ({ph})")
            params_s.extend(library_ids)
            clauses_b.append(f"b.library_id IN ({ph})")
            params_b.extend(library_ids)

        where_s = f"WHERE {' AND '.join(clauses_s)}" if clauses_s else ""
        where_b = f"WHERE {' AND '.join(clauses_b)}"

        search_clause = ""
        search_params: List[Any] = []
        if search:
            search_clause = "AND tag LIKE ?"
            search_params.append(f"%{search}%")

        query = f"""
            SELECT DISTINCT tag FROM (
                SELECT CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
                FROM series s, json_each(s.raw_json, '$.tags') as t
                {where_s}
                UNION
                SELECT CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
                FROM books b, json_each(b.raw_json, '$.metadata.tags') as t
                {where_b}
                UNION
                SELECT CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
                FROM books b, json_each(b.raw_json, '$.tags') as t
                {where_b}
            )
            WHERE tag IS NOT NULL AND TRIM(tag) != '' {search_clause}
            ORDER BY tag COLLATE NOCASE ASC
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params_s + params_b + params_b + search_params)
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    async def get_book_tags(
        self,
        library_ids: Optional[List[int]] = None,
        series_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[str]:
        clauses_b = ["b.deleted = 0"]
        params_b: List[Any] = []

        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            clauses_b.append(f"b.library_id IN ({ph})")
            params_b.extend(library_ids)

        if series_id:
            clauses_b.append("(b.series_id = ? OR b.series_id LIKE ? OR b.series_id LIKE ?)")
            params_b.extend([series_id, f"{series_id}-%", f"%-{series_id}"])

        where_b = f"WHERE {' AND '.join(clauses_b)}"

        search_clause = ""
        search_params: List[Any] = []
        if search:
            search_clause = "AND tag LIKE ?"
            search_params.append(f"%{search}%")

        query = f"""
            SELECT DISTINCT tag FROM (
                SELECT CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
                FROM books b, json_each(b.raw_json, '$.metadata.tags') as t
                {where_b}
                UNION
                SELECT CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
                FROM books b, json_each(b.raw_json, '$.tags') as t
                {where_b}
            )
            WHERE tag IS NOT NULL AND TRIM(tag) != '' {search_clause}
            ORDER BY tag COLLATE NOCASE ASC
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params_b + params_b + search_params)
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    async def get_series_tags(
        self,
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
    ) -> List[str]:
        clauses_s = []
        params_s: List[Any] = []

        if library_ids is not None:
            if len(library_ids) == 0:
                return []
            ph = ",".join("?" for _ in library_ids)
            clauses_s.append(f"s.library_id IN ({ph})")
            params_s.extend(library_ids)

        where_s = f"WHERE {' AND '.join(clauses_s)}" if clauses_s else ""

        search_clause = ""
        search_params: List[Any] = []
        if search:
            search_clause = "AND tag LIKE ?"
            search_params.append(f"%{search}%")

        query = f"""
            SELECT DISTINCT
                CASE WHEN t.type = 'object' THEN json_extract(t.value, '$.name') ELSE t.value END as tag
            FROM series s, json_each(s.raw_json, '$.tags') as t
            {where_s}
            WHERE tag IS NOT NULL AND TRIM(tag) != '' {search_clause}
            ORDER BY tag COLLATE NOCASE ASC
        """
        async with self.get_db() as db:
            cursor = await db.execute(query, params_s + search_params)
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    async def get_author_roles(self) -> List[str]:
        standard = ["writer", "penciller", "inker", "colorist", "letterer", "cover", "editor", "translator"]
        async with self.get_db() as db:
            cursor = await db.execute("SELECT DISTINCT role FROM authors WHERE role IS NOT NULL AND TRIM(role) != ''")
            rows = await cursor.fetchall()
            db_roles = [r[0] for r in rows if r[0]]
        all_roles = sorted(set(standard + db_roles))
        return all_roles

    async def get_authors_list(
        self,
        search: Optional[str] = None,
        role: Optional[str] = None,
        library_ids: Optional[List[int]] = None,
        series_ids: Optional[List[str]] = None,
        offset: int = 0,
        limit: int = 20,
        unpaged: bool = False,
    ) -> Tuple[List[Dict[str, str]], int]:
        outer_clauses = ["name IS NOT NULL AND TRIM(name) != ''"]
        outer_params: List[Any] = []
        if search:
            outer_clauses.append("name LIKE ?")
            outer_params.append(f"%{search}%")
        if role:
            outer_clauses.append("LOWER(role) = LOWER(?)")
            outer_params.append(role)
        where_outer = f"WHERE {' AND '.join(outer_clauses)}"

        if library_ids is None and not series_ids:
            # Fast path: query directly from indexed authors table if populated
            async with self.get_db() as db:
                db.row_factory = aiosqlite.Row
                count_q = f"SELECT COUNT(*) as total FROM authors {where_outer}"
                cur = await db.execute(count_q, outer_params)
                row = await cur.fetchone()
                total = row["total"] if row else 0

                if total > 0:
                    if unpaged:
                        data_q = f"SELECT name, COALESCE(role, 'writer') as role FROM authors {where_outer} ORDER BY name COLLATE NOCASE ASC"
                        data_params = outer_params
                    else:
                        data_q = f"SELECT name, COALESCE(role, 'writer') as role FROM authors {where_outer} ORDER BY name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
                        data_params = outer_params + [limit, offset]

                    cur_data = await db.execute(data_q, data_params)
                    rows = await cur_data.fetchall()
                    return [{"name": r["name"], "role": r["role"]} for r in rows], total

        clauses_b = ["b.deleted = 0"]
        params_b: List[Any] = []
        if library_ids is not None:
            if len(library_ids) == 0:
                return [], 0
            ph = ",".join("?" for _ in library_ids)
            clauses_b.append(f"b.library_id IN ({ph})")
            params_b.extend(library_ids)
        if series_ids:
            s_clauses = []
            for s_id in series_ids:
                s_clauses.append("(b.series_id = ? OR b.series_id LIKE ? OR b.series_id LIKE ?)")
                params_b.extend([s_id, f"{s_id}-%", f"%-{s_id}"])
            clauses_b.append(f"({' OR '.join(s_clauses)})")

        where_b = f"WHERE {' AND '.join(clauses_b)}"

        base_cte = f"""
            WITH extracted_authors AS (
                SELECT DISTINCT
                    CASE WHEN a.type = 'object' THEN json_extract(a.value, '$.name') ELSE a.value END as name,
                    CASE WHEN a.type = 'object' THEN COALESCE(json_extract(a.value, '$.role'), 'writer') ELSE 'writer' END as role
                FROM books b, json_each(b.raw_json, '$.metadata.authors') as a
                {where_b}
                UNION
                SELECT DISTINCT
                    CASE WHEN a.type = 'object' THEN json_extract(a.value, '$.name') ELSE a.value END as name,
                    CASE WHEN a.type = 'object' THEN COALESCE(json_extract(a.value, '$.role'), 'writer') ELSE 'writer' END as role
                FROM books b, json_each(b.raw_json, '$.authors') as a
                {where_b}
            )
        """
        count_q = f"{base_cte} SELECT COUNT(*) as total FROM extracted_authors {where_outer}"
        if unpaged:
            data_q = f"{base_cte} SELECT name, role FROM extracted_authors {where_outer} ORDER BY name COLLATE NOCASE ASC"
            data_params = params_b + params_b + outer_params
        else:
            data_q = f"{base_cte} SELECT name, role FROM extracted_authors {where_outer} ORDER BY name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
            data_params = params_b + params_b + outer_params + [limit, offset]

        async with self.get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(count_q, params_b + params_b + outer_params)
            row = await cur.fetchone()
            total = row["total"] if row else 0
            cur_data = await db.execute(data_q, data_params)
            rows = await cur_data.fetchall()
            return [{"name": r["name"], "role": r["role"]} for r in rows], total

    async def get_author_names_list(
        self,
        search: Optional[str] = None,
        role: Optional[str] = None,
        library_ids: Optional[List[int]] = None,
        series_ids: Optional[List[str]] = None,
        offset: int = 0,
        limit: int = 20,
        unpaged: bool = False,
    ) -> Tuple[List[str], int]:
        authors, total = await self.get_authors_list(
            search=search,
            role=role,
            library_ids=library_ids,
            series_ids=series_ids,
            offset=offset,
            limit=limit,
            unpaged=unpaged,
        )
        seen = set()
        names = []
        for a in authors:
            if a["name"].lower() not in seen:
                seen.add(a["name"].lower())
                names.append(a["name"])
        return names, total
