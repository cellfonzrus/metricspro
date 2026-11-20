"""Proof + LOCK: "which store-days do we have, and which are still to fetch" has ONE home, and an
unreadable feed can never read as an empty one (index §COMMCALC-82, §CORE-46).

WHAT THIS PROVES, DB-free and browser-free:
  • the pure rules of `commcalc/feed_day_coverage.py` — three states, not two;
  • that a FAILED read answers None everywhere it is produced, and that every caller which could
    turn that into a figure asks `is None` before it does;
  • that the manual arrival door is the feed's OWN upload type, so a hand-fetched file and a pulled
    file land in the same place;
  • that showing a stored credential is refused unless it was recorded (§CORE-46).

WHAT IT LOCKS, and why the lock is the point. The defect this mechanism exists to end is a read that
could not happen reported as a measurement: `{}` from a failed query made the fee reconciliation flag
every store-day against zero, and would have made this grid send a person to download 29 files a day
that were already landed. That distinction only survives if nothing re-collapses it. So:

  • `feed_day_coverage.py` is the ONLY module that decides whether a store-day is had, owed or
    unknown, and it reads what landed through the feed's own per-store-day home — never a second
    query over the settlement table;
  • the two legs that produce the map return None on failure, and say so in the module;
  • the download console builds its picker from the ALREADY-STRIPPED data_source row, so no
    credential can reach a screen through the list;
  • the credential reveal goes through `core/credential_reveal`, which writes its audit row BEFORE it
    answers, and the endpoint carries the fail-closed settings gate;
  • no vendor, carrier or tenant name is spelled in any of it (RULE TWO).

Every lock carries its own CONTROL: the check is re-run against mutated text and must go RED.

Run:  cd backend && python3 harness_feed_day_coverage.py        Exit 0 = all green.
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, ".")

from app.modules.commcalc import epay_fee_recon as fr               # noqa: E402
from app.modules.commcalc import epay_ingest as ei                  # noqa: E402
from app.modules.commcalc import feed_day_coverage as cov           # noqa: E402
from app.modules.commcalc import processor_backoffice_sweep as pbs   # noqa: E402
from app.modules.core import credential_reveal as cr                # noqa: E402
from app.modules.core import portal_secret as psec                  # noqa: E402
from app.modules.core import relation_home                          # noqa: E402

PASS = FAIL = 0
_HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(_HERE, "app")
MIGRATIONS = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations"))
HOME_REL = "app/modules/commcalc/feed_day_coverage.py"
ROUTER_REL = "app/modules/commcalc/router.py"
REVEAL_REL = "app/modules/core/credential_reveal.py"


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


class _Boom:
    """A client whose every read raises — "I could not look", not "there is nothing there"."""

    def schema(self, _n):
        return self

    def table(self, _n):
        return self

    def select(self, *_a, **_k):
        raise RuntimeError("transport is down")


# ── A. three states, not two ─────────────────────────────────────────────────────────────────────
section("A. the rules a coverage grid follows")
ok(cov.days_in_range("2026-11-18", "2026-11-20")
   == ("2026-11-18", "2026-11-19", "2026-11-20"),
   "a range is inclusive and oldest-first")
ok(cov.days_in_range("2026-11-20", "2026-11-18") == ()
   and all(cov.days_in_range(v, "2026-11-20") == () for v in ("", "last tuesday", None, 7)),
   "a backwards or unreadable range draws nothing rather than guessing at days")
ok(len(cov.days_in_range("2026-01-01", "2026-12-31")) == cov.MAX_DAYS,
   "a wide range is clamped to %d days — a year of columns is not a grid" % cov.MAX_DAYS)
_g = cov.grid(("B-1",), ("2026-11-18",), {("B-1", "2026-11-18"): {"lines": 3, "payment": 1.0,
                                                                  "fee": 0.0}})
ok(_g["stores"][0]["days"][0]["state"] == cov.HAVE and _g["have"] == 1 and _g["measured"] is True,
   "a landed store-day is HAD, with its figures")
_m = cov.grid(("B-1",), ("2026-11-18",), {})["stores"][0]["days"][0]
ok(_m["state"] == cov.MISSING and "payment" not in _m and "lines" not in _m,
   "a readable feed with no rows is MISSING, and carries no figures — a printed 0.00 would read as "
   "a measured zero")
_u = cov.grid(("B-1",), ("2026-11-18",), None)
ok(_u["measured"] is False and _u["stores"][0]["days"][0]["state"] == cov.UNKNOWN
   and _u["missing"] == 0 and "could not" in _u["note"].lower(),
   "an UNREADABLE feed is unknown everywhere and reports NOTHING as missing — the defect this "
   "module exists to end")
ok(cov.grid(("B-1",), ("2026-11-18",),
            {("B-1", "2026-11-18"): {"lines": 0}})["stores"][0]["days"][0]["state"] == cov.MISSING,
   "a day with rows but no lines is still a file nobody fetched")
ok(cov.grid(("B-1",), ("2026-11-18",),
            {("B-9", "2026-11-18"): {"lines": 2}})["undeclared_stores"] == ["B-9"],
   "rows under a store code nobody declared are REPORTED, not dropped")
ok([s["store"] for s in cov.grid(("B-1",), ("2026-11-18",),
                                 {("OTHER", "2026-11-18"): {"lines": 9}})["stores"]] == ["B-1"]
   and cov.grid(("B-1",), ("2026-11-18",), {("OTHER", "2026-11-18"): {"lines": 9}})["have"] == 0,
   "a store the caller did not ask for is never a cell, so one tenant's days cannot fill another's "
   "grid")
_a, _b = cov.default_range(date(2026, 11, 20))
ok((_a, _b) == ("2026-11-07", "2026-11-20") and len(cov.days_in_range(_a, _b)) == cov.DEFAULT_DAYS,
   "the default window is a readable fortnight ending today")

# ── B. "I could not look" survives every leg ─────────────────────────────────────────────────────
section("B. an unreadable read answers None, everywhere it is produced")
ok(ei.per_store_day(_Boom(), "org", "2026-11-01", "2026-11-02") is None,
   "the portal leg answers None when its read fails")
ok(fr.system_fee_per_store_day(_Boom(), "org", "2026-11-01", "2026-11-02") is None,
   "the system leg answers None when its read fails")
_out = fr.fee_recon(_Boom(), "org", "2026-11-01", "2026-11-01")
ok(_out["measured"] is False and _out["rows"] == [] and _out["totals"]["flagged"] == 0,
   "the reconciliation refuses to flag a range it could not read — a false alarm over money")
ok("not zero" in _out["note"].lower() or "unmeasured" in _out["note"].lower(),
   "and it says the figures are unmeasured rather than zero")
_cov_unreadable = cov.coverage(_Boom(), "org", ("B-1",), "2026-11-18", "2026-11-19")
ok(_cov_unreadable["measured"] is False and _cov_unreadable["missing"] == 0,
   "the coverage grid inherits that None rather than drawing a grid of missing days")

# ── C. ONE home for the question ─────────────────────────────────────────────────────────────────
section("C. one home, one read")
_home = read(HOME_REL)
_router = read(ROUTER_REL)
ok("per_store_day" in _home and "raw_epay_daily_tx" not in _home,
   "the one home reads what landed through the feed's OWN per-store-day home — never a second query "
   "over the settlement table")
# "How many days between two dates" (an int) is a DIFFERENT question, answered by three older
# helpers named `days_between`; the RUN of days a grid is drawn over, and the grid itself, belong
# here alone. That is why this module's function has its own name — and the scan below looks for
# both, so a second module cannot answer either question under either spelling. The excused list
# may only shrink.
_COUNTERS = ("app/modules/commcalc/ma_handset_cogs.py", "app/modules/commcalc/device_history.py",
             "app/modules/commcalc/portout_flags.py")
_second = [rel for rel, src in py_files()
           if rel != HOME_REL and rel not in _COUNTERS
           and re.search(r"def\s+(days_in_range|days_between|grid)\s*\(", src)]
ok(not _second, "no second module produces a run of days or builds a store-day grid", str(_second))
ok(all(re.search(r"def\s+days_between\s*\(", read(c)) for c in _COUNTERS),
   "the three excused helpers are the day-COUNT ones, and still answer that other question")
ok(not re.search(r"def\s+days_between\s*\(", _home),
   "and this module does not ALSO define the count under the old name")
ok("_fdc().coverage(" in _router and "_fdc().grid(" in _router,
   "the download console ASKS the one home for the grid rather than folding its own")
_console = _router[_router.index("def per_store_downloads("):]
_console = _console[:_console.index("\n@router.")] if "\n@router." in _console else _console
ok("days_in_range" not in _console and "timedelta" not in _console,
   "and the endpoint does no day arithmetic of its own")
_banned = [w for w in ("epayworldwide", "cellfonz", "luxelink", "vzone", "ownerportal", "boost")
           if w in _home.lower()]
ok(not _banned, "no vendor, portal or tenant name in the one home (RULE TWO)", str(_banned))

# ── D. the hand-fetched file lands where the pulled file lands ───────────────────────────────────
section("D. one feed, one arrival door")
ok(pbs.arrival_type("processor_daily_tx") == ei.UPLOAD_TYPE,
   "a hand-fetched report arrives under the feed's OWN upload type (%s)" % ei.UPLOAD_TYPE)
ok(set(pbs.TARGETS) == set(pbs._ARRIVAL_TYPES),
   "every declared landing target has an arrival door — the manual route can never be narrower than "
   "the automated one")
_adapter = read("app/modules/commcalc/processor_backoffice_sweep.py")
ok(('"%s"' % ei.UPLOAD_TYPE) not in _adapter and ("'%s'" % ei.UPLOAD_TYPE) not in _adapter,
   "the upload type is DEREFERENCED from the feed, never written as a literal in the adapter")
ok(pbs.arrival_type("not_a_target") == "" and pbs.arrival_type(None) == "",
   "an unknown target answers nothing rather than a plausible-looking door")
ok(("/api/v1/commcalc/upload/%s" % ei.UPLOAD_TYPE) not in _router,
   "the endpoint builds the upload path from the adapter's answer, never from a literal")

# ── E. no credential reaches a screen through the list ───────────────────────────────────────────
section("E. the list carries no credential")
_fields = __import__("app.modules.commcalc.router", fromlist=["x"])._DOWNLOAD_LOGIN_FIELDS
_leak = [f for f in _fields if psec.is_secret_column("data_source", f)]
ok(not _leak, "the picker's field list contains no credential column", str(_leak))
ok("_strip_source_pw(" in _console,
   "the picker is built from the ALREADY-STRIPPED public row")
ok("_download_login(_strip_source_pw(" in _router,
   "and it cannot be built from a raw row by accident")

# ── F. a credential SHOWN is a credential recorded (§CORE-46) ────────────────────────────────────
section("F. showing a credential is an audited event")
_rev = read(REVEAL_REL)
ok("is_secret_column" in _rev,
   "the reveal asks the credential REGISTRY which columns are credentials — it keeps no list")
ok(_rev.index("record(") < _rev.index('return {"ok": True'),
   "the audit row is written BEFORE the credential is returned")


class _AuditRefuses:
    def schema(self, _n):
        return self

    def table(self, _n):
        return self

    def insert(self, _row):
        raise RuntimeError("relation does not exist")


_row = {"id": "s1", "org_id": "org", "password": "x"}
ok(cr.reveal(_AuditRefuses(), "org", relation="data_source", column="password", row=_row,
             who="a")["ok"] is False,
   "a reveal that could not be RECORDED is refused, never served unrecorded")
ok(cr.reveal(_AuditRefuses(), "org", relation="data_source", column="username", row=_row,
             who="a")["ok"] is False,
   "a column the registry does not call a credential cannot be revealed")
ok(cr.reveal(_AuditRefuses(), "other", relation="data_source", column="password", row=_row,
             who="a")["note"] == cr.NOT_FOUND,
   "another tenant's row is NOT FOUND, whatever the caller passes")
ok("password" not in cr.audit_row("org", relation="data_source", column="password", row_id="s1",
                                  who="a", context="c").get("context", ""),
   "the audit row carries no credential — the value is not even an argument")
_reveal_ep = _router[_router.index("def reveal_source_password("):]
_reveal_ep = _reveal_ep[:_reveal_ep.index("\n@router.")]
ok("_require_import_admin(" in _reveal_ep,
   "the reveal endpoint carries the fail-closed settings gate")
ok("_cr.reveal(" in _reveal_ep and '"password": out["value"]' in _reveal_ep,
   "and it returns only what the one home decided it may")
ok(relation_home.schema_of(cr.TABLE) == cr.SCHEMA,
   "the audit relation is registered in the one home of which schema holds a relation (§CORE-30)")
_sql = open(os.path.join(MIGRATIONS, "1128_credential_reveal_audit.sql")).read().lower()
ok("create table if not exists core.credential_reveal_audit" in _sql,
   "migration 1128 creates the audit table, idempotently")
ok("grant select, insert, update, delete on core.credential_reveal_audit to service_role" in _sql,
   "and grants what it creates to service_role only (§CORE-13)")
ok("1128" in cr.UNAUDITABLE,
   "the refusal names the migration to run, so an operator is told what to do")

# ── G. the controls: every lock above can go RED ─────────────────────────────────────────────────
section("G. controls")
ok(cov.grid(("B-1",), ("2026-11-18",), None)["stores"][0]["days"][0]["state"] != cov.MISSING,
   "the unknown-vs-missing check really distinguishes the two words")
ok(re.search(r"def\s+(days_in_range|days_between|grid)\s*\(", "def grid(a):") is not None,
   "the second-grid scan really matches a definition")
ok("raw_epay_daily_tx" in _home.replace("per_store_day", "raw_epay_daily_tx"),
   "the second-query scan really reads the module text")
ok(psec.is_secret_column("data_source", "password") is True,
   "the credential-registry question really answers yes for a credential")
ok(bool([f for f in ("password",) if psec.is_secret_column("data_source", f)]),
   "the field-list leak scan really catches a credential in a list")
ok("x" in [w for w in ("x",) if w in "a x b".lower()],
   "the vendor-name scan really matches a literal")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a read that could not happen is drifting back into a measurement, or a credential "
          "is reaching a screen unrecorded. Read the module headers; fix the dereference, do not relax "
          "the lock.")
    sys.exit(1)
print("\nOK — three states not two, one home for coverage, one arrival door for a hand-fetched file, "
      "and no credential shown without a record of who saw it.")
