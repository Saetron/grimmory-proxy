# Configuration Guide

`grimmory-proxy` can be configured using environment variables, the persistent `config.json` configuration file, or directly via the Admin WebUI at `/settings`.

---

## Configuration Precedence

1. **First Boot**: When the proxy starts for the first time without an existing `config.json`, it generates `/app/data/config.json` using the values supplied by environment variables (or defaults).
2. **Subsequent Boots**: On all subsequent boots, values stored in `config.json` take precedence over environment variables.
3. **WebUI Runtime Management**: Administrators can modify settings live via the `/settings` interface without restarting the container.

---

## Environment Variables Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `GRIMMORY_URL` | `http://localhost:8080` | URL of your upstream Grimmory instance (without trailing slash). |
| `GRIMMORY_PUBLIC_URL` | *(empty)* | Public browser-accessible URL of Grimmory (falls back to `GRIMMORY_URL` if empty). Use when `GRIMMORY_URL` is an internal Docker address like `http://grimmory:6060`. |
| `SYNC_USERNAME` | *(empty)* | Dedicated username for background sync (e.g. an admin account with access to all libraries). Strictly isolated from client reading sessions. |
| `SYNC_PASSWORD` | *(empty)* | Dedicated password for background sync. |
| `SYNC_INTERVAL_MINUTES` | `30` | Interval in minutes between background metadata sync cycles (`0` to disable). |
| `USER_SYNC_INTERVAL_MINUTES` | `5` | Interval in minutes between background user read progress sync cycles (`0` to disable). |
| `SYNC_ON_STARTUP` | `true` | Whether to trigger background metadata sync on application launch. |
| `SYNC_CONCURRENCY` | `6` | Concurrency limit for background series/book page inspection. |
| `NOVEL_CHARS_PER_PAGE` | `1024` | Number of uncompressed characters per page for novels (Calibre ADE standard: 1024). |
| `IMAGE_COMPRESSION_ENABLED` | `false` | Whether to transcode and compress reader page images on the fly. |
| `IMAGE_COMPRESSION_FORMAT` | `webp` | Target format for compressed reader pages (`webp`, `jpeg`, or `original`). |
| `IMAGE_COMPRESSION_QUALITY` | `80` | Quality setting (1–100) for on-the-fly compressed reader pages. |
| `IMAGE_COMPRESSION_MAX_DIMENSION` | `1920` | Maximum width or height in pixels for compressed pages (`0` to preserve original dimensions). |
| `IMAGE_COMPRESSION_MIN_SIZE_KB` | `400` | Minimum file size in KB before compression is applied (smaller pages are served as-is to save CPU). |
| `IMAGE_CACHE_TYPE` | `memory` | Cache backend for compressed pages (`memory`, `none`, or `disk`). `memory` uses zero SSD writes. |
| `IMAGE_CACHE_MEMORY_MAX_ITEMS` | `150` | Maximum number of compressed pages retained in memory LRU cache. |
| `IMAGE_CACHE_DISK_DIR` | `/app/data/page_cache` | Disk directory for cached compressed pages when `IMAGE_CACHE_TYPE=disk`. |
| `SQLITE_CACHE_SIZE` | `-8000` | SQLite page cache size limit in KiB (negative value specifies size in KiB; default `-8000` = ~8 MB). |
| `SQLITE_MMAP_SIZE` | `67108864` | SQLite memory-mapped I/O size limit in bytes (default `67108864` = 64 MB; `0` to disable mmap). |
| `SQLITE_WAL_AUTOCHECKPOINT` | `1000` | SQLite WAL autocheckpoint page threshold (default: 1000 pages). |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated browser origins allowed by CORS. With `*` credentialed (cookie) cross-origin requests are disabled; list explicit origins to enable them. |
| `COOKIE_SECURE` | `false` | Set the `Secure` flag on the admin session cookie (enable when served over HTTPS). |
| `DATABASE_PATH` | `/app/data/bridge.db` | Path to persistent SQLite cache database. |
| `CONFIG_PATH` | `/app/data/config.json` | Path to persistent configuration file. |
| `THUMBNAILS_DIR` | `/app/data/thumbnails` | Path to disk cache for cover thumbnails. |
| `PORT` | `8080` | Port the proxy listens on inside the container. |
| `HOST` | `0.0.0.0` | Bind host address. |
| `CACHE_TTL` | `300` | TTL in seconds for in-memory page metadata caches. |
| `LOG_LEVEL` | `info` | Logging verbosity (`debug`, `info`, `warning`, `error`). |

---

## Image Compression & Caching Tuning

When reader clients (Mihon, Panels, Komic, Kuro Reader) stream comic or manga pages, original images can be several megabytes each. `grimmory-proxy` can optimize them on the fly:

- **Zero SSD Wear (`IMAGE_CACHE_TYPE="memory"`)**: Maintains an in-memory LRU ring buffer in RAM. Ideal for SBCs, NAS devices, or servers where SSD write endurance is a priority.
- **Smart Dimension Resampling (`IMAGE_COMPRESSION_MAX_DIMENSION=1920`)**: Downscales ultra-high resolution scans (e.g. 4K/6K) to fit typical mobile and tablet screens using high-quality Lanczos resampling.
- **Skip Small Files (`IMAGE_COMPRESSION_MIN_SIZE_KB=400`)**: Prevents wasting CPU cycles compressing files that are already lightweight.

---

## SQLite Performance & Concurrency

The proxy uses SQLite with Write-Ahead Logging (`WAL` mode):
- Centralized asynchronous write locks (`write_db()`) prevent `sqlite3.OperationalError: database is locked` errors during concurrent metadata syncing, read tracking, and page calculation.
- Automated WAL checkpoints (`PRAGMA wal_checkpoint(TRUNCATE)`) prevent unbounded `-wal` file growth.
- Storage optimization can be triggered manually via `/stats` or `POST /admin/api/optimize-storage`.
