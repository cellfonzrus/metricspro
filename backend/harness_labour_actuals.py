"""Proof harness + LOCK — a carried expense month never inherits the platform's own LABOUR, and the
rows that replace it come from the homes that already derive them (owner directive 2026-10-20).

Owner, verbatim: *"sepatember and october expeses wll be carried forward but salary and commisson
shoudl be derived from the actuals"*.

THE CLASS, stated once so nobody re-derives it
──────────────────────────────────────────────
**A fact the platform MEASURES per month was being inherited from another month by a rule written
for facts that genuinely repeat.** Rent repeats; payroll does not. The carry rule (§19.50) was
right about rent and wrong about the two rows the platform fills in itself, and because the result
is a plausible number rather than a blank, nothing on any screen said so.

Measured live 2026-10-20, before this fix: ALL 3,724 `store_expenses` rows are `source_key` NULL,
so they all carry. One tenant's August sheet is $159,512.55 of derived labour against $116,650.14
of everything else — so the $276,162.69 that September AND October both showed was 58% last
August's payroll. The house org's September carried $143,735.94 of derived labour the same way.

WHAT IS PROVEN HERE
  A. `wanted_halves` — the halves added back are EXACTLY the halves the vocabulary claims, so the
     carry rule's exclusion and this module's replacement can never take different views.
  B. `fold` is pure, total, and keeps the halves APART — a blended per-store figure split by a
     ratio would be an invented number, which is the defect this house treats as worse than a gap.
  C. `totals_by_store` sums verbatim, over the chosen halves only.
  D. `as_expense_rows` — the row shape a sheet-reader already sums; the row NAMES come from the
     vocabulary; `source_key='labour_actual'` so the carry rule can never take a measurement of
     THIS month into another one; an unmeasured half contributes NOTHING, never a zero (§19.49).
  E. THE REGRESSION, end to end over the REAL carry rule: a month with no sheet of its own inherits
     the rent and not the payroll, and its labour is its own.
  F. THE I/O SHELL over an in-memory client: `measured` tells the truth, including when a read
     fails — a month whose payroll cannot be read is an UNKNOWN month, never a cheaper one.
  G. RULE TWO and purity.
  H. The one-home lock: no dollar is derived here, and no row name is spelled here.

The WIRING lock (every caller of the carry rule passes the vocabulary; the keying plumbing has one
home) lives in `harness_expense_one_path.py` §H, beside the rule it guards.

Stdlib only, no DB, no network.
Run:  cd backend && python3 harness_labour_actuals.py
"""
import os
import sys
import types

sys.path.insert(0, "app")

if "pydantic_settings" not in sys.modules:                 # import stub — no settings are read
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                   # noqa: D401 — minimal import stub
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.commcalc import labour_actuals as LA      # noqa: E402
from app.modules.commcalc import labour_vocabulary as LV   # noqa: E402
from app.modules.commcalc import expenses_effective as EX  # noqa: E402

FAIL = 0
PASS = 0
HERE = os.path.dirname(os.path.abspath(__file__))
LA_SRC = open(os.path.join(HERE, "app/modules/commcalc/labour_actuals.py")).read()


def ok(name, cond, detail=None):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + ("" if detail is None else f"   {detail!r}"))


def eq(name, got, want):
    ok(name, got == want, None if got == want else f"got {got!r} want {want!r}")


HOUSE = LV.resolve()                       # no config at all ⇒ the platform's own default rows
OFF = LV.resolve(mode=LV.OFF)              # the tenant switched the mechanism off
NAMED = LV.resolve(payroll_cfg=["DM Salaries", "Store Payroll"],
                   commission_cfg=["Rep Commission"])

print("\n§A THE HALVES ADDED BACK ARE EXACTLY THE HALVES THE VOCABULARY CLAIMS")
eq("A1 the house vocabulary claims both halves", LA.wanted_halves(HOUSE),
   [LA.SALARY, LA.COMMISSION])
eq("A2 mode='off' claims NOTHING — nothing is excluded, so nothing may be added back",
   LA.wanted_halves(OFF), [])
eq("A3 a tenant's own names still claim both halves", LA.wanted_halves(NAMED),
   [LA.SALARY, LA.COMMISSION])
eq("A4 payroll named, commission not ⇒ salary only",
   LA.wanted_halves({"payroll_names": ["Store Payroll"], "commission_names": []}), [LA.SALARY])
eq("A5 commission named, payroll not ⇒ commission only",
   LA.wanted_halves({"payroll_names": [], "commission_names": ["Rep Commission"]}),
   [LA.COMMISSION])
for bad in (None, {}, "nonsense", 7, {"payroll_names": None, "commission_names": None}):
    eq(f"A6 a nonsense vocabulary claims nothing rather than raising ({bad!r})",
       LA.wanted_halves(bad), [])
eq("A7 the halves this module adds back are the names the carry rule refuses — ONE decision",
   bool(LA.wanted_halves(HOUSE)) and bool(LV.derived_row_names(HOUSE)), True)
eq("A8 …and with mode='off' both sides agree there is nothing to do",
   (LA.wanted_halves(OFF), LV.derived_row_names(OFF)), ([], []))

print("\n§B fold — PURE, TOTAL, AND THE HALVES STAY APART")
WAGES = {"1 S 60TH ST": 1000.0, "1800 GREAT NECK RD": 2500.5, None: 300.0}
COMMS = [{"store": "1 S 60TH ST", "total_payout": 150.0},
         {"store": "1800 GREAT NECK RD", "total_payout": "1,200.25"},
         {"store": None, "total_payout": 10.0}]
bhs, bh = LA.fold(WAGES, COMMS)
eq("B1 salary folds per store, verbatim",
   bhs[LA.SALARY], {"1 S 60TH ST": 1000.0, "1800 GREAT NECK RD": 2500.5, "": 300.0})
eq("B2 commission folds per store, with the money parsed out of a formatted string",
   bhs[LA.COMMISSION], {"1 S 60TH ST": 150.0, "1800 GREAT NECK RD": 1200.25, "": 10.0})
eq("B3 the halves are NEVER blended — a per-store split by ratio would be an invented number",
   sorted(bhs), sorted([LA.COMMISSION, LA.SALARY]))
eq("B4 the half totals are the sums of their own rows", bh,
   {LA.SALARY: 3800.5, LA.COMMISSION: 1360.25})
eq("B5 labour with no attributable store is kept on a row of its own, never dropped",
   (bhs[LA.SALARY][""], bhs[LA.COMMISSION][""]), (300.0, 10.0))
eq("B6 two rows for one store add up rather than overwrite",
   LA.fold({}, [{"store": "A", "total_payout": 1.0},
                {"store": "A", "total_payout": 2.0}])[0][LA.COMMISSION], {"A": 3.0})
eq("B7 `halves` limits what is folded", sorted(LA.fold(WAGES, COMMS, halves=[LA.SALARY])[0]),
   [LA.SALARY])
eq("B8 an empty halves list folds nothing at all", LA.fold(WAGES, COMMS, halves=[]), ({}, {}))
eq("B9 the store identity is INJECTED, so it is the one home and never a rule of its own",
   LA.fold({"  1 s 60th st  ": 5.0}, [], lambda v: "B-60TH")[0][LA.SALARY], {"B-60TH": 5.0})
eq("B10 with no injection the raw spelling is kept, stripped and nothing else",
   LA.fold({"  1 S 60TH ST  ": 5.0}, [])[0][LA.SALARY], {"1 S 60TH ST": 5.0})
for bad in (None, "junk", [], {}):
    ok(f"B11 nonsense inputs are tolerated, never raised ({bad!r})",
       isinstance(LA.fold(bad, bad)[0], dict))
eq("B12 an unparseable amount reads as 0.00 rather than crashing a month",
   LA.fold({"A": "n/a"}, [])[0][LA.SALARY], {"A": 0.0})
_in = {"A": 1.0}
LA.fold(_in, COMMS)
eq("B13 the inputs are not mutated", (_in, len(COMMS)), ({"A": 1.0}, 3))
eq("B14 fold invents no dollar — the total is the inputs' total",
   round(sum(bh.values()), 2), round(1000.0 + 2500.5 + 300.0 + 150.0 + 1200.25 + 10.0, 2))

print("\n§C totals_by_store")
eq("C1 both halves sum per store", LA.totals_by_store(bhs),
   {"1 S 60TH ST": 1150.0, "1800 GREAT NECK RD": 3700.75, "": 310.0})
eq("C2 one half only", LA.totals_by_store(bhs, [LA.SALARY]), bhs[LA.SALARY])
eq("C3 nothing asked for, nothing invented", LA.totals_by_store(bhs, []), {})
eq("C4 None is tolerated", LA.totals_by_store(None), {})

print("\n§D as_expense_rows — THE SHAPE A SHEET-READER ALREADY SUMS")
ACT = {"period": "September 2026", "by_half_store": bhs, "by_half": bh,
       "by_store": LA.totals_by_store(bhs), "measured": {LA.SALARY: True, LA.COMMISSION: True},
       "halves": [LA.SALARY, LA.COMMISSION]}
rows = LA.as_expense_rows(ACT, HOUSE, period="September 2026")
eq("D1 one row per store per half (the unattributed row included)", len(rows), 6)
eq("D2 the rows are named by the VOCABULARY, never by a literal in this module",
   sorted({r["expense_name"] for r in rows}),
   sorted(set(LV.DEFAULT_PAYROLL_ROWS) | set(LV.DEFAULT_COMMISSION_ROWS)))
eq("D3 a tenant's own names win wholesale",
   sorted({r["expense_name"] for r in LA.as_expense_rows(ACT, NAMED)}),
   ["DM Salaries", "Rep Commission"])
ok("D4 every row is stamped `labour_actual`, so the carry rule can never take it to another month",
   all(r["source_key"] == "labour_actual" for r in rows))
ok("D5 …which is exactly what the carry rule refuses to carry",
   EX.carryable_rows(rows, LV.derived_row_names(HOUSE)) == []
   and EX.manual_rows(rows) == [])
eq("D6 the money lands on `amount` and the store on `store_code` by default",
   sorted(set(rows[0]) ), sorted(["store_code", "expense_name", "amount", "source_key", "period"]))
eq("D7 the rows carry the month they measure", {r["period"] for r in rows}, {"September 2026"})
eq("D8 the period is omitted when the caller does not key by it",
   "period" in LA.as_expense_rows(ACT, HOUSE)[0], False)
eq("D9 the total of the rows IS the total of the fold",
   round(sum(r["amount"] for r in rows), 2), round(sum(bh.values()), 2))
eq("D10 the GP report's SALARY-ONLY read takes exactly the salary dollars (it already books "
   "commission on `rep_pay`)",
   round(sum(r["amount"] for r in LA.as_expense_rows(ACT, HOUSE, halves=[LA.SALARY])), 2),
   bh[LA.SALARY])
_un = dict(ACT, measured={LA.SALARY: True, LA.COMMISSION: False})
eq("D11 an UNMEASURED half contributes nothing — never a zero (§19.49)",
   {r["expense_name"] for r in LA.as_expense_rows(_un, HOUSE)},
   set(LV.DEFAULT_PAYROLL_ROWS))
eq("D12 nothing measured at all ⇒ no rows, so the caller must SAY it is unknown",
   LA.as_expense_rows(dict(ACT, measured={}), HOUSE), [])
eq("D13 a half the vocabulary does not claim is never materialised",
   {r["expense_name"] for r in LA.as_expense_rows(dict(ACT, halves=[LA.SALARY]), HOUSE)},
   set(LV.DEFAULT_PAYROLL_ROWS))
eq("D14 a $0.00 store is not given a row (a zero row reads as a measured zero)",
   LA.as_expense_rows(dict(ACT, by_half_store={LA.SALARY: {"A": 0.0}},
                           halves=[LA.SALARY], measured={LA.SALARY: True}), HOUSE), [])
eq("D15 `key_as` relabels canonical → store CODE for a sheet keyed by code",
   [r["store_code"] for r in LA.as_expense_rows(
       dict(ACT, by_half_store={LA.SALARY: {"1 S 60TH ST": 5.0}}, halves=[LA.SALARY],
            measured={LA.SALARY: True}), HOUSE, key_as=lambda k: "B-60TH")], ["B-60TH"])
eq("D16 a `key_as` that raises leaves the key as it was rather than losing the money",
   [r["store_code"] for r in LA.as_expense_rows(
       dict(ACT, by_half_store={LA.SALARY: {"X": 5.0}}, halves=[LA.SALARY],
            measured={LA.SALARY: True}), HOUSE,
       key_as=lambda k: (_ for _ in ()).throw(ValueError("boom")))], ["X"])
eq("D17 the column names are the caller's, for a reader that keys differently",
   sorted(LA.as_expense_rows(ACT, HOUSE, halves=[LA.SALARY], store_col="store",
                             amount_col="amt")[0]),
   sorted(["store", "expense_name", "amt", "source_key"]))
for bad in (None, {}, "junk"):
    eq(f"D18 nonsense input ⇒ no rows, never a raise ({bad!r})", LA.as_expense_rows(bad, HOUSE), [])

print("\n§E THE REGRESSION — over the REAL carry rule, end to end")
# August has a sheet; September has none. The owner's rule: September keeps August's RENT and takes
# its OWN payroll. Figures scaled from the live measurement quoted in this file's header.
AUG = [{"store_code": "B-1", "expense_name": "Rent", "amount": 116650.14, "period": "August 2026",
        "source_key": None},
       {"store_code": "B-1", "expense_name": "Employee Salaries", "amount": 146808.84,
        "period": "August 2026", "source_key": None},
       {"store_code": "B-1", "expense_name": "Employee Commission", "amount": 12703.71,
        "period": "August 2026", "source_key": None}]
SEP_ACTUAL = {"period": "September 2026",
              "by_half_store": {LA.SALARY: {"B-1": 110302.73},
                                LA.COMMISSION: {"B-1": 2145.25}},
              "by_half": {LA.SALARY: 110302.73, LA.COMMISSION: 2145.25},
              "by_store": {"B-1": 112447.98},
              "measured": {LA.SALARY: True, LA.COMMISSION: True},
              "halves": [LA.SALARY, LA.COMMISSION]}
_old = EX.effective_series(AUG, ["September 2026"])["September 2026"]
eq("E1 THE DEFECT, reproduced: with no vocabulary September inherits August's payroll to the penny",
   round(sum(r["amount"] for r in _old["rows"]), 2), 276162.69)
cell = EX.effective_series(AUG, ["September 2026"], LV.derived_row_names(HOUSE))["September 2026"]
eq("E2 September still carries, and still says which month it carried from",
   cell["carried_from"], "August 2026")
eq("E3 …but it inherits the RENT only", [r["expense_name"] for r in cell["rows"]], ["Rent"])
eq("E4 what did NOT carry is reported with its dollars, so a surface can SAY why (§19.49)",
   cell["carried_excluded"],
   {"Employee Salaries": 146808.84, "Employee Commission": 12703.71})
_fixed = list(cell["rows"]) + LA.as_expense_rows(SEP_ACTUAL, HOUSE, period="September 2026")
eq("E5 September's total is August's rent plus September's OWN labour",
   round(sum(r["amount"] for r in _fixed), 2), round(116650.14 + 112447.98, 2))
ok("E6 …and it is NOT the figure the defect produced", round(sum(r["amount"] for r in _fixed), 2)
   != 276162.69)
eq("E7 the per-store fold still folds them into ONE store — one month, one answer",
   EX.totals_by_store(_fixed), {"B-1": round(116650.14 + 112447.98, 2)})
_off = EX.effective_series(AUG, ["September 2026"], LV.derived_row_names(OFF))["September 2026"]
eq("E8 a tenant with mode='off' is left exactly as it was — the pre-2026-10-20 behaviour",
   round(sum(r["amount"] for r in _off["rows"]), 2), 276162.69)
eq("E9 …and nothing is added back for it either", LA.as_expense_rows(
    dict(SEP_ACTUAL, halves=LA.wanted_halves(OFF)), OFF), [])
_own = EX.effective_series(AUG, ["August 2026"], LV.derived_row_names(HOUSE))["August 2026"]
eq("E10 a month with its OWN sheet is untouched — the rule only ever concerns a CARRIED month",
   (round(sum(r["amount"] for r in _own["rows"]), 2), _own["carried_from"],
    _own["carried_excluded"]), (276162.69, None, {}))


class _Res:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self._boom:
            raise RuntimeError("commission table unreachable")
        return _Res(self._rows)


class _Schema:
    def __init__(self, tables, boom=()):
        self._t, self._boom = tables, set(boom)

    def table(self, name):
        return _Table(self._t.get(name) or [], name in self._boom)


class _Client:
    def __init__(self, tables, boom=()):
        self._t, self._boom = tables, boom

    def schema(self, name):
        return _Schema(self._t, self._boom)


print("\n§F THE I/O SHELL — `measured` TELLS THE TRUTH, INCLUDING WHEN A READ FAILS")
from app.modules.account import coa as _coa                                        # noqa: E402
_real_wages = _coa.wages_by_store
try:
    _coa.wages_by_store = lambda c, o, p: {"1 S 60TH ST": 110302.73}
    cl = _Client({"rep_commissions": [{"store": "1 S 60TH ST", "total_payout": 2145.25}]})
    got = LA.by_store(cl, "org", "September 2026", HOUSE)
    eq("F1 both halves measured, keyed by the injected identity home",
       (got["measured"], got["by_store"]),
       ({LA.SALARY: True, LA.COMMISSION: True}, {"1 S 60TH ST": 112447.98}))
    eq("F2 the halves are reported apart as well as together", got["by_half"],
       {LA.SALARY: 110302.73, LA.COMMISSION: 2145.25})
    eq("F3 the month it measured rides with the answer", got["period"], "September 2026")
    _b = LA.by_store(_Client({}, boom=("rep_commissions",)), "org", "September 2026", HOUSE)
    eq("F4 A FAILED READ IS AN UNKNOWN HALF, NOT A ZERO — the dangerous direction is the cheap one",
       (_b["measured"][LA.COMMISSION], _b["by_half"].get(LA.COMMISSION)), (False, None))
    eq("F5 …and the half that DID read is still reported", _b["measured"][LA.SALARY], True)
    eq("F6 nothing is materialised for the unmeasured half", 
       {r["expense_name"] for r in LA.as_expense_rows(_b, HOUSE)},
       set(LV.DEFAULT_PAYROLL_ROWS))
    _coa.wages_by_store = lambda c, o, p: (_ for _ in ()).throw(RuntimeError("roster unreachable"))
    _n = LA.by_store(_Client({"rep_commissions": []}), "org", "September 2026", HOUSE)
    eq("F7 a payroll read that raises is reported as unmeasured, and raises nothing at the caller",
       (_n["measured"][LA.SALARY], _n["by_half"].get(LA.SALARY)), (False, None))
    _coa.wages_by_store = lambda c, o, p: {"A": 1.0}
    _s = LA.series(_Client({"rep_commissions": []}), "org",
                   ["August 2026", "September 2026"], HOUSE)
    eq("F8 the series answers for EVERY month asked for — a missing month is never silently absent",
       sorted(_s), ["August 2026", "September 2026"])
    eq("F9 …each month carrying its own verdict",
       {p: _s[p]["measured"][LA.SALARY] for p in _s},
       {"August 2026": True, "September 2026": True})
    eq("F10 mode='off' reads NOTHING at all — no half is claimed, so no query is made",
       LA.by_store(_Client({}, boom=("rep_commissions",)), "org", "September 2026",
                   OFF)["measured"], {})
    eq("F11 no months asked for, nothing invented", LA.series(cl, "org", [], HOUSE), {})
finally:
    _coa.wages_by_store = _real_wages

print("\n§G RULE TWO AND PURITY")
import re as _re


def _code_only(src):
    """The module's CODE — every docstring and comment removed, so a lock cannot be satisfied (or
    broken) by prose. The docstrings here deliberately NAME the thing they forbid."""
    src = _re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', src)
    return "\n".join(ln.split("#")[0] for ln in src.splitlines())


_code = _code_only(LA_SRC)
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay", "total wireless"):
    ok(f"G1 no '{word}' in the module", word not in _code.lower())
for fn in ("fold", "totals_by_store", "as_expense_rows", "wanted_halves"):
    src = _code.split(f"def {fn}(")[1].split("\ndef ")[0]
    ok(f"G2 `{fn}` is pure — no client, no clock, no query",
       not any(t in src for t in ("client", "datetime", "execute(", "schema(")))
ok("G3 the module DERIVES no dollar of its own — no rate times hours anywhere",
   not any(t in _code for t in ("pay_rate", "* hours", "hours *", "hourly_rate")))
ok("G4 the dollars come from the homes that already derive them",
   "wages_by_store(" in _code and "rep_commissions" in _code)
ok("G5 the row names are NOT spelled here — the vocabulary is dereferenced",
   "Employee Salaries" not in _code and "Employee Commission" not in _code
   and "DEFAULT_PAYROLL_ROWS" in _code)
ok("G6 the store identity is injected, so it is §64's home and never a token rule",
   "store_key_of" in _code and not any(t in _code for t in ("squash", ".split()[0]")))
ok("G7 the month is parsed by ONE parser, the same one the P&L hands the carry rule",
   "period_keys" in _code)
ok("G8 the I/O shell is org-scoped",
   '.eq("org_id", org_id)' in _code or ".eq('org_id', org_id)" in _code)

print(f"\nharness_labour_actuals: {PASS} passed, {FAIL} failed")
print("ALL CHECKS PASSED" if not FAIL else "FAILURES ABOVE")
sys.exit(1 if FAIL else 0)
