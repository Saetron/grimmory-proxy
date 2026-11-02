# Deployment & Reverse Proxy Guide

This guide provides production-ready deployment configurations for running `grimmory-proxy` alongside **Grimmory** using Docker Compose, along with reverse-proxy configurations for **Caddy**, **Nginx**, and **Traefik**.

---

## 1. Full-Stack Docker Compose (Grimmory + Proxy)

If you are running Grimmory and `grimmory-proxy` on the same host, attaching both services to an internal Docker network ensures high throughput and avoids exposing internal Grimmory APIs unnecessarily.

Create a `docker-compose.yml`:

```yaml
version: "3.8"

networks:
  grimmory-net:
    driver: bridge

volumes:
  grimmory-data:
  proxy-data:

services:
  # Upstream Grimmory Server
  grimmory:
    image: ghcr.io/grimmory-org/grimmory:latest # or appropriate Grimmory tag
    container_name: grimmory
    restart: unless-stopped
    networks:
      - grimmory-net
    ports:
      - "6060:6060" # Grimmory WebUI
    volumes:
      - /path/to/your/books:/books:ro # Your comic/ebook libraries
      - grimmory-data:/data
    environment:
      - TZ=UTC

  # Grimmory-to-Komga API Proxy
  grimmory-proxy:
    image: ghcr.io/saetron/grimmory-proxy:latest
    container_name: grimmory-proxy
    restart: unless-stopped
    depends_on:
      - grimmory
    networks:
      - grimmory-net
    ports:
      - "8080:8080" # Komga API & Admin Dashboard
    volumes:
      - proxy-data:/app/data
    environment:
      # Internal Docker network connection to Grimmory
      - GRIMMORY_URL=http://grimmory:6060
      # Public URL for browser-based reader redirects (optional, e.g. when behind reverse proxy)
      - GRIMMORY_PUBLIC_URL=https://grimmory.example.com
      # Service account for background catalog synchronization
      - SYNC_USERNAME=admin_user
      - SYNC_PASSWORD=admin_password
      - SYNC_INTERVAL_MINUTES=30
      - USER_SYNC_INTERVAL_MINUTES=5
      # In-memory image compression (optional, reduces mobile bandwidth)
      - IMAGE_COMPRESSION_ENABLED=false
      - IMAGE_COMPRESSION_FORMAT=webp
      - IMAGE_CACHE_TYPE=memory
      - IMAGE_CACHE_MEMORY_MAX_ITEMS=150
      # HTTPS cookie security (enable when behind a reverse proxy)
      - COOKIE_SECURE=false
```

---

## 2. Reverse Proxy Configurations

When exposing `grimmory-proxy` to the internet for remote reader apps (such as Mihon, Panels, or Komic), always serve it over **HTTPS**.

### Caddy

Caddy provides automated Let's Encrypt certificates and HTTP/2 + HTTP/3 support:

```caddy
# grimmory-proxy (Komga API & Admin UI)
proxy.example.com {
    reverse_proxy grimmory-proxy:8080 {
        header_up X-Forwarded-Proto https
        header_up X-Forwarded-Host {host}
    }

    encode zstd gzip
}

# Upstream Grimmory WebUI (optional)
grimmory.example.com {
    reverse_proxy grimmory:6060
    encode zstd gzip
}
```

> [!TIP]
> When running behind HTTPS in Caddy, set `COOKIE_SECURE=true` in your proxy environment so session cookies carry the `Secure` attribute.

---

### Nginx / Nginx Proxy Manager

Comic and manga readers frequently stream high-resolution page images. Configure buffer settings to prevent stalling during page flips:

```nginx
server {
    listen 443 ssl http2;
    server_name proxy.example.com;

    # SSL Certificate Configuration
    ssl_certificate /etc/letsencrypt/live/proxy.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/proxy.example.com/privkey.pem;

    # Large archive uploads / API operations
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8080;

        # Standard Proxy Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket / Streaming Support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Page Streaming Optimizations
        proxy_buffering off;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

---

### Traefik (Docker Labels)

If you use Traefik as a reverse proxy, you can attach labels directly to the `grimmory-proxy` container in `docker-compose.yml`:

```yaml
services:
  grimmory-proxy:
    image: ghcr.io/saetron/grimmory-proxy:latest
    container_name: grimmory-proxy
    restart: unless-stopped
    networks:
      - web
      - grimmory-net
    labels:
      - "traefik.enable=true"
      # HTTP Router (Redirect to HTTPS)
      - "traefik.http.routers.grimmory-proxy-http.rule=Host(`proxy.example.com`)"
      - "traefik.http.routers.grimmory-proxy-http.entrypoints=web"
      - "traefik.http.routers.grimmory-proxy-http.middlewares=redirect-to-https"
      # HTTPS Router
      - "traefik.http.routers.grimmory-proxy.rule=Host(`proxy.example.com`)"
      - "traefik.http.routers.grimmory-proxy.entrypoints=websecure"
      - "traefik.http.routers.grimmory-proxy.tls=true"
      - "traefik.http.routers.grimmory-proxy.tls.certresolver=letsencrypt"
      # Internal Service Port
      - "traefik.http.services.grimmory-proxy.loadbalancer.server.port=8080"
```

---

## 3. Production Security Checklist

1. **Dedicated Sync Account**: Create a service account in Grimmory specifically for `SYNC_USERNAME` with access to the catalog libraries, instead of reusing personal reader accounts.
2. **Cookie Security**: Set `COOKIE_SECURE=true` whenever the Admin WebUI is accessed over HTTPS.
3. **CORS Restrictions**: If embedding the reader or proxy in external web apps, specify your allowed domains in `CORS_ALLOW_ORIGINS=https://app.example.com` instead of wildcard `*`.
4. **Internal Network Isolation**: Do not publish Grimmory's port `6060` to the public internet unless required. Let `grimmory-proxy` communicate over Docker's internal bridge network.
