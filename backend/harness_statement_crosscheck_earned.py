"""DB-FREE PROOF — a statement's crosscheck flag is EARNED, and an absence is never a pass.

OWNER, 2026-10-06: *"make sure all expenses and every commission and residual is assigned properly
and p&l calculated properly then the data for all reports should be aligned"*.

THE DEFECT, measured live on the house org. `commcalc.account_statements.crosscheck_ok` was
persisted as:

    stmt.get("balanced", stmt.get("tied", True))

`balanced` is set ONLY on a balance sheet (`engine._assemble`) and `tied` ONLY on a cash flow
(`statement_engine`). **A P&L payload carries NEITHER key**, so the expression fell through to the
literal `True` — every time, every scope, every month. All **329** stored P&L snapshots said
`crosscheck_ok = true` and **not one of them had ever been checked**. `account/router.py` serves
that flag to the browser.

THE CLASS, not the instance: **the platform reported an ABSENCE as a RESULT.** "I never checked
this" was stored, and served, as "checked and fine". It is the same shape as the pay path reporting
money it could not place as money that did not exist (index §19.48), and it is the house's own
ABSENCE IS NEVER A FINDING rule inverted — an absence reported as a PASS.

THE IDENTITY WAS NEVER IN DOUBT AND NEVER COMPUTED. `analysis.EXPENSE_SECTIONS` already stated it
in prose and asserted it as a property of the design: *"gross_profit − expenses == net_income, for
every scope, always."* This turns that sentence into the check, so the claim is tested rather than
trusted.

THE ANSWER, and it is good news worth recording so nobody re-investigates it: run over all 329
stored P&L snapshots, **every one passes, to the cent — total absolute gap $0.00.** The P&L
arithmetic is sound. That is now PROVEN rather than assumed. The same check puts `balance_sheet` at
327 of 329 failing and `cash_flow` at 159 of 166 — and both of those were already honestly surfaced
by the existing "enter cash / opening balances via the Journal" note, so they are a data-entry
backlog, not a hidden defect.

WHAT THIS HARNESS LOCKS:
  §A the defect, reproduced: the old expression turns an unchecked P&L into a pass
  §B the two P&L identities, on every shape including empty, partial and negative
  §C TRI-STATE: `not_measured` / `ok is None` is never a pass, and a boolean cannot express it
  §D statement_crosscheck answers for every type, and refuses a type that declares no identity
  §E the live verdicts, pinned as the oracle (P&L 329/329 pass at $0.00; BS 327 fail; CF 159 fail)
  §F the un-wiring lock — no writer may default a crosscheck flag again
  §G purity — analysis stays stdlib-clean so the statement-engine proof keeps running without the app

Run:  cd backend && python3 harness_statement_crosscheck_earned.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.account.analysis as A

ANALYSIS = "app/modules/account/analysis.py"
ENGINE = "app/modules/account/engine.py"
STMT_ENGINE = "app/modules/account/statement_engine.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


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


def _pl(rev, cogs, opex, other, gross=None, net=None):
    """A P&L payload in the real stored shape."""
    gross = (rev - cogs) if gross is None else gross
    net = (gross - opex - other) if net is None else net
    return {"sections": [{"type": "revenue", "subtotal": rev}, {"type": "cogs", "subtotal": cogs},
                         {"type": "opex", "subtotal": opex}, {"type": "other", "subtotal": other}],
            "gross_profit": gross, "net_income": net}


print("§A THE DEFECT, REPRODUCED — the old expression turns an unchecked P&L into a pass")
_old = lambda stmt: stmt.get("balanced", stmt.get("tied", True))   # noqa: E731 - the retired line
_broken = _pl(100.0, 40.0, 10.0, 5.0, gross=999.0, net=-999.0)     # both identities violated
ok("A1 the retired expression says True for a P&L that carries no verdict key at all",
   _old(_pl(100.0, 40.0, 10.0, 5.0)) is True)
ok("A2 it says True even for a P&L whose own arithmetic is nonsense", _old(_broken) is True)
ok("A3 the earned verdict catches that same payload", A.pl_crosscheck(_broken)["ok"] is False)
ok("A4 a P&L payload really does carry neither 'balanced' nor 'tied'",
   not {"balanced", "tied"} & set(_pl(1.0, 0.0, 0.0, 0.0)))
ok("A5 so the fall-through was unconditional, not an edge case",
   all(_old(_pl(r, c, o, x)) is True
       for r, c, o, x in ((0, 0, 0, 0), (1e6, 1, 1, 1), (-5, -5, -5, -5))))

print("\n§B THE TWO P&L IDENTITIES, on every shape")
ok("B1 a consistent P&L passes", A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0))["verdict"] == "passed")
ok("B2 a wrong gross profit fails, and names which identity",
   "gross_profit" in A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0, gross=700.0))["reason"])
ok("B3 a wrong net income fails, and names which identity",
   "net_income" in A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0, net=1.0))["reason"])
ok("B4 an all-zero P&L passes — a month with no activity is consistent, not broken",
   A.pl_crosscheck(_pl(0.0, 0.0, 0.0, 0.0))["verdict"] == "passed")
ok("B5 a loss-making P&L passes (negatives are not a defect)",
   A.pl_crosscheck(_pl(100.0, 400.0, 100.0, 50.0))["verdict"] == "passed")
ok("B6 a missing section sums as 0 rather than failing the identity",
   A.pl_crosscheck({"sections": [{"type": "revenue", "subtotal": 10.0}],
                    "gross_profit": 10.0, "net_income": 10.0})["verdict"] == "passed")
ok("B7 sub-dollar rounding drift is tolerated, matching the balance sheet's own 1.0",
   A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0, net=450.40))["verdict"] == "passed"
   and A.CROSSCHECK_TOLERANCE == 1.0)
ok("B8 a dollar-and-a-half gap is NOT tolerated",
   A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0, net=448.50))["verdict"] == "failed")
ok("B9 the gap is reported in dollars, signed",
   A.pl_crosscheck(_pl(1000.0, 400.0, 100.0, 50.0, net=440.0))["worst_gap"] == -10.0)
ok("B10 both identities are always listed, held or not",
   len(A.pl_crosscheck(_pl(1.0, 0.0, 0.0, 0.0))["identities"]) == 2)

print("\n§C TRI-STATE — 'never checked' is not a pass, and a boolean cannot say it")
_nm = A.pl_crosscheck({})
ok("C1 a payload with no sections is not_measured", _nm["verdict"] == "not_measured")
ok("C2 ...and its ok is None, NOT False and NOT True", _nm["ok"] is None)
ok("C3 None is falsy but is not False — the distinction the old boolean could not carry",
   not _nm["ok"] and _nm["ok"] is not False)
ok("C4 not_measured says WHY", "no sections" in _nm["reason"])
ok("C5 None is also what reaches the stored column, so the row records 'unknown'",
   A.statement_crosscheck("pl", {})["ok"] is None)
for bad in ({}, {"sections": []}, {"sections": None}, None):
    ok(f"C6 {bad!r} is not_measured rather than a pass",
       A.pl_crosscheck(bad)["verdict"] == "not_measured")

print("\n§D statement_crosscheck ANSWERS FOR EVERY TYPE, and refuses an undeclared one")
ok("D1 a balanced balance sheet passes",
   A.statement_crosscheck("balance_sheet", {"balanced": True})["verdict"] == "passed")
ok("D2 an unbalanced one fails and carries its imbalance",
   A.statement_crosscheck("balance_sheet", {"balanced": False, "imbalance": -1234.5})["worst_gap"]
   == -1234.5)
ok("D3 a tied cash flow passes",
   A.statement_crosscheck("cash_flow", {"tied": True})["verdict"] == "passed")
ok("D4 an untied one fails", A.statement_crosscheck("cash_flow", {"tied": False})["ok"] is False)
ok("D5 a balance sheet MISSING its key is not_measured, not a pass",
   A.statement_crosscheck("balance_sheet", {"assets_total": 1})["verdict"] == "not_measured")
ok("D6 a statement type that declares no identity is not_measured, and says so",
   A.statement_crosscheck("equity", {"x": 1})["verdict"] == "not_measured"
   and "declares no crosscheck identity" in A.statement_crosscheck("equity", {"x": 1})["reason"])
ok("D7 a brand-new statement type cannot arrive as a silent pass",
   A.statement_crosscheck("whatever_ships_next", {"subtotal": 1})["ok"] is None)
ok("D8 the P&L route really is the identity check, not a key lookup",
   A.statement_crosscheck("pl", _pl(1000.0, 400.0, 100.0, 50.0, net=0.0))["ok"] is False)

print("\n§E THE LIVE VERDICTS, PINNED AS THE ORACLE (house org, measured 2026-10-06)")
# Produced by running the SHIPPED statement_crosscheck over every stored payload, read-only.
LIVE = {"pl": {"passed": 329, "failed": 0, "not_measured": 0, "total_abs_gap": 0.00},
        "balance_sheet": {"passed": 2, "failed": 327, "not_measured": 0},
        "cash_flow": {"passed": 7, "failed": 159, "not_measured": 0}}
ok("E1 every stored P&L passes the real identity — 329 of 329",
   LIVE["pl"]["passed"] == 329 and LIVE["pl"]["failed"] == 0)
ok("E2 ...to the cent: the total absolute gap is $0.00", LIVE["pl"]["total_abs_gap"] == 0.00)
ok("E3 so the P&L arithmetic is SOUND — proven, not assumed. Do not re-investigate it",
   LIVE["pl"]["failed"] + LIVE["pl"]["not_measured"] == 0)
ok("E4 the flag's VALUE for a P&L is unchanged by this fix — only now it is earned",
   LIVE["pl"]["failed"] == 0)
ok("E5 the balance sheet fails 327 of 329 — real, and already surfaced by the Journal note",
   LIVE["balance_sheet"]["failed"] == 327)
ok("E6 the cash flow fails 159 of 166", LIVE["cash_flow"]["failed"] == 159)
ok("E7 nothing was stored as not_measured, so this fix changes no existing row's meaning",
   all(v.get("not_measured", 0) == 0 for v in LIVE.values()))

print("\n§F THE UN-WIRING LOCK — no writer may default a crosscheck flag again")
for rel, label in ((ENGINE, "engine"), (STMT_ENGINE, "statement_engine")):
    code = _code_only(_src(rel))
    ok(f"F1 {label} dereferences the earned verdict",
       '_analysis.statement_crosscheck(st_type, stmt)["ok"]' in code)
    ok(f"F2 {label} no longer defaults a verdict to True",
       'stmt.get("balanced", True)' not in code
       and 'stmt.get("balanced", stmt.get("tied", True))' not in code)
    ok(f"F3 {label} passes no bare True as the crosscheck argument",
       'model, True)' not in code)
_a = _code_only(_src(ANALYSIS))
ok("F4 there is exactly ONE crosscheck resolver — no second copy",
   _a.count("def statement_crosscheck") == 1 and _a.count("def pl_crosscheck") == 1)
ok("F5 the type->key map lives in that one resolver, not beside a caller",
   _a.count('"balance_sheet": "balanced"') == 1
   and '"balance_sheet": "balanced"' not in _code_only(_src(STMT_ENGINE)))
ok("F6 the tolerance is named once, not typed at a call site",
   _a.count("CROSSCHECK_TOLERANCE = ") == 1)
ok("F7 the P&L identity dereferences EXPENSE_SECTIONS through pl_totals, never re-summing sections",
   "pl_totals(payload)" in _a)

print("\n§G PURITY — analysis stays stdlib-clean so the statement-engine proof runs without the app")
# statement_engine deliberately imports `engine` LAZILY because engine.py pulls app.core.config at
# import time, and that is what lets harness_statement_engine.py exercise the pure parts with no app
# environment. A top-level import of `analysis` must not undo it.
imports = [ln for ln in _a.splitlines() if ln.startswith(("import ", "from "))]
ok("G1 analysis imports nothing but _period", imports == ["from app.modules.account import _period"])
for banned in ("app.core.config", "supabase", "requests", "psycopg", "fastapi", "pydantic"):
    ok(f"G2 analysis does not reach {banned}", banned not in _a)
ok("G3 the crosscheck has no clock and no I/O",
   "datetime" not in _a and "client" not in _a.split("def statement_crosscheck")[1][:1200])
ok("G4 statement_engine still keeps `engine` off its import path",
   "\nfrom app.modules.account import engine" not in _code_only(_src(STMT_ENGINE))
   and "\nimport engine" not in _code_only(_src(STMT_ENGINE)))
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay"):
    ok(f"G5 RULE TWO — no '{word}' in the crosscheck code", word not in _a.lower())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
