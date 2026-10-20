import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.internal import UserSession
from app.models.komga import (
    BookDto,
    CollectionDto,
    PageableDto,
    ReadListDto,
    SeriesDto,
    build_pageable,
)
from app.services.auth import AuthService
from app.services.cache import thumbnail_cache
from app.services.filter_utils import (
    is_unpaged_request,
    parse_library_ids_param,
    resolve_effective_library_ids,
)
from app.services.mapper import KomgaMapper

logger = logging.getLogger("grimmory_proxy.komga_collections")


def get_collections_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Collections & ReadLists"])

    @router.get("/api/v1/collections", response_model=PageableDto[CollectionDto])
    async def get_collections(
        request: Request,
        search: Optional[str] = Query(None),
        library_id: Optional[List[int]] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        unpaged: bool = Query(False),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[CollectionDto]:
        is_unpaged = is_unpaged_request(request, unpaged)
        collections: List[CollectionDto] = []

        # 1. Resolve Favorites collection for the authenticated user
        try:
            fav_book_ids = await grimmory_client.get_user_favorite_book_ids(
                user.user_id, token=user.token
            )
            if fav_book_ids:
                req_libs = parse_library_ids_param(request, library_id)
                effective_libs = resolve_effective_library_ids(user, req_libs)
                if effective_libs is None or len(effective_libs) > 0:
                    series_list = await db.get_series_for_books(
                        fav_book_ids,
                        library_ids=effective_libs,
                        user_id=user.user_id,
                    )
                    if series_list:
                        series_ids = [f"fav-{user.user_id}-{s['id']}" for s in series_list]
                        fav_collection = CollectionDto(
                            id=f"favorites-{user.user_id}",
                            name=f"Favorites - {user.username}",
                            ordered=False,
                            seriesIds=series_ids,
                            createdDate="2026-01-01T00:00:00Z",
                            lastModifiedDate="2026-01-01T00:00:00Z",
                            filtered=False,
                        )
                        collections.append(fav_collection)
        except Exception as e:
            logger.debug(f"Error fetching favorites shelf for user {user.username}: {e}")

        # 2. Add other Grimmory magic shelves (excluding any generic favorites shelf)
        try:
            magic_shelves = await grimmory_client.get_magic_shelves(token=user.token)
            for idx, s in enumerate(magic_shelves, start=1):
                s_name = str(s.get("name", "")).strip()
                if s_name.lower() == "favorites":
                    continue
                collections.append(
                    CollectionDto(
                        id=str(s.get("id", idx)),
                        name=s_name or f"Collection {idx}",
                        ordered=False,
                        seriesIds=[],
                    )
                )
        except Exception as e:
            logger.debug(f"Error fetching magic shelves: {e}")

        # Apply search filter
        if search:
            s_lower = search.strip().lower()
            collections = [c for c in collections if s_lower in c.name.lower()]

        total = len(collections)
        slice_items = collections if is_unpaged else collections[page * size : (page + 1) * size]
        return build_pageable(slice_items, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

    @router.get("/api/v1/collections/{collection_id}", response_model=CollectionDto)
    async def get_collection_by_id(
        collection_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> CollectionDto:
        # Strictly user-gated favorites collection
        if collection_id.startswith("favorites-"):
            expected_id = f"favorites-{user.user_id}"
            if collection_id != expected_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            fav_book_ids = await grimmory_client.get_user_favorite_book_ids(
                user.user_id, token=user.token
            )
            if not fav_book_ids:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            effective_libs = resolve_effective_library_ids(user, [])
            if effective_libs is not None and len(effective_libs) == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            series_list = await db.get_series_for_books(
                fav_book_ids,
                library_ids=effective_libs,
                user_id=user.user_id,
            )
            if not series_list:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            series_ids = [f"fav-{user.user_id}-{s['id']}" for s in series_list]
            return CollectionDto(
                id=expected_id,
                name=f"Favorites - {user.username}",
                ordered=False,
                seriesIds=series_ids,
                createdDate="2026-01-01T00:00:00Z",
                lastModifiedDate="2026-01-01T00:00:00Z",
                filtered=False,
            )

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    @router.get("/api/v1/collections/{collection_id}/series", response_model=PageableDto[SeriesDto])
    async def get_collection_series(
        collection_id: str,
        request: Request,
        library_id: Optional[List[int]] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        unpaged: bool = Query(False),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[SeriesDto]:
        is_unpaged = is_unpaged_request(request, unpaged)

        if collection_id.startswith("favorites-"):
            if collection_id != f"favorites-{user.user_id}":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            fav_book_ids = await grimmory_client.get_user_favorite_book_ids(
                user.user_id, token=user.token
            )
            if not fav_book_ids:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            req_libs = parse_library_ids_param(request, library_id)
            effective_libs = resolve_effective_library_ids(user, req_libs)
            if effective_libs is not None and len(effective_libs) == 0:
                return build_pageable([], page, size, 0, unpaged=is_unpaged)

            series_list = await db.get_series_for_books(
                fav_book_ids,
                library_ids=effective_libs,
                user_id=user.user_id,
            )
            if not series_list:
                return build_pageable([], page, size, 0, unpaged=is_unpaged)

            series_dtos: List[SeriesDto] = []
            for s in series_list:
                fav_count = await db.count_books_in_series(s["id"], book_ids=fav_book_ids)
                dto = KomgaMapper.to_series_dto(s)
                dto.id = f"fav-{user.user_id}-{s['id']}"
                dto.booksCount = fav_count
                series_dtos.append(dto)

            total = len(series_dtos)
            slice_items = series_dtos if is_unpaged else series_dtos[page * size : (page + 1) * size]
            return build_pageable(slice_items, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    @router.get("/api/v1/collections/{collection_id}/books", response_model=PageableDto[BookDto])
    async def get_collection_books(
        collection_id: str,
        request: Request,
        library_id: Optional[List[int]] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        sort: str = Query("number,asc"),
        unpaged: bool = Query(False),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        is_unpaged = is_unpaged_request(request, unpaged)

        if collection_id.startswith("favorites-"):
            if collection_id != f"favorites-{user.user_id}":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            fav_book_ids = await grimmory_client.get_user_favorite_book_ids(
                user.user_id, token=user.token
            )
            if not fav_book_ids:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            req_libs = parse_library_ids_param(request, library_id)
            effective_libs = resolve_effective_library_ids(user, req_libs)
            if effective_libs is not None and len(effective_libs) == 0:
                return build_pageable([], page, size, 0, unpaged=is_unpaged)

            sort_col, sort_dir = "number", "asc"
            if sort:
                parts = sort.split(",")
                sort_col = parts[0].replace("metadata.", "").strip()
                if len(parts) > 1:
                    sort_dir = parts[1].strip()

            records, total = await db.get_books_list(
                book_ids=fav_book_ids,
                library_ids=effective_libs,
                offset=0 if is_unpaged else page * size,
                limit=size,
                sort_by=sort_col,
                sort_dir=sort_dir,
                unpaged=is_unpaged,
                user_id=user.user_id,
            )

            dtos = []
            for r in records:
                dto = KomgaMapper.to_book_dto(r)
                dto.seriesId = f"fav-{user.user_id}-{dto.seriesId}"
                dtos.append(dto)

            return build_pageable(dtos, page, size if not is_unpaged else total, total, unpaged=is_unpaged)

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    @router.get("/api/v1/collections/{collection_id}/thumbnail")
    async def get_collection_thumbnail(
        collection_id: str,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        if collection_id.startswith("favorites-"):
            if collection_id != f"favorites-{user.user_id}":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

            fav_book_ids = await grimmory_client.get_user_favorite_book_ids(
                user.user_id, token=user.token
            )
            if not fav_book_ids:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection thumbnail not found")

            first_book_id = fav_book_ids[0]
            thumb = thumbnail_cache.get_thumbnail("book", str(first_book_id))
            if not thumb:
                thumb = await grimmory_client.stream_thumbnail(first_book_id, token=user.token)
                if thumb:
                    thumbnail_cache.save_thumbnail("book", str(first_book_id), thumb)

            if thumb:
                return Response(content=thumb, media_type="image/jpeg")

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection thumbnail not found")

    @router.get("/api/v1/readlists", response_model=PageableDto[ReadListDto])
    async def get_readlists(
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        unpaged: bool = Query(False),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[ReadListDto]:
        return build_pageable([], page, size, 0, unpaged=unpaged)

    return router
