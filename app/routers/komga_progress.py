import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.grimmory import (
    GrimmoryCbxProgress,
    GrimmoryEpubProgress,
    GrimmoryPdfProgress,
    GrimmoryReadProgressRequest,
)
from app.models.internal import UserSession
from app.models.komga import (
    ReadProgressDto,
    ReadProgressUpdateDto,
    TachiyomiReadProgressV2Dto,
    TachiyomiReadProgressUpdateV2Dto,
)
from app.services.auth import AuthService
from app.services.mapper import KomgaMapper

logger = logging.getLogger("grimmory_proxy.komga_progress")


async def _sync_series_books_read_to_grimmory(token: str, books: List[Dict[str, Any]], date_finished: str) -> None:
    tasks = []
    for b in books:
        p_count = b.get("page_count", 0) or 1
        req = GrimmoryReadProgressRequest(
            bookId=b["id"],
            cbxProgress=GrimmoryCbxProgress(page=p_count, percentage=100.0),
            pdfProgress=GrimmoryPdfProgress(page=p_count, percentage=100.0),
            epubProgress=GrimmoryEpubProgress(percentage=100.0),
            dateFinished=date_finished,
            readStatus="READ",
        )
        tasks.append(grimmory_client.update_read_progress(req, token=token))
    if tasks:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.debug(f"Background series read sync to Grimmory encountered error: {e}")


def get_progress_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Reading Progress"])

    # ---------------- Standard Komga Book Read Progress ----------------

    @router.get("/api/v1/books/{book_id}/read-progress", response_model=ReadProgressDto)
    async def get_read_progress(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> ReadProgressDto:
        # Check local database read progress first
        local_prog = await db.get_book_read_progress(user.user_id, book_id)
        if local_prog:
            return ReadProgressDto(
                page=local_prog["page"],
                completed=bool(local_prog["completed"]),
                readDate=local_prog["read_date"],
            )

        # Fetch book from Grimmory with this user's authentication context
        try:
            grimm_book = await grimmory_client.get_book(book_id, token=user.token)
            record = await db.get_book_by_id(book_id) or {"id": book_id}
            record["raw_json"] = grimm_book
            dto = KomgaMapper.to_book_dto(record)
            if dto.readProgress:
                return dto.readProgress
        except Exception as e:
            logger.debug(f"Error fetching live progress for book {book_id}: {e}")

        # Return default unread progress
        return ReadProgressDto(page=1, completed=False)

    @router.patch("/api/v1/books/{book_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    @router.post("/api/v1/books/{book_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    @router.put("/api/v1/books/{book_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    async def update_read_progress(
        book_id: int,
        update_dto: Optional[ReadProgressUpdateDto] = Body(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        dto = update_dto if update_dto is not None else ReadProgressUpdateDto(completed=True)
        record = await db.get_book_by_id(book_id)
        page_count = record.get("page_count", 0) if record else 0
        if page_count <= 0 and record and record.get("raw_json"):
            try:
                raw_data = json.loads(record["raw_json"]) if isinstance(record["raw_json"], str) else record["raw_json"]
                if isinstance(raw_data, dict):
                    page_count = raw_data.get("metadata", {}).get("pageCount") or 0
            except Exception:
                pass
        if page_count <= 0:
            page_count = 1

        # Check if marking unread
        if dto.completed is False and (dto.page is None or dto.page <= 0):
            await db.delete_read_progress(user_id=user.user_id, book_id=book_id)
            await db.delete_r2_progression(user_id=user.user_id, book_id=book_id)
            try:
                await grimmory_client.reset_read_progress([book_id], token=user.token)
            except Exception as e:
                logger.warning(f"Failed to reset progress on Grimmory for book {book_id}: {e}")
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # Determine completion and page position according to Komga specification
        if dto.completed is True:
            completed = True
            page_num = dto.page if dto.page is not None else page_count
            percentage = 100.0
        else:
            page_num = dto.page if dto.page is not None else 1
            if page_count > 1:
                percentage = min(100.0, max(0.0, (page_num / page_count) * 100.0))
            else:
                percentage = 0.0

            if dto.completed is None:
                # Per Komga spec: completed can be omitted, set according to page passed and total pages
                completed = (page_count > 1 and page_num >= page_count) or (page_count > 1 and percentage >= 99.0)
            else:
                completed = bool(dto.completed)

        if completed:
            percentage = 100.0
            page_num = page_count if page_count > 1 else page_num

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        date_finished = now_str if completed else None

        # Save to proxy database immediately for instant responsiveness on home screen
        await db.upsert_read_progress(
            user_id=user.user_id,
            book_id=book_id,
            page=page_num,
            completed=completed,
            read_date=now_str,
        )

        req = GrimmoryReadProgressRequest(
            bookId=book_id,
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=percentage, lastRead=now_str),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=percentage, lastRead=now_str),
            epubProgress=GrimmoryEpubProgress(percentage=percentage, lastRead=now_str),
            dateFinished=date_finished,
            readStatus="READ" if completed else "READING",
            lastRead=now_str,
        )

        try:
            success = await grimmory_client.update_read_progress(req, token=user.token)
            if not success:
                logger.warning(f"Failed to update progress on Grimmory for book {book_id} and user {user.username}")
        except Exception as e:
            logger.warning(f"Error calling Grimmory update_read_progress for book {book_id}: {e}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/books/{book_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_read_progress(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        await db.delete_read_progress(user_id=user.user_id, book_id=book_id)
        await db.delete_r2_progression(user_id=user.user_id, book_id=book_id)
        try:
            success = await grimmory_client.reset_read_progress([book_id], token=user.token)
            if not success:
                logger.warning(f"Failed to reset progress on Grimmory for book {book_id} and user {user.username}")
        except Exception as e:
            logger.warning(f"Error resetting progress on Grimmory for book {book_id}: {e}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---------------- Series Read Progress Endpoints ----------------

    @router.post("/api/v1/series/{series_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    @router.put("/api/v1/series/{series_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    @router.patch("/api/v1/series/{series_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_series_read_progress(
        series_id: str,
        update_dto: Optional[ReadProgressUpdateDto] = Body(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        """
        Mark all books for a series as read (or unread if completed=False).
        """
        series = await db.get_series_by_id(series_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        books = await db.get_books_by_series(series["id"])
        if not books:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        if update_dto and update_dto.completed is False:
            # Explicit unread requested via PUT/PATCH
            book_ids = [b["id"] for b in books]
            await db.delete_read_progress_batch(user.user_id, book_ids)
            await db.delete_r2_progression_batch(user.user_id, book_ids)
            asyncio.create_task(grimmory_client.reset_read_progress(book_ids, token=user.token))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # Mark all books in series as read
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        progress_records = []
        for b in books:
            p_count = b.get("page_count", 0) or 1
            progress_records.append({
                "user_id": user.user_id,
                "book_id": b["id"],
                "page": p_count,
                "completed": True,
                "read_date": now_str,
            })

        await db.upsert_read_progress_batch(progress_records)

        # Concurrently sync all books in series to Grimmory in background
        asyncio.create_task(_sync_series_books_read_to_grimmory(user.token, books, now_str))

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/series/{series_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_series_as_unread(
        series_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        """
        Mark all books in a series as unread.
        """
        series = await db.get_series_by_id(series_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        books = await db.get_books_by_series(series["id"])
        if not books:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        book_ids = [b["id"] for b in books]
        await db.delete_read_progress_batch(user.user_id, book_ids)
        await db.delete_r2_progression_batch(user.user_id, book_ids)

        asyncio.create_task(grimmory_client.reset_read_progress(book_ids, token=user.token))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---------------- Mihon / Tachiyomi Series Read Progress ----------------

    @router.get("/api/v2/series/{series_id}/read-progress/tachiyomi", response_model=TachiyomiReadProgressV2Dto)
    @router.get("/api/v1/series/{series_id}/read-progress/tachiyomi", response_model=TachiyomiReadProgressV2Dto)
    async def get_tachiyomi_series_progress(
        series_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> TachiyomiReadProgressV2Dto:
        series = await db.get_series_by_id(series_id, user_id=user.user_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id, user_id=user.user_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        books = await db.get_books_by_series(series["id"])
        books_count = len(books)
        read_count = series.get("books_read_count", 0) or 0
        in_prog_count = series.get("books_in_progress_count", 0) or 0
        unread_count = max(0, books_count - read_count - in_prog_count)

        numbers = [float(b.get("number", 0)) for b in books]
        max_num = max(numbers) if numbers else 0.0

        return TachiyomiReadProgressV2Dto(
            booksCount=books_count,
            booksInProgressCount=in_prog_count,
            booksReadCount=read_count,
            booksUnreadCount=unread_count,
            lastReadContinuousNumberSort=float(read_count),
            maxNumberSort=max_num,
        )

    @router.put("/api/v2/series/{series_id}/read-progress/tachiyomi", status_code=status.HTTP_204_NO_CONTENT)
    @router.put("/api/v1/series/{series_id}/read-progress/tachiyomi", status_code=status.HTTP_204_NO_CONTENT)
    async def update_tachiyomi_series_progress(
        series_id: str,
        update_dto: TachiyomiReadProgressUpdateV2Dto,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        series = await db.get_series_by_id(series_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        books = await db.get_books_by_series(series["id"])
        if not books:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        read_records = []
        read_books = []
        unread_ids = []

        for b in books:
            b_num = float(b.get("number", 0))
            if b_num <= update_dto.lastBookNumberSortRead:
                p_count = b.get("page_count", 0) or 1
                read_records.append({
                    "user_id": user.user_id,
                    "book_id": b["id"],
                    "page": p_count,
                    "completed": True,
                    "read_date": now_str,
                })
                read_books.append(b)
            else:
                unread_ids.append(b["id"])

        if read_records:
            await db.upsert_read_progress_batch(read_records)
            asyncio.create_task(_sync_series_books_read_to_grimmory(user.token, read_books, now_str))

        if unread_ids:
            await db.delete_read_progress_batch(user.user_id, unread_ids)
            await db.delete_r2_progression_batch(user.user_id, unread_ids)
            asyncio.create_task(grimmory_client.reset_read_progress(unread_ids, token=user.token))

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---------------- Readium (R2) Progression Endpoints ----------------

    @router.get("/api/v1/books/{book_id}/progression")
    async def get_book_progression(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        """
        Retrieves Readium (R2) progression JSON for a book.
        Returns 200 with stored JSON, or 204 No Content if not found.
        """
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        prog = await db.get_r2_progression(user.user_id, book_id)
        if prog:
            return Response(
                content=json.dumps(prog),
                media_type="application/json",
                status_code=status.HTTP_200_OK,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.put("/api/v1/books/{book_id}/progression", status_code=status.HTTP_204_NO_CONTENT)
    async def update_book_progression(
        book_id: int,
        payload: Dict[str, Any] = Body(...),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        """
        Saves Readium (R2) progression JSON from reader applications (Komic, etc.)
        and synchronizes progress back to Grimmory.
        """
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        page_count = record.get("page_count", 1) or 1
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Extract Readium locator information
        locator = payload.get("locator") if isinstance(payload.get("locator"), dict) else payload
        locations = locator.get("locations") if isinstance(locator.get("locations"), dict) else {}

        href = locator.get("href")
        cfi = locations.get("cfi") or (locations.get("fragment") if isinstance(locations.get("fragment"), str) else None)

        # Determine progression percentage and page position
        total_prog = locations.get("totalProgression")
        prog_val = locations.get("progression") if total_prog is None else total_prog
        if prog_val is None:
            prog_val = payload.get("progression")

        if prog_val is not None:
            try:
                prog_f = float(prog_val)
                percentage = min(100.0, max(0.0, (prog_f * 100.0) if prog_f <= 1.0 else prog_f))
            except (ValueError, TypeError):
                percentage = 0.0
        else:
            percentage = 0.0

        pos_val = locations.get("position") or locations.get("page") or payload.get("page")
        if pos_val is not None:
            try:
                page_num = max(1, int(pos_val))
                if percentage == 0.0 and page_count > 1:
                    percentage = min(100.0, max(0.0, (page_num / page_count) * 100.0))
            except (ValueError, TypeError):
                page_num = max(1, min(page_count, round((percentage / 100.0) * page_count))) if page_count > 1 else 1
        elif prog_val is not None:
            page_num = max(1, min(page_count, round((percentage / 100.0) * page_count))) if page_count > 1 else 1
        else:
            page_num = 1

        is_explicit_completed = bool(payload.get("completed")) or bool(locations.get("completed"))
        if is_explicit_completed:
            completed = True
        elif page_count > 1:
            completed = percentage >= 99.0 or page_num >= page_count
        else:
            completed = percentage >= 99.0 and prog_val is not None

        if completed:
            percentage = 100.0
            page_num = page_count if page_count > 1 else page_num

        modified = payload.get("modified") or now_str
        payload["modified"] = modified

        # 1. Store R2 progression JSON for seamless round-trip
        await db.upsert_r2_progression(
            user_id=user.user_id,
            book_id=book_id,
            progression_json=json.dumps(payload),
        )

        # 2. Store in local read_progress table for home screens / on-deck
        await db.upsert_read_progress(
            user_id=user.user_id,
            book_id=book_id,
            page=page_num,
            completed=completed,
            read_date=modified,
        )

        # 3. Synchronize to Grimmory backend
        req = GrimmoryReadProgressRequest(
            bookId=book_id,
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=percentage, lastRead=modified),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=percentage, lastRead=modified),
            epubProgress=GrimmoryEpubProgress(percentage=percentage, href=href, cfi=cfi, lastRead=modified),
            dateFinished=modified if completed else None,
            readStatus="READ" if completed else "READING",
            lastRead=modified,
        )
        try:
            success = await grimmory_client.update_read_progress(req, token=user.token)
            if not success:
                logger.warning(f"Failed to update progress on Grimmory for book {book_id} and user {user.username}")
        except Exception as e:
            logger.warning(f"Error calling Grimmory update_read_progress for book {book_id}: {e}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/books/{book_id}/progression", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_book_progression(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        await db.delete_read_progress(user_id=user.user_id, book_id=book_id)
        await db.delete_r2_progression(user_id=user.user_id, book_id=book_id)
        try:
            success = await grimmory_client.reset_read_progress([book_id], token=user.token)
            if not success:
                logger.warning(f"Failed to reset progress on Grimmory for book {book_id} and user {user.username}")
        except Exception as e:
            logger.warning(f"Error resetting progress on Grimmory for book {book_id}: {e}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
