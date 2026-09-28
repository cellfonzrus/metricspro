"""PROOF — "when sept is uploaded the system should calculate automatically without manual intervention"
(owner 2026-09-28; index §6l). DB-free: an in-memory client that serves the fixture tables and records every
write; the REAL landers (`_ingest_mapped_df` — the onboarding intake / mapped upload core; `upload_file` — the
upload pages and the Email Auto-Import; `_promote_feed_to_raw_sales` — the feed → raw_sales derivation), the REAL
hook (`auto_calc.landed` / `run_due`) and the REAL `_run_calculation`. Nothing the code under test owns is mocked.

  A. the pure rules: config inheritance (tenant > house > default ON), month spelling, debounce + max wait, the
     outcome classifier, the sentences;
  B. September landed through THREE different landers → ONE queued request → nothing runs inside the quiet window
     → exactly ONE Run Calculation for (org, September), none for any other org / month;
  C. the stored rep_commissions rows == what a manual Run Calculation (the button's background task) writes over
     the same data;
  D. a second landing of the same data → one more run, byte-identical rows (idempotent); a byte-identical file
     through the upload page is refused as a duplicate and queues nothing;
  E. a burst of 30 daily files → ONE calculation; a late correction to August while September waits → each month
     calculated once;
  F. a REFUSAL is recorded, not swallowed — the unconfigured-tenant refusal (R1) and the zero-wipe guard: outcome
     'refused' with the calculation's own words, calc_status 'error', the last good snapshot kept, the page
     sentence says so; a calculation already running → 'busy', re-queued, runs after;
  G. config off (the tenant's row, or inherited from the house row) → no calculation, the landing recorded as 'off';
     a tenant row ON beats a house row OFF;
  H. org-scoped: a landing in org A never calculates org B; B's rows untouched;
  I. migration 1030 not applied → the process-local queue: still exactly one calculation, the outcome recorded as a
     calc_notices entry;
  J. GET /calc-status serves the sentence the Rep Incentive page shows;
  N. NEGATIVE CONTROLS — a hook with no debounce runs the burst many times; a hook that records a refusal as a
     success; a hook that ignores the config → each RED.

    python3 backend/harness_auto_calc_on_landing.py
"""
import asyncio
import copy
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL"):
    os.environ.pop(_k, None)
os.environ["AUTO_CALC_POLLER"] = "0"

import pandas as pd                                                    # noqa: E402

from app.modules.commcalc import router as R                          # noqa: E402
from app.modules.commcalc import auto_calc as AC                       # noqa: E402
from app.modules.commcalc import column_mapping as CM                  # noqa: E402

ORG = "00000000-0000-0000-0000-0000000ac001"
ORG_B = "00000000-0000-0000-0000-0000000ac002"
HOUSE = AC.HOUSE_ORG
SEPT, AUG = "September 2026", "August 2026"
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:600]))


# ── the in-memory store (the recompute-rep e2e fake, with the filters the hook's claim needs) ──────────────────
class Store:
    def __init__(self, tables, missing_cols=()):
        self.t = copy.deepcopy(tables)
        self.writes = []
        self._id = 0
        self.missing_cols = set(missing_cols)     # columns that do not exist (a migration not applied)

    def next_id(self):
        self._id += 1
        return "row-%d" % self._id


class Q:
    def __init__(self, store, name):
        self.s, self.name, self.f, self.op, self.payload = store, name, [], "select", None
        self._limit, self._range, self.conflict = None, None, None

    def _rows(self):
        return self.s.t.setdefault(self.name, [])

    def _match(self, r):
        for kind, col, val in self.f:
            v = r.get(col)
            if kind == "eq" and str(v) != str(val):
                return False
            if kind == "neq" and str(v) == str(val):
                return False
            if kind == "in" and str(v) not in {str(x) for x in val}:
                return False
            if kind == "is" and not ((val == "null" and v is None) or (val != "null" and v is not None)):
                return False
            if kind == "lte" and not (v is not None and str(v) <= str(val)):
                return False
            if kind == "gte" and not (v is not None and str(v) >= str(val)):
                return False
            if kind == "lt" and not (v is not None and str(v) < str(val)):
                return False
        return True

    def select(self, cols="*", **k):
        for c in [c.strip() for c in str(cols).split(",") if c.strip()]:
            if c in self.s.missing_cols:
                raise RuntimeError(f'42703 column "{c}" does not exist')
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def is_(self, c, v):
        self.f.append(("is", c, v)); return self

    def lte(self, c, v):
        self.f.append(("lte", c, v)); return self

    def gte(self, c, v):
        self.f.append(("gte", c, v)); return self

    def lt(self, c, v):
        self.f.append(("lt", c, v)); return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        self._range = (a, b); return self

    def _noop(self, *a, **k):
        return self
    gt = ilike = like = order = or_ = contains = filter = maybe_single = single = not_ = _noop

    def insert(self, rows, **k):
        self.op, self.payload = "insert", rows; return self

    def upsert(self, rows, on_conflict=None, **k):
        self.op, self.payload, self.conflict = "upsert", rows, on_conflict; return self

    def update(self, payload, **k):
        self.op, self.payload = "update", payload; return self

    def delete(self):
        self.op = "delete"; return self

    def _check_cols(self, rows):
        for r in rows:
            bad = [c for c in r if c in self.s.missing_cols]
            if bad:
                raise RuntimeError(f'42703 column "{bad[0]}" does not exist')

    def execute(self):
        rows = self._rows()
        R_ = type("Res", (), {})
        if self.op == "select":
            out = [copy.deepcopy(r) for r in rows if self._match(r)]
            if self._range:
                out = out[self._range[0]: self._range[1] + 1]
            if self._limit is not None:
                out = out[: self._limit]
            res = R_(); res.data, res.count = out, len(out); return res
        payload = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op in ("insert", "upsert", "update"):
            self._check_cols(payload)
        self.s.writes.append((self.name, self.op, copy.deepcopy(self.payload), list(self.f)))
        res = R_(); res.data, res.count = [], 0
        if self.op == "insert":
            for r in payload:
                r = dict(r); r.setdefault("id", self.s.next_id()); rows.append(r)
            res.data = payload
        elif self.op == "upsert":
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            for r in payload:
                hit = next((x for x in rows if keys and all(str(x.get(k)) == str(r.get(k)) for k in keys)), None)
                if hit is not None:
                    hit.update(copy.deepcopy(r))
                else:
                    r = dict(r); r.setdefault("id", self.s.next_id()); rows.append(r)
            res.data = payload
        elif self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(copy.deepcopy(self.payload))
            res.data, res.count = [copy.deepcopy(r) for r in hit], len(hit)
        elif self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            self.s.t[self.name] = [r for r in rows if not self._match(r)]
            res.data = gone
        return res


class Client:
    def __init__(self, store):
        self.s = store

    def schema(self, n):
        st = self.s

        class S:
            def table(self_, t):
                return Q(st, n + "." + t)

            def rpc(self_, *a, **k):
                return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()
        return S()

    def table(self, t):
        return Q(self.s, "public." + t)

    def rpc(self, *a, **k):
        return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()


# ── the fixture: a plan-mode org, two reps, one plan ($10 per activation, $5 per upgrade) ──────────────────────
MAP_HDR = {r["target_field"]: r["source_header"] for r in CM.default_mapping("sales")}


def line(org, rep, tid, day, product, category, serial="", ext=0.0, contract="", month="09"):
    return {"org_id": org, "trans_id": tid, "trans_date": f"2026-{month}-{day:02d}", "store": "Store 1321",
            "salesperson": rep, "user_login": rep, "department": "Activations (Price Sheet)",
            "category": ">> Activations (Price Sheet) >> Carrier >> " + category, "product_desc": product,
            "serial_1": serial, "mdn": "", "ext_price": ext, "gp": ext, "voided": "No", "trans_type": "Sale",
            "contract_type": contract, "quantity": 1.0, "customer": "A CUSTOMER"}


def sept_lines(org=ORG):
    return [line(org, "Jona Sejat", "T1", 2, "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "9297458084", 150.0),
            line(org, "Jona Sejat", "T2", 3, "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "3478963977", 150.0),
            line(org, "Jona Sejat", "T3", 4, "DPA Upgrade iPhone", "Upgrades", "7185550101", 0.0, "Upgrade"),
            line(org, "Other Rep", "T9", 5, "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "6465550199", 150.0)]


def as_df(lines):
    cols = ["store", "salesperson", "user_login", "contract_type", "department", "category", "product_desc", "gp",
            "ext_price", "trans_id", "trans_date", "serial_1", "voided", "trans_type", "quantity", "customer"]
    return pd.DataFrame([{MAP_HDR[c]: (f"{r[c]} 10:00:00" if c == "trans_date" else r[c]) for c in cols}
                         for r in lines])


TENANT_ADR = {"fields": ["category", "product_desc", "contract_type"],
              "tokens": {"byod": ["customer provided", "byod"], "port": ["port"], "upgrade": ["upgrade"],
                         "activation": ["new act", "add a line"], "hardware_only": ["hardware only"]}}
RULES = [{"id": "RACT", "label": "Activation", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "premium,byod", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 10.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 0},
         {"id": "RUPG", "label": "Upgrade", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "upgrade", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 5.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 1}]


def org_tables(org, rules=RULES, assigned=True):
    return {
        "commcalc.carrier": [{"id": "C" + org[-3:], "org_id": org, "name": "Carrier X", "code": "carrier_x",
                              "is_default": True, "engine_mode": "plan"}],
        "commcalc.commission_plan": [{"id": "FLAT" + org[-3:], "org_id": org, "name": "Flat", "is_active": True,
                                      "carrier_id": "C" + org[-3:], "base_tier_metric": None,
                                      "commission_basis": "rules", "activation_source": "inherit"}],
        "commcalc.commission_rule": [dict(r, org_id=org, plan_id="FLAT" + org[-3:]) for r in rules],
        "commcalc.commission_tier": [],
        "commcalc.commission_plan_assignment": ([{"id": "A" + org[-3:], "org_id": org, "plan_id": "FLAT" + org[-3:],
                                                  "scope": "default", "scope_value": None, "priority": 0}]
                                                if assigned else []),
        "commcalc.accessory_config": [{"org_id": org, "activation_details_rules": TENANT_ADR, "contract_type_map": {},
                                       "activation_rules": []}],
    }


def base_tables(tenant_cfg=None, house_cfg=None, extra=None):
    t = {}
    for org in (ORG, ORG_B):
        for k, v in org_tables(org).items():
            t.setdefault(k, []).extend(copy.deepcopy(v))
    cfg = []
    if house_cfg is not None:
        cfg.append(dict(house_cfg, org_id=HOUSE))
    if tenant_cfg is not None:
        cfg.append(dict(tenant_cfg, org_id=ORG))
    t["commcalc.commission_org_config"] = cfg
    for k, v in (extra or {}).items():
        t[k] = copy.deepcopy(v)
    return t


RUNS = []


def counting_runner(period, org_id):
    """THE default runner (the real `_run_calculation`), counted — so 'exactly one calculation' is measured."""
    RUNS.append((org_id, period))
    return AC._default_runner(period, org_id)


def use(store):
    R.sb = lambda: Client(store)
    R.get_supabase = lambda: Client(store)
    R._TABLE_COL_PRESENT.clear()
    R.require_org = lambda org_id: None
    AC._STATE.update(ready=None, checked=0.0)
    AC._MEM.clear()
    return Client(store)


def now():
    return datetime.now(timezone.utc)


def later(minutes):
    return now() + timedelta(minutes=minutes)


def rep_rows(store, org=ORG, period=SEPT):
    drop = ("id", "created_at", "updated_at")
    rows = [{k: v for k, v in r.items() if k not in drop} for r in store.t.get("commcalc.rep_commissions", [])
            if r.get("org_id") == org and str(r.get("period")) in R._pvariants(period)]
    return sorted(rows, key=lambda r: str(r.get("epay_salesperson")))


def status_row(store, org=ORG, period=SEPT):
    return next((r for r in store.t.get("commcalc.calc_status", [])
                 if r.get("org_id") == org and r.get("period") == period), {})


def land_intake(client, lines, fname="sept_sales.xlsx"):
    return R._ingest_mapped_df(ORG, "sales", "raw_sales", CM.default_mapping("sales"), as_df(lines), period="",
                               fname=fname, trace_source="onboarding-intake")


class UF:
    def __init__(self, data, filename):
        self.filename, self.file = filename, io.BytesIO(data)

    async def read(self):
        return self.file.read()

    async def seek(self, n):
        self.file.seek(n)


def land_upload(lines, fname, file_type="daily_sales", source="email_sweep"):
    buf = io.StringIO()
    as_df(lines).to_csv(buf, index=False)
    return asyncio.run(R.upload_file(file_type, UF(buf.getvalue().encode(), fname), "", org_id=ORG,
                                     trace_source=source))


def land_promotion(client, period=SEPT):
    return R._promote_feed_to_raw_sales(client, ORG, period)


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── A. the pure rules ──")
c = AC.resolve_config
check("A1 no rows anywhere → the code default: ON, 5-minute window",
      c(None, None) == {"on": True, "on_source": "default", "debounce_minutes": 5, "debounce_source": "default"})
check("A2 the house row decides for a tenant with no row of its own; the tenant's row wins over the house",
      c(None, {"auto_calc_on_landing": False})["on"] is False
      and c({"auto_calc_on_landing": True}, {"auto_calc_on_landing": False})["on"] is True
      and c({"auto_calc_on_landing": None}, {"auto_calc_on_landing": False})["on_source"] == "house"
      and c({"auto_calc_debounce_minutes": 9999}, None)["debounce_minutes"] == 240)
check("A3 the months a landing touched, in the ONE stored spelling, oldest first; a non-month is dropped",
      AC.month_periods(["2026-09", "September 2026", "Aug 2026", "", "(no period)"]) == [AUG, SEPT])
t0 = now()
ls = [AC.landing_entry("manual", "raw_sales", "a.csv", 3, AC._iso(t0))]
check("A4 debounce: not due inside the quiet window, due after it; a stream cannot starve it (max wait)",
      not AC.is_due(AC._iso(t0), ls, 5, t0 + timedelta(minutes=4))
      and AC.is_due(AC._iso(t0), ls, 5, t0 + timedelta(minutes=5, seconds=1))
      and AC.is_due(AC._iso(t0 + timedelta(minutes=29)), ls, 5, t0 + timedelta(minutes=30, seconds=1)))
check("A5 the classifier keeps a REFUSAL apart from a crash, a success and a busy slot",
      AC.classify_run({"status": "error", "error": "REFUSED to calculate x"})[0] == "refused"
      and AC.classify_run({"status": "error", "error": "No sales data"})[0] == "failed"
      and AC.classify_run({"status": "done", "reps": 2})[0] == "calculated"
      and AC.classify_run({"status": "done", "save_errors": ["flags: x"]})[0] == "calculated_with_errors"
      and AC.classify_run({"skipped": "already_running"})[0] == "busy"
      and AC.classify_run(None)[0] == "failed")
s1 = AC.sentence("calculated", SEPT, ls, at=AC._iso(t0), reps=2)
check("A6 the page sentence names the time and the upload: 'Auto-calculated at … from the upload of a.csv (the Upload page)'",
      s1.startswith("Auto-calculated at ") and "from the upload of a.csv (the Upload page)" in s1, s1)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── B/C. September through three landers → ONE calculation == the manual Run Calculation ──")
st = Store(base_tables(house_cfg={"auto_calc_on_landing": True}))
cl = use(st)
RUNS.clear()
r1 = land_intake(cl, sept_lines()[:2])                                   # lander 1: the onboarding intake core
r2 = land_upload(sept_lines()[2:3], "Sales Transaction Details 0904.csv")  # lander 2: Email Auto-Import (upload_file)
st.t.setdefault("commcalc.daily_sales_feed", [])
for ln in sept_lines():
    st.t["commcalc.daily_sales_feed"].append(dict(ln, period=SEPT, id=st.next_id()))
r3 = land_promotion(cl)                                                    # lander 3: feed → raw_sales derivation
check("B1 each lander answered that September's calculation is QUEUED (the upload is not blocked by a run)",
      all((x or {}).get("auto_calc", {}).get("queued") for x in (r1, r2, r3))
      and all((x or {}).get("auto_calc", {}).get("periods") == [SEPT] for x in (r1, r2, r3)) and RUNS == [],
      [(x or {}).get("auto_calc") for x in (r1, r2, r3)])
row = status_row(st)
check("B2 ONE pending request for (org, September) carrying all three landings, in order",
      row.get(AC.COL_REQUESTED) and [x["source"] for x in row.get(AC.COL_LANDINGS) or []]
      == ["onboarding-intake", "email_sweep", "promotion"], row)
AC.run_due(cl, now=later(2), runner=counting_runner)
check("B3 inside the quiet window NOTHING runs", RUNS == [], RUNS)
out = AC.run_due(cl, now=later(6), runner=counting_runner)
check("B4 after it: exactly ONE Run Calculation, for (org, September) — no other org, no other month",
      RUNS == [(ORG, SEPT)] and len(out) == 1, RUNS)
AC.run_due(cl, now=later(12), runner=counting_runner)
check("B5 the claimed request is gone — a later tick runs nothing more", RUNS == [(ORG, SEPT)], RUNS)
auto_rows = rep_rows(st)
last = status_row(st).get(AC.COL_LAST) or {}
check("B6 the outcome is recorded: calculated, with the three landings and the rep count",
      last.get("outcome") == "calculated" and len(last.get("landings") or []) == 3 and last.get("reps") == 2
      and last.get("sentence", "").startswith("Auto-calculated at "), last)

st_manual = Store(base_tables(house_cfg={"auto_calc_on_landing": True}))
st_manual.t["commcalc.raw_sales"] = copy.deepcopy(st.t["commcalc.raw_sales"])
st_manual.t["commcalc.daily_sales_feed"] = copy.deepcopy(st.t["commcalc.daily_sales_feed"])
use(st_manual)
R._run_calculation(SEPT, ORG, False, "the-button")                        # what POST /calculate's task runs
manual_rows = rep_rows(st_manual)
use(st)
check("C1 the stored rows == what a manual Run Calculation writes over the same landed data (every column)",
      auto_rows == manual_rows and len(auto_rows) == 2, {"auto": auto_rows, "manual": manual_rows})
jona = next((r for r in auto_rows if r.get("epay_salesperson") == "Jona Sejat"), {})
check("C2 …and they are the real money: Jona $25.00 (2 activations × $10 + 1 upgrade × $5), Other $10.00",
      jona.get("total_payout") == 25.0
      and next((r for r in auto_rows if r.get("epay_salesperson") == "Other Rep"), {}).get("total_payout") == 10.0,
      [(r.get("epay_salesperson"), r.get("total_payout")) for r in auto_rows])

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── D. idempotent ──")
RUNS.clear()
before = rep_rows(st)
land_intake(cl, sept_lines()[:2])
AC.run_due(cl, now=later(6), runner=counting_runner)
check("D1 the same data landed again → one more calculation, rows byte-identical", RUNS == [(ORG, SEPT)]
      and rep_rows(st) == before, rep_rows(st))
RUNS.clear()
dup = land_upload(sept_lines()[2:3], "Sales Transaction Details 0904.csv")
check("D2 a byte-identical file through the upload page is refused as a duplicate (nothing lands, nothing queues) "
      "— or, if the duplicate guard is unavailable, it re-lands and queues", ("auto_calc" not in (dup or {}))
      or (dup.get("auto_calc") or {}).get("queued"), dup)
AC.run_due(cl, now=later(6), runner=counting_runner)
check("D3 …and the rows are still the same", rep_rows(st) == before)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── E. a burst coalesces; a late correction to an older month is calculated too ──")
RUNS.clear()
for d in range(30):
    AC.landed(cl, ORG, table="daily_sales_feed", periods=[SEPT], source="email_sweep", filename=f"day{d}.csv", rows=1)
AC.run_due(cl, now=later(6), runner=counting_runner)
check("E1 thirty daily files in a burst → ONE calculation", RUNS == [(ORG, SEPT)], RUNS)
check("E2 the request kept the first landing and the latest ones (capped), and said so on the record",
      len((status_row(st).get(AC.COL_LAST) or {}).get("landings") or []) == 30
      and "30 uploads" in (status_row(st).get(AC.COL_LAST) or {}).get("sentence", ""))
RUNS.clear()
aug = [line(ORG, "Jona Sejat", "A1", 20, "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "111", 150.0, month="08")]
res = land_intake(cl, aug + sept_lines()[:1], fname="aug_sep_correction.xlsx")
check("E3 one file spanning August and September queues BOTH months", (res.get("auto_calc") or {}).get("periods") == [AUG, SEPT],
      res.get("auto_calc"))
AC.run_due(cl, now=later(6), runner=counting_runner)
check("E4 …each calculated exactly once (the late correction to August included)",
      sorted(RUNS) == sorted([(ORG, AUG), (ORG, SEPT)]), RUNS)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── F. a refusal is recorded, never swallowed ──")
st_r = Store(base_tables())
for k in ("commcalc.commission_plan_assignment",):
    st_r.t[k] = []                                                        # nothing configured to pay from → R1
st_r.t["commcalc.rep_commissions"] = [{"id": "keep", "org_id": ORG, "period": SEPT, "epay_salesperson": "Jona Sejat",
                                       "total_payout": 42.0}]
cr = use(st_r)
RUNS.clear()
land_intake(cr, sept_lines())
AC.run_due(cr, now=later(6), runner=counting_runner)
last = status_row(st_r).get(AC.COL_LAST) or {}
check("F1 the unconfigured-tenant refusal (R1): outcome 'refused' with the calculation's own words",
      RUNS == [(ORG, SEPT)] and last.get("outcome") == "refused" and "REFUSED" in (last.get("message") or ""), last)
check("F2 calc_status says 'error' with the same message in save_errors (the dashboard's refusal banner)",
      status_row(st_r).get("calc_status") == "error" and "REFUSED" in str(status_row(st_r).get("save_errors")))
check("F3 the last good snapshot was KEPT (no delete, no zero rows written)",
      [r.get("total_payout") for r in st_r.t["commcalc.rep_commissions"]] == [42.0])
check("F4 the page sentence says it was refused and that the last good snapshot was kept",
      last.get("sentence", "").startswith("Auto-calculation refused at ") and "last good snapshot" in last.get("sentence", ""))

zero_rules = [dict(r, amount=0.0) for r in RULES]
tz = base_tables()
tz.update(org_tables(ORG, rules=zero_rules))
for k in ("commcalc.carrier", "commcalc.commission_plan", "commcalc.commission_rule",
          "commcalc.commission_plan_assignment", "commcalc.accessory_config"):
    tz[k] = [r for r in tz[k] if r.get("org_id") == ORG]
tz["commcalc.rep_commissions"] = [{"id": "keep", "org_id": ORG, "period": SEPT, "epay_salesperson": "Jona Sejat",
                                   "total_payout": 42.0}]
st_z = Store(tz)
cz = use(st_z)
RUNS.clear()
land_intake(cz, sept_lines())
AC.run_due(cz, now=later(6), runner=counting_runner)
lz = status_row(st_z).get(AC.COL_LAST) or {}
check("F5 the zero-wipe guard: an all-$0 result over a paying snapshot → 'refused', snapshot kept",
      lz.get("outcome") == "refused" and "zero-wipe" in (lz.get("message") or "")
      and [r.get("total_payout") for r in st_z.t["commcalc.rep_commissions"]] == [42.0], lz)

st_b = Store(base_tables())
cb = use(st_b)
RUNS.clear()
orig_acq = R._calc_guard_acquire
R._calc_guard_acquire = lambda *a, **k: (False, None, {"running_since": "2026-09-28T10:00:00Z", "stale_minutes": 20})
land_intake(cb, sept_lines())
AC.run_due(cb, now=later(6), runner=counting_runner)
lb = status_row(st_b).get(AC.COL_LAST) or {}
check("F6 a calculation already running (the single-flight guard) → 'busy', nothing written, the request RE-QUEUED",
      lb.get("outcome") == "busy" and not st_b.t.get("commcalc.rep_commissions")
      and status_row(st_b).get(AC.COL_REQUESTED), lb)
R._calc_guard_acquire = orig_acq
AC.run_due(cb, now=later(12), runner=counting_runner)
check("F7 …and once the slot is free it runs (the landed data is still calculated)",
      RUNS == [(ORG, SEPT), (ORG, SEPT)] and (status_row(st_b).get(AC.COL_LAST) or {}).get("outcome") == "calculated"
      and len(rep_rows(st_b)) == 2, RUNS)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── G. config off → no calculation, and the landing still says so ──")
for label, tenant, house, want_on in (("the tenant's own row off", {"auto_calc_on_landing": False}, None, False),
                                      ("inherited from the house row", None, {"auto_calc_on_landing": False}, False),
                                      ("tenant ON beats house OFF", {"auto_calc_on_landing": True},
                                       {"auto_calc_on_landing": False}, True)):
    st_g = Store(base_tables(tenant_cfg=tenant, house_cfg=house))
    cg = use(st_g)
    RUNS.clear()
    rg = land_intake(cg, sept_lines())
    AC.run_due(cg, now=later(6), runner=counting_runner)
    lg = status_row(st_g).get(AC.COL_LAST) or {}
    if want_on:
        check(f"G {label}: calculated", RUNS == [(ORG, SEPT)] and lg.get("outcome") == "calculated", (RUNS, lg))
    else:
        check(f"G {label}: NO calculation, nothing pending, the landing recorded as 'off' with the Run Calculation hint",
              RUNS == [] and not status_row(st_g).get(AC.COL_REQUESTED) and lg.get("outcome") == "off"
              and "press Run Calculation" in lg.get("sentence", "") and (rg.get("auto_calc") or {}).get("reason") == "off"
              and not st_g.t.get("commcalc.rep_commissions"), (RUNS, lg))

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── H. org-scoped ──")
st_h = Store(base_tables(extra={"commcalc.rep_commissions": [{"id": "b1", "org_id": ORG_B, "period": SEPT,
                                                              "epay_salesperson": "B Rep", "total_payout": 7.0}]}))
ch = use(st_h)
RUNS.clear()
land_intake(ch, sept_lines())
AC.run_due(ch, now=later(6), runner=counting_runner)
check("H1 a landing in org A calculates org A only; org B's request row does not exist and its rows are untouched",
      RUNS == [(ORG, SEPT)] and not status_row(st_h, ORG_B)
      and [r for r in st_h.t["commcalc.rep_commissions"] if r["org_id"] == ORG_B] == [
          {"id": "b1", "org_id": ORG_B, "period": SEPT, "epay_salesperson": "B Rep", "total_payout": 7.0}], RUNS)
claims = [w for w in st_h.writes if w[0] == "commcalc.calc_status" and w[1] == "update"]
check("H2 every claim the hook made is filtered by org_id and period",
      claims and all(("eq", "org_id", ORG) in w[3] and ("eq", "period", SEPT) in w[3] for w in claims), claims)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── I. migration 1030 not applied → the process-local queue ──")
st_i = Store(base_tables(), missing_cols=(AC.COL_REQUESTED, AC.COL_LANDINGS, AC.COL_LAST))
ci = use(st_i)
RUNS.clear()
ri = land_intake(ci, sept_lines())
land_intake(ci, sept_lines(), fname="again.xlsx")
AC.run_due(ci, now=later(2), runner=counting_runner)
none_yet = list(RUNS)
AC.run_due(ci, now=later(6), runner=counting_runner)
notes = [n for n in (status_row(st_i).get("calc_notices") or []) if n.get("type") == AC.NOTICE_TYPE]
check("I1 still queued (mode 'process'), still debounced, still exactly ONE calculation",
      (ri.get("auto_calc") or {}).get("mode") == "process" and none_yet == [] and RUNS == [(ORG, SEPT)], RUNS)
check("I2 the outcome is recorded as ONE calc_notices entry of type auto_calc (mig 247's column)",
      len(notes) == 1 and notes[0]["auto_calc"]["outcome"] == "calculated", notes)

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── J. what the Rep Incentive page reads ──")
st_j = Store(base_tables())
cj = use(st_j)
land_intake(cj, sept_lines(), fname="September sales.xlsx")
q = R.get_calc_status(SEPT, org_id=ORG)
check("J1 while queued: GET /calc-status says so, naming the upload and when it will run",
      (q.get("auto_calc") or {}).get("state") == "queued"
      and "the upload of september sales.xlsx (the onboarding intake)" in q["auto_calc"]["sentence"].lower()
      and "Auto-calculation is queued" in q["auto_calc"]["sentence"], q.get("auto_calc"))
AC.run_due(cj, now=later(6), runner=counting_runner)
q = R.get_calc_status(SEPT, org_id=ORG)
check("J2 after: 'Auto-calculated at … from the upload of September sales.xlsx (the onboarding intake) — 2 rep(s).'",
      (q.get("auto_calc") or {}).get("state") == "calculated" and q["auto_calc"]["tone"] == "ok"
      and q["auto_calc"]["sentence"].endswith("from the upload of September sales.xlsx (the onboarding intake) — 2 rep(s)."),
      q.get("auto_calc"))

# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("── N. negative controls ──")


def burst_runs(patch):
    st_n = Store(base_tables())
    cn = use(st_n)
    RUNS.clear()
    saved = {k: getattr(AC, k) for k in patch}
    try:
        for k, v in patch.items():
            setattr(AC, k, v)
        for d in range(5):
            AC.landed(cn, ORG, table="raw_sales", periods=[SEPT], source="manual", filename=f"d{d}.csv", rows=1)
            AC.run_due(cn, now=later(6), runner=counting_runner)
    finally:
        for k, v in saved.items():
            setattr(AC, k, v)
    return list(RUNS), st_n


runs_ok, _ = burst_runs({})
check("N0 the real hook: five landings each followed by a poller tick run once per landing (each is due) — "
      "the burst proof above is the quiet-window one", len(runs_ok) == 5, runs_ok)
RUNS.clear()
st_n2 = Store(base_tables())
cn2 = use(st_n2)
_orig_due = AC.is_due
AC.is_due = lambda *a, **k: True
try:
    for d in range(30):
        AC.landed(cn2, ORG, table="raw_sales", periods=[SEPT], source="manual", filename=f"d{d}.csv", rows=1)
        AC.run_due(cn2, now=now(), runner=counting_runner)
finally:
    AC.is_due = _orig_due
check("N1 a hook WITHOUT the quiet window runs the 30-file burst 30 times (E1 would be RED)", len(RUNS) == 30, len(RUNS))
_orig_cls = AC.classify_run
AC.classify_run = lambda r: ("calculated", "")
try:
    st_n3 = Store(base_tables())
    st_n3.t["commcalc.commission_plan_assignment"] = []
    cn3 = use(st_n3)
    land_intake(cn3, sept_lines())
    AC.run_due(cn3, now=later(6), runner=counting_runner)
    swallowed = (status_row(st_n3).get(AC.COL_LAST) or {}).get("outcome")
finally:
    AC.classify_run = _orig_cls
check("N2 a hook that records a refusal as a success → F1 would be RED (it says 'calculated')", swallowed == "calculated")
_orig_load = AC.load_config
AC.load_config = lambda client, org_id: AC.resolve_config(None, None)
try:
    st_n4 = Store(base_tables(tenant_cfg={"auto_calc_on_landing": False}))
    cn4 = use(st_n4)
    RUNS.clear()
    land_intake(cn4, sept_lines())
    AC.run_due(cn4, now=later(6), runner=counting_runner)
    ignored = list(RUNS)
finally:
    AC.load_config = _orig_load
check("N3 a hook that ignores the org's config calculates an OFF tenant → G would be RED", ignored == [(ORG, SEPT)], ignored)

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
