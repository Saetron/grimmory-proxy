import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
import aiosqlite

from app.database import Database
from app.models.internal import UserSession
from app.models.komga import AuthorDto, PageableDto, build_pageable
from app.services.auth import AuthService

logger = logging.getLogger("grimmory_proxy.komga_authors")


def get_authors_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Authors"])

    @router.get("/api/v1/authors", response_model=PageableDto[AuthorDto])
    @router.get("/api/v2/authors", response_model=PageableDto[AuthorDto])
    async def get_all_authors(
        search: Optional[str] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[AuthorDto]:
        async with db.get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if search:
                cursor = await conn.execute(
                    "SELECT COUNT(*) as total FROM authors WHERE name LIKE ?",
                    (f"%{search}%",),
                )
                total = (await cursor.fetchone())["total"]
                cur = await conn.execute(
                    "SELECT name FROM authors WHERE name LIKE ? ORDER BY name ASC LIMIT ? OFFSET ?",
                    (f"%{search}%", size, page * size),
                )
            else:
                cursor = await conn.execute("SELECT COUNT(*) as total FROM authors")
                total = (await cursor.fetchone())["total"]
                cur = await conn.execute(
                    "SELECT name FROM authors ORDER BY name ASC LIMIT ? OFFSET ?",
                    (size, page * size),
                )
            rows = await cur.fetchall()

        authors = [AuthorDto(name=r["name"], role="writer") for r in rows]
        return build_pageable(authors, page, size, total)

    @router.get("/api/v1/authors/names", response_model=List[str])
    async def get_author_names(
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        async with db.get_connection() as conn:
            cur = await conn.execute("SELECT name FROM authors ORDER BY name ASC LIMIT 1000")
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    return router
