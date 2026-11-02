import asyncio
import io
import logging
import re
import zipfile
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
import httpx
from PIL import Image
from pypdf import PdfReader

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.models.internal import PageCalcStatus
from app.models.komga import PageDto

logger = logging.getLogger("grimmory_proxy.page_calculator")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class PageCalculator:
    def __init__(self, db: Database):
        self.db = db
        self.status = PageCalcStatus()
        self.logs: List[str] = []
        self._stop_requested = False
        self._lock: Optional[asyncio.Lock] = None
        self._task: Optional[asyncio.Task] = None
        self._worker_tasks: List[asyncio.Task] = []

    def log(self, message: str, level: str = "info") -> None:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.logs.append(line)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]
        self.status.logs = list(self.logs)
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def clear_logs(self) -> None:
        self.logs.clear()
        self.status.logs.clear()

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_running(self) -> bool:
        if self._task is not None and not self._task.done():
            return True
        if self.status.is_running:
            self.status.is_running = False
        return False

    def start_calculation_job(self, missing_only: bool = True, retry_failed_only: bool = False) -> bool:
        if self.is_running:
            return False
        self._stop_requested = False
        self.status.is_running = True
        self.status.status = "running"
        self._task = asyncio.create_task(
            self.run_calculation_job(missing_only=missing_only, retry_failed_only=retry_failed_only)
        )
        return True

    async def request_stop(self) -> None:
        self._stop_requested = True
        logger.info("Page calculation job stop requested")

        # Cancel in-flight worker tasks immediately
        for t in list(self._worker_tasks):
            if not t.done():
                t.cancel()
        self._worker_tasks.clear()

        # Cancel main task if active
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            self._task = None

        self.status.is_running = False
        self.status.status = "stopped"
        self.status.current_book = "Calculation stopped by user"

        try:
            if self.status.job_id:
                await self.db.update_page_calc_progress(
                    job_id=self.status.job_id,
                    processed=self.status.processed_books,
                    updated=self.status.updated_books,
                    removed=self.status.removed_books,
                    errors=self.status.error_count,
                    current_book="Calculation stopped by user",
                    status="stopped",
                )
            else:
                latest = await self.db.get_latest_page_calc_job()
                if latest and latest.get("status") == "running":
                    await self.db.update_page_calc_progress(
                        job_id=latest["id"],
                        processed=latest.get("processed_books", 0),
                        updated=latest.get("updated_books", 0),
                        removed=latest.get("removed_books", 0),
                        errors=latest.get("error_count", 0),
                        current_book="Calculation stopped by user",
                        status="stopped",
                    )
        except Exception as e:
            logger.debug(f"Error marking stopped job in DB: {e}")

    @staticmethod
    def count_epub_pages(epub_bytes: bytes, chars_per_page: int = 1024) -> Tuple[int, List[PageDto]]:
        """
        Calculate page count for EPUB novel using Calibre's ADE algorithm.
        Standard ADE heuristic counts 1,024 uncompressed characters per page.
        """
        if not epub_bytes:
            raise ValueError("Empty book file received (0 bytes)")

        total_chars = 0
        try:
            with zipfile.ZipFile(io.BytesIO(epub_bytes)) as z:
                # Find all HTML/XHTML files in zip
                for name in z.namelist():
                    if name.lower().endswith((".html", ".xhtml", ".htm")):
                        try:
                            raw_content = z.read(name).decode("utf-8", errors="ignore")
                            # Remove script and style tags completely
                            cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_content)
                            # Remove remaining HTML tags
                            text = _HTML_TAG_RE.sub(" ", cleaned)
                            # Unescape HTML entities
                            text = unescape(text)
                            # Collapse whitespace
                            text = _WHITESPACE_RE.sub(" ", text).strip()
                            total_chars += len(text)
                        except Exception as e:
                            logger.debug(f"Error parsing chapter {name}: {e}")
        except zipfile.BadZipFile as e:
            raise ValueError(f"Corrupt EPUB archive (BadZipFile): {e}")
        except Exception as e:
            raise ValueError(f"Failed to read EPUB archive: {e}")

        pages_count = max(1, round(total_chars / chars_per_page)) if total_chars > 0 else 1
        page_dtos = [
            PageDto(
                number=i,
                fileName=f"page-{i:03d}.jpg",
                mediaType="image/jpeg",
                width=1200,
                height=1600,
            )
            for i in range(1, pages_count + 1)
        ]
        return pages_count, page_dtos

    @staticmethod
    def count_cbz_pages(cbz_bytes: bytes) -> Tuple[int, List[PageDto]]:
        """
        Calculate page count for CBZ/CBX: 1 image = 1 page.
        Also parses image dimensions for the first few pages.
        """
        if not cbz_bytes:
            raise ValueError("Empty book file received (0 bytes)")

        page_dtos: List[PageDto] = []
        try:
            with zipfile.ZipFile(io.BytesIO(cbz_bytes)) as z:
                # Filter out OS metadata files like __MACOSX
                image_entries = sorted([
                    n for n in z.namelist()
                    if not n.startswith("__MACOSX/") and any(n.lower().endswith(ext) for ext in IMAGE_EXTENSIONS)
                ])

                if not image_entries:
                    raise ValueError("No valid image files found inside CBZ archive")

                for idx, entry_name in enumerate(image_entries, start=1):
                    ext = entry_name.lower().split(".")[-1]
                    mime = f"image/{'jpeg' if ext in ['jpg', 'jpeg'] else ext}"
                    width, height = 1200, 1600
                    size_bytes = 0

                    # Try reading dimensions lazily from image header without decompressing full image
                    try:
                        info = z.getinfo(entry_name)
                        size_bytes = info.file_size
                        with z.open(entry_name) as zf, Image.open(zf) as img:
                            width, height = img.size
                    except Exception:
                        pass

                    page_dtos.append(
                        PageDto(
                            number=idx,
                            fileName=entry_name.split("/")[-1],
                            mediaType=mime,
                            width=width,
                            height=height,
                            sizeBytes=size_bytes,
                        )
                    )
        except zipfile.BadZipFile as e:
            raise ValueError(f"Corrupt CBZ archive (BadZipFile): {e}")
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"Failed to read CBZ archive: {e}")

        return len(page_dtos), page_dtos

    @staticmethod
    def count_pdf_pages(pdf_bytes: bytes) -> Tuple[int, List[PageDto]]:
        """Calculate page count for PDF."""
        if not pdf_bytes:
            raise ValueError("Empty book file received (0 bytes)")

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_count = len(reader.pages)
        except Exception as e:
            raise ValueError(f"Corrupt or unreadable PDF: {e}")

        if pages_count <= 0:
            raise ValueError("PDF document contains 0 pages")

        page_dtos = [
            PageDto(
                number=i,
                fileName=f"page-{i:03d}.jpg",
                mediaType="image/jpeg",
                width=1200,
                height=1600,
            )
            for i in range(1, max(1, pages_count) + 1)
        ]
        return max(1, pages_count), page_dtos

    async def inspect_book_pages(self, book_id: int, book_type: str) -> Tuple[int, List[PageDto]]:
        """
        Inspect and calculate page count for a book.
        Attempts fast API endpoints on Grimmory first when applicable,
        then falls back to downloading the book file for complete parsing.
        """
        btype = (book_type or "").upper()

        # Fast path for CBX if Grimmory already knows page list
        if btype in ["CBX", "CBZ"]:
            try:
                pages_list = await grimmory_client.get_cbx_pages(book_id)
                if pages_list:
                    page_count = len(pages_list)
                    page_dtos = [
                        PageDto(
                            number=p,
                            fileName=f"page-{p:03d}.jpg",
                            mediaType="image/jpeg",
                            width=1200,
                            height=1600,
                        )
                        for p in pages_list
                    ]
                    return page_count, page_dtos
            except Exception:
                pass

        # Fast path for PDF if Grimmory knows page list
        if btype == "PDF":
            try:
                pages_list = await grimmory_client.get_pdf_pages(book_id)
                if pages_list:
                    page_count = len(pages_list)
                    page_dtos = [
                        PageDto(
                            number=p,
                            fileName=f"page-{p:03d}.jpg",
                            mediaType="image/jpeg",
                            width=1200,
                            height=1600,
                        )
                        for p in pages_list
                    ]
                    return page_count, page_dtos
            except Exception:
                pass

        # Download book file to perform accurate Calibre / CBZ calculation
        file_bytes = await grimmory_client.download_book_bytes(book_id)

        if btype in ["CBX", "CBZ"]:
            return self.count_cbz_pages(file_bytes)
        elif btype == "PDF":
            return self.count_pdf_pages(file_bytes)
        else:
            # EPUB / Novel default: Calibre ADE algorithm
            return self.count_epub_pages(file_bytes, chars_per_page=settings.novel_chars_per_page)

    async def calculate_and_save_book(self, book: Dict[str, Any]) -> str:
        """
        Calculates pages for a single book and writes the result to both Grimmory and SQLite.
        Returns: "updated", "removed", or "error"
        """
        book_id = book["id"]
        book_name = book.get("name", f"Book {book_id}")
        book_type = book.get("book_type", "EPUB")
        series_name = book.get("series_name")
        library_id = book.get("library_id")
        file_path = book.get("file_path")

        try:
            page_count, page_dtos = await asyncio.wait_for(
                self.inspect_book_pages(book_id, book_type),
                timeout=60.0,
            )
            self.log(f"Calculated {page_count} pages for book {book_id} ('{book_name}') [{book_type}]")

            # 1. Update local SQLite DB first so page count is immediately enriched
            await self.db.update_book_page_count(book_id, page_count)

            # 2. Cache page details in SQLite
            page_records = [p.model_dump() for p in page_dtos]
            await self.db.upsert_book_pages(book_id, page_records)

            # 3. Clear any previously recorded error for this book now that it succeeded
            await self.db.delete_page_calc_error(book_id)

            # 4. Update Grimmory metadata upstream (best-effort with timeout)
            try:
                await asyncio.wait_for(
                    grimmory_client.update_book_metadata(book_id, {"pageCount": page_count}),
                    timeout=30.0,
                )
            except Exception as e:
                self.log(f"Warning: Could not push updated pageCount to Grimmory for book {book_id}: {e}", level="warning")

            return "updated"
        except asyncio.TimeoutError:
            err_msg = "Download or inspection timed out after 60 seconds"
            self.log(f"ERROR: {err_msg} for book {book_id} ('{book_name}') [{book_type}]", level="error")
            await self.db.record_page_calc_error(
                book_id=book_id,
                book_title=book_name,
                series_name=series_name,
                library_id=library_id,
                book_type=book_type,
                file_path=file_path,
                error_message=err_msg,
                job_id=self.status.job_id,
            )
            return "error"
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                self.log(f"Book {book_id} ('{book_name}') was removed from Grimmory (HTTP {e.response.status_code}); marking as deleted.", level="warning")
                await self.db.mark_book_deleted(book_id)
                await self.db.delete_page_calc_error(book_id)
                return "removed"
            err_msg = f"HTTP {e.response.status_code} error from Grimmory server"
            self.log(f"ERROR: {err_msg} calculating pages for book {book_id} ('{book_name}'): {e}", level="error")
            await self.db.record_page_calc_error(
                book_id=book_id,
                book_title=book_name,
                series_name=series_name,
                library_id=library_id,
                book_type=book_type,
                file_path=file_path,
                error_message=err_msg,
                job_id=self.status.job_id,
            )
            return "error"
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg or "Not Found" in err_msg:
                self.log(f"Book {book_id} ('{book_name}') was removed from Grimmory; marking as deleted: {e}", level="warning")
                await self.db.mark_book_deleted(book_id)
                await self.db.delete_page_calc_error(book_id)
                return "removed"
            self.log(f"ERROR calculating pages for book {book_id} ('{book_name}') [{book_type}]: {err_msg}", level="error")
            await self.db.record_page_calc_error(
                book_id=book_id,
                book_title=book_name,
                series_name=series_name,
                library_id=library_id,
                book_type=book_type,
                file_path=file_path,
                error_message=err_msg,
                job_id=self.status.job_id,
            )
            return "error"

    async def run_calculation_job(self, missing_only: bool = True, retry_failed_only: bool = False) -> PageCalcStatus:
        """
        Runs the background page calculation loop across books using SYNC_CONCURRENCY workers.
        """
        async with self.lock:
            if self._task and not self._task.done() and self._task != asyncio.current_task():
                self.log("A page calculation job is already in progress", level="warning")
                return self.status

            self._stop_requested = False
            self.status.is_running = True
            self.status.status = "running"
            self.status.error_message = None

            job_id = None
            try:
                # Fetch books to process
                if retry_failed_only:
                    books = await self.db.get_books_with_page_calc_errors()
                elif missing_only:
                    books = await self.db.get_books_missing_pages()
                else:
                    books = await self.db.get_all_books_for_page_calc()

                total_books = len(books)
                job_id = await self.db.create_page_calc_job(total_books)

                self.status.job_id = job_id
                self.status.total_books = total_books
                self.status.processed_books = 0
                self.status.updated_books = 0
                self.status.removed_books = 0
                self.status.error_count = 0
                self.status.current_book = "Initializing..."
                self.status.recent_errors = await self.db.get_page_calc_errors(limit=50)

                if total_books == 0:
                    self.status.is_running = False
                    self.status.status = "completed"
                    if retry_failed_only:
                        msg = "No problematic books with errors found to retry"
                    elif missing_only:
                        msg = "All books already have page counts calculated"
                    else:
                        msg = "No books found in catalog"
                    self.status.current_book = msg
                    await self.db.update_page_calc_progress(
                        job_id=job_id,
                        processed=0,
                        updated=0,
                        removed=0,
                        errors=0,
                        current_book=msg,
                        status="completed",
                    )
                    self.log(f"Page calculation job {job_id} finished immediately: {msg}")
                    return self.status

                self.log(f"Starting page calculation job {job_id} for {total_books} books (concurrency: {settings.sync_concurrency})")
            except Exception as e:
                self.log(f"Error initializing page calculation job: {e}", level="error")
                self.status.is_running = False
                self.status.status = "failed"
                self.status.error_message = str(e)
                self.status.current_book = f"Error: {e}"
                return self.status

        queue: asyncio.Queue = asyncio.Queue()
        for b in books:
            queue.put_nowait(b)

        async def worker():
            while not self._stop_requested:
                try:
                    book = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    self.status.current_book = book.get("name", f"Book {book['id']}")
                    result = await self.calculate_and_save_book(book)

                    self.status.processed_books += 1
                    if result == "updated":
                        self.status.updated_books += 1
                    elif result == "removed":
                        self.status.removed_books += 1
                    else:
                        self.status.error_count += 1

                    # Update progress periodically
                    if self.status.processed_books % 5 == 0 or self.status.processed_books == total_books:
                        await self.db.update_page_calc_progress(
                            job_id=job_id,
                            processed=self.status.processed_books,
                            updated=self.status.updated_books,
                            removed=self.status.removed_books,
                            errors=self.status.error_count,
                            current_book=self.status.current_book,
                        )
                    if self.status.processed_books % 50 == 0:
                        try:
                            await self.db.checkpoint("PASSIVE")
                        except Exception:
                            pass
                        from app.services.memory import release_memory
                        release_memory()
                finally:
                    queue.task_done()

        try:
            num_workers = min(settings.sync_concurrency, total_books) if total_books > 0 else 1
            tasks = [asyncio.create_task(worker()) for _ in range(num_workers)]
            self._worker_tasks = tasks
            await asyncio.gather(*tasks, return_exceptions=True)

            final_status = "stopped" if self._stop_requested else "completed"
            self.status.status = final_status
            self.status.current_book = "Calculation stopped" if self._stop_requested else "Calculation completed"
            if job_id:
                await self.db.update_page_calc_progress(
                    job_id=job_id,
                    processed=self.status.processed_books,
                    updated=self.status.updated_books,
                    removed=self.status.removed_books,
                    errors=self.status.error_count,
                    current_book=self.status.current_book,
                    status=final_status,
                )
            self.log(f"Page calculation job finished with status '{final_status}': {self.status.updated_books}/{total_books} updated, {self.status.error_count} errors, {self.status.removed_books} removed")
        except asyncio.CancelledError:
            self.log(f"Page calculation job {job_id} was cancelled", level="warning")
            self.status.status = "stopped"
            self.status.current_book = "Calculation stopped"
            if job_id:
                await self.db.update_page_calc_progress(
                    job_id=job_id,
                    processed=self.status.processed_books,
                    updated=self.status.updated_books,
                    removed=self.status.removed_books,
                    errors=self.status.error_count,
                    current_book="Calculation stopped",
                    status="stopped",
                )
        except Exception as e:
            self.log(f"Page calculation job failed: {e}", level="error")
            self.status.status = "failed"
            self.status.error_message = str(e)
            self.status.current_book = f"Error: {e}"
            if job_id:
                await self.db.update_page_calc_progress(
                    job_id=job_id,
                    processed=self.status.processed_books,
                    updated=self.status.updated_books,
                    removed=self.status.removed_books,
                    errors=self.status.error_count,
                    status="failed",
                    error_message=str(e),
                    current_book=f"Error: {e}",
                )
        finally:
            self.status.is_running = False
            self._stop_requested = False
            self._worker_tasks.clear()
            try:
                self.status.recent_errors = await self.db.get_page_calc_errors(limit=50)
            except Exception:
                pass
            try:
                await self.db.checkpoint("TRUNCATE")
            except Exception:
                pass
            from app.services.memory import release_memory
            release_memory()

        return self.status
