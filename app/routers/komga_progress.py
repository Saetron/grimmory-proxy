from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional
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
from app.models.komga import ReadProgressDto, ReadProgressUpdateDto
from app.services.auth import AuthService
from app.services.mapper import KomgaMapper

logger = logging.getLogger("grimmory_proxy.komga_progress")


def get_progress_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Reading Progress"])

    # ---------------- Standard Komga Read Progress ----------------

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
    async def update_read_progress(
        book_id: int,
        update_dto: ReadProgressUpdateDto,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        record = await db.get_book_by_id(book_id)
        page_count = record.get("page_count", 1) if record else 1
        page_num = update_dto.page if update_dto.page is not None else (page_count if update_dto.completed else 1)
        percentage = min(100.0, max(0.0, (page_num / max(1, page_count)) * 100.0))

        if update_dto.completed:
            percentage = 100.0
            page_num = page_count

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        date_finished = now_str if update_dto.completed else None

        # Save to proxy database immediately for instant responsiveness on home screen
        await db.upsert_read_progress(
            user_id=user.user_id,
            book_id=book_id,
            page=page_num,
            completed=bool(update_dto.completed),
            read_date=now_str,
        )

        req = GrimmoryReadProgressRequest(
            bookId=book_id,
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=percentage),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=percentage),
            epubProgress=GrimmoryEpubProgress(percentage=percentage),
            dateFinished=date_finished,
        )

        success = await grimmory_client.update_read_progress(req, token=user.token)
        if not success:
            logger.warning(f"Failed to update progress on Grimmory for book {book_id} and user {user.username}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/books/{book_id}/read-progress", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_read_progress(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        await db.delete_read_progress(user_id=user.user_id, book_id=book_id)
        await db.delete_r2_progression(user_id=user.user_id, book_id=book_id)
        success = await grimmory_client.reset_read_progress([book_id], token=user.token)
        if not success:
            logger.warning(f"Failed to reset progress on Grimmory for book {book_id} and user {user.username}")

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
                if percentage == 0.0:
                    percentage = min(100.0, max(0.0, (page_num / max(1, page_count)) * 100.0))
            except (ValueError, TypeError):
                page_num = max(1, min(page_count, round((percentage / 100.0) * page_count)))
        elif prog_val is not None:
            page_num = max(1, min(page_count, round((percentage / 100.0) * page_count)))
        else:
            page_num = 1

        completed = (
            percentage >= 99.0
            or bool(payload.get("completed"))
            or bool(locations.get("completed"))
        )
        if completed:
            percentage = 100.0
            page_num = page_count

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
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=percentage),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=percentage),
            epubProgress=GrimmoryEpubProgress(percentage=percentage, href=href, cfi=cfi),
            dateFinished=modified if completed else None,
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
        success = await grimmory_client.reset_read_progress([book_id], token=user.token)
        if not success:
            logger.warning(f"Failed to reset progress on Grimmory for book {book_id} and user {user.username}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
