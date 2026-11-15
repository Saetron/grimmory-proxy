import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from app.clients.grimmory import grimmory_client
from app.config import settings

logger = logging.getLogger("grimmory_proxy.reading_sessions")


@dataclass
class _ActiveSession:
    user_id: int
    username: str
    token: Optional[str]
    book_id: int
    start_time: datetime
    end_time: datetime
    timer_task: Optional[asyncio.Task] = None


class ReadingSessionTracker:
    """Aggregates page turns and progress updates into Grimmory reading sessions."""

    def __init__(self, inactivity_timeout_seconds: int = 120) -> None:
        self.inactivity_timeout_seconds = inactivity_timeout_seconds
        self._sessions: Dict[Tuple[int, int], _ActiveSession] = {}

    def record_activity(
        self,
        user_id: int,
        username: str,
        token: Optional[str],
        book_id: int,
        completed: bool = False,
    ) -> None:
        """Record reading activity for a user/book and schedule or flush the session."""
        now = datetime.now(timezone.utc)
        eff_token = None if (settings.sync_username and username == settings.sync_username) else token

        # Flush any active session for the same user on a different book
        other_keys = [
            k for k in list(self._sessions.keys())
            if k[0] == user_id and k[1] != book_id
        ]
        for k in other_keys:
            self._schedule_flush_now(k)

        key = (user_id, book_id)
        existing = self._sessions.get(key)
        if existing is not None:
            if existing.timer_task and not existing.timer_task.done():
                existing.timer_task.cancel()
            existing.end_time = now
            existing.token = eff_token
            session = existing
        else:
            session = _ActiveSession(
                user_id=user_id,
                username=username,
                token=eff_token,
                book_id=book_id,
                start_time=now,
                end_time=now,
            )
            self._sessions[key] = session

        if completed:
            self._schedule_flush_now(key)
        else:
            session.timer_task = asyncio.create_task(self._delayed_flush(key))

    def _schedule_flush_now(self, key: Tuple[int, int]) -> None:
        session = self._sessions.pop(key, None)
        if not session:
            return
        if session.timer_task and not session.timer_task.done():
            session.timer_task.cancel()
        asyncio.create_task(self._submit_session(session))

    async def _delayed_flush(self, key: Tuple[int, int]) -> None:
        try:
            await asyncio.sleep(self.inactivity_timeout_seconds)
            session = self._sessions.pop(key, None)
            if session:
                await self._submit_session(session)
        except asyncio.CancelledError:
            pass

    async def _submit_session(self, session: _ActiveSession) -> None:
        end_time = session.end_time
        if end_time <= session.start_time:
            end_time = session.start_time + timedelta(seconds=1)
        duration_seconds = max(1, int((end_time - session.start_time).total_seconds()))
        start_str = session.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            ok = await grimmory_client.create_reading_session(
                book_id=session.book_id,
                start_time=start_str,
                end_time=end_str,
                duration_seconds=duration_seconds,
                token=session.token,
            )
            if ok:
                logger.info(
                    f"Logged Grimmory reading session: user={session.username}, "
                    f"book={session.book_id}, duration={duration_seconds}s"
                )
        except Exception as e:
            logger.warning(f"Error logging reading session for book {session.book_id}: {e}")

    async def flush_all(self) -> None:
        """Flush all active reading sessions (e.g., on application shutdown)."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for s in sessions:
            if s.timer_task and not s.timer_task.done():
                s.timer_task.cancel()
        if sessions:
            await asyncio.gather(*(self._submit_session(s) for s in sessions), return_exceptions=True)


reading_session_tracker = ReadingSessionTracker()
