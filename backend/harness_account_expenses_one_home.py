"""Proof harness — the Account hub's EXPENSES column has ONE home (owner request 2026-09-21).

Owner: *"in teh finance accout module add expenses column also and let each store be drilled down to
get more details"*.

THE DEFECT THIS HARNESS EXISTS TO PREVENT is the one that cost a night last week: two surfaces
deriving the same money independently and disagreeing. An Expenses column on the Account hub could
have been summed from `commcalc.store_expenses` while the P&L shows BOOKED expense lines from the
chart of accounts — and the two would part company the first time a dollar was reclassified. So the
column is not a derivation at all: `account/analysis.pl_totals` owns "expenses on a statement
payload" once, as Σ `analysis.EXPENSE_SECTIONS` subtotals, and `GET /account/overview/{period}`
dereferences it.

Stdlib only, no DB, no network. Runs the REAL `engine._assemble` and the REAL
`statement_filter.filtered_statement` assembly so the identities are proven on genuinely assembled
statements, not on hand-written dicts.

  A. THE HOME — pl_totals carries `expenses` = Σ EXPENSE_SECTIONS, rounded to cents; the pre-existing
     keys are untouched; an empty/garbage payload degrades to 0.0 without raising.
  B. THE IDENTITY on REAL assembly — for statements built by `engine._assemble` over the REAL
     `coa.PL_SPEC`: gross_profit − expenses == net_income, at consolidated, company and store grain,
     with journal entries, negative lines and an empty scope.
  C. THE COLUMN IS THE P&L — for every scope, the number `overview` publishes equals the expense
     total a reader adds up off the P&L page for that same scope (the rendered section subtotals),
     and equals it again after the store/market re-attribution `/accounts/pl` performs
     (`statement_filter`), so the hub, the P&L and the FILTERED P&L are one number.
  D. NO SECOND DERIVATION — `expenses` is never GP − NI arithmetic and never a source-table sum:
     proven by moving a dollar BETWEEN two expense lines (the reclassification case) and by moving a
     dollar from COGS to OPEX, then checking what each surface reports.
  E. THE LOCK (fails the build if the wiring comes undone):
       E1 `router.overview` calls `analysis.pl_totals` and no longer re-sums the revenue sections;
       E2 `_consolidated_pl` (the narrative) dereferences the same home;
       E3 EXPENSE_SECTIONS == exactly the below-gross-profit section types of
          `statement_engine.PL_SECTIONS` — a NEW expense section in the statement fails here until
          it is registered in the one home;
       E4 the section types the engine assembles are UNIQUE (the one-home sum reads each once);
       E5 the frontend mirror `_components/scopeFinancials.ts` declares the SAME tuple, and the hub
          page reads `expenses` off the payload rather than computing one.
  F. ABSENCE IS NOT ZERO — a scope with no P&L snapshot publishes NO `expenses` key (the hub renders
     "not reported"); a scope whose P&L reports no expenses publishes 0.0.

Run:  cd backend && python3 harness_account_expenses_one_home.py
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

from app.modules.account import analysis, coa, engine, statement_engine as SE  # noqa: E402
from app.modules.account import statement_filter as SF  # noqa: E402

FAIL = 0
PASS = 0
HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER_SRC = open(os.path.join(HERE, "app/modules/account/router.py")).read()
FE_DIR = os.path.abspath(os.path.join(HERE, "..", "frontend/src/app/(platform)/accounts"))


def ok(name, cond, detail=None):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + ("" if detail is None else f"   {detail!r}"))


def blank_inputs():
    """Every spec line at zero — the shape `coa.build_inputs` returns."""
    return {k: {"by_store": {}, "company_wide": 0.0, "detail": {}} for k, *_ in coa.PL_SPEC + coa.BS_SPEC}


def put(inp, key, store, amount, detail=None):
    inp[key]["by_store"][store] = inp[key]["by_store"].get(store, 0.0) + amount
    if detail:
        for d, v in detail.items():
            inp[key]["detail"][d] = inp[key]["detail"].get(d, 0.0) + v


def put_company(inp, key, amount):
    inp[key]["company_wide"] = inp[key].get("company_wide", 0.0) + amount


def assemble(inp, journal=(), stores=None, company_wide=True):
    return engine._assemble(inp, list(journal), coa.PL_SPEC, coa.PL_LABEL, SE.PL_SECTIONS,
                            "scope", stores, company_wide)


def rendered_expense_total(pl):
    """What a reader ADDS UP off the P&L page: the section subtotals the page prints below Gross
    Profit ("Subtotal — Operating Expenses", "Subtotal — Other"). Deliberately written the way the
    page renders it, NOT by calling the home — that is the point of the cross-check."""
    total = 0.0
    for sec in pl["sections"]:
        if sec["type"] in ("opex", "other"):
            total += sec["subtotal"]
    return round(total, 2)


print("\n── A. the home: pl_totals carries `expenses` ──")
pay = {"sections": [{"type": "revenue", "subtotal": 1000.0, "lines": []},
                    {"type": "cogs", "subtotal": 400.0, "lines": []},
                    {"type": "opex", "subtotal": 250.55, "lines": []},
                    {"type": "other", "subtotal": 49.45, "lines": []}],
       "gross_profit": 600.0, "net_income": 300.0}
t = analysis.pl_totals(pay)
ok("expenses = opex + other", t["expenses"] == 300.0, t)
ok("pre-existing keys untouched",
   (t["revenue"], t["cogs"], t["opex"], t["other"], t["gross_profit"], t["net_income"])
   == (1000.0, 400.0, 250.55, 49.45, 600.0, 300.0), t)
ok("expenses is rounded to cents (float addition never leaks into the column)",
   analysis.pl_totals({"sections": [{"type": "opex", "subtotal": 0.1, "lines": []},
                                    {"type": "other", "subtotal": 0.2, "lines": []}]})["expenses"] == 0.3)
ok("empty payload degrades to 0.0 without raising", analysis.pl_totals({})["expenses"] == 0.0)
ok("garbage payload degrades to 0.0 without raising", analysis.pl_totals(None)["expenses"] == 0.0)
ok("a payload with NO expense section reports 0.0, not a crash",
   analysis.pl_totals({"sections": [{"type": "revenue", "subtotal": 5.0, "lines": []}]})["expenses"] == 0.0)

print("\n── B. the identity on REAL assembly: GP − expenses == NI ──")
inp = blank_inputs()
put(inp, "device_rev", "S1", 20000.0)
put(inp, "accessory_rev", "S1", 3000.0)
put(inp, "device_cost", "S1", 14000.0)
put(inp, "accessory_cost", "S1", 600.0)
put(inp, "device_rebate", "S1", -1200.0)             # contra-COGS, negative by design (ruling K1)
put(inp, "wages", "S1", 4100.0)
put(inp, "rep_comm", "S1", 812.34)
put(inp, "store_opex", "S1", 2933.21, {"Rent": 2000.0, "Utilities": 933.21})
put(inp, "chargebacks", "S1", -150.0)                # a clawback reversal — negative expense
put(inp, "device_rev", "S2", 9000.0)
put(inp, "store_opex", "S2", 1500.0)
put(inp, "wages", "S2", 2200.0)
put_company(inp, "mi_income", 7500.0)
put_company(inp, "carrier_comm", 15000.0)

journal = [{"statement": "pl", "account_type": "opex", "account_line": "Insurance", "amount": 410.0},
           {"statement": "pl", "account_type": "other", "account_line": "Interest expense", "amount": 95.5},
           {"statement": "pl", "account_type": "revenue", "account_line": "Other income", "amount": 250.0}]

cases = {
    "consolidated": assemble(inp, journal, None, True),
    "company (both stores, company-wide lines)": assemble(inp, journal, {"S1", "S2"}, True),
    "store S1": assemble(inp, journal, {"S1"}, False),
    "store S2": assemble(inp, journal, {"S2"}, False),
    "store with nothing in it": assemble(inp, journal, {"S3"}, False),
}
for name, pl in cases.items():
    tot = analysis.pl_totals(pl)
    ok(f"{name}: GP − expenses == NI",
       round(tot["gross_profit"] - tot["expenses"], 2) == round(tot["net_income"], 2),
       (tot["gross_profit"], tot["expenses"], tot["net_income"]))
    ok(f"{name}: expenses == what the P&L page prints",
       tot["expenses"] == rendered_expense_total(pl), (tot["expenses"], rendered_expense_total(pl)))

s1 = analysis.pl_totals(cases["store S1"])
ok("store S1 expenses are the real sum of its expense lines",
   s1["expenses"] == round(4100.0 + 812.34 + 2933.21 - 150.0 + 410.0 + 95.5, 2), s1["expenses"])
ok("a negative (reversed) expense line REDUCES the column — it is not abs()'d",
   s1["expenses"] < round(4100.0 + 812.34 + 2933.21 + 410.0 + 95.5, 2))
ok("an empty store reports MEASURED zero (a number), not absence",
   analysis.pl_totals(cases["store with nothing in it"])["expenses"] == round(410.0 + 95.5, 2))

print("\n── C. the column IS the P&L, including the filtered P&L ──")
# The /accounts/pl page re-attributes to selected stores by SUMMING the per-store snapshots
# (statement_filter). The hub's per-store columns must survive that route unchanged.
snap_s1 = assemble(inp, [], {"S1"}, False)
snap_s2 = assemble(inp, [], {"S2"}, False)
merged = SF.aggregate([snap_s1, snap_s2], "pl")      # the REAL re-attribution /accounts/pl performs
if merged is not None:
    mt = analysis.pl_totals(merged)
    ok("filtered (2 stores) expenses == sum of the two store columns",
       mt["expenses"] == round(analysis.pl_totals(snap_s1)["expenses"]
                               + analysis.pl_totals(snap_s2)["expenses"], 2),
       (mt["expenses"], analysis.pl_totals(snap_s1)["expenses"], analysis.pl_totals(snap_s2)["expenses"]))
    ok("filtered statement keeps the identity GP − expenses == NI",
       round(mt["gross_profit"] - mt["expenses"], 2) == round(mt["net_income"], 2), mt)
    ok("filtered expenses == what the filtered P&L page prints",
       mt["expenses"] == rendered_expense_total(merged))

print("\n── D. no second derivation (the reclassification case) ──")
# Move $500 from `store_opex` to `wages` — the exact event that breaks a hub column sourced from a
# raw expense feed. The statement's expense TOTAL must not move by a cent.
inp2 = blank_inputs()
put(inp2, "device_rev", "S1", 20000.0)
put(inp2, "store_opex", "S1", 3000.0)
put(inp2, "wages", "S1", 1000.0)
before = analysis.pl_totals(assemble(inp2, [], {"S1"}, False))
inp3 = blank_inputs()
put(inp3, "device_rev", "S1", 20000.0)
put(inp3, "store_opex", "S1", 2500.0)
put(inp3, "wages", "S1", 1500.0)
after = analysis.pl_totals(assemble(inp3, [], {"S1"}, False))
ok("reclassifying between two expense lines moves the column by $0.00",
   before["expenses"] == after["expenses"] == 4000.0, (before["expenses"], after["expenses"]))
ok("…and net income is likewise unchanged", before["net_income"] == after["net_income"])

# Move $500 from COGS to OPEX: expenses MUST rise by exactly 500 and GP fall by exactly 500 — the
# column follows the chart of accounts, it is not GP − NI arithmetic (which would be blind to this).
inp4 = blank_inputs()
put(inp4, "device_rev", "S1", 20000.0)
put(inp4, "device_cost", "S1", 5000.0)
put(inp4, "store_opex", "S1", 1000.0)
base = analysis.pl_totals(assemble(inp4, [], {"S1"}, False))
inp5 = blank_inputs()
put(inp5, "device_rev", "S1", 20000.0)
put(inp5, "device_cost", "S1", 4500.0)
put(inp5, "store_opex", "S1", 1500.0)
moved = analysis.pl_totals(assemble(inp5, [], {"S1"}, False))
ok("COGS → OPEX raises expenses by exactly the amount moved",
   round(moved["expenses"] - base["expenses"], 2) == 500.0)
ok("…and raises gross profit by exactly the same, leaving net income flat",
   round(moved["gross_profit"] - base["gross_profit"], 2) == 500.0
   and base["net_income"] == moved["net_income"])

print("\n── E. the lock: the wiring cannot come undone silently ──")
ov = ROUTER_SRC[ROUTER_SRC.index('@router.get("/overview/{period}")'):]
ov = ov[:ov.index('@router.get("/overview/{period}/narrative")')]
ok("E1a overview dereferences analysis.pl_totals", "analysis.pl_totals(p)" in ov)
ok("E1b overview publishes the expenses column", 's["expenses"] = t["expenses"]' in ov)
ok("E1c overview no longer re-sums the revenue sections itself",
   'sec["type"] == "revenue"' not in ov and "sec.get(\"type\") == \"revenue\"" not in ov)
ok("E1d overview computes no expense total of its own",
   not re.search(r'"opex"|"other"', ov))
cons = ROUTER_SRC[ROUTER_SRC.index("def _consolidated_pl("):]
cons = cons[:cons.index("def _account_narrative(")]
ok("E2 the narrative reads the same home", "analysis.pl_totals(" in cons
   and 'sec.get("type") == "revenue"' not in cons)

below_gp = tuple(t for _, t in SE.PL_SECTIONS if t not in ("revenue", "cogs"))
ok("E3 EXPENSE_SECTIONS == the statement's below-gross-profit sections",
   analysis.EXPENSE_SECTIONS == below_gp, (analysis.EXPENSE_SECTIONS, below_gp))
types_ = [t for _, t in SE.PL_SECTIONS]
ok("E4 statement section types are unique (the home reads each exactly once)",
   len(types_) == len(set(types_)), types_)
ok("E4b every expense section type really exists on an assembled statement",
   set(analysis.EXPENSE_SECTIONS) <= {s["type"] for s in cases["consolidated"]["sections"]})

def code_only(src):
    """Strip // and /* */ comments — a RULE TWO / no-second-source check must read CODE, not the
    prose explaining why the code does not do the thing."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.splitlines())


fe_helper = open(os.path.join(FE_DIR, "_components/scopeFinancials.ts")).read()
m = re.search(r"export const EXPENSE_SECTIONS = \[([^\]]*)\]", fe_helper)
fe_tuple = tuple(x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()) if m else ()
ok("E5a the frontend mirror declares the SAME expense sections",
   fe_tuple == analysis.EXPENSE_SECTIONS, (fe_tuple, analysis.EXPENSE_SECTIONS))
hub = open(os.path.join(FE_DIR, "page.tsx")).read()
ok("E5b the hub READS `expenses` off the payload (scopeExpenses), never recomputing one",
   "scopeExpenses(" in hub
   and "gross_profit - " not in hub and "gross_profit -" not in hub.replace("gross_profit - expenses", ""))
hub_new = hub[hub.index("function ExpenseCell("):]          # the code this PR added
ok("E5c the drill-down reads the canonical /account/pl endpoint, adds no new one",
   "scopeStatementPath(" in code_only(hub_new) and "/api/v1/account/pl/" in code_only(fe_helper)
   and "store_expenses" not in code_only(hub) and "store_expenses" not in code_only(fe_helper)
   and "/api/v1/" not in code_only(hub_new))          # the panel builds no URL of its own
ok("E5d RULE TWO — no tenant/carrier/store-name branch in the code this PR adds",
   not re.search(r"(?i)\b(boost|luxelink|nova\s*wave|cellfonz|t-?mobile|verizon)\b",
                 code_only(hub_new) + code_only(fe_helper)))

print("\n── F. absence is not zero ──")
# The overview only writes the key when a P&L row exists for that scope — the `if` is the honesty.
ok("F1 overview writes `expenses` only inside the pl branch",
   ov.index('s["expenses"]') > ov.index('if r["statement_type"] == "pl":'))
ok("F2 the hub renders absence as 'not reported', never $0.00",
   "not reported" in hub and "reported: false" in fe_helper)
ok("F3 a MEASURED zero is still a number (0.0), not absence",
   analysis.pl_totals(assemble(blank_inputs(), [], {"SX"}, False))["expenses"] == 0.0)
ok("F4 the export leaves an unmeasured cell BLANK rather than $0.00",
   "scopeExpenses(r).reported ? r.expenses : null" in hub)

print()
print(f"harness_account_expenses_one_home: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
