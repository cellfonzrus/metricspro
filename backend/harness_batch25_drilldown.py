"""PROOF + BUILD LOCK — batch 25 of §CORE-17: INVENTORY AGING, at DEVICE grain (§CORE-17.3y).

OWNER ASK 2026-10-26, verbatim, quoting this page's own banner: *"124 devices in the 9-12 day window
- sell before day 12 / $29,598.76 at risk of being billed unsold. These have days left; act now.,
need a drill donw for these , whic store market date range etc"*.

WHAT THIS PROVES, BY DRIVING THE REAL DECLARATION
  * `asset/device_asset_drilldown.aging_*` over device rows shaped exactly as `GET /asset/aging`
    stamps them — one panel per band plus the footer total, each folded over THAT band's devices.
  * The SEVENTH grain, measured off the panels as a SET compared for EQUALITY (never a `>=` bound),
    mutation-tested with a ghost swapped in at unchanged size.
  * The owner's three words on every row: which store, which market, which date.

WHAT IT LOCKS SO IT CANNOT UN-WIRE (CLAUDE.md)
  C  BAND ISOLATION — a band's panel holds that band's devices and nothing else, in BOTH directions.
  D  one fact, one home: the provenance stamp and its value are DEREFERENCED from the module that
     writes them, and the declaration carries no literal copy of either, nor of a relation name.
  E  two relations, per ROW — and the IMEI key is spelled the way EACH relation spells it.
  G  a cost nobody recorded is SHOWN WITHOUT A FIGURE and not counted: `$0.00` and "never recorded"
     are different facts, and the report's own footer coerces the second into the first.
  H  the four REFUSALS, each locked against the declaration's own text and against the page.
  I  PURE: the declaration mutates nothing it is handed.
  J  the ratchet and the honest re-classification of a route the audit mis-pinned as `flat`.
  K  registration: the index, the graph, the labels, the migrations and the guard job.

Run: `cd backend && python3 harness_batch25_drilldown.py`  (DB-free, stdlib only.)
"""
import copy
import glob
import os
import re
import sys

from app.modules.asset import device_asset_drilldown as dd
from app.modules.commcalc import data_lineage_registry as lineage
from app.modules.core import report_drilldown as rd
from app.modules.payables import unsold_inventory as unsold

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A MUTATION CONTROL. `red` is True when the mutated world really does break the thing the
    check above asserts — a check that cannot fail is not a check."""
    check("MUT " + name, bool(red))


def read(rel):
    p = f"{ROOT}/{rel}"
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def labels(panel):
    return sorted(r.get("label") or "" for r in (panel.get("rows") or ()))


def tables_of(r):
    return sorted({o["table"] for o in (r.get("origins") or ()) if o.get("table")})


def row_by(panel, label):
    for r in panel.get("rows") or ():
        if r.get("label") == label:
            return r
    return {}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — device rows shaped as `GET /asset/aging` stamps them, both sources, every band.
# Three warning-window devices in two markets, two missed, one safe, one unknown-age, one $0 owed
# and one whose cost was NEVER RECORDED — the row the report's own footer reads as $0.00.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def device(imei, band, store, market, owed, held, acquired, *, ledger=False, model="Model X",
           due=None, overdue=None, oop=None):
    r = {"esn_imei": imei, dd.BAND_FIELD: band, "store": store, "market": market,
         "owed_to_vip": owed, "days_aged": held, "acquired_date": acquired,
         "device_model": model, "due_date": due, "days_overdue": overdue,
         "out_of_pocket": oop, "sell_within_days": 12}
    if ledger:
        r[unsold.SOURCE_FIELD] = unsold.SOURCE_LEDGER
    else:
        r["id"] = 1000 + int(imei)
    return r


ROWS = [
    device("1", dd.BAND_WARN, "B-3735", "Chicago", 213.81, 10, "2026-10-16", ledger=True),
    device("2", dd.BAND_WARN, "B-3735", "Chicago", 213.81, 11, "2026-10-15", ledger=True),
    device("3", dd.BAND_WARN, "B-5601", "Chicago North", 319.00, 9, "2026-10-17", ledger=True),
    device("4", dd.BAND_MISSED, "B-1598", "New Jersey", 500.00, 70, "2026-08-17", due="2026-09-06",
           overdue=50, oop=True),
    device("5", dd.BAND_MISSED, "B-1598", "New Jersey", 100.00, 65, "2026-08-22", oop=True),
    device("6", dd.BAND_SAFE, "B-117", "Long Island", 275.00, 3, "2026-10-23"),
    device("7", dd.BAND_UNKNOWN_AGE, "B-60TH", "Philadelphia", 88.50, None, None),
    device("8", "zero", "B-117", "Long Island", 0.0, 2, "2026-10-24"),
    # THE ROW THE FOOTER MIS-READS: no cost was ever recorded. Not $0.00.
    device("9", "zero", "B-1800", "Long Island", None, 4, "2026-10-22"),
]
WARN_OWED = 213.81 + 213.81 + 319.00                     # 746.62
MISSED_OWED = 600.00
SAFE_OWED = 275.00
UNKNOWN_OWED = 88.50
# The report's own footer: `float(x or 0)` over EVERY row, so the unrecorded cost enters it as zero.
TOTAL_AMOUNT = round(WARN_OWED + MISSED_OWED + SAFE_OWED + UNKNOWN_OWED, 2)

OUT = {
    "buckets": {dd.BAND_SAFE: {"count": 1, "owed": SAFE_OWED, "rows": [ROWS[5]]},
                dd.BAND_WARN: {"count": 3, "owed": round(WARN_OWED, 2), "rows": ROWS[0:3]},
                dd.BAND_MISSED: {"count": 2, "owed": MISSED_OWED, "rows": ROWS[3:5]}},
    "unknown_age": {"count": 1, "owed": UNKNOWN_OWED, "rows": [ROWS[6]]},
    "zero_inventory": {"count": 2, "rows": ROWS[7:9]},
    "totals": {"flagged_count": 6, "flagged_owed": round(WARN_OWED + MISSED_OWED + SAFE_OWED, 2),
               "total_amount": TOTAL_AMOUNT, "total_amount_column": "owed_to_vip",
               "total_phones_outstanding": 9},
}
PANELS = dd.aging_drilldowns(OUT, ROWS)
DECL = read("backend/app/modules/asset/device_asset_drilldown.py")
PAGE = read("frontend/src/app/(platform)/commcalc/asset/aging/page.tsx")
ROUTER = read("backend/app/modules/asset/router.py")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the declaration drives the real producer's own rows ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 FIVE figures are declared and every one is served over this fixture — the footer total "
      "and the four bands",
      sorted(PANELS) == ["missed_owed", "total_amount", "under45_owed", "unknown_age_owed",
                         "warn_owed"],
      str(sorted(PANELS)))
check("A2 THE OWNER'S BAND TIES TO THE CENT, which is the whole claim: the devices behind the "
      "banner add up to the figure the banner shows",
      PANELS["warn_owed"]["figure"]["amount"] == round(WARN_OWED, 2)
      and PANELS["warn_owed"]["tie_out"]["ties"] is True,
      str(PANELS["warn_owed"]["tie_out"]))
check("A3 and so does every other band, and the footer total over every device",
      all(PANELS[k]["tie_out"]["ties"] is True
          for k in ("under45_owed", "missed_owed", "unknown_age_owed", "total_amount")),
      str({k: PANELS[k]["tie_out"]["ties"] for k in PANELS}))
check("A4 the figure each panel explains is the RESPONSE'S OWN, read through `aging_totals` and "
      "never recomputed — a dereference adds nothing up",
      dd.aging_totals(OUT) == {"total_amount": TOTAL_AMOUNT,
                               "under45_owed": SAFE_OWED, "warn_owed": round(WARN_OWED, 2),
                               "missed_owed": MISSED_OWED, "unknown_age_owed": UNKNOWN_OWED},
      str(dd.aging_totals(OUT)))
check("A5 a figure the response did NOT produce is absent rather than zero, so `tiles()` serves no "
      "panel asserting a number nobody computed",
      "warn_owed" not in dd.aging_totals(
          {"buckets": {dd.BAND_WARN: {"count": 0}}, "totals": {}}))
check("A6 AN EMPTY REPORT DRILLS TO NOTHING — no rows at all, and no totals at all, each yield {}",
      dd.aging_drilldowns(OUT, []) == {} and dd.aging_drilldowns({}, ROWS) == {})
check("A7 a band with devices but no figure, and a figure with no devices, BOTH serve no panel — "
      "the second is `tiles()`' rule and the first is that rule applied to the row list",
      "under45_owed" not in dd.aging_drilldowns(
          {"buckets": {dd.BAND_WARN: {"owed": round(WARN_OWED, 2)}}}, ROWS)
      and "unknown_age_owed" not in dd.aging_drilldowns(OUT, ROWS[0:3]))
check("A8 a caller holding only the RESPONSE gets the devices the response carries, so the panel "
      "works without the producer's uncapped list",
      sorted(dd.aging_drilldowns(OUT)) == sorted(PANELS)
      and dd.aging_drilldowns(OUT)["warn_owed"]["rows_total_count"] == 3)
check("A9 and `aging_rows` reads every band of the response, including the two collapsed lists — a "
      "device the response carries and the panel cannot see is a silent gap",
      len(dd.aging_rows(OUT)) == len(ROWS))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE GRAIN — an unsold DEVICE, and it is the family's seventh ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
SUBJECTS = {lab for k in PANELS for lab in labels(PANELS[k])}
EXPECT = {"B-3735 · 1", "B-3735 · 2", "B-5601 · 3", "B-1598 · 4", "B-1598 · 5",
          "B-117 · 6", "B-60TH · 7", "B-117 · 8", "B-1800 · 9"}
check("B1 the subjects are DEVICES, as a SET compared for EQUALITY — never a `>=` bound, which "
      "`harness_subject_set_lock.py` §B1 fails the build on and which has caught two batches",
      SUBJECTS == EXPECT, str(sorted(SUBJECTS ^ EXPECT)))
_ghost = dd.aging_drilldowns(OUT, ROWS[:-1] + [device("99", "zero", "GHOST", "Nowhere", None, 1,
                                                      "2026-10-25")])
ctl("B1a control: a GHOST SWAPPED IN AT UNCHANGED SIZE really breaks B1 — so the equality is "
    "about the devices and not about how many there are",
    {lab for k in _ghost for lab in labels(_ghost[k])} != EXPECT)
check("B2 a device is addressed by the store AND the IMEI, so two devices in one store are two rows "
      "rather than one subject counted twice",
      len([lab for lab in labels(PANELS["warn_owed"]) if lab.startswith("B-3735")]) == 2)
check("B3 THE SEVENTH GRAIN IS WRITTEN DOWN in the module header, beside the six it is not — an "
      "unstated grain is how a store's exposure comes to stand as a device's evidence",
      "8. an unsold DEVICE" in DECL and "SEVENTH grain" in DECL)
check("B4 and the declaration folds a BAND over its OWN devices rather than handing `tiles()` one "
      "row list, with the reason stated — `ROW_CAP` over every device would very likely omit the "
      "ones the owner clicked on",
      "rows_of" in DECL and "ROW_CAP" in DECL.split("# §8", 1)[1])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. BAND ISOLATION — in both directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("C1 the warning band's panel holds EXACTLY its three devices — no device of another band is "
      "in it, counted or as context",
      labels(PANELS["warn_owed"]) == ["B-3735 · 1", "B-3735 · 2", "B-5601 · 3"],
      str(labels(PANELS["warn_owed"])))
check("C2 and every band's panel holds exactly its own, which is what makes the count on the banner "
      "readable off `rows_total_count`",
      [PANELS[k]["rows_total_count"] for k in
       ("under45_owed", "warn_owed", "missed_owed", "unknown_age_owed")] == [1, 3, 2, 1])
check("C3 EVERY counted row is counted — a band panel shows nothing for context, so nothing in it "
      "is quietly excluded from the figure it explains",
      all(PANELS[k]["tie_out"]["shown_not_counted"] == 0
          for k in ("under45_owed", "warn_owed", "missed_owed", "unknown_age_owed")))
check("C4 the footer total's panel holds EVERY device, including the two neither banded nor priced",
      PANELS["total_amount"]["rows_total_count"] == len(ROWS))
check("C5 the band comes from the PRODUCER'S OWN stamp and is never re-decided here — the ladder is "
      "the carrier's declared window (§COMMCALC-26) and a second verdict is a second ladder",
      dd.band_rows(ROWS, dd.BAND_WARN) == ROWS[0:3]
      and dd.band_rows(ROWS, dd.BAND_ALL) == ROWS
      and dd.band_rows(ROWS, "no-such-band") == [])
_restamped = copy.deepcopy(ROWS)
for _r in _restamped[0:3]:
    _r[dd.BAND_FIELD] = dd.BAND_MISSED
ctl("C5a control: re-stamping the producer's band really moves the devices, so C5 reads the stamp "
    "rather than re-deriving a band from the day count",
    dd.band_rows(_restamped, dd.BAND_WARN) == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. ONE FACT, ONE HOME — the stamp, its value and the relation names ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
SEC8 = DECL.split("# §8", 1)[1]
check("D1 the provenance STAMP and its VALUE are dereferenced from the module that writes them",
      dd._SOURCE_FIELD is unsold.SOURCE_FIELD and dd._SOURCE_LEDGER is unsold.SOURCE_LEDGER)
check("D2 and the declaration carries NO literal copy of either — a literal here would stop "
      "matching the day the key moved, silently (§19.18)",
      '"_source"' not in SEC8 and "'_source'" not in SEC8
      and '"payable_ledger"' not in SEC8 and "'payable_ledger'" not in SEC8)
check("D3 the TWO relation names are dereferenced from the feed-lineage registry, not spelled here",
      dd.DEVICE_PAYABLE_LEDGER is lineage.DEVICE_PAYABLE_LEDGER
      and dd.ASSET_LEDGER is lineage.ASSET_LEDGER)
check("D4 and no relation name is a literal anywhere in this module — the whole-file axis, because "
      "a second copy added to another section is the same defect",
      not re.search(r"""['"](?:asset_ledger|device_payable_ledger|b2b_inventory)['"]""", DECL),
      str(re.findall(r"""['"](?:asset_ledger|device_payable_ledger|b2b_inventory)['"]""", DECL)))
check("D5 THE WRITER WRITES THROUGH THE NAME TOO — naming a fact is half the fix and deleting every "
      "copy is the other half, so the shaping module stamps `SOURCE_FIELD` rather than a literal",
      re.search(r"(?m)^\s+SOURCE_FIELD:\s*SOURCE_LEDGER,\s*$",
                read("backend/app/modules/payables/unsold_inventory.py")) is not None)
check("D6 and the registry says WHY this relation is a NAME constant and not a registered ingest "
      "edge — the platform writes it from feeds that are registered already",
      re.search(r"NAME constant[^\n]*\n?[^\n]*INGEST_TABLES_BY_MODULE",
                read("backend/app/modules/commcalc/data_lineage_registry.py")
                .split("DEVICE_PAYABLE_LEDGER", 1)[0][-1200:], re.S) is not None
      or "NAME constant" in read("backend/app/modules/commcalc/data_lineage_registry.py")
      .split("DEVICE_PAYABLE_LEDGER = ", 1)[0][-1400:])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. TWO RELATIONS, PER ROW — and each spells the IMEI its own way ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("E1 a device the platform owes for names the per-device payable ledger, and a consignment "
      "device the distributor's unit ledger — PER ROW, because naming one for both would be wrong "
      "on every row of the other tenant",
      tables_of(row_by(PANELS["warn_owed"], "B-3735 · 1")) == [lineage.DEVICE_PAYABLE_LEDGER]
      and tables_of(row_by(PANELS["missed_owed"], "B-1598 · 4")) == [lineage.ASSET_LEDGER])
check("E2 and the panel's own source roll-up says so — both relations, with how many rows touched "
      "each, which is the owner's 'where is this from' answered in one read",
      sorted((s["table"], s["rows"]) for s in PANELS["total_amount"]["sources"])
      == [(lineage.ASSET_LEDGER, 6), (lineage.DEVICE_PAYABLE_LEDGER, 3)],
      str(PANELS["total_amount"]["sources"]))
ctl("E2a control: a row with no provenance stamp really falls to the consignment ledger, so E1 "
    "reads the stamp instead of defaulting both ways",
    dd.device_tables({}) == (lineage.ASSET_LEDGER,)
    and dd.device_tables({unsold.SOURCE_FIELD: unsold.SOURCE_LEDGER})
    == (lineage.DEVICE_PAYABLE_LEDGER,))
check("E3 THE KEY IS SPELLED THE WAY EACH RELATION SPELLS IT — `imei` on the payable ledger and "
      "`esn_imei` on the consignment ledger. Naming one for both hands a support query a column "
      "half the rows' table does not have",
      dd.device_keys(ROWS[0]) == {"imei": "1", "store": "B-3735"}
      and dd.device_keys(ROWS[3]) == {"id": 1004, "esn_imei": "4"})
check("E4 and the key really travels on the drill row's origin, which is how a human finds the "
      "device again",
      (row_by(PANELS["warn_owed"], "B-3735 · 1")["origins"][0]["keys"]
       == {"imei": "1", "store": "B-3735"}))
check("E5 every origin names the COLUMN the figure was read out of, so a reader learns which money "
      "this is and not only which table",
      all(o.get("column") == dd.COL_OWED
          for r in PANELS["total_amount"]["rows"] for o in r["origins"]))
check("E6 and every origin is TRACEABLE — a panel reporting untraceable provenance is a gap, and "
      "this report has two known relations and no third",
      PANELS["total_amount"]["untraceable_origins"] == 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. THE OWNER'S THREE WORDS — which store, which market, which date ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_w = row_by(PANELS["warn_owed"], "B-5601 · 3")
check("F1 the STORE is in the row's own label, so the panel reads as a store list without opening "
      "a column",
      all((r.get("label") or "").split(" · ")[0] for r in PANELS["warn_owed"]["rows"]))
check("F2 the MARKET is on every row, named",
      [r["fields"]["market"] for r in PANELS["warn_owed"]["rows"]]
      == ["Chicago", "Chicago", "Chicago North"])
check("F3 the DATE is on every row twice over — the purchase date itself, and the days held it "
      "implies, so 'date range' can be read either way",
      _w["fields"]["purchased"] == "2026-10-17" and _w["fields"]["days_held"] == 9)
check("F4 and in words on the row's detail line, so a reader skimming needs no second column",
      _w["fields"]["detail"] == "9 day(s) held · purchased 2026-10-17",
      _w["fields"]["detail"])
check("F5 a device with NO usable purchase date says so in words rather than showing a blank that "
      "reads like a zero-day-old handset",
      row_by(PANELS["unknown_age_owed"], "B-60TH · 7")["fields"]["detail"]
      == "no usable purchase date · purchase date not recorded")
check("F6 the deadline and the verdict the page already prints per row travel on the panel too, so "
      "a reader who opened the band from the banner need not go back to the table",
      row_by(PANELS["missed_owed"], "B-1598 · 4")["fields"]["due_date"] == "2026-09-06"
      and row_by(PANELS["missed_owed"], "B-1598 · 4")["fields"]["days_overdue"] == 50
      and row_by(PANELS["missed_owed"], "B-1598 · 4")["fields"]["out_of_pocket"] is True)
check("F7 and the PAGE renders those fields as its own columns off the row's `fields` — a field the "
      "server carries and the screen drops is a question answered nowhere",
      all(("'%s'" % f) in PAGE for f in ("market", "purchased", "days_held", "device_model",
                                         "due_date")))
check("F8 the panel NOTE says what the rows are and that the bands move with the calendar — a band "
      "total that changes with no ledger change otherwise reads as a defect",
      "AS OF TODAY" in (PANELS["warn_owed"]["note"] or "")
      and "per unsold device" in (PANELS["warn_owed"]["note"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. A COST NOBODY RECORDED IS NOT $0.00 ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_u = row_by(PANELS["total_amount"], "B-1800 · 9")
_z = row_by(PANELS["total_amount"], "B-117 · 8")
check("G1 the device whose cost was never recorded is SHOWN, with no figure and with its reason — "
      "the report's own footer reads it as $0.00 through `float(x or 0)`",
      _u["amount"] is None and _u["present"] is False
      and _u["reason"] == "amount_not_recorded" and _u["reason_label"],
      str({k: _u.get(k) for k in ("amount", "present", "reason", "reason_label")}))
check("G2 and it is NOT counted in the figure, while the device that genuinely owes nothing IS — "
      "two different facts, told apart",
      _u["in_figure"] is False and _z["in_figure"] is True and _z["amount"] == 0.0)
check("G3 the panel SAYS how many rows it shows without counting them, so the reader subtracts "
      "nothing and wonders about nothing",
      PANELS["total_amount"]["tie_out"]["shown_not_counted"] == 1
      and "shown for context" in PANELS["total_amount"]["tie_out"]["words"])
check("G4 and the total still ties, because the footer's coercion adds zero either way — the gap is "
      "VISIBLE rather than moved",
      PANELS["total_amount"]["tie_out"]["ties"] is True)
check("G5 the amount reader is strict about what a number is: a blank, a bool and a word are each "
      "unrecorded, never zero",
      [dd._device_amount({"owed_to_vip": v}) for v in (None, True, "", "n/a", 0, "12.5")]
      == [None, None, None, None, 0.0, 12.5])
ctl("G5a control: reading the cost as `float(x or 0)` really collapses the two, so G1/G5 are about "
    "the distinction and not about the fixture",
    float((ROWS[8].get("owed_to_vip") or 0)) == float((ROWS[7].get("owed_to_vip") or 0)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. THE FOUR REFUSALS, each with a ruling behind it ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("H1 THE DEVICE COUNTS GET NO PANEL — each is the cardinality of the money panel beside it, "
      "and `rows_total_count` already serves it (the batch-11 `store_count` ruling)",
      not any(k.endswith("_count") for k in PANELS)
      and "total_phones_outstanding" not in PANELS)
check("H1b and the band count really IS readable off the band's own panel, which is what makes the "
      "refusal a refusal rather than a gap",
      PANELS["warn_owed"]["rows_total_count"] == OUT["buckets"][dd.BAND_WARN]["count"])
check("H2 `flagged_owed` / `flagged_count` get none: Σ of the three band tiles, so a tie-out "
      "against either could not fail (§CORE-17.3h as batch 10 extended it)",
      "flagged_owed" not in PANELS and "flagged_count" not in PANELS)
check("H3 THE OUT-OF-POCKET HEADLINE gets none: since §COMMCALC-33 it is the cost NET of what the "
      "carrier already recovered per device, so a fold over this report's own amount column would "
      "explain a DIFFERENT number — correctly, and uselessly",
      not any("out_of_pocket" in k for k in PANELS)
      and "COMMCALC-33" in DECL.split("def aging_catalogue", 1)[1].split("def aging_drilldowns", 1)[0])
check("H4 `age_buckets` gets none: the same bands over the same devices, counted rather than "
      "priced, which would be two answers to one question",
      not any("age_bucket" in k for k in PANELS) and "age_buckets" in SEC8)
check("H5 EVERY REFUSAL IS WRITTEN DOWN IN THE DECLARATION ITSELF, with its ruling — an excuse "
      "nobody wrote down is indistinguishable from an oversight (CLAUDE.md)",
      all(t in SEC8 for t in ("tautology", "batch 10", "COMMCALC-33", "two answers to one")))
check("H6 and the PAGE says so where the reader would look for the missing link — the count beside "
      "the total carries the reason in a comment rather than silently lacking a button",
      "tautology" in PAGE and "batch-11" in PAGE)
check("H7 the page offers a link ONLY for a figure the server served, through the one offer rule — "
      "a link that opens an empty panel reads like a bug (§CORE-17.3c)",
      "useDrillTile" in PAGE and "drill.dl(" in PAGE
      and not re.search(r"drilldown\?\.\w+\s*&&\s*<DrillDownPanel", PAGE))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. PURE — the declaration mutates nothing it is handed ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_before = copy.deepcopy(ROWS)
_out_before = copy.deepcopy(OUT)
dd.aging_drilldowns(OUT, ROWS)
check("I1 the device rows are unchanged after the fold — a presenter that stamps the rows it "
      "explains would change the report it is evidence for",
      ROWS == _before)
check("I2 and so is the response it read its figures out of",
      OUT == _out_before)
check("I3 the module imports no framework and no clock — the band is the producer's stamp, so "
      "nothing here needs to know what day it is",
      not re.search(r"(?m)^(from|import)\s+(fastapi|datetime|time|os|app\.core)\b", DECL))
check("I4 and names no carrier, tenant, product or store (RULE TWO) — the band labels this module "
      "emits are window-neutral words, never one carrier's day count",
      not re.search(r"(?i)\b(boost|luxelink|cellfonz|vidapay|total\s+wireless|t-mobile)\b", DECL))
check("I5 THE PAGE'S BAND LABELS ARE THE CARRIER'S DECLARED WINDOW TOO, not 45/60 typed in: the "
      "banner already resolved it and the three tiles beside it were still printing one carrier's "
      "contract to every tenant",
      re.search(r"label:`Under \$\{windowLabel\.warn\} Days`", PAGE) is not None
      and re.search(r"label:`Over \$\{windowLabel\.sell\} Days", PAGE) is not None)
check("I6 and the drill key on each tile is the SERVER'S total key, never the bucket name — a "
      "renamed bucket cannot silently open another band's panel",
      all(("drill:'%s'" % k) in PAGE for k in ("under45_owed", "warn_owed", "missed_owed")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE RATCHET — and an honest re-classification ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("backend/harness_drilldown_coverage.py")
LEDGER_TXT = read("backend/harness_drilldown_pending.txt")
MY_ROUTE = "/commcalc/asset/aging"
OWES_UNCHANGED = 18


def _pin(src):
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else None


def _owes(src):
    return sorted(ln.split("\t")[0] for ln in src.splitlines()
                  if len(ln.split("\t")) > 1 and ln.split("\t")[1].strip() == "owes")


def _row(src, route):
    return next((ln for ln in src.splitlines() if ln.split("\t")[0].strip() == route), "")


def _state(src, route):
    p = _row(src, route).split("\t")
    return p[1].strip() if len(p) > 1 else None


check("J1 THE ROUTE IS NOW `shared`, and the gate's own §D1 then requires the page to render the "
      "shared panel — writing the registry without wiring the caller is not a fix (§19.18)",
      _state(LEDGER_TXT, MY_ROUTE) == "shared"
      and re.search(r"<DrillDownPanel[\s/>]", PAGE) is not None)
check("J2 THE RE-CLASSIFICATION IS REPORTED, NOT QUIETLY TAKEN. The 2026-10-24 audit pinned this "
      "route `flat` — 'already shows the source rows; nothing is summarised above them' — and the "
      "second half was never true. A mis-classified route is how a ratchet stops ratcheting",
      "RE-CLASSIFIED from `flat`" in _row(LEDGER_TXT, MY_ROUTE)
      and "mis-pinned" in DECL or "mis-classified" in DECL)
check("J2b and the declaration itself carries the finding, in its own words",
      "WHY THE LEDGER SAID THIS REPORT WAS `flat`" in DECL)
check("J3 THE PIN DOES NOT MOVE, correctly: a `flat` route was never counted as owing one, so "
      "lowering the pin here would claim a debt was paid that was never on the books",
      _pin(GATE) == OWES_UNCHANGED and len(_owes(LEDGER_TXT)) == OWES_UNCHANGED,
      "pin=%s owes=%s" % (_pin(GATE), len(_owes(LEDGER_TXT))))
ctl("J3a control: the pin reader really reads the gate, so J3 cannot pass on a stale read",
    _pin("OWES_MAX = 99") == 99 and _pin("nothing here") is None)
check("J4 the route is not counted as owing one either", MY_ROUTE not in _owes(LEDGER_TXT))
check("J5 ITS NOTE NAMES THIS BATCH'S SECTION AND WHAT THE DRILL REFUSES TO CLAIM — a drill-down "
      "that does not say what it cannot explain is half a drill-down",
      "CORE-17.3y" in _row(LEDGER_TXT, MY_ROUTE)
      and "REFUSES" in _row(LEDGER_TXT, MY_ROUTE))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== K. REGISTRATION — index, graph, labels, migrations, guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIGS = "\n".join(open(f, encoding="utf-8", errors="ignore").read()
                 for f in glob.glob(f"{ROOT}/database/migrations/*.sql"))
NAMED = sorted({s["table"] for p in PANELS.values() for s in p["sources"] or ()})
check("K1 every relation a panel here can name is KNOWN TO THE MIGRATIONS — a provenance line "
      "pointing at a table nobody creates is a claim, not evidence",
      NAMED and all(re.search(r"(?is)create\s+(table|view|materialized\s+view)[^;]*?\b%s\b" % t,
                              MIGS) for t in NAMED),
      str([t for t in NAMED
           if not re.search(r"(?is)create\s+(table|view|materialized\s+view)[^;]*?\b%s\b" % t,
                            MIGS)]))
SRC_LABELS = read("frontend/src/lib/sourceLabels.ts")
check("K2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a plain "
      "name and never a database name",
      all(re.search(r"(?m)^\s*%s\s*:" % t, SRC_LABELS) for t in NAMED),
      str([t for t in NAMED if not re.search(r"(?m)^\s*%s\s*:" % t, SRC_LABELS)]))
GRAPH = read("backend/app/modules/core/module_graph.py")
check("K3 this declaration is a registered CALLER of the one drill-down home and of the feed "
      "lineage, by hand (§50: `--bless` silently deleted 11 of 12 facts once)",
      "'app/modules/asset/device_asset_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/asset/device_asset_drilldown.py': ('_lineage',)" in GRAPH)
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("K4 the index carries THIS batch's own section heading, once, and the next free letter was "
      "taken rather than an occupied one re-used",
      IDX.count("### §CORE-17.3y") == 1)
SEC = IDX.split("### §CORE-17.3y", 1)[-1].split("\n### ", 1)[0]
check("K4b and names the route, the declaration, the endpoint and the harness",
      all(t in SEC for t in (MY_ROUTE, "device_asset_drilldown", "harness_batch25_drilldown.py"))
      and "GET /asset/aging" in SEC)
check("K4c and states the duplicate check the house rules make a build gate — what was searched "
      "for and what was reused rather than rebuilt",
      "duplicate" in SEC.lower() and "reuse" in SEC.lower())
check("K5 the endpoint really attaches it, through the declaration and not a second fold",
      re.search(r'out\["drilldown"\]\s*=\s*_dd\.aging_drilldowns\(', ROUTER) is not None)
check("K5b and over the UNCAPPED population it just built, with the reason stated — the response's "
      "two collapsed lists are capped for payload size",
      "aging_drilldowns(out, all_rows)" in ROUTER and "UNCAPPED" in ROUTER)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("K6 the guard job RUNS this harness — matched on the `run:` LINE ITSELF, never on the name "
      "appearing somewhere in the workflow, because the paths filter names it too",
      re.search(r"(?m)^\s*run:\s*python3 harness_batch25_drilldown\.py\s*\|", WF) is not None)
check("K6b and its PATHS FILTER names the files it is about, so a change to one cannot skip the "
      "job that checks it",
      all(f in WF for f in ("backend/app/modules/asset/device_asset_drilldown.py",
                            "backend/harness_batch25_drilldown.py")))
check("K7 and this harness is in the ONE DRILL-DOWN HOME's own lock list, so a reader of that "
      "module finds the proof",
      "harness_batch25_drilldown.py" in GRAPH)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
