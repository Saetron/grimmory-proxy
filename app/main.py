import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import re
from urllib.parse import quote
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
from app.routers.komga_client_settings import get_client_settings_router
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

    # Start background sync tasks
    sync_task = asyncio.create_task(sync_service.start_background_loop())
    user_sync_task = asyncio.create_task(user_sync_service.start_background_loop())

    yield

    logger.info("Shutting down Grimmory Proxy...")
    sync_task.cancel()
    user_sync_task.cancel()
    await grimmory_client.close()


app = FastAPI(
    title="Komga API",
    version="1.12.0",
    description="Komga-compatible API proxy translating to Grimmory backend with automatic novel/CBZ page counting and admin dashboard",
    lifespan=lifespan,
)

# CORS Middleware (allows web readers like Komic, Mihon, Tachiyomi, Kuro, etc.)
# Credentialed cross-origin requests are only enabled for an explicit origin allow-list;
# a wildcard origin with credentials would let any site act with the admin's cookie.
_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Mount Routers
app.include_router(auth_router)
app.include_router(get_client_settings_router(db))
app.include_router(get_libraries_router(db))
app.include_router(get_series_router(db))
app.include_router(get_books_router(db, page_calculator))
app.include_router(get_progress_router(db))
app.include_router(collections_router)
app.include_router(get_authors_router(db))
app.include_router(get_admin_router(db, page_calculator, sync_service, user_sync_service))


@app.get("/series/{series_id:path}")
@app.get("/series")
async def redirect_series(request: Request, series_id: str = ""):
    """
    Redirect series browser clicks directly to Grimmory web UI.
    Translates internal proxy series IDs to Grimmory's URL scheme (/series/:seriesName).
    """
    query = f"?{request.url.query}" if request.url.query else ""
    clean_id = series_id.strip("/")
    if not clean_id:
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/series{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    first_segment = clean_id.split("/")[0]

    # Resolve series record to obtain canonical seriesName recognized by Grimmory
    series = await db.get_series_by_id(first_segment)
    if not series:
        series = await db.find_series_by_id_or_slug(first_segment)

    if series and series.get("name"):
        target_name = series["name"]
    else:
        # Fallback if metadata is not in DB: strip library id prefix and hex hash
        fallback = first_segment
        if "-" in fallback and fallback.split("-")[0].isdigit():
            fallback = fallback.split("-", 1)[1]
        fallback = re.sub(r"-[0-9a-f]{8}$", "", fallback)
        target_name = fallback

    encoded_name = quote(target_name, safe="")
    return RedirectResponse(
        url=f"{settings.public_grimmory_url}/series/{encoded_name}{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@app.get("/book/{book_id:path}")
@app.get("/books/{book_id:path}")
@app.get("/book")
@app.get("/books")
async def redirect_book(request: Request, book_id: str = ""):
    """
    Redirect book browser clicks directly to Grimmory web UI.
    Grimmory routes book metadata center to /book/:bookId,
    and reading sessions to reader components (/cbx-reader/book/:id, /ebook-reader/book/:id, /pdf-reader/book/:id).
    """
    query = f"?{request.url.query}" if request.url.query else ""
    clean_id = book_id.strip("/")
    if not clean_id:
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/all-books{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    parts = clean_id.split("/")
    clean_book_id = parts[0]
    is_reading = len(parts) > 1 and parts[1].lower() in ["read", "readium", "pages", "page"]

    if is_reading and clean_book_id.isdigit():
        book = await db.get_book_by_id(int(clean_book_id))
        btype = (book.get("book_type") if book else "EPUB") or "EPUB"
        btype = btype.upper()
        if btype in ["CBX", "CBZ"]:
            reader_route = f"cbx-reader/book/{clean_book_id}"
        elif btype == "PDF":
            reader_route = f"pdf-reader/book/{clean_book_id}"
        elif btype == "AUDIOBOOK":
            reader_route = f"audiobook-player/book/{clean_book_id}"
        else:
            reader_route = f"ebook-reader/book/{clean_book_id}"
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/{reader_route}{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    return RedirectResponse(
        url=f"{settings.public_grimmory_url}/book/{clean_book_id}{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@app.get("/library/{library_id:path}")
@app.get("/libraries/{library_id:path}")
@app.get("/library")
@app.get("/libraries")
async def redirect_library(request: Request, library_id: str = ""):
    """
    Redirect library browser clicks directly to Grimmory web UI.
    Grimmory routes library book browsing to /library/:libraryId/books.
    """
    query = f"?{request.url.query}" if request.url.query else ""
    clean_id = library_id.strip("/")
    if not clean_id:
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/all-books{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    clean_lib_id = clean_id.split("/")[0]
    return RedirectResponse(
        url=f"{settings.public_grimmory_url}/library/{clean_lib_id}/books{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@app.get("/shelf/{shelf_id:path}")
@app.get("/shelves/{shelf_id:path}")
@app.get("/collection/{collection_id:path}")
@app.get("/collections/{collection_id:path}")
async def redirect_shelf(request: Request, shelf_id: str = "", collection_id: str = ""):
    """
    Redirect shelf/collection clicks to Grimmory web UI (/shelf/:shelfId/books).
    """
    sid = shelf_id or collection_id
    query = f"?{request.url.query}" if request.url.query else ""
    clean_id = sid.strip("/")
    if not clean_id:
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/dashboard{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    first_part = clean_id.split("/")[0]
    return RedirectResponse(
        url=f"{settings.public_grimmory_url}/shelf/{first_part}/books{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@app.get("/author/{author_id:path}")
@app.get("/authors/{author_id:path}")
@app.get("/authors")
async def redirect_author(request: Request, author_id: str = ""):
    """
    Redirect author clicks to Grimmory web UI (/author/:authorId or /authors).
    """
    query = f"?{request.url.query}" if request.url.query else ""
    clean_id = author_id.strip("/")
    if not clean_id:
        return RedirectResponse(
            url=f"{settings.public_grimmory_url}/authors{query}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    clean_author_id = clean_id.split("/")[0]
    return RedirectResponse(
        url=f"{settings.public_grimmory_url}/author/{clean_author_id}{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


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
        "version": settings.app_version,
        "komga_version": "1.12.0",
        "status": "UP",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
