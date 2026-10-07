# grimmory-proxy - Komga to Grimmory API Bridge

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Komga API](https://img.shields.io/badge/Komga%20API-v1.12.0-orange.svg)](https://komga.org/)

A high-performance Python and Docker proxy/bridge that translates the **Grimmory API** (`https://grimmory.org/api/`) into the complete **Komga API specification** (`https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json`), restoring seamless compatibility for any Komga-compatible reader application such as **Mihon / Tachiyomi**, **Komic**, **Paperback**, and **Kuro Reader** after the deprecation of Grimmory's native Komga endpoint.

---

## Key Features

- **Full Komga API Emulation**: Seamlessly implements Auth, Actuator, Libraries, Series, Books, Page Streaming, File Downloads, Reading Progress, Collections, and Authors.
- **Strict Multi-User Isolation**:
  - Background catalog synchronization runs under an isolated account (`SYNC_USERNAME` / `SYNC_PASSWORD`).
  - Client reading sessions, progress tracking (`/read-progress`, on-deck), and user profile inspection (`/api/v1/users/me`) are strictly isolated and routed to each user's Grimmory identity.
- **Automated Book Page Calculation Engine**:
  - **Novels (EPUB, MOBI, AZW, FB2)**: Employs Calibre's **Adobe Digital Editions (ADE)** heuristic (1,024 uncompressed characters per page).
  - **Comics & Manga (CBZ / CBR / CBX)**: Calculates 1 image file = 1 page.
  - **PDF**: Automatic page count extraction.
  - **Grimmory Write-Back**: Automatically updates Grimmory's upstream metadata (`pageCount`) via `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED`, preserving all existing tags, authors, and metadata.
- **Admin Management WebUI**:
  - Password-protected management dashboard (`/admin`) restricted exclusively to **Grimmory Administrators**.
  - Real-time statistics: Total libraries, series, books, format distribution, cache metrics, and completion percentage.
  - Interactive controls to trigger missing page calculations, recalculate all books, or initiate background catalog syncs with live progress tracking.
- **High-Performance Caching**:
  - Persistent SQLite database in WAL mode (`aiosqlite`).
  - In-memory TTL caching for page metadata.
  - On-disk thumbnail cache (`THUMBNAILS_DIR`).

---

## Architecture Overview

```
+-------------------------------------------------------------+
|  Reader Clients (Mihon, Tachiyomi, Komic, Kuro Reader, etc.)|
+-------------------------------------------------------------+
                               |
                Komga API (Basic Auth / Session)
                               v
+-------------------------------------------------------------+
|                 GRIMMORY PROXY (Komga Bridge)               |
|                                                             |
|  +-------------------+        +--------------------------+  |
|  |   FastAPI Proxy   | <----> | SQLite Cache & WAL       |  |
|  | (Komga Endpoints) |        | (bridge.db)              |  |
|  +-------------------+        +--------------------------+  |
|            |                            |                   |
|            | Per-User Auth              |                   |
|            | & Progress                 |                   |
|            v                            v                   |
|  +-------------------+        +--------------------------+  |
|  |  Grimmory Client  |        | Page Calc Engine         |  |
|  | (Multi-User HTTP) |        | (Calibre ADE / CBZ)      |  |
|  +-------------------+        +--------------------------+  |
|            ^                            |                   |
|            | Background Sync            | Write-Back        |
|            | (SYNC_USERNAME)            | (pageCount)       |
+-------------------------------------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                      GRIMMORY SERVER                        |
|                 (https://grimmory.org/api/)                 |
+-------------------------------------------------------------+
```

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `GRIMMORY_URL` | `http://localhost:8080` | URL of your Grimmory instance (without trailing slash). |
| `SYNC_USERNAME` | *(empty)* | Dedicated username for background sync (e.g. an admin account with access to all libraries). Strictly isolated from client reading sessions. |
| `SYNC_PASSWORD` | *(empty)* | Dedicated password for background sync. |
| `SYNC_INTERVAL_MINUTES` | `30` | Interval in minutes between background metadata sync cycles (`0` to disable periodic sync). |
| `SYNC_ON_STARTUP` | `true` | Whether to trigger background metadata sync and startup page calculation when the database is empty. |
| `SYNC_CONCURRENCY` | `6` | Concurrency limit for background series/book page inspection. |
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
- `GET /api/v1/login/set-cookie` - Session cookie establishment.
- `GET /api/logout`, `POST /api/logout` - Session invalidation.

### Libraries
- `GET /api/v1/libraries` - List all accessible libraries (filtered per user permissions).
- `GET /api/v1/libraries/{id}` - Retrieve details for a specific library.

### Series
- `GET /api/v1/series`, `POST /api/v1/series/list` - Paginated series with search, library filtering, and sorting.
- `GET /api/v1/series/{id}` - Specific series details.
- `GET /api/v1/series/{id}/books` - Paginated books belonging to a series.
- `GET /api/v1/series/{id}/thumbnail` - Cover thumbnail streaming for a series.
- `GET /api/v1/series/latest` - Recently updated series.
- `GET /api/v1/series/new` - Recently added series.
- `GET /api/v1/series/updated` - Series with recent modifications.
- `GET /api/v1/series/alphabetical-groups` - Series counts grouped alphabetically.
- `GET /api/v1/series/genres` - Available series genres.
- `GET /api/v1/series/release-dates` - Available release dates.

### Books
- `GET /api/v1/books`, `POST /api/v1/books/list` - Paginated books query.
- `GET /api/v1/books/ondeck`, `POST /api/v1/books/ondeck` - Continue reading list for the authenticated user.
- `GET /api/v1/books/latest`, `POST /api/v1/books/latest` - Recently added books.
- `GET /api/v1/books/released`, `POST /api/v1/books/released` - Books ordered by release date.
- `GET /api/v1/books/{id}` - Enriched book details with accurate page count and user read progress.
- `GET /api/v1/books/{id}/thumbnail` - Cover thumbnail streaming with disk caching.
- `GET /api/v1/books/{id}/pages` - Synthesized `PageDto` array with dimension metadata.
- `GET /api/v1/books/{id}/pages/{pageNumber}` - Direct page image streaming (supports optional `?convert=png`).
- `GET /api/v1/books/{id}/file` - Direct binary book file download.
- `GET /api/v1/books/{id}/manifest`, `GET /api/v1/books/{id}/manifest/divina` - Readium WebPub / Divina manifest.

### Reading Progress
- `GET /api/v1/books/{id}/read-progress` - Current reading progress for the authenticated user.
- `PATCH /api/v1/books/{id}/read-progress` - Update page progress or mark completed on Grimmory.
- `DELETE /api/v1/books/{id}/read-progress` - Reset reading progress on Grimmory.

### Collections & ReadLists
- `GET /api/v1/collections` - Collections mapped from Grimmory Magic Shelves.
- `GET /api/v1/readlists` - Read lists.

### Authors
- `GET /api/v1/authors`, `GET /api/v2/authors` - Paginated authors directory.
- `GET /api/v1/authors/names` - List of all author names.

### Admin WebUI
- `GET /admin/login`, `POST /admin/login` - Secure sign-in page for Grimmory Administrators.
- `GET /admin` / `GET /dashboard` - Interactive statistics, health indicators, and control panel.
- `POST /admin/api/calculate-pages` - Trigger page calculation (missing only or full recalculation).
- `POST /admin/api/stop-calculation` - Gracefully abort active calculation job.
- `POST /admin/api/sync` - Trigger on-demand metadata sync.

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
         - GRIMMORY_URL=https://grimmory.yourdomain.com
         - SYNC_USERNAME=admin_user
         - SYNC_PASSWORD=admin_password
         - SYNC_INTERVAL_MINUTES=30
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
