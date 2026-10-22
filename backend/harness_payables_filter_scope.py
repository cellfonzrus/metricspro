"""Harness — Device Forecasting & Vendor Payables: the DATE WINDOW and the STORE SELECTION (module 095).

TWO owner reports, 2026-10-22, and they are the same class twice.

  1. *"Device Forecasting & Vendor Payables should have all the regular filters with date range store
     etc"* — the screen carried market + store and no date range at all. The forecast stated its
     window as a look-back NUMBER, `/owed-by-date` as its own inline `gte`/`lte` pair, and the
     Distributor Invoices screen (§15v) as `vip_invoice_filter.in_date_window` — three answers to
     "which days am I measuring", which is a window waiting to diverge.

  2. *"the filter for market brings up the forecasting but the filter for store does not bring up
     anything"* — and the asymmetry IS the diagnosis. The market half is RESOLVED: every row is
     stamped `market = resolver(row.store)`, and that resolver accepts any spelling, so a market pick
     matches whatever the feed calls the store. The store half was compared as a STRING, the picker's
     label against `row.store`. MEASURED live that day against the real resolver:

         asset_ledger          13 of 33 spellings matched NO option -> 10,801 rows unreachable
         device_payable_ledger 15 of 37 spellings matched NO option ->  4,044 rows unreachable
         raw_sales              6 of 28 spellings matched NO option -> 10,024 rows unreachable

     and after stamping both sides with the canonical key: 0, 0, 0.

     §13e (one option per physical store) made that the NORMAL case rather than the exception: for a
     store spelled two ways the surviving option is, by construction, not the spelling the other
     half of the rows carry.

THE FIX IS THE CLASS IN BOTH CASES. One home for the window (`commcalc/date_window.py`), read by
this module AND by the Distributor Invoices selector that already had a copy; and a store SELECTION
that binds through the canonical store identity (`account.store_identity` over `coa.store_resolver`,
§64) exactly as the market selection already does. This harness holds both, and §D/§E FAIL THE BUILD
if either un-wires.

PURE / DB-FREE: the window module does no I/O, and the wiring checks read source.
"""
import ast
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


from app.modules.account import store_identity as sid                         # noqa: E402
from app.modules.commcalc import date_window as dw                            # noqa: E402
from app.modules.commcalc import vip_invoice_filter as vf                      # noqa: E402

TODAY = date(2026, 10, 22)

# ── §A  the window predicate — inclusive, day-compared, fail-closed ─────────────────────────────
print("\n§A  in_window — what 'inside the range' means, said once")
ok(dw.in_window("2026-06-15", "", ""), "A1 no window set -> everything is in scope")
ok(dw.in_window("2026-06-15", "2026-06-15", "2026-06-15"), "A2 a one-day window selects that day (both ends inclusive)")
ok(dw.in_window("2026-06-15", "2026-06-01", "2026-06-30"), "A3 inside a month window")
ok(not dw.in_window("2026-06-15", "2026-06-16", "2026-06-30"), "A4 before the start is out")
ok(not dw.in_window("2026-06-15", "2026-06-01", "2026-06-14"), "A5 after the end is out")
ok(dw.in_window("2026-06-15", "2026-06-15", ""), "A6 an open end is open")
ok(dw.in_window("2026-06-15", "", "2026-06-15"), "A7 an open start is open")
ok(dw.in_window("2026-06-15T08:49:34+00:00", "2026-06-01", "2026-06-15"),
   "A8 A TIMESTAMP ON THE END DAY IS IN — the comparison is on DAYS, so a row booked at 08:49 on the "
   "'to' day is not silently dropped")
ok(not dw.in_window(None, "2026-06-01", "2026-06-30"),
   "A9 FAIL-CLOSED: a row with no date is excluded once a window is set (never guessed into it)")
ok(dw.in_window(None, "", ""), "A10 ...and in scope when no window is set")
ok(not dw.in_window("", "2026-06-01", ""), "A11 a blank date is a dateless row, not day zero")
ok(dw.row_in_window({"due_date": "2026-06-15"}, "2026-06-01", "2026-06-30", "due_date"),
   "A12 row_in_window reads the row's own date FIELD")
ok(not dw.row_in_window({"other": "2026-06-15"}, "2026-06-01", "2026-06-30", "due_date"),
   "A13 ...and a column list that forgot to fetch the date field EMPTIES the window rather than "
   "quietly ignoring it (the §15v lesson, kept)")

# ── §B  resolve_window — one window, two front ends ────────────────────────────────────────────
print("\n§B  resolve_window — a look-back and a date range are the SAME window")
w = dw.resolve_window(lookback_days=7, today=TODAY)
ok(w["basis"] == dw.BASIS_LOOKBACK and w["start"] == "2026-10-16" and w["end"] == "2026-10-22",
   "B1 a 7-day look-back is SEVEN days INCLUDING today (2026-10-16..22)")
ok(w["days"] == 7, "B2 ...and its day count is 7, which is what a per-day velocity divides by")
ok(dw.resolve_window(lookback_days=1, today=TODAY)["start"] == "2026-10-22",
   "B3 a 1-day look-back is today, not yesterday-and-today")
# THE OFF-BY-ONE THIS REPLACES: `today - timedelta(days=lookback)` spans lookback+1 days and then
# divides the units by lookback, so every velocity read ~14% high at a 7-day window.
ok((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1 == w["days"],
   "B4 the span the query scans and the divisor are THE SAME NUMBER (the old cutoff scanned 8 days "
   "and divided by 7)")
w = dw.resolve_window("2026-10-01", "2026-10-31", 7, TODAY)
ok(w["basis"] == dw.BASIS_RANGE and w["days"] == 31 and w["days_measured"],
   "B5 an explicit range measures its OWN inclusive day count (31), not the look-back box")
w = dw.resolve_window("2026-10-01", "", 7, TODAY)
ok(w["end"] == "2026-10-22" and w["days"] == 22,
   "B6 'from the 1st' is closed at today, so its day count is measured rather than guessed")
w = dw.resolve_window("", "2026-10-09", 7, TODAY)
ok(w["start"] == "" and not w["days_measured"],
   "B7 an open START cannot supply a day count — it falls back to the look-back and SAYS SO "
   "(days_measured False), instead of presenting a rate the window does not support")
ok(dw.resolve_window(lookback_days=99999, today=TODAY)["days"] == dw.MAX_DAYS,
   "B8 a look-back is clamped: a caller cannot ask for a 20-year scan")
ok(dw.resolve_window(lookback_days=0, today=TODAY)["days"] == 7
   and dw.resolve_window(lookback_days="x", today=TODAY)["days"] == 7,
   "B9 an unreadable or zero look-back falls back to the default, never to a zero divisor")
ok(dw.resolve_window("2026-10-31", "2026-10-01", 7, TODAY)["days"] == 7,
   "B10 a backwards range yields no negative day count")
ok(dw.resolve_window("not-a-date", "", 7, TODAY)["basis"] == dw.BASIS_LOOKBACK,
   "B11 an unparseable bound is no bound at all — the window falls back to the look-back")


class _Q:
    """Records the bounds apply_window adds, so the SERVER half is provable without a database."""

    def __init__(self):
        self.calls = []

    def gte(self, c, v):
        self.calls.append(("gte", c, v)); return self

    def lte(self, c, v):
        self.calls.append(("lte", c, v)); return self


q = dw.apply_window(_Q(), "due_date", dw.resolve_window("2026-10-01", "2026-10-31", 7, TODAY))
ok(q.calls == [("gte", "due_date", "2026-10-01"), ("lte", "due_date", "2026-10-31")],
   "B12 apply_window is the SERVER twin of in_window — gte/lte on the same inclusive bounds")
ok(dw.apply_window(_Q(), "due_date", {"start": "", "end": ""}).calls == [],
   "B13 ...and an open window adds no bound at all (never a 1970 floor)")
ok(dw.undated([{"due_date": "2026-10-01"}, {"due_date": None}, {}], "due_date") == 2,
   "B14 undated() counts the rows a window cannot judge, so a surface can REPORT them instead of "
   "letting their absence read as 'nothing is owed'")

# ── §C  store identity — the store half, resolved like the market half ──────────────────────────
print("\n§C  a store SELECTION binds through identity, not spelling")
MAPPING = [{"store_code": "B-1", "store_address": "5619 N Broad Street"},
           {"store_code": "B-2", "store_address": "2778 Mount Ephraim Ave"}]
resolve = sid.build_store_resolver(MAPPING, [])
# The shapes measured live on 2026-10-22: the feeds' spelling vs the roster's.
FEED = ["5619 N Broad St", "5619 N Broad Street", "2778 Mt Ephraim Ave", "2778 Ephraim Ave"]
OPTION_LABELS = [m["store_address"] for m in MAPPING]
opt_keys = {sid.store_key(resolve, o) for o in OPTION_LABELS}
ok(sum(1 for f in FEED if f in OPTION_LABELS) == 1,
   "C1 THE REGRESSION: a raw-string comparison binds only 1 of 4 live feed spellings — which is why "
   "picking a store showed nothing")
ok(all(sid.store_key(resolve, f) in opt_keys for f in FEED),
   "C2 ...and ALL 4 bind once both sides carry the canonical key")
ok(sid.store_key(resolve, "5619 N Broad St") == sid.store_key(resolve, "5619 N Broad Street"),
   "C3 two spellings of one physical store share ONE key (so the grid shows one row, not two halves)")
ok(sid.store_key(resolve, "2778 Mount Ephraim Ave") != sid.store_key(resolve, "5619 N Broad Street"),
   "C4 ...and two DIFFERENT stores never collapse into one")
ok(sid.store_key(resolve, "Nowhere Rd") == "Nowhere Rd",
   "C5 an unresolvable store keys on its own spelling — it groups with itself and stays SELECTABLE, "
   "never dropped")
ok(sid.store_key(resolve, "") == "" and sid.store_key(None, "X St") == "X St",
   "C6 a blank store is no key, and a missing resolver degrades to the raw spelling")

# ── §D  WIRING — the payables module dereferences both one homes ────────────────────────────────
print("\n§D  the module READS the one homes (it cannot un-wire silently)")
ROUTER = open("app/modules/payables/router.py").read()


def code_only(src):
    """Source stripped of comments and docstrings — the ban is on CODE, not on the prose that
    explains the design (the §15v convention, reused)."""
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docs.add(d)
    body = "\n".join(ln.split("  #")[0] for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for d in docs:
        body = body.replace(d, "")
    return body


RCODE = code_only(ROUTER)
ok("from app.modules.commcalc import date_window" in RCODE,
   "D1 the router imports the one window home")
ok("date_window.resolve_window(" in RCODE, "D2 ...and resolves its window through it")
ok("date_window.apply_window(" in RCODE, "D3 ...and applies the bounds through it")
ok("date_window.undated(" in RCODE, "D4 ...and REPORTS the rows the window cannot judge")
for banned in ('gte("due_date"', 'lte("due_date"', "timedelta(days=lookback)"):
    ok(banned not in RCODE,
       f"D5 no second copy of the window rule survives in the router ({banned})")
ok("_store_key_resolver" in RCODE and "store_resolver(client, org_id)" in RCODE,
   "D6 the store key comes from coa.store_resolver — THE one home for 'which store is this string'")
ok('"store_key"' in RCODE,
   "D7 ...and rows + filter options are both STAMPED with it, which is what the filter compares on")
for banned in ('q.eq("store", store)', "st != store"):
    ok(banned not in RCODE,
       f"D8 no string-equality store comparison survives in the router ({banned}) — that comparison "
       f"WAS the defect")
ok("squash_key" not in RCODE and "lead_num" not in RCODE,
   "D9 the router states no spelling rule of its own, so a new store_aliases row teaches this filter "
   "and the P&L in the same instant")

# ── §E  WIRING — the Distributor Invoices selector reads the SAME home ──────────────────────────
print("\n§E  §15v's own copy is gone — one window home, two readers")
VF = code_only(open("app/modules/commcalc/vip_invoice_filter.py").read())
ok("date_window" in VF, "E1 vip_invoice_filter imports the one window home")
ok("date_window.row_in_window" in VF, "E2 ...and in_date_window DEREFERENCES it")
ok("s[:10] if len(s) >= 10" not in VF,
   "E3 ...and no copy of the day-truncation rule survives there")
for f, t, exp in [("", "", True), ("2026-06-01", "2026-06-30", True), ("2026-07-01", "", False)]:
    ok(vf.in_date_window({"created_on": "2026-06-15T08:49:34+00:00"}, f, t) is exp,
       f"E4 ...and it still answers identically for the §15v cases ({f or '-'}..{t or '-'})")
ok(vf.in_date_window({"created_on": None}, "2026-06-01", "2026-06-30") is False,
   "E5 ...including fail-closed on a dateless invoice")

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
sys.exit(1 if FAIL else 0)
