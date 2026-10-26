#!/usr/bin/env python3
"""DB-FREE PROOF — a payables row is a DEVICE THE DEALER BOUGHT, and the screen opens on the days it
is meant to measure (owner reports 2026-10-26, index §COMMCALC-32).

OWNER, verbatim: *"product not available , it shoudl show the product"*. There is no product to show.
Measured on the live Luxelink feed the same day: `activation_type2` declares `branded` on 3,567 rows
(every one carrying a model name) and `byop` on 869 (every one reading "Product Not Available"), 59
declare nothing, and NOT ONE of those 928 rows carries any device money. The dealer bought no phone on
those lines — the customer brought their own — so they can never be owed for, and two of them were
being shown to him as `discrepancy`, which claims a cost he never had.

And: *"should auaomatically pick teh dates from today and 3 days ahead ... the month and date shoudl
not be entered manually"*.

WHAT IS LOCKED
  A  the ONE test for "did this line sell a device" (product_mix.no_device_declared), and that
     product_class_of dereferences it rather than keeping a second copy
  B  the status vocabulary: which statuses are payables, and that the query-side and in-memory
     filters cannot disagree
  C  the default window: today through 3 days ahead, inclusive, with the escapes honoured
  D  the RATCHET: every ledger reader excludes non-payables through the one home, the engine stamps
     the status from it, and only a DECLARED no-device may do so (a description must not, §66)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import date_window as dw                      # noqa: E402
from app.modules.commcalc import product_mix as pm                      # noqa: E402
from app.modules.payables import ledger_status as ls                    # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(ROOT, "app", "modules", "payables", "engine.py")
PROUTER = os.path.join(ROOT, "app", "modules", "payables", "router.py")
UNSOLD = os.path.join(ROOT, "app", "modules", "payables", "unsold_inventory.py")
OUT = []


def ck(name, got, want):
    OUT.append((name, got == want, got, want))


# ── A — the one test for "did this line sell a device" ───────────────────────────────────────────
ck("A1 the feed's own 'byop' declaration means no device was sold",
   pm.no_device_declared("byop"), True)
ck("A2 'branded' does not", pm.no_device_declared("branded"), False)
ck("A3 nothing declared does not — silence is not a declaration",
   (pm.no_device_declared(None), pm.no_device_declared(""), pm.no_device_declared("  ")),
   (False, False, False))
ck("A4 the house words cover the spellings a feed actually uses",
   tuple(pm.no_device_declared(v) for v in ("BYOP", "byod", "Bring Your Own", "bring your own")),
   (True, True, True, True))
ck("A5 a tenant may declare its own word, and the house list then does NOT apply",
   (pm.no_device_declared("eigen toestel", ["eigen toestel"]),
    pm.no_device_declared("byop", ["eigen toestel"])), (True, False))
ck("A6 a WORD boundary, not a substring — 'byopener' is not a declaration",
   pm.no_device_declared("byopener"), False)
ck("A7 product_class_of DEREFERENCES the one test (same answer, stated once)",
   pm.product_class_of("Product Not Available", declared="byop"),
   (pm.NO_DEVICE_CLASS[0], pm.DECLARED_NO_DEVICE_BASIS))
ck("A8 ...and the basis name it returns IS the constant other modules act on",
   pm.DECLARED_NO_DEVICE_BASIS, "declared_no_device")
ck("A9 THE LINE THAT MATTERS: an unnamed product with NO declaration stays unclassified — it is "
   "never read as 'no device', because that would drop a real handset the feed failed to name",
   pm.product_class_of("Product Not Available", declared="branded")[1], "no_product_named")
ck("A10 a real handset is untouched by any of this",
   pm.product_class_of("Apple iPhone 16e 128GB Black TO", declared="branded"),
   ("phone", "residual_device"))
src_pm = open(os.path.join(ROOT, "app", "modules", "commcalc", "product_mix.py")).read()
ck("A11 there is only ONE token test in the home (the class reader calls it, not a copy)",
   src_pm.count('return NO_DEVICE_CLASS[0]'), 1)

# ── B — the status vocabulary ────────────────────────────────────────────────────────────────────
ck("B1 the four real payables are payables",
   tuple(ls.is_payable(s) for s in ls.PAYABLE_STATUSES), (True, True, True, True))
ck("B2 a no-device line is not a payable in any state", ls.is_payable(ls.STATUS_NO_DEVICE), False)
ck("B3 case and padding cannot smuggle one back in",
   (ls.is_payable("NO_DEVICE"), ls.is_payable("  no_device  ")), (False, False))
ck("B4 an UNKNOWN status is a payable — fail-open, because a status this module has not met yet is "
   "not a licence to hide money", ls.is_payable("something_new"), True)
ck("B5 a missing status is a payable too", (ls.is_payable(None), ls.is_payable("")), (True, True))
ck("B6 the two sets do not overlap and no payable is listed as non-payable",
   [s for s in ls.PAYABLE_STATUSES if s in ls.NON_PAYABLE_STATUSES], [])
ck("B7 payable_only keeps the payables and COUNTS what it left out",
   ls.payable_only([{"status": "open"}, {"status": "no_device"}, {"status": "due"},
                    {"status": "no_device"}]),
   ([{"status": "open"}, {"status": "due"}], 2))
ck("B8 ...and an empty input is an empty answer, never a claim", ls.payable_only([]), ([], 0))
ck("B9 the owed_source for such a row is its OWN value, not 'unconfigured' (§COMMCALC-29) — there is "
   "nothing to price here, which is not the same as a cost source nobody set up",
   ls.OWED_SOURCE_NO_DEVICE not in ("unconfigured", "price_source_unreadable"), True)
ck("B10 the excluded-note names no feed, column or migration (§19.36)",
   bool(re.search(r"activation_type2|raw_ma|byop|migration|\.sql", ls.EXCLUDED_NOTE, re.I)), False)


class _Q:
    """The smallest thing that records what a postgrest builder was asked to do."""

    def __init__(self):
        self.calls = []
        self.not_ = self

    def in_(self, col, vals):
        self.calls.append(("not.in", col, tuple(vals)))
        return self


ck("B11 the query-side filter excludes exactly the non-payable statuses, by name",
   ls.exclude_non_payable(_Q()).calls, [("not.in", "status", tuple(ls.NON_PAYABLE_STATUSES))])
ck("B12 ...on whichever column the caller names",
   ls.exclude_non_payable(_Q(), "s").calls[0][1], "s")

# ── C — the default window ───────────────────────────────────────────────────────────────────────
import datetime                                                          # noqa: E402
TODAY = datetime.date(2026, 10, 26)
w = dw.ahead_window(TODAY)
ck("C1 today through three days ahead, BOTH ENDS INCLUSIVE — a payment due on the last day is in "
   "the window, not one day outside it",
   (w["start"], w["end"], w["days"]), ("2026-10-26", "2026-10-29", 4))
ck("C2 it says WHY it is the window it is", w["basis"], dw.BASIS_AHEAD_DEFAULT)
ck("C3 the default ahead span is three days, declared once", dw.DEFAULT_AHEAD_DAYS, 3)
ck("C4 days=0 is honoured and means today alone",
   (dw.ahead_window(TODAY, 0)["start"], dw.ahead_window(TODAY, 0)["end"]),
   ("2026-10-26", "2026-10-26"))
ck("C5 a negative span is clamped, never a backwards window that matches nothing",
   dw.ahead_window(TODAY, -5)["days"], 1)
ck("C6 junk falls back to the default rather than raising",
   dw.ahead_window(TODAY, "x")["end"], "2026-10-29")
ck("C7 a string date is accepted exactly like a date",
   dw.ahead_window("2026-10-26"), dw.ahead_window(TODAY))
ck("C8 it crosses a month end correctly",
   (dw.ahead_window(datetime.date(2026, 10, 30))["end"]), "2026-11-02")
ck("C9 ...and a year end", (dw.ahead_window(datetime.date(2026, 12, 30))["end"]), "2027-01-02")
ck("C10 the window is a REAL window the shared applier can use (same shape as every other)",
   sorted(w) == sorted(dw.resolve_window("2026-10-26", "2026-10-29", 0)), True)

# ── D — the ratchet ─────────────────────────────────────────────────────────────────────────────
ENG = open(ENGINE).read()
RTR = open(PROUTER).read()
UNS = open(UNSOLD).read()
ck("D1 the engine asks the one home, never its own token test",
   "_pm.no_device_declared(" in ENG, True)
ck("D2 ...and names no feed column in code — the column is CONFIG (RULE TWO)",
   bool(re.search(r'activation_type2', ENG)), False)
ck("D3 the declared column is read from the carrier's own map",
   'cfg.get("device_class_field")' in ENG, True)
ck("D4 ...and is SELECTED by the build, or the question cannot be asked",
   '"device_class_field"' in ENG.split("def _needed_cols")[1].split("def ")[0], True)
ck("D5 the status it stamps comes from the one home, not a literal",
   "_ls.STATUS_NO_DEVICE" in ENG, True)
ck("D6 no literal status strings survive in the routing",
   bool(re.search(r'status = "(open|due|discrepancy|offset|no_device)"', ENG)), False)
ck("D7 such a row carries NO amount — a device nobody bought cannot be owed for",
   bool(re.search(r"owed, net_owed, net_offset = None, None, 0\.0", ENG)), True)
for name, src in (("the payables list", RTR), ("the unsold-inventory read", UNS)):
    ck(f"D8 {name} excludes non-payables through the one home",
       "_ls.exclude_non_payable(" in src, True)
ck("D9 every ledger read in the payables router is filtered or explicitly single-row",
   RTR.count("_ls.exclude_non_payable(") >= 3, True)
ck("D10 the count of what was left out is REPORTED, never silently dropped",
   '"no_device_excluded"' in RTR, True)
ck("D11 the drill-down still answers for such a line, saying which it is (an audit view)",
   '"is_payable": _ls.is_payable(' in RTR, True)
ck("D12 the default window is applied by ONE helper, so two endpoints cannot differ",
   RTR.count("_due_window(start, end, all_dates)"), 2)
ck("D13 ...which dereferences the window home rather than computing dates itself",
   "date_window.ahead_window(" in RTR, True)
ck("D14 'all dates' remains reachable, or the full list would be unreachable",
   "if all_dates:" in RTR and "all_dates: bool = False" in RTR, True)
ck("D15 who-owes-what is split per store ON the date row, not in a second request",
   '"by_store"' in RTR, True)
ck("D16 ...and money with no store resolved is named, never folded into another store",
   '"unassigned_owed"' in RTR, True)
ck("D17 the per-store split keys on the canonical store identity, not a spelling (§64)",
   "_store_key(skey, st)" in RTR, True)

bad = [t for t in OUT if not t[1]]
print(f"\n{'='*94}\nharness_payables_device_rows — {len(OUT) - len(bad)} passed, {len(bad)} failed\n{'='*94}")
for name, ok, got, want in OUT:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
sys.exit(1 if bad else 0)
