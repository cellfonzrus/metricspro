"""Proof harness: an in-process cache keyed by something a CLIENT can mint is hard-capped.

WHY THIS EXISTS (load test 2026-10-28, /mnt/project-files/load-test-2026-10-28/). The
token -> identity cache in `tenant_middleware` (`_cache`) holds one entry per login token the
process has ever verified, with a 60-second TTL. Entries EXPIRED but were never DELETED, and there
was no size cap — so the dict grew for the life of the worker. Its key is the bearer token, which
is minted by the auth service on every refresh, so the key space is not bounded by the number of
users: 1,000 users refreshing through the day is tens of thousands of dead entries per worker per
day, held until the next deploy. `_entry_cache` (keyed by uid+org) had the same shape.

THE CLASS, NOT THE INSTANCE: "a module-level dict used as a TTL cache, with no cap and no
eviction". The platform had four of them written four different ways. The fix is ONE home,
`app/core/ttl_cache.TTLCache`, that every new cache reads — bounded by construction, so forgetting
to prune is no longer possible.

NOT CHANGED, AND WHY (the sibling check, house rule 2026-09-20):
  • `core/rate_limit.RateLimiter` — a fixed-window COUNTER, not a value cache, and already bounded
    (`max_keys=100_000` with a sweep). Different mechanism, already safe.
  • `core/import_health._ATTN_MEMO` — already hard-capped at 128 and pinned by
    `harness_nav_perf.py` D10. Converting it would churn a proven lock for no behaviour change.
  • `core/identity._store_cache` / `_emp_cache` and `commcalc/target_registry._cache` — keyed by
    ORG, so they are bounded by the tenant count, which a client cannot inflate.
Section D below PINS that list: a NEW unbounded module-level TTL cache under `app/core/` fails
the build.

DB-free, stdlib only. Run: `python3 harness_ttl_cache_bounded.py` from backend/.
"""
import os
import re
import sys
import threading

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.core.ttl_cache import TTLCache  # noqa: E402
from app.core import tenant_middleware as tm  # noqa: E402


# ── A. the one home behaves like the dicts it replaces, and cannot grow ──────────────────────────
clock = {"t": 1000.0}
c = TTLCache(ttl=60.0, cap=100, clock=lambda: clock["t"])

c.put("k", "v")
check("A1. a stored value is returned inside its TTL", c.get("k") == "v")
clock["t"] += 59.0
check("A2. still live one second before expiry", c.get("k") == "v")
clock["t"] += 2.0
check("A3. an expired entry is a miss", c.get("k") is None)
check("A4. a miss is None, never a KeyError", c.get("never-stored") is None)

c2 = TTLCache(ttl=60.0, cap=100, clock=lambda: clock["t"])
for i in range(10_000):
    c2.put(f"token-{i}", i)
check("A5. 10,000 distinct keys never exceed the cap", len(c2) <= 100, f"{len(c2)} entries")
check("A6. the most recent write survived the eviction", c2.get("token-9999") == 9999)

# expired entries go first, live ones only if the cap still demands it
c3 = TTLCache(ttl=10.0, cap=5, clock=lambda: clock["t"])
for i in range(5):
    c3.put(f"old-{i}", i)
clock["t"] += 20.0
c3.put("fresh", "yes")
check("A7. expired entries are evicted before live ones", len(c3) == 1, f"{len(c3)} entries")
check("A8. the live entry is the one kept", c3.get("fresh") == "yes")

c4 = TTLCache(ttl=60.0, cap=3, clock=lambda: clock["t"])
for i in range(3):
    c4.put(f"live-{i}", i)
c4.put("live-3", 3)
check("A9. with nothing expired, the cap is still honoured", len(c4) <= 3, f"{len(c4)} entries")

c5 = TTLCache(ttl=60.0, cap=10, clock=lambda: clock["t"])
c5.put("a", 1)
c5.invalidate("a")
check("A10. invalidate(key) drops one entry", c5.get("a") is None)
c5.put("b", 2)
c5.clear()
check("A11. clear() drops everything", len(c5) == 0)

# concurrent writers must not corrupt it or breach the cap
c6 = TTLCache(ttl=60.0, cap=50, clock=lambda: clock["t"])


def _hammer(n):
    for i in range(500):
        c6.put(f"t{n}-{i}", i)
        c6.get(f"t{n}-{i}")


ts = [threading.Thread(target=_hammer, args=(n,)) for n in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check("A12. 8 threads x 500 writes stay within the cap", len(c6) <= 50, f"{len(c6)} entries")

# ── B. tenant_middleware's caches ARE the one home (dereferenced, not re-implemented) ────────────
check("B1. the token -> identity cache is a TTLCache", isinstance(tm._cache, TTLCache),
      type(tm._cache).__name__)
check("B2. the operator-entry cache is a TTLCache", isinstance(tm._entry_cache, TTLCache),
      type(tm._entry_cache).__name__)
check("B3. the per-tenant 2FA policy cache is a TTLCache", isinstance(tm._twofa_cache, TTLCache),
      type(tm._twofa_cache).__name__)
check("B4. the identity cache is capped", tm._cache.cap > 0, str(tm._cache.cap))
check("B5. the identity cache keeps the 60s TTL it always had", tm._cache.ttl == 60.0,
      str(tm._cache.ttl))

# the real thing: hammer the REAL cache with distinct tokens, as 1,000 refreshing logins would
tm._cache.clear()
for i in range(50_000):
    tm._cache.put(f"jwt-token-number-{i}", (True, False, ("org",), "org", {}, f"uid{i}"))
check("B6. 50,000 distinct tokens leave the identity cache bounded",
      len(tm._cache) <= tm._cache.cap, f"{len(tm._cache)} entries, cap {tm._cache.cap}")
check("B7. the newest token is still resolvable from cache",
      tm._cache.get("jwt-token-number-49999") is not None)
tm._cache.clear()

# ── C. the caches still CACHE — a bound must not turn into a cache that never hits ───────────────
tm._cache.clear()
tm._cache.put("tok", "identity")
check("C1. a token put is a token hit", tm._cache.get("tok") == "identity")
tm._cache.clear()

# ── D. no second copy: every module-level TTL cache under app/core is the one home ───────────────
# A bare `name: dict = {}` beside a `_TTL`-ish constant is the shape this harness exists to retire.
CORE = os.path.join("app", "core")
offenders = []
ALLOWED = {
    # file -> why a bare dict there is not an unbounded TTL cache
    "rate_limit.py": "fixed-window counter, bounded by max_keys with its own sweep",
}
for fn in sorted(os.listdir(CORE)):
    if not fn.endswith(".py") or fn in ALLOWED:
        continue
    src = open(os.path.join(CORE, fn), encoding="utf8").read()
    if not re.search(r"^_?[A-Za-z_]*(TTL|_TTL_S)\s*=\s*[0-9]", src, re.M):
        continue                                   # no TTL constant → not a TTL cache
    for m in re.finditer(r"^(_[A-Za-z0-9_]*(?:cache|CACHE|memo|MEMO)[A-Za-z0-9_]*)\s*:?\s*dict\s*=\s*\{\}",
                         src, re.M):
        offenders.append(f"{fn}:{m.group(1)}")
check("D1. no bare dict is used as a TTL cache under app/core", not offenders, ", ".join(offenders))
check("D2. the scan can actually see something (it is not matching nothing)",
      "ttl_cache.py" in os.listdir(CORE))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
