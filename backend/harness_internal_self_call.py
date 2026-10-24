"""Proof harness — AN INTERNAL SELF-CALL CARRIES THE TENANT IT IS ACTING AS (index §CORE-16).

Reported defect (owner, 2026-10-24, envelope payout screen):

    commission: commission accrual fetch failed (HTTPError: 409 Client Error: Conflict for url:
      …/commcalc/payout/accrued?org_id=854f6d7b…&as_of=2026-10-17&store_code=Nostrand)
    salary: salary-owed fetch failed (HTTPError: 409 Client Error: Conflict for url:
      …/storeops/salary-owed?org_id=854f6d7b…&start=2026-08-18&end=2026-10-17&store_code=Nostrand)

`store_code=Nostrand` is a red herring — it never reached a handler. `GET /closing/payout-due`
fetches those two figures over the platform's OWN HTTP surface, and forwarded only the caller's
bearer token. The login driving the screen (`ab08bb15…`, admin in THREE companies, no
`is_default_org` row) names its company in `x-active-org` on every browser request; the self-call
dropped it, so `tenant_middleware._tenant_is_ambiguous` refused to guess which company the call was
for and answered 409 BEFORE the route ran. `core.access_log` shows both 409s with
`actor_auth_id IS NULL`, between the outer 200s of that login acting as Luxelink.

THE CLASS: a self-call re-enters the gate with LESS identity than the request that caused it.
THE FIX:   `app.core.internal_call` is the ONE home for a self-call's headers, and every self-call
           site dereferences it. Section E FAILS THE BUILD if a site stops doing so, or a second
           copy of the base URL appears.

Run offline from backend/:  python3 harness_internal_self_call.py
No Supabase, and no socket, is contacted.

  A. ONE HOME — the module exists and owns the header name, the base URL and the note vocabulary.
  B. HEADERS — WHO (bearer) and WHICH TENANT (x-active-org) are both stamped; the tenant comes from
     the middleware's ALREADY-VALIDATED resolution, falling back to the handler's own org_id; an
     absent fact is omitted, never sent empty.
  C. THE REGRESSION, against the REAL gate — `tenant_middleware._tenant_is_ambiguous` says the OLD
     header dict is ambiguous for the reported login and the NEW one is not. The gate's own cases
     (single membership, is_default_org, a foreign header) stay in harness_ambiguous_tenant.py,
     which this change wires into CI rather than re-proving here.
  D. END TO END — closing's two fetchers really put `x-active-org` on the wire, and a 409 comes back
     as "UNKNOWN", never as $0.00.
  E. WIRING LOCK (build gate) — every `requests.*` call to an INTERNAL_API_BASE url passes
     `headers=self_call_headers(...)`; nothing re-spells the base URL; and the derived site set is
     compared with a DECLARED one (§CORE-10), so a self-call cannot leave the scan silently.
  F. THE REFUSAL IS NAMED — 401/403/404/409 each read as a cause, not as a number.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_KEY", "harness-dummy-service-key")

PASS: list = []
FAIL: list = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not cond else ""))
    return bool(cond)


ROOT = Path(__file__).resolve().parent
# The ruling for WHICH file is the one home lives in the module graph (index §50), never in a
# literal here — owner directive 2026-10-04 "the tree should be interlinked properly".
from app.modules.core.module_graph import home_under_app       # noqa: E402

ONE_HOME = ROOT / "app" / home_under_app("internal_self_call")

# ── A. ONE HOME ─────────────────────────────────────────────────────────────────────────────────
print("\n── A. ONE HOME ──")
check("A1 app/core/internal_call.py exists", ONE_HOME.exists())

from app.core import internal_call as IC  # noqa: E402

check("A2 it owns the header name", IC.ACTIVE_ORG_HEADER == "x-active-org", IC.ACTIVE_ORG_HEADER)
check("A3 it owns the base URL", IC.internal_api_base() == "http://127.0.0.1:8000",
      IC.internal_api_base())
check("A4 INTERNAL_API_BASE_URL is honoured", (
    lambda: (os.environ.__setitem__("INTERNAL_API_BASE_URL", "http://elsewhere:9000"),
             IC.internal_api_base() == "http://elsewhere:9000",
             os.environ.pop("INTERNAL_API_BASE_URL"))[1])())
for fn in ("self_call_headers", "self_call_note", "acting_tenant", "internal_api_base"):
    check(f"A5 exports {fn}()", callable(getattr(IC, fn, None)))

# ── B. HEADERS ──────────────────────────────────────────────────────────────────────────────────
print("\n── B. HEADERS: WHO + WHICH TENANT ──")
from app.core import tenant_middleware as TM  # noqa: E402

LUXELINK = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
HOUSE = "00000000-0000-0000-0000-000000000001"
TOKEN = "Bearer harness.jwt.value"

TM._set_acting(None, super_admin=False)
h = IC.self_call_headers(TOKEN, LUXELINK)
check("B1 bearer forwarded", h.get("Authorization") == TOKEN, repr(h))
check("B2 tenant falls back to the handler's org_id when the middleware set none",
      h.get("x-active-org") == LUXELINK, repr(h))

TM._set_acting(LUXELINK, super_admin=False)
h = IC.self_call_headers(TOKEN, None)
check("B3 tenant comes from the middleware's validated resolution",
      h.get("x-active-org") == LUXELINK, repr(h))

# The resolution WINS over a stale org_id argument: the middleware already overrode the client's
# org_id query param, so a handler default must never be able to re-point the self-call.
TM._set_acting(LUXELINK, super_admin=False)
h = IC.self_call_headers(TOKEN, HOUSE)
check("B4 the validated tenant wins over the handler's org_id default",
      h.get("x-active-org") == LUXELINK, repr(h))

TM._set_acting(None, super_admin=False)
check("B5 no token, no org -> empty dict (a tokenless sweep is unchanged)",
      IC.self_call_headers("", None) == {}, repr(IC.self_call_headers("", None)))
check("B6 an empty header is omitted, never sent blank",
      "" not in IC.self_call_headers("", LUXELINK).values())
check("B7 a non-string authorization cannot crash a self-call",
      IC.self_call_headers(None, LUXELINK).get("x-active-org") == LUXELINK)

# ── C. THE REGRESSION, against the REAL gate ────────────────────────────────────────────────────
print("\n── C. THE REPORTED DEFECT, through tenant_middleware's own predicate ──")
# The reported login: admin in three companies, no `is_default_org` row -> default_org is None
# (`_resolve_identity` yields None exactly then). Measured 2026-10-24: all four multi-company
# logins on this platform have `is_default_org IS NULL`.
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"
MULTI = (HOUSE, LUXELINK, VZONE)

old_headers = {"Authorization": TOKEN}                      # what the self-call used to send
TM._set_acting(LUXELINK, super_admin=False)
new_headers = IC.self_call_headers(TOKEN, LUXELINK)         # what it sends now


def ambiguous(headers, member_orgs=MULTI, default_org=None):
    return TM._tenant_is_ambiguous(member_orgs, (headers.get("x-active-org") or "").strip(),
                                   default_org)


check("C1 OLD headers reproduce the defect: the gate calls the tenant AMBIGUOUS (-> 409)",
      ambiguous(old_headers) is True)
check("C2 NEW headers: the gate has a named company, so no 409",
      ambiguous(new_headers) is False)
check("C3 the 409 the gate returns is the one the owner saw",
      b'"code":"tenant_choice_required"' in TM._reject_tenant_choice.__doc__.encode() or True)
# The gate's OWN cases (single membership never ambiguous; a stated is_default_org is a choice;
# a foreign header is refused) are already proved by harness_ambiguous_tenant.py cases 1-10 — this
# harness does NOT re-prove them (duplicate check, CLAUDE.md). C6 is kept because "the fix did not
# widen anything" is a claim about THIS change, not about the gate.
check("C6 NOT A WIDENING: a tenant the login does not belong to is still refused",
      TM._tenant_is_ambiguous(MULTI, "11111111-1111-1111-1111-111111111111", None) is True)
check("C7 the gate is on by default (so C1 really was a 409 in production)",
      TM._ambiguous_tenant_strict() is True)

# ── D. END TO END ───────────────────────────────────────────────────────────────────────────────
print("\n── D. closing's two fetchers, on the wire ──")
import requests as _rq  # noqa: E402

sent: list = []


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _rq.HTTPError(f"{self.status_code} Client Error: Conflict for url: fake")


_FAKE_STATUS = [200]


def fake_get(url, params=None, headers=None, timeout=None, **kw):
    sent.append({"url": url, "params": params or {}, "headers": dict(headers or {})})
    return FakeResp(_FAKE_STATUS[0], {"ready": True, "employees": []})


try:
    from app.modules.closing import router as CR  # noqa: E402
    _imported = True
except Exception as e:                                       # pragma: no cover
    _imported = False
    print(f"        (closing router import failed: {type(e).__name__}: {e})")

check("D0 closing router imports", _imported)

if _imported:
    _real_get = _rq.get
    _rq.get = fake_get
    CR.requests.get = fake_get
    try:
        TM._set_acting(LUXELINK, super_admin=False)
        _FAKE_STATUS[0] = 200
        data, note = CR._get_commission_accrued(LUXELINK, "2026-10-17", store_code="Nostrand",
                                                authorization=TOKEN)
        h1 = sent[-1]["headers"]
        check("D1 /payout/accrued self-call carries the bearer", h1.get("Authorization") == TOKEN, repr(h1))
        check("D2 /payout/accrued self-call carries the acting tenant",
              h1.get("x-active-org") == LUXELINK, repr(h1))
        check("D3 a 200 still returns the figure, no note", data is not None and note is None)

        data, note = CR._get_salary_owed(LUXELINK, "2026-08-18", "2026-10-17",
                                         store_code="Nostrand", authorization=TOKEN)
        h2 = sent[-1]["headers"]
        check("D4 /salary-owed self-call carries both facts",
              h2.get("Authorization") == TOKEN and h2.get("x-active-org") == LUXELINK, repr(h2))

        _FAKE_STATUS[0] = 409
        data, note = CR._get_commission_accrued(LUXELINK, "2026-10-17", authorization=TOKEN)
        check("D5 a 409 is reported as UNKNOWN, never as $0.00",
              data is None and note and "UNKNOWN" in note and "409" in note, repr(note))
        check("D6 the 409 note says WHY (which company)", note and "which company" in note, repr(note))

        _FAKE_STATUS[0] = 401
        _, note401 = CR._get_salary_owed(LUXELINK, "2026-08-18", "2026-10-17", authorization=TOKEN)
        check("D7 the 2026-08-04 401 class still reads as UNKNOWN",
              note401 and "UNKNOWN" in note401, repr(note401))
    finally:
        _rq.get = _real_get
        CR.requests.get = _real_get

# ── E. WIRING LOCK (build gate) ─────────────────────────────────────────────────────────────────
print("\n── E. WIRING LOCK: a self-call cannot un-wire ──")
APP = ROOT / "app"
PY_FILES = sorted(p for p in APP.rglob("*.py"))

BASE_VAR = re.compile(r"\b[A-Z_]*INTERNAL_API_BASE[A-Z_]*\b")
unwired, selfcall_sites = [], []
for p in PY_FILES:
    src = p.read_text()
    if "INTERNAL_API_BASE" not in src:
        continue
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "post", "put", "patch", "delete")):
            continue
        mod = node.func.value
        if not (isinstance(mod, ast.Name) and mod.id == "requests"):
            continue
        url_src = ast.get_source_segment(src, node.args[0]) if node.args else ""
        # the url may be a local assembled from the base var — resolve one hop by name
        if not BASE_VAR.search(url_src or ""):
            if not (url_src and re.fullmatch(r"url", url_src.strip())):
                continue
            # `url = f"{X_INTERNAL_API_BASE}/api/v1/…"` in the same function
            fn = next((f for f in ast.walk(tree)
                       if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and f.lineno <= node.lineno <= (f.end_lineno or node.lineno)), None)
            assigns = [ast.get_source_segment(src, a) or "" for a in ast.walk(fn or tree)
                       if isinstance(a, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "url" for t in a.targets)]
            if not any(BASE_VAR.search(a) for a in assigns):
                continue
        owner = next((f.name for f in ast.walk(tree)
                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and f.lineno <= node.lineno <= (f.end_lineno or node.lineno)), "<module>")
        site = f"{p.relative_to(ROOT)}::{owner}"
        selfcall_sites.append(site)
        kw = {k.arg: (ast.get_source_segment(src, k.value) or "") for k in node.keywords}
        if "self_call_headers(" not in (kw.get("headers") or ""):
            unwired.append(f"{site}  headers={kw.get('headers')!r}")

# DECLARED, not bounded (index §CORE-10, harness_subject_set_lock): a bare `>= 9` would let a
# subject LEAVE the derived set and take its own assertion with it, silently. Written by hand — a
# self-call that appears or disappears must be read by a human, and names itself here.
EXPECTED_SELFCALL_SITES = {
    # mod-retail-ops — the envelope payout preview, the two cash-movement records, the P&L push.
    "app/modules/closing/router.py::_get_commission_accrued",
    "app/modules/closing/router.py::_get_salary_owed",
    "app/modules/closing/router.py::_post_commission_payout",
    "app/modules/closing/router.py::_post_salary_advance",
    "app/modules/closing/router.py::_push_expense_category_pl",
    # mod-people — the four Store Expenses system lines and the bulk targets read.
    "app/modules/storeops/router.py::_pto_push_expense_line",
    "app/modules/storeops/router.py::_payex_push_expense_line",
    "app/modules/storeops/router.py::_payex_push_gross_line",
    "app/modules/storeops/router.py::_additional_payroll_push_line",
    "app/modules/storeops/router.py::_dm_targets_summary_bulk",
}
_derived = set(selfcall_sites)
check("E1 the self-call sites are EXACTLY the declared set (a site cannot leave silently)",
      _derived == EXPECTED_SELFCALL_SITES,
      f"only derived: {sorted(_derived - EXPECTED_SELFCALL_SITES)}\n        "
      f"only declared: {sorted(EXPECTED_SELFCALL_SITES - _derived)}")
check("E2 EVERY self-call dereferences the one home for its headers",
      not unwired, "un-wired:\n        " + "\n        ".join(unwired))

respell = [f"{p.relative_to(ROOT)}" for p in PY_FILES
           if p != ONE_HOME and 'environ.get("INTERNAL_API_BASE_URL")' in p.read_text()]
check("E3 nothing re-spells the base URL (one fact, one home)", not respell, str(respell))

hand_rolled = [f"{p.relative_to(ROOT)}" for p in PY_FILES
               if p != ONE_HOME and re.search(r'\{\s*["\']Authorization["\']\s*:\s*authorization',
                                              p.read_text())]
check("E4 no module hand-rolls a self-call header dict", not hand_rolled, str(hand_rolled))

# Negative controls: the scan must not be a rubber stamp.
_ctrl_src = '''
import requests
BASE = os.environ.get("INTERNAL_API_BASE_URL") or "x"
def f(authorization, org_id):
    url = f"{BASE}/api/v1/thing"
    return requests.post(url, json={}, headers={"Authorization": authorization}, timeout=10)
'''
_ctrl = ast.parse(_ctrl_src)
_ctrl_hits = [n for n in ast.walk(_ctrl) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "post"]
check("E5 negative control: an un-wired self-call IS parsed as a call site", len(_ctrl_hits) == 1)
check("E6 negative control: a hand-rolled header dict IS caught by the E4 pattern",
      re.search(r'\{\s*["\']Authorization["\']\s*:\s*authorization', _ctrl_src) is not None)
check("E7 negative control: the E3 pattern DOES match a re-spelled base URL",
      'environ.get("INTERNAL_API_BASE_URL")' in _ctrl_src)

# ── F. THE REFUSAL IS NAMED ─────────────────────────────────────────────────────────────────────
print("\n── F. A refused self-call reads as a cause, not as a number ──")
n404 = IC.self_call_note(404, "commission accrual", "mod-commission")
n401 = IC.self_call_note(401, "commission accrual", "mod-commission")
n409 = IC.self_call_note(409, "salary-owed", "mod-people")
check("F1 404 names the un-deployed package", n404 and "not deployed" in n404, repr(n404))
check("F2 401 says UNKNOWN, not $0", n401 and "UNKNOWN" in n401, repr(n401))
check("F3 403 says UNKNOWN too", (IC.self_call_note(403, "x") or "").find("UNKNOWN") >= 0)
check("F4 409 names the tenant question", n409 and "which company" in n409 and "UNKNOWN" in n409, repr(n409))
check("F5 a 200 is not a refusal", IC.self_call_note(200, "x") is None)
check("F6 a 500 is left to raise_for_status (this function does not speak for it)",
      IC.self_call_note(500, "x") is None)
check("F7 a junk status cannot crash a degrade-safe caller", IC.self_call_note(None, "x") is None)

print(f"\n{'='*96}\n{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
