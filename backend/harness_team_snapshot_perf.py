"""Offline proof harness — agent/commission/team-snapshot-perf.

INCIDENT (2026-07-30, owner: "My Team -> Employees still loading after 4 min", masked ref 3bf51b4d,
failure_log message confirmed verbatim: "Unhandled server error [3bf51b4d] on
/api/v1/commcalc/team/July 2026/snapshot"). Live probes showed the WHOLE backend starved during the
episode (public /health 21.5s, recovered to ~0.1s after) -> the shared uvicorn worker was blocked.

`GET /commcalc/team/{period}/snapshot` is the ONLY fetch gating the My-Team page's spinner and it
recomputed, on EVERY load, with ZERO caching:
  * `get_targets_summary` for the WHOLE ORG (sales union + `_compute_feed_actuals_py` + `_exec_mtd`
    for trending + a nested store x rep Python pass), then threw away every store outside the span;
  * `rep_coaching` for the WHOLE ORG (+ `_kpi_defs` called once PER REP -> an N+1 read), then threw
    away every rep outside the span.

THIS PACKAGE (read/display path only — no rate, tier, plan rule, payout number or calc input is
written or altered anywhere in it):
  T1  span PUSHDOWN — team_snapshot passes its already-resolved span into `get_targets_summary(stores=)`
      (the param existed and was never passed) and into `rep_coaching(store=[...])` (widened to accept
      a list, single-value behaviour unchanged). The old Python span filters are KEPT, so the response
      is byte-identical; the pushdown only stops work that was going to be discarded.
  T2  TTL MEMO of the heavy pair per (org_id, CANONICAL period, today, span) — the owner's case, where
      T1 is a no-op because their span IS the org. Keyed on PLAIN VALUES only (never on the
      get_supabase() singleton), `time.monotonic()`, bounded, deep-copied in and out, busted by a
      finished recompute and by `?refresh=1`.
  T3  incident hardening in the same functions: period normalization (both spellings -> one canonical
      'Month YYYY', which is what `_period_bounds` requires; unparseable -> clean 400, never a 500) and
      `_kpi_defs` hoisted out of rep_coaching's per-rep loop (kills the N+1 read AND the
      `kpi_targets[k]` KeyError-500 class it created).

Every behavioural claim is proven DIFFERENTIALLY against the real pre-change code: the harness
extracts `backend/app/modules/commcalc/router.py` at the base commit with `git show` and imports it as
a second module, so OLD and NEW run over the SAME fixture and their payloads are compared as
sort_keys JSON.

No DB / no network: an in-memory fake Supabase client that (a) counts per-table READS, (b) RAISES on
every write verb (insert/update/upsert/delete) so "read-side only" is proven rather than asserted.

Run:  cd backend && python3 harness_team_snapshot_perf.py
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import os
import sys
import json
import copy
import time
import asyncio
import subprocess
import importlib.util
import calendar as _cal
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_COMMIT = os.environ.get("HARNESS_BASE", "b54a3f3")
ROUTER_REL = "backend/app/modules/commcalc/router.py"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"\n        [{detail}]" if detail and not cond else ""))


def j(x):
    return json.dumps(x, sort_keys=True, default=str)


# ══ the fake Supabase client ══════════════════════════════════════════════════════════════════════
class WriteAttempted(Exception):
    pass


class FakeQuery:
    def __init__(self, client, table):
        self.c, self.t = client, table
        self.f, self.rng, self._lim = [], None, None

    def select(self, *a, **k):
        return self

    # every write verb raises: this endpoint chain must be READ-ONLY
    def insert(self, *a, **k):
        raise WriteAttempted(f"INSERT attempted on {self.t}")

    def update(self, *a, **k):
        raise WriteAttempted(f"UPDATE attempted on {self.t}")

    def upsert(self, *a, **k):
        raise WriteAttempted(f"UPSERT attempted on {self.t}")

    def delete(self, *a, **k):
        raise WriteAttempted(f"DELETE attempted on {self.t}")

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def gt(self, c, v):
        self.f.append(("gt", c, v)); return self

    def gte(self, c, v):
        self.f.append(("gte", c, v)); return self

    def lt(self, c, v):
        self.f.append(("lt", c, v)); return self

    def lte(self, c, v):
        self.f.append(("lte", c, v)); return self

    def is_(self, c, v):
        self.f.append(("is", c, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, n, *a, **k):
        self._lim = n; return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def _m(self, r):
        for kind, c, v in self.f:
            rv = r.get(c)
            if kind == "eq" and rv != v:
                return False
            if kind == "neq" and rv == v:
                return False
            if kind == "in" and rv not in v:
                return False
            if kind == "gt" and not (rv is not None and str(rv) > str(v)):
                return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if kind == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if kind == "is" and v in ("null", None) and rv is not None:
                return False
        return True

    def execute(self):
        self.c.reads[self.t] = self.c.reads.get(self.t, 0) + 1
        if self.t in self.c.raise_on:
            self.c.raise_on[self.t] -= 1
            if self.c.raise_on[self.t] < 0:
                del self.c.raise_on[self.t]
            else:
                raise RuntimeError(f"simulated read failure on {self.t}")
        rows = self.c.store.setdefault(self.t, [])
        m = [copy.deepcopy(r) for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        elif self._lim is not None:
            m = m[: self._lim]
        return type("R", (), {"data": m, "count": len(m)})()


class FakeSchema:
    def __init__(self, client):
        self.c = client

    def table(self, t):
        return FakeQuery(self.c, t)

    def rpc(self, name, *a, **k):
        raise RuntimeError(f"unexpected rpc {name}")


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.reads = {}
        self.raise_on = {}

    def schema(self, _n):
        return FakeSchema(self)

    def table(self, t):
        return FakeQuery(self, t)

    def rpc(self, name, *a, **k):
        raise RuntimeError(f"unexpected rpc {name}")


# ══ load NEW (this tree) and OLD (base commit) copies of the router ═══════════════════════════════
import app.modules.commcalc.router as NEW           # noqa: E402
import app.modules.storeops.router as SO            # noqa: E402


def _load_base_router():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, ".."))
    src = subprocess.run(["git", "-C", repo, "show", f"{BASE_COMMIT}:{ROUTER_REL}"],
                         capture_output=True, text=True)
    if src.returncode != 0:
        raise SystemExit(f"cannot extract {BASE_COMMIT}:{ROUTER_REL} — {src.stderr.strip()}")
    path = os.path.join(here, "scratchpad", "_ts_base_router.py")
    with open(path, "w") as fh:
        fh.write(src.stdout)
    spec = importlib.util.spec_from_file_location("commcalc_router_base", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["commcalc_router_base"] = mod
    spec.loader.exec_module(mod)
    os.remove(path)
    return mod


OLD = _load_base_router()

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-0000000000ff"

_T = _date.today()
YM = f"{_T.year}-{_T.month:02d}"                    # feed/raw_sales period spelling
PERIOD = f"{_cal.month_name[_T.month]} {_T.year}"   # the API period label the platform selector emits
TODAY = _date(_T.year, _T.month, min(10, _cal.monthrange(_T.year, _T.month)[1]))
TODAY_ISO = TODAY.isoformat()
D05, D08 = f"{YM}-05", f"{YM}-08"

# 6 stores: the manager's span is S1+S2, the org has S1..S6 (so the pushdown has something to skip).
SPAN_CODES = ["S1", "S2"]
ALL_CODES = ["S1", "S2", "S3", "S4", "S5", "S6"]
ADDR = {c: f"{100 + i} Main St" for i, c in enumerate(ALL_CODES)}
MARKET = {c: ("North" if c in ("S1", "S2", "S3") else "South") for c in ALL_CODES}
REPS = {c: [f"{c}-ALICE", f"{c}-BOB"] for c in ALL_CODES}


def sale(store, rep, tid, ct, day=D05, cat="CellPhone", ext=100.0, org=HOUSE, period=YM):
    return {"org_id": org, "period": period, "trans_id": tid, "trans_date": day, "store": store,
            "salesperson": rep, "user_login": (rep or "").lower(), "category": cat, "department": "",
            "contract_type": ct, "product_desc": "", "ext_price": ext, "gp": 20.0,
            "voided": "", "trans_type": ""}


def fixture(org=HOUSE, codes=None, kpi_metric_rows=None):
    codes = codes or ALL_CODES
    feed, comms = [], []
    for i, c in enumerate(codes):
        for k, rep in enumerate(REPS[c]):
            feed.append(sale(ADDR[c], rep, f"{c}A{k}", "Activation", org=org, ext=100.0 + i))
            feed.append(sale(ADDR[c], rep, f"{c}U{k}", "Upgrade", org=org, ext=100.0))
            feed.append(sale(ADDR[c], rep, f"{c}C{k}", "", day=D08, cat="Accessory", org=org, ext=40.0 + k))
            comms.append({
                "org_id": org, "period": YM, "storeops_name": rep, "epay_salesperson": rep.upper(),
                "store": ADDR[c], "tier": 0.8 if k == 0 else 1.0, "subtotal": 1000.0 + 10 * i,
                "kpi_values": {"atu": 60, "protect": 70, "boostapp": 50, "familyplan": 40,
                               "byod": 30, "tmr3": 80, "aal": 3},
                "kpis_met": None, "total_kpis": 7,
                "total_payout": 1200.0 + 10 * i, "final_payout": None,
            })
    return {
        "daily_sales_feed": feed,
        "raw_sales": [],
        "accessory_config": [{"org_id": org, "departments": [], "categories": ["accessory"],
                              "product_keywords": [], "acima_tenders": []}],
        "store_mapping": [{"org_id": org, "store_address": ADDR[c], "store_code": c,
                           "market": MARKET[c]} for c in codes],
        "stores": [{"org_id": org, "store_code": c, "address": ADDR[c], "market": MARKET[c],
                    "monthly_target": 0} for c in codes],
        "targets": [{"org_id": org, "period": YM, "store_code": c, "activations_monthly": 10,
                     "upgrades_monthly": 5, "accessories_monthly": 1000} for c in codes],
        "shifts": [{"org_id": org, "employee_name": rep, "store_code": c, "shift_date": D05,
                    "scheduled_hours": 8, "is_deleted": False} for c in codes for rep in REPS[c]],
        "rep_commissions": comms,
        "payout_config": [{"org_id": org, "period": YM, "tier_100_min_kpis": 7}],
        "chargeback_items": [], "flags": [], "ops_chargeback": [], "ops_chargeback_policy": [],
        "carrier_kpi_metric": kpi_metric_rows or [],
        "exec_metric_config": [], "name_map": [], "rep_aliases": [], "store_aliases": [],
        "app_config": [], "report_definitions": [], "chargeback_review": [],
        # /exec-overview staples a P&L headline on, keyed 'YYYY-MM'. Two months on file so the old
        # code's parse_period('2026-07')->JANUARY mis-resolution is observable (section 9).
        "account_statements": [
            {"org_id": org, "period": f"{_T.year}-01", "statement_type": "pl",
             "scope_key": "consolidated",
             "payload": {"revenue": 1.0, "gross_profit": 1.0, "net_income": 1.0}},
            {"org_id": org, "period": YM, "statement_type": "pl", "scope_key": "consolidated",
             "payload": {"revenue": 777.0, "gross_profit": 333.0, "net_income": 111.0}},
        ],
    }


def merge(*stores):
    out = {}
    for s in stores:
        for k, v in s.items():
            out.setdefault(k, [])
            out[k] = out[k] + copy.deepcopy(v)
    return out


# ══ wiring: point both router copies at the fake client + stub the storeops span helpers ══════════
def wire(store, span=None, unrestricted=False):
    """Returns the fake client. `span` = the store_codes the signed-in caller manages (None -> all)."""
    fake = FakeClient(store)
    for mod in (NEW, OLD):
        mod.sb = lambda _f=fake: _f
        mod.get_supabase = lambda _f=fake: _f
    SO.sb = lambda _f=fake: _f
    codes = list(ALL_CODES if span is None else span)
    SO._caller_span_codes = lambda authorization="", org_id=HOUSE, _c=codes: list(_c)
    SO._unit_store_codes = lambda org_id, unit_id, _c=codes: list(_c)
    SO.caller_scope = lambda authorization="", org_id=HOUSE: None
    SO.scope_keyset = lambda authorization="", org_id=HOUSE: None
    for mod in (NEW, OLD):
        mod._caller_self_keyset = lambda authorization="", org_id=HOUSE: (False, None)
    return fake


def snap(mod, period=PERIOD, org=HOUSE, today=TODAY_ISO, **kw):
    return run_route(mod.team_snapshot(period=period, authorization="", today=today,
                                         org_id=org, **kw))


print(f"\nbase = {BASE_COMMIT} · period = {PERIOD!r} · today = {TODAY_ISO}\n")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 0. ROOT CAUSE — the pre-change endpoint 500s on EVERY call, with the exact period from the log
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── 0. root cause: the pre-change /team/{period}/snapshot cannot succeed at all ───────────")
st = fixture()
wire(st, span=SPAN_CODES)
old_err = None
try:
    snap(OLD, period=PERIOD)
except Exception as e:
    old_err = e
check("0.1 OLD /commcalc/team/'July 2026'/snapshot raises TypeError: 'Query' object is not iterable "
      "-> the unhandled 500 behind masked ref 3bf51b4d (NOT a period-spelling failure: 'July 2026' is "
      "exactly the spelling _period_bounds requires)",
      isinstance(old_err, TypeError) and "Query" in str(old_err) and "iterable" in str(old_err),
      f"{type(old_err).__name__}: {old_err}")
_tb = old_err.__traceback__
_frames = []
while _tb:
    _frames.append((os.path.basename(_tb.tb_frame.f_code.co_filename), _tb.tb_frame.f_code.co_name))
    _tb = _tb.tb_next
check("0.2 the raising frame is get_targets_summary, reached from team_snapshot (the internal call "
      "that omits the repeated-Query filter params)",
      ("_ts_base_router.py", "get_targets_summary") in _frames
      and ("_ts_base_router.py", "team_snapshot") in _frames, str(_frames))
check("0.3 mechanism: fastapi.params.Query(default=None) is TRUTHY and NOT iterable, so "
      "`for x in (stores or [])` iterates the Query object itself",
      bool(NEW.Query(default=None)) is True, "Query default was falsy?!")

st = fixture()
wire(st, span=SPAN_CODES)
old_ym_err = None
try:
    snap(OLD, period=YM)
except Exception as e:
    old_ym_err = e
check("0.4 OLD also rejects the OTHER spelling ('2026-07') with a 400 from _period_bounds before it "
      "even gets that far (the period-duality gap for a machine caller)",
      getattr(old_ym_err, "status_code", None) == 400,
      f"{type(old_ym_err).__name__}: {old_ym_err}")

old_blank_err = None
try:
    OLD._canon_period("")
except Exception as e:
    old_blank_err = e
check("0.5 OLD _canon_period('') raises IndexError (calculator.parse_period indexes parts[0]) — the "
      "unguarded-period 500 class the new normalizer closes",
      isinstance(old_blank_err, IndexError), repr(old_blank_err))

st = fixture()
wire(st, span=SPAN_CODES)
NEW._team_snap_invalidate()
new_ok = snap(NEW, period=PERIOD)
check("0.6 NEW: the same call now SUCCEEDS and returns the manager's span",
      new_ok.get("is_manager") is True
      and sorted(s["store_code"] for s in new_ok["stores"]) == SPAN_CODES,
      j(new_ok)[:400])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 0b. The differential REFERENCE. Since OLD.team_snapshot cannot complete, the reference for every
#     byte-identity claim below is the OLD module with the ONE defect neutralized: its own
#     get_targets_summary, called with the three repeated-Query params passed explicitly as None
#     (i.e. exactly what the old code MEANT to do — whole-org aggregation, then filter in Python).
#     Everything else in the old body — span resolution, the address keyset, both Python filters,
#     _team_totals, the rounding, the key order — runs unmodified.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_OLD_GTS_RAW = OLD.get_targets_summary


async def _old_gts_params_fixed(period, today="", include_untargeted=False, stores=None,
                                markets=None, reps=None, authorization="", org_id=HOUSE):
    return await _OLD_GTS_RAW(period, today=today, include_untargeted=include_untargeted,
                              stores=stores, markets=markets, reps=reps,
                              authorization=authorization, org_id=org_id)


OLD.get_targets_summary = _old_gts_params_fixed
st = fixture()
wire(st, span=SPAN_CODES)
ref = snap(OLD, period=PERIOD)
check("0.7 REFERENCE established: OLD's own body, with only the missing Query params supplied, "
      "produces the manager payload the old code intended",
      ref.get("is_manager") is True and len(ref["stores"]) == 2, j(ref)[:300])

# ── the N+1 that also made the endpoint slow AND could 500: _kpi_defs called once per rep ─────────
st = fixture(kpi_metric_rows=[
    {"org_id": HOUSE, "carrier_id": NEW._KPI_DEFAULT_CARRIER, "metric_key": k, "label": lab,
     "payout_config_col": col, "target_default": dv, "is_active": True, "sort": i}
    for i, (k, lab, col, dv) in enumerate(NEW.ACTION_KPI_DEFS)])
f_old = wire(st, span=None)
OLD.rep_coaching(period=PERIOD, org_id=HOUSE)
old_kpi_reads = f_old.reads.get("carrier_kpi_metric", 0)
n_reps = len(st["rep_commissions"])
check(f"0.8 OLD rep_coaching reads commcalc.carrier_kpi_metric ONCE PER REP (N+1): "
      f"{old_kpi_reads} reads for {n_reps} reps",
      old_kpi_reads >= n_reps, f"{old_kpi_reads} reads / {n_reps} reps")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. TIER 1 — span pushdown is BYTE-IDENTICAL for a manager-scoped caller
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. TIER 1 span pushdown — byte identity + measured work reduction ─────────────────────")
st_o = fixture(); f_o = wire(st_o, span=SPAN_CODES)
r_old = snap(OLD, period=PERIOD)
st_n = fixture(); f_n = wire(st_n, span=SPAN_CODES)
NEW._team_snap_invalidate()
r_new = snap(NEW, period=PERIOD)
check("1.1 manager-scoped snapshot payload is BYTE-IDENTICAL to the reference (sort_keys JSON): the "
      "pushdown changes WHERE the span filter is applied, never the result",
      j(r_old) == j(r_new),
      f"old={j(r_old)[:500]}\n         new={j(r_new)[:500]}")
check("1.2 the payload really is the manager's span only (2 of 6 stores, both in span)",
      sorted(s["store_code"] for s in r_new["stores"]) == SPAN_CODES, j(r_new["stores"])[:300])
check("1.3 and it still carries the per-rep coaching rollup + money_on_table",
      len(r_new["reps"]) == 4 and r_new["money_on_table"] == r_old["money_on_table"],
      f"{len(r_new['reps'])} reps · {r_new['money_on_table']} vs {r_old['money_on_table']}")

# measured work reduction: how many stores each side ran the per-store x rep pass over
_seen_old, _seen_new = [], []
_orig_scope_conv = NEW.targets_engine.scope_conversion


def _count_scope(seen):
    def f(actuals, code, rep, today, *a, **k):
        if rep is None:
            seen.append(code)
        return _orig_scope_conv(actuals, code, rep, today, *a, **k)
    return f


NEW.targets_engine.scope_conversion = _count_scope(_seen_new)
st_n = fixture(); wire(st_n, span=SPAN_CODES); NEW._team_snap_invalidate()
snap(NEW, period=PERIOD)
NEW.targets_engine.scope_conversion = _count_scope(_seen_old)
st_o = fixture(); wire(st_o, span=SPAN_CODES)
snap(OLD, period=PERIOD)
NEW.targets_engine.scope_conversion = _orig_scope_conv
check(f"1.4 the per-store x rep pass now runs over the SPAN only: {len(_seen_new)} stores (new) vs "
      f"{len(_seen_old)} (old, whole org)",
      len(_seen_new) == 2 and len(_seen_old) == 6, f"new={_seen_new} old={_seen_old}")

# rep_coaching pushdown: the coaching aggregation no longer builds the whole org's rep rows
st_o = fixture(); wire(st_o, span=SPAN_CODES)
old_coach_all = OLD.rep_coaching(period=PERIOD, org_id=HOUSE)
st_n = fixture(); wire(st_n, span=SPAN_CODES)
keys = sorted({c for c in SPAN_CODES} | {ADDR[c].upper() for c in SPAN_CODES})
new_coach_span = NEW.rep_coaching(period=PERIOD, store=keys, org_id=HOUSE)
check(f"1.5 rep_coaching(store=[span]) builds only the span's reps "
      f"({len(new_coach_span['reps'])}) instead of the whole org's ({len(old_coach_all['reps'])})",
      len(new_coach_span["reps"]) == 4 and len(old_coach_all["reps"]) == 12,
      f"{len(new_coach_span['reps'])} vs {len(old_coach_all['reps'])}")
old_filtered = [r for r in old_coach_all["reps"] if str(r.get("store") or "").strip().upper() in set(keys)]
check("1.6 and those rep rows are BYTE-IDENTICAL to the rows the old Python post-filter kept",
      j(old_filtered) == j(new_coach_span["reps"]),
      f"old={j(old_filtered)[:400]}\n         new={j(new_coach_span['reps'])[:400]}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. rep_coaching single-value backward compatibility (store / market / rep)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. rep_coaching store/market widened to lists — single-value behaviour unchanged ──────")
cases = [
    ("no filter", {}),
    ("store=<address>", {"store": ADDR["S1"]}),
    ("store=<address> lowercase (old code upper()s both sides)", {"store": ADDR["S1"].lower()}),
    ("store=<no such store>", {"store": "nowhere"}),
    ("market=North", {"market": "North"}),
    ("market=<no such market>", {"market": "Atlantis"}),
    ("rep=<one rep>", {"rep": REPS["S1"][0]}),
    ("store+market+rep together", {"store": ADDR["S1"], "market": "North", "rep": REPS["S1"][1]}),
    ("market=<blank string> (falsy -> no filter, as before)", {"market": ""}),
    ("store=<blank string> (falsy -> no filter, as before)", {"store": ""}),
]
for label, kw in cases:
    st_o = fixture(); wire(st_o, span=None)
    a = OLD.rep_coaching(period=PERIOD, org_id=HOUSE, **kw)
    st_n = fixture(); wire(st_n, span=None)
    b = NEW.rep_coaching(period=PERIOD, org_id=HOUSE, **kw)
    check(f"2.x rep_coaching byte-identical old vs new — {label}", j(a) == j(b),
          f"old={j(a)[:300]}\n         new={j(b)[:300]}")

st_n = fixture(); wire(st_n, span=None)
multi = NEW.rep_coaching(period=PERIOD, store=[ADDR["S1"], ADDR["S2"]], org_id=HOUSE)
check("2.11 NEW: a repeated store list returns the UNION of those stores' reps (the new capability)",
      sorted({r["store"] for r in multi["reps"]}) == sorted([ADDR["S1"], ADDR["S2"]]),
      j(sorted({r["store"] for r in multi["reps"]})))
st_n = fixture(); wire(st_n, span=None)
multi_m = NEW.rep_coaching(period=PERIOD, market=["North", "South"], org_id=HOUSE)
check("2.12 NEW: a repeated market list returns the union of those markets",
      len(multi_m["reps"]) == 12, str(len(multi_m["reps"])))
st_n = fixture(); wire(st_n, span=None)
summ_multi = NEW.rep_coaching(period=PERIOD, store=[ADDR["S1"], ADDR["S2"]], org_id=HOUSE)["summary"]
check("2.13 NEW: the summary tiles are recomputed over the FILTERED rep set (not the whole org)",
      summ_multi["reps"] == 4, j(summ_multi))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. TIER 3a — period normalization: both spellings, one payload; garbage -> 400 not 500
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. period normalization (the failure_log spelling) ────────────────────────────────────")
st = fixture(); wire(st, span=SPAN_CODES); NEW._team_snap_invalidate()
new_name = snap(NEW, period=PERIOD)
st = fixture(); wire(st, span=SPAN_CODES); NEW._team_snap_invalidate()
new_ym = snap(NEW, period=YM)
check("3.1 NEW: '2026-07' now returns a payload BYTE-IDENTICAL to 'July 2026' except the echoed "
      "`period` (the caller's own spelling is echoed back unchanged)",
      j({k: v for k, v in new_name.items() if k != "period"}) ==
      j({k: v for k, v in new_ym.items() if k != "period"}),
      f"name={j(new_name)[:400]}\n         ym={j(new_ym)[:400]}")
check("3.2 NEW: the echoed `period` is the caller's own spelling (no surprise rewrite in the payload)",
      new_name["period"] == PERIOD and new_ym["period"] == YM,
      f"{new_name['period']!r} / {new_ym['period']!r}")
check("3.3 NEW: 'July 2026' is unchanged from OLD (identity on the real input) — see 1.1", True)

for bad in ("", "banana", "Jul 2026", "2026-13", "13/2026", "None"):
    st = fixture(); wire(st, span=SPAN_CODES); NEW._team_snap_invalidate()
    err = None
    try:
        snap(NEW, period=bad)
    except Exception as e:
        err = e
    check(f"3.x NEW: unparseable period {bad!r} -> clean 400 (never a 500 / IndexError)",
          getattr(err, "status_code", None) == 400 and "expected" in str(getattr(err, "detail", "")).lower(),
          f"{type(err).__name__}: {getattr(err, 'detail', err)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. TIER 2 — TTL memo: identity, hit/miss, refresh, canonical key, org isolation, bounds
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. TIER 2 TTL memo (the owner's full-org case, where TIER 1 is a no-op) ───────────────")
st_o = fixture(); wire(st_o, span=None)
full_old = snap(OLD, period=PERIOD)
st_n = fixture(); f_n = wire(st_n, span=None); NEW._team_snap_invalidate()
full_new_1 = snap(NEW, period=PERIOD)
reads_miss = dict(f_n.reads)
check("4.1 FULL-ORG (owner) snapshot: first (uncached) NEW call is BYTE-IDENTICAL to the reference",
      j(full_old) == j(full_new_1),
      f"old={j(full_old)[:400]}\n         new={j(full_new_1)[:400]}")
f_n.reads.clear()
full_new_2 = snap(NEW, period=PERIOD)
reads_hit = dict(f_n.reads)
check("4.2 the CACHED call returns the byte-identical payload", j(full_new_1) == j(full_new_2),
      f"1st={j(full_new_1)[:400]}\n         2nd={j(full_new_2)[:400]}")
heavy = ("daily_sales_feed", "raw_sales", "targets", "shifts", "rep_commissions",
         "carrier_kpi_metric", "flags", "chargeback_items", "payout_config", "store_mapping")
miss_heavy = sum(reads_miss.get(t, 0) for t in heavy)
hit_heavy = sum(reads_hit.get(t, 0) for t in heavy)
check(f"4.3 the cached call does ZERO of the heavy reads ({hit_heavy} vs {miss_heavy} on the miss)",
      hit_heavy == 0 and miss_heavy > 0, f"miss={reads_miss}\n         hit={reads_hit}")
check(f"4.4 total reads collapse on a hit: {sum(reads_hit.values())} vs {sum(reads_miss.values())}",
      sum(reads_hit.values()) < sum(reads_miss.values()),
      f"{reads_hit} vs {reads_miss}")

# mutating the returned payload must not poison the cache
full_new_2["stores"].clear()
full_new_2["totals"]["poison"] = True
full_new_3 = snap(NEW, period=PERIOD)
check("4.5 mutating a returned payload cannot poison the cache (deep-copied in AND out)",
      j(full_new_3) == j(full_new_1), j(full_new_3)[:400])

# refresh=1 bypasses
f_n.reads.clear()
full_refresh = snap(NEW, period=PERIOD, refresh=True)
check("4.6 ?refresh=1 recomputes (heavy reads happen again) and returns the identical payload",
      j(full_refresh) == j(full_new_1) and sum(f_n.reads.get(t, 0) for t in heavy) > 0,
      f"{dict(f_n.reads)}")

# post-TTL expiry recomputes to the identical payload
key = list(NEW._team_snap_memo.keys())[0]
exp, val = NEW._team_snap_memo[key]
NEW._team_snap_memo[key] = (time.monotonic() - 1.0, val)     # force expiry
f_n.reads.clear()
full_expired = snap(NEW, period=PERIOD)
check("4.7 after the TTL expires the recomputed payload is byte-identical (and the heavy reads run)",
      j(full_expired) == j(full_new_1) and sum(f_n.reads.get(t, 0) for t in heavy) > 0,
      f"{j(full_expired)[:300]} · {dict(f_n.reads)}")

# the cache key: plain values only, NEVER the client object (get_supabase() is a process singleton)
check("4.8 every cache key is made of PLAIN values (str/tuple) — no client object, so an entry can "
      "never live for the whole process lifetime",
      all(isinstance(k, tuple) and all(isinstance(p, (str, tuple)) for p in k)
          for k in NEW._team_snap_memo),
      str(list(NEW._team_snap_memo.keys()))[:400])
check("4.9 the TTL is stored as a time.monotonic() deadline (unaffected by wall-clock changes)",
      all(isinstance(v[0], float) and v[0] <= time.monotonic() + NEW._TEAM_SNAP_TTL + 1
          for v in NEW._team_snap_memo.values()),
      str([v[0] for v in NEW._team_snap_memo.values()]))

# a NEW client object (the singleton being replaced, e.g. after a reconnect) still hits the same key
before_keys = set(NEW._team_snap_memo.keys())
f_other = wire(st_n, span=None)
f_other.reads.clear()
snap(NEW, period=PERIOD)
check("4.10 swapping the Supabase client object does NOT create a second cache entry (keyed on "
      "values, not on the client)",
      set(NEW._team_snap_memo.keys()) == before_keys,
      str(set(NEW._team_snap_memo.keys()) ^ before_keys))

# canonical period key: both spellings share ONE entry
NEW._team_snap_invalidate()
st_n = fixture(); f_n = wire(st_n, span=None)
snap(NEW, period=PERIOD)
n_after_name = len(NEW._team_snap_memo)
f_n.reads.clear()
snap(NEW, period=YM)
check("4.11 'July 2026' and '2026-07' SHARE one cache entry (keyed on the canonical spelling) — the "
      "second spelling is a HIT, not a second full computation",
      len(NEW._team_snap_memo) == n_after_name == 1
      and sum(f_n.reads.get(t, 0) for t in heavy) == 0,
      f"{len(NEW._team_snap_memo)} entries · reads={dict(f_n.reads)}")

# per-span keying: a manager and the owner must not share an entry
NEW._team_snap_invalidate()
st_n = fixture(); wire(st_n, span=None)
owner_payload = snap(NEW, period=PERIOD)
wire(st_n, span=SPAN_CODES)
mgr_payload = snap(NEW, period=PERIOD)
check("4.12 a manager's span gets its OWN entry — the owner's full-org payload is never served to a "
      "manager (2 entries, different store sets)",
      len(NEW._team_snap_memo) == 2 and len(owner_payload["stores"]) == 6
      and len(mgr_payload["stores"]) == 2,
      f"{len(NEW._team_snap_memo)} entries · {len(owner_payload['stores'])}/{len(mgr_payload['stores'])}")

# multi-tenant isolation, through the cache
NEW._team_snap_invalidate()
both = merge(fixture(HOUSE), fixture(OTHER))
wire(both, span=None)
h1 = snap(NEW, period=PERIOD, org=HOUSE)
o1 = snap(NEW, period=PERIOD, org=OTHER)
h2 = snap(NEW, period=PERIOD, org=HOUSE)
check("4.13 two tenants in ONE fixture: each org gets its own cache entry and its own payload; the "
      "house payload is stable across the other tenant's call (no cross-tenant serve)",
      j(h1) == j(h2) and len(NEW._team_snap_memo) == 2, f"{len(NEW._team_snap_memo)} entries")
check("4.14 org keys are distinct in the memo (org_id is the first key element)",
      {k[0] for k in NEW._team_snap_memo} == {HOUSE, OTHER},
      str({k[0] for k in NEW._team_snap_memo}))
check("4.15 the other tenant's snapshot is non-empty and structurally the same (isolation, not "
      "emptiness — both orgs have their own stores)",
      len(o1["stores"]) == 6 and len(h1["stores"]) == 6,
      f"{len(o1['stores'])}/{len(h1['stores'])}")

# invalidation on a finished recompute (period-spelling tolerant) + org scoping
NEW._team_snap_invalidate()
wire(both, span=None)
snap(NEW, period=PERIOD, org=HOUSE)
snap(NEW, period=PERIOD, org=OTHER)
NEW._team_snap_invalidate(HOUSE, YM)          # a calc that ran as '2026-07'
check("4.16 a finished recompute for org+period drops THAT org's entry even when the calc used the "
      "other period spelling ('2026-07' busts the 'July 2026' entry)",
      {k[0] for k in NEW._team_snap_memo} == {OTHER}, str(list(NEW._team_snap_memo.keys()))[:300])
NEW._team_snap_invalidate(OTHER, "January 1999")
check("4.17 invalidating a DIFFERENT period leaves the entry alone",
      {k[0] for k in NEW._team_snap_memo} == {OTHER}, str(list(NEW._team_snap_memo.keys()))[:300])
NEW._team_snap_invalidate(OTHER, PERIOD)
check("4.18 invalidating the right org+period drops it", NEW._team_snap_memo == {},
      str(NEW._team_snap_memo)[:200])
NEW._team_snap_invalidate(None, None)
check("4.19 _team_snap_invalidate() with no args clears everything (deploy/ops escape hatch)",
      NEW._team_snap_memo == {})
check("4.20 invalidate is tolerant of an unparseable period (never raises into a calc)",
      NEW._team_snap_invalidate(HOUSE, "") is None
      and NEW._team_snap_invalidate(HOUSE, None) is None)

# bounded
NEW._team_snap_invalidate()
for i in range(NEW._TEAM_SNAP_MAX + 25):
    NEW._team_snap_put(NEW._team_snap_key(HOUSE, f"M{i} 2026", TODAY_ISO, (f"S{i}",)), [{}, {}])
check(f"4.21 the memo is BOUNDED (<= {NEW._TEAM_SNAP_MAX} entries; cleared wholesale rather than "
      f"grown without limit)", len(NEW._team_snap_memo) <= NEW._TEAM_SNAP_MAX,
      str(len(NEW._team_snap_memo)))
NEW._team_snap_invalidate()
check("4.22 the TTL is ~900s (the platform-core _DERIVE_TTL precedent), not an unbounded cache",
      850.0 <= NEW._TEAM_SNAP_TTL <= 950.0, str(NEW._TEAM_SNAP_TTL))

# a caller outside any span still short-circuits BEFORE any heavy work (and is never cached wrong)
st_n = fixture(); f_n = wire(st_n, span=[])
NEW._team_snap_invalidate()
nospan = snap(NEW, period=PERIOD)
check("4.23 a caller with no span still gets the is_manager=False payload and does ZERO heavy work",
      nospan["is_manager"] is False and sum(f_n.reads.get(t, 0) for t in heavy) == 0,
      f"{j(nospan)[:200]} · {dict(f_n.reads)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. TIER 3b — _kpi_defs hoisted out of the per-rep loop: N+1 gone, KeyError-500 class gone
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. _kpi_defs N+1 + the kpi_targets[k] KeyError-500 class ──────────────────────────────")
kpi_rows = [{"org_id": HOUSE, "carrier_id": NEW._KPI_DEFAULT_CARRIER, "metric_key": k, "label": lab,
             "payout_config_col": col, "target_default": dv, "is_active": True, "sort": i}
            for i, (k, lab, col, dv) in enumerate(NEW.ACTION_KPI_DEFS)]
st_o = fixture(kpi_metric_rows=kpi_rows); f_o = wire(st_o, span=None)
a = OLD.rep_coaching(period=PERIOD, org_id=HOUSE)
old_reads = f_o.reads.get("carrier_kpi_metric", 0)
st_n = fixture(kpi_metric_rows=kpi_rows); f_n = wire(st_n, span=None)
b = NEW.rep_coaching(period=PERIOD, org_id=HOUSE)
new_reads = f_n.reads.get("carrier_kpi_metric", 0)
check("5.1 rep_coaching output is BYTE-IDENTICAL with the KPI defs hoisted", j(a) == j(b),
      f"old={j(a)[:400]}\n         new={j(b)[:400]}")
check(f"5.2 carrier_kpi_metric is now read ONCE, not once per rep: {new_reads} vs {old_reads} "
      f"({len(st_n['rep_commissions'])} reps)",
      new_reads == 1 and old_reads > new_reads, f"{new_reads} vs {old_reads}")

# the 500 class: a tenant with a CUSTOM metric key + one transient failure on a per-rep re-read ->
# OLD builds kpi_targets from the config set but falls back to ACTION_KPI_DEFS mid-loop -> KeyError.
custom = kpi_rows + [{"org_id": HOUSE, "carrier_id": NEW._KPI_DEFAULT_CARRIER,
                      "metric_key": "reviews", "label": "Google Reviews",
                      "payout_config_col": "kpi_reviews_target", "target_default": 90,
                      "is_active": True, "sort": 99}]
st_o = fixture(kpi_metric_rows=custom); f_o = wire(st_o, span=None)
f_o.raise_on["carrier_kpi_metric"] = 2      # the 3rd read (inside the rep loop) fails
err_old = None
try:
    OLD.rep_coaching(period=PERIOD, org_id=HOUSE)
except Exception as e:
    err_old = e
check("5.3 OLD: with a tenant-CUSTOM KPI metric, one transient failure on a per-rep _kpi_defs read "
      "raises KeyError out of the handler -> an unhandled 500 (the 3bf51b4d failure class)",
      isinstance(err_old, KeyError), f"{type(err_old).__name__}: {err_old}")
st_n = fixture(kpi_metric_rows=custom); f_n = wire(st_n, span=None)
f_n.raise_on["carrier_kpi_metric"] = 2
ok_new, err_new = None, None
try:
    ok_new = NEW.rep_coaching(period=PERIOD, org_id=HOUSE)
except Exception as e:
    err_new = e
check("5.4 NEW: the same fixture returns a normal payload (one read, one def set — nothing to "
      "disagree with, and the target lookup is defensive)",
      err_new is None and len(ok_new["reps"]) == 12, f"{type(err_new).__name__}: {err_new}")
check("5.5 NEW: when that ONE read fails, the whole request degrades COHERENTLY to the built-in "
      "ACTION_KPI_DEFS set (same defs everywhere — no half-config/half-fallback KPI list)",
      all([x["kpi"] for x in r["kpis"]] == [k for (k, _l, _c, _d) in NEW.ACTION_KPI_DEFS]
          for r in ok_new["reps"]),
      j(ok_new["reps"][0]["kpis"])[:300])
st_n = fixture(kpi_metric_rows=custom); wire(st_n, span=None)
ok_cfg = NEW.rep_coaching(period=PERIOD, org_id=HOUSE)
check("5.6 NEW: with the read healthy, the tenant's CUSTOM metric is on every rep's KPI list "
      "(config-driven per RULE TWO, not the hard-coded 7) — and its target comes from the config row",
      all(any(x["kpi"] == "reviews" and x["target"] == 90.0 for x in r["kpis"])
          for r in ok_cfg["reps"]),
      j(ok_cfg["reps"][0]["kpis"])[:400])
st_o = fixture(kpi_metric_rows=custom); wire(st_o, span=None)
ref_cfg = OLD.rep_coaching(period=PERIOD, org_id=HOUSE)
check("5.7 and that custom-metric payload is BYTE-IDENTICAL to the old code's (the hoist changes "
      "only how many times the def table is read)", j(ref_cfg) == j(ok_cfg),
      f"old={j(ref_cfg)[:300]}\n         new={j(ok_cfg)[:300]}")

# exec_overview is the OTHER internal caller of rep_coaching — it must be unaffected by the widened
# signature (it passes neither store nor market, so both arrive as the Query default object).
st_o = fixture(); wire(st_o, span=None)
eo_old = OLD.exec_overview(period=PERIOD, org_id=HOUSE)
st_n = fixture(); wire(st_n, span=None)
eo_new = NEW.exec_overview(period=PERIOD, org_id=HOUSE)
check("5.8 GET /exec-overview (the other internal rep_coaching caller) is BYTE-IDENTICAL — the "
      "widened store/market params arrive as Query defaults and normalize to 'no filter'",
      j(eo_old) == j(eo_new), f"old={j(eo_old)[:300]}\n         new={j(eo_new)[:300]}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. ZERO-WRITE + org-scoped reads on the whole touched chain
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. zero-write + org-scoping on every read in the chain ────────────────────────────────")


class ScopeCheckQuery(FakeQuery):
    def execute(self):
        self.c.seen.append((self.t, [f for f in self.f]))
        return super().execute()


class ScopeCheckClient(FakeClient):
    def __init__(self, store):
        super().__init__(store)
        self.seen = []

    def schema(self, _n):
        return type("S", (), {"table": lambda _s, t: ScopeCheckQuery(self, t),
                              "rpc": lambda _s, *a, **k: (_ for _ in ()).throw(RuntimeError("rpc"))})()

    def table(self, t):
        return ScopeCheckQuery(self, t)


sc = ScopeCheckClient(merge(fixture(HOUSE), fixture(OTHER)))
for mod in (NEW, OLD):
    mod.sb = lambda _f=sc: _f
    mod.get_supabase = lambda _f=sc: _f
SO.sb = lambda _f=sc: _f
NEW._team_snap_invalidate()
wrote = None
try:
    snapshot_scoped = snap(NEW, period=PERIOD, org=HOUSE)
except WriteAttempted as e:
    wrote = e
    snapshot_scoped = None
check("6.1 the whole /team/{period}/snapshot chain performs ZERO writes (every write verb on the "
      "fake client raises; the call completed)", wrote is None and snapshot_scoped is not None,
      repr(wrote))
unscoped = [(t, f) for (t, f) in sc.seen if not any(k == "eq" and c == "org_id" for k, c, _v in f)]
check("6.2 EVERY read in the chain is org-scoped (.eq('org_id', …))", unscoped == [],
      str(unscoped)[:500])
leaked = [s for s in (snapshot_scoped or {}).get("stores", [])
          if s["store_code"] not in ALL_CODES]
check("6.3 with two tenants sharing every table, the house payload contains only house rows",
      leaked == [] and len((snapshot_scoped or {}).get("reps") or []) == 12,
      f"{leaked} · {len((snapshot_scoped or {}).get('reps') or [])} reps")
check("6.4 the cached entry is stamped with the org_id that produced it (never reused across orgs)",
      all(k[0] in (HOUSE, OTHER) for k in NEW._team_snap_memo), str(list(NEW._team_snap_memo)))

# the invalidation hook the calc path calls must never raise into a recompute
NEW._team_snap_invalidate()
check("6.5 the calc-path hook `_team_snap_invalidate` is a pure in-memory no-op when the cache is "
      "empty (it can never fail a recompute)", NEW._team_snap_invalidate(HOUSE, PERIOD) is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 7. REAL ASGI round trip — the endpoint over HTTP, not just as a Python call
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. real ASGI HTTP round trip (FastAPI TestClient) ─────────────────────────────────────")
import warnings                                                     # noqa: E402
warnings.filterwarnings("ignore")
from fastapi import FastAPI                                         # noqa: E402
from fastapi.testclient import TestClient                           # noqa: E402

http_store = fixture()
f_http = wire(http_store, span=SPAN_CODES)
_app = FastAPI()
_app.include_router(NEW.router, prefix="/api/v1")
_tc = TestClient(_app)
NEW._team_snap_invalidate()

url = f"/api/v1/commcalc/team/{PERIOD}/snapshot?today={TODAY_ISO}&org_id={HOUSE}"
r1 = _tc.get(url)
check("7.1 GET /api/v1/commcalc/team/July 2026/snapshot -> 200 (the request that used to 500)",
      r1.status_code == 200, f"{r1.status_code} {r1.text[:300]}")
f_http.reads.clear()
r2 = _tc.get(url)
check("7.2 the second HTTP call is served from the memo (identical body, zero heavy reads)",
      r2.status_code == 200 and j(r2.json()) == j(r1.json())
      and sum(f_http.reads.get(t, 0) for t in heavy) == 0,
      f"{r2.status_code} · {dict(f_http.reads)}")
f_http.reads.clear()
r3 = _tc.get(url + "&refresh=1")
check("7.3 ?refresh=1 over HTTP bypasses the memo (heavy reads run again) and returns the same body",
      r3.status_code == 200 and j(r3.json()) == j(r1.json())
      and sum(f_http.reads.get(t, 0) for t in heavy) > 0,
      f"{r3.status_code} · {dict(f_http.reads)}")
r4 = _tc.get(f"/api/v1/commcalc/team/{YM}/snapshot?today={TODAY_ISO}&org_id={HOUSE}")
check("7.4 the '2026-07' spelling is accepted over HTTP too (200, same rollup)",
      r4.status_code == 200
      and j({k: v for k, v in r4.json().items() if k != "period"}) ==
          j({k: v for k, v in r1.json().items() if k != "period"}),
      f"{r4.status_code} {r4.text[:300]}")
r5 = _tc.get(f"/api/v1/commcalc/team/banana/snapshot?today={TODAY_ISO}&org_id={HOUSE}")
check("7.5 an unparseable period is a clean 400 with an actionable message, never a 500",
      r5.status_code == 400 and "expected" in r5.text.lower(),
      f"{r5.status_code} {r5.text[:300]}")

spec = _tc.get("/openapi.json").json()["paths"]
snap_get = spec["/api/v1/commcalc/team/{period}/snapshot"]["get"]
pnames = {p["name"]: p["in"] for p in snap_get.get("parameters", [])}
check("7.6 org_id is still a QUERY param (contract §2) and `refresh` is declared as one too",
      pnames.get("org_id") == "query" and pnames.get("refresh") == "query"
      and "requestBody" not in snap_get, str(pnames))
coach_get = spec["/api/v1/commcalc/coaching/{period}"]["get"]
cparams = {p["name"]: p for p in coach_get.get("parameters", [])}
check("7.7 /coaching/{period} now advertises store/market as ARRAY query params (repeatable), rep "
      "still a single string",
      cparams["store"]["in"] == "query" and cparams["market"]["in"] == "query"
      and "array" in json.dumps(cparams["store"]["schema"])
      and "array" not in json.dumps(cparams["rep"]["schema"]),
      json.dumps({k: cparams[k]["schema"] for k in ("store", "market", "rep")}))
c1 = _tc.get(f"/api/v1/commcalc/coaching/{PERIOD}?org_id={HOUSE}&store={ADDR['S1']}")
c2 = _tc.get(f"/api/v1/commcalc/coaching/{PERIOD}?org_id={HOUSE}"
             f"&store={ADDR['S1']}&store={ADDR['S2']}")
check("7.8 over HTTP: ?store=<one> keeps the historical single-store behaviour, and repeating it "
      "returns the union",
      c1.status_code == c2.status_code == 200
      and {r["store"] for r in c1.json()["reps"]} == {ADDR["S1"]}
      and {r["store"] for r in c2.json()["reps"]} == {ADDR["S1"], ADDR["S2"]},
      f"{c1.status_code}/{c2.status_code} · {c1.text[:150]}")
c3 = _tc.get(f"/api/v1/commcalc/coaching/{PERIOD}?org_id={HOUSE}&rep={REPS['S1'][0]}")
check("7.9 the employee/portal coaching card call (?rep=NAME, no store/market) still works",
      c3.status_code == 200 and len(c3.json()["reps"]) == 1
      and c3.json()["reps"][0]["rep"] == REPS["S1"][0], f"{c3.status_code} {c3.text[:200]}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 9. THE WHOLE FAMILY — the shared period normalizer at all five entry points the failure-log
#    backlog named (/team/{p}/snapshot, /coaching/{p}, /targets/{p}/calendar, /targets/{p}/summary,
#    /exec-overview/{p}). For each: 'Month YYYY' unchanged, '2026-07' now equivalent, garbage -> 400.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. shared period normalizer across the family ─────────────────────────────────────────")


def call(mod, fn, period, **kw):
    st = fixture()
    wire(st, span=SPAN_CODES)
    NEW._team_snap_invalidate()
    f = getattr(mod, fn)
    try:
        out = f(period=period, org_id=HOUSE, **kw)
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        return out, None
    except Exception as e:
        return None, e


FAMILY = [
    ("rep_coaching", "/coaching/{period}", {}),
    ("get_target_calendar", "/targets/{period}/calendar", {"store_code": "S1", "today": TODAY_ISO}),
    ("get_targets_summary", "/targets/{period}/summary",
     {"today": TODAY_ISO, "stores": None, "markets": None, "reps": None}),
    ("exec_overview", "/exec-overview/{period}", {}),
]
for fn, route, kw in FAMILY:
    o_name, e_o_name = call(OLD, fn, PERIOD, **kw)
    n_name, e_n_name = call(NEW, fn, PERIOD, **kw)
    check(f"9.x {route} with 'July 2026' is BYTE-IDENTICAL old vs new",
          e_o_name is None and e_n_name is None and j(o_name) == j(n_name),
          f"old_err={e_o_name} new_err={e_n_name}\n         old={j(o_name)[:300]}\n         new={j(n_name)[:300]}")
    n_ym, e_n_ym = call(NEW, fn, YM, **kw)
    check(f"9.x {route} with '2026-07' now returns the SAME payload (bar the echoed `period`)",
          e_n_ym is None
          and j({k: v for k, v in (n_ym or {}).items() if k != "period"}) ==
              j({k: v for k, v in (n_name or {}).items() if k != "period"}),
          f"err={e_n_ym}\n         ym={j(n_ym)[:300]}\n         name={j(n_name)[:300]}")
    check(f"9.x {route} echoes the caller's own period spelling back (no surprise rewrite)",
          (n_name or {}).get("period") == PERIOD and (n_ym or {}).get("period") == YM,
          f"{(n_name or {}).get('period')!r} / {(n_ym or {}).get('period')!r}")
    for bad in ("", "banana", "2026-13"):
        _, e_bad = call(NEW, fn, bad, **kw)
        check(f"9.x {route} with {bad!r} -> clean 400, never a 500",
              getattr(e_bad, "status_code", None) == 400,
              f"{type(e_bad).__name__}: {getattr(e_bad, 'detail', e_bad)}")

# what the OLD code actually did with the dashed spelling, per endpoint (documented evidence)
_, e_cal_old = call(OLD, "get_target_calendar", YM, store_code="S1", today=TODAY_ISO)
check("9.20 OLD /targets/{period}/calendar refused '2026-07' with a 400 from _period_bounds "
      "(duality gap, now closed)", getattr(e_cal_old, "status_code", None) == 400,
      f"{type(e_cal_old).__name__}: {e_cal_old}")
_, e_sum_old = call(OLD, "get_targets_summary", YM, today=TODAY_ISO, stores=None, markets=None, reps=None)
check("9.21 OLD /targets/{period}/summary refused '2026-07' the same way",
      getattr(e_sum_old, "status_code", None) == 400, f"{type(e_sum_old).__name__}: {e_sum_old}")
eo_old_ym, _ = call(OLD, "exec_overview", YM)
eo_new_ym, _ = call(NEW, "exec_overview", YM)
check("9.22 OLD /exec-overview/'2026-07' silently stapled JANUARY's P&L to this month's commission "
      "tiles (parse_period maps '2026-07' -> January, no error) — NEW resolves the right month",
      (eo_old_ym or {}).get("pl", {}).get("revenue") == 1.0
      and (eo_new_ym or {}).get("pl", {}).get("revenue") == 777.0,
      f"old_pl={(eo_old_ym or {}).get('pl')} new_pl={(eo_new_ym or {}).get('pl')}")
co_old_bad, e_co_old_bad = call(OLD, "rep_coaching", "banana")
check("9.23 OLD /coaching/'banana' returned a silently EMPTY payload (no error, no hint); NEW says "
      "400 with what it expected",
      e_co_old_bad is None and (co_old_bad or {}).get("reps") == [],
      f"{type(e_co_old_bad).__name__}: {e_co_old_bad} · {j(co_old_bad)[:200]}")
check("9.24 the normalizer is ONE shared function (`_period_or_400`), not per-endpoint parsing",
      callable(NEW._period_or_400)
      and NEW._period_or_400(YM) == NEW._period_or_400(PERIOD) == PERIOD,
      f"{NEW._period_or_400(YM)!r} / {NEW._period_or_400(PERIOD)!r}")
_lenient = {}
for _p in ("banana", "", "Jul 2026", "2026-00", "0000-05", "July", "2026"):
    try:
        _lenient[_p] = ("accepted", NEW._period_or_400(_p))
    except Exception as _e:
        _lenient[_p] = ("rejected", getattr(_e, "status_code", type(_e).__name__))
check("9.25 it refuses every lenient parse_period input that used to resolve to 'January 2026'",
      all(v == ("rejected", 400) for v in _lenient.values()), str(_lenient))
check("9.26 …while both real spellings map to the one canonical string",
      NEW._period_or_400("July 2026") == NEW._period_or_400("2026-07") == "July 2026"
      and NEW._period_or_400("december 2025") == "December 2025",
      f"{NEW._period_or_400('december 2025')!r}")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 8. MEASURED COST TABLE (fixture-level, 6 stores / 12 reps / 36 sale lines) — reproducible numbers
#    for the Gate-1 note. "reads" = Supabase round trips; "store-passes" = iterations of the
#    per-store x rep Python pass in get_targets_summary.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. measured cost: reads + per-store passes per snapshot load ───────────────────────────")


def measure(mod, span, cached_second=False):
    st = fixture()
    f = wire(st, span=span)
    seen = []
    _o = NEW.targets_engine.scope_conversion

    def _c(actuals, code, rep, today, *a, **k):
        if rep is None:
            seen.append(code)
        return _o(actuals, code, rep, today, *a, **k)
    NEW.targets_engine.scope_conversion = _c
    try:
        NEW._team_snap_invalidate()
        snap(mod, period=PERIOD)
        if cached_second:
            f.reads.clear(); seen.clear()
            snap(mod, period=PERIOD)
        return sum(f.reads.values()), len(seen), dict(f.reads)
    finally:
        NEW.targets_engine.scope_conversion = _o


rows = [
    ("manager span (2 of 6 stores) · OLD intended", measure(OLD, SPAN_CODES)),
    ("manager span (2 of 6 stores) · NEW cache miss", measure(NEW, SPAN_CODES)),
    ("manager span (2 of 6 stores) · NEW cache hit", measure(NEW, SPAN_CODES, True)),
    ("owner / full org (6 stores) · OLD intended", measure(OLD, None)),
    ("owner / full org (6 stores) · NEW cache miss", measure(NEW, None)),
    ("owner / full org (6 stores) · NEW cache hit", measure(NEW, None, True)),
]
print(f"    {'scenario':46s} {'reads':>6s} {'store-passes':>13s}")
for label, (nreads, npasses, _detail) in rows:
    print(f"    {label:46s} {nreads:6d} {npasses:13d}")
mgr_old, mgr_new, mgr_hit = rows[0][1][0], rows[1][1][0], rows[2][1][0]
own_old, own_new, own_hit = rows[3][1][0], rows[4][1][0], rows[5][1][0]
check(f"8.1 manager load: reads {mgr_old} -> {mgr_new} (pushdown) -> {mgr_hit} (memo hit)",
      mgr_new < mgr_old and mgr_hit < mgr_new, f"{mgr_old}/{mgr_new}/{mgr_hit}")
check(f"8.2 owner load: reads {own_old} -> {own_new} (pushdown is a no-op for the whole org, the "
      f"_kpi_defs N+1 is not) -> {own_hit} (memo hit)",
      own_new < own_old and own_hit < own_new, f"{own_old}/{own_new}/{own_hit}")
check("8.3 the per-store x rep pass shrinks with the span for a manager and is unchanged for the "
      "owner (nothing to narrow) — and is skipped entirely on a memo hit",
      rows[1][1][1] < rows[0][1][1] and rows[4][1][1] == rows[3][1][1]
      and rows[2][1][1] == rows[5][1][1] == 0,
      str([(r[0], r[1][1]) for r in rows]))

# ── summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
