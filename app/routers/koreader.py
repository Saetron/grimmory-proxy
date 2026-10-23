import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.grimmory import (
    GrimmoryCbxProgress,
    GrimmoryEpubProgress,
    GrimmoryPdfProgress,
    GrimmoryReadProgressRequest,
)
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.koreader_utils import format_koreader_progress

logger = logging.getLogger("grimmory_proxy.koreader")


class KoreaderProgressDto(BaseModel):
    document: str
    percentage: float = 0.0
    progress: str = "1"
    device: Optional[str] = "grimmory-proxy"
    deviceId: Optional[str] = Field(default="grimmory-proxy", alias="device_id")

    model_config = ConfigDict(populate_by_name=True)


def get_koreader_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["KOReader Sync"])

    # ---------------- KOReader Auth Endpoints ----------------

    @router.get("/koreader/users/auth")
    @router.get("/api/koreader/users/auth")
    async def koreader_auth(
        user: UserSession = Depends(AuthService.require_user),
    ) -> Dict[str, str]:
        return {"authorized": "OK"}

    @router.post("/koreader/users/create", status_code=status.HTTP_403_FORBIDDEN)
    @router.post("/api/koreader/users/create", status_code=status.HTTP_403_FORBIDDEN)
    async def koreader_register_user() -> Dict[str, str]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User creation via KOReader sync endpoint is disabled. Use Grimmory or proxy admin.",
        )

    # ---------------- KOReader Progress Sync Endpoints ----------------

    @router.get("/koreader/syncs/progress/{book_hash}", response_model=KoreaderProgressDto)
    @router.get("/api/koreader/syncs/progress/{book_hash}", response_model=KoreaderProgressDto)
    async def get_koreader_progress_endpoint(
        book_hash: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> KoreaderProgressDto:
        book = await db.find_book_for_koreader(book_hash)
        if not book:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        book_id = book["id"]
        page_count = book.get("page_count", 0) or 1
        b_type = book.get("book_type") or "EPUB"

        # Check local read progress first
        prog = await db.get_book_read_progress(user.user_id, book_id)
        if not prog:
            # Check upstream Grimmory KOReader progress as fallback
            try:
                upstream = await grimmory_client.get_koreader_progress(book_hash, token=user.token)
                if upstream:
                    return KoreaderProgressDto(
                        document=book_hash,
                        percentage=float(upstream.get("percentage") or 0.0),
                        progress=str(upstream.get("progress") or "1"),
                        device=upstream.get("device"),
                        deviceId=upstream.get("deviceId") or upstream.get("device_id"),
                    )
            except Exception:
                pass

            return KoreaderProgressDto(
                document=book_hash,
                percentage=0.0,
                progress="1",
                device="grimmory-proxy",
                deviceId="grimmory-proxy",
            )

        page_num = max(1, prog.get("page") or 1)
        completed = bool(prog.get("completed"))
        if completed:
            percentage = 1.0
        else:
            percentage = min(1.0, max(0.0, page_num / page_count)) if page_count > 0 else 0.0

        progress_str = format_koreader_progress(b_type, page_num, page_count, percentage)

        return KoreaderProgressDto(
            document=book_hash,
            percentage=percentage,
            progress=progress_str,
            device="grimmory-proxy",
            deviceId="grimmory-proxy",
        )

    @router.put("/koreader/syncs/progress", status_code=status.HTTP_200_OK)
    @router.put("/api/koreader/syncs/progress", status_code=status.HTTP_200_OK)
    async def update_koreader_progress_endpoint(
        payload: Dict[str, Any] = Body(...),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Dict[str, Any]:
        doc_hash = str(payload.get("document") or "").strip()
        if not doc_hash:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing document hash")

        book = await db.find_book_for_koreader(doc_hash)
        if not book:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        book_id = book["id"]
        page_count = book.get("page_count", 0) or 1
        b_type = (book.get("book_type") or "EPUB").upper()

        raw_perc = payload.get("percentage", 0.0)
        try:
            perc_val = float(raw_perc)
            # Normalize to 0.0 - 1.0 scale if sent as 0-100
            perc_0_to_1 = perc_val / 100.0 if perc_val > 1.0 else max(0.0, perc_val)
            perc_0_to_100 = perc_0_to_1 * 100.0
        except (ValueError, TypeError):
            perc_0_to_1 = 0.0
            perc_0_to_100 = 0.0

        raw_prog = str(payload.get("progress") or "1").strip()
        page_num = 1
        if raw_prog.isdigit():
            page_num = max(1, int(raw_prog))
        elif perc_0_to_100 > 0 and page_count > 0:
            page_num = max(1, min(page_count, round((perc_0_to_100 / 100.0) * page_count)))

        completed = perc_0_to_100 >= 95.0 or (page_count > 0 and page_num >= page_count)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Update SQLite read_progress
        await db.upsert_read_progress(
            user_id=user.user_id,
            book_id=book_id,
            page=page_num,
            completed=completed,
            read_date=now_str,
        )

        # 2. Update Grimmory main progress
        req = GrimmoryReadProgressRequest(
            bookId=book_id,
            cbxProgress=GrimmoryCbxProgress(page=page_num, percentage=perc_0_to_100, lastRead=now_str),
            pdfProgress=GrimmoryPdfProgress(page=page_num, percentage=perc_0_to_100, lastRead=now_str),
            epubProgress=GrimmoryEpubProgress(percentage=perc_0_to_100, lastRead=now_str),
            dateFinished=now_str if completed else None,
            readStatus="READ" if completed else "READING",
            lastRead=now_str,
            progressValid=True,
        )

        async def _forward_upstream():
            try:
                await grimmory_client.update_read_progress(req, token=user.token)
                await grimmory_client.update_read_status(
                    [book_id], "READ" if completed else "READING", token=user.token
                )
            except Exception as e:
                logger.warning(f"Failed to update Grimmory progress from KOReader: {e}")
            try:
                await grimmory_client.update_koreader_progress(
                    doc_hash,
                    raw_prog,
                    perc_0_to_1,
                    token=user.token,
                    device=payload.get("device", "KOReader"),
                    device_id=payload.get("device_id") or payload.get("deviceId", "KOReader"),
                )
            except Exception as e:
                logger.debug(f"Failed to forward KOReader progress upstream: {e}")

        asyncio.create_task(_forward_upstream())

        return {
            "document": doc_hash,
            "percentage": perc_0_to_1,
            "progress": raw_prog,
            "status": "updated",
        }

    return router
