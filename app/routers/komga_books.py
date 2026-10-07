import io
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from PIL import Image

from app.clients.grimmory import grimmory_client
from app.database import Database
from app.models.internal import UserSession
from app.models.komga import BookDto, PageDto, PageableDto, build_pageable
from app.services.auth import AuthService
from app.services.cache import memory_cache, thumbnail_cache
from app.services.mapper import KomgaMapper
from app.services.page_calculator import PageCalculator

logger = logging.getLogger("grimmory_proxy.komga_books")


def get_books_router(db: Database, page_calculator: PageCalculator) -> APIRouter:
    router = APIRouter(tags=["Komga Books"])

    async def _query_books(
        library_id: Optional[int] = None,
        series_id: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 0,
        size: int = 20,
        sort: str = "number,asc",
        user: Optional[UserSession] = None,
    ) -> PageableDto[BookDto]:
        sort_by, sort_dir = "number", "asc"
        if sort:
            parts = sort.split(",")
            sort_by = parts[0]
            if len(parts) > 1:
                sort_dir = parts[1]

        if user and not user.is_admin and user.assigned_library_ids:
            if library_id is not None and library_id not in user.assigned_library_ids:
                return build_pageable([], page, size, 0)

        records, total = await db.get_books_list(
            library_id=library_id,
            series_id=series_id,
            search=search,
            offset=page * size,
            limit=size,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.get("/api/v1/books", response_model=PageableDto[BookDto])
    async def get_all_books(
        library_id: Optional[int] = Query(None, alias="library_id"),
        series_id: Optional[str] = Query(None, alias="series_id"),
        search: Optional[str] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        sort: str = Query("number,asc"),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        return await _query_books(library_id, series_id, search, page, size, sort, user)

    @router.post("/api/v1/books/list", response_model=PageableDto[BookDto])
    async def list_books_post(
        library_id: Optional[int] = Query(None, alias="library_id"),
        series_id: Optional[str] = Query(None, alias="series_id"),
        search: Optional[str] = Query(None),
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        sort: str = Query("number,asc"),
        body: Optional[Dict[str, Any]] = None,
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        if body:
            if "libraryIds" in body and body["libraryIds"]:
                library_id = int(body["libraryIds"][0])
            if "seriesIds" in body and body["seriesIds"]:
                series_id = str(body["seriesIds"][0])
            if "searchTerm" in body:
                search = body["searchTerm"]
        return await _query_books(library_id, series_id, search, page, size, sort, user)

    @router.api_route("/api/v1/books/ondeck", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_ondeck(
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        # Return recently accessed books for this user
        records, total = await db.get_books_list(offset=page * size, limit=size, sort_by="lastmodified", sort_dir="desc")
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.api_route("/api/v1/books/latest", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_latest(
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        records, total = await db.get_books_list(offset=page * size, limit=size, sort_by="created", sort_dir="desc")
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.api_route("/api/v1/books/released", methods=["GET", "POST"], response_model=PageableDto[BookDto])
    async def get_books_released(
        page: int = Query(0, ge=0),
        size: int = Query(20, ge=1, le=500),
        user: UserSession = Depends(AuthService.require_user),
    ) -> PageableDto[BookDto]:
        records, total = await db.get_books_list(offset=page * size, limit=size, sort_by="created", sort_dir="desc")
        content = [KomgaMapper.to_book_dto(r) for r in records]
        return build_pageable(content, page, size, total)

    @router.get("/api/v1/books/{book_id}", response_model=BookDto)
    async def get_book_by_id(
        book_id: int,
        user: UserSession = Depends(AuthService.require_user),
    ) -> BookDto:
        record = await db.get_book_by_id(book_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        if not user.is_admin and user.assigned_library_ids and record["library_id"] not in user.assigned_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

        # Query user-specific live reading state from Grimmory
        try:
            user_book_data = await grimmory_client.get_book(book_id, token=user.token)
            merged_record = dict(record)
            merged_record["raw_json"] = user_book_data
            return KomgaMapper.to_book_dto(merged_record)
        except Exception:
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
            calc_count, calc_pages = await page_calculator.inspect_book_pages(book_id, book_type)
            await db.update_book_page_count(book_id, calc_count)
            await db.upsert_book_pages(book_id, [p.model_dump() for p in calc_pages])
            return calc_pages

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

        resp = await grimmory_client.download_book_stream(book_id, token=user.token)
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
