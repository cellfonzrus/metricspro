"""DB-FREE PROOF — a sweep that delivered NOTHING may not stamp `last_run_at`.

THE LIVE DEFECT THIS PINS (Boost / house org, 2026-09-07 → 2026-09-19). The b2breports@ mailbox's IMAP
login was rejected on EVERY hourly run for twelve days. Zero attachments imported; `daily_sales_feed`
for September froze at 09-01..09-07 (6,591 rows, all uploaded 2026-09-07) while 09-08..09-19 never
landed. Yet `email_sweep_config.last_run_at` read three minutes old on 2026-09-20, because the
connect-failure path stamped it anyway:

    _email_status_update(client, org_id, account,
        {'last_run_at': _datetime.now(...).isoformat(), 'last_status': em})   # ← the bug

So `_scan_connector_health`'s STALE arm — "no successful run in <window>" — could never fire for that
mailbox. It escaped total silence only because the rejection text happens to contain the word "failed",
which the ERRORED arm greps for. A differently-worded failure would have been invisible for twelve days
with no signal at all.

The contract already existed and was already written down at `_OPTIONAL_STATUS_COLS` (router ~line 3150,
mig 241): only a run that ACTUALLY IMPORTED DATA advances `last_run_at`; every non-delivering attempt —
rejected login, no filename rules, a crash, a connect error — records `last_attempt_at`. The portal
sweeps adopted it via `_sweep_set_status`. The mailbox and FTP sweeps never did. This harness is that
sentence as a test, for both.

WHAT IS NOT CHANGED: scheduling. `/email-sweep/run-due` and the FTP dispatcher key off `next_run_at`,
which is advanced independently before the worker runs — §D pins that, because moving a sweep's
last_run_at semantics would be a silent retry-storm if anything scheduled off it.

Run:  cd backend && python3 harness_sweep_freshness.py
"""
import ast
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

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
    """SOURCE TEXT of one top-level function, located by PARSING (immune to async/decorators/reflow).
    A missing anchor raises by name instead of dying on an opaque slice — a dead harness reads as
    'not run', which is worse than a failing one."""
    tree = ast.parse(_src(rel))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(_src(rel), node)
    raise AssertionError(f"function {name!r} not found in {rel}")


RSRC = _src(ROUTER)

# ── the unit under test, lifted out of router.py without importing FastAPI/supabase ──────────────
_ns = {"_datetime": datetime, "_timezone": timezone, "_MISSING_STATUS_COLS": set(),
       "_OPTIONAL_STATUS_COLS": ("last_attempt_at",)}
exec(_func_src(ROUTER, "_sweep_run_stamp"), _ns)
exec(_func_src(ROUTER, "_email_status_update"), _ns)
stamp = _ns["_sweep_run_stamp"]
esu = _ns["_email_status_update"]

print("\n§A  the stamp itself — success advances last_run_at, an attempt never does")
eq("A1 a delivering run stamps last_run_at only", sorted(stamp(True)), ["last_run_at"])
eq("A2 a non-delivering attempt stamps last_attempt_at only", sorted(stamp(False)), ["last_attempt_at"])
ok("A3 both are ISO-8601 UTC instants",
   stamp(True)["last_run_at"].endswith("+00:00") and stamp(False)["last_attempt_at"].endswith("+00:00"))
ok("A4 neither ever writes BOTH (a failure must not look like a success in either column)",
   len(stamp(True)) == 1 and len(stamp(False)) == 1)

print("\n§B  every mailbox/FTP exit path routes through it — no raw last_run_at survives in the sweeps")
for fn in ("_run_email_sweep", "_run_ftp_sweep"):
    try:
        body = _func_src(ROUTER, fn)
    except AssertionError:
        ok(f"B0 {fn} exists", False, "function not found — rename? the guard below is now blind")
        continue
    ok(f"B1 {fn} stamps only via _sweep_run_stamp (no hand-written 'last_run_at': ...)",
       "'last_run_at':" not in body and '"last_run_at":' not in body,
       "a raw last_run_at write is exactly the defect this harness pins")
    ok(f"B2 {fn} does stamp — it is not silently unstamped now",
       "_sweep_run_stamp(" in body)
    ok(f"B3 {fn} gates the success stamp on attachments actually ingested (ok > 0)",
       "_sweep_run_stamp(ok > 0)" in body,
       "connect-fine-but-imported-nothing must NOT advance last_run_at")

# the two failure shapes that bit live, asserted by name at their own call sites
_email_body = _func_src(ROUTER, "_run_email_sweep")
ok("B4 the AUTH/connect rejection path stamps an ATTEMPT (the Boost 12-day outage)",
   "_sweep_run_stamp(False), 'last_status': em}" in _email_body)
ok("B5 the 'no filename rules' path stamps an ATTEMPT (nothing can ever match → nothing delivered)",
   "_sweep_run_stamp(False)," in _email_body and "no filename rules configured" in _email_body)
_ftp_body = _func_src(ROUTER, "_run_ftp_sweep")
ok("B6 the FTP connect-error path stamps an ATTEMPT (same class, same fix)",
   "_sweep_run_stamp(False), 'last_status': f\"connect error: {e}\"}" in _ftp_body)

print("\n§C  the live regression — connector-health must now call the Boost mailbox out")
# _scan_connector_health's classification, restated (the scan itself needs a DB client).
_STALE_H = 30


def classify(row, now, stale_hours=_STALE_H):
    if row.get("enabled", True) is False:
        return None
    status = (row.get("last_status") or "").lower()
    failed = ("error" in status) or ("fail" in status) or ("403" in status)
    stale, lr = False, row.get("last_run_at")
    if not failed and lr:
        stale = (now - lr).total_seconds() > stale_hours * 3600
    return "errored" if failed else ("stalled" if stale else None)


NOW = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
LAST_REAL_DELIVERY = datetime(2026, 9, 7, 23, 11, 35, tzinfo=timezone.utc)
BOOST_STATUS = ("login rejected: b'[AUTHENTICATIONFAILED] Authentication failed.' — the saved password "
                "is UNCHANGED. Mail hosts sometimes temporarily block frequent logins; the next "
                "scheduled run usually recovers. Re-enter the password only if Test connection also fails.")

eq("C1 BEFORE — last_run_at bumped by the failing run: STALE can never fire",
   classify({"last_status": BOOST_STATUS, "last_run_at": NOW - timedelta(minutes=3)}, NOW), "errored")
eq("C2 AFTER — last_run_at frozen at the last real delivery: still reported",
   classify({"last_status": BOOST_STATUS, "last_run_at": LAST_REAL_DELIVERY}, NOW), "errored")
# the wording-independence that is the actual point: strip the words the ERRORED arm greps for.
QUIET = "login rejected by host; retrying next run"      # no 'error', no 'fail', no '403'
eq("C3 BEFORE — a failure whose TEXT lacks 'fail'/'error' was completely invisible",
   classify({"last_status": QUIET, "last_run_at": NOW - timedelta(minutes=3)}, NOW), None)
eq("C4 AFTER — the frozen last_run_at reports it on the STALE arm, whatever the wording",
   classify({"last_status": QUIET, "last_run_at": LAST_REAL_DELIVERY}, NOW), "stalled")
eq("C5 a healthy mailbox that imported within the window is reported as neither",
   classify({"last_status": "11/11 attachments ingested", "last_run_at": NOW - timedelta(hours=1)}, NOW),
   None)
eq("C6 a DAILY mailbox mid-cadence is not falsely stalled (26h < 30h window)",
   classify({"last_status": "3/3 attachments ingested", "last_run_at": NOW - timedelta(hours=26)}, NOW),
   None)
eq("C7 a disabled mailbox is never reported", classify({"enabled": False, "last_status": QUIET,
                                                        "last_run_at": LAST_REAL_DELIVERY}, NOW), None)
ok("C8 email_sweep_config and ftp_sweep_config are both scanned (the fix reaches a surface)",
   '("email_sweep_config", "Email import")' in RSRC and '("ftp_sweep_config", "FTP import")' in RSRC)

print("\n§D  scheduling is untouched — nothing sweeps off last_run_at")
ok("D1 the email /run-due selector filters on next_run_at, not last_run_at",
   ".lte('next_run_at', now_iso)" in RSRC)
ok("D2 next_run_at is advanced BEFORE the worker runs, independently of the outcome",
   "'next_run_at': nxt}" in RSRC and "_dispatch_email_sweep_worker(due)" in RSRC)
ok("D3 no sweep decides due-ness from last_run_at",
   ".lte('last_run_at'" not in RSRC and ".gte('last_run_at'" not in RSRC)

print("\n§E  a pre-migration database still gets its status line (the optional column degrades)")


class _FakeQ:
    def __init__(self, sink, reject):
        self.sink, self.reject, self._f = sink, reject, {}

    def update(self, row):
        self._row = dict(row)
        return self

    def eq(self, k, v):
        self._f[k] = v
        return self

    def execute(self):
        if any(k in self.reject for k in self._row):
            raise RuntimeError("column email_sweep_config.last_attempt_at does not exist")
        self.sink.append((dict(self._row), dict(self._f)))
        return self


class _FakeClient:
    def __init__(self, reject=()):
        self.writes, self.reject = [], set(reject)

    def schema(self, _s):
        return self

    def table(self, _t):
        return _FakeQ(self.writes, self.reject)


_ns["_MISSING_STATUS_COLS"] = set()
c = _FakeClient(reject={"last_attempt_at"})
esu(c, "org-1", "default", {**stamp(False), "last_status": "login rejected"})
ok("E1 a pre-241 database still records last_status when last_attempt_at is rejected",
   len(c.writes) == 1 and c.writes[0][0] == {"last_status": "login rejected"},
   c.writes)
eq("E2 the write stays scoped to org_id AND account (RULE ONE)",
   c.writes[0][1], {"org_id": "org-1", "account": "default"})
ok("E3 the missing column is remembered, so the retry happens once — not on every sweep",
   ("email_sweep_config", "last_attempt_at") in _ns["_MISSING_STATUS_COLS"])
c2 = _FakeClient()
_ns["_MISSING_STATUS_COLS"] = set()
esu(c2, "org-1", "default", {**stamp(True), "last_status": "11/11 attachments ingested"})
ok("E4 a migrated database writes the stamp and the status in ONE patch",
   len(c2.writes) == 1 and set(c2.writes[0][0]) == {"last_run_at", "last_status"}, c2.writes)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
