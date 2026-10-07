import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional
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
        self._lock = asyncio.Lock()
        self._http_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    # ---------------- Authentication ----------------

    async def login(self, username: str, password: str) -> GrimmoryLoginResponse:
        url = "/api/v1/auth/login"
        resp = await self._http_client.post(url, json={"username": username, "password": password})
        if resp.status_code != 200:
            logger.warning(f"Grimmory login failed for {username}: {resp.status_code} {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        return GrimmoryLoginResponse(**data)

    async def get_current_user(self, token: str) -> GrimmoryUser:
        resp = await self._http_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return GrimmoryUser(**resp.json())

    async def get_sync_token(self) -> str:
        """Get or refresh dedicated sync account token."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._sync_token and self._sync_token_expires_at and (self._sync_token_expires_at - now).total_seconds() > 120:
                return self._sync_token

            if not settings.sync_username or not settings.sync_password:
                raise ValueError("SYNC_USERNAME and SYNC_PASSWORD must be configured for background sync operations")

            login_res = await self.login(settings.sync_username, settings.sync_password)
            self._sync_token = login_res.accessToken
            self._sync_token_expires_at = now + timedelta(seconds=login_res.expires)
            logger.info("Renewed dedicated sync token for Grimmory")
            return self._sync_token

    def _headers(self, token: Optional[str]) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---------------- Upstream Queries ----------------

    async def get_libraries(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get("/api/v1/libraries", headers=self._headers(auth_token))
        resp.raise_for_status()
        return resp.json()

    async def get_library_books(self, library_id: int, token: Optional[str] = None) -> List[Dict[str, Any]]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get(f"/api/v1/libraries/{library_id}/book", headers=self._headers(auth_token))
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_all_books(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get("/api/v1/books", headers=self._headers(auth_token))
        resp.raise_for_status()
        return resp.json()

    async def get_book(self, book_id: int, token: Optional[str] = None) -> Dict[str, Any]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get(f"/api/v1/books/{book_id}", headers=self._headers(auth_token))
        resp.raise_for_status()
        return resp.json()

    async def get_cbx_pages(self, book_id: int, token: Optional[str] = None) -> List[int]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get(f"/api/v1/cbx/{book_id}/pages", headers=self._headers(auth_token))
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_pdf_pages(self, book_id: int, token: Optional[str] = None) -> List[int]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get(f"/api/v1/pdf/{book_id}/pages", headers=self._headers(auth_token))
        if resp.status_code == 200:
            return resp.json()
        return []

    async def get_magic_shelves(self, token: Optional[str] = None) -> List[Dict[str, Any]]:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get("/api/v1/magic-shelves", headers=self._headers(auth_token))
        if resp.status_code == 200:
            return resp.json()
        return []

    # ---------------- Streaming & Media ----------------

    async def stream_thumbnail(self, book_id: int, token: Optional[str] = None) -> Optional[bytes]:
        """Fetch thumbnail bytes from Grimmory."""
        auth_token = token or await self.get_sync_token()
        # Grimmory serves thumbnail at /api/v1/media/book/{id}/thumbnail or /cover
        for path in [f"/api/v1/media/book/{book_id}/thumbnail", f"/api/v1/media/book/{book_id}/cover"]:
            try:
                resp = await self._http_client.get(path, headers=self._headers(auth_token))
                if resp.status_code == 200 and resp.content:
                    return resp.content
            except Exception as e:
                logger.debug(f"Failed fetching thumbnail at {path}: {e}")
        return None

    async def stream_page_image(self, book_id: int, page_number: int, token: Optional[str] = None) -> Optional[bytes]:
        auth_token = token or await self.get_sync_token()
        path = f"/api/v1/media/book/{book_id}/cbx/pages/{page_number}"
        resp = await self._http_client.get(path, headers=self._headers(auth_token))
        if resp.status_code == 200:
            return resp.content
        return None

    async def download_book_stream(self, book_id: int, token: Optional[str] = None) -> httpx.Response:
        auth_token = token or await self.get_sync_token()
        req = self._http_client.build_request("GET", f"/api/v1/books/{book_id}/download", headers=self._headers(auth_token))
        resp = await self._http_client.send(req, stream=True)
        resp.raise_for_status()
        return resp

    async def download_book_bytes(self, book_id: int, token: Optional[str] = None) -> bytes:
        auth_token = token or await self.get_sync_token()
        resp = await self._http_client.get(f"/api/v1/books/{book_id}/download", headers=self._headers(auth_token))
        resp.raise_for_status()
        return resp.content

    # ---------------- Metadata & Progress Updates ----------------

    async def update_book_metadata(self, book_id: int, metadata: Dict[str, Any], token: Optional[str] = None) -> Dict[str, Any]:
        """Write metadata back to Grimmory preserving all other fields."""
        auth_token = token or await self.get_sync_token()
        url = f"/api/v1/books/{book_id}/metadata?replaceMode=REPLACE_WHEN_PROVIDED"
        payload = {"metadata": metadata}
        resp = await self._http_client.put(url, json=payload, headers=self._headers(auth_token))
        resp.raise_for_status()
        return resp.json()

    async def update_read_progress(self, progress_req: GrimmoryReadProgressRequest, token: str) -> bool:
        """Update read progress for the specific authenticated user."""
        url = "/api/v1/books/progress"
        resp = await self._http_client.post(url, json=progress_req.model_dump(exclude_none=True), headers=self._headers(token))
        return resp.status_code in [200, 204]

    async def reset_read_progress(self, book_ids: List[int], token: str) -> bool:
        """Reset read progress for the specific authenticated user."""
        url = "/api/v1/books/reset-progress?type=ALL"
        resp = await self._http_client.post(url, json=book_ids, headers=self._headers(token))
        return resp.status_code in [200, 204]


# Global Grimmory client instance
grimmory_client = GrimmoryClient(base_url=settings.grimmory_url)
