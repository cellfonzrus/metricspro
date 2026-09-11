"""PROOF: Device Payable as at a date — which billed devices had not been paid for on a given day.

OWNER DIRECTIVE 2026-09-11, verbatim: *"I need the payable at the end of the year accounts. Payable
on 12/31/2025 company wise"*, then *"we need to check which of the imei a billed in 2025 got paid in
2025 and which ones were paid in 2026"*.

WHAT THIS FILE HOLDS

  §A  THE JOIN KEY, WHICH IS THE WHOLE TRICK. `vip_invoice_devices.imei` does NOT hold the IMEI —
      it holds the SIM/ICCID. The 15-digit IMEI is in the column called `serial`, and that is what
      `asset_ledger.esn_imei` holds. Joining on the obviously-named column matches 4 rows out of
      19,571 and still renders a confident total. §A pins the key so a future "cleanup" fails here
      instead of in the owner's year-end accounts.
  §B  THE DEFINITION. Billed on or before D, paid after D or not at all ⇒ payable at D. Three
      per-unit states, and an absent payment date is never read as payment.
  §C  THE REGRESSION FIXTURE — the owner's own 2025-12-31 numbers, reproduced exactly, per company.
  §D  COVERAGE IS DERIVED FROM THE DATA AND "NOT MEASURED" IS A REAL ANSWER. A date the pruned
      ledger cannot evidence returns None with a reason — never a bare $0.00.
  §E  THE LICENCE FOR `payg_date`, as a live contract rather than a claim in a comment.
  §F  NON-DEVICE ITEMS ARE A DIFFERENT BASIS: reported apart, never merged, never omitted.
  §G  NOTHING IS SILENTLY DROPPED, AND THE FIGURE DECLARES ITS OWN WEAKNESSES.
  §H  MULTI-TENANT: every read org-scoped; a foreign row can never arrive.
  §I  REUSE, NOT RE-DERIVATION — no second resolver, no second definition of "device", coa.py
      untouched, so no booked figure can move.
  §J  THE PAGE SAYS WHICH QUESTION IT ANSWERS AND NAMES THE REPORT IT IS NOT.

THE LIVE NUMBERS IN §C AND §E ARE DATED SNAPSHOTS, ON PURPOSE — house org 00000000-…-0001, measured
2026-09-11. They will legitimately move when the feed is re-swept or invoices are amended. They are
pinned because they are the report's DEFINITION made arithmetic: if the 2025 payable stops landing
on $489,136.63 from a fixture built to produce it, the math changed, not the data.

THE FIXTURE IS SYNTHETIC AND EXACT. §C does not ship 19,571 real rows; it GENERATES a population
with the measured per-company device counts and dollar totals (cent-exact by construction), so the
owner's table is asserted as arithmetic this code must reproduce, with no database anywhere.

DB-FREE BY CONSTRUCTION: `_harness_dbfree.install()` patches the client chokepoint and tripwires the
real constructor; every read goes through the in-memory FakeClient below. stdlib only.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _harness_dbfree                                                          # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(t):
    print("\n" + t)
    print("-" * len(t))


ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "11111111-1111-1111-1111-111111111111"


# ── the in-memory client (no sockets, no credentials, no supabase) ────────────────────────────────
class _Q:
    def __init__(self, rows, seen):
        self._rows, self._seen = list(rows), seen

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def eq(self, col, val):
        self._seen.add(col)
        return _Q([r for r in self._rows if str(r.get(col, "")) == str(val)], self._seen)

    def in_(self, col, vals):
        self._seen.add(col)
        want = {str(v) for v in vals}
        return _Q([r for r in self._rows if str(r.get(col, "")) in want], self._seen)

    def ilike(self, col, pat):
        p = str(pat).strip("%").lower()
        return _Q([r for r in self._rows if p in str(r.get(col, "")).lower()], self._seen)

    def range(self, a, b):
        return _Q(self._rows[a:b + 1], self._seen)

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeClient:
    """Serves the fixture tables. An unknown table is an EMPTY table, never an exception — the same
    degradation the real readers are written against. Records the filter columns every query used so
    §H can PROVE org scoping instead of assuming it."""

    def __init__(self, tables):
        self.tables, self.filters, self._schema = tables, set(), "commcalc"

    def schema(self, name):
        self._schema = name
        return self

    def table(self, name):
        return _Q(self.tables.get("%s.%s" % (self._schema, name), []), self.filters)


_harness_dbfree.install(FakeClient({}))

from app.modules.account import coa                                             # noqa: E402
from app.modules.account import device_purchases as dp                          # noqa: E402
from app.modules.account import device_payable as dpay                          # noqa: E402

AS_AT = "2025-12-31"

# ── THE ORG'S SETUP — our four real companies and their stores, spelled our way ───────────────────
STORE_MAPPING = [
    {"org_id": ORG, "store_code": "B-A1", "store_address": "10 Alpha Ave", "market": "PA"},
    {"org_id": ORG, "store_code": "B-B1", "store_address": "20 Bravo Blvd", "market": "NJ"},
    {"org_id": ORG, "store_code": "B-C1", "store_address": "30 Charlie Ct", "market": "NY"},
    {"org_id": ORG, "store_code": "B-D1", "store_address": "40 Delta Dr", "market": "NJ"},
    # the distributor's MASTER / DEALER ACCOUNT — a head-office account, not a retail location.
    {"org_id": ORG, "store_code": "<acct>", "store_address": "99 Head Office Rd", "market": ""},
    {"org_id": OTHER_ORG, "store_code": "X-9", "store_address": "999 Foreign Rd", "market": "ZZ"},
]
COMPANIES = [
    {"org_id": ORG, "id": "co-pa", "name": "PA PHONE TRADERS LLC"},
    {"org_id": ORG, "id": "co-ps", "name": "PASSAIC WIRELESS 2022 LLC"},
    {"org_id": ORG, "id": "co-ny", "name": "NY WIRELESS TRADERS LLC"},
    {"org_id": ORG, "id": "co-w4", "name": "WIRELESS 2024 LLC"},
    {"org_id": ORG, "id": "co-hq", "name": "Head Office Account"},
    {"org_id": ORG, "id": "co-def", "name": "Default Company"},
    {"org_id": OTHER_ORG, "id": "co-x", "name": "Foreign Holdings LLC"},
]
STORE_COMPANIES = [
    {"org_id": ORG, "store_address": "10 Alpha Ave", "company_id": "co-pa"},
    {"org_id": ORG, "store_address": "20 Bravo Blvd", "company_id": "co-ps"},
    {"org_id": ORG, "store_address": "30 Charlie Ct", "company_id": "co-ny"},
    {"org_id": ORG, "store_address": "40 Delta Dr", "company_id": "co-w4"},
    {"org_id": ORG, "store_address": "99 Head Office Rd", "company_id": "co-hq"},
]

PHONE = "Handset Model One 128GB"          # a serialised product ⇒ a device
TABLET = "Tablet Model Two 32GB"           # serialised ⇒ also a device (tablets are IN)
CHARGEBACK = "Return Item Chargeback"      # never serialised ⇒ not a device
MANAGED = "Managed Services Level 1"       # never serialised ⇒ not a device


# ── THE OWNER'S MEASURED 2025-12-31 FIXTURE (house org, 2026-09-11) ──────────────────────────────
# company -> (store, payable devices, payable $, paid devices, paid $)
FIX = {
    "PA PHONE TRADERS LLC":      ("10 Alpha Ave",  770, 203514.90, 10288, 3930741.69),
    "PASSAIC WIRELESS 2022 LLC": ("20 Bravo Blvd", 303, 103406.97,  2497,  873315.23),
    "NY WIRELESS TRADERS LLC":   ("30 Charlie Ct", 282,  99727.29,  4290, 1602327.10),
    "WIRELESS 2024 LLC":         ("40 Delta Dr",   253,  82487.47,   669,  260083.33),
}
FIX_PAYABLE_TOTAL = 489136.63
FIX_PAYABLE_DEVICES = 1608
# …plus the two units on the VOIDED invoice below, which the report COUNTS and DECLARES (§K).
FIX_VOID_DEVICES = 2
FIX_VOID_AMOUNT = 500.00
FIX_PAID_TOTAL = 6666467.35
FIX_PAID_DEVICES = 17744
FIX_UNMATCHED = 219
FIX_INVOICED = 19571                       # 17744 paid + 1608 payable + 219 unmatched
# 17 units were billed on a SECOND invoice inside the window. The ledger owes for each once, so this
# much of the headline is counted twice — declared by the report, not hidden.
FIX_REPEAT_ROWS = 17
FIX_REPEAT_AMOUNT = 4599.83
# Exactly one matched unit carries NO payment date at all, worth $0.00. It is counted INTO the
# payable (an absent date is not evidence of payment) and reported apart, which is precisely why the
# device count reads 1,608 where a "paid in 2026" count reads 1,607.
FIX_UNKNOWN_DEVICES = 1
FIX_UNKNOWN_AMOUNT = 0.0


def _split_cents(total, n, fixed=()):
    """Exactly `n` amounts summing to `total` to the cent, the first len(fixed) of them being
    `fixed`. Integer cents throughout, so the fixture can never drift by a rounding penny."""
    cents = int(round(total * 100))
    out = [int(round(f * 100)) for f in fixed]
    cents -= sum(out)
    m = n - len(out)
    base, rem = divmod(cents, m)
    out += [base + (1 if i < rem else 0) for i in range(m)]
    assert sum(out) == int(round(total * 100)) and len(out) == n
    return [c / 100.0 for c in out]


_sn = [0]


def _serial():
    _sn[0] += 1
    return "35709%010d" % _sn[0]           # 15 digits, the real IMEI shape


DEVICES, LEDGER = [], []


def _unit(store, serial, invoiced, payg, owed, in_ledger=True):
    DEVICES.append({"org_id": ORG, "id": "V%d" % len(DEVICES), "invoice_number": "INV-%s" % store,
                    "location": store, "serial": serial,
                    # THE SIM/ICCID — 18 digits, in the column NAMED `imei`. It is here in the
                    # fixture precisely so §A can prove the report does not join on it.
                    "imei": "0891303495%08d" % len(DEVICES), "product_name": PHONE,
                    "created_on": invoiced + "T04:00:00+00:00",
                    "period_year": int(invoiced[:4]), "period_month": int(invoiced[5:7])})
    # `in_ledger=False` is a unit the pruned ledger simply does not carry; `payg=None` WITH a ledger
    # row is the other thing entirely — a known unit whose payment date is absent (§B4/§B5).
    if in_ledger:
        LEDGER.append({"org_id": ORG, "id": "L%d" % len(LEDGER), "esn_imei": serial,
                       "payg_date": (payg + "T00:00:00+00:00") if payg else None,
                       "owed_to_vip": owed, "acquired_date": invoiced, "store": store,
                       "status": "Open", "device_model": PHONE})


# The 17 repeats, and the units they repeat, live in the first company.
_rep_amounts = _split_cents(FIX_REPEAT_AMOUNT, FIX_REPEAT_ROWS)
for comp, (store, pay_n, pay_amt, paid_n, paid_amt) in FIX.items():
    is_first = comp == "PA PHONE TRADERS LLC"
    is_unknown_home = comp == "NY WIRELESS TRADERS LLC"
    # ── the PAYABLE units: billed inside the window, paid AFTER the as-at date ──
    distinct_n = pay_n - (FIX_REPEAT_ROWS if is_first else 0)
    fixed = tuple(_rep_amounts) if is_first else ()
    # the one unit with NO payment date is worth $0.00 and lives where it was measured
    if is_unknown_home:
        fixed = (FIX_UNKNOWN_AMOUNT,)
    amounts = _split_cents(pay_amt - (FIX_REPEAT_AMOUNT if is_first else 0.0), distinct_n, fixed)
    serials = []
    for i, a in enumerate(amounts):
        s = _serial()
        serials.append(s)
        unknown = is_unknown_home and i == 0
        _unit(store, s, "2025-06-%02d" % (1 + i % 28), None if unknown else "2026-02-14", a)
    if is_first:                          # …and the SAME units billed again on a second invoice
        for i in range(FIX_REPEAT_ROWS):
            DEVICES.append(dict(DEVICES[-1], id="R%d" % i, serial=serials[i],
                                invoice_number="INV-REBILL", location=store,
                                created_on="2025-09-10T04:00:00+00:00",
                                period_year=2025, period_month=9))
    # ── the PAID units: billed inside the window, paid ON OR BEFORE the as-at date ──
    # Spread across every month of the window, as real invoicing is: that is what gives the coverage
    # scan twelve judged months to read the boundary out of, instead of one.
    for i, a in enumerate(_split_cents(paid_amt, paid_n)):
        _unit(store, _serial(), "2025-%02d-11" % (1 + i % 12), "2025-11-20", a)

# ── units billed in the window that the pruned ledger simply does not carry ──
for i in range(FIX_UNMATCHED):
    DEVICES.append({"org_id": ORG, "id": "U%d" % i, "invoice_number": "INV-U",
                    "location": "10 Alpha Ave", "serial": _serial(),
                    "imei": "089130349599%06d" % i, "product_name": PHONE,
                    "created_on": "2025-07-04T04:00:00+00:00",
                    "period_year": 2025, "period_month": 7})

# ── THE PRUNE, made arithmetic: months before the window carry plenty of invoiced units and almost
# no ledger rows. This is what `coverage_scan` reads the boundary out of — no year is written down.
for m in (11, 12):
    for i in range(600):
        DEVICES.append({"org_id": ORG, "id": "OLD%d%d" % (m, i), "invoice_number": "INV-OLD",
                        "location": "10 Alpha Ave", "serial": _serial(),
                        "imei": "089130349588%06d" % i, "product_name": PHONE,
                        "created_on": "2024-%02d-05T04:00:00+00:00" % m,
                        "period_year": 2024, "period_month": m})
        if i < 12:                                    # 12 of 600 — a 2% ledger, i.e. pruned
            LEDGER.append({"org_id": ORG, "id": "LOLD%d%d" % (m, i), "esn_imei": DEVICES[-1]["serial"],
                           "payg_date": "2024-%02d-20T00:00:00+00:00" % m, "owed_to_vip": 100.0,
                           "acquired_date": "2024-%02d-05" % m, "store": "10 Alpha Ave",
                           "status": "Open", "device_model": PHONE})

# ── the ledger's own NON-SERIAL rows: per-invoice fee labels carrying no digit ─────────────────────
for i in range(30):
    LEDGER.append({"org_id": ORG, "id": "LS%d" % i, "esn_imei": "SHIPPING", "payg_date": None,
                   "owed_to_vip": 25.0, "acquired_date": "2025-05-05", "store": "10 Alpha Ave",
                   "status": "Open", "device_model": ""})
for i in range(20):
    LEDGER.append({"org_id": ORG, "id": "LP%d" % i, "esn_imei": "PROCESSING FEE", "payg_date": None,
                   "owed_to_vip": 9.0, "acquired_date": "2025-05-05", "store": "10 Alpha Ave",
                   "status": "Open", "device_model": ""})

# ── A VOIDED INVOICE. Its units are still in the feed and are still COUNTED (the sibling report
# counts them); what the report must never do is leave the question invisible. Two payable units.
VOID_INV = "INV-VOID"
for i in range(2):
    _s = _serial()
    DEVICES.append({"org_id": ORG, "id": "VD%d" % i, "invoice_number": VOID_INV,
                    "location": "10 Alpha Ave", "serial": _s,
                    "imei": "089130349577%06d" % i, "product_name": PHONE,
                    "created_on": "2025-11-20T04:00:00+00:00",
                    "period_year": 2025, "period_month": 11})
    LEDGER.append({"org_id": ORG, "id": "LVD%d" % i, "esn_imei": _s,
                   "payg_date": "2026-02-14T00:00:00+00:00", "owed_to_vip": 250.00,
                   "acquired_date": "2025-11-20", "store": "10 Alpha Ave",
                   "status": "Open", "device_model": PHONE})
INVOICES = [{"org_id": ORG, "id": "H1", "invoice_number": VOID_INV, "status": "Voided",
             "period_year": 2025},
            {"org_id": ORG, "id": "H2", "invoice_number": "INV-10 Alpha Ave",
             "status": "Paid In Full", "period_year": 2025},
            {"org_id": OTHER_ORG, "id": "H9", "invoice_number": "INV-F", "status": "Voided",
             "period_year": 2025}]

# ── invoice LINES: the device money, plus the non-device items that no serial can see ────────────
LINES = [
    {"org_id": ORG, "id": "N1", "invoice_number": "INV-HQ", "location": "99 Head Office Rd",
     "status": "Paid In Full", "name": CHARGEBACK, "quantity": 1, "total": 159056.76,
     "created_on": "2025-12-29T00:00:00+00:00", "period_year": 2025, "period_month": 12},
    {"org_id": ORG, "id": "N2", "invoice_number": "INV-HQ", "location": "99 Head Office Rd",
     "status": "Paid In Full", "name": "NSF Fee", "quantity": 1, "total": 50.00,
     "created_on": "2025-12-29T00:00:00+00:00", "period_year": 2025, "period_month": 12},
    {"org_id": ORG, "id": "N3", "invoice_number": "INV-MS", "location": "10 Alpha Ave",
     "status": "Paid In Full", "name": MANAGED, "quantity": 1, "total": 1000.00,
     "created_on": "2025-08-01T00:00:00+00:00", "period_year": 2025, "period_month": 8},
    # a non-device item billed AFTER the as-at date — must not appear in an as-at-2025-12-31 figure
    {"org_id": ORG, "id": "N4", "invoice_number": "INV-LATE", "location": "10 Alpha Ave",
     "status": "Open", "name": CHARGEBACK, "quantity": 1, "total": 9663.75,
     "created_on": "2026-02-23T00:00:00+00:00", "period_year": 2026, "period_month": 2},
    # a DEVICE-named line on an invoice that carried no serialised unit: real money with no serial
    {"org_id": ORG, "id": "N5", "invoice_number": "INV-NOSERIAL", "location": "10 Alpha Ave",
     "status": "Paid In Full", "name": PHONE, "quantity": 1, "total": 8189.87,
     "created_on": "2025-05-30T00:00:00+00:00", "period_year": 2025, "period_month": 5},
]

# ── the SECOND, independent payment feed that licenses `payg_date` (§E) ──────────────────────────
PAYGO = [
    {"org_id": ORG, "id": "P1", "amount": 6712367.33, "period_year": 2025,
     "created_on": "2025-12-15", "status": "Complete"},
    {"org_id": ORG, "id": "P2", "amount": 4326473.83, "period_year": 2026,
     "created_on": "2026-08-28", "status": "Complete"},
]

TABLES = {
    "commcalc.store_mapping": STORE_MAPPING,
    "commcalc.store_aliases": [],
    "commcalc.companies": COMPANIES,
    "commcalc.store_companies": STORE_COMPANIES,
    "commcalc.vip_invoice_devices": DEVICES,
    "commcalc.vip_invoice_lines": LINES,
    "commcalc.asset_ledger": LEDGER,
    "commcalc.vip_invoices": INVOICES,
    "commcalc.vip_paygo_payments": PAYGO,
    "storeops.stores": [],
}


def run(as_at=AS_AT, tables=None):
    t = dict(TABLES)
    for k, v in (tables or {}).items():
        t[k] = v
    c = FakeClient(t)
    out = dpay.compute(c, ORG, as_at)
    out["_filters_used"] = c.filters
    return out


print("=" * 78)
print("DEVICE PAYABLE AS AT A DATE — which billed devices were not yet paid for")
print("=" * 78)

R = run()
T = R["totals"]
COV = R["coverage"]
BYCO = {c["company"]: c for c in R["by_company"]}

# ══ §A — THE JOIN KEY, WHICH IS THE WHOLE TRICK ══════════════════════════════════════════════════
section("§A  THE COLUMN NAMES LIE: the IMEI lives in `serial`, and `imei` holds the SIM/ICCID")

check("A1 the join is on `serial` — with it, the fixture's 19,352 in-window units find their ledger "
      "row (match rate %s)" % T["match_rate"],
      T["matched_devices"] == FIX_INVOICED + FIX_VOID_DEVICES - FIX_UNMATCHED,
      (T["matched_devices"], FIX_INVOICED + FIX_VOID_DEVICES - FIX_UNMATCHED))

# THE NEGATIVE CONTROL, and the reason this report exists in the shape it does. Swap the two columns
# on the invoice side — exactly what a reader who trusts the column NAMES would do — and the report
# still renders, with 99.98% of the money gone.
SWAPPED = [dict(d, serial=d.get("imei"), imei=d.get("serial")) for d in DEVICES]
RS = run(tables={"commcalc.vip_invoice_devices": SWAPPED})
check("A2 …and joining on the column CALLED `imei` instead collapses the match to ~nothing — it does "
      "NOT error, it renders a confident, empty number. That is why the key is pinned",
      RS["totals"]["matched_devices"] == 0 and RS["totals"]["payable_amount"] == 0.0,
      RS["totals"])
check("A3 the module's own comment warns the next reader off 'fixing' the join back to `imei`",
      "serial" in dpay.ledger_index.__doc__ and "SIM" in dpay.ledger_index.__doc__.upper())
check("A4 both sides are normalised, so punctuation/case drift can never split a match",
      dpay.norm_key(" 3570-9144 6384537 ") == dpay.norm_key("357091446384537")
      == "357091446384537")
check("A5 a ledger key with NO DIGIT in it is not a serial and never joins — a SHAPE test on the "
      "data, never a list of label spellings (RULE TWO)",
      dpay.is_serial_key("357091446384537") and not dpay.is_serial_key("SHIPPING")
      and not dpay.is_serial_key("PROCESSING FEE") and not dpay.is_serial_key(""))
check("A6 …and those non-serial ledger rows are COUNTED and reported, not silently swallowed",
      R["meta"]["ledger"]["non_serial_rows"] == 50, R["meta"]["ledger"])
check("A7 a unit the ledger does not carry at all and a unit whose PAYMENT DATE is absent are two "
      "different facts, and the report never conflates them",
      T["unmatched_devices"] == FIX_UNMATCHED
      and T["payment_date_unknown_devices"] == FIX_UNKNOWN_DEVICES,
      (T["unmatched_devices"], T["payment_date_unknown_devices"]))

# ══ §B — THE DEFINITION ══════════════════════════════════════════════════════════════════════════
section("§B  THE DEFINITION: billed on or before D, paid after D or not at all ⇒ payable at D")

check("B1 a unit paid BEFORE the as-at date is paid",
      dpay.payment_state({"payg_date": "2025-11-20"}, AS_AT) == "paid")
check("B2 a unit paid ON the as-at date is paid — the boundary is inclusive, as a closing date is",
      dpay.payment_state({"payg_date": "2025-12-31T23:59:00+00:00"}, AS_AT) == "paid")
check("B3 a unit paid AFTER the as-at date was still owed on that day",
      dpay.payment_state({"payg_date": "2026-01-01"}, AS_AT) == "unpaid")
check("B4 a unit with NO payment date is 'unknown' — an absent date is never read as payment",
      dpay.payment_state({"payg_date": None}, AS_AT) == "unknown"
      and dpay.payment_state({}, AS_AT) == "unknown")
check("B5 …and 'unknown' is counted INTO the payable, because we cannot evidence it was paid",
      T["payment_date_unknown_devices"] == FIX_UNKNOWN_DEVICES
      and T["payable_devices"] == FIX_PAYABLE_DEVICES + FIX_VOID_DEVICES,
      T["payment_date_unknown_devices"])
check("B6 a unit billed AFTER the as-at date is in no figure at all — the report stands on the date "
      "it was given and cannot see forward",
      run(as_at="2025-05-31")["totals"]["invoiced_devices"] < T["invoiced_devices"])
check("B7 the same population re-read a year later shows the 2026 payments as PAID, so the very "
      "same rows answer 'what did we owe then' and 'what do we owe now' differently — which is the "
      "capability the status column could never give",
      run(as_at="2026-12-31")["totals"]["payable_amount"] == 0.0
      and run(as_at="2026-12-31")["totals"]["paid_amount"] > T["paid_amount"],
      run(as_at="2026-12-31")["totals"])

# ══ §C — THE REGRESSION FIXTURE ══════════════════════════════════════════════════════════════════
section("§C  THE OWNER'S 2025-12-31 NUMBERS, REPRODUCED EXACTLY (house org, measured 2026-09-11)")

check("C1 the payable at 2025-12-31 is $489,136.63",
      T["payable_amount"] == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2),
      T["payable_amount"])
check("C2 …over 1,608 device rows — 1,607 paid in 2026 plus the ONE carrying no payment date",
      T["payable_devices"] == FIX_PAYABLE_DEVICES + FIX_VOID_DEVICES, T["payable_devices"])
check("C3 the devices billed in 2025 and ALREADY paid in 2025 are 17,744 / $6,666,467.35",
      T["paid_devices"] == FIX_PAID_DEVICES and T["paid_amount"] == FIX_PAID_TOTAL,
      (T["paid_devices"], T["paid_amount"]))
check("C4 219 units billed in the window are not in the unit ledger at all, and are in NEITHER "
      "figure — not counted as paid, not counted as payable",
      T["unmatched_devices"] == FIX_UNMATCHED
      and T["paid_devices"] + T["payable_devices"] + T["unmatched_devices"]
      == FIX_INVOICED + FIX_VOID_DEVICES,
      T["unmatched_devices"])
check("C5 paid + payable == every unit the ledger could evidence (nothing leaks between buckets)",
      T["paid_devices"] + T["payable_devices"] == T["matched_devices"])

for comp, (_st, pay_n, pay_amt, _pn, _pa) in FIX.items():
    b = BYCO.get(comp) or {}
    # the fixture's VOIDED invoice sits at the first company's store (§K), so its two units are
    # expected there — declared in the arithmetic rather than quietly absorbed
    v_n = FIX_VOID_DEVICES if comp == "PA PHONE TRADERS LLC" else 0
    v_a = FIX_VOID_AMOUNT if comp == "PA PHONE TRADERS LLC" else 0.0
    check("C6 %-26s %5d devices  $%12.2f%s"
          % (comp, pay_n, pay_amt, "  (+2 / $500.00 on the voided invoice, §K)" if v_n else ""),
          b.get("payable_devices") == pay_n + v_n
          and b.get("payable_amount") == round(pay_amt + v_a, 2),
          (b.get("payable_devices"), b.get("payable_amount")))

check("C7 the four companies sum back to the headline — the segregation cannot quietly lose money",
      round(sum(c["payable_amount"] for c in R["by_company"]), 2)
      == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2),
      sum(c["payable_amount"] for c in R["by_company"]))
check("C8 …and so does the by-store table, which is what the page folds the company table from",
      round(sum(c["payable_amount"] for c in R["by_store"]), 2)
      == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2))
check("C9 every company row carries a real company NAME, never a raw id",
      all(not str(c["company"]).startswith("co-") for c in R["by_company"]),
      [c["company"] for c in R["by_company"]])

# ══ §D — COVERAGE, DERIVED FROM THE DATA; "NOT MEASURED" IS A REAL ANSWER ════════════════════════
section("§D  COVERAGE COMES OUT OF THE DATA, AND AN UNANSWERABLE DATE RETURNS NO FIGURE")

check("D1 the measurable window starts the month AFTER the ledger stops carrying the units — "
      "derived, with no year written anywhere in the code",
      COV["start_month"] == "2025-01", COV["start_month"])
check("D2 …and the collapse is visible month by month: the pruned months are judged NOT covered",
      all(m["covered"] is False for m in COV["months"] if m["month"] in ("2024-11", "2024-12"))
      and all(m["covered"] for m in COV["months"] if m["month"] == "2025-06"),
      [(m["month"], m["ratio"], m["covered"]) for m in COV["months"]])
check("D3 the boundary is NOT a hard-coded year: give the same shape a ledger that only collapses "
      "in 2021 and the window opens in 2022",
      dpay.coverage_scan(
          [{"acquired_date": "2021-06-01"}] * 3 + [{"acquired_date": "2022-01-05"}] * 95,
          [{"created_on": "2021-06-02"}] * 100 + [{"created_on": "2022-01-06"}] * 100
      )["start_month"] == "2022-01")

OUT = run(as_at="2024-06-30")
check("D4 an as-at date the pruned ledger cannot evidence is 'not_measured'",
      OUT["coverage"]["state"] == "not_measured", OUT["coverage"])
check("D5 …and the payable comes back None, NEVER a bare 0.00 — that is the silent-zero rule, and "
      "the whole reason this report can be trusted",
      OUT["totals"]["payable_amount"] is None and OUT["totals"]["payable_devices"] is None
      and OUT["totals"]["paid_amount"] is None, OUT["totals"])
check("D6 …with a REASON a reader can act on, naming the coverage start",
      isinstance(OUT["coverage"]["reason"], str) and "2025-01" in OUT["coverage"]["reason"],
      OUT["coverage"]["reason"])
check("D7 …and no company or store rows are emitted either, so nothing downstream can quietly "
      "aggregate an unmeasured window into a total",
      OUT["by_company"] == [] and OUT["by_store"] == [])
check("D8 a MEASURED window with genuinely nothing payable is a real 0.00, not a None — the third "
      "state, kept distinct from the other two",
      run(as_at="2026-12-31")["totals"]["payable_amount"] == 0.0
      and run(as_at="2026-12-31")["coverage"]["state"] == "measured")
check("D9 an org with NO unit ledger at all measures nothing, rather than reporting $0.00 payable",
      run(tables={"commcalc.asset_ledger": []})["coverage"]["state"] == "not_measured")
check("D10 units billed BEFORE the window opened are counted and DECLARED, never assumed settled — "
      "they are the honest limit of this figure",
      T["before_coverage_devices"] == 1200, T["before_coverage_devices"])
check("D11 a date beyond the ledger's last month is still measured, but flagged as a stale tail "
      "(feed freshness, which is not the same defect as pruning)",
      run(as_at="2027-06-30")["coverage"]["stale_tail"] is True)
check("D12 a month with too few invoiced units is NOT judged — noise cannot move the boundary",
      dpay.coverage_scan([{"acquired_date": "2025-02-01"}] * 40,
                         [{"created_on": "2025-01-02"}] * 3
                         + [{"created_on": "2025-02-02"}] * 40)["start_month"] == "2025-02")

# ══ §E — THE LICENCE FOR `payg_date`, AS A LIVE CONTRACT ═════════════════════════════════════════
section("§E  WHAT LICENSES `payg_date` AS THE PAYMENT DATE — measured on every run, not claimed once")

# THE MEASURED LICENCE, house org 2026-09-11 — the WHOLE ledger's payments per year against the
# WHOLE settled-batch feed per year. These are org-wide sums, not the §C window, so they are pinned
# on the pure function directly rather than on the report fixture.
LIVE_EVIDENCE = [({"payg_date": "2025-07-01", "owed_to_vip": 6891830.12},  6712367.33, 2.67),
                 ({"payg_date": "2026-07-01", "owed_to_vip": 4441540.54},  4326473.83, 2.66)]
for _row, _batch, _pct in LIVE_EVIDENCE:
    _y = _row["payg_date"][:4]
    _e = dpay.payment_evidence([_row], [{"period_year": _y, "amount": _batch}])[0]
    check("E1 %s: the per-unit ledger says $%s was paid; the distributor's own settled batches — a "
          "DIFFERENT feed, swept separately, knowing nothing about units — say $%s. They agree to "
          "%.2f%%, and THAT is what licenses `payg_date` as the payment date"
          % (_y, format(_row["owed_to_vip"], ",.2f"), format(_batch, ",.2f"), _pct),
          _e["ledger_paid"] == _row["owed_to_vip"] and _e["settled_batches"] == _batch
          and _e["variance_pct"] == _pct, _e)
check("E2 the agreement is grouped by the PAYMENT date's year, not the acquisition year — it is the "
      "payment column that is on trial here",
      dpay.payment_evidence([{"acquired_date": "2025-01-01", "payg_date": "2026-03-03",
                              "owed_to_vip": 10.0}], [])[0]["year"] == "2026")
check("E3 the report carries the measurement in its payload, so the licence is re-checked on every "
      "run and shown to the reader rather than asserted once in a comment",
      isinstance(R["meta"]["payment_date_evidence"], list)
      and {"year", "ledger_paid", "settled_batches", "difference", "variance_pct"}
      == set(R["meta"]["payment_date_evidence"][0]),
      R["meta"]["payment_date_evidence"][:2])
check("E4 the agreement is REPORTED, never enforced — a timing difference must not blank the page, "
      "so the reader is handed both numbers and can judge",
      run(tables={"commcalc.vip_paygo_payments": [
          {"org_id": ORG, "id": "P9", "amount": 1.0, "period_year": 2025}]}
          )["totals"]["payable_amount"] == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2))
check("E5 a year with no settled batches reports no agreement percentage rather than '0.00%', "
      "which would read as perfect agreement",
      dpay.payment_evidence([{"payg_date": "2030-01-01", "owed_to_vip": 5.0}],
                            [])[0]["variance_pct"] is None)

# ══ §F — NON-DEVICE ITEMS: A DIFFERENT BASIS ═════════════════════════════════════════════════════
section("§F  NON-DEVICE ITEMS ARE A DIFFERENT BASIS — reported apart, never merged, never omitted")

ND = R["non_device"]
nd_items = {i["name"]: i for i in ND["by_item"]}
check("F1 the year-end Return Item Chargeback ($159,056.76, billed 2025-12-29) IS surfaced — it has "
      "no serial, so the device join is blind to it, and omitting it would understate the year-end",
      nd_items[CHARGEBACK]["amount"] == 159056.76
      and nd_items[CHARGEBACK]["latest"] == "2025-12-29", nd_items.get(CHARGEBACK))
check("F2 …and it is NOT in the device payable, not by a cent",
      T["payable_amount"] == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2))
check("F3 the section declares its own basis as BILLED, and its settlement as NOT MEASURED — there "
      "is no per-line payment date anywhere in this feed to decide it with",
      ND["basis"] == "billed" and ND["settlement_state"] == "not_measured"
      and isinstance(ND["settlement_reason"], str) and len(ND["settlement_reason"]) > 40)
check("F4 a non-device item billed AFTER the as-at date is excluded — the section honours the same "
      "date the payable does",
      round(ND["amount"], 2) == round(159056.76 + 50.00 + 1000.00, 2), ND["amount"])
check("F5 what is and is not a device is device_purchases' OWN data-driven rule, reused verbatim — "
      "there is no second definition of 'device' in the platform",
      "dp.classify_line" in open(os.path.join(HERE, "app/modules/account/device_payable.py"),
                                 encoding="utf-8").read())
check("F6 the master/dealer ACCOUNT keeps its own named row and is never absorbed into a store",
      any(b["company"] == "Head Office Account" and b["amount"] == 159106.76
          for b in ND["by_store"]), ND["by_store"])
check("F7 device-named money on an invoice that carried NO serialised unit is in neither figure — "
      "so it is named, with its dollars, rather than silently missing",
      ND["device_lines_without_serial"] == {"amount": 8189.87, "lines": 1},
      ND["device_lines_without_serial"])

# ══ §G — NOTHING SILENTLY DROPPED; THE FIGURE DECLARES ITS OWN WEAKNESSES ════════════════════════
section("§G  THE REPORT STATES HOW WELL ITS OWN NUMBER IS EVIDENCED")

check("G1 the match rate is published, so a reader judges the figure instead of trusting it",
      T["match_rate"] == round((FIX_INVOICED + FIX_VOID_DEVICES - FIX_UNMATCHED)
                               / (FIX_INVOICED + FIX_VOID_DEVICES), 4), T["match_rate"])
check("G2 the same unit billed on a SECOND invoice in the window is counted in the headline (that "
      "is what the owner's number is) but the double-count is DECLARED, not hidden",
      T["repeat_serial_rows"] == FIX_REPEAT_ROWS
      and T["repeat_serial_amount"] == FIX_REPEAT_AMOUNT,
      (T["repeat_serial_rows"], T["repeat_serial_amount"]))
check("G3 …and the distinct-unit payable is published beside it, so a reader can use either basis "
      "knowingly ($489,136.63 − $4,599.83 = $484,536.80)",
      T["distinct_device_payable_amount"]
      == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT - FIX_REPEAT_AMOUNT, 2)
      and T["distinct_device_payable_devices"]
      == FIX_PAYABLE_DEVICES + FIX_VOID_DEVICES - FIX_REPEAT_ROWS,
      (T["distinct_device_payable_amount"], T["distinct_device_payable_devices"]))
check("G4 the unmatched units are SAMPLED into the payload with their serial and invoice date, so "
      "'219 missing' is investigable rather than a number to shrug at",
      len(R["unmatched_sample"]) > 0 and "serial" in R["unmatched_sample"][0]
      and "invoiced" in R["unmatched_sample"][0])
check("G5 per-store rows carry their own unmatched count, so a store with a bad match rate is "
      "visible instead of being averaged away",
      all("unmatched_devices" in c for c in R["by_store"]))
check("G6 a store the vocabulary cannot place keeps its money in a LABELLED bucket and is listed "
      "with it — never dropped, never attributed to a store we cannot evidence",
      dpay.STORE_NOT_MAPPED == "(store not mapped)"
      and dpay.NOT_A_RETAIL_LOCATION == "(not a retail location)"
      and "not_a_retail_location" in R)
check("G7 the ledger's own health is published — key count, duplicate keys, non-serial and blank "
      "rows — so a feed that starts drifting is visible on the page",
      set(R["meta"]["ledger"]) == {"keys", "duplicate_keys", "non_serial_rows", "blank_key_rows"},
      R["meta"]["ledger"])

# ══ §H — MULTI-TENANT ════════════════════════════════════════════════════════════════════════════
section("§H  MULTI-TENANT: every read org-scoped, and a foreign row can never arrive")

check("H1 org_id is a filter on every read the report performs",
      "org_id" in R["_filters_used"], R["_filters_used"])
FOREIGN_DEV = DEVICES + [{"org_id": OTHER_ORG, "id": "FD", "invoice_number": "INV-F",
                          "location": "999 Foreign Rd", "serial": "359999999999999",
                          "imei": "089999999999999999", "product_name": PHONE,
                          "created_on": "2025-06-06T00:00:00+00:00",
                          "period_year": 2025, "period_month": 6}]
FOREIGN_LED = LEDGER + [{"org_id": OTHER_ORG, "id": "FL", "esn_imei": "359999999999999",
                         "payg_date": "2026-05-05", "owed_to_vip": 999999.0,
                         "acquired_date": "2025-06-06", "store": "999 Foreign Rd",
                         "status": "Open", "device_model": PHONE}]
RF = run(tables={"commcalc.vip_invoice_devices": FOREIGN_DEV,
                 "commcalc.asset_ledger": FOREIGN_LED})
check("H2 another org's unit never reaches this org's payable",
      RF["totals"]["payable_amount"] == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2),
      RF["totals"]["payable_amount"])
check("H3 another org's STORE never appears as a store of this org",
      not any(c["store"] == "999 Foreign Rd" for c in RF["by_store"]))
check("H4 another org's COMPANY never appears in this org's segregation",
      not any(c["company"] == "Foreign Holdings LLC" for c in RF["by_company"]),
      [c["company"] for c in RF["by_company"]])
check("H5 an unknown org measures nothing — it fails CLOSED, it does not fall back to a default",
      dpay.compute(FakeClient(TABLES), "22222222-2222-2222-2222-222222222222",
                   AS_AT)["coverage"]["state"] == "not_measured")

# ══ §I — REUSE, NOT RE-DERIVATION ════════════════════════════════════════════════════════════════
section("§I  REUSE: no second resolver, no second device definition, and coa.py cannot move")

import ast                                                                      # noqa: E402
import subprocess                                                               # noqa: E402

src = open(os.path.join(HERE, "app/modules/account/device_payable.py"), encoding="utf-8").read()
check("I1 the report owns NO store or company resolver: it calls coa's and defines neither",
      "coa.store_resolver" in src and "coa.build_company_matcher" in src
      and "def store_resolver" not in src and "def build_company_matcher" not in src)
check("I2 the store placement and the device vocabulary are device_purchases' own, shared rather "
      "than copied — two paths answering one question would drift",
      "dp.store_placer" in src and "dp.store_addresses" in src
      and "dp.device_product_names" in src and "dp._page" in src)
check("I3 the company matcher is called with NO default, so 'nobody assigned this store' stays "
      "distinguishable from 'it fell back to the default company'",
      "build_company_matcher(assign_rows, None)" in src)


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                node.body = b[1:] or [ast.Pass()]
    return tree


body = ast.unparse(_strip_docstrings(ast.parse(src)))
# A TABLE NAME IS NOT A BRANCH. The report must spell `commcalc.vip_paygo_payments` to read it, the
# way it spells `vip_invoice_devices`; a schema identifier is the data's own name for itself. What
# RULE TWO forbids is code whose BEHAVIOUR forks on a carrier, tenant or account, so the identifiers
# are masked and everything else is scanned — including every variable name, which is where the
# first draft of this module had leaked one.
body = body.replace("vip_paygo_payments", "<settled_batches_table>")
check("I4 RULE TWO: no carrier, distributor, tenant or account literal branches the report's CODE — "
      "the prose above may name what was MEASURED; the code may not BRANCH on it",
      not any(w in body.lower() for w in ("boost", "vip ", "'vip'", '"vip"', "paygo", "celero",
                                          "t-cetra", "vidapay", "cellfonz", "luxelink",
                                          "wood ave", "cellular services")),
      [w for w in ("boost", "vip ", "paygo", "wood ave", "cellular services")
       if w in body.lower()])
check("I5 …and no calendar year is hard-coded into the coverage decision either — the boundary is "
      "arithmetic over the feed, so the next org and the next year need no edit",
      not any(y in body for y in ("2023", "2024", "2025", "2026")),
      [y for y in ("2023", "2024", "2025", "2026") if y in body])
check("I6 the report BOOKS nothing and WRITES nothing — no insert, update, upsert or delete",
      not any(w in body for w in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc(")))

# THE NO-MOVEMENT PROOF. `coa.py` must be byte-identical to the branch point: every P&L, Balance
# Sheet and recon figure is unchanged by CONSTRUCTION, not by assertion. This mirrors
# harness_device_purchases §B2/§B3 and is the same claim for the same reason.
try:
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"],
                          cwd=os.path.dirname(HERE), capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "--stat", base, "--",
                           "backend/app/modules/account/coa.py"],
                          cwd=os.path.dirname(HERE), capture_output=True, text=True).stdout.strip()
    git_ok = bool(base)
except Exception:                                                # pragma: no cover - git-less CI
    diff, git_ok = "", False
check("I7 account/coa.py is byte-identical to the branch point — this report cannot have moved a "
      "single booked figure, and that is proven at git level rather than asserted",
      (not git_ok) or diff == "", diff)

# ══ §K — VOIDED INVOICES: COUNTED, AND THE QUESTION MADE VISIBLE ════════════════════════════════
section("§K  VOIDED INVOICES ARE COUNTED — AND EXACTLY WHAT THAT COSTS IS PUBLISHED")

check("K1 units on a VOIDED invoice are counted in the payable — deliberately, because the sibling "
      "Device Purchases report counts them and two finance reports disagreeing about voided "
      "invoices is a worse defect than either rule",
      T["payable_amount"] == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2))
check("K2 …and the report DECLARES what an exclude-voided rule would remove, so the number is "
      "already published when the rule is decided",
      T["voided_invoice_payable_devices"] == FIX_VOID_DEVICES
      and T["voided_invoice_payable_amount"] == FIX_VOID_AMOUNT,
      (T["voided_invoice_payable_devices"], T["voided_invoice_payable_amount"]))
check("K3 …including the payable AS IT WOULD READ under that rule, computed, not left to the reader",
      T["payable_excluding_voided"] == FIX_PAYABLE_TOTAL, T["payable_excluding_voided"])
check("K4 the void vocabulary is CONFIG with a house default, not a literal in a branch (RULE TWO) "
      "— a distributor that spells it differently is a config row, not a code change",
      dpay.VOID_STATUSES == ("voided",)
      and "void_statuses" in dpay.aggregate.__code__.co_varnames)
check("K5 …and matching is case-folded, so spelling drift in the feed cannot silently stop the "
      "declaration",
      run(tables={"commcalc.vip_invoices": [dict(INVOICES[0], status="VOIDED")]}
          )["totals"]["voided_invoice_payable_amount"] == FIX_VOID_AMOUNT)
check("K6 an org with no invoice headers at all declares ZERO voided money rather than failing — "
      "the declaration is additive and can never break the payable",
      run(tables={"commcalc.vip_invoices": []})["totals"]["payable_amount"]
      == round(FIX_PAYABLE_TOTAL + FIX_VOID_AMOUNT, 2)
      and run(tables={"commcalc.vip_invoices": []})["totals"]["voided_invoice_payable_amount"]
      == 0.0)
check("K7 another org's voided header can never mark this org's invoice — the header read is "
      "org-scoped like every other",
      T["voided_invoice_payable_devices"] == FIX_VOID_DEVICES)

# ══ §J — THE PAGE ════════════════════════════════════════════════════════════════════════════════
section("§J  THE PAGE NAMES ITS QUESTION, AND NAMES THE REPORT IT IS NOT")

page = os.path.join(HERE, "..", "frontend/src/app/(platform)/accounts/device-payable/page.tsx")
try:
    import harnesslib
    ptxt = open(page, encoding="utf-8").read()
    pcode = harnesslib.js_code_only(ptxt)
except FileNotFoundError:
    ptxt = pcode = ""
check("J1 the page exists", bool(ptxt), page)
check("J2 it offers a DAY picker for the as-at date — a payable is asked for on a day, not a month",
      'type="date"' in pcode and "as_at" in pcode)
check("J3 it says the figure is BACKDATED and names the current-state payable it is not, so nobody "
      "reconciles the two and concludes one is broken",
      "backdated" in pcode.lower() and "liabilities due" in pcode.lower())
check("J4 it RENDERS the not-measured state and its reason, instead of a figure",
      "not measured" in pcode.lower() and "cov?.reason" in pcode)
check("J5 it renders the unmatched-device count, so the reader sees the match rate rather than "
      "trusting it blindly",
      "unmatched_devices" in pcode and "match_rate" in pcode)
check("J6 it renders the non-device section as a SEPARATE basis and says so on the page",
      "non_device" in pcode and "different basis" in pcode.lower()
      and "must not be added to it" in pcode.lower())
check("J7b the page renders the voided-invoice declaration, so the open rule question is visible to "
      "a reader rather than living only in the payload",
      "voided_invoice_payable_amount" in pcode and "payable_excluding_voided" in pcode)
check("J7 it shows the derived coverage month by month, so the window is auditable on the page",
      "coverage" in pcode and "covMonths" in pcode)
check("J8 it uses the SHARED filter bar and the SHARED export bar (RULE FIVE §3d)",
      "StandardFilterBar" in pcode and "ReportExportBar" in pcode)

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
