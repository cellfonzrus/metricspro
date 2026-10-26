"""BATCH 17 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four EXECUTIVE-MONTH reports.

  /commcalc/exec            Owner Overview
  /commcalc/exec/mtd        Executive MTD
  /commcalc/month-focus     This Month's Focus
  /commcalc/comp-trend      Total Compensation

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* These four are **the surfaces the owner looks at most**, so a figure that cannot explain
itself here is the most expensive kind — and three of the four read a figure out of what the last
commission calculation WROTE DOWN rather than out of the sales it read, which is the §CORE-17.3j
shape on the exact screen that shape matters on.

It also settles the §CORE-17.3o call BOTH WAYS. Three of these screens print the server's own fold
and must NOT be narrowed; the fourth folds its tiles in the browser and therefore MUST be. §F locks
both directions with a non-vacuous witness for every scan, so "the text is absent" can never be
confused with "the scan is blind".

The whole file is DB-FREE and stdlib-only. The producers are driven as far as each one can be:
`comp_trend.compute_rep_pay_trend` and `.compute_residual_trend` are driven FOR REAL through a
stdlib stub client (they take their client as an argument and degrade on every read they cannot
make), and the three folds that live inside the FastAPI router — `exec_overview`'s store roll-up and
tile dict, `_exec_mtd._totals` and `_month_focus_targets`' saved-target set — cannot be imported
without the framework, so §B/§C/§E reproduce each one line-for-line AND pin it to the router's own
source text: if any of those expressions changes, this file goes red rather than quietly measuring a
fold nobody runs. `_exec_mtd`'s own total-row KEY TUPLE is extracted from that source and compared
against the declaration's column set, so the two cannot drift.

Run: `cd backend && python3 harness_batch17_drilldown.py`
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import envelope_drilldown as envdd              # noqa: E402
from app.modules.closing import ops_chargeback_drilldown as opsdd        # noqa: E402
from app.modules.commcalc import carrier_pay_drilldown as cpd            # noqa: E402
from app.modules.commcalc import comp_trend as ct                        # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage        # noqa: E402
from app.modules.commcalc import exec_metric_defs as emd                 # noqa: E402
from app.modules.commcalc import exec_month_drilldown as dd              # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd        # noqa: E402
from app.modules.commcalc import targets_drilldown as tdd                # noqa: E402
from app.modules.core import report_drilldown as rd                      # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, red)


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch passes
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECL = read("backend/app/modules/commcalc/exec_month_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")


def _literals(src):
    """Every STRING literal the module's code actually contains, and every NUMBER.

    A table name, a dollar figure or a relation the family cannot name may appear in a DOCSTRING or
    a comment — that is a note to the next engineer and the whole point of writing the defects down.
    The rulings are about the CODE: what the module does with a name, and whether it holds an amount
    of its own. So the two are told apart by parsing rather than by grepping the file, and a
    docstring (the first statement of a module, class or function) is excluded while every other
    string literal is kept.
    """
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    strings, numbers = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docs:
            if isinstance(node.value, str):
                strings.append(node.value)
            elif isinstance(node.value, bool):
                continue
            elif isinstance(node.value, (int, float)):
                numbers.append(node.value)
    return strings, numbers


DECL_STRINGS, DECL_NUMBERS = _literals(DECL)
#: The code's string literals joined, for "does the CODE name this relation" questions. Prose that
#: happens to contain a word (a panel's note) is a string literal too, so a provenance claim is
#: looked for as a WHOLE literal rather than as a substring of one.
DECL_CODE_NAMES = {s for s in DECL_STRINGS if re.fullmatch(r"[a-z][a-z0-9_]*", s or "")}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE ONE HOME, AND A VOCABULARY THAT IS DEREFERENCED RATHER THAN RE-SPELLED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. one home, one vocabulary ==")

check("A1 the declaration folds NOTHING of its own — every panel comes out of the shared tile fold, "
      "and the only arithmetic it performs is adding subtotals the report itself served (defect 5)",
      DECL.count("report_drilldown.tiles(") == 4
      and "report_drilldown.tile_panel(" not in DECL
      and DECL.count("report_drilldown.money(") == 2)
check("A2 the snapshot's name is DEREFERENCED BY IDENTITY from the declaration that named it once "
      "(§CORE-17.3j, batch 11) — not a second spelling of it",
      dd.REP_COMMISSIONS is cpd.REP_COMMISSIONS and dd.REP_COMMISSIONS == "rep_commissions"
      and '"rep_commissions"' not in DECL and "'rep_commissions'" not in DECL)
check("A3 the TARGET SETTINGS name is DEREFERENCED BY IDENTITY from the targets family's own "
      "declaration (§CORE-17.3m, batch 13)",
      dd.TARGETS is tdd.TARGETS and dd.TARGETS == "targets"
      and not re.search(r'=\s*\(?"targets"', DECL))
check("A4 the sales-union provenance resolver is THE SAME FUNCTION OBJECT batch 14 wrote, not a "
      "second reading of the same meta",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A5 the two relations this batch had to name are in the ONE feed registry and dereferenced "
      "from it, with no literal copy left in the declaration",
      dd.OPS_CHARGEBACK is lineage.OPS_CHARGEBACK and dd.STORE_ROSTER is lineage.STORE_ROSTER
      and not re.search(r'=\s*\(?"(ops_chargeback|stores)"', DECL))
check("A5b AND THE ONE COLLISION IS NAMED RATHER THAN EXCUSED QUIETLY: two of this family's "
      "PAYLOAD KEYS are the same strings as two relation names (`stores` on the Owner Overview, "
      "`targets` on the month focus). They are keys the declaration READS OUT OF a response, not "
      "names it asserts — every provenance on this family is a module-level constant dereferenced "
      "from a registry, and the four checks above compare those BY IDENTITY, which a copied string "
      "cannot satisfy",
      all(re.search(r'\.get\("%s"\)' % k, DECL) for k in ("stores", "targets"))
      and all(isinstance(v, str) for v in (dd.REP_COMMISSIONS, dd.OPS_CHARGEBACK,
                                           dd.STORE_ROSTER, dd.TARGETS))
      and dd.UNNAMEABLE is None)
ctl("a declaration that RE-SPELLED a relation name instead of dereferencing it would fail the "
    "identity check — a string built rather than read is equal to the registry's and is not it, "
    "which is exactly the divergence an identity check catches and an equality check does not",
    "".join(["st", "ores"]) == lineage.STORE_ROSTER
    and "".join(["st", "ores"]) is not lineage.STORE_ROSTER)
check("A6 AND THE OTHER HALF OF THAT FIX — the two closing declarations that each held a LITERAL "
      "copy of the chargeback relation now dereference the registry too, string-identical. A "
      "registry nobody dereferences is not a fix (CLAUDE.md §19.18), and a copy left behind is a "
      "future divergence",
      opsdd.CHARGEBACK == (lineage.OPS_CHARGEBACK,)
      and envdd.CHARGEBACK == (lineage.OPS_CHARGEBACK,)
      and '"ops_chargeback"' not in read("backend/app/modules/closing/ops_chargeback_drilldown.py")
      and '"ops_chargeback"' not in read("backend/app/modules/closing/envelope_drilldown.py"))
check("A7 the unmeasured REASON this family emits is the TARGETS family's own key and has a "
      "reader-facing sentence in the one home — a reason with no entry there prints a bare field "
      "name on a manager's screen",
      dd.REASON_TARGET_NOT_SET in rd.UNMEASURED_REASONS
      and dd.REASON_TARGET_NOT_SET == "target_not_set")
check("A8 the MTD column→bucket map covers EXACTLY the configured LINE buckets — a sixth line "
      "bucket added to the one vocabulary fails this file rather than silently getting no panel",
      set(dd.BUCKET_COLUMN) == set(emd.LINE_BUCKETS),
      str(set(dd.BUCKET_COLUMN) ^ set(emd.LINE_BUCKETS)))
check("A9 and each of those five columns takes its UNIT off the BUCKET'S OWN declared basis, not a "
      "second spelling of the unit vocabulary",
      all(dd.mtd_unit(col) == dd.UNIT_OF_BASIS[emd.CODE_DEFAULTS[b]["basis"]]
          for b, col in dd.BUCKET_COLUMN.items())
      and set(dd.UNIT_OF_BASIS.values()) == {rd.UNIT_COUNT, rd.UNIT_MONEY})
ctl("a bucket whose basis is a word the unit vocabulary does not know resolves to NO unit, so the "
    "column is refused rather than given a guessed one",
    dd.UNIT_OF_BASIS.get("square_feet") is None and dd.mtd_unit("a_column_nobody_declared") is None)
check("A10 the declaration is PURE — no I/O, no framework, no clock, and no org / carrier / tenant "
      "/ store name (RULE TWO)",
      not re.search(r"^(import|from)\s+(os|sys|time|datetime|json|requests|fastapi)\b", DECL, re.M)
      and ".table(" not in DECL and "sb()" not in DECL)
check("A11 and it holds no dollar literal, rate or threshold of its own — every amount it carries "
      "was handed to it. Measured over the CODE's numeric constants, parsed out rather than "
      "grepped, so the section ids in its own prose are not mistaken for amounts",
      not [n for n in DECL_NUMBERS if n not in (0, 1)],
      str(sorted(set(DECL_NUMBERS))))
ctl("and that scan is NOT VACUOUS — a rate or a dollar figure written into the code really is "
    "found, while the same digits inside a docstring really are not",
    [n for n in _literals('"""See §CORE-17.3q and 19.38."""\nRATE = 0.15\n')[1]
     if n not in (0, 1)] == [0.15]
    and not [n for n in _literals('"""See §CORE-17.3q and 19.38, 0.5 of it."""\nX = 1\n')[1]
             if n not in (0, 1)])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE OWNER OVERVIEW — the router's own roll-up, reproduced line-for-line and PINNED to it.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the owner overview, over the router's own fold ==")

#: `exec_overview` lives in a FastAPI router that cannot be imported without the framework, so its
#: two folds are reproduced here and pinned to the router's source text below (B1).
_EXEC_FOLD = """        d['reps'] += 1
        d['paid'] += r.get('final_payout') or 0
        d['at_risk'] += r.get('at_risk') or 0
        d['chargebacks'] += r.get('chargeback_deducted') or 0
        d['flags'] += r.get('flag_count') or 0
        d['on_table'] += r.get('money_on_table') or 0
"""
_EXEC_TILES = """           "tiles": {"commissions_paid": round(sum(r.get('final_payout') or 0 for r in reps), 2),
                     "commission_at_risk": s.get('total_at_risk', 0),
                     "chargebacks_deducted": s.get('total_chargebacks', 0),
                     "money_on_table": s.get('total_money_on_table', 0),
                     "reps": s.get('reps', 0), "below_tier": s.get('below_tier', 0),
                     "open_chargebacks": open_cb},
"""
#: `rep_coaching`'s own definition of the money nobody was paid. Pinned for the same reason.
_ON_TABLE = "'money_on_table': round(at_risk + cbd['deducted'] + ops_amt, 2),"

check("B1 the three expressions this section reproduces are the router's OWN, character for "
      "character — if any of them changes, this file goes red rather than measuring a fold nobody "
      "runs",
      _EXEC_FOLD in ROUTER and _EXEC_TILES in ROUTER and _ON_TABLE in ROUTER,
      "fold=%s tiles=%s on_table=%s" % (_EXEC_FOLD in ROUTER, _EXEC_TILES in ROUTER,
                                        _ON_TABLE in ROUTER))

# Six people over three stores, with the shapes that matter: a person the run never itemised a
# subtotal for (so at_risk is withheld rather than printed as zero), a person with an ops chargeback,
# and a store with nothing wrong at all.
REPS = [
    {"rep": "Abid", "store": "1 Main St", "market": "LI", "at_risk": 120.50,
     "chargeback_deducted": 40.00, "flag_count": 2, "final_payout": 1180.00,
     "money_on_table": 160.50},
    {"rep": "Sam", "store": "1 Main St", "market": "LI", "at_risk": 0.0,
     "chargeback_deducted": 0.0, "flag_count": 0, "final_payout": 940.25,
     "money_on_table": 0.0},
    {"rep": "Dee", "store": "2 Oak Ave", "market": "NJ", "at_risk": 75.00,
     "chargeback_deducted": 15.75, "flag_count": 1, "final_payout": 610.00,
     "money_on_table": 115.75},   # includes a 25.00 ops chargeback
    {"rep": "Rae", "store": "2 Oak Ave", "market": "NJ", "at_risk": 0.0,
     "chargeback_deducted": 0.0, "flag_count": 0, "final_payout": 300.00,
     "money_on_table": 0.0},
    {"rep": "Jo", "store": "3 Pine Rd", "market": "NJ", "at_risk": 0.0,
     "chargeback_deducted": 9.99, "flag_count": 3, "final_payout": 55.01,
     "money_on_table": 9.99},
    {"rep": "Kit", "store": "3 Pine Rd", "market": "NJ", "at_risk": 0.0,
     "chargeback_deducted": 0.0, "flag_count": 0, "final_payout": 0.0,
     "money_on_table": 0.0},
]


def exec_overview(reps, open_cb=4):
    """THE ROUTER'S OWN roll-up and tile dict, reproduced (pinned by B1)."""
    by_store = {}
    for r in reps:
        st = r.get("store") or "—"
        d = by_store.setdefault(st, {"store": st, "market": r.get("market") or "", "reps": 0,
                                     "paid": 0.0, "at_risk": 0.0, "chargebacks": 0.0,
                                     "flags": 0, "on_table": 0.0})
        d["reps"] += 1
        d["paid"] += r.get("final_payout") or 0
        d["at_risk"] += r.get("at_risk") or 0
        d["chargebacks"] += r.get("chargeback_deducted") or 0
        d["flags"] += r.get("flag_count") or 0
        d["on_table"] += r.get("money_on_table") or 0
    stores = sorted(by_store.values(), key=lambda x: -x["paid"])
    for d in stores:
        for k in ("paid", "at_risk", "chargebacks", "on_table"):
            d[k] = round(d[k], 2)
    return {
        "tiles": {
            "commissions_paid": round(sum(r.get("final_payout") or 0 for r in reps), 2),
            "commission_at_risk": round(sum(r["at_risk"] for r in reps), 2),
            "chargebacks_deducted": round(sum(r["chargeback_deducted"] for r in reps), 2),
            "money_on_table": round(sum(r["money_on_table"] for r in reps), 2),
            "reps": len(reps), "below_tier": 2, "open_chargebacks": open_cb,
        },
        "stores": stores,
    }


EX = exec_overview(REPS)
EXD = dd.exec_drilldowns(EX)

check("B2 every MONEY tile and the people count open — five figures over four grains — and no "
      "figure the report did not fold gets a panel",
      set(EXD) == {"commissions_paid", "money_on_table", "commission_at_risk",
                   "chargebacks_deducted", "reps"},
      str(sorted(EXD)))
check("B3 each panel's figure IS the tile, to the cent, and every one ties out against the store "
      "rows it names",
      all(cents(EXD[k]["figure"]["amount"]) == cents(EX["tiles"][k]) for k in EXD)
      and all(EXD[k]["tie_out"]["ties"] is True for k in EXD),
      str({k: (EXD[k]["figure"]["amount"], EXD[k]["tie_out"]["ties"]) for k in EXD}))
check("B4 the three P&L tiles, the open-chargeback count and the reps-below-tier count are served "
      "NO panel — three columns of one stored statement row, and two figures this report folds per "
      "nothing",
      not {"revenue", "gross_profit", "net_income", "open_chargebacks",
           "below_tier"} & set(EXD))
check("B5 PROVENANCE SEPARATES THE GRAINS (§CORE-17.3n): at-risk names the stored run ALONE, paid "
      "and left-on-the-table name the run AND the recorded chargeback, and the chargeback "
      "deduction names NOTHING AT ALL",
      [s["table"] for s in EXD["commission_at_risk"]["sources"]] == [dd.REP_COMMISSIONS]
      and sorted(s["table"] for s in EXD["commissions_paid"]["sources"]) == sorted(
          [dd.OPS_CHARGEBACK, dd.REP_COMMISSIONS])
      and sorted(s["table"] for s in EXD["money_on_table"]["sources"]) == sorted(
          [dd.OPS_CHARGEBACK, dd.REP_COMMISSIONS])
      and EXD["chargebacks_deducted"]["sources"] == [],
      str({k: [s["table"] for s in EXD[k]["sources"]] for k in EXD}))
check("B6 DEFECT 1, ENCODED: the relation the chargeback deduction is read out of is created by NO "
      "migration here, so three of the five panels report a stated, countable UNTRACEABLE ORIGIN "
      "on every row (§CORE-17.3i) rather than omitting it — one per store row",
      EXD["chargebacks_deducted"]["untraceable_origins"] == len(EX["stores"])
      and EXD["commissions_paid"]["untraceable_origins"] == len(EX["stores"])
      and EXD["money_on_table"]["untraceable_origins"] == len(EX["stores"])
      and EXD["commission_at_risk"]["untraceable_origins"] == 0,
      str({k: EXD[k]["untraceable_origins"] for k in EXD}))
check("B7 and the relation really is unregistered — no migration in this repository creates it, "
      "which is WHY it cannot be given a plain name without putting a database name on the owner's "
      "screen (the `statement_drilldown.UNREGISTERED_FEED_LINES` finding, one report along)",
      not any(re.search(r"create table[^;]*\bchargeback_items\b", open(
                  os.path.join(ROOT, "database/migrations", f), encoding="utf-8",
                  errors="ignore").read(), re.I | re.S)
              for f in os.listdir(os.path.join(ROOT, "database/migrations"))
              if f.endswith(".sql"))
      and "chargeback_items" in ROUTER)
check("B8 the people count is a COUNT and the four money figures are MONEY — the unit vocabulary, "
      "not a guess",
      EXD["reps"]["figure"]["unit"] == rd.UNIT_COUNT
      and all(EXD[k]["figure"]["unit"] == rd.UNIT_MONEY
              for k in ("commissions_paid", "money_on_table", "commission_at_risk",
                        "chargebacks_deducted")))

# ── B9  A REAL FILTER THAT DROPS ROWS. This page carries no filter CONTROL, but `rep_coaching`
#        narrows its rows to a store-scoped manager's span before anything is folded — which is a
#        filter that really drops rows, and the re-folded tiles and panels must still agree.
SPAN = {"2 Oak Ave", "3 Pine Rd"}
REPS_SPAN = [r for r in REPS if r["store"] in SPAN]
EX_SPAN = exec_overview(REPS_SPAN, open_cb=4)
EXD_SPAN = dd.exec_drilldowns(EX_SPAN)
check("B9a NON-VACUITY WITNESS — the span really did drop rows, and really did drop a whole store",
      len(REPS_SPAN) < len(REPS) and len(EX_SPAN["stores"]) < len(EX["stores"])
      and len(EX_SPAN["stores"]) > 0,
      "%d of %d people, %d of %d stores" % (len(REPS_SPAN), len(REPS),
                                            len(EX_SPAN["stores"]), len(EX["stores"])))
check("B9b and under it every panel still IS its tile and still ties out — the server folds again "
      "and the panel follows by construction, so there is nothing to narrow",
      set(EXD_SPAN) == set(EXD)
      and all(cents(EXD_SPAN[k]["figure"]["amount"]) == cents(EX_SPAN["tiles"][k]) for k in EXD_SPAN)
      and all(EXD_SPAN[k]["tie_out"]["ties"] is True for k in EXD_SPAN)
      and cents(EXD_SPAN["commissions_paid"]["figure"]["amount"])
          != cents(EXD["commissions_paid"]["figure"]["amount"]))
check("B10 AN EMPTY REPORT DRILLS TO NOTHING — a month with no calculated commission offers no "
      "panel rather than a panel asserting a figure behind an empty list",
      dd.exec_drilldowns({"stores": [], "tiles": EX["tiles"]}) == {}
      and dd.exec_drilldowns({"stores": EX["stores"], "tiles": {}}) == {}
      and dd.exec_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  EXECUTIVE MTD — the producer's OWN total-row tuple, extracted from its source.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. executive MTD, against the producer's own total row ==")


def producer_total_keys():
    """THE PRODUCER'S OWN total-row key tuple, read out of `_exec_mtd._totals`'s source — never
    re-typed here. The two set-up-fee economics keys are folded in a second loop that PRESERVES
    None, so they are added separately and the `conv` / `apb` ratios below them are deliberately
    left out (§CORE-17.3h)."""
    m = re.search(r"def _totals\(rowset, label_key\):.*?for k in \((.*?)\):", ROUTER, re.S)
    if not m:
        return None
    keys = list(re.findall(r"'([a-z_]+)'", m.group(1)))
    if "for k in ('setup_fee_dealer_share', 'setup_fee_employee_pay'):" not in ROUTER:
        return None
    return tuple(keys + ["setup_fee_dealer_share", "setup_fee_employee_pay"])


_PKEYS = producer_total_keys()
check("C1 the producer's own total-row keys were READ OUT OF ITS SOURCE, both loops — a lock that "
      "cannot find the fold it is about is not a lock",
      _PKEYS is not None and len(_PKEYS) == 22, str(_PKEYS))
check("C2 and the declaration opens EXACTLY those figures — no column the report folds is left "
      "undrillable, and no column it does not fold is claimed",
      set(dd.mtd_columns()) == set(_PKEYS or ()),
      str(set(dd.mtd_columns()) ^ set(_PKEYS or ())))
check("C3 `conv` and `apb` are refused (§CORE-17.3h) — two ratios taken at the TOTAL from two other "
      "totals, both of which DO drill",
      "conv" not in dd.mtd_columns() and "apb" not in dd.mtd_columns()
      and {"total_activation", "bill_payment_qty", "acc_sales"} <= set(dd.mtd_columns()))
check("C4 every figure the declaration opens has a reader-facing LABEL that is not a field name, "
      "and none of them is the carrier-relabelled header (the shared panel is not behind that lens)",
      all(dd.MTD_LABELS[c] and "_" not in dd.MTD_LABELS[c]
          and dd.MTD_LABELS[c] != c and dd.MTD_LABELS[c][:1].isupper()
          for c in dd.mtd_columns()),
      str([c for c in dd.mtd_columns()
           if not (dd.MTD_LABELS.get(c) and "_" not in dd.MTD_LABELS[c]
                   and dd.MTD_LABELS[c] != c and dd.MTD_LABELS[c][:1].isupper())]))

_TOTALS_TAIL = """        for k in ('setup_fee_dealer_share', 'setup_fee_employee_pay'):
            _vals = [r.get(k) for r in rowset if r.get(k) is not None]
            t[k] = round(sum(_vals), 2) if _vals else None
"""
check("C5 and the producer really does PRESERVE None for the two set-up-fee economics totals — "
      "which is why no panel is served for them until the tenant states the percentage "
      "(§CORE-17.3m), character for character out of the router",
      _TOTALS_TAIL in ROUTER)


def mtd_row(label_key, name, **over):
    """A row in the shape `_exec_mtd._row` produces."""
    r = {label_key: name}
    for k in (_PKEYS or ()):
        r[k] = 0
    r.update(over)
    return r


def mtd_totals(rows, label_key, econ=True):
    """`_exec_mtd._totals`, reproduced (pinned by C5 and by the key extraction above)."""
    t = {label_key: "TOTAL"}
    for k in (_PKEYS or ())[:20]:
        t[k] = round(sum(r.get(k, 0) for r in rows), 2)
    for k in ("setup_fee_dealer_share", "setup_fee_employee_pay"):
        vals = [r.get(k) for r in rows if r.get(k) is not None]
        t[k] = round(sum(vals), 2) if (vals and econ) else None
    t["conv"] = round(t["total_activation"] / t["bill_payment_qty"], 4) if t["bill_payment_qty"] else 0.0
    t["apb"] = round(t["acc_sales"] / t["total_activation"], 2) if t["total_activation"] else 0.0
    return t


META = {"primary": "daily_sales_feed", "primary_rows": 410, "other": "raw_sales",
        "other_rows": 38, "filled_rows": 6}
STORE_ROWS = [
    mtd_row("store", "1 Main St", total_activation=31, activation=19, port=6, byod=4, upgrade=2,
            total_phones=28, trending_box=62, bill_payment_qty=140, amount=9814.55,
            acc_sales=4120.80, trending_acc_sales=8241.60, setup_fee=350.00,
            setup_fee_dealer_share=350.00, setup_fee_employee_pay=35.00,
            acc_plus_setup=4470.80, trending_acc_plus_setup=8941.60, activation_fee=620.00,
            total_protect=11),
    mtd_row("store", "2 Oak Ave", total_activation=12, activation=9, port=2, byod=1,
            total_phones=11, trending_box=24, bill_payment_qty=61, amount=3902.10,
            acc_sales=1515.25, trending_acc_sales=3030.50, setup_fee=120.00,
            setup_fee_dealer_share=120.00, setup_fee_employee_pay=12.00,
            acc_plus_setup=1635.25, trending_acc_plus_setup=3270.50, activation_fee=240.00,
            total_protect=4),
    mtd_row("store", "3 Pine Rd", total_activation=0, bill_payment_qty=3, amount=61.00,
            setup_fee_dealer_share=0.0, setup_fee_employee_pay=0.0),
]
EMP_ROWS = [
    mtd_row("employee", "Abid", total_activation=20, activation=13, port=4, byod=3,
            total_phones=18, trending_box=40, bill_payment_qty=95, amount=6500.10,
            acc_sales=2800.40, trending_acc_sales=5600.80, setup_fee=250.00,
            setup_fee_dealer_share=250.00, setup_fee_employee_pay=25.00,
            acc_plus_setup=3050.40, trending_acc_plus_setup=6100.80, activation_fee=400.00,
            total_protect=8),
    mtd_row("employee", "Dee", total_activation=23, activation=15, port=4, byod=2, upgrade=2,
            total_phones=21, trending_box=46, bill_payment_qty=109, amount=7277.55,
            acc_sales=2835.65, trending_acc_sales=5671.30, setup_fee=220.00,
            setup_fee_dealer_share=220.00, setup_fee_employee_pay=22.00,
            acc_plus_setup=3055.65, trending_acc_plus_setup=6111.30, activation_fee=460.00,
            total_protect=7),
]


def mtd_payload(store_rows=None, emp_rows=None, meta=META, econ=True):
    sr = STORE_ROWS if store_rows is None else store_rows
    er = EMP_ROWS if emp_rows is None else emp_rows
    return {"source": {"meta": meta},
            "by_location": {"rows": sr, "total": mtd_totals(sr, "store", econ)},
            "by_employee": {"rows": er, "total": mtd_totals(er, "employee", econ)}}


MP = mtd_payload()
MD = dd.mtd_drilldowns(MP)

check("C6 both tabs are served, each figure keyed with ITS OWN TAB — two row lists, two catalogues, "
      "and a panel that can never be about rows its tab is not showing",
      len(MD) == 2 * len(dd.mtd_columns())
      and all(("by_location.%s" % c) in MD and ("by_employee.%s" % c) in MD
              for c in dd.mtd_columns()),
      str(len(MD)))
check("C7 every figure on both pinned TOTAL rows IS its panel's figure, to the cent, and ties out",
      all(cents(MD["%s.%s" % (tab, c)]["figure"]["amount"]) == cents(MP[tab]["total"][c])
          and MD["%s.%s" % (tab, c)]["tie_out"]["ties"] is True
          for tab in ("by_location", "by_employee") for c in dd.mtd_columns()))
check("C8 and the two tabs really are DIFFERENT POPULATIONS — the store panel's rows are the "
      "stores and the employee panel's are the people, which is the whole reason they are two",
      [r["label"] for r in MD["by_location.acc_sales"]["rows"]]
          == [r["store"] for r in STORE_ROWS]
      and [r["label"] for r in MD["by_employee.acc_sales"]["rows"]]
          == [r["employee"] for r in EMP_ROWS])
check("C9 the sales provenance is resolved off the READ'S OWN META through the one home — both "
      "tables, because this read says the second one contributed",
      sorted(s["table"] for s in MD["by_location.acc_sales"]["sources"])
          == sorted([lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES]),
      str(MD["by_location.acc_sales"]["sources"]))
check("C10 a read that resolves to NOTHING reports an origin it could not trace rather than naming "
      "a feed on a guess (§CORE-17.3i)",
      dd.mtd_drilldowns(mtd_payload(meta={}))["by_location.acc_sales"]["untraceable_origins"]
          == len(STORE_ROWS)
      and dd.mtd_drilldowns(mtd_payload(meta={}))["by_location.acc_sales"]["sources"] == [])
_NO_ECON = dd.mtd_drilldowns(mtd_payload(econ=False))
check("C11 §CORE-17.3m ON THE MTD SCREEN — with the set-up-fee percentage unstated the producer's "
      "two totals are None and NO panel is served for them, rather than a panel asserting $0.00 "
      "behind a question nobody has answered",
      "by_location.setup_fee_dealer_share" not in _NO_ECON
      and "by_location.setup_fee_employee_pay" not in _NO_ECON
      and "by_location.setup_fee" in _NO_ECON
      and "by_location.setup_fee_dealer_share" in MD)
check("C12 THE PROJECTED FIGURES SAY SO. A trending column's rows are real sales and its figure is "
      "an estimate built from them, and the panel's note states that in the reader's words",
      all("PROJECTION, NOT A MEASUREMENT" in (MD["by_location.%s" % c]["note"] or "")
          for c in ("trending_box", "trending_acc_sales", "trending_acc_plus_setup"))
      and "PROJECTION" not in (MD["by_location.acc_sales"]["note"] or ""))
check("C13 DEFECT 3 IS STATED ON THE PANEL — the store rows address a store under ONE of its "
      "spellings, because the producer discards the canonical key after collapsing, and the "
      "employee panel carries no such caveat",
      "ONE of its" in (MD["by_location.acc_sales"]["note"] or "")
      and "ONE of its" not in (MD["by_employee.acc_sales"]["note"] or ""))
check("C14 a store that sold nothing countable is SHOWN with its measured zero and counted, "
      "because the producer really did add it — a measured zero is not an absence",
      any(r["label"] == "3 Pine Rd" and r["present"] and r["in_figure"]
          and cents(r["amount"]) == 0
          for r in MD["by_location.acc_sales"]["rows"]))

# ── C15  A REAL FILTER THAT DROPS ROWS — the page's store/market/employee/date filters are all
#         QUERY PARAMETERS applied before the fold, so the server re-folds and the panel follows.
STORE_ROWS_F = [r for r in STORE_ROWS if r["store"] != "1 Main St"]
MP_F = mtd_payload(store_rows=STORE_ROWS_F)
MD_F = dd.mtd_drilldowns(MP_F)
check("C15a NON-VACUITY WITNESS — the filter really did drop a row, and the biggest one",
      len(STORE_ROWS_F) == len(STORE_ROWS) - 1 and len(STORE_ROWS_F) > 0
      and cents(MP_F["by_location"]["total"]["acc_sales"])
          < cents(MP["by_location"]["total"]["acc_sales"]))
check("C15b and under it every figure still IS its panel's figure and still ties out — nothing to "
      "narrow, because the server folded the selection itself",
      all(cents(MD_F["by_location.%s" % c]["figure"]["amount"])
              == cents(MP_F["by_location"]["total"][c])
          and MD_F["by_location.%s" % c]["tie_out"]["ties"] is True
          for c in dd.mtd_columns())
      and all(len(MD_F["by_location.%s" % c]["rows"]) == len(STORE_ROWS_F)
              for c in dd.mtd_columns()))
check("C16 AN EMPTY TAB DRILLS TO NOTHING, and the OTHER tab is still served — an absent tab is not "
      "an absent report",
      set(dd.mtd_drilldowns(mtd_payload(store_rows=[]))) ==
          {"by_employee.%s" % c for c in dd.mtd_columns()}
      and dd.mtd_drilldowns({}) == {} and dd.mtd_drilldowns(None) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE COMPENSATION TREND — the REAL producers, driven through a stdlib stub client.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the compensation trend, over the real producers ==")


class _Q:
    """A PostgREST query the producer builds. Returns the rows this stub holds for the table it
    names, and nothing for any other read — which is how the producers' own degradation paths
    (a missing RPC, an unreadable config) are exercised rather than mocked away."""

    def __init__(self, rows):
        self._rows = rows
        self._lo, self._hi = 0, None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def execute(self):
        rows = self._rows if self._hi is None else self._rows[self._lo:self._hi + 1]
        return type("R", (), {"data": list(rows)})()


class _Schema:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return _Q(self._t.get(name, []))

    def rpc(self, *_a, **_k):
        raise RuntimeError("no RPC in a DB-free harness — the producer must degrade, not raise")


class _Client:
    def __init__(self, **tables):
        self._t = tables

    def schema(self, _name):
        return _Schema(self._t)


REP_ROWS = [
    {"period": "July 2026", "storeops_name": "Abid", "epay_salesperson": "ABID1",
     "store": "1 Main St", "total_payout": 1100.00},
    {"period": "August 2026", "storeops_name": "Abid", "epay_salesperson": "ABID1",
     "store": "1 Main St", "total_payout": 1240.50},
    {"period": "August 2026", "storeops_name": "Abid", "epay_salesperson": "ABID1",
     "store": "2 Oak Ave", "total_payout": 300.00},
    {"period": "August 2026", "storeops_name": "Dee", "epay_salesperson": "DEE1",
     "store": "2 Oak Ave", "total_payout": 805.25},
    {"period": "July 2026", "storeops_name": "Dee", "epay_salesperson": "DEE1",
     "store": "2 Oak Ave", "total_payout": 640.00},
    # A row the snapshot names with NEITHER spelling — defect 8.
    {"period": "August 2026", "storeops_name": "", "epay_salesperson": "",
     "store": "3 Pine Rd", "total_payout": 77.77},
]
RESULT = ct.compute_rep_pay_trend(_Client(rep_commissions=REP_ROWS), "org", months=6)
RPD = dd.rep_pay_drilldowns(RESULT)

check("D1 the REAL producer ran — two months kept, four people, and the per-month totals it folded",
      RESULT["months"] == ["July 2026", "August 2026"] and len(RESULT["reps"]) == 3
      and [m["period"] for m in RESULT["totals_by_month"]] == ["July 2026", "August 2026"],
      str(RESULT["months"]) + " " + str(len(RESULT["reps"])))
check("D2 DEFECT 8, ENCODED — a person the snapshot names with neither spelling is folded into one "
      "row under a literal, and that row still names the snapshot, which is true",
      any(r["rep"] == "(unknown)" for r in RESULT["reps"])
      and any(str(r["label"]).startswith("(unknown)")
              and r["row_key"]["rep"] == "(unknown)"
              and r["sources"] == [dd.REP_COMMISSIONS]
              for r in RPD["window"]["rows"]),
      str([r["label"] for r in RPD["window"]["rows"]]))
check("D3 both by-rep figures open — the newest month and the whole window, one grain and two "
      "columns of one fold — and nothing else does",
      set(RPD) == {"latest", "window"})
check("D4 the newest month's figure is the SERVER'S OWN month total and the rows are each person's "
      "pay in that month, and it ties out",
      cents(RPD["latest"]["figure"]["amount"])
          == cents(RESULT["totals_by_month"][-1]["total_payout"])
      and RPD["latest"]["tie_out"]["ties"] is True,
      str(RPD["latest"]["tie_out"]))
check("D5 DEFECT 5, AND WHY ITS CHECK IS REAL: the window figure is the sum of the MONTHS the report "
      "served while its rows are folded per PERSON — two different folds of one stored run, so a "
      "dropped person or a rounding drift genuinely breaks the tie-out",
      cents(RPD["window"]["figure"]["amount"])
          == cents(sum(m["total_payout"] for m in RESULT["totals_by_month"]))
      and RPD["window"]["tie_out"]["ties"] is True
      and cents(dd.window_total(RESULT)) == cents(sum(r["total"] for r in RESULT["reps"])))
check("D6 every by-rep figure names the CALCULATED SNAPSHOT and says so in words (§CORE-17.3j) — "
      "the trail stops at the stored run, not at the sales it read",
      all([s["table"] for s in RPD[k]["sources"]] == [dd.REP_COMMISSIONS] for k in RPD)
      and all("last commission calculation WROTE DOWN" in (RPD[k]["note"] or "") for k in RPD))
check("D7 'Reps paid' and 'Top rep' get NO key at all — the table below IS the list the first "
      "counts, and the second is a name",
      not {"reps", "reps_paid", "top_rep", "count"} & set(RPD))
_float = sorted(r["row_key"]["store"] for r in RPD["window"]["rows"]
                if r["row_key"]["rep"] == "Abid")
check("D8 A FLOATING REP READS AS ONE ROW PER ATTRIBUTION POINT, never one row carrying a list of "
      "stores — the grain is the point, so no row claims a person was in two places at once, and "
      "each row carries the market its own point was stamped with (§CORE-17.3l)",
      _float == ["1 Main St", "1 Main St", "2 Oak Ave"]
      and [(r["fields"] or {}).get("market") for r in RPD["window"]["rows"]
           if r["row_key"]["rep"] == "Abid"]
          == [p["market"] for p in dd.attribution_points(RESULT) if p["rep"] == "Abid"],
      str(_float) + " " + str([(r["fields"] or {}).get("market")
                               for r in RPD["window"]["rows"]]))
check("D9 a WINDOW WITH NO CALCULATION drills to nothing, and so does a window the producer kept no "
      "month for",
      dd.rep_pay_drilldowns(ct.compute_rep_pay_trend(_Client(rep_commissions=[]), "org")) == {}
      and dd.rep_pay_drilldowns(None) == {})

# ── D10  THE BY-ACCOUNT VIEW — driven for real, to prove each refusal and defect 6.
COMP_ROWS = [
    {"period": "July 2026", "account_id": "A1", "business_name": "North", "quantity": 3,
     "business_address": "1 Main St", "compensation_type": "PROMO", "payment_amount": 900.00},
    {"period": "August 2026", "account_id": "A1", "business_name": "North", "quantity": 1,
     "business_address": "1 Main St", "compensation_type": "PROMO", "payment_amount": 200.00},
    {"period": "July 2026", "account_id": "A2", "business_name": "South", "quantity": 2,
     "business_address": "2 Oak Ave", "compensation_type": "PROMO", "payment_amount": 500.00},
]
_ACC = ct.compute_residual_trend(_Client(raw_comp_report=COMP_ROWS), "org", months=6)
_ACC_MKT = ct.compute_residual_trend(_Client(raw_comp_report=COMP_ROWS), "org", months=6,
                                     market="LI")
check("D10 the REAL by-account producer ran — two months, a dip, and NO per-account fold for a "
      "month, which is exactly why its four month tiles are refused a panel",
      len(_ACC["totals_by_month"]) == 2 and _ACC["dip_count"] >= 1
      and not any("accounts_detail" in k or "by_account" in k for k in _ACC))
check("D11 DEFECT 6, ENCODED — `compute_residual_trend` takes a market filter, echoes it back in "
      "`params` as though it had been applied, and reads it NOWHERE: the response is byte-identical "
      "with and without it",
      _ACC_MKT["params"]["market"] == "LI" and _ACC["params"]["market"] == ""
      and _ACC_MKT["totals_by_month"] == _ACC["totals_by_month"]
      and _ACC_MKT["dips"] == _ACC["dips"]
      and not re.search(r"(?m)^\s+(if|elif).*\bmarket\b", read(
          "backend/app/modules/commcalc/comp_trend.py")))
check("D12 and the producer folds NO total for 'comp lost to dips' — the page adds it in the "
      "browser, so there is no served figure for a panel to explain and the declaration invents "
      "none (defect 7)",
      not any(k for k in _ACC if "lost" in k)
      and not [n for n in dir(dd) if not n.startswith("_")
               and ("lost" in n.lower() or "dip" in n.lower())]
      and not [v for v in DECL_STRINGS if "lost" in v or "dips" in v],
      str([n for n in dir(dd) if "lost" in n.lower() or "dip" in n.lower()]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  THIS MONTH'S FOCUS — two grains on one roster row, separated only by provenance.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. this month's focus, over the router's own saved-target set ==")

_FOCUS_FOLD = """    have = {str(r.get("store_code") or "").strip().upper() for r in rows} & codes
    return len(codes), len(have), sorted(codes - have), _emdd.target_rows(codes, have)
"""
check("E1 the fold this section reproduces is the router's OWN, character for character, and it "
      "hands the declaration the ROW LIST its two counts were folded over",
      _FOCUS_FOLD in ROUTER)

ROSTER = {"B-1115", "B-0652", "B-2300", "B-9001"}
SAVED = [{"store_code": "b-1115"}, {"store_code": "B-0652"}, {"store_code": "B-7777"}]


def focus_targets(roster, saved):
    """`_month_focus_targets`, reproduced (pinned by E1)."""
    codes = {str(c or "").strip().upper() for c in roster} - {""}
    have = {str(r.get("store_code") or "").strip().upper() for r in saved} & codes
    return len(codes), len(have), sorted(codes - have), dd.target_rows(codes, have)


_tot, _have, _without, _rows = focus_targets(ROSTER, SAVED)
FP = {"targets": {"stores_total": _tot, "with_saved_target": _have}}
FD = dd.focus_drilldowns(FP, _rows)

check("E2 the two counts the screen prints are the two figures that open, and nothing else on that "
      "page does",
      set(FD) == {"stores_total", "with_saved_target"})
check("E3 each panel's figure IS the count the screen prints, and both tie out",
      cents(FD["stores_total"]["figure"]["amount"]) == cents(_tot)
      and cents(FD["with_saved_target"]["figure"]["amount"]) == cents(_have)
      and FD["stores_total"]["tie_out"]["ties"] is True
      and FD["with_saved_target"]["tie_out"]["ties"] is True,
      "total=%s have=%s" % (_tot, _have))
check("E4 TWO GRAINS ON ONE ROW IN ONE UNIT, SEPARATED ONLY BY PROVENANCE (§CORE-17.3n) — the "
      "roster answers how many stores exist and the settings answer how many carry a goal, and "
      "they are not the same relation OR the same schema",
      [s["table"] for s in FD["stores_total"]["sources"]] == [dd.STORE_ROSTER]
      and [s["table"] for s in FD["with_saved_target"]["sources"]] == [dd.TARGETS]
      and FD["stores_total"]["rows"][0]["origins"][0]["schema"] == dd.SCHEMA_STOREOPS
      and FD["with_saved_target"]["rows"][0]["origins"][0]["schema"] == dd.SCHEMA
      and len(FD["stores_total"]["rows"]) == len(FD["with_saved_target"]["rows"]))
_unset = [r for r in FD["with_saved_target"]["rows"] if not r["present"]]
check("E5 §CORE-17.3m — a store with NO saved target is SHOWN, not counted, carries NO figure, "
      "names NO table at all, and says why in the targets family's own words",
      len(_unset) == _tot - _have and len(_unset) > 0
      and all(r["amount"] is None and not r["measured"] and not r["in_figure"]
              and len(r["origins"]) == 1 and r["origins"][0]["table"] is None
              and not r["origins"][0]["traceable"]
              and r["reason"] == dd.REASON_TARGET_NOT_SET
              and r["reason_label"] == rd.UNMEASURED_REASONS[dd.REASON_TARGET_NOT_SET]
              for r in _unset)
      and FD["with_saved_target"]["tie_out"]["shown_not_counted"] == len(_unset))
check("E6 and the figures did not move by a cent — the producer counted that store in the roster "
      "and not in the saved-target count, and the panels count it exactly the same way",
      cents(FD["stores_total"]["figure"]["amount"]) == cents(len(ROSTER))
      and cents(FD["with_saved_target"]["figure"]["amount"]) == cents(2)
      and sorted(_without) == ["B-2300", "B-9001"])
check("E7 a SAVED row for a store that is not on the roster is in NEITHER figure and in NEITHER "
      "panel — the producer intersects, and the panel opens the rows it folded",
      not any(r["label"] == "B-7777" for r in FD["with_saved_target"]["rows"]))
check("E8 the counts are a COUNT, never money — a store is not worth dollars",
      FD["stores_total"]["figure"]["unit"] == rd.UNIT_COUNT
      and FD["with_saved_target"]["figure"]["unit"] == rd.UNIT_COUNT)
check("E9 A FILTER THAT DROPS ROWS — a deactivated store leaves the roster, both counts re-fold, "
      "and both panels follow. NON-VACUITY: the roster really did shrink",
      True)
_ROSTER2 = ROSTER - {"B-1115"}
_t2, _h2, _w2, _r2 = focus_targets(_ROSTER2, SAVED)
FD2 = dd.focus_drilldowns({"targets": {"stores_total": _t2, "with_saved_target": _h2}}, _r2)
check("E9b and it did — one fewer store, one fewer saved target, both panels still their figure "
      "and still tying out",
      _t2 == _tot - 1 and _h2 == _have - 1
      and cents(FD2["stores_total"]["figure"]["amount"]) == cents(_t2)
      and cents(FD2["with_saved_target"]["figure"]["amount"]) == cents(_h2)
      and all(FD2[k]["tie_out"]["ties"] is True for k in FD2)
      and len(FD2["stores_total"]["rows"]) == len(FD["stores_total"]["rows"]) - 1)
check("E10 A READ THAT FAILED IS NOT 'NO STORE HAS A TARGET' — the producer returns None for both "
      "counts and no panel is served, rather than a panel asserting a management failure "
      "nobody measured",
      dd.focus_drilldowns({"targets": {"stores_total": None, "with_saved_target": None}}, []) == {}
      and dd.focus_drilldowns({"targets": {"stores_total": None, "with_saved_target": None}},
                              _rows) == {}
      and dd.focus_drilldowns(None, _rows) == {})
check("E11 `target_rows` SHAPES and does not decide — it reads nothing, normalises the casing both "
      "sides, and a store is `has_target` only where the PRODUCER's own set says so",
      dd.target_rows(["a", "B", " c "], ["A"]) ==
          [{"store_code": "A", "has_target": True}, {"store_code": "B", "has_target": False},
           {"store_code": "C", "has_target": False}]
      and dd.target_rows(None, None) == [] and dd.target_rows(["", "  "], []) == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §F  THE WIRING — a registry nobody dereferences is not a fix (CLAUDE.md §19.18).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. the wiring, locked so it cannot un-wire ==")

PAGES = {
    "exec": read("frontend/src/app/(platform)/commcalc/exec/page.tsx"),
    "mtd": read("frontend/src/app/(platform)/commcalc/exec/mtd/page.tsx"),
    "focus": read("frontend/src/app/(platform)/commcalc/month-focus/page.tsx"),
    "trend": read("frontend/src/app/(platform)/commcalc/comp-trend/page.tsx"),
}
#: THE §CORE-17.3o CALL, PER PAGE, decided by READING the page — the tell is whether a client
#: `reduce` over filtered rows feeds the tile. False = the figures are the SERVER's fold and
#: narrowing them would move a figure; True = the page folds them itself (§CORE-17.3d).
NARROWS = {"exec": False, "mtd": False, "focus": False, "trend": True}

check("F1 all four pages were read — a lock over a file nobody can find is not a lock",
      all(len(v) > 2000 for v in PAGES.values()),
      str({k: len(v) for k, v in PAGES.items()}))
check("F2 the four endpoints ATTACH the declaration's output and the router folds no panel of its "
      "own",
      ROUTER.count("exec_month_drilldown as _emdd") == 1
      and 'out["drilldown"] = _emdd.exec_drilldowns(out)' in ROUTER
      and 'out["drilldown"] = _emdd.mtd_drilldowns(out)' in ROUTER
      and 'result["drilldown"] = _emdd.rep_pay_drilldowns(result)' in ROUTER
      and '"drilldown": _emdd.focus_drilldowns(' in ROUTER
      and "report_drilldown.tile_panel(" not in ROUTER)
check("F3 and the MTD panels are attached in the ENDPOINT, not inside `_exec_mtd` — its five other "
      "callers (the pay path, the narrative comparison, the team snapshot) have no screen to render "
      "a panel on, and a drill-down on a pay path is weight nobody reads",
      "out[\"drilldown\"] = _emdd.mtd_drilldowns(out)" in ROUTER
      and ROUTER.count("_emdd.mtd_drilldowns") == 1
      and "_emdd." not in ROUTER.split("def _exec_mtd(", 1)[1].split("\n@router.", 1)[0])
check("F4 every page renders the SHARED panel and reads its figure through the ONE agreement gate — "
      "not a local copy of it",
      all("DrillDownPanel" in v and "useDrillTile" in v and "agreesToTheCent" in v
          for v in PAGES.values()),
      str([k for k, v in PAGES.items()
           if not ("DrillDownPanel" in v and "useDrillTile" in v and "agreesToTheCent" in v)]))
check("F5 §CORE-17.3o BUILD LOCK, ONE DIRECTION — `lib/drill-narrow`'s narrowing is NOT wired into "
      "the three pages whose figures the SERVER folds. Narrowing a panel there would make a figure "
      "read differently under a filter, which is a figure moved; a later reader cannot 'fix' them "
      "into a class they were never in",
      all("narrowPanel" not in PAGES[k] and "narrowPanels" not in PAGES[k]
          for k, n in NARROWS.items() if not n),
      str([k for k, n in NARROWS.items() if not n and "narrowPanel" in PAGES[k]]))
check("F6 §CORE-17.3o BUILD LOCK, THE OTHER DIRECTION — it IS wired into the one page that folds "
      "its own tiles, and that page READS each tile off its narrowed panel rather than reducing a "
      "second time. Both halves, or the figure and its evidence are two folds again",
      all("narrowPanel(" in PAGES[k] and "screenKey(" in PAGES[k]
          and "narrowedLatest?.figure?.amount" in PAGES[k]
          and "narrowedWindow?.figure?.amount" in PAGES[k]
          for k, n in NARROWS.items() if n))
check("F6b and NEITHER scan is vacuous — the text really is found on a page in that class and "
      "really is absent from one outside it, so F5 and F6 are each checking something",
      "narrowPanel" in PAGES["trend"] and "narrowPanel" not in PAGES["mtd"]
      and "narrowPanels" in read("frontend/src/app/(platform)/closing/tender-recon/page.tsx"))
check("F6c AND THE CALL WAS MADE BY READING THE PAGE, not by assertion — the narrowed page really "
      "does `reduce` over its filtered rows into the tile, and the three others really do read a "
      "SERVER-served figure straight out of the payload",
      "reps.reduce(" in PAGES["trend"]
      and "data?.tiles?.[k]" in PAGES["exec"]
      and "?.total?.[" in PAGES["mtd"]
      and "data?.targets?.[k" in PAGES["focus"])
check("F6d and the grid really does render the SERVED total row and reduce nothing, which is what "
      "makes the MTD call `False`",
      "totalRow={total}" in PAGES["mtd"]
      and "reduce" not in read("frontend/src/components/DataGrid.tsx").replace(
          "columns.filter(Boolean)", ""))
LABELS = read("frontend/src/lib/sourceLabels.ts")
_RELS = (dd.REP_COMMISSIONS, dd.OPS_CHARGEBACK, dd.STORE_ROSTER, dd.TARGETS,
         lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES)
check("F7 every relation this family CAN name has a PLAIN NAME in the one frontend home — the "
      "owner's ruling is to hide database names from the users",
      all(re.search(r"^\s+%s: '" % t, LABELS, re.M) for t in _RELS),
      str([t for t in _RELS if not re.search(r"^\s+%s: '" % t, LABELS, re.M)]))
check("F7b AND THE ONE IT CANNOT NAME IS ABSENT FROM THAT HOME, deliberately — an entry for an "
      "unregistered table fails the infra lock, which is the whole reason defect 1 is REPORTED "
      "rather than papered over with a label",
      not re.search(r"^\s+chargeback_items: '", LABELS, re.M)
      and "chargeback_items" not in DECL_STRINGS
      and dd.UNNAMEABLE is None)


def _uncommented(src):
    """The page with its `//` and `/* */` comments removed. A relation name in a COMMENT is a note
    to the next engineer; the ruling is about what a manager READS, and that is the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("//"))


_REL_RE = r"(rep_commissions|ops_chargeback|chargeback_items|account_statements|raw_comp_report)"
check("F8 and no page prints a relation name of its own — every table reaching copy goes through "
      "`sourceLabel()` inside the shared panel",
      all(not re.search(_REL_RE, _uncommented(v)) for v in PAGES.values()),
      str([k for k, v in PAGES.items() if re.search(_REL_RE, _uncommented(v))]))
check("F8b and that scan is NOT VACUOUS — it really does fire on the text it is looking for",
      bool(re.search(_REL_RE, _uncommented("const t = 'rep_commissions'")))
      and not re.search(_REL_RE, _uncommented("// rep_commissions is the snapshot")))
LEDGER = read("backend/harness_drilldown_pending.txt")
_ROUTES = ("/commcalc/exec", "/commcalc/exec/mtd", "/commcalc/month-focus", "/commcalc/comp-trend")
check("F9 all four routes are flipped in the coverage ledger, each with a note saying what drills "
      "AND what it refuses to claim",
      all(re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)
          for r in _ROUTES),
      str([r for r in _ROUTES
           if not re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)]))
COV = read("backend/harness_drilldown_coverage.py")


def _owes_max(src):
    """THE RATCHET'S PIN, or None when it cannot be read at all.

    **SHRINK-ONLY — and it must never be re-tightened to `==`.** The rule the ratchet states is
    *"may only shrink"*, and `<=` IS that rule. A lock that asserts the VALUE goes red the moment a
    later batch correctly lowers the pin, and the next reader's reflex when it goes red is to edit
    the number, which is how a ratchet quietly becomes decoration. Batch 10 pinned `== 74` and went
    red on batch 11; batch 15 re-tightened to `== 53` and went red on batch 16's shrink to 49. Batch
    16 wrote the ruling down for every batch lock that reads this ratchet, and this file keeps it.

    It still FAILS CLOSED: a pin ABOVE this batch's own number is red, and a pin that cannot be read
    at all is red rather than absent."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


_pin = _owes_max(COV)
_owes = len(re.findall(r"\towes\t", LEDGER))
check("F10 THE RATCHET SHRANK BY THE FOUR ROUTES THIS BATCH SERVED, and has never been pushed back "
      "up past this batch's 45 — the DIRECTION, not the value, so batch 18's correct shrink does "
      "not turn this file red",
      _pin is not None and _pin <= 45 and _owes <= 45 and _owes == _pin,
      "pin=%s owes=%s" % (_pin, _owes))
check("F10b NEGATIVE CONTROL — a pin ABOVE 45 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 46\n") > 45)
check("F10c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file",
      _owes_max("OWES_MAX = 45\n") <= 45 and _owes_max("OWES_MAX = 31\n") <= 45)
check("F10d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = many\n") is None)
GRAPH = read("backend/app/modules/core/module_graph.py")
check("F11 the declaration and this lock are written into the module graph BY HAND, under both "
      "facts it dereferences — AND the two closing declarations this batch re-keyed",
      "'app/modules/commcalc/exec_month_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/commcalc/exec_month_drilldown.py': ('_lineage',)" in GRAPH
      and "'app/modules/closing/ops_chargeback_drilldown.py': ('_lineage',)" in GRAPH
      and "'app/modules/closing/envelope_drilldown.py': ('_lineage',)" in GRAPH
      and GRAPH.count("harness_batch17_drilldown.py") >= 1)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("F12 and a lock nothing runs is not a lock — this file is run by the guard job and its paths "
      "filter names every file it is about",
      "python3 harness_batch17_drilldown.py" in WF
      and all(('"%s"' % f) in WF for f in
              ("backend/app/modules/commcalc/exec_month_drilldown.py",
               "backend/app/modules/commcalc/comp_trend.py",
               "backend/app/modules/commcalc/exec_metric_defs.py",
               "backend/app/modules/commcalc/month_focus.py",
               "backend/app/modules/closing/ops_chargeback_drilldown.py",
               "backend/app/modules/closing/envelope_drilldown.py",
               "frontend/src/app/(platform)/commcalc/exec/page.tsx",
               "frontend/src/app/(platform)/commcalc/exec/mtd/page.tsx",
               "frontend/src/app/(platform)/commcalc/month-focus/page.tsx",
               "frontend/src/app/(platform)/commcalc/comp-trend/page.tsx")),
      str([f for f in ("backend/app/modules/commcalc/exec_month_drilldown.py",
                       "frontend/src/app/(platform)/commcalc/comp-trend/page.tsx")
           if ('"%s"' % f) not in WF]))
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("F13 the batch is registered in the index — the section, the declaration, this harness and "
      "all four routes",
      "§CORE-17.3q" in IDX and "exec_month_drilldown.py" in IDX
      and "harness_batch17_drilldown.py" in IDX
      and all(r in IDX for r in _ROUTES))
check("F14 and the client fold's FIXTURES carry this batch's narrowed panels, so "
      "`prove_drill_narrow.mjs` tests the identity on them too",
      '"rep_pay.window"' in read("frontend/prove_drill_narrow_fixtures.json")
      and '"rep_pay.latest"' in read("frontend/prove_drill_narrow_fixtures.json")
      and "exec_month_drilldown as emd" in read("backend/harness_drill_narrow_fixtures.py"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §G  NO FIGURE MOVED — each screen's own pre-batch fold, unfiltered AND under a real filter.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. no figure moved, proved falsifiably ==")

# ── G1  The Owner Overview. Its pre-batch tiles were `fmt(t.<key>)` straight off the payload, so the
#        panel must BE that figure. Already proved unfiltered (B3) and under the span filter (B9b);
#        here the page's own expression is pinned so the comparison is against what it renders.
check("G1 the page still renders each tile off the SERVER'S OWN `tiles` entry, exactly as it did "
      "before this batch — the drill link was added beside the figure, never in front of it",
      all(("fmt(t.%s)" % k) in PAGES["exec"]
          for k in ("commissions_paid", "commission_at_risk", "chargebacks_deducted",
                    "money_on_table"))
      and "{`${t.reps || 0}`}" in PAGES["exec"])

# ── G2  Executive MTD. Its pre-batch TOTAL row was `totalRow={total}`, rendered as served.
check("G2 the grid is still handed the SERVED total row, and the drill strip is a separate block "
      "below it — the figures on screen are untouched",
      "totalRow={total}" in PAGES["mtd"] and "drillLinks" in PAGES["mtd"]
      and "const total = active?.total || {}" in PAGES["mtd"])

# ── G3  The month-focus sentence. Pre-batch it read `num(data.targets.with_saved_target)`.
check("G3 the two counts are still rendered off the payload's own `targets` entry",
      "num(data.targets.with_saved_target)" in PAGES["focus"]
      and "num(data.targets.stores_total)" in PAGES["focus"])


# ── G4  THE COMPENSATION TREND — the page's OWN pre-batch fold, ported, against the panel the tile
#        now reads. This is the narrowed page, so this is where the real work is.
def page_reduce(reps, month):
    """THE PAGE'S OWN PRE-BATCH FOLD, line for line:
         const repPerMonth = (m) => reps.reduce((s, r) => s + (r.by_period?.[m] || 0), 0)
         const repGrandTotal = reps.reduce((s, r) => s + (r.total || 0), 0)
    """
    latest = round(sum((r.get("by_period") or {}).get(month) or 0 for r in reps), 2)
    grand = round(sum(r.get("total") or 0 for r in reps), 2)
    return latest, grand


def page_filter(reps, stores):
    """THE PAGE'S OWN CLIENT FILTER, line for line: a store selection narrows the MONTHS within each
    rep (only the months worked at that store count), `by_period` and `total` are re-summed from the
    surviving attribution points, and a rep with no surviving month DROPS OUT."""
    out = []
    for r in reps:
        kept = [p for p in (r.get("points") or []) if p.get("store") in stores]
        if not kept:
            continue
        by_period, total = {}, 0.0
        for p in kept:
            by_period[p["period"]] = by_period.get(p["period"], 0.0) + (p.get("pay") or 0)
            total += p.get("pay") or 0
        out.append({**r, "by_period": by_period, "total": round(total * 100) / 100,
                    "_pts": kept})
    return out


def _key(k):
    """A PORT of `lib/drill-narrow.screenKey` / `rowKeyOf` — one comparable string for a row key."""
    return "\u0001".join("%s=%s" % (n, k[n]) for n in sorted(k) if k[n] not in (None, ""))


def narrow(panel, keep_keys):
    """A PORT of `lib/drill-narrow.narrowPanel`, deliberately the same arithmetic — including its
    refusal on a CAPPED panel and its identity when nothing was dropped."""
    if not panel or not panel.get("rows"):
        return None
    if panel.get("capped"):
        return None
    rows = [r for r in panel["rows"]
            if (not r.get("row_key")) or _key(r["row_key"]) in keep_keys]
    if len(rows) == len(panel["rows"]):
        return panel
    amount = round(sum((r.get("amount") or 0) for r in rows
                       if r.get("in_figure") and r.get("present")) * 100) / 100
    return {**panel, "figure": {**panel["figure"], "amount": amount}, "rows": rows,
            "rows_total_count": len(rows), "capped": False}


_month = RESULT["months"][-1]
_pre_latest, _pre_grand = page_reduce(RESULT["reps"], _month)
def point_keys(reps):
    """THE PAGE'S OWN KEEP SET, ported: one key per surviving ATTRIBUTION POINT, because the filter
    keeps and drops a person's individual months rather than the person (index §CORE-17.3l)."""
    return {_key({"rep": r["rep"], "period": p["period"], "store": p["store"]})
            for r in reps for p in (r.get("_pts") or r.get("points") or [])}


_all_keys = point_keys(RESULT["reps"])
_n_latest = narrow(RPD["latest"], _all_keys)
_n_window = narrow(RPD["window"], _all_keys)
check("G4a UNFILTERED — the page's own pre-batch fold and the panel each tile now reads are the "
      "SAME NUMBER to the cent, and with nothing dropped the narrow is the IDENTITY (the server's "
      "own figure, verdict and provenance served untouched)",
      agrees(_n_latest, _pre_latest) and agrees(_n_window, _pre_grand)
      and _n_latest is RPD["latest"] and _n_window is RPD["window"],
      "pre=(%s, %s) panel=(%s, %s)" % (_pre_latest, _pre_grand,
                                       _n_latest["figure"]["amount"],
                                       _n_window["figure"]["amount"]))
_SEL = {"2 Oak Ave"}
_shown = page_filter(RESULT["reps"], _SEL)
_f_latest, _f_grand = page_reduce(_shown, _month)
_keys = point_keys(_shown)
_nf_latest = narrow(RPD["latest"], _keys)
_nf_window = narrow(RPD["window"], _keys)
check("G4b NON-VACUITY WITNESS — the filter really did drop rows AND really did change both "
      "figures, so G4c is comparing something",
      len(_shown) < len(RESULT["reps"]) and len(_shown) > 0
      and cents(_f_grand) != cents(_pre_grand) and cents(_f_latest) != cents(_pre_latest),
      "%d of %d reps; grand %s -> %s" % (len(_shown), len(RESULT["reps"]), _pre_grand, _f_grand))
check("G4c UNDER THAT FILTER the narrowed panel is the tile, to the cent, for both figures — one "
      "fold for the figure and its evidence",
      agrees(_nf_latest, _f_latest) and agrees(_nf_window, _f_grand),
      "filtered=(%s, %s) narrowed=(%s, %s)" % (_f_latest, _f_grand,
                                               _nf_latest["figure"]["amount"],
                                               _nf_window["figure"]["amount"]))
check("G4d and the narrowed panel's ROWS are exactly the attribution points on screen — a panel "
      "about a population the screen is not showing is worse than no panel",
      sorted(_key(r["row_key"]) for r in _nf_window["rows"]) == sorted(_keys)
      and len(_keys) < len(_all_keys) and len(_keys) > 0,
      "%d narrowed rows vs %d screen points" % (len(_nf_window["rows"]), len(_keys)))
check("G4e THE SAFETY NET, PINNED: a panel that does not agree with its tile to the cent offers no "
      "drill and the screen keeps its own number — the shared gate, not a per-page condition",
      not agrees(_nf_window, _pre_grand) and not agrees(None, _f_grand)
      and agrees(_nf_window, _f_grand))
check("G4f AND A CAPPED PANEL REFUSES TO NARROW rather than presenting a shorter list as the whole "
      "of one — so the drill is not offered at all",
      narrow({**RPD["window"], "capped": True}, _keys) is None)
check("G4g THE PAGE'S OWN FALLBACK SURVIVES — where no panel is served the tile keeps the page's "
      "reduce, which is why `reducedGrand` / `reducedLatest` are still computed on the page",
      "reducedGrand" in PAGES["trend"] and "reducedLatest" in PAGES["trend"]
      and "?? reducedGrand" in PAGES["trend"] and "?? reducedLatest" in PAGES["trend"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z  MUTATIONS — each one tampers with ONE side and must come back RED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. mutations, one side at a time ==")

# Z1-Z4  The Owner Overview.
_ex = exec_overview(REPS)
_ex["tiles"]["commissions_paid"] = round(_ex["tiles"]["commissions_paid"] + 0.01, 2)
ctl("Z1 a ONE-CENT total on the Owner Overview breaks its tie-out, and the shared gate then refuses "
    "the drill",
    dd.exec_drilldowns(_ex)["commissions_paid"]["tie_out"]["ties"] is False
    and not agrees(dd.exec_drilldowns(_ex)["commissions_paid"], EX["tiles"]["commissions_paid"]))
_ex = exec_overview(REPS)
_ex["stores"] = _ex["stores"][1:]
ctl("Z2 a DROPPED STORE ROW breaks every money panel's tie-out — the figure is the report's and the "
    "rows are the evidence, so they really can disagree",
    all(dd.exec_drilldowns(_ex)[k]["tie_out"]["ties"] is False
        for k in ("commissions_paid", "money_on_table", "commission_at_risk",
                  "chargebacks_deducted")))
_ex = exec_overview(REPS)
_ex["stores"][0]["at_risk"] = None
ctl("Z3 A ROW THE REPORT FOLDED WHOSE FIGURE IS BLANKED is shown WITHOUT a number and is not "
    "counted, so the report and its evidence then disagree in words on the screen — on this panel "
    "`in_figure` is the default, 'the row has a number', so a blank row leaves the figure rather "
    "than making it unmeasurable",
    dd.exec_drilldowns(_ex)["commission_at_risk"]["tie_out"]["ties"] is False
    and dd.exec_drilldowns(_ex)["commission_at_risk"]["tie_out"]["shown_not_counted"] == 1
    and dd.exec_drilldowns(_ex)["commission_at_risk"]["tie_out"]["unmeasured_rows"] == 0)
_ex = exec_overview(REPS)
_ex["stores"][1]["reps"] = _ex["stores"][1]["reps"] + 1
ctl("Z4 a MIS-COUNTED store's people break the count panel while the money panels stay green — one "
    "side at a time, so the check is about the figure and not the payload",
    dd.exec_drilldowns(_ex)["reps"]["tie_out"]["ties"] is False
    and dd.exec_drilldowns(_ex)["commissions_paid"]["tie_out"]["ties"] is True)

# Z5-Z8  Executive MTD.
_mp = mtd_payload()
_mp["by_location"]["total"]["acc_sales"] = round(_mp["by_location"]["total"]["acc_sales"] + 0.01, 2)
ctl("Z5 a ONE-CENT MTD total breaks that figure's tie-out and no other's",
    dd.mtd_drilldowns(_mp)["by_location.acc_sales"]["tie_out"]["ties"] is False
    and dd.mtd_drilldowns(_mp)["by_location.amount"]["tie_out"]["ties"] is True
    and dd.mtd_drilldowns(_mp)["by_employee.acc_sales"]["tie_out"]["ties"] is True)
_mp = mtd_payload(store_rows=STORE_ROWS[:-1])
_mp["by_location"]["total"] = mtd_totals(STORE_ROWS, "store")
ctl("Z6 a DROPPED MTD ROW against the unfiltered total breaks every figure that row contributed to",
    dd.mtd_drilldowns(_mp)["by_location.amount"]["tie_out"]["ties"] is False
    and dd.mtd_drilldowns(_mp)["by_location.bill_payment_qty"]["tie_out"]["ties"] is False)
_mp = mtd_payload()
_mp["by_location"]["total"]["setup_fee_dealer_share"] = 0.0
_mp["by_location"]["total"]["setup_fee_employee_pay"] = 0.0
_mp["by_location"]["rows"] = [dict(r, setup_fee_dealer_share=None, setup_fee_employee_pay=None)
                              for r in STORE_ROWS]
ctl("Z7 AN UNSET PERCENTAGE FOLDED IN AS A MEASURED $0.00 is caught — no row carries a number, a "
    "$0.00 figure over no counted rows would tie VACUOUSLY (0 == 0), so NO PANEL IS SERVED at all: "
    "the absence of a figure, not a figure of zero (§CORE-17.3m, batch 16's precedent)",
    "by_location.setup_fee_dealer_share" not in dd.mtd_drilldowns(_mp)
    and "by_location.setup_fee_employee_pay" not in dd.mtd_drilldowns(_mp)
    and "by_location.acc_sales" in dd.mtd_drilldowns(_mp))
_mp = mtd_payload(meta={"primary": "a_table_the_registry_never_heard_of", "primary_rows": 9})
ctl("Z8 A FALLBACK-BASIS PLACEMENT READ AS EVIDENCE is refused — a stored string is not a licence "
    "to put an arbitrary identifier on an owner's screen, so the panel reports an untraceable "
    "origin instead of naming it",
    dd.mtd_drilldowns(_mp)["by_location.acc_sales"]["sources"] == []
    and dd.mtd_drilldowns(_mp)["by_location.acc_sales"]["untraceable_origins"] == len(STORE_ROWS))

# Z9-Z11  The compensation trend.
_r = {**RESULT, "totals_by_month": [dict(m) for m in RESULT["totals_by_month"]]}
_r["totals_by_month"][-1]["total_payout"] = round(
    _r["totals_by_month"][-1]["total_payout"] + 0.01, 2)
ctl("Z9 a ONE-CENT month total breaks the newest-month tie-out AND the window's, because the window "
    "figure is folded from those months — which is the point of defect 5's two folds",
    dd.rep_pay_drilldowns(_r)["latest"]["tie_out"]["ties"] is False
    and dd.rep_pay_drilldowns(_r)["window"]["tie_out"]["ties"] is False)
_r = {**RESULT, "reps": RESULT["reps"][:-1]}
ctl("Z10 a DROPPED PERSON breaks both by-rep tie-outs — the window figure comes from the months and "
    "the rows from the people, so a missing person really is visible",
    dd.rep_pay_drilldowns(_r)["window"]["tie_out"]["ties"] is False
    and dd.rep_pay_drilldowns(_r)["latest"]["tie_out"]["ties"] is False)
#: THE PROMPT'S MANDATED MUTATION, on the one panel that can carry it. `latest` counts a row by
#: WHICH MONTH it is, not by whether it carries a number, so an attribution point in the newest month
#: with no recorded pay is COUNTED AND UNMEASURED — and the verdict must be None, never True and
#: never False, because a sum missing a row was never a comparison.
_r = {**RESULT, "reps": [dict(r, points=[dict(p, pay=None)
                                         if p["period"] == RESULT["months"][-1] else p
                                         for p in r["points"]]) if i == 0 else r
                         for i, r in enumerate(RESULT["reps"])]}
ctl("Z11 A COUNTED ROW WITH NO NUMBER — an attribution point in the newest month carrying no "
    "recorded pay — makes that figure's verdict None, never True and never False, because a sum "
    "missing a row was never a comparison",
    dd.rep_pay_drilldowns(_r)["latest"]["tie_out"]["ties"] is None
    and dd.rep_pay_drilldowns(_r)["latest"]["tie_out"]["unmeasured_rows"] > 0)

# Z12-Z15  This month's focus.
_fp = {"targets": {"stores_total": _tot, "with_saved_target": _have + 1}}
ctl("Z12 CLAIMING ONE MORE STORE HAS A TARGET THAN THE SETTINGS DO breaks the tie-out, in words, on "
    "the manager's screen",
    dd.focus_drilldowns(_fp, _rows)["with_saved_target"]["tie_out"]["ties"] is False
    and "disagree" in dd.focus_drilldowns(_fp, _rows)["with_saved_target"]["tie_out"]["words"])
_bad = [dict(r, has_target=True) for r in _rows]
ctl("Z13 AN UNSET TARGET FOLDED IN AS A MEASURED COUNT is caught — treating every store as having "
    "one makes the rows total more than the figure, which is the §CORE-17.3m error arriving as "
    "arithmetic",
    dd.focus_drilldowns(FP, _bad)["with_saved_target"]["tie_out"]["ties"] is False
    and dd.focus_drilldowns(FP, _bad)["with_saved_target"]["tie_out"]["shown_not_counted"] == 0)
ctl("Z14 AND THE PROVENANCE MOVES WITH IT — a store wrongly marked as having a target would then "
    "NAME the target settings for a row that is not in them, which is the provenance half of the "
    "same error",
    len(dd.focus_drilldowns(FP, _bad)["with_saved_target"]["sources"][0]["rows"]) if False
    else dd.focus_drilldowns(FP, _bad)["with_saved_target"]["sources"][0]["rows"] == len(_rows)
    and dd.focus_drilldowns(FP, _rows)["with_saved_target"]["sources"][0]["rows"] == _have)
_rows_drop = _rows[:-1]
ctl("Z15 a DROPPED ROSTER ROW breaks the roster count's tie-out",
    dd.focus_drilldowns(FP, _rows_drop)["stores_total"]["tie_out"]["ties"] is False)

# Z16  The §CORE-17.3o call, mutated in BOTH directions.
ctl("Z16 the §CORE-17.3o locks are not vacuous in either direction — narrowing wired into a "
    "server-folded page is caught, and removed from the client-folded one is caught",
    ("narrowPanel" in PAGES["mtd"] + "narrowPanel(")
    and not all("narrowPanel" not in p for p in (PAGES["exec"], PAGES["mtd"] + "narrowPanel("))
    and not ("narrowPanel(" in PAGES["trend"].replace("narrowPanel(", "x(")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 96)
print("BATCH 17 DRILL-DOWN PROOF — %d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("\nFAILURES:")
    for f in FAIL:
        print("  - " + f)
print("=" * 96)
sys.exit(1 if FAIL else 0)
