import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request

from app.database import Database
from app.models.internal import UserSession
from app.models.komga import PageableDto, build_pageable
from app.services.auth import AuthService
from app.services.filter_utils import resolve_effective_library_ids

logger = logging.getLogger("grimmory_proxy.komga_metadata")


def get_metadata_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Metadata"])

    # ---------------- Genres ----------------

    @router.get("/api/v1/genres", response_model=List[str])
    async def get_genres_v1(
        request: Request,
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        # Handle repeated query params or comma-separated lists
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        return await db.get_all_genres(library_ids=effective_libs)

    @router.get("/api/v2/genres", response_model=PageableDto[str])
    async def get_genres_v2(
        request: Request,
        search: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[List[str]] = Query(None),
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[str]:
        is_unpaged = unpaged or request.query_params.get("unpaged", "").lower() in ("true", "1")
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0, unpaged=is_unpaged)

        genres = await db.get_all_genres(library_ids=effective_libs, search=search)
        total = len(genres)
        if is_unpaged:
            content = genres
        else:
            content = genres[page * size : (page + 1) * size]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    # ---------------- Publishers ----------------

    @router.get("/api/v1/publishers", response_model=List[str])
    async def get_publishers_v1(
        request: Request,
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        return await db.get_all_publishers(library_ids=effective_libs)

    @router.get("/api/v2/publishers", response_model=PageableDto[str])
    async def get_publishers_v2(
        request: Request,
        search: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[List[str]] = Query(None),
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[str]:
        is_unpaged = unpaged or request.query_params.get("unpaged", "").lower() in ("true", "1")
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0, unpaged=is_unpaged)

        publishers = await db.get_all_publishers(library_ids=effective_libs, search=search)
        total = len(publishers)
        if is_unpaged:
            content = publishers
        else:
            content = publishers[page * size : (page + 1) * size]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    # ---------------- Tags ----------------

    @router.get("/api/v1/tags", response_model=List[str])
    async def get_tags_v1(
        request: Request,
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        return await db.get_all_tags(library_ids=effective_libs)

    @router.get("/api/v2/tags", response_model=PageableDto[str])
    async def get_tags_v2(
        request: Request,
        search: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        collection_id: Optional[List[str]] = Query(None),
        series_id: Optional[List[str]] = Query(None),
        readlist_id: Optional[List[str]] = Query(None),
        include: Optional[str] = Query(None),
        unpaged: bool = Query(False),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[str]:
        is_unpaged = unpaged or request.query_params.get("unpaged", "").lower() in ("true", "1")
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0, unpaged=is_unpaged)

        if include and include.lower() == "book":
            first_series = series_id[0] if series_id else None
            tags = await db.get_book_tags(library_ids=effective_libs, series_id=first_series, search=search)
        elif include and include.lower() == "series":
            tags = await db.get_series_tags(library_ids=effective_libs, search=search)
        else:
            tags = await db.get_all_tags(library_ids=effective_libs, search=search)

        total = len(tags)
        if is_unpaged:
            content = tags
        else:
            content = tags[page * size : (page + 1) * size]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    @router.get("/api/v1/tags/book", response_model=List[str])
    async def get_book_tags_v1(
        request: Request,
        series_id: Optional[str] = Query(None),
        readlist_id: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        lib_ids = request.query_params.getlist("library_id") or (library_id if library_id is not None else [])
        parsed_libs: List[int] = []
        for l in lib_ids:
            for part in str(l).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        return await db.get_book_tags(library_ids=effective_libs, series_id=series_id)

    @router.get("/api/v1/tags/series", response_model=List[str])
    async def get_series_tags_v1(
        request: Request,
        library_id: Optional[str] = Query(None),
        collection_id: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        parsed_libs: List[int] = []
        if library_id:
            for part in str(library_id).split(","):
                if part.strip().isdigit():
                    parsed_libs.append(int(part.strip()))

        effective_libs = resolve_effective_library_ids(user, parsed_libs)
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        return await db.get_series_tags(library_ids=effective_libs)

    return router
