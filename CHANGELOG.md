# Changelog

All notable changes to **grimmory-proxy** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Full-Stack Deployment & Reverse Proxy Guide**: Added `docs/deployment.md` and `docker-compose.stack.example.yml` detailing production multi-container setup with Grimmory, alongside ready-to-use Caddy, Nginx (with streaming buffer optimizations), and Traefik reverse proxy configurations.
- **Reader Client Setup Documentation**: Added comprehensive setup guides in `docs/clients.md` covering Mihon/Tachiyomi, Panels (iOS), Komic (Android/Flutter), Paperback, and Kuro Reader with connection troubleshooting and feature compatibility matrix.
- **Automated GitHub Release Publishing**: Added step in `.github/workflows/ci.yml` to automatically create or update GitHub Releases with notes extracted from `CHANGELOG.md` whenever a release build workflow finishes.

## [1.12] - 2026-11-02

### Added
- **Single-Book Page Calculation & Read Progress Sync Controls**:
  - Added option to trigger page calculation for a specific book on-demand (`POST /admin/api/books/{book_id}/calculate-pages` and `POST /admin/api/calculate-pages` with `book_id`).
  - Added option to push local reading progress for an individual book directly to Grimmory (`POST /admin/api/books/{book_id}/progress/push`).
  - Added option to pull upstream reading progress from Grimmory for an individual book (`POST /admin/api/books/{book_id}/progress/pull`).
- **Page Calculation Error Tracking & Book File Validation**:
  - **Persistent Problematic Files Table (`page_calc_errors`)**:
    - Automatically records calculation and inspection failures (e.g., corrupt CBZ/EPUB archives, unreadable PDFs, zero-byte downloads, or timeouts) with book ID, title, series name, library ID, format, exact file path on disk, and specific error reason.
    - Prevents false-positive page counts (no longer defaults corrupted archives or zero-byte files to 1 page).
    - Automatically deletes resolved error records when a book is successfully recalculated and enriched.
  - **WebUI Problematic Book Files Inspection Panel (`/stats`)**:
    - Added dedicated "Problematic Files & Calculation Errors" panel in the Statistics & Page Engine page with instant issue badges.
    - Displays detailed table including direct link to book, series name, format, disk file path in `<code>` block, failure reason, and occurrence timestamp.
    - Added "Retry Failed Files" action to run targeted page calculation exclusively on books with recorded errors (`POST /admin/api/calculate-pages` with `retry_failed_only: true`).
    - Added "Clear Error History" action (`POST /admin/api/page-calc/errors/clear`) to reset error history.
  - **Live Page Calculation Streaming Logs (`/logs`)**:
    - Added dedicated "Page Calculation & Errors" tab in the live logs viewer, backed by an in-memory ring buffer capped at 200 lines.
    - Streams inspection events, calculation successes, timeouts, and highlighted error messages in real time.
    - Exposed programmatic endpoints `GET /admin/api/page-calc/errors` and `POST /admin/api/logs/clear` for page calculation logs.
- **Full WebUI Rework & Modular Page Architecture**:
  - **Clean Status & Book Query Explorer Dashboard (`/admin`, `/dashboard`)**:
    - Redesigned the primary landing dashboard to focus on overall health/sync status and an interactive book search explorer.
    - Added instant, debounced book querying by title, series, or ID (`GET /admin/api/books/search`) with library filtering, cover previews, and direct read links.
  - **Dedicated Statistics & Page Engine Page (`/stats`)**:
    - Moved overall KPI cards, format distribution breakdown, storage analytics, and page calculation triggers to a dedicated `/stats` interface.
  - **Dedicated Connected Users Page (`/users`)**:
    - Relocated connected reader clients and user read tracking management to a dedicated `/users` page.
  - **Dedicated Live Logs Viewer (`/logs`)**:
    - Created dedicated `/logs` stream viewer with tabbed categories: Proxy Application Logs, Catalog Metadata Sync Logs, and User Read Sync Logs.
    - Capped log streams to the last 200 lines via ring buffer memory management (`RingBufferLogHandler`), preventing memory bloat.
    - Added instant tab switching, auto-scroll toggle, auto-polling, and one-click log clearing (`POST /admin/api/logs/clear`).
- **Persistent Configuration File (`config.json`) with WebUI Settings Management**:
  - **First Startup Initialization**: On first launch, the proxy automatically generates a persistent `config.json` populated from environment variables and defaults.
  - **File Precedence**: On all subsequent startups, `config.json` takes precedence over environment variables, allowing persistent in-app configuration.
  - **New WebUI Settings Menu (`/settings`)**:
    - Added dedicated `/settings` page in the admin interface accessible from the header navigation.
    - Categorized configuration controls for Grimmory upstream connection, sync intervals, reader image compression, and storage.
    - Live raw JSON viewer and editor for `config.json` with syntax validation.
    - Exposed `GET /admin/api/settings` and `POST /admin/api/settings` for programmatic configuration management.
    - Dynamic runtime updates for sync loops, Grimmory URL, and reader caches without restarting the container.
- **On-The-Fly Reader Image Compression & Optimization**:
  - Added async `ImageCompressor` service utilizing Pillow with non-blocking execution via `asyncio.to_thread`.
  - Transcodes large comic/book page images to WebP or JPEG on demand, reducing transfer bandwidth and page loading latency for reader clients (Komic, Panels, Mihon, Tachiyomi, Kuro Reader).
  - **Zero SSD Writes (In-Memory LRU Cache)**:
    - Implemented `MemoryPageCache` (default `IMAGE_CACHE_TYPE="memory"`, max 150 pages) caching recently accessed compressed pages strictly in RAM.
    - Page flips and back-and-forth reading hit RAM cache (<1ms) without inflicting write wear on SSD storage.
  - **Smart Image Guard & Dimension Downscaling**:
    - Optional `IMAGE_COMPRESSION_MAX_DIMENSION` (default 1920px) preserving aspect ratio using high-quality Lanczos resampling.
    - `IMAGE_COMPRESSION_MIN_SIZE_KB` threshold (default 400KB) bypassing pages that are already lightweight to conserve CPU.
    - Guard against negative compression (serves original if re-encoding yields larger file size).
    - Robust color mode handling (CMYK conversion to RGB, alpha channel flattened against white background for JPEG).
  - **Full Komga Protocol Parity**:
    - Supports client-driven conversion via `GET /api/v1/books/{id}/pages/{pageNumber}?convert=webp` (or `jpeg`, `png`) regardless of whether global compression is enabled.
  - **Admin WebUI Dashboard Controls**:
    - Added Reader Compression status and In-Memory Page Cache metrics to the admin dashboard.
    - Added `POST /admin/api/image-compression` to toggle compression and update quality/format at runtime.
    - Added `POST /admin/api/image-compression/clear-cache` to immediately flush the in-memory page cache.
    - Hooked cache eviction into `release_memory()` to free RAM during storage optimization.

### Changed
- **Repository Documentation Restructure**:
  - Streamlined root README.md into a focused overview and quickstart guide.
  - Extracted exhaustive documentation into dedicated files under docs/: docs/configuration.md (environment variables and tuning), docs/endpoints.md (Komga, Admin WebUI, and browser redirect routes), and docs/development.md (architecture, page calculation engine, testing, and CI/CD workflows).
- **Book Query Explorer Streamlining**:
  - Removed redundant "Read" button from book cards in the WebUI Book Query Explorer.
  - Completely removed user reading progress badges (`Read`, `p. X`, `Unread`) from the admin dashboard to keep the interface focused strictly on catalog management and administrative operations.
- **Refactored Read Progress & KOReader Synchronization**:
  - Consolidated `GrimmoryReadProgressRequest` payload construction (`build_grimmory_progress_request`) and asynchronous KOReader progress forwarding (`schedule_koreader_progress_sync`) in `app/services/koreader_utils.py`, eliminating duplicated synchronization logic across `komga_progress.py`, `komga_books.py`, and `koreader.py`.
  - Extracted shared `_resolve_series_or_404` and `_clear_book_progress` helpers in `app/routers/komga_progress.py`.
  - Unified `KNOWN_AUTHOR_ROLES` constant definition in `app/services/filter_utils.py` and reused `parse_raw_json` across services.

### Fixed
- **Dashboard Action Buttons (`Sync Catalog` & `Refresh All`)**: Fixed missing JavaScript handler bindings (`triggerMetadataSync` and `triggerPurgeAndRefresh`) in `app/static/app.js` that prevented the top status bar buttons on `/admin` from triggering metadata synchronization or catalog purge-and-refresh.
- **Live Runtime Settings Propagation (`POST /admin/api/settings`)**:
  - Added `GrimmoryClient.update_base_url()` so updating `grimmory_url` at runtime also updates the underlying `httpx.AsyncClient.base_url` and invalidates `_sync_token` and `_sync_token_expires_at`.
  - Updated `SyncService.start_background_loop` and `UserSyncService.start_background_loop` to evaluate `sync_interval_minutes` and `user_sync_interval_minutes` dynamically on each iteration instead of exiting permanently when initialized to `0`.
  - Propagated live updates for `cache_ttl`, `thumbnails_dir`, `image_cache_memory_max_items`, `image_cache_disk_dir`, Jinja2 template globals, and root logger level when saving settings.
- **Book Explorer Search by Series Name & Book ID**: Fixed `Database.get_books_list` search filtering to match series name (`s.name LIKE ?`) and exact numeric book ID (`b.id = ?`) in addition to book title, and fixed `file_size_kb` field mapping in `GET /admin/api/books/search`.
- **Series Metadata Backfill Author Upsert**: Fixed `ON CONFLICT(library_id, name, role)` target in `Database.connect()` series metadata backfill migration to match the composite primary key of the `authors` table.
- **Test Suite HTTPX Cookie Deprecation Warnings**: Updated `AsyncClient` instantiations in test suites to pass `cookies` at client initialization rather than per-request, eliminating all `DeprecationWarning` emissions.

### Dependencies
- Bumped `fastapi` from `>=0.158.1` to `>=0.162.0`.
- Bumped `pydantic-settings` from `>=2.16.1` to `>=2.16.2`.
- Bumped `pypdf` from `>=6.21.2` to `>=6.21.4`.
- Bumped `pytest` from `>=9.1.1` to `>=9.2.0`.
- Bumped `pytest-asyncio` from `>=1.4.0` to `>=1.4.1`.

## [1.11] - 2026-10-27

### Fixed & Performance
- **Infinite Loading on Author Page in Komic & Mobile Readers**:
  - **Decoupled Correlated Series Author Subquery**: Replaced correlated subquery (`AND b.library_id = series.library_id`) in `Database.get_series_list` with an uncorrelated subquery leveraging bound library ID parameters (`b.library_id IN (...)`). This eliminates nested full-table book scans for every series in large libraries.
  - **Short-Circuit String Guard on Author Metadata**: Added `raw_json LIKE ?` substring pre-filtering before running `json_each` in `_build_book_author_clause` and `get_series_list`. Books and series without the author name now skip JSON parsing entirely, reducing query evaluation time from tens of seconds to single-digit milliseconds.
  - **Library-Scoped Indexed Authors Fast Path**:
    - Migrated `authors` table schema to composite primary key `(library_id, name, role)` with indices `idx_authors_lib_name` and `idx_authors_name`.
    - Added automated startup schema migration and backfill from existing books.
    - Updated `Database.get_authors_list` and `Database.get_author_names_list` to utilize the indexed `authors` table even when filtering by `library_id`, eliminating expensive `WITH extracted_authors` CTE scans across all books.
    - Updated `upsert_books_batch` to populate `(library_id, name, role)` for all synced books.
  - **Role Filtering Support in v1 Author Endpoints**: Added `role` query parameter support in `/api/v1/authors` and `/api/v1/authors/names`.
  - **Restricted Library Boundary Check**: Added boundary check in `komga_series.py` (`get_series_books`) when a restricted user has no access to the requested library.
- **Database Startup Migration on Existing Volumes**: Fixed an issue where `SCHEMA_SQL` attempted to create index `idx_authors_lib_name ON authors(library_id, name)` before the automated table migration could add the column on existing pre-v1.11 databases, causing `sqlite3.OperationalError: no such column: library_id`. Deferred index creation to the automated schema migration runner.

### Dependencies
- Bumped `fastapi` from `>=0.143.1` to `>=0.158.1`.
- Bumped `pydantic-settings` from `>=2.16.0` to `>=2.16.1`.
- Bumped `pillow` from `>=12.3.0` to `>=13.0.0`.
- Bumped `pypdf` from `>=6.20.1` to `>=6.21.2`.

---

## [1.10] - 2026-10-23

### Added
- **KOReader Sync Integration**:
  - Implemented KOReader sync endpoints (`/koreader/users/auth`, `/api/koreader/users/auth`, `/koreader/syncs/progress`, `/api/koreader/syncs/progress/{book_hash}`).
  - Added partial MD5 hash calculation (`compute_koreader_hash`) sampling 1024-byte chunks across 12 seek positions per KOReader standard, supporting both file paths and binary streams.
  - Added `koreader_hash` column and index on `books` with automated database schema migration.
  - Bi-directional sync: Komga client read progress updates (`PATCH /api/v1/books/{id}/read-progress`, `/progression`, and streaming pages) now synchronize downstream to Grimmory's `/api/koreader/syncs/progress` endpoint.
- **Admin Purge & Refresh from Grimmory**:
  - Replaced the "Optimize Storage & RAM" button in the Admin WebUI dashboard with a "Purge & Refresh from Grimmory" action.
  - Added `POST /admin/api/purge-and-refresh` endpoint to clear cached catalog data, authors, and read progress (including the sync user) and trigger an immediate fresh synchronization from Grimmory.
  - Allowed self-purge for active admin sessions (`POST /admin/api/users/{id}/purge`) to purge personal read progress without invalidating the active session or returning HTTP 400.

### Fixed
- **Database Startup Migration Crash on Existing Volumes**: Fixed an issue where `SCHEMA_SQL` attempted to create an index on `books(koreader_hash)` before the table migration could add the column on existing v1.9 databases, causing `sqlite3.OperationalError: no such column: koreader_hash`. Moved index creation to the automated schema migration runner.
- **Total Synced Records Counter Accumulation**: Fixed an issue in `UserSyncService` where `total_synced_records` continuously accumulated counts across background intervals instead of querying the actual distinct count of read progress records in SQLite, which caused inflated values (e.g. 352,010).
- **Library Isolation for Author Pages in Komic & Mobile Readers**:
  - Enforced strict library filtering in `komga_books.py` (`effective_libs` retained even when `canonical_series_ids` is present) and `komga_series.py` (`get_series_books`).
  - Added library query parameter support in `/api/v1/authors` and `/api/v1/authors/names`.
  - Scoped series author subqueries in `Database.get_series_list` to require `b.library_id = series.library_id`, ensuring author pages in Komic strictly show books and series belonging to the currently selected library.

---

## [1.9] - 2026-10-20

### Added
- **Cleanup Packages CI Workflow**: Automated maintenance workflow (`.github/workflows/cleanup-packages.yml`) to clean up untagged or stale container images from GitHub Container Registry (GHCR).

### Changed & Refactored
- **Documentation Overhaul**: Updated `README.md` with comprehensive coverage of Komga v1/v2 endpoints, virtual favorites collections, book adjacency, SQLite memory & WAL configuration options, local development guide, and CI package cleanup workflows.
- **Client Auth Retry & Shelf Book Extraction**: Centralized JWT renewal and error-handling logic in `GrimmoryClient._request_with_token_retry`, and extracted shelf book processing into `get_shelf_books`.
- **Router Deduplication**: Deduplicated filter condition parsing (`extract_filter_conditions`), progress page resolution, and status queries across `komga_books`, `komga_series`, `komga_authors`, `komga_metadata`, `komga_collections`, and `komga_progress`.
- **Mapper & Sync Helper Deduplication**: Streamlined helper methods between catalog synchronization, reading progress synchronization, and Komga DTO transformation.

### Performance
- **Database Query & Indexing Optimization**:
  - Added composite indexes on `read_progress (user_id, last_modified)` and `books (library_id, series_id)`.
  - Centralized SQLite write retry wrappers and deduplicated transactional write blocks in `Database.write_db`.
  - Implemented batch queries for book progress and metadata lookups.
- **Page Calculation**: Optimized queue processing and thumbnail existence checks to reduce disk I/O overhead.

---

## [1.8] - 2026-10-19

### Added
- **Memory Management & Reclamation Service**: Introduced `MemoryService` to explicitly invoke Python garbage collection and release fragmented heap memory back to the OS.
- **Proactive SQLite WAL Checkpointing**: Automated periodic and post-sync SQLite WAL truncate checkpoints (`PRAGMA wal_checkpoint(TRUNCATE)`) to prevent unbounded `-wal` file growth and high memory mapping footprints.
- **Admin WebUI Memory Metrics**: Added live process RSS memory stats and an on-demand memory reclamation action in the Admin WebUI dashboard.

### Dependencies
- Updated `fastapi` from `>=0.142.3` to `>=0.143.1`.
- Updated `pydantic` from `>=2.13.5` to `>=2.14.1`.
- Updated `pydantic-settings` from `>=2.5.2` to `>=2.16.0`.
- Updated `pypdf` from `>=6.19.0` to `>=6.20.1`.

---

## [1.7] - 2026-10-14

### Added
- **Conditional Standalone Series Author Disambiguation**: Appends ` - {author}` to standalone series titles *only* when two or more standalone books in the library share an identical title by different authors. Preserves clean, uncluttered series titles when no collision exists.
- **Series Collision Test Suite**: Added comprehensive unit test coverage (`tests/test_series_collision.py`) verifying single vs duplicate standalone series title behaviors.

---

## [1.6] - 2026-10-14

### Fixed
- **User Read Isolation & Sync Token Leak**: Fixed an issue where reader progress sync could fall back to the global sync user credentials, ensuring reading stats and tokens remain strictly scoped to individual authenticated users.
- **Typing Import**: Fixed missing `Set` import from `typing` in `app/database.py`.

---

## [1.5] - 2026-10-12

### Added
- **Grimmory Favorites Collection Emulation**: Emulates Grimmory's native "Favorites" shelf as a user-gated virtual collection in Komga (`/api/v1/collections`, `/api/v1/collections/{id}`, `/api/v1/collections/{id}/series`, `/api/v1/collections/{id}/books`).
- **Dynamic User-Specific Collections**: Restricted favorite collections and their contents to the requesting user session so users only see their own favorite titles.

---

## [1.4] - 2026-10-12

### Fixed
- **Database Write Serialization**: Enforced in-memory write serialization (`write_lock`) in `Database.write_db` to completely eradicate SQLite `database is locked` concurrency conflicts between background catalog sync, page calculation, and reading updates.
- **Author Query Performance**: Fast-path author queries directly against the indexed `authors` table in SQLite, eliminating expensive JSON CTE scans across 30,000+ book records on `/api/v1/authors`.
- **Standalone Series Disambiguation**: Initial implementation of author-based disambiguation for standalone books lacking upstream series names.

---

## [1.3] - 2026-10-12

### Added
- **Responsive Page Calculation Cancellation**: Added `PageCalculator.request_stop()` to immediately cancel in-flight worker tasks, reset calculation flags, and transition database job status to `stopped`.
- **Stale Job Lock Recovery**: Added `Database.cleanup_stale_page_calc_jobs()` on application startup to mark orphaned or interrupted jobs as `stopped`.

### Fixed
- **Network Timeouts on Page Calculation**: Added strict 60s timeouts for book downloads and 30s timeouts for upstream Grimmory metadata updates to prevent worker pool starvation.
- **WebUI Progress Feedback**: Real-time reactive updates and cancel action handling in the Admin WebUI calculation dashboard.

---

## [1.2] - 2026-10-12

### Added
- **Structured Author Condition Queries**: Added support for Komga v1.12+ structured author condition objects (`{"author": {"value": {"name": "...", "role": "..."}, "operator": "is"}}`) in POST search/filter requests.
- **Role-Aware Author Filtering**: Supported comma-delimited author strings with role filters (e.g. `?author=Name,role`).

### Performance
- **Uncorrelated Subquery Optimization**: Rewrote author series queries to use `series.id IN (SELECT b.series_id FROM books b WHERE ...)` to avoid expensive nested table-scan freezes on large catalogs.

---

## [1.1] - 2026-10-12

### Fixed
- **WebUI Reactivity & Status Feedback**: Updated page calculation status card to report `UP TO DATE` with an informational banner when 0 books need calculation, avoiding modal blocking and silent failures.
- **Static Asset Cache-Busting**: Added `?v={{ app_version }}` query parameters to static asset links and enforced `Cache-Control: no-cache` headers on static routes to prevent browsers from serving stale CSS and JS.
- **Missing Pages Query**: Refined SQLite query to inspect both `(page_count IS NULL OR page_count <= 0)` and `NOT EXISTS (SELECT 1 FROM book_pages WHERE book_id = books.id)` to leverage primary key index probing.

---

## [1.0] - 2026-10-12

### Added
- **Komga Metadata Discovery Endpoints**:
  - Implemented `/api/v1/authors` with role and search filtering.
  - Implemented `/api/v1/publishers` and `/api/v2/publishers`.
  - Implemented `/api/v1/genres`.
  - Implemented `/api/v1/tags` (book and series tags).
- **Metadata Aggregation**: Aggregated book-level authors, publishers, genres, and tags into series metadata during catalog sync and startup database migrations.
- **Series & Book Metadata Filtering**: Integrated multi-tag, genre, publisher, and author filters into GET and POST `/api/v1/series` and `/api/v1/books` endpoints.

### Fixed
- **Automatic Token Renewal**: Enhanced `GrimmoryClient` to intercept HTTP 401/403 responses and automatically renew the 2-hour JWT token across all client methods before failing.

---

## [0.9] - 2026-10-11

### Fixed
- **Admin Login & Dashboard 504 Gateway Timeouts**: Made admin verification and dashboard metric computations non-blocking and resilient against slow upstream network responses.

---

## [0.8] - 2026-10-11

### Security
- **CodeQL Scanning Fixes**: Resolved all security warnings flagged by GitHub CodeQL static analysis.
- **Path Traversal Protection**: Sanitized thumbnail and image cache file access to prevent directory traversal exploits.
- **Open Redirect Hardening**: Validated and constrained URL redirect targets in Grimmory frontend mapping routes.
- **Session & CORS Hardening**: Tightened admin session cookie flags (`HttpOnly`, `SameSite=Lax`) and restricted credentialed CORS origins.

---

## [0.7] - 2026-10-10

### Added
- **Dual Read Progress & Badge Synchronization**: Synchronizes both reading badges (`READ`, `READING`, `UNREAD`) via `POST /api/v1/books/status` and page positions via `POST /api/v1/books/progress` to maintain consistency in Grimmory.
- **Stream-Based Read Tracking**: Asynchronously records reading positions when reader clients (e.g. Mihon, Panels) stream pages via `GET /api/v1/books/{id}/pages/{pageNumber}` without issuing explicit `PATCH` requests.
- **AI Agent Context Guide**: Added `AGENTS.md` specifying architecture, upstream API invariants, and workflows.

---

## [0.6] - 2026-10-10

### Fixed
- **WebUI Login Loop**: Fixed an infinite redirect/refresh loop on the Admin WebUI login page.

### Dependencies
- Updated `jinja2` to `>=3.1.6`.
- Updated `pytest` to `>=9.1.1`.
- Updated `python-multipart`.

---

## [0.5] - 2026-10-10

### Fixed
- **WebUI Authentication Prompt**: Stopped sending HTTP Basic Auth `WWW-Authenticate` challenge headers on Admin WebUI routes to prevent native browser login popups from masking the custom login UI.

### Dependencies & CI
- Updated `fastapi` (`>=0.142.3`), `uvicorn` (`>=0.54.0`), `pydantic` (`>=2.13.5`), `aiosqlite` (`>=0.22.1`), `pypdf` (`>=6.19.0`), `pillow` (`>=12.3.0`), `httpx` (`>=0.28.1`), and `pytest-asyncio` (`>=1.4.0`).
- Upgraded GitHub Actions workflows to `actions/checkout@v7`, `actions/setup-python@v7`, `docker/build-push-action@v7`, and `docker/metadata-action@v6`.

---

## [0.4] - 2026-10-10

### Added
- **Connected Users & Reader Management**: Added connected user tracking to the Admin WebUI with library access lists, active reading progress counts, online badges, and user cache purge controls.

### Security
- **Non-Root Docker Container**: Updated `Dockerfile` to create and run as an unprivileged `appuser`.
- **Credential Hashing & Brute-Force Throttling**: Hashed cached user credentials with PBKDF2/SHA256, implemented login rate-limiting, and bounded in-memory session caches.

### Fixed
- **Database Locking in SQLite**: Configured SQLite WAL mode (`PRAGMA journal_mode=WAL`), set `busy_timeout = 60000`, and added transactional chunking with retry logic.
- **Version Display**: Injected `VERSION` into the Docker build via `APP_VERSION` build argument to fix version rendering in the Admin WebUI.

---

## [0.3] - 2026-10-10

### Added
- **Book Adjacency Endpoints**: Implemented `/api/v1/books/{id}/previous` and `/api/v1/books/{id}/next` to enable seamless chapter-to-chapter transitions in reader clients.
- **Automatic Read Completion**: Automatically transitions book reading status to completed when a user reaches the last page of a book.

---

## [0.2] - 2026-10-10

### Added
- **Grimmory Frontend URL Scheme Redirects**: Browser requests to proxy routes (`/series/{id}`, `/book/{id}`, `/library/{id}`, `/book/{id}/read`, `/shelf/{id}`, `/author/{id}`) automatically redirect to Grimmory's Angular web UI.
- **Grimmory-Themed WebUI Redesign**: Complete redesign of Admin WebUI matching Grimmory branding, obsidian dark palette, and typography.
- **Series Read Progress Endpoints**: Implemented `/api/v1/series/{id}/read-progress`.
- **Multi-Column Sorting**: Supported Spring Data multi-column sorting (e.g. `readProgress.readDate,desc`, `metadata.numberSort,asc`).
- **Unreleased Book Filtering**: Excluded future/unreleased books from Komga new releases queries.

---

## [0.1] - 2026-10-07

### Added
- **Komga v1.12.0 API Emulation**: Full emulation of the Komga API specification for Grimmory servers:
  - Authentication (HTTP Basic Auth, session cookies, `X-API-Key` headers, `/api/v1/users/me`).
  - Libraries (`/api/v1/libraries`), Series (`/api/v1/series`), and Books (`/api/v1/books`).
  - Media streaming for CBZ, CBR, CBX, PDF, and EPUB formats.
  - Readium WebPub manifests (`/progression`) and KMreader compatibility.
  - Server-Sent Events (SSE) notification stream.
- **Page Calculation Engine**:
  - Novels (EPUB/MOBI/AZW/FB2): Calibre ADE heuristic (1,024 uncompressed characters per page).
  - Comics & Manga (CBZ/CBR/CBX): 1:1 image page counting.
  - PDF: Native page count extraction via `pypdf`.
  - Upstream Grimmory metadata write-back with `replaceMode=REPLACE_WHEN_PROVIDED`.
- **Database & Architecture**:
  - Asynchronous SQLite architecture via `aiosqlite`.
  - Grimmory HTTP client with JWT authentication and auto-token management.
  - Background catalog synchronization worker for libraries, series, and books.
  - Background user read progress synchronization worker.
  - Multi-user isolation guaranteeing private read tracking and user profiles.
- **Admin Management WebUI**:
  - Admin dashboard at `/admin` for catalog statistics and cache inspection.
  - Manual triggers for catalog sync, page calculation, and cache purging.
- **Containerization & CI**:
  - Docker container packaging and multi-platform publishing (`amd64`, `arm64`).
  - Comprehensive unit and integration test suite with pytest.

---

[1.12]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.12
[1.11]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.11
[1.10]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.10
[1.9]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.9
[1.8]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.8
[1.7]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.7
[1.6]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.6
[1.5]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.5
[1.4]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.4
[1.3]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.3
[1.2]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.2
[1.1]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.1
[1.0]: https://github.com/Saetron/grimmory-proxy/releases/tag/v1.0
[0.9]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.9
[0.8]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.8
[0.7]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.7
[0.6]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.6
[0.5]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.5
[0.4]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.4
[0.3]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.3
[0.2]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.2
[0.1]: https://github.com/Saetron/grimmory-proxy/releases/tag/v0.1
