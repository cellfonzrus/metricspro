"""Proof harness for the IMEI ↔ REBATE reconciliation report — drives the REAL pure logic in
`app.modules.commcalc.imei_rebate_report` (no DB, no network, no FastAPI). Covers:

  KEYS + DATES
  • imei_key collapses the spellings the four feeds actually store (spaces, dashes, a trailing '.0' from
    an Excel float, a leading apostrophe) onto ONE match key; alphanumeric serials survive uppercased
  • is_device_identifier rejects the POS placeholders that would otherwise become phantom permanent gaps
  • parse_loose_date handles the ISO form AND the US MM/DD/YYYY form raw_mi's TEXT date columns carry
  • period_window / date_in_period across the 'June 2026' vs '2026-06' spelling duality + a year rollover

  SIGN CONVENTION (the money-correctness core)
  • MA amounts (negative = paid to dealer) come back POSITIVE; a positive raw cell (a charge/clawback)
    comes back NEGATIVE — device_history.ma_paid, the same normalization /ma-commission/summary uses
  • ePay payment-detail amounts are read AS-IS (already positive = paid to dealer) — the deliberate
    asymmetry between the two feeds
  • RECONCILIATION: per MA row, rebate + spiffs + other == that row's contribution to
    /ma-commission/summary's `total_payable` (= -Σ of the same component set). The two surfaces agree.

  CLASSIFICATION (received / none / partial-mismatch)
  • no rebate line at all → 'none' (the gap the report exists to surface)
  • a clean credit → 'received'; a credit partly reversed → 'partial'; net zero after a full reversal →
    'partial' (NOT 'none' — money moved); net negative → 'partial'
  • a raw rebate cell of 0/blank produces NO event → 'none', not a $0 "receipt"
  • an optional `expected` short-pays to 'partial' (no expectation feed exists today; the hook is proved)

  GAP DETECTION + MERGE
  • activations with NO rebate are FIRST-CLASS rows (sortable/filterable) and their own tile, with a $
    figure that is an explicitly-labelled ESTIMATE — never an invented recorded amount
  • the inverse gap: a rebate whose IMEI has no activation lands in `orphans`, not in the main table
  • the SAME handset seen on both an ePay legs (POS sale + residual line) merges to ONE row with the
    union of evidence, the EARLIEST date, and field-fill from the higher-ranked leg
  • an MA row landing in a LATER window period is an ADJUSTMENT of that activation, not a new activation

  FILTERS / OPTIONS (RULE THREE / FOUR / FIVE)
  • options come from the values present in the data, case-variants collapsed, first-seen casing kept
  • server-side filters narrow store/rep/market + the appended facets, case-insensitively
  • tiles_for over the FILTERED rows == what the table (and therefore the export) shows

Run: `python3 backend/scratchpad/imei_rebate_report_proof.py` from the backend dir (or by full path).
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import imei_rebate_report as irr
from app.modules.commcalc.discrepancy_engine import parse_payment_type

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


def approx(a, b, eps=1e-6):
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) < eps


COMP_OF = lambda pt: parse_payment_type(pt)[0]          # the REAL ePay classifier chain  # noqa: E731


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. match keys ─────────────────────────────────────────────────────────────────────")
# The same handset as the four feeds really spell it.
IMEI = "355163568356973"
for label, spelling in [("plain", IMEI), ("excel float", IMEI + ".0"), ("spaced", "355163568 356973"),
                        ("dashed", "35516356-8356973"), ("apostrophe", "'" + IMEI),
                        ("padded", "  " + IMEI + "  ")]:
    check(f"imei_key normalizes the {label} spelling to one key", irr.imei_key(spelling) == IMEI)
check("imei_key('') -> ''", irr.imei_key("") == "" and irr.imei_key(None) == "")
check("an alphanumeric serial survives, upper-cased", irr.imei_key(" a1b2c3d4e5f6 ") == "A1B2C3D4E5F6")

check("a 15-digit IMEI is a device identifier", irr.is_device_identifier(IMEI))
check("a 14-char hex MEID is a device identifier", irr.is_device_identifier("A1B2C3D4E5F6AB"))
for bad in ("", "0", "N/A", "1234", "NA", "NONE"):
    check(f"placeholder {bad!r} is NOT a device identifier", not irr.is_device_identifier(irr.imei_key(bad)))
check("a 10-digit MDN-shaped value IS admitted (some feeds key on it)",
      irr.is_device_identifier("5165551234"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. dates + period window ──────────────────────────────────────────────────────────")
check("ISO date parses", irr.parse_loose_date("2026-06-14") == "2026-06-14")
check("ISO datetime slice parses", irr.parse_loose_date("2026-06-14T09:12:00Z") == "2026-06-14")
check("US MM/DD/YYYY parses (raw_mi TEXT dates)", irr.parse_loose_date("6/14/2026") == "2026-06-14")
check("US zero-padded parses", irr.parse_loose_date("06/14/2026") == "2026-06-14")
for bad in ("", None, "nan", "-", "not a date", "13/45/2026"):
    check(f"unparseable {bad!r} -> None (never a guessed date)", irr.parse_loose_date(bad) is None)

w = irr.period_window("June 2026", 6)
check("window is activation month + 6", len(w) == 7)
check("window starts at the activation month", w[0] == "June 2026")
check("window ends 6 months later", w[-1] == "December 2026")
check("window is canonically spelled regardless of input spelling",
      irr.period_window("2026-06", 6) == w)
check("window rolls the year over", irr.period_window("November 2026", 3) ==
      ["November 2026", "December 2026", "January 2027", "February 2027"])
check("lag 0 = the activation month alone", irr.period_window("June 2026", 0) == ["June 2026"])
check("an unparseable period passes through", irr.period_window("Q3", 6) == ["Q3"])
check("period_ym reads both spellings", irr.period_ym("June 2026") == (2026, 6) == irr.period_ym("2026-06"))

check("date_in_period matches across spellings", irr.date_in_period("2026-06-14", "June 2026")
      and irr.date_in_period("6/14/2026", "2026-06"))
check("a date outside the month is NOT in period", not irr.date_in_period("2026-07-01", "June 2026"))
check("an unknown date NEVER counts as in-period", not irr.date_in_period(None, "June 2026"))

check("a date in an EARLIER month is before the period", irr.is_before_period("2026-04-10", "June 2026"))
check("a date in the period itself is NOT before it", not irr.is_before_period("2026-06-01", "June 2026"))
check("a LATER date is NOT before the period", not irr.is_before_period("2026-08-03", "June 2026"))
check("an unknown date is never ASSUMED old", not irr.is_before_period(None, "June 2026")
      and not irr.is_before_period("garbage", "June 2026"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. MA sign convention + reconciliation to /ma-commission/summary ──────────────────")
# The REAL shape the owner pulled from live raw_ma_commission (luxelink, June 2026): every amount NEGATIVE
# because negative = paid TO the dealer.
MA_ROW = {"id": "r1", "imei": IMEI, "tx_date": "2026-06-14", "period": "June 2026",
          "merchant_account_id": "MA-1001", "user_name": "Jane Doe", "sku": "MOTO-G-2026",
          "activation_type": "New", "activation_type2": "branded", "sub_type": "TWP",
          "line_status": "Active", "is_financed": "No", "platform": "Vidapay",
          "rebate": -529.0, "device_margin": -20.0, "consumer_margin": 0, "consumer_financing": 0,
          "wallet_funding": 0, "fees_margin": -3.5,
          "spiff_m1": -10.0, "spiff_m2": -10.0, "spiff_m3": -10.0,
          "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
          "mrc_net_discount": 40.0}

evs = irr.ma_events(MA_ROW)
reb = [e for e in evs if e["kind"] == "rebate"]
spf = [e for e in evs if e["kind"] == "spiff"]
oth = [e for e in evs if e["kind"] == "other"]
check("MA rebate -529 is shown paid-to-dealer POSITIVE 529", len(reb) == 1 and approx(reb[0]["amount"], 529.0))
check("three nonzero spiffs produce three events (zeros produce none)", len(spf) == 3)
check("spiff events carry their month index", sorted(e["month"] for e in spf) == [1, 2, 3])
check("MA spiffs are sign-flipped", approx(sum(e["amount"] for e in spf), 30.0))
check("the remaining payable components fold into ONE 'other' event", len(oth) == 1)
check("other = device_margin + fees_margin, sign-flipped", approx(oth[0]["amount"], 23.5))
check("mrc_net_discount (the PLAN PRICE) is NOT read as a payout",
      not any(approx(e["amount"], 40.0) or approx(e["amount"], -40.0) for e in evs))

# A CHARGE / clawback: a POSITIVE raw cell must come back NEGATIVE (sign preserved, never dropped).
CLAWBACK = {**MA_ROW, "id": "r2", "rebate": 529.0, "device_margin": 0, "fees_margin": 0,
            "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0}
cb = [e for e in irr.ma_events(CLAWBACK) if e["kind"] == "rebate"]
check("a POSITIVE raw rebate (a charge) comes back NEGATIVE", len(cb) == 1 and approx(cb[0]["amount"], -529.0))

# RECONCILIATION: this report's per-IMEI total must equal /ma-commission/summary's per-row contribution.
_MA_COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
                  "wallet_funding", "fees_margin",
                  "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]
summary_payable = -sum(float(MA_ROW.get(k) or 0) for k in _MA_COMPONENTS)     # the router's own formula
rep = irr.build_report([irr.ma_activation(MA_ROW)], evs)
row = rep["rows"][0]
check("rebate + spiffs + other == /ma-commission/summary total_payable for that row",
      approx(row["rebate"] + row["spiff_total"] + row["other_paid"], summary_payable))
check("total_received is that same figure", approx(row["total_received"], summary_payable))
check("the report's total_received tile agrees", approx(rep["tiles"]["total_received"], summary_payable))
check("per-month spiff breakdown is exposed", row["spiff_by_month"]["m1"] == 10.0
      and row["spiff_by_month"]["m4"] == 0.0)
check("activation carries the processor account as the store", row["store"] == "MA-1001")
check("activation carries the processor login as the rep", row["rep"] == "Jane Doe")
check("MA rows carry NO market (no store_mapping linkage — documented deviation)", row["market"] is None)
check("status of a clean MA rebate = received", row["rebate_status"] == "received")
check("rebate provenance names the source table", row["rebate_source"] == "raw_ma_commission")
check("rebate provenance names the date", row["rebate_date"] == "2026-06-14")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. ePay path: the EXISTING classifier, read as-is (no sign flip) ──────────────────")
PAY_REBATE = {"imei": IMEI, "payment_type": "Device Reimbursement - Month 1", "amount": 200.0,
              "period": "August 2026", "payment_date": "2026-08-15",
              "business_address": "1800 Great Neck Rd", "rep_username": "jdoe"}
PAY_SIM = {**PAY_REBATE, "payment_type": "SIM Card Reimbursement", "amount": 5.0}
PAY_BOUNTY = {**PAY_REBATE, "payment_type": "New Activation Bounty", "amount": 45.0}
PAY_MI = {**PAY_REBATE, "payment_type": "Monthly Incentive", "amount": 12.0}

e_reb = irr.epay_event(PAY_REBATE, COMP_OF)
e_sim = irr.epay_event(PAY_SIM, COMP_OF)
e_bnt = irr.epay_event(PAY_BOUNTY, COMP_OF)
e_mi = irr.epay_event(PAY_MI, COMP_OF)
check("Device Reimbursement classifies as REBATE (device_history.REBATE_COMP_TYPES)", e_reb["kind"] == "rebate")
check("SIM Card Reimbursement classifies as REBATE", e_sim["kind"] == "rebate")
check("New Activation Bounty is NOT a rebate (never blended)", e_bnt["kind"] == "other")
check("Monthly Incentive residual is NOT a rebate", e_mi["kind"] == "other")
check("ePay amounts are read AS-IS (already paid-to-dealer positive)", approx(e_reb["amount"], 200.0))
check("the comp_type from the real classifier rides along", e_reb["comp_type"] == "DEVICE_REIMB")
check("ePay events carry the payment period", e_reb["period"] == "August 2026")
check("a classifier that raises does not lose the row",
      irr.epay_event(PAY_REBATE, lambda pt: (_ for _ in ()).throw(ValueError("boom")))["kind"] == "other")

SALE = {"serial_1": IMEI + ".0", "trans_date": "2026-06-14", "period": "June 2026",
        "store": "1800 Great Neck Rd", "salesperson": "Jane Doe", "user_login": "jdoe",
        "product_desc": "Moto G 2026 64GB", "sku": "MOTOG26", "contract_type": "Activation",
        "voided": "", "ext_price": 129.99}
MI = {"device_serial": IMEI, "period": "June 2026", "mi_activation_date": "6/14/2026",
      "customer_plan": "Unlimited 50", "subscriber_status": "Active", "rep_username": "jdoe"}
a_sale = irr.epay_activation_from_sale(SALE, market_of=lambda s: "LI" if s else "")
a_mi = irr.epay_activation_from_mi(MI)
check("the sale leg keys on serial_1 (Excel '.0' normalized)", a_sale["key"] == IMEI)
check("the sale leg carries store/rep/device", a_sale["store"] == "1800 Great Neck Rd"
      and a_sale["rep"] == "Jane Doe" and a_sale["device"] == "Moto G 2026 64GB")
check("the sale leg resolves a market from store_mapping", a_sale["market"] == "LI")
check("the residual leg keys on device_serial", a_mi["key"] == IMEI)
check("the residual leg reads the US-formatted MI activation date", a_mi["date"] == "2026-06-14")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. rebate_status classification ───────────────────────────────────────────────────")
mk = lambda amt, **kw: {"key": IMEI, "kind": "rebate", "amount": amt, "date": "2026-08-15",  # noqa: E731
                        "period": "August 2026", "label": "Device Reimbursement", **kw}

s, why = irr.classify_rebate([])
check("no rebate line at all -> 'none'", s == "none")
check("'none' says why", "no rebate line" in (why or ""))

s, _ = irr.classify_rebate([mk(200.0)])
check("a clean credit -> 'received'", s == "received")

s, why = irr.classify_rebate([mk(200.0), mk(-50.0)])
check("credit partly reversed -> 'partial'", s == "partial")
check("'partial' names the reversal", "reversed" in (why or ""))

s, why = irr.classify_rebate([mk(200.0), mk(-200.0)])
check("credit FULLY reversed (nets to 0) -> 'partial', NOT 'none' (money moved)", s == "partial")
check("full-reversal reason is explicit", "fully reversed" in (why or ""))

s, why = irr.classify_rebate([mk(-75.0)])
check("net NEGATIVE -> 'partial'", s == "partial")
check("net-negative reason names the chargeback", "charged back" in (why or ""))

s, why = irr.classify_rebate([mk(120.0)], expected=200.0)
check("short of an expectation -> 'partial'", s == "partial")
check("short-pay reason states both numbers", "120.00" in (why or "") and "200.00" in (why or ""))
s, _ = irr.classify_rebate([mk(200.0)], expected=200.0)
check("meeting the expectation -> 'received'", s == "received")
s, _ = irr.classify_rebate([mk(199.999)], expected=200.0)
check("a rounding epsilon does not manufacture a mismatch", s == "received")

check("a spiff-only event set is NOT a rebate", irr.classify_rebate(
    [{"key": IMEI, "kind": "spiff", "amount": 10.0}])[0] == "none")
zero_reb = irr.ma_events({**MA_ROW, "rebate": 0})
check("a raw rebate cell of 0 produces NO rebate event -> 'none', not a $0 receipt",
      irr.classify_rebate(zero_reb)[0] == "none")
check("a raw rebate cell of blank likewise -> 'none'",
      irr.classify_rebate(irr.ma_events({**MA_ROW, "rebate": None}))[0] == "none")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. merge: one handset, two evidence legs ──────────────────────────────────────────")
merged = irr.merge_activations([a_mi, a_sale])          # residual first, on purpose
check("two legs collapse to ONE activation row", len(merged) == 1)
m = merged[IMEI]
check("evidence is the union, ordered sale -> residual", m["evidence"] == ["sale", "residual"])
check("the sale leg (higher rank) wins store", m["store"] == "1800 Great Neck Rd")
check("a residual-only field still fills in", m["line_status"] == "Active")
check("rows counts both contributing source rows", m["rows"] == 2)

EARLY = {**a_sale, "date": "2026-06-02"}
m2 = irr.merge_activations([a_sale, EARLY])[IMEI]
check("the EARLIEST activation date wins", m2["date"] == "2026-06-02")

both_src = irr.merge_activations([irr.ma_activation(MA_ROW), a_sale])[IMEI]
check("a device seen by BOTH feeds is tagged source='both'", both_src["source"] == "both")
check("both feeds are listed", sorted(both_src["sources"]) == ["epay", "ma"])
check("a keyless activation is dropped, not crashed", irr.merge_activations([{"key": ""}]) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. GAPS are first-class + the inverse gap ─────────────────────────────────────────")
IMEI_B, IMEI_C, IMEI_D = "355163568356974", "355163568356975", "355163568356976"
acts = [
    irr.epay_activation_from_sale({**SALE, "serial_1": IMEI}),                       # rebate lands
    irr.epay_activation_from_sale({**SALE, "serial_1": IMEI_B, "store": "42 Main St",
                                   "salesperson": "Sam Ray"}),                       # NO rebate  <- gap
    irr.epay_activation_from_sale({**SALE, "serial_1": IMEI_C}),                     # partly reversed
    irr.epay_activation_from_sale({**SALE, "serial_1": IMEI_D, "store": "42 Main St"}),  # NO rebate <- gap
]
evts = [
    irr.epay_event({**PAY_REBATE, "imei": IMEI, "amount": 200.0}, COMP_OF),
    irr.epay_event({**PAY_REBATE, "imei": IMEI_C, "amount": 200.0}, COMP_OF),
    irr.epay_event({**PAY_REBATE, "imei": IMEI_C, "amount": -60.0}, COMP_OF),
    irr.epay_event({**PAY_BOUNTY, "imei": IMEI_B, "amount": 45.0}, COMP_OF),   # other != rebate: still a gap
    irr.epay_event({**PAY_REBATE, "imei": "999999999999999", "amount": 150.0}, COMP_OF),   # ORPHAN
]
rep = irr.build_report(acts, evts)
rows, tiles, orph = rep["rows"], rep["tiles"], rep["orphans"]
check("every activation is a row (gaps included, never absent)", len(rows) == 4)
by = {r["imei"]: r for r in rows}
check("the gap rows are present and statused 'none'",
      by[IMEI_B]["rebate_status"] == "none" and by[IMEI_D]["rebate_status"] == "none")
check("a non-rebate payment does NOT satisfy the rebate (bounty != rebate)",
      by[IMEI_B]["rebate"] == 0.0 and by[IMEI_B]["other_paid"] == 45.0)
check("gap rows sort FIRST-CLASS to the top (partial, then none, then received)",
      [r["rebate_status"] for r in rows][:1] == ["partial"]
      and set(r["rebate_status"] for r in rows[1:3]) == {"none"}
      and rows[-1]["rebate_status"] == "received")
check("the gap TILE counts them", tiles["no_rebate"]["count"] == 2)
check("the gap tile's $ is an ESTIMATE, explicitly labelled",
      "ESTIMATE" in (tiles["no_rebate"]["estimate_basis"] or ""))
check("the gap estimate = count x the median received rebate",
      approx(tiles["no_rebate"]["estimated_amount"], 2 * 200.0))
check("the received tile counts + sums only clean receipts",
      tiles["with_rebate"]["count"] == 1 and approx(tiles["with_rebate"]["amount"], 200.0))
check("the partial tile carries the NET", tiles["partial"]["count"] == 1
      and approx(tiles["partial"]["amount"], 140.0))
check("the inverse gap goes to `orphans`, NOT the main table", len(orph) == 1
      and orph[0]["imei"] == "999999999999999")
check("the orphan carries its amount + provenance", approx(orph[0]["amount"], 150.0)
      and orph[0]["source"] == "raw_payment_detail")
check("the orphan tile counts + sums", tiles["orphan"]["count"] == 1
      and approx(tiles["orphan"]["amount"], 150.0))

no_receipts = irr.build_report([acts[1]], [])
check("with NO received rebate at all there is nothing to estimate from (no invented $)",
      no_receipts["tiles"]["no_rebate"]["estimated_amount"] is None
      and "nothing to estimate" in no_receipts["tiles"]["no_rebate"]["estimate_basis"])
empty = irr.build_report([], [])
check("an empty org yields empty sections, not a crash",
      empty["rows"] == [] and empty["tiles"]["activations"] == 0
      and empty["tiles"]["no_rebate"]["estimated_amount"] is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. MA adjustment in a LATER window period is not a second activation ─────────────")
# The endpoint admits an ACTIVATION only when the MA row's tx_date is in the reported period; a later row
# for the same IMEI still contributes its money. Proved here at the same grain the endpoint applies.
LATER = {**MA_ROW, "id": "r3", "tx_date": "2026-08-03", "period": "August 2026",
         "rebate": -25.0, "device_margin": 0, "fees_margin": 0,
         "spiff_m1": 0, "spiff_m2": 0, "spiff_m3": 0}
window_rows = [MA_ROW, LATER]
acts_ma = [irr.ma_activation(r) for r in window_rows if irr.date_in_period(r["tx_date"], "June 2026")]
evs_ma = [e for r in window_rows for e in irr.ma_events(r)]
check("only the June row is an ACTIVATION", len(acts_ma) == 1)
rep2 = irr.build_report(acts_ma, evs_ma)
check("still ONE row for the handset", len(rep2["rows"]) == 1)
check("the later adjustment's money IS credited to it",
      approx(rep2["rows"][0]["rebate"], 529.0 + 25.0))
check("and it counts as two rebate lines", rep2["rows"][0]["rebate_lines"] == 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. options + server-side filters + WYSIWYG tiles ─────────────────────────────────")
opts = irr.filter_options(rows, orph)
check("store options come from the data present", set(opts["store_options"]) ==
      {"1800 Great Neck Rd", "42 Main St"})
check("rep options come from the data present", "Jane Doe" in opts["rep_options"])
check("status options are the three buckets, labelled",
      [o["id"] for o in opts["status_options"]] == ["none", "partial", "received"]
      and dict((o["id"], o["label"]) for o in opts["status_options"])["partial"] == "Partial / mismatch")
case_rows = [{"store_label": "42 Main St", "rep": "Jane Doe"},
             {"store_label": "42 MAIN ST", "rep": "jane doe"}]
co = irr.filter_options(case_rows)
check("case-variant spellings collapse to ONE option, first-seen casing kept",
      co["store_options"] == ["42 Main St"] and co["rep_options"] == ["Jane Doe"])

f_gap = irr.apply_filters(rows, status="none")
check("filtering to the gaps returns exactly the gap rows", len(f_gap) == 2
      and all(r["rebate_status"] == "none" for r in f_gap))
f_store = irr.apply_filters(rows, stores="42 main st")
check("the store filter is case-insensitive", len(f_store) == 2)
f_both = irr.apply_filters(rows, stores="42 Main St", status="none")
check("filters compose", len(f_both) == 2)
check("a blank selection narrows nothing", len(irr.apply_filters(rows, stores="", reps="")) == len(rows))
check("an unmatched selection returns nothing (never silently everything)",
      irr.apply_filters(rows, stores="nowhere") == [])
f_at = irr.apply_filters(rows, activation_type="activation")
check("the appended activation-type facet filters", len(f_at) == len(rows))
check("the source facet filters", len(irr.apply_filters(rows, source="epay")) == len(rows)
      and irr.apply_filters(rows, source="ma") == [])

t_gap = irr.tiles_for(f_gap, [])
check("tiles recomputed over the FILTERED rows describe exactly those rows (WYSIWYG)",
      t_gap["activations"] == 2 and t_gap["no_rebate"]["count"] == 2
      and t_gap["with_rebate"]["count"] == 0 and approx(t_gap["rebate_total"], 0.0))
check("a filtered-out orphan is not double-counted", t_gap["orphan"]["count"] == 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 10. the notes say out loud what the report means ─────────────────────────────────")
n_ma = irr.definition_note(["ma"])
n_ep = irr.definition_note(["epay"], "both")
check("the MA definition names raw_ma_commission + the transaction date",
      "raw_ma_commission" in n_ma and "transaction date" in n_ma)
check("the ePay definition names BOTH legs", "raw_sales.serial_1" in n_ep
      and "raw_mi.mi_activation_date" in n_ep)
check("basis='sales' states only the sales leg",
      "raw_mi.mi_activation_date" not in irr.definition_note(["epay"], "sales"))
check("basis='residual' states only the residual leg",
      "raw_sales.serial_1" not in irr.definition_note(["epay"], "residual"))
check("the definition promises parity with Device History", "Device History" in n_ep)
check("the definition says a blank activation date is EXCLUDED, not guessed",
      "EXCLUDED" in n_ep and "never guessed" in n_ep)
check("no source -> an honest empty statement", "No activation source" in irr.definition_note([]))
check("the sign note states the MA flip", "sign-flipped" in (irr.sign_note(["ma"]) or ""))
check("the sign note states the ePay as-is posture", "as-is" in (irr.sign_note(["epay"]) or ""))
_sn = irr.sign_note(["ma", "epay"]) or ""
check("both sources -> BOTH conventions stated in one note",
      "sign-flipped" in _sn and "as-is" in _sn)
wn = irr.window_note(irr.period_window("June 2026", 6), 6)
check("the window note names the range", "June 2026" in wn and "December 2026" in wn)
check("the window note warns that a later rebate reads as a gap", "widen the lag" in wn)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*90}\n  {_pass} passed, {_fail} failed\n{'='*90}")
sys.exit(1 if _fail else 0)
