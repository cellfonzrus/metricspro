"""PROOF — agent/commission/month-boundary-derive.

THE DEFECT (owner-verified on production data, tenant luxelink, 2026-08-01)
--------------------------------------------------------------------------
The hourly feed→raw_sales derivation picked its period from the WALL CLOCK:

    router._ftp_current_period()  ->  datetime.now().strftime("%B %Y")

so from 00:00 on August 1 it derived only "August 2026" — while the B2B daily email feed carried on
FINALIZING July (that night's trace: July feed rows 283 → 313 → 317 across the 00:09–04:05 sweeps; the
04:08 derivation logged "no feed or monthly rows for this period" *for August* while July sat
un-rederived). Owner SQL the same morning: July feed 3,787 distinct trans_ids vs July raw_sales 3,744 —
**45 transactions in the feed and not in the paid basis**, invisible to every July report and UNPAID in
a July recompute. It recurs every month boundary.

WHAT IS PROVEN HERE — pure + fixture, over the REAL router/sales_derive/sales_recon functions, no DB:

  A  PURE PERIOD ARITHMETIC     the grace window's own maths: the current month is ALWAYS first and is
                                always exactly what shipped before; the prior month is added ONLY inside
                                the window; clamps; disabled/absent/garbage config.
  B  DIFFERENTIAL vs 3d176fc    the pinned base module is loaded from git and the SAME fixture is driven
                                through BOTH `_promote_feed_impl`s. Outside a grace window the plan is
                                one period and the write set — deleted keys, inserted rows, upload_trace
                                rows, returned summary — is IDENTICAL, key for key.
  C  MONTH-ROLLOVER SIMULATION  the real incident: July finalizes after midnight. Pre-fix the 45 late
                                transactions never reach raw_sales; post-fix the grace run picks them up.
  D  ★ EMPTY-FEED PRIOR PERIOD  the money-critical assertion. A grace run on a period whose feed is EMPTY
                                SKIPS — zero deletes, zero inserts, raw_sales byte-identical — instead of
                                rewriting (and content-deduping) a basis somebody is about to be paid
                                from. Judged per period: an empty July says nothing about August.
  E  IDEMPOTENCE                re-deriving with an unchanged feed produces the identical raw_sales.
  F  NO RECOMPUTE               a grace re-derive never calls _run_calculation. Money moves attended.
  G  TWO-TENANT ISOLATION       the sweep loops tenants; each org's window comes from its OWN config row
                                and each org only ever reads/writes its own rows.
  H  TRACE SELF-EXPLANATION     every upload_trace row a grace run writes says what it is.

Run:  cd backend && python3 scratchpad/month_boundary_derive_proof.py
"""
import copy
import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import router as R                    # noqa: E402
from app.modules.commcalc import sales_derive as SD             # noqa: E402
from app.modules.commcalc import sales_recon as SR              # noqa: E402

BASE_SHA = "3d176fc"            # LITERAL pinned differential base (origin/main at dispatch)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")


def head(t):
    print(f"\n{'=' * 100}\n{t}\n{'=' * 100}")


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# Fake Supabase — org_id + period filters are HONORED (an isolation proof needs a client that can leak)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Q:
    def __init__(self, c, t):
        self.c, self.t = c, t
        self.op = "select"
        self.filters = []       # (kind, col, val)
        self.rows = None
        self.rng = None
        self.lim = None
        self.on_conflict = None

    # -- builders -------------------------------------------------------------------------------
    def select(self, *a, **k):
        self.op = "select"
        self.cols = a[0] if a else "*"
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", rows
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.rows, self.on_conflict = "upsert", row, on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def order(self, col, **k):
        self.ordering = col
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    # -- execution ------------------------------------------------------------------------------
    def _match(self, r):
        for kind, col, val in self.filters:
            if kind == "eq" and r.get(col) != val:
                return False
            if kind == "in" and r.get(col) not in val:
                return False
            if kind == "neq" and r.get(col) == val:
                return False
        return True

    def execute(self):
        tbl = self.c.tables.setdefault(self.t, [])
        if self.op in ("insert", "upsert"):
            rows = self.rows if isinstance(self.rows, list) else [self.rows]
            if self.op == "upsert" and self.on_conflict:
                keys = [k.strip() for k in self.on_conflict.split(",")]
                for r in rows:
                    hit = next((x for x in tbl if all(x.get(k) == r.get(k) for k in keys)), None)
                    if hit:
                        hit.update(copy.deepcopy(r))
                    else:
                        tbl.append(copy.deepcopy(r))
            else:
                for r in rows:
                    n = copy.deepcopy(r)
                    n.setdefault("id", f"{self.t}-{self.c._seq()}")
                    tbl.append(n)
            self.c.writes.append((self.op, self.t, len(rows)))
            return _Resp(rows)
        if self.op == "delete":
            keep = [r for r in tbl if not self._match(r)]
            gone = len(tbl) - len(keep)
            self.c.tables[self.t] = keep
            self.c.deletes.append((self.t, tuple(self.filters), gone))
            return _Resp([])
        out = [copy.deepcopy(r) for r in tbl if self._match(r)]
        if getattr(self, "ordering", None):
            out.sort(key=lambda r: str(r.get(self.ordering) or ""))
        if self.rng is not None:
            a, b = self.rng
            out = out[a:b + 1]
        elif self.lim is not None:
            out = out[:self.lim]
        self.c.reads.append({"table": self.t, "filters": tuple(self.filters),
                             "cols": getattr(self, "cols", "*"), "limit": self.lim})
        return _Resp(out)


class Fake:
    def __init__(self, tables=None):
        self.tables = {k: [copy.deepcopy(r) for r in v] for k, v in (tables or {}).items()}
        self.reads, self.writes, self.deletes = [], [], []
        self._n = 0
        self.rpc_enabled = True

    def _seq(self):
        self._n += 1
        return self._n

    def schema(self, _s):
        return self

    def table(self, t):
        return _Q(self, t)

    def rpc(self, name, params):
        if not self.rpc_enabled:
            raise RuntimeError("rpc missing (mig 204 unapplied)")
        if name == "sales_feed_orgs_for_period":
            ps = params.get("p_periods") or []
            seen = []
            for r in self.tables.get("daily_sales_feed", []):
                if r.get("period") in ps and r.get("org_id") not in seen:
                    seen.append(r["org_id"])
            return _Q(self, "_rpc")._rpc_result([{"org_id": o} for o in seen])
        raise RuntimeError(f"unknown rpc {name}")


def _rpc_result(self, data):
    class _X:
        def execute(_s):
            return _Resp(data)
    return _X()


_Q._rpc_result = _rpc_result


ORG_A = "854f6d7b-0000-0000-0000-00000000aaaa"     # a NON-house tenant (luxelink shape)
ORG_B = "00000000-0000-0000-0000-0000000000bb"     # a second tenant, for isolation


def frow(org, tid, day, price, period="July 2026", **extra):
    r = {"org_id": org, "period": period, "trans_id": tid, "trans_date": day,
         "ext_price": price, "store": "S1", "salesperson": "REP", "category": "Accessory",
         "voided": "false", "gp": round(price * 0.3, 2), "contract_type": "New Activation"}
    r.update(extra)
    return r


def stored(client, table, org, period=None):
    return [r for r in client.tables.get(table, [])
            if r.get("org_id") == org and (period is None or r.get("period") == period)]


def sig(rows):
    """Order-insensitive content signature of a raw_sales set (ids/created_at excluded)."""
    return sorted(R._row_content_sig(r) for r in rows)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("A — PURE PERIOD ARITHMETIC (sales_derive)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
AUG1 = datetime(2026, 8, 1, 0, 9)
AUG3 = datetime(2026, 8, 3, 23, 59)
AUG4 = datetime(2026, 8, 4, 0, 1)
AUG20 = datetime(2026, 8, 20, 12, 0)
JAN1 = datetime(2027, 1, 1, 3, 0)

DEF = SD.resolve(None)
check("absent config = code default {enabled:True, days:3, retain:None}",
      DEF == {"enabled": True, "days": 3, "retain": None}, DEF)
check("Aug 1 00:09 → ['August 2026', 'July 2026'] (the incident hour)",
      SD.periods(AUG1, DEF) == ["August 2026", "July 2026"], SD.periods(AUG1, DEF))
check("current month is ALWAYS entry 0", SD.periods(AUG1, DEF)[0] == "August 2026")
check("last hour of the window (Aug 3 23:59) still grace", SD.window_open(AUG3, DEF))
check("Aug 4 00:01 → window CLOSED, ['August 2026'] only",
      SD.periods(AUG4, DEF) == ["August 2026"] and not SD.window_open(AUG4, DEF))
check("mid-month (Aug 20) → current only", SD.periods(AUG20, DEF) == ["August 2026"])
check("year rollover Jan 1 → prior = 'December 2026'",
      SD.periods(JAN1, DEF) == ["January 2027", "December 2026"], SD.periods(JAN1, DEF))

OFF = SD.resolve({"enabled": False})
check("enabled=false → today's behaviour exactly (current only, on the 1st)",
      SD.periods(AUG1, OFF) == ["August 2026"])
ZERO = SD.resolve({"days": 0})
check("days=0 → disabled (and enabled forced False)",
      ZERO["enabled"] is False and SD.periods(AUG1, ZERO) == ["August 2026"])
check("days clamped to MAX_GRACE_DAYS (999 → 15)", SD.resolve({"days": 999})["days"] == SD.MAX_GRACE_DAYS)
check("negative days clamped to 0 → off", SD.resolve({"days": -5})["enabled"] is False)
check("garbage days ignored, default kept", SD.resolve({"days": "three"})["days"] == 3)
check("garbage json string → default", SD.resolve("{not json") == DEF)
check("json STRING config parsed like a dict", SD.resolve('{"days": 5}')["days"] == 5)
check("retain clamped up to the normal guard (0.10 → 0.85, never WEAKER)",
      SD.resolve({"retain": 0.10})["retain"] == 0.85)
check("retain 1.0 accepted (never lose a line)", SD.resolve({"retain": 1.0})["retain"] == 1.0)
check("retain None = 'use the normal guard'", SD.resolve({"retain": None})["retain"] is None)

PLAN = SD.plan(AUG1, DEF)
check("plan entry 0 = (current, grace=False, 0.85) — the pre-fix call, unchanged",
      PLAN[0] == ("August 2026", False, 0.85), PLAN[0])
check("plan entry 1 = (prior, grace=True, 0.85 default)",
      PLAN[1] == ("July 2026", True, 0.85), PLAN[1])
check("plan honours a tenant's strict grace retain",
      SD.plan(AUG1, SD.resolve({"retain": 1.0}))[1] == ("July 2026", True, 1.0))
check("plan OUTSIDE the window is a ONE-entry list = the pre-fix behaviour",
      SD.plan(AUG20, DEF) == [("August 2026", False, 0.85)])
check("enumeration_needed short-circuits the back half of the month",
      SD.enumeration_needed(AUG1) and not SD.enumeration_needed(AUG20))

# load() degrades when migration 266 is unapplied (the column read raises)
class _NoCol(Fake):
    def table(self, t):
        q = _Q(self, t)
        if t == "commission_org_config":
            def boom(*a, **k):
                raise RuntimeError('column "sales_derive_grace" does not exist')
            q.select = boom
        return q


check("load() with mig 266 UNAPPLIED → code default (feature works before the SQL)",
      SD.load(_NoCol({}), ORG_A) == DEF)
check("load() with no config row → code default", SD.load(Fake({}), ORG_A) == DEF)
_cfgc = Fake({"commission_org_config": [{"org_id": ORG_A, "sales_derive_grace": {"days": 7}},
                                        {"org_id": ORG_B, "sales_derive_grace": {"enabled": False}}]})
check("load() is ORG-SCOPED: A gets 7 days, B gets off",
      SD.load(_cfgc, ORG_A)["days"] == 7 and SD.load(_cfgc, ORG_B)["enabled"] is False)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head(f"B — DIFFERENTIAL vs pinned base {BASE_SHA}: outside a grace window, NOTHING moves")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 1) every commcalc file except the four this package touches is byte-identical to the pinned base.
changed = subprocess.run(["git", "-C", REPO, "diff", "--name-only", BASE_SHA, "--"],
                         capture_output=True, text=True).stdout.split()
EXPECTED = {
    "backend/app/modules/commcalc/router.py",
    "backend/app/modules/commcalc/sales_derive.py",
    "backend/app/modules/commcalc/sales_recon.py",
    "backend/app/modules/commcalc/import_audit.py",
    "database/migrations/266_commission_sales_derive_grace.sql",
    "frontend/src/app/(platform)/commcalc/sales-derive/page.tsx",
    "backend/scratchpad/month_boundary_derive_proof.py",
    "backend/scratchpad/month_boundary_derive_asgi_smoke.py",
}
unexpected = [f for f in changed if f not in EXPECTED]
check("no file outside the declared set differs from the base", not unexpected, unexpected)
MONEY_FILES = ["calculator.py", "commission_engine.py", "sale_installment_engine.py",
               "installment_engine.py", "commission_ledger.py", "plan_pay_gate.py",
               "setup_fee_pay.py", "expected_commission.py", "targets_engine.py",
               "gp_report.py", "accessory_definition.py", "commission_catalog.py"]
check("every pay engine is byte-identical to base",
      not [m for m in MONEY_FILES if f"backend/app/modules/commcalc/{m}" in changed],
      [m for m in MONEY_FILES if f"backend/app/modules/commcalc/{m}" in changed])

# 2) load the BASE router as a separate module and drive the SAME fixture through both impls.
_tmp = tempfile.mkdtemp(prefix="mbd_base_")
_base_path = os.path.join(_tmp, "base_router.py")
_blob = subprocess.run(["git", "-C", REPO, "show", f"{BASE_SHA}:backend/app/modules/commcalc/router.py"],
                       capture_output=True, text=True)
open(_base_path, "w", encoding="utf-8").write(_blob.stdout)
_spec = importlib.util.spec_from_file_location("base_router", _base_path)
BASE = importlib.util.module_from_spec(_spec)
sys.modules["base_router"] = BASE
_spec.loader.exec_module(BASE)
check(f"pinned base router loaded from git {BASE_SHA}", hasattr(BASE, "_promote_feed_impl"))
check("base has NO grace parameter (this is genuinely the pre-fix code)",
      "grace" not in BASE._promote_feed_impl.__code__.co_varnames[:7])


def run_impl(mod, tables, org, period, grace=None, retain=0.85, force=False, dry_run=False):
    """Drive one module's _promote_feed_impl over a fresh copy of `tables`; return (summary, client)."""
    c = Fake(tables)
    old = mod.sb
    mod.sb = lambda: c
    try:
        pv = mod._pvariants(period)
        canon = next((v for v in pv if v[:1].isalpha()), period)
        if grace is None:
            s = mod._promote_feed_impl(c, org, pv, canon, dry_run, force, retain)
        else:
            s = mod._promote_feed_impl(c, org, pv, canon, dry_run, force, retain, grace)
    finally:
        mod.sb = old
    return s, c


def trace_rows(c):
    return [{k: v for k, v in r.items() if k != "id"} for r in c.tables.get("upload_trace", [])]


FIX = {
    "daily_sales_feed": [frow(ORG_A, f"T{i}", f"2026-07-{(i % 28) + 1:02d}", 10 + i) for i in range(1, 41)],
    "raw_sales": [frow(ORG_A, f"T{i}", f"2026-07-{(i % 28) + 1:02d}", 10 + i) for i in range(1, 36)]
                 + [frow(ORG_A, "M99", "2026-07-05", 500)],       # a monthly-only transaction
}
s_new, c_new = run_impl(R, FIX, ORG_A, "July 2026", grace=False)
s_base, c_base = run_impl(BASE, FIX, ORG_A, "July 2026")
check("summary identical to base (bar the additive 'grace' key)",
      {k: v for k, v in s_new.items() if k != "grace"} == s_base,
      {k: (s_base.get(k), v) for k, v in s_new.items() if s_base.get(k) != v})
check("'grace' is the ONLY new summary key and is False on the normal path",
      set(s_new) - set(s_base) == {"grace"} and s_new["grace"] is False)
check("raw_sales written IDENTICALLY to base",
      sig(stored(c_new, "raw_sales", ORG_A)) == sig(stored(c_base, "raw_sales", ORG_A)))
check("same delete issued as base", [d[:2] for d in c_new.deletes] == [d[:2] for d in c_base.deletes])
check("upload_trace row identical to base (note included)", trace_rows(c_new) == trace_rows(c_base))
check("monthly-only transaction M99 survived on BOTH paths",
      any(r["trans_id"] == "M99" for r in stored(c_new, "raw_sales", ORG_A))
      and any(r["trans_id"] == "M99" for r in stored(c_base, "raw_sales", ORG_A)))

# the retain guard and the empty-everything skip also still behave exactly as base
# a HALF-DELIVERED feed: one line for a transaction the basis holds 40 distinct lines for. Every
# existing row's trans_id IS in the feed, so nothing is carried over and the result collapses to 1 line.
THIN = {"daily_sales_feed": [frow(ORG_A, "T1", "2026-07-01", 10)],
        "raw_sales": [frow(ORG_A, "T1", "2026-07-01", 10 + i) for i in range(40)]}
g_new, gc_new = run_impl(R, THIN, ORG_A, "July 2026", grace=False)
g_base, gc_base = run_impl(BASE, THIN, ORG_A, "July 2026")
check("retain guard fires identically to base",
      g_new.get("skipped") == g_base.get("skipped") and g_new["skipped"].startswith("guard:"))
check("guard skip wrote nothing on either path",
      not gc_new.deletes and not gc_base.deletes)
e_new, _ = run_impl(R, {}, ORG_A, "July 2026", grace=False)
e_base, _ = run_impl(BASE, {}, ORG_A, "July 2026")
check("empty-everything skip message identical to base",
      e_new.get("skipped") == e_base.get("skipped") == "no feed or monthly rows for this period")


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("C — MONTH-ROLLOVER SIMULATION (the luxelink incident, replayed)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 23:50 July 31 — the feed and the basis are in step at 40 transactions.
JULY_FEED = [frow(ORG_A, f"J{i}", f"2026-07-{(i % 28) + 1:02d}", 20 + i) for i in range(1, 41)]
JULY_BASIS = [frow(ORG_A, f"J{i}", f"2026-07-{(i % 28) + 1:02d}", 20 + i) for i in range(1, 41)]
# 00:09–04:05 August 1 — the feed FINALIZES 5 more July transactions after the boundary…
LATE = [frow(ORG_A, f"LATE{i}", "2026-07-31", 300 + i) for i in range(1, 6)]
# …and August's own feed starts arriving.
AUG_FEED = [frow(ORG_A, f"A{i}", "2026-08-01", 50 + i, period="August 2026") for i in range(1, 8)]

tables = {"daily_sales_feed": JULY_FEED + LATE + AUG_FEED,
          "raw_sales": list(JULY_BASIS),
          "commission_org_config": [{"org_id": ORG_A, "sales_derive_grace": None}],
          "report_definitions": [{"org_id": ORG_A, "report_key": "sales", "auto": True}]}

# PRE-FIX behaviour: the wall clock says August, so only August is derived.
pre_plan = ["August 2026"]
c_pre = Fake(tables)
for p in pre_plan:
    R.sb = lambda: c_pre
    pvv = R._pvariants(p)
    R._promote_feed_impl(c_pre, ORG_A, pvv, p, False, False, 0.85, False)
july_pre = {r["trans_id"] for r in stored(c_pre, "raw_sales", ORG_A, "July 2026")}
check("PRE-FIX: the 5 late July transactions never reach the basis",
      not (july_pre & {f"LATE{i}" for i in range(1, 6)}))
check("PRE-FIX: July basis stuck at 40 transactions while the feed has 45",
      len(july_pre) == 40)

# POST-FIX: the plan at 00:09 on Aug 1 covers both months.
c_post = Fake(tables)
R.sb = lambda: c_post
plan_now = R._sales_derive_plan(c_post, ORG_A, now=AUG1)
check("POST-FIX plan at 00:09 Aug 1 = August then July",
      [p for p, _g, _r in plan_now] == ["August 2026", "July 2026"], plan_now)
for p, g, ret in plan_now:
    R._promote_feed_impl(c_post, ORG_A, R._pvariants(p), p, False, False, ret, g)
july_post = {r["trans_id"] for r in stored(c_post, "raw_sales", ORG_A, "July 2026")}
aug_post = {r["trans_id"] for r in stored(c_post, "raw_sales", ORG_A, "August 2026")}
check("POST-FIX: all 5 late July transactions are now in the basis",
      {f"LATE{i}" for i in range(1, 6)} <= july_post, sorted(july_post)[:8])
check("POST-FIX: July basis = 45 transactions, matching the feed", len(july_post) == 45)
check("POST-FIX: August still derived exactly as before (7 transactions)", len(aug_post) == 7)
check("POST-FIX: no July transaction was LOST", {f"J{i}" for i in range(1, 41)} <= july_post)
gap = SR.derive_gap("July 2026", org_id=ORG_A, client=c_post)
check("derive_gap agrees: 0 missing after the grace run",
      gap["missing_in_monthly"] == 0 and gap["feed_trans"] == 45 and gap["monthly_trans"] == 45, gap)
gap_pre = SR.derive_gap("July 2026", org_id=ORG_A, client=c_pre)
check("derive_gap on the PRE-FIX state reports the 5 as missing (this is the alert's number)",
      gap_pre["missing_in_monthly"] == 5, gap_pre)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("D — ★ MONEY-CRITICAL: a grace run with an EMPTY feed SKIPS, it does not wipe or churn")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# July has a hand-uploaded basis (with two genuinely-identical line items on one ticket) and NO feed.
DUP_TICKET = [frow(ORG_A, "H1", "2026-07-09", 40), frow(ORG_A, "H1", "2026-07-09", 40)]
HAND = {"daily_sales_feed": [frow(ORG_A, "A1", "2026-08-01", 9, period="August 2026")],
        "raw_sales": [frow(ORG_A, f"H{i}", "2026-07-02", 15 + i) for i in range(2, 30)] + DUP_TICKET}
before = sig(stored(Fake(HAND), "raw_sales", ORG_A, "July 2026"))

s_g, c_g = run_impl(R, HAND, ORG_A, "July 2026", grace=True)
after = sig(stored(c_g, "raw_sales", ORG_A, "July 2026"))
check("grace run SKIPPED with an explicit, honest reason",
      str(s_g.get("skipped", "")).startswith("grace re-derive: no daily-feed rows"), s_g.get("skipped"))
check("★ ZERO deletes issued against raw_sales", not c_g.deletes, c_g.deletes)
check("★ ZERO rows inserted into raw_sales",
      not [w for w in c_g.writes if w[1] == "raw_sales"], c_g.writes)
check("★ raw_sales byte-identical after the skipped grace run", before == after)
check("★ the duplicate line item was NOT content-deduped away (2 'H1' lines survive)",
      sum(1 for r in stored(c_g, "raw_sales", ORG_A, "July 2026") if r["trans_id"] == "H1") == 2)

# and the SAME fixture on the NORMAL (non-grace) path still does what it always did — the guard is
# scoped to grace runs and changes nothing about the open-month self-heal.
s_n, c_n = run_impl(R, HAND, ORG_A, "July 2026", grace=False)
s_nb, c_nb = run_impl(BASE, HAND, ORG_A, "July 2026")
check("non-grace path on the same fixture is IDENTICAL to base (guard is grace-only)",
      {k: v for k, v in s_n.items() if k != "grace"} == s_nb
      and sig(stored(c_n, "raw_sales", ORG_A, "July 2026")) == sig(stored(c_nb, "raw_sales", ORG_A, "July 2026")))
check("…and the base DOES rewrite + dedupe that month (which is exactly what grace must not do)",
      s_nb.get("dupes_dropped") == 1 and bool(c_nb.deletes))

# per-period independence: an empty JULY feed must not stop AUGUST deriving in the same sweep
c_i = Fake(HAND)
R.sb = lambda: c_i
for p, g, ret in [("August 2026", False, 0.85), ("July 2026", True, 0.85)]:
    R._promote_feed_impl(c_i, ORG_A, R._pvariants(p), p, False, False, ret, g)
check("periods judged INDEPENDENTLY: August derived, July untouched",
      len(stored(c_i, "raw_sales", ORG_A, "August 2026")) == 1
      and sig(stored(c_i, "raw_sales", ORG_A, "July 2026")) == before)

# a feed that exists but is thin still meets the retain guard, not the empty-skip
THINF = {"daily_sales_feed": [frow(ORG_A, "H2", "2026-07-02", 17)],
         "raw_sales": [frow(ORG_A, "H2", "2026-07-02", 15 + i) for i in range(28)]}
s_t, c_t = run_impl(R, THINF, ORG_A, "July 2026", grace=True)
check("a THIN (not empty) grace feed hits the retain guard, still writes nothing",
      str(s_t.get("skipped", "")).startswith("guard:") and not c_t.deletes, s_t.get("skipped"))
s_t1, c_t1 = run_impl(R, THINF, ORG_A, "July 2026", grace=True, retain=1.0)
check("retain=1.0 (the strict grace guard a hand-upload tenant sets) also refuses",
      str(s_t1.get("skipped", "")).startswith("guard:") and not c_t1.deletes)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("E — IDEMPOTENCE: an unchanged feed re-derives to identical raw_sales")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
IDEM = {"daily_sales_feed": JULY_FEED + LATE, "raw_sales": list(JULY_BASIS)}
c1 = Fake(IDEM)
R.sb = lambda: c1
r1 = R._promote_feed_impl(c1, ORG_A, R._pvariants("July 2026"), "July 2026", False, False, 0.85, True)
snap1 = sig(stored(c1, "raw_sales", ORG_A, "July 2026"))
r2 = R._promote_feed_impl(c1, ORG_A, R._pvariants("July 2026"), "July 2026", False, False, 0.85, True)
snap2 = sig(stored(c1, "raw_sales", ORG_A, "July 2026"))
r3 = R._promote_feed_impl(c1, ORG_A, R._pvariants("July 2026"), "July 2026", False, False, 0.85, True)
snap3 = sig(stored(c1, "raw_sales", ORG_A, "July 2026"))
check("run 1 wrote the merged month", r1.get("written") and len(snap1) == 45)
check("run 2 produced IDENTICAL raw_sales", snap1 == snap2)
check("run 3 produced IDENTICAL raw_sales (stable, not oscillating)", snap2 == snap3)
check("no row multiplication across three runs (45 transactions, 45 lines)",
      len(stored(c1, "raw_sales", ORG_A, "July 2026")) == 45)
check("summary is stable run over run",
      {k: r2[k] for k in ("result_lines", "result_trans", "result_amount")}
      == {k: r3[k] for k in ("result_lines", "result_trans", "result_amount")})


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("F — COMPOSITION: deriving NEVER recomputes (money moves attended)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
src_all_due = inspect.getsource(R._promote_all_due)
src_impl = inspect.getsource(R._promote_feed_impl)
src_wrap = inspect.getsource(R._promote_feed_to_raw_sales)
src_plan = inspect.getsource(R._sales_derive_plan)
check("_promote_all_due never calls _run_calculation", "_run_calculation" not in src_all_due)
check("the promotion impl never calls _run_calculation", "_run_calculation" not in src_impl)
check("the promotion wrapper never calls _run_calculation", "_run_calculation" not in src_wrap)
check("the plan helper never calls _run_calculation", "_run_calculation" not in src_plan)
check("sales_derive.py names no calculator/engine at all",
      not any(k in open(SD.__file__).read() for k in
              ("_run_calculation", "calc_rep_commissions", "commission_engine", "rep_commissions")))

# the email sweep's ONE recompute is the pre-existing current-month one, and the grace loop is outside it
sweep_src = inspect.getsource(R._run_email_sweep)
check("the email sweep still has exactly ONE _run_calculation call", sweep_src.count("_run_calculation") == 1)
check("that call is still the CURRENT period (unchanged)",
      "await _run_calculation(_ftp_current_period(), org_id)" in sweep_src)
gi = sweep_src.index("_sales_derive_plan")
ri = sweep_src.index("await _run_calculation")
check("the grace loop sits BEFORE the recompute block and is not inside it", gi < ri)
check("the grace loop passes grace=True through to the promotion",
      "grace=_g, retain=_gret" in sweep_src)
check("rep_commissions is never named by anything this package changed",
      "rep_commissions" not in src_all_due + src_impl + src_wrap + src_plan)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("G — TWO-TENANT ISOLATION through the real org-agnostic sweep (_promote_all_due)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# Tenant A: grace ON (default). Tenant B: grace explicitly OFF. Both have late July + live August feeds.
def two_tenant():
    return {
        "daily_sales_feed":
            [frow(ORG_A, f"AJ{i}", "2026-07-15", 30 + i) for i in range(1, 6)]
            + [frow(ORG_A, "ALATE", "2026-07-31", 900)]
            + [frow(ORG_A, "AAUG", "2026-08-01", 11, period="August 2026")]
            + [frow(ORG_B, f"BJ{i}", "2026-07-15", 40 + i) for i in range(1, 6)]
            + [frow(ORG_B, "BLATE", "2026-07-31", 800)]
            + [frow(ORG_B, "BAUG", "2026-08-01", 12, period="August 2026")],
        "raw_sales":
            [frow(ORG_A, f"AJ{i}", "2026-07-15", 30 + i) for i in range(1, 6)]
            + [frow(ORG_B, f"BJ{i}", "2026-07-15", 40 + i) for i in range(1, 6)],
        "commission_org_config": [
            {"org_id": ORG_A, "sales_derive_grace": None},                    # default = ON
            {"org_id": ORG_B, "sales_derive_grace": {"enabled": False}},      # explicitly OFF
        ],
        "report_definitions": [{"org_id": ORG_A, "report_key": "sales", "auto": True},
                               {"org_id": ORG_B, "report_key": "sales", "auto": True}],
    }


class _FrozenNow(datetime):
    pass


_real_dt = R._datetime


class _AugDT(object):
    """Freeze _datetime.now() at 00:09 on Aug 1 for the sweep, leaving everything else alone."""
    def __getattr__(self, k):
        return getattr(_real_dt, k)

    @staticmethod
    def now(tz=None):
        return AUG1 if tz is None else _real_dt.now(tz)


c_s = Fake(two_tenant())
R.sb = lambda: c_s
R._datetime = _AugDT()
try:
    res = R._promote_all_due(c_s)
finally:
    R._datetime = _real_dt

a_july = {r["trans_id"] for r in stored(c_s, "raw_sales", ORG_A, "July 2026")}
b_july = {r["trans_id"] for r in stored(c_s, "raw_sales", ORG_B, "July 2026")}
a_aug = {r["trans_id"] for r in stored(c_s, "raw_sales", ORG_A, "August 2026")}
b_aug = {r["trans_id"] for r in stored(c_s, "raw_sales", ORG_B, "August 2026")}
check("sweep reports the grace period it used", res.get("grace_period") == "July 2026", res.get("grace_period"))
check("tenant A (grace ON): the late July transaction was picked up", "ALATE" in a_july)
check("tenant B (grace OFF): its late July transaction was NOT", "BLATE" not in b_july)
check("both tenants' August derived exactly as before", a_aug == {"AAUG"} and b_aug == {"BAUG"})
check("no A row ever landed under B", not any(r["org_id"] == ORG_A for r in c_s.tables["raw_sales"]
                                              if r.get("org_id") == ORG_B))
check("every raw_sales row carries a real org_id",
      all(r.get("org_id") in (ORG_A, ORG_B) for r in c_s.tables["raw_sales"]))
check("A's July basis contains ONLY A's transactions",
      all(t.startswith("A") for t in a_july) and all(t.startswith("B") for t in b_july))
def _is_col_probe(r):
    """The PRE-EXISTING first-promotion schema probe: `.select(<one column>).limit(1)` with no filters,
    used only to discover which columns raw_sales has when it is empty. Untouched by this package and
    byte-identical to the pinned base — recorded as a known pre-existing unscoped read, not introduced
    here (see the handoff's hygiene note)."""
    return (r["filters"] == () and r["limit"] == 1 and r["cols"] not in ("*", None)
            and "," not in str(r["cols"]))


_probes = [r for r in c_s.reads if r["table"] in ("raw_sales", "daily_sales_feed") and _is_col_probe(r)]
_unscoped = [r for r in c_s.reads
             if r["table"] in ("raw_sales", "daily_sales_feed") and not _is_col_probe(r)
             and not any(k == "eq" and col == "org_id" for k, col, _v in r["filters"])]
check("every sales DATA read the sweep made was org-scoped (no bare table scan)", not _unscoped, _unscoped[:3])
check("the only unscoped touches are the pre-existing column probes, and they are base code",
      all("client.schema('commcalc').table('raw_sales').select(c).limit(1).execute()" in src_probe
          for src_probe in [inspect.getsource(R._promote_feed_impl)]),
      f"{len(_probes)} probe(s)")
check("every raw_sales delete was org-scoped",
      all(any(k == "eq" and col == "org_id" for k, col, _v in f)
          for t, f, _n in c_s.deletes if t == "raw_sales"))

# an explicitly-requested period turns grace OFF entirely
c_e = Fake(two_tenant())
R.sb = lambda: c_e
R._datetime = _AugDT()
try:
    res_e = R._promote_all_due(c_e, period="August 2026")
finally:
    R._datetime = _real_dt
check("explicit period ⇒ no grace anywhere (targeted call unchanged)",
      res_e.get("grace_period") is None and res_e.get("grace_orgs") == 0)
check("explicit period ⇒ July basis untouched for BOTH tenants",
      {r["trans_id"] for r in stored(c_e, "raw_sales", ORG_A, "July 2026")} == {f"AJ{i}" for i in range(1, 6)})

# report_definitions auto=false still opts a tenant out of BOTH the current and the grace run
t_off = two_tenant()
t_off["report_definitions"] = [{"org_id": ORG_A, "report_key": "sales", "auto": False},
                              {"org_id": ORG_B, "report_key": "sales", "auto": True}]
c_o = Fake(t_off)
R.sb = lambda: c_o
R._datetime = _AugDT()
try:
    R._promote_all_due(c_o)
finally:
    R._datetime = _real_dt
check("sales auto=false opts the tenant out of the grace run too",
      "ALATE" not in {r["trans_id"] for r in stored(c_o, "raw_sales", ORG_A, "July 2026")})

# a tenant with NO current-month feed still gets its closed month re-derived (and nothing else)
t_only = two_tenant()
t_only["daily_sales_feed"] = [r for r in t_only["daily_sales_feed"]
                              if not (r["org_id"] == ORG_A and r["period"] == "August 2026")]
c_p = Fake(t_only)
R.sb = lambda: c_p
R._datetime = _AugDT()
try:
    R._promote_all_due(c_p)
finally:
    R._datetime = _real_dt
check("prior-period-only tenant: closed month re-derived",
      "ALATE" in {r["trans_id"] for r in stored(c_p, "raw_sales", ORG_A, "July 2026")})
check("prior-period-only tenant: no phantom current month created",
      not stored(c_p, "raw_sales", ORG_A, "August 2026"))

# mid-month: the sweep must behave exactly as it did pre-fix
class _MidDT(_AugDT):
    @staticmethod
    def now(tz=None):
        return AUG20 if tz is None else _real_dt.now(tz)


c_m = Fake(two_tenant())
R.sb = lambda: c_m
R._datetime = _MidDT()
try:
    res_m = R._promote_all_due(c_m)
finally:
    R._datetime = _real_dt
check("mid-month sweep does NO grace work at all", res_m.get("grace_orgs") == 0)
check("mid-month July basis untouched",
      {r["trans_id"] for r in stored(c_m, "raw_sales", ORG_A, "July 2026")} == {f"AJ{i}" for i in range(1, 6)})

# the mig-204 RPC being absent must not disable the grace enumeration either
c_r = Fake(two_tenant())
c_r.rpc_enabled = False
R.sb = lambda: c_r
R._datetime = _AugDT()
try:
    R._promote_all_due(c_r)
finally:
    R._datetime = _real_dt
check("RPC-absent fallback path still runs the grace re-derive",
      "ALATE" in {r["trans_id"] for r in stored(c_r, "raw_sales", ORG_A, "July 2026")})


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("H — THE TRACE SAYS WHAT IT IS")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
tr = [r for r in c_s.tables.get("upload_trace", []) if r.get("org_id") == ORG_A]
grace_tr = [r for r in tr if SD.GRACE_NOTE in str(r.get("note") or "")]
check("the grace run wrote a labelled upload_trace row", len(grace_tr) >= 1, [r.get("note") for r in tr])
check("the label is the documented constant", SD.GRACE_NOTE == "month-boundary grace re-derive")
check("grace trace rows are still source='promotion', upload_type='sales' (same trail)",
      all(r.get("source") == "promotion" and r.get("upload_type") == "sales" for r in grace_tr))
check("grace trace rows carry the period they re-derived",
      all(str(r.get("periods") or {}).find("July 2026") >= 0 or r.get("skipped") for r in grace_tr))
check("the CURRENT-month trace row is NOT labelled (only grace runs are)",
      any(SD.GRACE_NOTE not in str(r.get("note") or "") for r in tr))
check("every trace row is org-stamped", all(r.get("org_id") for r in tr))
# a SKIPPED grace run is traced too, so a silent no-op is impossible
_c = Fake(HAND)
R.sb = lambda: _c
R._promote_feed_impl(_c, ORG_A, R._pvariants("July 2026"), "July 2026", False, False, 0.85, True)
sk = _c.tables.get("upload_trace", [])
check("a SKIPPED grace run still writes a trace row saying why",
      len(sk) == 1 and SD.GRACE_NOTE in str(sk[0].get("note")) and "no daily-feed rows" in str(sk[0].get("note")),
      sk)
# the mutex-skip path is labelled too
mutex_src = inspect.getsource(R._promote_feed_to_raw_sales)
check("the concurrent-run skip is labelled on grace runs too",
      'f"{sales_derive.GRACE_NOTE} — {note}" if grace else note' in mutex_src)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("I — derive_gap (the alert's number) is bounded, org-scoped and spelling-proof")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
MIX = {"daily_sales_feed": [frow(ORG_A, f"X{i}", "2026-07-03", 5) for i in range(1, 11)]
                           + [frow(ORG_B, "BX", "2026-07-03", 5)],
       "raw_sales": [frow(ORG_A, f"X{i}", "2026-07-03", 5, period="2026-07") for i in range(1, 9)]}
cm = Fake(MIX)
g = SR.derive_gap("July 2026", org_id=ORG_A, client=cm)
check("finds raw_sales stored under the OTHER spelling ('2026-07')", g["monthly_trans"] == 8, g)
check("gap = 2 (the two the feed has and the basis lacks)", g["missing_in_monthly"] == 2, g)
check("tenant B's row never counted into tenant A's gap", g["feed_trans"] == 10, g)
g2 = SR.derive_gap("2026-07", org_id=ORG_A, client=cm)
check("asking with the OTHER spelling gives the same answer", g2["missing_in_monthly"] == 2)
check("sample_missing names the actual transactions",
      set(g["sample_missing"]) == {"X9", "X10"}, g["sample_missing"])
gb = SR.derive_gap("July 2026", org_id=ORG_B, client=cm)
check("tenant B sees only its own (1 in feed, 0 in basis)",
      gb["feed_trans"] == 1 and gb["missing_in_monthly"] == 1)
check("every derive_gap read was org-scoped",
      all(any(k == "eq" and col == "org_id" for k, col, _v in r["filters"]) for r in cm.reads))
VOIDED = {"daily_sales_feed": [frow(ORG_A, "V1", "2026-07-03", 5, voided="true"),
                              frow(ORG_A, "V2", "2026-07-03", 5)],
          "raw_sales": [frow(ORG_A, "V2", "2026-07-03", 5)]}
gv = SR.derive_gap("July 2026", org_id=ORG_A, client=Fake(VOIDED))
check("voided feed lines are not reported as a missing transaction", gv["missing_in_monthly"] == 0, gv)
check("a table that does not exist contributes nothing (no false alarm)",
      SR.derive_gap("July 2026", org_id=ORG_A, client=Fake({}))["has_feed"] is False)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("J — THE ATTENTION SIGNAL (import_audit provider)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import import_audit as IA               # noqa: E402

prov = next((p for p in getattr(IA, "_register_provider", None).__globals__["PROVIDERS"]
             if p["key"] == "commcalc_sales_derive_gap"), None) if IA._register_provider else None
if prov is None:
    from app.modules.core.import_health import PROVIDERS as CORE_PROVIDERS
    prov = next((p for p in CORE_PROVIDERS if p["key"] == "commcalc_sales_derive_gap"), None)
check("provider registered with platform-core", prov is not None)
check("provider is HEAVY (never runs on a login popup)", prov and prov["cost"] == "heavy")

GAPPY = {"daily_sales_feed": [frow(ORG_A, f"G{i}", "2026-07-10", 7) for i in range(1, 11)],
         "raw_sales": [frow(ORG_A, f"G{i}", "2026-07-10", 7) for i in range(1, 8)],
         "commission_org_config": [{"org_id": ORG_A, "sales_derive_grace": None}],
         "report_definitions": [{"org_id": ORG_A, "report_key": "sales", "auto": True}]}
items_open = IA.p_sales_derive_gap(Fake(GAPPY), ORG_A, {"now": AUG1})
items_closed = IA.p_sales_derive_gap(Fake(GAPPY), ORG_A, {"now": AUG20})
check("fires on the closed month with a gap", len(items_open) == 1 and len(items_closed) == 1)
check("INSIDE the window it is a warning (something will fix it)", items_open[0]["severity"] == "warning")
check("OUTSIDE the window it is an error (nothing will)", items_closed[0]["severity"] == "error")
check("count = the missing transactions", items_closed[0]["count"] == 3, items_closed[0]["count"])
check("names the period in the label", "July 2026" in items_closed[0]["label"])
check("says what to do, in plain language",
      "re-derive" in items_closed[0]["detail"].lower() and "re-calculate" in items_closed[0]["detail"].lower(),
      items_closed[0]["detail"])
check("deep-links to the derive console for that period",
      items_closed[0]["deep_link"] == "/commcalc/sales-derive?period=July 2026",
      items_closed[0]["deep_link"])
check("the item key is period-scoped (so it clears when fixed)",
      items_closed[0]["key"] == "commcalc:derive_gap:July 2026")

HEALTHY = dict(GAPPY, raw_sales=[frow(ORG_A, f"G{i}", "2026-07-10", 7) for i in range(1, 11)])
check("silent when the basis is in step", IA.p_sales_derive_gap(Fake(HEALTHY), ORG_A, {"now": AUG20}) == [])
NOFEED = {"raw_sales": [frow(ORG_A, "G1", "2026-07-10", 7)]}
check("silent for a tenant with no daily feed", IA.p_sales_derive_gap(Fake(NOFEED), ORG_A, {"now": AUG20}) == [])
MANUAL = dict(GAPPY, report_definitions=[{"org_id": ORG_A, "report_key": "sales", "auto": False}])
mi = IA.p_sales_derive_gap(Fake(MANUAL), ORG_A, {"now": AUG1})
check("auto=false tenant gets an ERROR even inside the window (nothing will derive it)",
      mi and mi[0]["severity"] == "error" and "MANUAL" in mi[0]["detail"], mi and mi[0]["detail"])
OFFCFG = dict(GAPPY, commission_org_config=[{"org_id": ORG_A, "sales_derive_grace": {"enabled": False}}])
oi = IA.p_sales_derive_gap(Fake(OFFCFG), ORG_A, {"now": AUG1})
check("grace-disabled tenant gets an ERROR and is told the window is off",
      oi and oi[0]["severity"] == "error" and "switched off" in oi[0]["detail"], oi and oi[0]["detail"])
check("provider is org-scoped: tenant B sees nothing from A's gap",
      IA.p_sales_derive_gap(Fake(GAPPY), ORG_B, {"now": AUG20}) == [])
check("provider never writes", not Fake(GAPPY).writes)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 100}\nRESULT: {_pass} passed, {_fail} failed\n{'=' * 100}")
sys.exit(1 if _fail else 0)
