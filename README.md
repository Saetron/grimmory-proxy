# grimmory-proxy - Komga to Grimmory API Bridge

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Version](https://img.shields.io/badge/Version-v1.9-orange.svg)](VERSION)
[![Komga API](https://img.shields.io/badge/Komga%20API-v1.12.0-orange.svg)](https://komga.org/)
[![Theme](https://img.shields.io/badge/Theme-Grimmory%20Design-f97316.svg)](https://grimmory.org/)
[![Changelog](https://img.shields.io/badge/Changelog-Keep%20a%20Changelog-blue.svg)](CHANGELOG.md)

A high-performance Python and Docker proxy/bridge that translates the **[Grimmory API](https://grimmory.org/api/)** into the complete **[Komga API v1.12.0 specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json)**, restoring seamless compatibility for any Komga-compatible reader application—including **Mihon / Tachiyomi**, **Panels**, **Komic**, **Paperback**, and **Kuro Reader**—following Grimmory's deprecation of its native Komga endpoint.

---

## Key Features

- **Full Komga API Emulation (v1 & v2)**:
  - Implements Auth, Actuator, Libraries, Series, Books, Page Streaming, File Downloads, Reading Progress, Collections/Shelves, Authors, Genres, Publishers, Tags, Client Settings, API Keys, and SSE Events.
  - Supports both standard GET query filters and Komga v1.12+ POST search condition objects (`POST /api/v1/books/list`, `POST /api/v1/series/list`).
  - Role-aware author search and condition filtering (e.g. `?author=Name,role` or `{"author": {"value": {"name": "...", "role": "..."}, "operator": "is"}}`).
  - Spring Data multi-column sorting (e.g. `series,metadata.numberSort,asc`, `readProgress.readDate,desc`, `createdDate,desc`).
  - Book adjacency navigation (`/api/v1/books/{id}/previous` and `/api/v1/books/{id}/next`) for seamless chapter/volume switching in reader apps.
  - Virtual "Favorites" collection emulation (`/api/v1/collections/favorites-{userId}`) mirroring Grimmory's native favorite shelves.

- **High-Speed User Data & Dual Read Status Sync**:
  - Automatically captures authenticated user sessions and periodically syncs reading progress (`read_progress`) from Grimmory into a local SQLite database in the background.
  - Eliminates on-the-fly network round-trips for library browsing and bookshelf views.
  - **Dual Grimmory Synchronization**: Synchronizes reading badges (`READ`, `READING`, `UNREAD`) via `POST /api/v1/books/status` and accurate percentage/page positions via `POST /api/v1/books/progress`.
  - **Stream-Based Read Tracking**: Automatically records progress when mobile reader clients stream pages (`GET /api/v1/books/{id}/pages/{pageNumber}`) even without explicit `PATCH /read-progress` calls.
  - Automatically transitions books to `completed` upon reaching the last page.
  - Protects progress against regression (preserves completed status and page counts against transient empty upstream responses).

- **Strict Multi-User Isolation**:
  - Background catalog synchronization runs under a designated service account (`SYNC_USERNAME` / `SYNC_PASSWORD`).
  - Reading sessions, progress history, on-deck lists, favorite shelves, and `/api/v1/users/me` profiles are strictly isolated to each user's authenticated Grimmory identity.
  - Sync user progress is never leaked to reader clients.

- **Smart Standalone Series Disambiguation**:
  - Automatically derives series titles for standalone books without upstream series names.
  - Appends ` - {author}` *only* when two or more standalone books in the library share an identical title by different authors, keeping library views clean and uncluttered while preventing accidental series mergers.

- **Automated Book Page Calculation Engine**:
  - **Novels (EPUB, MOBI, AZW, FB2)**: Computes accurate page counts using Calibre's **Adobe Digital Editions (ADE)** heuristic (1,024 uncompressed characters per page, configurable via `NOVEL_CHARS_PER_PAGE`).
  - **Comics & Manga (CBZ / CBR / CBX)**: Fast 1:1 image file counting.
  - **PDF Documents**: Direct page count extraction via `pypdf`.
  - **Upstream Write-Back**: Automatically syncs calculated page counts back to Grimmory metadata (`pageCount`) via `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED`, preserving all existing tags, authors, and genres.
  - Fully asynchronous worker pool with concurrency control (`SYNC_CONCURRENCY`), graceful cancellation (`/admin/api/stop-calculation`), and automatic cleanup of stale locks on startup.

- **Grimmory Frontend URL Scheme Redirects**:
  - Direct browser requests (e.g. clicking a series, book, or library in web readers or opening proxy URLs) automatically redirect into Grimmory's Angular frontend routes:
    - `/series/{id}` &rarr; `{public_grimmory_url}/series/{seriesName}`
    - `/library/{id}` &rarr; `{public_grimmory_url}/library/{libraryId}/books`
    - `/book/{id}` &rarr; `{public_grimmory_url}/book/{bookId}`
    - `/book/{id}/read` &rarr; format reader (`/cbx-reader/book/{id}`, `/ebook-reader/book/{id}`, `/pdf-reader/book/{id}`)
    - `/collection/{id}` / `/shelf/{id}` &rarr; `{public_grimmory_url}/shelf/{shelfId}/books`
    - `/author/{id}` &rarr; `{public_grimmory_url}/author/{authorId}`
  - Komga DTOs include `metadata.links` pointing directly to Grimmory for one-click navigation in supported readers.

- **Grimmory-Themed Admin Management WebUI**:
  - Designed with Grimmory's official visual identity, dark obsidian palette, and warm orange accents.
  - Password-protected dashboard (`/admin`) restricted exclusively to **Grimmory Administrators**.
  - Real-time catalog metrics: Libraries, series, books, format distribution, cache sizes, memory usage, and page calculation progress.
  - **Connected Readers Management**: View all readers connected through the bridge with library access lists, read progress counts, and online indicators; trigger individual user syncs or purge cached user data on demand.
  - Interactive controls to trigger missing page calculations, recalculate all books, or initiate background catalog and read progress syncs with live terminal logs.
  - On-demand storage optimization and memory reclamation action.

- **Performance, Concurrency & Memory Management**:
  - Persistent SQLite database in WAL mode (`aiosqlite`) with centralized write serialization (`Database.write_db`) to prevent `database is locked` concurrency errors.
  - Proactive SQLite WAL checkpoints (`PRAGMA wal_checkpoint(TRUNCATE)`) preventing unbounded `-wal` file growth.
  - Memory management service (`MemoryService`) invoking garbage collection and releasing fragmented heap memory back to the OS.
  - Fast-path indexed queries for author lists and metadata discovery, bypassing heavy CTE scans on large catalogs.
  - Uncorrelated subqueries preventing nested table-scan freezes during author and series filtering.
  - Multi-tier caching: in-memory TTL cache for page metadata and on-disk thumbnail cache (`THUMBNAILS_DIR`).

---

## Architecture Overview

```
+-------------------------------------------------------------------+
|     Reader Clients (Mihon, Panels, Komic, Kuro Reader, etc.)      |
+-------------------------------------------------------------------+
                                  |
                   Komga API (Basic Auth / Session / Token)
                                  v
+-------------------------------------------------------------------+
|                   GRIMMORY PROXY (Komga Bridge)                   |
|                                                                   |
|  +--------------------+        +-------------------------------+  |
|  |   FastAPI Proxy    | <----> | SQLite Cache & WAL            |  |
|  |  (Komga Endpoints) |        | (bridge.db - serialized locks)|  |
|  +--------------------+        +-------------------------------+  |
|            |                              |      ^                |
|            | Per-User Auth                |      | Periodic Sync  |
|            | & Reading Progress           |      | (User Status)  |
|            v                              v      |                |
|  +--------------------+        +-------------------------------+  |
|  |   Grimmory Client  |        | Page Calc & Sync Engines      |  |
|  |  (Multi-User HTTP) |        | (Calibre ADE / CBZ / Memory)  |  |
|  +--------------------+        +-------------------------------+  |
|            ^                              |                       |
|            | Background Catalog Sync      | Write-Back            |
|            | (SYNC_USERNAME)              | (pageCount)           |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                        GRIMMORY SERVER                            |
|                  (https://grimmory.org/api/)                      |
+-------------------------------------------------------------------+
```

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `GRIMMORY_URL` | `http://localhost:8080` | URL of your Grimmory instance (without trailing slash). |
| `GRIMMORY_PUBLIC_URL` | *(empty)* | Public browser-accessible URL of Grimmory (falls back to `GRIMMORY_URL` if empty). Use when `GRIMMORY_URL` is an internal container address like `http://grimmory:6060`. |
| `SYNC_USERNAME` | *(empty)* | Dedicated username for background sync (e.g. an admin account with access to all libraries). Strictly isolated from client reading sessions. |
| `SYNC_PASSWORD` | *(empty)* | Dedicated password for background sync. |
| `SYNC_INTERVAL_MINUTES` | `30` | Interval in minutes between background metadata sync cycles (`0` to disable). |
| `USER_SYNC_INTERVAL_MINUTES` | `5` | Interval in minutes between background user read progress sync cycles (`0` to disable). |
| `SYNC_ON_STARTUP` | `true` | Whether to trigger background metadata sync on startup. |
| `SYNC_CONCURRENCY` | `6` | Concurrency limit for background series/book page inspection. |
| `NOVEL_CHARS_PER_PAGE` | `1024` | Number of characters per page for novels (Calibre ADE standard: 1024). |
| `SQLITE_CACHE_SIZE` | `-8000` | SQLite page cache size limit in KiB (negative value specifies size in KiB; default `-8000` = ~8 MB). |
| `SQLITE_MMAP_SIZE` | `67108864` | SQLite memory-mapped I/O size limit in bytes (default `67108864` = 64 MB; `0` to disable mmap). |
| `SQLITE_WAL_AUTOCHECKPOINT` | `1000` | SQLite WAL autocheckpoint page threshold (default: 1000 pages). |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated browser origins allowed by CORS. With `*` credentialed (cookie) cross-origin requests are disabled; list explicit origins to enable them. |
| `COOKIE_SECURE` | `false` | Set the `Secure` flag on the admin session cookie (enable when served over HTTPS). |
| `DATABASE_PATH` | `/app/data/bridge.db` | Path to persistent SQLite cache database. |
| `THUMBNAILS_DIR` | `/app/data/thumbnails` | Path to disk cache for cover thumbnails. |
| `PORT` | `8080` | Port the proxy listens on inside the container. |
| `HOST` | `0.0.0.0` | Bind host address. |
| `CACHE_TTL` | `300` | TTL in seconds for in-memory page metadata caches. |
| `LOG_LEVEL` | `info` | Logging verbosity (`debug`, `info`, `warning`, `error`). |

---

## Supported Endpoints

### Auth & System
- `GET /actuator/info` - Komga version and build telemetry.
- `GET /api/v1/users/me` - Authenticated user profile and assigned library permissions.
- `GET /api/v2/users/me` - Komga v2 user endpoint.
- `POST /api/v2/users/me/api-keys` - List and manage user API keys.
- `GET /api/v1/login/set-cookie` - Session cookie establishment.
- `GET /api/logout`, `POST /api/logout` - Session invalidation.
- `GET /sse/v1/events` - Server-Sent Events notification stream.

### Client Settings
- `GET /api/v1/client-settings/global/list` - Global client settings list.
- `GET /api/v1/client-settings/global/{name}` - Retrieve specific global client setting.
- `PATCH /api/v1/client-settings/global` - Update global client setting.
- `GET /api/v1/client-settings/user/list` - User-scoped client settings list.
- `GET /api/v1/client-settings/user/{name}` - Retrieve user-scoped client setting.
- `PATCH /api/v1/client-settings/user` - Update user-scoped client setting.

### Libraries
- `GET /api/v1/libraries` - List all accessible libraries (filtered per user permissions).
- `GET /api/v1/libraries/{id}` - Retrieve details for a specific library.

### Series
- `GET /api/v1/series`, `POST /api/v1/series/list` - Paginated series with search, library filtering, condition filtering, and multi-column sorting.
- `GET /api/v1/series/{id}` - Specific series details.
- `GET /api/v1/series/{id}/books` - Paginated books belonging to a series.
- `GET /api/v1/series/{id}/thumbnail` - Cover thumbnail streaming for a series.
- `GET /api/v1/series/latest` - Recently updated series.
- `GET /api/v1/series/new` - Recently added series.
- `GET /api/v1/series/updated` - Series with recent modifications.
- `GET /api/v1/series/alphabetical-groups` - Series counts grouped alphabetically.
- `GET /api/v1/series/genres` - Available series genres.
- `GET /api/v1/series/release-dates` - Available release dates.
- `POST /api/v1/series/{id}/read-progress` - Mark entire series as read.
- `DELETE /api/v1/series/{id}/read-progress` - Mark entire series as unread.
- `PUT /api/v2/series/{id}/read-progress/tachiyomi` - Tachiyomi series progress update.

### Books
- `GET /api/v1/books`, `POST /api/v1/books/list` - Paginated books query with multi-column sorting and condition filters.
- `GET /api/v1/books/ondeck`, `POST /api/v1/books/ondeck` - Continue reading list for the authenticated user.
- `GET /api/v1/books/latest`, `POST /api/v1/books/latest` - Recently added books.
- `GET /api/v1/books/released`, `POST /api/v1/books/released` - Books ordered by release date (ignores books without release dates).
- `GET /api/v1/books/{id}` - Enriched book details with accurate page count and user read progress.
- `GET /api/v1/books/{id}/previous` - Navigate to the preceding book in the series.
- `GET /api/v1/books/{id}/next` - Navigate to the subsequent book in the series.
- `GET /api/v1/books/{id}/thumbnail` - Cover thumbnail streaming with disk caching.
- `GET /api/v1/books/{id}/pages` - Synthesized `PageDto` array with dimension metadata.
- `GET /api/v1/books/{id}/pages/{pageNumber}` - Direct page image streaming with asynchronous read-tracking.
- `GET /api/v1/books/{id}/pages/{pageNumber}/thumbnail` - Scaled thumbnail for an individual page.
- `GET /api/v1/books/{id}/file` - Direct binary book file download.
- `GET /api/v1/books/{id}/manifest`, `GET /api/v1/books/{id}/manifest/divina` - Readium WebPub / Divina manifest.

### Reading Progress
- `GET /api/v1/books/{id}/read-progress` - Current reading progress for the authenticated user.
- `PATCH /api/v1/books/{id}/read-progress` - Update page progress or mark completed on Grimmory.
- `DELETE /api/v1/books/{id}/read-progress` - Reset reading progress on Grimmory.
- `PUT /api/v1/books/{id}/progression/r2` - Readium R2 locator progression update.

### Collections & Shelves
- `GET /api/v1/collections` - Collections mapped from Grimmory Shelves, Magic Shelves, and user Favorites.
- `GET /api/v1/collections/{id}` - Specific collection details.
- `GET /api/v1/collections/{id}/series` - Paginated series in a collection.
- `GET /api/v1/collections/{id}/books` - Paginated books in a collection.
- `GET /api/v1/collections/{id}/thumbnail` - Collection cover thumbnail.
- `GET /api/v1/readlists` - Read lists.

### Authors & Metadata Discovery
- `GET /api/v1/authors`, `GET /api/v2/authors` - Authors directory with role and library filtering.
- `GET /api/v1/authors/names` - List of all author names.
- `GET /api/v1/genres`, `GET /api/v2/genres` - Available genres with library filtering.
- `GET /api/v1/publishers`, `GET /api/v2/publishers` - Available publishers.
- `GET /api/v1/tags`, `GET /api/v2/tags` - Combined book and series tags.
- `GET /api/v1/tags/book` - Book-specific tags.
- `GET /api/v1/tags/series` - Series-specific tags.

### Browser Redirects to Grimmory
- `GET /series/{id}` &rarr; Redirects to `{public_grimmory_url}/series/{seriesName}`
- `GET /library/{id}` &rarr; Redirects to `{public_grimmory_url}/library/{libraryId}/books`
- `GET /book/{id}` &rarr; Redirects to `{public_grimmory_url}/book/{bookId}`
- `GET /book/{id}/read` &rarr; Redirects to reader (`/cbx-reader/book/{id}`, `/ebook-reader/book/{id}`, `/pdf-reader/book/{id}`)
- `GET /collection/{id}` / `/shelf/{id}` &rarr; Redirects to `{public_grimmory_url}/shelf/{shelfId}/books`
- `GET /author/{id}` &rarr; Redirects to `{public_grimmory_url}/author/{authorId}`

### Admin WebUI & Management API
- `GET /admin/login`, `POST /admin/login` - Secure sign-in page for Grimmory Administrators.
- `GET /admin` / `GET /dashboard` - Interactive statistics, health indicators, connected users panel, and control panel.
- `GET /admin/api/users` - List connected reader users with library access, reading progress statistics, and online indicators.
- `POST /admin/api/users/{id}/purge` - Permanently purge a user and delete all their cached read progress, settings, and sessions.
- `POST /admin/api/users/{id}/sync` - Force an immediate on-demand Grimmory read progress sync for a specific user.
- `POST /admin/api/calculate-pages` - Trigger page calculation (missing only or full recalculation).
- `POST /admin/api/stop-calculation` - Gracefully abort active calculation job.
- `POST /admin/api/sync` - Trigger on-demand metadata sync.
- `POST /admin/api/sync/clear-logs` - Clear metadata sync log history.
- `POST /admin/api/read-sync` - Trigger on-demand user read progress sync.
- `POST /admin/api/read-sync/clear-logs` - Clear read progress sync log history.
- `POST /admin/api/optimize-storage` - Trigger an immediate SQLite WAL checkpoint (`TRUNCATE`) and reclaim Python heap memory.

---

## Deployment

### Using Docker Compose (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/Saetron/grimmory-proxy.git
   cd grimmory-proxy
   ```

2. Configure `docker-compose.yml`:
   ```yaml
   services:
     grimmory-proxy:
       image: ghcr.io/saetron/grimmory-proxy:latest
       container_name: grimmory-proxy
       restart: unless-stopped
       ports:
         - "8080:8080"
       environment:
         - GRIMMORY_URL=http://grimmory:6060
         - GRIMMORY_PUBLIC_URL=https://grimmory.yourdomain.com
         - SYNC_USERNAME=admin_user
         - SYNC_PASSWORD=admin_password
         - SYNC_INTERVAL_MINUTES=30
         - USER_SYNC_INTERVAL_MINUTES=5
         - SYNC_ON_STARTUP=true
         - SYNC_CONCURRENCY=6
         - DATABASE_PATH=/app/data/bridge.db
         - THUMBNAILS_DIR=/app/data/thumbnails
       volumes:
         - ./data:/app/data
   ```

3. Start the container:
   ```bash
   docker compose up -d
   ```

4. Access the Admin Dashboard at `http://localhost:8080/admin`.

---

### Local Development Setup

To run grimmory-proxy locally without Docker:

1. Clone the repository and set up a virtual environment:
   ```bash
   git clone https://github.com/Saetron/grimmory-proxy.git
   cd grimmory-proxy
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure environment variables in a `.env` file:
   ```env
   GRIMMORY_URL=http://localhost:6060
   SYNC_USERNAME=admin
   SYNC_PASSWORD=your_password
   DATABASE_PATH=./data/bridge.db
   THUMBNAILS_DIR=./data/thumbnails
   ```

3. Launch the server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```

---

## Reader Configuration

### Connecting Komga Readers (Mihon, Panels, Komic, Kuro Reader, Paperback, etc.)

1. In your reader app, add a new **Komga** server/repository.
2. **Server URL / Address**: `http://<server-ip>:8080` (or `https://proxy.yourdomain.com` behind a reverse proxy).
3. **Username & Password**: Enter your personal Grimmory credentials.
4. Browse your libraries, read comics, manga, and novels with accurate page totals, and sync reading progress automatically.

> [!NOTE]
> Client reading sessions are strictly authenticated against your Grimmory server. Each user only sees their assigned libraries, reading progress, and personal shelves.

---

## Running Tests

Run the complete test suite locally using `pytest`:

```bash
pytest
```

Or target specific test suites:

```bash
pytest tests/test_api.py
pytest tests/test_series_collision.py
pytest tests/test_security.py
```

---

## Build & Release Workflow

The project utilizes automated GitHub Actions workflows for continuous integration, image publishing, and registry maintenance:

1. **Build & Release (`.github/workflows/ci.yml`)**:
   - Runs on manual trigger via **Workflow Dispatch**.
   - Executes the full pytest test suite.
   - Automatically increments the version in `VERSION` (or accepts a manual version override).
   - Commits and pushes the git release tag `v{version}`.
   - Builds multi-architecture Docker images (`linux/amd64`, `linux/arm64`) tagged with both `:v{version}` and `:latest`.
   - Pushes images to GitHub Container Registry (`ghcr.io/saetron/grimmory-proxy`).

2. **Package Cleanup (`.github/workflows/cleanup-packages.yml`)**:
   - Automated workflow to clean up untagged or orphaned container image layers from GHCR, keeping the registry tidy.
