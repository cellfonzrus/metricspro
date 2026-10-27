"""PROOF + LOCK: what a supplier billed us, read supplier-neutrally (index §COMMCALC-38).

WHAT THIS PROVES. The invoice parser and the adapter contract, DB-free and browser-free: the column
vocabulary, the two recon keys and their normalization, the blank-is-not-zero rule, the skip reasons,
the natural key, the summing dedupe, and the four contract functions the one pull loop asks for.

WHAT THIS LOCKS, and why the lock is the point.

  • ONE COLUMN-VOCABULARY MECHANISM. `report_columns` is the home for "which column is which fact".
    Neither feed may define its own normalizer, header mapper or header scorer — `norm` is what makes
    'B-1115' and 'b 1115' one store key, and this platform has already lost $289 to a store code
    compared under one rule here and another rule there. A second normalizer is that defect waiting.
  • ONE MONEY PARSER. `merchant_portals.money` parses a portal money cell. `report_columns.money_or_none`
    DEREFERENCES it and adds exactly one rule (a blank is None, not 0.0). A third parser fails here.
  • ONE REFERENCE RULE. The supplier prints a tracking number spaced one way and the POS prints it
    another; if the two sides normalize differently a shipment reads as unbilled on one side and
    uncollected on the other. `match_key` is that one rule and the landed *_key columns are built
    with it.
  • THE NATURAL KEY MATCHES THE MIGRATION. `supplier_invoice_sweep.ON_CONFLICT` and mig 1098's unique
    index are read from both files and compared, so an upsert can never silently stop restating.
  • NO SUPPLIER IN THE CODE. RULE TWO, scanned whole-word over executable text with comments and
    docstrings stripped.

Run:  cd backend && python3 harness_supplier_invoice_feed.py        Exit 0 = all green.
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import report_columns as rc              # noqa: E402
from app.modules.commcalc import supplier_invoice_feed as feed      # noqa: E402
from app.modules.commcalc import supplier_invoice_sweep as sweep    # noqa: E402
from app.modules.commcalc import pos_report_feed as posfeed         # noqa: E402
from app.modules.commcalc import portal_report_pull as prp          # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def read(rel):
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def strip_text(src):
    """Executable text only — comments AND docstrings gone. A comment naming the thing a fix is about
    otherwise reads as the fix's own violation (a trap this repo has already hit)."""
    out, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            t, s = tok.type, tok.string
            if t == tokenize.COMMENT:
                continue
            if t == tokenize.STRING and prev in (None, tokenize.INDENT, tokenize.DEDENT,
                                                 tokenize.NEWLINE, tokenize.NL):
                prev = t
                continue
            out.append(s)
            # `prev` tracks EVERY token, the layout ones included: a docstring is preceded by
            # NEWLINE + INDENT, and skipping those left `prev` on the `def` line's colon, so every
            # function docstring survived the strip and read as executable text.
            prev = t
    except tokenize.TokenError:
        return src
    return " ".join(out)


print("\n== §A  THE ADAPTER CONTRACT — four functions, and the loop can find it ==")
for name in prp.REQUIRED:
    ok(callable(getattr(sweep, name, None)), "supplier_invoice_sweep exposes %s()" % name)
ok(callable(getattr(sweep, "setup_hint", None)), "…and the optional setup_hint()")
eq(sweep.connector_keys(), ("supplier_invoice",), "it declares exactly the one key it serves")
ok(prp.adapter_for("supplier_invoice") is sweep,
   "the ONE loop resolves this connector to this adapter, through the registry")
ok(prp.adapter_for("Supplier Invoice") is sweep,
   "…case and space tolerant, like every other connector")
ok(prp.adapter_for("pos_report") is not sweep,
   "control: it does not claim another feed's connector")
d = sweep.portal_descriptor("supplier_invoice")
ok(d is not None and d["base_url"] is None,
   "its descriptor carries NO base_url — the address is the tenant's declaration, not a constant")
eq(d["date_window_days"], 31,
   "its default window is a month, because a supplier bills on its own cycle, not a week")
ok(sweep.portal_descriptor("pos_report") is None,
   "…and it refuses to describe a connector it does not serve")
ok("No invoice report is declared" in sweep.setup_hint(),
   "an unconfigured source reads as UNCONFIGURED, not as a failed pull")

print("\n== §B  THE DECLARATION IS READ BY ONE READER ==")
SRC = {"org_id": "ORG", "id": "SRCID",
       "portal_reports": [{"key": "billing_detail", "label": "Billing detail",
                           "nav": ["billing", "detail"]}]}
specs = sweep.report_specs(src_row=SRC)
eq(len(specs), 1, "the declared report is read back")
eq(specs[0]["key"], "billing_detail", "…by its key")
eq(specs[0]["grain"], "invoice_line", "…and this feed's grain overrides the shared reader's")
eq(sweep.report_specs(src_row={}), (), "a source with nothing declared declares nothing")
eq(sweep.report_specs(src_row={"portal_reports": ["billing detail"]})[0]["key"], "billing_detail",
   "the bare-string shape the sibling feed accepts is accepted here, by the SAME code")
sweep_src = strip_text(read("backend/app/modules/commcalc/supplier_invoice_sweep.py"))
ok("portal_reports" not in sweep_src.replace("pos_report_sweep", ""),
   "it does not parse portal_reports itself — one config column, one reader")

print("\n== §C  ONE COLUMN-VOCABULARY MECHANISM (no second normalizer) ==")
for mod, path in ((feed, "backend/app/modules/commcalc/supplier_invoice_feed.py"),
                  (posfeed, "backend/app/modules/commcalc/pos_report_feed.py")):
    src = strip_text(read(path))
    name = path.rsplit("/", 1)[-1]
    ok("re.sub" not in src or "report_columns" in src,
       "%s does not spell its own normalizer" % name)
    ok("pairs.sort" not in src, "%s does not spell its own header matcher" % name)
eq(feed.match_key("1Z 999-AA1 01 2345 6784"), "1z 999 aa1 01 2345 6784",
   "a reference normalizes under the ONE rule")
eq(feed.match_key("1z999aa1012345  6784"), "1z999aa1012345 6784",
   "…and a differently-spaced spelling normalizes under the same rule")
eq(feed.match_key(None), "", "a missing reference is the empty key, never None in a key column")
eq(rc.norm("B-1115"), posfeed.norm("B-1115"),
   "both feeds' norm() is the SAME function — the store-code case trap cannot reopen")
eq(rc.norm("b 1115"), rc.norm("B-1115"), "…and it still collapses the spelling it exists to collapse")
eq(posfeed.header_score(["Store", "Employee", "Net Sales"]), 3,
   "the sibling feed's scorer still scores its own vocabulary, unchanged")
eq(feed.header_score(["Store", "Employee", "Net Sales"]), 1,
   "…and this feed scores only against ITS vocabulary: an invoice has a store, and it has no "
   "employee and no net sales, so the sibling's header scores 1 here and 3 there")
eq(feed.header_score(["Invoice Number", "Tracking Number", "Net Charge"]), 3,
   "control: this feed's OWN header scores fully, so the banner above it cannot win")

print("\n== §D  A BLANK IS NOT A ZERO ==")
ok(rc.money_or_none("") is None, "a blank charge cell reads as None")
ok(rc.money_or_none("   ") is None, "…whitespace too")
eq(rc.money_or_none("$1,063.52"), 1063.52, "a money cell parses")
eq(rc.money_or_none("(12.40)"), -12.4, "…a bracketed credit is negative")
eq(rc.money_or_none("12.40-"), -12.4, "…so is a trailing minus")
eq(rc.money_or_none("0.00"), 0.0, "a cell that SAYS zero is zero — the distinction is the point")
rc_src = strip_text(read("backend/app/modules/commcalc/report_columns.py"))
ok("merchant_portals" in rc_src,
   "money_or_none DEREFERENCES the one money parser rather than re-implementing it")
ok(len(re.findall(r"def money\s*\(", rc_src)) == 0,
   "…and defines no `money` of its own for a caller to pick by accident")

print("\n== §E  ONE INVOICE EXPORT -> LINES ==")
TABLE = [
    ["Supplier Billing Detail — do not reply"],          # the banner a portal prints above the header
    ["Invoice Number", "Invoice Date", "Shipped Date", "Tracking Number", "Reference",
     "Service", "Pieces", "Net Charge", "Store Number"],
    ["INV-9001", "10/20/2026", "10/18/2026", "1Z 999 AA1 01 2345 6784", "R-5512",
     "Next Day", "1", "$24.80", "B-1598"],
    ["INV-9001", "10/20/2026", "10/18/2026", "1Z999AA1012345 6785", "R-5513",
     "Ground", "2", "$9.10", "B-1598"],
]
res = feed.normalize(TABLE[1:], org_id="ORG", source_id="SRCID", connector="supplier_invoice",
                     report_key="billing_detail")
eq(len(res["rows"]), 2, "both charge lines parsed")
r0 = res["rows"][0]
eq(r0["invoice_number"], "INV-9001", "the invoice is named")
eq(r0["charge_date"], "2026-10-18",
   "the CHARGE day is the shipped day, not the invoice day — a recon keyed on the invoice date would "
   "look for the sale in the wrong week")
eq(r0["invoice_date"], "2026-10-20", "…and the invoice day is kept beside it")
eq(r0["amount"], 24.8, "the charge parsed")
eq(r0["quantity"], 1.0, "the count parsed")
eq(r0["service"], "Next Day", "the supplier's own wording is kept verbatim")
eq(r0["shipment_ref"], "1Z 999 AA1 01 2345 6784", "…so is the reference as printed")
eq(r0["shipment_key"], feed.match_key("1Z 999 AA1 01 2345 6784"),
   "…and the KEY beside it is the normalized one the recon will compare")
eq(r0["counter_key"], feed.match_key("R-5512"), "the counter reference gets a key too")
eq(r0["store_code"], "B-1598", "the store the supplier billed")
eq(r0["store_key"], rc.norm("B-1598"), "…normalized by the one rule")
ok(isinstance(r0["source_line"], list) and r0["source_line"][0] == "INV-9001",
   "the export's own row is carried, so nothing is lost")
eq(res["warnings"], [], "an export carrying a shipment reference warns about nothing")

print("\n== §F  WHAT IS REFUSED, NAMED, NEVER GUESSED ==")
bad = [TABLE[1],
       ["", "10/20/2026", "10/18/2026", "1Z1", "R", "Ground", "1", "$5.00", "B-1"],
       ["INV-9002", "10/20/2026", "", "1Z2", "R", "Ground", "1", "$5.00", "B-1"],
       ["INV-9003", "10/20/2026", "10/18/2026", "1Z3", "R", "Ground", "1", "", "B-1"]]
res2 = feed.normalize(bad[0:1] + bad[1:], org_id="ORG")
eq(len(res2["rows"]), 1, "only the lines that name an invoice, a day and a charge are kept")
whys = [s["why"] for s in res2["skipped"]]
ok(feed.SKIP_NO_INVOICE in whys, "a line naming no invoice is skipped with its reason")
ok(feed.SKIP_NO_AMOUNT in whys,
   "a line with a BLANK charge is skipped and REPORTED — never filled in with a zero a recon would "
   "close as collected in full")
eq(res2["rows"][0]["invoice_number"], "INV-9002",
   "and the line with no charge day is the one that was dropped, not the one with no charge")
ok(all(w in feed.SKIP_REASONS for w in whys), "every reason comes from the shared vocabulary")
only_date = feed.normalize([["Invoice Number", "Invoice Date", "Net Charge"],
                            ["INV-1", "10/20/2026", "$5.00"]])
eq(only_date["rows"][0]["charge_date"], "2026-10-20",
   "an export with no charge-date column falls back to the invoice date")
eq(len(only_date["warnings"]), 1, "…and says once that it cannot match shipment-for-shipment")
ok("shipment_ref" in only_date["warnings"][0],
   "…naming the column to declare, so the operator knows what to do")
unknown = feed.normalize([["Widget", "Sprocket"], ["a", "b"]])
eq(unknown["rows"], [], "an export whose columns mean nothing to us lands NOTHING")
ok("column_synonyms" in unknown["warnings"][0],
   "…and says which config declares them, rather than failing silently")
eq(feed.normalize([])["rows"], [], "an empty export is not an error")
eq(feed.normalize(None)["rows"], [], "…nor is nothing at all")

print("\n== §G  THE TENANT'S OWN COLUMN WORDS OUTRANK THE HOUSE DEFAULTS ==")
mine = feed.normalize([["Bill Ref", "Movement Day", "Cost"], ["INV-7", "10/18/2026", "$3.00"]],
                      calibration={"column_synonyms": {"invoice_number": ["bill ref"],
                                                       "charge_date": ["movement day"],
                                                       "amount": ["cost"]}})
eq(len(mine["rows"]), 1, "an export in the tenant's own words parses once declared")
eq(mine["rows"][0]["amount"], 3.0, "…reading the declared money column")
feed_src = strip_text(read("backend/app/modules/commcalc/supplier_invoice_feed.py"))
ok("column_synonyms" in feed_src, "the declaration is read from config, not branched on in code")

print("\n== §H  THE NATURAL KEY, AND A RE-PULL THAT RESTATES ==")
dupes = [dict(r0), dict(r0)]
dupes[1]["amount"] = 3.0
one = feed.dedupe(dupes)
eq(len(one), 1, "two lines sharing the natural key become one")
eq(one[0]["amount"], 27.8,
   "…SUMMED, not overwritten — a surcharge printed as its own line is money still owed")
two = feed.dedupe([dict(r0), dict(res["rows"][1])])
eq(len(two), 2, "control: two different shipments stay two lines")
eq(feed.totals_by_invoice(res["rows"]), {"INV-9001": 33.9},
   "the invoice total is the sum of its lines, for checking against the supplier's own figure")
null_q = dict(r0)
null_q["quantity"] = None
summed = feed.dedupe([null_q, dict(r0)])
eq(summed[0]["quantity"], 1.0,
   "summing a None with a figure keeps the figure — a blank does not zero a count")

print("\n== §I  THE KEY THE CODE UPSERTS ON IS THE KEY THE MIGRATION CREATED ==")
mig = read("database/migrations/1098_supplier_invoice_line.sql")
m = re.search(r"create unique index if not exists supplier_invoice_line_key\s*"
              r"on commcalc\.supplier_invoice_line\s*\(([^)]*)\)", mig, re.I | re.S)
ok(m is not None, "mig 1098 creates the natural-key unique index")
if m:
    cols = tuple(c.strip() for c in m.group(1).split(","))
    eq(cols, tuple(c.strip() for c in sweep.ON_CONFLICT.split(",")),
       "…and it is EXACTLY what store_rows upserts on, so a re-pull can never stop restating")
eq(sweep.TABLE, "supplier_invoice_line", "the table is named once, by its owning module")
ok("commcalc.%s" % sweep.TABLE in mig, "…and that is the table the migration creates")
ok("grant select, insert, update, delete on commcalc.%s to service_role" % sweep.TABLE in mig.lower(),
   "a migration GRANTS what it creates — service_role only (the browser never reads billing detail)")
ok("-- REVERT:" in mig, "…and carries its REVERT note")
for col in ("shipment_key", "counter_key", "service_key", "store_key"):
    ok(col in mig, "the key column %s exists in the table" % col)
    ok(col in sweep._LAND_COLS, "…and is landed by the projection")

print("\n== §J  SUPPLIER-NEUTRAL, AND THE SIBLING IS NAMED NOT HIDDEN ==")
for word in ("ups", "fedex", "dhl", "usps", "cellfonz", "luxelink", "boost"):
    hit = re.search(r"\b%s\b" % word, feed_src, re.I) or re.search(r"\b%s\b" % word, sweep_src, re.I)
    ok(hit is None, "no supplier or tenant named in executable text: %r" % word)
ok(re.search(r"\bups\b", "upsert setup", re.I) is None,
   "control: the scan is whole-word, so 'upsert' and 'setup' are not a supplier")
ok(re.search(r"\bups\b", "pulled from ups today", re.I) is not None,
   "control: …and it does find a real mention")
ok("vip_invoice" in mig,
   "the migration NAMES the distributor-only sibling and why it is not reused — an unstated "
   "duplicate is worse than a stated one")
ok("supplier_invoice_line" in mig and "report_pull_map" in mig,
   "…and registers the feed's table where a consumer looks for it (mig 207's contract)")
reg = read("database/migrations/1099_connector_registry_supplier_invoice.sql")
ok("'supplier_invoice'" in reg, "the connector kind gets its registry DOOR, so a tenant can pick it")
ok("NULL, 'portal'" in reg,
   "…with no host: a kind, not a supplier, and the address is the tenant's own declaration")

print("\n== §K  IT WRITES ONLY WHAT IT SAYS IT WRITES ==")
ok("def run_supplier_invoice_sweep" in sweep_src, "the scheduled entry point exists")
ok("run_cold_sweep" in sweep_src, "…and the cold restore is the ONE loop's, not a copy")
for banned in ("async def", "playwright", "requests.", "httpx"):
    ok(banned not in feed_src, "the parser is pure: no %s in it" % banned)
ok("upsert" in sweep_src and "delete" not in sweep_src.lower().replace("delete on", ""),
   "the lander upserts and never deletes — a re-pull restates, it does not wipe a slice")
proj = sweep._project([dict(r0)], "ORG2", "SRC2")
eq(proj[0]["org_id"], "ORG2", "the projection stamps the org it was told")
eq(proj[0]["service_key"], feed.match_key(r0["service"]),
   "…and derives service_key with the SAME match_key the recon will use")
eq(set(proj[0]), set(sweep._LAND_COLS), "…and lands exactly the declared columns, no more")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
