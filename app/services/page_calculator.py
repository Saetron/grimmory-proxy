import asyncio
import io
import logging
import re
import zipfile
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
from pypdf import PdfReader

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.models.internal import PageCalcStatus
from app.models.komga import PageDto

logger = logging.getLogger("komic.page_calculator")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}


class PageCalculator:
    def __init__(self, db: Database):
        self.db = db
        self.status = PageCalcStatus()
        self._stop_requested = False
        self._lock = asyncio.Lock()

    def request_stop(self) -> None:
        self._stop_requested = True
        logger.info("Page calculation job stop requested")

    @staticmethod
    def count_epub_pages(epub_bytes: bytes, chars_per_page: int = 1024) -> Tuple[int, List[PageDto]]:
        """
        Calculate page count for EPUB novel using Calibre's ADE algorithm.
        Standard ADE heuristic counts 1,024 uncompressed characters per page.
        """
        total_chars = 0
        try:
            with zipfile.ZipFile(io.BytesIO(epub_bytes)) as z:
                # Find all HTML/XHTML files in zip
                for name in z.namelist():
                    if name.lower().endswith((".html", ".xhtml", ".htm")):
                        try:
                            raw_content = z.read(name).decode("utf-8", errors="ignore")
                            # Remove script and style tags completely
                            cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_content, flags=re.DOTALL | re.IGNORECASE)
                            # Remove remaining HTML tags
                            text = re.sub(r"<[^>]+>", " ", cleaned)
                            # Unescape HTML entities
                            text = unescape(text)
                            # Collapse whitespace
                            text = re.sub(r"\s+", " ", text).strip()
                            total_chars += len(text)
                        except Exception as e:
                            logger.debug(f"Error parsing chapter {name}: {e}")
        except Exception as e:
            logger.warning(f"Error reading EPUB archive: {e}")

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
        page_dtos: List[PageDto] = []
        try:
            with zipfile.ZipFile(io.BytesIO(cbz_bytes)) as z:
                # Filter out OS metadata files like __MACOSX
                image_entries = sorted([
                    n for n in z.namelist()
                    if not n.startswith("__MACOSX/") and any(n.lower().endswith(ext) for ext in IMAGE_EXTENSIONS)
                ])

                for idx, entry_name in enumerate(image_entries, start=1):
                    ext = entry_name.lower().split(".")[-1]
                    mime = f"image/{'jpeg' if ext in ['jpg', 'jpeg'] else ext}"
                    width, height = 1200, 1600
                    size_bytes = 0

                    # Try reading dimensions for page metadata
                    try:
                        info = z.getinfo(entry_name)
                        size_bytes = info.file_size
                        img_data = z.read(entry_name)
                        with Image.open(io.BytesIO(img_data)) as img:
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
        except Exception as e:
            logger.warning(f"Error reading CBZ archive: {e}")

        pages_count = len(page_dtos) if page_dtos else 1
        if not page_dtos:
            page_dtos = [PageDto(number=1, fileName="page-001.jpg", mediaType="image/jpeg", width=1200, height=1600)]
        return pages_count, page_dtos

    @staticmethod
    def count_pdf_pages(pdf_bytes: bytes) -> Tuple[int, List[PageDto]]:
        """Calculate page count for PDF."""
        pages_count = 1
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_count = len(reader.pages)
        except Exception as e:
            logger.warning(f"Error reading PDF: {e}")

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

    async def calculate_and_save_book(self, book: Dict[str, Any]) -> bool:
        """
        Calculates pages for a single book and writes the result to both Grimmory and SQLite.
        """
        book_id = book["id"]
        book_name = book.get("name", f"Book {book_id}")
        book_type = book.get("book_type", "EPUB")

        try:
            page_count, page_dtos = await self.inspect_book_pages(book_id, book_type)
            logger.info(f"Calculated {page_count} pages for book {book_id} ('{book_name}') [{book_type}]")

            # 1. Update Grimmory metadata
            await grimmory_client.update_book_metadata(book_id, {"pageCount": page_count})

            # 2. Update local SQLite DB
            await self.db.update_book_page_count(book_id, page_count)

            # 3. Cache page details
            page_records = [p.model_dump() for p in page_dtos]
            await self.db.upsert_book_pages(book_id, page_records)

            return True
        except Exception as e:
            logger.error(f"Error calculating pages for book {book_id} ('{book_name}'): {e}")
            return False

    async def run_calculation_job(self, missing_only: bool = True) -> PageCalcStatus:
        """
        Runs the background page calculation loop across books using SYNC_CONCURRENCY workers.
        """
        async with self._lock:
            if self.status.is_running:
                logger.warning("A page calculation job is already in progress")
                return self.status

            self._stop_requested = False
            self.status.is_running = True
            self.status.error_message = None

            # Fetch books to process
            if missing_only:
                books = await self.db.get_books_missing_pages()
            else:
                books = await self.db.get_all_books_for_page_calc()

            total_books = len(books)
            job_id = await self.db.create_page_calc_job(total_books)

            self.status.job_id = job_id
            self.status.total_books = total_books
            self.status.processed_books = 0
            self.status.updated_books = 0
            self.status.error_count = 0

            logger.info(f"Starting page calculation job {job_id} for {total_books} books (concurrency: {settings.sync_concurrency})")

        sem = asyncio.Semaphore(settings.sync_concurrency)

        async def worker(book: Dict[str, Any]):
            if self._stop_requested:
                return

            async with sem:
                if self._stop_requested:
                    return

                self.status.current_book = book.get("name", f"Book {book['id']}")
                success = await self.calculate_and_save_book(book)

                self.status.processed_books += 1
                if success:
                    self.status.updated_books += 1
                else:
                    self.status.error_count += 1

                # Update progress periodically
                if self.status.processed_books % 5 == 0 or self.status.processed_books == total_books:
                    await self.db.update_page_calc_progress(
                        job_id=job_id,
                        processed=self.status.processed_books,
                        updated=self.status.updated_books,
                        errors=self.status.error_count,
                        current_book=self.status.current_book,
                    )

        try:
            tasks = [asyncio.create_task(worker(b)) for b in books]
            await asyncio.gather(*tasks, return_exceptions=True)

            final_status = "stopped" if self._stop_requested else "completed"
            self.status.is_running = False
            await self.db.update_page_calc_progress(
                job_id=job_id,
                processed=self.status.processed_books,
                updated=self.status.updated_books,
                errors=self.status.error_count,
                current_book="",
                status=final_status,
            )
            logger.info(f"Page calculation job finished with status '{final_status}': {self.status.updated_books}/{total_books} updated")
        except Exception as e:
            logger.error(f"Page calculation job failed: {e}")
            self.status.is_running = False
            self.status.error_message = str(e)
            await self.db.update_page_calc_progress(
                job_id=job_id,
                processed=self.status.processed_books,
                updated=self.status.updated_books,
                errors=self.status.error_count,
                status="failed",
                error_message=str(e),
            )

        return self.status
