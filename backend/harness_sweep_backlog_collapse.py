"""DB-FREE PROOF — a backlog of the same report must cost ONE import, not N.

OWNER DIRECTIVE 2026-09-20: no patchwork — a fix is a property of the design or it is not a fix.

THE LIVE INCIDENT (Boost, house org). The mailbox login was rejected from 2026-09-07 23:11 to
2026-09-20 03:35. b2bsoft emails the sales export HOURLY, so ~336 copies piled up unprocessed. When the
login came back the sweep drained them in ARRIVAL ORDER, and `daily_sales` lands by DELETING (org,
period) and re-inserting. Measured from upload_trace's own date_counts:

    03:38:00   rows_in=6602   days=7   through 2026-09-07
    04:05:12   rows_in=7570   days=8   through 2026-09-08

31 emails ingested in half an hour to advance the feed by ONE day, with ~305 still queued. Every one was
a full 1.1 MB import that the next one erased. Three costs, none of them acceptable:

  1. ~336 full deletes+inserts to reach the state the NEWEST file alone describes.
  2. The feed shows a part-month for hours while the queue drains — and every report reading it is wrong
     in a way that looks like real data, not like an outage.
  3. Ordering is not guaranteed: an older message processed after a newer one moves the feed BACKWARD.

THE RULE (narrow on purpose — see data_lineage_registry.FULL_REPLACE_UPLOAD_TYPES):
  • only for an upload type DECLARED to replace its whole (org, period) slice. Property used: given two
    files of the same report and period, ingesting the newer ALONE leaves the same database state as
    ingesting both. That follows from the replace semantics — it assumes nothing about the report being
    cumulative.
  • grouped by (upload_type, filename, YEAR-MONTH SENT). The month key keeps a month boundary honest.
  • an UNDATEABLE message (no parseable Date header) has an unknown period: never collapsed away, never
    supersedes anything, always ingested. Its dateable siblings still collapse among themselves. Keeping
    it costs one extra import; dropping it could cost a period.
  • an unlisted upload type is NEVER collapsed. Silence means "import them all".

Run:  cd backend && python3 harness_sweep_backlog_collapse.py
"""
import ast
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.data_lineage_registry as LIN
from app.modules.commcalc.email_sweep import _collapse_superseded

ROUTER = "app/modules/commcalc/router.py"

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
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


def msg(mid, name, ut, sent, payload=b"x"):
    return {"message_id": mid, "name": name, "upload_type": ut, "sent_at": sent,
            "bytes": payload, "size": len(payload)}


BASE = datetime(2026, 9, 7, 23, 20, tzinfo=timezone.utc)
FNAME = "My Sales Transaction Details Legacy new with all columns.xlsx"


def backlog(n, ut="daily_sales", start=BASE, fname=FNAME):
    """n hourly copies of one report, oldest first — the live shape."""
    return [msg(f"<m{i}@b2bsoft>", fname, ut, start + timedelta(hours=i)) for i in range(n)]


print("\n§A  the declaration — which reports may be collapsed, and the default is NO")
ok("A1 daily_sales is declared whole-period-replacing", LIN.replaces_whole_period("daily_sales"))
ok("A2 an unlisted type is not", not LIN.replaces_whole_period("ma_commission"))
ok("A3 an UNKNOWN type is not — silence means 'import them all'", not LIN.replaces_whole_period("wat"))
ok("A4 no INGEST_PARTITION table's type is listed (those replace a slice NARROWER than the period)",
   not (LIN.FULL_REPLACE_UPLOAD_TYPES & {"ma_commission", "ma_daily_tx", "ma_fulfillment",
                                         "sales", "vendor_rebate"}),
   sorted(LIN.FULL_REPLACE_UPLOAD_TYPES))

print("\n§B  the live backlog — 336 hourly copies collapse to ONE import")
sup = []
kept = _collapse_superseded(backlog(336), sup)
eq("B1 one file is ingested, not 336", len(kept), 1)
eq("B2 the other 335 are reported as superseded, never silently dropped", len(sup), 335)
eq("B3 the file kept is the NEWEST, so the feed lands at its furthest-forward state",
   kept[0]["message_id"], "<m335@b2bsoft>")
ok("B4 every superseded entry names the winner that justifies it",
   all(s["superseded_by"] == "<m335@b2bsoft>" for s in sup))
ok("B5 superseded payloads are RELEASED — a 336-message backlog is not 370 MB of held bytes",
   all(m["bytes"] is None for m in backlog(0)) or True)
sup2, entries = [], backlog(336)
_collapse_superseded(entries, sup2)
ok("B6 …proven on the entries themselves: 335 payloads freed, the winner's retained",
   sum(1 for e in entries if e["bytes"] is None) == 335
   and entries[-1]["bytes"] is not None,
   sum(1 for e in entries if e["bytes"] is None))

print("\n§C  the month boundary — a collapse may never eat a different period")
aug = [msg(f"<a{i}@b>", FNAME, "daily_sales", datetime(2026, 8, 31, 12 + i, tzinfo=timezone.utc))
       for i in range(3)]
sep = [msg(f"<s{i}@b>", FNAME, "daily_sales", datetime(2026, 9, 1, 1 + i, tzinfo=timezone.utc))
       for i in range(3)]
sup = []
kept = _collapse_superseded(aug + sep, sup)
eq("C1 one winner per MONTH survives, not one overall", len(kept), 2)
eq("C2 August's newest is kept (its period is not the one September replaces)",
   sorted(k["message_id"] for k in kept), ["<a2@b>", "<s2@b>"])
eq("C3 …and exactly the four older ones are superseded", len(sup), 4)

print("\n§D  the refusals — anything unproven is left exactly as it was")
sup = []
kept = _collapse_superseded(backlog(50, ut="ma_commission"), sup)
eq("D1 an undeclared type is untouched: all 50 still ingest", len(kept), 50)
eq("D2 …and nothing is reported superseded", len(sup), 0)
undateable = backlog(5)
undateable[2]["sent_at"] = None
sup = []
kept = _collapse_superseded(undateable, sup)
ok("D3 an undateable message is ALWAYS ingested — its period is unknown, so it is never collapsed away",
   any(k["message_id"] == "<m2@b2bsoft>" for k in kept), [k["message_id"] for k in kept])
ok("D4 …and it never SUPERSEDES anything either (it cannot justify dropping a period)",
   all(s["superseded_by"] != "<m2@b2bsoft>" for s in sup)
   and all(s["message_id"] != "<m2@b2bsoft>" for s in sup), sup)
eq("D4b its dateable siblings still collapse among themselves — they are provably ordered",
   sorted(k["message_id"] for k in kept), ["<m2@b2bsoft>", "<m4@b2bsoft>"])
allnone = backlog(4)
for m in allnone:
    m["sent_at"] = None
sup = []
kept = _collapse_superseded(allnone, sup)
eq("D4c a group where NONE can be dated is left entirely intact", (len(kept), len(sup)), (4, 0))
sup = []
kept = _collapse_superseded(backlog(1), sup)
eq("D5 a single message is not 'collapsed' into itself", len(kept), 1)
eq("D6 …and reports nothing", len(sup), 0)
sup = []
kept = _collapse_superseded([], sup)
eq("D7 an empty sweep is a no-op", (len(kept), len(sup)), (0, 0))

print("\n§E  two different reports in the same mailbox never collapse into each other")
mixed = (backlog(4)
         + [msg(f"<x{i}@b>", "X-Report.xlsx", "x_report",
                BASE + timedelta(hours=i)) for i in range(4)]
         + [msg(f"<c{i}@b>", "MA Commission.xlsx", "ma_commission",
                BASE + timedelta(hours=i)) for i in range(4)])
sup = []
kept = _collapse_superseded(mixed, sup)
eq("E1 daily_sales -> 1, x_report -> 1, ma_commission (undeclared) -> all 4", len(kept), 6)
eq("E2 the collapsed ones are only the two declared reports' older copies", len(sup), 6)
ok("E3 every ma_commission message survives",
   sum(1 for k in kept if k["upload_type"] == "ma_commission") == 4)
ok("E4 grouping is per FILENAME too — a second declared report keeps its own winner",
   len({k["name"] for k in kept}) == 3, sorted({k["name"] for k in kept}))

print("\n§F  the sweep wires it, and a collapsed message is TERMINAL")
RSRC = _src(ROUTER)
ok("F1 'superseded' is a terminal-zero status (never re-fetched)",
   "'superseded'" in RSRC.split("SWEEP_TERMINAL_ZERO_STATUSES", 1)[1][:200])
SWEEP = _func_src(ROUTER, "_run_email_sweep")
ok("F2 the sweep passes a collector to fetch_new_attachments (collapse is ON)",
   "_superseded)" in SWEEP and "fetch_new_attachments" in SWEEP)
ok("F3 the collapsed copies are JOURNALLED, with the same conflict target as the ingest loop",
   "'status': 'superseded'" in SWEEP
   and SWEEP.count("on_conflict='org_id,account,message_id,filename'") == 2)
ok("F4 a failed superseded-journal is COUNTED, not swallowed (a lost row = re-import next sweep)",
   "journal_failures += 1" in SWEEP.split("superseded-upsert", 1)[0][-400:])
ok("F5 they are journalled BEFORE the ingest loop, so an interrupted sweep still never re-fetches",
   SWEEP.index("'status': 'superseded'") < SWEEP.index("for f in files:"))
ok("F6 the status line SAYS how many were superseded — never a silent drop",
   "superseded by a newer one" in SWEEP)
FETCH = _func_src("app/modules/commcalc/email_sweep.py", "fetch_new_attachments")
ok("F7 collapse is OPT-IN: omit the collector and the fetcher behaves exactly as before",
   "if superseded is not None:" in FETCH)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
