"""BATCH 21 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four SALES-OUTCOME reports.

  /commcalc/peer-comparison  Peer Sales Comparison — who is behind their own traffic band
  /commcalc/product-mix      Product Mix & Ports — which rep is low on both signals
  /commcalc/spiff-impact     Spiff Impact — what one carrier pay type is worth, per store
  /commcalc/tax-collected    Tax Collected — the tax a store owes the state, by day and tender

WHAT THIS PROVES, AND WHY THESE FOUR
────────────────────────────────────
ONE QUESTION, WORKED THROUGH IN SEQUENCE: *this store sold less than its peers — show me the rows.*
The peer report says WHO is behind, at store grain. The mix report says WHICH REP inside that store
and on which of the two signals. The spiff report says WHAT THAT COSTS the store in carrier dollars
and in profit. The tax report is the SEAM: it is the same retail sales lines folded a fourth way, per
day and per tender, and it is the one of the four whose figures reach the financial statements. All
four fold the SAME month of the SAME retail sales lines through the SAME union read, which is why
they share a provenance resolver rather than four.

Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* Here that lands hardest on the ABSENCES, because every figure in this family is an accusation
about a store or a person: a store alone in its traffic band has nobody to be behind, a rep below the
box floor has no verdict at all, a store with no stored P&L has no profit to lift, and a sales line
with no date is in a store's tax total and in none of its days. Every one of those must read as a
blank with a reason, never as a zero somebody gets coached on (§CORE-17.3i).

THE PRODUCERS ARE DRIVEN FOR REAL. `peer_comparison.build` / `.lagging` / `.with_extra_metric`,
`product_mix.build`, `spiff_impact.pay_type_options` / `.default_selection` / `.store_money` /
`.units_per_100_boxes` / `.build` and `tax_collected.aggregate` are all called directly. The GRAIN
COUNT in §H is therefore MEASURED off the panels, as a SET compared for EQUALITY, not asserted.

THE ONE FOLD THAT LIVES INSIDE THE FRAMEWORK ROUTER IS REPRODUCED **AND PINNED**. The tax endpoint's
own payload assembly is written inline in `commcalc/router.py`, which cannot be imported without
FastAPI, so §E builds that payload in its own shape AND pins it to the router's OWN SOURCE TEXT: if
the assembly changes, this file goes red rather than quietly measuring a fold nobody runs. That is
batch 17's, 19's and 20's posture for exactly this problem.

The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch21_drilldown.py`
"""
import ast
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import statement_drilldown as acct_stmt            # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage           # noqa: E402
from app.modules.commcalc import peer_comparison as peer                    # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd           # noqa: E402
from app.modules.commcalc import product_mix as pmix                        # noqa: E402
from app.modules.commcalc import sales_outcome_drilldown as dd              # noqa: E402
from app.modules.commcalc import spiff_impact as spiff                      # noqa: E402
from app.modules.commcalc import tax_collected as tax                       # noqa: E402
from app.modules.core import report_drilldown as rd                         # noqa: E402

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
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch hands
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECL = read("backend/app/modules/commcalc/sales_outcome_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
HOME = read("backend/app/modules/core/report_drilldown.py")
B20_DECL = read("backend/app/modules/commcalc/performance_coaching_drilldown.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")

PAGES = {
    "peer-comparison": read("frontend/src/app/(platform)/commcalc/peer-comparison/page.tsx"),
    "product-mix": read("frontend/src/app/(platform)/commcalc/product-mix/page.tsx"),
    "spiff-impact": read("frontend/src/app/(platform)/commcalc/spiff-impact/page.tsx"),
    "tax-collected": read("frontend/src/app/(platform)/commcalc/tax-collected/page.tsx"),
}
#: THE ONE PAGE THAT FOLDS ITS OWN TILES IN THE BROWSER, AND THE THREE THAT DO NOT. DECIDED BY
#: READING EACH PAGE (§CORE-17.3o), and §F locks each decision in BOTH directions.
#:
#: The tax page re-adds its OWN tender and store tiles out of the rows it has in hand and applies a
#: market filter in the browser, so its panels must be narrowed to the stores on screen or they would
#: explain a number the page is not printing.
#:
#: The product-mix page is the interesting negative: it HAS a browser filter — the "only reps low on
#: both" checkbox — but that checkbox narrows the rep TABLE and touches no figure above it. A panel
#: explains the FIGURE, not the table, so narrowing there would make each panel disagree with the
#: tile it was opened from. Peer and spiff put every filter in the query string.
NARROWED_PAGES = ("tax-collected",)
SERVER_FOLDED_PAGES = ("peer-comparison", "product-mix", "spiff-impact")


def _literals(src):
    """Every STRING literal the module's CODE contains, and every number.

    A table name or a dollar figure may appear in a DOCSTRING or a comment — that is a note to the
    next engineer and the whole point of writing the defects down. The rulings are about the CODE, so
    the two are told apart by PARSING rather than by grepping.
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


def _code_only(src):
    """The module's CODE as text, with every docstring gone, by UNPARSING the tree. The rulings are
    about code; the defects are written down in prose, and a grep cannot tell the two apart."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


DECL_CODE = _code_only(DECL)


def strip_comments(src):
    """A TS/JS source with its comments removed, so a rule WRITTEN DOWN in a page's header cannot be
    mistaken for the rule being broken in its code. Batch 18's §F posture, reused verbatim."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", out)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — two design fixes, with BOTH halves of each ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the declaration dereferences the SALES UNION PROVENANCE resolver by OBJECT IDENTITY — the "
      "one home for 'which tables did this period's sales actually come from', not a copy of its "
      "logic, exactly as batches 14, 17, 18, 19 and 20 bound it",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A2 DESIGN FIX ONE, IN THE ONE HOME — the resolver now answers for a multi-month RANGE read by "
      "unioning its own answer over the per-month metas the range carries. Batch 20 reported that a "
      "range read could not name its own relations at all (its defect 11); the fix went into the "
      "ONE resolver rather than a second range resolver beside it",
      pmd.sales_union_tables({"per_period": [
          {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 5},
          {"primary": lineage.MONTHLY_SALES, "primary_rows": 3}]})
      == (lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES))
check("A2b AND IT IS A UNION OVER THE MONTHS, NOT A CARRIED PAIR — two months led by DIFFERENT "
      "relations name both, which a single carried `primary`/`other` pair could not have said. That "
      "is this batch's own improvement on the one-line fix batch 20 proposed",
      pmd.sales_union_tables({"per_period": [
          {"primary": lineage.MONTHLY_SALES, "primary_rows": 3},
          {"primary": lineage.MONTHLY_SALES, "primary_rows": 1}]})
      == (lineage.MONTHLY_SALES,))
check("A2c and EVERY EXISTING CALLER IS BYTE-IDENTICAL: a single-month meta carries no `per_period` "
      "at all and still resolves exactly as it did before this commit",
      pmd.sales_union_tables({"primary": lineage.LIVE_SALES_FEED, "primary_rows": 9,
                              "other": lineage.MONTHLY_SALES, "other_rows": 4,
                              "other_only_rows": 4})
      == (lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES)
      and pmd.sales_union_tables({"primary": lineage.LIVE_SALES_FEED, "primary_rows": 9,
                                  "other": lineage.MONTHLY_SALES, "other_rows": 4})
      == (lineage.LIVE_SALES_FEED,))
ctl("A2d control: the new key is not a free pass — an EMPTY `per_period` falls through to the "
    "single-month answer rather than returning nothing",
    pmd.sales_union_tables({"per_period": [], "primary": lineage.LIVE_SALES_FEED,
                            "primary_rows": 9}) == (lineage.LIVE_SALES_FEED,))
check("A3 DESIGN FIX TWO, IN THE ONE HOME — 'which figures can honestly be explained at all' is now "
      "`report_drilldown.served_tiles`, and BOTH this declaration and batch 20's dereference it BY "
      "IDENTITY. Neither file holds a copy of the rule any more",
      dd._served is rd.served_tiles and B20_DECL and "_served = report_drilldown.served_tiles" in B20_DECL)
check("A3b and the moved rule is BYTE-IDENTICAL in behaviour: a tile whose total is absent, a tile "
      "whose total is None, and a tile that counts no row at a zero total are each skipped, while a "
      "MEASURED zero is served — a measured 0 and a blank must not read alike (§CORE-17.3i)",
      rd.served_tiles(({"key": "a", "total": "a", "label": "A", "value": lambda r: r.get("v"),
                        "row_label": lambda r: "r", "keys": ("k",), "schema": "commcalc",
                        "tables": ("t",), "unit": rd.UNIT_COUNT},),
                      [{"k": "1", "v": None}], {"a": 0}) == {}
      and sorted(rd.served_tiles(
          ({"key": "a", "total": "a", "label": "A", "value": lambda r: r.get("v"),
            "row_label": lambda r: "r", "keys": ("k",), "schema": "commcalc",
            "tables": ("t",), "unit": rd.UNIT_COUNT},),
          [{"k": "1", "v": 0}], {"a": 0})) == ["a"])
ctl("A3c control: an absent and a None total are BOTH skipped, so the rule is not merely a "
    "truthiness test on the amount",
    rd.served_tiles(({"key": "a", "total": "a", "label": "A", "value": lambda r: r.get("v"),
                      "row_label": lambda r: "r", "keys": ("k",), "schema": "commcalc",
                      "tables": ("t",), "unit": rd.UNIT_COUNT},),
                    [{"k": "1", "v": 5}], {}) == {}
    and rd.served_tiles(({"key": "a", "total": "a", "label": "A", "value": lambda r: r.get("v"),
                          "row_label": lambda r: "r", "keys": ("k",), "schema": "commcalc",
                          "tables": ("t",), "unit": rd.UNIT_COUNT},),
                        [{"k": "1", "v": 5}], {"a": None}) == {})
check("A4 THE STORED FINANCIAL STATEMENTS ARE A REGISTRY CONSTANT AS OF THIS BATCH, dereferenced by "
      "identity rather than spelled — 'which relation is the stored statement run' has one home",
      dd.FINANCIAL_STATEMENTS is lineage.FINANCIAL_STATEMENTS
      and lineage.FINANCIAL_STATEMENTS == "account_statements")
check("A4b AND THE OTHER HALF OF THE FIX — the one pre-existing literal copy in a drill-down "
      "declaration is re-keyed in the SAME commit, bound by OBJECT IDENTITY and not by equal text. "
      "Writing the registry without wiring the callers to it is not a fix at all (CLAUDE.md §19.18)",
      acct_stmt.SNAPSHOT is lineage.FINANCIAL_STATEMENTS)
check("A5 the carrier's own compensation statement is the registry's name too, by identity",
      dd.COMP_REPORT is lineage.COMP_REPORT)


#: WHERE A PROVENANCE NAME IS A PROVENANCE NAME. The registry's scope is the scope it has carried
#: since batch 18: this is the one home for WHAT A DRILL-DOWN TELLS A READER A FIGURE CAME OUT OF. A
#: `.table("account_statements")` read is I/O, not a second statement about a figure's origin. So the
#: lock scans the files that MAKE provenance claims: every drill-down declaration and every lineage
#: registry. A second copy there is the divergence, and it fails the build.
def _declares_provenance(rel):
    return rel.endswith("_drilldown.py") or "lineage" in os.path.basename(rel) \
        or rel.endswith("report_drilldown.py")


def _sole_home(name):
    """Every file whose CODE makes a PROVENANCE CLAIM spelling `name` as a string literal, with how
    many lines do so. One home means ONE copy, not zero."""
    out = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "backend", "app")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, os.path.join(ROOT, "backend"))
            if not _declares_provenance(rel):
                continue
            src = open(path, encoding="utf-8", errors="replace").read()
            try:
                strings, _n = _literals(src)
            except SyntaxError:
                continue
            if name not in strings:
                continue
            spelled = [ln for ln in src.splitlines()
                       if '"%s"' % name in ln and ".table(" not in ln
                       and "#" not in ln.split('"')[0]]
            if spelled:
                out.append((rel, len(spelled)))
    return sorted(out)


_homes = _sole_home("account_statements")
check("A5b BUILD LOCK — across every drill-down declaration and lineage registry in the backend, the "
      "stored statements' name is spelled in exactly ONE file, the registry, on exactly ONE line. A "
      "second copy in any declaration fails the build",
      _homes == [("app/modules/commcalc/data_lineage_registry.py", 1)], str(_homes))
ctl("A5c control: the sole-home scan is NOT vacuous — pointed at a name a declaration really does "
    "spell it finds it, so a reintroduced copy would be seen",
    len(_sole_home("rep_commissions")) >= 1)
check("A6 every reason this declaration can emit has an entry in the ONE vocabulary, so a reader "
      "gets a sentence and never a bare field name — including the TWO this batch added",
      all(k in rd.UNMEASURED_REASONS for k in
          (dd.REASON_NO_PEER, dd.REASON_BELOW_FLOOR, dd.REASON_NO_SNAPSHOT,
           dd.REASON_NO_SALES_ROWS))
      and dd.REASON_NO_PEER == "no_peer_in_band"
      and dd.REASON_BELOW_FLOOR == "below_ranking_floor")
check("A7 THE PRODUCERS' OWN THRESHOLDS AND WORDS, DEREFERENCED, NEVER RE-SPELLED — the band's "
      "minimum peer count, the report's default ranked metric, the mix report's box floor and the "
      "spiff metric key are all bound by identity to the module that owns each",
      dd.BAND_MIN_PEERS is peer.BAND_MIN_PEERS
      and dd.DEFAULT_GAP_METRIC is peer.DEFAULT_GAP_METRIC
      and dd.MIN_BOXES_TO_RANK is pmix.MIN_BOXES_TO_RANK
      and dd.SPIFF_METRIC is spiff.SPIFF_METRIC)
check("A8 PURE — the declaration imports no framework, does no I/O, reads no clock and holds no org, "
      "carrier, tenant, product or store name in its CODE (RULE TWO)",
      not re.search(r"(?m)^\s*(import|from)\s+(fastapi|pydantic|requests|httpx|datetime|time)\b", DECL)
      and "sb()" not in DECL and ".execute()" not in DECL)
_money_lits = [n for n in DECL_NUMBERS if isinstance(n, float)]
check("A8b and it holds NO dollar literal, rate or threshold of its own: 'is this store at a loss' "
      "asks the one home's own rounding rather than writing `0.0`",
      _money_lits == [] and "report_drilldown.money(0)" in DECL_CODE, str(_money_lits))
check("A8c THE VERDICT IS THE PRODUCER'S, NOT THIS FILE'S — 'low on both signals' is read off the "
      "row as `product_mix`'s own first verdict word, so this declaration spells no verdict and "
      "re-tests nothing against two medians",
      "_pmix.VERDICTS[0][0]" in DECL_CODE and "'both'" not in DECL_CODE
      and '"both"' not in DECL_CODE)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE SHARED SALES CELLS — one fixture, four reports, as the estate really reads ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: THE CELL `_sales_cell_agg` BUILDS, in its own shape. Written by hand because the router cannot be
#: imported without FastAPI, and pinned to the router's own source text in §G so a change to the real
#: fold goes red here rather than leaving this file measuring a shape nobody produces.
def cell(store, rep, day, *, boxes=0, acc=0.0, tids=(), ports=(), billpay=(), aal=(),
         dev_lines=0, bands=None, models=None, price_sum=0.0):
    return {
        "store": store, "salesperson": rep, "day": day,
        "box_count": boxes, "accessory_rev": acc,
        "_txn": set(tids), "_prem": set(tids), "_port": set(ports),
        "_byod": set(), "_upg": set(), "_swap": set(), "_dev_tablet": set(),
        "_aal": set(aal), "_billpay_exec": set(billpay),
        "_dev_lines": dev_lines, "_dev_unclassified": 0, "_dev_credit": 0, "_dev_unnamed": 0,
        "_dev_price_sum": price_sum, "_dev_bands": dict(bands or {}),
        "_dev_models": dict(models or {}),
    }


#: FOUR STORES ON PURPOSE. S1 and S2 sit in ONE traffic band so they have each other to be compared
#: with; S3 sits ALONE in its own band, which is the absence the peer report must refuse to call a
#: result. Ben sells four boxes — below the mix report's own ranking floor — so he must come back
#: without a verdict rather than counted as having passed.
CELLS = {
    ("S1", "ANA"): cell("S1", "Ana", "2026-09-01", boxes=40, acc=1200.0,
                        tids=("T1", "T2"), ports=("T1",),
                        billpay=tuple("B%d" % i for i in range(300)),
                        aal=("T2",), dev_lines=20,
                        bands={"free": 2, "premium": 18}, price_sum=9000.0,
                        models={"PHONE A": {"lines": 20, "price_sum": 9000.0,
                                            "free_lines": 2, "cheap_lines": 2}}),
    ("S1", "BEN"): cell("S1", "Ben", "2026-09-02", boxes=4, acc=20.0,
                        tids=("T3",), dev_lines=3,
                        bands={"free": 3}, price_sum=0.0,
                        models={"PHONE B": {"lines": 3, "price_sum": 0.0,
                                            "free_lines": 3, "cheap_lines": 3}}),
    ("S2", "CAI"): cell("S2", "Cai", "2026-09-01", boxes=30, acc=200.0,
                        tids=("T4",), billpay=tuple("C%d" % i for i in range(290)),
                        dev_lines=15, bands={"free": 9, "premium": 6}, price_sum=2000.0,
                        models={"PHONE A": {"lines": 15, "price_sum": 2000.0,
                                            "free_lines": 9, "cheap_lines": 9}}),
    ("S3", "DOV"): cell("S3", "Dov", "2026-09-03", boxes=12, acc=500.0,
                        tids=("T5",), ports=("T5",),
                        billpay=tuple("D%d" % i for i in range(80)),
                        dev_lines=11, bands={"premium": 11}, price_sum=8000.0,
                        models={"PHONE C": {"lines": 11, "price_sum": 8000.0,
                                            "free_lines": 0, "cheap_lines": 0}}),
}
#: The SINGLE-MONTH union meta, in `_sales_rows_union`'s own shape.
META = {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 900,
        "other": lineage.MONTHLY_SALES, "other_rows": 120, "filled_rows": 7}

check("B1 the shared fixture really does put two stores in one band and one store alone, which is "
      "what makes the peer report's refusal measurable rather than asserted",
      len({k[0] for k in CELLS}) == 3 and len(CELLS) == 4)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. PEER COMPARISON — the real producer, and the store with nobody to be behind ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PEER_PAYLOAD = peer.build(CELLS, period="2026-09", resolve_market=lambda s: "North",
                          params={"period": "2026-09", "metric": peer.DEFAULT_GAP_METRIC})
PEER_PAYLOAD["lagging"] = peer.lagging(PEER_PAYLOAD, metric=peer.DEFAULT_GAP_METRIC)
PEER_PAYLOAD["source_meta"] = {"sales": META, "rows": 1020, "cells": len(CELLS)}
PD = dd.peer_drilldowns(PEER_PAYLOAD)

#: WHO THE PEER REPORT REALLY BANDED, DECLARED AS A SET AND COMPARED FOR EQUALITY. A `>=` on a set
#: derived from the code stays GREEN while a subject silently LEAVES it and another takes its place
#: — the guard goes blind, which is the shape `harness_subject_set_lock.py` §B1 refuses. Every set in
#: this file that the producers derive is therefore declared here and compared with `==`, printing
#: `extra` and `missing` both ways so a subject that disappears NAMES ITSELF.
_EXPECTED_PEER_STORES = {"S1", "S2", "S3"}
_EXPECTED_PEER_BANDS = {0, 2}
_MEASURED_PEER_STORES = {str(r.get("store") or "") for r in PEER_PAYLOAD["rows"]}
_MEASURED_PEER_BANDS = {b.get("band") for b in PEER_PAYLOAD["bands"]}
check("C1 the real producer was driven, and the STORES it banded and the BANDS it opened are "
      "exactly the declared sets — not 'at least three', which would stay green while a store "
      "vanished and another took its place",
      _MEASURED_PEER_STORES == _EXPECTED_PEER_STORES
      and _MEASURED_PEER_BANDS == _EXPECTED_PEER_BANDS,
      "stores extra=%s missing=%s · bands extra=%s missing=%s"
      % (sorted(_MEASURED_PEER_STORES - _EXPECTED_PEER_STORES),
         sorted(_EXPECTED_PEER_STORES - _MEASURED_PEER_STORES),
         sorted(_MEASURED_PEER_BANDS - _EXPECTED_PEER_BANDS),
         sorted(_EXPECTED_PEER_BANDS - _MEASURED_PEER_BANDS)))
ctl("C1b control: the equality is STRICTER THAN THE BOUND IT REPLACED — swapping one real store for "
    "a ghost at UNCHANGED SIZE leaves a `>=` green and makes the set comparison red, naming the "
    "ghost in `extra` and the real store in `missing`",
    len((_MEASURED_PEER_STORES - {"S3"}) | {"S9"}) >= 3
    and ((_MEASURED_PEER_STORES - {"S3"}) | {"S9"}) != _EXPECTED_PEER_STORES
    and sorted(((_MEASURED_PEER_STORES - {"S3"}) | {"S9"}) - _EXPECTED_PEER_STORES) == ["S9"]
    and sorted(_EXPECTED_PEER_STORES - ((_MEASURED_PEER_STORES - {"S3"}) | {"S9"})) == ["S3"])
check("C2 ONE panel per band plus the lagging count — and nothing else, because every other column "
      "on this report is a rate, an order statistic or a distinct-transaction set",
      dd.PEER_BEHIND_KEY in PD
      and all(k == dd.PEER_BEHIND_KEY or k.startswith(dd.PEER_BAND_PREFIX + ".") for k in PD),
      str(sorted(PD)))
for _k in sorted(PD):
    check(f"C3[{_k}] the panel's figure IS the producer's own, and its counted rows add up to it",
          PD[_k]["tie_out"]["ties"] is True, str(PD[_k]["tie_out"]["words"]))
check("C4 each band panel counts only the stores IN that band, measured against the producer's own "
      "store count for it",
      all(agrees(PD[dd.band_key(b["band"])], b["stores"])
          for b in PEER_PAYLOAD["bands"] if dd.band_key(b["band"]) in PD))
check("C5 the lagging count agrees with the producer's own list through the SHARED offer gate",
      agrees(PD[dd.PEER_BEHIND_KEY], len(PEER_PAYLOAD["lagging"])))
_alone = [r for r in PD[dd.PEER_BEHIND_KEY]["rows"] if r["reason"] == dd.REASON_NO_PEER]
check("C6 AN ABSENCE IS NOT A ROW AND NEVER A ZERO (§CORE-17.3i) — the store alone in its band comes "
      "back UNMEASURED with the `no_peer_in_band` reason and a sentence, never as a store that "
      "passed. Being alone in a band is not a result",
      len(_alone) == 1 and _alone[0]["measured"] is False and _alone[0]["amount"] is None
      and _alone[0]["in_figure"] is False
      and _alone[0]["reason_label"] == rd.UNMEASURED_REASONS[dd.REASON_NO_PEER],
      str([(r["label"], r["measured"], r["reason"]) for r in PD[dd.PEER_BEHIND_KEY]["rows"]]))
check("C6b and the panel SAYS SO rather than tying out silently — a counted row with no number "
      "leaves the tie-out undecided (`None`), which is the one home's own rule",
      PD[dd.PEER_BEHIND_KEY]["rows_total_count"] == 3)
ctl("C6c control: the lone-band store is really the one the producer refused to rank, not an "
    "artefact of the panel — the producer itself gives it no peers",
    all(int(r.get("peers") or 0) == 0 for r in PEER_PAYLOAD["rows"]
        if r["store"] == _alone[0]["label"]))
check("C7 every peer panel names the SALES RELATIONS THE READ ACTUALLY ANSWERED OUT OF, resolved "
      "off this read's own meta and not declared",
      all({s["table"] for s in PD[k]["sources"]}
          == {lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES} for k in PD))
check("C8 ONE GRAIN from this report — a peer store row, COUNTED, out of one provenance. The gap "
      "columns are a subtraction composed at the TOTAL and get no panel (§CORE-17.3k); the band "
      "best and median are order statistics, not folds",
      {PD[k]["figure"]["unit"] for k in PD} == {rd.UNIT_COUNT}
      and not any(k.startswith(("gap", "band_best", "band_median")) for k in PD),
      str(sorted(PD)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. PRODUCT MIX — the real producer, the rep below the floor, and two units ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIX_PAYLOAD = pmix.build(CELLS, period="2026-09", carrier_port={"S1": 12.0})
MIX_PAYLOAD["source_meta"] = {"sales": META}
MD = dd.product_mix_drilldowns(MIX_PAYLOAD)

check("D1 the real producer was driven and gave rep rows, a basis and per-store roll-ups",
      len(MIX_PAYLOAD["reps"]) == 4 and len(MIX_PAYLOAD["stores"]) == 3
      and MIX_PAYLOAD["basis"]["ranked_reps"] is not None)
check("D2 the two COMPANY figures the page prints a count for are served, and the three MEDIAN "
      "tiles beside them are not — an order statistic over the rows is not a fold of them",
      "flagged_reps" in MD and "ranked_reps" in MD
      and not any("median" in k for k in MD), str(sorted(k for k in MD if "." not in k)))
for _k in sorted(MD):
    check(f"D3[{_k}] the panel's figure IS the producer's own and its counted rows add up to it",
          MD[_k]["tie_out"]["ties"] is True, str(MD[_k]["tie_out"]["words"]))
#: WHICH SALESPEOPLE THE REPORT WITHHELD A VERDICT ABOUT — the SUBJECTS, not a count. A count cannot
#: say WHO was withheld, and the whole content of this check is who.
_EXPECTED_BELOW_FLOOR = {"Ben"}
_floor = {r["label"] for r in MD["flagged_reps"]["rows"] if r["reason"] == dd.REASON_BELOW_FLOOR}
check("D4 THE REP BELOW THE BOX FLOOR REACHES NO VERDICT, AND THE PANEL NAMES HIM — exactly the "
      "declared set comes back unmeasured with `below_ranking_floor`, never counted as having "
      "passed. A rate per box over four boxes is an accusation with nothing behind it",
      _floor == _EXPECTED_BELOW_FLOOR
      and all(r["measured"] is False and r["amount"] is None
              for r in MD["flagged_reps"]["rows"] if r["label"] in _floor),
      "extra=%s missing=%s" % (sorted(_floor - _EXPECTED_BELOW_FLOOR),
                               sorted(_EXPECTED_BELOW_FLOOR - _floor)))
ctl("D4b control: the floor row is the PRODUCER'S refusal, read off the row, not this file's — the "
    "producer really does give that rep no verdict",
    any(not r.get("verdict") for r in MIX_PAYLOAD["reps"]))
check("D5 EVERY STORE'S OWN FIGURES OPEN TOO, over that store's own reps and nobody else's",
      all(any(k.startswith("store.%s." % s["store"]) for k in MD) for s in MIX_PAYLOAD["stores"]),
      str(sorted(k for k in MD if k.startswith("store."))))
check("D5b and a store panel carries ONLY its own people — a panel can never hold a different set "
      "of reps than the row it opened from",
      all(MD["store.%s.boxes" % s["store"]]["rows_total_count"]
          == len([r for r in MIX_PAYLOAD["reps"] if r["store"] == s["store"]])
          for s in MIX_PAYLOAD["stores"] if "store.%s.boxes" % s["store"] in MD))
check("D5c every store panel is SUBJECT-STAMPED with the store it belongs to, so a renderer cannot "
      "put one store's rows under another's heading",
      all(MD[k]["subject"] == k.split(".")[1] and MD[k]["subject_kind"] == "store"
          for k in MD if k.startswith("store.")))
check("D6 TWO UNITS ON ONE REP-AT-A-STORE ROW — boxes are a count and accessory dollars are money, "
      "out of the same sales provenance, which is a real grain split and not a fixture artefact",
      MD["store.S1.boxes"]["figure"]["unit"] == rd.UNIT_COUNT
      and MD["store.S1.accessory_revenue"]["figure"]["unit"] == rd.UNIT_MONEY
      and {s["table"] for s in MD["store.S1.boxes"]["sources"]}
      == {s["table"] for s in MD["store.S1.accessory_revenue"]["sources"]})
check("D7 and no RATE is served a panel on this report at all (§CORE-17.3h): not accessory per box, "
      "not port share, not cheap share, not free share, not the carrier's own port percentage",
      not any(k.endswith(("per_box", "_share", "_pct")) or ".share" in k for k in MD),
      str(sorted(MD)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. SPIFF IMPACT — the real producer, THREE provenances on one store row ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: The carrier's own compensation statement rows, in the shape `spiff_impact` reads. S9 is paid for a
#: store the sales read never saw, which is how a statement really arrives.
COMP = [
    {"business_address": "S1", "compensation_type": "Promo Spiff",
     "payment_amount": 500.0, "quantity": 10},
    {"business_address": "S1", "compensation_type": "Base Commission",
     "payment_amount": 2000.0, "quantity": 40},
    {"business_address": "S2", "compensation_type": "Promo Spiff",
     "payment_amount": 0.0, "quantity": 0},
    {"business_address": "S2", "compensation_type": "Base Commission",
     "payment_amount": 900.0, "quantity": 30},
    {"business_address": "S9", "compensation_type": "Promo Spiff",
     "payment_amount": 120.0, "quantity": 3},
]
#: The org's own declared pay categories, as config rows and never as code (RULE TWO).
CLS = {"Promo Spiff": {"component": "SPIFF"}, "Base Commission": {"component": "COMMISSION"}}
SPIFF_OPTS = spiff.pay_type_options(COMP, lambda t: CLS.get(str(t or "").strip(), {"component": None}),
                                    component_line=lambda _c: "carrier_comm",
                                    month_token=lambda _t: None)
SPIFF_SEL, SPIFF_BASIS = spiff.default_selection(SPIFF_OPTS)
SPIFF_MONEY, _pl_line, _unres = spiff.store_money(
    COMP, SPIFF_SEL, lambda t: CLS.get(str(t or "").strip(), {"component": None}),
    component_line=lambda _c: "carrier_comm")
_rate = {}
for _r in (PEER_PAYLOAD.get("rows") or []) + (PEER_PAYLOAD.get("unbanded") or []):
    _m = SPIFF_MONEY.get(_r.get("store")) or {}
    _rate[_r["store"]] = (spiff.units_per_100_boxes(_m.get("spiff_units", 0), _r.get("boxes"))
                          if _r["store"] in SPIFF_MONEY else None)
PEER_FOR_SPIFF = peer.with_extra_metric(dict(PEER_PAYLOAD), spiff.SPIFF_METRIC,
                                        spiff.SPIFF_METRIC_LABEL, _rate)
#: S1 has a stored P&L and S2 has one at a LOSS; S3 has NONE at all, which is the absence the profit
#: panels must refuse to print as a zero.
SPIFF_PAYLOAD = spiff.build(SPIFF_MONEY, PEER_FOR_SPIFF,
                            {"S1": {"net_income": 5000.0, "revenue": 90000.0},
                             "S2": {"net_income": -200.0, "revenue": 40000.0}},
                            selected=SPIFF_SEL, selected_entry=SPIFF_OPTS[0],
                            selection_basis=SPIFF_BASIS, options=SPIFF_OPTS,
                            commission_line="carrier_comm", period="2026-09")
SPIFF_PAYLOAD["source_meta"] = {"sales": META, "comp_rows": len(COMP)}
SD = dd.spiff_drilldowns(SPIFF_PAYLOAD)

#: WHICH STORES THE SPIFF REPORT ROWED, DECLARED AS A SET. S9 is on the carrier's statement and in
#: no sales read — a real shape, and the reason this is a set rather than a count.
_EXPECTED_SPIFF_STORES = {"S1", "S2", "S3", "S9"}
_MEASURED_SPIFF_STORES = {str(r.get("store") or "") for r in SPIFF_PAYLOAD["rows"]}
check("E1 the real producer chain was driven end to end — pay-type options, the default selection, "
      "the per-store money, the per-100-box rate and the estate build — and the STORES it rowed are "
      "exactly the declared set, including the one the carrier paid that no sales read ever saw",
      bool(SPIFF_SEL) and SPIFF_PAYLOAD["estate"]["spiff_amount"] is not None
      and _MEASURED_SPIFF_STORES == _EXPECTED_SPIFF_STORES,
      "extra=%s missing=%s" % (sorted(_MEASURED_SPIFF_STORES - _EXPECTED_SPIFF_STORES),
                               sorted(_EXPECTED_SPIFF_STORES - _MEASURED_SPIFF_STORES)))
for _k in sorted(SD):
    check(f"E2[{_k}] the panel's figure IS the producer's own estate figure, to the cent",
          SD[_k]["tie_out"]["ties"] is True, str(SD[_k]["tie_out"]["words"]))
check("E3 THREE PROVENANCES ON ONE STORE ROW, which is what makes this report worth a family place: "
      "the carrier's compensation statement, the stored financial statements, and the sales "
      "relations the month's union read",
      {s["table"] for s in SD["spiff_amount"]["sources"]} == {dd.COMP_REPORT}
      and {s["table"] for s in SD["net_income"]["sources"]} == {dd.FINANCIAL_STATEMENTS}
      and {s["table"] for s in SD["boxes"]["sources"]}
      == {lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES})
check("E3b and the statements are separated from the statement by PROVENANCE ALONE (§CORE-17.3n) — "
      "the spiff dollars and the net profit are both MONEY over the same store row and differ only "
      "in where they were read",
      SD["spiff_amount"]["figure"]["unit"] == SD["net_income"]["figure"]["unit"] == rd.UNIT_MONEY
      and {s["table"] for s in SD["spiff_amount"]["sources"]}
      != {s["table"] for s in SD["net_income"]["sources"]})
#: WHICH STORES THE STATEMENT RUN NEVER COMPUTED — again the SUBJECTS. S3 sells and has no stored
#: P&L; S9 the carrier paid and the platform has no store row for at all. Two different absences,
#: and a count would hide which of them went missing.
_EXPECTED_NO_SNAPSHOT = {"S3", "S9"}
_nosnap = {r["label"] for r in SD["net_income"]["rows"] if r["reason"] == dd.REASON_NO_SNAPSHOT}
check("E4 THE STORES WITH NO STORED P&L ARE BLANKS WITH A REASON AND ARE NAMED, NEVER ZERO PROFITS "
      "— exactly the declared set is unmeasured, out of the figure, and carries the vocabulary's own "
      "sentence. A store that was not computed has not broken even",
      _nosnap == _EXPECTED_NO_SNAPSHOT
      and all(r["measured"] is False and r["amount"] is None and r["in_figure"] is False
              for r in SD["net_income"]["rows"] if r["label"] in _nosnap),
      "extra=%s missing=%s" % (sorted(_nosnap - _EXPECTED_NO_SNAPSHOT),
                               sorted(_EXPECTED_NO_SNAPSHOT - _nosnap)))
ctl("E4d control: this equality is STRICTER THAN THE BOUND IT REPLACED TOO — swapping one withheld "
    "store for another at UNCHANGED SIZE leaves a `>=` green and makes the set comparison red, "
    "naming both sides",
    len((_nosnap - {"S3"}) | {"S1"}) >= 1
    and ((_nosnap - {"S3"}) | {"S1"}) != _EXPECTED_NO_SNAPSHOT
    and sorted(((_nosnap - {"S3"}) | {"S1"}) - _EXPECTED_NO_SNAPSHOT) == ["S1"]
    and sorted(_EXPECTED_NO_SNAPSHOT - ((_nosnap - {"S3"}) | {"S1"})) == ["S3"])
check("E4b and the profit panels NAME THE SNAPSHOT and stop there (§CORE-17.3j) — what the statement "
      "run itself read is that run's business, not this panel's claim",
      [s["table"] for s in SD["net_income"]["sources"]] == [dd.FINANCIAL_STATEMENTS])
ctl("E4c control: the blank really is the store the fixture gave no stored P&L — the producer's own "
    "row carries None for it",
    any(r.get("net_income") is None for r in SPIFF_PAYLOAD["rows"]))
check("E5 NO PERCENTAGE ON THIS REPORT GETS A PANEL (§CORE-17.3h) — not the share of the line, not "
      "the profit lift, not the margin points, not the units per 100 boxes. Each tile opens the "
      "MEASURED FIGURES IT DIVIDES instead, which is what the page actually links",
      not any(k.endswith(("_pct", "_points", "per_100_boxes")) for k in SD), str(sorted(SD)))
check("E5b and the PROFIT LIFT gets none for a second reason as well: it is a subtraction composed "
      "at the TOTAL, and a subtraction drills at the ROW but not at the total (§CORE-17.3k). What "
      "opens underneath it is the profit, not the difference",
      "net_income_ex_spiff" not in SD and "net_income" in SD)
check("E6 the count of stores with no profit base to lift IS served — it is a count over the same "
      "rows, not a rate — and a store whose base could not be measured is unmeasured in it rather "
      "than counted as profitable",
      "stores_at_a_loss" in SD
      and any(r["reason"] == dd.REASON_NO_SNAPSHOT for r in SD["stores_at_a_loss"]["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. TAX COLLECTED — the real aggregation, the range union, and the undated line ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: Raw retail sales lines in the shape `tax_collected.aggregate` reads. Line 4 is a RETURN (excluded
#: from the gross headline by the producer's own rule) and line 5 HAS NO DATE — a real shape from the
#: live feed, and the defect this batch reports rather than hides.
SALES = [
    {"trans_id": "1", "trans_date": "2026-09-01", "store": "S1", "ext_price": 100.0, "tax": 8.5,
     "voided": "", "trans_type": "", "tender_type": "Cash"},
    {"trans_id": "2", "trans_date": "2026-09-02", "store": "S1", "ext_price": 200.0, "tax": 0.0,
     "voided": "", "trans_type": "", "tender_type": "Credit Card"},
    {"trans_id": "3", "trans_date": "2026-09-02", "store": "S2", "ext_price": 50.0, "tax": 4.25,
     "voided": "", "trans_type": "", "tender_type": "Cash; Credit Card"},
    {"trans_id": "4", "trans_date": "2026-09-03", "store": "S2", "ext_price": -20.0, "tax": -1.7,
     "voided": "", "trans_type": "Return", "tender_type": "Cash"},
    {"trans_id": "5", "trans_date": "", "store": "S1", "ext_price": 300.0, "tax": 25.0,
     "voided": "", "trans_type": "", "tender_type": "Cash"},
]
TAX_BUCKET = {"Cash": "cash", "Credit Card": "card", "Cash; Credit Card": "mixed"}
AGG = tax.aggregate(SALES, resolve_market=lambda s: "North",
                    bucket=lambda t: TAX_BUCKET.get(str(t or "").strip(), "other"),
                    tender_buckets=("cash", "card", "financing", "other", "mixed"))
#: THE RANGE READ'S META, in the shape `_sales_rows_union_txn_range` now carries — two months led by
#: DIFFERENT relations, which is the case a single carried pair could not have described.
RANGE_META = {"per_period": [
    {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 500, "other": lineage.MONTHLY_SALES,
     "other_rows": 10, "other_only_rows": 4},
    {"primary": lineage.MONTHLY_SALES, "primary_rows": 300, "other": lineage.LIVE_SALES_FEED,
     "other_rows": 0, "other_only_rows": 0}],
    "periods_read": ["2026-08", "2026-09"]}
#: THE ROUTER'S OWN PAYLOAD, rebuilt in its own shape and PINNED to its source text in §G.
TAX_PAYLOAD = {"period": "2026-09", "stores": AGG["stores"], "totals": AGG["totals"],
               "rows_in_window": AGG["rows_in_window"],
               "source_meta": {"sales": RANGE_META}}
TD = dd.tax_drilldowns(TAX_PAYLOAD)

check("F1 the real aggregation was driven and gave per-store rows with their own day rows",
      len(AGG["stores"]) == 2 and all(s.get("days") for s in AGG["stores"]))
check("F2 THE RANGE READ CAN NAME ITS OWN RELATIONS AT LAST — every tax panel names both, because "
      "the two months were led by different ones and the resolver unions its own answers (the fix "
      "in §A2, batch 20's defect 11)",
      all({s["table"] for s in TD[k]["sources"]}
          == {lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES} for k in TD),
      str({k: sorted(s["table"] for s in TD[k]["sources"]) for k in sorted(TD)}))
check("F3 the COMPANY figures, each TENDER BUCKET that moved, and each STORE's own tax are served",
      "tax" in TD and any(k.startswith(dd.TAX_TENDER_PREFIX + ".") for k in TD)
      and any(k.startswith(dd.TAX_STORE_PREFIX + ".") for k in TD), str(sorted(TD)))
check("F3b and a tender bucket NOTHING went through is not served a zero panel — a bucket with no "
      "sales and no tax has no rows to show, and an empty panel reads like a bug",
      not any(k.startswith(dd.tax_tender_key("financing", "")) for k in TD), str(sorted(TD)))
_ties = {k: TD[k]["tie_out"]["ties"] for k in TD}
check("F4 every COMPANY and TENDER panel ties out to the producer's own total, to the cent",
      all(v is True for k, v in _ties.items() if not k.startswith(dd.TAX_STORE_PREFIX + ".")),
      str({k: v for k, v in _ties.items() if v is not True}))
#: DEFECT 2, REPRODUCED RATHER THAN HIDDEN. A sales line with no date is in its store's TAX TOTAL and
#: in NONE of that store's days, so the per-store DAY panel cannot tie out — and it says so, loudly,
#: instead of being quietly reconciled by a panel that drops the undated line to make the sum work.
_undated = [k for k in TD if k.startswith(dd.TAX_STORE_PREFIX + ".") and _ties[k] is False]
check("F5 DEFECT 2 IS REPORTED, NOT PAPERED OVER — an undated sales line is counted in its store's "
      "tax total and appears in no day of it, so that store's DAY panel comes back explicitly NOT "
      "TIED and names the difference. A defect in live data is reported, never fixed by code that "
      "hides it (CLAUDE.md)",
      len(_undated) == 1 and "undated" in DECL.lower(),
      str({k: TD[k]["tie_out"]["words"] for k in TD if k.startswith(dd.TAX_STORE_PREFIX + ".")}))
ctl("F5b control: the other store's DAY panel DOES tie, so the red one above is the undated line "
    "and not a panel that can never tie",
    any(_ties[k] is True for k in TD if k.startswith(dd.TAX_STORE_PREFIX + ".")))
check("F6 THE EFFECTIVE TAX RATE GETS NO PANEL (§CORE-17.3h), and neither does the non-taxable "
      "revenue — which this batch reports as a defect of its own: its COMPANY total is composed "
      "from two other totals (`revenue - taxable_revenue`) while every store row folds its own, so "
      "the two can disagree. A figure composed at the total does not drill (§CORE-17.3k)",
      not any(k.endswith(("_rate", "rate")) for k in TD)
      and "untaxed_revenue" not in TD, str(sorted(TD)))
check("F6b AND THE COMPOSITION IS PINNED TO THE PRODUCER'S OWN SOURCE — if the total ever starts "
      "folding the store rows instead, this goes red and the refusal gets re-examined rather than "
      "standing on a docstring",
      re.search(r'"untaxed_revenue":\s*round\(tot_rev - tot_taxable, 2\)',
                read("backend/app/modules/commcalc/tax_collected.py")) is not None)
#: THE DIVERGENCE, DEMONSTRATED. Each store's own non-taxable revenue rounds DOWN to nothing, while
#: the company total — composed as revenue minus taxable revenue out of two separately rounded sums —
#: comes to a cent. Small, and that is the point: a figure whose total is composed rather than folded
#: cannot name the rows it came from, whatever the size of the gap.
_DIV = tax.aggregate(
    [{"trans_id": "a", "trans_date": "2026-09-01", "store": "A", "ext_price": 0.001, "tax": 0.0,
      "voided": "", "trans_type": "", "tender_type": "Cash"},
     {"trans_id": "b", "trans_date": "2026-09-01", "store": "A", "ext_price": 0.004, "tax": 0.01,
      "voided": "", "trans_type": "", "tender_type": "Cash"},
     {"trans_id": "c", "trans_date": "2026-09-01", "store": "B", "ext_price": 0.001, "tax": 0.0,
      "voided": "", "trans_type": "", "tender_type": "Cash"}],
    resolve_market=lambda s: "North",
    bucket=lambda t: TAX_BUCKET.get(str(t or "").strip(), "other"),
    tender_buckets=("cash", "card", "financing", "other", "mixed"))
ctl("F6c control: the untaxed-revenue divergence this batch reports is REAL in the producer's own "
    "output, not a claim in a docstring — the store rows sum to nothing and the composed company "
    "total is a cent",
    cents(round(sum(s["untaxed_revenue"] for s in _DIV["stores"]), 2))
    != cents(_DIV["totals"]["untaxed_revenue"]))
check("F7 every store panel is SUBJECT-STAMPED with its store, and the figure key says which of the "
      "store's four figures it opens",
      all(TD[k]["subject"] == k.split(dd.TAX_KEY_SEP)[1] and TD[k]["subject_kind"] == "store"
          for k in TD if k.startswith(dd.TAX_STORE_PREFIX + ".")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. §CORE-17.3o — a panel explains the FIGURE, not the table. All four, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _name, _src in PAGES.items():
    check(f"G0[{_name}] the page was read", bool(_src), _name)
#: THE CALL, NEVER THE IDENTIFIER. `"narrowPanel" in src` is satisfied by the IMPORT ALONE, which
#: batch 20 measured by renaming a call site and leaving its import. Every scan in this section
#: therefore matches a CALL (`narrowPanel(`) or an ELEMENT (`<DrillDownPanel`).
_NARROW_CALL = "narrowPanel("


def _calls_narrow(name):
    return _NARROW_CALL in strip_comments(PAGES[name])


check("G1 THE ONE BROWSER-FOLDED PAGE IS NARROWED — the tax page re-adds its own tender and store "
      "tiles out of the rows it holds and filters by market in the browser, so it CALLS "
      "`narrowPanel(` and its tiles and its panels are one fold over the selection on screen",
      all(_calls_narrow(n) for n in NARROWED_PAGES),
      str([n for n in NARROWED_PAGES if not _calls_narrow(n)]))
check("G1b AND THE THREE SERVER-FOLDED PAGES ARE NOT — no `narrowPanel` at all in the CODE of any "
      "of them, because narrowing a panel there would make it explain a figure the page is not "
      "showing. The rule is written down in each page's own HEADER, where somebody would break it, "
      "which is exactly why this scan strips comments first",
      all("narrowPanel" not in strip_comments(PAGES[n]) for n in SERVER_FOLDED_PAGES)
      and all("CORE-17.3u" in PAGES[n] for n in PAGES),
      str([n for n in SERVER_FOLDED_PAGES if "narrowPanel" in strip_comments(PAGES[n])]))
ctl("G1c control: the comment-stripping scan is NOT blind — it finds the real CALL in code",
    _NARROW_CALL in strip_comments("const x = narrowPanel(p, k)\n"))
ctl("G1d control: and it really does strip a comment that mentions it",
    _NARROW_CALL not in strip_comments("// never call narrowPanel(p) here\nconst x = 1\n"))
ctl("G1e control: AND THE CALL SCAN IS STRICTER THAN THE IDENTIFIER SCAN — an import-only page has "
    "the identifier and NOT the call, which is the hole batch 20 shipped with for one draft",
    "narrowPanel" in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n")
    and _NARROW_CALL not in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n"))
check("G2 THE NARROWED PAGE REALLY DOES FOLD ITS OWN TILES — it contains a `reduce(` in its code, "
      "which is the fold its panels have to follow",
      all("reduce(" in strip_comments(PAGES[n]) for n in NARROWED_PAGES),
      str([n for n in NARROWED_PAGES if "reduce(" not in strip_comments(PAGES[n])]))
check("G2b AND NO SERVER-FOLDED PAGE FOLDS A TILE IN THE BROWSER — no `reduce(` at all in any of "
      "the three, so there is no client fold for a panel to have to follow",
      all("reduce(" not in strip_comments(PAGES[n]) for n in SERVER_FOLDED_PAGES),
      str([n for n in SERVER_FOLDED_PAGES if "reduce(" in strip_comments(PAGES[n])]))
ctl("G2c control: the `reduce(` scan is NOT vacuous — it finds one where there is one",
    "reduce(" in strip_comments("const t = rows.reduce((a, r) => a + r.x, 0)\n"))
check("G2d THE PRODUCT-MIX PAGE IS THE INTERESTING NEGATIVE, AND ITS FILTER IS REAL — it carries a "
      "browser filter (`onlyFlagged`) that narrows the rep TABLE, and NOT ONE FIGURE above it. A "
      "panel explains the FIGURE, not the table (§CORE-17.3o), so narrowing there would make every "
      "panel disagree with the tile it was opened from. The page says so in its own header",
      "onlyFlagged ? reps.filter" in strip_comments(PAGES["product-mix"])
      and "narrowPanel" not in strip_comments(PAGES["product-mix"])
      and "not the table" in PAGES["product-mix"])
check("G3 the narrowed page reads its tile OFF its narrowed panel through the SHARED gate, rather "
      "than keeping two numbers and hoping",
      all("agreesToTheCent" in strip_comments(PAGES[n])
          and "figure?.amount" in strip_comments(PAGES[n]) for n in NARROWED_PAGES))
check("G4 and the three server-folded pages say in their own text WHY they are not narrowed — their "
      "filters are query parameters, which this asserts by finding each parameter being SENT",
      "qs.set('metric'" in PAGES["peer-comparison"]
      and "qs.set('bands'" in PAGES["product-mix"]
      and "qs.set('spiff'" in PAGES["spiff-impact"])
#: THE ELEMENT, NOT THE IDENTIFIER. `"DrillDownPanel" in src` is satisfied by the IMPORT ALONE,
#: which batch 19 measured by deleting a render and watching every scan stay green.
_RENDERS = {n: bool(re.search(r"<DrillDownPanel[\s/>]", strip_comments(s)))
            for n, s in PAGES.items()}
check("G5 all four pages RENDER the shared panel — matched on the JSX element `<DrillDownPanel`, "
      "never on the bare identifier, which an unused import satisfies on a page whose render was "
      "deleted — and hand-roll none of their own",
      all(_RENDERS.values())
      and not any(("rows_total_count" in strip_comments(s)) or ("tie_out" in strip_comments(s))
                  for s in PAGES.values()),
      str([n for n, ok in _RENDERS.items() if not ok]))
ctl("G5b control: the element scan is NOT vacuous, and it really is stricter than the identifier "
    "scan — an import-only page has the identifier and NOT the element",
    bool(re.search(r"<DrillDownPanel[\s/>]", "  <DrillDownPanel panel={p} />\n"))
    and not re.search(r"<DrillDownPanel[\s/>]",
                      "import DrillDownPanel from '@/components/DrillDownPanel'\n"))
check("G6 and each reads what the SERVER served, through the shared offer gate, rather than "
      "deriving a drill of its own",
      all("drilldown" in s and "useDrillTile" in s for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "useDrillTile" not in s]))
check("G7 no page spells a relation name of its own: a feed's plain name has one home and it is not "
      "a page",
      not any(re.search(r"'(raw_[a-z_]+|account_statements|daily_sales_feed|rep_commissions)'",
                        strip_comments(s)) for s in PAGES.values()),
      str([n for n, s in PAGES.items()
           if re.search(r"'raw_[a-z_]+'", strip_comments(s))]))
check("G8 THE NARROWER IS THE IDENTITY WHEN NOTHING IS DROPPED, which is what makes a narrowed tile "
      "trustworthy at all — the one home's own rule, asserted here because the tax page relies on "
      "it, and a capped panel is REFUSED rather than narrowed to a smaller number",
      "if (rows.length === panel.rows.length)" in read("frontend/src/lib/drill-narrow.ts")
      and "if (panel.capped) return null" in read("frontend/src/lib/drill-narrow.ts"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. NO FIGURE MOVED — read-only over the producer's own payload, and the router pinned ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _label, _payload, _fn in (("peer-comparison", PEER_PAYLOAD, dd.peer_drilldowns),
                              ("product-mix", MIX_PAYLOAD, dd.product_mix_drilldowns),
                              ("spiff-impact", SPIFF_PAYLOAD, dd.spiff_drilldowns),
                              ("tax-collected", TAX_PAYLOAD, dd.tax_drilldowns)):
    _before = copy.deepcopy(_payload)
    _fn(_payload)
    check(f"H1[{_label}] the payload is BYTE-IDENTICAL after the declaration has run over it — "
          f"deep-compared before and after, so no producer's arithmetic was moved",
          _before == _payload)
ctl("H1b control: the deep compare is NOT vacuous — it notices a single moved cent",
    copy.deepcopy(TAX_PAYLOAD) != {**TAX_PAYLOAD,
                                   "totals": {**TAX_PAYLOAD["totals"],
                                              "tax": TAX_PAYLOAD["totals"]["tax"] + 0.01}})
check("H2 the declaration's entry points take a PAYLOAD and nothing else — no client, no query, no "
      "period — so a panel is folded over rows the caller is already being served and a drill-down "
      "cannot become a disclosure",
      all(re.search(r"def %s\(payload, \*, cap=None\)" % fn, DECL)
          for fn in ("peer_drilldowns", "product_mix_drilldowns", "spiff_drilldowns",
                     "tax_drilldowns")))
check("H3 every endpoint attaches the key LAST, after everything it filters — the peer and mix "
      "reports after their RBAC keyset and market filter, and the spiff report after its own",
      ROUTER.index('out["drilldown"] = _sod.peer_drilldowns(out)')
      > ROUTER.index("rows = [r for r in rows if _keep(r.get(\"store\"))]")
      and 'out["drilldown"] = _sod.product_mix_drilldowns(out)' in ROUTER
      and 'out["drilldown"] = _sod.spiff_drilldowns(out)' in ROUTER)
check("H4 BUILD LOCK — no endpoint builds a panel of its own: there is no second shape for the same "
      "question",
      "rd.panel(" not in ROUTER and "report_drilldown.panel(" not in ROUTER)
#: THE ROUTER'S OWN TAX PAYLOAD, PINNED. This file rebuilds that assembly because FastAPI is not
#: importable here; the pin is what stops this harness quietly measuring a shape nobody produces.
_TAX_PIN = [
    "'stores': out, 'markets': all_markets, 'totals': agg['totals'],",
    "'source_meta': {'sales': _meta}}",
    "return {**_payload, 'drilldown': _sod.tax_drilldowns(_payload),",
    "rows, _meta = _sales_rows_union_txn_range(",
]
check("H5 THE TAX ENDPOINT'S OWN PAYLOAD ASSEMBLY IS PINNED TO THE ROUTER'S SOURCE TEXT — the keys "
      "this file rebuilds by hand are the keys the endpoint really serves, so a change to the real "
      "assembly goes RED here rather than leaving the fixture measuring a fold nobody runs",
      all(p in ROUTER for p in _TAX_PIN), str([p for p in _TAX_PIN if p not in ROUTER]))
ctl("H5b control: the pin is NOT vacuous — a line that is not in the router is reported missing",
    "'source_meta': {'sales': _nonexistent_meta}}" not in ROUTER)
check("H6 AND THE RANGE READ NOW CARRIES ITS PER-MONTH METAS, which is the one-line half of the "
      "design fix in §A2 — without it the resolver has nothing to union and the tax panels could "
      "name no relation at all",
      "meta['per_period'] = metas" in ROUTER)
check("H7 DEFECT 1 IS REPORTED, NOT FIXED HERE — `/commcalc/tax-collected` applies NO per-store RBAC "
      "scope, so a panel on it discloses exactly what the report already does and no more. Widening "
      "a sibling route's scope is not this PR's business; saying so plainly is",
      "applies no RBAC store scope at all" in DECL
      and "applies NO per-store RBAC scope at all" in DECL
      and "scope_keyset" not in ROUTER[ROUTER.index("'drilldown': _sod.tax_drilldowns") - 4000:
                                       ROUTER.index("'drilldown': _sod.tax_drilldowns")])
_ALL = dict(PD)
for _d in (MD, SD, TD):
    _ALL.update(_d)
check("H8 THE ROW CAP IS THE ONE HOME'S, and the true count is always served",
      all(p["rows_total_count"] >= p["rows_shown"] and p["capped"] is False
          for p in _ALL.values()))
_wide_rows = [dict(SPIFF_PAYLOAD["rows"][0], store="S%d" % i) for i in range(rd.ROW_CAP + 25)]
_wide = {**SPIFF_PAYLOAD, "rows": _wide_rows,
         "estate": {**SPIFF_PAYLOAD["estate"],
                    "spiff_amount": round((SPIFF_PAYLOAD["rows"][0]["spiff_amount"] or 0)
                                          * (rd.ROW_CAP + 25), 2)}}
_wp = dd.spiff_drilldowns(_wide)["spiff_amount"]
check("H8b and a REAL cap behaves — the panel serves ROW_CAP rows, states the TRUE count, and its "
      "tie-out is over ALL of them rather than over the slice anybody can see",
      _wp["capped"] is True and _wp["rows_shown"] == rd.ROW_CAP
      and _wp["rows_total_count"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["counted_rows"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["ties"] is True, str(_wp["tie_out"]))
check("H9 every panel carries its SCHEMA, so a stale renderer fails loudly rather than drawing half "
      "a panel",
      all(p["schema"] == rd.SCHEMA for p in _ALL.values()))
check("H10 and every panel's figure carries a unit from the ONE vocabulary",
      all(p["figure"]["unit"] in rd.UNITS for p in _ALL.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE GRAIN COUNT, MEASURED OFF THE PANELS AS A SET ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A grain fixes the ROW, the UNIT and the PROVENANCE (§CORE-17.3l / §CORE-17.3n). The row shape is
# named per report because the payload cannot say it; the unit and the provenance are READ OFF THE
# PANEL, so the number below is a measurement and not a claim.
ROW_SHAPE = {}
for _k in PD:
    ROW_SHAPE[("peer-comparison", _k)] = "peer_store_row"
for _k in MD:
    ROW_SHAPE[("product-mix", _k)] = "rep_at_a_store_row"
for _k in SD:
    ROW_SHAPE[("spiff-impact", _k)] = "spiff_store_row"
for _k in TD:
    ROW_SHAPE[("tax-collected", _k)] = ("tax_store_day_row"
                                        if _k.startswith(dd.TAX_STORE_PREFIX + ".")
                                        else "tax_store_row")


#: How this file writes "this panel's rows report an origin that cannot be traced" when it collects
#: provenances. Not a relation name: the whole point is that there is none (§CORE-17.3i).
UNTRACEABLE = "(nothing nameable)"


def _provenance(panel):
    """The set of relations this panel's rows were actually read out of, with an untraceable origin
    named as such — read off the panel, never declared."""
    names = {s["table"] for s in panel.get("sources") or ()}
    if panel.get("untraceable_origins"):
        names.add(UNTRACEABLE)
    return frozenset(names)


PANELS_BY_REPORT = {"peer-comparison": PD, "product-mix": MD, "spiff-impact": SD,
                    "tax-collected": TD}
MEASURED = set()
for (_report, _key), _shape in ROW_SHAPE.items():
    _panel = PANELS_BY_REPORT[_report][_key]
    MEASURED.add((_shape, _panel["figure"]["unit"], _provenance(_panel)))
print(f"       MEASURED {len(MEASURED)} distinct grains over {len(ROW_SHAPE)} panels in "
      f"{len({s for s in ROW_SHAPE.values()})} row shapes")
for _g in sorted(MEASURED, key=lambda g: (g[0], g[1], sorted(g[2]))):
    print(f"         · {_g[0]} / {_g[1]} / {sorted(_g[2])}")
#: THE GRAINS THIS FAMILY HAS, DECLARED AS A SET AND COMPARED FOR EQUALITY — batch 19's and 20's §H
#: shape. A BOUND ON THE SIZE IS NOT AN ASSERTION ABOUT THE SET: `len(MEASURED) >= 11` stays green
#: while a grain silently vanishes and another takes its place, which is the "guard that goes blind"
#: shape `harness_subject_set_lock.py` §B1 exists to refuse. The relations are the registry's and the
#: units are the one home's, so this declaration spells no table name of its own either.
_SALES_UNION = frozenset({lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES})
_EXPECTED = {
    # PEER — one store row, COUNTED, out of the sales union. One grain, and that is the report: every
    # other column on it is a rate, an order statistic or a distinct-transaction set.
    ("peer_store_row", rd.UNIT_COUNT, _SALES_UNION),
    # MIX — one rep-at-a-store row, two units over the same provenance.
    ("rep_at_a_store_row", rd.UNIT_COUNT, _SALES_UNION),
    ("rep_at_a_store_row", rd.UNIT_MONEY, _SALES_UNION),
    # SPIFF — one store row and THREE provenances on it, which is what earns this report its place.
    ("spiff_store_row", rd.UNIT_MONEY, frozenset({dd.COMP_REPORT})),
    ("spiff_store_row", rd.UNIT_COUNT, frozenset({dd.COMP_REPORT})),
    ("spiff_store_row", rd.UNIT_MONEY, frozenset({dd.FINANCIAL_STATEMENTS})),
    ("spiff_store_row", rd.UNIT_COUNT, frozenset({dd.FINANCIAL_STATEMENTS})),
    ("spiff_store_row", rd.UNIT_COUNT, _SALES_UNION),
    # TAX — the same retail lines folded a fourth way: a store row and, under it, a DAY row.
    ("tax_store_row", rd.UNIT_MONEY, _SALES_UNION),
    ("tax_store_row", rd.UNIT_COUNT, _SALES_UNION),
    ("tax_store_day_row", rd.UNIT_MONEY, _SALES_UNION),
}
_extra, _missing = sorted(MEASURED - _EXPECTED), sorted(_EXPECTED - MEASURED)
check("I1 THE GRAIN COUNT IS MEASURED AND THE SET IS COMPARED FOR EQUALITY — every panel's unit and "
      "provenance are read off the panel, collected as a SET so two panels that really are one "
      "grain collapse, and checked against the declared eleven. A bound on the SIZE would stay "
      "green while one grain vanished and another took its place, so a lost grain NAMES ITSELF here",
      MEASURED == _EXPECTED and len(MEASURED) == 11 and len(MEASURED) < len(ROW_SHAPE),
      "extra=%s missing=%s" % (_extra, _missing))
ctl("I1a control: the equality is not vacuous — dropping one declared grain is reported BY NAME, "
    "and the comparison really is a set difference rather than a count",
    (MEASURED - (_EXPECTED - {("tax_store_day_row", rd.UNIT_MONEY, _SALES_UNION)}))
    == {("tax_store_day_row", rd.UNIT_MONEY, _SALES_UNION)})
check("I1b THREE PROVENANCES ON ONE SPIFF STORE ROW is a real split and not an artefact of the "
      "fixture — the spiff dollars, the net profit and the boxes share a ROW and a UNIT family and "
      "differ only in where they were read. Provenance ALONE separates them (§CORE-17.3n)",
      len({_provenance(SD[k]) for k in ("spiff_amount", "net_income", "boxes")}) == 3)
check("I1c AND TWO ROW SHAPES INSIDE ONE REPORT — the tax report's company and tender figures fold "
      "STORE rows while each store's own figure folds that store's DAY rows, which is why its two "
      "money grains are two and not one",
      ROW_SHAPE[("tax-collected", "tax")] != ROW_SHAPE[("tax-collected",
                                                        [k for k in TD
                                                         if k.startswith(dd.TAX_STORE_PREFIX + ".")][0])])
ctl("I1d control: the provenance reader really reads the PANEL and not a declaration — pointed at a "
    "panel with no sources at all it says so rather than inventing one",
    _provenance({"sources": []}) == frozenset())
check("I2 TWO REPORTS SHARE A GRAIN WITH NO OTHER, AND THAT IS THE FAMILY'S POINT — the peer "
      "report's counted store row and the mix report's counted rep row come out of the SAME sales "
      "union, which is what makes the second report a drill of the first rather than a rival",
      _provenance(PD[dd.PEER_BEHIND_KEY]) == _provenance(MD["ranked_reps"]) == _SALES_UNION)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE RATCHET — shrink-only, and mutation-tested in all three directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("harness_drilldown_coverage.py")
LEDGER_TXT = read("harness_drilldown_pending.txt")
MY_ROUTES = ("/commcalc/peer-comparison", "/commcalc/product-mix", "/commcalc/spiff-impact",
             "/commcalc/tax-collected")


def _pin(src):
    """THE PINNED CEILING, READ OUT OF THE GATE. Returns None when it cannot be read, so this lock
    FAILS CLOSED rather than passing on an unreadable pin."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


def _owes(src):
    out = []
    for ln in (src or "").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) > 1 and parts[1].strip() == "owes":
            out.append(parts[0].strip())
    return out


def _state(src, route):
    for ln in (src or "").splitlines():
        parts = ln.split("\t")
        if parts and parts[0].strip() == route:
            return (parts[1].strip() if len(parts) > 1 else ""), \
                   (parts[2].strip() if len(parts) > 2 else "")
    return None, None


_p, _o = _pin(GATE), len(_owes(LEDGER_TXT))
check("J1 THE PIN AND THE LEDGER AGREE, AND BOTH ARE AT OR BELOW THIS BATCH'S NUMBER. The assertion "
      "is deliberately `<=` on the VALUE and `==` between the two: equality on the value has broken "
      "three times, because a later batch lowering the pin further is CORRECT and must stay green",
      _p is not None and _p <= 29 and _o <= 29 and _o == _p, str((_p, _o)))
ctl("J1b control: a HIGHER pin than this batch left is RED — the ratchet really is shrink-only",
    not (_pin("OWES_MAX = 33\n") is not None and _pin("OWES_MAX = 33\n") <= 29))
ctl("J1c control: a LATER, LOWER pin is GREEN — a future batch serving more routes must not have to "
    "come back and edit this file",
    (_pin("OWES_MAX = 22\n") or 99) <= 29)
ctl("J1d control: an UNREADABLE pin FAILS CLOSED rather than passing",
    _pin("OWES_MAX = nope\n") is None and _pin("") is None and _pin(None) is None)
check("J2 exactly the FOUR routes this batch serves flipped from `owes` to `shared`, and the state "
      "is true of the code — each page really renders the shared panel",
      all(_state(LEDGER_TXT, r)[0] == "shared" for r in MY_ROUTES),
      str({r: _state(LEDGER_TXT, r)[0] for r in MY_ROUTES}))
check("J3 and none of the four is still counted as owing one",
      not (set(MY_ROUTES) & set(_owes(LEDGER_TXT))))
check("J4 EVERY ONE OF THE FOUR NOTES CARRIES A `REFUSES TO CLAIM` CLAUSE — a drill-down that does "
      "not say what it declined to explain is an excuse nobody wrote down",
      all("REFUSES TO CLAIM" in (_state(LEDGER_TXT, r)[1] or "") for r in MY_ROUTES),
      str([r for r in MY_ROUTES if "REFUSES TO CLAIM" not in (_state(LEDGER_TXT, r)[1] or "")]))
check("J5 and each note names THIS batch's index section and the declaration that serves it, so a "
      "reader of the ledger can reach the reasoning",
      all("CORE-17.3u" in (_state(LEDGER_TXT, r)[1] or "")
          and "sales_outcome_drilldown" in (_state(LEDGER_TXT, r)[1] or "")
          for r in MY_ROUTES))
ctl("J4b control: the clause scan is NOT vacuous — a note without the clause is caught",
    "REFUSES TO CLAIM" not in "a note that declines to say what it declined")
check("J6 THE SIBLING ROUTES THIS PR DID NOT WIDEN TO ARE STILL ON THE LEDGER, honestly marked. A "
      "batch that quietly removed a row it had not served would shrink the ratchet without serving "
      "anybody",
      _state(LEDGER_TXT, "/commcalc")[0] == "owes")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== K. REGISTRATION — the index, the graph, the labels and the guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MG = read("backend/app/modules/core/module_graph.py")
WF = read(".github/workflows/carrier-vocab-guard.yml")
MIG = ""
_migdir = os.path.join(ROOT, "database", "migrations")
if os.path.isdir(_migdir):
    for _f in sorted(os.listdir(_migdir)):
        if _f.endswith(".sql"):
            MIG += open(os.path.join(_migdir, _f), encoding="utf-8", errors="replace").read()

DRILL_TABLES = sorted({t for t in (dd.COMP_REPORT, dd.FINANCIAL_STATEMENTS,
                                   lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES) if t})
_unknown = [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % re.escape(t), MIG)]
check("K1 every table a panel in this batch can name is KNOWN TO THE MIGRATIONS — a provenance claim "
      "about a relation nothing in this repository has ever heard of is worse than none",
      bool(MIG) and _unknown == [], str(_unknown))
_created = sorted(t for t in DRILL_TABLES
                  if re.search(r"create table[^;]*\b%s\b" % re.escape(t), MIG, re.I | re.S))
_only_altered = sorted(set(DRILL_TABLES) - set(_created))
check("K1b AND THE ONE THIS FOLDER DOES NOT CREATE IS NAMED RATHER THAN GLOSSED — the carrier's own "
      "compensation statement is only ever INDEXED and ALTERED by these migrations, never created "
      "by one, because it predates this folder. It is a real relation with a plain-name label and a "
      "panel may name it; the batch-20 `chargeback_items` case is the opposite one, where NOTHING "
      "in the repository knows the relation and the rows report an untraceable origin instead",
      _only_altered == [dd.COMP_REPORT]
      and re.search(r"(?mi)^\s*(do \$\$ begin )?create index[^;]*\b%s\b"
                    % re.escape(dd.COMP_REPORT), MIG) is not None,
      "created=%s only_altered=%s" % (_created, _only_altered))
_no_label = [t for t in DRILL_TABLES if not re.search(r"(?m)^\s+%s:" % re.escape(t), LABELS)]
check("K2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never an identifier (§19.38). This batch needed no new entry: all four "
      "relations were already labelled, which is why it trips no frontend phrase lock",
      _no_label == [], str(_no_label))
check("K3 the declaration is a registered CALLER of the one drill-down home, by hand",
      "'app/modules/commcalc/sales_outcome_drilldown.py': ('report_drilldown',)" in MG)
check("K4 and of the feed-lineage home, so 'who else answers which table this is' is one call away "
      "— ALONGSIDE the re-keyed caller, because a hand-written edge for the registry without one "
      "for the file that stopped spelling the literal is half a registration",
      "'app/modules/commcalc/sales_outcome_drilldown.py': ('_lineage',)" in MG
      and "'app/modules/account/statement_drilldown.py': ('_lineage',)" in MG)
check("K4b and of the THREE PRODUCERS whose own thresholds and verdict words it dereferences, each "
      "under the fact that producer is the home of",
      MG.count("'app/modules/commcalc/sales_outcome_drilldown.py': ('_peer',)") == 1
      and MG.count("'app/modules/commcalc/sales_outcome_drilldown.py': ('_pmix',)") == 1
      and MG.count("'app/modules/commcalc/sales_outcome_drilldown.py': ('_spiff',)") == 1)
check("K5 this batch's harness is registered in the one home's lock list",
      "'harness_batch21_drilldown.py'" in MG)
check("K6 the index carries THIS batch's section, and the next free letter was taken rather than "
      "guessed", "§CORE-17.3u" in IDX and "sales_outcome_drilldown.py" in IDX)
#: THE RUN STEP, NEVER THE MENTION. The harness's own name appears in the job's PATHS FILTER too, so
#: `"harness_batch21_drilldown.py" in WF` would stay green with the run line pointed at another
#: batch — which is how batch 20 found this hole. Match the `run:` line itself.
_RUN_STEP = [ln for ln in WF.splitlines()
             if re.search(r"run:\s*python3\s+harness_batch21_drilldown\.py\b", ln)]
check("K7 the guard job RUNS this harness — matched on the `run:` line itself, never on the file's "
      "name, which the paths filter below also carries — with a paths filter naming every file it "
      "is about",
      len(_RUN_STEP) == 1
      and "backend/app/modules/commcalc/sales_outcome_drilldown.py" in WF
      and "backend/app/modules/commcalc/peer_comparison.py" in WF
      and "backend/app/modules/commcalc/product_mix.py" in WF
      and "backend/app/modules/commcalc/spiff_impact.py" in WF
      and "backend/app/modules/commcalc/tax_collected.py" in WF,
      str(len(_RUN_STEP)))
ctl("K7b control: the run-step scan really is stricter than naming the file — a paths-filter line "
    "carrying the same name is NOT a run step",
    not re.search(r"run:\s*python3\s+harness_batch21_drilldown\.py\b",
                  "      - 'backend/harness_batch21_drilldown.py'"))
_steps = [ln for ln in WF.splitlines() if "batch21" in ln]
check("K8 and its step's `name:` / `run:` scalars carry NO colon-space — a plain scalar that does "
      "makes the WHOLE workflow fail to parse, so every job in it silently never runs",
      bool(_steps) and not any(re.search(r"(?m)^\s*(?:-\s*)?(name|run):\s+[^'\"].*:\s", ln)
                               for ln in _steps), str(_steps))
check("K9 the four pages are registered in the index's endpoint rows with this batch's section",
      IDX.count("DRILL-DOWN §CORE-17.3u") >= 4, str(IDX.count("DRILL-DOWN §CORE-17.3u")))
check("K10 the ratchet gate itself still passes its own `shared` lock for these routes, which is "
      "what makes the ledger flip an assertion rather than a claim",
      "D1 BUILD LOCK" in GATE and "<DrillDownPanel" in GATE)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
_mut = len([p for p in PASS if p.startswith("MUT")]) + len([f for f in FAIL if f.startswith("MUT")])
print(f"{_mut} mutations / negative controls")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
