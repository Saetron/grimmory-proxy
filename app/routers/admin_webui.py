import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.models.internal import PageCalcStatus, UserSession
from app.services.auth import AuthService
from app.services.cache import memory_cache, thumbnail_cache
from app.services.image_compressor import image_compressor
from app.services.log_buffer import proxy_log_handler
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
    retry_failed_only: bool = False


class ImageCompressionConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    format: Optional[Literal["webp", "jpeg", "original"]] = None
    quality: Optional[int] = Field(None, ge=1, le=100)
    max_dimension: Optional[int] = Field(None, ge=0)
    min_size_kb: Optional[int] = Field(None, ge=0)
    cache_type: Optional[Literal["memory", "none", "disk"]] = None


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

    async def _resolve_calc_job_status() -> PageCalcStatus:
        calc_job_status = page_calculator.status.model_copy()
        calc_job_status.is_running = page_calculator.is_running
        calc_job_status.logs = list(page_calculator.logs)
        try:
            calc_job_status.recent_errors = await db.get_page_calc_errors(limit=50)
        except Exception:
            calc_job_status.recent_errors = []

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
                        logs=list(page_calculator.logs),
                        recent_errors=calc_job_status.recent_errors,
                    )
            except Exception as e:
                logger.debug(f"Could not load latest calc job for dashboard: {e}")
        return calc_job_status

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

        try:
            user_sync.status.total_synced_records = await asyncio.wait_for(
                db.get_total_read_progress_count(), timeout=5.0
            )
        except Exception as e:
            logger.debug(f"Error fetching total synced records for dashboard: {e}")

        calc_job_status = await _resolve_calc_job_status()

        try:
            libraries = await db.get_libraries()
        except Exception:
            libraries = []

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "user": user,
                "active_page": "dashboard",
                "libraries": libraries,
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
                "image_compression": {
                    "enabled": settings.image_compression_enabled,
                    "format": settings.image_compression_format,
                    "quality": settings.image_compression_quality,
                    "max_dimension": settings.image_compression_max_dimension,
                    "min_size_kb": settings.image_compression_min_size_kb,
                    "cache_type": settings.image_cache_type,
                    "cache_stats": image_compressor.get_cache_stats(),
                },
            },
        )

    @router.get("/stats", response_class=HTMLResponse)
    @router.get("/admin/stats", response_class=HTMLResponse)
    async def stats_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        try:
            stats = await asyncio.wait_for(db.get_stats(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching stats for stats page: {e}")
            stats = {
                "libraries_count": 0, "series_count": 0, "books_count": 0,
                "books_with_pages": 0, "books_missing_pages": 0, "formats_breakdown": {},
                "database_size_bytes": 0, "wal_size_bytes": 0,
            }

        try:
            thumb_count, thumb_bytes = await asyncio.wait_for(
                asyncio.to_thread(thumbnail_cache.get_cache_size), timeout=5.0
            )
        except Exception:
            thumb_count, thumb_bytes = 0, 0

        calc_job_status = await _resolve_calc_job_status()
        try:
            page_calc_errors = await db.get_page_calc_errors(limit=100)
        except Exception as e:
            logger.debug(f"Error loading page_calc_errors for stats page: {e}")
            page_calc_errors = []

        return templates.TemplateResponse(
            request=request,
            name="stats.html",
            context={
                "user": user,
                "active_page": "stats",
                "stats": stats,
                "calc_job": calc_job_status,
                "page_calc_errors": page_calc_errors,
                "thumbnail_count": thumb_count,
                "thumbnail_bytes": thumb_bytes,
                "app_version": settings.app_version,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
            },
        )

    @router.get("/users", response_class=HTMLResponse)
    @router.get("/admin/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        try:
            users_list = await asyncio.wait_for(db.get_users_detailed(), timeout=5.0)
        except Exception as e:
            logger.warning(f"Error fetching users list for users page: {e}")
            users_list = []

        return templates.TemplateResponse(
            request=request,
            name="users.html",
            context={
                "user": user,
                "active_page": "users",
                "users": users_list,
                "app_version": settings.app_version,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
            },
        )

    @router.get("/logs", response_class=HTMLResponse)
    @router.get("/admin/logs", response_class=HTMLResponse)
    async def logs_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        return templates.TemplateResponse(
            request=request,
            name="logs.html",
            context={
                "user": user,
                "active_page": "logs",
                "sync_logs": sync_service.status.logs[-200:],
                "read_sync_logs": user_sync.status.logs[-200:],
                "proxy_logs": proxy_log_handler.get_logs()[-200:],
                "page_calc_logs": page_calculator.logs[-200:],
                "app_version": settings.app_version,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
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

        try:
            user_sync.status.total_synced_records = await asyncio.wait_for(
                db.get_total_read_progress_count(), timeout=5.0
            )
        except Exception as e:
            logger.debug(f"Error fetching total synced records for dashboard API: {e}")

        calc_job_status = await _resolve_calc_job_status()

        page_calc_errors = []
        try:
            page_calc_errors = await db.get_page_calc_errors(limit=50)
        except Exception as e:
            logger.debug(f"Error fetching page_calc_errors for status API: {e}")

        return {
            "stats": stats,
            "calc_job": calc_job_status.model_dump(),
            "page_calc_errors": page_calc_errors,
            "page_calc_logs": page_calculator.logs[-200:],
            "sync_job": sync_service.status.model_dump(),
            "read_sync_job": user_sync.status.model_dump(),
            "thumbnails": {"count": thumb_count, "bytes": thumb_bytes},
            "sync_running": sync_service.status.is_running,
            "read_sync_running": user_sync.status.is_running,
            "app_version": settings.app_version,
            "users": users_list,
            "image_compression": {
                "enabled": settings.image_compression_enabled,
                "format": settings.image_compression_format,
                "quality": settings.image_compression_quality,
                "max_dimension": settings.image_compression_max_dimension,
                "min_size_kb": settings.image_compression_min_size_kb,
                "cache_type": settings.image_cache_type,
                "cache_stats": image_compressor.get_cache_stats(),
            },
        }

    @router.post("/admin/api/calculate-pages")
    async def trigger_calculate_pages(
        payload: CalcPagesRequest,
        request: Request,
    ) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if page_calculator.is_running:
            return {"status": "already_running", "message": "A calculation job is already running"}

        if payload.retry_failed_only:
            failed_books = await db.get_books_with_page_calc_errors()
            if not failed_books:
                return {
                    "status": "no_work",
                    "message": "No problematic books with errors found to retry!",
                    "retry_failed_only": True,
                    "books_to_process": 0,
                }
            page_calculator.start_calculation_job(missing_only=False, retry_failed_only=True)
            return {"status": "started", "retry_failed_only": True, "books_to_process": len(failed_books)}

        if payload.missing_only:
            missing_books = await db.get_books_missing_pages()
            if not missing_books:
                return {
                    "status": "no_work",
                    "message": "All books already have page counts calculated! If you wish to recalculate existing pages, use 'Recalculate All Pages'.",
                    "missing_only": True,
                    "books_to_process": 0,
                }

        page_calculator.start_calculation_job(missing_only=payload.missing_only, retry_failed_only=False)
        return {"status": "started", "missing_only": payload.missing_only}

    @router.get("/admin/api/page-calc/errors")
    async def get_page_calc_errors_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        errors = await db.get_page_calc_errors(limit=200)
        return {"errors": errors, "total": len(errors)}

    @router.post("/admin/api/page-calc/errors/clear")
    async def clear_page_calc_errors_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        await db.clear_page_calc_errors()
        page_calculator.status.recent_errors = []
        return {"status": "cleared", "message": "Page calculation error history has been cleared."}

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

    @router.post("/admin/api/optimize-storage")
    async def optimize_storage_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        try:
            await db.checkpoint("TRUNCATE")
        except Exception as e:
            logger.warning(f"Error during manual WAL checkpoint: {e}")

        from app.services.memory import release_memory
        release_memory()

        stats = await db.get_stats()
        return {
            "success": True,
            "message": "WAL checkpoint completed and memory reclaimed successfully.",
            "stats": stats,
        }

    @router.post("/admin/api/purge-and-refresh")
    async def purge_and_refresh_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        # 1. Purge all read progress and R2 progression records from SQLite
        deleted_counts = await db.purge_all_read_progress()

        # 2. Reset user sync cooldowns and locks
        user_sync.clear_all_user_caches()
        user_sync.status.total_synced_records = 0

        # 3. Trigger fresh metadata sync and fresh read status sync (including sync user)
        async def _run_full_refresh():
            try:
                await sync_service.sync_all_metadata()
            except Exception as e:
                logger.error(f"Error during metadata sync in purge-and-refresh: {e}")
            try:
                await user_sync.sync_all_active_users()
            except Exception as e:
                logger.error(f"Error during user read progress sync in purge-and-refresh: {e}")

        asyncio.create_task(_run_full_refresh())

        logger.info(f"Admin '{user.username}' initiated Purge & Refresh from Grimmory: {deleted_counts}")
        return {
            "success": True,
            "message": "Local read progress purged. Fresh synchronization of catalog metadata and read progress from Grimmory (including sync user) has been started.",
            "deleted": deleted_counts,
        }

    # ---------------- User Management Endpoints ----------------

    @router.get("/admin/api/users")
    async def get_users_list_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        users_list = await db.get_users_detailed()
        return {"users": users_list, "total": len(users_list)}

    @router.post("/admin/api/users/{user_id}/purge")
    async def purge_user_endpoint(user_id: int, request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)

        is_self = (user.user_id == user_id)
        target_row = await db.get_user_by_id(user_id)
        if not target_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        username = target_row.get("username", f"user-{user_id}")
        user_sync.purge_user_cache(user_id)

        if is_self:
            # Purge read progress and progression, but preserve the active admin user account and session token
            deleted_counts = await db.purge_user_read_progress(user_id)
            user_sync.status.total_synced_records = await db.get_total_read_progress_count()
            logger.info(f"Admin '{user.username}' purged own read progress (id={user_id}): {deleted_counts}")
            return {
                "success": True,
                "message": f"Read progress for '{username}' has been purged. Click 'Sync' to re-import fresh status from Grimmory.",
                "deleted": deleted_counts,
            }
        else:
            AuthService.purge_user_sessions(user_id, username=username)
            deleted_counts = await db.purge_user(user_id)
            user_sync.status.total_synced_records = await db.get_total_read_progress_count()
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

    # ---------------- Reader Image Optimization Endpoints ----------------

    @router.post("/admin/api/image-compression")
    async def update_image_compression_endpoint(
        payload: ImageCompressionConfigRequest,
        request: Request,
    ) -> Dict[str, Any]:
        user = await require_admin_session(request)

        if payload.enabled is not None:
            settings.image_compression_enabled = payload.enabled
        if payload.format is not None:
            settings.image_compression_format = payload.format
        if payload.quality is not None:
            settings.image_compression_quality = payload.quality
        if payload.max_dimension is not None:
            settings.image_compression_max_dimension = payload.max_dimension
        if payload.min_size_kb is not None:
            settings.image_compression_min_size_kb = payload.min_size_kb
        if payload.cache_type is not None:
            settings.image_cache_type = payload.cache_type

        status_text = "enabled" if settings.image_compression_enabled else "disabled"
        logger.info(
            f"Admin '{user.username}' updated image compression config: "
            f"enabled={settings.image_compression_enabled}, format={settings.image_compression_format}, "
            f"quality={settings.image_compression_quality}, max_dimension={settings.image_compression_max_dimension}, "
            f"cache_type={settings.image_cache_type}"
        )
        return {
            "success": True,
            "message": f"Reader image compression is now {status_text}.",
            "image_compression": {
                "enabled": settings.image_compression_enabled,
                "format": settings.image_compression_format,
                "quality": settings.image_compression_quality,
                "max_dimension": settings.image_compression_max_dimension,
                "min_size_kb": settings.image_compression_min_size_kb,
                "cache_type": settings.image_cache_type,
                "cache_stats": image_compressor.get_cache_stats(),
            },
        }

    @router.post("/admin/api/image-compression/clear-cache")
    async def clear_image_compression_cache_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        image_compressor.clear_cache()
        logger.info(f"Admin '{user.username}' cleared image compression page cache.")
        return {
            "success": True,
            "message": "In-memory reader image cache cleared successfully.",
            "cache_stats": image_compressor.get_cache_stats(),
        }

    # ---------------- Configuration File & Settings Endpoints ----------------

    @router.get("/settings", response_class=HTMLResponse)
    @router.get("/admin/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        user = await AuthService.get_current_user_optional(request)
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

        cfg_dict = settings.to_config_dict()
        cfg_path = str(settings.resolve_config_path())
        raw_json_str = json.dumps(cfg_dict, indent=2)

        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "user": user,
                "active_page": "settings",
                "settings": cfg_dict,
                "config_path": cfg_path,
                "raw_json": raw_json_str,
                "app_version": settings.app_version,
                "grimmory_url": settings.grimmory_url,
                "grimmory_public_url": settings.public_grimmory_url,
            },
        )

    @router.get("/admin/api/settings")
    async def get_settings_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        cfg_dict = settings.to_config_dict()
        cfg_path = str(settings.resolve_config_path())
        return {
            "success": True,
            "settings": cfg_dict,
            "config_path": cfg_path,
            "raw_json": json.dumps(cfg_dict, indent=2),
        }

    @router.post("/admin/api/settings")
    async def save_settings_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

        updates = body.get("settings", body)
        if not isinstance(updates, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Settings payload must be a JSON object")

        old_grimmory_url = settings.grimmory_url
        old_sync_creds = (settings.sync_username, settings.sync_password)

        updated_dict = settings.save_config_file(updates)

        # Propagate dynamic changes to live singletons
        if settings.grimmory_url != old_grimmory_url:
            grimmory_client.update_base_url(settings.grimmory_url)
        elif (settings.sync_username, settings.sync_password) != old_sync_creds:
            grimmory_client._sync_token = None
            grimmory_client._sync_token_expires_at = None

        templates.env.globals["grimmory_url"] = settings.grimmory_url
        templates.env.globals["grimmory_public_url"] = settings.public_grimmory_url
        memory_cache.ttl = settings.cache_ttl
        thumbnail_cache.base_dir = Path(settings.thumbnails_dir)
        image_compressor.memory_cache.max_items = settings.image_cache_memory_max_items
        image_compressor.disk_cache.base_dir = Path(settings.image_cache_disk_dir)
        image_compressor.clear_cache()

        log_level = getattr(logging, settings.log_level.upper(), None)
        if isinstance(log_level, int):
            logging.getLogger().setLevel(log_level)

        logger.info(f"Admin '{user.username}' saved configuration to {settings.resolve_config_path()}")
        return {
            "success": True,
            "message": f"Configuration saved successfully to {settings.resolve_config_path()}.",
            "settings": updated_dict,
            "config_path": str(settings.resolve_config_path()),
        }

    # ---------------- Book Explorer & Logs API Endpoints ----------------

    @router.get("/admin/api/books/search")
    async def search_books_endpoint(
        request: Request,
        query: Optional[str] = Query(None),
        library_id: Optional[int] = Query(None),
        limit: int = Query(24, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> Dict[str, Any]:
        user = await require_admin_session(request)

        records, total = await db.get_books_list(
            library_id=library_id,
            search=query.strip() if query else None,
            offset=offset,
            limit=limit,
            sort_by="created",
            sort_dir="desc",
            user_id=user.user_id if user else None,
        )

        formatted_books = []
        for r in records:
            formatted_books.append({
                "id": r["id"],
                "name": r.get("name", ""),
                "series_id": r.get("series_id"),
                "series_name": r.get("series_name") or r.get("series_id", ""),
                "number": r.get("number", 0.0),
                "book_type": r.get("book_type") or "EPUB",
                "page_count": r.get("page_count", 0),
                "file_size": r.get("file_size_kb", 0) or r.get("file_size", 0),
                "library_id": r.get("library_id"),
                "user_completed": bool(r.get("user_completed")),
                "user_page": r.get("user_page", 0),
            })

        return {"books": formatted_books, "total": total, "limit": limit, "offset": offset}

    @router.get("/admin/api/logs")
    async def get_logs_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        return {
            "sync_logs": sync_service.status.logs[-200:],
            "read_sync_logs": user_sync.status.logs[-200:],
            "proxy_logs": proxy_log_handler.get_logs()[-200:],
            "page_calc_logs": page_calculator.logs[-200:],
        }

    @router.post("/admin/api/logs/clear")
    async def clear_logs_endpoint(request: Request) -> Dict[str, Any]:
        user = await require_admin_session(request)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        log_type = body.get("type", "all")
        if log_type in ("sync", "all"):
            sync_service.status.logs.clear()
        if log_type in ("read-sync", "read_sync", "all"):
            user_sync.status.logs.clear()
        if log_type in ("proxy", "all"):
            proxy_log_handler.clear()
        if log_type in ("page-calc", "page_calc", "all"):
            page_calculator.clear_logs()

        logger.info(f"Admin '{user.username}' cleared log buffer ({log_type})")
        return {"success": True, "message": f"Logs cleared ({log_type})."}

    return router
