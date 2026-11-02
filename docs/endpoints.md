# Supported API Endpoints & Routes

`grimmory-proxy` provides full emulation of the **Komga API v1.12.0** specification, dedicated Admin WebUI endpoints, and browser URL redirects back into Grimmory.

---

## 1. Komga API Emulation

### Auth & System
- `GET /actuator/info` - Komga build telemetry and version report.
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

---

## 2. Browser Redirects to Grimmory Frontend

Opening proxy links directly in a web browser redirects cleanly into Grimmory's Angular frontend:

- `GET /series/{id}` &rarr; `{public_grimmory_url}/series/{seriesName}`
- `GET /library/{id}` &rarr; `{public_grimmory_url}/library/{libraryId}/books`
- `GET /book/{id}` &rarr; `{public_grimmory_url}/book/{bookId}`
- `GET /book/{id}/read` &rarr; Format reader (`/cbx-reader/book/{id}`, `/ebook-reader/book/{id}`, `/pdf-reader/book/{id}`)
- `GET /collection/{id}` / `/shelf/{id}` &rarr; `{public_grimmory_url}/shelf/{shelfId}/books`
- `GET /author/{id}` &rarr; `{public_grimmory_url}/author/{authorId}`

---

## 3. Admin WebUI & Management API

Accessible to authenticated **Grimmory Administrators**:

### Web Pages
- `GET /admin/login`, `POST /admin/login` - Secure administrator sign-in page.
- `GET /admin`, `GET /dashboard` - Dashboard with sync status, operational health, and interactive book query explorer.
- `GET /stats` - Comprehensive catalog KPIs, format distribution, storage analytics, and page calculation engine controls.
- `GET /users` - Connected reader users directory with library access, reading progress statistics, and sync/purge actions.
- `GET /logs` - Multi-stream terminal logs (metadata sync, user read sync, server logs) capped at 200 lines.
- `GET /settings` - Persistent configuration manager for live editing and raw JSON inspection.

### API Endpoints
- `GET /admin/api/books/search` - Search and inspect books available in the proxy cache.
- `POST /admin/api/books/{book_id}/calculate-pages` - Calculate pages for a single book on demand.
- `POST /admin/api/books/{book_id}/progress/pull` - Pull upstream read progress from Grimmory for a single book.
- `POST /admin/api/books/{book_id}/progress/push` - Push local read progress to Grimmory for a single book.
- `POST /admin/api/calculate-pages` - Trigger bulk page calculation (`missing_only`, `retry_failed_only`, or single `book_id`).
- `POST /admin/api/stop-calculation` - Gracefully abort active calculation job.
- `GET /admin/api/page-calc/errors` - List recorded book calculation errors and problematic file paths.
- `POST /admin/api/page-calc/errors/clear` - Clear recorded page calculation errors history.
- `GET /admin/api/settings`, `POST /admin/api/settings` - Retrieve or update runtime persistent configuration.
- `GET /admin/api/users` - List connected reader users with library access and reading progress statistics.
- `POST /admin/api/users/{id}/purge` - Permanently purge a user and delete all their cached read progress.
- `POST /admin/api/users/{id}/sync` - Force an immediate on-demand Grimmory read progress sync for a specific user.
- `POST /admin/api/sync` - Trigger on-demand metadata sync.
- `POST /admin/api/sync/clear-logs` - Clear metadata sync log history.
- `POST /admin/api/read-sync` - Trigger on-demand user read progress sync.
- `POST /admin/api/read-sync/clear-logs` - Clear read progress sync log history.
- `POST /admin/api/optimize-storage` - Trigger an immediate SQLite WAL checkpoint (`TRUNCATE`) and reclaim Python heap memory.
- `GET /admin/api/logs` - Fetch the last 200 lines across log streams.
- `POST /admin/api/logs/clear` - Clear selected log buffer stream.
