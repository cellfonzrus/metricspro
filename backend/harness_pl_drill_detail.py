"""Proof harness + LOCK — a P&L scope EXPLAINS ITS OWN NUMBER (owner report 2026-10-07).

Owner, verbatim: *"the details are missing and the details with the drop down does not tie any
information which appears on the summary line, details should be same as on the p&l page, the
expenses should be able to drill down, each line should be able to drill down to get to each level
and finally down to the line level."*

THE CLASS: **a drill-down was accumulated at ONE grain while the statement it explains is rendered
at MANY grains.** `coa.add` kept each line's drill labels in a single org-wide dict, so
`engine._scoped` had nothing per-store to hand a scoped read and did the only honest thing
available to it — `detail = {}`, with the comment "detail is company-wide; only meaningful
consolidated". The consequences, measured live on the house org 2026-10-07:

  · of 40 stored August 2026 P&L scopes, exactly ONE (`consolidated`) carried any drill detail;
  · `store:103 Fulton Ave` (the owner's screenshot) stored `detail: {}` on every one of its lines,
    so the dropdown behind "Store operating expenses $13,585.14" was empty;
  · a store/market FILTERED read sums the matched per-store snapshots in
    `statement_filter.aggregate` — which already summed `detail` correctly, and was therefore
    summing empty dicts forever.

THE FIX, one grain for both: `coa.accrue_detail` records every drill dollar at the SAME grain as the
dollar itself, and each reader SUMS the stores in its own scope. `detail` stays the org-wide roll-up
so consolidated is byte-identical. NOTHING in `statement_filter` changed — it was already right.

  A. THE IDENTITY on the REAL accumulator — Σ detail_by_store + detail_company_wide == detail.
  B. CONSOLIDATED IS BYTE-IDENTICAL — the pre-fix statement, line for line, cent for cent.
  C. A SCOPE EXPLAINS ITSELF — through the REAL `engine._assemble`: a store's detail is its own
     stores' detail, sums to its own amount, and excludes every other store's.
  D. THE FILTERED READ TIES — through the REAL `statement_filter.filtered_statement`: a store
     filter and a market filter carry detail that sums to the line they sit under.
  E. THE REGRESSION — the owner's case: the pre-fix `_scoped` returns an empty drill-down for a
     store scope; the fixed one returns the rows behind that store's own number.
  F. HONESTY — a zero-valued drill row is dropped (it would read as measured); absence stays
     absence; a line with no detail still renders.
  G. THE LOCK — `engine._scoped` has no `detail = {}` for a scope any more, `coa.add` goes through
     the one accumulator, and no second drill-detail derivation exists in `backend/app`.

Stdlib only, no DB, no network. Uses the REAL `coa.PL_SPEC`, `coa.accrue_detail`,
`engine._assemble` and `statement_filter.filtered_statement`.
Run:  cd backend && python3 harness_pl_drill_detail.py
"""
import os
import re
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

from app.modules.account import coa, engine, statement_engine as SE   # noqa: E402
from app.modules.account import statement_filter as SF                # noqa: E402

FAIL = 0
PASS = 0
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SRC = open(os.path.join(HERE, "app/modules/account/engine.py")).read()
COA_SRC = open(os.path.join(HERE, "app/modules/account/coa.py")).read()
SF_SRC = open(os.path.join(HERE, "app/modules/account/statement_filter.py")).read()


def ok(name, cond, detail=None):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + ("" if detail is None else f"   {detail!r}"))


def blank():
    """The shape `coa.build_inputs` returns, at both drill grains."""
    return {k: {"by_store": {}, "company_wide": 0.0, "detail": {},
                "detail_by_store": {}, "detail_company_wide": {}}
            for k, *_ in coa.PL_SPEC + coa.BS_SPEC}


def book(inp, key, store, amt, label=None):
    """Book one dollar EXACTLY as `coa.add` does — the per-store amount plus, through the REAL
    accumulator, the drill label at both grains. Deliberately NOT a re-implementation: the detail
    half is `coa.accrue_detail` itself, which is the function under proof."""
    amt = round(amt, 2)
    if not amt:
        return
    if store:
        inp[key]["by_store"][store] = round(inp[key]["by_store"].get(store, 0.0) + amt, 2)
    else:
        inp[key]["company_wide"] = round(inp[key]["company_wide"] + amt, 2)
    coa.accrue_detail(inp[key], store, label, amt)


def assemble(inp, stores=None, company_wide=True, journal=()):
    return engine._assemble(inp, list(journal), coa.PL_SPEC, coa.PL_LABEL, SE.PL_SECTIONS,
                            "scope", stores, company_wide)


def line_of(pl, key):
    for sec in pl["sections"]:
        for ln in sec["lines"]:
            if ln.get("key") == key:
                return ln
    return None


# ── the owner's own store-month, three stores so a scope can be wrong ────────────────────────────
S1, S2, S3 = "103 Fulton Ave", "1115 Liberty Ave", "6149 Woodland Ave"


def live_inputs():
    inp = blank()
    # B-103's real August 2026 expense rows (verified live 2026-10-07)
    book(inp, "store_opex", S1, 4150.00, "Rent / Lease")
    book(inp, "store_opex", S1, 600.00, "Electric")
    book(inp, "wages", S1, 4479.16, "Employee Salaries")
    book(inp, "wages", S1, 1450.00, "Owner / Mgmt Salaries")
    book(inp, "store_opex", S1, 850.00, "Dm Salary")
    book(inp, "rep_comm", S1, 235.98, "Rep commission")
    # two other stores, so a scoped read that leaks would be caught
    book(inp, "store_opex", S2, 3000.00, "Rent / Lease")
    book(inp, "store_opex", S2, 111.11, "Cleaning")
    book(inp, "wages", S2, 9999.99, "Employee Salaries")
    book(inp, "store_opex", S3, 1.23, "Rent / Lease")
    # a dollar attributable to no store at all
    book(inp, "store_opex", None, 500.00, "Back Office Fee")
    # revenue too, so the proof is not only about expenses
    book(inp, "device_rev", S1, 20000.00)
    book(inp, "device_rev", S2, 15000.00)
    book(inp, "service_income", S1, 588.00, "Bill pay fee")
    return inp


print("\n── A. the identity on the REAL accumulator ──")
inp = live_inputs()
for key in ("store_opex", "wages", "rep_comm", "service_income"):
    ln = inp[key]
    rolled = {}
    for s, d in ln["detail_by_store"].items():
        for k, v in d.items():
            rolled[k] = round(rolled.get(k, 0.0) + v, 2)
    for k, v in ln["detail_company_wide"].items():
        rolled[k] = round(rolled.get(k, 0.0) + v, 2)
    ok(f"A1 {key}: Σ detail_by_store + detail_company_wide == detail",
       rolled == ln["detail"], (rolled, ln["detail"]))
ok("A2 the per-store drill sums to the per-store AMOUNT on each line",
   all(round(sum(inp["store_opex"]["detail_by_store"][s].values()), 2)
       == inp["store_opex"]["by_store"][s] for s in (S1, S2, S3)))
ok("A3 a dollar with no store files as company-wide, not under a store",
   inp["store_opex"]["detail_company_wide"] == {"Back Office Fee": 500.0}
   and all("Back Office Fee" not in d for d in inp["store_opex"]["detail_by_store"].values()))
ok("A4 a booking with NO label touches neither drill grain",
   inp["device_rev"]["detail"] == {} and inp["device_rev"]["detail_by_store"] == {}
   and inp["device_rev"]["by_store"][S1] == 20000.0)
ok("A5 accrue_detail is PURE — same line, same args, additive and total",
   (lambda d: (coa.accrue_detail(d, "X", "L", 1.0), coa.accrue_detail(d, "X", "L", 2.0),
               d["detail"] == {"L": 3.0} and d["detail_by_store"] == {"X": {"L": 3.0}})[-1])(
       {"detail": {}, "detail_by_store": {}, "detail_company_wide": {}}))
ok("A6 accrue_detail never raises on a line dict missing a drill key",
   (lambda d: (coa.accrue_detail(d, "X", "L", 1.0), d.get("detail_by_store") == {"X": {"L": 1.0}})[-1])(
       {"detail": {}}))
ok("A7 a zero amount or a blank label records nothing",
   (lambda d: (coa.accrue_detail(d, "X", "L", 0.0), coa.accrue_detail(d, "X", None, 5.0),
               d["detail"] == {} and d["detail_by_store"] == {})[-1])(
       {"detail": {}, "detail_by_store": {}, "detail_company_wide": {}}))
ok("A8 cents are exact — three thirds of a cent do not drift",
   (lambda d: ([coa.accrue_detail(d, "X", "L", 0.01) for _ in range(3)],
               d["detail"] == {"L": 0.03} and d["detail_by_store"]["X"] == {"L": 0.03})[-1])(
       {"detail": {}, "detail_by_store": {}, "detail_company_wide": {}}))
ok("A9 a NEGATIVE drill row (a correction) is carried at both grains",
   (lambda d: (coa.accrue_detail(d, "X", "L", -40.0),
               d["detail"] == {"L": -40.0} and d["detail_by_store"]["X"] == {"L": -40.0})[-1])(
       {"detail": {}, "detail_by_store": {}, "detail_company_wide": {}}))

print("\n── B. consolidated is BYTE-IDENTICAL to the pre-fix statement ──")


def pre_fix_scoped(line, stores_in_scope, include_company_wide):
    """`engine._scoped` EXACTLY as it stood before this change — the regression baseline."""
    by_store = line["by_store"]
    if stores_in_scope is None:
        amt = sum(by_store.values())
        detail = dict(line["detail"])
    else:
        amt = sum(v for s, v in by_store.items() if s in stores_in_scope)
        detail = {}
    if include_company_wide:
        amt += line["company_wide"]
    return round(amt, 2), {k: round(v, 2) for k, v in detail.items() if v}


for key in ("store_opex", "wages", "rep_comm", "device_rev", "service_income"):
    ok(f"B1 {key}: consolidated (amount, detail) identical to pre-fix",
       engine._scoped(inp[key], None, True) == pre_fix_scoped(inp[key], None, True),
       (engine._scoped(inp[key], None, True), pre_fix_scoped(inp[key], None, True)))
_before = engine._scoped
try:
    engine._scoped = pre_fix_scoped
    pre_pl = assemble(inp, None, True)
finally:
    engine._scoped = _before
ok("B3 the whole consolidated statement is JSON-identical to the pre-fix assembly",
   assemble(inp, None, True) == pre_pl)
ok("B4 a line that writes `detail` directly (device cost basis) still rolls up consolidated",
   (lambda d: (d["detail"].update({"Invoice basis": 123.45}),
               engine._scoped(d, None, True)[1] == {"Invoice basis": 123.45})[-1])(
       {"by_store": {}, "company_wide": 0.0, "detail": {},
        "detail_by_store": {}, "detail_company_wide": {}}))

print("\n── C. a scope explains ITSELF (REAL engine._assemble) ──")
pl1 = assemble(inp, {S1}, False)
op1 = line_of(pl1, "store_opex")
ok("C1 the store's own drill rows, and only its own",
   op1["detail"] == {"Rent / Lease": 4150.0, "Electric": 600.0, "Dm Salary": 850.0}, op1["detail"])
ok("C2 the drill SUMS to the summary line it sits under (what 'tie' means)",
   round(sum(op1["detail"].values()), 2) == op1["amount"] == 5600.0, op1["amount"])
ok("C3 another store's rows do NOT leak in",
   "Cleaning" not in op1["detail"])
ok("C4 a company-wide dollar is excluded from a scope that excludes company-wide dollars",
   "Back Office Fee" not in op1["detail"])
w1 = line_of(pl1, "wages")
ok("C5 the salary line drills to the rows behind it, and ties",
   w1["detail"] == {"Employee Salaries": 4479.16, "Owner / Mgmt Salaries": 1450.0}
   and round(sum(w1["detail"].values()), 2) == w1["amount"] == 5929.16, w1)
pl2 = assemble(inp, {S2}, False)
ok("C6 a different store gets a different, tying drill-down",
   line_of(pl2, "store_opex")["detail"] == {"Rent / Lease": 3000.0, "Cleaning": 111.11}
   and round(sum(line_of(pl2, "store_opex")["detail"].values()), 2) == 3111.11)
pl12 = assemble(inp, {S1, S2}, False)
ok("C7 a MULTI-store scope (a company, a market) sums its members' drill rows",
   line_of(pl12, "store_opex")["detail"]
   == {"Rent / Lease": 7150.0, "Electric": 600.0, "Dm Salary": 850.0, "Cleaning": 111.11})
ok("C8 …and still ties to its own summary line",
   round(sum(line_of(pl12, "store_opex")["detail"].values()), 2)
   == line_of(pl12, "store_opex")["amount"] == 8711.11)
plcw = assemble(inp, {S1}, True)
ok("C9 a scope that INCLUDES company-wide dollars shows them in the drill too, and ties",
   line_of(plcw, "store_opex")["detail"].get("Back Office Fee") == 500.0
   and round(sum(line_of(plcw, "store_opex")["detail"].values()), 2)
   == line_of(plcw, "store_opex")["amount"] == 6100.0)
plnone = assemble(inp, set(), False)
ok("C10 an EMPTY scope shows no drill rows and a measured zero",
   line_of(plnone, "store_opex")["detail"] == {}
   and line_of(plnone, "store_opex")["amount"] == 0.0)
ok("C11 EVERY line of EVERY scope ties: drill sum == line amount, or the line carries no drill",
   all(not ln.get("detail")
       or abs(round(sum(ln["detail"].values()), 2) - round(ln["amount"], 2)) < 0.005
       for stores in ({S1}, {S2}, {S3}, {S1, S2}, {S1, S2, S3})
       for ln in [x for sec in assemble(inp, stores, False)["sections"] for x in sec["lines"]]))
ok("C12 a scope's revenue lines are unaffected (no drill invented where there was none)",
   line_of(pl1, "device_rev")["detail"] == {}
   and line_of(pl1, "device_rev")["amount"] == 20000.0)

print("\n── D. the FILTERED read ties (REAL statement_filter) ──")
SNAPS = {}
for st, key in ((S1, "store:" + S1), (S2, "store:" + S2), (S3, "store:" + S3)):
    SNAPS[key] = assemble(inp, {st}, False)
agg = SF.aggregate([SNAPS["store:" + S1], SNAPS["store:" + S2]], "pl")
ok("D1 the aggregate of two store snapshots sums their drill rows",
   line_of(agg, "store_opex")["detail"]
   == {"Rent / Lease": 7150.0, "Electric": 600.0, "Dm Salary": 850.0, "Cleaning": 111.11},
   line_of(agg, "store_opex")["detail"])
ok("D2 …and ties to the aggregated summary line",
   round(sum(line_of(agg, "store_opex")["detail"].values()), 2)
   == line_of(agg, "store_opex")["amount"] == 8711.11)
ok("D3 a one-store filter equals that store's own scope, drill rows included",
   SF.aggregate([SNAPS["store:" + S1]], "pl") == SNAPS["store:" + S1])
ok("D4 the aggregate equals the scope assembled over the same stores, drill for drill",
   line_of(agg, "store_opex")["detail"] == line_of(assemble(inp, {S1, S2}, False),
                                                   "store_opex")["detail"])
ok("D5 the salary line survives the filter with its breakdown",
   line_of(agg, "wages")["detail"] == {"Employee Salaries": 14479.15,
                                       "Owner / Mgmt Salaries": 1450.0},
   line_of(agg, "wages")["detail"])
ok("D6 statement_filter was ALREADY right and is UNCHANGED by this fix",
   'tgt["detail"][dk] = _r(tgt["detail"].get(dk, 0.0) + safe_float(dv))' in SF_SRC)
ok("D7 an empty filter result carries no drill rows (absence, not a fabricated breakdown)",
   not (line_of(SF.aggregate([], "pl") or {"sections": []}, "store_opex") or {}).get("detail"))

print("\n── E. the regression: the owner's screenshot ──")
pre_amt, pre_detail = pre_fix_scoped(inp["store_opex"], {S1}, False)
post_amt, post_detail = engine._scoped(inp["store_opex"], {S1}, False)
ok("E1 DEFECT REPRODUCED — pre-fix a store scope's drill-down is EMPTY",
   pre_detail == {} and pre_amt == 5600.0)
ok("E2 FIXED — the same scope now carries the rows behind its own number",
   post_detail == {"Rent / Lease": 4150.0, "Electric": 600.0, "Dm Salary": 850.0})
ok("E3 the AMOUNT did not move — this change explains a number, it does not alter one",
   pre_amt == post_amt)
ok("E4 DEFECT REPRODUCED — pre-fix every scope but consolidated stored an empty drill-down",
   all(pre_fix_scoped(inp[k], {S1}, False)[1] == {}
       for k in ("store_opex", "wages", "rep_comm", "service_income")))
ok("E5 FIXED — every one of those scoped lines now carries a tying drill-down",
   all(engine._scoped(inp[k], {S1}, False)[1] != {}
       for k in ("store_opex", "wages", "rep_comm", "service_income")))
ok("E6 the pre-fix comment that documented the defect is gone",
   "detail is company-wide; only meaningful consolidated" not in ENGINE_SRC)

print("\n── F. honesty ──")
inp2 = live_inputs()
book(inp2, "store_opex", S1, 40.0, "Reversed charge")
book(inp2, "store_opex", S1, -40.0, "Reversed charge")
ok("F1 a drill row that nets to zero is DROPPED (a $0.00 row reads as measured)",
   "Reversed charge" not in engine._scoped(inp2["store_opex"], {S1}, False)[1])
ok("F2 …and the summary line is unchanged by the pair",
   engine._scoped(inp2["store_opex"], {S1}, False)[0]
   == engine._scoped(inp["store_opex"], {S1}, False)[0])
ok("F3 a store with no bookings at all has no drill rows (absence stays absence)",
   engine._scoped(inp["store_opex"], {"NOT A STORE"}, False) == (0.0, {}))
ok("F4 a line with no drill mechanism still renders its amount",
   line_of(assemble(inp, {S1}, False), "chargebacks")["amount"] == 0.0)
ok("F5 the drill values are rounded to cents, never raw floats",
   all(round(v, 2) == v for v in engine._scoped(inp["wages"], {S1, S2}, False)[1].values()))

print("\n── G. the lock ──")
ok("G1 engine._scoped no longer discards a scope's detail",
   not re.search(r"detail\s*=\s*\{\}\s*#\s*detail is company-wide", ENGINE_SRC))
ok("G2 engine._scoped reads the per-store drill grain",
   "detail_by_store" in ENGINE_SRC and "detail_company_wide" in ENGINE_SRC)
ok("G3 coa.add accrues drill detail through the ONE accumulator",
   "accrue_detail(L[key], s, detail_label, amt)" in COA_SRC)
ok("G4 build_inputs initialises BOTH drill grains on every spec line",
   '"detail_by_store": {}, "detail_company_wide": {}' in COA_SRC)
# No SECOND derivation: nothing in backend/app may build a per-store drill dict of its own.
offenders = []
for dirpath, _d, files in os.walk(os.path.join(HERE, "app")):
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dirpath, f)
        rel = os.path.relpath(p, HERE)
        if rel.endswith(("account/coa.py", "account/engine.py")):
            continue
        body = open(p, encoding="utf-8", errors="ignore").read()
        if "detail_by_store" in body and "accrue_detail" not in body:
            offenders.append(rel)
ok("G5 NO second drill-detail derivation in backend/app", not offenders, offenders)
ok("G6 statement_filter still has exactly ONE detail summation (no sibling added)",
   SF_SRC.count('tgt["detail"][dk]') == 1)
ok("G7 RULE TWO — no tenant / carrier / store-name branch in the changed logic",
   not re.search(r"(?i)\b(boost|luxelink|nova\s*wave|cellfonz|t-?mobile|verizon)\b",
                 re.sub(r"(?m)^\s*#.*$", "", ENGINE_SRC)))

print()
print(f"harness_pl_drill_detail: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
