from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import json
import logging
import os

logger = logging.getLogger("grimmory_proxy.config")

CONFIG_FIELDS: List[str] = [
    "grimmory_url",
    "grimmory_public_url",
    "sync_username",
    "sync_password",
    "sync_interval_minutes",
    "sync_on_startup",
    "sync_concurrency",
    "user_sync_interval_minutes",
    "database_path",
    "thumbnails_dir",
    "sqlite_cache_size",
    "sqlite_mmap_size",
    "sqlite_wal_autocheckpoint",
    "port",
    "host",
    "cache_ttl",
    "log_level",
    "cors_allow_origins",
    "cookie_secure",
    "novel_chars_per_page",
    "image_compression_enabled",
    "image_compression_format",
    "image_compression_quality",
    "image_compression_max_dimension",
    "image_compression_min_size_kb",
    "image_cache_type",
    "image_cache_memory_max_items",
    "image_cache_disk_dir",
]


def _cast_setting_value(field_name: str, value: Any, current_val: Any) -> Any:
    if value is None:
        return current_val
    if isinstance(current_val, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if isinstance(current_val, int):
        try:
            return int(value)
        except (ValueError, TypeError):
            return current_val
    if isinstance(current_val, float):
        try:
            return float(value)
        except (ValueError, TypeError):
            return current_val
    if isinstance(current_val, str):
        val_str = str(value)
        if field_name in ("grimmory_url", "grimmory_public_url"):
            val_str = val_str.rstrip("/")
        return val_str
    return value


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
    sqlite_cache_size: int = Field(
        default=-8000,
        description="SQLite page cache size limit in KiB (negative value specifies size in KiB, default: -8000 = 8MB)",
    )
    sqlite_mmap_size: int = Field(
        default=67108864,
        description="SQLite mmap size limit in bytes (default: 67108864 = 64MB, 0 to disable mmap)",
    )
    sqlite_wal_autocheckpoint: int = Field(
        default=1000,
        description="SQLite WAL autocheckpoint page threshold (default: 1000 pages)",
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

    # Web security
    cors_allow_origins: str = Field(
        default="*",
        description=(
            "Comma-separated list of browser origins allowed by CORS. '*' allows any origin "
            "but disables credentialed (cookie) cross-origin requests."
        ),
    )
    cookie_secure: bool = Field(
        default=False,
        description="Set the Secure flag on the admin session cookie (enable when served over HTTPS)",
    )

    # Calibre page calculation characters per page
    novel_chars_per_page: int = Field(
        default=1024,
        description="Number of text characters considered one page for novels (Calibre ADE standard: 1024)",
    )

    # Reader image compression & optimization
    image_compression_enabled: bool = Field(
        default=False,
        description="Whether to compress/optimize reader page images on the fly",
    )
    image_compression_format: Literal["webp", "jpeg", "original"] = Field(
        default="webp",
        description="Target format for compressed reader pages ('webp', 'jpeg', or 'original')",
    )
    image_compression_quality: int = Field(
        default=80,
        description="Quality setting (1-100) for on-the-fly compressed pages",
    )
    image_compression_max_dimension: int = Field(
        default=1920,
        description="Maximum width or height in pixels for compressed pages (0 to preserve original dimensions)",
    )
    image_compression_min_size_kb: int = Field(
        default=400,
        description="Minimum file size in KB before compression is applied (pages smaller than this are served as-is)",
    )
    image_cache_type: Literal["memory", "none", "disk"] = Field(
        default="memory",
        description="Cache backend for compressed pages ('memory', 'none', or 'disk')",
    )
    image_cache_memory_max_items: int = Field(
        default=150,
        description="Maximum number of compressed pages retained in memory LRU cache",
    )
    image_cache_disk_dir: str = Field(
        default="/app/data/page_cache",
        description="Disk directory for cached compressed pages when image_cache_type='disk'",
    )
    config_path: str = Field(
        default="",
        description="Path to persistent config.json file (defaults to data/config.json or alongside database_path)",
    )

    def resolve_config_path(self) -> Path:
        """Resolve the active config.json path."""
        if self.config_path:
            return Path(self.config_path)
        env_path = os.environ.get("CONFIG_PATH", "").strip()
        if env_path:
            return Path(env_path)

        # 1. Try alongside database_path parent directory
        if self.database_path:
            try:
                db_parent = Path(self.database_path).parent
                db_parent.mkdir(parents=True, exist_ok=True)
                if db_parent.is_dir():
                    return db_parent / "config.json"
            except (PermissionError, OSError):
                pass

        # 2. Try container /app/data
        container_data = Path("/app/data")
        try:
            container_data.mkdir(parents=True, exist_ok=True)
            if container_data.is_dir():
                return container_data / "config.json"
        except (PermissionError, OSError):
            pass

        # 3. Local data directory fallback
        local_data = Path("data")
        try:
            local_data.mkdir(parents=True, exist_ok=True)
            return local_data / "config.json"
        except (PermissionError, OSError):
            return Path("config.json")

    def to_config_dict(self) -> Dict[str, Any]:
        """Export all configurable settings into a dictionary."""
        result = {}
        for field in CONFIG_FIELDS:
            result[field] = getattr(self, field, None)
        return result

    def load_or_create_config_file(self) -> Path:
        """
        Loads settings from config.json if it exists (takes precedence over env vars).
        If config.json does not exist (first startup), builds and creates config.json
        from the current environment variables and defaults.
        """
        cfg_path = self.resolve_config_path()
        if cfg_path.exists():
            try:
                content = cfg_path.read_text(encoding="utf-8")
                data = json.loads(content)
                if isinstance(data, dict):
                    needs_resave = False
                    for field in CONFIG_FIELDS:
                        if field in data:
                            curr_val = getattr(self, field, None)
                            casted_val = _cast_setting_value(field, data[field], curr_val)
                            setattr(self, field, casted_val)
                        else:
                            # Setting added in a newer version; merge into file
                            needs_resave = True
                    if needs_resave:
                        self.save_config_file()
                logger.info(f"Loaded configuration from {cfg_path} (precedes environment variables)")
            except Exception as e:
                logger.warning(f"Error loading config file from {cfg_path}: {e}")
        else:
            try:
                self.save_config_file()
                logger.info(f"Created initial config file at {cfg_path} based on environment variables")
            except Exception as e:
                logger.warning(f"Error creating initial config file at {cfg_path}: {e}")
        return cfg_path

    def save_config_file(self, updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Updates in-memory settings and writes the updated dictionary to config.json.
        """
        if updates:
            for k, v in updates.items():
                if k in CONFIG_FIELDS:
                    if k == "sync_password" and (v is None or v == ""):
                        continue  # Keep existing password if blank
                    curr_val = getattr(self, k, None)
                    casted = _cast_setting_value(k, v, curr_val)
                    setattr(self, k, casted)

        cfg_path = self.resolve_config_path()
        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(self.to_config_dict(), indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Error saving config file to {cfg_path}: {e}")
        return self.to_config_dict()

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.load_or_create_config_file()

    @property
    def cors_origins_list(self) -> List[str]:
        origins = [o.strip().rstrip("/") for o in self.cors_allow_origins.split(",") if o.strip()]
        return origins or ["*"]

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
