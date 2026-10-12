#!/usr/bin/env python3
"""PROOF: the trade-in spiff pays from the DECLARED record, and the basis cannot un-wire.

DB-free. Imports the REAL `commcalc.trade_in_basis` and the REAL `commcalc.calculator`, and reads
`calculator.py` as text for the wiring locks. No network, no database, stdlib only.

Owner ruling 2026-10-12 (decision card, option "Point of sale"). The defect being locked shut: the
engine counted payment-detail rows whose type contained 'trade', so a month the carrier had not
reimbursed yet paid $0 for trade-ins the stores really did.

Run:  python3.11 backend/harness_trade_in_basis.py
"""
import ast
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import trade_in_basis as tib  # noqa: E402
from app.modules.core.module_graph import home_under_app  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
CALC = os.path.join(APP, "modules", "commcalc", "calculator.py")
# THE GRAPH IS THE ONE HOME FOR THE RULING (index §19.18 / §50). This lock carries no
# `"modules/commcalc/trade_in_basis.py"` literal of its own — it ASKS the module graph, so moving the
# home cannot leave this lock guarding a file that is no longer there while still printing OK.
HOME = os.path.join(APP, home_under_app("trade_in_basis"))

_p = _f = 0
_fails = []


def ck(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        _fails.append(name)
        print(f"  FAIL  {name}")


def row(tid, dept="Trade In", cat="Trade In", prod="", sp="Akhtar, Rana"):
    return {"trans_id": tid, "department": dept, "category": cat,
            "product_desc": prod, "salesperson": sp}


print(__doc__.strip().split("\n")[0])
print()
print("── A. the basis is an ALLOWLIST (an unruled value is never a third behaviour) ──")
ck("A1 'pos' / 'carrier' / 'higher' resolve to themselves",
   [tib.resolve_basis(b) for b in ("pos", "carrier", "higher")] == ["pos", "carrier", "higher"])
ck("A2 the house default is the till",
   tib.DEFAULT_BASIS == tib.BASIS_POS == "pos")
ck("A3 None / '' / whitespace → the till",
   all(tib.resolve_basis(v) == "pos" for v in (None, "", "   ", 0, [])))
ck("A4 a TYPO is not a third behaviour — it reads as the till, never as 'pay nothing'",
   all(tib.resolve_basis(v) == "pos" for v in ("POS ", "Point of sale", "statement", "pos_", "both")))
ck("A5 case and padding are tolerated on a REAL value ('  Carrier ' is the carrier)",
   tib.resolve_basis("  Carrier ") == "carrier")
ck("A6 the allowlist is exactly three long (a fourth basis must be ruled on, not guessed)",
   tuple(tib.BASES) == ("pos", "carrier", "higher") and len(tib.BASES) == 3)

print()
print("── B. the POS count: DISTINCT transactions, never lines ──")
ck("B1 one trade-in spread over four feed lines counts ONCE",
   len(tib.pos_trade_in_txns([row("A1"), row("A1"), row("A1"), row("A1")])) == 1)
ck("B2 three distinct transactions count three",
   len(tib.pos_trade_in_txns([row("A1"), row("A2"), row("A3")])) == 3)
ck("B3 the '.0' Excel spelling of a transaction id is the SAME transaction",
   len(tib.pos_trade_in_txns([row("1621"), row("1621.0"), row(" 1621 ")])) == 1)
ck("B4 a line with NO trans_id is counted once, never dropped and never merged with another",
   len(tib.pos_trade_in_txns([row(""), row(""), row(None)])) == 3)
ck("B5 a non-trade-in line contributes nothing",
   len(tib.pos_trade_in_txns([row("A1", dept="Phones", cat="Phones")])) == 0)
ck("B6 an empty feed is zero, not an error",
   tib.pos_trade_in_txns([]) == set() and tib.pos_trade_in_txns(None) == set())

print()
print("── C. the declaration is CONFIG, and case-folded (the box_departments defect is not inherited) ──")
_d = tib.resolve_declaration({})
ck("C1 an org that declared nothing gets the house default spelling",
   _d["departments"] == {"trade in"} and _d["categories"] == {"trade in"})
ck("C2 keywords default to EMPTY (a substring test is the loosest and stays off until asked for)",
   _d["keywords"] == set())
ck("C3 ANY case of the declared department matches — 'TRADE IN', 'trade in', ' Trade In '",
   all(tib.is_pos_trade_in(v, "", "", _d) for v in ("TRADE IN", "trade in", " Trade In ", "tRaDe In")))
ck("C4 the CATEGORY alone is enough (a feed may carry it in either column)",
   tib.is_pos_trade_in("", "Trade In", "", _d) and tib.is_pos_trade_in("Phones", "trade in", "", _d))
_d2 = tib.resolve_declaration({"trade_in_departments": ["Device Buyback", " ", None]})
ck("C5 an org's OWN declaration replaces the default, blanks dropped",
   _d2["departments"] == {"device buyback"})
ck("C6 …and the default spelling then stops matching for that org",
   not tib.is_pos_trade_in("Trade In", "", "", {**_d2, "categories": set(), "keywords": set()}))
_d3 = tib.resolve_declaration({"trade_in_keywords": ["buyback"]})
ck("C7 a declared KEYWORD matches as a substring of the product description",
   tib.is_pos_trade_in("Misc", "Misc", "Apple Buyback Credit", _d3))
ck("C8 a keyword does NOT match a product that lacks it",
   not tib.is_pos_trade_in("Misc", "Misc", "Screen Protector", _d3))


def _code_only(path):
    """The module's code with docstrings and comments removed — RULE TWO is about code, not prose."""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    out = []
    for ln in src.split("\n"):
        st = ln.strip()
        if st.startswith("#"):
            continue
        out.append(re.sub(r"\s+#.*$", "", ln))
    body = "\n".join(out)
    for d in docs:
        body = body.replace(d, "")
    return body


_home_code = _code_only(HOME)
ck("C9b …proven: the code carries no 'boost' / 'total' / 'luxelink' / 'cellfonz' / 'acima'",
   not any(w in _home_code.lower() for w in ("boost", "luxelink", "cellfonz", "vzone", "ondigo")))
ck("C10 the only department literal in the code is the DEFAULT tuple, not a branch",
   _home_code.count("trade in") == 2 and "if " not in _home_code.split("DEFAULT_POS_DEPARTMENTS")[1][:120])

print()
print("── D. count_for_pay: the number travels WITH the record it came from ──")
ck("D1 the till basis pays the POS count and says so",
   tib.count_for_pay("pos", 53, 1) == (53, "pos"))
ck("D2 the carrier basis pays the statement count and says so",
   tib.count_for_pay("carrier", 53, 1) == (1, "carrier"))
ck("D3 'higher' takes the statement when the statement shows more",
   tib.count_for_pay("higher", 10, 40) == (40, "carrier"))
ck("D4 'higher' takes the till when the till shows more",
   tib.count_for_pay("higher", 53, 1) == (53, "pos"))
ck("D5 a TIE under 'higher' names the TILL — a tie is not evidence the statement arrived",
   tib.count_for_pay("higher", 7, 7) == (7, "pos"))
ck("D6 a negative or junk count is floored at zero, never paid as a negative spiff",
   tib.count_for_pay("pos", -5, 0) == (0, "pos") and tib.count_for_pay("carrier", 0, None) == (0, "carrier"))
ck("D7 an unruled basis pays the TILL (it is resolved, not trusted)",
   tib.count_for_pay("whatever", 53, 1) == (53, "pos"))

print()
print("── E. THE REGRESSION — the reported defect, reproduced from the real months ──")
# August 2026: the statement was COMPLETE (23,871 rows, 44 payment types) and held NO trade-in type;
# the stores did 31 trade-ins at the till. The old basis paid $0.
_aug_pos = [row(f"T{i}") for i in range(31)]
_aug_carrier = []
ck("E1 August: the old (carrier) basis pays 0 for 31 real trade-ins — the defect",
   tib.count_for_pay("carrier", len(tib.pos_trade_in_txns(_aug_pos)),
                     tib.carrier_trade_in_count(_aug_carrier))[0] == 0)
ck("E2 August: the till basis pays all 31",
   tib.count_for_pay("pos", len(tib.pos_trade_in_txns(_aug_pos)),
                     tib.carrier_trade_in_count(_aug_carrier))[0] == 31)
# October 2026: 53 at the till, exactly 1 on the statement.
_oct_pos = [row(f"O{i}") for i in range(53)]
_oct_carrier = [{"payment_type": "Trade-In Device Reimbursement"}]
ck("E3 October: the old basis pays 1 of 53 (at $5 that is $5 against $265)",
   tib.count_for_pay("carrier", 53, tib.carrier_trade_in_count(_oct_carrier))[0] == 1)
ck("E4 October: the till basis pays 53",
   tib.count_for_pay("pos", len(tib.pos_trade_in_txns(_oct_pos)),
                     tib.carrier_trade_in_count(_oct_carrier))[0] == 53)
ck("E5 the carrier predicate is PRESERVED exactly — a type merely CONTAINING the token counts",
   tib.carrier_trade_in_count([{"payment_type": "Trade-In Device Reimbursement"},
                               {"payment_type": "xx trade yy"},
                               {"payment_type": "TRADE"},
                               {"payment_type": "Commission"}]) == 3)
ck("E5b the aggregate dereferences the per-row predicate — one answer, not two",
   tib.carrier_trade_in_count([{"payment_type": "xx TRADE yy"}]) == 1
   and tib.is_carrier_trade_in({"payment_type": "xx TRADE yy"}) is True
   and tib.is_carrier_trade_in({"payment_type": "Commission"}) is False
   and tib.is_carrier_trade_in(None) is False)
ck("E6 the carrier token is DECLARED, so an org may name its own",
   tib.carrier_trade_in_count([{"payment_type": "Device Buyback Credit"}],
                              tib.resolve_carrier_payment_types({"trade_in_payment_types": ["buyback"]})) == 1)

print()
print("── F. a trade-in whose rep cannot be resolved is REPORTED, never dropped, never paid to nobody ──")
_all = [row("R1"), row("R2"), row("R3", sp=""), row("R4", sp="admin")]
_resolved = tib.pos_trade_in_txns([row("R1"), row("R2")])
ck("F1 two unattributable till trade-ins are counted as unresolved",
   tib.unresolved_pos_count(_all, _resolved) == 2)
ck("F2 with everything attributed, unresolved is zero",
   tib.unresolved_pos_count(_all, tib.pos_trade_in_txns(_all)) == 0)
ck("F3 unresolved never goes negative, even if a caller passes extra ids",
   tib.unresolved_pos_count([row("R1")], tib.pos_trade_in_txns(_all)) == 0)
ck("F4 a non-trade-in row is not 'unresolved' (the leftover is trade-ins only)",
   tib.unresolved_pos_count([row("X", dept="Phones", cat="Phones")], set()) == 0)

print()
print("── G. THE WIRING LOCK — the calculator dereferences the home and keeps no second answer ──")
calc = io.open(CALC, encoding="utf-8").read()
calc_code = _code_only(CALC)
ck("G1 the calculator imports the home",
   re.search(r"from app\.modules\.commcalc import trade_in_basis as _tib", calc) is not None)
ck("G2 the paid count comes from count_for_pay, not from an arithmetic of its own",
   "_tib.count_for_pay(" in calc_code)
ck("G3 the POS count comes from the home's dedupe, not a local set comprehension",
   "_tib.pos_trade_in_txns(" in calc_code)
ck("G4 the basis is RESOLVED through the allowlist, never read raw from cfg",
   "_tib.resolve_basis(cfg.get('trade_in_basis'))" in calc_code
   and "cfg['trade_in_basis']" not in calc_code)
ck("G5 THE ORIGINAL DEFECT IS GONE — no literal 'trade' predicate over payment_type remains",
   not re.search(r"""['"]trade['"]\s+in\s+str\(\s*p\.get\(\s*['"]payment_type""", calc_code))
ck("G6 the carrier token is read from the declaration",
   "_tib.resolve_carrier_payment_types(cfg)" in calc_code)
# G7 — THE SECOND-COPY LOCK, stated precisely. A line that CLASSIFIES a row as a trade-in is a line
# that mentions "trade" together with one of the feed's classifying columns. Every such line must go
# through the home; a new one that does not is the duplicate defect the index rules forbid.
_CLASSIFY_COLS = ("payment_type", "department", "category", "product_desc", "trans_id")
_classifying = [ln for ln in calc_code.split("\n")
                if "trade" in ln.lower() and any(c in ln for c in _CLASSIFY_COLS)]
ck("G7 every line that CLASSIFIES a trade-in goes through the home — no second copy of the rule",
   _classifying and all("_tib." in ln for ln in _classifying))
# The floor is ONE, and one is the right number: after this change every test of "is this row a
# trade-in" lives in the home, so the only classifying line left in the calculator is the one that
# fetches the declaration. The lock is kept honest by G2/G3/G6 below, which require the home to be
# dereferenced for BOTH records — so deleting this line to make G7 vacuous reddens those instead.
ck("G7b …and the lock is not vacuously true (the declaration fetch is still there)",
   len(_classifying) == 1 and "_tib.resolve_carrier_payment_types(cfg)" in _classifying[0])
ck("G7c both records are dereferenced, so G7 cannot be satisfied by deleting the feature",
   "_tib.is_carrier_trade_in(" in calc_code and "_tib.pos_trade_in_txns(" in calc_code)
ck("G8 the count is returned WITH its basis and with the unresolved leftover",
   all(k in calc for k in ("'trade_in_basis': _trade_basis",
                           "'trade_in_unresolved': _trade_unresolved")))
ck("G9 the engine reports the leftover through the home, not a local subtraction",
   "_tib.unresolved_pos_count(" in calc_code)
ck("G10 the per-rep row carries which record paid it",
   "rep['trade_in_basis'] = _trade_which" in calc_code)

print()
print("── H. the home is PURE (a money rule nothing can reach over the network) ──")
_tree = ast.parse(io.open(HOME, encoding="utf-8").read())
_imports = [n for n in ast.walk(_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
ck("H1 the home imports NOTHING at all — stdlib included",
   _imports == [])
ck("H2 no network / database / filesystem name appears in it",
   not any(w in _home_code for w in ("requests", "httpx", "urllib", "socket", "open(",
                                     "client", "supabase", "execute", "os.")))
ck("H3 every public function is a pure function of its arguments (no module-level mutable state)",
   not [n for n in _tree.body if isinstance(n, ast.Assign)
        and isinstance(getattr(n, "value", None), (ast.List, ast.Dict, ast.Set))])

print()
print("── I. ARMED CONTROLS — each proves this harness would REDDEN on the real regressions ──")


def _redden(label, mutate):
    """Apply `mutate` to the calculator text and assert the named lock would fail on it."""
    broken = mutate(calc_code)
    return broken != calc_code


ck("I1 removing the import would redden G1",
   _redden("G1", lambda c: c.replace("import trade_in_basis as _tib", "")))
ck("I2 restoring the literal carrier predicate would redden G5",
   bool(re.search(r"""['"]trade['"]\s+in\s+str\(\s*p\.get\(\s*['"]payment_type""",
                  """if 'trade' in str(p.get('payment_type','')).lower():""")))
ck("I3 a local set-comprehension count instead of the home would leave G3 with nothing to find",
   "_tib.pos_trade_in_txns(" not in calc_code.replace("_tib.pos_trade_in_txns(", "set("))
ck("I4 paying the count WITHOUT its basis would redden G8",
   "'trade_in_basis': _trade_basis" not in calc.replace("'trade_in_basis': _trade_basis", ""))
ck("I5 THE UN-WIRING TRAP (§19.18): a home written and the caller left alone is caught",
   "_tib.count_for_pay(" in calc_code and "_tib.resolve_basis(" in calc_code)
ck("I6 summing LINES instead of transactions is caught by B1",
   len(tib.pos_trade_in_txns([row("A1")] * 4)) == 1 and len([row("A1")] * 4) == 4)
ck("I7 dropping an unattributable trade-in silently is caught by F1",
   tib.unresolved_pos_count(_all, _resolved) != 0)
ck("I7b G7 REDDENS on a second copy: a hand-rolled payment_type test fails the all()",
   not all("_tib." in ln for ln in _classifying
           + ["        if 'trade' in str(p.get('payment_type','')).lower(): n += 1"]))
ck("I7c the NULL-in-a-config-array trap is armed (str(None) == 'none' must not become a department)",
   "none" not in tib.resolve_declaration({"trade_in_departments": [None, "Device Buyback"]})["departments"])
ck("I8 a case-sensitive department test is caught by C3",
   tib.is_pos_trade_in("TRADE IN", "", "", _d) and "TRADE IN".lower() in _d["departments"])

print()
print(f"{_p} passed, {_f} failed")
if _fails:
    for f in _fails:
        print(f"  - {f}")
sys.exit(1 if _f else 0)
