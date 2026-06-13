"""In-memory ring buffer of recent log records, exposed at GET /api/logs.

Lets the operator read what Hearth is doing from the UI without shelling into
`docker logs`. It's deliberately tiny and bounded — the last N records live in a
deque; nothing is persisted to disk (logs already go to stdout/journald for
that). Attached to the root logger in main.create_app so it captures hearth,
uvicorn and library logs alike.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque


class RingBufferHandler(logging.Handler):
    """A logging.Handler that keeps the most recent `capacity` records."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken format string must not crash logging
            msg = record.msg
        with self._lock:
            self._seq += 1
            entry = {
                "seq": self._seq,
                "ts": time.time(),
                "level": record.levelname,
                "levelno": record.levelno,
                "logger": record.name,
                "message": msg,
            }
            if record.exc_info:
                entry["message"] += "\n" + self.format(
                    logging.LogRecord(record.name, record.levelno, "", 0, "", (),
                                      record.exc_info))
            self._buf.append(entry)

    def records(self, *, min_level: int = 0, limit: int = 500,
                since_seq: int | None = None) -> list[dict]:
        """Newest-last slice, optionally filtered by level and a cursor.

        `since_seq` returns only records after that sequence number, so the UI
        can poll incrementally instead of re-fetching the whole buffer.
        """
        with self._lock:
            items = list(self._buf)
        if since_seq is not None:
            items = [e for e in items if e["seq"] > since_seq]
        if min_level:
            items = [e for e in items if e["levelno"] >= min_level]
        return items[-limit:]
