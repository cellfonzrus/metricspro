"""Proof harness for the Supabase client SINGLETON (P0 perf, agent/core/perf-p0-latency).

Exercises the ACTUAL shipped app.core.database against the ACTUAL installed supabase/postgrest
stack — no network is ever touched (nothing calls .execute(); client construction is local: the
service-key path has no session to fetch). Run: `python3 harness_db_singleton.py` from backend/.

Proves (the thread-safety claims documented in app/core/database.py):
  1. get_supabase() returns ONE process-wide instance, even under a 32-thread construction race
     (double-checked lock), and get_supabase_admin() IS that same instance.
  2. .schema(name) is memoized: same object per schema, distinct objects across schemas, ONE
     underlying httpx session per schema (no per-call pool churn), stable under a thread race.
  3. Schema isolation: builders minted concurrently from many threads carry the RIGHT
     Accept-Profile/Content-Profile for the schema they asked for — never a neighbor's.
  4. Per-request header isolation: two builders from the SAME schema client own DIFFERENT Headers
     objects; mutating one (as .single()/insert-Prefer paths do) never bleeds into the other
     builder, the schema client's shared headers, or another schema's builders.
  5. The default-schema path (client.table / client.postgrest) is also cached and stays 'public'.
"""
import os
import sys
import threading

sys.path.insert(0, ".")

# Dummy creds BEFORE importing app config (pydantic reads env at import). Construction is offline.
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

from app.core import database as db  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── 1. singleton identity under a construction race ────────────────────────────────────────────
N = 32
barrier = threading.Barrier(N)
got = [None] * N


def grab(i):
    barrier.wait()               # maximize the constructor race
    got[i] = db.get_supabase()


threads = [threading.Thread(target=grab, args=(i,)) for i in range(N)]
[t.start() for t in threads]
[t.join() for t in threads]
ids = {id(c) for c in got}
check("1a. one instance across 32 racing threads", len(ids) == 1, f"got {len(ids)} distinct")
check("1b. get_supabase_admin() is the same singleton", db.get_supabase_admin() is db.get_supabase())

client = db.get_supabase()

# ── 2. per-schema memoization ──────────────────────────────────────────────────────────────────
sc1, sc2 = client.schema("commcalc"), client.schema("commcalc")
so = client.schema("storeops")
check("2a. same object per schema", sc1 is sc2)
check("2b. distinct objects across schemas", sc1 is not so)
check("2c. one httpx session per schema (reused)", sc1.session is sc2.session)
check("2d. sessions independent across schemas", sc1.session is not so.session)

# memoization stays single under a thread race for a FRESH schema name
race_out = [None] * N
barrier2 = threading.Barrier(N)


def race_schema(i):
    barrier2.wait()
    race_out[i] = client.schema("core")


threads = [threading.Thread(target=race_schema, args=(i,)) for i in range(N)]
[t.start() for t in threads]
[t.join() for t in threads]
check("2e. schema memoization race-safe", len({id(c) for c in race_out}) == 1)

# ── 3 + 4. concurrent builders: schema correctness + header isolation ──────────────────────────
SCHEMAS = ["commcalc", "storeops", "core", "coa"]
ITER = 200
errors = []
barrier3 = threading.Barrier(8)


def hammer(tid):
    barrier3.wait()
    for k in range(ITER):
        s = SCHEMAS[(tid + k) % len(SCHEMAS)]
        b = client.schema(s).table("some_table").select("*")
        req = b.request if hasattr(b, "request") else b  # RequestConfig carries per-request headers
        ap, cp = req.headers.get("accept-profile"), req.headers.get("content-profile")
        if ap != s or cp != s:
            errors.append(f"t{tid}#{k}: schema {s} got Accept-Profile={ap} Content-Profile={cp}")
            return
        # per-request mutation (what .single()/insert Prefer paths do) must stay in THIS builder
        req.headers[f"x-harness-{tid}"] = str(k)
        if f"x-harness-{tid}" in client.schema(s).headers:
            errors.append(f"t{tid}#{k}: per-request header bled into shared {s} client headers")
            return


threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
check("3a. 8 threads x 200 builders: schema headers always correct, no bleed",
      not errors, errors[0] if errors else "")

b1 = client.schema("commcalc").table("t").select("*")
b2 = client.schema("commcalc").table("t").select("*")
check("4a. two builders own different Headers objects", b1.request.headers is not b2.request.headers)
b1.request.headers["x-only-b1"] = "1"
check("4b. mutating b1 never reaches b2", "x-only-b1" not in b2.request.headers)
check("4c. mutating b1 never reaches the shared schema client",
      "x-only-b1" not in client.schema("commcalc").headers)
b3 = client.schema("commcalc").table("t").select("*")
check("4d. later builders unaffected by earlier mutations", "x-only-b1" not in b3.request.headers)

# ── 5. default-schema path ─────────────────────────────────────────────────────────────────────
check("5a. client.postgrest cached", client.postgrest is client.postgrest)
bpub = client.table("t").select("*")
check("5b. default path stays public schema", bpub.request.headers.get("accept-profile") == "public")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
