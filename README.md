# grimmory-proxy - Komga to Grimmory API Bridge

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Komga API](https://img.shields.io/badge/Komga%20API-v1.12.0-orange.svg)](https://komga.org/)
[![Theme](https://img.shields.io/badge/Theme-Grimmory%20Design-f97316.svg)](https://grimmory.org/)

A high-performance Python and Docker proxy/bridge that translates the **Grimmory API** (`https://grimmory.org/api/`) into the complete **Komga API specification** (`https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json`), restoring seamless compatibility for any Komga-compatible reader application such as **Mihon / Tachiyomi**, **Komic**, **Paperback**, and **Kuro Reader** after the deprecation of Grimmory's native Komga endpoint.

---

## Key Features

- **Full Komga API Emulation**:
  - Implements Auth, Actuator, Libraries, Series, Books, Page Streaming, File Downloads, Reading Progress, Collections/Shelves, Authors, Client Settings, API Keys, and SSE Events.
  - Multi-column and Spring Data sorting support (e.g. `series,metadata.numberSort,asc`, `readProgress.readDate,desc`, `createdDate,desc`).
- **High-Speed User Data & Read Status Sync**:
  - Automatically records authenticated user sessions and periodically syncs reading progress (`read_progress`) from Grimmory into local SQLite cache in the background.
  - Drastically speeds up client response times (eliminates on-the-fly network bottlenecks for library requests).
  - Protects progress against regression (preserves completed status and page progress against transient empty upstream responses).
- **Grimmory Frontend URL Scheme Redirects**:
  - Direct browser requests (e.g. clicking a series, book, or library in web readers or opening proxy URLs) automatically map into Grimmory's Angular frontend routes:
    - `/series/{id}` &rarr; `{public_grimmory_url}/series/{seriesName}`
    - `/library/{id}` &rarr; `{public_grimmory_url}/library/{libraryId}/books`
    - `/book/{id}` &rarr; `{public_grimmory_url}/book/{bookId}`
    - `/book/{id}/read` &rarr; dedicated format reader (`/cbx-reader/book/{id}`, `/ebook-reader/book/{id}`, `/pdf-reader/book/{id}`)
    - `/collection/{id}` / `/shelf/{id}` &rarr; `{public_grimmory_url}/shelf/{shelfId}/books`
    - `/author/{id}` &rarr; `{public_grimmory_url}/author/{authorId}`
  - Komga DTOs include `metadata.links` pointing directly to Grimmory for one-click navigation in supported readers.
- **Strict Multi-User Isolation**:
  - Background catalog synchronization runs under an isolated account (`SYNC_USERNAME` / `SYNC_PASSWORD`).
  - Client reading sessions, progress tracking (`/read-progress`, on-deck), and user profile inspection (`/api/v1/users/me`) are strictly isolated and routed to each user's Grimmory identity.
- **Automated Book Page Calculation Engine**:
  - **Novels (EPUB, MOBI, AZW, FB2)**: Employs Calibre's **Adobe Digital Editions (ADE)** heuristic (1,024 uncompressed characters per page, configurable via `NOVEL_CHARS_PER_PAGE`).
  - **Comics & Manga (CBZ / CBR / CBX)**: Calculates 1 image file = 1 page.
  - **PDF**: Automatic page count extraction.
  - **Grimmory Write-Back**: Automatically updates Grimmory's upstream metadata (`pageCount`) via `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED`, preserving all existing tags, authors, and metadata.
- **Grimmory-Themed Admin Management WebUI**:
  - Redesigned with the official Grimmory design system, SVG branding, dark obsidian palette, and warm orange accents.
  - Password-protected management dashboard (`/admin`) restricted exclusively to **Grimmory Administrators**.
  - Real-time statistics: Total libraries, series, books, format distribution, cache metrics, and completion percentage.
  - Interactive controls to trigger missing page calculations, recalculate all books, or initiate background catalog and read progress syncs with live terminal logs.
- **High-Performance Caching**:
  - Persistent SQLite database in WAL mode (`aiosqlite`).
  - In-memory TTL caching for page metadata.
  - On-disk thumbnail cache (`THUMBNAILS_DIR`).

---

## Architecture Overview

```
+-------------------------------------------------------------------+
|   Reader Clients (Mihon, Tachiyomi, Komic, Kuro Reader, etc.)     |
+-------------------------------------------------------------------+
                                  |
                   Komga API (Basic Auth / Session / Token)
                                  v
+-------------------------------------------------------------------+
|                   GRIMMORY PROXY (Komga Bridge)                   |
|                                                                   |
|  +--------------------+        +-------------------------------+  |
|  |   FastAPI Proxy    | <----> | SQLite Cache & WAL            |  |
|  |  (Komga Endpoints) |        | (bridge.db)                   |  |
|  +--------------------+        +-------------------------------+  |
|            |                              |      ^                |
|            | Per-User Auth                |      | Periodic Sync  |
|            | & Reading Progress           |      | (User Status)  |
|            v                              v      |                |
|  +--------------------+        +-------------------------------+  |
|  |   Grimmory Client  |        | Page Calc & Sync Engines      |  |
|  |  (Multi-User HTTP) |        | (Calibre ADE / CBZ)           |  |
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
| `ADMIN_SESSION_SECRET` | `grimmory-proxy-secret-key-change-me` | Secret key for signing WebUI admin session cookies. |
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
- `GET /sse/v1/events` - Server-Sent Events stream.

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
- `GET /api/v1/series`, `POST /api/v1/series/list` - Paginated series with search, library filtering, and multi-column sorting.
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
- `GET /api/v1/books`, `POST /api/v1/books/list` - Paginated books query with multi-column sorting.
- `GET /api/v1/books/ondeck`, `POST /api/v1/books/ondeck` - Continue reading list for the authenticated user.
- `GET /api/v1/books/latest`, `POST /api/v1/books/latest` - Recently added books.
- `GET /api/v1/books/released`, `POST /api/v1/books/released` - Books ordered by release date (ignores books without release dates).
- `GET /api/v1/books/{id}` - Enriched book details with accurate page count and user read progress.
- `GET /api/v1/books/{id}/thumbnail` - Cover thumbnail streaming with disk caching.
- `GET /api/v1/books/{id}/pages` - Synthesized `PageDto` array with dimension metadata.
- `GET /api/v1/books/{id}/pages/{pageNumber}` - Direct page image streaming.
- `GET /api/v1/books/{id}/file` - Direct binary book file download.
- `GET /api/v1/books/{id}/manifest`, `GET /api/v1/books/{id}/manifest/divina` - Readium WebPub / Divina manifest.

### Reading Progress
- `GET /api/v1/books/{id}/read-progress` - Current reading progress for the authenticated user.
- `PATCH /api/v1/books/{id}/read-progress` - Update page progress or mark completed on Grimmory.
- `DELETE /api/v1/books/{id}/read-progress` - Reset reading progress on Grimmory.
- `PUT /api/v1/books/{id}/progression/r2` - Readium R2 locator progression update.

### Collections & Shelves
- `GET /api/v1/collections` - Collections mapped from Grimmory Shelves / Magic Shelves.
- `GET /api/v1/readlists` - Read lists.

### Authors
- `GET /api/v1/authors`, `GET /api/v2/authors` - Paginated authors directory.
- `GET /api/v1/authors/names` - List of all author names.

### Browser Redirects to Grimmory
- `GET /series/{id}` &rarr; Redirects to `{public_grimmory_url}/series/{seriesName}`
- `GET /library/{id}` &rarr; Redirects to `{public_grimmory_url}/library/{libraryId}/books`
- `GET /book/{id}` &rarr; Redirects to `{public_grimmory_url}/book/{bookId}`
- `GET /book/{id}/read` &rarr; Redirects to reader (`/cbx-reader/book/{id}`, `/ebook-reader/book/{id}`, `/pdf-reader/book/{id}`)
- `GET /collection/{id}` &rarr; Redirects to `{public_grimmory_url}/shelf/{shelfId}/books`
- `GET /author/{id}` &rarr; Redirects to `{public_grimmory_url}/author/{authorId}`

### Admin WebUI
- `GET /admin/login`, `POST /admin/login` - Secure sign-in page for Grimmory Administrators.
- `GET /admin` / `GET /dashboard` - Interactive statistics, health indicators, and control panel.
- `POST /admin/api/calculate-pages` - Trigger page calculation (missing only or full recalculation).
- `POST /admin/api/stop-calculation` - Gracefully abort active calculation job.
- `POST /admin/api/sync` - Trigger on-demand metadata sync.
- `POST /admin/api/sync/clear-logs` - Clear metadata sync log history.
- `POST /admin/api/read-sync` - Trigger on-demand user read progress sync.
- `POST /admin/api/read-sync/clear-logs` - Clear read progress sync log history.

---

## Deployment

### Using Docker Compose (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/Saetron/grimmory-proxy.git
   cd grimmory-proxy
   ```

2. Edit `docker-compose.yml` or set environment variables:
   ```yaml
   services:
     grimmory-proxy:
       image: ghcr.io/saetron/grimmory-proxy:latest
       build: .
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

## Reader Configuration

### Connecting Komga Readers (Mihon, Tachiyomi, Komic, Kuro Reader, Paperback, etc.)
1. In your reader app, add a new **Komga** server/repository.
2. **Server URL / Address**: `http://<server-ip>:8080` (or `https://proxy.yourdomain.com` behind reverse proxy).
3. **Username & Password**: Enter your personal Grimmory credentials.
4. Browse your libraries, read comics, manga, and novels with accurate page totals, and sync reading progress automatically.

---

## Running Tests

Run the test suite locally using `pytest`:

```bash
pytest
```
