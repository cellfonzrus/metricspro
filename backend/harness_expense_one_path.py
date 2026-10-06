"""DB-FREE PROOF — one question, one path: the expense and statement reads that disagreed.

OWNER, 2026-10-06: *"make sure all expenses and every commission and residual is assigned properly
and p&l calculated properly then the data for all reports should be aligned"*. Three mechanisms were
answering a question a second time, or reporting an absence as a result. All three are unfixed
SIBLINGS of design fixes this house has already shipped — which is exactly the failure the
"find the siblings before you ship" rule exists to catch.

THE THREE DEFECTS, measured live on the house and LuxeLink orgs.

1. A GATED DETECTOR REPORTED A CLEAN ZERO (sibling of §19.49, the absence-as-a-result class).
   `labour_coverage.commission_collisions` can only run on the org's OWN
   `account_config.labour_commission_expense_names` (RULE TWO — no expense name is spelled in
   code). With that list empty it returned `total_double_booked: 0.0` and `note: None`, which reads
   exactly like "measured, and nothing is double-booked". The house org IS that org, and the live
   overlap by the detector's own formula is $2,707.88 July / $7,176.71 August / $9,975.64 September
   — $19,860.23 of labour in the P&L twice while the platform reported $0.00. `suppression_plan`
   right below it had always drawn this line with its `active` flag; the detector had not.

2. A PERIOD FILTER MATCHED ONE SPELLING (sibling of §19.47, the one-stored-spelling class).
   `statement_filter.filtered_statement` read `.eq("period", period)` for both its per-store fetch
   and its line-skeleton fetch, while `router._read` already dereferenced `_period.period_keys`. So
   `GET /account/pl/<month>?stores=<any>` returned `computed: true` with an EMPTY statement and
   `net_income 0.00` for a month stored under the other spelling — a filter that reads as "this
   subset earned nothing" rather than "not computed". Latent rather than on-screen (the browser
   always sends the month-name form), but any API, notify or scheduled caller holding the ISO form
   got the $0.00. Widening the filter ALONE would have been worse than the defect: a month stored
   under BOTH spellings would contribute every store twice and the filtered figures would DOUBLE.

3. A THIRD PATH FOR "WHAT DID THIS STORE SPEND THAT MONTH?" (sibling of §6623's one home).
   `GET /commcalc/expenses-trend` read `store_expenses` raw — no carry-forward and no store
   resolution — while the P&L (`account/coa.build_inputs`) and the GP report (`_compute_gp`) both go
   through `expenses_effective.effective_expense_rows`. Live: the house P&L booked $379,108.81 of
   CARRIED October store opex and the trend had no October row at all; LuxeLink's September and
   October both booked $275,610.15 in the P&L and neither appeared in the trend.

WHAT THIS HARNESS LOCKS:
  §A the gated detector's tri-state verdict, and the retired clean-zero reproduced
  §B `effective_series` — the carry-forward rule over a whole axis of months
  §C THE EQUIVALENCE PROOF: the series and the single-period reader agree, month for month
  §D the filtered statement reads every spelling AND dedupes, so widening cannot double
  §E the un-wiring locks — no caller may go back to a second path or a defaulted verdict
  §F RULE TWO and purity

Run:  cd backend && python3 harness_expense_one_path.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.modules.commcalc import expenses_effective as EX
from app.modules.commcalc import labour_coverage as LC

EXPFX = "app/modules/commcalc/expenses_effective.py"
LCOV = "app/modules/commcalc/labour_coverage.py"
SFILT = "app/modules/account/statement_filter.py"
ANALYSIS = "app/modules/account/analysis.py"
ROUTER = "app/modules/commcalc/router.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"\n     got:  {got!r}\n     want: {want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel)).read()


def _code_only(src):
    """Source with comments and docstrings stripped, so a lock tests CODE not prose."""
    out = [ln.split("  #")[0] for ln in src.splitlines() if not ln.strip().startswith("#")]
    txt = "\n".join(out)
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return txt
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                txt = txt.replace(d, "")
    return txt


def _fn_src(rel, name):
    """The CODE of one function, comments and docstring stripped."""
    txt = _code_only(_src(rel))
    tree = ast.parse(txt)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(txt, node) or ""
    return ""


def exp(code, name, amount, period, source_key=None):
    return {"store_code": code, "expense_name": name, "amount": amount,
            "period": period, "source_key": source_key}


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A A GATED DETECTOR SAYS SO — the clean zero that was never a measurement")
# ══════════════════════════════════════════════════════════════════════════════════════════════
AUG = [exp("B-1", "Employee Commission", 500.0, "August 2026"),
       exp("B-2", "Employee Commission", 500.0, "August 2026")]
REP = {"B-1": 821.65, "B-2": 300.0}

_retired = lambda: {"names": [], "stores": [], "total_double_booked": 0.0,   # noqa: E731
                    "note": None}
_was = _retired()
ok("A1 the retired shape reported a total of 0.0 for an org that never measured",
   _was["total_double_booked"] == 0.0 and _was["note"] is None)
ok("A2 …which is arithmetically indistinguishable from a real finding of nothing",
   _was["total_double_booked"] == LC.commission_collisions([], {}, ["employee commission"]
                                                           )["total_double_booked"])
_un = LC.commission_collisions(AUG, REP, None)
eq("A3 unconfigured now returns the not-configured verdict", _un.get("verdict"),
   getattr(LC, "VERDICT_NOT_CONFIGURED", "not_configured"))
eq("A4 …and `measured` is False", _un.get("measured"), False)
eq("A5 …and the total is None, so an absence can never be summed or charted",
   _un.get("total_double_booked"), None)
ok("A6 …and None is not 0.0 — the defect's own shape cannot come back",
   _un.get("total_double_booked") is None and _un.get("total_double_booked") != 0.0)
ok("A7 …and the note tells a reader it was NOT checked",
   "NOT CHECKED" in (_un.get("note") or ""))
for empty in ([], (), None, set(), ["", "  "]):
    _e = LC.commission_collisions(AUG, REP, empty)
    ok(f"A8 every empty vocabulary shape is not_configured ({empty!r})",
       _e.get("verdict") == getattr(LC, "VERDICT_NOT_CONFIGURED", "not_configured")
       and _e.get("total_double_booked") is None)
_con = LC.commission_collisions(AUG, REP, ["employee commission"])
eq("A9 a CONFIGURED org is measured", _con.get("verdict"), getattr(LC, "VERDICT_MEASURED", "measured"))
eq("A10 …with a real total", _con.get("measured"), True)
ok("A11 …and the measured total is a number, never None",
   isinstance(_con.get("total_double_booked"), float))
ok("A12 a configured org that really has no overlap reports 0.0 AND measured=True",
   (lambda r: r.get("total_double_booked") == 0.0 and r.get("measured") is True)(
       LC.commission_collisions(AUG, {}, ["employee commission"])))
eq("A13 the two verdict strings are the ONE vocabulary",
   sorted({getattr(LC, "VERDICT_MEASURED", None), getattr(LC, "VERDICT_NOT_CONFIGURED", None)},
          key=str), ["measured", "not_configured"])
ok("A14 the sibling `suppression_plan` already said so with `active` — still does",
   LC.suppression_plan(AUG, REP, None)["active"] is False)

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B effective_series — THE carry-forward rule over a whole axis of months")
# ══════════════════════════════════════════════════════════════════════════════════════════════
ROWS = [exp("B-1", "Rent", 100.0, "July 2026"),
        exp("B-2", "Rent", 200.0, "July 2026"),
        exp("B-1", "PTO accrual", 50.0, "July 2026", source_key="payroll_gross"),
        exp("B-1", "Rent", 110.0, "August 2026")]
S = EX.effective_series(ROWS, ["July 2026", "August 2026", "September 2026", "October 2026"])
eq("B1 a month with its OWN rows uses them verbatim, system rows included",
   sorted(r["amount"] for r in S["July 2026"]["rows"]), [50.0, 100.0, 200.0])
eq("B2 …and is not marked carried", S["July 2026"]["carried_from"], None)
eq("B3 a month with its own rows is untouched even when a prior month is richer",
   [r["amount"] for r in S["August 2026"]["rows"]], [110.0])
eq("B4 an empty month carries the latest STRICTLY-PRIOR month", S["September 2026"]["carried_from"],
   "August 2026")
eq("B5 …and carries only that month's MANUAL rows", [r["amount"] for r in
   S["September 2026"]["rows"]], [110.0])
eq("B6 a second empty month carries from the same latest real month, not from the carried one",
   (S["October 2026"]["carried_from"], [r["amount"] for r in S["October 2026"]["rows"]]),
   ("August 2026", [110.0]))
_sys = EX.effective_series([exp("B-1", "PTO accrual", 9.0, "July 2026", source_key="payroll_gross")],
                           ["August 2026"])
eq("B7 a SYSTEM row is never carried into another month (it would double-book)",
   (_sys["August 2026"]["rows"], _sys["August 2026"]["carried_from"]), ([], "July 2026"))
_both = EX.effective_series([exp("B-1", "Rent", 7.0, "2026-07")], ["July 2026"])
eq("B8 both spellings of a month occupy ONE slot", [r["amount"] for r in _both["July 2026"]["rows"]],
   [7.0])
eq("B9 nothing prior -> empty, and no carry claimed",
   EX.effective_series(ROWS, ["June 2026"])["June 2026"], {"rows": [], "carried_from": None})
eq("B10 an unparseable month claims no carry rather than guessing",
   EX.effective_series(ROWS, ["nonsense"])["nonsense"], {"rows": [], "carried_from": None})
eq("B11 an unparseable ROW never wins a carry",
   EX.effective_series([exp("B-1", "Rent", 5.0, "junk")], ["August 2026"])["August 2026"],
   {"rows": [], "carried_from": None})
eq("B12 no rows at all -> every month empty", EX.effective_series([], ["July 2026"]),
   {"July 2026": {"rows": [], "carried_from": None}})
eq("B13 no months asked for -> nothing invented", EX.effective_series(ROWS, []), {})
eq("B14 None inputs are tolerated, never raised", EX.effective_series(None, None), {})
_before = [dict(r) for r in ROWS]
EX.effective_series(ROWS, ["September 2026"])
eq("B15 the input rows are not mutated", ROWS, _before)
ok("B16 the series invents no dollar — every amount came from an input row",
   {r["amount"] for m in S.values() for r in m["rows"]} <= {r["amount"] for r in ROWS})

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C THE EQUIVALENCE PROOF — the series and the single-period reader agree, month for month")
# ══════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, rows, cols):
        self._rows, self._cols = rows, cols
        self._period = None

    def select(self, cols):
        for c in [c for c in cols.split(",") if c]:
            if c not in self._cols:
                raise RuntimeError(f"column {c} does not exist")
        self._sel = cols
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, col, vals):
        if col == "period":
            self._period = [str(v) for v in vals]
        return self

    def limit(self, _n):
        return self

    def range(self, a, b):
        self._rng = (a, b)
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = self._rows if self._period is None else [
            r for r in self._rows if str(r.get("period")) in self._period]
        keep = [c for c in self._sel.split(",") if c]
        return type("R", (), {"data": [{k: r.get(k) for k in keep} for r in rows]})()


class _Schema:
    def __init__(self, rows, cols):
        self._rows, self._cols = rows, cols

    def table(self, _name):
        return _Q(self._rows, self._cols)


class _Client:
    def __init__(self, rows, cols=("period", "store_code", "amount", "expense_name", "source_key")):
        self._rows, self._cols = rows, cols

    def schema(self, _s):
        return _Schema(self._rows, self._cols)


MONTHS = ["June 2026", "July 2026", "August 2026", "September 2026", "October 2026"]
FIXTURES = {
    "the live shape — rows stop in August, two months carry": ROWS,
    "a system-only prior month": [exp("B-1", "PTO", 9.0, "July 2026", source_key="payroll_gross")],
    "both spellings present": [exp("B-1", "Rent", 7.0, "2026-07"),
                               exp("B-2", "Rent", 8.0, "August 2026")],
    "nothing at all": [],
    "one month only, the newest asked for": [exp("B-9", "Rent", 1.0, "October 2026")],
    "a junk period alongside real ones": [exp("B-1", "Rent", 3.0, "junk"),
                                          exp("B-1", "Rent", 4.0, "July 2026")],
}
for label, rows in FIXTURES.items():
    series = EX.effective_series(rows, MONTHS)
    for m in MONTHS:
        io_rows, io_carried = EX.effective_expense_rows(
            _Client(rows), "org", m, list(__import__(
                "app.modules.account._period", fromlist=["x"]).period_keys(m)),
            "store_code,amount,expense_name")
        s_rows = series[m]["rows"]
        eq(f"C[{label}] {m}: same carried_from", series[m]["carried_from"], io_carried)
        eq(f"C[{label}] {m}: same money",
           sorted((r.get("store_code"), r.get("amount")) for r in s_rows),
           sorted((r.get("store_code"), r.get("amount")) for r in io_rows))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D THE FILTERED STATEMENT — every spelling, deduped, so widening cannot double")
# ══════════════════════════════════════════════════════════════════════════════════════════════
import app.modules.account.analysis as AN   # noqa: E402

ok("D1 the 'freshest wins' rule is PUBLIC so a second reader can dereference it",
   callable(getattr(AN, "dedupe_latest", None)))
_rows = [{"period": "September 2026", "statement_type": "pl", "scope_key": "store:A",
          "computed_at": "2026-10-01T00:00:00Z", "payload": {"n": 1}},
         {"period": "2026-09", "statement_type": "pl", "scope_key": "store:A",
          "computed_at": "2026-10-02T00:00:00Z", "payload": {"n": 2}}]
_d = AN.dedupe_latest(_rows)
eq("D2 one month under two spellings collapses to ONE row", len(_d), 1)
eq("D3 …and the newest computed_at wins", list(_d.values())[0]["payload"], {"n": 2})
eq("D4 two different stores stay two rows",
   len(AN.dedupe_latest(_rows[:1] + [dict(_rows[1], scope_key="store:B")])), 2)
eq("D5 an unparseable period has no month key, so the dedupe judges nothing",
   AN.dedupe_latest([dict(_rows[0], period="junk")]), {})

_sf = _code_only(_src(SFILT))
ok("D6 the filtered read widens to every spelling of the month",
   "_per.period_keys(period)" in _sf)
ok("D7 …and goes through the ONE dedupe, never a private copy",
   "_analysis.dedupe_latest(" in _sf)
ok("D8 no single-spelling period filter is left in the module",
   '.eq("period", period)' not in _sf and ".eq('period', period)" not in _sf)
ok("D9 the line-skeleton read widens too, and takes the newest",
   'eq("scope_key", "consolidated")' in _sf and 'order("computed_at", desc=True)' in _sf)
ok("D10 the module defines no second 'freshest wins' loop of its own",
   "computed_at" not in _sf.split("def filtered_statement")[0])

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E THE UN-WIRING LOCKS — no caller may go back to a second path or a defaulted verdict")
# ══════════════════════════════════════════════════════════════════════════════════════════════
_trend = _fn_src(ROUTER, "expenses_trend")
ok("E1 the trend endpoint exists and was found", bool(_trend))
ok("E2 …and dereferences the ONE carry-forward rule", "effective_series(" in _trend)
ok("E3 …and any store_expenses read it makes is CONSUMED by that rule, not by its own loop",
   ("store_expenses" not in _trend)
   or ("effective_series(" in _trend and "eff.get(" in _trend))
ok("E4 …and carries no literal row ceiling (index §19.48)",
   ".limit(" not in _trend)
ok("E5 …and still names no carrier, tenant or product (RULE TWO)",
   not any(w in _trend.lower() for w in ("boost", "luxelink", "cellfonz", "verizon", "total ")))
ok("E6 the trend reports WHICH month it carried, so a carried figure is never silent",
   "carried_from" in _trend)

_lc = _code_only(_src(LCOV))
_coll = _fn_src(LCOV, "commission_collisions")
ok("E7 the detector returns a verdict on every path",
   _coll.count("verdict") >= 2)
ok("E8 …and never returns a 0.0 total on the unconfigured path again",
   "total_double_booked\": 0.0" not in _coll and "'total_double_booked': 0.0" not in _coll)
ok("E9 the verdict vocabulary has ONE home and the detector reads it",
   "VERDICT_MEASURED" in _lc and "VERDICT_NOT_CONFIGURED" in _lc
   and '"measured"' in (_lc.split("VERDICT_MEASURED =") + ["", ""])[1][:40])
ok("E10 the verdict strings are not spelled a second time inside the detector",
   '"not_configured"' not in _coll and "'not_configured'" not in _coll)
ok("E11 the series rule has ONE home — the router does not re-implement the carry",
   "pick_carry_period" not in _code_only(_src(ROUTER)))
_ex = _code_only(_src(EXPFX))
ok("E12 effective_series dereferences the two shared pure facts rather than re-deciding",
   "pick_carry_period(" in _fn_src(EXPFX, "effective_series")
   and "manual_rows(" in _fn_src(EXPFX, "effective_series"))

# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F RULE TWO AND PURITY")
# ══════════════════════════════════════════════════════════════════════════════════════════════
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay"):
    ok(f"F1 no '{word}' in the carry-forward rule", word not in _ex.lower())
    ok(f"F2 no '{word}' in the collision detector", word not in _lc.lower())
ok("F3 effective_series is pure — no client, no clock, no I/O",
   not any(t in _fn_src(EXPFX, "effective_series") for t in ("client", "datetime", "execute(")))
ok("F4 the expenses_effective module still imports nothing at module scope",
   [ln for ln in _ex.splitlines() if ln.startswith(("import ", "from "))] == [])
ok("F5 analysis still imports only _period, so the statement proofs keep running app-free",
   [ln for ln in _code_only(_src(ANALYSIS)).splitlines()
    if ln.startswith(("import ", "from "))] == ["from app.modules.account import _period"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
