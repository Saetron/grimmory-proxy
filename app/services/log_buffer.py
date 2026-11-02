import logging
from collections import deque
from typing import List


class RingBufferLogHandler(logging.Handler):
    """
    In-memory logging ring buffer retaining the last N log lines (default 200).
    Provides thread-safe log retrieval for the WebUI /logs viewer.
    """

    def __init__(self, capacity: int = 200):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

    def get_logs(self) -> List[str]:
        return list(self.buffer)

    def clear(self) -> None:
        self.buffer.clear()


proxy_log_handler = RingBufferLogHandler(capacity=200)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
proxy_log_handler.setFormatter(_formatter)
