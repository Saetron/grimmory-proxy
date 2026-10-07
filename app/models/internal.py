from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class UserSession(BaseModel):
    user_id: int
    username: str
    token: str
    is_admin: bool = False
    assigned_library_ids: List[int] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime


class PageCalcStatus(BaseModel):
    job_id: Optional[int] = None
    is_running: bool = False
    total_books: int = 0
    processed_books: int = 0
    updated_books: int = 0
    error_count: int = 0
    current_book: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class SyncStatus(BaseModel):
    is_running: bool = False
    last_sync_time: Optional[str] = None
    current_phase: str = "idle"
    total_items: int = 0
    processed_items: int = 0
    error_message: Optional[str] = None
