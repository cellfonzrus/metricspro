"""PROOF — THE SEVEN DEVICE-ASSET REPORTS NAME THE ROWS BEHIND THEIR FIGURES.

DB-free, stdlib only, driving the REAL declarations (`asset/device_asset_drilldown.py`) through the
REAL shared fold (`core/report_drilldown.tiles` / `tile_panel` / `panel`), over fixtures shaped like
what each producer actually hands them. Batch 12 of the §CORE-17 audit: /commcalc/asset,
/commcalc/asset/dashboard, /commcalc/asset/on-inventory, /commcalc/asset/owed-weekly,
/commcalc/asset/inventory-recon, /commcalc/asset/borrowed and /commcalc/asset/lending.

WHAT THIS BATCH IS ABOUT.

1. THE GRAIN COUNT IS MEASURED, NOT ASSUMED. Six row shapes, seven catalogues, and §Y proves each
   catalogue's keys really address its own grain — four of them are keyed by a store and are still
   four different populations in three different units. §A–§G.

2. PROVENANCE READ PER ROW WHERE THE ANSWER DIFFERS PER ROW. The POS recon names the ledger, the POS
   snapshot, or both, off the producer's own `in_asset`/`in_b2b` verdict. §E.

3. ABSENCE IS NEVER ZERO. A PayGo batch the portal reported without an amount, and a store the POS
   never mentioned, are SHOWN and not folded in at $0.00 — and neither moves a figure by a cent,
   because a null adds nothing to a sum either way. §G2, §Z3, §Z9.

4. A COMPOSED TOTAL, A LEAF AND A COUNT WHOSE LIST IS ON SCREEN GET NO PANEL AT ALL. Not an empty
   one: no key. §Y5, §Y6.

5. TWO FOLDS BECAME ONE, AND THREE FIGURES DID NOT MOVE. The RMA net-loss rule, the On-Inventory
   predicate and the lifetime device count each have ONE home now; §Z6–§Z8 pin each against the
   arithmetic it replaced, and each has a negative control proving the comparison can fail.

6. FOUR LIVE DEFECTS, REPORTED NOT HIDDEN. §Z10–§Z12.

  python3.11 backend/harness_batch12_drilldown.py
"""
import ast
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.asset import device_asset_drilldown as dd                       # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage                # noqa: E402
from app.modules.core import report_drilldown as rd                              # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label
          + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECL = os.path.join(ROOT, "backend", "app", "modules", "asset", "device_asset_drilldown.py")
ASSET_ROUTER = os.path.join(ROOT, "backend", "app", "modules", "asset", "router.py")
CC_ROUTER = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py")
PAGES = {
    "/commcalc/asset": "frontend/src/app/(platform)/commcalc/asset/page.tsx",
    "/commcalc/asset/dashboard": "frontend/src/app/(platform)/commcalc/asset/dashboard/page.tsx",
    "/commcalc/asset/on-inventory": "frontend/src/app/(platform)/commcalc/asset/on-inventory/page.tsx",
    "/commcalc/asset/owed-weekly": "frontend/src/app/(platform)/commcalc/asset/owed-weekly/page.tsx",
    "/commcalc/asset/inventory-recon": "frontend/src/app/(platform)/commcalc/asset/inventory-recon/page.tsx",
    "/commcalc/asset/borrowed": "frontend/src/app/(platform)/commcalc/asset/borrowed/page.tsx",
    "/commcalc/asset/lending": "frontend/src/app/(platform)/commcalc/asset/lending/page.tsx",
}


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def _code_only(path):
    """The file's CODE, with comments and docstrings dropped — the §CORE-17.3j narrowing of
    `harness_pl_drill_detail` §G5, reused rather than re-derived. A build lock about what a module
    DOES must not fire on prose describing it. A file that will not tokenise is returned WHOLE, so
    the narrowing fails CLOSED."""
    try:
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                    out.append(tok.string)
        return " ".join(out)
    except Exception:
        return open(path, encoding="utf-8").read()


def _module_constant(path, name):
    """A module-level literal, read out of the REAL file by AST — so a harness that needs the
    router's own declaration reads it instead of keeping a second copy. The asset router imports
    FastAPI and a Supabase client, so it cannot be imported into a DB-free proof; its data can still
    be read honestly."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    return None


def _func_src(path, name):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines(True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1:node.end_lineno])
    return ""


def _row(panel, label):
    return next((r for r in (panel or {}).get("rows") or [] if r.get("label") == label), None)


def _tie(panel):
    return ((panel or {}).get("tie_out") or {}).get("ties")


def _tables(row):
    return sorted((row or {}).get("sources") or [])


#: The asset router's OWN charge classification, read out of the real file (see `_module_constant`).
CHARGE_GROUPS = _module_constant(ASSET_ROUTER, "CHARGE_GROUPS") or {}
GROUP_LABELS = _module_constant(ASSET_ROUTER, "GROUP_LABELS") or {}


def group_of(cat):
    """`router._cat_to_group`, over the router's OWN `CHARGE_GROUPS`. §Y7 fails the build if that
    function stops being this loop, so this cannot quietly become a second classification."""
    for g, cats in CHARGE_GROUPS.items():
        if cat in cats:
            return g
    return None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A. THE ASSET LEDGER'S FIVE CARDS — Σ over its own By-Status / By-Category tables.
#
# The fixture is DEVICE rows the shape `GET /asset/summary` selects, folded here with that endpoint's
# OWN expressions, so the figures the panels tie against are the figures it really serves.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the Asset Ledger cards open to the statuses and categories behind them ==")

LEDGER_DEVICES = [
    {"status": "Open", "category": "On Inventory", "owed_to_vip": 100.0, "reimbursement": 0.0,
     "commissions": 5.0},
    {"status": "Open", "category": "RMA", "owed_to_vip": 40.0, "reimbursement": 10.0,
     "commissions": 2.5},
    {"status": "Paid In Full", "category": "On Inventory - Aged", "owed_to_vip": 60.0,
     "reimbursement": 60.0, "commissions": 1.5},
    {"status": "Partial", "category": "SHIPPING", "owed_to_vip": 20.0, "reimbursement": 5.0,
     "commissions": 0.0},
]


def _endpoint_summary(rows):
    """`GET /asset/summary`'s own fold, expression for expression, so this proof ties the panels
    against the numbers the endpoint really serves rather than against numbers of its own."""
    by_status, by_category = {}, {}
    for r in rows:
        s = r.get("status") or "Unknown"
        d = by_status.setdefault(s, {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0})
        d["count"] += 1
        d["owed"] += float(r.get("owed_to_vip") or 0)
        d["reimbursed"] += float(r.get("reimbursement") or 0)
        d["fees"] += float(r.get("commissions") or 0)
        c = r.get("category") or "Unknown"
        e = by_category.setdefault(c, {"count": 0, "owed": 0, "fees": 0})
        e["count"] += 1
        e["owed"] += float(r.get("owed_to_vip") or 0)
        e["fees"] += float(r.get("commissions") or 0)
    return {
        "loaded": True, "total_rows": len(rows),
        "total_fees": round(sum(float(r.get("commissions") or 0) for r in rows), 2),
        "total_open_balance": round(sum(float(r.get("owed_to_vip") or 0) for r in rows
                                        if (r.get("status") or "") == dd.OPEN_STATUS), 2),
        "total_reimbursed": round(sum(float(r.get("reimbursement") or 0) for r in rows), 2),
        "total_owed_alltime": round(sum(float(r.get("owed_to_vip") or 0) for r in rows), 2),
        "on_inventory": round(sum(float(r.get("owed_to_vip") or 0) for r in rows
                                  if dd.counts_as_on_inventory(r.get("category"))), 2),
        "by_status": by_status, "by_category": by_category,
    }


SUMMARY = _endpoint_summary(LEDGER_DEVICES)
LEDGER_P = dd.ledger_drilldowns(SUMMARY)

check("A1 all five headline cards serve a panel, and nothing else does",
      sorted(LEDGER_P) == ["on_inventory", "total_fees", "total_open_balance", "total_owed_alltime",
                           "total_reimbursed"], sorted(LEDGER_P))
check("A2 every one TIES against the endpoint's own figure",
      all(_tie(p) is True for p in LEDGER_P.values()),
      {k: v["tie_out"] for k, v in LEDGER_P.items() if _tie(v) is not True})
check("A3 the Open Balance card counts ONLY the open status and shows the others as context — a "
      "card that counts one status is readable only beside the rest",
      (LEDGER_P["total_open_balance"]["tie_out"]["counted_rows"] == 1
       and LEDGER_P["total_open_balance"]["tie_out"]["shown_not_counted"] == 2
       and LEDGER_P["total_open_balance"]["rows_total_count"] == 3),
      LEDGER_P["total_open_balance"]["tie_out"])
check("A4 the On-Inventory card is folded over the CATEGORY table, not the status table — a panel "
      "over the wrong population would be proof of a different number",
      (LEDGER_P["on_inventory"]["rows_total_count"] == 4
       and _row(LEDGER_P["on_inventory"], "On Inventory") is not None
       and _row(LEDGER_P["on_inventory"], "Open") is None),
      [r["label"] for r in LEDGER_P["on_inventory"]["rows"]])
check("A5 and it counts BOTH on-inventory categories, including the aged one",
      LEDGER_P["on_inventory"]["tie_out"]["counted_rows"] == 2
      and LEDGER_P["on_inventory"]["figure"]["amount"] == 160.0,
      LEDGER_P["on_inventory"]["tie_out"])
check("A6 every row names the distributor's unit ledger, by its registry constant",
      all(_tables(r) == [lineage.ASSET_LEDGER] for p in LEDGER_P.values() for r in p["rows"]),
      [(r["label"], _tables(r)) for p in LEDGER_P.values() for r in p["rows"]][:4])
check("A7 and each row is addressed by the key that finds it in the ledger",
      (_row(LEDGER_P["total_fees"], "Open")["row_key"] == {"status": "Open"}
       and _row(LEDGER_P["on_inventory"], "RMA")["row_key"] == {"category": "RMA"}),
      (_row(LEDGER_P["total_fees"], "Open")["row_key"],
       _row(LEDGER_P["on_inventory"], "RMA")["row_key"]))
check("A8 an UNLOADED ledger drills to nothing at all, rather than five panels asserting $0.00 "
      "behind no rows",
      dd.ledger_drilldowns({"loaded": False}) == {} and dd.ledger_drilldowns(None) == {},
      dd.ledger_drilldowns({"loaded": False}))
check("A9 the panel's own figure IS the card's figure, so the two cannot drift",
      all(LEDGER_P[k]["figure"]["amount"] == SUMMARY[k] for k in LEDGER_P),
      {k: (LEDGER_P[k]["figure"]["amount"], SUMMARY[k]) for k in LEDGER_P})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B. THE CHARGES DASHBOARD — over the Postgres aggregate's own store × category rows.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the Charges Dashboard tiles open to the stores behind them ==")

APPEAL_CAT = CHARGE_GROUPS.get("appeals", ["?"])[0]
FEE_CAT = CHARGE_GROUPS.get("vip_fees", ["?"])[0]
AGG = [
    {"store": "S-1", "market": "M-1", "category": APPEAL_CAT, "cnt": 2, "owed": 120.0, "reimb": 0.0},
    {"store": "S-2", "market": "M-2", "category": APPEAL_CAT, "cnt": 1, "owed": 30.0, "reimb": 0.0},
    {"store": "S-1", "market": "M-1", "category": FEE_CAT, "cnt": 4, "owed": 16.0, "reimb": 0.0},
    # RMA, the three shapes the net-loss rule has: nothing back, part back, all back.
    {"store": "S-1", "market": "M-1", "category": dd.RMA_CATEGORY, "cnt": 1, "owed": 90.0, "reimb": 0.0},
    {"store": "S-2", "market": "M-2", "category": dd.RMA_CATEGORY, "cnt": 1, "owed": 50.0, "reimb": 20.0},
    {"store": "S-3", "market": "M-2", "category": dd.RMA_CATEGORY, "cnt": 1, "owed": 25.0, "reimb": 25.0},
    # A category no charge group claims — it must reach no tile at all.
    {"store": "S-9", "market": "M-9", "category": "Something Unmapped", "cnt": 7, "owed": 700.0, "reimb": 0.0},
]


def _endpoint_charges(agg):
    """`GET /asset/charges-summary`'s own group fold (the part this batch did not touch), plus the
    Total Loss block as the endpoint now computes it — through the declaration's one home."""
    groups = {}
    for gk in CHARGE_GROUPS:
        groups[gk] = {"key": gk, "label": GROUP_LABELS.get(gk, gk), "count": 0, "owed": 0.0,
                      "by_category": {}, "by_store": {}}
    for row in agg:
        gk = group_of(row.get("category") or "")
        if not gk:
            continue
        g = groups[gk]
        g["count"] += int(row.get("cnt") or 0)
        g["owed"] += float(row.get("owed") or 0)
        c = row.get("category") or "Unknown"
        g["by_category"].setdefault(c, {"category": c, "count": 0, "owed": 0.0})
        g["by_category"][c]["owed"] += float(row.get("owed") or 0)
    for g in groups.values():
        g["owed"] = round(g["owed"], 2)
        g["by_category"] = sorted(({**v, "owed": round(v["owed"], 2)} for v in g["by_category"].values()),
                                  key=lambda x: x["owed"], reverse=True)
    appeals = round(sum(v["owed"] for v in groups["appeals"]["by_category"]), 2)
    rma = round(sum(dd.rma_net_loss(r) for r in agg if dd.is_rma_row(r)), 2)
    return groups, {"total": round(appeals + rma, 2), "appeals": appeals, "rma": rma}


GROUPS, TOTAL_LOSS = _endpoint_charges(AGG)
CHARGE_P = dd.charges_drilldowns(AGG, GROUPS, TOTAL_LOSS, group_of=group_of)

check("B1 a tile is served for every charge group that has rows, plus BOTH halves of the headline",
      (sorted(CHARGE_P) == ["group_appeals", "group_vip_fees", "loss_appeals", "loss_rma"]),
      sorted(CHARGE_P))
check("B2 THE HEADLINE TOTAL SERVES NO KEY AT ALL — it is appeals + RMA, composed from two other "
      "figures, so no set of rows adds up to it (§CORE-17.3h)",
      not any(k for k in CHARGE_P if "total" in k and k not in ("loss_appeals", "loss_rma")),
      sorted(CHARGE_P))
check("B3 every tile ties against the endpoint's own figure",
      all(_tie(p) is True for p in CHARGE_P.values()),
      {k: v["tie_out"] for k, v in CHARGE_P.items() if _tie(v) is not True})
check("B4 a category NO charge group claims reaches no tile — the groups partition the rows they "
      "claim and claim nothing else",
      all(_row(p, "S-9 · Something Unmapped") is None for p in CHARGE_P.values()),
      [k for k, p in CHARGE_P.items() if _row(p, "S-9 · Something Unmapped")])
check("B5 the RMA panel's figure is the NET loss, folded per store — the whole amount where nothing "
      "came back, the shortfall where it fell short, and a MEASURED $0.00 where it did not",
      (_row(CHARGE_P["loss_rma"], "S-1 · %s" % dd.RMA_CATEGORY)["amount"] == 90.0
       and _row(CHARGE_P["loss_rma"], "S-2 · %s" % dd.RMA_CATEGORY)["amount"] == 30.0
       and _row(CHARGE_P["loss_rma"], "S-3 · %s" % dd.RMA_CATEGORY)["amount"] == 0.0),
      [(r["label"], r["amount"]) for r in CHARGE_P["loss_rma"]["rows"]])
check("B6 and the fully-reimbursed row is COUNTED, because a measured zero is a measurement",
      (CHARGE_P["loss_rma"]["tie_out"]["counted_rows"] == 3
       and _row(CHARGE_P["loss_rma"], "S-3 · %s" % dd.RMA_CATEGORY)["measured"] is True),
      CHARGE_P["loss_rma"]["tie_out"])
check("B7 the appeals HALF of the headline and the appeals GROUP tile are the same rows and the "
      "same figure — the headline is not a second derivation of it",
      (CHARGE_P["loss_appeals"]["figure"]["amount"] == CHARGE_P["group_appeals"]["figure"]["amount"]
       and CHARGE_P["loss_appeals"]["rows_total_count"]
       == CHARGE_P["group_appeals"]["rows_total_count"]),
      (CHARGE_P["loss_appeals"]["figure"], CHARGE_P["group_appeals"]["figure"]))
check("B8 every row names the unit ledger and is addressed by store AND category",
      all(_tables(r) == [lineage.ASSET_LEDGER] and set(r["row_key"]) == {"store", "category"}
          for p in CHARGE_P.values() for r in p["rows"]),
      [(r["label"], _tables(r), r["row_key"]) for p in CHARGE_P.values() for r in p["rows"]][:3])
check("B9 an EMPTY aggregate drills to nothing",
      dd.charges_drilldowns([], GROUPS, TOTAL_LOSS, group_of=group_of) == {}
      and dd.charges_drilldowns(None, None, None, group_of=group_of) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C. ON-INVENTORY BY STORE — six figures over the per-store table the screen prints.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. On-Inventory by Store opens to its stores ==")

ONINV_STORES = [
    {"store": "S-1", "market": "M-1", "count": 10, "owed": 900.0, "under45_count": 4,
     "under45_owed": 400.0, "warn_count": 2, "warn_owed": 200.0, "missed_count": 2,
     "missed_owed": 250.0, "zero_count": 1, "unknown_age_count": 1, "unknown_age_owed": 50.0},
    {"store": "S-2", "market": "M-2", "count": 3, "owed": 150.0, "under45_count": 3,
     "under45_owed": 150.0, "warn_count": 0, "warn_owed": 0.0, "missed_count": 0,
     "missed_owed": 0.0, "zero_count": 0, "unknown_age_count": 0, "unknown_age_owed": 0.0},
]
ONINV = {
    "stores": ONINV_STORES,
    "totals": {
        "store_count": 2,
        "device_count": 13,
        "owed": 1050.0,
        "missed_owed": 250.0,
        "warn_owed": 200.0,
        "zero_count": 1,
        "unknown_age_count": 1,
        "unknown_age_owed": 50.0,
        "total_amount": 1050.0,
        "total_amount_column": dd.COL_OWED,
        "total_phones_outstanding": 13,
    },
}
ONINV_P = dd.on_inventory_drilldowns(ONINV)

check("C1 every figure the page shows above or below its table opens, and the STORE COUNT does not "
      "— the table below IS the list of stores (batch-11 precedent)",
      (sorted(ONINV_P) == ["missed_owed", "total_amount", "total_phones_outstanding",
                           "unknown_age_owed", "warn_owed", "zero_count"]),
      sorted(ONINV_P))
check("C2 every one ties", all(_tie(p) is True for p in ONINV_P.values()),
      {k: v["tie_out"] for k, v in ONINV_P.items() if _tie(v) is not True})
check("C3 a COUNT figure is served in count units and a MONEY figure in money — the unit vocabulary "
      "is the one home's, so a screen never has to guess",
      (ONINV_P["total_phones_outstanding"]["figure"]["unit"] == rd.UNIT_COUNT
       and ONINV_P["zero_count"]["figure"]["unit"] == rd.UNIT_COUNT
       and ONINV_P["total_amount"]["figure"]["unit"] == rd.UNIT_MONEY
       and ONINV_P["warn_owed"]["figure"]["unit"] == rd.UNIT_MONEY),
      {k: v["figure"]["unit"] for k, v in ONINV_P.items()})
check("C4 a band a store has nothing in is a MEASURED $0.00 and is counted — the ledger was read "
      "for it and held no device in that band, which is not an absence",
      (_row(ONINV_P["warn_owed"], "S-2")["amount"] == 0.0
       and _row(ONINV_P["warn_owed"], "S-2")["measured"] is True
       and ONINV_P["warn_owed"]["tie_out"]["counted_rows"] == 2),
      ONINV_P["warn_owed"]["tie_out"])
check("C5 the money column is named, so a panel says which column held the figure and not only "
      "which table",
      all(o.get("column") == dd.COL_OWED
          for r in ONINV_P["total_amount"]["rows"] for o in r["origins"]),
      [o for r in ONINV_P["total_amount"]["rows"] for o in r["origins"]][:2])
check("C6 an empty report drills to nothing",
      dd.on_inventory_drilldowns({"stores": [], "totals": ONINV["totals"]}) == {}
      and dd.on_inventory_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D. WEEKLY OWED TO DISTRIBUTOR — three KPIs over the By-Store table, for ONE billing Friday.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. Weekly Owed to Distributor opens to the stores billing that Friday ==")

FRIDAY = "2026-10-30"
WEEK = {
    "thursday": FRIDAY,
    "due_this_week": {"sold": {"count": 5, "owed": 500.0},
                      "aging": {"count": 2, "owed": 180.0},
                      "total": {"count": 7, "owed": 680.0}},
    "by_store": [
        {"store": "S-1", "market": "M-1", "sold_count": 4, "sold_owed": 400.0,
         "aging_count": 2, "aging_owed": 180.0, "total_owed": 580.0},
        {"store": "S-2", "market": "M-2", "sold_count": 1, "sold_owed": 100.0,
         "aging_count": 0, "aging_owed": 0.0, "total_owed": 100.0},
    ],
}
WEEK_P = dd.owed_weekly_drilldowns(WEEK)

check("D1 the three money KPIs open and the device COUNTS do not — every panel row already carries "
      "both of them (batch-11's rep-count precedent)",
      sorted(WEEK_P) == ["aging_owed", "sold_owed", "total_owed"], sorted(WEEK_P))
check("D2 all three tie", all(_tie(p) is True for p in WEEK_P.values()),
      {k: v["tie_out"] for k, v in WEEK_P.items() if _tie(v) is not True})
check("D3 every row is addressed by STORE AND BILLING FRIDAY, so a panel can never be read as being "
      "about a different week",
      all(r["row_key"] == {"store": r["row_key"]["store"], "billing_friday": FRIDAY}
          for p in WEEK_P.values() for r in p["rows"]),
      [r["row_key"] for p in WEEK_P.values() for r in p["rows"]][:3])
check("D4 and the Friday is in the row's own label too",
      _row(WEEK_P["total_owed"], "S-1 · %s" % FRIDAY) is not None,
      [r["label"] for r in WEEK_P["total_owed"]["rows"]])
check("D5 the device counts travel on the row as context rather than as a second figure",
      _row(WEEK_P["total_owed"], "S-1 · %s" % FRIDAY)["fields"]["detail"] == "4 sold · 2 aged",
      _row(WEEK_P["total_owed"], "S-1 · %s" % FRIDAY)["fields"])
check("D6 a Friday nothing bills on drills to nothing, not to three panels asserting $0.00",
      dd.owed_weekly_drilldowns({"thursday": FRIDAY, "by_store": [],
                                 "due_this_week": WEEK["due_this_week"]}) == {}
      and dd.owed_weekly_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E. THE POS RECON — a difference that drills, and provenance read PER ROW.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the On-Inventory ↔ POS recon opens to its stores, each naming its own side(s) ==")


def _cats(pairs):
    return {k: {"asset": a, "b2b": b, "diff": a - b} for k, (a, b) in pairs.items()}


RECON_ROWS = [
    # Both sides present, and disagreeing on two device types.
    {"store": "S-1", "market": "M-1", "categories": _cats({"iphone": (10, 8), "android": (4, 5)}),
     "total_abs_diff": 3, "in_asset": True, "in_b2b": True, "asset_unclassified": 6},
    # The POS reported a store the ledger holds no unsold device for.
    {"store": "S-2", "market": None, "categories": _cats({"iphone": (0, 2), "android": (0, 0)}),
     "total_abs_diff": 2, "in_asset": False, "in_b2b": True, "asset_unclassified": 0},
    # The ledger holds devices the POS file never mentioned.
    {"store": "S-3", "market": "M-2", "categories": _cats({"iphone": (1, 0), "android": (0, 0)}),
     "total_abs_diff": 1, "in_asset": True, "in_b2b": False, "asset_unclassified": 0},
]
RECON = {"b2b_loaded": True, "as_of": "2026-10-20", "rows": RECON_ROWS,
         "mismatch_stores": 3, "total_abs_diff": 6}
RECON_P = dd.inventory_recon_drilldowns(RECON)

check("E1 the unit disagreement opens and the MISMATCHED-STORE COUNT does not — the table below "
      "highlights exactly those rows and sorts them to the top",
      sorted(RECON_P) == ["total_abs_diff"], sorted(RECON_P))
check("E2 and it TIES: the figure is Σ(the rows' own per-store disagreement), which is a real "
      "comparison because the two were folded in different places (§CORE-17.3k)",
      _tie(RECON_P["total_abs_diff"]) is True, RECON_P["total_abs_diff"]["tie_out"])
check("E3 it is a COUNT of units, never money",
      RECON_P["total_abs_diff"]["figure"]["unit"] == rd.UNIT_COUNT)
check("E4 a store BOTH sides reported names both the ledger and the POS snapshot",
      _tables(_row(RECON_P["total_abs_diff"], "S-1"))
      == sorted([lineage.ASSET_LEDGER, lineage.POS_INVENTORY_SNAPSHOT]),
      _tables(_row(RECON_P["total_abs_diff"], "S-1")))
check("E5 a store only the POS reported names ONLY the POS snapshot — naming the ledger for it "
      "would be a provenance claim about rows that do not exist",
      _tables(_row(RECON_P["total_abs_diff"], "S-2")) == [lineage.POS_INVENTORY_SNAPSHOT],
      _tables(_row(RECON_P["total_abs_diff"], "S-2")))
check("E6 and a store only the ledger holds names ONLY the ledger",
      _tables(_row(RECON_P["total_abs_diff"], "S-3")) == [lineage.ASSET_LEDGER],
      _tables(_row(RECON_P["total_abs_diff"], "S-3")))
check("E7 the panel's sources line therefore reports BOTH feeds with their true row counts, which "
      "is the owner's question answered in one read",
      RECON_P["total_abs_diff"]["sources"]
      == sorted([{"table": lineage.ASSET_LEDGER, "rows": 2},
                 {"table": lineage.POS_INVENTORY_SNAPSHOT, "rows": 2}],
                key=lambda s: (-s["rows"], s["table"])),
      RECON_P["total_abs_diff"]["sources"])
check("E8 LIVE DEFECT 2, VISIBLE ON THE ROW — the devices whose model classifies into none of the "
      "five types are counted into NOTHING and are stated per store, so the reader can see the "
      "hole in the ledger side instead of reading the gap as missing POS stock",
      (_row(RECON_P["total_abs_diff"], "S-1")["fields"]["unclassified_on_ledger"] == 6
       and _row(RECON_P["total_abs_diff"], "S-1")["amount"] == 3),
      _row(RECON_P["total_abs_diff"], "S-1")["fields"])
check("E9 NO PANEL AT ALL until a POS snapshot is loaded — with one side absent the difference is "
      "the ledger minus nothing, and a panel tying that out to itself would read like proof that "
      "the two inventories agree",
      dd.inventory_recon_drilldowns({**RECON, "b2b_loaded": False}) == {}
      and dd.inventory_recon_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §F. INTER-STORE BORROWED MONEY — three figures over the loans, two tables between them.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. Inter-store Borrowed Money opens to the loans ==")

LOANS = [
    {"id": "L-1", "borrower_store": "S-1", "lender_store": "S-2", "market": "M-1",
     "borrowed_date": "2026-09-01", "amount": 1000.0, "repaid": 400.0, "outstanding": 600.0,
     "settled": False, "payments": [{"id": "P-1", "amount": 400.0}]},
    {"id": "L-2", "borrower_store": "S-3", "lender_store": "S-1", "market": "M-2",
     "borrowed_date": "2026-09-15", "amount": 250.0, "repaid": 250.0, "outstanding": 0.0,
     "settled": True, "payments": [{"id": "P-2", "amount": 250.0}]},
    # A loan nobody has paid against: `repaid` is a real $0.00, read from a table that WAS looked up.
    {"id": "L-3", "borrower_store": "S-2", "lender_store": "S-3", "market": "M-1",
     "borrowed_date": "2026-10-01", "amount": 75.0, "repaid": 0.0, "outstanding": 75.0,
     "settled": False, "payments": []},
]
_tb = round(sum(r["amount"] for r in LOANS), 2)
_tr = round(sum(r["repaid"] for r in LOANS), 2)
BORROW = {"borrowings": LOANS, "count": len(LOANS), "total_borrowed": _tb, "total_repaid": _tr,
          "total_outstanding": round(_tb - _tr, 2)}
BORROW_P = dd.borrowings_drilldowns(BORROW)

check("F1 the three money figures open and the BORROWING COUNT does not — the ledger of every loan "
      "is printed below it",
      sorted(BORROW_P) == ["total_borrowed", "total_outstanding", "total_repaid"], sorted(BORROW_P))
check("F2 all three tie", all(_tie(p) is True for p in BORROW_P.values()),
      {k: v["tie_out"] for k, v in BORROW_P.items() if _tie(v) is not True})
check("F3 THE OUTSTANDING TIE-OUT IS A REAL COMPARISON. The endpoint subtracts at the TOTAL "
      "(Σamount − Σrepaid) while every row carries its OWN amount minus its OWN paybacks, so the "
      "two numbers come from different places and CAN disagree (§CORE-17.3k)",
      ("total_outstanding" in _func_src(ASSET_ROUTER, "list_borrowings")
       and "tot_borrowed - tot_repaid" in _func_src(ASSET_ROUTER, "list_borrowings")
       and BORROW_P["total_outstanding"]["tie_out"]["rows_total"]
       == BORROW_P["total_outstanding"]["figure"]["amount"]),
      BORROW_P["total_outstanding"]["tie_out"])
check("F4 the BORROWED figure names the loan table and the REPAID figure names the paybacks — a "
      "figure read out of the paybacks must not claim the loan recorded it",
      (_tables(_row(BORROW_P["total_borrowed"], dd._loan_label(LOANS[0]))) == [lineage.STORE_BORROWINGS]
       and _tables(_row(BORROW_P["total_repaid"], dd._loan_label(LOANS[0])))
       == [lineage.STORE_BORROWING_PAYMENTS]),
      (_tables(_row(BORROW_P["total_borrowed"], dd._loan_label(LOANS[0]))),
       _tables(_row(BORROW_P["total_repaid"], dd._loan_label(LOANS[0])))))
check("F5 and the OUTSTANDING figure names both, because it was read out of both",
      _tables(_row(BORROW_P["total_outstanding"], dd._loan_label(LOANS[0])))
      == sorted([lineage.STORE_BORROWINGS, lineage.STORE_BORROWING_PAYMENTS]),
      _tables(_row(BORROW_P["total_outstanding"], dd._loan_label(LOANS[0]))))
check("F6 a loan nobody has paid against reads a MEASURED $0.00 repaid and is counted — the "
      "paybacks table really was read for it, and found none",
      (_row(BORROW_P["total_repaid"], dd._loan_label(LOANS[2]))["amount"] == 0.0
       and _row(BORROW_P["total_repaid"], dd._loan_label(LOANS[2]))["measured"] is True),
      _row(BORROW_P["total_repaid"], dd._loan_label(LOANS[2])))
check("F7 a row's label names the borrower, the lender and the date — who owes whom, without "
      "leaving the panel",
      dd._loan_label(LOANS[0]) == "S-1 ← S-2 · 2026-09-01", dd._loan_label(LOANS[0]))
check("F8 no borrowings at all drills to nothing",
      dd.borrowings_drilldowns({"borrowings": [], "total_borrowed": 0.0}) == {}
      and dd.borrowings_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §G. ASSET LENDING (DISTRIBUTOR PAYGO) — the lifetime figures over the weekly batches.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. Asset Lending opens to the weekly batches ==")

BATCHES = [
    {"vip_payment_id": 101, "batch_type": "approved", "created_on": "2026-10-03", "period": "2026-10",
     "invoice_count": 12, "amount": 1200.0, "amount_overdue": 0.0, "status": "Paid"},
    {"vip_payment_id": 102, "batch_type": "approved", "created_on": "2026-10-10", "period": "2026-10",
     "invoice_count": 9, "amount": 900.0, "amount_overdue": 50.0, "status": "Paid"},
    # The portal reported a batch with NO amount (live defect 4).
    {"vip_payment_id": 103, "batch_type": "approved", "created_on": "2026-10-17", "period": "2026-10",
     "invoice_count": 4, "amount": None, "amount_overdue": None, "status": "Failed"},
]
PENDING = {"vip_payment_id": 104, "batch_type": "pending", "created_on": "2026-10-24",
           "period": "2026-10", "invoice_count": 5, "amount": 500.0, "amount_overdue": 0.0,
           "status": None}
PAYGO = {"configured": True, "current": PENDING, "history": BATCHES,
         "totals": {"current_owed": 500.0, "current_overdue": 0.0, "weeks": len(BATCHES),
                    "lifetime_paid": round(sum(float(r.get("amount") or 0) for r in BATCHES), 2)}}
PAYGO["totals"]["devices_lifetime"] = dd.devices_lifetime(PAYGO)
PAYGO_P = dd.lending_drilldowns(PAYGO)

check("G1 the two LIFETIME figures open; the open week's two figures and the grand total do not — "
      "each of the first two is one column of ONE stored batch row with nothing under it on this "
      "report (batch 10's leaf ruling), and the grand total is composed from two other tiles",
      sorted(PAYGO_P) == ["devices_lifetime", "lifetime_paid"], sorted(PAYGO_P))
check("G2 a batch the portal reported WITHOUT an amount is shown, carries NO figure, is not "
      "counted, and says why — a missing amount is not a week that billed nothing",
      (_row(PAYGO_P["lifetime_paid"], "week of 2026-10-17 · 2026-10")["amount"] is None
       and _row(PAYGO_P["lifetime_paid"], "week of 2026-10-17 · 2026-10")["measured"] is False
       and _row(PAYGO_P["lifetime_paid"], "week of 2026-10-17 · 2026-10")["reason"]
       == "amount_not_recorded"
       and _row(PAYGO_P["lifetime_paid"], "week of 2026-10-17 · 2026-10")["reason_label"]),
      _row(PAYGO_P["lifetime_paid"], "week of 2026-10-17 · 2026-10"))
check("G3 …and the money panel still TIES, because the unmeasured row is excluded from the figure "
      "rather than counted as zero",
      _tie(PAYGO_P["lifetime_paid"]) is True
      and PAYGO_P["lifetime_paid"]["tie_out"]["counted_rows"] == 2,
      PAYGO_P["lifetime_paid"]["tie_out"])
check("G4 the DEVICE count panel counts all three weeks — the device count was reported even where "
      "the amount was not, and the two questions have different answers",
      (_tie(PAYGO_P["devices_lifetime"]) is True
       and PAYGO_P["devices_lifetime"]["tie_out"]["counted_rows"] == 3
       and PAYGO_P["devices_lifetime"]["figure"]["amount"] == 25),
      PAYGO_P["devices_lifetime"]["tie_out"])
check("G5 the PENDING batch is in no panel — it is not part of either lifetime figure",
      all(_row(p, "week of 2026-10-24 · 2026-10") is None for p in PAYGO_P.values()))
check("G6 every row names the distributor's lending batches and is addressed by its batch id",
      all(_tables(r) == [lineage.PAYGO_BATCHES] and set(r["row_key"]) == {"vip_payment_id"}
          for p in PAYGO_P.values() for r in p["rows"]),
      [(r["label"], _tables(r), r["row_key"]) for p in PAYGO_P.values() for r in p["rows"]][:2])
check("G7 an unconfigured portal drills to nothing",
      dd.lending_drilldowns({"configured": False, "history": [], "totals": {}}) == {}
      and dd.lending_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Y. LOCKED SO IT CANNOT UN-WIRE.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Y. build locks ==")

DECL_SRC = open(DECL, encoding="utf-8").read()
DECL_CODE = _code_only(DECL)
AR_SRC = open(ASSET_ROUTER, encoding="utf-8").read()
CC_SRC = open(CC_ROUTER, encoding="utf-8").read()

check("Y1 BUILD LOCK — the declaration DEREFERENCES the one home and defines no panel shape of its "
      "own: no second answer to what a drill-down looks like",
      ("report_drilldown" in DECL_CODE and "def panel(" not in DECL_CODE
       and "def tie_out(" not in DECL_CODE and "def drill_row(" not in DECL_CODE))
check("Y2 BUILD LOCK — it spells NO table name as a literal: every one is a registry constant",
      not re.search(r'"(asset_ledger|b2b_inventory|store_borrowings|store_borrowing_payments|'
                    r'vip_paygo_payments)"', DECL_SRC),
      re.findall(r'"(asset_ledger|b2b_inventory|store_borrowings|vip_paygo_payments)"', DECL_SRC))
check("Y2b …and the five constants it reads really are the registry's own values",
      (dd.ASSET_LEDGER == lineage.ASSET_LEDGER
       and dd.POS_INVENTORY == lineage.POS_INVENTORY_SNAPSHOT
       and dd.STORE_BORROWINGS == lineage.STORE_BORROWINGS
       and dd.STORE_BORROWING_PAYMENTS == lineage.STORE_BORROWING_PAYMENTS
       and dd.PAYGO_BATCHES == lineage.PAYGO_BATCHES))
MIG = ""
for _f in sorted(os.listdir(os.path.join(ROOT, "database", "migrations"))):
    if _f.endswith(".sql"):
        MIG += open(os.path.join(ROOT, "database", "migrations", _f), encoding="utf-8",
                    errors="ignore").read()
DRILL_TABLES = [dd.ASSET_LEDGER, dd.POS_INVENTORY, dd.STORE_BORROWINGS,
                dd.STORE_BORROWING_PAYMENTS, dd.PAYGO_BATCHES]
check("Y3 every table these panels can name is CREATED BY A MIGRATION — a provenance claim about a "
      "relation that does not exist is worse than none",
      all(re.search(r"\b%s\b" % t, MIG) for t in DRILL_TABLES),
      [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % t, MIG)])
LABELS = _read("frontend/src/lib/sourceLabels.ts")
check("Y4 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a plain "
      "name and never a database identifier (§19.38)",
      all(re.search(r"^\s+%s:" % t, LABELS, re.M) for t in DRILL_TABLES),
      [t for t in DRILL_TABLES if not re.search(r"^\s+%s:" % t, LABELS, re.M)])
_CAT_KEYS = set()
for _cat in (dd.ledger_catalogue(), dd.on_inventory_catalogue(), dd.owed_weekly_catalogue(),
             dd.inventory_recon_catalogue(), dd.borrowings_catalogue(), dd.lending_catalogue()):
    _CAT_KEYS |= {c["key"] for c in _cat}
check("Y5 BUILD LOCK — NO catalogue declares a composed total, a leaf or a count whose list is on "
      "screen. Those get no key at all, not an empty panel",
      not (_CAT_KEYS & {"total_loss", "total", "grand_total", "current_owed", "current_overdue",
                        "store_count", "count", "mismatch_stores", "total_rows", "weeks",
                        "device_count"}),
      sorted(_CAT_KEYS & {"total_loss", "grand_total", "current_owed", "store_count", "count",
                          "mismatch_stores", "total_rows", "weeks", "device_count"}))
check("Y5b …and the refusal is not vacuous: those figures really are in the producers' payloads, so "
      "a careless successor COULD have declared them",
      ("current_owed" in PAYGO["totals"] and "store_count" in ONINV["totals"]
       and "count" in BORROW and "mismatch_stores" in RECON and "total" in TOTAL_LOSS))
check("Y6 BUILD LOCK — every catalogue key the declaration emits is a key some producer's payload "
      "really holds, so a panel can never assert a figure nothing produced",
      ({c["total"] for c in dd.on_inventory_catalogue()} <= set(ONINV["totals"])
       and {c["total"] for c in dd.owed_weekly_catalogue()} <= set(dd.owed_weekly_totals(WEEK))
       and {c["total"] for c in dd.borrowings_catalogue()} <= set(BORROW)
       and {c["total"] for c in dd.lending_catalogue()} <= set(PAYGO["totals"])
       and {c["total"] for c in dd.inventory_recon_catalogue()} <= set(RECON)),
      [sorted({c["total"] for c in dd.lending_catalogue()} - set(PAYGO["totals"]))])
_GRAIN_KEYS = {
    "ledger(status)": ("status",), "ledger(category)": ("category",),
    "charges": ("store", "category"), "on-inventory": ("store",),
    "owed-weekly": ("store", "billing_friday"), "recon": ("store",),
    "borrowed": ("id",), "lending": ("vip_payment_id",),
}
check("Y6b THE GRAIN COUNT IS MEASURED. Eight catalogue row shapes across six grains, and each "
      "panel's keys really address its OWN grain — four of them keyed by a store and still four "
      "different populations, which is why one catalogue could not have served them",
      ({tuple(sorted(r["row_key"])) for r in LEDGER_P["total_fees"]["rows"]} == {("status",)}
       and {tuple(sorted(r["row_key"])) for r in LEDGER_P["on_inventory"]["rows"]} == {("category",)}
       and {tuple(sorted(r["row_key"])) for r in CHARGE_P["group_appeals"]["rows"]} == {("category", "store")}
       and {tuple(sorted(r["row_key"])) for r in ONINV_P["total_amount"]["rows"]} == {("store",)}
       and {tuple(sorted(r["row_key"])) for r in WEEK_P["total_owed"]["rows"]} == {("billing_friday", "store")}
       and {tuple(sorted(r["row_key"])) for r in RECON_P["total_abs_diff"]["rows"]} == {("store",)}
       and {tuple(sorted(r["row_key"])) for r in BORROW_P["total_borrowed"]["rows"]} == {("id",)}
       and {tuple(sorted(r["row_key"])) for r in PAYGO_P["lifetime_paid"]["rows"]} == {("vip_payment_id",)}),
      sorted(_GRAIN_KEYS))
check("Y7 BUILD LOCK — the charges endpoint passes the router's OWN classification in rather than "
      "letting the declaration keep a second copy of which categories make a charge group",
      ("group_of=_cat_to_group" in AR_SRC
       and "CHARGE_GROUPS" not in DECL_CODE
       and "for g, cats in CHARGE_GROUPS.items()" in _func_src(ASSET_ROUTER, "_cat_to_group")),
      _func_src(ASSET_ROUTER, "_cat_to_group"))
_RMA_FN = _func_src(ASSET_ROUTER, "get_charges_summary")
check("Y8 BUILD LOCK — the charges endpoint DEREFERENCES the per-row RMA rule and no longer spells "
      "it: one fact, one home, and the copy deleted in the same commit",
      ("_dd.rma_net_loss(" in _RMA_FN and "_dd.is_rma_row(" in _RMA_FN
       and "rma_loss += " not in _RMA_FN and 'reimb < owed - 0.01' not in _RMA_FN),
      _RMA_FN[-900:])
check("Y8b BUILD LOCK — and the summary endpoint dereferences the On-Inventory predicate and the "
      "open-status value, instead of re-typing either",
      ("_dd.counts_as_on_inventory(" in _func_src(ASSET_ROUTER, "get_asset_summary")
       and "_dd.OPEN_STATUS" in _func_src(ASSET_ROUTER, "get_asset_summary")
       and '"On Inventory" in (r.get("category")' not in AR_SRC),
      _func_src(ASSET_ROUTER, "get_asset_summary")[:300])
_ENDPOINT_FNS = {
    "get_asset_summary": "ledger_drilldowns",
    "get_charges_summary": "charges_drilldowns",
    "get_on_inventory_by_store": "on_inventory_drilldowns",
    "get_owed_weekly": "owed_weekly_drilldowns",
    "inventory_recon": "inventory_recon_drilldowns",
    "list_borrowings": "borrowings_drilldowns",
}
for _fn, _producer in _ENDPOINT_FNS.items():
    _src = _func_src(ASSET_ROUTER, _fn)
    check("Y9(%s) the endpoint ATTACHES the declaration's output and folds no panel of its own" % _fn,
          ("_dd.%s(" % _producer) in _src and '"drilldown"' in _src
          and "rd.panel(" not in _src and '"tie_out"' not in _src
          and '"rows_total_count"' not in _src,
          _src[-400:])
_PAYGO_FN = _func_src(CC_ROUTER, "vip_paygo_summary")
check("Y9b the PayGo endpoint does the same, and serves the lifetime device count the page used to "
      "reduce for itself (§CORE-17.3d)",
      ("_asset_dd.lending_drilldowns(" in _PAYGO_FN
       and "_asset_dd.devices_lifetime(" in _PAYGO_FN
       and '"tie_out"' not in _PAYGO_FN),
      _PAYGO_FN[-400:])
for _route, _page in PAGES.items():
    _src = _read(_page)
    check("Y10(%s) the page renders the SHARED component and hand-rolls no panel" % _route,
          ("DrillDownPanel" in _src and "useDrillTile(" in _src
           and "tie_out" not in _src and "rows_total_count" not in _src),
          _route)
    check("Y11(%s) NO CLIENT NARROWING — every filter that moves a figure on this screen is a "
          "QUERY PARAMETER, so the server's own fold already covers what is on screen. This FAILS "
          "THE BUILD if `drill-narrow` is wired in, so a later reader cannot 'fix' these into a "
          "class they were never in (the §CORE-17.3e posture)" % _route,
          "@/lib/drill-narrow" not in _src and "narrowPanels(" not in _src, _route)
check("Y11b …and that lock is NOT vacuous: a page that really is in the client-narrowing class "
      "does import it, so the scan would catch one of these joining it",
      "@/lib/drill-narrow" in _read("frontend/src/app/(platform)/closing/recon/page.tsx"))
check("Y12 the lending page no longer sums its own lifetime device count — two folds of one figure "
      "are two numbers hoping to agree",
      "reduce(" not in _read(PAGES["/commcalc/asset/lending"]),
      [l for l in _read(PAGES["/commcalc/asset/lending"]).splitlines() if "reduce(" in l])
check("Y13 RULE TWO — no carrier, tenant, product, vendor or store name in the declaration",
      not re.search(r"(?i)\b(boost|total wireless|vidapay|luxelink|cellfonz|verizon|t-?mobile|"
                    r"at&t|tracfone|rq|b2bsoft|b2b ?soft)\b", DECL_SRC))
# A ZERO IS NOT A FIGURE; A NON-ZERO LITERAL WOULD BE — the same exception
# `harness_report_drilldown` §G4 makes for the one home, applied here to a declaration.
_FLOATS = [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", DECL_CODE)
           if float(m) != 0.0]
check("Y14 BUILD LOCK — the declaration carries exactly ONE non-zero numeric literal: the "
      "reimbursement tolerance it is now the one home for. Every other figure was handed to it, so "
      "it can never become a second opinion about money",
      _FLOATS == [str(dd.RMA_SHORTFALL_TOLERANCE)], _FLOATS)
check("Y14b NEGATIVE CONTROL — the scan really does catch a non-zero dollar literal, so Y14 is not "
      "a check that cannot fire",
      [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", "x = 12.50 ; y = 0.0")
       if float(m) != 0.0] == ["12.50"])
check("Y15 BUILD LOCK — the declaration is PURE: no DB, no framework, no clock",
      not re.search(r"^(import|from)\s+(fastapi|supabase|datetime|time|requests|httpx|pandas)",
                    DECL_SRC, re.M))
check("Y16 every reason key the declaration can emit has a sentence in the ONE home's vocabulary, "
      "so a reader is never shown a bare field name",
      all(k in rd.UNMEASURED_REASONS for k in re.findall(r'reason["\']?\s*[:=]\s*["\'](\w+)["\']',
                                                         DECL_SRC) + ["amount_not_recorded"]),
      re.findall(r'["\'](\w*not_recorded\w*)["\']', DECL_SRC))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z. NEGATIVE CONTROLS — a tie-out that cannot fail is not a proof.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. negative controls — each one BREAKS a tie-out, or pins a reported defect ==")

# Z1 — A ONE-CENT TOTAL. The report's figure moved by a cent and the rows did not.
_z1 = rd.tile_panel("Total amount owed (unsold exposure)", 1050.01, ONINV_STORES,
                    value="owed", row_label="store", keys=("store",),
                    tables=(dd.ASSET_LEDGER,), schema=dd.SCHEMA)
check("Z1 a ONE-CENT total breaks the on-inventory tie-out, in words",
      _tie(_z1) is False and "disagree" in _z1["tie_out"]["words"], _z1["tie_out"])

# Z2 — A DROPPED ROW. One store vanishes from the evidence.
_z2 = rd.tile_panel("Total due this Friday", 680.0, dd.owed_weekly_rows(WEEK)[:1],
                    value="total_owed", row_label="store", keys=("store",),
                    tables=(dd.ASSET_LEDGER,), schema=dd.SCHEMA)
check("Z2 a DROPPED store breaks it too — the rows add up to less than the figure",
      _tie(_z2) is False and _z2["tie_out"]["difference"] == -100.0, _z2["tie_out"])

# Z3 — A FIGURE FOLDED IN AT $0.00 THAT NOBODY RECORDED. The careless lending declaration coerces
# the null amount; the honest one shows it. BOTH tie — which is exactly why the difference has to be
# visible on the ROW rather than in the total.
_z3 = rd.tile_panel("Lifetime billed", PAYGO["totals"]["lifetime_paid"], BATCHES,
                    value=lambda r: rd.money(r.get("amount")), row_label=dd._batch_label,
                    keys=("vip_payment_id",), tables=(dd.PAYGO_BATCHES,), schema=dd.SCHEMA)
check("Z3 the CARELESS declaration (a null amount coerced to $0.00) counts a week the portal never "
      "priced, and the honest one shows it instead — the figure is identical, the row is not, and "
      "the row is the whole point",
      (_tie(_z3) is True and _z3["tie_out"]["counted_rows"] == 3
       and _row(_z3, "week of 2026-10-17 · 2026-10")["amount"] == 0.0
       and _z3["figure"]["amount"] == PAYGO_P["lifetime_paid"]["figure"]["amount"]
       and PAYGO_P["lifetime_paid"]["tie_out"]["counted_rows"] == 2),
      (_z3["tie_out"], PAYGO_P["lifetime_paid"]["tie_out"]))

# Z4 — A TAMPERED BREAKDOWN CELL. One store's band figure is edited after the fold.
_z4rows = [dict(r) for r in ONINV_STORES]
_z4rows[0]["warn_owed"] = (_z4rows[0]["warn_owed"] or 0) + 1.0
_z4 = rd.tile_panel("45–60 day band — owed", ONINV["totals"]["warn_owed"], _z4rows,
                    value="warn_owed", row_label="store", keys=("store",),
                    tables=(dd.ASSET_LEDGER,), schema=dd.SCHEMA)
check("Z4 a TAMPERED band cell breaks the band tie-out",
      _tie(_z4) is False and _z4["tie_out"]["difference"] == 1.0, _z4["tie_out"])

# Z5 — A COUNTED ROW WITH NO NUMBER. `ties` must be None, never True and never False.
_z5 = rd.tile_panel("Lifetime billed", PAYGO["totals"]["lifetime_paid"], BATCHES,
                    value="amount", row_label=dd._batch_label, keys=("vip_payment_id",),
                    tables=(dd.PAYGO_BATCHES,), schema=dd.SCHEMA, in_figure=lambda _r: True)
check("Z5 a declaration that COUNTS the unpriced week cannot claim a tie at all — `ties` is None, "
      "never False, because a sum missing a row was never a comparison",
      _tie(_z5) is None and "cannot be added up" in _z5["tie_out"]["words"], _z5["tie_out"])

# Z6 — THE RMA NET LOSS DID NOT MOVE. The endpoint's old inline loop against the one home.
def _old_rma_loss(agg):
    """`GET /asset/charges-summary`'s RMA fold as it stood BEFORE this batch, verbatim."""
    rma_loss = 0.0
    for row in agg:
        if (row.get("category") or "") == "RMA":
            owed = float(row.get("owed") or 0)
            reimb = float(row.get("reimb") or 0)
            if reimb <= 0:
                rma_loss += owed
            elif reimb < owed - 0.01:
                rma_loss += (owed - reimb)
    return round(rma_loss, 2)


_new_rma = round(sum(dd.rma_net_loss(r) for r in AGG if dd.is_rma_row(r)), 2)
check("Z6 NO FIGURE MOVED — the RMA net loss the endpoint used to fold inline and the one home's "
      "per-row rule agree to the cent, over all three shapes the rule has",
      _old_rma_loss(AGG) == _new_rma == TOTAL_LOSS["rma"] == 120.0,
      (_old_rma_loss(AGG), _new_rma, TOTAL_LOSS["rma"]))
check("Z6b and the comparison DISCRIMINATES — the gross owed on the same rows is a different "
      "number, so Z6 is a check and not a tautology",
      round(sum(float(r["owed"]) for r in AGG if dd.is_rma_row(r)), 2) == 165.0
      and 165.0 != _new_rma,
      round(sum(float(r["owed"]) for r in AGG if dd.is_rma_row(r)), 2))
check("Z6c and the rule's three branches really are three different answers — a row fully "
      "reimbursed contributes nothing, a short one contributes the shortfall, and one with nothing "
      "back contributes the lot",
      (dd.rma_net_loss({"owed": 90.0, "reimb": 0.0}) == 90.0
       and dd.rma_net_loss({"owed": 50.0, "reimb": 20.0}) == 30.0
       and dd.rma_net_loss({"owed": 25.0, "reimb": 25.0}) == 0.0
       and dd.rma_net_loss({}) == 0.0),
      [dd.rma_net_loss({"owed": 90.0, "reimb": 0.0}), dd.rma_net_loss({"owed": 50.0, "reimb": 20.0})])

# Z7 — THE ON-INVENTORY CARD DID NOT MOVE. The predicate's old spelling against its one home.
_old_oninv = round(sum(float(r.get("owed_to_vip") or 0) for r in LEDGER_DEVICES
                       if "On Inventory" in (r.get("category") or "")), 2)
check("Z7 NO FIGURE MOVED — the On-Inventory card's old inline predicate and "
      "`counts_as_on_inventory` select exactly the same categories",
      _old_oninv == SUMMARY["on_inventory"] == 160.0, (_old_oninv, SUMMARY["on_inventory"]))
check("Z7b and the comparison CAN fail — a case-insensitive predicate would select MORE rows here, "
      "which is why unifying it with the two `ilike` reads was refused as a figure change "
      "(live defect 3)",
      (round(sum(float(r.get("owed_to_vip") or 0) for r in
                 LEDGER_DEVICES + [{"category": "on inventory", "owed_to_vip": 7.0}]
                 if "on inventory" in (r.get("category") or "").lower()), 2) != _old_oninv))

# Z8 — THE LIFETIME DEVICE COUNT DID NOT MOVE. The page's old reduce against the served fold.
_js_like = sum((b.get("invoice_count") or 0) for b in BATCHES)
check("Z8 NO FIGURE MOVED — the lending page's own reduce and the served `devices_lifetime` agree "
      "exactly, which is what makes serving it a presentation fix and not a number changing",
      _js_like == dd.devices_lifetime(PAYGO) == 25, (_js_like, dd.devices_lifetime(PAYGO)))
check("Z8b and that comparison can fail too",
      dd.devices_lifetime({"history": BATCHES[:2]}) != _js_like)

# Z9 — A STORE THE POS NEVER REPORTED IS NOT GIVEN A GUESSED PROVENANCE.
check("Z9 a careless recon declaration naming BOTH tables on every row would claim the POS file "
      "mentioned a store it never did — the real one reads the producer's own verdict per row",
      (dd.recon_tables(RECON_ROWS[2]) == (dd.ASSET_LEDGER,)
       and dd.recon_tables({"in_asset": False, "in_b2b": False}) == ()),
      dd.recon_tables(RECON_ROWS[2]))
_z9 = rd.tile_panel("Total unit disagreement", RECON["total_abs_diff"], RECON_ROWS,
                    value="total_abs_diff", row_label="store", keys=("store",),
                    tables=lambda _r: (), schema=dd.SCHEMA, unit=rd.UNIT_COUNT)
check("Z9b a row whose tables did not resolve REPORTS an untraceable origin rather than a silent "
      "blank — three rows, three stated gaps",
      _z9["untraceable_origins"] == 3 and _z9["sources"] == [], _z9)

# Z10 — LIVE DEFECT 1, PINNED. The RMA tile can only ever read "0 items".
_DASH = _read(PAGES["/commcalc/asset/dashboard"])
check("Z10 LIVE DEFECT 1, PINNED — the Charges Dashboard's RMA tile reads its count from a group "
      "that does not exist, so it is structurally `0 items` while the dollars beside it are real. "
      "NOT fixed here: putting the true count on the tile changes a figure the owner reads. If "
      "someone fixes it this check goes red and the panel's note must follow",
      ("rma" not in CHARGE_GROUPS and "setRmaData({ count: 0" in _DASH
       and CHARGE_P["loss_rma"]["rows_total_count"] == 3),
      (sorted(CHARGE_GROUPS), CHARGE_P["loss_rma"]["rows_total_count"]))

# Z11 — LIVE DEFECT 2, PINNED AT THE SOURCE. The recon still drops an unclassifiable device.
_OB = _func_src(ASSET_ROUTER, "_asset_oninv_by_bucket")
check("Z11 LIVE DEFECT 2, PINNED — the recon's asset side still SKIPS a device whose model "
      "classifies into none of the five types, and the count is kept OUTSIDE `by_store` so which "
      "stores appear is unchanged. Reported, not fixed: counting them would move the comparison",
      ("if not b:" in _OB and "unclassified[s] = unclassified.get(s, 0) + 1" in _OB
       and "continue" in _OB and "return by_store, unclassified" in _OB),
      _OB)
check("Z11b and the dropped count reaches the reader WITHOUT being counted into the figure",
      (_row(RECON_P["total_abs_diff"], "S-1")["fields"]["unclassified_on_ledger"] == 6
       and RECON_P["total_abs_diff"]["figure"]["amount"] == 6
       and RECON_P["total_abs_diff"]["tie_out"]["rows_total"] == 6),
      RECON_P["total_abs_diff"]["tie_out"])

#: The tables the feed watchdog really watches, by name.
_WATCHED = {w["table"] for w in lineage.watched_feeds()}

# Z12 — LIVE DEFECT 5 (REPORTED, NOT FIXED): the POS unit snapshot is not a registered feed, while
# the $ side of the SAME upload is.
check("Z12 LIVE DEFECT, PINNED — the POS UNIT snapshot is NOT a registered ingest feed, while the "
      "$ side of the same uploaded file (`inventory_value`) is. So the watchdog cannot tell a stale "
      "unit snapshot from a missing one. Reported, not fixed: registering a feed needs a lineage "
      "seed migration, which this read-only batch does not carry",
      (lineage.POS_INVENTORY_SNAPSHOT not in _WATCHED and "inventory_value" in _WATCHED),
      sorted(_WATCHED)[:6])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §I. REGISTRATION — the index rules are a build gate, not advice (CLAUDE.md).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. registration ==")

_IDX = _read("docs/SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("device_asset_drilldown.py", "harness_batch12_drilldown.py"):
    check("I1(%s) registered in the index by its path" % _f, _f in _IDX)
check("I2 and the section this batch adds is there, so the next reader does not re-derive how a "
      "report family's GRAIN COUNT is measured", "CORE-17.3l" in _IDX)
_MG = _read("backend/app/modules/core/module_graph.py")
check("I3 the declaration is a caller of the one home in the module graph, and the harness is one "
      "of its locks",
      "asset/device_asset_drilldown.py" in _MG and "harness_batch12_drilldown.py" in _MG)
_WF = _read(".github/workflows/carrier-vocab-guard.yml")
check("I4 the harness is locked into CI, with its own paths-filter block",
      "harness_batch12_drilldown.py" in _WF and _WF.count("harness_batch12_drilldown.py") >= 2)
_LEDGER = _read("backend/harness_drilldown_pending.txt")
for _r in PAGES:
    _ln = next((l for l in _LEDGER.splitlines() if l.split("\t")[0] == _r), "")
    check("I5(%s) the ledger reads `shared` and says what it REFUSES to claim — an excuse nobody "
          "wrote down is indistinguishable from an oversight" % _r,
          _ln.split("\t")[1:2] == ["shared"] and "REFUSES to claim" in _ln, _ln[:160])


def _owes_max(src):
    """THE RATCHET'S PIN, or None when it cannot be read at all.

    **SHRINK-ONLY, AND IT MUST NOT BE RE-TIGHTENED TO `==`.** Batch 10 pinned `OWES_MAX == 74` and
    went red the moment batch 11 correctly lowered the ratchet; batch 11 rewrote both as `<=`. The
    rule the ratchet states is *"may only shrink"*, and `<=` IS that rule — a value assertion makes
    the lock expire on the next correct change, and the next reader's reflex when it goes red is to
    edit the number, which is how a ratchet quietly becomes decoration.

    It still FAILS CLOSED: a pin above this batch's 64 is red, and a pin that cannot be read at all
    is red rather than absent."""
    m = re.search(r"^OWES_MAX = (\d+)", src or "", re.M)
    return int(m.group(1)) if m else None


_pin = _owes_max(_read("backend/harness_drilldown_coverage.py"))
check("I6 the ratchet was re-pinned DOWNWARD in the same PR and has never been pushed back up past "
      "this batch's 64", _pin is not None and _pin <= 64, _pin)
check("I6b NEGATIVE CONTROL — a pin ABOVE 64 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 65\n") > 64)
check("I6c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file, which "
      "is the whole point of asserting the direction instead of the value",
      _owes_max("OWES_MAX = 64\n") <= 64 and _owes_max("OWES_MAX = 60\n") <= 64)
check("I6d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = many\n") is None)


print("\n" + "=" * 96)
print("PASS %d   FAIL %d" % (len(P), len(F)))
if F:
    print("\nFAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
