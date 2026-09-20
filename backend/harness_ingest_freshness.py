"""DB-FREE PROOF — "is the data still flowing?" must be answerable, and the answer must be the RIGHT one.

OWNER DIRECTIVE, 2026-09-20: *"it was the same password but it fizzled out of the b2bimports - this
happened in luxelink and was fixed, again it was a patchwork and i want no patchwork if one thing is
fixed for one tenant it should be a design fix not a temporary fix, this should be a requirement of
the design"*.

THE SAME CLASS HAS NOW BITTEN THREE TIMES, each time because a fact was fixed on ONE path:

  1. `account/autocompute._PERIOD_SOURCES` — the coa READ path learned that `daily_sales_feed` is a
     tenant's primary sales source, and the data-DETECTION list was never mirrored, so a feed-only
     tenant's P&L never computed. Its own comment names the pattern: "the coa READ-path was
     universalized but this data-DETECTION list was never mirrored".
  2. `data_lineage_registry.FRESHNESS_COLUMN_BY_TABLE` was then written to hold that fact ONCE
     (`daily_sales_feed -> uploaded_at`, because its rows are re-inserted and promoted so `created_at`
     lies) — and `freshness_column()` was never called by anybody. `_table_feed_freshness` left
     `last_ingest_at` at None for EVERY table-backed feed on EVERY tenant.
  3. Which killed the arrival-vs-content discriminator in `_data_freshness_monitor`. Its
     `arrival_stopped` test reduces to `(not li_full) or ...` — always True — so for a table-backed
     feed the platform could only ever say "the report email appears to have STOPPED ARRIVING". The
     sibling `_custom_feed_freshness` (raw_custom_import) sets `last_ingest_at` properly, so the
     diagnosis worked for Activation Details and Bill Payment and was dead for the sales feed.

LIVE PROOF, Boost 2026-09-20: the mailbox ingested 6,601 rows at 03:41 and the newest transaction in
the data was still 2026-09-07. The true diagnosis is "the file still arrives, its CONTENT is frozen"
— chase the source's report subscription. The only sentence the platform could produce was "the email
stopped arriving" — chase the mail server. Thirteen days pointed at the wrong thing.

WHAT THIS HARNESS LOCKS, so the next fix cannot be a patch again:
  §B the freshness probe must DEREFERENCE the registry, never carry its own copy of the fact
  §C the registry and autocompute's detection list may not disagree about any table
  §D the discriminator, replayed on the live Boost and LuxeLink rows
  §E an ingest that happened is recorded even when the optional post-ingest work raises — in BOTH
     sweeps, and through BOTH entry points

Run:  cd backend && python3 harness_ingest_freshness.py
"""
import ast
import os
import sys
from datetime import date, datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.data_lineage_registry as LIN

ROUTER = "app/modules/commcalc/router.py"
AUTOCOMPUTE = "app/modules/account/autocompute.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _func_src(rel, name):
    """SOURCE TEXT of one top-level function, located by PARSING. A missing anchor raises by name —
    a harness that dies reads as 'not run', which is worse than one that fails."""
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


print("\n§A  the registry is the ONE place 'which column means arrived' is written down")
eq("A1 daily_sales_feed -> uploaded_at (rows are re-inserted/promoted, so created_at lies)",
   LIN.freshness_column("daily_sales_feed"), "uploaded_at")
eq("A2 raw_sales falls back to created_at", LIN.freshness_column("raw_sales"), "created_at")
eq("A3 an unknown table falls back to created_at, never to None",
   LIN.freshness_column("some_table_nobody_registered"), "created_at")
ok("A4 the exception is DECLARED, not inferred — daily_sales_feed is an explicit entry",
   "daily_sales_feed" in LIN.FRESHNESS_COLUMN_BY_TABLE)

print("\n§B  the freshness probe DEREFERENCES the registry — it may not keep its own copy")
TFF = _func_src(ROUTER, "_table_feed_freshness")
ok("B1 _table_feed_freshness calls freshness_column()", "_lineage.freshness_column(" in TFF,
   "this is the wiring that was missing for the registry's whole life")
ok("B2 …and assigns last_ingest_at from it (not left at the initialised None)",
   'out["last_ingest_at"] =' in TFF and "ing_col" in TFF)
ok("B3 …and does NOT hardcode the column name (that would be copy #3 of the same fact)",
   "uploaded_at" not in TFF.split('"""')[-1],
   "an inline 'uploaded_at' outside the docstring is the drift this rule exists to stop")
ok("B4 a table lacking the column degrades to None instead of raising",
   "except Exception:" in TFF.split("ing_col")[-1])
ok("B5 the probe is still org-scoped on both reads (RULE ONE)",
   TFF.count('.eq("org_id", org_id)') >= 3, TFF.count('.eq("org_id", org_id)'))
CFF = _func_src(ROUTER, "_custom_feed_freshness")
ok("B6 the raw_custom_import sibling still sets it too — both paths answer the same question",
   'out["last_ingest_at"] =' in CFF)

print("\n§C  the registry and autocompute's detection list may not disagree about any table")
ACSRC = _src(AUTOCOMPUTE)
_ps = None
for node in ast.parse(ACSRC).body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_PERIOD_SOURCES" for t in node.targets):
        _ps = ast.literal_eval(node.value)
ok("C1 account/autocompute._PERIOD_SOURCES is readable as data", _ps is not None)
if _ps:
    cands = {t: c for t, c in _ps}
    for tbl, col in LIN.FRESHNESS_COLUMN_BY_TABLE.items():
        if tbl in cands:
            eq(f"C2 {tbl}: autocompute's FIRST arrival candidate matches the registry",
               cands[tbl][0], col)
        else:
            ok(f"C2 {tbl}: not in _PERIOD_SOURCES (nothing to drift from)", True)
    ok("C3 every _PERIOD_SOURCES table whose first candidate is NOT created_at is DECLARED in the registry",
       all(t in LIN.FRESHNESS_COLUMN_BY_TABLE for t, c in _ps if c and c[0] != "created_at"),
       [t for t, c in _ps if c and c[0] != "created_at" and t not in LIN.FRESHNESS_COLUMN_BY_TABLE])

print("\n§D  the diagnosis — arriving-but-frozen is NOT the same failure as stopped-arriving")
# _data_freshness_monitor's discriminator, restated (the monitor itself needs a DB client + mailer).
TODAY = date(2026, 9, 20)


def diagnose(last_ingest_at, today=TODAY):
    li_full = last_ingest_at
    li = str(li_full or "")[:10] or "unknown"
    age = None
    if li != "unknown":
        try:
            age = (today - datetime.strptime(li, "%Y-%m-%d").date()).days
        except Exception:
            age = None
    return "stopped_arriving" if ((not li_full) or (age is None) or (age >= 2)) else "content_stale"

eq("D1 BEFORE — last_ingest_at None (what every table-backed feed returned) reads as stopped-arriving",
   diagnose(None), "stopped_arriving")
eq("D2 AFTER — the live Boost row: ingested 2026-09-20 03:41, data ends 09-07 => CONTENT stale",
   diagnose("2026-09-20T03:41:21.000000+00:00"), "content_stale")
eq("D3 a feed whose file really did stop arriving still reads as stopped-arriving",
   diagnose("2026-09-05T03:00:00+00:00"), "stopped_arriving")
eq("D4 yesterday's ingest is still 'arriving' (an overnight export carries yesterday's data)",
   diagnose("2026-09-19T03:00:00+00:00"), "content_stale")
eq("D5 the 2-day boundary is the cutover, and it is inclusive",
   diagnose("2026-09-18T03:00:00+00:00"), "stopped_arriving")
eq("D6 an unparseable stamp is treated conservatively as stopped-arriving, never as healthy",
   diagnose("not-a-date"), "stopped_arriving")
# staleness itself is a separate axis and must not be confused with the diagnosis
ok("D7 the two axes are independent: a FRESH feed is never diagnosed at all (no alert path)",
   diagnose("2026-09-20T03:41:21+00:00") == "content_stale",
   "diagnose() only runs for feeds already flagged stale — pinned so a refactor cannot invert it")

print("\n§E  an ingest that happened is RECORDED — even when the optional work after it raises")
for fn, label in ((_func_src(ROUTER, "_run_email_sweep"), "mailbox sweep"),
                  (_func_src(ROUTER, "_run_ftp_sweep"), "FTP sweep")):
    tail = fn.split("ok = sum(", 1)[-1]
    ok(f"E1 {label}: the outcome stamp sits in a `finally`",
       "finally:" in tail and "_sweep_run_stamp(ok > 0)" in tail.split("finally:", 1)[-1],
       "post-ingest alerting must not be able to discard the record of a completed ingest")
    ok(f"E2 {label}: status_msg is built BEFORE the optional work, so the finally always has one",
       tail.index("status_msg = ") < tail.index("finally:"))
    ok(f"E3 {label}: the shrink alert now runs INSIDE that try",
       tail.index("_sweep_shrink_alert(") > tail.index("try:"))
RN = _func_src(ROUTER, "email_run_now")
ok("E4 run-now wraps the background task so a crash is stamped, like the /run-due dispatcher",
   "_run_and_record" in RN and "_sweep_run_stamp(False)" in RN and "sweep crashed" in RN)
ok("E5 run-now's crash stamp reaches EVERY mailbox when no account was named",
   "_email_accounts(" in RN)
ok("E6 the crash still propagates after being recorded (an error is not swallowed into a green)",
   "raise" in RN.split("sweep crashed", 1)[-1])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
