import asyncio
import logging
from typing import Dict, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.models.internal import UserSession
from app.models.komga import ApiKeyDto, ClaimStatusDto, OAuth2ClientDto, UserDto
from app.services.auth import AuthService

logger = logging.getLogger("grimmory_proxy.komga_auth")

router = APIRouter(tags=["Komga Auth & System"])


@router.get("/actuator/info")
async def actuator_info() -> Dict[str, dict]:
    return {
        "git": {
            "branch": "master",
            "commit": {
                "id": "grimmory-bridge",
                "time": "2026-10-07T10:00:00Z",
            },
        },
        "build": {
            "artifact": "komga",
            "name": "komga",
            "version": "1.12.0",
        },
    }


@router.get("/api/v1/claim", response_model=ClaimStatusDto)
async def get_claim_status() -> ClaimStatusDto:
    """KMreader & Komga client claim check. Returns isClaimed=True."""
    return ClaimStatusDto(isClaimed=True)


@router.post("/api/v1/claim")
async def claim_server():
    """Attempting to claim an already claimed server returns 400 Bad Request."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Server has already been claimed",
    )


@router.get("/api/v1/oauth2/providers", response_model=List[OAuth2ClientDto])
async def get_oauth2_providers() -> List[OAuth2ClientDto]:
    """Returns available OAuth2 providers (empty list for proxy)."""
    return []


def _map_user_to_dto(user: UserSession) -> UserDto:
    roles = ["ROLE_USER", "ROLE_FILE_DOWNLOAD", "ROLE_PAGE_STREAMING"]
    if user.is_admin:
        roles.append("ROLE_ADMIN")

    return UserDto(
        id=str(user.user_id),
        email=f"{user.username}@grimmory.local",
        roles=roles,
        sharedAllLibraries=len(user.assigned_library_ids) == 0 or user.is_admin,
        sharedLibrariesIds=[str(lib_id) for lib_id in user.assigned_library_ids],
    )


def _set_session_cookies(response: Response, user: UserSession) -> None:
    cookie_val = user.token or str(user.user_id)
    for name in ["KOMGA-SESSION", "SESSION", "remember-me"]:
        response.set_cookie(
            key=name,
            value=cookie_val,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,
        )


@router.get("/api/v1/users/me", response_model=UserDto)
async def get_current_user_v1(
    response: Response,
    user: UserSession = Depends(AuthService.require_user),
) -> UserDto:
    _set_session_cookies(response, user)
    return _map_user_to_dto(user)


@router.get("/api/v2/users/me", response_model=UserDto)
async def get_current_user_v2(
    response: Response,
    user: UserSession = Depends(AuthService.require_user),
) -> UserDto:
    _set_session_cookies(response, user)
    return _map_user_to_dto(user)


@router.get("/api/v2/users/me/api-keys", response_model=List[ApiKeyDto])
async def get_current_user_api_keys(
    user: UserSession = Depends(AuthService.require_user),
) -> List[ApiKeyDto]:
    """Returns user API keys."""
    from app.main import db
    keys = await db.get_api_keys(user.user_id)
    return [ApiKeyDto(**k) for k in keys]


@router.post("/api/v2/users/me/api-keys", response_model=ApiKeyDto, status_code=status.HTTP_201_CREATED)
async def create_user_api_key(
    request: Request,
    user: UserSession = Depends(AuthService.require_user),
) -> ApiKeyDto:
    """Creates a new API key for KMreader / client integrations."""
    from app.main import db
    comment = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            comment = body.get("comment")
    except Exception:
        pass

    key_data = await db.create_api_key(user.user_id, comment=comment)
    return ApiKeyDto(**key_data)


@router.delete("/api/v2/users/me/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_api_key(
    key_id: str,
    user: UserSession = Depends(AuthService.require_user),
) -> Response:
    from app.main import db
    await db.delete_api_key(user.user_id, key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sse/v1/events")
async def sse_events(
    request: Request,
    user: UserSession = Depends(AuthService.require_user),
) -> StreamingResponse:
    """Server-Sent Events endpoint for real-time clients like KMreader."""
    async def event_generator():
        try:
            yield ":keepalive\n\n"
            while not await request.is_disconnected():
                await asyncio.sleep(15)
                yield ":keepalive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/v1/login/set-cookie")
async def set_cookie_login(
    response: Response,
    user: UserSession = Depends(AuthService.require_user),
) -> UserDto:
    _set_session_cookies(response, user)
    return _map_user_to_dto(user)


@router.api_route("/api/logout", methods=["GET", "POST"])
async def logout(response: Response) -> Response:
    for name in ["KOMGA-SESSION", "SESSION", "remember-me"]:
        response.delete_cookie(key=name)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
