# grimmory-proxy: Komga API Emulation for Grimmory

!!! info "Looking for Grimmory's Komga API Emulation?"
    In Grimmory 3.x, Grimmory deprecated its native Komga endpoint. `grimmory-proxy` is a high-performance Docker API bridge that restores full **Komga v1.12.0 API emulation**, allowing Komga-compatible reader applications—including **Mihon / Tachiyomi**, **Panels**, **Komic**, **Paperback**, and **Kuro Reader**—to connect transparently to modern Grimmory servers.

---

A high-performance Python and Docker bridge that translates the **[Grimmory API](https://grimmory.org/api/)** into the complete **[Komga API v1.12.0 specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json)**. 

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

## Navigation & Guides

- **[Reader Apps Setup](clients.md)**: Configuration guides for Mihon, Panels, Komic, Paperback, and Kuro Reader.
- **[Full-Stack Deployment & Reverse Proxy](deployment.md)**: Production multi-container Docker Compose with Grimmory, Caddy, Nginx, and Traefik.
- **[Configuration Reference](configuration.md)**: Full list of environment variables, caching options, and performance tuning.
- **[API Endpoints](endpoints.md)**: Supported Komga v1/v2 endpoints, browser redirect handlers, and Admin WebUI routes.
- **[Architecture & Development](development.md)**: System design, page calculation engine, testing, and CI/CD pipelines.
- **[Changelog](changelog.md)**: Release history and version updates.
