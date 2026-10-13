"""Proof harness: the back-office POS report feed — export payload in, declared-category rows out.

WHY THIS EXISTS. The portal sits behind a login with a second factor that nobody can reach from CI, so
the part of the integration that CAN be proven without the portal is proven here: given the report the
portal exports and the categories the TENANT declared, do we produce the right rows, and do we refuse
to produce a row we cannot justify? That is where the damage would be — a category matched by
resemblance and then compared against a rep's declaration as if it were the same thing, a worker's
lines summed into one, a totals row counted as a day, a store spelling landing twice.

Fixtures are REPRESENTATIVE shapes (header wording, a banner above the header, a totals row, two
spellings of one store, several lines inside one category). The CATEGORIES in them are the fixture's
own invention and must stay that way: this file proves a MECHANISM, and a tenant's real categories
living in a harness would be the hard-coding the owner asked us not to do.

No DB, no network, no browser. Run:  cd backend && python3 harness_pos_report_feed.py
Exit 0 = all green.
"""
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import pos_report_feed as feed              # noqa: E402
from app.modules.commcalc import pos_report_sweep as adapter          # noqa: E402
from app.modules.commcalc import portal_report_pull as prp            # noqa: E402

PASS = FAIL = 0
_HERE = os.path.dirname(os.path.abspath(__file__))
_MIG = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations",
                                     "1078_portal_metric_day.sql"))


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


# ── the tenant's declared catalog (what metric_catalog.build returns, in the shape callers read) ──
CATALOG = [
    {"key": "pkg_dropoff", "label": "Package Drop Off", "value_kind": "both", "declared_by": "employee"},
    {"key": "ship_second_day", "label": "Second Day", "value_kind": "both", "declared_by": "employee"},
    {"key": "ship_third_day", "label": "Third Day", "value_kind": "both", "declared_by": "employee"},
    {"key": "printing", "label": "Printing", "value_kind": "amount", "declared_by": "employee"},
    {"key": "mailbox_annual", "label": "Mailbox Annual", "value_kind": "both", "declared_by": "store"},
]

# A representative export: a title banner above the real header, two stores, two workers, several
# lines inside one category on one day, a blank row, and the trailing TOTAL row every portal prints.
EXPORT = """Worker Sales By Product Category
Generated 10/12/2026

Date,Center Number,Worker,Product Category,Quantity,Net Sales
10/11/2026,0001,Asha,Package Drop Off,6,$0.00
10/11/2026,0001,Asha,Package Drop Off,4,$0.00
10/11/2026,0001,Asha,Second Day,3,"$1,240.50"
10/11/2026,0001,Bea,Second Day,1,$310.00
10/11/2026,0001,Bea,Printing,0,$84.25
10/11/2026,0002,Cyd,Third Day,2,$220.00
10/11/2026,0002,Cyd,Notary Service,1,$15.00
,,,,,
TOTAL,,,,17,"$1,869.75"
"""


def table():
    return adapter.read_table(EXPORT, "worker_sales.csv")


def normalize(**kw):
    kw.setdefault("catalog", CATALOG)
    kw.setdefault("org_id", "ORG")
    kw.setdefault("source_id", "SRC")
    kw.setdefault("connector", adapter.CONNECTOR)
    kw.setdefault("report_key", "worker_sales_by_category")
    return feed.normalize(table(), **kw)


print("\nA. the header is found under the banner the portal prints above it")
tbl = table()
eq(tbl[0][0], "Date", "the real header row is the first row returned, not the title banner")
idx = feed.map_headers(tbl[0])
for field in ("business_date", "store_code", "employee", "line_label", "units", "amount"):
    ok(field in idx, "the '%s' axis is recognized in this export's own wording" % field)
ok(feed.header_score(tbl[0]) >= 2, "header_score lets read_table tell the header from the banner")
ok(feed.header_score(["Worker Sales By Product Category"]) < 2,
   "…and the banner itself does not score as a header")

print("\nB. the match ladder — and what it refuses")
k, b = feed.match_metric("Second Day", catalog=CATALOG)
eq((k, b), ("ship_second_day", feed.BASIS_LABEL),
   "the portal's wording IS the category's label — the design effect, no mapping needed")
k, b = feed.match_metric("2nd Day Air", catalog=CATALOG,
                         line_map={"2nd day air": "ship_second_day"})
eq((k, b), ("ship_second_day", feed.BASIS_LINE_MAP),
   "the tenant's own mapping wins, and is recorded as the basis")
k, b = feed.match_metric("  2ND-DAY   AIR ", catalog=CATALOG,
                         line_map={"2nd day air": "ship_second_day"})
eq(k, "ship_second_day", "matching is normalized: case, spacing and punctuation do not matter")
k, b = feed.match_metric("Printing Services", catalog=CATALOG)
eq((k, b), ("printing", feed.BASIS_LABEL_PART),
   "one-and-only-one containment match is accepted, and says so")
k, b = feed.match_metric("Notary Service", catalog=CATALOG)
eq((k, b), (None, feed.BASIS_UNMATCHED), "a line nothing declares is NOT guessed")
k, b = feed.match_metric("Day", catalog=CATALOG)
eq((k, b), (None, feed.BASIS_AMBIGUOUS),
   "two categories could claim it, so NEITHER does — a comparison must not rest on a coin toss")
two = CATALOG + [{"key": "printing_colour", "label": "Printing", "value_kind": "amount"}]
k, b = feed.match_metric("Printing", catalog=two)
eq((k, b), (None, feed.BASIS_AMBIGUOUS), "two categories sharing one label is ambiguous, not the first one")
k, b = feed.match_metric("Second Day", catalog=CATALOG, line_map={"second day": "no_such_key"})
eq((k, b), ("ship_second_day", feed.BASIS_LABEL),
   "a mapping onto a category the tenant no longer declares is not honoured silently")
eq(feed.match_metric("", catalog=CATALOG), (None, feed.BASIS_UNMATCHED), "a blank line label matches nothing")
eq(feed.match_metric("Second Day", catalog=[]), (None, feed.BASIS_UNMATCHED),
   "no declared categories ⇒ nothing matches (the adapter refuses the whole report in that case)")

print("\nC. basis is evidence, or it is not (the §66 rule in its second home)")
ok(feed.basis_is_declared(feed.BASIS_LINE_MAP) and feed.basis_is_declared(feed.BASIS_LABEL),
   "a declared mapping and a label match ARE the tenant's own words")
for b in (feed.BASIS_LABEL_PART, feed.BASIS_UNMATCHED, feed.BASIS_AMBIGUOUS):
    ok(not feed.basis_is_declared(b), "'%s' is NOT read as the tenant's declaration" % b)
ok(not feed.basis_is_declared("some_basis_invented_next_year"),
   "DECLARED_BASES is an ALLOWLIST — an unruled basis reads as not evidence")

print("\nD. the normalizer — rows, and the rows it will not write")
res = normalize()
rows = res["rows"]
by = {(r["store_code"], r["employee_name"], r["metric_key"]): r for r in rows}
eq(len(rows), 5, "five (store, worker, category) rows from this export")
eq(by[("0001", "Asha", "pkg_dropoff")]["units"], 10,
   "two lines of one category on one day are SUMMED, not overwritten (6 + 4)")
eq(by[("0001", "Asha", "ship_second_day")]["amount"], 1240.50, "'$1,240.50' parses as money")
eq(by[("0001", "Bea", "printing")]["units"], 0, "an amount-only category keeps its zero units")
ok(all(r["business_date"] == "2026-10-11" for r in rows), "MM/DD/YYYY became an ISO business date")
ok(("0002", "Cyd", "ship_third_day") in by, "the second store's worker is kept separate")
ok(not any(r["metric_key"] == "" for r in rows), "no row carries an empty category")
ok(all(r["employee_name"] for r in rows), "the worker's own spelling is kept on every row")
ok("Notary" not in str(rows), "the unmatched line produced NO row…")
unmapped = {u["line"]: u for u in res["unmapped"]}
ok("Notary Service" in unmapped, "…and is reported, with the money it carried")
eq(unmapped["Notary Service"]["amount"], 15.00, "the unmapped line's dollars are stated, not dropped")
ok(any("not mapped to a declared category" in w for w in res["warnings"]),
   "the pull's answer says a human must map it or add the category")
ok(not any(r.get("line_label", "").upper() == "TOTAL" for r in rows),
   "the TOTAL row is not a day (no date ⇒ skipped, never summed as one)")
eq(sum(1 for r in rows if r["store_code"] == "0001"), 4, "store 0001 contributes four worker-category rows")

print("\nE. two spellings of one store-day-worker-category restate each other")
twice = feed.dedupe([
    {"business_date": "2026-10-11", "store_code": "B-1", "employee_name": "Asha",
     "metric_key": "printing", "metric_basis": feed.BASIS_LABEL, "units": 1, "amount": 10.0},
    {"business_date": "2026-10-11", "store_code": "b 1", "employee_name": "asha",
     "metric_key": "printing", "metric_basis": feed.BASIS_LABEL, "units": 2, "amount": 5.5},
])
eq(len(twice), 1, "'B-1'/'b 1' and 'Asha'/'asha' are ONE row — the store-code case trap, closed here")
eq(twice[0]["amount"], 15.5, "…with the figures summed")
weak = feed.dedupe([
    {"business_date": "2026-10-11", "store_code": "B-1", "employee_name": "A", "metric_key": "printing",
     "metric_basis": feed.BASIS_LABEL, "units": 1, "amount": 10.0},
    {"business_date": "2026-10-11", "store_code": "B-1", "employee_name": "A", "metric_key": "printing",
     "metric_basis": feed.BASIS_LABEL_PART, "units": 1, "amount": 1.0},
])
eq(weak[0]["metric_basis"], feed.BASIS_LABEL_PART,
   "a fold takes the WEAKEST basis — one coincidence in the sum makes the sum not a declaration")

print("\nF. an unplaced store is reported, never attributed and never zero")
res2 = normalize(resolve_store=lambda raw: "B-100" if raw == "0001" else None)
placed = [r for r in res2["rows"] if r["store_code"]]
unplaced = [r for r in res2["rows"] if not r["store_code"]]
ok(all(r["store_code"] == "B-100" for r in placed), "a resolvable store lands on its canonical code")
eq(len(unplaced), 1, "store 0002 resolves to nothing — its row is still STORED")
ok(unplaced and all(r["store_label"] == "0002" for r in unplaced),
   "…keeping the portal's own spelling as evidence")
totals = feed.totals_by_store_day(res2["rows"])
ok(all(k[0] for k in totals), "…and EXCLUDED from store totals, so no store reads high")
ok(("B-100", "2026-10-11", "pkg_dropoff") in totals, "the placed store's totals are keyed for comparison")
mixed = feed.totals_by_store_day([
    {"store_code": "B-1", "business_date": "2026-10-11", "metric_key": "printing",
     "metric_basis": feed.BASIS_LABEL, "units": 1, "amount": 10.0},
    {"store_code": "B-1", "business_date": "2026-10-11", "metric_key": "printing",
     "metric_basis": feed.BASIS_LABEL_PART, "units": 1, "amount": 1.0},
])
ok(mixed[("B-1", "2026-10-11", "printing")]["declared_basis"] is False,
   "a total containing a coincidence is flagged — the consumer can see it is not all declared")

print("\nG. an export this feed cannot read fails NAMED, never silently")
eq(feed.normalize([], catalog=CATALOG)["rows"], [], "an empty export produces no rows")
ok(feed.normalize([], catalog=CATALOG)["warnings"], "…and says it was empty")
nocat = feed.normalize([["Date", "Worker", "Quantity"], ["10/11/2026", "Asha", "3"]], catalog=CATALOG)
eq(nocat["rows"], [], "an export with no category column produces NOTHING")
ok(any("names the report line" in w for w in nocat["warnings"]),
   "…and says which column is missing and where to declare it")
nodate = feed.normalize([["Date", "Product Category", "Quantity"], ["", "Second Day", "3"]],
                        catalog=CATALOG)
eq(nodate["rows"], [], "a line with no date is never given a day")
ok(nodate["skipped"] and nodate["skipped"][0]["why"] == "no date on the row", "…and is reported as skipped")

print("\nH. the adapter contract, and the config it refuses to assume")
for name in prp.REQUIRED:
    ok(callable(getattr(adapter, name, None)), "the adapter exposes %s() — the pull loop's contract" % name)
eq(adapter.connector_keys(), ("pos_report",), "it serves one connector KIND, not a vendor")
eq(prp.adapter_for("pos_report"), adapter, "the pull registry resolves this connector to this adapter")
ok(adapter.portal_descriptor("pos_report")["base_url"] is None,
   "the portal's ADDRESS is config on the source row — no vendor URL in this repository")
eq(adapter.report_specs("pos_report", None, src_row={}), (),
   "a source that has declared no report pulls NOTHING")
ok("live login" in adapter.setup_hint({}) and "export" in adapter.setup_hint({}),
   "…and the answer says what the operator must declare, where they are already looking")
specs = adapter.report_specs("pos_report", None, src_row={"portal_reports": [
    {"key": "worker_sales", "label": "Worker sales by category", "nav": ["reports", "worker sales"]}]})
eq(len(specs), 1, "a declared report becomes one spec")
eq(specs[0]["nav"], ("reports", "worker sales"), "the menu words are the tenant's, from config")
bare = adapter.report_specs("pos_report", None, src_row={"portal_reports": ["Worker Sales"]})
eq(bare[0]["key"], "worker_sales", "a bare string declaration still works (the key's words are the hint)")
ok("worker" in bare[0]["nav"] and "sales" in bare[0]["nav"], "…and its own words become the navigation hint")
filtered = adapter.report_specs("pos_report", ["worker_sales"], src_row={"portal_reports": [
    {"key": "worker_sales", "label": "A"}, {"key": "other", "label": "B"}]})
eq([s["key"] for s in filtered], ["worker_sales"], "the enabled-report filter narrows the declaration")
lm = adapter._line_map({"portal_calibration": {"line_map": {"a": "k1"},
                                               "worker_sales": {"line_map": {"b": "k2"}}}},
                       {"key": "worker_sales"})
eq(lm, {"a": "k1", "b": "k2"}, "the source's mapping and the report's own are ONE fact, narrower winning")

print("\nI. the code's natural key IS the migration's unique index")
sql = open(_MIG).read()
m = re.search(r"create unique index[^(]*portal_metric_day_key\s*\n?\s*on commcalc\.portal_metric_day \(([^)]*)\)",
              sql, re.I)
ok(m is not None, "migration 1078 declares the natural-key unique index")
cols = [c.strip() for c in (m.group(1) if m else "").split(",")]
eq(cols, [c.strip() for c in adapter.ON_CONFLICT.split(",")],
   "the upsert's on_conflict and the index agree, column for column — a drift here duplicates days")
for c in cols:
    ok(("%s " % c) in sql or ("%s\n" % c) in sql, "the key column '%s' exists on the table" % c)
ok(all(c in sql for c in adapter._LAND_COLS),
   "every column the adapter writes exists in the migration")
ok(adapter.TABLE in sql and ("commcalc.%s" % adapter.TABLE) in sql,
   "the table the adapter names is the table the migration creates")
ok("-- REVERT:" in sql or "-- REVERT: " in sql, "the migration carries its REVERT note")
ok("create table if not exists" in sql.lower() and "if not exists" in sql.lower(),
   "idempotent and additive")

print("\nJ. negative controls — the checks above can fail")
ok(feed.match_metric("Second Day", catalog=CATALOG)[0] is not None,
   "control: a label match DOES resolve (so B is not vacuously green)")
ok(feed.norm("B-1") == feed.norm("b 1") and feed.norm("B-1") != feed.norm("B-2"),
   "control: norm folds spelling but does not fold two different stores together")
ok(len(feed.dedupe([{"business_date": "d", "store_code": "a", "employee_name": "x",
                     "metric_key": "k1", "metric_basis": feed.BASIS_LABEL, "units": 1, "amount": 1},
                    {"business_date": "d", "store_code": "a", "employee_name": "x",
                     "metric_key": "k2", "metric_basis": feed.BASIS_LABEL, "units": 1, "amount": 1}])) == 2,
   "control: two DIFFERENT categories are not folded into one")
_armed = [m for m in CATALOG if m["key"] != "printing"]
ok(feed.match_metric("Printing Services", catalog=_armed)[0] is None,
   "control: remove the category and its containment match goes away — §B is reading the catalog, "
   "not a list in this file")

src = open(os.path.join(_HERE, "app/modules/commcalc/pos_report_feed.py")).read()
code = re.sub(r'"""[\s\S]*?"""', "", src)
print("\nK. RULE TWO — no tenant, vendor or category is spelled in the code")
def spells(text, word):
    """Whole-word search. A substring scan is useless here: 'ups' is inside 'upsert' and 'setup', and a
    check that cannot tell those apart would either be red forever or be deleted."""
    return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(word), text.lower()) is not None

FORBIDDEN = ("ups", "theupsstore", "drop off", "dropoff", "2nd day", "second day", "third day",
             "notary", "mailbox", "logistics", "shopify", "boost", "total wireless")
acode = re.sub(r'"""[\s\S]*?"""', "", open(os.path.join(_HERE, "app/modules/commcalc/pos_report_sweep.py")).read())
for word in FORBIDDEN:
    ok(not spells(code, word),
       "the feed's executable text does not spell %r (it is config, or the tenant's own label)" % word)
    ok(not spells(acode, word), "the adapter's executable text does not spell %r" % word)
ok(spells("a notary service", "notary") and not spells("upsert setup", "ups"),
   "control: the word scan finds a real mention and is not fooled by 'upsert' or 'setup'")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
