import io
import json
import logging
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
import httpx
from PIL import Image

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.internal import UserSession
from app.models.komga import BookDto, PageDto, PageableDto, build_pageable
from app.services.auth import AuthService
from app.services.cache import memory_cache, thumbnail_cache
from app.services.filter_utils import extract_filter_params, resolve_effective_library_ids
from app.services.mapper import KomgaMapper
from app.services.page_calculator import PageCalculator

logger = logging.getLogger("grimmory_proxy.komga_books")


def get_books_router(db: Database, page_calculator: PageCalculator) -> APIRouter:
    router = APIRouter(tags=["Komga Books"])

    async def _query_books(
        library_ids: Optional[List[int]] = None,
        series_ids: Optional[List[str]] = None,
        read_status: Optional[List[str]] = None,
        search: Optional[str] = None,
        page: int = 0,
        size: int = 20,
        sort: Optional[Union[str, List[str]]] = None,
        unpaged: bool = False,
        user: Optional[UserSession] = None,
    ) -> PageableDto[BookDto]:
        # Resolve series IDs to canonical database IDs if possible
        canonical_series_ids = None
        if series_ids:
            canonical_series_ids = []
            series_lib_ids = []
            for s_id in series_ids:
                series_rec = await db.find_series_by_id_or_slug(s_id)
                if series_rec:
                    if series_rec["id"] not in canonical_series_ids:
                        canonical_series_ids.append(series_rec["id"])
                    if series_rec.get("slug") and series_rec["slug"] not in canonical_series_ids:
                        canonical_series_ids.append(series_rec["slug"])
                    series_lib_ids.append(series_rec["library_id"])
                if s_id not in canonical_series_ids:
                    canonical_series_ids.append(s_id)

            # If user has library restrictions, check that requested series belongs to allowed libraries
            if user and not user.is_admin and user.assigned_library_ids:
                if series_lib_ids and not any(lid in user.assigned_library_ids for lid in series_lib_ids):
                    logger.warning(
                        f"User {user.username} blocked from series {series_ids} outside assigned libraries {user.assigned_library_ids}"
                    )
                    return build_pageable([], page, size, 0, unpaged=unpaged)

        effective_libs = None
        if user:
            effective_libs = resolve_effective_library_ids(user, library_ids or [])
            if effective_libs is not None and len(effective_libs) == 0:
                return build_pageable([], page, size, 0, unpaged=unpaged)
        elif library_ids:
            effective_libs = library_ids

        records, total = await db.get_books_list(
            library_ids=effective_libs if not canonical_series_ids else None,
            series_ids=canonical_series_ids,
            read_status=read_status,
            search=search,
            offset=0 if unpaged else page * size,
            limit=size,
            sort=sort,
            unpaged=unpaged,
            user_id=user.user_id if user else None,
        )

        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size if not unpaged else total, total, unpaged=unpaged)

    @router.get("/api/v1/books", response_model=PageableDto[BookDto])
    async def get_all_books(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        sort: str = Query("number,asc"),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        filters = extract_filter_params(request)
        sort_list = request.query_params.getlist("sort")
        sort_arg = sort_list if sort_list else sort
        return await _query_books(
            library_ids=filters["library_ids"],
            series_ids=filters["series_ids"],
            read_status=filters["read_status"],
            search=filters["search"],
            page=page,
            size=size,
            sort=sort_arg,
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.post("/api/v1/books/list", response_model=PageableDto[BookDto])
    async def list_books_post(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        sort: str = Query("number,asc"),
        body: Optional[Any] = None,
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        parsed_body = body
        if not isinstance(parsed_body, (dict, list)):
            try:
                parsed_body = await request.json()
            except Exception:
                parsed_body = None
        filters = extract_filter_params(request, parsed_body)
        logger.info(
            f"POST /api/v1/books/list: query_params={dict(request.query_params)}, "
            f"body={parsed_body}, extracted_filters={filters}"
        )
        sort_list = request.query_params.getlist("sort")
        sort_arg = sort_list if sort_list else sort
        return await _query_books(
            library_ids=filters["library_ids"],
            series_ids=filters["series_ids"],
            read_status=filters["read_status"],
            search=filters["search"],
            page=page,
            size=size,
            sort=sort_arg,
            unpaged=filters["unpaged"],
            user=user,
        )

    @router.api_route("/api/v1/books/ondeck", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_ondeck(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        filters = extract_filter_params(request)
        effective_libs = resolve_effective_library_ids(user, filters["library_ids"])
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0)
        records, total = await db.get_books_ondeck(
            library_ids=effective_libs,
            user_id=user.user_id if user else None,
            offset=page * size,
            limit=size,
        )
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.api_route("/api/v1/books/latest", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_latest(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        filters = extract_filter_params(request)
        effective_libs = resolve_effective_library_ids(user, filters["library_ids"])
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0)
        records, total = await db.get_books_list(
            library_ids=effective_libs,
            offset=page * size,
            limit=size,
            sort_by="created",
            sort_dir="desc",
            user_id=user.user_id if user else None,
        )
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.api_route("/api/v1/books/released", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_released(
        request: Request,
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        filters = extract_filter_params(request)
        effective_libs = resolve_effective_library_ids(user, filters["library_ids"])
        if effective_libs is not None and len(effective_libs) == 0:
            return build_pageable([], page, size, 0)
        records, total = await db.get_books_list(
            library_ids=effective_libs,
            offset=page * size,
            limit=size,
            sort_by="releasedate",
            sort_dir="desc",
            user_id=user.user_id if user else None,
        )
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.get("/api/v1/books/{book_id}", response_model=BookDto)
    async def get_book_by_id(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> BookDto:
        record = await db.get_book_by_id(book_id, user_id=user.user_id if user else None)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        if not user.is_admin and user.assigned_library_ids and record["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        return KomgaMapper.to_book_dto(record)

    @router.get("/api/v1/books/{book_id}/thumbnail")
    async def get_book_thumbnail(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        cached = thumbnail_cache.get_thumbnail("book", str(book_id))
        if cached:
            return Response(content=cached, media_type="image/jpeg")

        thumb = await grimmory_client.stream_thumbnail(book_id, token=user.token)
        if thumb:
            thumbnail_cache.save_thumbnail("book", str(book_id), thumb)
            return Response(content=thumb, media_type="image/jpeg")

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book thumbnail not found")

    @router.get("/api/v1/books/{book_id}/pages", response_model=List[PageDto])
    async def get_book_pages(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> List[PageDto]:
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        # Check cached page details in DB
        pages = await db.get_book_pages(book_id)
        if pages:
            return [
                PageDto(
                    number=p["page_number"],
                    fileName=p["file_name"],
                    mediaType=p["media_type"],
                    width=p["width"],
                    height=p["height"],
                    sizeBytes=p["size_bytes"],
                )
                for p in pages
            ]

        # Synthesize pages from page_count or calculate if missing
        page_count = record.get("page_count", 0)
        book_type = record.get("book_type", "EPUB")

        if page_count <= 0:
            try:
                calc_count, calc_pages = await page_calculator.inspect_book_pages(book_id, book_type)
                await db.update_book_page_count(book_id, calc_count)
                await db.upsert_book_pages(book_id, [p.model_dump() for p in calc_pages])
                return calc_pages
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (404, 410):
                    logger.warning(f"Book {book_id} was removed from Grimmory (HTTP {e.response.status_code}); marking deleted.")
                    await db.mark_book_deleted(book_id)
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
                raise

        # Synthesize PageDto array
        page_dtos = [
            PageDto(
                number=i,
                fileName=f"page-{i:03d}.jpg",
                mediaType="image/jpeg",
                width=1200,
                height=1600,
            )
            for i in range(1, page_count + 1)
        ]
        await db.upsert_book_pages(book_id, [p.model_dump() for p in page_dtos])
        return page_dtos

    @router.get("/api/v1/books/{book_id}/pages/{page_number}")
    async def get_book_page_image(
        book_id: int,
        page_number: int,
        convert: Optional[str] = Query(None),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        img_bytes = await grimmory_client.stream_page_image(book_id, page_number, token=user.token)
        if not img_bytes:
            # Fallback to book thumbnail if page image not directly streamable
            img_bytes = thumbnail_cache.get_thumbnail("book", str(book_id))
            if not img_bytes:
                img_bytes = await grimmory_client.stream_thumbnail(book_id, token=user.token)

        if not img_bytes:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page image not found")

        content_type = "image/jpeg"
        if convert and convert.lower() == "png":
            try:
                with Image.open(io.BytesIO(img_bytes)) as img:
                    out = io.BytesIO()
                    img.save(out, format="PNG")
                    img_bytes = out.getvalue()
                    content_type = "image/png"
            except Exception as e:
                logger.warning(f"Error converting page image to PNG: {e}")

        return Response(content=img_bytes, media_type=content_type)

    @router.get("/api/v1/books/{book_id}/file")
    async def download_book_file(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> StreamingResponse:
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        if not user.is_admin and user.assigned_library_ids and record["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        try:
            resp = await grimmory_client.download_book_stream(book_id, token=user.token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                logger.warning(f"Book {book_id} was removed from Grimmory (HTTP {e.response.status_code}); marking deleted.")
                await db.mark_book_deleted(book_id)
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
            raise

        media_type = resp.headers.get("Content-Type", "application/octet-stream")
        content_disposition = resp.headers.get("Content-Disposition", f'attachment; filename="{record.get("name", "book")}.epub"')

        return StreamingResponse(
            resp.aiter_bytes(),
            media_type=media_type,
            headers={"Content-Disposition": content_disposition},
        )

    @router.get("/api/v1/books/{book_id}/manifest")
    @router.get("/api/v1/books/{book_id}/manifest/divina")
    async def get_book_manifest(
        book_id: int,
        request: Request,
        user: UserSession = Depends(AuthService.require_user),
    ) -> Dict[str, Any]:
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        book_dto = KomgaMapper.to_book_dto(record)
        pages = await get_book_pages(book_id, user)
        base_url = str(request.base_url).rstrip("/")
        return KomgaMapper.to_divina_manifest(book_dto, pages, base_url=base_url)

    return router
