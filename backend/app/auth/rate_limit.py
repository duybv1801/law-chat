"""In-memory sliding-window rate limiter (single backend process, per docs/06-api.md)."""

import math
import time
from collections import deque
from collections.abc import Callable

from fastapi import status

from app.core.errors import AppError

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_S = 15 * 60
REGISTER_MAX_ATTEMPTS = 5
REGISTER_WINDOW_S = 60 * 60
# Saving an API key calls the provider (T17): stop using the server as a key-checking oracle.
API_KEY_SAVE_MAX_ATTEMPTS = 20
API_KEY_SAVE_WINDOW_S = 60 * 60


class SlidingWindowLimiter:
    def __init__(
        self, max_hits: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_hits = max_hits
        self.window_s = window_s
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = clock()

    def _prune(self, key: str, now: float) -> deque[float] | None:
        hits = self._hits.get(key)
        if hits is None:
            return None
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def _sweep(self, now: float) -> None:
        """Drop keys idle for a whole window, at most once per window (bounds memory)."""
        if now - self._last_sweep < self.window_s:
            return
        self._last_sweep = now
        for key in list(self._hits):
            self._prune(key, now)

    def retry_after(self, key: str) -> float | None:
        """Return None if `key` may make another attempt, else seconds until the next slot."""
        now = self.clock()
        self._sweep(now)
        hits = self._prune(key, now)
        if hits is not None and len(hits) >= self.max_hits:
            return hits[0] + self.window_s - now
        return None

    def hit(self, key: str) -> float | None:
        """Record one attempt. Return None if allowed, else seconds until the next slot."""
        wait = self.retry_after(key)
        if wait is None:
            self._hits.setdefault(key, deque()).append(self.clock())
        return wait

    def reset(self) -> None:
        self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)


# Login counts failed attempts only, so successful logins from a shared IP (NAT, proxy)
# never lock others out.
login_limiter = SlidingWindowLimiter(LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S)
register_limiter = SlidingWindowLimiter(REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S)
api_key_save_limiter = SlidingWindowLimiter(API_KEY_SAVE_MAX_ATTEMPTS, API_KEY_SAVE_WINDOW_S)


def too_many_requests(retry_after: float) -> AppError:
    return AppError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too_many_requests",
        "Bạn đã thử quá nhiều lần. Vui lòng thử lại sau.",
        headers={"Retry-After": str(math.ceil(retry_after))},
    )


def enforce(limiter: SlidingWindowLimiter, key: str, *, record: bool) -> None:
    """Raise 429 if `key` is over the limit; `record=True` also counts this attempt."""
    retry_after = limiter.hit(key) if record else limiter.retry_after(key)
    if retry_after is not None:
        raise too_many_requests(retry_after)
