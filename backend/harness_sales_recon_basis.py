"""Offline proof (no DB/network) for A RECONCILIATION'S PRECONDITION — owner report 2026-10-06.

THE REPORT. The owner was shown a `sales_leak` flag reading "Trans 211866 is in the daily feed
($35.33, 2026-09-18) but NOT in the authoritative monthly sales file — revenue/commission leak or an
unrecorded void", and answered:

    "b2b is updated daily, that is how our sales mtd is reporting the daily numbers"

He is right, and it makes the comparison WRONG rather than merely noisy. Measured live the same day on
the house org, `commcalc.flags` where `flag_type='sales_leak'`:

    October 2026   3,001 flags, severity critical   ← the month IN PROGRESS
    September 2026 11,233 flags, severity critical   ← closed, archive never built
    August 2026     8,001 flags                      ← archive loaded; a real disagreement, left alone
    July 2026           0
    June 2026           0

and `commcalc.raw_sales` line counts: Jul 39,731 · Aug 29,181 · **Sep 0** · **Oct 0**, while
`daily_sales_feed` is current (newest trans_date 2026-10-06, 32,428 September lines). So the daily feed
never stopped; only the month-end archive did. October's 3,001 findings are against an archive that
CANNOT exist — the month has not ended. September's 11,233 are one missing file reported once per
transaction, which is what buried it.

THE CLASS, not the instance (house rule: *a fix is a DESIGN fix or it is not a fix*). The general fact
that was wrong is not "September's basis is missing". It is **"the month-end archive was treated as the
authority for every month"** — it is the authority for a CLOSED month and does not exist for an open
one. So: **a reconciliation may report a difference only when BOTH sides have actually arrived**, and an
absent side is ONE condition at period grain, never N findings about N transactions.

THE SIBLINGS, which is where this class usually survives a fix. "Is this month still open" had THREE
answers and no home (§19.18's shape, now for the fifth time):

  • `router._is_open_month`     — `parse_period` + `date.today()`; silently read '2026-07' as January
  • `router.sales_derive_gap`   — `_canon_period(p) != _canon_period(_ftp_current_period())`
  • `commcalc/sales_recon.py`   — never asked, which IS the defect

It now has one: `feed_period.month_state` / `archive_due`. Both readers inside sales_recon — the full
`run_sales_recon` report and the cheap `derive_gap` count the derive console and the login attention
provider read — dereference the same `comparability()` verdict, so neither can drift.

WHAT THIS HARNESS DOES NOT CLAIM. It does not say September's archive is now present (it is not — that
is a data step needing the owner's word), and it does not touch August's 8,001, which are a genuine
feed-vs-archive disagreement on a month whose archive IS loaded and remain fully reportable. §D is the
lock: it FAILS THE BUILD if a private copy of the open-month rule comes back, or if a caller stops
dereferencing the one home.

Run: `cd backend && python3 harness_sales_recon_basis.py`
"""
import datetime as dt
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── ONE import stub, at the real seam. `sales_recon` imports `get_supabase` at module scope, so the
# module cannot be loaded without a database layer. Stubbing `app.core.database` — rather than chasing
# the client library's internals — is what keeps this proof DB-free: `get_supabase` is replaced by a
# callable that RAISES, so any function below that reaches for a client fails the harness loudly
# instead of silently opening a connection. Every §A/§B assertion stands on pure functions only.
_db = types.ModuleType("app.core.database")


def _no_client(*a, **k):
    raise AssertionError("harness_sales_recon_basis must stay DB-free: a tested path asked for a "
                         "live client")


_db.get_supabase = _no_client
_db.get_supabase_admin = _no_client
_db.supabase = None
sys.modules.setdefault("app.core.database", _db)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# The module is pure stdlib, so it imports with no app context, no client and no FastAPI.
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.modules.commcalc import feed_period as FP          # noqa: E402

TODAY = dt.date(2026, 10, 6)        # the day of the owner's report — every §A/§B case stands on it


# ══ A. THE ONE HOME — is this month still open ═══════════════════════════════════════════════════
check("A1 the month in progress is OPEN", FP.month_state("October 2026", TODAY) == FP.OPEN)
check("A2 a month that has ended is CLOSED", FP.month_state("September 2026", TODAY) == FP.CLOSED)
check("A3 a month long past is CLOSED", FP.month_state("March 2026", TODAY) == FP.CLOSED)
check("A4 a FUTURE month is OPEN, not closed — nothing about it has finished, so no archive is owed",
      FP.month_state("December 2026", TODAY) == FP.OPEN)
check("A5 a future YEAR is OPEN too (a (year, month) tuple compare, not a month-number compare)",
      FP.month_state("January 2027", TODAY) == FP.OPEN)
check("A6 a past year's same month is CLOSED (the bug a bare `mo == t.month` compare would hide)",
      FP.month_state("October 2025", TODAY) == FP.CLOSED)

# The spelling trap this module exists for: router._is_open_month's old body ran parse_period, which
# understood the month-name form ONLY and silently mapped '2026-07' to January.
check("A7 the ISO spelling resolves to the same answer as the month-name one",
      FP.month_state("2026-10", TODAY) == FP.OPEN
      and FP.month_state("2026-09", TODAY) == FP.CLOSED)
check("A8 ... and '2026-07' is NOT read as January (the source-selection bug, pinned)",
      FP.period_month_year("2026-07") == (7, 2026))
check("A9 an abbreviated month is accepted ('Sept 2026', 'oct 2026', 'Aug. 2026')",
      FP.period_month_year("Sept 2026") == (9, 2026)
      and FP.period_month_year("oct 2026") == (10, 2026)
      and FP.period_month_year("Aug. 2026") == (8, 2026))
check("A10 a full day resolves to its month", FP.period_month_year("2026-09-18") == (9, 2026))
for junk in ("", None, "not a period", "Smarch 2026", "2026-13", "2026"):
    check(f"A11 {junk!r} is UNKNOWN, and UNKNOWN is never asserted to be either state",
          FP.month_state(junk, TODAY) == FP.UNKNOWN and FP.archive_due(junk, TODAY) is False)

check("A12 archive_due is exactly 'the month has closed'",
      FP.archive_due("September 2026", TODAY) is True
      and FP.archive_due("October 2026", TODAY) is False)
check("A13 the fact is injectable, so it is testable and has no hidden clock",
      FP.month_state("October 2026", dt.date(2026, 11, 1)) == FP.CLOSED)


# ══ B. THE PRECONDITION — may these two sides be compared ════════════════════════════════════════
from app.modules.commcalc import sales_recon as SR          # noqa: E402

# The live numbers from the owner's report, so the regression is the defect itself and not a sketch.
OCT = SR.comparability("October 2026",   monthly_lines=0,     feed_lines=4_000,  today=TODAY)
SEP = SR.comparability("September 2026", monthly_lines=0,     feed_lines=32_428, today=TODAY)
AUG = SR.comparability("August 2026",    monthly_lines=29_181, feed_lines=30_000, today=TODAY)

check("B1 REGRESSION (October, 3,001 false criticals): the live month is NOT comparable, because its "
      "month-end archive is not due yet",
      OCT["verdict"] == SR.ARCHIVE_NOT_DUE and OCT["reportable"] is False, str(OCT))
check("B2 REGRESSION (September, 11,233 criticals for one missing file): a CLOSED month with an empty "
      "archive is not comparable either, and says so as its own condition",
      SEP["verdict"] == SR.ARCHIVE_NOT_LOADED and SEP["reportable"] is False, str(SEP))
check("B3 August is UNCHANGED — its archive is loaded, so its 8,001 findings stay fully reportable. "
      "This fix silences a comparison that could not be made, never a disagreement that can.",
      AUG["verdict"] == SR.COMPARABLE and AUG["reportable"] is True, str(AUG))

# Precedence is load-bearing: an open month whose archive is empty is the NORMAL state and must be
# reported as not-due, never as not-loaded — otherwise the fix just renames October's 3,001.
check("B4 PRECEDENCE: not-due outranks not-loaded, so an open month's empty archive is never a "
      "missing-file finding",
      SR.comparability("October 2026", 0, 500, today=TODAY)["verdict"] == SR.ARCHIVE_NOT_DUE)
check("B5 PRECEDENCE: no feed outranks everything — this module has nothing to say without a feed, "
      "and its silence is not a verdict about the archive",
      SR.comparability("September 2026", 0, 0, today=TODAY)["verdict"] == SR.NOTHING_TO_COMPARE
      and SR.comparability("September 2026", 5, 0, today=TODAY)["verdict"] == SR.NO_FEED)
check("B6 an UNKNOWN period is not comparable — an unparseable month may not be asserted closed",
      SR.comparability("not a period", 0, 100, today=TODAY)["reportable"] is False)
check("B7 only COMPARABLE lets an ABSENCE be reported, and the set is NAMED rather than spelled at "
      "each caller",
      SR.REPORTABLE_VERDICTS == (SR.COMPARABLE,))

# The fix must not overreach. An `amount_mismatch` is the same trans_id in BOTH tables at different
# money, which PROVES both sides arrived for that transaction — a real disagreement, not the lag the
# hourly promote step produces. Muting it along with the absences would have traded one defect for
# another, quieter one.
check("B7a an open month still reports an amount DISAGREEMENT, because both sides demonstrably hold "
      "that transaction",
      "amount_mismatch" in OCT["reportable_buckets"], str(OCT["reportable_buckets"]))
check("B7b ... while its ABSENCE buckets are withheld, which is the whole of the fix",
      "missing_in_monthly" not in OCT["reportable_buckets"]
      and "missing_in_daily" not in OCT["reportable_buckets"])
check("B7c a closed month with NO archive withholds every bucket — there is nothing to disagree with",
      SEP["reportable_buckets"] == ())
check("B7d a month with both sides present reports all three",
      set(AUG["reportable_buckets"]) == {"missing_in_monthly", "missing_in_daily", "amount_mismatch"})
check("B7e the bucket map covers every verdict the module can return, so a new verdict cannot "
      "silently report nothing",
      set(SR.REPORTABLE_BUCKETS) == {SR.COMPARABLE, SR.ARCHIVE_NOT_DUE, SR.ARCHIVE_NOT_LOADED,
                                     SR.NO_FEED, SR.NOTHING_TO_COMPARE})

check("B8 every verdict reports the one home's answers verbatim",
      OCT["archive_due"] is False and SEP["archive_due"] is True
      and OCT["month_state"] == FP.OPEN and SEP["month_state"] == FP.CLOSED)

# Every non-comparable verdict must be able to TELL A HUMAN WHY — a silent skip is how a missing
# September survives unnoticed for a month, which is the defect's other half.
for v, nm in ((OCT, "October"), (SEP, "September")):
    txt = SR.verdict_reason(v, "B2BSoft")
    check(f"B9 the {nm} verdict carries an actionable sentence", len(txt) > 60, txt)
    check(f"B10 ... with the tenant's POS term substituted, not a vendor name hardcoded (RULE TWO)",
          "{pos}" not in txt and "{pos}" in (v["reason"] or ""), txt)
check("B11 a COMPARABLE verdict carries no excuse sentence", SR.verdict_reason(AUG, "x") == "")


# ══ C. ONE CONDITION, PERIOD GRAIN — not one finding per transaction ═════════════════════════════
recon = read("backend/app/modules/commcalc/sales_recon.py")
reg = read("backend/app/modules/commcalc/flag_registry.py")

check("C1 the missing archive has its own flag type, so it is REPORTED rather than hidden",
      '"sales_basis_not_loaded"' in reg and '"sales_basis_not_loaded"' in recon)
spec = reg.split('"sales_basis_not_loaded": {')[1][:260]
check("C2 ... registered at PERIOD grain, which is what makes it one row per month",
      '"grain": "period"' in spec, spec[:160])
check("C3 ... and owned by this module", 'commcalc/sales_recon.py' in spec, spec[:160])

sync = recon.split("def sync_recon_flags(")[1].split("\ndef ")[0]
check("C4 sync_recon_flags reads the verdict's bucket set BEFORE the per-transaction loop",
      sync.index("_buckets = _v.get(") < sync.index('for r in res["rows"]'))
check("C5 ... and the loop SKIPS a bucket the verdict withheld, which is the single line that stops "
      "11,233 criticals being written for one missing file",
      "if b not in _buckets:" in sync and "continue" in sync)
check("C6 the missing-archive row is built ONCE, outside the per-transaction loop",
      sync.split('for r in res["rows"]')[0].count('"sales_basis_not_loaded"') == 1
      and sync.split('for r in res["rows"]')[1].count('"sales_basis_not_loaded"') == 0)
check("C7 the one row is keyed on the PERIOD, so re-running replaces it instead of accumulating",
      '"source_ref": plabel' in sync)
check("C8 there is ONE persist call for every path, so a withheld run retires a cleared condition "
      "exactly as a reporting run does",
      sync.count("_persist(client, org_id, flags") == 1 and "def _persist(" in recon)
check("C9 the description says it is ONE missing file, not a leak per transaction, so the next "
      "reader does not re-derive what it means",
      "not a leak per transaction" in sync)
check("C10 the returned counts are what was WRITTEN, not what was counted — a withheld run cannot "
      "report the number the verdict just explained",
      '"missing_in_monthly": _leaks' in sync
      and '_leaks = sum(1 for f in flags if f.get("flag_type") == "sales_leak")' in sync)


# ══ D. THE LOCK — the private copies cannot come back ════════════════════════════════════════════
router = read("backend/app/modules/commcalc/router.py")

iom = router.split("def _is_open_month(period):")[1].split("\ndef ")[0]
check("D1 router._is_open_month DEREFERENCES the one home",
      "_feed_period.month_state(" in iom, iom[-200:])
check("D2 ... and keeps NO derivation of its own — no clock, no parse, no month compare",
      not re.search(r"_date\.today\(|datetime\.now\(|parse_period\(|\.month\b", iom), iom)

gap_ep = router.split("gap = sales_recon.derive_gap(")[1][:700]
check("D3 the derive-gap endpoint no longer string-compares against _ftp_current_period — the THIRD "
      "copy is gone and it reads the gap's own verdict",
      "_canon_period(_ftp_current_period())" not in gap_ep
      and 'gap.get("archive_due")' in gap_ep, gap_ep[:240])

check("D4 sales_recon imports the one home rather than importing the router (which would be a cycle) "
      "or re-deriving the rule",
      "from app.modules.commcalc import feed_period as _fp" in recon
      and "_fp.archive_due(" in recon and "_fp.month_state(" in recon)
comp = recon.split("def comparability(")[1].split("\ndef ")[0]
check("D5 comparability itself holds no copy of the open-month rule",
      not re.search(r"today\(\)|datetime|month_name|\.month\b", comp), comp[:200])

# The sibling: the cheap counting path must read the SAME verdict, or the console and the report
# disagree about the same month — the duplicate defect wearing a hat.
dg = recon.split("def derive_gap(")[1].split("\ndef ")[0]
check("D6 derive_gap, the cheap sibling the derive console reads, dereferences the SAME verdict",
      "comparability(plabel" in dg and '"comparable"' in dg and '"archive_due"' in dg, dg[-300:])

check("D7 the verdict vocabulary is spelled ONCE as named constants, never as bare strings at a "
      "caller",
      all(f'{n} = "' in recon for n in ("COMPARABLE", "ARCHIVE_NOT_DUE", "ARCHIVE_NOT_LOADED",
                                        "NO_FEED", "NOTHING_TO_COMPARE")))

# RULE TWO — no carrier/tenant/product name anywhere in the fix.
for name in ("Boost", "boost", "Total Wireless", "b2bsoft", "B2BSoft", "Luxelink", "luxelink",
             "Cellfonz", "Verizon"):
    check(f"D8 RULE TWO: {name!r} appears in neither the verdict nor the one home",
          name not in comp and name not in FP.__doc__.split("IS THIS MONTH")[-1])

# CONTROLS — a harness that cannot fail is not a proof.
check("control: a month-state copy in _is_open_month WOULD fail D2",
      bool(re.search(r"_date\.today\(|\.month\b", "t = _date.today()\nreturn mo == t.month")))
# This control originally asserted the opposite and FAILED, which is how the design was corrected:
# an open month's archive is filled incrementally by the hourly promote step, so the feed is ALWAYS
# ahead of it and an absence there is lag whether the archive is empty or half-built. The open month is
# therefore never an absence comparison — and the disagreement bucket (B7a) is what keeps that from
# being a blanket mute.
_open_loaded = SR.comparability("October 2026", 1_000, 1_000, today=TODAY)
check("control: an open month with a HALF-BUILT archive is still not an absence comparison — the "
      "promote step's lag is not a leak",
      _open_loaded["verdict"] == SR.ARCHIVE_NOT_DUE
      and "missing_in_monthly" not in _open_loaded["reportable_buckets"], str(_open_loaded))
check("control: ... and that same month still reports a real amount disagreement",
      "amount_mismatch" in _open_loaded["reportable_buckets"])
check("control: once October closes, its own empty archive DOES become a finding",
      SR.comparability("October 2026", 0, 4_000,
                       today=dt.date(2026, 11, 2))["verdict"] == SR.ARCHIVE_NOT_LOADED)


# ══ REPORT ═══════════════════════════════════════════════════════════════════════════════════════
for p in PASS:
    print(f"  PASS  {p}")
for f in FAIL:
    print(f"  FAIL  {f}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
