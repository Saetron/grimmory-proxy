import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger("komic.cache")


class MemoryCache:
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            item = self._cache.get(key)
            if not item:
                return None
            expiry, val = item
            if time.time() > expiry:
                self._cache.pop(key, None)
                return None
            return val

    async def set(self, key: str, val: Any, custom_ttl: Optional[int] = None) -> None:
        async with self._lock:
            ttl = custom_ttl if custom_ttl is not None else self.ttl
            self._cache[key] = (time.time() + ttl, val)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()


class DiskThumbnailCache:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not pre-create thumbnail cache directory {self.base_dir}: {e}")

    def _get_path(self, item_type: str, item_id: str) -> Path:
        return self.base_dir / f"{item_type}_{item_id}.jpg"

    def get_thumbnail(self, item_type: str, item_id: str) -> Optional[bytes]:
        try:
            file_path = self._get_path(item_type, str(item_id))
            if file_path.exists():
                return file_path.read_bytes()
        except Exception as e:
            logger.warning(f"Error reading cached thumbnail: {e}")
        return None

    def save_thumbnail(self, item_type: str, item_id: str, data: bytes) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            file_path = self._get_path(item_type, str(item_id))
            file_path.write_bytes(data)
        except Exception as e:
            logger.warning(f"Error writing thumbnail to cache: {e}")

    def get_cache_size(self) -> Tuple[int, int]:
        """Returns (count of files, total bytes)."""
        count = 0
        total_bytes = 0
        if self.base_dir.exists():
            for f in self.base_dir.glob("*.jpg"):
                count += 1
                total_bytes += f.stat().st_size
        return count, total_bytes


memory_cache = MemoryCache(ttl=settings.cache_ttl)
thumbnail_cache = DiskThumbnailCache(base_dir=settings.thumbnails_dir)
