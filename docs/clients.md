# Reader Client Setup Guide

`grimmory-proxy` fully emulates the [Komga API v1.12.0 specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json), allowing any reader application designed for Komga to connect directly to your Grimmory server.

---

## Connection Essentials

Across all reader clients, use the following connection parameters:

| Parameter | Value |
|---|---|
| **Server Address / URL** | `http://<proxy-ip>:8080` or `https://proxy.yourdomain.com` *(no trailing slash)* |
| **Authentication** | HTTP Basic Auth or API Key |
| **Username** | Your Grimmory username |
| **Password** | Your Grimmory password |

> [!NOTE]
> Each user authenticates with their own Grimmory credentials. Reading progress, read badges (`READ`, `READING`, `UNREAD`), and personal shelves are strictly isolated per user and kept in sync with Grimmory.

---

## 1. Mihon / Tachiyomi / Forked Apps

[Mihon](https://mihon.app/) and Tachiyomi forks (such as TachiyomiSY or J2K) connect to `grimmory-proxy` via the official **Komga** source extension.

### Installation & Configuration

1. In Mihon, open **Browse** → **Extensions**.
2. Search for **Komga** and install it (available in the Keiyoushi repository).
3. Go to **Browse** → **Sources** and tap the **Settings** (gear icon) next to Komga.
4. Configure the server connection:
   - **Address**: `http://<proxy-ip>:8080` (or your reverse proxy URL).
   - **Username**: Your Grimmory username.
   - **Password**: Your Grimmory password.
5. Tap **Restart app** or back out to refresh the source.
6. Open **Komga** in the Sources list to view your Grimmory libraries, series, and volumes.

> [!TIP]
> **Automatic Read Tracking**: Mihon streams page images directly. `grimmory-proxy` detects page streaming in real time, updating your Grimmory reading progress and badges (`READING` / `READ`) even though Mihon does not send separate progress sync requests.

---

## 2. Panels (iOS / iPadOS)

[Panels](https://panels.app/) is a popular comic and manga reader for iPhone and iPad with native Komga support.

### Setup Steps

1. Open Panels and tap the **Services** or **Connect** icon in the library tab.
2. Select **Add Service** → **Komga**.
3. Fill in the connection settings:
   - **Host / URL**: `https://proxy.yourdomain.com` (or `http://<proxy-ip>:8080`).
   - **Username**: Your Grimmory username.
   - **Password**: Your Grimmory password.
4. Tap **Connect** / **Sign In**.
5. Your Grimmory libraries and collections will appear. You can stream books directly or download them for offline reading.

---

## 3. Komic (Android / Flutter)

[Komic](https://github.com/Tsubaki/Komic) is an open-source reader app tailored for comic and manga servers.

### Setup Steps

1. Open Komic and navigate to **Settings** → **Accounts / Servers**.
2. Tap **Add Server** and choose **Komga**.
3. Enter your server URL and credentials:
   - **Server URL**: `http://<proxy-ip>:8080`
   - **Username**: Grimmory username
   - **Password**: Grimmory password
4. Tap **Login**.

> [!NOTE]
> `grimmory-proxy` supports Komic's advanced POST condition filters (`/api/v1/series/list`, `/api/v1/books/list`) and role-aware author queries (e.g. `writer`, `penciller`, `cover artist`).

---

## 4. Paperback (iOS)

1. Ensure you have the Komga community extension installed in Paperback.
2. In **Settings** → **Sources** → **Komga Settings**:
   - Set **Server URL** to your proxy address.
   - Provide your login credentials.
3. Your Grimmory library books and series will appear in the source feed.

---

## 5. Kuro Reader / CDisplayEx (Android)

Both Kuro Reader and CDisplayEx support connecting to Komga instances:

1. In the app drawer or library menu, select **Network** or **Add Server**.
2. Choose **Komga**.
3. Enter the server host, port (`8080`), and credentials.
4. Browse and read your remote library with cover caching enabled.

---

## Reader Feature Compatibility Matrix

| Feature | Support in `grimmory-proxy` | Notes |
|---|---|---|
| **Komga API v1 / v2** | Full Parity | Emulates Spring Data pagination, sorting, and metadata DTOs. |
| **Stream CBZ / CBR / PDF** | Native | Direct page streaming with in-memory decompression. |
| **Stream EPUB** | Calibre ADE | Computes synthetic pages (1,024 uncompressed chars/page) for consistent pagination. |
| **On-the-Fly WebP Transcoding** | Optional | Saves mobile bandwidth when `IMAGE_COMPRESSION_ENABLED=true`. |
| **Dual Read Tracking** | Automatic | Supports both explicit `PATCH /read-progress` and passive image stream tracking. |
| **Role-Aware Author Filtering** | Native | Supports queries like `?author=Name,writer` across series and books. |
| **Multi-User Shelves & Readlists** | Strict Isolation | Each user's reading status and progress remains private. |

---

## Troubleshooting Client Connections

- **Cannot Connect / 401 Unauthorized**:
  - Verify you can log in to Grimmory directly with the same username and password.
  - Check that `GRIMMORY_URL` in your proxy environment is reachable from the proxy container.
- **Images Load Slowly on Mobile Data**:
  - Enable reader image compression in `config.json` or Admin WebUI Settings (`IMAGE_COMPRESSION_ENABLED=true`, `IMAGE_COMPRESSION_FORMAT=webp`).
- **Covers Not Updating**:
  - Clear thumbnail cache in the Admin WebUI under **Settings** or restart the reader app to clear its local image cache.
