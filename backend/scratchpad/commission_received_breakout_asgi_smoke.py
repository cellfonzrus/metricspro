"""REAL-ASGI smoke for the Commission Received breakout (owner directive 2026-08-05).

The unit harness (`harness_commission_received_breakout.py`) drives the PURE module and the real
`gp_report.calc_gp_report`, which proves the arithmetic but NOT the mount. `[[curl-verified-not-ui-
verified-apiv1]]`: `client.ts` `api()` needs an explicit /api/v1 prefix, so a handler that answers
when called directly can still sit at a path the page never reaches. This drives the whole FastAPI
app through Starlette's TestClient at the EXACT URL the page requests, and asserts:

  • GET /api/v1/commcalc/commission-received-breakout answers 200 and carries the streams, the
    per-leg columns (M1 … M6), the group totals and the identity block
  • the BARE path (no /api/v1) is 404 — the page must use the prefix
  • the migration-278 RPC being ABSENT degrades to the bounded per-period read and SAYS SO in
    `notes` — it never returns a wrong number and never 500s
  • migration 274 being absent too (no label / ma / mi rollups) still answers 200
  • org_id really travels as a QUERY PARAM: a second tenant's URL returns that tenant's OWN rows and
    NONE of the house tenant's (RULE ONE)
  • the RULE FIVE store/market filter narrows the ePay rows AND drops the company-wide VidaPay money,
    saying so in `notes`
  • a Boost-mode org with no ePay data gets a NAMED gap, not a silent empty page
  • NO WRITE is ever attempted on any table (every write method raises)

Run: `python3 scratchpad/commission_received_breakout_asgi_smoke.py` from the backend dir.
"""
import os, re, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "00000000-0000-0000-0000-0000000000ff"

WRITES = []
RPCS = []

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _failures.append(f"{name} {extra}".strip())
        print(f"  FAIL  {name} {extra}")


# ── in-memory fake supabase client — READS ONLY ──────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, table, absent):
        self.store, self.t, self.absent = store, table, absent
        self.f, self.rng = [], None

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def is_(self, c, v):
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    def insert(self, *a, **k):
        WRITES.append(("insert", self.t)); raise AssertionError("WRITE ATTEMPTED: insert " + self.t)

    def update(self, *a, **k):
        WRITES.append(("update", self.t)); raise AssertionError("WRITE ATTEMPTED: update " + self.t)

    def upsert(self, *a, **k):
        WRITES.append(("upsert", self.t)); raise AssertionError("WRITE ATTEMPTED: upsert " + self.t)

    def delete(self, *a, **k):
        WRITES.append(("delete", self.t)); raise AssertionError("WRITE ATTEMPTED: delete " + self.t)

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "in" and rv not in v:
                return False
            if k == "neq" and rv == v:
                return False
        return True

    def execute(self):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = self.store.setdefault(self.t, [])
        m = [dict(r) for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=m)


class FakeSchema:
    def __init__(self, store, absent, rpcs):
        self.store, self.absent, self.rpcs = store, absent, rpcs

    def table(self, t):
        return FakeQuery(self.store, t, self.absent)

    def rpc(self, name, params):
        RPCS.append(name)
        fn = self.rpcs.get(name)
        if fn is None:
            raise Exception("no such rpc: " + name)

        class _R:
            def execute(_s):
                return FakeResult(fn(params))
        return _R()


class FakeClient:
    def __init__(self, store, absent=None, rpcs=None):
        self.store, self.absent, self.rpcs = store, set(absent or []), dict(rpcs or {})

    def schema(self, s):
        return FakeSchema(self.store, self.absent, self.rpcs)


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────
def base_store():
    return {
        # house = a Boost/ePay tenant; luxelink = a Total/VidaPay tenant (carrier_mode 'plan')
        "carrier": [
            {"org_id": HOUSE, "name": "Boost", "carrier_mode": "boost", "is_active": True},
            {"org_id": LUXE, "name": "Total Wireless", "carrier_mode": "plan", "is_active": True},
        ],
        "store_mapping": [
            {"org_id": HOUSE, "store_address": "100 Main St", "store_code": "S100",
             "market": "NY", "salesforce_id": "SF1"},
            {"org_id": HOUSE, "store_address": "200 Oak Ave", "store_code": "S200",
             "market": "NJ", "salesforce_id": "SF2"},
        ],
        "payment_categories": [
            {"org_id": HOUSE, "description": "New Activation Bounty - Month 1", "category": "Commission"},
            {"org_id": HOUSE, "description": "New Activation Bounty - Month 3", "category": "Commission"},
            {"org_id": HOUSE, "description": "Boost Auto Top-Up", "category": "Commission"},
        ],
        "raw_payment_detail": [
            {"org_id": HOUSE, "period": "June 2026", "business_address": "100 Main St",
             "payment_type": "New Activation Bounty - Month 1", "amount": 5000.0},
            {"org_id": HOUSE, "period": "June 2026", "business_address": "200 Oak Ave",
             "payment_type": "New Activation Bounty - Month 3", "amount": 1500.0},
            {"org_id": HOUSE, "period": "June 2026", "business_address": "200 Oak Ave",
             "payment_type": "Boost Auto Top-Up", "amount": 700.0},
        ],
        "raw_comp_report": [],
        "raw_mi": [
            {"org_id": HOUSE, "period": "June 2026", "salesforce_id": "SF1",
             "actual_mi_payout": 900.0, "actual_atu_payout": 300.0, "mi_activation_date": "2026-06-05"},
            {"org_id": HOUSE, "period": "June 2026", "salesforce_id": "SF2",
             "actual_mi_payout": 2400.0, "actual_atu_payout": 800.0, "mi_activation_date": "2026-03-11"},
        ],
        # luxelink's VidaPay data — the export posts money paid TO the dealer as NEGATIVE
        "raw_ma_commission": [
            {"org_id": LUXE, "period": "June 2026",
             "device_margin": -4700.0, "consumer_margin": 0.0, "consumer_financing": -1200.0,
             "rebate": -60000.0, "wallet_funding": -25000.0, "fees_margin": -5100.0,
             "spiff_m1": -28000.0, "spiff_m2": -9000.0, "spiff_m3": -6000.0,
             "spiff_m4": -3000.0, "spiff_m5": -1500.0, "spiff_m6": -900.0},
        ],
        "raw_ma_daily_tx": [
            {"org_id": LUXE, "period": "June 2026", "order_type": "Postpaid Residual Order",
             "merchant_discount": 3100.0, "retail_cost": -41000.0},
            {"org_id": LUXE, "period": "June 2026", "order_type": "Airtime Top Up",
             "merchant_discount": 10900.0, "retail_cost": -100.0},
        ],
        "commission_leg_config": [],
        "commission_leg_label_map": [],
        "whatif_source_config": [],
    }


def label_rollup(store):
    def fn(p):
        org, periods = p["p_org_id"], p["p_periods"]
        cats = {r["description"]: r["category"] for r in store["payment_categories"]
                if r["org_id"] == org}
        agg = {}
        for r in store["raw_payment_detail"]:
            if r["org_id"] != org or r["period"] not in periods:
                continue
            k = ("payment_detail", r["period"], str(r["business_address"]).split(" ")[0],
                 r["payment_type"], cats.get(r["payment_type"], "Unknown"))
            a = agg.setdefault(k, [0.0, 0])
            a[0] += r["amount"]; a[1] += 1
        return [{"source": k[0], "period": k[1], "store_num": k[2], "label": k[3],
                 "category": k[4], "amount": v[0], "n": v[1]} for k, v in agg.items()]
    return fn


def ma_rollup(store):
    COLS = ["device_margin", "consumer_margin", "consumer_financing", "rebate", "wallet_funding",
            "fees_margin", "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]

    def fn(p):
        org, periods = p["p_org_id"], p["p_periods"]
        agg = {}
        for r in store["raw_ma_commission"]:
            if r["org_id"] != org or r["period"] not in periods:
                continue
            a = agg.setdefault(r["period"], {c: 0.0 for c in COLS})
            for c in COLS:
                a[c] += r.get(c, 0.0)
            a["n"] = a.get("n", 0) + 1
        return [dict(v, period=k) for k, v in agg.items()]
    return fn


def mi_rollup(store):
    MON = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6, "July": 7,
           "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}

    def fn(p):
        org, periods = p["p_org_id"], p["p_periods"]
        out = {}
        for r in store["raw_mi"]:
            if r["org_id"] != org or r["period"] not in periods:
                continue
            mn, yr = r["period"].split()
            py, pm = int(yr), MON[mn]
            d = str(r.get("mi_activation_date") or "")
            leg = None
            if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", d):
                ay, am = int(d[:4]), int(d[5:7])
                off = (py - ay) * 12 + (pm - am)
                leg = off + 1 if off >= 0 else None
            k = (r["period"], r.get("salesforce_id") or "", leg)
            a = out.setdefault(k, [0.0, 0.0, 0])
            a[0] += r["actual_mi_payout"]; a[1] += r["actual_atu_payout"]; a[2] += 1
        return [{"period": k[0], "salesforce_id": k[1], "leg_month": k[2],
                 "mi": v[0], "atu": v[1], "n": v[2]} for k, v in out.items()]
    return fn


def tx_rollup(store):
    def fn(p):
        org, periods, pat = p["p_org_id"], p["p_periods"], (p.get("p_residual_pattern") or "")
        agg = {}
        for r in store["raw_ma_daily_tx"]:
            if r["org_id"] != org or r["period"] not in periods:
                continue
            a = agg.setdefault(r["period"], {"airtime_all": 0.0, "airtime_residual_orders": 0.0,
                                             "residual_orders": 0.0, "n": 0, "n_residual": 0})
            a["airtime_all"] += r["merchant_discount"]
            a["n"] += 1
            if pat.lower() in str(r["order_type"]).lower():
                a["airtime_residual_orders"] += r["merchant_discount"]
                a["residual_orders"] += r["retail_cost"]
                a["n_residual"] += 1
        return [dict(v, period=k) for k, v in agg.items()]
    return fn


def all_rpcs(store):
    return {"commission_leg_label_rollup": label_rollup(store),
            "commission_leg_ma_rollup": ma_rollup(store),
            "commission_leg_mi_rollup": mi_rollup(store),
            "commission_received_tx_rollup": tx_rollup(store)}


# ── drive the REAL app ───────────────────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient          # noqa: E402
import app.core.database as DB                     # noqa: E402
from app.main import app                           # noqa: E402
from app.modules.commcalc import router as R       # noqa: E402

URL = "/api/v1/commcalc/commission-received-breakout"


def install(store, absent=None, rpcs=None):
    c = FakeClient(store, absent=absent, rpcs=rpcs)
    DB.get_supabase = lambda: c
    R.get_supabase = lambda: c
    if hasattr(R, "sb"):
        R.sb = lambda: c
    return c


def stream(d, key):
    return next((s for s in (d.get("streams") or []) if s["key"] == key), None)


print("=" * 100)
print("ASGI SMOKE — /api/v1/commcalc/commission-received-breakout")
print("=" * 100)

client = TestClient(app)

# ── 1. the house (ePay/Boost) tenant, everything migrated ────────────────────────────────────────
print("\n1. house tenant (ePay) — full migration state")
st = base_store()
install(st, rpcs=all_rpcs(st))
r = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": HOUSE})
check("1a 200 OK", r.status_code == 200, str(r.status_code) + " " + r.text[:200])
d = r.json() if r.status_code == 200 else {}
check("1b money flag + basis present",
      d.get("money") is True and "month-of-life" in (d.get("basis") or ""),
      str(d.get("basis"))[:120])
check("1c identity holds", d.get("identity_ok") is True, str(d.get("identity"))[:160])
s_comm, s_mi, s_atu = stream(d, "comm_epay"), stream(d, "mi"), stream(d, "atu")
check("1d ePay commission stream present", s_comm is not None)
check("1e commission total == 5000+1500+700", s_comm and abs(s_comm["total"] - 7200.0) < 0.01,
      str(s_comm and s_comm["total"]))
check("1f M1 rung == 5000", s_comm and abs(s_comm["legs"].get("1", 0) - 5000.0) < 0.01)
check("1g M3 rung == 1500", s_comm and abs(s_comm["legs"].get("3", 0) - 1500.0) < 0.01)
check("1h Auto Top-Up (no month in label) is Unsplit == 700",
      s_comm and abs(s_comm["legs"].get("unsplit", 0) - 700.0) < 0.01)
check("1i MI stream split by activation date: M1 900 + M4 2400",
      s_mi and abs(s_mi["legs"].get("1", 0) - 900.0) < 0.01
      and abs(s_mi["legs"].get("4", 0) - 2400.0) < 0.01, str(s_mi and s_mi["legs"]))
check("1j ATU stream split the same way: M1 300 + M4 800",
      s_atu and abs(s_atu["legs"].get("1", 0) - 300.0) < 0.01
      and abs(s_atu["legs"].get("4", 0) - 800.0) < 0.01, str(s_atu and s_atu["legs"]))
check("1k MI and ATU are SEPARATE rows (the owner's question)", s_mi is not None and s_atu is not None)
check("1l leg columns expose M1 and M4 individually", 1 in (d.get("leg_columns") or [])
      and 4 in (d.get("leg_columns") or []), str(d.get("leg_columns")))
check("1m the mi rollup RPC was actually called (mig 274's unused aggregate is now wired)",
      "commission_leg_mi_rollup" in RPCS)
check("1n carrier_mode reported", d.get("carrier_mode") == "boost", str(d.get("carrier_mode")))
groups = {g["group"]: g for g in (d.get("groups") or [])}
check("1o commission group total == the commission stream", "commission" in groups
      and abs(groups["commission"]["total"] - 7200.0) < 0.01)
check("1p residual group total == MI + ATU", "residual" in groups
      and abs(groups["residual"]["total"] - (3300.0 + 1100.0)) < 0.01,
      str(groups.get("residual", {}).get("total")))
check("1q no write attempted", not WRITES, str(WRITES))

# ── 2. the bare path must 404 (the /api/v1 prefix trap) ──────────────────────────────────────────
print("\n2. mount")
rb = client.get("/commcalc/commission-received-breakout",
                params={"period": "June 2026", "org_id": HOUSE})
check("2a bare path is 404 — the page must use /api/v1", rb.status_code == 404, str(rb.status_code))

# ── 3. RULE ONE — org_id is a QUERY PARAM and really isolates ────────────────────────────────────
print("\n3. RULE ONE — multi-tenant isolation")
st = base_store()
install(st, rpcs=all_rpcs(st))
rl = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": LUXE})
check("3a luxelink 200 OK", rl.status_code == 200, str(rl.status_code) + " " + rl.text[:200])
dl = rl.json() if rl.status_code == 200 else {}
check("3b luxelink sees NO house ePay commission", stream(dl, "comm_epay") is None)
check("3c luxelink sees NO house MI/ATU", stream(dl, "mi") is None and stream(dl, "atu") is None)
sl = stream(dl, "comm_ma")
check("3d luxelink sees its OWN VidaPay commission", sl is not None)
check("3e VidaPay M1 == spiff_m1 alone == 28,000 (owner ruling 2026-08-05)",
      sl and abs(sl["m1"] - 28000.0) < 0.01, str(sl and sl["m1"]))
check("3f VidaPay M2..M6 are their OWN lines",
      sl and all(abs(sl["legs"].get(str(n), 0) - v) < 0.01
                 for n, v in ((2, 9000.0), (3, 6000.0), (4, 3000.0), (5, 1500.0), (6, 900.0))),
      str(sl and sl["legs"]))
check("3g the six margins are Unsplit == 96,000 and NAMED",
      sl and abs(sl["unsplit"] - 96000.0) < 0.01
      and set(sl.get("meta", {}).get("unsplit_fields") or []) == {
          "rebate", "device_margin", "consumer_margin", "consumer_financing",
          "wallet_funding", "fees_margin"},
      str(sl and (sl["unsplit"], sl.get("meta"))))
sa, sr = stream(dl, "ma_airtime"), stream(dl, "ma_residual_orders")
check("3h ATU / airtime margin row == 14,000 (all daily-tx rows — the GP basis)",
      sa and abs(sa["total"] - 14000.0) < 0.01, str(sa and sa["total"]))
check("3i Postpaid Residual Orders row == 41,000 (sign applied, the ma-overview basis)",
      sr and abs(sr["total"] - 41000.0) < 0.01, str(sr and sr["total"]))
check("3j the residual-orders row is a CROSS-CHECK, in no total", sr and sr["in_total"] is False)
check("3k the overlap between the two readings is named == 3,100",
      sa and abs((sa.get("meta") or {}).get("airtime_on_residual_orders", 0) - 3100.0) < 0.01,
      str(sa and sa.get("meta")))
check("3l divergence stated in plain English", "not the same money" in (dl.get("divergence_note") or "")
      or "Postpaid Residual Orders" in (dl.get("divergence_note") or ""))
check("3m luxelink carrier_mode is 'plan'", dl.get("carrier_mode") == "plan", str(dl.get("carrier_mode")))
check("3n luxelink identity holds", dl.get("identity_ok") is True, str(dl.get("identity"))[:160])
check("3o no write attempted", not WRITES, str(WRITES))

# ── 4. migration 278 ABSENT — bounded fallback, and it says so ───────────────────────────────────
print("\n4. migration 278 not applied yet")
st = base_store()
rpcs = all_rpcs(st)
rpcs.pop("commission_received_tx_rollup")
install(st, rpcs=rpcs)
r4 = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": LUXE})
check("4a still 200", r4.status_code == 200, str(r4.status_code) + " " + r4.text[:200])
d4 = r4.json() if r4.status_code == 200 else {}
s4 = stream(d4, "ma_airtime")
check("4b airtime is still RIGHT via the bounded read", s4 and abs(s4["total"] - 14000.0) < 0.01,
      str(s4 and s4["total"]))
check("4c the page SAYS the aggregate is degraded",
      any("278" in n for n in (d4.get("notes") or [])), str(d4.get("notes")))
check("4d degraded flag set", d4.get("degraded") is True)

# ── 5. migrations 274 AND 278 both absent — never 500 ────────────────────────────────────────────
print("\n5. no migrations at all")
st = base_store()
install(st, rpcs={})
r5 = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": HOUSE})
check("5a still 200", r5.status_code == 200, str(r5.status_code) + " " + r5.text[:200])
d5 = r5.json() if r5.status_code == 200 else {}
check("5b ePay commission still computed from the raw table",
      stream(d5, "comm_epay") and abs(stream(d5, "comm_epay")["total"] - 7200.0) < 0.01,
      str(stream(d5, "comm_epay")))
check("5c MI/ATU still computed (Python activation split)",
      stream(d5, "mi") and abs(stream(d5, "mi")["legs"].get("1", 0) - 900.0) < 0.01,
      str(stream(d5, "mi") and stream(d5, "mi")["legs"]))
check("5d migration 274 is named in the notes",
      any("274" in n for n in (d5.get("notes") or [])), str(d5.get("notes")))
check("5e identity still holds", d5.get("identity_ok") is True)

# ── 6. RULE FIVE — filters narrow, and company-wide money is DROPPED (not silently kept) ─────────
print("\n6. RULE FIVE filters")
st = base_store()
install(st, rpcs=all_rpcs(st))
r6 = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": HOUSE, "market": "NY"})
d6 = r6.json()
check("6a NY-only commission == the 100-Main-St label only",
      abs(stream(d6, "comm_epay")["total"] - 5000.0) < 0.01, str(stream(d6, "comm_epay")["total"]))
check("6b NY-only MI == SF1 only", abs(stream(d6, "mi")["total"] - 900.0) < 0.01)
check("6c filtered flag set", d6.get("filtered") is True)
check("6d the page says company-wide VidaPay money is excluded under a filter",
      any("company-wide" in n for n in (d6.get("notes") or [])), str(d6.get("notes")))
r6b = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": LUXE, "market": "NY"})
d6b = r6b.json()
check("6e a filtered VidaPay tenant shows NO company-wide rows (never mis-attributed to a store)",
      stream(d6b, "comm_ma") is None and stream(d6b, "ma_airtime") is None)

# ── 7. a NAMED gap, not a silent empty page ──────────────────────────────────────────────────────
print("\n7. missing-feed honesty")
st = base_store()
st["raw_payment_detail"] = []
st["raw_mi"] = []
install(st, rpcs=all_rpcs(st))
r7 = client.get(URL, params={"period": "June 2026", "months": 3, "org_id": HOUSE})
d7 = r7.json()
check("7a 200 with no data", r7.status_code == 200)
gk = {g["stream"] for g in (d7.get("gaps") or [])}
check("7b the missing ePay Payment Detail is NAMED", "comm_epay" in gk, str(d7.get("gaps")))
check("7c the missing MI report is NAMED", "mi" in gk, str(d7.get("gaps")))
check("7d each gap says HOW to import it",
      all(g.get("how") for g in (d7.get("gaps") or [])), str(d7.get("gaps")))
check("7e a Boost org is NOT told to import VidaPay reports it does not use",
      "comm_ma" not in gk and "ma_airtime" not in gk, str(gk))

# ── 8. no write, anywhere, in any scenario ───────────────────────────────────────────────────────
print("\n8. money safety")
check("8a zero write attempts across every scenario", not WRITES, str(WRITES))
check("8b only READ rpcs were used",
      set(RPCS) <= {"commission_leg_label_rollup", "commission_leg_ma_rollup",
                    "commission_leg_mi_rollup", "commission_received_tx_rollup"}, str(sorted(set(RPCS))))

print("\n" + "=" * 100)
print(f"RESULT: {_pass} passed, {_fail} failed")
if _failures:
    print("\nFAILURES:")
    for f in _failures:
        print("  -", f)
print("=" * 100)
sys.exit(1 if _fail else 0)
