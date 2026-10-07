import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.internal import UserSession
from app.models.komga import BookDto, CollectionDto, PageableDto, SeriesDto, build_pageable
from app.services.auth import AuthService
from app.services.cache import thumbnail_cache
from app.services.filter_utils import extract_filter_params, resolve_effective_library_ids
from app.services.mapper import KomgaMapper

logger = logging.getLogger("grimmory_proxy.komga_series")


def get_series_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Series"])

    async def _query_series(
        library_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        page: int = 0,
        size: int = 20,
        sort: str = "title,asc",
        unpaged: bool = False,
        user: Optional[UserSession] = None,
    ) -> PageableDto[SeriesDto]:
        sort_by, sort_dir = "name", "asc"
        if sort:
            parts = sort.split(",")
            sort_by = parts[0]
            if len(parts) > 1:
                sort_dir = parts[1]

        # Enforce user library restrictions
        effective_libs = None
        if user:
            effective_libs = resolve_effective_library_ids(user, library_ids or [])
            if effective_libs is not None and len(effective_libs) == 0:
                return build_pageable([], page, size, 0, unpaged=unpaged)
        elif library_ids:
            effective_libs = library_ids

        records, total = await db.get_series_list(
            library_ids=effective_libs,
            search=search,
            offset=0 if unpaged else page * size,
            limit=size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            unpaged=unpaged,
        )

        content = [KomgaMapper.to_series_dto(r) for r in records]
        return build_pageable(content, page, size if not unpaged else total, total, unpaged=unpaged)

    @router.get("/api/v1/series", response_model=PageableDto[SeriesDto])
    async def get_all_series(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        sort: str = Query("title,asc"),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        filters = extract_filter_params(request)
        return await _query_series(
            library_ids=filters["library_ids"],
            search=filters["search"],
            page=page,
            size=size,
            sort=sort,
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.post("/api/v1/series/list", response_model=PageableDto[SeriesDto])
    async def list_series_post(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        sort: str = Query("title,asc"),
        body: Optional[Any] = None,
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        parsed_body = body
        if not isinstance(parsed_body, (dict, list)):
            try:
                parsed_body = await request.json()
            except Exception:
                parsed_body = None
        filters = extract_filter_params(request, parsed_body)
        logger.info(
            f"POST /api/v1/series/list: query_params={dict(request.query_params)}, "
            f"body={parsed_body}, extracted_filters={filters}"
        )
        return await _query_series(
            library_ids=filters["library_ids"],
            search=filters["search"],
            page=page,
            size=size,
            sort=sort,
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.get("/api/v1/series/latest", response_model=PageableDto[SeriesDto])
    async def get_series_latest(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        filters = extract_filter_params(request)
        return await _query_series(
            library_ids=filters["library_ids"],
            search=filters["search"],
            page=page,
            size=size,
            sort="lastmodified,desc",
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.get("/api/v1/series/new", response_model=PageableDto[SeriesDto])
    async def get_series_new(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        filters = extract_filter_params(request)
        return await _query_series(
            library_ids=filters["library_ids"],
            search=filters["search"],
            page=page,
            size=size,
            sort="created,desc",
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.get("/api/v1/series/updated", response_model=PageableDto[SeriesDto])
    async def get_series_updated(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        filters = extract_filter_params(request)
        return await _query_series(
            library_ids=filters["library_ids"],
            search=filters["search"],
            page=page,
            size=size,
            sort="lastmodified,desc",
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.get("/api/v1/series/alphabetical-groups")
    async def get_series_alphabetical_groups(
        request: Request,
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[Dict[str, Any]]:
        filters = extract_filter_params(request)
        effective_libs = resolve_effective_library_ids(user, filters["library_ids"])
        if effective_libs is not None and len(effective_libs) == 0:
            return []
        records, _ = await db.get_series_list(library_ids=effective_libs, limit=5000)
        groups: Dict[str, int] = {}
        for r in records:
            first_char = r["name"][:1].upper() if r.get("name") else "#"
            if not first_char.isalpha():
                first_char = "#"
            groups[first_char] = groups.get(first_char, 0) + 1
        return [{"group": k, "count": v} for k, v in sorted(groups.items())]

    @router.get("/api/v1/series/genres")
    async def get_series_genres(
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        # Return unique genres found in series/books
        return ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mystery", "Romance", "Sci-Fi"]

    @router.get("/api/v1/series/release-dates")
    async def get_series_release_dates(
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[str]:
        return []

    @router.get("/api/v1/series/{series_id}", response_model=SeriesDto)
    async def get_series_by_id(
        series_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> SeriesDto:
        record = await db.get_series_by_id(series_id)
        if not record:
            record = await db.find_series_by_id_or_slug(series_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        if not user.is_admin and user.assigned_library_ids and record["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        return KomgaMapper.to_series_dto(record)

    @router.get("/api/v1/series/{series_id}/collections", response_model=List[CollectionDto])
    async def get_series_collections(
        series_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[CollectionDto]:
        series = await db.get_series_by_id(series_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        if not user.is_admin and user.assigned_library_ids and series["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        return []

    @router.get("/api/v1/series/{series_id}/books", response_model=PageableDto[BookDto])
    async def get_series_books(
        series_id: str,
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        sort: str = Query("number,asc"),
        unpaged: bool = Query(False),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        is_unpaged = unpaged or request.query_params.get("unpaged", "").lower() in ("true", "1")

        series = await db.get_series_by_id(series_id)
        if not series:
            series = await db.find_series_by_id_or_slug(series_id)
        if not series:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        if not user.is_admin and user.assigned_library_ids and series["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

        sort_col, sort_dir = "number", "asc"
        if sort:
            parts = sort.split(",")
            sort_col = parts[0].replace("metadata.", "").strip()
            if len(parts) > 1:
                sort_dir = parts[1].strip()

        records, total = await db.get_books_list(
            series_id=series["id"],
            offset=0 if is_unpaged else page * size,
            limit=size,
            sort_by=sort_col,
            sort_dir=sort_dir,
            unpaged=is_unpaged,
        )

        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    @router.get("/api/v1/series/{series_id}/thumbnail")
    async def get_series_thumbnail(
        series_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        # Check disk cache first
        cached = thumbnail_cache.get_thumbnail("series", series_id)
        if cached:
            return Response(content=cached, media_type="image/jpeg")

        # Find first book in series
        books, _ = await db.get_books_list(series_id=series_id, limit=1)
        if not books:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series thumbnail not found")

        first_book_id = books[0]["id"]
        book_thumb = thumbnail_cache.get_thumbnail("book", str(first_book_id))
        if not book_thumb:
            book_thumb = await grimmory_client.stream_thumbnail(first_book_id, token=user.token)

        if book_thumb:
            thumbnail_cache.save_thumbnail("series", series_id, book_thumb)
            return Response(content=book_thumb, media_type="image/jpeg")

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series thumbnail not found")

    return router
