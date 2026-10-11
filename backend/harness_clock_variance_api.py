"""HARNESS — the clock-variance ENDPOINTS, run against an in-memory Supabase (no DB, no network).

The pure decisions are proven in `harness_clock_variance.py` and `harness_payroll_hours_basis.py`.
What THIS proves is the thing a pure harness structurally cannot: that the shipped HTTP surface
actually reaches those decisions, with the right period, the right scope and the right refusals —
and that it behaves honestly while the whole feature is switched OFF, which is how it ships.

THE FOUR FAILURE MODES IT IS AIMED AT
  A. OFF READS AS OFF, NOT AS CLEAN. A board that returns `[]` when the feed is disabled is
     indistinguishable from a board that looked and found nothing. That is the absence-is-never-zero
     defect, on the screen a DM is about to be gated into.
  B. THE PERIOD IS PAYROLL'S PERIOD. The board must resolve its window through the SAME
     `_resolve_range` as `GET /storeops/payroll`, or a DM corrects one fortnight while payroll pays
     another. Proven by calling both and comparing the windows, not by reading the code.
  C. ★ NO LOCKOUT ★. The gate must never refuse a sign-in, must never gate while switched off, and
     must NOT gate when it cannot read the board — failing closed there would lock a DM out of the
     app over a database hiccup, which is the property failing in the direction it exists to prevent.
  D. AN OVERRIDE WITHOUT A REASON IS REFUSED, in words a human can act on, and an override WITH one
     reaches `payroll_change_log` through the one writer.

Run: cd backend && python3 harness_clock_variance_api.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(label, got, want=True):
    """EQUALITY, with `want` defaulting to True so a plain predicate reads naturally.

    Deliberately not `check(label, cond, detail)`: that shape silently passes whenever the third
    argument is truthy, which is most of the time, so a harness written against it reports green
    while proving nothing. This one compares, and prints both sides on a failure.
    """
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


def _raises(fn):
    """Did the call refuse at all? Used where the refusal's SHAPE is proven elsewhere."""
    try:
        fn()
        return False
    except Exception:
        return True


def _status(fn):
    """The HTTP status a refusal carries. A 500 where a 400 or 404 belongs is its own defect: it
    tells the caller "we broke" when the truth is "you asked for something impossible"."""
    from fastapi import HTTPException
    try:
        fn()
        return None
    except HTTPException as e:
        return e.status_code
    except Exception as e:
        return f"NOT AN HTTPException: {type(e).__name__}: {e}"


def _message(fn):
    """The sentence a refusal gives the human. A refusal nobody can act on is a wall, not a gate."""
    from fastapi import HTTPException
    try:
        fn()
        return ""
    except HTTPException as e:
        return str(e.detail)
    except Exception as e:
        return f"{type(e).__name__}: {e}"


# ── the in-memory Supabase, same convention as harness_salary_owed_router_integration.py ─────────
class FakeQuery:
    def __init__(self, store, table, schema):
        self.store, self.table_name, self.schema_name = store, table, schema
        self.filters, self._limit, self._mode, self._payload = [], None, None, None

    def _key(self):
        return f"{self.schema_name}.{self.table_name}"

    def select(self, *a, **k):
        self._mode = "select"; return self

    def eq(self, kk, v):
        self.filters.append(("eq", kk, v)); return self

    def gte(self, kk, v):
        self.filters.append(("gte", kk, v)); return self

    def lte(self, kk, v):
        self.filters.append(("lte", kk, v)); return self

    def lt(self, kk, v):
        self.filters.append(("lt", kk, v)); return self

    def ilike(self, kk, v):
        self.filters.append(("ilike", kk, str(v).strip().lower())); return self

    def in_(self, kk, vals):
        self.filters.append(("in", kk, set(vals))); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, *a, **k):
        return self

    def insert(self, p):
        self._mode, self._payload = "insert", p; return self

    def update(self, p):
        self._mode, self._payload = "update", p; return self

    def upsert(self, p, **k):
        self._mode, self._payload = "upsert", p; return self

    def _match(self, row):
        for op, kk, v in self.filters:
            rv = row.get(kk)
            if op == "eq" and rv != v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if op == "ilike" and str(rv or "").strip().lower() != v:
                return False
            if op == "in" and rv not in v:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self._key(), [])
        if self._mode == "select":
            out = [dict(r) for r in rows if self._match(r)]
            return FakeResult(out[: self._limit] if self._limit else out)
        if self._mode in ("insert", "upsert"):
            ps = self._payload if isinstance(self._payload, list) else [self._payload]
            made = []
            for p in ps:
                r = dict(p)
                r.setdefault("id", f"{self.table_name}-{len(rows)}")
                rows.append(r)
                made.append(r)
            return FakeResult(made)
        if self._mode == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self._payload)
            return FakeResult(hit)
        raise RuntimeError("no mode")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeClient:
    def __init__(self, store):
        self.store = store
        self._schema = "public"

    def schema(self, name):
        c = FakeClient(self.store)
        c._schema = name
        return c

    def table(self, name):
        return FakeQuery(self.store, name, self._schema)

    def rpc(self, *a, **k):
        raise RuntimeError("no rpc in this harness")


STORE = {}
CLIENT = FakeClient(STORE)

import _harness_dbfree  # noqa: E402

_harness_dbfree.install(CLIENT)

import app.modules.core.router as core_router  # noqa: E402
import app.modules.storeops.router as router_mod  # noqa: E402
from app.modules.storeops import clock_variance as _cv  # noqa: E402
from app.modules.storeops import clock_variance_api as api  # noqa: E402
from app.modules.storeops import payroll_hours_basis as _hb  # noqa: E402

router_mod.get_supabase = lambda: CLIENT
api.get_supabase = lambda: CLIENT
core_router._uid_from_token = lambda auth: {"Bearer dm": "dm-uid",
                                            "Bearer mm": "mm-uid"}.get(auth)

ORG = "ORG-CLOCKVAR"
DAY = "2026-10-09"


def seed(*, enabled=False, gate=False, run_gate=False, pay_source=False, rate=17.0,
         feed_hours=8.90, our_hours=9.18, settled=None, no_rate_extra=0):
    """One store, one employee, one day, and a real disagreement — the shaik row from 2026-10-09."""
    STORE.clear()
    _hb.invalidate_config()
    _cv.invalidate_config() if hasattr(_cv, "invalidate_config") else None
    emp = {"id": "e-1", "org_id": ORG, "employee_id": "E2", "name": "Junaid shaik",
           "email": "junaid@example.com", "epay_login": "Junaid", "home_store": "B-4712",
           "is_active": True, "pay_basis": "hourly", "pay_rate": rate,
           "pay_from_schedule": None}
    STORE["storeops.employees"] = [emp] + [
        {"id": f"e-nr{i}", "org_id": ORG, "employee_id": f"E9{i}", "name": f"No Rate {i}",
         "is_active": True, "pay_basis": "hourly", "pay_rate": None}
        for i in range(no_rate_extra)]
    # The rate-less employees are SCHEDULED, which is what makes them a blocker: a dormant record
    # with no rate blocks nothing (the run gate narrows to who it would actually pay), but somebody
    # rostered with no rate would be priced at $0.00 and read as a finished payroll.
    STORE["storeops.shifts"] = [{"org_id": ORG, "employee_id": "E2", "shift_date": DAY,
                                 "scheduled_hours": 9.0, "actual_hours": 0,
                                 "store_code": "B-4712", "status": "scheduled"}] + [
        {"org_id": ORG, "employee_id": f"E9{i}", "shift_date": DAY, "scheduled_hours": 8.0,
         "actual_hours": 0, "store_code": "B-4712", "status": "scheduled"}
        for i in range(no_rate_extra)]
    STORE["storeops.timelog"] = [{"org_id": ORG, "employee_id": "E2", "work_date": DAY,
                                "hours": our_hours, "clock_in": f"{DAY}T14:06:00Z",
                                "clock_out": f"{DAY}T23:17:00Z", "store_code": "B-4712",
                                "auto_clocked_out": False}]
    STORE["storeops.manual_hours"] = []
    STORE["storeops.raw_clock_punch"] = [{
        "org_id": ORG, "employee_raw": "shaik, Junaid", "feed_login": "Junaid",
        "store_code": "B-4712", "store_raw": "4712 Main St", "business_date": DAY,
        "hours": feed_hours, "clock_in": f"{DAY}T14:06:00Z", "clock_out": f"{DAY}T23:00:00Z",
        "excluded": None}]
    STORE["storeops.clock_feed_login_link"] = []
    STORE["storeops.clock_variance_correction"] = list(settled or [])
    STORE["storeops.payroll_hours_basis_config"] = [{
        "org_id": ORG, "hours_basis_mode": "schedule_fallback", "pay_from_schedule_default": True,
        "clock_feed_enabled": enabled, "variance_ask_enabled": enabled,
        "login_gate_enabled": gate, "payroll_run_gate_enabled": run_gate,
        "clock_feed_is_pay_source": pay_source,
        "ask_weekday": 0, "gate_weekday": 1, "run_weekday": 2}]
    STORE["storeops.app_users"] = [
        {"org_id": ORG, "auth_id": "dm-uid", "email": "dm@example.com",
         "role": "district_manager", "employee_id": "D1"},
        # The pay-visibility rule is the owner's: money is hidden below MARKET MANAGER, and which
        # roles see it is per-org config (mig 434). The tick box decides whether somebody is PAID
        # for an unmeasured day, so it is gated with the money — §H proves both directions.
        {"org_id": ORG, "auth_id": "mm-uid", "email": "mm@example.com",
         "role": "market_manager", "employee_id": "M1"}]
    STORE["storeops.tenants"] = [{"org_id": ORG, "id": ORG, "pay_visibility": None}]
    return emp


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. switched OFF reads as OFF, never as a clean board")
seed(enabled=False)
off = api.clock_variances(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("A1 the board says enabled=false rather than returning a bare empty list",
      off["enabled"], False)
check("A2 …and SAYS WHY, in a sentence a reader can act on",
      bool(off["not_enabled_reason"]) and "switched" in off["not_enabled_reason"])
check("A3 the gate is off and says so",
      api.clock_gate(org_id=ORG, authorization="Bearer dm")["gated"], False)
check("A4 …with its own reason, not a silent false",
      "not switched" in (api.clock_gate(org_id=ORG, authorization="Bearer dm")["reason"] or ""))
check("A5 the kind vocabulary is published WITH the board, so a screen renders the house sentences",
      sorted(off["kinds"]) == sorted(_cv.KINDS))
check("A6 the correction path travels with it, so the gate's escape is never hardcoded in the UI",
      off["correction_path"], _cv.CORRECTION_PATH)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("B. switched on: the real 0.28h disagreement, with our basis on the row")
seed(enabled=True)
on = api.clock_variances(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("B1 the board is enabled", on["enabled"], True)
rows = [r for r in on["rows"] if r.get("employee_id") == "E2"]
check("B2 the disagreement is raised exactly once", len(rows), 1)
check("B3 both numbers are on the row",
      (rows[0]["feed_hours"], rows[0]["our_hours"]) if rows else None, (8.9, 9.18))
check("B4 …and it is classified", rows[0]["kind"] if rows else None, _cv.HOURS_DIFFER)
check("B5 OUR BASIS is on the row — the reader is never asked to arbitrate two unexplained figures",
      rows[0]["our_basis"] if rows else None, _hb.CLOCKED)
check("B6 …with the sentence explaining it, not just the slug",
      bool(rows[0].get("our_basis_reason")) if rows else False, True)
check("B7 the person's NAME is resolved, so the DM is not reading employee ids",
      rows[0].get("employee_name") if rows else None, "Junaid shaik")
check("B8 the row blocks", rows[0]["blocks"] if rows else None, True)
check("B9 every store that was looked at appears, including the clean ones (absence ≠ zero)",
      "B-4712" in on["all_clear"])
check("B10 the declared tolerance is published, so the reader knows what was ignored",
      on["tolerance_hours"], 0.25)

# A SETTLED ROW STOPS BLOCKING AND STILL APPEARS.
seed(enabled=True, settled=[{"org_id": ORG, "store_code": "B-4712", "employee_id": "E2",
                             "business_date": DAY, "resolution": "accepted_ours",
                             "override_hours": None}])
cleared = api.clock_variances(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
_c = [r for r in cleared["rows"] if r.get("employee_id") == "E2"]
check("B11 a settled row is reported resolved and stops blocking",
      [(r["resolved"], r["blocks"]) for r in _c], [(True, False)])
check("B12 …and is still ON the board, so 'somebody looked' is visible", len(_c), 1)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE PERIOD IS PAYROLL'S PERIOD — one resolver, proven by calling both")
seed(enabled=True)
want = router_mod._resolve_range(None, DAY, DAY)
check("C1 the board's window IS `_resolve_range`'s answer, not a second calendar",
      (on["period"]["start"], on["period"]["end"]), want)
check("C2 a month spelling resolves the same way payroll resolves it",
      (api.clock_variances(month="2026-10", org_id=ORG, authorization="Bearer dm")["period"]["start"],
       api.clock_variances(month="2026-10", org_id=ORG, authorization="Bearer dm")["period"]["end"]),
      router_mod._resolve_range("2026-10", None, None))
check("C3 a malformed range is refused by the shared resolver, not silently widened",
      _raises(lambda: api.clock_variances(start="not-a-date", end=DAY, org_id=ORG)), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("D. ★ NO LOCKOUT ★ — the gate confines, it never refuses the sign-in")
seed(enabled=True, gate=True)
import datetime as _dt  # noqa: E402

_real_date = _dt.date


class _Tue(_real_date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 13)   # a Tuesday — the house gate weekday


_dt.date = _Tue
try:
    g = api.clock_gate(org_id=ORG, authorization="Bearer dm")
finally:
    _dt.date = _real_date
check("D1 on the gate weekday with something blocking, the DM is gated", g["gated"], True)
check("D2 …and ALERTED", g["alert"], True)
check("D3 ★ sign-in itself is always in the allowed paths — this is a confinement, not a lockout",
      all(any(p.startswith(a) for a in g["allowed_paths"]) for p in ("/login", "/logout")), True)
check("D4 …and the correction board is reachable", _cv.CORRECTION_PATH in g["allowed_paths"], True)
check("D5 the reason names what to do, not merely that they are blocked",
      "sign in" in (g["reason"] or "") and "correction" in (g["reason"] or ""), True)

# A GATE THAT CANNOT LOOK DOES NOT GATE. Failing closed here is the property failing in the exact
# direction it exists to prevent: a DM locked out of the app by a database hiccup.
seed(enabled=True, gate=True)
_broken = FakeClient({})


def _explode(*a, **k):
    raise RuntimeError("the board could not be read")


_saved = api._assemble
api._assemble = _explode
try:
    gb = api.clock_gate(org_id=ORG, authorization="Bearer dm")
finally:
    api._assemble = _saved
check("D6 when the board cannot be read, NO gate is applied", gb["gated"], False)
check("D7 …and the inability is REPORTED rather than swallowed",
      "could not be read" in (gb["reason"] or ""), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E. one human decision, and the override that must carry a reason")
seed(enabled=True)
check("E1 an override with NO reason is refused",
      _status(lambda: api.resolve_variance(
          body={"store_code": "B-4712", "employee_id": "E2", "business_date": DAY,
                "resolution": "corrected", "override_hours": 8.5},
          authorization="Bearer dm", org_id=ORG)), 400)
check("E2 …and the refusal explains WHY, in terms of the consequence",
      "pay" in _message(lambda: api.resolve_variance(
          body={"store_code": "B-4712", "employee_id": "E2", "business_date": DAY,
                "resolution": "corrected", "override_hours": 8.5},
          authorization="Bearer dm", org_id=ORG)).lower(), True)
check("E3 an unknown resolution is refused rather than stored",
      _status(lambda: api.resolve_variance(
          body={"store_code": "B-4712", "employee_id": "E2", "business_date": DAY,
                "resolution": "whatever"}, authorization="Bearer dm", org_id=ORG)), 400)
check("E4 an override above a day's worth of hours is refused",
      _status(lambda: api.resolve_variance(
          body={"store_code": "B-4712", "employee_id": "E2", "business_date": DAY,
                "resolution": "corrected", "override_hours": 30, "override_reason": "x"},
          authorization="Bearer dm", org_id=ORG)), 400)

seed(enabled=True)
saved = api.resolve_variance(
    body={"store_code": "B-4712", "employee_id": "E2", "business_date": DAY,
          "resolution": "corrected", "override_hours": 8.5,
          "override_reason": "stayed for the cash count", "our_hours": 9.18,
          "start": DAY, "end": DAY},
    authorization="Bearer dm", org_id=ORG)
check("E5 a complete override is stored, with the reason and the person", 
      (saved["override_hours"], saved["override_reason"], bool(saved.get("override_by"))),
      (8.5, "stayed for the cash count", True))
_log = STORE.get("storeops.payroll_change_log", [])
check("E6 …and it reaches the payroll change log through the ONE writer",
      [(r.get("entry_point"), r.get("field"), r.get("after_value")) for r in _log],
      [(_cv.OVERRIDE_ENTRY_POINT, _cv.OVERRIDE_FIELD, "8.5")])
check("E7 …carrying the DM's reason, so the audit row explains itself",
      [r.get("reason") for r in _log], ["stayed for the cash count"])

# THE OVERRIDE OUT-RANKS THE RULE, end to end through the board.
seed(enabled=True, pay_source=True,
     settled=[{"org_id": ORG, "store_code": "B-4712", "employee_id": "E2",
               "business_date": DAY, "resolution": "corrected", "override_hours": 8.5,
               "override_reason": "cash count", "override_by": "dm@example.com"}])
ov = api.clock_variances(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
_r = [r for r in ov["rows"] if r.get("employee_id") == "E2"]
check("E8 with an override on file, OUR hours on the board are the DM's figure",
      [r["our_hours"] for r in _r] or ["(no row — agreement)"], [8.5])
check("E9 …on the override basis, which out-ranks the org's clock-source rule",
      [r["our_basis"] for r in _r] or [_hb.CLOCKED_OVERRIDE], [_hb.CLOCKED_OVERRIDE])


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("F. login links — a proposal is never an attribution")
seed(enabled=True)
STORE["storeops.raw_clock_punch"].append({
    "org_id": ORG, "employee_raw": "Nobody, Here", "feed_login": "zzz",
    "store_code": "B-4712", "business_date": DAY, "hours": 5.0, "excluded": None})
props = api.clock_link_proposals(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("F1 the unknown login is listed as unmatched, with its hours",
      [(u["feed_login"], u["hours"]) for u in props["unmatched"]], [("zzz", 5.0)])
check("F1b …and with the sentence saying why nothing bound, not a bare slug",
      all(u.get("reason") for u in props["unmatched"]), True)
check("F2 the known login is reported as already bound",
      [b["feed_login"] for b in props["bound"]], ["Junaid"])
from app.modules.storeops import payroll_identity as _pid  # noqa: E402
check("F3 every match basis is published WITH its sentence, so a screen renders the house wording",
      sorted(props["reasons"]) == sorted(_pid.ALL_MATCH_BASES), True)

d = api.decide_clock_link(body={"feed_login": "zzz", "employee_id": "E2", "status": "approved"},
                          authorization="Bearer dm", org_id=ORG)
check("F4 an approval is stored as approved, with who approved it",
      (d["status"], bool(d.get("approved_by"))), ("approved", True))
check("F5 a second decision on the SAME login UPDATES rather than inserting a rival row",
      len(api.decide_clock_link(
          body={"feed_login": "ZZZ", "employee_id": "E2", "status": "rejected"},
          authorization="Bearer dm", org_id=ORG) and STORE["storeops.clock_feed_login_link"]), 1)
check("F6 …case-insensitively, because 'zzz' and 'ZZZ' are one login",
      STORE["storeops.clock_feed_login_link"][0]["status"], "rejected")
check("F7 an unknown status is refused",
      _status(lambda: api.decide_clock_link(
          body={"feed_login": "zzz", "employee_id": "E2", "status": "maybe"},
          authorization="Bearer dm", org_id=ORG)), 400)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the payroll run preflight — refuses, and NAMES what blocks it")
seed(enabled=True, run_gate=True)
pf = api.run_preflight(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("G1 the run is refused while a variance is uncorrected", pf["refused"], True)
check("G2 …and the reason names the count, not merely 'blocked'",
      "variance" in (pf["reason"] or "").lower(), True)
check("G3 the blocking rows travel with the verdict", bool(pf.get("blocking")), True)
check("G4 the coverage figure and the interlock ride the SAME preflight — one question, one answer",
      ("coverage" in pf and "interlock" in pf), True)

seed(enabled=True, run_gate=False, no_rate_extra=13)
pf2 = api.run_preflight(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("G5 a rate-less employee refuses the run EVEN WITH THE VARIANCE GATE OFF",
      pf2["refused"], True)
check("G5b …and names every one of them, so the refusal is a list of people not a number",
      len(pf2.get("employees_without_rate") or []), 13)
# THE OTHER DIRECTION, and it is why the gate narrows: a DORMANT record with no rate must not block
# a store's payroll. Thirteen rate-less employees who are not rostered are not this run's problem.
seed(enabled=True, run_gate=False)
STORE["storeops.employees"] += [
    {"id": f"e-d{i}", "org_id": ORG, "employee_id": f"E8{i}", "name": f"Dormant {i}",
     "is_active": True, "pay_basis": "hourly", "pay_rate": None} for i in range(13)]
check("G5c a rate-less employee this run would NOT pay does not block it",
      api.run_preflight(start=DAY, end=DAY, org_id=ORG,
                        authorization="Bearer dm")["refused"], False)
check("G6 …and the refusal says they would be priced at nothing",
      "$0.00" in (pf2["reason"] or ""), True)

seed(enabled=True, run_gate=True, feed_hours=9.18)
pf3 = api.run_preflight(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")
check("G7 with the clocks agreeing and every rate on file, the run proceeds",
      pf3["refused"], False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("H. the per-employee TICK BOX")
emp = seed(enabled=False)
# THE BOX IS GATED WITH THE MONEY, and that is the owner's own rule: ticking it decides whether
# somebody is PAID for a day nobody measured, which is a pay decision and not a schedule preference.
# A DM may view and adjust HOURS all day; they may not decide that an unmeasured day pays nothing.
check("H0 a caller below the pay-visibility level is refused, in words they can act on",
      _status(lambda: router_mod.set_pay_from_schedule(
          "E2", body={"pay_from_schedule": False}, authorization="Bearer dm", org_id=ORG)), 403)
check("H0b …and the refusal says who to ask rather than just 'forbidden'",
      "administers roles" in _message(lambda: router_mod.set_pay_from_schedule(
          "E2", body={"pay_from_schedule": False}, authorization="Bearer dm", org_id=ORG)), True)
res = router_mod.set_pay_from_schedule("E2", body={"pay_from_schedule": False},
                                       authorization="Bearer mm", org_id=ORG)
check("H1 unticking is stored", res["pay_from_schedule"], False)
check("H2 …and the effective answer flips with it", res["effective"], False)
check("H3 …and the basis an unmeasured day would now get is NAMED",
      res["basis_if_unmeasured"], _hb.NOT_MEASURED_UNPAID)
check("H4 …with the sentence, so the person ticking the box knows what it does",
      bool(res["reason"]), True)
check("H5 the change reaches the payroll change log under its own entry point",
      [(r.get("entry_point"), r.get("field"))
       for r in STORE.get("storeops.payroll_change_log", [])],
      [(_hb.CHANGE_ENTRY_POINT, _hb.CHANGE_FIELD)])
seed(enabled=False)
res2 = router_mod.set_pay_from_schedule("E2", body={"pay_from_schedule": None},
                                        authorization="Bearer mm", org_id=ORG)
check("H6 NULL is a real value meaning 'use the org default', not 'unknown' and not false",
      (res2["pay_from_schedule"], res2["effective"], res2["org_default"]), (None, True, True))
check("H7 an absent field is refused rather than guessed",
      _status(lambda: router_mod.set_pay_from_schedule(
          "E2", body={}, authorization="Bearer mm", org_id=ORG)), 400)
check("H8 an unknown employee is a clean 404, not a 500",
      _status(lambda: router_mod.set_pay_from_schedule(
          "NOPE", body={"pay_from_schedule": True}, authorization="Bearer mm", org_id=ORG)), 404)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("I. THE MONDAY ASK — off is off, the weekday is the BUSINESS weekday, and an unowned "
        "store is named")
import asyncio as _aio

seed(enabled=False)
_off = _aio.run(api.clock_variance_ask(start=DAY, end=DAY, org_id=ORG))
check("I1 with the ask switched off nobody is asked", (_off["sent"], _off["skipped"]), (0, 0))
check("I2 …and it says so rather than reporting a clean week",
      _off["enabled"] is False and "switched off" in _off["reason"])
check("I3 nothing was read either — the reason says that too",
      "nothing was sent" in _off["reason"].lower())

seed(enabled=True)
# 2026-10-09 is a FRIDAY and this org asks on weekday 0. A sweep run on the wrong day must say
# which day it is and which day the org asks on — "nothing sent" with no reason is the shape that
# gets debugged by reading code.
_wrong = _aio.run(api.clock_variance_ask(start=DAY, end=DAY, org_id=ORG))
if _wrong["weekday"] != 0:
    check("I4 a non-ask weekday sends nothing", (_wrong["sent"], _wrong["skipped"]), (0, 0))
    check("I5 …and names both weekdays, so the operator can tell a holiday from a bug",
          f"weekday {_wrong['weekday']}" in _wrong["reason"]
          and "weekday 0" in _wrong["reason"])
else:
    check("I4 a non-ask weekday sends nothing (today IS the ask day — skipped)", True)
    check("I5 …and names both weekdays (today IS the ask day — skipped)", True)

_now = _aio.run(api.clock_variance_ask(start=DAY, end=DAY, org_id=ORG, respect_weekday=False))
check("I6 respect_weekday=false overrides the DAY, never the enable switch", _now["enabled"], True)
# The org tree is not wired in this fixture, which is the MEASURED production shape: 6 of 29
# Cellfonz stores and 0 of 20 Luxelink stores resolve a district manager. The ask must therefore
# report the stores it cannot address — an ask that silently drops them looks like it asked
# everybody, which is the exact failure this ladder exists to prevent.
check("I7 a store nobody owns is NAMED, not dropped",
      [u["store_code"] for u in _now["unowned_stores"]], ["B-4712"])
check("I8 …and the coverage figures travel with the answer, so the gap is countable",
      sorted(_now["coverage"]),
      ["levels", "store_assignments", "stores_unowned", "stores_with_blocking"])
check("I9 the owner's REASON travels with the ask, in his own terms",
      "time in the store, not time worked" in _now["why"])
# A sweep whose first run sends real email because a parameter defaulted the wrong way is not
# recoverable. The default is proven from the SIGNATURE, not from a call.
import inspect as _insp
check("I10 dry_run DEFAULTS TO TRUE on the shipped signature",
      _insp.signature(api.clock_variance_ask).parameters["dry_run"].default, True)
check("I11 a dry run carries nothing, so it counts nothing", _now["sent"], 0)
# THE SEND RECORD IS NOT THIS ENDPOINT'S TO KEEP. It routes through the one ladder, so a second
# "already asked" fact cannot appear here (index §15.1/§15.2, locked by the module graph).
_src = open(os.path.join(HERE, "app/modules/storeops/clock_variance_api.py")).read()
check("I12 the endpoint holds no mail call of its own",
      ("send_email" in _src or "send_document" in _src), False)
check("I13 …and writes no alert_log row of its own",
      ("alert_log" in _src and "record_sent" in _src), False)
check("I14 …it dereferences the delivery ladder instead", "deliver_digests" in _src)
# THE CRON ENTRYPOINT. An unauthenticated sweep that sends email is a mail relay, so the secret
# guard is proven here and not assumed — the same `NOTIFY_RUN_SECRET` guard the rest of the estate's
# run-due sweeps carry.
check("I15 the cron entrypoint refuses without the run secret",
      _status(lambda: _aio.run(api.clock_variance_ask_run_due(x_notify_secret=""))), 403)
check("I16 …and with a wrong one",
      _status(lambda: _aio.run(api.clock_variance_ask_run_due(x_notify_secret="nope"))), 403)
# ONE JOB, EVERY TENANT, EACH IN ITS OWN TIMEZONE. The sweep must not carry a weekday of its own:
# a server-weekday job fires on Sunday evening for a behind-UTC store.
check("I17 the sweep reads the weekday per ORG and spells none itself",
      ("weekday() ==" in _src or "== 0" in _src.split("run_due")[-1]), False)
check("I18 …it defaults to a REAL send, because a cron job that only ever previews asks nobody",
      _insp.signature(api.clock_variance_ask_run_due).parameters["dry_run"].default, False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("Z. ARMED negative control")
_f0 = len(FAIL)
seed(enabled=False)
check("Z-armed a disabled board reports enabled=true",
      api.clock_variances(start=DAY, end=DAY, org_id=ORG, authorization="Bearer dm")["enabled"],
      True)
check("Z-armed the gate refuses the sign-in path",
      "/login" in api.clock_gate(org_id=ORG, authorization="Bearer dm")["allowed_paths"], False)
_fired = len(FAIL) - _f0
if _fired == 2:
    FAIL[:] = FAIL[:_f0]
    PASS.append("Z1 negative control fired on both wrong expectations (the checks are live)")
else:
    FAIL.append(f"Z1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/2 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
