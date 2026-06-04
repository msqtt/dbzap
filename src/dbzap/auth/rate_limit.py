"""In-process sliding-window rate limiter for auth endpoints.

See specs/06-auth.md "Rate limiting on /auth/login". The limiter is
process-local — adequate to make online brute-force expensive on a
single worker, but not coordinated across multiple workers or pods.
A shared backend (Redis) is the standard next step; out of scope for
the initial fix.

Time source is injectable so tests can drive the clock without sleep.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Thread-safe per-key sliding-window rate limiter.

    ``check(key)`` returns ``(allowed, retry_after_seconds)``. When
    ``allowed`` is ``True`` the call is recorded as one usage of the
    window. When ``False`` the caller MUST short-circuit BEFORE running
    any expensive work (e.g. bcrypt verify) — that is the resource the
    limiter is protecting.

    A ``max_calls`` of ``0`` disables the limiter entirely (always
    allows). Useful for tests and for environments fronted by an
    external limiter (CDN, ingress, etc.).
    """

    def __init__(
        self,
        *,
        max_calls: int,
        window_seconds: float,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        if max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max = max_calls
        self._window = float(window_seconds)
        self._time = time_func or time.monotonic
        self._lock = threading.Lock()
        # ``deque`` of timestamps per key. Oldest at index 0.
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return self._max > 0

    def check(self, key: str) -> tuple[bool, float]:
        if self._max == 0:
            return True, 0.0

        now = self._time()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets[key]
            # Drop entries outside the current window.
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                # Time until the oldest entry exits the window.
                retry_after = bucket[0] + self._window - now
                return False, max(0.0, retry_after)
            bucket.append(now)
            return True, 0.0

    def reset(self, key: str | None = None) -> None:
        """Clear state. ``key=None`` wipes everything (used by tests)."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)
