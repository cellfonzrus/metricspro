"""Harness — the three FEED-SHAPE defects that silently corrupt a config-driven import.

DB-FREE. Reproduces each defect on the REAL numbers measured from the first POS line-sales feed
(a 48,876-row 'Sales By Product' export, Jan 2025 – Aug 2026, one store) and its companion
'Sales By Location' summary, then proves the fix.

MEASURED GROUND TRUTH (the location summary, which the line file must reproduce exactly):
    Gross Sales   4,604,292.43        Net Quantity   33,013
    Total Cost    3,591,798.85        Quantity Sold  40,944
    Gross Profit  1,012,493.58        Qty Refunded   -7,931

  1. FOOTER/TOTALS ROW. The sheet's last row repeats the whole file's totals with its identity
     columns blank. Ingested as data every total DOUBLES: Total Price summed 9,208,584.86 against a
     true 4,604,292.43. Row count 48,876 parsed = 48,875 records + 1 footer (40,944 + 7,931).
  2. US DATETIME. '06/27/2025 15:52:40' through `date10` stored '06/27/2025' — not ISO, and
     ambiguous with DD/MM on the 18,927 rows (39%) whose day is <= 12.
  3. MULTI-MONTH FILE. One caller-supplied period would book all 20 months to a single label.
"""
import sys, os
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


from app.modules.commcalc.feed_shape import period_fields, is_footer_row, category_path
from app.modules.commcalc.merchant_portals import iso_date
from app.modules.commcalc import column_mapping as cm

# ── (1) the footer row ───────────────────────────────────────────────────────────────────────
print("\n(1) FOOTER/TOTALS ROW — ingesting it doubles every total")
REQ = cm.required_fields("pos_product_sales")
ok(REQ == ["trans_id"], f"pos_product_sales declares its identity field: {REQ}")

record = {"trans_id": "Z1321IN6530", "ext_price": 0.0, "gp": 0.0, "quantity": 1.0}
footer = {"trans_id": "", "ext_price": 4604292.43, "gp": 1012493.58, "quantity": 33013.0}
ok(not is_footer_row(record, REQ), "a real line (has an invoice #) is KEPT")
ok(is_footer_row(footer, REQ), "the totals row (no invoice #, but carries the totals) is DROPPED")

# the regression, on the real magnitudes: with the footer in, every sum is exactly doubled
rows = [{"trans_id": f"INV{i}", "ext_price": 4604292.43 / 10} for i in range(10)] + [
    {"trans_id": "", "ext_price": 4604292.43}]
naive = round(sum(r["ext_price"] for r in rows), 2)
kept, dropped = cm.drop_footer_rows(list(rows), "pos_product_sales", {"org_id": "x"})
fixed = round(sum(r["ext_price"] for r in kept), 2)
ok(naive == 9208584.86, f"PRE-FIX reproduces the doubling: {naive:,.2f} (true 4,604,292.43)")
ok(fixed == 4604292.43, f"POST-FIX ties to the location summary: {fixed:,.2f}")
ok(dropped == 1, "exactly one footer row reported, never silently swallowed")
ok(len(kept) == 10, "every real record survives")

# the shape rule must not fire where it cannot be proven
ok(cm.drop_footer_rows([{"a": 1}], "no_such_report_key", {})[1] == 0,
   "a report with no declared identity is returned UNTOUCHED (rule cannot fire)")
ok(not is_footer_row({"trans_id": ""}, []), "no required fields declared -> never a footer")
ok(not is_footer_row({"trans_id": "0"}, ["trans_id"]), "'0' is a real identity, not a blank")

# a no-serial inventory row is a RECORD, not a footer (the 72 On Order / Back Order rows)
IREQ = cm.required_fields("pos_inventory_listing")
ok(IREQ == ["sku"], f"pos_inventory_listing keys on SKU, not serial: {IREQ}")
ok(not is_footer_row({"sku": "CLVZSA006970", "imei": "", "status": "On Order"}, IREQ),
   "an ordered-but-not-received row (no serial) is KEPT, not mistaken for a footer")

# ── (2) the US datetime ──────────────────────────────────────────────────────────────────────
print("\n(2) US DATETIME — date10 stored a non-ISO, DD/MM-ambiguous string")
ok(cm.apply_transform("06/27/2025 15:52:40", "date10") == "06/27/2025",
   "PRE-FIX reproduces it: date10 -> '06/27/2025' (not ISO)")
ok(cm.apply_transform("06/27/2025 15:52:40", "date_auto") == "2025-06-27",
   "POST-FIX: date_auto -> '2025-06-27'")
# the ambiguous half: day <= 12 on both sides. 07/08/2025 must be 8 July, never 7 August.
ok(cm.apply_transform("07/08/2025 09:00:00", "date_auto") == "2025-07-08",
   "an ambiguous cell (both parts <= 12) resolves MM/DD, not DD/MM")
ok(cm.apply_transform("not a date", "date_auto") is None, "an undatable cell -> None, never a guess")
ok(cm.apply_transform("", "date_auto") is None, "a blank cell -> None")

print("  -- iso_date REGRESSION: every format it already accepted still parses --")
for src, exp in [("2025-06-27", "2025-06-27"), ("08-12-2026", "2026-08-12"),
                 ("Aug 12, 2026", "2026-08-12"), ("12 Aug 2026", "2026-08-12"),
                 ("2026-08-12T10:00:00", "2026-08-12"), ("8/3/26 4:04 PM", "2026-08-03")]:
    ok(iso_date(src) == exp, f"iso_date({src!r}) == {exp}")

# ── (3) the multi-month file ─────────────────────────────────────────────────────────────────
print("\n(3) MULTI-MONTH FILE — one period label would mis-book 20 months")
ok(cm.period_source_field("pos_product_sales") == "trans_date",
   "the period is derived from the report's own date field")
ok(cm.period_source_field("pos_inventory_listing") is None,
   "a report with no date field derives no period (a snapshot has none)")

# the real span: 2025-01 .. 2026-08, with the measured per-month row counts
SPAN = [("2025-01", 2220), ("2025-06", 3239), ("2025-12", 6110), ("2026-01", 3087), ("2026-08", 1056)]
sample = []
for ym, n in SPAN:
    sample += [{"trans_id": f"{ym}-{i}", "trans_date": f"{ym}-15"} for i in range(n)]
stamped, dated = cm.derive_row_periods(list(sample), "pos_product_sales")
months = {}
for r in stamped:
    months[r["period"]] = months.get(r["period"], 0) + 1
ok(dated == len(sample), f"every dated row stamped ({dated:,} rows)")
ok(months == {"January 2025": 2220, "June 2025": 3239, "December 2025": 6110,
              "January 2026": 3087, "August 2026": 1056},
   f"each row books to ITS OWN month: {months}")
ok(len(months) == 5, "a 5-month sample yields 5 period labels, not 1")

ok(period_fields("2025-06-27") == {"period": "June 2025", "period_month": 6, "period_year": 2025},
   "period label matches the canonical '%B %Y' spelling every read matches")
ok(period_fields("06/27/2025") == {}, "a NON-ISO date derives NOTHING (caller must parse first)")
ok(period_fields("") == {} and period_fields(None) == {}, "blank/None derive nothing")
ok(period_fields("2025-13-01") == {}, "an impossible month derives nothing, never a wrong label")

undated = [{"trans_id": "A", "trans_date": None}, {"trans_id": "B", "trans_date": "2025-06-27"}]
rows2, n2 = cm.derive_row_periods(undated, "pos_product_sales")
ok(n2 == 1 and "period" not in rows2[0], "an undated row is left UNSTAMPED, not booked to a guess")

# ── (4) the category path ────────────────────────────────────────────────────────────────────
print("\n(4) HIERARCHICAL CATEGORY — one cell feeds both category and department")
CAT = " >> Activations (Price Sheet) >> Verizon Wireless >> Cellular Equipment >> Customer Provided Device"
ok(category_path(CAT) == ("Activations (Price Sheet)", "Customer Provided Device"),
   "top level and leaf are both recovered")
ok(cm.apply_transform(CAT, "category_top") == "Activations (Price Sheet)", "category_top transform")
ok(cm.apply_transform(CAT, "category_leaf") == "Customer Provided Device", "category_leaf transform")
ok(cm.apply_transform(CAT, "text") == CAT.strip(), "the FULL path is still kept on `category`")
ok(category_path("") == ("", ""), "a blank category yields no department")
ok(category_path("Accessories") == ("Accessories", "Accessories"), "a flat category is its own leaf")

# ── (5) the layouts are wired end to end ─────────────────────────────────────────────────────
print("\n(5) LAYOUTS — the real export headers map to real columns")
SALES_HDR = ["Invoice #", "Invoiced By", "Invoiced At", "Sold By", "Tendered By", "Sold On",
             "Invoice Comments", "Customer", "Product SKU", "Tracking #", "Sold As Used",
             "Contract #", "Product Name", "Vendor SKU", "Refund", "Quantity", "Unit Cost",
             "Unit Price", "Total Cost", "Total Price", "List Price", "Selling Price",
             "Original Price", "Adjusted Price", "Net Profit", "Gross Profit", "Carrier Price",
             "Net Sales", "Pricing Discounts", "Sold For", "Total Product Coupons", "Channel",
             "Region", "District", "Category", "Location Type"]
INV_HDR = ["Location", "Product SKU", "Product Name", "Tracking #", "Quantity", "Unit Cost",
           "Total Cost", "Status", "Vendor", "Vendor SKU", "Manufacturer SKU", "Bar Code",
           "Category", "Warehouse Location", "Discontinued Date", "Do Not Sell", "Do Not Order",
           "Special Order", "EOL", "Write Off", "Refund Period (days)", "Channel", "Region",
           "District", "Location Type", "Used"]
for key, hdr in (("pos_product_sales", SALES_HDR), ("pos_inventory_listing", INV_HDR)):
    sug = {s["target_field"]: s for s in cm.suggest(hdr, key)}
    unmatched = sorted(f for f, s in sug.items() if not s.get("suggested_source"))
    ok(not unmatched, f"{key}: every declared field finds its header (unmatched: {unmatched})")
    ok(cm.TABLE_MAP[key] in ("raw_sales", "inventory_aging_device"),
       f"{key} targets the EXISTING table {cm.TABLE_MAP[key]} (no sibling raw_* table)")

# the six redundant money columns must NOT each be mapped (that would book the same money 6x)
sales_fields = {f[0] for f in cm.TARGET_FIELDS["pos_product_sales"]}
ok("ext_price" in sales_fields, "one price column is mapped (ext_price)")
ok(len([f for f in cm.TARGET_FIELDS["pos_product_sales"]
        if f[4] in ("Total Price", "Net Sales", "Sold For", "Selling Price",
                    "Adjusted Price", "Unit Price")]) == 1,
   "exactly ONE of the six identical price columns is mapped — the money is never counted twice")
ok(len([f for f in cm.TARGET_FIELDS["pos_product_sales"]
        if f[4] in ("Gross Profit", "Net Profit")]) == 1,
   "exactly ONE of the two identical profit columns is mapped")

print(f"\nharness_rq_ingest: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
