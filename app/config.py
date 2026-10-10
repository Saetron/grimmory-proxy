from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


import os


def _load_version() -> str:
    # 1. Check environment variable override
    env_ver = os.environ.get("APP_VERSION", "").strip()
    if env_ver:
        return env_ver

    # 2. Check candidate paths for VERSION file
    candidates = [
        Path(__file__).resolve().parent.parent / "VERSION",  # /app/VERSION or repo/VERSION
        Path(__file__).resolve().parent / "VERSION",         # /app/app/VERSION
        Path.cwd() / "VERSION",                              # working directory
        Path("/app/VERSION"),                                # container default root
    ]
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except Exception:
            pass
    return "0.3"



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_version: str = Field(
        default_factory=_load_version,
        description="Grimmory Proxy release version",
    )

    # Grimmory upstream connection
    grimmory_url: str = Field(
        default="http://localhost:8080",
        description="URL of your Grimmory instance (without trailing slash)",
    )
    grimmory_public_url: str = Field(
        default="",
        description="Public browser-accessible URL of your Grimmory instance (falls back to grimmory_url if empty)",
    )

    # Dedicated background sync credentials (strictly isolated from client reading sessions)
    sync_username: str = Field(
        default="",
        description="Dedicated username for background sync (e.g. admin account)",
    )
    sync_password: str = Field(
        default="",
        description="Dedicated password for background sync",
    )
    sync_interval_minutes: int = Field(
        default=30,
        description="Interval in minutes between background metadata sync cycles (0 to disable)",
    )
    sync_on_startup: bool = Field(
        default=True,
        description="Whether to trigger a background sync on startup",
    )
    sync_concurrency: int = Field(
        default=6,
        description="Concurrency limit for background series/book page inspection",
    )
    user_sync_interval_minutes: int = Field(
        default=5,
        description="Interval in minutes between background user read status sync cycles (0 to disable)",
    )

    # Storage and paths
    database_path: str = Field(
        default="/app/data/bridge.db",
        description="Path to persistent SQLite cache database",
    )
    thumbnails_dir: str = Field(
        default="/app/data/thumbnails",
        description="Path to disk cache for cover thumbnails",
    )

    # Server configuration
    port: int = Field(
        default=8080,
        description="Port the proxy listens on inside the container",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Bind host address",
    )
    cache_ttl: int = Field(
        default=300,
        description="TTL in seconds for in-memory page metadata caches",
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info",
        description="Logging verbosity (debug, info, warning, error)",
    )

    # WebUI admin session secret key
    admin_session_secret: str = Field(
        default="grimmory-proxy-secret-key-change-me",
        description="Secret key for signing WebUI admin session cookies",
    )

    # Calibre page calculation characters per page
    novel_chars_per_page: int = Field(
        default=1024,
        description="Number of text characters considered one page for novels (Calibre ADE standard: 1024)",
    )

    @property
    def public_grimmory_url(self) -> str:
        if self.grimmory_public_url:
            return self.grimmory_public_url.rstrip("/")
        return self.grimmory_url.rstrip("/")

    @field_validator("grimmory_url", "grimmory_public_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else ""

    def ensure_directories(self) -> None:
        """Ensure database parent directory and thumbnails directory exist."""
        try:
            db_path = Path(self.database_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            thumb_path = Path(self.thumbnails_dir)
            thumb_path.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass


settings = Settings()
