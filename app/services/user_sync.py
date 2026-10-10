import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
import httpx

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.models.internal import ReadSyncStatus, UserSession
from app.services.mapper import format_iso_timestamp

logger = logging.getLogger("grimmory_proxy.user_sync")


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


class UserSyncService:
    def __init__(self, db: Optional[Database] = None, cooldown_seconds: int = 60):
        self._db = db
        self.cooldown_seconds = cooldown_seconds
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._last_sync_times: Dict[int, datetime] = {}
        self._global_lock = asyncio.Lock()
        self._background_tasks: Set[asyncio.Task] = set()
        self.status = ReadSyncStatus()

    def _log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] {message}"
        self.status.logs.append(entry)
        if len(self.status.logs) > 100:
            self.status.logs = self.status.logs[-100:]
        logger.info(message)

    def set_db(self, db: Database) -> None:
        self._db = db

    @property
    def database(self) -> Database:
        if self._db is not None:
            return self._db
        from app.main import db
        return db

    async def save_user_data(self, user: UserSession) -> None:
        """Stores or updates the user profile and Grimmory token in the persistent database."""
        try:
            await self.database.upsert_user(
                user_id=user.user_id,
                username=user.username,
                token=user.token,
                is_admin=user.is_admin,
                assigned_libraries=user.assigned_library_ids,
            )
        except Exception as e:
            logger.warning(f"Error saving user record for {user.username}: {e}")

    def trigger_background_sync(self, user: UserSession, force: bool = False) -> None:
        """Schedules a non-blocking background read status sync for the user."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self.capture_and_sync_user(user, force=force))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            pass

    async def sync_all_active_users(self) -> None:
        """
        Periodically syncs read states for all registered users who have a token stored in SQLite.
        """
        self.status.is_running = True
        self.status.error_message = None
        self._log("Starting read status synchronization for active users...")
        try:
            users = await self.database.get_users_with_tokens()
            self.status.active_users_count = len(users)
            if not users:
                self._log("No active users with tokens found for periodic read state sync.")
                return

            self._log(f"Found {len(users)} active user session(s) to synchronize.")
            total_synced_this_cycle = 0

            for u_row in users:
                uname = u_row.get("username", "unknown")
                self.status.current_user = uname
                try:
                    assigned_libs = []
                    try:
                        assigned_libs = json.loads(u_row.get("assigned_libraries") or "[]")
                    except Exception:
                        pass
                    user = UserSession(
                        user_id=u_row["id"],
                        username=uname,
                        token=u_row["token"],
                        is_admin=bool(u_row["is_admin"]),
                        assigned_library_ids=assigned_libs,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                    )
                    await self.capture_and_sync_user(user, force=True)
                except Exception as e:
                    self._log(f"Error syncing user '{uname}': {e}")
                    logger.debug(f"Periodic sync failed for user {uname}: {e}")

            self.status.last_sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self._log(f"Completed read status sync cycle.")
        except Exception as e:
            self.status.error_message = str(e)
            self._log(f"Error during periodic user read status sync: {e}")
            logger.error(f"Error during periodic user read status sync: {e}")
        finally:
            self.status.is_running = False
            self.status.current_user = ""

    async def start_background_loop(self) -> None:
        """
        Starts periodic background loop syncing read states for all active users.
        """
        interval_minutes = settings.user_sync_interval_minutes
        if interval_minutes <= 0:
            logger.info("Periodic user read status sync is disabled (USER_SYNC_INTERVAL_MINUTES=0)")
            return

        interval_sec = interval_minutes * 60
        logger.info(f"Starting periodic user read status sync scheduler every {interval_minutes} minutes")

        # Initial delay before the first periodic cycle so startup metadata sync finishes
        await asyncio.sleep(15)

        while True:
            try:
                logger.info("Periodic user read status sync interval elapsed, checking users...")
                await self.sync_all_active_users()
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                logger.info("Periodic user read status sync task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic user read status sync loop: {e}")
                await asyncio.sleep(30)

    async def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        async with self._global_lock:
            if user_id not in self._user_locks:
                self._user_locks[user_id] = asyncio.Lock()
            return self._user_locks[user_id]

    async def capture_and_sync_user(self, user: UserSession, force: bool = False) -> bool:
        """
        Captures user connection data and synchronizes read states from Grimmory.
        Protected with per-user lock and throttled by cooldown_seconds.
        """
        # 1. Always capture user data in the database
        try:
            await self.database.upsert_user(
                user_id=user.user_id,
                username=user.username,
                token=user.token,
                is_admin=user.is_admin,
                assigned_libraries=user.assigned_library_ids,
            )
        except Exception as e:
            logger.warning(f"Error capturing user connection record for {user.username}: {e}")

        # If user has no Grimmory token, skip remote sync
        if not user.token:
            return False

        # 2. Check cooldown before acquiring lock
        now = datetime.now(timezone.utc)
        last_sync = self._last_sync_times.get(user.user_id)
        if not force and last_sync and (now - last_sync).total_seconds() < self.cooldown_seconds:
            return False

        user_lock = await self._get_user_lock(user.user_id)
        async with user_lock:
            # Re-check cooldown after lock acquisition
            now = datetime.now(timezone.utc)
            last_sync = self._last_sync_times.get(user.user_id)
            if not force and last_sync and (now - last_sync).total_seconds() < self.cooldown_seconds:
                return False

            # Set cooldown immediately so transient failures don't hammer Grimmory
            self._last_sync_times[user.user_id] = datetime.now(timezone.utc)
            try:
                await self._sync_grimmory_read_states(user)
                await self.database.update_user_progress_sync_time(user.user_id)
                return True
            except Exception as e:
                logger.warning(f"Failed to check Grimmory read states for user {user.username}: {e}")
                return False

    async def _sync_grimmory_read_states(self, user: UserSession) -> None:
        logger.info(f"Checking Grimmory read states for user '{user.username}' (id={user.user_id})")

        all_books_map: Dict[int, Dict[str, Any]] = {}

        # 1. Fetch library books for user's accessible libraries
        try:
            db_libraries = await self.database.get_libraries()
            target_libs = db_libraries
            if not user.is_admin and user.assigned_library_ids:
                target_libs = [lib for lib in db_libraries if lib["id"] in user.assigned_library_ids]

            for lib in target_libs:
                lib_id = lib["id"]
                try:
                    lib_books = await grimmory_client.get_library_books(lib_id, token=user.token)
                    for b in lib_books:
                        all_books_map[b["id"]] = b
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
                    logger.debug(f"Grimmory unreachable for library {lib_id}: {e}")
                    return
                except Exception as e:
                    logger.debug(f"Could not fetch books for library {lib_id} with user token: {e}")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError):
            return
        except Exception as e:
            logger.debug(f"Error resolving libraries for user read states: {e}")

        # 2. Fetch all books with user token as complement/fallback
        try:
            all_books = await grimmory_client.get_all_books(token=user.token)
            for b in all_books:
                if b["id"] not in all_books_map:
                    all_books_map[b["id"]] = b
                else:
                    all_books_map[b["id"]].update(b)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
            logger.debug(f"Grimmory unreachable for all books: {e}")
            return
        except Exception as e:
            logger.debug(f"Could not fetch all books with user token: {e}")

        # 3. Check magic shelves if available
        try:
            magic_shelves = await grimmory_client.get_magic_shelves(token=user.token)
            for shelf in magic_shelves:
                shelf_books = shelf.get("books") or shelf.get("items") or []
                for b in shelf_books:
                    if isinstance(b, dict) and "id" in b:
                        bid = b["id"]
                        if bid not in all_books_map:
                            all_books_map[bid] = b
                        else:
                            all_books_map[bid].update(b)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError):
            return
        except Exception as e:
            logger.debug(f"Could not fetch magic shelves with user token: {e}")

        if not all_books_map:
            logger.debug(f"No books returned from Grimmory for user {user.username}")
            return 0

        # Query all existing local read progress for this user to avoid overwriting newer local progress
        existing_local = await self.database.get_all_user_read_progress(user.user_id)

        progress_records: List[Dict[str, Any]] = []

        for book_id, b in all_books_map.items():
            meta = b.get("metadata") or {}
            page_count = _safe_int(meta.get("pageCount") or b.get("page_count"), 0)

            read_status = b.get("readStatus")
            date_finished = b.get("dateFinished")
            cbx = b.get("cbxProgress") if isinstance(b.get("cbxProgress"), dict) else None
            pdf = b.get("pdfProgress") if isinstance(b.get("pdfProgress"), dict) else None
            epub = b.get("epubProgress") if isinstance(b.get("epubProgress"), dict) else None
            rp = b.get("readProgress") if isinstance(b.get("readProgress"), dict) else None

            cbx_perc = _safe_float(cbx.get("percentage")) if cbx else 0.0
            cbx_page = _safe_int(cbx.get("page")) if cbx else 0
            pdf_perc = _safe_float(pdf.get("percentage")) if pdf else 0.0
            pdf_page = _safe_int(pdf.get("page")) if pdf else 0
            epub_perc = _safe_float(epub.get("percentage")) if epub else 0.0

            is_completed = False
            is_in_prog = False
            page = 1
            read_date = None

            # Completed check
            if read_status == "READ" or date_finished:
                is_completed = True
                read_date = date_finished
            elif cbx and cbx_perc >= 99.0:
                is_completed = True
                read_date = cbx.get("lastRead")
            elif pdf and pdf_perc >= 99.0:
                is_completed = True
                read_date = pdf.get("lastRead")
            elif epub and epub_perc >= 99.0:
                is_completed = True
                read_date = epub.get("lastRead")
            elif rp and rp.get("completed"):
                is_completed = True
                read_date = rp.get("readDate")

            # In-progress check
            if not is_completed:
                epub_page = max(1, round((epub_perc / 100.0) * page_count)) if epub_perc > 0 and page_count > 0 else 1

                if read_status in ("READING", "IN_PROGRESS"):
                    is_in_prog = True
                    read_date = (
                        (cbx.get("lastRead") if cbx else None)
                        or (pdf.get("lastRead") if pdf else None)
                        or (epub.get("lastRead") if epub else None)
                        or (rp.get("readDate") if rp else None)
                    )
                    page = cbx_page or pdf_page or (epub_page if epub_perc > 0 else 1)
                elif cbx_perc > 0 or cbx_page > 0:
                    is_in_prog = True
                    page = cbx_page or 1
                    read_date = cbx.get("lastRead")
                elif pdf_perc > 0 or pdf_page > 0:
                    is_in_prog = True
                    page = pdf_page or 1
                    read_date = pdf.get("lastRead")
                elif epub_perc > 0:
                    is_in_prog = True
                    page = epub_page
                    read_date = epub.get("lastRead")
                elif rp:
                    rp_page = _safe_int(rp.get("page"))
                    if rp_page > 1 or (rp.get("completed") is False and rp.get("readDate")):
                        is_in_prog = True
                        page = rp_page or 1
                        read_date = rp.get("readDate")

            # Check against local progress to protect local reading state
            local_rec = existing_local.get(book_id)
            if local_rec:
                # If local record is already completed, do not downgrade it
                if local_rec.get("completed") and not is_completed:
                    continue
                local_ts = local_rec.get("read_date") or ""
                remote_ts = format_iso_timestamp(read_date or b.get("last_modified") or b.get("addedOn"))
                # If local record timestamp is newer, preserve local progress
                if local_ts and remote_ts and local_ts > remote_ts:
                    continue
                # If remote has no progress (or unread), but local has active progress, keep local
                if not is_completed and not is_in_prog:
                    continue

            if is_completed:
                final_page = (
                    page_count
                    or (cbx_page if cbx_page > 0 else None)
                    or (pdf_page if pdf_page > 0 else None)
                    or (epub_page if epub_perc > 0 else None)
                    or 1
                )
                progress_records.append({
                    "user_id": user.user_id,
                    "book_id": book_id,
                    "page": final_page,
                    "completed": True,
                    "read_date": format_iso_timestamp(read_date or b.get("last_modified") or b.get("addedOn")),
                })
            elif is_in_prog:
                progress_records.append({
                    "user_id": user.user_id,
                    "book_id": book_id,
                    "page": page,
                    "completed": False,
                    "read_date": format_iso_timestamp(read_date or b.get("last_modified") or b.get("addedOn")),
                })

        if progress_records:
            await self.database.upsert_read_progress_batch(progress_records)
            self.status.total_synced_records += len(progress_records)
            self._log(f"Synchronized {len(progress_records)} read states from Grimmory for user '{user.username}'")
            return len(progress_records)
        return 0


user_sync_service = UserSyncService()
