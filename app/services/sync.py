import asyncio
import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List
import aiosqlite

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.models.internal import SyncStatus
from app.services.page_calculator import PageCalculator

logger = logging.getLogger("grimmory_proxy.sync")

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def generate_series_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    cleaned = SLUG_PATTERN.sub("-", normalized.lower()).strip("-")
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    if cleaned:
        return f"{cleaned[:50]}-{name_hash}"
    return f"s-{name_hash}"


class SyncService:
    def __init__(self, db: Database, page_calculator: PageCalculator):
        self.db = db
        self.page_calculator = page_calculator
        self.status = SyncStatus()
        self._background_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _log(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        self.status.logs.append(log_line)
        if len(self.status.logs) > 200:
            self.status.logs = self.status.logs[-200:]

    async def sync_all_metadata(self) -> SyncStatus:
        """
        Executes a complete metadata sync from Grimmory into SQLite cache.
        """
        async with self._lock:
            if self.status.is_running:
                logger.warning("Sync is already running")
                return self.status

            self.status.is_running = True
            self.status.current_phase = "starting"
            self.status.error_message = None

        try:
            self._log("Starting Grimmory metadata synchronization cycle...")
            # Check if this is initial startup / empty database
            initial_stats = await self.db.get_stats()
            was_database_empty = initial_stats["books_count"] == 0

            logger.info("Starting Grimmory metadata synchronization cycle...")
            await self.db.set_state("sync_status", "running")

            # 1. Fetch & Store Libraries
            self.status.current_phase = "fetching_libraries"
            libraries = await grimmory_client.get_libraries()
            await self.db.upsert_libraries(libraries)
            self._log(f"Synchronized {len(libraries)} libraries from Grimmory")
            logger.info(f"Synchronized {len(libraries)} libraries from Grimmory")

            # 2. Fetch all books (per library for authoritative libraryId, with fallback to get_all_books)
            self.status.current_phase = "fetching_books"
            all_books_map: Dict[int, Dict[str, Any]] = {}
            for lib in libraries:
                lib_id = lib.get("id")
                if lib_id is not None:
                    try:
                        lib_books = await grimmory_client.get_library_books(lib_id)
                        for b in lib_books:
                            b["libraryId"] = lib_id
                            all_books_map[b["id"]] = b
                    except Exception as e:
                        logger.debug(f"get_library_books({lib_id}): {e}")

            if not all_books_map:
                books_raw = await grimmory_client.get_all_books()
                for b in books_raw:
                    all_books_map[b["id"]] = b
            else:
                try:
                    books_raw = await grimmory_client.get_all_books()
                    for b in books_raw:
                        if b["id"] not in all_books_map:
                            all_books_map[b["id"]] = b
                except Exception as e:
                    logger.debug(f"get_all_books fallback: {e}")

            books_raw = list(all_books_map.values())
            self._log(f"Fetched {len(books_raw)} total books from Grimmory")
            logger.info(f"Fetched {len(books_raw)} total books from Grimmory")

            # Detect and handle books removed upstream on Grimmory
            current_remote_book_ids = {b["id"] for b in books_raw if not b.get("deleted")}
            active_db_ids = await self.db.get_active_book_ids()
            removed_ids = [bid for bid in active_db_ids if bid not in current_remote_book_ids]
            if removed_ids:
                marked = await self.db.mark_books_deleted(removed_ids)
                self._log(f"Detected and marked {marked} removed books as deleted")
                logger.info(f"Detected and marked {marked} removed books as deleted from database")

            # 3. Group books into series and map book records
            self.status.current_phase = "processing_series_and_books"
            series_dict: Dict[str, Dict[str, Any]] = {}
            book_records: List[Dict[str, Any]] = []
            author_names: set[str] = set()

            for b in books_raw:
                meta = b.get("metadata") or {}
                pfile = b.get("primaryFile") or {}
                lib_id = (
                    b.get("libraryId")
                    or (b.get("library", {}).get("id") if isinstance(b.get("library"), dict) else None)
                    or (b.get("metadata", {}).get("libraryId") if isinstance(b.get("metadata"), dict) else None)
                    or 1
                )

                # Series name heuristic matching Grimmory's KomgaMapper
                series_name = meta.get("seriesName")
                if not series_name:
                    series_name = meta.get("title") or pfile.get("fileName") or "Unknown Series"

                series_slug = generate_series_slug(series_name)
                series_id = f"{lib_id}-{series_slug}"

                if series_id not in series_dict:
                    series_dict[series_id] = {
                        "id": series_id,
                        "library_id": lib_id,
                        "name": series_name,
                        "slug": series_slug,
                        "sort_title": series_name,
                        "books_count": 0,
                        "created": b.get("addedOn"),
                        "last_modified": b.get("addedOn"),
                        "raw_json": {
                            "name": series_name,
                            "libraryId": lib_id,
                            "slug": series_slug,
                        },
                    }
                series_dict[series_id]["books_count"] += 1

                # Authors
                for auth in meta.get("authors", []):
                    if isinstance(auth, str) and auth.strip():
                        author_names.add(auth.strip())
                    elif isinstance(auth, dict) and "name" in auth:
                        author_names.add(auth["name"].strip())

                # Grimmory released date field
                raw_released = (
                    meta.get("released")
                    or b.get("released")
                    or meta.get("releaseDate")
                    or b.get("releaseDate")
                    or meta.get("publishedDate")
                )
                released_str = str(raw_released).strip() if raw_released else None

                book_records.append({
                    "id": b["id"],
                    "series_id": series_id,
                    "library_id": lib_id,
                    "name": meta.get("title") or pfile.get("fileName") or f"Book {b['id']}",
                    "number": meta.get("seriesNumber") or 1.0,
                    "book_type": pfile.get("bookType", "EPUB"),
                    "file_path": pfile.get("filePath"),
                    "file_size_kb": pfile.get("fileSizeKb", 0),
                    "page_count": meta.get("pageCount") or 0,
                    "deleted": 1 if b.get("deleted") else 0,
                    "released": released_str,
                    "created": b.get("addedOn"),
                    "last_modified": b.get("addedOn"),
                    "raw_json": b,
                })

            # Upsert series
            await self.db.upsert_series_batch(list(series_dict.values()))
            self._log(f"Upserted {len(series_dict)} series into database")
            logger.info(f"Upserted {len(series_dict)} series into database")

            # Upsert books
            await self.db.upsert_books_batch(book_records)
            self._log(f"Upserted {len(book_records)} books into database")
            logger.info(f"Upserted {len(book_records)} books into database")

            # Prune any empty series
            await self.db.cleanup_empty_series()

            # Update authors table
            if author_names:
                async with self.db.get_connection() as conn:
                    for author in author_names:
                        await conn.execute(
                            "INSERT INTO authors (name, count) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET count = count + 1",
                            (author,),
                        )
                    await conn.commit()

            # Record sync timestamp
            now_str = datetime.now(timezone.utc).isoformat()
            await self.db.set_state("last_sync_time", now_str)
            await self.db.set_state("sync_status", "idle")

            self.status.is_running = False
            self.status.last_sync_time = now_str
            self.status.current_phase = "completed"

            self._log("Metadata sync cycle successfully completed!")
            logger.info("Metadata sync cycle successfully completed")

            # If this was initial startup with an empty database, trigger automatic page calculation!
            if was_database_empty and settings.sync_on_startup:
                self._log("First startup with empty database detected: triggering background page calculation...")
                logger.info("First startup with empty database detected: triggering background page calculation for all missing pages...")
                asyncio.create_task(self.page_calculator.run_calculation_job(missing_only=True))

        except Exception as e:
            self._log(f"ERROR: Sync cycle failed: {e}")
            logger.error(f"Sync cycle failed: {e}", exc_info=True)
            self.status.is_running = False
            self.status.error_message = str(e)
            self.status.current_phase = "failed"
            await self.db.set_state("sync_status", "idle")

        return self.status

    async def start_background_loop(self) -> None:
        """
        Starts the periodic synchronization loop according to SYNC_INTERVAL_MINUTES.
        """
        if settings.sync_on_startup:
            logger.info("Triggering initial sync on startup...")
            await self.sync_all_metadata()

        if settings.sync_interval_minutes <= 0:
            logger.info("Periodic sync is disabled (SYNC_INTERVAL_MINUTES=0)")
            return

        interval_sec = settings.sync_interval_minutes * 60
        logger.info(f"Starting background sync scheduler with interval of {settings.sync_interval_minutes} minutes")

        while True:
            try:
                await asyncio.sleep(interval_sec)
                logger.info("Periodic sync interval elapsed, triggering sync...")
                await self.sync_all_metadata()
            except asyncio.CancelledError:
                logger.info("Background sync task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background sync loop: {e}")
                await asyncio.sleep(30)
