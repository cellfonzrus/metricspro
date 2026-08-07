"""Offline proof (no live DB/network) for the 2026-08-06 owner report (mod-people, branch
agent/people/storeops-inactive-store-filter): "t-902 / 531 etc all t-stores have been disabled but
they still show in time clock, targets etc reports - check and remove".

ROOT CAUSE (confirmed live via the read-only prod probe, mp.py, GET only): storeops.stores.is_active
was correctly false for the 6 disabled T-stores (T-531, T-7812, T-902, T-957, T21880, T3560 — all
market TT), but GET /stores (feeds pickers/dropdowns app-wide) and GET /timeclock/stores (the kiosk
clock-in picker) never filtered the flag at all — confirmed live: /timeclock/stores returned all 6.

THE TRAP (explicit ask): storeops.stores.is_active is a NULLABLE column (`DEFAULT true`, no NOT NULL).
A blanket `.eq("is_active", True)` would silently drop any store whose flag was never explicitly set —
worse than the bug being fixed. Live house-org data: 26 stores, 20 explicitly is_active=true, 6
explicitly is_active=false, ZERO with a null flag today — but the fix must be correct regardless of
that being true only by luck. Sections A/B/C below synthesize the NULL-flag case explicitly (live data
alone can't prove the trap is avoided, since prod currently has no example of it).

Covers:
  A. _store_is_active / _active_stores_only (pure, no DB) — True/False/None/missing-key, matching the
     established `_inactive_ids_from` (employee-side) convention exactly.
  B. GET /stores end-to-end (FakeClient) — default active-only (NULL flag NOT dropped), include_inactive
     opt-in, RBAC keyset narrowing composes correctly with the active filter (a scoped manager's span
     does not resurrect an inactive store), and multi-tenant org isolation as a NON-house org.
  C. GET /timeclock/stores end-to-end — always active-only (no include_inactive escape hatch), NULL
     flag not dropped, response shape unchanged (store_code/address/market only — is_active never
     leaks through despite being fetched to filter).
  D. google_reviews.sweep_org — hardened NULL-safe active filter (was a blanket `.eq(is_active, True)`
     query filter, same trap class) — a NULL-flagged store is still swept, an explicitly-false one is
     not.

Run: `python3 harness_inactive_store_filter.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — pure _store_is_active / _active_stores_only
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import app.modules.storeops.router as R  # noqa: E402

check("A1 is_active=True -> active", R._store_is_active({"is_active": True}) is True)
check("A2 is_active=False -> NOT active", R._store_is_active({"is_active": False}) is False)
check("A3 is_active=None (explicit null) -> active (THE TRAP — NULL must read as active)",
      R._store_is_active({"is_active": None}) is True)
check("A4 is_active key missing entirely -> active", R._store_is_active({"store_code": "S1"}) is True)

mixed = [
    {"store_code": "ACTIVE1", "is_active": True},
    {"store_code": "INACTIVE1", "is_active": False},
    {"store_code": "NULLFLAG1", "is_active": None},
    {"store_code": "MISSINGFLAG1"},
]
kept = R._active_stores_only(mixed)
kept_codes = {s["store_code"] for s in kept}
check("A5 _active_stores_only keeps True/None/missing, drops only explicit False",
      kept_codes == {"ACTIVE1", "NULLFLAG1", "MISSINGFLAG1"}, kept_codes)
check("A6 _active_stores_only on an empty/None input never crashes", R._active_stores_only(None) == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B / C — router-level wiring against an in-memory fake Supabase client
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._order = None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v))
        return self

    def order(self, k, desc=False):
        self._order = (k, desc)
        return self

    def limit(self, *_a, **_k):
        return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            if kind == "eq" and str(row.get(k)) != str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        matched = [r for r in rows if self._matches(r)]
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t))

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()
R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")
R.caller_scope = lambda authorization="", org_id=R.ORG_ID: None   # default: unrestricted (no RBAC span)

ORG = "org-nonhouse-1"   # RULE ONE: verify as a NON-house tenant, not just 0000...0001
OTHER_ORG = "org-other-2"

STORES_FIXTURE = [
    {"org_id": ORG, "id": 1, "store_code": "B-100", "address": "100 Main St", "market": "NJ", "is_active": True},
    {"org_id": ORG, "id": 2, "store_code": "T-902", "address": None, "market": "TT", "is_active": False},
    {"org_id": ORG, "id": 3, "store_code": "T-957", "address": None, "market": "TT", "is_active": False},
    # THE TRAP CASE — synthesized deliberately (live prod currently has zero examples of this):
    {"org_id": ORG, "id": 4, "store_code": "LEGACY-NULLFLAG", "address": None, "market": "NJ", "is_active": None},
    {"org_id": ORG, "id": 5, "store_code": "LEGACY-NOFLAGKEY", "address": None, "market": "NJ"},
]


def reset():
    fake.store.clear()
    fake.seed("storeops", "stores", STORES_FIXTURE + [
        {"org_id": OTHER_ORG, "id": 99, "store_code": "GHOST", "address": None, "market": "ZZ", "is_active": True},
    ])
    R.caller_scope = lambda authorization="", org_id=R.ORG_ID: None


AUTH = "Bearer x"

# ── B1: default GET /stores is active-only, and the NULL-flag / missing-flag rows are NOT dropped ──
reset()
rows = R.get_stores(authorization=AUTH, org_id=ORG)
codes = {r["store_code"] for r in rows}
check("B1 default GET /stores returns exactly the active set (3 of 5): True + NULL-flag + missing-flag, "
      "NEVER the 2 explicitly-false ones",
      codes == {"B-100", "LEGACY-NULLFLAG", "LEGACY-NOFLAGKEY"}, codes)
check("B1b the disabled T-stores are ABSENT from the default picker (the reported bug, fixed)",
      "T-902" not in codes and "T-957" not in codes, codes)

# ── B2: include_inactive=true returns everyone (unchanged blast radius for admin/config callers) ──
rows2 = R.get_stores(include_inactive=True, authorization=AUTH, org_id=ORG)
codes2 = {r["store_code"] for r in rows2}
check("B2 include_inactive=true returns ALL 5 stores for this org, including the disabled T-stores",
      codes2 == {"B-100", "T-902", "T-957", "LEGACY-NULLFLAG", "LEGACY-NOFLAGKEY"}, codes2)

# ── B3: multi-tenant isolation — the other org's store never appears, for EITHER mode ──────────────
check("B3 org isolation (default mode): another org's store never leaks in", "GHOST" not in codes, codes)
check("B3b org isolation (include_inactive=true): still never leaks another org's store", "GHOST" not in codes2, codes2)
rows_other = R.get_stores(include_inactive=True, authorization=AUTH, org_id=OTHER_ORG)
check("B3c the OTHER org, queried directly, sees only ITS OWN store",
      {r["store_code"] for r in rows_other} == {"GHOST"}, rows_other)

# ── B4: RBAC keyset composes correctly with the active filter — a scoped manager's span does not
# resurrect a disabled store, and does correctly narrow out an active store outside their span ──────
R.caller_scope = lambda authorization="", org_id=R.ORG_ID: {"B-100", "T-902"}   # manager's span
rows4 = R.get_stores(authorization=AUTH, org_id=ORG)
codes4 = {r["store_code"] for r in rows4}
check("B4 scoped manager, default mode: sees their in-span ACTIVE store, NOT their in-span but "
      "DISABLED store (T-902 is in-span yet still excluded — active-filter still applies)",
      codes4 == {"B-100"}, codes4)
rows4b = R.get_stores(include_inactive=True, authorization=AUTH, org_id=ORG)
codes4b = {r["store_code"] for r in rows4b}
check("B4b scoped manager, include_inactive=true: NOW sees their in-span disabled store too (T-902), "
      "still never an out-of-span store (T-957/LEGACY-* excluded — RBAC unaffected by the active fix)",
      codes4b == {"B-100", "T-902"}, codes4b)
R.caller_scope = lambda authorization="", org_id=R.ORG_ID: None   # restore unrestricted

# ── C: GET /timeclock/stores — always active-only, NULL-safe, response shape unchanged ─────────────
reset()
tc_rows = R.timeclock_stores(org_id=ORG)
tc_codes = {r["store_code"] for r in tc_rows}
check("C1 kiosk picker: active-only by default with NO include_inactive param at all",
      tc_codes == {"B-100", "LEGACY-NULLFLAG", "LEGACY-NOFLAGKEY"}, tc_codes)
check("C1b the disabled T-stores are absent from the kiosk clock-in picker (the exact live-confirmed bug)",
      "T-902" not in tc_codes and "T-957" not in tc_codes, tc_codes)
check("C2 NULL-flag / missing-flag stores are NOT dropped from the kiosk picker either",
      "LEGACY-NULLFLAG" in tc_codes and "LEGACY-NOFLAGKEY" in tc_codes, tc_codes)
check("C3 response shape unchanged — exactly {store_code, address, market}, is_active never leaks through",
      all(set(r.keys()) == {"store_code", "address", "market"} for r in tc_rows), tc_rows)

# ── D: google_reviews.sweep_org — NULL-safe active filter (hardened from a blanket .eq query filter) ──
import app.modules.storeops.google_reviews as GR  # noqa: E402

fake.seed("storeops", "google_review_config", [{"org_id": ORG, "enabled": True, "api_key": "test-key"}])
swept_codes = []


def _fake_sweep_store(client, org_id, store_row, cfg):
    swept_codes.append(store_row.get("store_code"))
    return {"store_code": store_row.get("store_code"), "status": "ok"}


GR.sweep_store = _fake_sweep_store
result = GR.sweep_org(fake, ORG)
check("D1 sweep_org is not skipped when enabled + api_key are set", result.get("skipped") is False, result)
check("D2 sweep_org sweeps the active + NULL-flag + missing-flag stores, but NEVER the 2 disabled T-stores",
      set(swept_codes) == {"B-100", "LEGACY-NULLFLAG", "LEGACY-NOFLAGKEY"}, swept_codes)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
