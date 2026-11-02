import asyncio
import hashlib
import io
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from app.config import settings

logger = logging.getLogger("grimmory_proxy.image_compressor")


def detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from initial header bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    elif data.startswith(b"%PDF"):
        return "application/pdf"
    elif data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


class MemoryPageCache:
    """
    High-performance in-memory LRU page cache.
    Eliminates all SSD disk writes while providing sub-millisecond retrieval
    for reader page flips.
    """

    def __init__(self, max_items: int = 150):
        self.max_items = max_items
        self._cache: OrderedDict[str, Tuple[bytes, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._total_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0

    def get(self, key: str) -> Optional[Tuple[bytes, str]]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, data: bytes, mime_type: str) -> None:
        with self._lock:
            if key in self._cache:
                old_data, _ = self._cache[key]
                self._total_bytes -= len(old_data)
                self._cache.pop(key)

            self._cache[key] = (data, mime_type)
            self._cache.move_to_end(key)
            self._total_bytes += len(data)

            while len(self._cache) > self.max_items:
                _, (evicted_data, _) = self._cache.popitem(last=False)
                self._total_bytes -= len(evicted_data)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._total_bytes = 0

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "type": "memory",
                "item_count": len(self._cache),
                "max_items": self.max_items,
                "total_bytes": self._total_bytes,
                "hits": self._hits,
                "misses": self._misses,
            }


class DiskPageCache:
    """Optional disk-based page cache with hashed filenames."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not pre-create disk page cache directory {self.base_dir}: {e}")
        self._hits: int = 0
        self._misses: int = 0

    @staticmethod
    def _safe_component(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def _get_path(self, key: str, ext: str = "bin") -> Path:
        filename = f"page_{self._safe_component(key)}.{ext}"
        base_path = os.path.realpath(str(self.base_dir))
        full_path = os.path.realpath(os.path.join(base_path, filename))
        if not full_path.startswith(base_path):
            raise ValueError(f"Path traversal detected: {filename}")
        return Path(full_path)

    def get(self, key: str) -> Optional[Tuple[bytes, str]]:
        try:
            file_path = self._get_path(key)
            meta_path = self._get_path(f"{key}_meta", ext="txt")
            if file_path.is_file() and meta_path.is_file():
                mime = meta_path.read_text(encoding="utf-8").strip()
                data = file_path.read_bytes()
                self._hits += 1
                return data, mime
        except Exception as e:
            logger.debug(f"Error reading disk page cache: {e}")
        self._misses += 1
        return None

    def set(self, key: str, data: bytes, mime_type: str) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            file_path = self._get_path(key)
            meta_path = self._get_path(f"{key}_meta", ext="txt")
            file_path.write_bytes(data)
            meta_path.write_text(mime_type, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Error writing to disk page cache: {e}")

    def clear(self) -> None:
        try:
            if self.base_dir.exists():
                for p in self.base_dir.glob("page_*"):
                    try:
                        p.unlink()
                    except OSError:
                        pass
        except Exception as e:
            logger.debug(f"Error clearing disk page cache: {e}")

    def get_stats(self) -> Dict[str, Any]:
        count = 0
        total_bytes = 0
        try:
            if self.base_dir.exists():
                for p in self.base_dir.glob("page_*.bin"):
                    count += 1
                    total_bytes += p.stat().st_size
        except Exception:
            pass
        return {
            "type": "disk",
            "item_count": count,
            "total_bytes": total_bytes,
            "hits": self._hits,
            "misses": self._misses,
        }


class ImageCompressor:
    """
    On-the-fly image optimizer and transcoder for reader clients.
    Compresses high-resolution comic/book pages to WebP or JPEG in RAM,
    reducing bandwidth and transfer latency without touching the SSD.
    """

    def __init__(self):
        self.memory_cache = MemoryPageCache(max_items=settings.image_cache_memory_max_items)
        self.disk_cache = DiskPageCache(base_dir=settings.image_cache_disk_dir)

    def _get_cache(self):
        cache_type = settings.image_cache_type.lower()
        if cache_type == "memory":
            return self.memory_cache
        elif cache_type == "disk":
            return self.disk_cache
        return None

    def _compress_sync(
        self,
        raw_bytes: bytes,
        target_format: str,
        quality: int,
        max_dimension: int,
        force_format: bool = False,
    ) -> Tuple[bytes, str]:
        """
        Synchronous Pillow processing executed inside an asyncio worker thread.
        """
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                orig_format = (img.format or "").upper()
                width, height = img.size

                # 1. Downscale if max_dimension is configured and exceeded
                if max_dimension > 0 and max(width, height) > max_dimension:
                    if width > height:
                        new_w = max_dimension
                        new_h = max(1, int(round(height * (max_dimension / width))))
                    else:
                        new_h = max_dimension
                        new_w = max(1, int(round(width * (max_dimension / height))))
                    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

                fmt = target_format.lower()
                out = io.BytesIO()

                # 2. Encode to target format with appropriate color mode handling
                if fmt == "webp":
                    # WebP supports RGB and RGBA. Convert CMYK, P, or 1 to RGB/RGBA.
                    if img.mode == "CMYK":
                        img = img.convert("RGB")
                    elif img.mode == "P":
                        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
                    elif img.mode not in ("RGB", "RGBA", "L"):
                        img = img.convert("RGB")

                    img.save(out, format="WEBP", quality=quality, method=4)
                    result_bytes = out.getvalue()
                    content_type = "image/webp"

                elif fmt in ("jpeg", "jpg"):
                    # JPEG does not support transparency. Flatten against white background.
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        rgba_img = img.convert("RGBA")
                        bg.paste(rgba_img, mask=rgba_img.split()[3])
                        img = bg
                    elif img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")

                    img.save(out, format="JPEG", quality=quality, optimize=True)
                    result_bytes = out.getvalue()
                    content_type = "image/jpeg"

                elif fmt == "png":
                    if img.mode == "CMYK":
                        img = img.convert("RGB")
                    img.save(out, format="PNG", optimize=True)
                    result_bytes = out.getvalue()
                    content_type = "image/png"

                else:
                    # Fallback to JPEG
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(out, format="JPEG", quality=quality, optimize=True)
                    result_bytes = out.getvalue()
                    content_type = "image/jpeg"

                # 3. Guard against negative compression (if compressed size > original size)
                # Only apply guard when client didn't explicitly request format conversion
                if not force_format and len(result_bytes) >= len(raw_bytes):
                    return raw_bytes, detect_image_mime(raw_bytes)

                return result_bytes, content_type

        except Exception as e:
            logger.warning(f"Error compressing page image: {e}")
            return raw_bytes, detect_image_mime(raw_bytes)

    async def process_page_image(
        self,
        book_id: int,
        page_number: int,
        raw_bytes: bytes,
        convert: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """
        Process, transcode, and compress page image asynchronously.
        Leverages in-memory LRU cache to ensure zero SSD wear.
        """
        if not raw_bytes:
            return raw_bytes, "image/jpeg"

        # Determine target format and whether compression should run
        client_requested_convert = convert is not None and convert.strip() != ""
        target_format: Optional[str] = None
        force_format = False

        if client_requested_convert:
            target_format = convert.strip().lower()
            force_format = True
        elif settings.image_compression_enabled:
            # Check minimum size threshold before applying compression
            threshold_bytes = settings.image_compression_min_size_kb * 1024
            if len(raw_bytes) < threshold_bytes:
                # Page is already sufficiently small; return original without recompressing
                return raw_bytes, detect_image_mime(raw_bytes)

            fmt_setting = settings.image_compression_format.lower()
            if fmt_setting == "original":
                # Detect original format
                orig_mime = detect_image_mime(raw_bytes)
                if "png" in orig_mime:
                    target_format = "png"
                elif "webp" in orig_mime:
                    target_format = "webp"
                else:
                    target_format = "jpeg"
            else:
                target_format = fmt_setting
        else:
            # Compression disabled and no conversion requested
            return raw_bytes, detect_image_mime(raw_bytes)

        quality = max(1, min(100, settings.image_compression_quality))
        max_dim = max(0, settings.image_compression_max_dimension)

        # Check cache
        cache = self._get_cache()
        cache_key = f"{book_id}:{page_number}:{target_format}:{quality}:{max_dim}"
        if cache:
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result

        # Run compression in thread pool to prevent blocking the async event loop
        compressed_bytes, content_type = await asyncio.to_thread(
            self._compress_sync,
            raw_bytes,
            target_format,
            quality,
            max_dim,
            force_format,
        )

        # Store in cache
        if cache:
            cache.set(cache_key, compressed_bytes, content_type)

        return compressed_bytes, content_type

    def clear_cache(self) -> None:
        """Clear all cached compressed pages from memory and disk."""
        self.memory_cache.clear()
        if settings.image_cache_type.lower() == "disk":
            self.disk_cache.clear()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Retrieve cache metrics and performance stats."""
        cache = self._get_cache()
        if cache:
            return cache.get_stats()
        return {
            "type": "none",
            "item_count": 0,
            "total_bytes": 0,
            "hits": 0,
            "misses": 0,
        }


image_compressor = ImageCompressor()
