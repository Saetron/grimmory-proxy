import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request

from app.database import Database
from app.models.internal import UserSession
from app.models.komga import AuthorDto, PageableDto, build_pageable
from app.services.auth import AuthService
from app.services.filter_utils import (
    is_unpaged_request,
    parse_library_ids_param,
    resolve_effective_library_ids,
)

logger = logging.getLogger("grimmory_proxy.komga_authors")


def get_authors_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Authors"])

    # ---------------- Authors List ----------------

    @router.get("/api/v1/authors", response_model=List[AuthorDto])
    async def get_authors_v1(
        search: Optional[str] = Query(None),
        library_id: Optional[str] = Query(None),
        collection_id: Optional[str] = Query(None),
        series_id: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[AuthorDto]:
        parsed_libs = parse_library_ids_param(library_id=library_id)
        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []

        series_ids = [series_id] if series_id else None
        authors, _ = await db.get_authors_list(
            search=search,
            library_ids=effective_libs,
            series_ids=series_ids,
            unpaged=True,
        )
        return [AuthorDto(name=a["name"], role=a.get("role", "writer")) for a in authors]

    @router.get("/api/v2/authors", response_model=PageableDto[AuthorDto])
    async def get_authors_v2(
        request: Request,
        search: Optional[str] = Query(None),
        role: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[List[str]] = Query(None),
        series_id: Optional[List[str]] = Query(None),
        readlist_id: Optional[List[str]] = Query(None),
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[AuthorDto]:
        is_unpaged = is_unpaged_request(request, unpaged)
        parsed_libs = parse_library_ids_param(request, library_id)
        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0, unpaged=is_unpaged)

        authors, total = await db.get_authors_list(
            search=search,
            role=role,
            library_ids=effective_libs,
            series_ids=series_id,
            offset=0 if is_unpaged else page * size,
            limit=size,
            unpaged=is_unpaged,
        )
        content = [AuthorDto(name=a["name"], role=a.get("role", "writer")) for a in authors]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    # ---------------- Authors Names ----------------

    @router.get("/api/v1/authors/names", response_model=List[str])
    async def get_author_names_v1(
        search: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        effective_libs = resolve_effective_library_ids(user, [])
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        names, _ = await db.get_author_names_list(search=search, library_ids=effective_libs, unpaged=True)
        return names

    @router.get("/api/v2/authors/names", response_model=PageableDto[str])
    async def get_author_names_v2(
        request: Request,
        search: Optional[str] = Query(None),
        role: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[List[str]] = Query(None),
        series_id: Optional[List[str]] = Query(None),
        readlist_id: Optional[List[str]] = Query(None),
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[str]:
        is_unpaged = is_unpaged_request(request, unpaged)
        parsed_libs = parse_library_ids_param(request, library_id)
        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0, unpaged=is_unpaged)

        names, total = await db.get_author_names_list(
            search=search,
            role=role,
            library_ids=effective_libs,
            series_ids=series_id,
            offset=0 if is_unpaged else page * size,
            limit=size,
            unpaged=is_unpaged,
        )
        return build_pageable(names, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    # ---------------- Authors Roles ----------------

    @router.get("/api/v1/authors/roles", response_model=List[str])
    async def get_author_roles_v1(
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        return await db.get_author_roles()

    @router.get("/api/v2/authors/roles", response_model=PageableDto[str])
    async def get_author_roles_v2(
        request: Request,
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[str]:
        is_unpaged = is_unpaged_request(request, unpaged)
        roles = await db.get_author_roles()
        total = len(roles)
        content = roles if is_unpaged else roles[page * size : (page + 1) * size]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    return router
