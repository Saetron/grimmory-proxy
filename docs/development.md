# Architecture & Development Guide

This document covers the architectural design, core engine behaviors, local development workflows, and testing practices for **grimmory-proxy**.

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

## Core Engine Behaviors

### 1. Smart Standalone Series Disambiguation
For books that lack an upstream `seriesName`:
- The proxy derives the series name from `meta.title`.
- To prevent one-shots or standalone books by different authors from colliding into a single merged series in Komga, `" - {author_name}"` is automatically appended **only when there would be a title collision** (i.e. two or more series sharing the exact same base title in the library).
- If only one series in the library shares that title, the author name is omitted, keeping series lists clean.

### 2. Page Calculation Engine & Error Validation
Grimmory stores book files, but many EPUBs or archives lack explicit page counts:
- **EPUB & Novels**: Computes pages using Calibre's **Adobe Digital Editions (ADE)** heuristic (1,024 uncompressed characters per page, configurable via `NOVEL_CHARS_PER_PAGE`).
- **CBZ / CBR / Manga**: Fast 1:1 image archive inspection.
- **PDF Documents**: Direct page count extraction via `pypdf`.
- **Validation & Problematic Files (`page_calc_errors`)**: Corrupted files or unreadable downloads are logged with disk path and failure reasons in SQLite, surfaced on `/stats`, and excluded from false-positive defaults.
- **Upstream Write-Back**: Computed page totals are written back to Grimmory metadata (`pageCount`) using `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED` to preserve existing tags and author roles.

### 3. Read Tracking & Synchronization
- Explicit client requests via `PATCH /api/v1/books/{id}/read-progress` update both reading position and reading status badges (`READ`, `READING`, `UNREAD`).
- When mobile readers stream image pages directly (`GET /api/v1/books/{id}/pages/{pageNumber}`), the proxy tracks reading position asynchronously in the background.
- Background user sync (`user_sync.py`) periodically pulls read state from Grimmory for all active user tokens.

---

## Local Development Setup

To run `grimmory-proxy` locally without Docker:

1. **Clone and create virtual environment**:
   ```bash
   git clone https://github.com/Saetron/grimmory-proxy.git
   cd grimmory-proxy
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables** (e.g. in `.env`):
   ```env
   GRIMMORY_URL=http://localhost:6060
   SYNC_USERNAME=admin
   SYNC_PASSWORD=your_password
   DATABASE_PATH=./data/bridge.db
   THUMBNAILS_DIR=./data/thumbnails
   ```

3. **Run the development server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```

---

## Running the Test Suite

The test suite covers authentication, multi-user isolation, Komga DTO parity, image compression, page calculation, and error tracking.

Run all tests:
```bash
pytest
```

Run a specific test module:
```bash
pytest tests/test_api.py
pytest tests/test_ui_rework.py
pytest tests/test_page_calc_errors.py
pytest tests/test_image_compression.py
pytest tests/test_series_collision.py
```

---

## Build & Release Workflow

Continuous Integration and container image builds are managed via GitHub Actions:

- **Build & Release (`.github/workflows/ci.yml`)**:
  - Triggered via GitHub **Workflow Dispatch**.
  - Runs the full `pytest` suite.
  - Automatically bumps version in `VERSION`, `README.md`, and `SECURITY.md` (or applies manual version override).
  - Tags and pushes the Git release tag (`v{version}`).
  - Builds and publishes multi-architecture container images (`linux/amd64`, `linux/arm64`) to `ghcr.io/saetron/grimmory-proxy`.
