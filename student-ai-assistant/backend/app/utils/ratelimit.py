"""
In-process sliding-window rate limiter.

Scope, stated plainly: counters live in this process's memory. With one backend
instance — the current Railway deployment — that is accurate. Run two replicas
and each enforces the limit separately, so the effective limit doubles.

That is a deliberate trade for now: the limits here exist to stop one student
burning the shared Groq free-tier quota, not to enforce a billing boundary. When
this becomes a paid entitlement, move the counters to Redis (already a
dependency via REDIS_URL) — the call signature is designed not to change.
"""

import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: int, name: str = "default"):
        self.max_calls = max_calls
        self.window = window_seconds
        self.name = name
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()
        return hits

    def check(self, key: str) -> tuple[bool, int, int]:
        """
        Test and consume one unit.

        Returns (allowed, remaining, retry_after_seconds).
        """
        now = time.monotonic()
        hits = self._prune(key, now)

        if len(hits) >= self.max_calls:
            retry_after = int(hits[0] + self.window - now) + 1
            logger.info("Rate limit hit (%s) for %s — retry in %ds", self.name, key, retry_after)
            return False, 0, retry_after

        hits.append(now)
        return True, self.max_calls - len(hits), 0

    def peek(self, key: str) -> int:
        """Remaining allowance without consuming any."""
        return max(0, self.max_calls - len(self._prune(key, time.monotonic())))

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
