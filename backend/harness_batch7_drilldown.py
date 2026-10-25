"""PROOF — the envelope receipt and the Daily Closing dashboard name their evidence.

DB-free, stdlib only, driving the real `closing/envelope_drilldown`, `closing/rollup_drilldown` and
`closing/recon_drilldown`'s submissions sibling through the shared fold
(`core/report_drilldown.tiles`). Batch 7 of the §CORE-17 audit: /closing/envelope-report and
/closing.

WHAT THIS BATCH IS ABOUT.

1. THREE PEOPLE COUNT ONE ENVELOPE, in three tables, and the receipt showed their numbers side by
   side as if they were one measurement. The rep DECLARES on the closing sheet, the DM counts at
   pickup, management counts here — and a shortage can become a chargeback against the rep's pay. So
   each figure names the tables it was actually read out of: two for the variance figures, three for
   the chargeback ones, never all three on every row. §A, §B.

2. AN UNCOUNTED ENVELOPE IS NOT A CLEAN ONE. It is a real envelope and `envelopes` counts it, but it
   is deliberately not counted in short / over / match or in either dollar figure. Folding it in at
   $0.00 would read as "management counted this and it came out exactly right", which is the
   opposite of the truth. §A6, §Z1.

3. THE DASHBOARD'S TILES ARE STORE-DAY SUMS, AND THAT IS NOT A STYLE CHOICE. `verified_overlay`
   makes a DM's correction authoritative FOR THE STORE-DAY and its own docstring says per-rep detail
   keeps the reps' raw figures. So a panel of rep-days would NOT add up to the tile on any day a DM
   corrected. §D proves the store-day fold ties AND that the rep-day fold would not — the one check
   that makes the grain a fact rather than a preference. §D, §Z4.

4. A DM CORRECTS MONEY, NOT COUNTS, and the panel says so per row: the verification table is named
   only on the store-days a correction actually answered for. A provenance claimed where no
   authority was exercised is a lie that reads like rigour. §E, §Z5.

5. THE TWO CASH TILES ARE THE RECON'S OWN ACCUSATION, FOLDED AS MONEY. Same `_money_issues` verdict,
   so the declaration is a SIBLING in `recon_drilldown.py`, not a second file. §F, §G4.

6. REPORTED, NOT PAPERED OVER: a rep-day the recon could not judge contributes 0.00 to both cash
   tiles, so "Cash short $0.00" reads as "no cash is missing" when nothing was compared at all.
   The figures are UNCHANGED — a figure is never moved to make a screen read better — and the panels
   show those rows, uncounted, each naming which blank it is. §F3, §F4.

  python3 backend/harness_batch7_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import envelope_drilldown as evd                       # noqa: E402
from app.modules.closing import envelope_report as evr                          # noqa: E402
from app.modules.closing import recon_drilldown as rcd                          # noqa: E402
from app.modules.closing import rollup_drilldown as rld                         # noqa: E402
from app.modules.closing import verified_overlay as vov                         # noqa: E402
from app.modules.core import report_drilldown as rd                             # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
    """A file's CODE, with strings and comments stripped — a section number in a docstring is prose,
    a float in an expression is a threshold, and only the second is what §G forbids."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _body(fn_name, src):
    i = src.index(fn_name)
    j = src.find("\ndef ", i + len(fn_name))
    k = src.find("\n@router.", i + len(fn_name))
    ends = [x for x in (j, k) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


_rt = open(os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py"),
           encoding="utf-8").read()
_BASIS = {"total_cash": "Total cash", "store_cash": "Store cash",
          "bill_payment_cash": "Bill payments"}


def _bl(k):
    return _BASIS.get(k) if k else None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The envelope receipt. One row per envelope = one rep's day, addressed by the closing row's id.
ENV = [
    # counted short, and the shortage was assigned to the rep as a chargeback
    {"closing_row_id": "e1", "store_code": "B-1", "store_address": "1 A St", "close_date": "2026-10-01",
     "employee_name": "Abid", "declared_cash": 500.0, "counted": True, "counted_amount": 480.0,
     "variance": -20.0, "status": "short", "counted_by_name": "Mgr",
     "counted_basis": {"basis": "total_cash", "recorded": True, "label": "x"},
     "chargeback_id": "cb1", "chargeback_amount": 20.0, "chargeback_status": "pending"},
    # counted short, NOT charged back — context for the chargeback tiles
    {"closing_row_id": "e2", "store_code": "B-5", "store_address": "5 E St", "close_date": "2026-10-03",
     "employee_name": "Abid", "declared_cash": 90.0, "counted": True, "counted_amount": 80.0,
     "variance": -10.0, "status": "short", "counted_basis": None},
    # counted over
    {"closing_row_id": "e3", "store_code": "B-2", "store_address": "2 B St", "close_date": "2026-10-01",
     "employee_name": "Bea", "declared_cash": 300.0, "counted": True, "counted_amount": 310.0,
     "variance": 10.0, "status": "over", "counted_basis": None},
    # counted, came out right
    {"closing_row_id": "e4", "store_code": "B-3", "store_address": "3 C St", "close_date": "2026-10-02",
     "employee_name": "Cy", "declared_cash": 200.0, "counted": True, "counted_amount": 200.0,
     "variance": 0.0, "status": "match", "counted_basis": None},
    # NOBODY HAS COUNTED IT. Not a clean envelope.
    {"closing_row_id": "e5", "store_code": "B-4", "store_address": "4 D St", "close_date": "2026-10-02",
     "employee_name": "Dee", "declared_cash": 150.0, "counted": False, "counted_amount": None,
     "variance": None, "status": "uncounted", "counted_basis": None},
    # a waived chargeback: assigned, then forgiven — still counted in "assigned"
    {"closing_row_id": "e6", "store_code": "B-6", "store_address": "6 F St", "close_date": "2026-10-04",
     "employee_name": "Bea", "declared_cash": 70.0, "counted": True, "counted_amount": 65.0,
     "variance": -5.0, "status": "short", "counted_basis": None,
     "chargeback_id": "cb2", "chargeback_amount": 5.0, "chargeback_status": "waived"},
]
ENV_T = evr.totals(ENV)
ENV_G = evr.by_employee(ENV)
EV = evd.drilldowns(ENV, ENV_T, basis_label=_bl)
EVG = evd.by_employee_drilldowns(ENV, ENV_G, basis_label=_bl)

print("\n== A. the envelope receipt: every figure opens to its envelopes ==")
_cat = {t["key"]: t for t in evd.catalogue()}
check("A1 every figure `envelope_report.totals` produces is DECLARED — a figure with no declaration "
      "is a figure nobody can check",
      sorted(_cat) == sorted(ENV_T), (sorted(set(ENV_T) - set(_cat)), sorted(set(_cat) - set(ENV_T))))
check("A2 and every declared figure was actually SERVED a panel", sorted(EV) == sorted(_cat),
      sorted(set(_cat) - set(EV)))
_bad = {k: p["tie_out"]["words"] for k, p in EV.items() if p["tie_out"]["ties"] is not True}
check("A3 every panel ADDS UP TO ITS FIGURE to the cent — the report against its own evidence",
      _bad == {}, _bad)
check("A4 a count figure is counted in envelopes, not dollars",
      all(EV[k]["figure"]["unit"] == rd.UNIT_COUNT
          for k in ("envelopes", "counted", "short", "over", "match", "chargebacks"))
      and all(EV[k]["figure"]["unit"] == rd.UNIT_MONEY
              for k in ("short_total", "over_total", "chargeback_total")))
check("A5 the rows a figure does NOT count stay in its panel as context — that is the difference "
      "between a drill-down and a filtered list",
      EV["short"]["tie_out"]["shown_not_counted"] == 3
      and EV["envelopes"]["tie_out"]["shown_not_counted"] == 0,
      (EV["short"]["tie_out"]["shown_not_counted"], EV["envelopes"]["tie_out"]["shown_not_counted"]))
_unc = [r for r in EV["short_total"]["rows"] if r["row_key"]["closing_row_id"] == "e5"][0]
check("A6 AN UNCOUNTED ENVELOPE IS NOT A CLEAN ONE: shown in the money panel, not counted in it, "
      "carrying no number and stating why",
      _unc["in_figure"] is False and _unc["amount"] is None
      and _unc["reason"] == "not_counted" and bool(_unc["reason_label"]),
      _unc)
check("A7 but it IS one of the envelopes filed, and `envelopes` counts it",
      [r for r in EV["envelopes"]["rows"]
       if r["row_key"]["closing_row_id"] == "e5"][0]["in_figure"] is True)
check("A8 short is served as a POSITIVE figure, the way the tile is headed and the way "
      "`envelope_report.totals` folds it — the sign has one home, there",
      EV["short_total"]["figure"]["amount"] == 35.0
      and all((r["amount"] or 0) > 0 for r in EV["short_total"]["rows"] if r["in_figure"]),
      EV["short_total"]["figure"])
check("A9 a WAIVED chargeback is still counted in what was assigned, with the decision on the row "
      "— the figure counts assignments, and forgiveness is a later fact about one",
      ENV_T["chargebacks"] == 2 and EV["chargeback_total"]["figure"]["amount"] == 25.0
      and {r["fields"]["chargeback"] for r in EV["chargebacks"]["rows"] if r["in_figure"]}
          == {"pending", "waived"})
check("A10 a short envelope with NO chargeback is shown in the chargeback panels, uncounted — it is "
      "the money nobody has assigned to anyone",
      [r for r in EV["chargebacks"]["rows"]
       if r["row_key"]["closing_row_id"] == "e2"][0]["in_figure"] is False)

print("\n== B. the envelope receipt's provenance: three people, three tables ==")
_src_of = lambda k, rid: [r for r in EV[k]["rows"] if r["row_key"]["closing_row_id"] == rid][0]["sources"]
check("B1 a count of envelopes FILED names the closing sheet alone — whether anybody counted it is "
      "the next figure, not this one",
      _src_of("envelopes", "e1") == ["daily_closing"], _src_of("envelopes", "e1"))
check("B2 a variance figure names the closing sheet AND the management count, because the variance "
      "is one scored against the other",
      _src_of("short_total", "e1") == ["daily_closing", "envelope_count"],
      _src_of("short_total", "e1"))
check("B3 a chargeback figure names the chargeback row too, because that is where the amount and "
      "the decision live and nowhere else",
      _src_of("chargeback_total", "e1") == ["daily_closing", "envelope_count", "ops_chargeback"],
      _src_of("chargeback_total", "e1"))
check("B4 a row with no chargeback does NOT name the chargeback table — a provenance claimed where "
      "nothing was read is a lie that reads like rigour",
      "ops_chargeback" not in _src_of("chargebacks", "e2"), _src_of("chargebacks", "e2"))
check("B5 every envelope is addressed by the closing row's own id, the id the count POST and the "
      "chargeback link both use",
      evd.ROW_KEYS == ("closing_row_id",)
      and all(set(r["row_key"]) == {"closing_row_id"} for p in EV.values() for r in p["rows"]))
_e1 = _src_of("short_total", "e1") and [r for r in EV["short_total"]["rows"]
                                        if r["row_key"]["closing_row_id"] == "e1"][0]
check("B6 the basis a stored count was SCORED on rides on the row, in the tenant's own wording — "
      "a variance scored on other cash is a different measurement, not a different rounding",
      _e1["fields"]["counted_on"] == "Total cash"
      and _e1["fields"]["counted_on_recorded"] is True, _e1["fields"])
check("B7 a count taken before the basis column existed says so rather than naming a basis it never "
      "recorded",
      [r for r in EV["short_total"]["rows"]
       if r["row_key"]["closing_row_id"] == "e2"][0]["fields"]["counted_on"] is None)
check("B8 the declaration spells NO basis key and no basis label of its own — the vocabulary's one "
      "home is `envelope_report.basis_options` / `basis_label` (§19.38)",
      not re.search(r"total_cash|store_cash|bill_payment_cash|Whole drawer",
                    _code_only(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                            "envelope_drilldown.py"))))

print("\n== C. the By-employee rollup: one panel per line ==")
check("C1 one panel per line, keyed by the employee name THAT LINE is keyed on, so a panel can "
      "never hold a different set of envelopes than the line it opens from",
      sorted(EVG) == sorted(str(g["employee_name"]) for g in ENV_G), sorted(EVG))
_bad = {k: p["tie_out"]["words"] for k, p in EVG.items() if p["tie_out"]["ties"] is not True}
check("C2 and every one adds up to the line's own net variance", _bad == {}, _bad)
check("C3 the grouping is the ROLLUP'S OWN rule, read rather than restated",
      evd._emp_key({"employee_name": "  Abid "}) == "Abid"
      and evd._emp_key({"employee_name": None}) == "(unnamed)"
      and evd._emp_key({}) == "(unnamed)")
check("C4 NET IS NOT CLEAN, and the label says so: Abid is $20 short one day and $10 short another "
      "against nothing over, so his net is the sum of real failures, not a cancelled-out zero",
      "net of short and over" in EVG["Abid"]["figure"]["label"].lower()
      and EVG["Abid"]["figure"]["amount"] == -30.0, EVG["Abid"]["figure"])
check("C5 and the note carries the GROSS counts beside the net, plus the envelopes nobody counted — "
      "a net of zero must read as 'they cancelled out', never as 'clean'",
      "short and" in (EVG["Bea"]["note"] or "")
      and "not the same as a count that came out right" in (EVG["Bea"]["note"] or ""),
      EVG["Bea"]["note"])
check("C6 a line's panel carries the same provenance per row as the tiles do",
      {t for r in EVG["Abid"]["rows"] for t in r["sources"]}
      >= {"daily_closing", "envelope_count"})
check("C7 an uncounted envelope is in its person's panel, uncounted, with its reason — the figure "
      "is light by an unknown amount, not by nothing",
      [r for r in EVG["Dee"]["rows"]][0]["reason"] == "not_counted"
      and EVG["Dee"]["tie_out"]["counted_rows"] == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The Daily Closing dashboard. Four rep-days over three store-days, with a DM correction on one of
# them — the case the grain exists for.
MONEY = ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account")
COUNT = ("upgrade_count", "new_line_count", "postpaid_count")
REPDAYS = [
    # B-1 / 1 Oct: two reps, and a DM later corrects the store-day's cash DOWN to 900
    {"store_code": "B-1", "close_date": "2026-10-01", "employee_name": "Abid",
     "store_cash": 500.0, "store_cc": 100.0, "epay_cash": 0.0, "epay_cc": 0.0,
     "acc_sale": 40.0, "other_account": 0.0, "epay_on_cash": 30.0, "epay_on_cc": 10.0,
     "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0},
    {"store_code": "B-1", "close_date": "2026-10-01", "employee_name": "Bea",
     "store_cash": 450.0, "store_cc": 50.0, "epay_cash": 0.0, "epay_cc": 0.0,
     "acc_sale": 10.0, "other_account": 5.0, "epay_on_cash": 0.0, "epay_on_cc": 0.0,
     "upgrade_count": 0, "new_line_count": 1, "postpaid_count": 1},
    # B-2 / 1 Oct: one rep, no correction
    {"store_code": "B-2", "close_date": "2026-10-01", "employee_name": "Cy",
     "store_cash": 200.0, "store_cc": 20.0, "epay_cash": 0.0, "epay_cc": 0.0,
     "acc_sale": 0.0, "other_account": 0.0, "epay_on_cash": 0.0, "epay_on_cc": 0.0,
     "upgrade_count": 2, "new_line_count": 0, "postpaid_count": 0},
    # B-2 / 2 Oct: one rep, no correction, and nobody verified it
    {"store_code": "B-2", "close_date": "2026-10-02", "employee_name": "Cy",
     "store_cash": 80.0, "store_cc": 0.0, "epay_cash": 0.0, "epay_cc": 0.0,
     "acc_sale": 7.0, "other_account": 0.0, "epay_on_cash": 0.0, "epay_on_cc": 0.0,
     "upgrade_count": 0, "new_line_count": 0, "postpaid_count": 1},
]
#: The DM's corrected store-day total for B-1 / 1 Oct. Cash 950 -> 900.
DM = {("B-1", "2026-10-01"): {"verified": True, "dm_store_cash": 900.0}}


def _replicate_dashboard():
    """The endpoint's own accumulation, replicated: `grand` is Σ(rep-days) with the overlay's deltas
    applied at STORE-DAY grain, and `store_days` is the same store-days after the overlay. Keeping
    both lets §D ask the question that matters — which population adds up to the tile."""
    grand = {k: 0.0 for k in MONEY} | {"epay_on_cash": 0.0, "epay_on_cc": 0.0} \
        | {k: 0 for k in COUNT} | {"rows": 0}
    sd, sd_counts, sd_meta = {}, {}, {}
    for r in REPDAYS:
        k = (r["store_code"], r["close_date"])
        for f in MONEY:
            grand[f] = round(grand[f] + float(r.get(f) or 0), 2)
        for f in ("epay_on_cash", "epay_on_cc"):
            grand[f] = round(grand[f] + float(r.get(f) or 0), 2)
        for f in COUNT:
            grand[f] += int(r.get(f) or 0)
        grand["rows"] += 1
        a = sd.setdefault(k, {f: 0.0 for f in MONEY} | {"epay_on_cash": 0.0, "epay_on_cc": 0.0})
        for f in list(a):
            a[f] = round(a[f] + float(r.get(f) or 0), 2)
        c = sd_counts.setdefault(k, {f: 0 for f in COUNT} | {"rows": 0})
        for f in COUNT:
            c[f] += int(r.get(f) or 0)
        c["rows"] += 1
        sd_meta.setdefault(k, {"store_code": r["store_code"], "store_address": r["store_code"],
                               "market": "LI"})
    final, corrected = {k: dict(v) for k, v in sd.items()}, set()
    for k, raw in sd.items():
        dm = DM.get(k)
        if not dm:
            continue
        corr = vov.apply_overlay(dict(raw), dm)
        for f, rv in raw.items():
            delta = round(corr.get(f, rv) - rv, 2)
            if not delta:
                continue
            final[k][f] = round(rv + delta, 2)
            corrected.add(k)
            grand[f] = round(grand[f] + delta, 2)
    store_days = []
    for (code, day), money in sorted(final.items()):
        store_days.append({**money, **sd_counts[(code, day)], **sd_meta[(code, day)],
                           "close_date": day, "dm_corrected": (code, day) in corrected,
                           "verified": bool((DM.get((code, day)) or {}).get("verified"))})
    return grand, store_days


GRAND, STORE_DAYS = _replicate_dashboard()
#: The tile formulas as the SCREEN reads them (page.tsx's cashTotal / cardTotal / activations).
SCREEN = {"cash": round(GRAND["store_cash"] + GRAND["epay_cash"], 2),
          "credit": round(GRAND["store_cc"] + GRAND["epay_cc"], 2),
          "epay_on_cash": GRAND["epay_on_cash"], "epay_on_cc": GRAND["epay_on_cc"],
          "acc_sale": GRAND["acc_sale"], "other_account": GRAND["other_account"],
          "activations": GRAND["new_line_count"] + GRAND["postpaid_count"],
          "upgrade_count": GRAND["upgrade_count"], "rows": GRAND["rows"]}
RFIG = rld.totals(STORE_DAYS, submitted_keys=3, verified_keys=1)
RL = rld.drilldowns(STORE_DAYS, RFIG, processor_label="ePay")

print("\n== D. the dashboard's grain: the store-day is a FACT, not a preference ==")
check("D1 the store-day fold agrees with the dashboard's own tiles, figure for figure",
      all(RFIG[k] == SCREEN[k] for k in SCREEN),
      {k: (RFIG[k], SCREEN[k]) for k in SCREEN if RFIG[k] != SCREEN[k]})
check("D2 a DM correction really did move the figure, so D1 is not passing on a no-op "
      "(cash 950 -> 900 on the corrected store-day)",
      SCREEN["cash"] != round(sum(float(r.get("store_cash") or 0) + float(r.get("epay_cash") or 0)
                                  for r in REPDAYS), 2),
      SCREEN["cash"])
_rep_fold = round(sum(float(r.get("store_cash") or 0) + float(r.get("epay_cash") or 0)
                      for r in REPDAYS), 2)
check("D3 THE CHECK THAT MAKES THE GRAIN A FACT: a panel of REP-DAYS would NOT add up to the tile "
      "on a corrected day, so store-days are the only population that can honestly explain it "
      "(`verified_overlay`: 'applied to store-day AGGREGATES only')",
      _rep_fold != SCREEN["cash"], (_rep_fold, SCREEN["cash"]))
_bad = {k: p["tie_out"]["words"] for k, p in RL.items() if p["tie_out"]["ties"] is not True}
check("D4 and every panel adds up to its figure to the cent", _bad == {}, _bad)
_cat = {t["key"] for t in rld.catalogue()}
check("D5 every figure the catalogue declares was served a panel", sorted(RL) == sorted(_cat),
      sorted(_cat - set(RL)))
check("D6 `days` has NO panel: it is a DISTINCT count of dates, not a sum over rows, so no set of "
      "rows adds up to it and a panel would be a claim nobody could check",
      "days" not in _cat and "days" not in RL)
check("D7 every store-day is addressed by its store and its date",
      rld.ROW_KEYS == ("store_code", "close_date")
      and all(set(r["row_key"]) == {"store_code", "close_date"}
              for p in RL.values() for r in p["rows"]))
check("D8 the coverage pair is TWO figures over the same store-days, and the unverified ones are "
      "shown uncounted — they are the work the figure is measuring",
      RL["verified_keys"]["tie_out"]["counted_rows"] == 1
      and RL["verified_keys"]["tie_out"]["shown_not_counted"] == 2
      and RL["submitted_keys"]["tie_out"]["counted_rows"] == 3,
      (RL["verified_keys"]["tie_out"], RL["submitted_keys"]["tie_out"]))

print("\n== E. the dashboard's provenance: a DM corrects money, not counts ==")
_corr = [r for r in RL["cash"]["rows"] if r["row_key"]["store_code"] == "B-1"][0]
_plain = [r for r in RL["cash"]["rows"] if r["row_key"]["close_date"] == "2026-10-02"][0]
check("E1 a corrected store-day names the verification the figure came from",
      _corr["sources"] == ["daily_closing", "daily_closing_verification"], _corr["sources"])
check("E2 an uncorrected one names the closing sheet ALONE — naming the verification table where no "
      "correction answered would claim an authority nobody exercised",
      _plain["sources"] == ["daily_closing"], _plain["sources"])
check("E3 and the row SAYS whose figures they are, in words",
      _corr["fields"]["figures"] == "DM corrected"
      and _plain["fields"]["figures"] == "as submitted")
check("E4 the COUNT panels name the closing sheet alone even on a corrected day, because a DM's "
      "correction covers the six money figures and the activations stay the reps' own",
      [r for r in RL["activations"]["rows"]
       if r["row_key"]["store_code"] == "B-1"][0]["sources"] == ["daily_closing"],
      [r for r in RL["activations"]["rows"] if r["row_key"]["store_code"] == "B-1"][0]["sources"])
check("E5 and the note says so, rather than leaving a reader to notice",
      "money figures only" in (RL["activations"]["note"] or ""), RL["activations"]["note"])
check("E6 the breakdown tiles say they are already inside another figure — two tiles that add to "
      "more than the cash collected would otherwise read as more cash",
      "already counted inside" in (RL["epay_on_cash"]["note"] or "").lower()
      and "already counted inside" in (RL["epay_on_cc"]["note"] or "").lower())
check("E7 the processor's name is the TENANT'S, passed in, never spelled in the declaration",
      RL["epay_on_cash"]["figure"]["label"] == "ePay cash"
      and not re.search(r"(?i)\bepay\b|\bvidapay\b",
                        _code_only(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                                "rollup_drilldown.py"))))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The dashboard's two cash tiles: `_money_issues`' CASH verdict per rep-day, folded as money.
SUBS = [
    {"close_date": "2026-10-01", "store_code": "B-1", "store_address": "1 A St",
     "employee_name": "Abid", "t_cash": 400.0, "b2b_cash": 380.0, "gate_status": "blocked",
     "gate_reasons": ["Cash short $20.00 vs POS sales"],
     "cash_short_amount": 20.0, "cash_over_amount": 0.0},
    {"close_date": "2026-10-01", "store_code": "B-2", "store_address": "2 B St",
     "employee_name": "Bea", "t_cash": 300.0, "b2b_cash": 280.0, "gate_status": "flagged",
     "gate_reasons": ["Cash over $20.00 vs POS — investigate"],
     "cash_short_amount": 0.0, "cash_over_amount": 20.0},
    # THE FEED WAS NEVER LOADED for this day. Contributes 0.00 to both tiles today.
    {"close_date": "2026-10-02", "store_code": "B-3", "store_address": "3 C St",
     "employee_name": "Cy", "t_cash": 100.0, "b2b_cash": None, "gate_status": "recon_pending",
     "gate_reasons": [], "cash_short_amount": 0.0, "cash_over_amount": 0.0},
    # judged, and it agreed
    {"close_date": "2026-10-02", "store_code": "B-4", "store_address": "4 D St",
     "employee_name": "Dee", "t_cash": 50.0, "b2b_cash": 50.0, "gate_status": "ok",
     "gate_reasons": [], "cash_short_amount": 0.0, "cash_over_amount": 0.0},
]
SUB_T = rcd.submission_totals(SUBS)
SUB = rcd.submission_drilldowns(SUBS, SUB_T, sales_tables=("daily_sales_feed",))

print("\n== F. the two cash tiles: the recon's accusation, folded as money ==")
check("F1 both figures are declared and served", sorted(SUB) == ["cash_over", "cash_short"],
      sorted(SUB))
_bad = {k: p["tie_out"]["words"] for k, p in SUB.items() if p["tie_out"]["ties"] is not True}
check("F2 and each adds up to its figure to the cent", _bad == {}, _bad)
_pend = [r for r in SUB["cash_short"]["rows"] if r["row_key"]["store_code"] == "B-3"][0]
check("F3 REPORTED, NOT PAPERED OVER: a rep-day the recon could not judge is SHOWN and NOT counted, "
      "carrying no number and stating which blank it is — 'Cash short $0.00' must not read as 'no "
      "cash is missing' when nothing was compared",
      _pend["in_figure"] is False and _pend["amount"] is None
      and _pend["reason"] == "no_sales_feed_match" and bool(_pend["reason_label"]), _pend)
check("F4 and the FIGURE is unchanged by saying so — a figure is never moved to make a screen read "
      "better (the tile is still Σ of the judged rows, $20.00)",
      SUB_T == {"cash_short": 20.0, "cash_over": 20.0}, SUB_T)
check("F5 an unjudged row names NO feed, and the gap is counted rather than silent",
      _pend["sources"] == ["daily_closing"] and SUB["cash_short"]["untraceable_origins"] == 1,
      (_pend["sources"], SUB["cash_short"]["untraceable_origins"]))
check("F6 a judged row names the feed the comparison was ACTUALLY answered from, dereferenced from "
      "the day read rather than named at the call site",
      SUB["cash_short"]["rows"][0]["sources"] == ["daily_closing", "daily_sales_feed"],
      SUB["cash_short"]["rows"][0]["sources"])
check("F7 the grain is the REP-DAY, not the accusation — a submissions row carries one cash figure, "
      "not one per metric, so `metric` is no part of its address",
      rcd.SUBMISSION_KEYS == ("close_date", "store_code", "employee_name")
      and "metric" not in rcd.SUBMISSION_KEYS and "metric" in rcd.ROW_KEYS)
check("F8 short and over are NEVER netted against each other — a rep $50 short one day and $50 over "
      "the next has twice handed over the wrong cash, which is why the owner asked for two tiles",
      rcd.submission_totals(SUBS[:2]) == {"cash_short": 20.0, "cash_over": 20.0})
check("F9 a judged row that agreed is shown, uncounted — the days that came out right are the "
      "context that makes the figure readable",
      [r for r in SUB["cash_short"]["rows"]
       if r["row_key"]["store_code"] == "B-4"][0]["in_figure"] is False
      and SUB["cash_short"]["tie_out"]["shown_not_counted"] == 3)
check("F10 the verdict and the gate state are READ OFF the row `_money_issues` already wrote, so "
      "the rule that made a row short has exactly one home",
      SUB["cash_short"]["rows"][0]["fields"]["severity"] == "blocked"
      and "Cash short" in (SUB["cash_short"]["rows"][0]["fields"]["verdict"] or ""))

print("\n== Z. negative controls — the checks above must be capable of failing ==")
_z = [dict(r) for r in ENV]
_z[4]["counted"], _z[4]["status"], _z[4]["variance"] = True, "match", 0.0
check("Z1 folding the UNCOUNTED envelope in as a counted $0.00 changes what `match` counts — so A6 "
      "is testing a real distinction, not restating one",
      evr.totals(_z)["match"] != ENV_T["match"])
_z2 = [dict(r) for r in ENV]
_z2[0]["variance"] = -25.0
check("Z2 a mis-scored variance breaks the money panel's tie-out",
      evd.drilldowns(_z2, ENV_T, basis_label=_bl)["short_total"]["tie_out"]["ties"] is False)
check("Z3 a DROPPED envelope breaks it too — a panel missing a row cannot be right by luck",
      evd.drilldowns(ENV[1:], ENV_T, basis_label=_bl)["short_total"]["tie_out"]["ties"] is False)
_z4 = [dict(r) for r in STORE_DAYS]
_z4[0]["dm_corrected"] = False
check("Z4 a corrected store-day that STOPS declaring itself corrected stops naming the verification "
      "table — so E1/E3 are reading the row, not a constant",
      "daily_closing_verification" not in rld.drilldowns(_z4, RFIG)["cash"]["rows"][0]["sources"])
_z5 = [dict(r) for r in STORE_DAYS]
_z5[0]["store_cash"] = 0.0
check("Z5 a store-day whose money is wiped breaks the cash panel's tie-out against the dashboard's "
      "own figure",
      rld.drilldowns(_z5, RFIG)["cash"]["tie_out"]["ties"] is False)
_z6 = [dict(r) for r in SUBS]
_z6[2]["gate_status"] = "blocked"
check("Z6 an unjudged rep-day re-labelled as judged would be counted at $0.00 and would claim the "
      "feed — which is exactly the reading F3/F5 exist to refuse",
      [r for r in rcd.submission_drilldowns(_z6, rcd.submission_totals(_z6),
                                            sales_tables=("daily_sales_feed",))["cash_short"]["rows"]
       if r["row_key"]["store_code"] == "B-3"][0]["reason"] is None)
check("Z7 a figure the report did not produce is SKIPPED, never served as $0.00 — a figure with no "
      "rows behind it must not be invented",
      "short_total" not in evd.drilldowns(ENV, {k: v for k, v in ENV_T.items()
                                                if k != "short_total"}, basis_label=_bl))
check("Z8 the coverage figure is capable of disagreeing: claiming two verified store-days where one "
      "is marked verified breaks its tie-out",
      rld.drilldowns(STORE_DAYS, dict(RFIG, verified_keys=2))["verified_keys"]["tie_out"]["ties"]
      is False)

print("\n== G. build locks — the mechanism cannot un-wire ==")
_DECLS = (("envelope_drilldown", "app/modules/closing/envelope_drilldown.py"),
          ("rollup_drilldown", "app/modules/closing/rollup_drilldown.py"),
          ("recon_drilldown", "app/modules/closing/recon_drilldown.py"))
for _mod, _path in _DECLS:
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
    check("G1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel", "def tie_out")))
    check("G2(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("G3(%s) and it reads NOTHING from the database" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase")))
check("G4 the two cash tiles are declared BESIDE the counts they share a fact with, not in a file "
      "of their own — `_money_issues` is one home, so its two folds live together",
      hasattr(rcd, "submission_catalogue") and hasattr(rcd, "catalogue")
      and not os.path.exists(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                          "submissions_drilldown.py")))
_FLOATS = [f for _m, _p in _DECLS for f in re.findall(
    r"\d+\.\d+", _code_only(os.path.join(ROOT, "backend", _p)))]
check("G5 no new declaration holds a NUMBER of its own — no tolerance, no rate, no dollar, and not "
      "an accumulator seed either. The rules that produce these figures live in `_money_issues` and "
      "`envelope_report.count_fields`; a copy here could drift from the one actually applied. This "
      "is the SAME rule the batch-6 lock applies (§E2), deliberately, so two locks cannot disagree "
      "about what counts as a constant",
      _FLOATS == [], _FLOATS)
check("G6 the envelope receipt's statuses are READ from `envelope_report.STATUSES`, so a status "
      "added there cannot leave a tile here counting the wrong rows",
      evd.STATUSES == tuple(evr.STATUSES) and "STATUSES = tuple(_envelope_report.STATUSES)" in open(
          os.path.join(ROOT, "backend", "app", "modules", "closing", "envelope_drilldown.py"),
          encoding="utf-8").read())
_ev_body = _body("def envelope_report(", _rt)
check("G7 the receipt endpoint folds its totals and its rollup ONCE each and serves those same "
      "objects to the screen AND to the drill-down, so a tile and its panel cannot disagree",
      _ev_body.count("_totals = envelope_report_mod.totals(out)") == 1
      and _ev_body.count("_groups = envelope_report_mod.by_employee(out)") == 1
      and '"totals": _totals,' in _ev_body and '"by_employee": _groups,' in _ev_body
      and "_envelope_drilldown.drilldowns(out, _totals," in _ev_body
      and "_envelope_drilldown.by_employee_drilldowns(\n                out, _groups," in _ev_body)
check("G8 the processor term is read ONCE in that endpoint and dereferenced by both the selector's "
      "labels and the drill-down's wording — two reads would be two copies of one fact",
      _ev_body.count("_carrier_term(client, org_id, envelope_report_mod.ENVELOPE_BASIS_TERM_KEY)") == 1
      and "basis_options(_processor)" in _ev_body)
_rl_body = _body("def closing_rollup(", _rt)
check("G9 the rollup endpoint KEEPS the corrected store-day figure its overlay loop computes, "
      "instead of applying the delta and discarding it",
      "_sd_final[(_k, _dd)][_fld] = round(_rv + _delta, 2)" in _rl_body
      and "_sd_dm.add((_k, _dd))" in _rl_body)
check("G10 and the drill rows read `verified` from the SAME set the coverage figure is counted "
      "from, through the same `_vkey` — the tile and its rows cannot disagree about which "
      "store-days a DM signed off",
      '"verified": _vk in verified_keys and _vk in submitted_keys' in _rl_body
      and "_vk = _vkey(_m.get(\"store_code\"), _dd)" in _rl_body)
check("G11 the count figures are accumulated SEPARATELY from the money the overlay walks — a figure "
      "the overlay does not know about has no business being handed to it",
      "_sd_counts, _sd_meta = {}, {}" in _rl_body
      and "_sdc = _sd_counts.setdefault(" in _rl_body)
_sb_body = _body("def closing_submissions(", _rt)
check("G12 the submissions endpoint serves NO panels to a caller whose money figures it already "
      "withholds — the evidence behind a hidden figure is the hidden figure",
      "if can_review:" in _sb_body and "_sub_drill = None" in _sb_body
      and '"drilldown": _sub_drill' in _sb_body)
check("G13 and the feed each cash row was compared against is dereferenced from the day read, not "
      "named at the call site",
      "_sub_sales_tables = tuple(sorted({t for d in day_cache.values()" in _sb_body)

_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"),
               encoding="utf-8").read()
_named = {t for fam in (EV, EVG, RL, SUB) for v in fam.values() for r in v["rows"]
          for t in r["sources"] if t}
_unlabelled = sorted(t for t in _named if ("  %s:" % t) not in _labels)
check("G14 every table these drills can name has a PLAIN-WORDS label — a manager never sees a table "
      "name (§19.38, owner: 'hide database names from the users')",
      _unlabelled == [], _unlabelled)
check("G15 every reason these declarations can emit has a SENTENCE in the one home for those words",
      all(k in rd.UNMEASURED_REASONS
          for k in ("not_counted", "no_sales_feed_match", "not_computed")))

_env_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                              "envelope-report", "page.tsx"), encoding="utf-8").read()
_dash = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing", "page.tsx"),
             encoding="utf-8").read()
for _n, _pg in (("closing/envelope-report", _env_page), ("closing", _dash)):
    check("G16(%s) the screen renders the SHARED panel and hand-rolls none of it" % _n,
          "DrillDownPanel" in _pg and "tie_out" not in _pg)
    check("G17(%s) and offers each figure's link through the SHARED offer rule, never a local copy "
          "of the loop" % _n,
          "useDrillTile(" in _pg and "const dl = (...pairs: string[]) => {" not in _pg)
check("G18 on the dashboard, a provenance link inside a clickable tile stops the click reaching it "
      "— the card opens the ROWS and the link opens WHERE THE FIGURE CAME FROM, and a reader asked "
      "for one of them",
      "ev.stopPropagation()" in _dash)
check("G19 EVERY FIGURE THE CATALOGUES DECLARE IS REACHABLE ON SCREEN — a panel nobody can open is "
      "the un-wiring §CORE-17 exists to stop",
      all(("'%s'" % k) in _env_page for k in (t["key"] for t in evd.catalogue()))
      and all(("'%s'" % k) in _dash for k in (t["key"] for t in rld.catalogue()))
      and all(("'%s'" % k) in _dash for k in (t["key"] for t in rcd.submission_catalogue())),
      [k for k in (t["key"] for t in evd.catalogue()) if ("'%s'" % k) not in _env_page]
      + [k for k in (t["key"] for t in rld.catalogue()) if ("'%s'" % k) not in _dash])

print("\n== H. narrowing is applied where it is needed and NOWHERE else ==")
check("H1 the dashboard's two cash tiles ARE narrowed: their rows come from an endpoint this screen "
      "filters on the client, so the server's panel covers more than the tile does",
      "narrowPanels(subMeta?.drilldown || {}, (_r, k) => subSelected.has(k))" in _dash
      and "screenKey({" in _dash)
check("H2 and the selection is keyed EXACTLY as the server keyed its rows",
      all(("%s:" % f) in _dash.split("const subSelected")[1].split("}))")[0]
          for f in rcd.SUBMISSION_KEYS))
check("H3 those two tiles are READ OFF their narrowed panels, so the number on the card and the "
      "rows behind it are one fold",
      "cashPanels.cash_short ? (cashPanels.cash_short.figure?.amount ?? 0) : cashShortRaw" in _dash
      and "cashPanels.cash_over ? (cashPanels.cash_over.figure?.amount ?? 0) : cashOverRaw" in _dash)
check("H4 with the client fold kept as the FALLBACK for the cases there is no panel — a caller "
      "without money review, and a panel the server had to cap",
      "const cashShortRaw = useMemo(" in _dash and "const cashOverRaw = useMemo(" in _dash)
check("H5 the dashboard's ten rollup tiles are NOT narrowed, and must not be: every filter on that "
      "endpoint is a query parameter, so the server already folded the store-days on screen",
      "narrowPanels(data?.drilldown" not in _dash
      and "const rollupPanels = useMemo<Record<string, DrillPanel>>(() => data?.drilldown || {}" in _dash)
check("H6 the envelope receipt is NOT narrowed either, for the same reason — date range, stores, "
      "markets, employees, status and basis are all query parameters there",
      "drill-narrow" not in _env_page and "narrowPanels" not in _env_page)

print("\n== I. registration ==")
_IDX = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
for _f in ("envelope_drilldown.py", "rollup_drilldown.py", "harness_batch7_drilldown.py"):
    check("I1(%s) registered in the index" % _f, _f in _IDX)
_mg = open(os.path.join(ROOT, "backend", "app", "modules", "core", "module_graph.py"),
           encoding="utf-8").read()
check("I2 both new declarations are callers of the one home in the module graph",
      "envelope_drilldown.py" in _mg and "rollup_drilldown.py" in _mg)
check("I3 the harness is locked into CI",
      "harness_batch7_drilldown.py" in open(
          os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml"),
          encoding="utf-8").read())
_ledger = open(os.path.join(ROOT, "backend", "harness_drilldown_pending.txt"), encoding="utf-8").read()
for _r in ("/closing/envelope-report", "/closing"):
    check("I4(%s) reads `shared` in the coverage ledger" % _r,
          re.search(r"^%s\tshared\t\S" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for x in F:
        print("  -", x)
    sys.exit(1)
