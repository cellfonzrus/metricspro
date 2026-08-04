"""Router-level (I/O-wiring) proof harness for `GET /storeops/dm-accessory-attribution/{period}`
(mod-people, owner directive 2026-08-04, ledger Q7; Gate-1 rework 2026-08-04: span-scoping +
bulk-fetch performance path). Same fake-Supabase-chain convention as
harness_storeops_scope_wiring.py — calls the REAL router function directly (not through FastAPI/HTTP)
against a fake client, and monkeypatches ONLY `requests.get` (the ONE internal call to
mod-commission's `GET /commcalc/targets/{period}/summary`) so this proves the WIRING —
schema-qualification, market resolution (including a store_mapping-ONLY market, same trap class as
the scope-wiring package), DM roster resolution off `roles.permissions.scope=='market'`,
shift-to-pair extraction, the LOCAL rep-share proration off `storeops.shifts`, the bulk HTTP call's
own URL/params construction, and the market-scope redaction — without a live second process.

harness_dm_target_attribution.py (sibling) proves the pure attribution/rollup/proration/redaction
math in isolation; this harness proves the router glue that feeds it real-shaped data.

The period used throughout ('January 2020') is deliberately in the PAST relative to any real
wall-clock "today" this harness could run on, so `project_future_hours`'s `today > month_end` early-
return keeps every hours computation fully deterministic regardless of when the suite executes —
see the router's own `today = date.today()` call inside `_dm_target_rows`.

Proves:
  1. A single-DM employee's schedule-derived target reaches that DM's total (a), via the bulk
     `/summary` payload's store-level $ x a LOCALLY-computed hours share.
  2. A 2-DM employee (worked stores in 2 different DMs' markets this period) splits per-store with
     no double-count / no drop, INCLUDING when one of the two stores' market is known only to
     commcalc.store_mapping (the exact trap the scope-wiring package fixed) (b).
  3. `achieved` on every row is the untouched value the bulk fake endpoint returned — never
     recomputed here (c).
  4. `dm_id` narrows the response to one DM without dropping the shared unassigned/ambiguous/totals.
  5. A 'self'-scope AND a 'store'-scope caller are both refused (403-equivalent HTTPException).
  6. A total bulk-fetch failure degrades every row to $0 with ONE endpoint-level warning — never a
     500, and never a per-row warning flood.
  7. SPAN-SCOPING (Gate-1 rework): a market-scope DM sees ONLY their own market's card — dmA
     (Fresno) never sees dmC's (Reno) total, roster, or rows; the org-wide grand totals a market-scope
     caller sees are recomputed over ONLY their visible card(s); the cross-DM employee they share with
     another DM still appears (to explain the split) but the OTHER dm is reduced to a bare label.
  8. BULK-FETCH PERFORMANCE (owner directive 2026-08-04, "plan for a bigger tenant"): a synthetic
     60-employee x 15-store roster (60 worked pairs) is served by EXACTLY ONE upstream `/summary`
     call — not one per pair — with full coverage (no truncation) and a hand-verifiable aggregate.

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
PERIOD = "January 2020"   # safely in the past — see module docstring


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
        {"org_id": HOUSE, "store_code": "FAIL", "address": "9 Bad St", "market": "Fresno"},
    ],
    ("commcalc", "store_mapping"): [
        {"org_id": HOUSE, "store_code": "S3", "store_address": "3 Reno Rd", "market": "Reno"},
    ],
    ("storeops", "roles"): [
        {"org_id": HOUSE, "name": "District Manager", "permissions": {"scope": "market"}},
        {"org_id": HOUSE, "name": "Store Manager", "permissions": {"scope": "store"}},
        {"org_id": HOUSE, "name": "Sales Rep", "permissions": {"scope": "self"}},
    ],
    ("storeops", "app_users"): [
        app_user("dmA", HOUSE, "District Manager", market="Fresno"),
        app_user("dmB", HOUSE, "District Manager", market="Bakersfield"),
        app_user("dmC", HOUSE, "District Manager", market="Reno"),
        app_user("rep1", HOUSE, "Sales Rep", employee_id="E-ROAMER"),
        app_user("storemgr1", HOUSE, "Store Manager"),
    ],
    ("storeops", "employees"): [
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "name": "Roamer Rep", "home_store": "S1"},
        {"org_id": HOUSE, "employee_id": "E-HOME", "name": "Home Rep", "home_store": "S1"},
    ],
    ("storeops", "shifts"): [
        # Roamer Rep + Home Rep BOTH work S1 the same day (20h + 30h = 50h store total) -> a genuine
        # FRACTIONAL share (0.4 / 0.6), no weekday projection involved (single concrete day).
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "employee_name": "Roamer Rep", "store_code": "S1",
         "scheduled_hours": 20, "shift_date": "2020-01-15", "is_deleted": False},
        {"org_id": HOUSE, "employee_id": "E-HOME", "employee_name": "Home Rep", "store_code": "S1",
         "scheduled_hours": 30, "shift_date": "2020-01-15", "is_deleted": False},
        # Roamer Rep ALSO works S3 (Reno -> dmC) — the cross-DM case. Sole worker there -> share 1.0.
        {"org_id": HOUSE, "employee_id": "E-ROAMER", "employee_name": "Roamer Rep", "store_code": "S3",
         "scheduled_hours": 10, "shift_date": "2020-01-16", "is_deleted": False},
        # deleted shift at S2 must NOT produce a row (would otherwise land under dmB).
        {"org_id": HOUSE, "employee_id": "E-HOME", "employee_name": "Home Rep", "store_code": "S2",
         "scheduled_hours": 8, "shift_date": "2020-01-07", "is_deleted": True},
        # FailRep works "FAIL" (market=Fresno, resolves fine — the failure is that the bulk /summary
        # payload has NO ENTRY for this store, not a market-resolution gap) -> still routes to dmA
        # (market match succeeds), just priced at $0 (no store-target to multiply against).
        {"org_id": HOUSE, "employee_id": "E-FAIL", "employee_name": "FailRep", "store_code": "FAIL",
         "scheduled_hours": 5, "shift_date": "2020-01-18", "is_deleted": False},
    ],
}


def wire():
    fake = FakeClient(store)
    SO.get_supabase = lambda: fake
    CORE._uid_from_token = lambda tok: TOKENS.get(tok)
    CS.invalidate_market_index()
    SO._dm_target_cache.clear()
    return fake


wire()

# ── canned bulk `/summary` payload — swappable per test section ────────────────────────────────────
SUMMARY_PAYLOAD = {
    "stores": [
        {"store_code": "S1", "categories": {"accessories": {"monthly": 1000.0}},
         "reps": [{"rep": "Roamer Rep", "accessories": 120.0}, {"rep": "Home Rep", "accessories": 250.0}]},
        {"store_code": "S3", "categories": {"accessories": {"monthly": 150.0}},
         "reps": [{"rep": "Roamer Rep", "accessories": 60.0}]},
        # S2 included but IRRELEVANT (no shift ever lands there — Home Rep's S2 shift is deleted).
        {"store_code": "S2", "categories": {"accessories": {"monthly": 999.0}}, "reps": []},
        # NOTE: "FAIL" store deliberately NOT present in the summary -> its worked pair still gets a
        # ROW (never dropped), just priced at $0 (no store-target entry to multiply against) — this
        # is NOT a "failure" under the new bulk design (see test 6 for an ACTUAL bulk-call failure).
    ],
}
CALLS = []
FAIL_SUMMARY_CALL = {"on": False}


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
    if url.endswith("/summary"):
        if FAIL_SUMMARY_CALL["on"]:
            return _FakeResp({}, status=500)
        return _FakeResp(SUMMARY_PAYLOAD)
    return _FakeResp({}, status=404)   # anything else this package might accidentally call


SO.requests = SimpleNamespace(get=fake_get, post=SO.requests.post)

# ══════════════════════ 1/2/3: full endpoint call, admin caller (RBAC scope 'all') ═════════════════
CALLS.clear()
resp = SO.dm_accessory_attribution(PERIOD, authorization="", dm_id="", org_id=HOUSE)

check("call-shape: EXACTLY ONE upstream call for the whole rollup (the bulk-fetch point)",
      len(CALLS) == 1, CALLS)
check("call-shape: it targets /commcalc/targets/.../summary, not a per-pair /calendar call",
      CALLS[0][0].endswith("/summary") and "/commcalc/targets/" in CALLS[0][0], CALLS)

by_dm = resp["by_dm"]
dmA_key = next(k for k, d in by_dm.items() if "Fresno" in d.get("markets", []))
dmC_key = next(k for k, d in by_dm.items() if "Reno" in d.get("markets", []))
dmB_key = next(k for k, d in by_dm.items() if "Bakersfield" in d.get("markets", []))

check("1a. dmA (Fresno) total = Roamer's S1 share (1000*0.4=400) + Home Rep's S1 share (1000*0.6=600) = 1000",
      by_dm[dmA_key]["total_target"] == 1000.0, by_dm[dmA_key])
check("1b. dmC (Reno, store_mapping-ONLY market) total = Roamer's S3 share (150*1.0=150) — proves the "
      "store_mapping-only market resolved (the scope-wiring trap class)",
      by_dm[dmC_key]["total_target"] == 150.0, by_dm[dmC_key])
check("2a. dmB (Bakersfield) has ZERO rows — no shift ever landed there (Home Rep's S2 shift was deleted)",
      by_dm[dmB_key]["total_target"] == 0.0 and by_dm[dmB_key]["rows"] == [], by_dm[dmB_key])
check("2b. NO double-count: Σ(by_dm) == total_target_all_rows (1150) — Fresno+Reno markets are unambiguous",
      round(sum(d["total_target"] for d in by_dm.values()), 2) == resp["total_target_all_rows"] == 1150.0,
      resp)
check("2c. NO dropped row: 4 (employee,store) pairs considered (incl. the FAIL-store $0 pair), 4 rows "
      "total across all DMs + unassigned",
      resp["pairs_considered"] == 4
      and sum(len(d["rows"]) for d in by_dm.values()) + len(resp["unassigned"]["rows"]) == 4, resp)
check("2c'. the FAIL-store pair (market resolves fine to dmA — the bulk summary just has no ENTRY for "
      "that store) still ROUTES to dmA at $0, never silently vanishes and never falsely 'unassigned'",
      any(r["store_code"] == "FAIL" and r["target"] == 0.0 for r in by_dm[dmA_key]["rows"])
      and resp["unassigned"]["rows"] == [], by_dm[dmA_key]["rows"])
check("2d. Roamer Rep is the cross_dm_employees flag (worked under 2 different DMs' markets)",
      [e["employee_name"] for e in resp["cross_dm_employees"]] == ["Roamer Rep"], resp["cross_dm_employees"])

roamer_row_s1 = next(r for r in by_dm[dmA_key]["rows"] if r["employee_name"] == "Roamer Rep")
check("3a. achieved on the Fresno row is the UNTOUCHED bulk-summary value (120.0), never recomputed",
      roamer_row_s1["achieved"] == 120.0, roamer_row_s1)
roamer_row_s3 = by_dm[dmC_key]["rows"][0]
check("3b. achieved on the Reno row is the UNTOUCHED bulk-summary value (60.0)",
      roamer_row_s3["achieved"] == 60.0, roamer_row_s3)
check("3c. total_achieved_all_rows sums the untouched achieved values (120+60+250=430)",
      resp["total_achieved_all_rows"] == 430.0, resp)
check("3d. target on the Fresno rows reflects the LOCALLY-computed hours share (400.0 / 600.0), "
      "matching the pure sibling harness's proration formula",
      roamer_row_s1["target"] == 400.0
      and next(r for r in by_dm[dmA_key]["rows"] if r["employee_name"] == "Home Rep")["target"] == 600.0,
      by_dm[dmA_key]["rows"])

check("caller_scope field: unrestricted caller reports 'all'", resp["caller_scope"] == "all", resp)

# ══════════════════════ 4: dm_id narrows the response ══════════════════════════════════════════════
resp_one = SO.dm_accessory_attribution(PERIOD, authorization="", dm_id=dmA_key, org_id=HOUSE)
check("4a. dm_id narrows by_dm to just that one DM",
      set(resp_one["by_dm"].keys()) == {dmA_key}, resp_one["by_dm"].keys())
check("4b. shared totals/cross_dm_employees still present when narrowed by dm_id",
      resp_one["total_target_all_rows"] == 1150.0 and len(resp_one["cross_dm_employees"]) == 1, resp_one)

# ══════════════════════ 5: self-scope AND store-scope callers are both refused ══════════════════════
for tok, label in (("Bearer rep1", "self-scope"), ("Bearer storemgr1", "store-scope")):
    try:
        SO.dm_accessory_attribution(PERIOD, authorization=tok, dm_id="", org_id=HOUSE)
        check(f"5. {label} caller raises HTTPException(403)", False, "did not raise")
    except Exception as e:
        check(f"5. {label} caller raises HTTPException(403)",
              type(e).__name__ == "HTTPException" and getattr(e, "status_code", None) == 403, repr(e))

# ══════════════════════ 6: a TOTAL bulk-fetch failure degrades everything to $0 + ONE warning ══════
FAIL_SUMMARY_CALL["on"] = True
SO._dm_target_cache.clear()
resp_fail = SO.dm_accessory_attribution(PERIOD, authorization="", dm_id="", org_id=HOUSE)
FAIL_SUMMARY_CALL["on"] = False
SO._dm_target_cache.clear()
check("6a. a total bulk-fetch failure never raises — endpoint still returns cleanly",
      isinstance(resp_fail, dict), resp_fail)
check("6b. EVERY row is $0 when the bulk fetch failed (nothing partially-priced off a dead source)",
      all(d["total_target"] == 0.0 for d in resp_fail["by_dm"].values())
      and resp_fail["total_target_all_rows"] == 0.0, resp_fail)
check("6c. EXACTLY ONE endpoint-level warning is raised — not a per-row flood",
      len(resp_fail["warnings"]) == 1, resp_fail["warnings"])
check("6d. pairs are still fully accounted for (never dropped just because pricing failed)",
      resp_fail["pairs_considered"] == 4
      and sum(len(d["rows"]) for d in resp_fail["by_dm"].values()) + len(resp_fail["unassigned"]["rows"]) == 4,
      resp_fail)

# ══════════════════════ 7: SPAN-SCOPING (Gate-1 rework) — market-scope DM narrowing ═════════════════
resp_dmA = SO.dm_accessory_attribution(PERIOD, authorization="Bearer dmA", dm_id="", org_id=HOUSE)
check("7a. caller_scope field reports 'market' for a District Manager caller", resp_dmA["caller_scope"] == "market", resp_dmA)
check("7b. a market-scope DM (dmA/Fresno) sees ONLY their own card — dmB/dmC absent entirely",
      set(resp_dmA["by_dm"].keys()) == {dmA_key}, resp_dmA["by_dm"].keys())
check("7c. their OWN total is unchanged from the admin view (1000.0)",
      resp_dmA["by_dm"][dmA_key]["total_target"] == 1000.0, resp_dmA["by_dm"][dmA_key])
check("7d. grand totals are recomputed over ONLY the visible card(s) — NOT the org-wide 1150",
      resp_dmA["total_target_all_rows"] == 1000.0 and resp_dmA["total_achieved_all_rows"] == 370.0,
      resp_dmA)
check("7e. the FAIL-store $0 row is still in dmA's OWN visible rows (their market resolved fine — "
      "only the summary pricing was missing, and that's still THEIR data to see)",
      any(r["store_code"] == "FAIL" for r in resp_dmA["by_dm"][dmA_key]["rows"]),
      resp_dmA["by_dm"][dmA_key]["rows"])

# cross-DM: Roamer Rep is shared with dmC (Reno) — dmA must see the split exists but NOT dmC's numbers.
cdm = resp_dmA["cross_dm_employees"]
check("7f. dmA still sees Roamer Rep in the cross-DM list (their own market's slice of the split)",
      [e["employee_name"] for e in cdm] == ["Roamer Rep"], cdm)
roamer_entry = cdm[0]
own_dm_entry = next(d for d in roamer_entry["dms"] if d["dm_key"] == dmA_key)
other_dm_entry = next(d for d in roamer_entry["dms"] if d["dm_key"] == dmC_key)
check("7g. dmA's OWN slice of the split keeps FULL detail (rows + total_target = 400)",
      "rows" in own_dm_entry and own_dm_entry["total_target"] == 400.0 and not own_dm_entry.get("redacted"),
      own_dm_entry)
check("7h. dmC's slice is REDACTED to a bare label — no rows, no total_target (never leaks dmC's numbers)",
      other_dm_entry.get("redacted") is True and "rows" not in other_dm_entry
      and "total_target" not in other_dm_entry, other_dm_entry)

# a market-scope caller trying to dm_id their way into ANOTHER dm's card gets nothing, not a leak.
resp_dmA_reach = SO.dm_accessory_attribution(PERIOD, authorization="Bearer dmA", dm_id=dmC_key, org_id=HOUSE)
check("7i. dm_id can't be used to escape a market-scope caller's own visible set",
      resp_dmA_reach["by_dm"] == {}, resp_dmA_reach["by_dm"])

# a market-scope caller with a total bulk-fetch failure still gets the ONE system-wide warning (it's
# not attributable to any one store, so it's not filtered out by the market narrowing).
FAIL_SUMMARY_CALL["on"] = True
SO._dm_target_cache.clear()
resp_dmA_fail = SO.dm_accessory_attribution(PERIOD, authorization="Bearer dmA", dm_id="", org_id=HOUSE)
FAIL_SUMMARY_CALL["on"] = False
SO._dm_target_cache.clear()
check("7j. a market-scope caller STILL sees the one system-wide bulk-failure warning",
      len(resp_dmA_fail["warnings"]) == 1, resp_dmA_fail["warnings"])

# a DM whose OWN market has no grant match anywhere sees an empty (not error, not everything) view —
# reuse dmB (Bakersfield) which genuinely has zero rows this period.
resp_dmB = SO.dm_accessory_attribution(PERIOD, authorization="Bearer dmB", dm_id="", org_id=HOUSE)
check("7k. a market-scope DM with zero rows this period still sees their OWN (empty) card, not nothing",
      set(resp_dmB["by_dm"].keys()) == {dmB_key} and resp_dmB["by_dm"][dmB_key]["total_target"] == 0.0,
      resp_dmB)

# ══════════════════════ 8: BULK-FETCH PERFORMANCE — a large synthetic roster ═══════════════════════
N_STORES, PER_STORE = 15, 4
large_store_codes = [f"L{i}" for i in range(N_STORES)]
store[("storeops", "stores")] += [
    {"org_id": HOUSE, "store_code": c, "address": f"{c} Ave", "market": "LargeMarket"} for c in large_store_codes
]
store[("storeops", "app_users")].append(app_user("dmLarge", HOUSE, "District Manager", market="LargeMarket"))
large_shifts = []
large_reps = []
STORE_MONTHLY = 800.0
for si, code in enumerate(large_store_codes):
    for j in range(PER_STORE):
        name = f"LargeRep{si}_{j}"
        large_reps.append(name)
        large_shifts.append({"org_id": HOUSE, "employee_id": f"E-{name}", "employee_name": name,
                             "store_code": code, "scheduled_hours": 10, "shift_date": "2020-01-20",
                             "is_deleted": False})
store[("storeops", "shifts")] = large_shifts   # replace — this test stands alone, no interaction with 1-7's data
SUMMARY_PAYLOAD["stores"] = [
    {"store_code": c, "categories": {"accessories": {"monthly": STORE_MONTHLY}}, "reps": []}
    for c in large_store_codes
]
CS.invalidate_market_index()
SO._dm_target_cache.clear()
CALLS.clear()

resp_large = SO.dm_accessory_attribution(PERIOD, authorization="", dm_id="", org_id=HOUSE)

n_pairs = N_STORES * PER_STORE   # 60
check(f"8a. {n_pairs} (employee,store) pairs -> STILL exactly ONE upstream call (not one per pair)",
      len(CALLS) == 1, CALLS)
check("8b. full coverage — no truncation on a 60-pair roster", resp_large["truncated"] is False, resp_large)
check(f"8c. all {n_pairs} pairs considered, none dropped", resp_large["pairs_considered"] == n_pairs, resp_large)
dmLarge_key = next(k for k, d in resp_large["by_dm"].items() if "LargeMarket" in d.get("markets", []))
expected_total = N_STORES * STORE_MONTHLY   # each store's 4 equal-hour reps sum their shares back to 1.0
check(f"8d. aggregate target matches the hand-computed expectation ({expected_total}) — every equal-"
      "hour rep's 0.25 share summed back to the store's full target, org-wide",
      resp_large["by_dm"][dmLarge_key]["total_target"] == expected_total, resp_large["by_dm"][dmLarge_key])
check("8e. each individual rep's row is exactly store_monthly/PER_STORE (200.0) — proves per-row math, "
      "not just a coincidentally-matching aggregate",
      all(r["target"] == STORE_MONTHLY / PER_STORE for r in resp_large["by_dm"][dmLarge_key]["rows"]),
      resp_large["by_dm"][dmLarge_key]["rows"][:3])


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
