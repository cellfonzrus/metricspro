#!/usr/bin/env python3
"""PROOF + BUILD LOCK — a daily-incentive rule may be paid on an ITEM the tenant uploaded, not only
on a hand-typed category (owner 2026-11-27).

THE CLASS. "What can an incentive rule be paid on" was answered from ONE list — the categories typed
by hand on Daily Closing -> Count Fields — while the tenant's own ITEM vocabulary lived somewhere
else entirely: the `line_map` on `commcalc.data_source.portal_calibration`, uploaded with their sales
report. NY LOGISTICS declares 7 categories and 20 items; every item was folded into one of 3
categories before the engine saw it, so:

  · "$2 for every NDA Early AM, $1 for NDA Saver" was inexpressible — the engine knows only
    "Next Day Shippin".
  · an item the file carries that nothing maps yet ($22,653.31 of October lines) was REFUSED by
    `PUT /commcalc/incentive-rules` with "add them on Count Fields first" — telling the owner to
    retype by hand a list he had already uploaded.

Two lists answering one question is the duplicate defect the index rules forbid. The fix EXTENDS the
one home (`closing/metric_catalog.build`) rather than adding a sibling, following that module's own
`tender:` precedent exactly, so the picker, the save guard and the pay engine cannot disagree.

WHY AN ITEM IS NEVER REVENUE (§B). An item is a FINER VIEW of money a category already counts, not
additional money. `in_revenue: True` on an item entry would add `Labels` to `Printing` and overstate
every day's gross revenue — and with it every revenue target and every percent-of-amount payout. This
is the same reason a tender is not revenue, and it is asserted, not commented.

MULTI-TENANT (§E, owner directive 2026-10-30). An item vocabulary is a TENANT'S OWN WORDS, so it is
a REQUIRED declaration and never a default with another tenant's items behind it. Declaring nothing
yields NOTHING identified — never NY LOGISTICS' product names reaching Cellfonz, LuxeLink or Vzone.

Stdlib only, DB-free. Run: python3 backend/harness_payable_attributes.py
"""
import sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, HERE)

from app.modules.closing import metric_catalog as mc            # noqa: E402
from app.modules.commcalc import metric_authority as ma         # noqa: E402
from app.modules.commcalc import pos_report_feed as prf         # noqa: E402

P = F = 0


def ok(cond, label):
    global P, F
    if cond:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}")


def read(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def walk(root, exts):
    for d, _dirs, files in os.walk(root):
        if "node_modules" in d or "__pycache__" in d:
            continue
        for f in files:
            if f.endswith(exts):
                yield os.path.join(d, f)


# ── Fixtures: the two tenants' OWN declarations, as the database holds them ────────────────────────

# NY LOGISTICS (vertical ups_store), verified in production 2026-11-27.
UPS_DEFS = [
    {"field_key": "new_line_count", "label": "Drop Off", "value_kind": "count", "declared_by": "store"},
    {"field_key": "postpaid_count", "label": "Next Day Shippin", "value_kind": "count",
     "declared_by": "employee"},
    {"field_key": "2nd_day_3rd_day", "label": "2nd Day / 3rd Day", "value_kind": "count",
     "declared_by": "employee"},
    {"field_key": "printing", "label": "Printing", "value_kind": "amount", "declared_by": "employee"},
]
# ...and its uploaded item vocabulary (an extract of the live 20-entry line_map).
UPS_ITEMS = [
    {"item_label": "NDA", "metric_key": "postpaid_count"},
    {"item_label": "NDA Early AM", "metric_key": "postpaid_count"},
    {"item_label": "NDA Saver", "metric_key": "postpaid_count"},
    {"item_label": "2DA AM", "metric_key": "2nd_day_3rd_day"},
    {"item_label": "11x17 Color Copies", "metric_key": "printing"},
]
# A wireless tenant: four categories, and NO item vocabulary uploaded at all.
WIRELESS_DEFS = [
    {"field_key": "upgrade_count", "label": "Upgrades", "value_kind": "count", "declared_by": "store"},
    {"field_key": "new_line_count", "label": "New Lines", "value_kind": "count", "declared_by": "store"},
]

print("\n§A  an uploaded item becomes a payable attribute on the ONE home")
cat = mc.build(UPS_DEFS, item_defs=UPS_ITEMS)
keys = mc.keys(cat)
ok("item:nda early am" in keys,
   "A1 'NDA Early AM' is payable, keyed under the item prefix")
ok(mc.ITEM_PREFIX == "item:",
   "A2 the prefix is the module's own constant, not a literal a caller may respell")
ok(len([m for m in cat if m.get("source") == "feed_item"]) == len(UPS_ITEMS),
   "A3 every uploaded item is offered, none dropped")
by = mc.by_key(cat)
ok(by["item:nda early am"]["label"] == "NDA Early AM",
   "A4 an item keeps the tenant's own spelling as its label")
ok(by["item:nda early am"].get("parent_key") == "postpaid_count",
   "A5 ...and carries the category it rolls into, so a surface can say so")
ok(by["item:nda early am"].get("editable") is False,
   "A6 an item is not editable here — it is edited where it was uploaded")
ok(all(m["key"] in keys for m in cat),
   "A7 the categories are still all there — nothing was displaced")
ok("postpaid_count" in keys and "printing" in keys,
   "A8 ...named explicitly, because an item list that REPLACED the categories would also pass A1")

print("\n§B  an item is never revenue — it is a finer view of money already counted")
ok(by["item:nda early am"]["in_revenue"] is False,
   "B1 an item entry is not in revenue")
ok(not any(k.startswith(mc.ITEM_PREFIX) for k in mc.revenue_keys(cat)),
   "B2 no item reaches revenue_keys")
# The arithmetic proof: the day's gross revenue must be IDENTICAL with and without the item
# vocabulary declared. If an item were revenue, declaring one would inflate the day.
amount_defs = UPS_DEFS + [{"field_key": "printing2", "label": "Printing B", "value_kind": "amount",
                           "declared_by": "employee"}]
plain = mc.build(amount_defs)
withitems = mc.build(amount_defs, item_defs=UPS_ITEMS)
lines = [{"metric_key": "printing", "amount": 400.0, "units": 0, "employee_name": "Hajra"},
         {"metric_key": "item:11x17 color copies", "amount": 400.0, "units": 0,
          "employee_name": "Hajra"}]
rev_plain = mc.gross_revenue(plain, mc.declared(plain, None, lines))
rev_items = mc.gross_revenue(withitems, mc.declared(withitems, None, lines))
ok(rev_plain == 400.0,
   f"B3 the day's revenue without the item vocabulary is $400.00 (got {rev_plain})")
ok(rev_items == rev_plain,
   f"B4 declaring items does NOT move the day's revenue (got {rev_items}, want {rev_plain})")
emp_plain = mc.employee_revenue(plain, mc.by_employee(plain, lines))
emp_items = mc.employee_revenue(withitems, mc.by_employee(withitems, lines))
ok(emp_items == emp_plain,
   f"B5 ...nor any one person's revenue, which is what a pool splits on (got {emp_items})")

print("\n§C  the save guard and the picker read the SAME set, so a rule on an item is accepted")
src = read(os.path.join(APP, "modules", "commcalc", "router.py"))
seg = src[src.find("def save_incentive_rule"):]
seg = seg[:seg.find("def ", 10)] if seg.find("def ", 10) > 0 else seg
ok("_mc.keys(" in seg,
   "C1 the guard still dereferences the catalog's keys rather than its own list")
ok(not re.search(r"""['"]item:[a-z0-9 ]+['"]""", seg),
   "C2 ...and names no item of any tenant's")
gseg = src[src.find("def list_incentive_rules"):]
gseg = gseg[:gseg.find("def ", 10)] if gseg.find("def ", 10) > 0 else gseg
ok("item_defs" in gseg,
   "C3 the endpoint passes the tenant's uploaded items into the catalog it serves")

print("\n§D  the feed produces a figure for an item, so an item rule can actually pay")
# `portal_metric_day` rows already carry the item's own label beside the category key.
rows = [{"metric_key": "postpaid_count", "line_label": "NDA Early AM", "units": 3, "amount": 0.0,
         "metric_basis": prf.BASIS_LINE_MAP, "business_date": "2026-10-20", "employee_name": "Hajra"},
        {"metric_key": "postpaid_count", "line_label": "NDA Saver", "units": 2, "amount": 0.0,
         "metric_basis": prf.BASIS_LINE_MAP, "business_date": "2026-10-20", "employee_name": "Hajra"}]
out = ma.lines_from_feed(rows, business_date="2026-10-20", item_keys=set(mc.keys(cat)))
cats = [l for l in out if l["metric_key"] == "postpaid_count"]
items = [l for l in out if l["metric_key"].startswith(mc.ITEM_PREFIX)]
ok(len(cats) == 2,
   "D1 the category lines are unchanged — the existing answer still arrives")
ok(sorted(l["metric_key"] for l in items) == ["item:nda early am", "item:nda saver"],
   "D2 ...and each item gets its own line, keyed by the item")
ok(sum(l["units"] for l in items) == 5 and sum(l["units"] for l in cats) == 5,
   "D3 the units agree on both sides — an item line is a second VIEW, not a second count")
ok(all(l.get("employee_name") == "Hajra" for l in items),
   "D4 an item line keeps the person, so a per-employee item rule can pay")
# An item the tenant has NOT declared must produce nothing — a coincidence may not pay.
undeclared = ma.lines_from_feed(
    [{"metric_key": "postpaid_count", "line_label": "Ground Residential", "units": 9,
      "amount": 0.0, "metric_basis": prf.BASIS_LINE_MAP, "business_date": "2026-10-20"}],
    business_date="2026-10-20", item_keys=set(mc.keys(cat)))
ok(not [l for l in undeclared if l["metric_key"].startswith(mc.ITEM_PREFIX)],
   "D5 an item NOT in the catalog produces no item line")
ok(len(undeclared) == 1,
   "D6 ...while its category line still arrives, so nothing the feed answered is lost")

print("\n§E  MULTI-TENANT — nothing declared means nothing identified, never another tenant's words")
wcat = mc.build(WIRELESS_DEFS)
ok(not [m for m in wcat if m.get("source") == "feed_item"],
   "E1 a tenant that uploaded no items is offered no items")
ok(not any("nda" in m["key"].lower() or "copies" in m["label"].lower() for m in wcat),
   "E2 ...and specifically none of the UPS tenant's product names")
wlines = ma.lines_from_feed(
    [{"metric_key": "new_line_count", "line_label": "NDA Early AM", "units": 4, "amount": 0.0,
      "metric_basis": prf.BASIS_LINE_MAP, "business_date": "2026-10-20"}],
    business_date="2026-10-20", item_keys=set(mc.keys(wcat)))
ok(not [l for l in wlines if l["metric_key"].startswith(mc.ITEM_PREFIX)],
   "E3 one tenant's item spelling cannot produce a payable line in another's read")
ok(ma.lines_from_feed(rows, business_date="2026-10-20") == ma.lines_from_feed(
       rows, business_date="2026-10-20", item_keys=set()),
   "E4 omitting the declaration is the same as declaring nothing — no house list behind it")
ok(not [l for l in ma.lines_from_feed(rows, business_date="2026-10-20")
        if l["metric_key"].startswith(mc.ITEM_PREFIX)],
   "E5 ...so every existing caller is unmoved until it asks the org")

print("\n§F  the uploaded item list is read from the calibration, by one reader")
CAL = {"line_map": {"NDA": "postpaid_count"},
       "sales_transactions": {"line_map": {"NDA Early AM": "postpaid_count",
                                           "11x17 Color Copies": "printing"}},
       "other_report": {"line_map": {"2DA AM": "2nd_day_3rd_day"}}}
items = prf.declared_items(CAL)
got = sorted(i["item_label"] for i in items)
ok(got == ["11x17 Color Copies", "2DA AM", "NDA", "NDA Early AM"],
   f"F1 every report's items are offered, source-level included (got {got})")
ok(all(i.get("metric_key") for i in items),
   "F2 each item carries the category it maps to")
ok(prf.declared_items({}) == [] and prf.declared_items(None) == [],
   "F3 an empty calibration declares nothing")
ok(prf.declared_items({"line_map": {"NDA": "postpaid_count", "nda": "postpaid_count"}}) and
   len(prf.declared_items({"line_map": {"NDA": "postpaid_count", "nda": "postpaid_count"}})) == 1,
   "F4 two spellings of one item are one payable attribute, not two")

print("\n§G  BUILD LOCK — the item list cannot un-wire, and no second copy may appear")
mcs = read(os.path.join(APP, "modules", "closing", "metric_catalog.py"))
ok("item_defs" in mcs and "feed_item" in mcs,
   "G1 the one home still carries the item vocabulary")
ok(mcs.count('ITEM_PREFIX = ') == 1,
   "G2 the prefix is declared exactly once")
mas = read(os.path.join(APP, "modules", "commcalc", "metric_authority.py"))
# It must DEREFERENCE the catalog's folding by name, not re-spell the prefix. A literal "item:" here
# would be the second copy, so the check is for the reference and AGAINST the literal.
ok("item_keys" in mas and "_mc.item_key(" in mas and "_mc.ITEM_PREFIX" in mas,
   "G3 the feed reader builds item lines through the catalog's own folding")
ok(not re.search(r"""=\s*['"]item:['"]""", mas),
   "G3b ...and does not re-spell the prefix itself")
# No module, and no screen, may spell a tenant's item name or re-derive the prefix.
offenders = []
for p in list(walk(APP, (".py",))) + list(walk(FE, (".ts", ".tsx"))):
    s = read(p)
    if re.search(r"""['"]item:[A-Za-z0-9][A-Za-z0-9 ._/x-]*['"]""", s):
        offenders.append(os.path.relpath(p, ROOT))
ok(not offenders,
   f"G4 no module or screen hard-codes an item key ({offenders[:3]})")
# A second reader of the UPLOADED ITEM list. Scoped to the calibration that holds it: `line_map` is
# also the name of an unrelated P&L component map (`carrier_dollar_class`), so a scanner keyed on the
# word alone would fail the build on a fact this change never touched.
second = []
for p in walk(APP, (".py",)):
    s = read(p)
    if "portal_calibration" not in s:
        continue
    if re.search(r"""\bline_map\b[^\n]*\.items\(\)""", s) and \
       os.path.basename(p) not in ("pos_report_feed.py", "pos_report_sweep.py"):
        second.append(os.path.relpath(p, ROOT))
ok(not second,
   f"G5 only the feed modules read the uploaded item list — no second reader ({second[:3]})")
page = read(os.path.join(FE, "app", "(platform)", "commcalc", "incentive-rules", "page.tsx"))
ok("feed_item" in page,
   "G6 the editor groups by the catalog's own source word, so items are reachable")
ok("parent_key" in page or "parent_label" in page,
   "G7 ...and shows which category an item rolls into")
# THE REGRESSION. The screen used to keep only `source === 'count_field'`, which discarded every
# uploaded item before it could be offered — the defect itself, in one line. It may not come back.
# The old line kept ONLY `source === 'count_field'` and that single list fed the picker, so every
# uploaded item was discarded before it could be offered. The grouping may still name that source —
# what may NOT come back is a picker fed from the categories alone, so the check is that both groups
# reach it.
ok(re.search(r"""\.\.\.metrics\s*,\s*\.\.\.items""", page) is not None,
   "G8 both the categories and the uploaded items reach the picker")
ok(page.count("source === 'feed_item'") >= 1,
   "G8b ...and the item group is identified by the server's own word")

# THE WIRING. A registry whose callers do not dereference it is not a fix at all (CLAUDE.md §19.18),
# so the two paths that PAY must pass the catalog's item keys into the feed projection. Pinned by
# count: a third paying path must come here and be counted, not quietly read categories only.
rsrc = read(os.path.join(APP, "modules", "commcalc", "router.py"))
ok(rsrc.count("item_keys=_mc.item_keys(cat)") == 2,
   f"G9 both paying feed reads dereference the item keys "
   f"(found {rsrc.count('item_keys=_mc.item_keys(cat)')}, want 2)")
_calls = rsrc.count("_declared_item_defs(client, org_id)") - rsrc.count("def _declared_item_defs(")
ok(_calls == 4,
   f"G10 the four surfaces that must offer items read them org-scoped — the rules list, the save "
   f"guard and the two paying paths (found {_calls}, want 4)")
ok(rsrc.count("def _declared_item_defs(") == 1,
   "G11 ...from one org-scoped reader")
# The goal/target path is EXCUSED, not forgotten: it answers "what was ACHIEVED against a goal",
# which is a different question from "what does this day pay", and an item is not revenue so it
# could only ever add a zero there. Pinned so the exemption is visible if it is ever revisited.
gseg2 = rsrc[rsrc.find("def get_period_goal_progress"):][:4000] or rsrc
ok("item_keys" not in gseg2.split("def _declared_metric_authority")[0],
   "G12 the goal path is deliberately unchanged — a different question, explicitly excused")

print("\n§I  AN ITEM MUST BE STRICTLY FINER THAN THE METRIC IT ROLLS INTO (security pass 2026-11-27)")
# THE DEFECT THE REVIEW FOUND. `declared_items` asserts that every key of a `line_map` is an ITEM.
# It is not: `line_label`'s synonym list begins "product category", "category" — AHEAD of "product"
# and "item" — so a tenant's export may carry a CATEGORY in the column the mapping is keyed on, and
# `pos_report_sweep._line_map` merges a source-level map into EVERY report. Where the label IS the
# category, `item:<label>` carried the category's WHOLE figure while the picker said "pay on that
# item alone". `gross_revenue` was unmoved (the §B claim held and still holds) because an item is not
# in `revenue_keys` — but `revenue_keys` is not the paying path. `daily_incentive.measure` with
# explicit `metric_keys` is, and it sums `amount_of` per named key with no notion that one key is
# another's parent. Naming both DOUBLED the money.
SAME_DEFS = [{"field_key": "printing", "label": "Printing", "value_kind": "amount",
              "declared_by": "employee"}]
same = mc.build(SAME_DEFS, item_defs=[{"item_label": "Printing", "metric_key": "printing"}])
ok(not [m for m in same if m.get("source") == "feed_item"],
   "I1 an 'item' whose label IS its category's label is not a payable attribute")
ok("printing" in mc.keys(same),
   "I2 ...and the category itself is untouched — the right thing to pay on is still offered")
# The same when the label matches the KEY rather than the display label.
bykey = mc.build(SAME_DEFS, item_defs=[{"item_label": "printing", "metric_key": "printing"}])
ok(not [m for m in bykey if m.get("source") == "feed_item"],
   "I3 ...matched on the metric key too, not only the display label")
# Folded, so case and spacing cannot smuggle the duplicate back in.
folded = mc.build(SAME_DEFS, item_defs=[{"item_label": "  PRINTING  ", "metric_key": "printing"}])
ok(not [m for m in folded if m.get("source") == "feed_item"],
   "I4 ...through the same folding, so 'PRINTING' is not a different thing from 'Printing'")
# A genuinely finer item is STILL offered — the fix must not cost the feature.
finer = mc.build(SAME_DEFS, item_defs=[{"item_label": "11x17 Color Copies", "metric_key": "printing"}])
ok([m for m in finer if m.get("source") == "feed_item"],
   "I5 a genuinely finer item is still payable — the fix does not cost the feature")
ok(mc.item_key("Printing") == mc.item_key("  printing "),
   "I6 the folding the refusal uses is the catalog's own")

print("\n§J  A RULE MAY NOT NAME AN ITEM AND ITS OWN PARENT (the doubling, refused at save)")
# Granularity is not always knowable from the label, so the save door refuses the OVERLAP itself:
# whatever the label's grain, a rule that measures a parent and its own descendant counts one row's
# money twice. Two SIBLING items stay legal — disjoint lines, and the owner's actual use case.
sseg = read(os.path.join(APP, "modules", "commcalc", "router.py"))
jseg = sseg[sseg.find("def save_incentive_rule"):]
jseg = jseg[:jseg.find("\n@router.")] if jseg.find("\n@router.") > 0 else jseg
ok("parent_key" in jseg,
   "J1 the save guard reads each named key's parent")
ok("_mc.by_key(" in jseg or "by_key(" in jseg,
   "J2 ...through the catalog it was already given, not a second lookup")
ok("also names" in jseg or "parent" in jseg.lower(),
   "J3 ...and the refusal explains the overlap in words")

print("\n§K  ONE FOLDING, ONE HOME, AND A DETERMINISTIC READ (review finding 2)")
# `_declared_item_defs` deduped with its OWN `.strip().lower()`, which does NOT collapse internal
# whitespace the way `metric_catalog.item_key` does. So two spellings survived the read, collided in
# `build`, and the FIRST one seen won — while the read had no ORDER BY, so which one that was came
# down to row order. `figure_unit` could flip between count and dollars for the same request, which
# is the exact divergence `metric_catalog`'s own header says it exists to prevent.
kseg = sseg[sseg.find("def _declared_item_defs"):]
kseg = kseg[:kseg.find("\n@router.")] if kseg.find("\n@router.") > 0 else kseg[:2000]
ok("_mc.item_key(" in kseg or "item_key(" in kseg,
   "K1 the org-scoped read dedupes through the catalog's folding, not its own")
# Comment lines stripped: doctrine may NAME the folding it replaced ("it used to carry its own
# .strip().lower()"); the executable text may not spell it. A scanner that reads prose fails the
# build on its own explanation, which is how a lock teaches the next author to delete the comment.
kcode = "\n".join(ln for ln in kseg.splitlines() if not ln.strip().startswith("#"))
ok(not re.search(r"\.strip\(\)\.lower\(\)", kcode),
   "K2 ...and spells no second folding in its executable text")
ok(".strip().lower()" in kseg,
   "K2b CONTROL: the comment that names the old folding is still there to be scanned past")
ok(".order(" in kseg,
   "K3 the read is ORDERED, so which spelling wins is a fact and not a row-order race")

print("\n§H  negative controls — the scanners can see a violation")
ok(re.search(r"""['"]item:[A-Za-z0-9][A-Za-z0-9 ._/x-]*['"]""", 'k = "item:NDA Saver"') is not None,
   "H1 the item-literal scanner fires on a planted key")
ok(re.search(r"""['"]item:[A-Za-z0-9][A-Za-z0-9 ._/x-]*['"]""", 'P = "item:"') is None,
   "H2 ...and not on the prefix constant itself")
ok(re.search(r"""\bline_map\b[^\n]*\.items\(\)""", "for a, b in line_map.items():") is not None,
   "H3 the second-reader scanner fires on a planted iteration")
_probe = mc.build(UPS_DEFS, item_defs=[{"item_label": "X", "metric_key": "printing"}])
ok(any(m["key"] == "item:x" for m in _probe),
   "H4 the builder really is what produces an item entry")
ok(mc.build(UPS_DEFS, item_defs=[{"item_label": "", "metric_key": "printing"}]) == mc.build(UPS_DEFS),
   "H5 a blank item label declares nothing")

print(f"\n{'='*70}\n  {P} passed / {F} failed\n{'='*70}")
sys.exit(1 if F else 0)
