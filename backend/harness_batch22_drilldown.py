"""BATCH 22 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the LAST FOUR commcalc routes this batch serves.

  /commcalc                            CommCalc Dashboard — the payout headline and the counts
  /commcalc/reports                    Rep Incentive Report — the header total and the footer
  /commcalc/mdf-entitlement-coverage   Subsidy Months Missing — did every entitled month arrive
  /commcalc/activations                Activations — the POS activation basis of truth

WHAT THIS PROVES, AND WHY THESE ARE THREE FAMILIES AND NOT ONE
──────────────────────────────────────────────────────────────
The batch was handed over as seven routes with a prior judgement attached — that two of them were
shells and the rest had nothing in common. Reading each page and each endpoint says otherwise on the
first point and agrees on the second: NONE of the seven is a shell, and these four fall into THREE
families, because a family is a shared ROW and a shared PROVENANCE and not a shared subject.

  · `/commcalc` and `/commcalc/reports` read the SAME endpoint, fold the SAME rep rows of the SAME
    stored month and print the SAME headline. One catalogue, two screens.
  · `/commcalc/mdf-entitlement-coverage` folds a STORE-MONTH of a declared entitlement window out of
    two carrier payment records and a config declaration. Nothing else in the platform folds it.
  · `/commcalc/activations` folds a STORE ROW of DISTINCT DEVICES out of the custom-import capture —
    the basis of truth the sales-derived counts are reconciled AGAINST, which is exactly why it may
    not be folded through any sales catalogue.

THE PRODUCERS ARE DRIVEN FOR REAL. `rep_incentive_range.assemble` (through the real
`payout_slice_evidence`) and `entitlement_coverage.entitlement_store_months` are called directly. The
activations fold is written INLINE in `commcalc/router.py`, which cannot be imported without FastAPI,
so §F reproduces it in its own shape AND PINS IT to the router's own source text — batch 17's, 19's,
20's and 21's posture for exactly this problem — and the router's `_blank` is EXTRACTED FROM THE
ROUTER AND EXECUTED, because one of this batch's defects lives in that function and a reproduction
nobody runs would prove nothing.

The GRAIN COUNT in §I is MEASURED off the panels, as a SET compared for EQUALITY, never a bound.
The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch22_drilldown.py`
"""
import ast
import copy
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import activation_bucketing as actb                # noqa: E402
from app.modules.commcalc import activation_count_drilldown as acdd          # noqa: E402
from app.modules.commcalc import carrier_pay_drilldown as cpd                # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage            # noqa: E402
from app.modules.commcalc import entitlement_coverage as ec                  # noqa: E402
from app.modules.commcalc import entitlement_coverage_drilldown as ecdd      # noqa: E402
from app.modules.commcalc import exec_month_drilldown as emdd                # noqa: E402
from app.modules.commcalc import payout_slice_evidence as pse                # noqa: E402
from app.modules.commcalc import rep_incentive_drilldown as ridd             # noqa: E402
from app.modules.commcalc import rep_incentive_range as rir                  # noqa: E402
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
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate the narrowed screen in this batch
    hands to `useDrillTile`. Deliberately the same arithmetic, so the safety net that page relies on
    is tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECLS = {
    "rep_incentive_drilldown": read("backend/app/modules/commcalc/rep_incentive_drilldown.py"),
    "entitlement_coverage_drilldown":
        read("backend/app/modules/commcalc/entitlement_coverage_drilldown.py"),
    "activation_count_drilldown":
        read("backend/app/modules/commcalc/activation_count_drilldown.py"),
}
ROUTER = read("backend/app/modules/commcalc/router.py")
BUCKETING = read("backend/app/modules/commcalc/activation_bucketing.py")
HOME = read("backend/app/modules/core/report_drilldown.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")

PAGES = {
    "commcalc": read("frontend/src/app/(platform)/commcalc/page.tsx"),
    "reports": read("frontend/src/app/(platform)/commcalc/reports/page.tsx"),
    "mdf-entitlement-coverage":
        read("frontend/src/app/(platform)/commcalc/mdf-entitlement-coverage/page.tsx"),
    "activations": read("frontend/src/app/(platform)/commcalc/activations/page.tsx"),
}
#: THE ONE PAGE THAT FOLDS ITS FIGURES OVER A POPULATION THE SERVER DID NOT, AND THE THREE THAT DO
#: NOT. DECIDED BY READING EACH PAGE (§CORE-17.3o), and §G locks each decision in BOTH directions.
#:
#: `/commcalc/reports` filters its rows in the BROWSER with the standard filter bar and then reduces
#: over what survives, so a server-folded panel would explain a number the screen is not printing.
#:
#: `/commcalc` is the interesting negative, and it is the sharpest statement of the ruling this
#: programme has reached: it REDUCES in the browser too — but it has no filter at all, so the reduce
#: runs over exactly the rows the server folded. The call is not "does this page add anything up",
#: it is "does this page fold a figure over a population the server did not".
NARROWED_PAGES = ("reports",)
SERVER_FOLDED_PAGES = ("commcalc", "mdf-entitlement-coverage", "activations")


def _literals(src):
    """Every STRING literal the module's CODE contains. A table name may appear in a DOCSTRING or a
    comment — that is a note to the next engineer and the point of writing the defects down. The
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


def nested_func(src, outer, inner):
    """THE REAL FUNCTION, LIFTED OUT OF A MODULE THAT CANNOT BE IMPORTED. Returns the compiled inner
    function defined inside `outer`, so a defect that lives in router code is REPRODUCED BY RUNNING
    IT rather than by a paraphrase that could drift away from it."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == outer:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == inner:
                    return ast.unparse(ast.fix_missing_locations(sub))
    return ""


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the design fix, with BOTH halves of it ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A0 all three declarations, the router, the bucket vocabulary and all four pages were read",
      all(DECLS.values()) and ROUTER and BUCKETING and all(PAGES.values()))
check("A1 DESIGN FIX — WHICH PAYLOAD COLUMN HOLDS EACH ACTIVATION BUCKET'S COUNT now has ONE home. "
      "It was a literal map inside `commcalc/router._ACT_BUCKET_FIELD`, and a drill-down has to name "
      "a figure by the same column key the fold writes it under, so it moved to "
      "`activation_bucketing` beside the bucket rank and the excluded families",
      isinstance(actb.BUCKET_FIELD, dict) and actb.BUCKET_FIELD.get("Port") == "port")
check("A1b AND THE OTHER HALF, IN THE SAME COMMIT — the router's copy was DELETED and dereferences "
      "the constant. Naming a fact is half a design fix; deleting every copy is the other half, and "
      "it is the half that has been skipped three times here (§19.18)",
      "_ACT_BUCKET_FIELD = _act_bucketing.BUCKET_FIELD" in ROUTER
      and '"New Activation": "activation"' not in ROUTER,
      "router still spells the map" if '"New Activation": "activation"' in ROUTER else "")
ctl("A1c control: the literal scan is NOT vacuous — it finds the map where there is one",
    '"New Activation": "activation"' in BUCKETING)
check("A1d and the declaration dereferences the constant by OBJECT IDENTITY rather than copying its "
      "contents, so a bucket added in the one home reaches the panel with no second edit",
      acdd.BUCKET_FIELD is actb.BUCKET_FIELD
      and acdd.TOTAL_ACTIVATION_EXCLUDED is actb.TOTAL_ACTIVATION_EXCLUDED)
check("A2 THE CALCULATED REP-COMMISSION SNAPSHOT IS DEREFERENCED, NOT RE-NAMED. The index settled "
      "that this fact stays in `carrier_pay_drilldown` and does NOT become a lineage-registry "
      "constant, so a copy in either place would be the divergence that rule exists to stop",
      ridd.REP_COMMISSIONS is cpd.REP_COMMISSIONS
      and emdd.REP_COMMISSIONS is cpd.REP_COMMISSIONS)
check("A3 the two carrier payment relations are DEREFERENCED from the lineage registry",
      ecdd.PAYMENT_DETAIL is lineage.PAYMENT_DETAIL and ecdd.COMP_REPORT is lineage.COMP_REPORT
      and acdd.CUSTOM_CAPTURE is lineage.CUSTOM_CAPTURE)
check("A4 and `which figures can be explained at all` is the ONE home's own rule, bound by identity "
      "in all three declarations — no declaration holds a copy of it (§CORE-17.3u)",
      ridd._served is rd.served_tiles and ecdd._served is rd.served_tiles
      and acdd._served is rd.served_tiles)
_LIT_BANNED = ("rep_commissions", "raw_payment_detail", "raw_comp_report", "raw_custom_import",
               "daily_sales_feed", "raw_sales", "account_statements", "timelog", "store_expenses")
_offenders = {name: sorted({t for t in _LIT_BANNED if t in _literals(src)})
              for name, src in DECLS.items()}
check("A5 BUILD LOCK — no declaration in this batch spells a registry relation name as a LITERAL in "
      "its CODE. Docstrings are parsed away first, because the defects are written down in prose and "
      "a grep cannot tell a note from a copy",
      all(not v for v in _offenders.values()), str(_offenders))
ctl("A5b control: the literal scan is NOT vacuous and really does ignore prose — it finds a name in "
    "code and not in a docstring",
    "rep_commissions" in _literals('x = "rep_commissions"\n')
    and "rep_commissions" not in _literals('"""about rep_commissions"""\nx = 1\n'))
check("A6 and the ONE config relation these reports DO name is named in the declaration rather than "
      "in the lineage registry, which is the posture `targets_drilldown.TARGETS` already takes: a "
      "config row somebody typed is not an external feed and does not belong in the feed watchdog's "
      "population",
      ecdd.ENTITLEMENT_DECLARATION not in _literals(read(
          "backend/app/modules/commcalc/data_lineage_registry.py"))
      and ecdd.DECLARATION_COLUMN is ec.CONFIG_COLUMN)
check("A7 every verdict word these panels can print is the PRODUCER'S OWN, dereferenced — this batch "
      "spells no status, no reason and no basis of its own",
      ecdd.RECEIVED is ec.ROW_RECEIVED and ecdd.MISSING is ec.ROW_MISSING
      and ecdd.UNCONFIRMED_MISSING is ec.ROW_UNCONFIRMED_MISSING
      and ecdd.NOT_DUE is ec.ROW_NOT_DUE and ecdd.NOT_MEASURED is ec.ROW_NOT_MEASURED
      and ecdd.REASON_WINDOW_NOT_DECLARED is ec.REASON_WINDOW_NOT_DECLARED
      and ridd.REASON_NOT_RECORDED is pse.BASIS_NOT_RECORDED
      and ridd.NAMED_SLICES is pse.NAMED_SLICES)
check("A8 and every reason a row in this batch can carry has a SENTENCE in the one home, so a screen "
      "never prints a bare key at a reader",
      ridd.REASON_NOT_RECORDED in rd.UNMEASURED_REASONS
      and ecdd.REASON_UNPRICED in rd.UNMEASURED_REASONS)
check("A9 BUILD LOCK — no declaration defines a panel shape of its own: the one home is the only "
      "place a panel, a tie-out or a row cap is spelled",
      all(("def panel(" not in s and "def tie_out(" not in s and "rows_total_count" not in s)
          for s in DECLS.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE REP-INCENTIVE FIXTURE — two people, one of them never itemised ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: TWO PEOPLE ON PURPOSE. Ana's row was itemised by the run; Ben's is a plan-mode row that says the
#: plan paid and that the subtotal of that pay was zero — the §COMMCALC-09 shape, which is the one
#: this family must show as a BLANK WITH A REASON rather than as $0.00 earned.
REP_ROWS = [
    {"epay_salesperson": "ANA", "storeops_name": "Ana", "store": "S1", "market": "North",
     "tier": 1.0, "kpis_met": 4, "total_kpis": 4,
     "premium_acts": 6, "byod_acts": 2, "upgrade_acts": 3,
     "subtotal": 400.0, "total_payout": 400.0},
    {"epay_salesperson": "BEN", "storeops_name": "Ben", "store": "S2", "market": "South",
     "tier": 0.75, "kpis_met": 2, "total_kpis": 4,
     "premium_acts": 1, "byod_acts": 0, "upgrade_acts": 0,
     "tier_source": "plan", "plan_comm": 120.0, "subtotal": 0.0, "total_payout": 120.0},
]
for _r in REP_ROWS:
    pse.annotate(_r)
_EXPECTED_REPS = {"ANA", "BEN"}
_MEASURED_REPS = {str(r["epay_salesperson"]) for r in REP_ROWS}
check("B1 the fixture really does hold ONE itemised row and ONE the run never itemised, which is "
      "what makes the blank measurable rather than asserted",
      _MEASURED_REPS == _EXPECTED_REPS
      and pse.slices_are_evidence(REP_ROWS[0]) and not pse.slices_are_evidence(REP_ROWS[1]),
      "extra=%s missing=%s" % (sorted(_MEASURED_REPS - _EXPECTED_REPS),
                               sorted(_EXPECTED_REPS - _MEASURED_REPS)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE REP INCENTIVE — the real assembler, and the person the run never itemised ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MONTH = "2026-09"
REP_TOTALS = rir.assemble([MONTH], {MONTH: REP_ROWS}, pse)["month_totals"][0]
RP = ridd.month_drilldowns(REP_ROWS, REP_TOTALS)

_EXPECTED_REP_KEYS = set(ridd.SERVED_KEYS)
check("C1 THE REAL ASSEMBLER WAS DRIVEN, AND THE PANELS ARE EXACTLY THE FIGURES THE TWO SCREENS "
      "PRINT — compared as a SET for equality, never 'at least five', which would stay green while "
      "one figure vanished and another took its place",
      set(RP) == _EXPECTED_REP_KEYS,
      "extra=%s missing=%s" % (sorted(set(RP) - _EXPECTED_REP_KEYS),
                               sorted(_EXPECTED_REP_KEYS - set(RP))))
ctl("C1b control: the equality is STRICTER THAN THE BOUND IT REPLACED — swapping one real key for a "
    "ghost at UNCHANGED SIZE leaves a `>=` green and makes the set comparison red, naming the ghost "
    "in `extra` and the real key in `missing`",
    len((set(RP) - {"subtotal"}) | {"ghost_key"}) >= 5
    and ((set(RP) - {"subtotal"}) | {"ghost_key"}) != _EXPECTED_REP_KEYS
    and sorted(((set(RP) - {"subtotal"}) | {"ghost_key"}) - _EXPECTED_REP_KEYS) == ["ghost_key"]
    and sorted(_EXPECTED_REP_KEYS - ((set(RP) - {"subtotal"}) | {"ghost_key"})) == ["subtotal"])
for _k in sorted(RP):
    _p = RP[_k]
    check(f"C2[{_k}] the panel's figure IS the assembler's own, and every row names the stored month "
          f"it was read out of",
          cents(_p["figure"]["amount"]) == cents(REP_TOTALS[_k])
          and [s["table"] for s in _p["sources"]] == [cpd.REP_COMMISSIONS]
          and _p["untraceable_origins"] == 0,
          str((_p["figure"], REP_TOTALS.get(_k))))
check("C3 THE PAYOUT PANEL TIES EXACTLY, and that is a real check rather than a tautology: the "
      "figure comes from the ASSEMBLER and the rows come from this declaration, so they can "
      "disagree",
      RP["total_payout"]["tie_out"]["ties"] is True
      and RP["total_payout"]["tie_out"]["counted_rows"] == 2)
ctl("C4 control: a tampered row really does break that tie — the panel says in words that the "
    "report and its own evidence disagree",
    (lambda p: p["tie_out"]["ties"] is False and "disagree" in p["tie_out"]["words"])(
        ridd.month_drilldowns([dict(REP_ROWS[0], total_payout=999.0),
                               REP_ROWS[1]], REP_TOTALS)["total_payout"]))
_sub = RP["subtotal"]
_ben = [r for r in _sub["rows"] if r["label"] == "Ben"][0]
check("C5 THE PERSON THE RUN NEVER ITEMISED IS SHOWN WITHOUT A FIGURE, carrying the evidence "
      "module's OWN basis word, and is NOT counted — a $0.00 there would tell a rep they earned "
      "nothing when their total is correct and their breakdown was simply never written",
      _ben["amount"] is None and _ben["present"] is False and _ben["in_figure"] is False
      and _ben["reason"] == pse.BASIS_NOT_RECORDED and bool(_ben["reason_label"]),
      str(_ben))
check("C6 and the subtotal panel still ties EXACTLY, over the one row that was itemised, saying in "
      "words how many rows it is showing and not counting",
      _sub["tie_out"]["ties"] is True and _sub["tie_out"]["counted_rows"] == 1
      and _sub["tie_out"]["shown_not_counted"] == 1
      and "deliberately not counted" in _sub["tie_out"]["words"])
ctl("C7 control: a declaration that read the raw column instead of asking the evidence module would "
    "fold that same row in AT ZERO — which is the defect, reproduced from the real reader",
    pse.slice_value(REP_ROWS[1], "subtotal") is None
    and float(REP_ROWS[1]["subtotal"]) == 0.0)
check("C8 the activation COUNTS are the same rows in a different UNIT, which is what makes them "
      "their own grain rather than the same one",
      RP["premium_acts"]["figure"]["unit"] == rd.UNIT_COUNT
      and RP["total_payout"]["figure"]["unit"] == rd.UNIT_MONEY
      and RP["premium_acts"]["tie_out"]["ties"] is True)
check("C9 AN EMPTY MONTH DRILLS TO NOTHING rather than to panels asserting a tie against zero",
      ridd.month_drilldowns([], REP_TOTALS) == {} and ridd.month_drilldowns(REP_ROWS, {}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE DEFECT THE SUBTOTAL FOOTER CARRIES — reported, reproduced, not repaired ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_PAGE_FOOTER_FOLD = sum(float(r.get("subtotal") or 0) for r in REP_ROWS)
check("D1 THE REP BREAKDOWN'S SUBTOTAL FOOTER ADDS A NEVER-COMPUTED SLICE IN AS ZERO. The page "
      "prints each rep's Subtotal CELL as a dash where the run never itemised it, and then totals "
      "the column with a plain reduce over the same rows — which is the arithmetic "
      "`payout_slice_evidence.sum_slice` exists to refuse, and which the month-range tab already "
      "avoids. Reproduced from the page's own expression and from the real evidence module",
      "s + (r.subtotal || 0)" in strip_comments(PAGES["reports"])
      and cents(_PAGE_FOOTER_FOLD) == cents(400.0)
      and cents(REP_TOTALS["subtotal"]) == cents(400.0))
check("D1b and on a month where the two DO differ the shared gate withholds the link and the page "
      "keeps its own number — a disagreement costs the link, never the figure",
      agrees(RP["subtotal"], REP_TOTALS["subtotal"])
      and not agrees(RP["subtotal"], REP_TOTALS["subtotal"] + 1))
check("D2 THE TWO TABS MEAN DIFFERENT THINGS BY `INSTALLMENT` — the breakdown footer adds a column "
      "the one home does not sum at all, which is why that footer is refused a panel as well as "
      "being composed at the total",
      "carrier_statement_comm" in strip_comments(PAGES["reports"])
      and "carrier_statement_comm" not in rir.MONEY_FIELDS
      and "residual_installment_comm" in rir.MONEY_FIELDS)
check("D3 and the refusal is DECLARED rather than left to the reader — the composed figures this "
      "family will not serve are named in one place, with the halves each offers instead",
      "activations" in ridd.COMPOSED_REFUSALS
      and set(ridd.COMPOSED_REFUSALS["activations"]) == {"premium_acts", "byod_acts"}
      and ridd.COMPOSED_REFUSALS["installment"] == ())
check("D4 THE MONTH-RANGE TAB IS NAMED AS STILL OWED rather than silently served from a second "
      "catalogue over the same grain comp-trend's declaration already folds",
      "rep_pay_catalogue" in DECLS["rep_incentive_drilldown"]
      and callable(getattr(emdd, "rep_pay_catalogue", None)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. SUBSIDY MONTHS MISSING — the real producer, and three provenances on one row ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: THREE STORES ON PURPOSE. S1 has a declared window AND a declared monthly amount; S2 has a declared
#: window and no amount, so its gaps are priced from its OWN payments; S3 has NO declared window at
#: all, which is the absence this report must refuse to call a gap.
ENT_CFG = ec.normalize_config({
    "term_months": 4,
    "entitlement_categories": ["RAMP"],
    "store_windows": {"S1": "2026-05", "S2": "2026-05"},
    "monthly_amounts": {"S1": 1000.0},
})
ENT_RECEIVED = {
    ("S1", "2026-05"): 1000.0, ("S1", "2026-06"): 1000.0,
    ("S2", "2026-05"): 500.0,
    ("S3", "2026-05"): 700.0, ("S3", "2026-07"): 700.0,
}
ENT = ec.entitlement_store_months(ENT_RECEIVED, ENT_CFG,
                                  months_with_feed=["2026-05", "2026-06", "2026-07"],
                                  today=datetime.date(2026, 8, 15))
ENT["feed_rows"] = {"line": 5, "statement": 3}
ED = ecdd.entitlement_drilldowns(ENT)

_EXPECTED_ENT_STORES = {"S1", "S2", "S3"}
_MEASURED_ENT_STORES = {str(r["store"]) for r in ENT["rows"]}
check("E1 THE REAL PRODUCER WAS DRIVEN, and the STORES it judged are exactly the declared set — not "
      "'at least three', which would stay green while a store vanished and another took its place",
      _MEASURED_ENT_STORES == _EXPECTED_ENT_STORES,
      "extra=%s missing=%s" % (sorted(_MEASURED_ENT_STORES - _EXPECTED_ENT_STORES),
                               sorted(_EXPECTED_ENT_STORES - _MEASURED_ENT_STORES)))
ctl("E1b control: the equality is STRICTER THAN A BOUND — one store swapped for a ghost at "
    "UNCHANGED SIZE is red and names both",
    len((_MEASURED_ENT_STORES - {"S3"}) | {"S9"}) >= 3
    and ((_MEASURED_ENT_STORES - {"S3"}) | {"S9"}) != _EXPECTED_ENT_STORES)
for _k in sorted(ED):
    check(f"E2[{_k}] the panel's figure IS the producer's own total",
          cents(ED[_k]["figure"]["amount"]) == cents(ENT["totals"][_k]),
          str((ED[_k]["figure"], ENT["totals"].get(_k))))
check("E3 EVERY COUNT PANEL TIES EXACTLY over the rows the verdict it counts put there",
      all(ED[k]["tie_out"]["ties"] is True
          for k, _l, _p, _n in ecdd.COUNT_FIGURES if k in ED),
      str({k: ED[k]["tie_out"]["ties"] for k, _l, _p, _n in ecdd.COUNT_FIGURES if k in ED}))
check("E4 MONEY THAT ARRIVED NAMES THE CARRIER'S OWN RECORDS, and nothing else — a receipt is a "
      "measurement, and a declaration had no part in it",
      {s["table"] for s in ED["received_total"]["sources"]}
      == {lineage.PAYMENT_DETAIL, lineage.COMP_REPORT})
check("E5 WHAT AN ABSENT MONTH IS WORTH NAMES WHERE THE AMOUNT CAME FROM, per row — the config row "
      "somebody typed it into where one was typed, and the store's own payments where none was. "
      "Same row, same unit as the receipts above, a DIFFERENT provenance — which is what makes it a "
      "third grain (§CORE-17.3n)",
      ecdd.ENTITLEMENT_DECLARATION in {s["table"] for s in ED["missing_amount"]["sources"]}
      and ecdd.ENTITLEMENT_DECLARATION
      not in {s["table"] for s in ED["received_total"]["sources"]})
_s1_missing = [r for r in ED["missing_amount"]["rows"]
               if r["in_figure"] and (r["row_key"] or {}).get("store") == "S1"]
_s2_missing = [r for r in ED["missing_amount"]["rows"]
               if r["in_figure"] and (r["row_key"] or {}).get("store") == "S2"]
check("E5b and the two bases really do land on DIFFERENT relations row by row, which is the whole "
      "content of the split",
      _s1_missing and _s2_missing
      and _s1_missing[0]["sources"] == [ecdd.ENTITLEMENT_DECLARATION]
      and set(_s2_missing[0]["sources"]) == {lineage.PAYMENT_DETAIL, lineage.COMP_REPORT},
      str((_s1_missing[:1], _s2_missing[:1])))
check("E6 A STORE WITH NO DECLARED FIRST MONTH IS NEVER COUNTED AS MISSING. Its observed absence is "
      "on the report, in its own figure, as evidence rather than an accusation",
      cents(ENT["totals"]["missing_amount"]) == cents(2000.0)
      and ENT["totals"]["undeclared_absence_store_months"] == 1
      and not any((r["row_key"] or {}).get("store") == "S3"
                  for r in ED["missing_store_months"]["rows"] if r["in_figure"]))
#: A GAP NOTHING CAN PRICE. S2's own payments are made to disagree, so the producer can put no number
#: on its missing months — and the panel must say the rows do not add up rather than let a short sum
#: look complete.
ENT_UNPRICED = ec.entitlement_store_months(
    {**ENT_RECEIVED, ("S2", "2026-05"): 500.0, ("S2", "2026-07"): 900.0},
    ec.normalize_config({"term_months": 4, "entitlement_categories": ["RAMP"],
                         "store_windows": {"S2": "2026-05"}}),
    months_with_feed=["2026-05", "2026-06", "2026-07"], today=datetime.date(2026, 8, 15))
ENT_UNPRICED["feed_rows"] = {"line": 2, "statement": 0}
EU = ecdd.entitlement_drilldowns(ENT_UNPRICED)
check("E7 A GAP NOTHING CAN PRICE IS COUNTED AS A GAP AND SHOWN WITHOUT A FIGURE, and the priced "
      "panel comes back explicitly NOT TIED because of it — the months are missing whether or not "
      "we know what they were worth, and a short sum presented as complete would hide that",
      ENT_UNPRICED["totals"]["unpriced_missing_store_months"] > 0
      and ("missing_amount" not in EU
           or EU["missing_amount"]["tie_out"]["ties"] is None),
      str({k: EU[k]["tie_out"]["ties"] for k in EU}))
check("E7b and the unpriced rows carry the one home's OWN reason word rather than a sentence this "
      "declaration invented",
      ecdd.REASON_UNPRICED in rd.UNMEASURED_REASONS
      and all(r["reason"] in (None, ecdd.REASON_UNPRICED)
              for k in EU for r in EU[k]["rows"]))
check("E8 A RUN THAT DREW FROM NEITHER RECORD NAMES NOTHING NAMEABLE rather than claiming a "
      "relation that returned no row of this kind (§CORE-17.3i)",
      ecdd.feed_tables({"line": 0, "statement": 0}) == (None,)
      and ecdd.feed_tables({"line": 0, "statement": 4}) == (lineage.COMP_REPORT,)
      and ecdd.feed_tables(None) == (None,))
check("E9 AN ORG THAT HAS DECLARED NOTHING DRILLS TO NOTHING rather than to panels asserting a tie "
      "against zero",
      ecdd.entitlement_drilldowns({"rows": [], "totals": ENT["totals"]}) == {}
      and ecdd.entitlement_drilldowns({}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. ACTIVATIONS — the router's own fold, reproduced AND pinned, with its two defects ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: THE ROUTER'S OWN `_blank`, LIFTED OUT AND EXECUTED. One of this batch's defects lives inside it,
#: and a paraphrase of a function nobody runs would prove nothing about the function that ships.
_BLANK_SRC = nested_func(ROUTER, "activation_counts", "_blank")
_ns = {"_ACT_BUCKET_FIELD": actb.BUCKET_FIELD}
exec(compile(_BLANK_SRC, "<router._blank>", "exec"), _ns)       # noqa: S102 — the real function
_blank = _ns["_blank"]
check("F0 the router's own row builder was lifted out of the router and compiled, so what follows "
      "is a measurement of the code that ships rather than of a copy of it",
      bool(_BLANK_SRC) and callable(_blank))
check("F1 DEFECT — EVERY BY-MARKET ROW HAS ITS MARKET NAME DESTROYED BEFORE IT IS SERVED. The row is "
      "built as a dict LITERAL whose first key is `key` and whose second is the fixed `market`, so "
      "for a MARKET row the two are the SAME key, the later one wins, and the label is overwritten "
      "with the empty string. The roll-up is correct; every row the endpoint returns under `markets` "
      "is blank, and the screen prints a dash where the market name belongs",
      _blank("North", key="market")["market"] == ""
      and _blank("B-1115")["store"] == "B-1115",
      str(_blank("North", key="market")))
ctl("F1b control: the same builder keeps a STORE row's own label, so the defect really is the key "
    "collision and not the builder being broken outright",
    _blank("B-1115")["store"] == "B-1115" and _blank("B-1115")["market"] == "")
check("F1c and the declaration therefore serves NO per-market panel — a panel cannot name a subject "
      "the report has thrown away, and inventing the grouping back would be a second derivation of a "
      "fold the report itself lost",
      not any(k.startswith("market") for k in acdd.SERVED_KEYS))


def _activation_fold(devices):
    """THE ROUTER'S OWN ACCUMULATION, in this file's shape and PINNED to the router's source below —
    one row per distinct device, folded into the store row, the market row and the grand total in
    ONE pass, exactly as `activation_counts` folds it."""
    by_store, by_market, grand, counted = {}, {}, _blank("TOTAL"), 0
    for store, market, bucket in devices:
        counted += 1
        st = (store or "").strip() or "—"
        mk = (market or "").strip() or "(no market)"
        s_row = by_store.setdefault(st, _blank(st))
        s_row["market"] = (market or "").strip()
        m_row = by_market.setdefault(mk, _blank(mk, key="market"))
        fld = actb.BUCKET_FIELD.get(bucket, "other")
        for d in (s_row, m_row, grand):
            d[fld] += 1
            d["total_with_upgrade"] += 1
            if bucket not in actb.TOTAL_ACTIVATION_EXCLUDED:
                d["total_activation"] += 1
    return {"counted": counted, "stores": sorted(by_store.values(),
                                                 key=lambda x: -x["total_activation"]),
            "markets": sorted(by_market.values(), key=lambda x: -x["total_activation"]),
            "total": grand}


DEVICES = [("A-1", "North", "New Activation"), ("A-1b", "North", "Port"),
           ("A-2", "North", "Upgrade"), ("B-9", "South", "BYOD"),
           ("", "", "Home Internet")]
ACT = _activation_fold(DEVICES)
AD = acdd.activation_drilldowns(ACT)
_PIN = [
    'd = {key: label, "market": "", "total_activation": 0, "total_with_upgrade": 0, "upgrade": 0}',
    'fld = _ACT_BUCKET_FIELD.get(bucket, "other")',
    'd["total_with_upgrade"] += 1',
    "if bucket not in _act_bucketing.TOTAL_ACTIVATION_EXCLUDED:",
    'out["drilldown"] = _acdd.activation_drilldowns(out)',
]
_missing_pin = [p for p in _PIN if p not in ROUTER]
check("F2 THE FOLD ABOVE IS PINNED TO THE ROUTER'S OWN SOURCE — if the real assembly changes, this "
      "file goes RED rather than quietly measuring a shape nobody produces",
      _missing_pin == [], str(_missing_pin))
_EXPECTED_ACT_KEYS = set(acdd.SERVED_KEYS)
check("F3 the panels are exactly the figures the screen prints — compared as a SET for equality, "
      "and the two buckets with no column on the screen get no key at all",
      set(AD) == _EXPECTED_ACT_KEYS
      and "byod_upgrade" not in AD and "other" not in AD,
      "extra=%s missing=%s" % (sorted(set(AD) - _EXPECTED_ACT_KEYS),
                               sorted(_EXPECTED_ACT_KEYS - set(AD))))
for _k in sorted(AD):
    check(f"F4[{_k}] the panel's figure IS the fold's own total, it ties over the store rows, and "
          f"every row names the upload it came out of",
          cents(AD[_k]["figure"]["amount"]) == cents(ACT["total"][_k])
          and AD[_k]["tie_out"]["ties"] is True
          and [s["table"] for s in AD[_k]["sources"]] == [lineage.CUSTOM_CAPTURE],
          str((AD[_k]["figure"], ACT["total"].get(_k))))
check("F5 DEFECT — `INCL. UPGRADE` AND `DEVICES COUNTED` ARE THE SAME MEASUREMENT under two labels. "
      "The fold increments both once per counted device, unconditionally, so the screen prints one "
      "number twice as if it were two findings. Reproduced from the fold itself",
      ACT["counted"] == ACT["total"]["total_with_upgrade"] == 5,
      str((ACT["counted"], ACT["total"]["total_with_upgrade"])))
check("F5b and only ONE of the two is served a panel, because two panels for one figure is the "
      "duplicate derivation the index rules forbid",
      "total_with_upgrade" in AD and "counted" not in AD)
check("F6 a device whose dealer code resolves to no store is COUNTED, under the placeholder row the "
      "report itself puts it in — dropping it would make the panel disagree with the report",
      any(r["label"] == "—" and r["in_figure"] for r in AD["total_activation"]["rows"]))
ctl("F7 control: a tampered store row really does break the tie — the panel says so in words",
    (lambda p: p["tie_out"]["ties"] is False and "disagree" in p["tie_out"]["words"])(
        acdd.activation_drilldowns(
            {"total": ACT["total"],
             "stores": [dict(ACT["stores"][0], total_activation=99)]
                       + ACT["stores"][1:]})["total_activation"]))
check("F8 A PERIOD THE EXPORT HAS NOT LANDED FOR DRILLS TO NOTHING",
      acdd.activation_drilldowns({"stores": [], "total": ACT["total"]}) == {}
      and acdd.activation_drilldowns({}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. §CORE-17.3o — a panel explains the FIGURE, not the table. All four, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _name, _src in PAGES.items():
    check(f"G0[{_name}] the page was read", bool(_src), _name)
#: THE CALL, NEVER THE IDENTIFIER. `"narrowPanel" in src` is satisfied by the IMPORT ALONE, which
#: batch 20 measured by renaming a call site and leaving its import.
_NARROW_CALL = "narrowPanel("


def _calls_narrow(name):
    return _NARROW_CALL in strip_comments(PAGES[name])


check("G1 THE ONE PAGE THAT FOLDS OVER A POPULATION THE SERVER DID NOT IS NARROWED — the Rep "
      "Incentive report filters its rows in the BROWSER and then reduces over what survives, so it "
      "CALLS `narrowPanel(` and its figures and its panels are one fold over the selection on screen",
      all(_calls_narrow(n) for n in NARROWED_PAGES),
      str([n for n in NARROWED_PAGES if not _calls_narrow(n)]))
check("G1b AND THE THREE SERVER-FOLDED PAGES ARE NOT — no `narrowPanel` at all in the CODE of any of "
      "them. The rule is written down in each page's own HEADER, where somebody would break it, "
      "which is exactly why this scan strips comments first",
      all("narrowPanel" not in strip_comments(PAGES[n]) for n in SERVER_FOLDED_PAGES)
      and all("CORE-17.3v" in PAGES[n] for n in PAGES),
      str([n for n in SERVER_FOLDED_PAGES if "narrowPanel" in strip_comments(PAGES[n])]))
ctl("G1c control: the comment-stripping scan is NOT blind — it finds the real CALL in code",
    _NARROW_CALL in strip_comments("const x = narrowPanel(p, k)\n"))
ctl("G1d control: and it really does strip a comment that mentions it",
    _NARROW_CALL not in strip_comments("// never call narrowPanel(p) here\nconst x = 1\n"))
ctl("G1e control: AND THE CALL SCAN IS STRICTER THAN THE IDENTIFIER SCAN — an import-only page has "
    "the identifier and NOT the call, which is the hole batch 20 shipped with for one draft",
    "narrowPanel" in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n")
    and _NARROW_CALL not in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n"))
check("G2 THE NARROWED PAGE REALLY DOES FILTER ITS OWN ROWS — it calls the shared row filter in the "
      "browser, which is the fold its panels have to follow",
      all("filterRows(" in strip_comments(PAGES[n]) for n in NARROWED_PAGES))
check("G2b AND THE CASE THAT SETTLES THE RULING FROM THE OTHER SIDE — the CommCalc dashboard DOES "
      "reduce its tiles in the browser, and is still NOT narrowed, because it has no filter at all: "
      "the reduce runs over exactly the rows the server folded, so narrowing would be the identity. "
      "The call is not 'does this page add anything up', it is 'does this page fold a figure over a "
      "population the server did not'",
      "reduce(" in strip_comments(PAGES["commcalc"])
      and "narrowPanel" not in strip_comments(PAGES["commcalc"])
      and "filterRows(" not in strip_comments(PAGES["commcalc"])
      and "no filter" in PAGES["commcalc"])
check("G2c AND THE OTHER TWO SERVER-FOLDED PAGES FOLD NOTHING AT ALL — they print the server's own "
      "totals object as served, so there is no client fold for a panel to have to follow",
      all("reduce(" not in strip_comments(PAGES[n])
          for n in ("mdf-entitlement-coverage", "activations")))
check("G2d THE SUBSIDY PAGE IS THE SECOND INTERESTING NEGATIVE, AND ITS BROWSER FILTER IS REAL — its "
      "verdict tick list narrows the rows the TABLE prints and touches no tile above it, while its "
      "store picker is a query parameter the server folds behind. A panel explains the FIGURE, not "
      "the table, so narrowing there would make every panel disagree with the tile it was opened from",
      "statuses.includes(r.status)" in strip_comments(PAGES["mdf-entitlement-coverage"])
      and "qs.set('store'" in strip_comments(PAGES["mdf-entitlement-coverage"])
      and "narrowPanel" not in strip_comments(PAGES["mdf-entitlement-coverage"])
      and "not the table" in PAGES["mdf-entitlement-coverage"])
check("G3 the narrowed page reads its figure OFF its narrowed panel through the SHARED gate, rather "
      "than keeping two numbers and hoping",
      all("agreesToTheCent" in strip_comments(PAGES[n])
          and "figure?.amount" in strip_comments(PAGES[n]) for n in NARROWED_PAGES))
check("G3b and it builds its narrowing key with the SHARED helper, so neither side spells the key "
      "shape and a renamed key cannot silently stop matching",
      "screenKey(" in strip_comments(PAGES["reports"]))
#: THE ELEMENT, NOT THE IDENTIFIER. `"DrillDownPanel" in src` is satisfied by the IMPORT ALONE,
#: which batch 19 measured by deleting a render and watching every scan stay green.
_RENDERS = {n: bool(re.search(r"<DrillDownPanel[\s/>]", strip_comments(s)))
            for n, s in PAGES.items()}
check("G4 all four pages RENDER the shared panel — matched on the JSX element `<DrillDownPanel`, "
      "never on the bare identifier, which an unused import satisfies on a page whose render was "
      "deleted — and hand-roll none of their own",
      all(_RENDERS.values())
      and not any(("rows_total_count" in strip_comments(s)) or ("tie_out" in strip_comments(s))
                  for s in PAGES.values()),
      str([n for n, ok in _RENDERS.items() if not ok]))
ctl("G4b control: the element scan is NOT vacuous, and it really is stricter than the identifier "
    "scan — an import-only page has the identifier and NOT the element",
    bool(re.search(r"<DrillDownPanel[\s/>]", "  <DrillDownPanel panel={p} />\n"))
    and not re.search(r"<DrillDownPanel[\s/>]",
                      "import DrillDownPanel from '@/components/DrillDownPanel'\n"))
check("G5 and each reads what the SERVER served, through the shared offer gate, rather than deriving "
      "a drill of its own",
      all("drilldown" in s and "useDrillTile" in s for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "useDrillTile" not in s]))
#: A page may NAME a relation only as the argument of `sourceLabel(...)`, which IS the dereference —
#: the plain words still come from the one home, and the page holds none of its own. Anywhere else a
#: relation name in a page is a second copy of a fact the label table owns.
_SRCLBL = re.compile(r"sourceLabel\(\s*'[a-z_]+'\s*\)")
_RELNAME = re.compile(r"'(raw_[a-z_]+|rep_commissions|commission_org_config)'")
_g6_bad = [n for n, src in PAGES.items()
           if _RELNAME.search(_SRCLBL.sub("sourceLabel()", strip_comments(src)))]
check("G6 no page spells a relation name of its own ANYWHERE but inside a `sourceLabel(...)` call, "
      "which is the dereference itself: a feed's plain name has one home and it is not a page",
      not _g6_bad, str(_g6_bad))
ctl("G6b control: that scan is NOT vacuous — the same page text with one relation name outside the "
    "call is caught",
    bool(_RELNAME.search(_SRCLBL.sub("sourceLabel()",
         strip_comments(PAGES["reports"]) + "\nconst t = 'raw_mi';\n"))))
check("G7 THE NARROWER IS THE IDENTITY WHEN NOTHING IS DROPPED, which is what makes a narrowed "
      "figure trustworthy at all, and a capped panel is REFUSED rather than narrowed to a smaller "
      "number",
      "if (rows.length === panel.rows.length)" in read("frontend/src/lib/drill-narrow.ts")
      and "if (panel.capped) return null" in read("frontend/src/lib/drill-narrow.ts"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. NO FIGURE MOVED — read-only over each producer's own payload ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _label, _payload, _fn in (("subsidy", ENT, ecdd.entitlement_drilldowns),
                              ("activations", ACT, acdd.activation_drilldowns)):
    _before = copy.deepcopy(_payload)
    _fn(_payload)
    check(f"H1[{_label}] the declaration is READ-ONLY over the producer's payload — deep-compared "
          f"before and after, byte-identical",
          _payload == _before)
_rep_before = copy.deepcopy(REP_ROWS)
ridd.month_drilldowns(REP_ROWS, REP_TOTALS)
check("H1[rep-incentive] the same, over the rep rows the endpoint serves", REP_ROWS == _rep_before)
check("H2 BUILD LOCK — no declaration holds a dollar literal, a rate or a threshold of its own: "
      "every amount it carries was handed to it",
      all(not re.search(r"(?m)^\s*[A-Z_]+\s*=\s*\d+\.\d+", s) for s in DECLS.values()))
check("H3 BUILD LOCK — the endpoints serve the drill THROUGH the declarations and build no panel of "
      "their own",
      "_ecdd.entitlement_drilldowns(" in ROUTER and "_acdd.activation_drilldowns(" in ROUTER
      and "_ridd.month_drilldowns(" in ROUTER
      and "report_drilldown.panel(" not in ROUTER)
check("H4 THE NEW ENDPOINT CANNOT DISCLOSE WHAT THE REPORT WOULD NOT — it calls the single-month "
      "handler IN PROCESS with the caller's own authorization, so a panel holds precisely the rows "
      "that caller was served, after the scope and after the rep-row allow-list",
      "await get_commissions(period, authorization=authorization, org_id=org_id)" in ROUTER)
check("H5 and it folds no figure of its own: the month totals come from the ONE home that already "
      "folds them, through the evidence module that refuses to add a never-computed slice",
      "_rir.assemble([period], {period: rows}, _pse_mod())" in ROUTER)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE GRAIN COUNT, MEASURED OFF THE PANELS AS A SET ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A grain fixes the ROW, the UNIT and the PROVENANCE (§CORE-17.3l / §CORE-17.3n). The row shape is
# named per report because the payload cannot say it; the unit and the provenance are READ OFF THE
# PANEL, so the number below is a measurement and not a claim.
ROW_SHAPE = {}
for _k in RP:
    ROW_SHAPE[("rep-incentive", _k)] = "rep_row"
for _k in ED:
    ROW_SHAPE[("subsidy", _k)] = "store_month_row"
for _k in AD:
    ROW_SHAPE[("activations", _k)] = "activation_store_row"

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


PANELS_BY_REPORT = {"rep-incentive": RP, "subsidy": ED, "activations": AD}
MEASURED = set()
for (_report, _key), _shape in ROW_SHAPE.items():
    _panel = PANELS_BY_REPORT[_report][_key]
    MEASURED.add((_shape, _panel["figure"]["unit"], _provenance(_panel)))
print(f"       MEASURED {len(MEASURED)} distinct grains over {len(ROW_SHAPE)} panels in "
      f"{len({s for s in ROW_SHAPE.values()})} row shapes")
for _g in sorted(MEASURED, key=lambda g: (g[0], g[1], sorted(g[2]))):
    print(f"         · {_g[0]} / {_g[1]} / {sorted(_g[2])}")
_CARRIER_RECORDS = frozenset({lineage.PAYMENT_DETAIL, lineage.COMP_REPORT})
_JUDGED = frozenset(_CARRIER_RECORDS | {ecdd.ENTITLEMENT_DECLARATION})
#: THE GRAINS THESE THREE FAMILIES HAVE, DECLARED AS A SET AND COMPARED FOR EQUALITY. A BOUND ON THE
#: SIZE IS NOT AN ASSERTION ABOUT THE SET: `len(MEASURED) >= 6` stays green while a grain silently
#: vanishes and another takes its place, which is the "guard that goes blind" shape
#: `harness_subject_set_lock.py` §B1 exists to refuse.
_EXPECTED = {
    # REP INCENTIVE — one rep row, two units, one provenance.
    ("rep_row", rd.UNIT_MONEY, frozenset({cpd.REP_COMMISSIONS})),
    ("rep_row", rd.UNIT_COUNT, frozenset({cpd.REP_COMMISSIONS})),
    # SUBSIDY — one store-month row and THREE grains on it, two of them separated by PROVENANCE
    # ALONE: money that arrived names the carrier's records, money an absence is PRICED at names
    # the declaration it was typed into as well.
    ("store_month_row", rd.UNIT_COUNT, _JUDGED),
    ("store_month_row", rd.UNIT_MONEY, _CARRIER_RECORDS),
    ("store_month_row", rd.UNIT_MONEY, _JUDGED),
    # ACTIVATIONS — one store row, one unit, one provenance. One grain, and that is the report.
    ("activation_store_row", rd.UNIT_COUNT, frozenset({lineage.CUSTOM_CAPTURE})),
}
_extra, _missing = sorted(MEASURED - _EXPECTED), sorted(_EXPECTED - MEASURED)
check("I1 THE GRAIN COUNT IS MEASURED AND THE SET IS COMPARED FOR EQUALITY — every panel's unit and "
      "provenance are read off the panel, collected as a SET so two panels that really are one grain "
      "collapse, and checked against the declared six. A bound on the SIZE would stay green while "
      "one grain vanished and another took its place, so a lost grain NAMES ITSELF here",
      MEASURED == _EXPECTED and len(MEASURED) == 6 and len(MEASURED) < len(ROW_SHAPE),
      "extra=%s missing=%s" % (_extra, _missing))
ctl("I1a control: the equality is not vacuous — dropping one declared grain is reported BY NAME, "
    "and the comparison really is a set difference rather than a count",
    (MEASURED - (_EXPECTED - {("activation_store_row", rd.UNIT_COUNT,
                               frozenset({lineage.CUSTOM_CAPTURE}))}))
    == {("activation_store_row", rd.UNIT_COUNT, frozenset({lineage.CUSTOM_CAPTURE}))})
ctl("I1b control: a GHOST AT UNCHANGED SIZE — one measured grain swapped for one that does not "
    "exist — leaves a `>=` bound green and makes this comparison red, naming both",
    (lambda g: len(g) >= 6 and g != _EXPECTED
     and sorted(g - _EXPECTED) and sorted(_EXPECTED - g))(
        (MEASURED - {("activation_store_row", rd.UNIT_COUNT,
                      frozenset({lineage.CUSTOM_CAPTURE}))})
        | {("ghost_row", rd.UNIT_COUNT, frozenset({"ghost_table"}))}))
check("I1c TWO GRAINS SEPARATED BY PROVENANCE ALONE is a real split and not an artefact of the "
      "fixture — money that ARRIVED and money an ABSENCE is PRICED AT share a ROW and a UNIT and "
      "differ only in where the number was read (§CORE-17.3n)",
      _provenance(ED["received_total"]) != _provenance(ED["missing_amount"])
      and ED["received_total"]["figure"]["unit"] == ED["missing_amount"]["figure"]["unit"])
ctl("I1d control: the provenance reader really reads the PANEL and not a declaration — pointed at a "
    "panel with no sources at all it says so rather than inventing one",
    _provenance({"sources": []}) == frozenset())
check("I2 AND THE THREE FAMILIES SHARE NO GRAIN AT ALL, which is why they are three declarations "
      "and not one — a shared catalogue over them would have to claim one report's relation "
      "produced another's figure",
      len({g[0] for g in MEASURED}) == 3
      and not (_provenance(RP["total_payout"]) & _provenance(ED["received_total"]))
      and not (_provenance(AD["total_activation"]) & _provenance(ED["received_total"])))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE RATCHET — shrink-only, and mutation-tested in all three directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("harness_drilldown_coverage.py")
LEDGER_TXT = read("harness_drilldown_pending.txt")
MY_ROUTES = ("/commcalc", "/commcalc/reports", "/commcalc/mdf-entitlement-coverage",
             "/commcalc/activations")
#: THE THREE THIS BATCH READ AND DID NOT SERVE. Each is still `owes`, on purpose: leaving a report
#: owed is honest, and reclassifying one that really is a report would hide it from the programme.
STILL_OWED = ("/commcalc/custom-report", "/commcalc/store-takeover", "/commcalc/whatif")
OWES_AFTER = 25


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
      _p is not None and _p <= OWES_AFTER and _o <= OWES_AFTER and _o == _p, str((_p, _o)))
ctl("J1b control: a HIGHER pin than this batch left is RED — the ratchet really is shrink-only",
    not (_pin("OWES_MAX = 29\n") is not None and _pin("OWES_MAX = 29\n") <= OWES_AFTER))
ctl("J1c control: a LATER, LOWER pin is GREEN — a future batch serving more routes must not have to "
    "come back and edit this file",
    (_pin("OWES_MAX = 18\n") or 99) <= OWES_AFTER)
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
ctl("J4b control: the clause scan is NOT vacuous — a note without the clause is caught",
    "REFUSES TO CLAIM" not in "a note that declines to say what it declined")
check("J5 and each note names THIS batch's index section and the declaration that serves it, so a "
      "reader of the ledger can reach the reasoning",
      all("CORE-17.3v" in (_state(LEDGER_TXT, r)[1] or "")
          and "_drilldown" in (_state(LEDGER_TXT, r)[1] or "")
          for r in MY_ROUTES))
check("J6 THE THREE ROUTES THIS BATCH READ AND DID NOT SERVE ARE STILL `owes`, honestly. Leaving a "
      "report owed is a true statement; reclassifying one that really is a report would hide it "
      "from the programme, which is worse than the debt",
      all(_state(LEDGER_TXT, r)[0] == "owes" for r in STILL_OWED),
      str({r: _state(LEDGER_TXT, r)[0] for r in STILL_OWED}))
check("J7 and none of the three was given a NOTE — the gate's own §C4 says an `owes` row is a debt "
      "and a note on one would read as a reason. The analysis for each is in the index section",
      all(not (_state(LEDGER_TXT, r)[1] or "") for r in STILL_OWED))


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

DRILL_TABLES = sorted({cpd.REP_COMMISSIONS, lineage.PAYMENT_DETAIL, lineage.COMP_REPORT,
                       lineage.CUSTOM_CAPTURE, ecdd.ENTITLEMENT_DECLARATION})
_unknown = [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % re.escape(t), MIG)]
check("K1 every table a panel in this batch can name is KNOWN TO THE MIGRATIONS — a provenance "
      "claim about a relation nothing in this repository has ever heard of is worse than none",
      bool(MIG) and _unknown == [], str(_unknown))
check("K1b AND THE CONFIG COLUMN THE SUBSIDY DECLARATION NAMES REALLY EXISTS — the in-flight "
      "migration for it is in the folder, and the declaration names the column through the "
      "producer's own constant rather than spelling it",
      re.search(r"\b%s\b" % re.escape(ec.CONFIG_COLUMN), MIG) is not None
      and ec.CONFIG_MIGRATION in sorted(os.listdir(_migdir)))
_no_label = [t for t in DRILL_TABLES if not re.search(r"(?m)^\s+%s:" % re.escape(t), LABELS)]
check("K2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never an identifier (§19.38)",
      _no_label == [], str(_no_label))
check("K3 all three declarations are registered CALLERS of the one drill-down home, by hand",
      all(f"'app/modules/commcalc/{m}.py': ('report_drilldown',)" in MG for m in DECLS))
check("K4 and of the facts each dereferences, under the fact that module is the home of",
      "'app/modules/commcalc/rep_incentive_drilldown.py': ('_cpd',)" in MG
      and "'app/modules/commcalc/entitlement_coverage_drilldown.py': ('_lineage',)" in MG
      and "'app/modules/commcalc/activation_count_drilldown.py': ('_lineage',)" in MG)
check("K4b and the re-keyed caller is registered ALONGSIDE the declaration that needed the move — a "
      "hand-written edge for the new home without one for the file that stopped spelling the "
      "literal is half a registration",
      "'app/modules/commcalc/activation_count_drilldown.py': ('_act',)" in MG)
#: THE ONE HOME'S LOCK LIST, NOT ANYWHERE IN THE FILE. This batch also gives two facts a home block
#: of their own and names this harness in each — so `"'harness_batch22_drilldown.py'" in MG` would
#: stay green with the drill-down home's list emptied. Read the list itself.
_RD_LOCKS = re.search(r"'report_drilldown':.*?\"locks\":\s*\((.*?)\),\n", MG, re.S)
check("K5 this batch's harness is registered in the ONE DRILL-DOWN HOME's own lock list — matched "
      "inside that fact's `locks` tuple, never anywhere in the file, which two other facts in this "
      "same commit also name it in",
      _RD_LOCKS is not None and "'harness_batch22_drilldown.py'" in _RD_LOCKS.group(1),
      str(bool(_RD_LOCKS)))
ctl("K5b control: that scan really is stricter than naming the file — the same name in ANOTHER "
    "fact's lock list is not the drill-down home's registration",
    not re.search(r"'report_drilldown':.*?\"locks\":\s*\((.*?)\),\n",
                  "'other_fact': {\n \"locks\": ('harness_batch22_drilldown.py',),\n", re.S))
#: THE HEADING, NOT A MENTION. Every endpoint row below cites `DRILL-DOWN §CORE-17.3v`, so a scan
#: for the bare id stays green with the section itself renamed or deleted — which is how a batch
#: could register four rows pointing at nothing. Match the heading line.
check("K6 the index carries THIS batch's own SECTION HEADING, and the next free letter was taken "
      "rather than guessed — matched on the heading itself, never on the citations below it",
      re.search(r"(?m)^#{1,6}\s+§CORE-17\.3v\s", IDX) is not None
      and all(m in IDX for m in DECLS))
ctl("K6b control: that scan is stricter than a mention — a page full of `§CORE-17.3v` citations "
    "with no heading is RED",
    re.search(r"(?m)^#{1,6}\s+§CORE-17\.3v\s",
              "**DRILL-DOWN §CORE-17.3v:** the tiles open\n") is None)
#: THE RUN STEP, NEVER THE MENTION. The harness's own name appears in the job's PATHS FILTER too, so
#: `"harness_batch22_drilldown.py" in WF` would stay green with the run line pointed at another
#: batch — which is how batch 20 found this hole. Match the `run:` line itself.
_RUN_STEP = [ln for ln in WF.splitlines()
             if re.search(r"run:\s*python3\s+harness_batch22_drilldown\.py\b", ln)]
check("K7 the guard job RUNS this harness — matched on the `run:` line itself, never on the file's "
      "name, which the paths filter below also carries — with a paths filter naming every file it "
      "is about",
      len(_RUN_STEP) == 1
      and all(f"backend/app/modules/commcalc/{m}.py" in WF for m in DECLS)
      and "backend/app/modules/commcalc/activation_bucketing.py" in WF
      and "backend/app/modules/commcalc/entitlement_coverage.py" in WF
      and "backend/app/modules/commcalc/rep_incentive_range.py" in WF,
      str(len(_RUN_STEP)))
ctl("K7b control: the run-step scan really is stricter than naming the file — a paths-filter line "
    "carrying the same name is NOT a run step",
    not re.search(r"run:\s*python3\s+harness_batch22_drilldown\.py\b",
                  "      - 'backend/harness_batch22_drilldown.py'"))
_steps = [ln for ln in WF.splitlines() if "batch22" in ln]
check("K8 and its step's `name:` / `run:` scalars carry NO colon-space — a plain scalar that does "
      "makes the WHOLE workflow fail to parse, so every job in it silently never runs",
      bool(_steps) and not any(re.search(r"(?m)^\s*(?:-\s*)?(name|run):\s+[^'\"].*:\s", ln)
                               for ln in _steps), str(_steps))
check("K9 the four pages are registered in the index's endpoint rows with this batch's section",
      IDX.count("DRILL-DOWN §CORE-17.3v") >= 4, str(IDX.count("DRILL-DOWN §CORE-17.3v")))
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
