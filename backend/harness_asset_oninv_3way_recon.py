"""Offline proof harness for the On-Inventory 3-Way Rebate Recon (mod-asset, OWNER DIRECTIVE
2026-07-28, backend/app/modules/asset/oninv_recon.py + database/migrations/310_...sql).

No database, no network: a small recording fake Supabase client feeds the REAL module code
(get_oninv_3way_recon, _call_recon_rpc, _shape_row, _p_asset_oninv_missing_phones).

The actual 3-way JOIN + classification decision tree lives in SQL (migration 310) — there's no live
Postgres in this environment to execute it against (CLAUDE.md: Supabase SQL Editor is web-only /
operator-run). So this harness does two things:

  (A) `sql_classify()` below is a byte-for-byte Python MIRROR of the SQL CASE expression in
      310_asset_oninv_3way_recon_rpc.sql's final SELECT (compare the two side by side — they must
      stay in sync; a comment at the top of each references the other). It is unit-tested
      EXHAUSTIVELY (every reachable combination of imei-blank / leg2-paid / epay-loaded / leg3-match)
      as the executable spec for the classification rule.
  (B) `sql_classify()` then GENERATES the canned RPC rows fed to the fake client's `rpc_fns` for
      every other test below (endpoint aggregation, filter/param translation, attention-provider
      threshold, org isolation) — so those rows are never hand-labeled independently of the
      classification rule; they're internally consistent with it by construction.

Run:  cd backend && python3 harness_asset_oninv_3way_recon.py
"""
import asyncio
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"
TODAY = date.today()


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# (A) sql_classify — Python mirror of migration 310's final SELECT CASE expression.
#     SQL (310_asset_oninv_3way_recon_rpc.sql, final SELECT):
#       WHEN esn_imei blank/null                                             -> 'unmatchable'
#       WHEN reimbursement>0 AND leg3 matched                                -> 'missing_phone_candidate'
#       WHEN reimbursement>0 AND leg3 NOT matched AND epay_loaded            -> 'conflict'
#       WHEN reimbursement<=0 AND leg3 matched                               -> 'conflict'
#       WHEN reimbursement>0 AND leg3 NOT matched AND NOT epay_loaded        -> 'missing_phone_candidate'
#       ELSE                                                                 -> 'non_activated'
def sql_classify(imei_blank: bool, leg2_paid: bool, leg3_matched: bool, epay_loaded: bool) -> str:
    if imei_blank:
        return "unmatchable"
    if leg2_paid and leg3_matched:
        return "missing_phone_candidate"
    if leg2_paid and not leg3_matched and epay_loaded:
        return "conflict"
    if (not leg2_paid) and leg3_matched:
        return "conflict"
    if leg2_paid and not leg3_matched and not epay_loaded:
        return "missing_phone_candidate"
    return "non_activated"


def leg3_status_of(imei_blank, leg3_matched, epay_loaded):
    if imei_blank:
        return "na"
    if leg3_matched:
        return "paid"
    return "not_paid" if epay_loaded else "na"


print("A. sql_classify — exhaustive decision-tree proof (mirrors migration 310's SQL CASE)")
cases = []
for imei_blank in (True, False):
    for leg2_paid in (True, False):
        for leg3_matched in (True, False):
            for epay_loaded in (True, False):
                cases.append((imei_blank, leg2_paid, leg3_matched, epay_loaded))

expect = {
    (True, True, True, True): "unmatchable",
    (True, True, True, False): "unmatchable",
    (True, True, False, True): "unmatchable",
    (True, True, False, False): "unmatchable",
    (True, False, True, True): "unmatchable",
    (True, False, True, False): "unmatchable",
    (True, False, False, True): "unmatchable",
    (True, False, False, False): "unmatchable",
    (False, True, True, True): "missing_phone_candidate",   # both legs agree: paid
    (False, True, True, False): "missing_phone_candidate",  # leg3 "matched" implies epay had data; same result
    (False, True, False, True): "conflict",                 # ledger says paid, ePay (loaded) shows nothing
    (False, True, False, False): "missing_phone_candidate", # ledger says paid, ePay not loaded -> single-leg, not a disagreement
    (False, False, True, True): "conflict",                 # ePay shows a payment, ledger's own reimbursement blank/0
    (False, False, True, False): "conflict",                # (leg3_matched=True implies epay_loaded=True in reality; kept for exhaustiveness)
    (False, False, False, True): "non_activated",           # neither leg shows anything, ePay checked
    (False, False, False, False): "non_activated",          # neither leg shows anything, ePay not loaded either
}
for c in cases:
    got = sql_classify(*c)
    want = expect[c]
    ok(f"classify(imei_blank={c[0]}, leg2_paid={c[1]}, leg3_matched={c[2]}, epay_loaded={c[3]}) -> {want}",
       got == want, f"got {got}")

# Every one of the 4 required buckets is reachable (never a 5th silent bucket):
reachable = {sql_classify(*c) for c in cases}
ok("all 4 classifications reachable", reachable == {"missing_phone_candidate", "conflict", "non_activated", "unmatchable"}, reachable)
# CONFLICT specifically requires disagreement, never silently collapsed into either single-leg bucket:
ok("conflict fires when leg2=paid, leg3=checked-negative", sql_classify(False, True, False, True) == "conflict")
ok("conflict fires when leg2=not-paid, leg3=paid", sql_classify(False, False, True, True) == "conflict")
ok("NOT conflict when leg3 unavailable (na) and leg2=paid (single-leg evidence, not disagreement)",
   sql_classify(False, True, False, False) == "missing_phone_candidate")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# ── fake supabase client (same shape as harness_asset_settings_audit.py's FakeClient) ────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, fn, params):
        self.fn, self.params = fn, params

    def execute(self):
        return _Resp(self.fn(self.params))


class _Schema:
    def __init__(self, rpc_fns, calls):
        self.rpc_fns, self.calls = rpc_fns, calls

    def table(self, name):
        raise AssertionError(f"oninv_recon.py must never read commcalc.{name} directly — "
                              f"the 3-way join lives entirely in the RPC (perf guardrail)")

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        fn = self.rpc_fns.get(name)
        if fn is None:
            raise Exception(f"PGRST202 function {name} does not exist (schema cache)")
        return _RpcCall(fn, params)


class FakeClient:
    def __init__(self, rpc_fns):
        self.rpc_fns = rpc_fns
        self.calls = []

    def schema(self, name):
        assert name == "commcalc"
        return _Schema(self.rpc_fns, self.calls)


def mk_row(store, market, imei, model, acquired, value, imei_blank, leg2_paid, leg3_matched, epay_loaded,
           leg2_amount=50.0, leg3_amount=75.0, pay_count=1, pay_types="Device Financing Bounty"):
    cls = sql_classify(imei_blank, leg2_paid, leg3_matched, epay_loaded)
    st = leg3_status_of(imei_blank, leg3_matched, epay_loaded)
    return {
        "store": store, "market": market, "esn_imei": ("" if imei_blank else imei),
        "device_model": model, "acquired_date": acquired,
        "aging_days": (TODAY - date.fromisoformat(acquired)).days if acquired else None,
        "device_value": value,
        "leg2_paid": leg2_paid, "leg2_amount": (leg2_amount if leg2_paid else 0.0),
        "leg2_date": (acquired if leg2_paid else None),
        "leg3_status": st,
        "leg3_amount": (leg3_amount if st == "paid" else None),
        "leg3_last_date": (acquired if st == "paid" else None),
        "leg3_payment_count": (pay_count if st == "paid" else 0),
        "leg3_payment_types": (pay_types if st == "paid" else None),
        "classification": cls,
    }


import app.modules.asset.oninv_recon as R  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. GET /asset/oninv-3way-recon — degrade when migration 310 hasn't run")
c = FakeClient(rpc_fns={})
R.sb = lambda: c
out = run(R.get_oninv_3way_recon(org_id=ORG_A))
ok("migrated=False, empty rows, never a 500", out["migrated"] is False and out["rows"] == [], out)
ok("degrade payload still has all 4 classification buckets at zero (never a missing key)",
   set(out["totals"].keys()) == {"missing_phone_candidate", "non_activated", "conflict", "unmatchable"}, out["totals"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. GET /asset/oninv-3way-recon — full classification mix, one store, aggregation arithmetic")
acq_old = (TODAY - timedelta(days=80)).isoformat()
rows_a = [
    mk_row("Store 1", "NJ", "111111111111111", "iPhone 15", acq_old, 500.0, False, True, True, True),   # missing_phone_candidate
    mk_row("Store 1", "NJ", "222222222222222", "iPhone 14", acq_old, 400.0, False, True, False, True),  # conflict (leg2 paid, epay checked-negative)
    mk_row("Store 1", "NJ", "333333333333333", "Galaxy S24", acq_old, 300.0, False, False, True, True), # conflict (leg3 paid, leg2 blank)
    mk_row("Store 1", "NJ", "444444444444444", "Galaxy S23", acq_old, 200.0, False, False, False, True),# non_activated
    mk_row("Store 1", "NJ", "", "Pixel 8", acq_old, 100.0, True, False, False, True),                   # unmatchable
    mk_row("Store 2", "LI", "555555555555555", "iPhone 15", acq_old, 600.0, False, True, False, False), # missing_phone_candidate (epay NOT loaded org-wide -> na)
]


def rpc_recon(params):
    # simulates the RPC's own org/store/market filtering so the harness also proves those params
    # are actually threaded through by _call_recon_rpc (not ignored).
    out = list(rows_a)
    stores = params.get("p_stores")
    if stores:
        out = [r for r in out if r["store"] in stores]
    market = params.get("p_market")
    if params.get("p_no_market_only"):
        out = [r for r in out if not r["market"]]
    elif market:
        out = [r for r in out if r["market"] == market]
    return out


c = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon})
R.sb = lambda: c
out = run(R.get_oninv_3way_recon(org_id=ORG_A))
ok("migrated=True", out["migrated"] is True)
ok("6 device rows returned", len(out["rows"]) == 6, len(out["rows"]))
ok("2 stores in summary", len(out["stores"]) == 2, out["stores"])
s1 = next(s for s in out["stores"] if s["store"] == "Store 1")
ok("Store 1 missing_phone_candidate: 1 device, $500", s1["classes"]["missing_phone_candidate"] == {"count": 1, "exposure": 500.0}, s1)
ok("Store 1 conflict: 2 devices, $700", s1["classes"]["conflict"] == {"count": 2, "exposure": 700.0}, s1)
ok("Store 1 non_activated: 1 device, $200", s1["classes"]["non_activated"] == {"count": 1, "exposure": 200.0}, s1)
ok("Store 1 unmatchable: 1 device, $100 (never dropped)", s1["classes"]["unmatchable"] == {"count": 1, "exposure": 100.0}, s1)
ok("Store 1 device_count/total_exposure sums", s1["device_count"] == 5 and s1["total_exposure"] == 1500.0, s1)
s2 = next(s for s in out["stores"] if s["store"] == "Store 2")
ok("Store 2 missing_phone_candidate (epay-not-loaded path): 1 device, $600", s2["classes"]["missing_phone_candidate"] == {"count": 1, "exposure": 600.0}, s2)
ok("grand_total_devices == sum of all rows", out["grand_total_devices"] == 6, out["grand_total_devices"])
ok("grand_total_exposure == sum of all device_value", out["grand_total_exposure"] == 2100.0, out["grand_total_exposure"])
ok("totals arithmetic: sum(per-store class counts) == totals per class",
   all(out["totals"][k]["count"] == sum(s["classes"][k]["count"] for s in out["stores"]) for k in out["totals"]))
ok("device_value_column labeled owed_to_vip (matches every other asset report's $ convention)",
   out["device_value_column"] == "owed_to_vip")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. Filter param translation — store CSV, NO_MARKET_SENTINEL, date range")
c = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon})
R.sb = lambda: c
_ = run(R.get_oninv_3way_recon(org_id=ORG_A, store="Store 1, Store 2", market="NJ",
                                date_from="2026-01-01", date_to="2026-12-31"))
name, params = c.calls[-1]
ok("rpc name", name == "asset_oninv_3way_recon")
ok("p_org_id threaded through", params["p_org_id"] == ORG_A)
ok("p_stores split on comma + trimmed", params["p_stores"] == ["Store 1", "Store 2"], params["p_stores"])
ok("p_market passed through, p_no_market_only False", params["p_market"] == "NJ" and params["p_no_market_only"] is False)
ok("date range passed through", params["p_date_from"] == "2026-01-01" and params["p_date_to"] == "2026-12-31")

c2 = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon})
R.sb = lambda: c2
_ = run(R.get_oninv_3way_recon(org_id=ORG_A, market=R.NO_MARKET_SENTINEL))
_, params2 = c2.calls[-1]
ok("NO_MARKET_SENTINEL -> p_no_market_only=True, p_market=None", params2["p_no_market_only"] is True and params2["p_market"] is None, params2)

c3 = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon})
R.sb = lambda: c3
_ = run(R.get_oninv_3way_recon(org_id=ORG_A))
_, params3 = c3.calls[-1]
ok("no filters -> p_stores None, p_market None, p_no_market_only False", params3["p_stores"] is None and params3["p_market"] is None and params3["p_no_market_only"] is False)

# store filter actually narrows the summary (not just passed through and ignored):
c4 = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon})
R.sb = lambda: c4
out4 = run(R.get_oninv_3way_recon(org_id=ORG_A, store="Store 2"))
ok("store filter narrows to 1 store", len(out4["stores"]) == 1 and out4["stores"][0]["store"] == "Store 2", out4["stores"])
# no-market-only actually narrows (Store 3 below has no market):
rows_nm = list(rows_a) + [mk_row("Store 3", None, "666666666666666", "Moto G", acq_old, 50.0, False, False, False, True)]


def rpc_recon_nm(params):
    out = list(rows_nm)
    if params.get("p_no_market_only"):
        out = [r for r in out if not r["market"]]
    return out


c5 = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_recon_nm})
R.sb = lambda: c5
out5 = run(R.get_oninv_3way_recon(org_id=ORG_A, market=R.NO_MARKET_SENTINEL))
ok("(no market) bucket reachable and isolates Store 3 only", len(out5["stores"]) == 1 and out5["stores"][0]["store"] == "Store 3", out5["stores"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. Org isolation — two orgs never see each other's rows/params")
calls_log = []


def rpc_org_scoped(params):
    calls_log.append(params["p_org_id"])
    if params["p_org_id"] == ORG_A:
        return rows_a
    return [mk_row("Org B Store", "TX", "777777777777777", "iPhone 15", acq_old, 999.0, False, True, True, True)]


cX = FakeClient(rpc_fns={"asset_oninv_3way_recon": rpc_org_scoped})
R.sb = lambda: cX
out_a = run(R.get_oninv_3way_recon(org_id=ORG_A))
out_b = run(R.get_oninv_3way_recon(org_id=ORG_B))
ok("org A gets org A's 2-store result", len(out_a["stores"]) == 2)
ok("org B gets ONLY org B's 1-store result (no cross-tenant bleed)",
   len(out_b["stores"]) == 1 and out_b["stores"][0]["store"] == "Org B Store", out_b["stores"])
ok("both RPC calls carried their own org_id (query-param scoped, never a shared constant)",
   calls_log == [ORG_A, ORG_B], calls_log)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. Admin-attention provider (_p_asset_oninv_missing_phone_candidates) — threshold + degrade")

# F1: below threshold everywhere -> silent
rows_low = [mk_row("Store 1", "NJ", f"{i}"*15, "iPhone 15", acq_old, 100.0, False, True, True, True) for i in range(1, 3)]
c = FakeClient(rpc_fns={"asset_oninv_3way_recon": lambda p: rows_low})
items = R._p_asset_oninv_missing_phones(c, ORG_A, {})
ok("F1 below threshold (2 < 3) -> silent", items == [], items)

# F2: at/over threshold -> fires with count + exposure
rows_hot = [mk_row("Store 1", "NJ", f"{i}"*15, "iPhone 15", acq_old, 100.0, False, True, True, True) for i in range(1, 4)]
c = FakeClient(rpc_fns={"asset_oninv_3way_recon": lambda p: rows_hot})
items = R._p_asset_oninv_missing_phones(c, ORG_A, {})
ok("F2 at threshold (3) -> fires", len(items) == 1 and items[0]["key"] == "asset_oninv_missing_phone_candidates", items)
ok("F2 group='other' per dispatch", items and items[0]["group"] == "other")
ok("F2 count == 3, deep_link to the new report page",
   items and items[0]["count"] == 3 and items[0]["deep_link"] == "/commcalc/asset/oninv-3way-recon", items)

# F3: migration not run -> silent, never raises
c = FakeClient(rpc_fns={})
items = R._p_asset_oninv_missing_phones(c, ORG_A, {})
ok("F3 migration-not-run degrades to silent (no exception)", items == [], items)

# F4: conflict/non_activated-only rows never trigger this provider (it's specifically about
# missing_phone_candidate, not "any anomaly")
rows_conflict_only = [mk_row("Store 1", "NJ", f"{i}"*15, "iPhone 15", acq_old, 100.0, False, True, False, True) for i in range(1, 6)]
c = FakeClient(rpc_fns={"asset_oninv_3way_recon": lambda p: rows_conflict_only})
items = R._p_asset_oninv_missing_phones(c, ORG_A, {})
ok("F4 conflict-only rows don't fire the missing-phone provider", items == [], items)

# F5: registered with core.import_health as cost='heavy', group='other'
try:
    from app.modules.core.import_health import PROVIDERS
    spec = next((p for p in PROVIDERS if p["key"] == "asset_oninv_missing_phone_candidates"), None)
    ok("F5 provider registered", spec is not None)
    ok("F5 registered cost='heavy' (deferred unless deep=1, matches the join-heavy precedent)",
       spec and spec["cost"] == "heavy", spec)
    ok("F5 registered group='other'", spec and spec["group"] == "other", spec)
except Exception as e:
    ok("F5 provider registered", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. Row shaping (_shape_row) — leg2/leg3 evidence never silently coerced/lost")
r = mk_row("Store 1", "NJ", "888888888888888", "iPhone 15", acq_old, 250.0, False, False, True, True,
           leg3_amount=88.5, pay_count=2, pay_types="Device Financing Bounty, In-Store Device Financing")
shaped = R._shape_row(r)
ok("leg2.paid False preserved", shaped["leg2"]["paid"] is False)
ok("leg3.status 'paid' + amount/date/count/types all preserved", shaped["leg3"] == {
    "status": "paid", "amount": 88.5, "last_date": acq_old, "payment_count": 2,
    "payment_types": "Device Financing Bounty, In-Store Device Financing",
}, shaped["leg3"])
ok("classification carried through unchanged", shaped["classification"] == "conflict", shaped)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
