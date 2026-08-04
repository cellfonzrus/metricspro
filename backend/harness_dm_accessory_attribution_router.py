"""Router-level (I/O-wiring) proof harness for `GET /storeops/dm-accessory-attribution/{period}`
(mod-people, owner directive 2026-08-04, ledger Q7). Same fake-Supabase-chain convention as
harness_storeops_scope_wiring.py — calls the REAL router function directly (not through FastAPI/HTTP)
against a fake client, and monkeypatches ONLY `requests.get` (the internal call to mod-commission's
`/commcalc/targets/{period}/calendar?scope=rep`) so this proves the WIRING — schema-qualification,
market resolution (including a store_mapping-ONLY market, same trap class as the scope-wiring
package), DM roster resolution off `roles.permissions.scope=='market'`, shift-to-pair extraction,
and the HTTP call's own URL/params construction — without a live second process.

harness_dm_target_attribution.py (sibling) proves the pure attribution/rollup math in isolation;
this harness proves the router glue that feeds it real-shaped data.

Proves:
  1. A single-DM employee's schedule-derived target reaches that DM's total (a).
  2. A 2-DM employee (worked stores in 2 different DMs' markets this period) splits per-store with
     no double-count / no drop, INCLUDING when one of the two stores' market is known only to
     commcalc.store_mapping (the exact trap the scope-wiring package fixed) (b).
  3. `achieved` on every row is the untouched value the fake calendar endpoint returned — never
     recomputed here (c).
  4. `dm_id` narrows the response to one DM without dropping the shared unassigned/ambiguous/totals.
  5. A 'self'-scope (plain rep) caller is refused (403-equivalent HTTPException).
  6. A calendar-call failure for one row degrades to a $0, flagged row — not a 500 for the endpoint.

Run: `cd backend && python3 harness_dm_accessory_attribution_router.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


# ── stateful fake supabase client (same convention as the sibling scope-wiring harness) ────────────
class Q:
    def __init__(self, store, key):
        self.s, self.k = store, key
        self.op = "select"
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.k, [])
        matched = [dict(r) for r in rows if self._match(r)]
        if self._limit is not None:
            matched = matched[: self._limit]
        return SimpleNamespace(data=matched)


class FakeSchema:
    def __init__(self, client, name): self.client, self.name = client, name
    def table(self, t): return Q(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return Q(self.store, ("storeops", t))


import app.modules.storeops.router as SO              # noqa: E402
import app.core.scope as CS                            # noqa: E402
import app.modules.core.router as CORE                 # noqa: E402

TOKENS = {}


def app_user(auth_name, org_id, role, *, market=None, employee_id=None):
    uid = f"uid-{auth_name}"
    TOKENS[f"Bearer {auth_name}"] = uid
    return {"id": f"au-{auth_name}", "org_id": org_id, "auth_id": uid, "role": role,
            "employee_id": employee_id, "market": market, "full_name": None, "email": f"{auth_name}@x.com"}


store = {
    ("storeops", "app_config"): [{"id": 1, "rbac_enabled": True}],
    ("storeops", "stores"): [
        {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Fresno"},
        {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Bakersfield"},
        # S3's market is known ONLY to commcalc.store_mapping — the exact trap the scope-wiring
        # package fixed; this DM-attribution endpoint must resolve it the same way.
        {"org_id": HOUSE, "store_code": "S3", "address": "3 Reno Rd", "market": None},
    ],
    ("commcalc", "store_mapping"): [
        {"org_id": HOUSE, "store_code": "S3", "store_address": "3 Reno Rd", "market": "Reno"},
    ],
    ("storeops", "roles"): [
        {"org_id": HOUSE, "name": "District Manager", "permissions": {"scope": "market"}},
        {"org_id": HOUSE, "name": "Sales Rep", "permissions": {"scope": "self"}},
    ],
    ("storeops", "app_users"): [
        app_user("dmA", HOUSE, "District Manager", market="Fresno"),
        app_user("dmB", HOUSE, "District Manager", market="Bakersfield"),
        app_user("dmC", HOUSE, "District Manager", market="Reno"),
        app_user("rep1", HOUSE, "Sales Rep", employee_id="E-ROAMER"),
    ],
    ("storeops", "employees"): [
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "name": "Roamer Rep", "home_store": "S1"},
        {"org_id": HOUSE, "employee_id": "E-HOME", "name": "Home Rep", "home_store": "S1"},
    ],
    ("storeops", "shifts"): [
        # Roamer Rep worked BOTH S1 (Fresno -> dmA) and S3 (Reno -> dmC) this period -> cross-DM.
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "employee_name": "Roamer Rep", "store_code": "S1",
         "scheduled_hours": 20, "shift_date": "2026-08-05", "is_deleted": False},
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "employee_name": "Roamer Rep", "store_code": "S3",
         "scheduled_hours": 10, "shift_date": "2026-08-12", "is_deleted": False},
        # Home Rep worked only S1 (Fresno -> dmA).
        {"org_id": HOUSE, "employee_id": "E-HOME", "employee_name": "Home Rep", "store_code": "S1",
         "scheduled_hours": 30, "shift_date": "2026-08-06", "is_deleted": False},
        # deleted shift at S2 must NOT produce a row (would otherwise land under dmB, unassigned check).
        {"org_id": HOUSE, "employee_id": "E-HOME", "employee_name": "Home Rep", "store_code": "S2",
         "scheduled_hours": 8, "shift_date": "2026-08-07", "is_deleted": True},
    ],
}


def wire():
    fake = FakeClient(store)
    SO.get_supabase = lambda: fake
    CORE._uid_from_token = lambda tok: TOKENS.get(tok)
    CS.invalidate_market_index()
    return fake


wire()

# ── canned calendar responses, keyed by (store_code, REP upper) — the exact call this endpoint makes ──
CANNED = {
    ("S1", "ROAMER REP"): {"monthly_targets": {"accessories": 300.0},
                           "categories": {"accessories": {"achieved_mtd": 120.0}}, "rep_share": 0.4},
    ("S3", "ROAMER REP"): {"monthly_targets": {"accessories": 150.0},
                           "categories": {"accessories": {"achieved_mtd": 60.0}}, "rep_share": 0.6},
    ("S1", "HOME REP"): {"monthly_targets": {"accessories": 700.0},
                         "categories": {"accessories": {"achieved_mtd": 250.0}}, "rep_share": 0.6},
}
CALLS = []


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


def fake_get(url, params=None, timeout=None):
    CALLS.append((url, dict(params or {})))
    key = (params.get("store_code"), str(params.get("rep") or "").upper())
    if key == ("FAIL", "FAILREP"):
        return _FakeResp({}, status=500)
    payload = CANNED.get(key)
    if payload is None:
        return _FakeResp({"monthly_targets": {"accessories": 0.0},
                          "categories": {"accessories": {"achieved_mtd": 0.0}}, "rep_share": 0.0})
    return _FakeResp(payload)


SO.requests = SimpleNamespace(get=fake_get, post=SO.requests.post)

# ══════════════════════ 1/2/3: full endpoint call, admin caller ═══════════════════════════════════
resp = SO.dm_accessory_attribution("August 2026", authorization="", dm_id="", org_id=HOUSE)

by_dm = resp["by_dm"]
dmA_label = None
for key, d in by_dm.items():
    if "Fresno" in d.get("markets", []):
        dmA_label = key
dmC_key = next(k for k, d in by_dm.items() if "Reno" in d.get("markets", []))
dmB_key = next(k for k, d in by_dm.items() if "Bakersfield" in d.get("markets", []))

check("1a. dmA (Fresno) total = Roamer's S1 share (300) + Home Rep's S1 share (700) = 1000",
      by_dm[dmA_label]["total_target"] == 1000.0, by_dm[dmA_label])
check("1b. dmC (Reno, store_mapping-ONLY market) total = Roamer's S3 share (150) — proves the "
      "store_mapping-only market resolved (the scope-wiring trap class)",
      by_dm[dmC_key]["total_target"] == 150.0, by_dm[dmC_key])
check("2a. dmB (Bakersfield) has ZERO rows — no shift ever landed there (Home Rep's S2 shift was deleted)",
      by_dm[dmB_key]["total_target"] == 0.0 and by_dm[dmB_key]["rows"] == [], by_dm[dmB_key])
check("2b. NO double-count: Σ(by_dm) == total_target_all_rows (1150) — Fresno+Reno markets are unambiguous",
      round(sum(d["total_target"] for d in by_dm.values()), 2) == resp["total_target_all_rows"] == 1150.0,
      resp)
check("2c. NO dropped row: exactly 3 (employee,store) pairs considered, 3 rows total across all DMs",
      resp["pairs_considered"] == 3 and sum(len(d["rows"]) for d in by_dm.values()) == 3, resp)
check("2d. Roamer Rep is the cross_dm_employees flag (worked under 2 different DMs' markets)",
      [e["employee_name"] for e in resp["cross_dm_employees"]] == ["Roamer Rep"], resp["cross_dm_employees"])

roamer_row_s1 = next(r for r in by_dm[dmA_label]["rows"] if r["employee_name"] == "Roamer Rep")
check("3a. achieved on the Fresno row is the UNTOUCHED fake-endpoint value (120.0), never recomputed",
      roamer_row_s1["achieved"] == 120.0, roamer_row_s1)
roamer_row_s3 = by_dm[dmC_key]["rows"][0]
check("3b. achieved on the Reno row is the UNTOUCHED fake-endpoint value (60.0)",
      roamer_row_s3["achieved"] == 60.0, roamer_row_s3)
check("3c. total_achieved_all_rows sums the untouched achieved values (120+60+250=430)",
      resp["total_achieved_all_rows"] == 430.0, resp)

# the internal call itself hit the RIGHT commcalc path, unauthenticated (server-to-server, matches
# the existing PTO system-line push convention) — proves the URL/param wiring, not just the math.
check("call-shape: internal calendar call targets /commcalc/targets/.../calendar with scope=rep",
      all("/commcalc/targets/" in u and u.endswith("/calendar") and p.get("scope") == "rep"
          for u, p in CALLS), CALLS[:2])

# ══════════════════════ 4: dm_id narrows the response ══════════════════════════════════════════════
resp_one = SO.dm_accessory_attribution("August 2026", authorization="", dm_id=dmA_label, org_id=HOUSE)
check("4a. dm_id narrows by_dm to just that one DM",
      set(resp_one["by_dm"].keys()) == {dmA_label}, resp_one["by_dm"].keys())
check("4b. shared totals/cross_dm_employees still present when narrowed by dm_id",
      resp_one["total_target_all_rows"] == 1150.0 and len(resp_one["cross_dm_employees"]) == 1, resp_one)

# ══════════════════════ 5: a self-scope rep is refused ═════════════════════════════════════════════
try:
    SO.dm_accessory_attribution("August 2026", authorization="Bearer rep1", dm_id="", org_id=HOUSE)
    check("5a. self-scope caller raises HTTPException", False, "did not raise")
except Exception as e:
    check("5a. self-scope caller raises HTTPException",
          type(e).__name__ == "HTTPException" and getattr(e, "status_code", None) == 403, repr(e))

# ══════════════════════ 6: a calendar-call failure degrades to $0 + warning, never a 500 ═══════════
store[("storeops", "shifts")].append(
    {"org_id": HOUSE, "employee_id": "E-FAIL", "employee_name": "FailRep", "store_code": "FAIL",
     "scheduled_hours": 5, "shift_date": "2026-08-15", "is_deleted": False})
store[("storeops", "stores")].append(
    {"org_id": HOUSE, "store_code": "FAIL", "address": "9 Bad St", "market": "Fresno"})
resp2 = SO.dm_accessory_attribution("August 2026", authorization="", dm_id="", org_id=HOUSE)
check("6a. a per-row calendar failure never raises — endpoint still returns cleanly",
      isinstance(resp2, dict), resp2)
check("6b. the failing row is flagged in `warnings`, not silently dropped",
      any(w["employee_name"] == "FailRep" for w in resp2["warnings"]), resp2["warnings"])
check("6c. the failing row still counts as $0 (not omitted from pairs_considered)",
      resp2["pairs_considered"] == 4, resp2)


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
