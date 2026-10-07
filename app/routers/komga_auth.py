import logging
from typing import Dict
from fastapi import APIRouter, Depends, Request, Response, status

from app.models.internal import UserSession
from app.models.komga import UserDto
from app.services.auth import AuthService

logger = logging.getLogger("komic.komga_auth")

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


@router.get("/api/v1/users/me", response_model=UserDto)
async def get_current_user_v1(user: UserSession = Depends(AuthService.require_user)) -> UserDto:
    return _map_user_to_dto(user)


@router.get("/api/v2/users/me", response_model=UserDto)
async def get_current_user_v2(user: UserSession = Depends(AuthService.require_user)) -> UserDto:
    return _map_user_to_dto(user)


@router.get("/api/v1/login/set-cookie")
async def set_cookie_login(
    response: Response,
    user: UserSession = Depends(AuthService.require_user),
) -> UserDto:
    response.set_cookie(
        key="KOMGA-SESSION",
        value=user.token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    return _map_user_to_dto(user)


@router.api_route("/api/logout", methods=["GET", "POST"])
async def logout(response: Response) -> Response:
    response.delete_cookie(key="KOMGA-SESSION")
    response.delete_cookie(key="SESSION")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
