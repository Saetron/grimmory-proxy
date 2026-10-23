import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import httpx

from app.config import settings
from app.models.grimmory import (
    GrimmoryLoginResponse,
    GrimmoryReadProgressRequest,
    GrimmoryUser,
)

logger = logging.getLogger("grimmory_proxy.grimmory_client")


class GrimmoryClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._sync_token: Optional[str] = None
        self._sync_token_expires_at: Optional[datetime] = None
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop = None
        self._user_favorites_cache: Dict[int, Tuple[float, List[int]]] = {}
        self._http_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    # ---------------- Authentication ----------------

    async def login(self, username: str, password: str, timeout: float = 15.0) -> GrimmoryLoginResponse:
        url = "/api/v1/auth/login"
        resp = await self._http_client.post(
            url,
            json={"username": username, "password": password},
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        )
        if resp.status_code != 200:
            logger.warning(f"Grimmory login failed for {username}: {resp.status_code} {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        return GrimmoryLoginResponse(**data)

    async def get_current_user(self, token: str, timeout: float = 15.0) -> GrimmoryUser:
        resp = await self._http_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        )
        resp.raise_for_status()
        return GrimmoryUser(**resp.json())

    @property
    def lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._lock_loop != loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def get_sync_token(self, force: bool = False) -> str:
        """Get or refresh dedicated sync account token."""
        async with self.lock:
            now = datetime.now(timezone.utc)
            if not force and self._sync_token and self._sync_token_expires_at and (self._sync_token_expires_at - now).total_seconds() > 120:
                return self._sync_token

            if not settings.sync_username or not settings.sync_password:
                raise ValueError("SYNC_USERNAME and SYNC_PASSWORD must be configured for background sync operations")

            login_res = await self.login(settings.sync_username, settings.sync_password)
            self._sync_token = login_res.accessToken
            self._sync_token_expires_at = now + timedelta(seconds=login_res.expires)
            logger.info("Renewed dedicated sync token for Grimmory")
            return self._sync_token

    def _is_sync_token(self, token: Optional[str]) -> bool:
        if token is None:
            return True
        if self._sync_token and token == self._sync_token:
            return True
        return False

    def _headers(self, token: Optional[str]) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request_with_auth(
        self,
        method: str,
        url: str,
        token: Optional[str] = None,
        retry_always: bool = False,
        **kwargs: Any,
    ) -> Tuple[httpx.Response, str]:
        is_sync = self._is_sync_token(token)
        auth_token = token or await self.get_sync_token()
        http_fn = getattr(self._http_client, method.lower())
        resp = await http_fn(url, headers=self._headers(auth_token), **kwargs)
        if resp.status_code in (401, 403) and (retry_always or is_sync):
            auth_token = await self.get_sync_token(force=True)
            resp = await http_fn(url, headers=self._headers(auth_token), **kwargs)
        return resp, auth_token

    # ---------------- Upstream Queries ----------------

    async def get_libraries(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        resp, _ = await self._request_with_auth("GET", "/api/v1/libraries", token=token)
        resp.raise_for_status()
        return resp.json()

    async def get_library_books(self, library_id: int, token: Optional[str] = None) -> List[Dict[str, Any]]:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/libraries/{library_id}/book", token=token)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_all_books(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        is_sync = self._is_sync_token(token)
        resp, _ = await self._request_with_auth("GET", "/api/v1/books", token=token)
        if resp.status_code == 200:
            return resp.json()
        if not is_sync and resp.status_code in (401, 403):
            return []
        resp.raise_for_status()
        return resp.json()

    async def get_book(self, book_id: int, token: Optional[str] = None) -> Dict[str, Any]:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/books/{book_id}", token=token, retry_always=True)
        resp.raise_for_status()
        return resp.json()

    async def get_cbx_pages(self, book_id: int, token: Optional[str] = None) -> List[int]:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/cbx/{book_id}/pages", token=token, retry_always=True)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_pdf_pages(self, book_id: int, token: Optional[str] = None) -> List[int]:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/pdf/{book_id}/pages", token=token, retry_always=True)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_magic_shelves(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        resp, _ = await self._request_with_auth("GET", "/api/v1/magic-shelves", token=token)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_shelves(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        resp, _ = await self._request_with_auth("GET", "/api/v1/shelves", token=token)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_shelf_books(self, shelf_id: Any, token: Optional[str] = None) -> List[Dict[str, Any]]:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/shelves/{shelf_id}/books", token=token)
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_user_favorite_book_ids(
        self, user_id: int, token: Optional[str] = None, force: bool = False
    ) -> List[int]:
        now = time.time()
        if not force and user_id in self._user_favorites_cache:
            cache_ts, cached_ids = self._user_favorites_cache[user_id]
            if now - cache_ts < 30.0:
                return list(cached_ids)

        if not token:
            return []

        def _extract_ids(items: Any) -> List[int]:
            ids: List[int] = []
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, int):
                        ids.append(it)
                    elif isinstance(it, str) and it.isdigit():
                        ids.append(int(it))
                    elif isinstance(it, dict):
                        b_id = it.get("id") or it.get("bookId")
                        if b_id is not None:
                            try:
                                ids.append(int(b_id))
                            except (ValueError, TypeError):
                                pass
            return ids

        async def _find_in_shelves(shelves_list: List[Dict[str, Any]]) -> Tuple[bool, List[int]]:
            for s in shelves_list:
                if str(s.get("name", "")).strip().lower() == "favorites":
                    books = s.get("books") or s.get("items") or s.get("bookIds")
                    if books:
                        return True, _extract_ids(books)
                    if "id" in s:
                        shelf_books = await self.get_shelf_books(s["id"], token=token)
                        return True, _extract_ids(shelf_books)
                    return True, []
            return False, []

        fav_ids: List[int] = []
        found_shelf = False

        # 1. Check /api/v1/shelves
        try:
            shelves = await self.get_shelves(token=token)
            found_shelf, fav_ids = await _find_in_shelves(shelves)
        except Exception as e:
            logger.debug(f"Error checking Grimmory shelves for user {user_id}: {e}")

        # 2. Fallback to /api/v1/magic-shelves
        if not found_shelf or not fav_ids:
            try:
                magic_shelves = await self.get_magic_shelves(token=token)
                _, extracted = await _find_in_shelves(magic_shelves)
                if extracted:
                    fav_ids = extracted
            except Exception as e:
                logger.debug(f"Error checking Grimmory magic-shelves for user {user_id}: {e}")

        # Deduplicate while preserving order
        deduped = list(dict.fromkeys(fav_ids))
        self._user_favorites_cache[user_id] = (now, deduped)
        return deduped

    def clear_user_favorites_cache(self, user_id: Optional[int] = None) -> None:
        if user_id is not None:
            self._user_favorites_cache.pop(user_id, None)
        else:
            self._user_favorites_cache.clear()

    # ---------------- Streaming & Media ----------------

    async def stream_thumbnail(self, book_id: int, token: Optional[str] = None) -> Optional[bytes]:
        """Fetch thumbnail bytes from Grimmory."""
        for path in [f"/api/v1/media/book/{book_id}/thumbnail", f"/api/v1/media/book/{book_id}/cover"]:
            try:
                resp, _ = await self._request_with_auth("GET", path, token=token, retry_always=True)
                if resp.status_code == 200 and resp.content:
                    return resp.content
            except Exception as e:
                logger.debug(f"Failed fetching thumbnail at {path}: {e}")
        return None

    async def stream_page_image(self, book_id: int, page_number: int, token: Optional[str] = None) -> Optional[bytes]:
        path = f"/api/v1/media/book/{book_id}/cbx/pages/{page_number}"
        resp, _ = await self._request_with_auth("GET", path, token=token, retry_always=True)
        if resp.status_code == 200:
            return resp.content
        return None

    async def download_book_stream(self, book_id: int, token: Optional[str] = None) -> httpx.Response:
        auth_token = token or await self.get_sync_token()
        req = self._http_client.build_request("GET", f"/api/v1/books/{book_id}/download", headers=self._headers(auth_token))
        resp = await self._http_client.send(req, stream=True)
        if resp.status_code in (401, 403):
            await resp.aclose()
            auth_token = await self.get_sync_token(force=True)
            req = self._http_client.build_request("GET", f"/api/v1/books/{book_id}/download", headers=self._headers(auth_token))
            resp = await self._http_client.send(req, stream=True)
        resp.raise_for_status()
        return resp

    async def download_book_bytes(self, book_id: int, token: Optional[str] = None) -> bytes:
        resp, _ = await self._request_with_auth("GET", f"/api/v1/books/{book_id}/download", token=token, retry_always=True)
        resp.raise_for_status()
        return resp.content

    # ---------------- Metadata & Progress Updates ----------------

    async def update_book_metadata(self, book_id: int, metadata: Dict[str, Any], token: Optional[str] = None) -> Dict[str, Any]:
        """Write metadata back to Grimmory preserving all other fields."""
        url = f"/api/v1/books/{book_id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED"
        resp, _ = await self._request_with_auth("PUT", url, token=token, retry_always=True, json={"metadata": metadata})
        resp.raise_for_status()
        return resp.json()

    async def update_read_progress(self, progress_req: GrimmoryReadProgressRequest, token: Optional[str] = None) -> bool:
        """Update read progress for the specific authenticated user."""
        payload = progress_req.model_dump(exclude_none=True)
        resp, _ = await self._request_with_auth("POST", "/api/v1/books/progress", token=token, json=payload)
        return resp.status_code in [200, 204]

    async def update_read_status(self, book_ids: List[int], status: str, token: Optional[str] = None) -> bool:
        """Update read status ('READ', 'READING', 'UNREAD') for one or more books on Grimmory."""
        if not book_ids:
            return True
        payload = {"bookIds": book_ids, "status": status}
        resp, _ = await self._request_with_auth("POST", "/api/v1/books/status", token=token, json=payload)
        return resp.status_code in [200, 204]

    async def reset_read_progress(self, book_ids: List[int], token: Optional[str] = None) -> bool:
        """Reset read progress and status for the specific authenticated user."""
        if not book_ids:
            return True
        resp, auth_token = await self._request_with_auth(
            "POST", "/api/v1/books/reset-progress?type=ALL", token=token, json=book_ids
        )
        # Ensure read status on Grimmory is also set to UNREAD
        try:
            await self.update_read_status(book_ids, "UNREAD", token=auth_token)
        except Exception as e:
            logger.debug(f"Failed to reset read status to UNREAD for books {book_ids}: {e}")
        return resp.status_code in [200, 204]

    async def update_koreader_progress(
        self,
        document_hash: str,
        progress: str,
        percentage: float,
        token: Optional[str] = None,
        device: str = "grimmory-proxy",
        device_id: str = "grimmory-proxy",
    ) -> bool:
        """Update KOReader progress on Grimmory (best effort)."""
        payload = {
            "document": document_hash,
            "progress": progress,
            "percentage": percentage,
            "device": device,
            "device_id": device_id,
        }
        try:
            resp, _ = await self._request_with_auth(
                "PUT", "/api/koreader/syncs/progress", token=token, json=payload
            )
            return resp.status_code in [200, 204]
        except Exception as e:
            logger.debug(f"Failed to update KOReader progress on Grimmory for {document_hash}: {e}")
            return False

    async def get_koreader_progress(
        self, document_hash: str, token: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch KOReader progress from Grimmory for a given document hash."""
        try:
            resp, _ = await self._request_with_auth(
                "GET", f"/api/koreader/syncs/progress/{document_hash}", token=token
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.debug(f"Failed to fetch KOReader progress from Grimmory for {document_hash}: {e}")
            return None


# Global Grimmory client instance
grimmory_client = GrimmoryClient(base_url=settings.grimmory_url)
