"""Offline proof harness — a REFUSED daily closing leaves a record, and one dedup-key formula
guards against duplicates (index §29.11). Runs the REAL `closing/router.create_row`,
`closing/router._refuse`, `closing/router.closing_attempts` and the two pure modules against the
stateful fake Supabase client convention of harness_envelope_photo_required_gate.py — no live
database, no network.

Run: `cd backend && python3 harness_closing_submit_refusal.py`

THE DEFECT (owner bug report 2026-10-02, 117 E Burnside Ave): *"abid did the daily closing for the
117 bunrsoide ave … we cannot see the daily closing which was submitted, also he did it again today
it is still not showing"*. Measured read-only in live data first: B-117 had closings on 09-28/29/30
and none on 10-01/02; `closing_attempt` held nothing from that submitter at any store since 09-28;
no DM-verify row either. The closing was never stored — it was REFUSED, and a refusal left no trace,
because `create_row` ran every validation before the first write and the audit trail was written
only once they all passed. That is the class: for any store, any rep, any tenant, "I submitted it"
versus "we see nothing" was unresolvable.

Proves:
  A. PURE — the refusal registry: every code declares a status and submitter-facing words; an
     undeclared code is a hard error at the raise site, not a silent pass; no database/hosting name
     leaks into the words (index §19.38).
  B. PURE — `audit_row`: a refusal is recorded as NOT a try (blocked/accepted/auto_accepted all
     false, `refused` true) and `is_real_try` is the one rule every counter reads.
  C. PURE — `dedup_key`: trimmed store, trimmed+folded employee, and the whitespace divergence that
     migration 502's SQL had against the old inline Python formula is gone.
  D. BEHAVIOURAL — each refusal path through the REAL create_row: nothing is written to
     daily_closing, AND exactly one closing_attempt row carries the reason. This is the regression
     that reproduces the reported defect: before the fix these assertions all found zero rows.
  E. BEHAVIOURAL — a refusal never advances the 3-try close gate. Two refused submits followed by a
     blocking mismatch must still be attempt 1, or a rep whose photo failed twice would reach the
     auto-accepting third try without ever having recounted. (Money-affecting.)
  F. BEHAVIOURAL — GET /closing/attempts shows the refusals: `attempts` counts real tries only,
     `refusals` and `last_refusal_code` are reported, and a store-day whose ONLY events are
     refusals still qualifies for `only_review=true` — that store-day is precisely what management
     needs to see.
  G. DEGRADE pre-migration-1035 — the refusal columns do not exist yet: the refusal is still
     recorded (without its reason columns) rather than lost, and the submit still refuses cleanly.
  H. WIRING LOCKS — these FAIL THE BUILD if the design un-wires (the house's "lock it so it cannot
     un-wire" rule): no submit validation may raise HTTPException directly, every code raised must
     be declared, the router must not spell a dedup key inline, the attempt counter must dereference
     `_real_attempt_count`, and migration 1035's SQL must be `dedup_key.SQL_EXPR` verbatim.
"""
import sys
import os
import re
import ast
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (copied convention from harness_eep_retail_ops.py) ────────────────
class Q:
    def __init__(self, store, table, poison_writes=False):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None
        self._order = None
        self._poison = poison_writes

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def upsert(self, rows, **k): self.op = "upsert"; self.payload = rows; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def is_(self, c, v): self.filters.append((c, "is", v)); return self
    def ilike(self, c, v): self.filters.append((c, "ilike", v)); return self
    def order(self, col, desc=False, **k): self._order = (col, desc); return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if kind == "is" and v == "null" and rv is not None: return False
            if kind == "ilike" and str(rv or "").lower() != str(v or "").lower(): return False
        return True

    def execute(self):
        if self._poison and self.op in ("insert", "update", "delete", "upsert"):
            raise AssertionError(f"UNEXPECTED WRITE ({self.op}) on {self.t}")
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._order:
                col, desc = self._order
                matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op in ("insert", "upsert"):
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r)
                if self.op == "upsert" and r.get("id"):
                    existing = next((x for x in rows if x.get("id") == r["id"]), None)
                    if existing:
                        existing.update(r); out.append(dict(existing)); continue
                r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            deleted = [r for r in rows if self._match(r)]
            self.s[self.t] = keep
            return SimpleNamespace(data=deleted)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store, poison_writes=False):
        self.store = store
        self.poison_writes = poison_writes

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name, poison_writes=self.poison_writes)


class PoisonTableClient(FakeClient):
    """envelope_payout_config's own select raises (table doesn't exist yet, pre-mig-507) — every
    other table behaves normally."""
    def table(self, name):
        if name == "envelope_payout_config":
            class Boom:
                def select(self, *a, **k): raise Exception('relation "commcalc.envelope_payout_config" does not exist')
            return Boom()
        return super().table(name)


def fresh_store():
    return {"daily_closing": [], "employees": [], "stores": [], "envelope_payout_config": [], "closing_expense": [],
            "envelope_withdrawal": [], "tenants": [], "closing_attempt": []}


import app.modules.core.router as core                # noqa: E402
import app.modules.storeops.router as storeops         # noqa: E402
import app.modules.closing.router as cr                # noqa: E402


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)


def wire(store, unrestricted_span=True, manager=True):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    if unrestricted_span:
        storeops.scope_keyset = lambda auth, org: None
    if manager:
        cr._caller_perms = lambda client, auth: {"__super_admin": True, "__resolved": True}
        cr._caller_email = lambda client, auth: "dm@test.com"
    return fake


import asyncio  # noqa: E402


async def _submit(payload, org_id=HOUSE):
    return await cr.create_row(payload, org_id=org_id)


def base_payload(employee_name, store_code="S1", t_cash="0", envelope_picture=""):
    return {"close_date": "2026-08-07", "store_code": store_code, "store_name": "1 Main St",
            "employee_name": employee_name, "t_cash": t_cash, "t_credit": "0",
            "envelope_picture": envelope_picture}




from app.modules.closing import submit_refusal as sr   # noqa: E402
from app.modules.closing import dedup_key as dk        # noqa: E402
from fastapi import HTTPException                       # noqa: E402

ROUTER_PATH = "app/modules/closing/router.py"
MIG_PATH = "../database/migrations/1035_closing_submit_refusal_audit.sql"
ROUTER_SRC = open(ROUTER_PATH).read()


def refuse(payload, org_id=HOUSE, auth=""):
    """Run the REAL submit and return (http_status, message) of the refusal, or None if accepted."""
    try:
        asyncio.run(cr.create_row(payload, org_id=org_id, authorization=auth))
        return None
    except HTTPException as e:
        return (e.status_code, str(e.detail))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. PURE — the refusal registry
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. the refusal registry: one home, complete, submitter-safe ==")
check("every code declares (status, message)",
      all(isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str) and v[1]
          for v in sr.REFUSALS.values()))
check("every status is a client/server error, never a 2xx",
      all(400 <= s < 600 for s, _ in sr.REFUSALS.values()))
try:
    sr.Refusal("no_such_reason")
    check("an UNDECLARED code is a hard error at the raise site", False)
except KeyError as e:
    check("an UNDECLARED code is a hard error at the raise site", "no_such_reason" in str(e))
try:
    sr.audit_row(HOUSE, "2026-10-01", {}, "no_such_reason")
    check("an UNDECLARED code cannot be audited either", False)
except KeyError:
    check("an UNDECLARED code cannot be audited either", True)
# index §19.38 — no storage/hosting identifier in copy a submitter reads.
_BANNED = ("commcalc", "storeops", "postgres", "supabase", "daily_closing", "closing_attempt",
           "dedup_key", "relation ", "SUPABASE_")
check("no storage or hosting name in any submitter-facing refusal message",
      not [c for c, (_s, m) in sr.REFUSALS.items() if any(b.lower() in m.lower() for b in _BANNED)],
      detail=str([c for c, (_s, m) in sr.REFUSALS.items()
                  if any(b.lower() in m.lower() for b in _BANNED)]))
r = sr.Refusal("duplicate_already_submitted", detail="existing row abc for Rana at B-117 on 2026-10-01")
check("the message is the registry's words; the circumstance rides as management-only detail",
      r.message == sr.message_for("duplicate_already_submitted") and "B-117" in r.detail)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. PURE — a refusal is recorded, and is NOT a try
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. audit_row: recorded as a refusal, never as a try ==")
row = sr.audit_row(HOUSE, "2026-10-01",
                   {"store_code": "B-117", "store_address": "117 E Burnside Ave",
                    "employee_name": "Rana", "sfid": "001U100000Ga3kfIAB"},
                   "envelope_photo_required", detail="declared cash 521.0 with no photo",
                   real_attempts=0, tenders={"cash": 521.0, "credit": 0.0, "ext_cc": 0.0})
check("refused is true, and blocked/accepted/auto_accepted are all false",
      row["refused"] is True and row["blocked"] is False
      and row["accepted"] is False and row["auto_accepted"] is False)
check("the row carries the store, the name and the reason",
      (row["store_code"], row["employee_name"], row["refusal_code"])
      == ("B-117", "Rana", "envelope_photo_required"))
check("period is derived from the close date", row["period"] == "2026-10")
check("the money the submitter declared is kept (the first management question)",
      row["entered_cash"] == 521.0 and row["t_cash"] == 521.0)
check("attempt_no records the REAL tries that preceded it, never an increment", row["attempt_no"] == 0)
check("is_real_try is the one rule: a refusal is not a try",
      sr.is_real_try({"refused": True}) is False)
check("a pre-migration row with no `refused` key IS a real try (which is what it was)",
      sr.is_real_try({"attempt_no": 1}) is True and sr.is_real_try({"refused": False}) is True)
check("a refusal with no amounts yet records none rather than a fabricated zero",
      "t_cash" not in sr.audit_row(HOUSE, "2026-10-01", {}, "bad_close_date"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. PURE — one dedup-key formula
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. dedup_key: one home, and the whitespace divergence is gone ==")
check("the key is org|store|folded-name|date",
      dk.for_row(HOUSE, "B-117", "Rohit", "2026-10-01") == f"{HOUSE}|B-117|rohit|2026-10-01")
check("THE DIVERGENCE: a stray space in the name no longer makes a second key",
      dk.for_row(HOUSE, "B-117", " Rohit ", "2026-10-01")
      == dk.for_row(HOUSE, "B-117", "Rohit", "2026-10-01"))
check("case in the name folds (one person types it two ways)",
      dk.for_row(HOUSE, "B-117", "ROHIT", "2026-10-01")
      == dk.for_row(HOUSE, "B-117", "rohit", "2026-10-01"))
check("case in the STORE CODE does not fold (a code is an assigned identifier)",
      dk.for_row(HOUSE, "b-117", "Rohit", "2026-10-01")
      != dk.for_row(HOUSE, "B-117", "Rohit", "2026-10-01"))
check("two reps at one store on one day are two keys (one row per rep per day, by design)",
      dk.for_row(HOUSE, "B-117", "Rohit", "2026-10-01")
      != dk.for_row(HOUSE, "B-117", "Kashif", "2026-10-01"))
check("'Rohit' and 'Rohit Kumar' stay DIFFERENT (converging rep identity is a roster question)",
      dk.for_row(HOUSE, "B-117", "Rohit", "2026-10-01")
      != dk.for_row(HOUSE, "B-117", "Rohit Kumar", "2026-10-01"))
check("no store or no name -> no key, and not dedupable",
      dk.for_row(HOUSE, "", "Rohit", "2026-10-01") == ""
      and dk.for_row(HOUSE, "B-117", "   ", "2026-10-01") == ""
      and not dk.dedupable("", "Rohit") and not dk.dedupable("B-117", " "))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. BEHAVIOURAL — every refusal path: nothing stored as a closing, exactly one audited reason.
#    THIS IS THE REGRESSION that reproduces the reported defect: before the fix, each of these
#    assertions found ZERO closing_attempt rows — the refusal simply vanished.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the REGRESSION: a refused closing is recorded instead of vanishing ==")


def one_refusal(label, payload, expect_code, expect_status, seed=None, auth="", perms=None):
    store = fresh_store()
    fake = wire(store)
    if perms is not None:
        cr._caller_perms = lambda client, a: perms
    for tbl, rows in (seed or {}).items():
        store.setdefault(tbl, []).extend(rows)
    before_closings = len(store["daily_closing"])
    got = refuse(payload, auth=auth)
    att = [a for a in store["closing_attempt"] if a.get("refused")]
    check(f"{label} -> refused {expect_status}",
          bool(got) and got[0] == expect_status, detail=str(got))
    check(f"{label} -> the submitter reads the registry's words",
          bool(got) and got[1] == sr.message_for(expect_code), detail=str(got))
    check(f"{label} -> NO closing stored", len(store["daily_closing"]) == before_closings,
          detail=str(store["daily_closing"]))
    check(f"{label} -> exactly ONE recorded refusal, carrying the reason",
          len(att) == 1 and att[0].get("refusal_code") == expect_code,
          detail=str(store["closing_attempt"]))
    check(f"{label} -> the recorded refusal is not counted as a try",
          bool(att) and not sr.is_real_try(att[0]))
    return store


# D1 — the photo-required gate: the likeliest refusal behind the reported Burnside closing.
one_refusal("photo required (cash declared, upload never landed)",
            {**base_payload("Rana", store_code="B-117", t_cash="521", envelope_picture=""),
             "close_date": "2026-10-01"},
            "envelope_photo_required", 400,
            seed={"envelope_payout_config": [{"org_id": HOUSE, "store_code": None,
                                              "require_photo_if_cash": True}]})

# D2 — already submitted for the day (the duplicate guard doing its job, now visibly).
one_refusal("already submitted for the day",
            {**base_payload("Rana", store_code="B-117", t_cash="100"), "close_date": "2026-10-01"},
            "duplicate_already_submitted", 409,
            seed={"daily_closing": [{"id": "dc-1", "org_id": HOUSE, "close_date": "2026-10-01",
                                     "store_code": "B-117", "employee_name": "Rana",
                                     "released_at": None, "submitted_at": "2026-10-01T23:00:00Z"}]})

# D3 — more than one existing row (the double-submit fingerprint).
one_refusal("more than one existing closing for the rep-day",
            {**base_payload("Rana", store_code="B-117", t_cash="100"), "close_date": "2026-10-01"},
            "duplicate_multiple", 409,
            seed={"daily_closing": [
                {"id": "dc-1", "org_id": HOUSE, "close_date": "2026-10-01", "store_code": "B-117",
                 "employee_name": "Rana", "released_at": None, "submitted_at": "2026-10-01T22:00:00Z"},
                {"id": "dc-2", "org_id": HOUSE, "close_date": "2026-10-01", "store_code": "B-117",
                 "employee_name": "Rana", "released_at": None, "submitted_at": "2026-10-01T23:00:00Z"}]})

# D4 — an unparseable close_date has no date of its own, so the refusal is filed on the day it
#      happened rather than lost.
store = fresh_store()
wire(store)
got = refuse({**base_payload("Rana", store_code="B-117"), "close_date": "not-a-date"})
check("unparseable close_date -> refused 400", bool(got) and got[0] == 400, detail=str(got))
check("unparseable close_date -> the refusal is still recorded (filed on the business day)",
      len([a for a in store["closing_attempt"] if a.get("refused")]) == 1
      and store["closing_attempt"][0].get("close_date"))
check("unparseable close_date -> the value sent rides as management detail",
      "not-a-date" in str(store["closing_attempt"][0].get("refusal_detail")))

# D5 — identity: a closing with no store cannot be deduped, so it is refused rather than written
#      unprotected (which is what the old `if store and emp:` guard did).
one_refusal("no store on the submit", {**base_payload("Rana", store_code="", t_cash="100")},
            "identity_missing", 400)
one_refusal("no employee name on the submit", {**base_payload("", store_code="B-117", t_cash="100")},
            "identity_missing", 400)

# D6 — the closer gate, both of its refusals (index §29.7). A caller with a name on file who
#      submits under SOMEBODY ELSE's is told to use their own; a caller with NO name on file is told
#      what an admin must fix — two different refusals, which the old single 403 message conflated.
import app.core.tenant_middleware as _tm                                        # noqa: E402
core._uid_from_token = lambda a: "uid-1"
cr._uid_from_token = getattr(cr, "_uid_from_token", None)
_tm.caller_app_user = lambda uid, cols="": {"org_id": HOUSE, "full_name": "Rana",
                                            "employee_id": "E012"}
one_refusal("a named caller submitting under another person's name",
            {**base_payload("Kashif", store_code="B-117", t_cash="100")},
            "closer_not_permitted", 403, auth="Bearer t",
            perms={"__resolved": True, "scope": "store"},
            seed={"employees": [{"org_id": HOUSE, "employee_id": "E012", "name": "Rana"}]})

_tm.caller_app_user = lambda uid, cols="": {"org_id": HOUSE, "full_name": "", "employee_id": ""}
one_refusal("a caller with no name on file",
            {**base_payload("Rana", store_code="B-117", t_cash="100")},
            "closer_no_name", 403, auth="Bearer t",
            perms={"__resolved": True, "scope": "store"})

# A caller whose own name IS the submitted one passes the gate (the gate must not block the normal
# case it exists to allow).
_tm.caller_app_user = lambda uid, cols="": {"org_id": HOUSE, "full_name": "Rana",
                                            "employee_id": "E012"}
store = fresh_store()
wire(store)
cr._caller_perms = lambda client, a: {"__resolved": True, "scope": "store"}
store["employees"].append({"org_id": HOUSE, "employee_id": "E012", "name": "Rana"})
ok = asyncio.run(cr.create_row({**base_payload("Rana", store_code="B-117", t_cash="100"),
                                "close_date": "2026-10-01"}, org_id=HOUSE, authorization="Bearer t"))
check("a store-scope caller submitting under their OWN name is accepted", ok.get("accepted") is True)
check("...and no refusal is recorded for it",
      not [a for a in store["closing_attempt"] if a.get("refused")])
cr._caller_perms = lambda client, a: {"__super_admin": True, "__resolved": True}

# D7 — the expense description rule.
one_refusal("expense amount with no description",
            {**base_payload("Rana", store_code="B-117", t_cash="100"),
             "expense_amount": "40", "expense_description": ""},
            "expense_description_required", 400)

# D8 — a clean submit must be UNTOUCHED by all of this: it is accepted, stored, and records a real
#      try, not a refusal.
store = fresh_store()
wire(store)
ok = asyncio.run(cr.create_row({**base_payload("Rana", store_code="B-117", t_cash="100"),
                                "close_date": "2026-10-01"}, org_id=HOUSE))
check("a clean submit is still accepted", ok.get("accepted") is True)
check("a clean submit stores the closing", len(store["daily_closing"]) == 1)
check("a clean submit carries the one-home dedup key",
      store["daily_closing"][0].get("dedup_key")
      == dk.for_row(HOUSE, "B-117", "Rana", "2026-10-01"))
check("a clean submit records a REAL try, never a refusal",
      len(store["closing_attempt"]) == 1
      and sr.is_real_try(store["closing_attempt"][0])
      and store["closing_attempt"][0].get("attempt_no") == 1)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. BEHAVIOURAL, MONEY-AFFECTING — a refusal must never advance the 3-try close gate.
#    The gate blocks a cash-short closing twice and AUTO-ACCEPTS the third try (flagged for review).
#    If a refused submit counted as a try, a rep whose envelope photo failed twice would reach that
#    auto-accept on their FIRST actual count, with a real cash variance waved through.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. a refusal does not buy a try (the auto-accept must still be earned) ==")
store = fresh_store()
fake = wire(store)
store["envelope_payout_config"].append({"org_id": HOUSE, "store_code": None,
                                        "require_photo_if_cash": True})
# Two refusals: the photo never landed, twice.
for _ in range(2):
    refuse({**base_payload("Rana", store_code="B-117", t_cash="300", envelope_picture=""),
            "close_date": "2026-10-01"})
refusals = [a for a in store["closing_attempt"] if a.get("refused")]
check("two refused submits are both recorded", len(refusals) == 2)
check("neither is a try", all(not sr.is_real_try(a) for a in refusals))
check("_real_attempt_count still reads ZERO real tries",
      cr._real_attempt_count(fake, HOUSE, "2026-10-01", "B-117", "Rana") == 0)
# Now a real count, WITH a photo, against a POS day that makes it cash-short (so the gate blocks).
cr._gate_row = lambda *a, **k: {"status": "blocked", "block_reasons": ["cash short"], "flags": [],
                                "b2b": {"cash": 900.0, "card": 0.0}}
cr._money_issues = lambda dc, dcr, bc, bcard, tol=1.0, pos="": [
    {"severity": "block", "reason": "cash short", "metric": "cash", "variance": -600.0}]
resp = asyncio.run(cr.create_row({**base_payload("Rana", store_code="B-117", t_cash="300",
                                                 envelope_picture="env/x.jpg"),
                                  "close_date": "2026-10-01"}, org_id=HOUSE))
real = [a for a in store["closing_attempt"] if sr.is_real_try(a)]
check("THE MONEY POINT: the first real count after two refusals is attempt 1, not attempt 3",
      len(real) == 1 and real[0].get("attempt_no") == 1, detail=str([a.get("attempt_no") for a in real]))
check("...so it is still blocked for a recount rather than auto-accepted",
      resp.get("accepted") is False and not store["daily_closing"],
      detail=str(resp))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. BEHAVIOURAL — Management Review SEES the refusals (the half the owner actually reads)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. GET /closing/attempts reports the refusals ==")
store = fresh_store()
fake = wire(store)
store["envelope_payout_config"].append({"org_id": HOUSE, "store_code": None,
                                        "require_photo_if_cash": True})
for _ in range(2):
    refuse({**base_payload("Rana", store_code="B-117", t_cash="521", envelope_picture=""),
            "close_date": "2026-10-01"})
res = cr.closing_attempts(date="2026-10-01", org_id=HOUSE, authorization="")
grp = next((g for g in res["groups"] if g["store_code"] == "B-117"), None)
check("the refused store-day appears at all", grp is not None, detail=str(res["groups"]))
check("`attempts` counts REAL tries only — zero here, because nothing was ever counted",
      grp and grp["attempts"] == 0, detail=str(grp))
check("`refusals` reports how many submits were turned away", grp and grp["refusals"] == 2)
check("the latest reason is named", grp and grp["last_refusal_code"] == "envelope_photo_required")
check("the detail that answers 'why' is carried",
      grp and "521" in str(grp["last_refusal_detail"]))
check("each try row says whether it was refused",
      grp and all(t["refused"] for t in grp["tries"]))
only = cr.closing_attempts(date="2026-10-01", only_review=True, org_id=HOUSE, authorization="")
check("a store-day whose ONLY events are refusals still qualifies for review",
      any(g["store_code"] == "B-117" for g in only["groups"]), detail=str(only["groups"]))
# A clean accepted closing must NOT be dragged into only_review by this change.
store2 = fresh_store()
wire(store2)
asyncio.run(cr.create_row({**base_payload("Kashif", store_code="B-118", t_cash="100"),
                           "close_date": "2026-10-01"}, org_id=HOUSE))
only2 = cr.closing_attempts(date="2026-10-01", only_review=True, org_id=HOUSE, authorization="")
check("a clean single-try closing is still NOT in the review list", not only2["groups"],
      detail=str(only2["groups"]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# G. DEGRADE — migration 1035 not run yet: the refusal columns do not exist
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. pre-migration-1035: the refusal is recorded without its reason columns, never lost ==")


class PreMigClient(FakeClient):
    """closing_attempt rejects any insert carrying the migration-1035 columns, exactly as PostgREST
    does before the migration is applied; the retry without them must still land."""
    def table(self, name):
        q = super().table(name)
        if name == "closing_attempt":
            real_insert = q.insert

            def insert(rows, **k):
                payload = rows if isinstance(rows, list) else [rows]
                if any(c in (payload[0] or {}) for c in sr.REFUSED_COLUMNS):
                    raise Exception("column \"refused\" of relation does not exist")
                return real_insert(rows, **k)
            q.insert = insert
        return q


store = fresh_store()
fake = PreMigClient(store)
cr.sb = lambda: fake
cr.get_supabase = lambda: fake
core.get_supabase = lambda: fake
storeops.scope_keyset = lambda auth, org: None
cr._caller_perms = lambda client, auth: {"__super_admin": True, "__resolved": True}
store["envelope_payout_config"].append({"org_id": HOUSE, "store_code": None,
                                        "require_photo_if_cash": True})
got = refuse({**base_payload("Rana", store_code="B-117", t_cash="521", envelope_picture=""),
              "close_date": "2026-10-01"})
check("the submit still refuses cleanly", bool(got) and got[0] == 400, detail=str(got))
check("the refusal is STILL recorded (one row, reason columns dropped)",
      len(store["closing_attempt"]) == 1
      and not any(c in store["closing_attempt"][0] for c in sr.REFUSED_COLUMNS),
      detail=str(store["closing_attempt"]))
check("...and it reads as a real try pre-migration, which is the honest pre-migration meaning",
      sr.is_real_try(store["closing_attempt"][0]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# H. WIRING LOCKS — "lock it so it cannot un-wire" (owner directive 2026-09-20). Each of these FAILS
#    THE BUILD, because the registry has been written before without the callers being wired to it.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. the locks: the design cannot quietly un-wire ==")
_tree = ast.parse(ROUTER_SRC)
_create = next(n for n in ast.walk(_tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "create_row")
_lines = ROUTER_SRC.splitlines()

# H1 — no submit validation may raise its own HTTPException: that is how a refusal goes unrecorded.
_stray = [(n.lineno, _lines[n.lineno - 1].strip()[:80]) for n in ast.walk(_create)
          if isinstance(n, ast.Raise)]
check("H1 create_row raises NO HTTPException of its own — every refusal goes through _refuse",
      not _stray, detail=str(_stray))

# H2 — every code the router raises is declared in the registry (and nothing raises a computed
#      string the registry cannot vouch for, other than the closer gate's two declared codes).
_refuse_calls = [n for n in ast.walk(_create)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_refuse"]
check("H2a create_row has a _refuse call for every refusal path", len(_refuse_calls) >= 9,
      detail=str(len(_refuse_calls)))
_literal_codes, _dynamic = set(), 0
for c in _refuse_calls:
    code_arg = c.args[4] if len(c.args) > 4 else None
    if isinstance(code_arg, ast.Constant) and isinstance(code_arg.value, str):
        _literal_codes.add(code_arg.value)
    else:
        _dynamic += 1
_undeclared = sorted(_literal_codes - set(sr.REFUSALS))
check("H2b every code raised is DECLARED in submit_refusal.REFUSALS", not _undeclared,
      detail=str(_undeclared))
_gate_fn = next(n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_closer_gate")
_gate_codes = {n.value for n in ast.walk(_gate_fn)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value in sr.REFUSALS}
check("H2c the one dynamic code comes from _closer_gate, whose codes are declared too",
      _dynamic <= 1 and _gate_codes == {"closer_no_name", "closer_not_permitted"},
      detail=f"dynamic={_dynamic} gate={sorted(_gate_codes)}")
check("H2d the registry declares nothing dead — every code is raised somewhere",
      not sorted(set(sr.REFUSALS) - _literal_codes - _gate_codes),
      detail=str(sorted(set(sr.REFUSALS) - _literal_codes - _gate_codes)))

# H3 — the dedup key is dereferenced, never re-spelled. A second copy is what drifted before.
_inline = [(i + 1, ln.strip()[:90]) for i, ln in enumerate(_lines)
           if "dedup_key" in ln and "|" in ln and "_dedup." not in ln and not ln.strip().startswith("#")]
check("H3a the router spells NO dedup key of its own", not _inline, detail=str(_inline))
check("H3b the router dereferences dedup_key.for_row", "_dedup.for_row(" in ROUTER_SRC)
check("H3c the router asks dedup_key whether a submit can be deduped at all",
      "_dedup.dedupable(" in ROUTER_SRC)

_att_fn = next(n for n in ast.walk(_tree)
               if isinstance(n, ast.FunctionDef) and n.name == "closing_attempts")
# H4 — the attempt counter dereferences the one rule, instead of counting rows itself.
check("H4a create_row derives attempt_no from _real_attempt_count",
      "_real_attempt_count(client, org_id, d," in ROUTER_SRC)
_raw_count = [(i + 1, ln.strip()[:90]) for i, ln in enumerate(_lines)
              if 'table("closing_attempt")' in ln and "select" in ln]
_count_fn = next(n for n in ast.walk(_tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_real_attempt_count")
_count_span = range(_count_fn.lineno, (_count_fn.end_lineno or _count_fn.lineno) + 1)
_log_fn = next(n for n in ast.walk(_tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_log_attempt")
# `_log_attempt` writes, and `closing_attempts` REPORTS (and does its own separation, locked by H5).
# Any OTHER reader of the table is a second way of counting tries, which is what H4 forbids.
_allowed_spans = [_count_span, range(_log_fn.lineno, (_log_fn.end_lineno or _log_fn.lineno) + 1),
                  range(_att_fn.lineno, (_att_fn.end_lineno or _att_fn.lineno) + 1)]
_outside = [x for x in _raw_count if not any(x[0] in sp for sp in _allowed_spans)]
check("H4b nothing outside the counter, the writer and the report reads closing_attempt",
      not _outside, detail=str(_outside))
check("H4c _real_attempt_count dereferences submit_refusal.is_real_try",
      "_refusal.is_real_try" in "\n".join(_lines[_count_fn.lineno - 1:_count_fn.end_lineno]))

# H5 — the attempts endpoint must filter refusals out of the try count it reports.
_att_src = "\n".join(_lines[_att_fn.lineno - 1:_att_fn.end_lineno])
check("H5 GET /closing/attempts separates refusals from tries through the one rule",
      "_refusal.is_real_try" in _att_src and '"refusals"' in _att_src)

# H6 — the SQL half of the dedup key must be the module's own expression, verbatim. Change one and
#      the build fails; that is the only thing stopping the Python/SQL divergence coming back.
_mig = open(MIG_PATH).read() if os.path.exists(MIG_PATH) else ""
check("H6a migration 1035 exists", bool(_mig), detail=MIG_PATH)
check("H6b it contains dedup_key.SQL_EXPR verbatim", dk.SQL_EXPR in _mig)
check("H6c SQL_EXPR and for_row fold the same way (trim store, trim+lower name)",
      "btrim(coalesce(d.store_code,''))" in dk.SQL_EXPR
      and "lower(btrim(coalesce(d.employee_name,'')))" in dk.SQL_EXPR)
# Any OTHER migration that computes a dedup key must be the one that predates this home (502).
_migdir = os.path.dirname(MIG_PATH)
_others = []
for fn in sorted(os.listdir(_migdir)) if os.path.isdir(_migdir) else []:
    if not fn.endswith(".sql") or fn.startswith(("502_", "1035_")):
        continue
    body = open(os.path.join(_migdir, fn)).read()
    if re.search(r"set\s+dedup_key\s*=", body, re.I):
        _others.append(fn)
check("H6d no THIRD migration spells a dedup-key formula", not _others, detail=str(_others))

# H7 — the refusal columns the code writes must be the ones the migration adds.
for col in sr.REFUSED_COLUMNS:
    check(f"H7 migration 1035 adds the column the code writes: {col}",
          f"add column if not exists {col}" in _mig)


# H8 — the screen must have words for every code the server can refuse with. A code with no words
#      renders as a bare identifier to the manager who has to act on it.
_MGMT = "../frontend/src/app/(platform)/closing/management/page.tsx"
_page = open(_MGMT).read() if os.path.exists(_MGMT) else ""
check("H8a the Management Review screen exists and reads the refusal fields", bool(_page)
      and "g.refusals" in _page and "t.refused" in _page, detail=_MGMT)
_worded = set(re.findall(r"^\s{2}(\w+):\s*'", _page, re.M))
_missing = sorted(set(sr.REFUSALS) - _worded)
check("H8b every declared refusal code has manager-facing words on the screen", not _missing,
      detail=str(_missing))
_extra = sorted(_worded - set(sr.REFUSALS) - {"bad_close_date"})
check("H8c the screen words no code the server cannot raise",
      not [c for c in _extra if c in _page.split("REFUSAL_WORDS")[1].split("}")[0]],
      detail=str(_extra))


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
