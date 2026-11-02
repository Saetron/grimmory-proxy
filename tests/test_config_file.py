import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from httpx import ASGITransport, AsyncClient

from app.config import Settings, settings
from app.main import app
from app.models.internal import UserSession
from app.services.auth import AuthService


def test_first_startup_creates_config_file_from_env(tmp_path, monkeypatch):
    """
    On first startup, when config.json does not exist:
    Settings loads environment variables/defaults, then creates and exports
    them into config.json.
    """
    test_cfg_path = tmp_path / "config.json"
    assert not test_cfg_path.exists()

    monkeypatch.setenv("CONFIG_PATH", str(test_cfg_path))
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "42")
    monkeypatch.setenv("IMAGE_COMPRESSION_QUALITY", "92")

    # Instantiate Settings - triggers model_post_init and load_or_create_config_file
    s = Settings()
    assert s.sync_interval_minutes == 42
    assert s.image_compression_quality == 92

    # Verify config.json was created on first startup
    assert test_cfg_path.exists()
    content = json.loads(test_cfg_path.read_text(encoding="utf-8"))
    assert content["sync_interval_minutes"] == 42
    assert content["image_compression_quality"] == 92


def test_subsequent_startup_config_file_takes_precedence_over_env(tmp_path, monkeypatch):
    """
    On subsequent startups, when config.json exists:
    Values in config.json take precedence over environment variables.
    """
    test_cfg_path = tmp_path / "config.json"
    # Write pre-existing config.json
    existing_config = {
        "sync_interval_minutes": 15,
        "image_compression_enabled": True,
        "image_compression_format": "jpeg",
        "image_compression_quality": 65,
    }
    test_cfg_path.write_text(json.dumps(existing_config), encoding="utf-8")

    monkeypatch.setenv("CONFIG_PATH", str(test_cfg_path))
    # Environment variable contradicts the config file
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "99")
    monkeypatch.setenv("IMAGE_COMPRESSION_QUALITY", "100")

    s = Settings()
    # Config file must take precedence!
    assert s.sync_interval_minutes == 15
    assert s.image_compression_enabled is True
    assert s.image_compression_format == "jpeg"
    assert s.image_compression_quality == 65


def test_save_config_file_updates_file_and_memory(tmp_path, monkeypatch):
    """Saving settings updates the in-memory settings object and writes to disk."""
    test_cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("CONFIG_PATH", str(test_cfg_path))

    s = Settings()
    s.save_config_file({
        "sync_interval_minutes": 25,
        "image_compression_max_dimension": 1600,
        "image_cache_type": "memory",
    })

    assert s.sync_interval_minutes == 25
    assert s.image_compression_max_dimension == 1600

    saved_data = json.loads(test_cfg_path.read_text(encoding="utf-8"))
    assert saved_data["sync_interval_minutes"] == 25
    assert saved_data["image_compression_max_dimension"] == 1600


@pytest.mark.asyncio
async def test_admin_settings_webui_and_api(tmp_path, monkeypatch):
    """Verify /settings WebUI route and /admin/api/settings endpoints."""
    test_cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("CONFIG_PATH", str(test_cfg_path))
    settings.config_path = str(test_cfg_path)
    settings.load_or_create_config_file()

    user = UserSession(
        user_id=777,
        username="admin_settings_tester",
        token="tok_settings_tester",
        is_admin=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    AuthService.cache_session(user)
    headers = {"Authorization": "Bearer tok_settings_tester"}
    cookies = {"admin_session": "tok_settings_tester"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /settings HTML page
        resp_html = await client.get("/settings", cookies=cookies)
        assert resp_html.status_code == 200
        assert "Configuration & Bridge Settings" in resp_html.text
        assert "config.json" in resp_html.text

        # 2. GET /admin/api/settings API endpoint
        resp_api = await client.get("/admin/api/settings", headers=headers)
        assert resp_api.status_code == 200
        data = resp_api.json()
        assert data["success"] is True
        assert "sync_interval_minutes" in data["settings"]
        assert "raw_json" in data

        # 3. POST /admin/api/settings update
        resp_post = await client.post(
            "/admin/api/settings",
            json={
                "settings": {
                    "sync_interval_minutes": 18,
                    "image_compression_enabled": True,
                    "image_compression_quality": 88,
                }
            },
            headers=headers,
        )
        assert resp_post.status_code == 200
        post_data = resp_post.json()
        assert post_data["success"] is True
        assert post_data["settings"]["sync_interval_minutes"] == 18
        assert post_data["settings"]["image_compression_quality"] == 88

        # Verify disk file was updated
        disk_data = json.loads(test_cfg_path.read_text(encoding="utf-8"))
        assert disk_data["sync_interval_minutes"] == 18
        assert disk_data["image_compression_quality"] == 88
