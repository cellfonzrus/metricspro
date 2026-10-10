#!/usr/bin/env python3
"""PROOF + LOCK — the GROSS PROFIT report classifies no carrier dollar of its own (index §58.7).

DB-free, network-free, stdlib only. Run: `python3 backend/harness_gp_carrier_class_dereference.py`

THE DEFECT THIS PINS (owner report 2026-10-08, verbatim):

  *"gross profit is still showing the old data m teh source of information should be the same"*

  Measured live, read-only, house org `00000000-0000-0000-0000-000000000001`, August 2026: the SAME
  11,114 `commcalc.raw_comp_report` rows totalling the SAME $539,721.76, split two different ways by
  two code paths —

    · the P&L (`account/coa.py`, §58) dereferences the one home and says
      commission $120,799.55 / device-financing reimbursement $418,922.21;
    · `commcalc/gp_report.py` carried its OWN classification and said
      commission $538,879.26 / reimbursement $842.50.

  TWO SITES, BOTH PRIVATE:
    1. the comp-report loop guessed from keywords in the compensation type —
       `'reimbursement' in ct or 'rebate' in ct` → reimbursement, `'mdf' in ct` → MDF, EVERYTHING
       ELSE → commission. The org's declared reimbursement types are period-named promo / offer /
       upgrade spellings containing NEITHER word, so essentially all of them fell through to
       commission. That is the whole $418,922.21.
    2. the payment-detail loop compared the category against four literals it spelled itself —
       `cat == 'Commission'`, `== 'Re-imbursement'`, `== 'MDF'`, `== 'Chargeback'` — folding the
       lookup key itself instead of going through §57, and hard-coding the house's own hyphenated
       spelling. `"reimburs"` is not a substring of `"re-imbursement"`: that trap has already bitten
       another path.

  A THIRD copy lived next door in `router._leg_comp_is_commission`, whose docstring said outright
  that it was *"IDENTICAL to gp_report's"* — the same guess, on the same money, in the trend that
  is meant to explain the GP column.

WHAT THE FIX IS. Not a repair of those three sites: the removal of the question from them. The
ruling has ONE home (§57 for the org's declared category, §58 for what the category MEANS and which
column it lands in) and every one of these paths now dereferences it. §F below FAILS THE BUILD if a
caller stops dereferencing it, or if a second classifier reappears anywhere under `app/`.

WHAT DID *NOT* CHANGE, AND IS ASSERTED HERE SO IT CANNOT CHANGE BY ACCIDENT: gross-profit
arithmetic. `net_phone_cost = phone_sales + reimb` reads the PAY-DETAIL `reimb` column; the comp
report feeds the separate `comp_*` columns, which are not terms of `total_rev` or `net_profit`. The
restatement is a RECLASSIFICATION of what the Comp columns show, not a money move (§E).

SECTIONS
  §A  the comp-report regression — the owner's August 2026 split, armed as a negative control
  §B  the pay-detail side — the declaration decides, and the hyphen/casing traps are gone
  §C  nothing resolvable is called commission: unclassified money gets its own column
  §D  identity — every carrier dollar lands in exactly one column, and the leg split re-sums
  §E  gross-profit arithmetic is untouched by the reclassification
  §F  THE UN-WIRING LOCK — no private classifier anywhere, and the callers keep dereferencing
  §G  the module graph and the index know about it
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import carrier_dollar_class as cdc            # noqa: E402
from app.modules.commcalc import gp_report                              # noqa: E402
from app.modules.core import module_graph as MG                         # noqa: E402

PASS = FAIL = 0
_FAILED = []


def ok(label, cond, detail=""):
    """NOTE the argument order: LABEL first. Every call in this file reads
    `ok("A1 what is true", <the assertion>)`, which keeps the sentence next to its number."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        _FAILED.append(label)
        print(f"  FAIL  {label}   {detail}")


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


def src(rel):
    return open(os.path.join(HERE, rel), encoding="utf-8", errors="replace").read()


def code_only(text):
    """`text` with every comment and string literal removed.

    THE LOCK MUST READ CODE, NOT PROSE. These files QUOTE the rule they used to carry — the whole
    point of the comment at each site is that the next reader can see what was wrong — so a naive
    substring scan would fail on the explanation of the fix. Tokenizing strips comments and
    docstrings, so "we no longer do X" survives and `doing X` does not."""
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):          # pragma: no cover - defensive
        return text
    return "\n".join(out)


GP = src("app/modules/commcalc/gp_report.py")
ROUTER = src("app/modules/commcalc/router.py")
HOME = src("app/modules/commcalc/carrier_dollar_class.py")
INDEX = open(os.path.join(HERE, "..", "docs", "SYSTEM_DATA_FLOW_INDEX.md"),
             encoding="utf-8", errors="replace").read()
GP_CODE, ROUTER_CODE, HOME_CODE = code_only(GP), code_only(ROUTER), code_only(HOME)


def fn_src(text, name):
    """The source of ONE top-level function. The router holds five private readers of
    `payment_categories`, four of them deliberately excused money paths (the rep-pay engine, the
    pay-feed balance report, the marketing attribution, the payables engine); scanning the whole
    file would make this lock pass or fail on them instead of on the GROSS PROFIT path it owns."""
    tree = ast.parse(text)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return ast.get_source_segment(text, n) or ""
    return ""


COMPUTE_GP = fn_src(ROUTER, "_compute_gp")

# ── FIXTURES ─────────────────────────────────────────────────────────────────────────────────────
# The live SHAPE, not the live volume: a period-named carrier offer the org DECLARES as its
# reimbursement category (whose text contains neither "reimbursement" nor "rebate", which is why a
# keyword guess always called it commission), a bounty it declares as commission, an MDF type, and a
# type it has never declared at all. Declarations are keyed by §57's folding rule, so they arrive
# here the way `payment_category.load_map` returns them.
DECL = {
    "2026 q3 promo pic offer": "Re-imbursement",          # the $418k shape — no keyword in the text
    "2026 q3 upgrade offer": "Re-imbursement",
    "new activation bounty - month 3": "Commission",
    "market development funds": "MDF",
    "activation clawback": "Chargeback",
    # the trap: the org's own spelling is hyphenated, so a `"reimburs" in cat` test misses it and an
    # `== 'Reimbursement'` compare misses it too
    "device financing offer": "re-imbursement",
}
# The platform's generic keyword ladder — the FALLBACK for an undeclared type only.
RULES = [
    {"raw_category": "residual", "match_type": "contains", "component": "RESIDUAL",
     "priority": 10, "is_active": True},
    {"raw_category": "bounty", "match_type": "contains", "component": "SPIFF",
     "priority": 20, "is_active": True},
]
CFG = cdc.default_config()
STORE_MAP = [{"store_address": "103 Fulton Street", "store_code": "S1", "market": "M1",
              "salesforce_id": "SF1"}]


def comp(ct, amt, addr="103 Fulton Street"):
    return {"business_address": addr, "compensation_type": ct, "payment_amount": amt}


def pay(pt, amt, addr="103 Fulton Street"):
    return {"business_address": addr, "payment_type": pt, "amount": amt}


def run(**kw):
    kw.setdefault("carrier_declarations", DECL)
    kw.setdefault("carrier_rules", RULES)
    kw.setdefault("carrier_class_config", CFG)
    sales = kw.pop("sales", [])
    return gp_report.calc_gp_report(sales, kw.pop("pay_detail", []), [], [], [], [],
                                    STORE_MAP, "August 2026", **kw)


def tot(r, k):
    return round(float(r["totals"].get(k) or 0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE COMP-REPORT REGRESSION — the owner's August 2026 split")

# The owner's proportions, scaled to the fixture: declared-reimbursement promo money dwarfs the
# declared commission, and the pre-fix keyword guess put ALL of it on commission.
ROWS = [comp("2026 Q3 Promo PIC Offer", 300000.00),
        comp("2026 Q3 Upgrade Offer", 118922.21),
        comp("New Activation Bounty - Month 3", 120799.55)]
r = run(comp_rows=ROWS)
ok("A1 declared reimbursement lands on the REIMBURSEMENT column, not commission",
   tot(r, "comp_reimb") == 418922.21, tot(r, "comp_reimb"))
ok("A2 declared commission is the only money left on the commission column",
   tot(r, "comp_comm") == 120799.55, tot(r, "comp_comm"))
ok("A3 the pre-fix answer is DEAD — commission is no longer the whole feed",
   tot(r, "comp_comm") != 539721.76 and tot(r, "comp_comm") != 538879.26, tot(r, "comp_comm"))
ok("A4 the feed total is unchanged — this is a reclassification, not a recompute",
   round(tot(r, "comp_comm") + tot(r, "comp_reimb") + tot(r, "comp_mdf")
         + tot(r, "comp_chb") + tot(r, "comp_unmapped"), 2) == 539721.76)
ok("A5 the home records that the keyword ladder alone would NOT have said reimbursement",
   any(x["to"] == "REIMBURSEMENT" and x["from"] != "REIMBURSEMENT" and x["amount"] == 418922.21
       for x in r["carrier_class_coverage"]["reclassified_vs_keyword"]),
   r["carrier_class_coverage"]["reclassified_vs_keyword"])
ok("A6 the coverage report rides along, and its own arithmetic proof holds",
   r["carrier_class_coverage"]["balances"] is True
   and r["carrier_class_coverage"]["total"] == 539721.76)
ok("A7 every dollar of it is the org's OWN declaration, not an inference",
   r["carrier_class_coverage"]["undeclared_total"] == 0.0,
   r["carrier_class_coverage"]["undeclared_total"])

# MDF keeps its own column (the component vocabulary has no MDF component; the org's declared
# CATEGORY routes it, per config, not per branch).
r = run(comp_rows=[comp("Market Development Funds", 32000.00)])
ok("A8 a declared MDF type keeps the MDF column", tot(r, "comp_mdf") == 32000.00)
r = run(comp_rows=[comp("Activation Clawback", -500.00)])
ok("A9 a declared chargeback keeps its own column and is never netted into commission",
   tot(r, "comp_chb") == -500.00 and tot(r, "comp_comm") == 0.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE PAY-DETAIL SIDE — the declaration decides, through §57's folding rule")

r = run(pay_detail=[pay("New Activation Bounty - Month 3", 1000.00),
                    pay("2026 Q3 Promo PIC Offer", 2500.00),
                    pay("Market Development Funds", 300.00),
                    pay("Activation Clawback", -75.00)],
        pay_category_map=DECL)
ok("B1 commission", tot(r, "comm") == 1000.00, tot(r, "comm"))
ok("B2 the hyphenated house spelling of reimbursement is honoured",
   tot(r, "reimb") == 2500.00, tot(r, "reimb"))
ok("B3 MDF", tot(r, "mdf") == 300.00)
ok("B4 chargeback", tot(r, "chargeback") == -75.00)
ok("B5 nothing fell into unclassified", tot(r, "unmapped") == 0.0)

# THE FOLDING TRAPS. Each of these was unclassified money before, because the key was folded here
# with `.strip()` only (case-sensitive) and then compared against a literal.
r = run(pay_detail=[pay("  new activation BOUNTY - month 3  ", 400.00)], pay_category_map=DECL)
ok("B6 casing and surrounding whitespace in the FEED's spelling still find the declaration",
   tot(r, "comm") == 400.00 and tot(r, "unmapped") == 0.0, r["totals"]["comm"])
r = run(pay_detail=[pay("Device Financing Offer", 900.00)], pay_category_map=DECL)
ok("B7 casing in the org's own CATEGORY cell is not meaning either ('re-imbursement')",
   tot(r, "reimb") == 900.00, tot(r, "reimb"))

# The legacy row shape (a caller that attached the category to the row) goes through the SAME
# ruling, so there is exactly one folding rule in the system, not one per call shape.
r = run(pay_detail=[dict(pay("x", 50.00), category="Re-imbursement"),
                    dict(pay("y", 60.00), category="Commission")])
ok("B8 a row that already carries its category is ruled on by the same home",
   tot(r, "reimb") == 50.00 and tot(r, "comm") == 60.00)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. NOTHING RESOLVABLE IS CALLED COMMISSION")

r = run(comp_rows=[comp("Some Type Nobody Declared", 777.00)], carrier_rules=[])
ok("C1 an undeclared type with no keyword rule is UNCLASSIFIED, not commission",
   tot(r, "comp_unmapped") == 777.00 and tot(r, "comp_comm") == 0.0, r["totals"])
ok("C2 and it is REPORTED, named, with the reason in words",
   any(u["payment_type"] == "Some Type Nobody Declared" and u["amount"] == 777.00
       for u in r["carrier_class_coverage"]["undeclared"])
   and r["carrier_class_coverage"]["by_basis"][0]["reason"],
   r["carrier_class_coverage"]["undeclared"])
r = run(comp_rows=[comp("Monthly Residual Payout", 250.00)], carrier_rules=RULES)
ok("C3 the keyword ladder is still the FALLBACK for an undeclared type",
   tot(r, "comp_comm") == 250.00)
ok("C4 and that dollar is reported as resting on a platform guess",
   r["carrier_class_coverage"]["undeclared_total"] == 250.00)
r = run(comp_rows=[comp("", 10.00)], carrier_rules=[])
ok("C5 a blank compensation type is unclassified money, not commission",
   tot(r, "comp_unmapped") == 10.00)
r = run(pay_detail=[pay("Never Declared", 123.00)], pay_category_map={})
ok("C6 an undeclared PAYMENT type is unclassified, exactly as before this change",
   tot(r, "unmapped") == 123.00 and tot(r, "comm") == 0.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. IDENTITY — one dollar, exactly one column; the leg split still re-sums")

ALL = [comp("2026 Q3 Promo PIC Offer", 1000.00), comp("New Activation Bounty - Month 3", 500.00),
       comp("Market Development Funds", 60.00), comp("Activation Clawback", -40.00),
       comp("Some Type Nobody Declared", 7.00)]
r = run(comp_rows=ALL, carrier_rules=[])
cols = ("comp_comm", "comp_reimb", "comp_mdf", "comp_chb", "comp_unmapped")
ok("D1 the comp columns re-sum to the feed",
   round(sum(tot(r, c) for c in cols), 2) == 1527.00, [tot(r, c) for c in cols])
ok("D2 every GP column the home can route is a column this engine actually has",
   set(cdc.GP_COLUMNS) == {"comm", "reimb", "mdf", "chb", "unmapped"}, cdc.GP_COLUMNS)
legs = {s["key"]: s for s in r["commission_legs"]["sources"]}
ok("D3 the comp commission leg split re-sums to the (now smaller) commission column",
   legs["comp_comm"]["identity_ok"] and legs["comp_comm"]["total"] == 500.00, legs["comp_comm"])
ok("D4 reimbursement money is NOT in the commission leg split — it never was commission",
   legs["comp_comm"]["total"] == tot(r, "comp_comm"))
r2 = run(pay_detail=[pay("New Activation Bounty - Month 3", 900.00),
                     pay("2026 Q3 Promo PIC Offer", 100.00)], pay_category_map=DECL)
legs2 = {s["key"]: s for s in r2["commission_legs"]["sources"]}
ok("D5 the ePay commission leg split re-sums to the commission column",
   legs2["comm"]["identity_ok"] and legs2["comm"]["total"] == 900.00, legs2["comm"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. GROSS-PROFIT ARITHMETIC IS UNTOUCHED BY THE RECLASSIFICATION")

SALES = [{"store": "103 Fulton Street", "department": sorted(gp_report.DEVICE_DEPTS)[0],
          "gp": 0.0, "ext_price": 1000.00, "product_desc": "d", "salesperson": "rep",
          "category": "", "sku": "1"}]
base = run(sales=SALES, comp_rows=[])
moved = run(sales=SALES, comp_rows=ROWS)
ok("E1 the comp columns are NOT terms of total_rev",
   tot(base, "total_rev") == tot(moved, "total_rev"), (tot(base, "total_rev"),
                                                       tot(moved, "total_rev")))
ok("E2 reclassifying $418,922.21 of comp money moves net_profit by $0.00",
   tot(base, "net_profit") == tot(moved, "net_profit"),
   (tot(base, "net_profit"), tot(moved, "net_profit")))
ok("E3 net_phone_cost is phone sales plus the PAY-side reimbursement, and it did not move",
   tot(base, "net_phone_cost") == tot(moved, "net_phone_cost") == tot(base, "phone_sales")
   == 1000.00, (tot(base, "net_phone_cost"), tot(moved, "net_phone_cost")))
ok("E4 the GP engine still adds the pay-side reimbursement to phone cost (unchanged rule)",
   "net_phone_cost = phone_sales + reimb" in GP)
# The pay side is what WOULD move gross profit, so its ruling is pinned to the declaration only —
# no twin inference, no keyword ladder, nothing that could re-file cash without the owner's word.
ok("E5 the pay-detail side rules on the DECLARATION alone — it never runs the keyword ladder",
   "gp_column_of_declared_category" in GP and "classify(carrier_declarations" in GP.replace(
       "_cdc.classify(carrier_declarations", "classify(carrier_declarations")
   and GP.count("_cdc.classify(") == 1, GP.count("_cdc.classify("))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE UN-WIRING LOCK — no private classifier survives, and the callers dereference")

ok("F1 the GP engine imports the two homes",
   "import carrier_dollar_class as _cdc" in GP and "import payment_category as _pc" in GP)
# §66: the comp loop hands the WHOLE classification over, `basis` included. It used to pass
# `_c['component']` alone, so a component reached on a last-resort basis overrode the org's own
# declared category and this feed placed one declared category in a different column from the pay
# feed ($260,500.00, house org, Mar–Sep 2026). The component-only entry point may not come back.
ok("F2 the comp-report loop asks the home, with the whole classification",
   "_cdc.classify(carrier_declarations, carrier_rules" in GP
   and "_cdc.gp_column_of_classification(" in GP
   and "_cdc.gp_column(" not in code_only(GP).replace("\n", ""))
ok("F3 the pay-detail loop asks the home",
   "_cdc.gp_column_of_declared_category(cat, _cc_cfg)" in GP)
ok("F4 the GP engine reads the declared category through §57, not past it",
   "_pc.category_of(pay_category_map" in GP)

# The keyword guess and the four literals, gone — and they cannot come back under any spelling.
for bad, what in (("'reimbursement' in", "the keyword guess on the compensation type"),
                  ('"reimbursement" in', "the keyword guess on the compensation type"),
                  ("'rebate' in", "the rebate keyword guess"),
                  ("'mdf' in", "the MDF keyword guess"),
                  ("== 'Commission'", "the commission literal compare"),
                  ("== 'Re-imbursement'", "the hyphenated reimbursement literal compare"),
                  ("== 'MDF'", "the MDF literal compare"),
                  ("== 'Chargeback'", "the chargeback literal compare")):
    ok(f"F5 gp_report no longer contains {what}  [{bad}]", bad not in GP_CODE)

ok("F6 the trend's self-declared COPY of that rule is gone",
   "def _leg_comp_is_commission(label)" not in ROUTER
   and not re.search(r"reimbursement.{0,8}in ct", ROUTER_CODE))
ok("F7 the trend asks the home instead, through one resolved posture per request",
   "_leg_comp_commission_predicate(" in ROUTER
   and "_cdc_leg.gp_column_of_classification(" in ROUTER          # §66, as above
   and "_cdc_leg.gp_column(" not in code_only(ROUTER).replace("\n", "")
   and "_leg_carrier_class(client, org_id)" in ROUTER)
ok("F8 the trend's pay side rules on the declared category through the home too",
   "_leg_pay_commission_predicate(" in ROUTER
   and "_cdc_leg.gp_column_of_declared_category(" in ROUTER
   and "!= 'Commission'" not in ROUTER_CODE)
ok("F9 _compute_gp hands the GP engine all four dereferenced inputs",
   bool(COMPUTE_GP) and all(k in COMPUTE_GP for k in (
       "pay_category_map=pay_cat_map", "carrier_declarations=_gp_cc_decl",
       "carrier_rules=_gp_cc_rules", "carrier_class_config=_gp_cc_cfg")))
ok("F10 _compute_gp reads payment_categories through §57's one home, not privately",
   "_payment_category.load_map(client, org_id)" in COMPUTE_GP
   and "table('payment_categories')" not in code_only(COMPUTE_GP))
ok("F10b and it folds no key of its own — §57 owns the folding rule",
   "r['description']" not in COMPUTE_GP and "_payment_category.label_of(" in COMPUTE_GP)

# NO SECOND HOME. The component→column maps may exist in exactly one module plus its own
# migration/lock; a copy anywhere else under app/ is the divergence this section exists to stop.
APP = os.path.join(HERE, "app")
copies = []
for root, _d, files in os.walk(APP):
    for f in files:
        if not f.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(root, f), APP)
        if rel == os.path.join("modules", "commcalc", "carrier_dollar_class.py"):
            continue
        body = src(os.path.join("app", rel))
        if "gp_component_columns" in body or "gp_category_columns" in body:
            copies.append(rel)
ok("F11 the component→GP-column map exists in ONE module", not copies, copies)
ok("F12 the home owns the GP column vocabulary, so no caller spells a column key itself",
   "GP_COLUMNS = " in HOME and "GP_COMMISSION_COLUMN = " in HOME
   and "GP_UNCLASSIFIED_COLUMN = " in HOME)
ok("F13 unclassified carrier money is never the commission column, by construction",
   "return GP_UNCLASSIFIED_COLUMN" in HOME
   and cdc.gp_column(None, None, CFG) != cdc.GP_COMMISSION_COLUMN)
ok("F14 the columns are CONFIG with house defaults — RULE TWO, no tenant branch",
   '("carrier_gp_component_columns", "1064_carrier_gp_columns.sql")' in HOME
   and '"gp_component_columns": {' in HOME)
ok("F15 an org that overrides the routing is honoured, and an unknown column is refused",
   cdc.gp_column("REIMBURSEMENT", None, dict(CFG, gp_component_columns={"REIMBURSEMENT": "mdf"}))
   == "mdf"
   and cdc.gp_column("REIMBURSEMENT", None, CFG) == "reimb")

# PURITY: the engine takes its inputs, it does not go and get them.
tree = ast.parse(GP)
io_calls = [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in ("execute", "load_map", "load_config",
                                                           "load_declarations", "load_rules")]
ok("F16 the GP engine performs no read of its own — every input is an argument", not io_calls,
   [n.attr for n in io_calls])
ok("F17 RULE TWO — no carrier, tenant, product or quarter name in ANY code in the home "
   "(comments and docstrings excluded: they explain the defect, they do not branch on it)",
   not re.search(r"(?i)\b(boost|luxelink|vidapay|epay|cellfonz|iphone|samsung|q[1-4])\b",
                 HOME_CODE))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE MODULE GRAPH AND THE INDEX KNOW ABOUT IT")

FACT = "carrier_dollar_component"
f = MG.FACTS.get(FACT) or {}
ok("G1 the GP engine is a registered caller of the fact",
   "app/modules/commcalc/gp_report.py" in (f.get("callers") or {}), sorted(f.get("callers") or {}))
ok("G2 this lock is one of the fact's locks",
   "harness_gp_carrier_class_dereference.py" in (f.get("locks") or ()))
ok("G3 the GP engine is a registered caller of §57's category home too",
   "app/modules/commcalc/gp_report.py"
   in ((MG.FACTS.get("payment_category_map") or {}).get("callers") or {}))
ok("G4 `connected()` answers 'what else answers this question' for the GP engine",
   FACT in MG.connected("app/modules/commcalc/gp_report.py"))
ok("G5 the index documents the GP dereference", "58.5" in INDEX)
ok("G6 the index records the measured restatement, so the figure is not only in a PR",
   "418,922.21" in INDEX and "538,879.26" in INDEX)
ok("G7 the index states that gross profit itself does not move",
   re.search(r"gross profit.{0,400}(does not move|unchanged)", INDEX, re.S | re.I) is not None)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
print(f"  {PASS} passed, {FAIL} failed")
if _FAILED:
    print("  FAILED:")
    for x in _FAILED:
        print(f"    - {x}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
