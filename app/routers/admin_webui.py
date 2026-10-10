import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.database import Database
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.cache import thumbnail_cache
from app.services.page_calculator import PageCalculator
from app.services.sync import SyncService
from app.services.user_sync import UserSyncService

logger = logging.getLogger("grimmory_proxy.admin_webui")

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


class CalcPagesRequest(BaseModel):
    missing_only: bool = True


def get_admin_router(
    db: Database,
    page_calculator: PageCalculator,
    sync_service: SyncService,
    user_sync: Optional[UserSyncService] = None,
) -> APIRouter:
    if user_sync is None:
        from app.services.user_sync import user_sync_service
        user_sync = user_sync_service

    router = APIRouter(tags=["Admin WebUI"])

    @router.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        # If already logged in as admin, redirect to dashboard
        user = await AuthService.get_current_user_optional(request)
        if user and user.is_admin:
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"user": None, "error": None},
        )

    @router.post("/admin/login", response_class=HTMLResponse)
    async def login_post(
        request: Request,
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        try:
            user = await AuthService.authenticate_credentials(username, password)
            if not user.is_admin:
                return templates.TemplateResponse(
                    request=request,
                    name="login.html",
                    context={
                        "user": None,
                        "error": "Access Denied: Only Grimmory Administrators are permitted to access this management dashboard.",
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            redirect = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
            redirect.set_cookie(
                key="admin_session",
                value=user.token,
                httponly=True,
                samesite="lax",
                max_age=86400 * 7,
            )
            return redirect
        except Exception as e:
            logger.warning(f"Admin login failed: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"user": None, "error": "Invalid Grimmory credentials or account not found."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    @router.get("/admin/logout")
    async def logout_admin(response: Response) -> Response:
        redirect = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
        redirect.delete_cookie("admin_session")
        return redirect

    @router.get("/admin", response_class=HTMLResponse)
    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        stats = await db.get_stats()
        thumb_count, thumb_bytes = thumbnail_cache.get_cache_size()

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "user": user,
                "stats": stats,
                "calc_job": page_calculator.status,
                "sync_job": sync_service.status,
                "read_sync_job": user_sync.status,
                "thumbnail_count": thumb_count,
                "thumbnail_bytes": thumb_bytes,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
                "sync_interval_minutes": settings.sync_interval_minutes,
                "user_sync_interval_minutes": settings.user_sync_interval_minutes,
            },
        )

    # ---------------- AJAX Endpoints for WebUI ----------------

    @router.get("/admin/api/status")
    async def get_dashboard_status(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin access required")

        stats = await db.get_stats()
        thumb_count, thumb_bytes = thumbnail_cache.get_cache_size()
        return {
            "stats": stats,
            "calc_job": page_calculator.status.model_dump(),
            "sync_job": sync_service.status.model_dump(),
            "read_sync_job": user_sync.status.model_dump(),
            "thumbnails": {"count": thumb_count, "bytes": thumb_bytes},
            "sync_running": sync_service.status.is_running,
            "read_sync_running": user_sync.status.is_running,
        }

    @router.post("/admin/api/calculate-pages")
    async def trigger_calculate_pages(
        payload: CalcPagesRequest,
        request: Request,
    ) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        if page_calculator.status.is_running:
            return {"status": "already_running", "message": "A calculation job is already running"}

        asyncio.create_task(page_calculator.run_calculation_job(missing_only=payload.missing_only))
        return {"status": "started", "missing_only": payload.missing_only}

    @router.post("/admin/api/stop-calculation")
    async def stop_calculate_pages(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        page_calculator.request_stop()
        return {"status": "stopping"}

    @router.post("/admin/api/sync")
    async def trigger_manual_sync(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        if sync_service.status.is_running:
            return {"status": "already_running", "message": "Sync is already in progress"}

        asyncio.create_task(sync_service.sync_all_metadata())
        return {"status": "started"}

    @router.post("/admin/api/sync/clear-logs")
    async def clear_sync_logs(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        sync_service.status.logs = []
        return {"status": "cleared"}

    @router.post("/admin/api/read-sync")
    async def trigger_manual_read_sync(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        if user_sync.status.is_running:
            return {"status": "already_running", "message": "Read sync is already in progress"}

        asyncio.create_task(user_sync.sync_all_active_users())
        return {"status": "started"}

    @router.post("/admin/api/read-sync/clear-logs")
    async def clear_read_sync_logs(request: Request) -> Dict[str, Any]:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        user_sync.status.logs = []
        return {"status": "cleared"}

    return router
