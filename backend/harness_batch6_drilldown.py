"""PROOF — the close recon's accusations and the DM-verify chargebacks name their evidence.

DB-free, stdlib only, driving the real `closing/recon_drilldown` and `closing/ops_chargeback_drilldown`
declarations through the shared fold (`core/report_drilldown.tiles`). Batch 6 of the §CORE-17 audit:
/closing/recon and the chargeback strip on /closing/verify.

WHAT THIS BATCH IS ABOUT, AND WHY IT IS DIFFERENT FROM THE EARLIER ONES.

1. THESE FIGURES ARE ACCUSATIONS, NOT MEASUREMENTS. A recon row says *this rep declared $400 and the
   feed says $380, so the close is blocked*; a chargeback row says *$150 is coming out of this
   district manager's commission*. A figure that costs a named person money has to be able to show
   which days it is counting and where each side of the comparison came from. §A, §C.

2. A COUNT'S PANEL IS THE ACCUSATIONS THEMSELVES. The recon's four figures are counts of rows, so a
   drill is not "the rows that sum to it" but "the accusations it is making" — and the rows it does
   NOT count stay in the panel as the context that makes the count readable. §A5, §Z4.

3. THE PENDING ROW IS THE ONE THAT MATTERS MOST. It is an accusation the report REFUSES to make: the
   feed was not loaded, or the rep could not be matched, so no comparison happened. It therefore
   names no feed, carries no variance and states its reason — never a $0.00 variance, which would
   read as "we checked and it agreed". §B, §Z2.

4. THE RULE THAT MADE IT A BLOCK IS NOT COPIED HERE. `router._money_issues` is the one home for
   "cash short blocks, cash over flags; credit over blocks, credit under flags" and it writes its
   verdict onto the row. The declaration SHOWS that verdict; it does not restate the thresholds,
   because a second copy could drift from the one actually applied. §E5, §E6.

5. THE RECON'S TILES WERE READING THE WHOLE MONTH WHILE THE TABLE READ THE FILTER. Pre-existing and
   never written down: the four counts came from the server's summary, the rows below them were
   filtered on the client. They are narrowed now (§CORE-17.3d) — and the severity filter is
   deliberately NOT part of the selection, or clicking "Blocks" would make every tile read the
   blocked subset. §F.

  python3 backend/harness_batch6_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import ops_chargeback_drilldown as ocd                 # noqa: E402
from app.modules.closing import recon_drilldown as rcd                          # noqa: E402
from app.modules.core import report_drilldown as rd                             # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
    """A file's CODE, with strings and comments stripped — a section number in a docstring is prose,
    a float in an expression is a threshold, and only the second is what §E forbids."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _body(fn_name, src):
    """One endpoint function's own source, bounded at the next top-level def."""
    i = src.index(fn_name)
    j = src.find("\ndef ", i + len(fn_name))
    k = src.find("\n@router.", i + len(fn_name))
    ends = [x for x in (j, k) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


_rt = open(os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py"),
           encoding="utf-8").read()


# ── Fixtures ────────────────────────────────────────────────────────────────────────────────────
# Three accusations and one refusal to accuse, which is every shape /closing/recon produces.
RECON = [
    {"date": "2026-09-01", "store_code": "B-1", "store_address": "1 Main St", "rep": "Abid",
     "metric": "cash", "declared": 400.00, "b2b": 380.00, "variance": -20.00,
     "severity": "block", "status": "block", "reason": "Cash short $20.00 vs POS sales"},
    {"date": "2026-09-01", "store_code": "B-2", "store_address": "2 Oak Ave", "rep": "Sam",
     "metric": "credit", "declared": 100.00, "b2b": 105.00, "variance": -5.00,
     "severity": "flag", "status": "flag", "reason": "Credit under $5.00 vs POS"},
    # A store-level COUNT mismatch: no rep, so the row is addressed without one.
    {"date": "2026-09-01", "store_code": "B-2", "store_address": "2 Oak Ave", "rep": None,
     "metric": "activations", "declared": 7, "b2b": 6, "variance": 1,
     "severity": "flag", "status": "flag", "reason": "Activations count mismatch"},
    # THE REFUSAL TO ACCUSE — the feed was not loaded, or the rep was not matched.
    {"date": "2026-09-02", "store_code": "B-3", "store_address": "3 Elm Rd", "rep": "Rita",
     "metric": "recon", "declared": 50.00, "b2b": None, "variance": None,
     "severity": "pending", "status": "recon_pending", "reason": "POS sales not loaded"},
    # Beyond the recompute cap: never queried this request, which is a different blank again.
    {"date": "2026-08-01", "store_code": "B-4", "store_address": "4 Pine Ct", "rep": "Ana",
     "metric": "recon", "declared": 10.00, "b2b": None, "variance": None,
     "severity": "pending", "status": "not_computed", "reason": "Not recomputed this request"},
]
RECON_SUMMARY = {"blocks": 1, "flags": 2, "pending": 2, "total": 5}
RC = rcd.drilldowns(RECON, RECON_SUMMARY, sales_tables=("daily_sales_feed",))

CHARGEBACKS = [
    {"id": "a1", "store_code": "B-1", "incident_date": "2026-09-01", "employee_name": "Rita",
     "amount": 150.00, "status": "pending"},
    {"id": "a2", "store_code": "B-2", "incident_date": "2026-09-02", "employee_name": "Rita",
     "amount": 150.00, "status": "posted", "posted_ref": "2026-09", "decided_by": "owner"},
    {"id": "a3", "store_code": "B-3", "incident_date": "2026-09-03", "employee_name": "Sam",
     "amount": 150.00, "status": "waived", "decided_by": "owner"},
]
CB_TOTALS = {"pending": 150.00, "posted": 150.00, "waived": 150.00, "to_be_foregone": 300.00}
CB = ocd.drilldowns(CHARGEBACKS, CB_TOTALS)


print("== A. a count of accusations opens to the accusations ==")
check("A1 every recon figure is served", sorted(RC) == ["blocks", "flags", "pending", "total"],
      sorted(RC))
check("A2 and every one of them ties to the rows served under it",
      [k for k, v in RC.items() if v["tie_out"]["ties"] is False] == [])
check("A3 the figures are declared as COUNTS, not as dollars — they count rows, and printing them "
      "with a dollar sign would read as money",
      all(v["figure"]["unit"] == rd.UNIT_COUNT for v in RC.values()))
check("A4 an accusation is addressed by its day, store, person AND figure, because one rep-day can "
      "be blocked on cash and flagged on credit at once — two accusations, not one",
      {k for v in RC.values() for r in v["rows"] for k in (r.get("row_key") or {})}
      <= {"date", "store_code", "rep", "metric"}
      and any(sorted(r["row_key"]) == ["date", "metric", "rep", "store_code"]
              for r in RC["blocks"]["rows"] if r.get("row_key")))
check("A5 the rows a count does NOT count stay in the panel as context — that is the difference "
      "between a drill-down and a filtered list",
      len(RC["blocks"]["rows"]) == len(RECON)
      and len([r for r in RC["blocks"]["rows"] if r["in_figure"]]) == 1)
check("A6 a store-level count row is addressed WITHOUT a rep rather than with an empty one",
      any(r.get("row_key") and "rep" not in r["row_key"] for r in RC["total"]["rows"]))
check("A7 every row names the store, the day, the person and the figure in words",
      all(r["label"] and "·" in r["label"] for r in RC["total"]["rows"]),
      [r["label"] for r in RC["total"]["rows"]])


print("\n== B. provenance: where each side of the accusation came from ==")
_blk = RC["blocks"]["rows"][0]
check("B1 the declared figure names the closing row it was filed on",
      "daily_closing" in _blk["sources"])
check("B2 and the counterpart names the sales-pair side the read ACTUALLY answered from, "
      "dereferenced from `_b2b_day` (§CORE-17.3c) — never a constant",
      "daily_sales_feed" in _blk["sources"])
_arch = rcd.drilldowns(RECON, RECON_SUMMARY, sales_tables=("raw_sales",))
check("B3 and names the ARCHIVE when that is the side that answered",
      "raw_sales" in _arch["blocks"]["rows"][0]["sources"])
_pend = [r for r in RC["pending"]["rows"] if r["in_figure"]]
check("B4 a PENDING row names NO feed — nothing answered, and claiming one would be a provenance "
      "claim nothing checked",
      len(_pend) == 2 and all(r["sources"] == ["daily_closing"] for r in _pend),
      [r["sources"] for r in _pend])
check("B5 that missing side is reported as an untraceable origin, so the gap is COUNTED rather than "
      "being an empty list that reads as a clean answer",
      RC["pending"]["untraceable_origins"] == 2, RC["pending"]["untraceable_origins"])
check("B6 and each pending row says WHY, in a sentence, distinguishing a feed that was not loaded "
      "from a day this request never recomputed",
      {r["reason"] for r in _pend} == {"no_sales_feed_match", "not_computed"}
      and all(r["reason_label"] for r in _pend),
      [(r["reason"], r["reason_label"]) for r in _pend])
check("B7 a pending row carries NO variance — a $0.00 variance would read as 'we checked and it "
      "agreed'",
      all(r["fields"]["counterpart"] is None for r in _pend))


print("\n== C. the chargeback strip: money out of a named person's pay ==")
check("C1 every chargeback figure is served and ties",
      sorted(CB) == ["pending", "posted", "to_be_foregone", "waived"]
      and [k for k, v in CB.items() if v["tie_out"]["ties"] is False] == [])
check("C2 'to be foregone' is pending + posted, folded from the rows rather than composed from two "
      "other tiles — a tile composed from two tiles has no rows of its own",
      CB["to_be_foregone"]["figure"]["amount"] == 300.00
      and len([r for r in CB["to_be_foregone"]["rows"] if r["in_figure"]]) == 2)
check("C3 a WAIVED chargeback is forgiven: counted in neither, and still shown",
      CB["to_be_foregone"]["figure"]["amount"] == 300.00
      and [r["fields"]["employee_name"]
           for r in CB["waived"]["rows"] if r["in_figure"]] == ["Sam"]
      and len(CB["to_be_foregone"]["rows"]) == 3)
check("C4 the statuses 'to be foregone' covers are declared ONCE and read off that tuple",
      ocd.FOREGONE_STATUSES == ("pending", "posted"))
check("C5 a row names the store, the day and the PERSON — a figure that costs someone money must "
      "say whose days it is counting",
      all(r["fields"]["employee_name"] for r in CB["pending"]["rows"]))
check("C6 and it names the one table the amount and the decision actually live on, not the closing "
      "tables a reader would look in and not find it",
      {t for v in CB.values() for r in v["rows"] for t in r["sources"]} == {"ops_chargeback"})
check("C7 a decided chargeback shows who decided it and the period it was taken from",
      any(r["fields"]["decided_by"] == "owner" and r["fields"]["period"] == "2026-09"
          for r in CB["posted"]["rows"]))


print("\n== Z. negative controls — the checks above must be capable of failing ==")
_z1 = rcd.drilldowns(RECON, dict(RECON_SUMMARY, blocks=3), sales_tables=("daily_sales_feed",))
check("Z1 a count that disagrees with its own rows comes back ties=False, in words",
      _z1["blocks"]["tie_out"]["ties"] is False
      and "disagree" in _z1["blocks"]["tie_out"]["words"])
_z2 = rcd.drilldowns([dict(RECON[0], severity="pending", b2b=None)],
                     {"pending": 1}, sales_tables=("daily_sales_feed",))
check("Z2 a row the report calls pending never claims the feed, whatever else is on it",
      _z2["pending"]["rows"][0]["sources"] == ["daily_closing"]
      and _z2["pending"]["untraceable_origins"] == 1)
_z3 = rcd.drilldowns(RECON, {k: v for k, v in RECON_SUMMARY.items() if k != "flags"},
                     sales_tables=("daily_sales_feed",))
check("Z3 a figure the report did not produce is ABSENT, not served as 0 behind nothing",
      "flags" not in _z3 and "blocks" in _z3)
_z4 = rcd.drilldowns(RECON, RECON_SUMMARY, sales_tables=())
check("Z4 an UNRESOLVED sales feed reads as untraceable on EVERY row that needed it, while the "
      "declared leg stays traceable — one absent feed is not a blank row",
      _z4["blocks"]["untraceable_origins"] == len(RECON)
      and all(r["sources"] == ["daily_closing"] for r in _z4["blocks"]["rows"]),
      (_z4["blocks"]["untraceable_origins"],
       [r["sources"] for r in _z4["blocks"]["rows"]]))
_z5 = ocd.drilldowns([dict(CHARGEBACKS[2], amount=0.00)], {"waived": 0.00})
check("Z5 a chargeback OF ZERO is a measured zero and is counted — the exclusion is read off the "
      "status, never off whether the amount is truthy",
      _z5["waived"]["rows"][0]["in_figure"] is True
      and _z5["waived"]["rows"][0]["measured"] is True)
_z6 = ocd.drilldowns([CHARGEBACKS[2]], dict(CB_TOTALS, to_be_foregone=150.00))
check("Z6 and a waived chargeback counted into 'to be foregone' is caught, rather than quietly "
      "charging someone for a forgiven incident",
      _z6["to_be_foregone"]["tie_out"]["ties"] is False)


print("\n== E. build locks — the mechanism cannot un-wire ==")
for _mod, _path in (("recon_drilldown", "app/modules/closing/recon_drilldown.py"),
                    ("ops_chargeback_drilldown",
                     "app/modules/closing/ops_chargeback_drilldown.py")):
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
    check("E1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel", "def tie_out")))
    check("E2(%s) it holds no tolerance, threshold or dollar of its own — the rule that made a row "
          "a block lives in `_money_issues`, and a copy here could drift from it" % _mod,
          not re.findall(r"\d+\.\d+", _c), re.findall(r"\d+\.\d+", _c))
    check("E3(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("E4(%s) and it reads NOTHING from the database" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase")))
_rc_body = _body("def closing_recon(", _rt)
check("E5 the recon endpoint builds its summary ONCE and serves THAT dict both to the screen and "
      "to the fold, so a count and its drill-down cannot disagree",
      _rc_body.count("_summary = ") == 1
      and '"summary": _summary,' in _rc_body
      and "_recon_drilldown.drilldowns(errors, _summary," in _rc_body)
check("E6 and the severity/reason the rows carry come from `_money_issues`, which stays the one "
      "home for the close rules",
      "def _money_issues(" in _rt
      and 'severity": "block"' not in open(
          os.path.join(ROOT, "backend", "app", "modules", "closing", "recon_drilldown.py"),
          encoding="utf-8").read())
check("E7 the sales-pair side that answered is dereferenced from the day read itself, not guessed "
      "at the call site",
      '"sales_tables": _sales_tables' in _rt
      and "_b2b_sales_rows_sourced(" in _body("def _b2b_day(", _rt)
      and "_recon_sales_tables = tuple(sorted({t for d in day_cache.values()" in _rc_body)
_cb_body = _body("def get_missed_dm_verifies(", _rt)
check("E8 the chargeback endpoint folds its totals once, through the module that owns them",
      _cb_body.count("_cb_totals = ") == 1
      and "ops_chargebacks.totals(rows)" in _cb_body
      and "_ops_chargeback_drilldown.drilldowns(rows, _cb_totals)" in _cb_body)
_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"),
               encoding="utf-8").read()
_named = {t for p in (RC, CB, _arch) for v in p.values() for r in v["rows"]
          for t in r["sources"] if t}
_unlabelled = sorted(t for t in _named if ("  %s:" % t) not in _labels)
check("E9 every table these drills can name has a PLAIN-WORDS label — a manager never sees a table "
      "name (§19.38, owner: 'hide database names from the users')",
      _unlabelled == [], _unlabelled)
check("E10 every reason these declarations can emit has a SENTENCE in the one home for those words",
      all(k in rd.UNMEASURED_REASONS for k in ("no_sales_feed_match", "not_computed")))

_recon_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing", "recon",
                                "page.tsx"), encoding="utf-8").read()
_verify = open(os.path.join(ROOT, "frontend", "src", "components", "DailyClosingVerify.tsx"),
               encoding="utf-8").read()
for _n, _pg in (("closing/recon", _recon_page), ("DailyClosingVerify", _verify)):
    check("E11(%s) the screen renders the SHARED panel and hand-rolls none of it" % _n,
          "DrillDownPanel" in _pg and "tie_out" not in _pg)
    check("E12(%s) and offers each figure's link through the SHARED offer rule, never a local copy "
          "of the loop" % _n,
          "useDrillTile(" in _pg and "dl(" in _pg
          and "const dl = (...pairs: string[]) => {" not in _pg)
    check("E13(%s) a link inside a clickable control stops the click reaching it — opening the "
          "evidence must not also change what the screen is showing" % _n,
          "ev.stopPropagation()" in _pg)


print("\n== F. the recon's counts follow the screen's own filter ==")
check("F1 the counts are narrowed to this selection through the one home, not read off the "
      "server's whole-month summary",
      "narrowPanels(drills, (_r, k) => selected.has(k))" in _recon_page
      and "from '@/lib/drill-narrow'" in _recon_page
      and "data?.summary" not in _recon_page)
check("F2 each count is read OFF its narrowed panel, with the client count surviving only as the "
      "fallback for a figure no panel was served for",
      "panels[k] ? (panels[k].figure?.amount ?? 0) : fallback()" in _recon_page)
check("F3 the SEVERITY filter is deliberately NOT part of the selection — the four tiles ARE that "
      "filter, so including it would make every tile read its own subset",
      "const shown = base.filter(e => filter === 'all' || e.severity === filter)" in _recon_page
      and "if (filter !== 'all' && e.severity !== filter) return false" not in _recon_page)
check("F4 the selection key is the one the declaration addresses rows by",
      "screenKey({" in _recon_page and rcd.ROW_KEYS == ("date", "store_code", "rep", "metric")
      and all(k in _recon_page for k in ("date:", "store_code:", "rep:", "metric:")))
check("F5 a count the fallback computed says on screen why it cannot be opened",
      "unpanelled" in _recon_page)
check("F6 the chargeback strip does NOT narrow and does not need to: every filter on it is a query "
      "parameter, so the server's fold covers exactly the incidents listed under it",
      "drill-narrow" not in _verify and "query parameter" in _verify)


print("\n== G. registration ==")
_idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("G1 both declarations are registered in the index (owner directive 2026-09-01)",
      "recon_drilldown" in _idx and "ops_chargeback_drilldown" in _idx)
check("G2 so is this harness", "harness_batch6_drilldown" in _idx)
_ledger = open("harness_drilldown_pending.txt", encoding="utf-8").read()
for _r in ("/closing/recon", "/closing/verify"):
    check("G3(%s) the coverage ledger no longer says this report owes a drill-down" % _r,
          re.search(r"^%s\tshared" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
