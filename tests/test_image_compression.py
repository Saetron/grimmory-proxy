import asyncio
import io
import pytest
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import settings
from app.main import app, db
from app.clients.grimmory import grimmory_client
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.image_compressor import ImageCompressor, MemoryPageCache, detect_image_mime, image_compressor


def create_test_image(width: int = 2400, height: int = 1600, mode: str = "RGB", color="blue", format: str = "JPEG") -> bytes:
    """Helper to generate in-memory image bytes."""
    img = Image.new(mode, (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()


def test_memory_page_cache_lru_and_zero_ssd_writes(tmp_path):
    """Verify that MemoryPageCache operates strictly in RAM with LRU eviction."""
    cache = MemoryPageCache(max_items=3)

    cache.set("page_1", b"data_1", "image/webp")
    cache.set("page_2", b"data_2", "image/webp")
    cache.set("page_3", b"data_3", "image/webp")

    stats = cache.get_stats()
    assert stats["type"] == "memory"
    assert stats["item_count"] == 3
    assert stats["total_bytes"] == len(b"data_1") + len(b"data_2") + len(b"data_3")

    # Hit page_1 so it becomes most recently used
    res1 = cache.get("page_1")
    assert res1 == (b"data_1", "image/webp")

    # Adding page_4 should evict page_2 (since page_1 was accessed)
    cache.set("page_4", b"data_4", "image/webp")
    assert cache.get("page_2") is None
    assert cache.get("page_1") is not None
    assert cache.get("page_3") is not None
    assert cache.get("page_4") is not None

    # Clear cache
    cache.clear()
    assert cache.get_stats()["item_count"] == 0
    assert cache.get_stats()["total_bytes"] == 0


def test_image_compressor_webp_downscaling_and_modes():
    """Verify ImageCompressor downscales dimensions and properly handles color modes."""
    compressor = ImageCompressor()

    # 1. Test downscaling high-res 3000x2000 JPEG to max_dimension=1000
    large_jpeg = create_test_image(3000, 2000, mode="RGB", color="green", format="JPEG")
    comp_bytes, mime = compressor._compress_sync(
        raw_bytes=large_jpeg,
        target_format="webp",
        quality=80,
        max_dimension=1000,
        force_format=True,
    )
    assert mime == "image/webp"
    assert comp_bytes[:4] == b"RIFF"
    with Image.open(io.BytesIO(comp_bytes)) as out_img:
        assert max(out_img.width, out_img.height) == 1000
        # Aspect ratio preserved (3000:2000 = 3:2, so 1000:667)
        assert out_img.width == 1000
        assert out_img.height == 667

    # 2. Test RGBA to JPEG (ensures alpha channel is flattened against white background without error)
    rgba_png = create_test_image(200, 200, mode="RGBA", color=(255, 0, 0, 128), format="PNG")
    comp_jpeg, mime_jpeg = compressor._compress_sync(
        raw_bytes=rgba_png,
        target_format="jpeg",
        quality=85,
        max_dimension=0,
        force_format=True,
    )
    assert mime_jpeg == "image/jpeg"
    assert comp_jpeg[:2] == b"\xff\xd8"

    # 3. Test CMYK to WebP conversion
    cmyk_img = create_test_image(100, 100, mode="CMYK", format="JPEG")
    comp_cmyk, mime_cmyk = compressor._compress_sync(
        raw_bytes=cmyk_img,
        target_format="webp",
        quality=80,
        max_dimension=0,
        force_format=True,
    )
    assert mime_cmyk == "image/webp"

    # 4. Graceful handling of corrupt bytes
    corrupt_data = b"not_an_image_data_at_all"
    res_bytes, res_mime = compressor._compress_sync(
        raw_bytes=corrupt_data,
        target_format="webp",
        quality=80,
        max_dimension=1000,
        force_format=True,
    )
    assert res_bytes == corrupt_data


@pytest.mark.asyncio
async def test_get_book_page_image_compression_and_caching(monkeypatch):
    """Test full GET /api/v1/books/{id}/pages/{page_number} with compression and RAM cache."""
    await db.connect()
    user_id = 450
    user = UserSession(
        user_id=user_id,
        username="compress_tester",
        token="tok_compress",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer tok_compress"}

    # Setup dummy library and book
    await db.upsert_libraries([{"id": 45, "name": "Manga Lib", "paths": []}])
    await db.upsert_series_batch([{"id": "45-ser", "library_id": 45, "name": "Big Manga", "slug": "big-manga", "books_count": 1}])
    await db.upsert_books_batch([{"id": 4501, "series_id": "45-ser", "library_id": 45, "name": "Big Book", "number": 1.0, "page_count": 10}])

    # Generate a large fake image (> 500 KB uncompressed)
    large_img = create_test_image(1600, 2400, mode="RGB", color="purple", format="JPEG")

    async def mock_stream_page(b_id, p_num, token=None):
        return large_img

    monkeypatch.setattr(grimmory_client, "stream_page_image", mock_stream_page)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Compression DISABLED: should return original JPEG image
        settings.image_compression_enabled = False
        resp = await client.get("/api/v1/books/4501/pages/1", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert len(resp.content) == len(large_img)

        # B. Client explicitly passes ?convert=webp: should transcode to WebP regardless of global setting
        resp_webp = await client.get("/api/v1/books/4501/pages/1?convert=webp", headers=headers)
        assert resp_webp.status_code == 200
        assert resp_webp.headers["content-type"] == "image/webp"
        assert resp_webp.content[:4] == b"RIFF"

        # C. Enable global compression: should compress to WebP by default
        image_compressor.clear_cache()
        settings.image_compression_enabled = True
        settings.image_compression_format = "webp"
        settings.image_compression_quality = 75
        settings.image_compression_max_dimension = 1200
        settings.image_compression_min_size_kb = 10  # force compression on our test image

        resp_comp = await client.get("/api/v1/books/4501/pages/1", headers=headers)
        assert resp_comp.status_code == 200
        assert resp_comp.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(resp_comp.content)) as im:
            assert max(im.size) <= 1200

        # D. Verify subsequent fetch hits the in-memory cache (0 SSD writes)
        stats_before = image_compressor.get_cache_stats()
        hits_before = stats_before["hits"]
        resp_cached = await client.get("/api/v1/books/4501/pages/1", headers=headers)
        assert resp_cached.status_code == 200
        stats_after = image_compressor.get_cache_stats()
        assert stats_after["hits"] == hits_before + 1

        # Reset setting
        settings.image_compression_enabled = False


@pytest.mark.asyncio
async def test_admin_api_image_compression_toggle_and_cache_clear():
    """Test Admin WebUI endpoints for toggling compression and clearing RAM cache."""
    user = UserSession(
        user_id=451,
        username="admin_compress",
        token="tok_admin_comp",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer tok_admin_comp"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Update image compression config
        resp_update = await client.post(
            "/admin/api/image-compression",
            json={
                "enabled": True,
                "format": "webp",
                "quality": 85,
                "max_dimension": 2048,
                "min_size_kb": 300,
            },
            headers=headers,
        )
        assert resp_update.status_code == 200
        data = resp_update.json()
        assert data["success"] is True
        assert data["image_compression"]["enabled"] is True
        assert data["image_compression"]["format"] == "webp"
        assert data["image_compression"]["quality"] == 85
        assert data["image_compression"]["max_dimension"] == 2048
        assert settings.image_compression_enabled is True

        # 2. Check dashboard status API includes image_compression
        resp_status = await client.get("/admin/api/status", headers=headers)
        assert resp_status.status_code == 200
        status_data = resp_status.json()
        assert "image_compression" in status_data
        assert status_data["image_compression"]["enabled"] is True

        # 3. Clear cache endpoint
        resp_clear = await client.post("/admin/api/image-compression/clear-cache", headers=headers)
        assert resp_clear.status_code == 200
        clear_data = resp_clear.json()
        assert clear_data["success"] is True
        assert clear_data["cache_stats"]["item_count"] == 0

        # Reset setting
        settings.image_compression_enabled = False
