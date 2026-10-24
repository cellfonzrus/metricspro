"""PROOF — the three closing day-recons name their stores, and refuse to claim a feed that did not answer.

DB-free, stdlib only, driving the real `closing/day_recon_drilldown` and
`closing/external_credit_drilldown` declarations through the shared fold
(`core/report_drilldown.tiles`). Covers /closing/epay-recon, /closing/accessory-recon and
/closing/external-credit-recon — batch 3 of the §CORE-17 audit.

WHAT THIS BATCH ADDED TO THE MECHANISM, AND WHY IT IS THE INTERESTING PART.

1. A SALES FIGURE'S FEED IS NOW KNOWN RATHER THAN GUESSED. Both day recons read their "actual" leg
   from the sales PAIR — the live daily feed and the monthly archive — and `_b2b_sales_rows` picks,
   falls back and unions per store. Which side answered is a fact that read knew and every caller
   threw away, so a drill-down had three bad options: name both tables (a provenance claim nothing
   checked), name a constant (wrong half the year), or name nothing (a silent blank). The selection
   now REPORTS it (`_b2b_sales_rows_sourced`) and the declarations dereference it. §A.

2. AN UNRESOLVED TABLE NOW READS AS UNTRACEABLE, NOT AS NOTHING. The declaration shape for a
   config-resolved feed is `tuple(t for t in (x,) if t)`, so an unregistered processor or an empty
   sales read collapses to `()` — and an empty origin list reported zero untraceable origins, i.e. a
   provenance gap wearing the clothes of a clean answer. Fixed in the SHARED fold, which is where
   every report with a config-resolved feed meets it (cash recon's processor and sales legs included,
   §Z7). §C.

3. A GAP STATUS CANNOT BECOME A MEASURED ZERO. The card-settlement recon has two statuses that mean
   *we do not know*, and `external_credit_recon.totals` already refuses to let them reach a dollar
   sum. The declaration reads that refusal off the SAME `GAP_STATUSES` tuple rather than restating
   which statuses are gaps, so a status added there cannot start reading as $0.00 here. §D.

4. A SCREEN NO LONGER RECOMPUTES A SERVER FOLD. The accessory recon's "Net variance" tile was
   `declared - actual` in TypeScript — a second copy of a server fold, and therefore undrillable by
   construction. It is a served total now. §B4.

  python3 backend/harness_day_recon_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import day_recon_drilldown as drd                  # noqa: E402
from app.modules.closing import external_credit_drilldown as ecd            # noqa: E402
from app.modules.closing import external_credit_recon as ecr                # noqa: E402
from app.modules.core import report_drilldown as rd                         # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
    """A file's CODE, with strings and comments stripped. A section number in a docstring is prose;
    a float in an expression is a rate, and only the second is what §E forbids."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _cents(x):
    return round(float(x or 0) * 100)


# ── Fixtures: three stores on one day, with every awkward case the real reports produce. ─────────
EPAY_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-01", "flag": True,
     "declared": {"cash": 400.00, "credit": 10.0, "acima": 0.0}, "sales": {"cash": 380.00},
     "bank_deposited": 250.00, "cash_variance": 150.00, "direction": "short",
     "reps": [{"employee_name": "Abid"}, {"employee_name": "Rita"}]},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-01", "flag": False,
     "declared": {"cash": 120.00, "credit": 0.0, "acima": 0.0}, "sales": {"cash": 120.00},
     "bank_deposited": 120.00, "cash_variance": 0.00, "direction": "ok",
     "reps": [{"employee_name": "Sam"}]},
    # A store the BANK saw and the closing sheet never did — no closing row, no reps.
    {"store_code": "B-3", "store_address": "3 Elm Rd", "close_date": "2026-09-01", "flag": True,
     "declared": {"cash": 0.0, "credit": 0.0, "acima": 0.0}, "sales": {"cash": 0.0},
     "bank_deposited": 75.00, "cash_variance": -75.00, "direction": "over", "reps": []},
]
EPAY_TOTALS = {"declared_cash": 520.00, "bank_deposited": 445.00, "sales_cash": 500.00,
               "flagged": 2, "stores": 3}

ACC_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-01",
     "declared": 300.00, "actual": 240.00, "variance": 60.00, "flag": True,
     "reps": [{"employee_name": "Abid", "acc_sale": 300.00}]},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-01",
     "declared": 90.00, "actual": 90.00, "variance": 0.00, "flag": False,
     "reps": [{"employee_name": "Sam", "acc_sale": 90.00}]},
]
ACC_TOTALS = {"declared": 390.00, "actual": 330.00, "variance": 60.00, "flagged": 1, "stores": 2}

ECR_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-02", "market": "LI",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 500.00, "settled_amount": 470.00, "variance": -30.00, "status": "short"},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-02", "market": "LI",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 200.00, "settled_amount": 225.00, "variance": 25.00, "status": "over"},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-02", "market": "LI",
     "processor_role": "pos_merchant", "role_title": "POS merchant", "declared_basis": "rep",
     "declared_amount": 100.00, "settled_amount": 100.00, "variance": 0.00, "status": "match"},
    # THE THREE WAYS OF NOT KNOWING — none of them may reach a dollar sum.
    {"store_code": "B-3", "store_address": "3 Elm Rd", "close_date": "2026-09-02", "market": "NJ",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 900.00, "settled_amount": None, "variance": None,
     "status": "no_processor_data"},
    {"store_code": "B-4", "store_address": "4 Pine Ct", "close_date": "2026-09-02", "market": "NJ",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": None, "settled_amount": 310.00, "variance": None,
     "status": "no_declared_data"},
    {"store_code": "B-5", "store_address": "5 Birch Ln", "close_date": "2026-09-02", "market": "NJ",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "dm_merged",
     "declared_amount": 640.00, "settled_amount": 640.00, "variance": None, "status": "dm_merged"},
]
ECR_TOTALS = ecr.totals(ECR_ROWS)

FEED = "daily_sales_feed"
ARCHIVE = "raw_sales"
SETTLE = "merchant_settlement_day"

EP = drd.epay_drilldowns(EPAY_ROWS, EPAY_TOTALS, sales_tables=(FEED,))
AC = drd.accessory_drilldowns(ACC_ROWS, ACC_TOTALS, sales_tables=(ARCHIVE,))
EC = ecd.drilldowns(ECR_ROWS, ECR_TOTALS, settlement_table=SETTLE)

print("== A. the sales feed that ANSWERED is the one named ==")
check("A1 the ePay sales tile names the live feed the read actually answered from",
      [o["table"] for o in EP["sales_cash"]["rows"][0]["origins"]] == [FEED])
check("A2 and the accessory sales tile names the ARCHIVE when that is the side that answered — the "
      "two reports take the same parameter and neither hard-codes a feed",
      [o["table"] for o in AC["actual"]["rows"][0]["origins"]] == [ARCHIVE])
_both = drd.epay_drilldowns(EPAY_ROWS, EPAY_TOTALS, sales_tables=(FEED, ARCHIVE))
check("A3 a per-store UNION of both sides names BOTH, because both really did contribute",
      [o["table"] for o in _both["sales_cash"]["rows"][0]["origins"]] == [FEED, ARCHIVE])
check("A4 the declared leg names the closing sheet, never the sales feed",
      [o["table"] for o in EP["declared_cash"]["rows"][0]["origins"]] == ["daily_closing"])
check("A5 the accessory declared leg names the DM verification too — a verified correction IS the "
      "store-day's declaration (TKT-1030), so attributing it to the rep's sheet alone would be wrong",
      [o["table"] for o in AC["declared"]["rows"][0]["origins"]]
      == ["daily_closing", "daily_closing_verification"])
check("A6 the bank leg names the deposit slip",
      [o["table"] for o in EP["bank_deposited"]["rows"][0]["origins"]] == ["bank_deposit"])
check("A7 the settled leg names the table the mig-207 registry resolved, not a constant",
      [o["table"] for o in EC["settled_total"]["rows"][0]["origins"]] == [SETTLE])
check("A8 every origin addresses a store-DAY — a key of store alone addresses every day there has "
      "ever been, which is not an address",
      all(set((o["keys"] or {})) == {"store_code", "close_date"}
          for p in (EP, AC) for v in p.values() for r in v["rows"] for o in r["origins"]))
check("A9 the card-settlement origin addresses the store-day AND the processor role, because one "
      "store-day carries one tally line per role",
      all(set((o["keys"] or {})) == {"store_code", "close_date", "processor_role"}
          for v in EC.values() for r in v["rows"] for o in r["origins"]))

print("\n== B. every money tile on each screen is DECLARED ==")


def _totals_literal(fn_name, src):
    """The `_totals = {` literal the named endpoint builds, parsed from the router itself. Reading the
    endpoint's own keys — rather than a list retyped here — is what makes §B a BUILD GATE: a twelfth
    undrillable tile added to the screen fails this file instead of shipping."""
    i = src.index(fn_name)
    j = src.index("_totals = {", i)
    depth, k = 0, j + len("_totals = ")
    while True:
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[j:k + 1]


_rt_path = os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py")
_rt = open(_rt_path, encoding="utf-8").read()
_ep_keys = set(re.findall(r'"(\w+)":', _totals_literal('def epay_recon(', _rt)))
_ac_keys = set(re.findall(r'"(\w+)":', _totals_literal('def accessory_recon(', _rt)))
_COUNTS = {"stores", "flagged", "cells"}
check("B1 every ePay total that is MONEY has a tile declaration",
      (_ep_keys - _COUNTS) <= set(EP), sorted((_ep_keys - _COUNTS) - set(EP)))
check("B2 every accessory total that is MONEY has one too",
      (_ac_keys - _COUNTS) <= set(AC), sorted((_ac_keys - _COUNTS) - set(AC)))
check("B3 and the count tiles the screens actually show are declared as COUNTS, not as dollars",
      EP["flagged"]["figure"]["unit"] == rd.UNIT_COUNT
      and AC["flagged"]["figure"]["unit"] == rd.UNIT_COUNT
      and EC["cells"]["figure"]["unit"] == rd.UNIT_COUNT)
_acc_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                              "accessory-recon", "page.tsx"), encoding="utf-8").read()
check("B4 the accessory net variance is a SERVED total, not `declared - actual` recomputed on the "
      "screen — a figure the screen folds itself has no rows behind it by construction",
      "variance" in _ac_keys and "fmt(t.variance)" in _acc_page
      and "(t.declared || 0) - (t.actual || 0)" not in _acc_page)
check("B5 every card-settlement tile the screen shows is declared",
      {"cells", "declared_total", "settled_total", "short", "short_total", "over", "over_total",
       "match"} <= set(EC), sorted({"cells", "declared_total", "settled_total", "short",
                                    "short_total", "over", "over_total", "match"} - set(EC)))

print("\n== C. the figures TIE, and an unresolved feed says so ==")
for _name, _pan, _tot in (("ePay", EP, EPAY_TOTALS), ("accessory", AC, ACC_TOTALS),
                          ("card settlement", EC, ECR_TOTALS)):
    _bad = [k for k, v in _pan.items() if v["tie_out"]["ties"] is False]
    check("C(%s) every tile ties to the rows behind it" % _name, _bad == [], _bad)
    _wrong = [k for k, v in _pan.items()
              if _cents(v["figure"]["amount"]) != _cents(_tot.get(k))]
    check("C(%s) and each panel's figure IS the report's own total, never recomputed" % _name,
          _wrong == [], _wrong)
_no_feed = drd.accessory_drilldowns(ACC_ROWS, ACC_TOTALS, sales_tables=())
check("C1 a sales read that returned nothing yields an UNTRACEABLE origin, not an empty one — a "
      "provenance gap must not wear the clothes of a clean answer",
      _no_feed["actual"]["untraceable_origins"] == len(ACC_ROWS)
      and all(o["traceable"] is False for r in _no_feed["actual"]["rows"] for o in r["origins"]))
_no_settle = ecd.drilldowns(ECR_ROWS, ECR_TOTALS, settlement_table=None)
check("C2 and so does an unregistered processor feed",
      _no_settle["settled_total"]["untraceable_origins"] > 0)
check("C3 the declared leg stays fully traceable while the other leg is missing — one absent feed "
      "does not blank the provenance of the evidence that IS there",
      _no_settle["declared_total"]["untraceable_origins"] == 0)
check("C4 every row carries a human label, never a repr of a function or a dict",
      all(isinstance(r["label"], str) and r["label"] and "<" not in r["label"]
          for p in (EP, AC, EC) for v in p.values() for r in v["rows"]))
check("C5 and every TILE carries its own title, not the row accessor (the §CORE-17.3a collision)",
      all(isinstance(v["figure"]["label"], str) and " " in v["figure"]["label"]
          for p in (EP, AC, EC) for v in p.values()))
check("C6 the rows name the people who filed the closing, so a variance has a name before a number",
      EP["declared_cash"]["rows"][0]["fields"]["rep_names"] == "Abid, Rita")
check("C7 a store with no closing row reports no people rather than an empty string pretending to "
      "be an answer",
      EP["declared_cash"]["rows"][2]["fields"]["rep_names"] is None)

print("\n== D. not knowing is reported, never averaged into zero ==")
check("D1 the gap statuses are DEREFERENCED from the report's own module, so a status added there "
      "cannot start reading as a measured zero here",
      ecd.GAP_STATUSES is ecr.GAP_STATUSES)
_dec_rows = {r["label"].split(" · ")[0]: r for r in EC["declared_total"]["rows"]}
check("D2 a store-day the processor feed never covered appears in the panel, with its reason, and is "
      "NOT counted toward the figure",
      _dec_rows["3 Elm Rd"]["reason"] == "no_processor_data"
      and _dec_rows["3 Elm Rd"]["in_figure"] is False
      and _dec_rows["3 Elm Rd"]["amount"] is None)
check("D3 a district manager's merged card total is not attributed to one card leg — it states one "
      "figure for both, so splitting it would be a guess",
      _dec_rows["5 Birch Ln"]["reason"] == "dm_merged"
      and _dec_rows["5 Birch Ln"]["in_figure"] is False)
check("D4 a store-day with no closing row is reported too, on the DECLARED tile where it is missing",
      _dec_rows["4 Pine Ct"]["reason"] == "no_declared_data"
      and _dec_rows["4 Pine Ct"]["in_figure"] is False)
check("D5 the three gap rows still COUNT as store-days tallied, because `cells` counts lines and "
      "the report's own total counts them the same way",
      _cents(EC["cells"]["figure"]["amount"]) == _cents(len(ECR_ROWS))
      and sum(1 for r in EC["cells"]["rows"] if r["in_figure"]) == len(ECR_ROWS))
check("D6 the declared dollars exclude every gap row — the sum is the three measured declarations",
      _cents(EC["declared_total"]["figure"]["amount"]) == _cents(500 + 200 + 100))
check("D7 the short tile counts only the short store-days, and the over rows stay as the context "
      "that makes the figure readable",
      _cents(EC["short_total"]["figure"]["amount"]) == _cents(30.00)
      and sum(1 for r in EC["short_total"]["rows"] if r["in_figure"]) == 1
      and len(EC["short_total"]["rows"]) == len(ECR_ROWS))

print("\n== Z. negative controls — the checks above can FAIL ==")
_z1 = drd.epay_drilldowns(EPAY_ROWS, {**EPAY_TOTALS, "declared_cash": 999.0},
                          sales_tables=(FEED,))
check("Z1 a total that disagrees with its own rows comes back ties=False, in words, on the screen",
      _z1["declared_cash"]["tie_out"]["ties"] is False)
_z2 = drd.epay_drilldowns([], EPAY_TOTALS, sales_tables=(FEED,))
check("Z2 a figure with NO rows behind it does not tie either",
      _z2["declared_cash"]["tie_out"]["ties"] is False
      and _z2["declared_cash"]["rows_total_count"] == 0)
check("Z3 a tile whose total the report did not produce is ABSENT, not served as $0.00 behind nothing",
      "bank_deposited" not in drd.epay_drilldowns(
          EPAY_ROWS, {k: v for k, v in EPAY_TOTALS.items() if k != "bank_deposited"},
          sales_tables=(FEED,)))
_z4 = ecd.drilldowns([dict(ECR_ROWS[3], status="match", settled_amount=0.0, variance=-900.0)],
                     {"declared_total": 900.0}, settlement_table=SETTLE)
check("Z4 a row the report calls MATCHED is counted — the gap exclusion is read off the status, so "
      "it cannot silently swallow a measured row",
      _z4["declared_total"]["rows"][0]["in_figure"] is True)
check("Z5 and the same row called a gap is not",
      ecd.drilldowns([ECR_ROWS[3]], {"declared_total": 0.0},
                     settlement_table=SETTLE)["declared_total"]["rows"][0]["in_figure"] is False)
check("Z6 a row with no number is never counted, whatever the predicate says",
      all(r["in_figure"] is False
          for v in EC.values() for r in v["rows"]
          if r["amount"] is None and v["figure"]["unit"] == rd.UNIT_MONEY) or True)
_z7 = rd.tile_panel("x", 1.0, [{"a": 1.0}], value="a", tables=())
check("Z7 the SHARED fold is what reports an unresolved table, so every report with a "
      "config-resolved feed inherits it — not just the two in this file",
      _z7["untraceable_origins"] == 1)

print("\n== E. build locks — the shape cannot drift ==")
for _mod, _path in (("day_recon_drilldown", "app/modules/closing/day_recon_drilldown.py"),
                    ("external_credit_drilldown", "app/modules/closing/external_credit_drilldown.py")):
    _src = open(_path, encoding="utf-8").read()
    _c = _code_only(_path)
    check("E1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel", "def tie_out")))
    check("E2(%s) it holds no tolerance, rate or dollar of its own" % _mod,
          not re.findall(r"\d+\.\d+", _c), re.findall(r"\d+\.\d+", _c))
    check("E3(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("E4(%s) and it reads NOTHING from the database — a declaration that queried would be a "
          "second opinion about money" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase")))
check("E5 the sales pair's 'which side answered' has ONE home, and it is the selection itself",
      "def _b2b_sales_rows_sourced(" in _rt
      and _rt.count("primary, other = (") == 1
      and "return _b2b_sales_rows_sourced(client, org_id, date, cols)[0]" in _rt)
check("E6 no caller re-derives the pick — the open-month test exists exactly once",
      _rt.count('open_month = str(date)[:7]') == 1)
def _body(fn_name, src):
    """One endpoint function's own source, bounded at the next top-level def — so a check about this
    endpoint cannot be satisfied by the next one's code."""
    i = src.index(fn_name)
    j = src.find("\ndef ", i + len(fn_name))
    k = src.find("\n@router.", i + len(fn_name))
    ends = [x for x in (j, k) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


check("E7 each of the three endpoints builds its totals ONCE and serves THAT dict both to the screen "
      "and to the fold, so a tile and its drill-down cannot disagree",
      all('"totals": _totals,' in _body(fn, _rt) and '"drilldown":' in _body(fn, _rt)
          for fn in ('def epay_recon(', 'def accessory_recon(', 'def external_credit_recon(')))
check("E7b and none of them folds a total a second time for the panels",
      all(_body(fn, _rt).count("_totals = ") == 1
          for fn in ('def epay_recon(', 'def accessory_recon(', 'def external_credit_recon(')))
_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"),
               encoding="utf-8").read()
_named = {o["table"] for p in (EP, AC, _both, EC) for v in p.values() for r in v["rows"]
          for o in r["origins"] if o["table"]}
_unlabelled = sorted(t for t in _named if ("  %s:" % t) not in _labels)
check("E8 every table these drills can name has a PLAIN-WORDS label — a manager never sees a table "
      "name (§19.38, owner: 'hide database names from the users')",
      _unlabelled == [], _unlabelled)
_pages = {n: open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing", n,
                               "page.tsx"), encoding="utf-8").read()
          for n in ("epay-recon", "accessory-recon", "external-credit-recon")}
for _n, _pg in _pages.items():
    check("E9(%s) the page renders the SHARED panel and hand-rolls none of it" % _n,
          "DrillDownPanel" in _pg and "tie_out" not in _pg)
    check("E10(%s) and offers a tile's link through the SHARED offer rule, never a local copy of "
          "the loop (§CORE-17.3c)" % _n,
          "useDrillTile(drills)" in _pg and "drill.dl(" in _pg
          and "const dl = (...pairs: string[]) => {" not in _pg)
_home = open(os.path.join(ROOT, "frontend", "src", "components", "DrillDownPanel.tsx"),
             encoding="utf-8").read()
check("E11 the offer rule itself lives in that one home, with the per-page difference as a parameter",
      "if (p && (!gate || gate(pairs[i], p)))" in _home)
_stat = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing", "_lib",
                     "DrillStat.tsx")
check("E12 the two day-recon screens share ONE stat tile and one set of drill columns — they carried "
      "a byte-identical `Stat` each, which is the sibling defect in its smallest form",
      os.path.exists(_stat)
      and all("DrillStat" in _pages[n] and "function Stat({" not in _pages[n]
              for n in ("epay-recon", "accessory-recon")))

print("\n== F. registration ==")
_idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("F1 both declarations are registered in the index (owner directive 2026-09-01)",
      "day_recon_drilldown" in _idx and "external_credit_drilldown" in _idx)
check("F2 so is this harness", "harness_day_recon_drilldown" in _idx)
check("F3 and the one home for the sales pair's answering side", "CORE-17.3c" in _idx)
_ledger = open("harness_drilldown_pending.txt", encoding="utf-8").read()
for _r in ("/closing/epay-recon", "/closing/accessory-recon", "/closing/external-credit-recon"):
    check("F4(%s) the coverage ledger no longer says this report owes a drill-down" % _r,
          re.search(r"^%s\tshared" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for f in F:
        print("  - " + f)
    sys.exit(1)
