# AI Agent Context & Project Guide: grimmory-proxy

This document provides AI coding agents and LLM-assisted tools with essential context, architectural understanding, upstream API specifications, and operational workflows for maintaining and extending **grimmory-proxy**.

---

## 1. Project Mission & Core Purpose

`grimmory-proxy` is a specialized, high-performance bridge written in Python with FastAPI that translates between two book server ecosystems:
- **Upstream Server**: [Grimmory](https://grimmory.org/) (a modern e-book, comic, and audiobook server).
- **Client Protocol**: [Komga API v1.12.0](https://komga.org/) (the open standard supported by reader clients such as Mihon / Tachiyomi, Panels, Komic, Kuro Reader, Paperback, etc.).

Grimmory deprecated its native Komga endpoint. `grimmory-proxy` emulates the full Komga v1 API specification so third-party reader apps can connect to Grimmory transparently.

---

## 2. Upstream API Specifications & Documentation

When implementing, debugging, or verifying API behavior, refer to the following authoritative upstream specifications:

1. **Grimmory API Documentation**:
   - URL: [https://grimmory.org/api/](https://grimmory.org/api/)
   - Key Grimmory behaviors to keep in mind:
     - Authentication: JWT tokens via `POST /api/v1/auth/login`. Tokens expire after 2 hours.
     - Reading Position: `POST /api/v1/books/progress` with body payload containing `{"bookId": int, "progressValid": true, "cbxProgress": {"page": int, ...}, ...}`. Note that `progressValid: true` is strictly required.
     - Reading Status Flags: Grimmory manages reading badges (`READ`, `READING`, `UNREAD`) via a dedicated endpoint: `POST /api/v1/books/status` with `{"bookIds": [...], "status": "READ" | "READING" | "UNREAD"}`. Both position and status must be coordinated.
     - Book Metadata: `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED` to preserve existing tags and authors when writing back computed page counts.
     - Media Streaming: Format-specific routes (`/api/v1/media/book/{id}/cbx/pages/{page}`, `/api/v1/media/book/{id}/raw`, etc.).

2. **Komga OpenAPI Specification**:
   - URL: [https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json)
   - Key Komga expectations:
     - Auth: HTTP Basic Auth (`/api/v1/login`), session cookies, and `X-API-Key` headers.
     - Endpoints: `/api/v1/libraries`, `/api/v1/series`, `/api/v1/books`, `/api/v1/authors`, `/api/v1/collections`, `/api/v1/readlists`, `/api/v1/users/me`.
     - Sorting: Spring Data format (`sort=readProgress.readDate,desc`, `sort=metadata.numberSort,asc`, `sort=createdDate,desc`).
     - Pagination: Spring Data `Page<T>` structure (`content`, `pageable`, `totalElements`, `totalPages`, `size`, `number`, etc.).
     - Filtering: Supports both GET query parameters and Komga v1.12+ POST condition queries (`POST /api/v1/books/list`).
     - Reader Streaming: `GET /api/v1/books/{id}/pages/{pageNumber}` and Readium WebPub manifests (`/api/v1/books/{id}/progression`).

---

## 3. Architecture & Codebase Structure

```
grimmory-proxy/
├── app/
│   ├── clients/
│   │   └── grimmory.py         # Async HTTP client interfacing with Grimmory REST endpoints.
│   │                           # Handles JWT auth, automatic token refresh, retries on 401/403.
│   ├── config.py               # Pydantic Settings (env vars: GRIMMORY_URL, SYNC_USERNAME, etc.).
│   ├── database.py             # SQLite data access layer using aiosqlite (WAL mode).
│   │                           # Manages books, series, libraries, users, read_progress tables.
│   ├── main.py                 # FastAPI application lifecycle, startup tasks, and URL redirects.
│   ├── models/
│   │   ├── grimmory.py         # Pydantic schemas representing Grimmory responses and requests.
│   │   ├── internal.py        # Internal proxy models (UserSession, PageCalculationResult).
│   │   └── komga.py            # Pydantic schemas adhering to Komga OpenAPI DTOs.
│   ├── routers/
│   │   ├── admin_webui.py      # Admin dashboard (/admin) for catalog and cache management.
│   │   ├── komga_auth.py       # Komga auth emulation (/api/v1/login, /api/v1/users/me).
│   │   ├── komga_authors.py    # Komga authors endpoints.
│   │   ├── komga_books.py      # Komga books, pages, thumbnails, list/filter queries.
│   │   ├── komga_client_settings.py # Komga client setting sync endpoints.
│   │   ├── komga_collections.py# Shelves and collections mapping.
│   │   ├── komga_libraries.py  # Komga libraries endpoints.
│   │   ├── komga_metadata.py   # Komga metadata discovery endpoints (genres, tags, publishers).
│   │   ├── komga_progress.py   # Komga reading progress and Readium endpoints.
│   │   └── komga_series.py     # Komga series endpoints.
│   ├── services/
│   │   ├── auth.py             # Multi-user session caching and authentication resolution.
│   │   ├── cache.py            # In-memory and disk TTL caching for images and pages.
│   │   ├── filter_utils.py     # Parses Komga filter/condition JSON bodies into DB queries.
│   │   ├── mapper.py           # Maps Grimmory DB rows into Komga DTOs.
│   │   ├── page_calculator.py  # Computes page counts (ADE heuristic for EPUB, 1:1 for CBZ/PDF).
│   │   ├── sync.py             # Background catalog synchronization (libraries, series, books).
│   │   └── user_sync.py        # Background multi-user reading progress synchronization.
│   ├── static/                 # Static assets for Admin WebUI (Grimmory branding).
│   └── templates/              # Jinja2 templates for Admin WebUI.
├── tests/                      # Pytest suite covering auth, API parity, mapper, and filters.
├── Dockerfile                  # Production container definition.
├── docker-compose.yml          # Local container compose configuration.
└── pytest.ini                  # Pytest configuration.
```

---

## 4. Key Implementation Patterns & Gotchas

### A. Multi-User Isolation
- Catalog metadata is synced globally using a background sync user (`SYNC_USERNAME` / `SYNC_PASSWORD`).
- Reading progress and `/api/v1/users/me` are strictly tied to the requesting user's identity (`UserSession`).
- Never leak `sync_username`'s read progress to anonymous or non-sync users querying catalog books. Progress mapping must always filter by `user_id`.

### B. Dual Read-Tracking in Reader Clients
- Standard clients issue explicit `PATCH /api/v1/books/{id}/read-progress`.
- Many mobile reader apps (Tachiyomi forks, Mihon, Panels) only stream image pages via `GET /api/v1/books/{id}/pages/{pageNumber}` without calling the PATCH endpoint.
- The proxy tracks reading positions asynchronously inside `get_book_page_image` in `komga_books.py` to ensure Grimmory progress stays up to date in real time.

### C. Grimmory Token Lifespan & 401 Retries
- Grimmory JWT tokens expire in 2 hours.
- When long-lived Komga clients authenticate via API keys, stored session tokens in the database may be expired.
- Any request made via `GrimmoryClient` handles HTTP 401 and 403 responses by attempting to renew credentials via `get_sync_token(force=True)` before failing. Background tasks pass `token=None` to ensure the global sync token is renewed and utilized.

### D. Metadata Aggregation & Reader Client Filtering
- Upstream Grimmory stores rich metadata (authors, publishers, genres, tags) primarily on book records.
- Komga clients (Komic, Panels, Mihon, Tachiyomi, Kuro Reader) query series-level metadata (`/api/v1/series`, `booksMetadata`, `/api/v1/genres`, `/api/v1/tags`, `/api/v1/publishers`, `/api/v2/publishers`).
- The proxy aggregates book-level authors, publishers, genres, and tags into series `raw_json` during sync and database startup migrations, enabling filtering across books and series.

### E. Page Calculation Engine & Catalog Enrichment
- Page counts are computed using Calibre's ADE algorithm (1,024 uncompressed chars/page) for EPUBs and 1:1 image counts for CBZ/PDF archives.
- Calculated page counts are written upstream to Grimmory via `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED` and cached in SQLite `book_pages` and `page_calc_jobs`.
- Missing pages queries check both `(page_count IS NULL OR page_count <= 0)` and missing cached `book_pages` rows (`id NOT IN (SELECT DISTINCT book_id FROM book_pages)`) to ensure all books have page entries available for reader streaming.
- Static assets (`app.js`, `style.css`) include cache-busting query strings (`?v={{ app_version }}`) and no-cache HTTP headers to prevent browsers from caching stale frontend code.
- When catalogs are already fully enriched (`0` books missing pages), the WebUI reports `no_work` with an in-page notification banner and updates the Calculation Task Status card (`UP TO DATE`) without silent failures or modal blocking.

### F. Series Identification & Slug Matching
- Series identifiers in Komga requests can be numeric IDs (e.g. `123`), composite IDs (e.g. `90-pdf`), or slug-based strings (`series-pdf`).
- `Database.find_series_by_id_or_slug` handles prefixes like `series-` to maintain compatibility with reader clients.

### G. SQLite Concurrency (WAL Mode)
- The database uses SQLite in WAL mode with `busy_timeout = 60000` ms and a centralized connection lock to avoid `database is locked` errors during heavy catalog synchronization.
- Always use the provided helper methods in `app/database.py` rather than opening unmanaged connections.

---

## 5. Development & Testing Commands

To run tests in the workspace:
```bash
.venv/bin/pytest
```

To run a specific test suite or test:
```bash
.venv/bin/pytest tests/test_api.py -k test_grimmory_read_status_and_page_progress_sync
```

When adding new features or fixing bugs:
1. Ensure all 62+ pytest unit and integration tests pass.
2. Check that upstream contract invariants with Grimmory (`https://grimmory.org/api/`) and Komga (`https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json`) are respected.
3. Commit and push changes to the target branch (e.g., `dev`).
