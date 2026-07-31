"""REAL-ASGI smoke for the carrier-income source swap (raw_ma_commission → commission_ledger).

The unit harness (carrier_income_ledger_swap_proof.py) calls the functions directly, which proves the
math but NOT the mount. `[[curl-verified-not-ui-verified-apiv1]]`: `client.ts` `api()` needs an explicit
/api/v1 prefix, so a handler that answers when called can still sit at a path the page never reaches.
This drives the whole FastAPI app through Starlette's TestClient at the EXACT URLs the What-If Company
Payout tab requests, and asserts:

  • /api/v1/commcalc/whatif/carrier-income with income_source='ma_ledger' serves the LEDGER figures,
    names the ledger in `income_legs` / `params.income_leg_source`, and keeps residual + airtime on
    MA Daily Tx
  • the same URL with the legacy income_source='ma' is money-identical to today (nothing moves on merge)
  • `source_swap` — the Gate-2 delta table — ships over HTTP in BOTH modes
  • the DATA-GAP note names the Commission Ledger and distinguishes un-synced from un-pulled months
  • a missing commission_ledger table degrades to the legacy source with a loud `ledger_note`, never $0
  • /api/v1/commcalc/whatif/source-config GET offers `ma_ledger` with its label, and PUT saves it
  • the BARE paths (no /api/v1) are 404 — the page must use the prefix
  • org_id really travels as a QUERY PARAM (a second tenant's URL returns that tenant's own view)
  • no write is ever attempted

Run: `python3 scratchpad/carrier_income_ledger_swap_asgi_smoke.py` from the backend dir.
"""
import copy, io, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)


def _load_proof_helpers():
    """Reuse the proof harness's FakeClient + fixtures WITHOUT re-running its assertions: execute only
    the source ABOVE its first section banner (house harness style)."""
    path = os.path.join(_HERE, "carrier_income_ledger_swap_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("=" * 100)\nprint("A. CONSTANTS + CONFIG DEFAULTS")'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("carrier_income_swap_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_proof_helpers()

from fastapi.testclient import TestClient
import app.core.database as DB
from app.main import app
from app.modules.commcalc import router as R

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


def wire(store, **kw):
    fake = H.FakeClient(copy.deepcopy(store), **kw)
    R.sb = lambda: fake                       # noqa: E731
    DB.get_supabase = lambda *a, **k: fake    # noqa: E731
    H.WRITES.clear()
    H.READS.clear()
    return fake


LUX, OTHER, TOTAL, BOOST = H.LUX, H.OTHER, H.TOTAL_ID, H.BOOST_ID
JUNE, MAY, JULY = H.JUNE, H.MAY, H.JULY
CI = "/api/v1/commcalc/whatif/carrier-income"
SC = "/api/v1/commcalc/whatif/source-config"
LEDGER = H.ma_store(income="ma_ledger")
LEGACY = H.ma_store(income="ma")

client = TestClient(app, raise_server_exceptions=False)


def mon(payload, period):
    return next((t for t in payload.get("totals_by_month", []) if t["period"] == period), {})


print("\n── ASGI: the LEDGER source over HTTP ─────────────────────────────────────────────────")
wire(LEDGER)
r = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
check(f"GET {CI} → 200", r.status_code == 200, r.text[:300])
b = r.json() if r.status_code == 200 else {}
j = mon(b, JUNE)
check("COMMISSION 215.00 from the canonical ledger (not the 23.00 the thin source shows)",
      j.get("components", {}).get("COMMISSION") == 215.0, j.get("components"))
check("SPIFF 8.00 (canonical bucket, not the rebate column's 14.00)",
      j["components"]["SPIFF"] == 8.0)
check("EQUIPMENT_REBATE 12.25 — a heading the legacy source could not produce",
      j["components"]["EQUIPMENT_REBATE"] == 12.25)
check("LEDGER_OTHER 42.50 — unmapped carrier payout surfaced, not dropped",
      j["components"]["LEDGER_OTHER"] == 42.5)
check("RESIDUAL still 175.00 straight off MA Daily Tx", j["residual_mi_atu"] == 175.0)
check("airtime margin still 4.37 off MA Daily Tx", j["components"]["UNMAPPED"] == 4.37)
check("total_comp 282.12", j["total_comp"] == 282.12, j.get("total_comp"))
check("income_legs name the ledger for commission + spiff",
      b.get("income_legs", {}).get("commission") == "commission_ledger"
      and b["income_legs"]["spiff"] == "commission_ledger", b.get("income_legs"))
check("income_legs keep MA Daily Tx for residual + airtime",
      b["income_legs"]["residual"] == b["income_legs"]["airtime"] == "raw_ma_daily_tx")
check("params.income_leg_source == commission_ledger",
      b.get("params", {}).get("income_leg_source") == "commission_ledger")
check("income_source_effective == ma_ledger", b.get("income_source_effective") == "ma_ledger")
check("ledger_ready true over HTTP", b.get("ledger_ready") is True)
check("per-month ledger line count travels", j.get("ledger_lines") == 8, j.get("ledger_lines"))
check("per-month ledger origin mix travels", j.get("ledger_origins") == ["file", "ma_sync"])

print("\n── ASGI: the DATA-GAP note names the LEDGER ──────────────────────────────────────────")
note = b.get("data_note") or ""
check("note present", bool(note))
check("names the Commission Ledger", "Commission Ledger" in note, note[:160])
check("says NO Commission Ledger lines", "NO Commission Ledger lines" in note)
check("states origin-agnostic", "origin-agnostic" in note)
check("separates un-synced (July, raw rows present) from un-pulled (May)",
      "ALREADY loaded for " + JULY in note and "For " + MAY in note, note[-260:])
check("May + July are the flagged months",
      [t["period"] for t in b["totals_by_month"] if t["comp_source_missing"]] == [MAY, JULY])

print("\n── ASGI: source_swap — the Gate-2 delta table, over HTTP, in BOTH modes ──────────────")
sw = b.get("source_swap") or {}
check("source_swap present in ledger mode", bool(sw.get("by_month")))
check("active flag says ma_ledger", sw.get("active") == "ma_ledger")
jrow = next((x for x in sw.get("by_month", []) if x["period"] == JUNE), {})
check("June old 23/14 → new 215/8/12.25/42.5, delta +240.75",
      (jrow.get("old_commission"), jrow.get("old_spiff"), jrow.get("new_total"),
       jrow.get("delta_total")) == (23.0, 14.0, 277.75, 240.75), jrow)
check("residual-overlap lines reported (2 lines / $133.00 excluded, not stacked)",
      (jrow.get("residual_overlap_lines"), jrow.get("residual_overlap_total")) == (2, 133.0))
check("totals row travels", (sw.get("totals") or {}).get("delta_total") == 236.75, sw.get("totals"))

wire(LEGACY)
r2 = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
b2 = r2.json() if r2.status_code == 200 else {}
j2 = mon(b2, JUNE)
check("LEGACY mode still 200", r2.status_code == 200, r2.text[:200])
check("legacy COMMISSION 23.00 / SPIFF 14.00 — unchanged on merge",
      (j2["components"]["COMMISSION"], j2["components"]["SPIFF"]) == (23.0, 14.0))
check("legacy total_comp 41.37 — unchanged on merge", j2["total_comp"] == 41.37)
check("legacy RESIDUAL 175.00 — unchanged", j2["residual_mi_atu"] == 175.0)
check("legacy data_note is the pre-swap wording (NOT a stale ledger)",
      "NOT a stale ledger" in (b2.get("data_note") or ""), b2.get("data_note"))
check("source_swap ALSO ships in legacy mode (see the delta before switching)",
      bool((b2.get("source_swap") or {}).get("by_month")))
check("…with active='ma' and the 'nothing has moved' note",
      b2["source_swap"]["active"] == "ma"
      and "Nothing on this page has moved" in b2["source_swap"]["note"])
check("…and an IDENTICAL by_month table to ledger mode",
      b2["source_swap"]["by_month"] == sw["by_month"])

print("\n── ASGI: degradation — commission_ledger absent ──────────────────────────────────────")
wire(LEDGER, absent={"commission_ledger"})
r3 = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
b3 = r3.json() if r3.status_code == 200 else {}
check("still 200 with no ledger table", r3.status_code == 200, r3.text[:200])
check("falls back to the legacy figures rather than $0",
      mon(b3, JUNE).get("components", {}).get("COMMISSION") == 23.0, mon(b3, JUNE).get("components"))
check("ledger_ready false", b3.get("ledger_ready") is False)
check("a loud ledger_note explains it", "could not be read" in (b3.get("ledger_note") or ""),
      b3.get("ledger_note"))
check("configured source still echoed honestly", b3.get("income_source") == "ma_ledger")

print("\n── ASGI: ⚙️ Sources offers the new source ───────────────────────────────────────────")
wire(LEDGER)
r4 = client.get(SC, params={"carrier_id": TOTAL, "org_id": LUX})
b4 = r4.json() if r4.status_code == 200 else {}
check(f"GET {SC} → 200", r4.status_code == 200, r4.text[:200])
opts = (b4.get("options") or {}).get("income_source") or []
check("ma_ledger offered", "ma_ledger" in opts, opts)
check("legacy ma still offered (one-click revert, no deploy)", "ma" in opts)
lbl = ((b4.get("option_labels") or {}).get("income_source") or {}).get("ma_ledger", "")
check("label names the Commission Ledger and marks it recommended",
      "Commission Ledger" in lbl and "recommended" in lbl, lbl)
check("resolved config echoes ma_ledger", (b4.get("resolved") or {}).get("income_source") == "ma_ledger")

_saved = {}


class PutFake:
    def schema(self, *a, **k):
        class _S:
            def table(_s, t):
                class _T:
                    def upsert(_t, row, **kw):
                        _saved.update(row)

                        class _E:
                            def execute(_e):
                                return types.SimpleNamespace(data=[row])
                        return _E()

                    def select(_t, *a, **k):
                        class _Q:
                            def eq(_q, *a, **k):
                                return _q

                            def execute(_q):
                                return types.SimpleNamespace(data=[])
                        return _Q()
                return _T()
        return _S()


_gate = R._require_commission_admin
R._require_commission_admin = lambda *a, **k: None
R.sb = lambda: PutFake()
try:
    r5 = client.put(SC, params={"org_id": LUX},
                    json={"carrier_id": "00000000-0000-0000-0000-000000000000",
                          "carrier_mode": "plan", "income_source": "ma_ledger"})
    check("PUT source-config → 200", r5.status_code == 200, r5.text[:200])
    check("ma_ledger is savable as config (RULE TWO: no code edit to switch)",
          _saved.get("income_source") == "ma_ledger", _saved)
    check("the write is org-scoped to the caller", _saved.get("org_id") == LUX)
finally:
    R._require_commission_admin = _gate

print("\n── ASGI: the /api/v1 trap + org_id really a query param + zero writes ───────────────")
wire(LEDGER)
check("bare /commcalc/whatif/carrier-income is 404 (the page must use /api/v1)",
      client.get("/commcalc/whatif/carrier-income",
                 params={"org_id": LUX, "carrier_id": TOTAL}).status_code == 404)
mixed = copy.deepcopy(LEDGER)
other = H.ma_store(org=OTHER, income="ma_ledger")
for tbl in ("carrier", "raw_ma_daily_tx", "raw_ma_commission", "commission_ledger"):
    mixed[tbl] = list(mixed[tbl]) + list(other[tbl])
mixed["commission_ledger"].append(
    H._lrow(OTHER, JUNE, "ma_commission", "ma_sync", "new", "commission",
            commission=999999.0, payout_total=999999.0))
wire(mixed)
rl = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX}).json()
ro = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": OTHER}).json()
check("tenant LUX sees 215.00", mon(rl, JUNE)["components"]["COMMISSION"] == 215.0)
check("tenant OTHER sees its own 1,000,214.00 (org_id really switches the view)",
      mon(ro, JUNE)["components"]["COMMISSION"] == 1000214.0,
      mon(ro, JUNE)["components"])
check("no cross-tenant leak either way",
      mon(rl, JUNE)["components"] != mon(ro, JUNE)["components"])
check("empty org_id is rejected (org really is a required query param)",
      client.get(CI, params={"org_id": "", "carrier_id": TOTAL}).status_code in (400, 422))
check("ZERO writes attempted across the whole smoke", H.WRITES == [], H.WRITES)

print(f"\n══ carrier-income ledger-swap ASGI smoke: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
