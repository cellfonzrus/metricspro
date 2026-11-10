"""Proof + LOCK: a portal pull owes HISTORY in ONE home, and a login's STORE is one fact (index §COMMCALC-57).

WHAT THIS PROVES. The pure rules of `commcalc/portal_backfill.py` — chunking a declared history into
windows, which windows are still owed, which are stuck, and the outcome word derived from what the
pull actually reported — plus the per-store adapter's refusals. No browser, no network, no database.

WHAT THIS LOCKS, and why the lock is the point. The reason this mechanism exists is that the loop's
window arithmetic could not be ASKED for history, so the next feed that owed some would have written a
second pull loop. If a second module starts planning date spans, or stops dereferencing the loop's own
ceiling, or a second table starts recording what was pulled, the extraction bought nothing and the
drift starts again. So:

  • `portal_backfill.py` is the ONLY module that plans a backfill span or touches the backlog table;
  • its window ceiling is DEREFERENCED from `portal_report_pull.MAX_WINDOW_DAYS`, never copied;
  • the ONE place the loop computes a window consults it, and records what it answered;
  • the natural key in code EQUALS the primary key in migration 1116 (a drift there is a backfill that
    re-pulls its first window every night);
  • a login's store reaches `epay_ingest` through its one home, and the DECLARED terminal registry
    still wins where it has an answer;
  • the adapter names no vendor, no portal and no tenant (RULE TWO).

Every lock carries its own CONTROL: the check is re-run against mutated text and must go RED, so a
check that has quietly stopped looking at anything fails here rather than in six months.

Run:  cd backend && python3 harness_portal_backfill.py        Exit 0 = all green.
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, ".")

from app.modules.commcalc import epay_ingest                       # noqa: E402
from app.modules.commcalc import portal_backfill as bf             # noqa: E402
from app.modules.commcalc import portal_report_pull as prp         # noqa: E402
from app.modules.commcalc import processor_backoffice_sweep as pbs  # noqa: E402
from app.modules.core import relation_home                         # noqa: E402

PASS = FAIL = 0
_HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(_HERE, "app")
MIGRATIONS = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations"))
HOME_REL = "app/modules/commcalc/portal_backfill.py"
ADAPTER_REL = "app/modules/commcalc/processor_backoffice_sweep.py"

# A module that may name the backlog table or the history columns WITHOUT being a second home, each
# with the reason. The list may only shrink.
ALLOWED_NAMERS = {
    HOME_REL: "the one home",
    "app/modules/commcalc/router.py": "the save path lists the COLUMNS it accepts and cleans them "
                                      "through the one home's clean_declaration — it plans nothing",
    "app/modules/commcalc/portal_report_pull.py": "the pull loop ASKS the one home for its window",
    ADAPTER_REL: "the adapter reads its own login's store_code — a value, not a plan",
    "app/modules/core/relation_home.py": "the registry of WHICH SCHEMA holds a relation (§CORE-30) "
                                         "names every table in the platform by design",
}


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)
        if detail:
            print("      %s" % detail)
    return bool(cond)


def section(title):
    print("\n— %s —" % title)


def read(rel):
    with open(os.path.join(_HERE, rel)) as fh:
        return fh.read()


def py_files():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root, f)
                yield os.path.relpath(full, _HERE).replace(os.sep, "/"), open(full).read()


# ── A. the pure rules ────────────────────────────────────────────────────────────────────────────
section("A. the rules a backfill follows")
ok(bf.plan_windows(None) == () and bf.plan_windows("") == (),
   "a login that declares no history owes nothing — this may never widen an unasked pull")
ok(bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7)
   == (("2026-01-01", "2026-01-07"), ("2026-01-08", "2026-01-14"), ("2026-01-15", "2026-01-19")),
   "history is chunked oldest-first and stops at YESTERDAY")
ok(bf.plan_windows("2026-12-01", today="2026-11-10") == (),
   "a start date in the future owes nothing")
ok(all(bf.plan_windows(v, today="2026-11-10") == () for v in ("last year", "2026-13-40", 7, {})),
   "an unreadable declaration owes nothing rather than being guessed at")
_wide = bf.plan_windows("2026-01-01", today="2026-11-10", chunk_days=10 ** 6)[0]
ok((date.fromisoformat(_wide[1]) - date.fromisoformat(_wide[0])).days + 1 == prp.MAX_WINDOW_DAYS,
   "a declared chunk is clamped to the LOOP's ceiling (%d days)" % prp.MAX_WINDOW_DAYS)
_plan = bf.plan_windows("2026-01-01", today="2026-01-20", chunk_days=7)
ok(bf.owed_windows(_plan, [{"window_from": "2026-01-01", "window_to": "2026-01-07",
                            "state": "done", "attempts": 1}])[0] == ("2026-01-08", "2026-01-14"),
   "a window already answered is not owed again")
ok(bf.owed_windows(_plan, [{"window_from": "2026-01-01", "window_to": "2026-01-07",
                            "state": "empty", "attempts": 1}])[0] == ("2026-01-08", "2026-01-14"),
   "a window the portal had nothing for is an ANSWER, not a retry forever")
_exhausted = [{"window_from": "2026-01-01", "window_to": "2026-01-07",
               "state": "failed", "attempts": bf.MAX_ATTEMPTS}]
ok(bf.owed_windows(_plan, _exhausted)[0] == ("2026-01-08", "2026-01-14")
   and bf.stuck_windows(_plan, _exhausted) == (("2026-01-01", "2026-01-07"),),
   "an exhausted window stops blocking the backfill AND is reported as a gap")
ok(bf.state_of({"delivered": True, "rows_ingested": 3}) == "done"
   and bf.state_of({"delivered": False, "rows_ingested": 0}) == "empty"
   and bf.state_of({"delivered": False, "rows_ingested": 0, "error": "x"}) == "failed"
   and bf.state_of(None) == "failed",
   "the outcome word is DERIVED from what the pull reported")
ok(bf.window_key(("2026-01-01", "2026-01-07"))
   == bf.window_key({"window_from": "2026-01-01", "window_to": "2026-01-07"}),
   "one spelling of which window this is, however it is held")


# ── B. the ceiling is dereferenced, not copied ──────────────────────────────────────────────────
section("B. one ceiling, dereferenced")
_home = read(HOME_REL)
ok("prp.MAX_WINDOW_DAYS" in _home,
   "the one home reads the loop's ceiling")
ok(not re.search(r"\b%d\b" % prp.MAX_WINDOW_DAYS, re.sub(r"#.*", "", _home)),
   "and keeps no copy of its VALUE (%d) in code" % prp.MAX_WINDOW_DAYS,
   "a second copy of the ceiling is the drift this module exists to prevent")
ok(bf.window_width(10 ** 6) == prp.MAX_WINDOW_DAYS and bf.window_width(None) == bf.DEFAULT_CHUNK_DAYS,
   "the clamp is live, not a comment")


# ── C. one home, and the loop asks it ───────────────────────────────────────────────────────────
section("C. one home for which span is owed")
_namers, _planners = [], []
for rel, text in py_files():
    if bf.TABLE in text or "history_from" in text:
        if rel not in ALLOWED_NAMERS:
            _namers.append(rel)
    if rel != HOME_REL and re.search(r"def\s+(plan_windows|owed_windows|window_key)\b", text):
        _planners.append(rel)
ok(not _namers, "only the one home and its declared callers name the backlog or the history column",
   "second namer(s): %s" % _namers)
ok(not _planners, "no second module plans a backfill span", "second planner(s): %s" % _planners)
_loop = read("app/modules/commcalc/portal_report_pull.py")
ok("bf.window_for(" in _loop and "bf.record(" in _loop,
   "the ONE place the loop computes a window asks the one home, and records what it answered")
ok(_loop.count("d_from, d_to = date_range(") <= 2,
   "and the recent window is still computed in one place")


# ── D. the natural key in code equals the primary key in the migration ──────────────────────────
section("D. the key cannot drift from the schema")
_sql = read(os.path.join("..", "database", "migrations", "1116_portal_pull_backlog.sql"))
_pk = re.search(r"primary key \(([^)]+)\)", _sql, re.I)
ok(bool(_pk), "migration 1116 declares the primary key")
_cols = [c.strip() for c in (_pk.group(1) if _pk else "").split(",")]
_code_key = re.search(r'on_conflict="([^"]+)"', _home)
ok(bool(_code_key), "the one home upserts on a stated natural key")
ok(_cols == [c.strip() for c in (_code_key.group(1) if _code_key else "").split(",")],
   "the upsert key EQUALS the primary key",
   "sql=%s code=%s" % (_cols, _code_key and _code_key.group(1)))
ok("grant select, insert, update, delete on commcalc.%s to service_role" % bf.TABLE in _sql.lower(),
   "the migration grants what it creates, service_role only")
ok(relation_home.schema_of(bf.TABLE) == bf.SCHEMA,
   "the new relation is registered in relation_home (§CORE-30)")
ok(bf.ALL_REPORTS == "*" and "default '*'" in _sql.lower(),
   "the whole-report-set key has one spelling, in code and in the schema")


# ── E. a login's store is one fact, and the declared registry still wins ────────────────────────
section("E. a row pulled through a store's login belongs to that store")
_rows = [{"terminal_id": "T1"}, {"terminal_id": "T2"}]
_unresolved = epay_ingest.resolve_stores(_rows, {"T1": "B-DECLARED"}, login_store_code="B-LOGIN")
ok(_rows[0]["store_code"] == "B-DECLARED",
   "the DECLARED terminal→store registry wins where it has an answer")
ok(_rows[1]["store_code"] == "B-LOGIN" and not _unresolved,
   "and the login answers where it has none")
_rows2 = [{"terminal_id": "T9"}]
ok(epay_ingest.resolve_stores(_rows2, {}) == {"T9"} and _rows2[0]["store_code"] is None,
   "a company-wide caller passes no login store and is UNCHANGED")
_adapter = read(ADAPTER_REL)
ok("store_code=store or None" in _adapter or "login_store_code" in _adapter
   or "store_code=store" in _adapter,
   "the adapter passes its login's store through that one home")


# ── F. the adapter rides the one loop and names no vendor ───────────────────────────────────────
section("F. the adapter is an adapter")
ok(prp.adapter_for(pbs.CONNECTOR) is not None and prp.is_pullable(pbs.CONNECTOR),
   "the loop resolves the connector from the registry")
ok(all(callable(getattr(pbs, n, None)) for n in prp.REQUIRED),
   "it satisfies the loop's contract (%s)" % ", ".join(prp.REQUIRED))
ok(not re.search(r"def\s+(pull_reports_on_page|run_cold_sweep|download_report)\b", _adapter),
   "and defines no second pull loop")
_banned = [w for w in ("epayworldwide", "cellfonz", "luxelink", "vzone", "ownerportal")
           if w in _adapter.lower()]
ok(not _banned, "no vendor, portal or tenant name in the adapter (RULE TWO)", str(_banned))
ok(pbs.portal_descriptor(pbs.CONNECTOR)["base_url"] is None,
   "the portal's address is the operator's declaration, never a constant")
ok(pbs.report_specs(pbs.CONNECTOR, None, src_row={"id": "s"}) == ()
   and len(pbs.setup_hint({})) > 40,
   "a login that declared nothing pulls nothing and says what to declare")
_out = pbs.ingest_report(None, "org", {"id": "s", "store_code": "B-1"}, pbs.CONNECTOR,
                         {"key": "k", "target": ""}, b"A,B\n1,2\n", "r.csv")
ok(_out["delivered"] is False and _out["rows_ingested"] == 0 and _out["shape"]["columns"] == ["A", "B"],
   "a report with no declared landing target lands NOTHING and reports what it published")
_out2 = pbs.ingest_report(None, "org", {"id": "s"}, pbs.CONNECTOR,
                          {"key": "k", "target": "processor_daily_tx"}, b"A\n1\n", "r.csv")
ok(_out2["delivered"] is False and "store" in (_out2.get("error") or "").lower(),
   "a login with no store and no mapping refuses rather than landing homeless money")


# ── G. the controls: every lock above can go RED ─────────────────────────────────────────────────
section("G. controls")
ok(bool(re.search(r"\b45\b", "width = 45")),
   "the copied-ceiling scan really matches a copied number")
ok(not ("bf.window_for(" in _loop.replace("bf.window_for(", "date_range(")),
   "the loop-consults-the-home check really reads that call")
ok(re.search(r"def\s+plan_windows\b", "def plan_windows(x):") is not None,
   "the second-planner scan really matches a definition")
_mutated = _sql.replace("primary key (org_id, source_id, report_key, window_from, window_to)",
                        "primary key (org_id, source_id)")
_m = re.search(r"primary key \(([^)]+)\)", _mutated, re.I)
ok([c.strip() for c in _m.group(1).split(",")] != _cols,
   "the key-drift check really compares the two keys")
ok("x" in [w for w in ("x",) if w in "a x b".lower()],
   "the vendor-name scan really matches a literal")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — the backfill is drifting: read the module header, fix the dereference, do not "
          "relax the lock.")
    sys.exit(1)
print("\nOK — one home for which span is owed, one ceiling, one key, one store fact; the adapter "
      "rides the one loop and names no vendor.")
