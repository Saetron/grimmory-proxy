import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import settings
from app.database import Database
from app.models.internal import PageCalcStatus, UserSession
from app.services.auth import AuthService
from app.services.cache import thumbnail_cache
from app.services.page_calculator import PageCalculator
from app.services.sync import SyncService
from app.services.user_sync import UserSyncService

logger = logging.getLogger("grimmory_proxy.admin_webui")

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))
templates.env.globals["app_version"] = settings.app_version
templates.env.globals["grimmory_url"] = settings.grimmory_url
templates.env.globals["grimmory_public_url"] = settings.public_grimmory_url


class CalcPagesRequest(BaseModel):
    missing_only: bool = True


async def require_admin_session(request: Request) -> UserSession:
    """
    Admin WebUI/API guard. Unlike the Komga API guards, it never sends a
    `WWW-Authenticate: Basic` challenge, so browsers don't show a native
    Basic-auth dialog on top of the WebUI login page. The dashboard JS
    redirects to /admin/login on 401 instead.
    """
    try:
        user = await AuthService.get_current_user_optional(request)
    except HTTPException as e:
        # Re-raise without the Basic challenge header (keeps 401/429 semantics)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user

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
            context={"user": None, "error": None, "app_version": settings.app_version},
        )

    @router.post("/admin/login", response_class=HTMLResponse)
    async def login_post(
        request: Request,
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        try:
            user = await AuthService.authenticate_credentials(
                username, password, client_ip=request.client.host if request.client else "unknown"
            )
            if not user.is_admin:
                return templates.TemplateResponse(
                    request=request,
                    name="login.html",
                    context={
                        "user": None,
                        "error": "Access Denied: Only Grimmory Administrators are permitted to access this management dashboard.",
                        "app_version": settings.app_version,
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            redirect = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
            redirect.set_cookie(
                key="admin_session",
                value=user.token,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                max_age=86400,
                path="/",
            )
            return redirect
        except (httpx.TimeoutException, TimeoutError) as e:
            logger.warning(f"Admin login timed out connecting to Grimmory upstream: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "user": None,
                    "error": "Connection to Grimmory upstream server timed out. Please check that Grimmory is running and accessible.",
                    "app_version": settings.app_version,
                },
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except (httpx.ConnectError, httpx.NetworkError) as e:
            logger.warning(f"Admin login failed to connect to Grimmory upstream: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "user": None,
                    "error": "Could not connect to Grimmory upstream server. Please check that GRIMMORY_URL is configured correctly.",
                    "app_version": settings.app_version,
                },
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        except HTTPException as e:
            logger.warning(f"Admin login failed: {e.detail}")
            is_throttled = e.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "user": None,
                    "error": e.detail if is_throttled else "Invalid Grimmory credentials or account not found.",
                    "app_version": settings.app_version,
                },
                status_code=e.status_code if is_throttled else status.HTTP_401_UNAUTHORIZED,
            )
        except Exception as e:
            logger.warning(f"Admin login failed: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "user": None,
                    "error": "Invalid Grimmory credentials or account not found.",
                    "app_version": settings.app_version,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    @router.get("/admin/logout")
    async def logout_admin(response: Response) -> Response:
        redirect = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
        for cookie_name in ["admin_session", "KOMGA-SESSION", "SESSION", "remember-me"]:
            redirect.delete_cookie(cookie_name, path="/")
        return redirect

    @router.get("/admin", response_class=HTMLResponse)
    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        try:
            stats = await asyncio.wait_for(db.get_stats(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching stats for dashboard: {e}")
            stats = {
                "libraries_count": 0,
                "series_count": 0,
                "books_count": 0,
                "books_with_pages": 0,
                "books_missing_pages": 0,
                "users_count": 0,
                "formats_breakdown": {},
                "last_sync_time": None,
                "sync_status": "idle",
                "database_size_bytes": 0,
            }

        try:
            thumb_count, thumb_bytes = await asyncio.wait_for(
                asyncio.to_thread(thumbnail_cache.get_cache_size), timeout=5.0
            )
        except Exception as e:
            logger.warning(f"Error fetching thumbnail stats for dashboard: {e}")
            thumb_count, thumb_bytes = 0, 0

        try:
            users_list = await asyncio.wait_for(db.get_users_detailed(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching users list for dashboard: {e}")
            users_list = []

        calc_job_status = page_calculator.status
        calc_job_status.is_running = page_calculator.is_running
        if not page_calculator.is_running and calc_job_status.job_id is None:
            try:
                latest_job = await db.get_latest_page_calc_job()
                if latest_job:
                    calc_job_status = PageCalcStatus(
                        job_id=latest_job["id"],
                        is_running=False,
                        total_books=latest_job.get("total_books", 0),
                        processed_books=latest_job.get("processed_books", 0),
                        updated_books=latest_job.get("updated_books", 0),
                        removed_books=latest_job.get("removed_books", 0),
                        error_count=latest_job.get("error_count", 0),
                        current_book=latest_job.get("current_book") or ("Completed" if latest_job["status"] == "completed" else "-"),
                        error_message=latest_job.get("error_message"),
                        status=latest_job.get("status", "idle"),
                    )
            except Exception as e:
                logger.debug(f"Could not load latest calc job for dashboard: {e}")

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "user": user,
                "users": users_list,
                "stats": stats,
                "calc_job": calc_job_status,
                "sync_job": sync_service.status,
                "read_sync_job": user_sync.status,
                "thumbnail_count": thumb_count,
                "thumbnail_bytes": thumb_bytes,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
                "sync_interval_minutes": settings.sync_interval_minutes,
                "user_sync_interval_minutes": settings.user_sync_interval_minutes,
                "app_version": settings.app_version,
            },
        )

    # ---------------- AJAX Endpoints for WebUI ----------------

    @router.get("/admin/api/status")
    async def get_dashboard_status(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        try:
            stats = await asyncio.wait_for(db.get_stats(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching stats for dashboard API: {e}")
            stats = {}

        try:
            thumb_count, thumb_bytes = await asyncio.wait_for(
                asyncio.to_thread(thumbnail_cache.get_cache_size), timeout=5.0
            )
        except Exception as e:
            logger.warning(f"Error fetching thumbnail stats for dashboard API: {e}")
            thumb_count, thumb_bytes = 0, 0

        try:
            users_list = await asyncio.wait_for(db.get_users_detailed(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching users list for dashboard API: {e}")
            users_list = []

        calc_dict = page_calculator.status.model_dump()
        calc_dict["is_running"] = page_calculator.is_running
        if not page_calculator.is_running and page_calculator.status.job_id is None:
            try:
                latest_job = await db.get_latest_page_calc_job()
                if latest_job:
                    calc_dict = {
                        "job_id": latest_job["id"],
                        "is_running": False,
                        "total_books": latest_job.get("total_books", 0),
                        "processed_books": latest_job.get("processed_books", 0),
                        "updated_books": latest_job.get("updated_books", 0),
                        "removed_books": latest_job.get("removed_books", 0),
                        "error_count": latest_job.get("error_count", 0),
                        "current_book": latest_job.get("current_book") or ("Completed" if latest_job["status"] == "completed" else "-"),
                        "error_message": latest_job.get("error_message"),
                        "status": latest_job.get("status", "idle"),
                    }
            except Exception as e:
                logger.debug(f"Could not load latest calc job for status API: {e}")

        return {
            "stats": stats,
            "calc_job": calc_dict,
            "sync_job": sync_service.status.model_dump(),
            "read_sync_job": user_sync.status.model_dump(),
            "thumbnails": {"count": thumb_count, "bytes": thumb_bytes},
            "sync_running": sync_service.status.is_running,
            "read_sync_running": user_sync.status.is_running,
            "app_version": settings.app_version,
            "users": users_list,
        }

    @router.post("/admin/api/calculate-pages")
    async def trigger_calculate_pages(
        payload: CalcPagesRequest,
        request: Request,
    ) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if page_calculator.is_running:
            return {"status": "already_running", "message": "A calculation job is already running"}

        if payload.missing_only:
            missing_books = await db.get_books_missing_pages()
            if not missing_books:
                return {
                    "status": "no_work",
                    "message": "All books already have page counts calculated! If you wish to recalculate existing pages, use 'Recalculate All Pages'.",
                    "missing_only": True,
                    "books_to_process": 0,
                }

        page_calculator.start_calculation_job(missing_only=payload.missing_only)
        return {"status": "started", "missing_only": payload.missing_only}

    @router.post("/admin/api/stop-calculation")
    async def stop_calculate_pages(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        await page_calculator.request_stop()
        return {"status": "stopped", "message": "Page calculation job stopped"}

    @router.post("/admin/api/sync")
    async def trigger_manual_sync(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if sync_service.status.is_running:
            return {"status": "already_running", "message": "Sync is already in progress"}

        asyncio.create_task(sync_service.sync_all_metadata())
        return {"status": "started"}

    @router.post("/admin/api/sync/clear-logs")
    async def clear_sync_logs(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        sync_service.status.logs = []
        return {"status": "cleared"}

    @router.post("/admin/api/read-sync")
    async def trigger_manual_read_sync(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if user_sync.status.is_running:
            return {"status": "already_running", "message": "Read sync is already in progress"}

        asyncio.create_task(user_sync.sync_all_active_users())
        return {"status": "started"}

    @router.post("/admin/api/read-sync/clear-logs")
    async def clear_read_sync_logs(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        user_sync.status.logs = []
        return {"status": "cleared"}

    # ---------------- User Management Endpoints ----------------

    @router.get("/admin/api/users")
    async def get_users_list_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        users_list = await db.get_users_detailed()
        return {"users": users_list, "total": len(users_list)}

    @router.post("/admin/api/users/{user_id}/purge")
    async def purge_user_endpoint(user_id: int, request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if user.user_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot purge your own active admin user session.",
            )

        target_row = await db.get_user_by_id(user_id)
        if not target_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        username = target_row.get("username", f"user-{user_id}")
        AuthService.purge_user_sessions(user_id, username=username)
        user_sync.purge_user_cache(user_id)
        deleted_counts = await db.purge_user(user_id)

        logger.info(f"Admin '{user.username}' purged user '{username}' (id={user_id}): {deleted_counts}")
        return {
            "success": True,
            "message": f"User '{username}' and all associated read progress, settings, and cached sessions have been purged.",
            "deleted": deleted_counts,
        }

    @router.post("/admin/api/users/{user_id}/sync")
    async def sync_user_endpoint(user_id: int, request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        target_row = await db.get_user_by_id(user_id)
        if not target_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if not target_row.get("token"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User '{target_row.get('username')}' has no saved authentication token to connect to Grimmory.",
            )

        assigned_libs = []
        try:
            assigned_libs = json.loads(target_row.get("assigned_libraries") or "[]")
        except Exception:
            pass

        target_session = UserSession(
            user_id=target_row["id"],
            username=target_row["username"],
            token=target_row["token"],
            is_admin=bool(target_row["is_admin"]),
            assigned_library_ids=assigned_libs,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        success = await user_sync.capture_and_sync_user(target_session, force=True)
        return {
            "success": success,
            "message": f"Read progress sync completed for user '{target_row['username']}'." if success else f"Read progress sync failed for user '{target_row['username']}'."
        }

    return router
