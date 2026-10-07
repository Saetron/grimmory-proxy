import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Body, Depends, Request, Response, status

from app.database import Database
from app.models.internal import UserSession
from app.models.komga import (
    ClientSettingDto,
    ClientSettingGlobalUpdateDto,
    ClientSettingUserUpdateDto,
)
from app.services.auth import AuthService

logger = logging.getLogger("grimmory_proxy.komga_client_settings")


def get_client_settings_router(db: Database) -> APIRouter:
    router = APIRouter(tags=["Komga Client Settings"])

    # ---------------- Global Client Settings ----------------

    @router.get("/api/v1/client-settings/global/list", response_model=Dict[str, ClientSettingDto])
    @router.get("/api/v1/client-settings/global", response_model=Dict[str, ClientSettingDto])
    async def get_global_client_settings(request: Request) -> Dict[str, ClientSettingDto]:
        user = await AuthService.get_current_user_optional(request)
        allow_unauthorized_only = user is None
        settings_map = await db.get_client_settings(
            scope="global",
            user_id=0,
            allow_unauthorized_only=allow_unauthorized_only,
        )
        return {
            k: ClientSettingDto(
                value=v["value"],
                allowUnauthorized=v.get("allowUnauthorized", False),
            )
            for k, v in settings_map.items()
        }

    @router.patch("/api/v1/client-settings/global", status_code=status.HTTP_204_NO_CONTENT)
    async def update_global_client_settings(
        payload: Dict[str, ClientSettingGlobalUpdateDto],
        admin: UserSession = Depends(AuthService.require_admin),
    ) -> Response:
        settings_data = {
            k: {"value": v.value, "allowUnauthorized": v.allowUnauthorized}
            for k, v in payload.items()
        }
        await db.save_client_settings(scope="global", user_id=0, settings=settings_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/client-settings/global", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_global_client_settings(
        names: List[str] = Body(default=[]),
        admin: UserSession = Depends(AuthService.require_admin),
    ) -> Response:
        await db.delete_client_settings(scope="global", user_id=0, names=names)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---------------- User Client Settings ----------------

    @router.get("/api/v1/client-settings/user/list", response_model=Dict[str, ClientSettingDto])
    @router.get("/api/v1/client-settings/user", response_model=Dict[str, ClientSettingDto])
    async def get_user_client_settings(
        user: UserSession = Depends(AuthService.require_user),
    ) -> Dict[str, ClientSettingDto]:
        settings_map = await db.get_client_settings(scope="user", user_id=user.user_id)
        return {
            k: ClientSettingDto(value=v["value"], allowUnauthorized=False)
            for k, v in settings_map.items()
        }

    @router.patch("/api/v1/client-settings/user", status_code=status.HTTP_204_NO_CONTENT)
    async def update_user_client_settings(
        payload: Dict[str, ClientSettingUserUpdateDto],
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        settings_data = {
            k: {"value": v.value, "allowUnauthorized": False}
            for k, v in payload.items()
        }
        await db.save_client_settings(scope="user", user_id=user.user_id, settings=settings_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/api/v1/client-settings/user", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_user_client_settings(
        names: List[str] = Body(default=[]),
        user: UserSession = Depends(AuthService.require_user),
    ) -> Response:
        await db.delete_client_settings(scope="user", user_id=user.user_id, names=names)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
