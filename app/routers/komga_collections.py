import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query

from app.clients.grimmory import grimmory_client
from app.models.internal import UserSession
from app.models.komga import CollectionDto, PageableDto, ReadListDto, build_pageable
from app.services.auth import AuthService

logger = logging.getLogger("grimmory_proxy.komga_collections")

router = APIRouter(tags=["Komga Collections & ReadLists"])


@router.get("/api/v1/collections", response_model=PageableDto[CollectionDto])
async def get_collections(
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=500),
    user: UserSession = Depends(AuthService.require_user),
) -> PageableDto[CollectionDto]:
    try:
        shelves = await grimmory_client.get_magic_shelves(token=user.token)
        collections = [
            CollectionDto(
                id=str(s.get("id", idx)),
                name=s.get("name", f"Collection {idx}"),
                ordered=False,
                seriesIds=[],
            )
            for idx, s in enumerate(shelves, start=1)
        ]
        total = len(collections)
        slice_items = collections[page * size : (page + 1) * size]
        return build_pageable(slice_items, page, size, total)
    except Exception as e:
        logger.debug(f"Error fetching collections: {e}")
        return build_pageable([], page, size, 0)


@router.get("/api/v1/readlists", response_model=PageableDto[ReadListDto])
async def get_readlists(
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=500),
    user: UserSession = Depends(AuthService.require_user),
) -> PageableDto[ReadListDto]:
    return build_pageable([], page, size, 0)
