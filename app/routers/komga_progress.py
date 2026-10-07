from datetime import datetime, timezone
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.grimmory import GrimmoryCbxProgress, GrimmoryPdfProgress, GrimmoryReadProgressRequest
from app.models.internal import UserSession
from app.models.komga import ReadProgressDto, ReadProgressUpdateDto
from app.services.auth import AuthService
from app.services.mapper import KomgaMapper

logger = logging.getLogger("komic.komga_progress")


def get_progress_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Reading Progress"])

    @router.get("/api/v1/books/{book_id}/read-progress", response_model=ReadProgressDto)
    async def get_read_progress(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> ReadProgressDto:
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

        now_str = datetime.now(timezone.utc).isoformat()
        date_finished = now_str if update_dto.completed else None

        req = GrimmoryReadProgressRequest(
            bookId=book_id,
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=percentage),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=percentage),
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
        success = await grimmory_client.reset_read_progress([book_id], token=user.token)
        if not success:
            logger.warning(f"Failed to reset progress on Grimmory for book {book_id} and user {user.username}")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
