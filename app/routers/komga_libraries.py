import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import Database
from app.models.internal import UserSession
from app.models.komga import LibraryDto
from app.services.auth import AuthService
from app.services.mapper import KomgaMapper

logger = logging.getLogger("komic.komga_libraries")


def get_libraries_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Libraries"])

    @router.get("/api/v1/libraries", response_model=List[LibraryDto])
    async def get_all_libraries(user: UserSession = Depends(AuthService.require_user)) -> List[LibraryDto]:
        records = await db.get_libraries()
        # Filter if user is non-admin with specific library restrictions
        if not user.is_admin and user.assigned_library_ids:
            records = [r for r in records if r["id"] in user.assigned_library_ids]
        return [KomgaMapper.to_library_dto(r) for r in records]

    @router.get("/api/v1/libraries/{library_id}", response_model=LibraryDto)
    async def get_library_by_id(
        library_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> LibraryDto:
        if not user.is_admin and user.assigned_library_ids and library_id not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library not found")

        record = await db.get_library(library_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library not found")
        return KomgaMapper.to_library_dto(record)

    return router
