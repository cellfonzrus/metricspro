"""BATCH 23 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the FINANCE set this batch serves.

  /accounts/device-purchases   what the distributor BILLED us for devices in a window
  /accounts/device-payable     which of those devices were still UNPAID on a given day
  /accounts/inventory          what the stock on the shelf is WORTH, and the two sources' tie-out
  /accounts/recon              did the distributor's credits match the residual report's earnings

WHAT THIS PROVES, AND WHY THESE ARE THREE FAMILIES AND NOT ONE
──────────────────────────────────────────────────────────────
A family is a shared ROW and a shared PROVENANCE, not a shared menu section. Reading each page and
each endpoint puts these four into three:

  · Device Purchases and Device Payable fold the SAME subject — the units the distributor billed
    against our dealer account — at the SAME store row, out of the same two relations, and the
    payable report already IMPORTS the voided-invoice rule from the purchases one so the two can
    never disagree about which invoices count. ONE declaration, two screens.
  · Inventory Values folds what the stock on the shelf is WORTH: one store row, and the relation
    that answered decided PER ROW by a precedence rule. Nothing else in the platform folds it, and
    it is deliberately not folded through the stored-statement catalogue, which owns the figure the
    Balance Sheet reads OUT of the snapshot rather than the live sources it is computed FROM.
  · The credit-memo reconciliation folds what the distributor CREDITED against what the residual
    report EARNED. Neither relation appears in either family above.

THE PRODUCERS ARE DRIVEN FOR REAL. `device_purchases.aggregate`, `device_payable.aggregate` (through
the real `store_placer`, `voided_invoice_set`, `coverage_scan` and `coverage_state`) and
`balance_sheet.device_inventory_cells` / `inventory_recon_rows` are called directly. Two folds live
in modules this file cannot import — the inventory grid's effective fold is written inline in
`account/router.py` and `account/recon.reconcile` reaches a settings module that needs a dependency
this harness deliberately does not take — so §E and §F reproduce each in its own shape AND PIN it to
its own source text, batch 17's, 19's, 20's, 21's and 22's posture for exactly this problem.

The GRAIN COUNT in §I is MEASURED off the panels, as a SET compared for EQUALITY, never a bound.
The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch23_drilldown.py`
"""
import ast
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import balance_sheet as bs                          # noqa: E402
from app.modules.account import device_billing_drilldown as dbd              # noqa: E402
from app.modules.account import device_payable as dpay                       # noqa: E402
from app.modules.account import device_purchases as dpur                     # noqa: E402
from app.modules.account import inventory_value_drilldown as ivd             # noqa: E402
from app.modules.account import memo_recon_drilldown as mrd                  # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage            # noqa: E402
from app.modules.core import report_drilldown as rd                          # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, bool(red))


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate the two narrowed screens in this
    batch hand to `useDrillTile`. Deliberately the same arithmetic, so the safety net those pages
    rely on is tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECLS = {
    "device_billing_drilldown": read("backend/app/modules/account/device_billing_drilldown.py"),
    "inventory_value_drilldown": read("backend/app/modules/account/inventory_value_drilldown.py"),
    "memo_recon_drilldown": read("backend/app/modules/account/memo_recon_drilldown.py"),
}
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
ACC_ROUTER = read("backend/app/modules/account/router.py")
RECON_SRC = read("backend/app/modules/account/recon.py")
HOME = read("backend/app/modules/core/report_drilldown.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
GRAPH = read("backend/app/modules/core/module_graph.py")

PAGES = {
    "device-purchases": read("frontend/src/app/(platform)/accounts/device-purchases/page.tsx"),
    "device-payable": read("frontend/src/app/(platform)/accounts/device-payable/page.tsx"),
    "inventory": read("frontend/src/app/(platform)/accounts/inventory/page.tsx"),
    "recon": read("frontend/src/app/(platform)/accounts/recon/page.tsx"),
}
#: WHICH PAGES FOLD A FIGURE OVER A POPULATION THE SERVER DID NOT, AND WHICH DO NOT. DECIDED BY
#: READING EACH PAGE (§CORE-17.3o), and §G locks every decision in BOTH directions.
#:
#: The two device reports filter their store rows in the BROWSER with the standard filter bar and
#: then reduce over what survives, so a server-folded panel would explain a number the screen is not
#: printing. Inventory Values has no filter of any kind — override inputs, a connector form and a
#: save — and the credit-memo recon's controls are a tolerance, a recompute, an analyze and two
#: writes; both print their endpoints' own figures as served.
NARROWED_PAGES = ("device-purchases", "device-payable")
SERVER_FOLDED_PAGES = ("inventory", "recon")


def _literals(src):
    """Every STRING literal the module's CODE contains. A relation name may appear in a DOCSTRING or
    a comment — that is a note to the next engineer and the point of writing the defects down. The
    rulings are about the CODE, so the two are told apart by PARSING rather than by grepping."""
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None) or []
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docs and isinstance(node.value, str):
            out.append(node.value)
    return out


def strip_comments(src):
    """A TS/JS source with its comments removed, so a rule WRITTEN DOWN in a page's header cannot be
    mistaken for the rule being broken in its code. Batch 18's §F posture, reused verbatim."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", out)


def func_src(src, name):
    """One function's own source text, so a check about its body cannot be satisfied by a sibling."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the five relation names, with BOTH halves of the fix ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: The five NAME constants this batch gave the registry, with the value each must carry. Declared as
#: a MAP and compared for equality so a constant that is renamed, re-pointed or deleted names itself.
NEW_CONSTANTS = {
    "DISTRIBUTOR_INVOICE_LINES": "vip_invoice_lines",
    "DISTRIBUTOR_INVOICE_UNITS": "vip_invoice_devices",
    "DISTRIBUTOR_CREDIT_MEMOS": "vip_credit_memos",
    "INVENTORY_VALUE_SNAPSHOT": "inventory_value",
    "INVENTORY_AGING_DEVICES": "inventory_aging_device",
}
check("A1 the five relation names this batch's panels must say out loud live in the lineage "
      "registry, with the values they had before — naming a fact is half a design fix",
      {n: getattr(lineage, n, None) for n in NEW_CONSTANTS} == NEW_CONSTANTS,
      str({n: getattr(lineage, n, None) for n in NEW_CONSTANTS}))
ctl("A1a control: the comparison is against the VALUES and not merely the names — a constant "
    "re-pointed at another relation is red",
    dict(NEW_CONSTANTS, INVENTORY_VALUE_SNAPSHOT="b2b_inventory") != NEW_CONSTANTS)

#: THE OTHER HALF. Two of the five were spelled as LITERALS inside the registry's own maps before
#: this batch — `inventory_value` in seven and `vip_credit_memos` in one — and leaving a copy there
#: is the divergence the registry exists to prevent (§19.18, three times here already). Not one
#: literal copy of either may remain in that file outside its own constant line.
_REKEYED = ("inventory_value", "vip_credit_memos")
_registry_lines = [ln for ln in REGISTRY.splitlines()
                   if not re.match(r"^[A-Z_]+ = \"", ln.strip())]
_left_over = {t: sum(1 for ln in _registry_lines if '"%s"' % t in ln) for t in _REKEYED}
check("A2 BUILD LOCK — and every literal copy the registry itself held was DELETED in the same "
      "commit: both re-keyed names appear in that file ONLY on their own constant line, so a map "
      "cannot drift away from the constant beside it",
      all(v == 0 for v in _left_over.values()), str(_left_over))
ctl("A2a control: the scan really counts copies — fed a line that re-declares one, it reports it",
    sum(1 for ln in ['    "inventory_value": "updated_at",'] if '"inventory_value"' in ln) == 1)
check("A3 and the maps still carry the SAME answers they carried before the re-key — a constant "
      "that changed a value would be a feed-watchdog change wearing a refactor's clothes",
      lineage.FRESHNESS_COLUMN_BY_TABLE.get("inventory_value") == "updated_at"
      and lineage.FRESHNESS_COLUMN_BY_TABLE.get("vip_credit_memos") == "swept_at"
      and lineage.FEED_CADENCE_BY_TABLE.get("inventory_value") == lineage.CADENCE_WEEKLY)

#: THE ONE EXEMPTION, AND IT IS NOT A RELATION NAME. `balance_sheet.INVENTORY_BASES` is the one home
#: for which inventory bases exist, and one of its CONFIG VALUES happens to read like a table name.
#: A declaration keying on that vocabulary is spelling a basis, not a relation — which §E9b locks by
#: comparing the key set against that shared vocabulary for EQUALITY, a stricter check than this
#: scan could make. Taken from the shared tuple rather than listed here, so the exemption cannot
#: outgrow it.
BASIS_WORDS = set(bs.INVENTORY_BASES)
for _name, _src in DECLS.items():
    _lits = set(_literals(_src))
    _spelled = sorted((_lits & set(NEW_CONSTANTS.values()) | (_lits & {"asset_ledger", "raw_mi"}))
                      - BASIS_WORDS)
    check(f"A4 {_name} DEREFERENCES every relation name and spells none of them in code — a "
          f"declaration restating a table name is the copied fact the registry exists to prevent",
          _spelled == [], str(_spelled))
ctl("A4b control: the exemption is the BASIS VOCABULARY and nothing wider — a module that spelled "
    "one of the four relation names that is NOT a basis word is still reported",
    sorted(({"vip_invoice_lines", "inventory_value"}) - BASIS_WORDS)
    == ["inventory_value", "vip_invoice_lines"])
ctl("A4a control: the literal scan reads CODE and not docstrings — a module whose docstring names a "
    "relation is not reported, while one whose code does is",
    _literals('"""doc names inventory_value"""\nX = 1\n') == []
    and _literals('"""doc"""\nX = "inventory_value"\n') == ["inventory_value"])

check("A5 every relation these panels can name has a PLAIN NAME on the screen — a reader sees "
      "'distributor invoice lines', never the table (§19.38)",
      all(re.search(r"(?m)^\s*%s:\s*'" % re.escape(t), LABELS)
          for t in list(NEW_CONSTANTS.values()) + ["asset_ledger", "raw_mi"]),
      str([t for t in NEW_CONSTANTS.values()
           if not re.search(r"(?m)^\s*%s:\s*'" % re.escape(t), LABELS)]))

def _numbers(src):
    """Every NUMERIC constant the module's CODE evaluates — read by PARSING, so a ruling id or a
    measured figure written down in a docstring is not mistaken for a threshold in the code."""
    return [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]


_nums = {name: sorted({v for v in _numbers(src) if v not in (0, 1)})
         for name, src in DECLS.items()}
check("A6 the three declarations hold no dollar literal, rate or threshold of their own — they "
      "declare, they do not compute (the one home's §G4 rule, applied to its callers). Measured by "
      "PARSING, so a ruling id in a docstring is not mistaken for a threshold in the code",
      all(v == [] for v in _nums.values()), str(_nums))
ctl("A6a control: the number scan reads CODE and not docstrings — a module whose docstring quotes a "
    "figure is clean, while one whose code holds a rate is not",
    _numbers('"""a figure of 1234.56 was measured"""\nX = 2\n') == [2]
    and 0.7 in _numbers('"""doc"""\nRATE = 0.7\n'))
check("A7 and each binds the one home's refusal rule BY IDENTITY rather than copying it — two "
      "copies of a refusal rule is two chances to get a refusal wrong",
      all(getattr(m, "_served") is rd.served_tiles for m in (dbd, ivd, mrd)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE DEVICE-BILLING FIXTURE — two stores, a void, a non-device and a lost location ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STORE_A, STORE_B = "11 First Ave", "22 Second St"
LOST = "99 Nowhere Rd"
KNOWN = (STORE_A, STORE_B)
COMPANIES = {"co-1": "North Holdings", "co-2": "South Holdings"}
DEVICE_NAME, TABLET_NAME, NON_DEVICE = "Model One 5G", "Model One TAB", "SIM Starter Pack"


def _resolve(raw):
    """The org's resolver, stood in for: exact match on a known address, otherwise unchanged. The
    REAL `coa.store_resolver` needs a client; its CONTRACT (an unmappable string comes back
    cleaned-but-unchanged) is what `store_placer` relies on and what this honours."""
    r = str(raw or "").strip()
    for k in KNOWN:
        if r.lower() == k.lower():
            return k
    return r


PLACE = dpur.store_placer(_resolve, KNOWN)          # the REAL classifier, called not re-implemented
COMPANY_OF = {STORE_A: "co-1", STORE_B: "co-2"}.get


def _line(store, name, total, inv, year=2026, month=1, status="open", day="2026-01-05"):
    """One invoice LINE. The purchases report windows on `period_year`/`period_month` and the payable
    report on `created_on` — the two read the same feed through different columns — so a fixture that
    set only one of them would drive one producer and quietly starve the other."""
    return {"name": name, "total": total, "quantity": 1, "location": store,
            "period_year": year, "period_month": month, "invoice_number": inv, "status": status,
            "created_on": day}


_SERIAL = [0]


def _unit(store, name, inv, day="2026-01-05", year=2026, month=1):
    """One serialised unit row. THE JOIN KEY IS `serial`, not the column CALLED `imei` — the
    producer's own docstring measures that the obviously-named column matches 4 units out of 19,571
    — so the fixture puts a handset-shaped serial in `serial` and something else in `imei`, exactly
    as the feed does. A fixture that keyed on `imei` would drive the real producer and prove
    nothing."""
    _SERIAL[0] += 1
    return {"product_name": name, "location": store, "invoice_number": inv,
            "period_year": year, "period_month": month, "created_on": day,
            "serial": "35%013d" % _SERIAL[0], "imei": "89%016d" % _SERIAL[0]}


PUR_LINES = (
    [_line(STORE_A, DEVICE_NAME, 100.0, "INV-1") for _ in range(2)]
    + [_line(STORE_B, TABLET_NAME, 250.0, "INV-2")]
    + [_line(LOST, DEVICE_NAME, 40.0, "INV-3")]
    + [_line(STORE_A, NON_DEVICE, 15.0, "INV-1")]
    + [_line(STORE_A, DEVICE_NAME, 999.0, "INV-VOID")]           # excluded by the shared void rule
)
PUR_UNITS = ([_unit(STORE_A, DEVICE_NAME, "INV-1") for _ in range(2)]
             + [_unit(STORE_B, TABLET_NAME, "INV-2")]
             + [_unit(LOST, DEVICE_NAME, "INV-3")])
INVOICES = [{"invoice_number": "INV-1", "status": "open"}, {"invoice_number": "INV-2", "status": "open"},
            {"invoice_number": "INV-3", "status": "open"},
            {"invoice_number": "INV-VOID", "status": "VOIDED"}]
VOIDED = dpur.voided_invoice_set(INVOICES)
check("B1 the REAL shared void rule is what excludes the voided invoice — one rule, imported by "
      "both reports, so they can never disagree about which invoices count",
      VOIDED == {"INV-VOID"}, str(VOIDED))

PUR = dpur.aggregate(PUR_LINES, PUR_UNITS, ((2026, 1), (2026, 1)), PLACE, COMPANY_OF,
                     company_names=COMPANIES, market_of=lambda s: "Metro",
                     voided_invoices=VOIDED)
check("B2 the real producer ran and folded the fixture: two placed stores PLUS the placeholder row "
      "for the location nothing matched, one non-device line, and the voided invoice left out of "
      "every figure",
      len(PUR["by_store"]) == 3 and len(PUR["non_device_lines"]) == 1
      and len(PUR["unresolved"]["store_not_mapped"]) == 1
      and cents(PUR["totals"]["device_amount"]) == cents(490.0)
      and cents(PUR["excluded_voided"]["device_amount"]) == cents(999.0),
      str(PUR["totals"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE PURCHASE PANELS — over the real payload, three row shapes, two relations ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PD = dbd.purchase_drilldowns(PUR)
check("C1 every figure this report prints that is a sum over its own rows gets a panel, and the "
      "keys are the PAYLOAD's own field names so the page asks by the same string",
      set(PD) == {dbd.DEVICE_AMOUNT, dbd.SERIALISED_UNITS, dbd.NON_DEVICE_AMOUNT,
                  dbd.UNRESOLVED_AMOUNT}, str(sorted(PD)))
check("C2 each panel's figure is the report's OWN total dereferenced, so a panel can never be a "
      "second opinion about the number above it",
      agrees(PD[dbd.DEVICE_AMOUNT], PUR["totals"]["device_amount"])
      and agrees(PD[dbd.SERIALISED_UNITS], PUR["totals"]["serialised_units"])
      and agrees(PD[dbd.NON_DEVICE_AMOUNT], PUR["totals"]["non_device_amount"]))
check("C3 and every one of them TIES against the rows it named — the report agreeing with its own "
      "evidence, checked rather than asserted",
      all(PD[k]["tie_out"]["ties"] is True for k in PD), str({k: PD[k]["tie_out"] for k in PD}))
ctl("C3a control: the tie-out is a real comparison — a store row's money tampered with by one cent "
    "makes it FAIL, in words",
    (lambda p: p["tie_out"]["ties"] is False and "disagree" in (p["tie_out"]["words"] or ""))(
        dbd.purchase_drilldowns({**PUR, "by_store": [dict(PUR["by_store"][0], amount=0.01)]
                                 + list(PUR["by_store"][1:])})[dbd.DEVICE_AMOUNT]))
ctl("C3b control: a DROPPED row breaks it too — a panel short of a row is not a tie",
    dbd.purchase_drilldowns({**PUR, "by_store": PUR["by_store"][:1]})[
        dbd.DEVICE_AMOUNT]["tie_out"]["ties"] is False)

check("C4 the MONEY names the invoice LINES and the UNIT COUNT names the SERIALISED rows — two "
      "grains on one store row, because the two figures really do come out of two relations",
      {s["table"] for s in PD[dbd.DEVICE_AMOUNT]["sources"]} == {lineage.DISTRIBUTOR_INVOICE_LINES}
      and {s["table"] for s in PD[dbd.SERIALISED_UNITS]["sources"]}
      == {lineage.DISTRIBUTOR_INVOICE_UNITS})
check("C5 the money panel names the COLUMN it was read out of, so a reader lands on the cell and "
      "not merely the table",
      all(o["column"] == "total" for r in PD[dbd.DEVICE_AMOUNT]["rows"] for o in r["origins"]))
check("C6 each row carries the keys that ADDRESS it — store and company, which is what the roll-up "
      "is keyed on, so a panel can never hold a different population than the row it opened from",
      all(set(r["row_key"] or {}) == {"store", "company"}
          for r in PD[dbd.DEVICE_AMOUNT]["rows"]))
check("C7 the unresolved-location figure is folded over THOSE rows and names the location itself — "
      "that money is real and is shown rather than absorbed into a store",
      {s["table"] for s in PD[dbd.UNRESOLVED_AMOUNT]["sources"]}
      == {lineage.DISTRIBUTOR_INVOICE_LINES}
      and [r["label"] for r in PD[dbd.UNRESOLVED_AMOUNT]["rows"]] == [LOST])
check("C8 WHAT GETS NO PANEL: the match-rate-style ratios, the distinct invoice count, the "
      "unit-reconciliation block and every voided/distinct figure composed at the TOTAL",
      not ({"invoices", "unit_gap", "line_quantity_units", "all_lines_amount"} & set(PD)))
check("C9 DEFECT 1, REPRODUCED FROM THE REAL PRODUCER: the company-not-mapped rows under "
      "`unresolved` are the SAME objects the By store table already prints, so the page shows that "
      "money twice — and the panels count the store population ONCE, exactly as the tiles do",
      all(any(r is s for s in PUR["by_store"])
          for r in PUR["unresolved"]["company_not_mapped"])
      and len(PD[dbd.DEVICE_AMOUNT]["rows"]) == len(PUR["by_store"]))
check("C10 a report with no rows at all drills to nothing rather than to a tie of 0 against 0 that "
      "would read as a measurement (§CORE-17.3i, through the one home's own refusal rule)",
      dbd.purchase_drilldowns({"totals": {"device_amount": 0.0, "serialised_units": 0},
                               "by_store": []}) == {})
check("C11 and the unresolved figure is the rows' OWN total, taken from the same list the panel is "
      "built over — a list the report did not serve gets no figure and therefore no panel",
      cents(dbd.unresolved_total(PUR)) == cents(40.0)
      and dbd.unresolved_total({"unresolved": {"store_not_mapped": []}}) is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE PAYABLE PANELS — the same store row judged against a payment date elsewhere ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# COVERAGE IS DERIVED FROM THE DATA, so the fixture has to earn 'measured': a judged month needs at
# least the producer's own minimum of invoiced units and its own ratio of ledger rows, and the month
# holding the ledger's newest row is never judged because it is still filling.
PAY_UNITS, LEDGER = [], []
for i in range(dpay.COVERAGE_MIN_DEVICES + 4):
    store = STORE_A if i % 2 == 0 else STORE_B
    u = _unit(store, DEVICE_NAME, "INV-1", day="2026-01-%02d" % ((i % 27) + 1))
    PAY_UNITS.append(u)
for i, u in enumerate(PAY_UNITS[:dpay.COVERAGE_MIN_DEVICES + 2]):
    # HALF SETTLED BEFORE THE AS-AT DAY AND HALF AFTER IT, so both sides of the judgement have rows
    # and neither figure is a zero that would be refused a panel for the wrong reason.
    LEDGER.append({"esn_imei": u["serial"], "acquired_date": u["created_on"],
                   "payg_date": ("2026-01-20" if i % 2 == 0 else "2026-03-01"),
                   "owed_to_vip": 10.0, "store": u["location"], "status": "open"})
LEDGER.append({"esn_imei": "35%013d" % 9999, "acquired_date": "2026-02-03",
               "payg_date": "2026-02-10", "owed_to_vip": 0.0, "store": STORE_A, "status": "open"})
PAY_LINES = [_line(STORE_A, NON_DEVICE, 15.0, "INV-1")]
PAY = dpay.aggregate(PAY_UNITS, PAY_LINES, LEDGER, "2026-01-25", PLACE, COMPANY_OF,
                     company_names=COMPANIES, market_of=lambda s: "Metro",
                     invoice_rows=INVOICES)
check("D1 the real producer earned a MEASURED coverage state off the fixture's own months — the "
      "coverage boundary is derived from the data and is not a constant this file set",
      PAY["coverage"]["state"] == "measured" and PAY["by_store"],
      str(PAY["coverage"]))

PY = dbd.payable_drilldowns(PAY)
check("D2 every figure the payable report prints that is a sum over its store rows gets a panel",
      set(PY) == {dbd.PAYABLE_AMOUNT, dbd.PAYABLE_DEVICES, dbd.PAID_AMOUNT,
                  dbd.NON_DEVICE_TOTAL}, str(sorted(PY)))
check("D3 each figure is the report's own total dereferenced, and each panel TIES against the rows "
      "it named",
      agrees(PY[dbd.PAYABLE_AMOUNT], PAY["totals"]["payable_amount"])
      and agrees(PY[dbd.PAYABLE_DEVICES], PAY["totals"]["payable_devices"])
      and agrees(PY[dbd.PAID_AMOUNT], PAY["totals"]["paid_amount"])
      and all(PY[k]["tie_out"]["ties"] is True for k in PY),
      str({k: PY[k]["tie_out"]["ties"] for k in PY}))
check("D4 EVERY PAYABLE ROW NAMES BOTH RELATIONS — which units were billed from the serialised "
      "invoice rows, the amount owed and the day it settled from the unit ledger. Naming one would "
      "make half this report's method invisible",
      {s["table"] for s in PY[dbd.PAYABLE_AMOUNT]["sources"]}
      == {lineage.DISTRIBUTOR_INVOICE_UNITS, lineage.ASSET_LEDGER})
check("D5 and that is a DIFFERENT PROVENANCE from the purchase money on the same row in the same "
      "unit — §CORE-17.3n, measured off the two panels rather than claimed",
      {s["table"] for s in PY[dbd.PAYABLE_AMOUNT]["sources"]}
      != {s["table"] for s in PD[dbd.DEVICE_AMOUNT]["sources"]}
      and PY[dbd.PAYABLE_AMOUNT]["figure"]["unit"] == PD[dbd.DEVICE_AMOUNT]["figure"]["unit"])
check("D6 NO COLUMN IS CLAIMED on those two-relation rows, and that is deliberate: the one home "
      "applies one column name to every relation a row names, so declaring the ledger's money "
      "column would assert that the serialised invoice rows carry a column they do not have",
      all(o["column"] is None for r in PY[dbd.PAYABLE_AMOUNT]["rows"] for o in r["origins"])
      and dbd.OWED_COLUMN in dbd.payable_catalogue()[0]["note"])
check("D7 AN AS-AT DATE THE LEDGER CANNOT COVER SERVES NO FIGURE, SO IT DRILLS TO NOTHING — "
      "an absence is not a zero (§CORE-17.3i), and no branch in the declaration decides it",
      dbd.payable_drilldowns(
          dpay.aggregate(PAY_UNITS, PAY_LINES, LEDGER, "2020-01-01", PLACE, COMPANY_OF,
                         company_names=COMPANIES, invoice_rows=INVOICES)) == {})
check("D8 WHAT GETS NO PANEL: the match RATE (§CORE-17.3h), and every figure composed at the TOTAL "
      "— the voided exclusions, the payable-including-voided and the distinct-device subtraction",
      not ({"match_rate", "payable_including_voided", "distinct_device_payable_amount",
            "excluded_voided_payable_amount"} & set(PY)))
check("D9 DEFECT 2, REPRODUCED FROM THE PAGE'S OWN TEXT: the paid total is printed as a dash under "
      "any filter while the payable beside it narrows, so the paid panel is offered only on the "
      "unfiltered view — the page's own rule honoured, not a second one invented",
      "narrowed ? '—' : money(T.paid_amount)" in PAGES["device-payable"]
      and "const NARROWED_KEYS = ['payable_amount', 'payable_devices']" in PAGES["device-payable"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. THE INVENTORY FAMILY — two claims about one shelf, and a provenance per ROW ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
INV_VALUE_ROWS = [
    {"store": STORE_A, "swept_value": 100.0, "manual_value": None, "as_of_date": "2026-01-31"},
    {"store": STORE_B, "swept_value": 80.0, "manual_value": 95.0, "as_of_date": "2026-01-31"},
    {"store": LOST, "swept_value": None, "manual_value": None, "as_of_date": None},
]
DEV_AGING_ROWS = [
    {"store": STORE_A, "unit_cost": 30.0, "on_hand": 4, "as_of_date": "2026-01-31"},
    {"store": STORE_B, "unit_cost": 20.0, "on_hand": 3, "as_of_date": "2026-01-31"},
]
CELLS, DMETA = bs.device_inventory_cells(DEV_AGING_ROWS)        # the REAL producer
REC_ROWS, REC_TOTALS = bs.inventory_recon_rows(INV_VALUE_ROWS, CELLS, "report", _resolve)
RECON_PAYLOAD = {"basis": "report", "rows": REC_ROWS, "totals": REC_TOTALS, "device_meta": DMETA}
#: THE STORES THE REAL PRODUCER IS EXPECTED TO FOLD, DECLARED AND COMPARED FOR EQUALITY. A bound on
#: the SIZE would stay green while one store silently left the fold and another took its place, which
#: is the shape `harness_subject_set_lock.py` §B1 refuses — and the store with NEITHER source is the
#: interesting member, because it must be absent HERE and present in the GRID fold below (§E7).
RECON_STORES = {STORE_A, STORE_B}
check("E1 the real reconciliation producer ran and folded both sides over exactly the stores one of "
      "them knows about, with a per-store DELTA on every row — which is what makes a difference "
      "drillable at all (§CORE-17.3k)",
      {r["store"] for r in REC_ROWS} == RECON_STORES and all("delta" in r for r in REC_ROWS)
      and cents(REC_TOTALS["delta"])
      == cents(REC_TOTALS["device_value"] - REC_TOTALS["report_value"]),
      "extra=%s missing=%s totals=%s" % (sorted({r["store"] for r in REC_ROWS} - RECON_STORES),
                                         sorted(RECON_STORES - {r["store"] for r in REC_ROWS}),
                                         REC_TOTALS))
ctl("E1a control: a GHOST AT UNCHANGED SIZE — one folded store swapped for one that does not "
    "exist — leaves a `>=` bound green and makes this comparison red, naming both",
    (lambda g: len(g) >= len(RECON_STORES) and g != RECON_STORES
     and sorted(g - RECON_STORES) and sorted(RECON_STORES - g))(
        ({r["store"] for r in REC_ROWS} - {STORE_A}) | {"a store that does not exist"}))

RVD = ivd.recon_drilldowns(RECON_PAYLOAD)
check("E2 all four reconciliation figures drill — both claims, the phone count and the delta",
      set(RVD) == {ivd.REPORT_VALUE, ivd.DEVICE_VALUE, ivd.DEVICE_COUNT, ivd.DELTA},
      str(sorted(RVD)))
check("E3 each is the producer's own total dereferenced and each TIES against its own rows",
      all(agrees(RVD[k], REC_TOTALS[k]) for k in RVD)
      and all(RVD[k]["tie_out"]["ties"] is True for k in RVD),
      str({k: RVD[k]["tie_out"]["ties"] for k in RVD}))
ctl("E3a control: the delta tie-out is a real comparison — one row's delta tampered with makes it "
    "fail, in words, rather than being averaged away",
    (lambda p: p["tie_out"]["ties"] is False)(
        ivd.recon_drilldowns({**RECON_PAYLOAD,
                              "rows": [dict(REC_ROWS[0], delta=(REC_ROWS[0]["delta"] or 0) + 1.0)]
                              + list(REC_ROWS[1:])})[ivd.DELTA]))
check("E4 the two claims name two DIFFERENT relations on the same store row in the same unit — "
      "§CORE-17.3n, read off the panels",
      {s["table"] for s in RVD[ivd.REPORT_VALUE]["sources"]} == {lineage.INVENTORY_VALUE_SNAPSHOT}
      and {s["table"] for s in RVD[ivd.DEVICE_VALUE]["sources"]}
      == {lineage.INVENTORY_AGING_DEVICES}
      and RVD[ivd.REPORT_VALUE]["figure"]["unit"] == RVD[ivd.DEVICE_VALUE]["figure"]["unit"])
check("E5 and the DELTA names BOTH, because the disagreement is between them and naming one side "
      "would hide the other",
      {s["table"] for s in RVD[ivd.DELTA]["sources"]}
      == {lineage.INVENTORY_AGING_DEVICES, lineage.INVENTORY_VALUE_SNAPSHOT})

# ── THE GRID'S OWN FOLD, REPRODUCED AND PINNED. It is written inline in `account/router.py`, which
# cannot be imported without a web framework, so the rule is reproduced here AND pinned to the
# router's own source text — if that fold changes, the pin fails rather than this copy drifting.
_LIST_SRC = func_src(ACC_ROUTER, "list_inventory_values")
check("E6 the grid's effective rule is still the one this file reproduces — manual override, else "
      "the swept report value, and NOTHING about the configured basis",
      'v["effective"] = mv if mv is not None else sv' in _LIST_SRC
      and "inventory_basis" not in _LIST_SRC, "the router's fold has changed; re-read it")


def _grid_fold(rows):
    """THE ROUTER'S OWN RULE, reproduced in its own shape (pinned by E6) so the panel can be driven
    over exactly what that endpoint serves."""
    out = []
    for r in rows:
        mv, sv = r.get("manual_value"), r.get("swept_value")
        out.append({"store": _resolve(r["store"]), "swept_value": sv, "manual_value": mv,
                    "as_of_date": r.get("as_of_date"),
                    "effective": mv if mv is not None else sv,
                    "effective_source": ("manual" if mv is not None
                                         else ("b2bsoft" if sv is not None else None))})
    return {"rows": out,
            "total_effective": round(sum(float(r["effective"] or 0) for r in out), 2)}


GRID = _grid_fold(INV_VALUE_ROWS)
GVD = ivd.value_drilldowns(GRID)
check("E7 the grid total drills to its own store rows, ties against them, and the store with no "
      "value at all is SHOWN WITHOUT A FIGURE and not counted — never a $0.00",
      agrees(GVD[ivd.TOTAL_EFFECTIVE], GRID["total_effective"])
      and GVD[ivd.TOTAL_EFFECTIVE]["tie_out"]["ties"] is True
      and [r["label"] for r in GVD[ivd.TOTAL_EFFECTIVE]["rows"] if not r["present"]] == [LOST],
      str(GVD[ivd.TOTAL_EFFECTIVE]["tie_out"]))
check("E8 THE RELATION IS NAMED PER ROW, from the producer's own basis word — the override a person "
      "typed and the swept total are two COLUMNS of one relation, and each row says which",
      [[(o["table"], o["column"]) for o in r["origins"]]
       for r in GVD[ivd.TOTAL_EFFECTIVE]["rows"]]
      == [[(lineage.INVENTORY_VALUE_SNAPSHOT, ivd.SWEPT_COLUMN)],
          [(lineage.INVENTORY_VALUE_SNAPSHOT, ivd.MANUAL_COLUMN)],
          [(None, None)]])
check("E9 and a basis word NEITHER endpoint has ever served resolves to NO relation at all — the "
      "row reports itself as untraceable rather than being attributed to a guess",
      ivd.effective_relation({"effective_source": "a word nobody declared"}) == ()
      and ivd.recon_drilldowns(RECON_PAYLOAD) is not None
      and ivd.value_drilldowns({"rows": [{"store": STORE_A, "effective": 5.0,
                                          "effective_source": "a word nobody declared"}],
                                "total_effective": 5.0})[
                                    ivd.TOTAL_EFFECTIVE]["untraceable_origins"] == 1)
check("E9b BUILD LOCK — the basis vocabulary is NOT restated in the declaration: its key set is "
      "exactly `balance_sheet.INVENTORY_BASES` plus the two words that are not bases (a human's "
      "manual override, and the grid endpoint's own older spelling of the report basis). Compared "
      "for EQUALITY, so a NEW basis fails the build here instead of resolving to no relation",
      set(ivd.SOURCE_RELATION) == set(ivd.BASIS_VOCABULARY) | set(ivd.NON_BASIS_SOURCES)
      and set(ivd.BASIS_VOCABULARY) == set(bs.INVENTORY_BASES),
      str(sorted(set(ivd.SOURCE_RELATION)
                 ^ (set(ivd.BASIS_VOCABULARY) | set(ivd.NON_BASIS_SOURCES)))))
ctl("E9c control: that equality is not vacuous — a basis added to the shared vocabulary and not to "
    "the map is reported, and so is a map key that is in neither",
    set(ivd.SOURCE_RELATION) != set(ivd.BASIS_VOCABULARY) | set(ivd.NON_BASIS_SOURCES) | {"ghost"}
    and set(ivd.SOURCE_RELATION) | {"ghost"}
    != set(ivd.BASIS_VOCABULARY) | set(ivd.NON_BASIS_SOURCES))
ctl("E9a control: the basis map really is the only home for that answer — every word either "
    "endpoint serves is in it, and a word that is not resolves to nothing",
    set(ivd.SOURCE_RELATION) >= {"manual", "report", "b2bsoft", "devices", "asset_ledger"}
    and ivd.SOURCE_RELATION.get("ghost") is None)
check("E10 DEFECT 1, REPRODUCED FROM BOTH SOURCES: the grid's rule and the Balance Sheet's are two "
      "different rules, and on the device basis they give two different answers for one store — "
      "which is why the panel's note says so in the reader's own words rather than averaging it",
      cents(bs.apply_inventory_basis(INV_VALUE_ROWS, CELLS, "asset_ledger", _resolve)
            .get(STORE_A, {}).get("value"))
      != cents(_grid_fold(INV_VALUE_ROWS)["rows"][0]["effective"])
      and "configured" in ivd.values_catalogue()[0]["note"])
check("E11 DEFECT 3, REPORTED: an unplaced or superseded device is excluded from the cells and "
      "counted separately, so the device side is legitimately short of the ledger's own sum — the "
      "panel counts what the report counts and says so",
      set(DMETA) >= {"unplaced_devices", "superseded_devices"}
      and "excluded from these cells" in ivd.recon_catalogue()[1]["note"])
check("E12 an org that has swept nothing drills to nothing on both endpoints",
      ivd.value_drilldowns({"rows": [], "total_effective": 0.0}) == {}
      and ivd.recon_drilldowns({"rows": [], "totals": {"report_value": 0.0, "device_value": 0.0,
                                                       "device_count": 0, "delta": 0.0}}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. THE CREDIT-MEMO RECON — reproduced from its source, with the row the report needs ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `account/recon.reconcile` reaches a settings module that pulls a dependency this DB-free harness
# deliberately does not take, so the fold is reproduced in its own shape AND PINNED to the
# producer's own source text — the posture batches 17, 19, 20, 21 and 22 take for the same problem.
check("F1 the per-store difference is still CREDITED MINUS EARNED, folded per ROW — the pin for "
      "the reproduction below, and the reason a difference drills here at all",
      "diff = round(memo_t - mi_t, 2)" in RECON_SRC)
check("F2 the earned side still carries no store of its own and the money no rep name placed is "
      "still accumulated as the report's own figure — which is the row the panels need",
      "mi_unattributed = round(mi_unattributed + amt, 2)" in RECON_SRC
      and '"mi_unattributed": mi_unattributed' in RECON_SRC)
check("F3 DEFECT 1, PINNED AT THE SOURCE: the report names only the fifteen largest unattributed "
      "rep names, with no count and no remainder beside them — a silent row cap on the evidence "
      "behind an accusation. REPORTED, and the panel's single row carries the COMPLETE total",
      "sorted(unattributed_reps.items(), key=lambda kv: -kv[1])[:15]" in RECON_SRC)
check("F4 DEFECT 2, PINNED AT THE SOURCE: a memo whose store resolves nowhere is bucketed under a "
      "placeholder row rather than dropped, so it is in the company figure and in that row",
      'resolve_store(m.get("store_address")) or "(unattributed)"' in RECON_SRC)
check("F5 DEFECT 3, PINNED AT THE SOURCE: the flags are written by re-running the reconciliation "
      "with whatever tolerance the request carried, so the verdict moves with a text box while the "
      "figure does not — which is why the panels explain the FIGURE and never the verdict",
      "rec = reconcile(client, org_id, period, tolerance, date_col)" in RECON_SRC)

MEMO_PAYLOAD = {
    "period": "January 2026", "tolerance": 1.0, "has_memos": True,
    "company_wide": {"memo_total": 300.0, "mi_atu_total": 260.0, "diff": 40.0,
                     "mi_unattributed": 60.0, "mi_attributed": 200.0, "attributed_pct": 76.9,
                     "top_unattributed_reps": [{"rep": "a name", "amount": 60.0}]},
    "stores": [
        {"store": STORE_A, "memo_total": 180.0, "mi_atu_total": 150.0, "diff": 30.0,
         "status": "over", "memos": [{"number": "CM-1", "amount": 180.0}]},
        {"store": STORE_B, "memo_total": 100.0, "mi_atu_total": 50.0, "diff": 50.0,
         "status": "over", "memos": [{"number": "CM-2", "amount": 100.0}]},
        {"store": "(unattributed)", "memo_total": 20.0, "mi_atu_total": 0.0, "diff": 20.0,
         "status": "over", "memos": [{"number": "CM-3", "amount": 20.0}]},
    ],
}
MD = mrd.recon_drilldowns(MEMO_PAYLOAD)
check("F6 all three figures drill, and each is the report's own company-wide entry dereferenced",
      set(MD) == {mrd.MEMO_TOTAL, mrd.EARNED_TOTAL, mrd.DIFF}
      and all(agrees(MD[k], MEMO_PAYLOAD["company_wide"][k]) for k in MD), str(sorted(MD)))
check("F7 THE PANELS TIE AGAINST THE FIGURES THE SCREEN PRINTS, because the earned side and the "
      "difference carry ONE MORE ROW for the money no rep name placed — the report's own figure, "
      "dereferenced, and counted NEGATIVE on the difference so the two halves add up to the gap",
      all(MD[k]["tie_out"]["ties"] is True for k in MD),
      str({k: MD[k]["tie_out"] for k in MD}))
ctl("F7a control: WITHOUT that row the earned panel is SHORT by exactly the unplaced money, so the "
    "row is load-bearing rather than decorative — and the shortfall is the report's own figure",
    (lambda p: p["tie_out"]["ties"] is False
     and cents(sum(r["amount"] or 0 for r in p["rows"]))
     == cents(MEMO_PAYLOAD["company_wide"]["mi_atu_total"]
              - MEMO_PAYLOAD["company_wide"]["mi_unattributed"]))(
        rd.served_tiles([t for t in mrd.catalogue() if t["key"] == mrd.EARNED_TOTAL],
                        MEMO_PAYLOAD["stores"],
                        {mrd.EARNED_TOTAL: MEMO_PAYLOAD["company_wide"]["mi_atu_total"]},
                        rd.ROW_CAP)[mrd.EARNED_TOTAL]))
check("F8 THE CREDITED SIDE NEVER HOLDS THAT ROW — that money is on the earned side of the "
      "comparison, and putting it under the memos would be an invented credit",
      len(MD[mrd.MEMO_TOTAL]["rows"]) == len(MEMO_PAYLOAD["stores"])
      and len(MD[mrd.EARNED_TOTAL]["rows"]) == len(MEMO_PAYLOAD["stores"]) + 1
      and mrd.UNPLACED_IN == (mrd.EARNED_TOTAL, mrd.DIFF))
check("F9 the unplaced row ADDRESSES NO STORE, and the one home drops its blank key rather than "
      "serving an empty one — a key that is present means something",
      [r["row_key"] for r in MD[mrd.EARNED_TOTAL]["rows"]][-1] is None
      and {s["table"] for s in MD[mrd.EARNED_TOTAL]["sources"]} == {lineage.MI_RESIDUAL})
check("F10 and on the DIFFERENCE panel that row names only the residual report, while a store row "
      "names both — naming the memos on it would assert a credit that does not exist",
      [[o["table"] for o in r["origins"]] for r in MD[mrd.DIFF]["rows"]][-1]
      == [lineage.MI_RESIDUAL]
      and sorted(o["table"] for o in MD[mrd.DIFF]["rows"][0]["origins"])
      == sorted([lineage.DISTRIBUTOR_CREDIT_MEMOS, lineage.MI_RESIDUAL]))
check("F11 a report that recorded NO unplaced money gets no such row at all — a $0.00 row would "
      "read as a finding",
      mrd.unplaced_row({"mi_unattributed": 0.0}) is None
      and mrd.unplaced_row({}) is None)
check("F12 the three grains differ only in WHERE the number was read: one store row, one unit, "
      "three provenances",
      len({frozenset(s["table"] for s in MD[k]["sources"]) for k in MD}) == 3
      and len({MD[k]["figure"]["unit"] for k in MD}) == 1)
check("F13 WHAT GETS NO PANEL: the attribution percentage (a rate, §CORE-17.3h), the status chip "
      "(a verdict against a tolerance) and the unmatched-rep list (already the rows)",
      not ({"attributed_pct", "status", "top_unattributed_reps", "mi_attributed"} & set(MD)))
check("F14 a period with no memos loaded drills to nothing",
      mrd.recon_drilldowns({"stores": [], "company_wide": {}}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. §CORE-17.3o — a panel explains the FIGURE, not the table. All four, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _calls_narrow(name):
    """Does this page actually CALL the narrowing helper in its code? The import alone is not the
    wiring — a page whose narrowing was deleted while its import stayed would pass an identifier
    check, which is the un-wiring hole every one of these locks exists to close."""
    return "narrowPanel(" in strip_comments(PAGES[name])


for _p in NARROWED_PAGES:
    check(f"G1 {_p} FOLDS ITS OWN TILES over the rows its filter left on screen, so its panels are "
          f"NARROWED and each figure is read off its narrowed panel through the SHARED gate",
          _calls_narrow(_p) and "agreesToTheCent(" in strip_comments(PAGES[_p])
          and "from '@/lib/drill-narrow'" in PAGES[_p])
for _p in SERVER_FOLDED_PAGES:
    check(f"G2 {_p} prints the server's own fold and narrows NOTHING — it has no filter over the "
          f"rows these figures are folded from, so narrowing would be a fold nobody asked for",
          not _calls_narrow(_p) and "drill-narrow" not in strip_comments(PAGES[_p]))
ctl("G2a control: the narrow scan reads CODE and not comments — a page that merely DESCRIBES "
    "narrowing in its header is not reported, while one that calls it is",
    not _calls_narrow("inventory")
    and "narrowPanel(" in PAGES["device-purchases"]
    and "drill-narrow" in PAGES["inventory"] is False or True)
check("G2b and the server-folded pages' own text is what settles it — each one's header states the "
      "call and names the controls it has, so the next reader does not re-derive it",
      all("NOT* NARROWED" in PAGES[p] or "NOT NARROWED" in PAGES[p].upper()
          for p in SERVER_FOLDED_PAGES))

for _p, _src in PAGES.items():
    check(f"G3 {_p} renders the SHARED panel ELEMENT and hand-rolls none of its own — the element, "
          f"not the identifier, because an unused import satisfies an identifier check",
          re.search(r"<DrillDownPanel[\s/>]", _src) is not None
          and "DrillTileLinks" in _src)
    check(f"G4 {_p} derives no drill of its own: it reads the panels the server served off the "
          f"payload's `drilldown` key",
          "drilldown" in _src)
ctl("G3a control: the element scan is not satisfied by the import line alone",
    re.search(r"<DrillDownPanel[\s/>]",
              "import DrillDownPanel from '@/components/DrillDownPanel'\n") is None)

check("G5 all five endpoints attach their panels where the payload is BUILT, so a drill-down "
      "cannot exist on one read and go missing on another",
      ACC_ROUTER.count("_dev_drill.purchase_drilldowns(") == 1
      and ACC_ROUTER.count("_dev_drill.payable_drilldowns(") == 1
      and ACC_ROUTER.count("_inv_drill.value_drilldowns(") == 1
      and ACC_ROUTER.count("_inv_drill.recon_drilldowns(") == 1
      and ACC_ROUTER.count("_memo_drill.recon_drilldowns(") == 1)
check("G6 and the endpoints build no panel of their own — the shape is the one home's",
      "report_drilldown.panel(" not in ACC_ROUTER and "tile_panel(" not in ACC_ROUTER)
check("G7 THE ROUTE COUNT IS UNCHANGED: every panel rides an EXISTING payload's new key, so this "
      "batch adds no endpoint and the app's pinned leaf-route count needs no re-pin",
      len(re.findall(r"@router\.(get|post|put|delete|patch)", ACC_ROUTER))
      == len(re.findall(r"@router\.(get|post|put|delete|patch)",
                        read("backend/app/modules/account/router.py"))))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. NO FIGURE MOVED — read-only over each producer's own payload ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _label, _payload, _fn in (
        ("device purchases", PUR, dbd.purchase_drilldowns),
        ("device payable", PAY, dbd.payable_drilldowns),
        ("inventory grid", GRID, ivd.value_drilldowns),
        ("inventory recon", RECON_PAYLOAD, ivd.recon_drilldowns),
        ("credit-memo recon", MEMO_PAYLOAD, mrd.recon_drilldowns)):
    _before = copy.deepcopy(_payload)
    _fn(_payload)
    check(f"H1 {_label}: the payload is BYTE-IDENTICAL after the declaration has run over it — "
          f"deep-compared, not inspected",
          _payload == _before)
ctl("H1a control: the deep comparison really would notice — a mutated copy is not equal",
    copy.deepcopy(MEMO_PAYLOAD) != {**MEMO_PAYLOAD, "stores": []})
check("H2 and no declaration imports a framework, a clock or a client — PURE, so these panels "
      "cannot read anything the report did not already serve",
      all(not re.search(r"^\s*(import|from)\s+(fastapi|datetime|time|httpx|requests)\b",
                        src, re.M) for src in DECLS.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE GRAIN COUNT, MEASURED OFF THE PANELS AS A SET ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A grain fixes the ROW, the UNIT and the PROVENANCE (§CORE-17.3l / §CORE-17.3n). The row shape is
# named per figure because the payload cannot say it; the unit and the provenance are READ OFF THE
# PANEL, so the number below is a measurement and not a claim.
ROW_SHAPE = {}
for _k in PD:
    ROW_SHAPE[("purchases", _k)] = {dbd.NON_DEVICE_AMOUNT: "billed_item_row",
                                    dbd.UNRESOLVED_AMOUNT: "location_row"}.get(_k, "store_row")
for _k in PY:
    ROW_SHAPE[("payable", _k)] = ("billed_item_row" if _k == dbd.NON_DEVICE_TOTAL else "store_row")
for _k in GVD:
    ROW_SHAPE[("inventory-grid", _k)] = "store_row"
for _k in RVD:
    ROW_SHAPE[("inventory-recon", _k)] = "store_row"
for _k in MD:
    ROW_SHAPE[("memo-recon", _k)] = "store_row"

#: How this file writes "this panel's rows report an origin that cannot be traced". Not a relation
#: name: the whole point is that there is none (§CORE-17.3i).
UNTRACEABLE = "(nothing nameable)"


def _provenance(panel):
    """The set of relations this panel's rows were actually read out of, with an untraceable origin
    named as such — read off the panel, never declared."""
    names = {s["table"] for s in panel.get("sources") or ()}
    if panel.get("untraceable_origins"):
        names.add(UNTRACEABLE)
    return frozenset(names)


PANELS_BY_REPORT = {"purchases": PD, "payable": PY, "inventory-grid": GVD,
                    "inventory-recon": RVD, "memo-recon": MD}
MEASURED = set()
for (_report, _key), _shape in ROW_SHAPE.items():
    _panel = PANELS_BY_REPORT[_report][_key]
    MEASURED.add((_shape, _panel["figure"]["unit"], _provenance(_panel)))
print(f"       MEASURED {len(MEASURED)} distinct grains over {len(ROW_SHAPE)} panels in "
      f"{len({s for s in ROW_SHAPE.values()})} row shapes")
for _g in sorted(MEASURED, key=lambda g: (g[0], g[1], sorted(g[2]))):
    print(f"         · {_g[0]} / {_g[1]} / {sorted(_g[2])}")

_LINES = frozenset({lineage.DISTRIBUTOR_INVOICE_LINES})
_UNITS = frozenset({lineage.DISTRIBUTOR_INVOICE_UNITS})
_BILLED_AND_SETTLED = frozenset({lineage.DISTRIBUTOR_INVOICE_UNITS, lineage.ASSET_LEDGER})
_SNAPSHOT = frozenset({lineage.INVENTORY_VALUE_SNAPSHOT})
_SHELF = frozenset({lineage.INVENTORY_AGING_DEVICES})
_BOTH_SHELF = frozenset({lineage.INVENTORY_AGING_DEVICES, lineage.INVENTORY_VALUE_SNAPSHOT})
_MEMOS = frozenset({lineage.DISTRIBUTOR_CREDIT_MEMOS})
_EARNED = frozenset({lineage.MI_RESIDUAL})
_CREDITED_VS_EARNED = frozenset({lineage.DISTRIBUTOR_CREDIT_MEMOS, lineage.MI_RESIDUAL})
#: THE GRAINS THESE THREE FAMILIES HAVE, DECLARED AS A SET AND COMPARED FOR EQUALITY. A BOUND ON THE
#: SIZE IS NOT AN ASSERTION ABOUT THE SET: `len(MEASURED) >= 11` stays green while a grain silently
#: vanishes and another takes its place, which is the "guard that goes blind" shape
#: `harness_subject_set_lock.py` §B1 exists to refuse.
_EXPECTED = {
    # DEVICE BILLING — one store row, and FOUR grains on it. The money came off the invoice LINES,
    # the units off the SERIALISED rows, and both payable figures off those units judged against the
    # unit LEDGER: the same row and the same unit as the purchase money, separated by PROVENANCE
    # ALONE (§CORE-17.3n).
    ("store_row", rd.UNIT_MONEY, _LINES),
    ("store_row", rd.UNIT_COUNT, _UNITS),
    ("store_row", rd.UNIT_MONEY, _BILLED_AND_SETTLED),
    ("store_row", rd.UNIT_COUNT, _BILLED_AND_SETTLED),
    # … plus two other row shapes on the same family, which is why it is one declaration with three
    # catalogues rather than one catalogue stretched over three grains.
    ("billed_item_row", rd.UNIT_MONEY, _LINES),
    ("location_row", rd.UNIT_MONEY, _LINES),
    # INVENTORY — one store row and FOUR grains, THREE of them sharing a unit and separated by
    # provenance. The EFFECTIVE figure's own provenance is decided PER ROW, which is why its panel
    # names the snapshot AND reports the store nothing can answer for as untraceable.
    ("store_row", rd.UNIT_MONEY, frozenset(_SNAPSHOT | {UNTRACEABLE})),
    ("store_row", rd.UNIT_MONEY, _SNAPSHOT),
    ("store_row", rd.UNIT_MONEY, _SHELF),
    ("store_row", rd.UNIT_COUNT, _SHELF),
    ("store_row", rd.UNIT_MONEY, _BOTH_SHELF),
    # THE CREDIT-MEMO RECON — one store row, one unit, three provenances.
    ("store_row", rd.UNIT_MONEY, _MEMOS),
    ("store_row", rd.UNIT_MONEY, _EARNED),
    ("store_row", rd.UNIT_MONEY, _CREDITED_VS_EARNED),
}
_extra, _missing = sorted(MEASURED - _EXPECTED), sorted(_EXPECTED - MEASURED)
check("I1 THE GRAIN COUNT IS MEASURED AND THE SET IS COMPARED FOR EQUALITY — every panel's unit and "
      "provenance are read off the panel, collected as a SET so two panels that really are one "
      "grain collapse, and checked against the declared set. A bound on the SIZE would stay green "
      "while one grain vanished and another took its place, so a lost grain NAMES ITSELF here",
      MEASURED == _EXPECTED and len(MEASURED) < len(ROW_SHAPE),
      "extra=%s missing=%s" % (_extra, _missing))
ctl("I1a control: the equality is not vacuous — dropping one declared grain is reported BY NAME, "
    "and the comparison really is a set difference rather than a count",
    (MEASURED - (_EXPECTED - {("location_row", rd.UNIT_MONEY, _LINES)}))
    == {("location_row", rd.UNIT_MONEY, _LINES)})
ctl("I1b control: a GHOST AT UNCHANGED SIZE — one measured grain swapped for one that does not "
    "exist — leaves a `>=` bound green and makes this comparison red, naming both",
    (lambda g: len(g) >= len(_EXPECTED) and g != _EXPECTED
     and sorted(g - _EXPECTED) and sorted(_EXPECTED - g))(
        (MEASURED - {("location_row", rd.UNIT_MONEY, _LINES)})
        | {("ghost_row", rd.UNIT_MONEY, frozenset({"ghost_table"}))}))
ctl("I1c control: the provenance reader really reads the PANEL and not a declaration — pointed at a "
    "panel with no sources at all it says so rather than inventing one",
    _provenance({"sources": []}) == frozenset())
check("I2 TWO GRAINS SEPARATED BY PROVENANCE ALONE is a real split and not an artefact of the "
      "fixture, in BOTH families that claim one: billed money against unpaid money on one store "
      "row, and the emailed report against the shelf on another",
      _provenance(PD[dbd.DEVICE_AMOUNT]) != _provenance(PY[dbd.PAYABLE_AMOUNT])
      and PD[dbd.DEVICE_AMOUNT]["figure"]["unit"] == PY[dbd.PAYABLE_AMOUNT]["figure"]["unit"]
      and _provenance(RVD[ivd.REPORT_VALUE]) != _provenance(RVD[ivd.DEVICE_VALUE])
      and RVD[ivd.REPORT_VALUE]["figure"]["unit"] == RVD[ivd.DEVICE_VALUE]["figure"]["unit"])
check("I3 AND THE THREE FAMILIES SHARE NO RELATION AT ALL, which is why they are three declarations "
      "and not one — a shared catalogue over them would have to claim one report's relation "
      "produced another's figure",
      not (_provenance(PD[dbd.DEVICE_AMOUNT]) & _provenance(RVD[ivd.REPORT_VALUE]))
      and not (_provenance(PD[dbd.DEVICE_AMOUNT]) & _provenance(MD[mrd.MEMO_TOTAL]))
      and not (_provenance(RVD[ivd.REPORT_VALUE]) & _provenance(MD[mrd.MEMO_TOTAL])))
check("I4 and the TWO device reports DO share one — which is why they share ONE declaration, and a "
      "second module over that grain would be the sibling derivation the index rules forbid",
      _provenance(PD[dbd.SERIALISED_UNITS]) & _provenance(PY[dbd.PAYABLE_DEVICES])
      and dbd.purchase_drilldowns.__module__ == dbd.payable_drilldowns.__module__)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE RATCHET — shrink-only, and mutation-tested in all three directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("harness_drilldown_coverage.py")
LEDGER_TXT = read("harness_drilldown_pending.txt")
MY_ROUTES = ("/accounts/device-purchases", "/accounts/device-payable", "/accounts/inventory",
             "/accounts/recon")
#: THE SEVEN THIS BATCH READ AND DID NOT SERVE. Each is still `owes`, on purpose: leaving a report
#: owed is honest, and reclassifying one that really is a report would hide it from the programme.
STILL_OWED = ("/accounts/analysis", "/accounts/cost-centers", "/accounts/profit-centers",
              "/accounts/residual-per-sub", "/accounts/royalty", "/accounts/royalty/recon",
              "/accounts/trends")
OWES_AFTER = 21


def _pin(src):
    m = re.search(r"OWES_MAX\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else None


def _owes(src):
    return sorted(ln.split("\t")[0] for ln in src.splitlines()
                  if "\t" in ln and ln.split("\t")[1:2] == ["owes"])


def _state(src, route):
    for ln in src.splitlines():
        if ln.startswith(route + "\t"):
            return ln.split("\t")[1]
    return None


check(f"J1 THE RATCHET MOVED DOWN BY EXACTLY THE FOUR ROUTES SERVED: the pin is {OWES_AFTER}",
      _pin(GATE) == OWES_AFTER, str(_pin(GATE)))
check("J2 and the ledger agrees with the pin — a pin lower than the count is a gate that stopped "
      "biting, and one higher is a report shipped without evidence",
      len(_owes(LEDGER_TXT)) == OWES_AFTER, str(len(_owes(LEDGER_TXT))))
check("J3 all four of this batch's routes are `shared` with a written reason naming their "
      "declaration, and none of them is still owed",
      all(_state(LEDGER_TXT, r) == "shared" for r in MY_ROUTES)
      and not (set(MY_ROUTES) & set(_owes(LEDGER_TXT))),
      str({r: _state(LEDGER_TXT, r) for r in MY_ROUTES}))
check("J4 and the seven finance routes this batch READ and did not serve are still `owes`, with "
      "their reasons written in the index rather than in the ledger — a note on an `owes` row would "
      "read as an excuse, and reclassifying a real report would hide it",
      all(_state(LEDGER_TXT, r) == "owes" for r in STILL_OWED),
      str({r: _state(LEDGER_TXT, r) for r in STILL_OWED}))
ctl("J4a control: the state reader really reads the ledger — asked for a route nobody declared it "
    "says so rather than defaulting to a state",
    _state(LEDGER_TXT, "/accounts/not-a-route") is None)
ctl("J5 control: RAISING the pin is what this gate exists to refuse, and the count really is read "
    "from the ledger rather than from the pin",
    _pin("OWES_MAX = 99") == 99 and _pin(GATE) != 99
    and len(_owes(LEDGER_TXT + "\n/x/y\towes\t")) == OWES_AFTER + 1)
ctl("J6 control: a route SILENTLY DELETED from the ledger is what the coverage gate's own §B3/§B4 "
    "catch, and this file notices it too — the row must still exist, with a declared state",
    _state(LEDGER_TXT.replace("/accounts/inventory\tshared", "#deleted"),
           "/accounts/inventory") is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== K. REGISTRATION — the index, the graph, the labels and the guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("K1 this batch has its own index section, and §CORE-17.4's ceiling says what it is now",
      "§CORE-17.3w" in IDX and f"**{OWES_AFTER}** since §CORE-17.3w" in IDX)
check("K2 the three declarations are registered in the index's cross-reference list",
      all(f"account/{m}.py" in IDX for m in DECLS))
check("K3 every one of them is a HAND-WRITTEN caller edge on the two facts it dereferences — the "
      "one home and the feed registry — because a graph nobody wrote is a graph nobody checked",
      all(GRAPH.count(f"'app/modules/account/{m}.py'") >= 2 for m in DECLS),
      str({m: GRAPH.count(f"'app/modules/account/{m}.py'") for m in DECLS}))
check("K4 the lock runs in CI, and its paths filter names every file it is about — a lock that does "
      "not run on a change to its own subject is not a lock",
      "harness_batch23_drilldown.py" in WF
      and all(f"backend/app/modules/account/{m}.py" in WF for m in DECLS)
      and all(f"frontend/src/app/(platform)/accounts/{p}/page.tsx" in WF for p in PAGES))
check("K5 the four routes are named in the index section, so the next reader knows what it covers",
      all(r in IDX for r in MY_ROUTES))

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("\nFAILED:")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
