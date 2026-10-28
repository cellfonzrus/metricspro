"""THE in-process TTL cache — bounded by construction (index §CORE-19, load test 2026-10-28).

WHY THIS MODULE EXISTS
──────────────────────
`tenant_middleware._cache` mapped a bearer token to its resolved identity with a 60-second TTL.
Entries EXPIRED but were never DELETED, and there was no size cap, so the dict grew for the life of
the worker process. Its key is the token, which the auth service re-mints on every refresh — so the
key space is NOT bounded by the number of users. The 1,000-user load test made that concrete: a
single run put a thousand dead entries in, and nothing ever took one out. `_entry_cache`
(uid+org → operator entry session) had the same shape.

THE CLASS, NOT THE INSTANCE (house rule 2026-09-20). The defect is not "that one dict leaks"; it is
"a module-level dict used as a TTL cache, with no cap and no eviction", and the platform had four of
them written four different ways. Capping one dict is the patchwork the rules forbid. So eviction
lives HERE, in one place that is bounded by construction, and callers hold an instance rather than
re-deriving the bookkeeping. Forgetting to prune stops being possible.

WHAT IT IS, EXACTLY. A dict with a time-to-live and a hard entry cap. On insert, if the cap is
reached, EXPIRED entries are dropped first (they are free) and only then the oldest live ones — so
pressure never evicts something fresh while something dead is still held. Thread-safe: one lock
around the mutating paths, which is enough because the app serves sync handlers on a worker-thread
pool and several threads reach the same cache at once.

WHAT IT IS NOT. Not a rate limiter (`core/rate_limit.RateLimiter` is a fixed-window COUNTER with its
own bound), not a cross-process cache (each gunicorn worker holds its own, exactly as before), and
not a correctness mechanism — every caller must still behave correctly on a miss, because a bounded
cache may evict at any time. That is why only POSITIVE, re-derivable results belong in one.

`clock` is injectable so a proof harness can move time without sleeping
(`backend/harness_ttl_cache_bounded.py`).
"""
import os
import threading
import time
from typing import Any, Callable, Hashable, Optional

DEFAULT_CAP = 4096

MISS = _MISS = object()   # distinguishes "not cached" from a cached None (see `get`)


def cap_from_env(name: str, default: int) -> int:
    """An operator-tunable cap. A missing or unparseable value falls back — never crashes a boot."""
    try:
        v = int(os.environ.get(name, "") or default)
        return v if v > 0 else default
    except Exception:
        return default


class TTLCache:
    """A bounded, time-limited, thread-safe memo. `get` returns None on a miss OR an expired entry."""

    def __init__(self, ttl: float, cap: int = DEFAULT_CAP,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if cap <= 0:
            raise ValueError("cap must be positive")
        self.ttl = float(ttl)
        self.cap = int(cap)
        self._clock = clock
        self._lock = threading.Lock()
        self._data: "dict[Hashable, tuple[float, Any]]" = {}   # key -> (expires_at, value)

    # ── reads ─────────────────────────────────────────────────────────────────────────────────
    def get(self, key: Hashable, default: Any = None) -> Optional[Any]:
        """The value, or `default` on a miss or an expired entry.

        `default` matters when None is a legitimate CACHED value ("this tenant has no policy row"):
        pass a sentinel and a stored None stops being indistinguishable from a miss."""
        hit = self._data.get(key)
        if hit is None:
            return default
        if hit[0] <= self._clock():
            return default         # expired: left for the next insert to reap, never served
        return hit[1]

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, _MISS) is not _MISS

    # ── writes ────────────────────────────────────────────────────────────────────────────────
    def put(self, key: Hashable, value: Any, ttl: Optional[float] = None) -> None:
        now = self._clock()
        with self._lock:
            self._data[key] = (now + (self.ttl if ttl is None else float(ttl)), value)
            if len(self._data) > self.cap:
                self._evict(now)

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # ── eviction ──────────────────────────────────────────────────────────────────────────────
    def _evict(self, now: float) -> None:
        """Dead entries first (free), then the oldest live ones — only as far as the cap demands.

        Caller holds the lock. Insertion order is Python's dict order, so popping from the front is
        oldest-first; with a single TTL that is also nearest-to-expiry-first."""
        for k in [k for k, v in self._data.items() if v[0] <= now]:
            self._data.pop(k, None)
        while len(self._data) > self.cap:
            self._data.pop(next(iter(self._data)), None)
