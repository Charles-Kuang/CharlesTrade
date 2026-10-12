"""In-process pub/sub for chart live updates (SSE)."""

from __future__ import annotations

import threading
from typing import Any, Optional


class UpdateHub:
    """Notify waiters when Live flush (or other writers) land new bars."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        self._payload: Optional[dict[str, Any]] = None

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def publish(self, payload: dict[str, Any]) -> int:
        with self._cond:
            self._seq += 1
            self._payload = dict(payload)
            self._payload["seq"] = self._seq
            self._cond.notify_all()
            return self._seq

    def wait_after(
        self,
        after_seq: int,
        *,
        timeout: float = 25.0,
    ) -> tuple[int, Optional[dict[str, Any]]]:
        """Block until seq > after_seq or timeout. Returns (seq, payload)."""
        with self._cond:
            if self._seq > after_seq:
                return self._seq, self._payload
            self._cond.wait(timeout=timeout)
            return self._seq, self._payload
