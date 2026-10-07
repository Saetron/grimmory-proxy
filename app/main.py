import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.clients.grimmory import grimmory_client
from app.config import settings
from app.database import Database
from app.routers.admin_webui import get_admin_router
from app.routers.komga_auth import router as auth_router
from app.routers.komga_authors import get_authors_router
from app.routers.komga_books import get_books_router
from app.routers.komga_collections import router as collections_router
from app.routers.komga_libraries import get_libraries_router
from app.routers.komga_progress import get_progress_router
from app.routers.komga_series import get_series_router
from app.services.page_calculator import PageCalculator
from app.services.sync import SyncService
from app.services.user_sync import user_sync_service

# Configure logging
log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("grimmory_proxy.main")

# Database & Core Services
db = Database(db_path=settings.database_path)
user_sync_service.set_db(db)
page_calculator = PageCalculator(db=db)
sync_service = SyncService(db=db, page_calculator=page_calculator)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Grimmory Proxy (Komga API Bridge)...")
    settings.ensure_directories()
    await db.connect()

    # Start background sync task
    sync_task = asyncio.create_task(sync_service.start_background_loop())

    yield

    logger.info("Shutting down Grimmory Proxy...")
    sync_task.cancel()
    await grimmory_client.close()


app = FastAPI(
    title="Komga API",
    version="1.12.0",
    description="Komga-compatible API proxy translating to Grimmory backend with automatic novel/CBZ page counting and admin dashboard",
    lifespan=lifespan,
)

# CORS Middleware (allows web readers like Komic, Mihon, Tachiyomi, Kuro, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Mount Routers
app.include_router(auth_router)
app.include_router(get_libraries_router(db))
app.include_router(get_series_router(db))
app.include_router(get_books_router(db, page_calculator))
app.include_router(get_progress_router(db))
app.include_router(collections_router)
app.include_router(get_authors_router(db))
app.include_router(get_admin_router(db, page_calculator, sync_service))


@app.get("/")
async def root_redirect(request: Request):
    """
    If accessed by a browser, redirect to the Admin WebUI dashboard.
    Otherwise return Komga API info.
    """
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    return {
        "app": "Grimmory Proxy",
        "description": "Grimmory to Komga API Bridge",
        "version": "1.12.0",
        "status": "UP",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
