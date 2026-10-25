"""PROOF — the 3-way tender recon's selection strip and the cash-pickup footers name their rows.

DB-free, stdlib only, driving the real `closing/three_way_drilldown` and `closing/pickup_drilldown`
declarations through the shared fold (`core/report_drilldown.tiles`). Batch 5 of the §CORE-17 audit:
/closing/tender-recon-3way and /closing/pickup — the two reports §CORE-17.3d was blocking.

WHAT THIS BATCH SETTLED, AND WHY IT IS THE INTERESTING PART.

1. THE 3-WAY STRIP IS THE THIRD INSTANCE OF THE CLIENT-FOLDED-TILE CLASS, AND IS FIXED THE WAY THE
   CLASS IS FIXED. Eleven figures were reduced in TypeScript over client-filtered store-days while
   the server knew nothing of them. The server now folds them ONCE (`three_way_drilldown.totals`)
   over a store-day list flattened ONCE (`flat_rows`), the screen narrows the served panels to its own
   selection with the shared `drill-narrow` home, and each tile reads its number off its narrowed
   panel. §A, §B, §E.

2. THE BILL-PAY PICKUP IS THE SAME MECHANISM AND IS FIXED IN THE SAME BREATH. /closing/pickup and
   /closing/billpay-pickup are one process wearing two screens (owner directive 2026-09-02, "the same
   process same wiring") with the same envelope fields. So the declaration is EXTENDED with a second
   catalogue rather than copied into a sibling file, and the two screens share one stat tile and one
   set of drill columns. Fixing one and not the other is the defect the house rules name. §H.

3. /closing/pickup WAS LISTED IN THAT CLASS AND IS NOT IN IT. §CORE-17.3b named it on the strength of
   its own comment — "footer totals over the envelopes CURRENTLY ON SCREEN". Every filter on that page
   is a QUERY PARAMETER, so the server already returns exactly those envelopes and its fold covers
   exactly them: the screen needs the plain tile shape, not narrowing. Checking a claim rather than
   inheriting it is what kept this report simple. §F2, §F3.

4. GROSS IS NOT NETTED, AND BOTH ARE DRILLABLE IN THEIR OWN RIGHT. `gross_cx`/`gross_xs` sum
   |difference| per store-day so offsetting errors cannot hide; `net_cx`/`net_xs` are folded per row
   rather than subtracted from two other tiles, which is what makes them openable at all. §B4, §Z3.

5. A COUNTED FIGURE STILL REFUSES TO COVER AN UNCOUNTED ENVELOPE. The pickup's "actually picked"
   figures sum only the collected envelopes that carry a count — folding in a declared amount would
   manufacture agreement and reading an uncounted envelope as $0.00 would manufacture a 100%
   shortfall. The declaration reads that refusal off the same fields the endpoint does, and the
   excluded envelopes appear in the panel with their reason rather than vanishing. §C, §Z5, §Z6.

  python3 backend/harness_batch5_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import pickup_drilldown as pud                     # noqa: E402
from app.modules.closing import three_way_drilldown as twd                  # noqa: E402
from app.modules.core import report_drilldown as rd                         # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
    """A file's CODE, with strings and comments stripped — a section number in a docstring is prose,
    a float in an expression is a rate, and only the second is what §E forbids."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _body(fn_name, src):
    """One endpoint function's own source, bounded at the next top-level def — so a check about this
    endpoint cannot be satisfied by the next one's code."""
    i = src.index(fn_name)
    j = src.find("\ndef ", i + len(fn_name))
    k = src.find("\n@router.", i + len(fn_name))
    ends = [x for x in (j, k) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


def _totals_literal(fn_name, src):
    """The `_totals = {` literal the named endpoint builds, parsed from the router itself. Reading the
    endpoint's OWN keys — rather than a list retyped here — is what makes §B a build gate: a twelfth
    undrillable figure added to the screen fails this file instead of shipping."""
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


# ── Fixtures ────────────────────────────────────────────────────────────────────────────────────
# The 3-way payload shape: one block per calendar date, each with its stores. Two stores over two
# days, with a mismatch, an unmapped label, and a store the X-report never covered.
DAYS = [
    {"date": "2026-09-01", "stores": [
        {"store_code": "B-1", "store_address": "1 Main St",
         "totals": {"closing": 400.00, "x_report": 410.00, "sales": 405.00},
         "tenders": [{"tender": "cash", "match": False}, {"tender": "card", "match": True}],
         "x_report_unmapped": {"amount": 5.00, "raw_labels": ["GIFTCARD"]},
         "sales_unmapped": None},
        {"store_code": "B-2", "store_address": "2 Oak Ave",
         "totals": {"closing": 100.00, "x_report": 100.00, "sales": 100.00},
         "tenders": [{"tender": "cash", "match": True}],
         "x_report_unmapped": None, "sales_unmapped": None},
    ]},
    {"date": "2026-09-02", "stores": [
        # A store-day the X-report never covered — its legs are a real zero against a real 90.
        {"store_code": "B-2", "store_address": "2 Oak Ave",
         "totals": {"closing": 90.00, "x_report": 0.00, "sales": 90.00},
         "tenders": [{"tender": "cash", "match": False}],
         "x_report_unmapped": None, "sales_unmapped": {"amount": 1.50, "raw_labels": ["OTHER"]}},
    ]},
]

PICKUPS = [
    # Collected AND counted — short by $20.
    {"store_code": "B-1", "store_name": "1 Main St", "close_date": "2026-09-01",
     "employee_name": "Abid", "cash": 400.00, "cash_sales": 380.00, "cash_gross": 420.00,
     "picked_up": True, "actual_picked_amount": 380.00, "deposit_flagged": True},
    # Collected with NO count recorded — may never read as $0.00 nor as its declared amount.
    {"store_code": "B-2", "store_name": "2 Oak Ave", "close_date": "2026-09-01",
     "employee_name": "Sam", "cash": 120.00, "cash_sales": 120.00, "cash_gross": None,
     "picked_up": True, "actual_picked_amount": None, "deposit_flagged": False},
    # Still waiting.
    {"store_code": "B-3", "store_name": "3 Elm Rd", "close_date": "2026-09-01",
     "employee_name": "Rita", "cash": 75.00, "cash_sales": 70.00, "cash_gross": 80.00,
     "picked_up": False, "actual_picked_amount": None, "deposit_flagged": False},
]
PICKUP_TOTALS = {
    "ready": 1, "collected": 2, "flagged": 1,
    "total_cash": 595.00, "collected_cash": 520.00, "ready_cash": 75.00,
    "collected_actual": 380.00, "collected_actual_envelopes": 1, "collected_actual_missing": 1,
    "collected_actual_variance": -20.00,
    "total_cash_sales": 570.00, "total_cash_gross": 620.00,
}

FLAT = twd.flat_rows(DAYS)
TOT = twd.totals(FLAT)
TW = twd.drilldowns(FLAT, TOT, sales_tables=("daily_sales_feed",))
PU = pud.drilldowns(PICKUPS, PICKUP_TOTALS)

# The bill-pay mirror: the same envelope on the other side of the same physical cash. A bill payment
# taken ON CARD is a display row with no cash to collect, and must never read as "waiting".
BILLPAY = [
    {"store_code": "B-1", "store_name": "1 Main St", "close_date": "2026-09-01",
     "employee_name": "Abid", "cash": 100.00, "credit": 25.00,
     "picked_up": True, "actual_picked_amount": 100.00, "deposit_flagged": False},
    {"store_code": "B-2", "store_name": "2 Oak Ave", "close_date": "2026-09-01",
     "employee_name": "Sam", "cash": 0.00, "credit": 40.00,
     "picked_up": False, "actual_picked_amount": None, "deposit_flagged": False},
    {"store_code": "B-3", "store_name": "3 Elm Rd", "close_date": "2026-09-01",
     "employee_name": "Rita", "cash": 60.00, "credit": 0.00,
     "picked_up": False, "actual_picked_amount": None, "deposit_flagged": True},
]
BILLPAY_TOTALS = {"ready": 1, "collected": 1, "flagged": 1, "total_cash": 160.00,
                  "total_credit": 65.00, "collected_cash": 100.00, "ready_cash": 60.00}
BP = pud.billpay_drilldowns(BILLPAY, BILLPAY_TOTALS)


print("== A. the 3-way strip is folded ONCE, over a list flattened ONCE ==")
check("A1 the flattening yields one row per store-DAY, not one per day or one per store",
      [(r["store_code"], r["close_date"]) for r in FLAT]
      == [("B-1", "2026-09-01"), ("B-2", "2026-09-01"), ("B-2", "2026-09-02")])
check("A2 the three legs total what the store-days hold, to the cent",
      (TOT["closing"], TOT["x_report"], TOT["sales"]) == (590.00, 510.00, 595.00),
      (TOT["closing"], TOT["x_report"], TOT["sales"]))
check("A3 GROSS is not netted — each store-day's difference counts at its absolute size, so a store "
      "over and another short cannot cancel",
      TOT["gross_cx"] == 10.00 + 0.00 + 90.00 and TOT["net_cx"] == -80.00,
      (TOT["gross_cx"], TOT["net_cx"]))
check("A4 and the net figures are folded per row, not subtracted from two other tiles — which is "
      "what makes them openable at all",
      TOT["net_cx"] == round(TOT["x_report"] - TOT["closing"], 2)
      and TOT["net_xs"] == round(TOT["sales"] - TOT["x_report"], 2)
      and "net_cx" in TW and "net_xs" in TW)
check("A5 the mismatched store-days are COUNTED as store-days, not as tender lines",
      TOT["mismatch_store_days"] == 2 and TOT["store_days"] == 3)
check("A6 every row addresses a store-DAY — a key of store alone addresses every day that store has "
      "ever had",
      all(tuple(sorted((r.get("row_key") or {}))) == ("close_date", "store_code")
          for v in TW.values() for r in v["rows"] if r.get("row_key")))
check("A7 the closing leg names the closing sheet and the X-report leg names the X-report — never "
      "each other, and never the sales feed",
      {o["table"] for r in TW["closing"]["rows"] for o in r["origins"]} == {"daily_closing"}
      and {o["table"] for r in TW["x_report"]["rows"] for o in r["origins"]}
      == {"pos_tender_summary"})
check("A8 the sales leg names the pair side the read actually answered from, dereferenced — never a "
      "constant (§CORE-17.3c)",
      {o["table"] for r in TW["sales"]["rows"] for o in r["origins"]} == {"daily_sales_feed"})
_arch = twd.drilldowns(FLAT, TOT, sales_tables=("raw_sales",))
check("A9 and names the ARCHIVE when that is the side that answered",
      {o["table"] for r in _arch["sales"]["rows"] for o in r["origins"]} == {"raw_sales"})
check("A10 a comparison tile names BOTH legs it compares, because both really did contribute",
      {o["table"] for r in TW["gross_cx"]["rows"] for o in r["origins"]}
      == {"pos_tender_summary", "daily_closing"})
check("A11 every row carries a human label naming the store and the day, never a repr of a function",
      all(re.match(r"^.+ · \d{4}-\d{2}-\d{2}$", r["label"]) for r in TW["closing"]["rows"]),
      [r["label"] for r in TW["closing"]["rows"]])


print("\n== B. every figure on each screen is DECLARED ==")
_tw_keys = set(re.findall(r'"(\w+)":', _code_only(
    os.path.join(ROOT, "backend", "app", "modules", "closing", "three_way_drilldown.py"))))
_pu_keys = set(re.findall(r'"(\w+)":', _totals_literal('def closing_pickups(', _rt)))
_TW_COUNTS = {"store_days"}
check("B1 every 3-way selection total that is a FIGURE has a tile declaration — `store_days` is the "
      "row count the strip prints as a denominator, declared beside the tile it qualifies",
      (set(TOT) - _TW_COUNTS) <= set(TW), sorted((set(TOT) - _TW_COUNTS) - set(TW)))
check("B2 every cash-pickup total the endpoint builds has one too — read off the endpoint's own "
      "literal, so a twelfth undrillable footer fails this file instead of shipping",
      _pu_keys <= set(PU), sorted(_pu_keys - set(PU)))
check("B3 the count figures are declared as COUNTS, not as dollars",
      TW["mismatch_store_days"]["figure"]["unit"] == rd.UNIT_COUNT
      and all(PU[k]["figure"]["unit"] == rd.UNIT_COUNT
              for k in ("ready", "collected", "flagged", "collected_actual_envelopes",
                        "collected_actual_missing")))
check("B4 and the money figures as money",
      all(TW[k]["figure"]["unit"] == rd.UNIT_MONEY
          for k in ("closing", "x_report", "sales", "net_cx", "gross_cx"))
      and all(PU[k]["figure"]["unit"] == rd.UNIT_MONEY
              for k in ("total_cash", "collected_actual", "collected_actual_variance")))
check("B5 every tile carries its own title rather than the row accessor (the §CORE-17.3a collision)",
      all(v["figure"]["label"] and not v["figure"]["label"].startswith("<")
          for v in list(TW.values()) + list(PU.values())))


print("\n== C. a counted figure refuses to cover an envelope nobody counted ==")
check("C1 the counted total covers ONLY the envelope that carries a count",
      PU["collected_actual"]["figure"]["amount"] == 380.00
      and len([r for r in PU["collected_actual"]["rows"] if r["in_figure"]]) == 1)
check("C2 the uncounted collected envelope is in the panel, with its reason, and is NOT $0.00",
      any(r["fields"]["employee_name"] == "Sam" and not r["in_figure"] and r["reason"]
          for r in PU["collected_actual"]["rows"]),
      [(r["label"], r["in_figure"], r.get("reason")) for r in PU["collected_actual"]["rows"]])
check("C3 nor is it folded in at its DECLARED amount — the figure would read 500.00 if it were",
      PU["collected_actual"]["figure"]["amount"] != 500.00)
check("C4 the variance covers exactly the envelopes the total covers",
      PU["collected_actual_variance"]["figure"]["amount"] == -20.00
      and len([r for r in PU["collected_actual_variance"]["rows"] if r["in_figure"]]) == 1)
check("C5 the uncounted envelopes have their own figure, so the reason a counted total covers less "
      "than the whole pickup is itself openable",
      PU["collected_actual_missing"]["figure"]["amount"] == 1
      and [r["fields"]["employee_name"]
           for r in PU["collected_actual_missing"]["rows"] if r["in_figure"]] == ["Sam"])
check("C6 the DECLARED side of a collected envelope is its own figure — the one a short pickup does "
      "not change, which is why the counted figures beside it exist",
      PU["collected_cash"]["figure"]["amount"] == 520.00)
check("C7 declared legs name the closing sheet, counted legs name the pickup record — a figure never "
      "claims it knows what the DM found by reading what the store said",
      {o["table"] for r in PU["collected_cash"]["rows"] for o in r["origins"]} == {"daily_closing"}
      and {o["table"] for r in PU["collected_actual"]["rows"] for o in r["origins"]}
      == {"cash_pickup"})
check("C8 an envelope row addresses the store-day AND the person who filed it, because two envelopes "
      "for one store-day are two people's declarations",
      all(tuple(sorted((r.get("row_key") or {})))
          == ("close_date", "employee_name", "store_code")
          for v in PU.values() for r in v["rows"] if r.get("row_key")))
check("C9 the gross column's own total is a SERVED figure now, not the one footer the screen folded "
      "itself — and an envelope with no separate gross reads as the amount being collected",
      PU["total_cash_gross"]["figure"]["amount"] == 620.00
      and PU["total_cash_gross"]["tie_out"]["ties"] is True)


print("\n== D. every figure ties out to its own rows ==")
for _name, _panels in (("3-way", TW), ("pickup", PU), ("billpay", BP)):
    _bad = [k for k, v in _panels.items() if v["tie_out"]["ties"] is False]
    check("D1(%s) every served figure agrees with the rows served under it" % _name,
          _bad == [], _bad)
    check("D2(%s) and every tie-out says so in words a manager can read" % _name,
          all(v["tie_out"]["words"] for v in _panels.values()))


print("\n== Z. negative controls — the checks above must be capable of failing ==")
_z1 = twd.drilldowns(FLAT, dict(TOT, closing=TOT["closing"] + 1), sales_tables=("daily_sales_feed",))
check("Z1 a total that disagrees with its own rows comes back ties=False, in words",
      _z1["closing"]["tie_out"]["ties"] is False
      and "disagree" in _z1["closing"]["tie_out"]["words"])
_z2 = twd.drilldowns([], TOT, sales_tables=("daily_sales_feed",))
check("Z2 a figure with NO rows behind it does not tie either",
      _z2["closing"]["tie_out"]["ties"] is False)
_z3 = twd.drilldowns(FLAT, {k: v for k, v in TOT.items() if k != "gross_cx"},
                     sales_tables=("daily_sales_feed",))
check("Z3 a tile whose total the report did not produce is ABSENT, not served as $0.00 behind "
      "nothing", "gross_cx" not in _z3 and "closing" in _z3)
_z4 = twd.drilldowns(FLAT, TOT, sales_tables=())
check("Z4 an UNRESOLVED sales feed reads as untraceable, not as an empty origin list pretending to "
      "be a clean answer (the §CORE-17.3c fix, in the shared fold)",
      _z4["sales"]["untraceable_origins"] > 0 and _z4["closing"]["untraceable_origins"] == 0,
      (_z4["sales"]["untraceable_origins"], _z4["closing"]["untraceable_origins"]))
_z5 = pud.drilldowns(
    [dict(PICKUPS[1], actual_picked_amount=0.00)], dict(PICKUP_TOTALS, collected_actual=0.00,
                                                        collected_actual_envelopes=1,
                                                        collected_actual_missing=0),
    )
check("Z5 an envelope counted AT ZERO is a measured zero and IS counted — the exclusion is read off "
      "whether a count exists, never off whether it is truthy",
      _z5["collected_actual"]["rows"][0]["in_figure"] is True
      and _z5["collected_actual"]["rows"][0]["measured"] is True)
_z6 = pud.drilldowns([PICKUPS[1]], dict(PICKUP_TOTALS, collected_actual=0.00,
                                        collected_actual_envelopes=0))
check("Z6 and the same envelope with no count is NOT counted, whatever the total says",
      _z6["collected_actual"]["rows"][0]["in_figure"] is False)
_z7 = pud.drilldowns([PICKUPS[2]], dict(PICKUP_TOTALS, ready_cash=75.00, ready=1))
check("Z7 a waiting envelope never reaches a COLLECTED figure",
      _z7["collected_cash"]["rows"][0]["in_figure"] is False
      and _z7["ready_cash"]["rows"][0]["in_figure"] is True)


print("\n== E. build locks — the mechanism cannot un-wire ==")
for _mod, _path in (("three_way_drilldown", "app/modules/closing/three_way_drilldown.py"),
                    ("pickup_drilldown", "app/modules/closing/pickup_drilldown.py")):
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
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
_tw_body = _body("def tender_recon_3way(", _rt)
check("E5 the 3-way endpoint flattens its store-days through the ONE home and folds them there too — "
      "the screen flattens the same payload for its cards, so a second fold would be two populations",
      "_three_way_drilldown.flat_rows(" in _tw_body
      and "_three_way_drilldown.totals(" in _tw_body
      and '"drilldown": _three_way_drilldown.drilldowns(' in _tw_body)
check("E6 the cash-pickup endpoint builds its footers ONCE and serves THAT dict both to the screen "
      "and to the fold, so a footer and its drill-down cannot disagree",
      _body("def closing_pickups(", _rt).count("_totals = ") == 1
      and '"totals": _totals,' in _body("def closing_pickups(", _rt)
      and '"drilldown": _pickup_drilldown.drilldowns(out, _totals)' in _body("def closing_pickups(", _rt))
_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"),
               encoding="utf-8").read()
_named = {o["table"] for p in (TW, PU, _arch) for v in p.values() for r in v["rows"]
          for o in r["origins"] if o["table"]}
_unlabelled = sorted(t for t in _named if ("  %s:" % t) not in _labels)
check("E7 every table these drills can name has a PLAIN-WORDS label — a manager never sees a table "
      "name (§19.38, owner: 'hide database names from the users')",
      _unlabelled == [], _unlabelled)

_3way = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                          "tender-recon-3way", "page.tsx"), encoding="utf-8").read()
_pickup = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                            "pickup", "page.tsx"), encoding="utf-8").read()
for _n, _pg in (("tender-recon-3way", _3way), ("pickup", _pickup)):
    check("E8(%s) the page renders the SHARED panel and hand-rolls none of it" % _n,
          "DrillDownPanel" in _pg and "tie_out" not in _pg)
    check("E9(%s) and offers each figure's link through the SHARED offer rule, never a local copy "
          "of the loop" % _n,
          "useDrillTile(" in _pg and "dl(" in _pg
          and "const dl = (...pairs: string[]) => {" not in _pg)


print("\n== F. the narrowing is applied where it is needed and NOT where it is not ==")
check("F1 the 3-way strip narrows the served panels to its own selection, through the one home",
      "narrowPanels(drills, (_r, k) => selected.has(k))" in _3way
      and "from '@/lib/drill-narrow'" in _3way)
check("F2 and reads each figure OFF its narrowed panel — a reduce beside the panel would be a second "
      "fold hoping to agree",
      "panels[k] ? (panels[k].figure?.amount ?? 0) : fallback" in _3way)
check("F3 its selection key is the store-DAY the declaration addresses rows by, not the store alone",
      "screenKey({ store_code: r.store_code, close_date: r.date })" in _3way
      and twd.ROW_KEYS == ("store_code", "close_date"))
check("F4 a figure the fallback computed says on screen why it cannot be opened",
      "unpanelled" in _3way)
check("F5 /closing/pickup does NOT narrow, and the reason is recorded where someone would look for "
      "it: every filter on that page is a query parameter, so the server's fold already covers "
      "exactly the envelopes on screen (§CORE-17.3b named it in the class; that was half wrong)",
      "from '@/lib/drill-narrow'" not in _pickup and "narrowPanels" not in _pickup
      and "QUERY PARAMETER" in _pickup
      and "QUERY PARAMETER" in open(os.path.join(
          ROOT, "backend", "app", "modules", "closing", "pickup_drilldown.py"),
          encoding="utf-8").read())
check("F6 and its footers read the SERVED totals rather than reducing the rows again",
      "const t = data?.totals" in _pickup and "t.collected_actual_variance" in _pickup)


print("\n== H. the bill-pay pickup is the same mechanism, not a sibling file ==")
_pud_src = open(os.path.join(ROOT, "backend", "app", "modules", "closing",
                             "pickup_drilldown.py"), encoding="utf-8").read()
_bp_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                             "billpay-pickup", "page.tsx"), encoding="utf-8").read()
_pu_page = _pickup
check("H1 the bill-pay catalogue lives in the SAME declaration as the cash one, and reuses its "
      "label, row-fields and state readers rather than restating them",
      "def billpay_catalogue(" in _pud_src
      and not os.path.exists(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                          "billpay_pickup_drilldown.py"))
      and _pud_src.count("def _row_fields(") == 1 and _pud_src.count("def _label(") == 1)
check("H2 every bill-pay figure the endpoint builds has a tile declaration",
      set(re.findall(r'"(\w+)":', _totals_literal('def billpay_pickups(', _rt))) <= set(BP),
      sorted(set(re.findall(r'"(\w+)":',
                            _totals_literal('def billpay_pickups(', _rt))) - set(BP)))
check("H3 the endpoint builds them ONCE and serves that dict both to the screen and to the fold",
      _body("def billpay_pickups(", _rt).count("_totals = ") == 1
      and '"totals": _totals,' in _body("def billpay_pickups(", _rt)
      and '_pickup_drilldown.billpay_drilldowns(out, _totals)' in _body("def billpay_pickups(", _rt))
check("H4 a bill payment taken ON CARD is never counted as waiting for anyone — it has no physical "
      "cash to collect, and that test is read off the same rule the endpoint uses",
      [r["fields"]["employee_name"] for r in BP["ready"]["rows"] if r["in_figure"]] == ["Rita"]
      and BP["ready"]["figure"]["amount"] == 1)
check("H5 the card side is its OWN figure and is never added into a cash one",
      BP["total_credit"]["figure"]["amount"] == 65.00
      and BP["total_cash"]["figure"]["amount"] == 160.00)
# The card-only envelope is in BOTH panels, and what it means differs between them — which is the
# distinction a reader of either figure needs, and the reason it is never simply dropped.
_sam_ready = [r for r in BP["ready"]["rows"] if r["fields"]["employee_name"] == "Sam"]
_sam_cash = [r for r in BP["ready_cash"]["rows"] if r["fields"]["employee_name"] == "Sam"]
check("H6 the card-only envelope appears in the WAITING COUNT panel shown-but-not-counted, with its "
      "reason — an envelope dropped from a panel is a figure quietly missing a row",
      len(_sam_ready) == 1 and _sam_ready[0]["in_figure"] is False)
check("H6b and appears in the waiting CASH panel counted at a real $0.00 — it holds no cash, which "
      "is a measured zero, not an absence",
      len(_sam_cash) == 1 and _sam_cash[0]["in_figure"] is True
      and _sam_cash[0]["measured"] is True and _sam_cash[0]["amount"] == 0.0)
check("H7 the two screens share ONE stat tile and one set of drill columns — each carried a "
      "byte-identical `Stat`, which is the sibling defect in its smallest form",
      "DrillPickupStat as Stat" in _bp_page and "DrillPickupStat as Stat" in _pu_page
      and "const Stat = (" not in _bp_page and "const Stat = (" not in _pu_page
      and "ENVELOPE_DRILL_COLUMNS" in _bp_page and "ENVELOPE_DRILL_COLUMNS" in _pu_page)
check("H8 the bill-pay screen renders the SHARED panel and hand-rolls none of it",
      "DrillDownPanel" in _bp_page and "tie_out" not in _bp_page
      and "useDrillTile(" in _bp_page
      and "const dl = (...pairs: string[]) => {" not in _bp_page)
check("H9 REPORTED, NOT PAPERED OVER: bill-pay envelopes carry a pickup COUNT and that endpoint "
      "never totals it, so the screen has no counted side at all — written down where the next "
      "reader will meet it, not quietly added with a drill-down",
      "never totals it" in _pud_src
      and "collected_actual" not in _totals_literal('def billpay_pickups(', _rt))

print("\n== G. registration ==")
_idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("G1 all the declarations are registered in the index (owner directive 2026-09-01)",
      "three_way_drilldown" in _idx and "pickup_drilldown" in _idx)
check("G2 so is this harness", "harness_batch5_drilldown" in _idx)
_ledger = open("harness_drilldown_pending.txt", encoding="utf-8").read()
for _r in ("/closing/tender-recon-3way", "/closing/pickup", "/closing/billpay-pickup"):
    check("G3(%s) the coverage ledger no longer says this report owes a drill-down" % _r,
          re.search(r"^%s\tshared" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
