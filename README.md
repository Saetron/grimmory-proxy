# grimmory-proxy - Komga to Grimmory API Bridge

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Version](https://img.shields.io/badge/Version-v1.12-orange.svg)](VERSION)
[![Komga API](https://img.shields.io/badge/Komga%20API-v1.12.0-orange.svg)](https://komga.org/)
[![Grimmory](https://img.shields.io/badge/Grimmory-3.6.0%2FNightly-f97316.svg)](https://grimmory.org/)
[![Documentation](https://img.shields.io/badge/Docs-Zensical-0ea5e9.svg)](https://saetron.github.io/grimmory-proxy/)

A high-performance Python and Docker proxy that translates the **[Grimmory API](https://grimmory.org/api/)** into the complete **[Komga API v1.12.0 specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json)**. It seamlessly restores compatibility for Komga-compatible reader applications—including **Mihon / Tachiyomi**, **Panels**, **Komic**, **Paperback**, and **Kuro Reader**—following Grimmory's deprecation of its native Komga endpoint.

---

## Key Highlights

- **Full Komga Protocol Parity**: Emulates Komga v1 & v2 endpoints, Spring Data pagination, multi-column sorting, and complex POST condition queries (`/api/v1/books/list`, `/api/v1/series/list`).
- **High-Speed Background Sync**: Synchronizes catalog metadata and user reading progress (`read_progress`) into SQLite, providing sub-millisecond reader responses with zero upstream latency.
- **Strict Multi-User Isolation**: Background catalog indexing uses a dedicated sync service account, while reading sessions, progress tracking, and shelves strictly respect individual user identities.
- **Smart Page Calculation Engine**: Computes page counts using Calibre's ADE heuristic for novels (EPUB/MOBI/AZW), 1:1 image counting for CBZ/CBR manga, and PDF inspection, writing totals back to Grimmory.
- **On-the-Fly Image Compression**: Optional WebP/JPEG transcoding with an in-memory LRU cache (`IMAGE_CACHE_TYPE=memory`) for zero SSD write wear.
- **Modular Admin WebUI**: Password-protected dashboard with operational health stats, book query explorer, problematic file tracking, live streaming logs, and runtime settings management.

---

## Quickstart (Docker Compose)

The easiest way to run `grimmory-proxy` is via Docker Compose:

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
    volumes:
      - ./data:/app/data
```

Start the container:
```bash
docker compose up -d
```

Access the Admin Dashboard at `http://localhost:8080/admin`.

---

## Connecting Your Reader Apps

Connect **Mihon**, **Panels**, **Komic**, **Paperback**, or **Kuro Reader** using the Komga server option:

1. In your reader app, add a new **Komga** server.
2. **Server Address**: `http://<server-ip>:8080` (or `https://proxy.yourdomain.com` behind your reverse proxy).
3. **Username & Password**: Enter your personal Grimmory credentials.
4. Enjoy your libraries, search, read tracking, and collections transparently!

> [!NOTE]
> Client reading sessions authenticate against your Grimmory server. Each user only sees their permitted libraries, reading progress, and personal shelves.

---

## Documentation & Community

For in-depth guides, technical details, and contributing guidelines:

- 🌐 **[Live Web Documentation](https://saetron.github.io/grimmory-proxy/)**: Interactive documentation with instant search.
- ⚙️ **[Configuration Guide](docs/configuration.md)**: Full environment variables reference, config.json precedence rules, and caching tuning.
- 🔌 **[Supported API Endpoints](docs/endpoints.md)**: Exhaustive breakdown of Komga routes, Admin API endpoints, and Grimmory browser redirects.
- 🏛️ **[Architecture & Development](docs/development.md)**: Architecture diagram, Calibre ADE calculation engine, local development, and test suite guide.
- 🤝 **[Contributing Guidelines](CONTRIBUTING.md)**: Development workflow, branch rules (dev -> main), testing, and PR submission.
- 📜 **[Code of Conduct](CODE_OF_CONDUCT.md)**: Community standards and pledge.
- 📋 **[Changelog](CHANGELOG.md)**: Complete release history and version migration notes.
- 🛡️ **[Security Policy](SECURITY.md)**: Supported versions and private vulnerability reporting.
