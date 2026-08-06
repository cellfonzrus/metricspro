"""PROOF — `_storeops_roster` / `_store_active` (owner defect 2026-08-06, disabled stores in Targets).

Runs entirely offline against a stub Supabase client. It proves the four things a screenshot cannot,
above all the NULL trap: `storeops.stores.is_active` is a NULLABLE column, so the naive
`.eq("is_active", True)` would DROP every row the operator never touched and silently empty a
tenant's store roster.

    python3 backend/tools/active_store_roster_proof.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.modules.commcalc.router import _store_active, _storeops_roster  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


class _Q:
    """Minimal PostgREST-ish stub. `boom_on` makes the given column name raise on select()."""

    def __init__(self, rows, sel, boom_on=None, log=None):
        self._rows, self._sel, self._boom, self._log = rows, sel, boom_on, log
        if boom_on and boom_on in [c.strip() for c in sel.split(",")]:
            raise RuntimeError(f"column {boom_on} does not exist")
        if log is not None:
            log.append(sel)

    def eq(self, *a, **k):
        return self

    def execute(self):
        cols = [c.strip() for c in self._sel.split(",")]
        return type("R", (), {"data": [{c: r.get(c) for c in cols if c in r} for r in self._rows]})()


class _Tbl:
    def __init__(self, rows, boom_on, log):
        self._rows, self._boom, self._log = rows, boom_on, log

    def select(self, sel):
        return _Q(self._rows, sel, self._boom, self._log)


class _Client:
    def __init__(self, rows, boom_on=None, log=None):
        self._rows, self._boom, self._log = rows, boom_on, log

    def schema(self, _s):
        return self

    def table(self, _t):
        return _Tbl(self._rows, self._boom, self._log)


ORG = "00000000-0000-0000-0000-000000000001"

# The live house roster shape as probed 2026-08-06: 20 active + 6 explicitly-disabled T-stores,
# PLUS a NULL-flag row standing in for a tenant that never touched the column.
ROWS = (
    [{"store_code": f"B-{i}", "address": f"{i} Main St", "market": "LI",
      "monthly_target": 1000, "is_active": True} for i in range(1, 21)]
    + [{"store_code": c, "address": None, "market": "TT", "monthly_target": 6000, "is_active": False}
       for c in ("T-531", "T-7812", "T-902", "T-957", "T21880", "T3560")]
    + [{"store_code": "NULLFLAG", "address": "9 Legacy Ave", "market": "NJ",
        "monthly_target": 5000, "is_active": None}]
)
CODES = lambda rs: sorted(str(r.get("store_code")) for r in rs)  # noqa: E731
DISABLED = ["T-531", "T-7812", "T-902", "T-957", "T21880", "T3560"]
ACTIVE_20 = sorted([f"B-{i}" for i in range(1, 21)])

# ── 1. THE NULL TRAP ────────────────────────────────────────────────────────────────────────────
check("null is_active is ACTIVE", _store_active({"is_active": None}), True)
check("absent is_active is ACTIVE", _store_active({}), True)
check("true is ACTIVE", _store_active({"is_active": True}), True)
check("explicit false is INACTIVE", _store_active({"is_active": False}), False)
# The exact bug the naive fix would have caused, stated as a value:
naive = [r for r in ROWS if r.get("is_active") is True]
check("naive .eq(is_active,True) WOULD drop the NULL row", "NULLFLAG" in CODES(naive), False)
check("ours KEEPS the NULL row",
      "NULLFLAG" in CODES(_storeops_roster(_Client(ROWS), ORG)), True)

# ── 2. DEFAULT = active only ────────────────────────────────────────────────────────────────────
got = _storeops_roster(_Client(ROWS), ORG)
check("default drops all 6 disabled", CODES(got), sorted(ACTIVE_20 + ["NULLFLAG"]))
check("default keeps 21 of 27", len(got), 21)
check("no disabled code survives", [c for c in CODES(got) if c in DISABLED], [])

# ── 3. include_inactive opt-in (the storeops /employees convention) ─────────────────────────────
allrows = _storeops_roster(_Client(ROWS), ORG, include_inactive=True)
check("include_inactive returns everything", len(allrows), 27)
check("include_inactive is byte-identical to the unfiltered read", allrows,
      _Client(ROWS).schema("s").table("t").select(
          "store_code,address,market,monthly_target,is_active").eq("org_id", ORG).execute().data)

# ── 4. HISTORY: keep_codes rescues a disabled store that has data for the period ────────────────
kept = _storeops_roster(_Client(ROWS), ORG, keep_codes={"T-902", "T3560"})
check("keep_codes rescues exactly those two", CODES(kept),
      sorted(ACTIVE_20 + ["NULLFLAG", "T-902", "T3560"]))
check("rescued row still reports is_active False",
      [_store_active(r) for r in kept if r["store_code"] == "T-902"], [False])
check("keep_codes is case-insensitive", "T-902" in CODES(
    _storeops_roster(_Client(ROWS), ORG, keep_codes={"t-902"})), True)
check("keep_codes tolerates blanks/None",
      CODES(_storeops_roster(_Client(ROWS), ORG, keep_codes={"", None, "  "})),
      sorted(ACTIVE_20 + ["NULLFLAG"]))

# ── 5. DEGRADE: a tenant whose table predates the is_active column ─────────────────────────────
legacy = [{"store_code": "L-1", "address": "1 A St", "market": "M", "monthly_target": 1},
          {"store_code": "L-2", "address": "2 B St", "market": "M", "monthly_target": 2}]
log = []
got = _storeops_roster(_Client(legacy, boom_on="is_active", log=log), ORG,
                       cols="store_code,address,market,monthly_target")
check("no is_active column -> every store treated ACTIVE", CODES(got), ["L-1", "L-2"])
check("it retried without is_active", log, ["store_code,address,market,monthly_target"])
check("roster unreadable -> [] not an exception",
      _storeops_roster(_Client(legacy, boom_on="store_code"), ORG, cols="store_code"), [])

# ── 6. the caller's own column list is honoured (is_active added, never substituted) ───────────
log = []
_storeops_roster(_Client(ROWS, log=log), ORG, cols="store_code,address")
check("is_active appended to a narrow select", log, ["store_code,address,is_active"])
log = []
_storeops_roster(_Client(ROWS, log=log), ORG, cols="store_code,address,is_active")
check("is_active not duplicated when already asked for", log, ["store_code,address,is_active"])

# ── 7. THE SAME TRAP IN THE MONEY PATH: gp_report's store_mapping loop ─────────────────────────
# `calc_gp_report` adds every MAPPED store to the report even when it had no sales. It used
# `s.get('is_active', True)`, which returns the default only when the KEY is ABSENT — a row whose
# column exists but is NULL read falsy and the store vanished from the GP report (and therefore
# from the P&L store list). Owner approved closing it on 2026-08-06. Proven here as VALUES, and
# proven to be a NO-OP on today's live shape (every live store_mapping row is is_active=true).
from app.modules.commcalc import gp_report as _GP  # noqa: E402

_SM = [
    {"store_address": "1 Alpha St", "store_code": "A1", "market": "M", "is_active": True},
    {"store_address": "2 Beta St", "store_code": "B2", "market": "M", "is_active": None},   # NULL
    {"store_address": "3 Gamma St", "store_code": "C3", "market": "M"},                      # absent
    {"store_address": "4 Delta St", "store_code": "D4", "market": "M", "is_active": False},  # closed
]


def _gp_stores(sm):
    """Store rows calc_gp_report emits for a mapping list with NO sales at all."""
    r = _GP.calc_gp_report(sales=[], pay_detail=[], mi_rows=[], rep_commissions=[], expenses=[],
                           catalog=[], store_mapping=sm, period="July 2026")
    return sorted(str(x.get("store")) for x in (r.get("store_rows") or []))


_old = lambda rows: [x for x in rows if x.get("is_active", True)]     # noqa: E731  the buggy form
_new = lambda rows: [x for x in rows if x.get("is_active") is not False]      # noqa: E731  the fix
_addr = lambda rows: [x["store_address"] for x in rows]                       # noqa: E731
check("gp: OLD form DROPS the NULL-flag store", "2 Beta St" in _addr(_old(_SM)), False)
check("gp: NEW form KEEPS the NULL-flag store", "2 Beta St" in _addr(_new(_SM)), True)
check("gp: both forms still drop the EXPLICITLY closed store",
      ("4 Delta St" in _addr(_old(_SM)), "4 Delta St" in _addr(_new(_SM))), (False, False))
# THE LIVE CASE: every store_mapping row in both tenants is is_active=true (house 31/31, lux 39/39).
# On that shape the two forms are identical, so no GP/P&L number can move.
_LIVE = [dict(x, is_active=True) for x in _SM]
check("gp: on the LIVE all-true shape the two forms are byte-identical",
      _addr(_old(_LIVE)) == _addr(_new(_LIVE)) == _addr(_LIVE), True)
# ...and end-to-end through the real engine:
_gp = _gp_stores(_SM)
check("gp engine: NULL-flag store now appears in store_rows", "2 Beta St" in _gp, True)
check("gp engine: absent-flag store appears", "3 Gamma St" in _gp, True)
check("gp engine: explicitly-closed store still excluded", "4 Delta St" not in _gp, True)
check("gp engine: all-true mapping (the LIVE case) yields every store, unchanged",
      _gp_stores(_LIVE), ["1 Alpha St", "2 Beta St", "3 Gamma St", "4 Delta St"])

for n, g, w in FAIL:
    print(f"FAIL  {n}\n        got  {g!r}\n        want {w!r}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
