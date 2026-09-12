"""Harness — the per-line vendor rebate/commission history feed LANDS, and books NOTHING (mig 1005).

DB-FREE, stdlib-only. Every fixture below is SYNTHETIC: the real export contains customer names,
customer identifiers, phone numbers and ZIP codes, so it is never copied into the repo. What is
reproduced here is its SHAPE and the ratios measured from it — the numbers in the docstring are
measurements, the rows are invented.

MEASURED GROUND TRUTH (first real export: 47,253 parsed rows, one store, 2024-01-02 → 2026-08-31):
    data rows            47,252  (+ 1 grand-total FOOTER row)
    invoices              6,094      devices (IMEI)        6,449
    rebate components       258      reversal rows (qty -1) 6,283
    EARNED  (Total Rebate)      $6,748,358.09
    COLLECTED (Collected)               $0.00      ← on every single row
    BALANCE (Balance)           $6,748,358.09

FIVE WAYS THIS FILE MISREPORTS ITSELF, each proven below:

  1. THE FOOTER DOUBLES THE FILE. Ingested as data the totals row makes earned read
     $13,496,716.18 — exactly 2x the true $6,748,358.09. (§25.3 feed_shape.is_footer_row.)
  2. `Unit Rebate` IS UNSIGNED. 6,283 rows are reversals with Quantity = -1 and
     Total Rebate = Unit Rebate x Quantity on 47,252/47,252 rows. Summing the unit column instead
     of the signed total overstates by $630,615.08 ($7,378,973.17 vs $6,748,358.09).
  3. `device_cost` REPEATS PER COMPONENT ROW. ~7 rebate rows share one device, so the naive per-row
     sum reads $41,040,251.81 against a true (deduped-by-IMEI) $5,393,764.59 — 7.61x.
  4. A TRUE DATETIME WITH FRACTIONAL SECONDS DID NOT PARSE. 'Sold On' renders through the ingest
     reader as '2024-03-16 12:39:07.243000'; merchant_portals.iso_date returned None for that, so
     only 104 of 47,252 rows got a date. Regression pinned below.
  5. THREE HEADERS LIE. 'Invoiced At' holds the STORE, 'Related Tracking Number' holds the IMEI
     (while 'Tracking Number' holds the line's number), and 'Region' holds a PERSON'S NAME.

AND THE MONEY RULE: earned is not collected. `BOOKS_TO` is empty and nothing in this feed reaches
the P&L, the Balance Sheet, GP or commission payout while that decision is open.

    python3 backend/harness_vendor_rebate_landing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


from app.modules.commcalc import column_mapping as cm          # noqa: E402
from app.modules.commcalc import feed_shape                    # noqa: E402
from app.modules.commcalc import ingest_slice                  # noqa: E402
from app.modules.commcalc import vendor_rebate_feed as vrf     # noqa: E402
from app.modules.commcalc.merchant_portals import iso_date     # noqa: E402

RK = "vendor_rebate_history"
STORE = "Example Store Brooklyn ZZ0001"          # synthetic; shape = "<name> <code>"
OTHER_STORE = "Example Store Queens ZZ0002"


# ── synthetic rows in the real SHAPE ──────────────────────────────────────────────────────────
def row(invoice, component, qty, unit, sold_on, *, imei="", store=STORE, cost=0.0,
        collected=None, vendor="Program A", balance=None):
    """One landed row. earned = unit x qty, exactly as the source computes Total Rebate."""
    earned = round(unit * qty, 2)
    r = {"invoice_no": invoice, "rebate_name": component, "quantity": qty,
         "unit_amount": unit, "earned_amount": earned,
         "balance_amount": earned if balance is None else balance,
         "sold_on": sold_on, "imei": imei, "store": store, "device_cost": cost,
         "vendor_account": vendor, "period": None}
    if collected is not None:
        r["collected_amount"] = collected
    return r


# One device carrying FOUR rebate components (the real file averages ~7), so the repeated device
# cost is exercised; plus a reversal pair and a second device.
DEVICE_A = "350000000000001"
DEVICE_B = "350000000000002"
FIXTURE = [
    row("INV-1", "Device Payment Rebate",  1, 1000.00, "2024-01-02 11:30:05.847000", imei=DEVICE_A, cost=800.00),
    row("INV-1", "Financing Fee",          1,   50.00, "2024-01-02 11:30:05.847000", imei=DEVICE_A, cost=800.00),
    row("INV-1", "Rate Plan Rebate",       1,  200.00, "2024-01-02 11:30:05.847000", imei=DEVICE_A, cost=800.00),
    row("INV-1", "Protection Coverage",    1,   25.00, "2024-01-02 11:30:05.847000", imei=DEVICE_A, cost=800.00),
    row("INV-2", "Device Payment Rebate",  1,  900.00, "2024-02-11 09:05:00.000000", imei=DEVICE_B, cost=700.00),
    # the reversal: same component, quantity -1, so earned is negative
    row("INV-2", "Device Payment Rebate", -1,  900.00, "2024-02-12 16:44:21.500000", imei=DEVICE_B, cost=700.00),
    row("INV-3", "Rate Plan Rebate",       1,  150.00, "2026-08-31 17:46:36.840000", imei="",       cost=0.0,
        vendor="Program B"),
]
TRUE_EARNED = 1000.00 + 50.00 + 200.00 + 25.00 + 900.00 - 900.00 + 150.00      # 1425.00

# The grand-total FOOTER, exactly as the real export writes it: every NUMERIC column repeated with
# the file's own total, every IDENTITY column blank. That is why ingesting it doubles the file — the
# row is the answer, added back in as if it were another question.
FOOTER = {"invoice_no": "", "rebate_name": "",
          "quantity": 5,                                   # the file's NET quantity
          "unit_amount": 3225.00,                          # Σ unit (unsigned — see section B)
          "earned_amount": TRUE_EARNED, "balance_amount": TRUE_EARNED,
          "sold_on": "", "imei": "", "store": "", "device_cost": 4600.00, "vendor_account": ""}
TRUE_DEVICE_COST_ONCE = 800.00 + 700.00                                          # 1500.00
NAIVE_DEVICE_COST = 800.00 * 4 + 700.00 * 2                                      # 4600.00

print("\nA. THE FOOTER ROW — ingested as data it DOUBLES the file")
req = cm.required_fields(RK)
ok(req == ["invoice_no", "rebate_name"],
   f"the report declares its identity fields, so a footer is detectable by SHAPE: {req}")
ok(feed_shape.is_footer_row(FOOTER, req),
   "the grand-total row (identity columns blank, numbers present) IS classified as a footer")
for i, r in enumerate(FIXTURE):
    ok(not feed_shape.is_footer_row(r, req), f"real record #{i + 1} is NOT mistaken for a footer")
kept, dropped = cm.drop_footer_rows(list(FIXTURE) + [FOOTER], RK, {"org_id": "ORG"})
ok(dropped == 1 and len(kept) == len(FIXTURE), "drop_footer_rows removes exactly the one footer")
ok(round(vrf.totals(kept)["earned"], 2) == TRUE_EARNED,
   f"earned with the footer dropped = {TRUE_EARNED:,.2f} (the truth)")
with_footer = vrf.totals(list(FIXTURE) + [FOOTER])["earned"]
ok(round(with_footer, 2) == round(TRUE_EARNED * 2, 2),
   f"NEGATIVE CONTROL: the footer ingested makes earned {with_footer:,.2f} — exactly 2x, the "
   f"same doubling measured on the real file (13,496,716.18 vs 6,748,358.09)")

print("\nB. `Unit Rebate` IS UNSIGNED — summing it books the reversals as income")
unit_sum = sum(r["unit_amount"] for r in FIXTURE)
ok(round(unit_sum, 2) == 3225.00, f"the unit column sums {unit_sum:,.2f}")
ok(round(unit_sum - TRUE_EARNED, 2) == 1800.00,
   "it overstates by TWICE the reversal (the swing from -900 to +900) — the same shape as the "
   "$630,615.08 overstatement measured on the real file")
ok(all(round(r["unit_amount"] * r["quantity"], 2) == r["earned_amount"] for r in FIXTURE),
   "earned_amount = unit_amount x quantity on every row, as the source computes it")
ok(vrf.totals(FIXTURE)["reversal_rows"] == 1, "the reversal row is COUNTED and reported, not hidden")
ok(("unit_amount" in {f[0] for f in cm.TARGET_FIELDS[RK]}
    and "earned_amount" in {f[0] for f in cm.TARGET_FIELDS[RK]}),
   "both columns are LANDED (the unit rate is kept) — only the summing is opinionated")

print("\nC. device_cost REPEATS PER COMPONENT ROW — a per-row sum multiplies it")
dc = vrf.device_cost_once(FIXTURE)
ok(dc["cost"] == TRUE_DEVICE_COST_ONCE and dc["devices"] == 2,
   f"device_cost_once dedupes by IMEI: {dc['cost']:,.2f} over {dc['devices']} devices")
ok(round(NAIVE_DEVICE_COST / TRUE_DEVICE_COST_ONCE, 2) == 3.07,
   "NEGATIVE CONTROL: the naive per-row sum is 3.07x here (7.61x on the real file: "
   "$41,040,251.81 against a true $5,393,764.59)")
ok(dc["rows_without_imei"] == 1,
   "a row with no IMEI cannot be deduped and is REPORTED, never silently counted or dropped")

print("\nD. FRACTIONAL SECONDS — the regression that left 47,148 of 47,252 rows undated")
ok(iso_date("2024-03-16 12:39:07.243000") == "2024-03-16",
   "REGRESSION: a true datetime with fractional seconds now parses (it returned None before)")
ok(iso_date("2024-01-02 11:30:05.847000") == "2024-01-02", "…and at the file's first row")
for spelling, want in (("2024-03-16 12:39:07", "2024-03-16"),
                       ("06/27/2025 15:52:40", "2025-06-27"),
                       ("8/3/26 4:04 PM", "2026-08-03"),
                       ("2024-03-16", "2024-03-16"),
                       ("Aug 12, 2026", "2026-08-12"),
                       ("12 Aug 2026", "2026-08-12")):
    ok(iso_date(spelling) == want, f"UNCHANGED: {spelling!r} still parses to {want}")
ok(iso_date("") is None and iso_date("not a date") is None,
   "an undatable cell still returns None — never a guessed day")

print("\nE. EVERY ROW BOOKS TO THE MONTH OF ITS OWN SALE (a 32-month file)")
ok(cm.period_source_field(RK) == "sold_on",
   "the period is derived from sold_on — the report's first date-typed field, asked of the registry")
mapped = [dict(r) for r in FIXTURE]
for m in mapped:
    m["sold_on"] = iso_date(m["sold_on"])
mapped, n = cm.derive_row_periods(mapped, RK)
ok(n == len(FIXTURE), f"all {n} rows are period-stamped (none left to a guessed month)")
periods = sorted({m["period"] for m in mapped})
ok(periods == ["August 2026", "February 2024", "January 2024"],
   f"rows land in the months they actually cover: {periods}")
ok(mapped[0]["period_month"] == 1 and mapped[0]["period_year"] == 2024,
   "period_month / period_year are stamped alongside the label")

print("\nF. A RE-UPLOAD REPLACES THIS FILE'S OWN SLICE — and nothing else")
scope = ingest_slice.replace_scope("raw_vendor_rebate", mapped)
ok(ingest_slice.INGEST_PARTITION["raw_vendor_rebate"] == {"partition": "store", "date": "sold_on"},
   "the slice is store ∩ sold_on — the store the file proves it owns, over its own date range")
ok(scope and scope["values"] == [STORE], "the scope names ONLY this file's store")
ok(scope and scope["lo"] == "2024-01-02" and scope["hi"] == "2026-08-31",
   f"…over exactly its own date range ({scope['lo']} → {scope['hi']})")
# another store's rows are outside the slice and therefore survive
other = [dict(r, store=OTHER_STORE) for r in mapped]
ok(all(o["store"] not in scope["values"] for o in other),
   "another store's rows are OUTSIDE the delete — the two-portal wipe incident (§2) cannot recur here")
blank = mapped + [dict(mapped[0], store="")]
ok(ingest_slice.replace_scope("raw_vendor_rebate", blank) is None,
   "a file with ANY blank store proves no slice → no delete is run (an honest append, never a guess)")
nodate = [dict(m, sold_on=None) for m in mapped]
ok(ingest_slice.replace_scope("raw_vendor_rebate", nodate) is None,
   "a file with no usable dates likewise proves no slice — this is why D had to be fixed first: "
   "unparsed dates would have made the re-upload DUPLICATE instead of replace")

print("\nG. EARNED IS NOT COLLECTED — the money rule for this feed")
ok(vrf.BOOKS_TO == (),
   "BOOKS_TO is EMPTY: this feed books to no P&L head, no Balance-Sheet line, no payout")
t = vrf.totals(FIXTURE)
ok(t["booked_to"] == [], "every summary carries `booked_to: []` — the surface says so too")
ok("collected" in t and "earned" in t and "outstanding" in t,
   "earned, collected and outstanding are reported as THREE separate figures")
ok(t["earned"] + 0 == TRUE_EARNED and t["collected"] == 0.0,
   f"the fixture is 100% earned and 0% collected, like the real file "
   f"(earned {t['earned']:,.2f}, collected {t['collected']:,.2f})")
ok(t["outstanding"] == t["earned"] - t["collected"],
   "outstanding is DERIVED (earned - collected), never taken on trust")
ok(not any(k in t for k in ("income", "revenue", "gross_profit", "payout")),
   "no key in the summary names income, revenue, GP or payout — the words would be a claim we "
   "cannot support while Collected is $0.00")
src = open(os.path.join(os.path.dirname(__file__),
                        "app/modules/commcalc/vendor_rebate_feed.py")).read()
for banned in ("carrier_comm", "device_rebate_amount", "activation_rebate_ledger", "journal_entries"):
    ok(banned not in src, f"the module never references the booking path `{banned}`")

# THE DURABLE FORM OF "IT BOOKS NOTHING": a static scan, so a later edit that quietly wires this
# table into a money module fails the build instead of moving a figure nobody was watching. The
# allow-list is the landing path itself — the mapper's TABLE_MAP, the slice-replace partition, the
# lineage registry, and the one read-only endpoint.
_ALLOWED = {"app/modules/commcalc/column_mapping.py", "app/modules/commcalc/ingest_slice.py",
            "app/modules/commcalc/data_lineage_registry.py", "app/modules/commcalc/router.py",
            "app/modules/commcalc/vendor_rebate_feed.py"}
_readers = []
for base, dirs, files in os.walk(os.path.join(os.path.dirname(__file__), "app")):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        full = os.path.join(base, fn)
        rel = os.path.relpath(full, os.path.dirname(__file__))
        try:
            body = open(full, encoding="utf-8").read()
        except Exception:
            continue
        if "raw_vendor_rebate" in body and rel not in _ALLOWED:
            _readers.append(rel)
ok(not _readers,
   f"NO module outside the landing path references commcalc.raw_vendor_rebate (found: {_readers or 'none'}) "
   f"— in particular none of account/coa.py, account/ma_store_pnl.py, account/device_cogs.py, "
   f"account/residual_subs.py or the payout engines")
for money_mod in ("app/modules/account/coa.py", "app/modules/account/device_cogs.py",
                  "app/modules/account/residual_subs.py", "app/modules/account/ma_store_pnl.py"):
    path = os.path.join(os.path.dirname(__file__), money_mod)
    if os.path.exists(path):
        ok("raw_vendor_rebate" not in open(path, encoding="utf-8").read(),
           f"{money_mod} does not read this feed — no P&L / COGS / residual figure can move")

print("\nH. 'not reported' IS NOT 'unpaid' — the distinction the recon precedent requires")
ok(vrf.settlement_status({"earned_amount": 100, "collected_amount": 0}) == vrf.UNPAID,
   "collected 0.00 ⇒ unpaid (the carrier has paid nothing)")
ok(vrf.settlement_status({"earned_amount": 100}) == vrf.UNKNOWN,
   "collected MISSING ⇒ not_reported — a feed that lacks the column must never read as 'unpaid'")
ok(vrf.settlement_status({"earned_amount": 100, "collected_amount": 40}) == vrf.PARTIAL,
   "collected < earned ⇒ partially_collected")
ok(vrf.settlement_status({"earned_amount": 100, "collected_amount": 100}) == vrf.COLLECTED,
   "collected == earned ⇒ collected")
ok(vrf.settlement_status({"earned_amount": -900, "collected_amount": -900}) == vrf.COLLECTED,
   "a fully-settled REVERSAL classifies by magnitude, not by sign")
counts = vrf.status_counts(FIXTURE)
ok(len(counts) == 1 and counts[0]["status"] == vrf.UNKNOWN,
   "the fixture's rows carry no Collected cell, so they report as not_reported — not as paid")
withc = [dict(r, collected_amount=0.0) for r in FIXTURE]
ok(vrf.status_counts(withc)[0]["status"] == vrf.UNPAID and vrf.status_counts(withc)[0]["rows"] == 7,
   "with the column present and $0.00, all 7 rows are UNPAID — the real file's state")

print("\nI. A FEED WHOSE OWN BALANCE DISAGREES IS VISIBLE, NOT AVERAGED AWAY")
ok(vrf.totals(FIXTURE)["balance_disagrees_by"] == 0.0,
   "a self-consistent feed reports a zero disagreement (the real file agrees to the cent)")
bad = [dict(FIXTURE[0], collected_amount=100.0, balance_amount=1000.0)]   # 1000 earned, 100 paid
ok(vrf.totals(bad)["balance_disagrees_by"] == 100.0,
   "a feed whose Balance column contradicts earned - collected SURFACES the gap (+100.00)")

print("\nJ. THE THREE LYING HEADERS ARE MAPPED BY VALUE, AND PROPOSED — NEVER ASSUMED")
fields = {f[0]: f for f in cm.TARGET_FIELDS[RK]}
ok(fields["store"][4] == "Invoiced At",
   "'Invoiced At' is proposed for STORE, not for a date — it holds the store name + code")
ok(fields["store"][2] == "text" and fields["sold_on"][2] == "date_auto",
   "…and it is typed as text while sold_on is the date field, so a name match cannot swap them")
ok(fields["imei"][4] == "Related Tracking Number" and fields["mdn"][4] == "Tracking Number",
   "the IMEI comes from 'Related Tracking Number'; 'Tracking Number' is the line's own number")
ok(fields["region_label"][0] == "region_label",
   "'Region' lands in `region_label`, which cannot be mistaken for the org hierarchy's region")
for f in ("store", "imei", "mdn", "region_label"):
    ok(fields[f][5] == [],
       f"`{f}` declares NO aliases — a confidently-wrong name match is worse than no match")
ok(all(len(f) == 6 for f in cm.TARGET_FIELDS[RK]),
   "every field tuple is well-formed, so the mapper can render a proposal for each")

print("\nK. DUPLICATE CHECK + RULE TWO — pinned so a later edit cannot quietly undo them")
ok(cm.TABLE_MAP[RK] == "raw_vendor_rebate", "the report lands in its own LANDING table")
ok(cm.TABLE_MAP[RK] != "raw_ma_commission",
   "NOT raw_ma_commission: device_cogs prices its IMEIs into MA COGS and residual_subs counts its "
   "rows as subscribers, and this feed is ~7 rows per device")
ok(cm.TABLE_MAP[RK] != "activation_rebate_ledger",
   "NOT activation_rebate_ledger: that table BOOKS to the P&L (carrier_comm / device_rebate)")
reg = repr(cm.TARGET_FIELDS[RK]) + src
for name in ("verizon", "wireless zone", "rq ", "iqmetrix", "boost", "vidapay", "t-cetra"):
    ok(name not in reg.lower(),
       f"RULE TWO: no carrier/POS/vendor name '{name.strip()}' appears in the registry or the module")
ok(RK == "vendor_rebate_history",
   "the report key names the FEED SHAPE, as mig 1004 did — which tenant is offered it is decided "
   "by report_definitions.carrier_id rows")

print(f"\nharness_vendor_rebate_landing: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
