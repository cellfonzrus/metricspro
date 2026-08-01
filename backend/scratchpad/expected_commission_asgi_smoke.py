"""REAL-ASGI smoke for EXPECTED vs EARNED + the permission-gated promote (mig 258).

The proof harness calls the engine as plain Python — that proves the arithmetic but NOT the mount, the
`/api/v1` prefix, the query-param binding or the PERMISSION GATE. This drives the WHOLE FastAPI app
through Starlette's TestClient at the exact URLs the page fetches, and asserts:

  • every endpoint answers at its real /api/v1 URL; the BARE path 404s (the api() trap)
  • ROUTE ORDER: /expected-commission/config and /promotes are NOT swallowed by /{period}
  • THE PERMISSION GATE, both ways — a user WITHOUT the `commission_promote` setting gets 403 and
    writes nothing; a user WITH it succeeds; an UNIDENTIFIED caller is refused by default and allowed
    only when the tenant opts in
  • org_id is really a QUERY PARAM: a second tenant cannot see or promote tenant A's rows
  • the READ paths write NOTHING; the promote writes ONLY the promote table, org-stamped, with reason
  • a promote with no reason / outside the window / on a non-existent month / on an already-earned
    month is refused with a clear message, never a 500
  • pre-migration: every GET still 200, the writes 400 naming the file
  • the route COUNT is the pinned base + exactly the routes this package adds

Run: cd backend && PYTHONPATH=. python3 scratchpad/expected_commission_asgi_smoke.py
"""
import io
import os
import subprocess
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
_REPO = os.path.abspath(os.path.join(_BACKEND, ".."))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")

BASE_ROUTES = 940          # pinned: local main @ 79a969c, measured
NEW_ROUTES = 6


def _helpers():
    path = os.path.join(_HERE, "expected_commission_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = '\nsec("§A'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("xc_asgi_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _helpers()

from fastapi.testclient import TestClient            # noqa: E402
from app.main import app                             # noqa: E402
import app.core.database as DB                       # noqa: E402
from app.modules.commcalc import router as R         # noqa: E402
from app.modules.commcalc import sale_installment_engine as sie   # noqa: E402
import app.modules.core.router as CR                 # noqa: E402

_pass = _fail = 0
_fails = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _fails.append(name)
        print(f"  FAIL  {name}   {extra}")


ORG, ORG_B, PER = H.ORG, H.ORG_B, H.PER
CFG = "/api/v1/commcalc/expected-commission/config"
LIST = "/api/v1/commcalc/expected-commission/promotes"
PROMOTE = "/api/v1/commcalc/expected-commission/promote"
REVOKE = "/api/v1/commcalc/expected-commission/revoke"
REPORT = f"/api/v1/commcalc/expected-commission/{PER}"


class WQuery(H.FakeQuery):
    def __init__(self, store, key, log, writes):
        super().__init__(store.get(key, []), log, key, None)
        self._store, self._key, self._wlog = store, key, writes

    def insert(self, rows, *a, **k):
        rows = rows if isinstance(rows, list) else [rows]
        self._wlog.append(("insert", self._key, rows))
        self._store.setdefault(self._key, []).extend(dict(r) for r in rows)
        return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

    def upsert(self, rows, *a, **k):
        rows = rows if isinstance(rows, list) else [rows]
        self._wlog.append(("upsert", self._key, rows))
        cur = self._store.setdefault(self._key, [])
        for row in rows:
            key_fields = ("org_id", "pay_period", "trans_id", "mdn", "month_index")
            hit = None
            if all(f in row for f in key_fields):
                hit = next((c for c in cur if all(str(c.get(f)) == str(row.get(f))
                                                  for f in key_fields)), None)
            elif "org_id" in row and self._key.endswith("commission_org_config"):
                hit = next((c for c in cur if c.get("org_id") == row.get("org_id")), None)
            if hit is not None:
                hit.update(row)
            else:
                cur.append(dict(row, id=row.get("id") or f"id-{len(cur)+1}"))
        return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

    def update(self, patch, *a, **k):
        self._wlog.append(("update", self._key, patch))
        self._patch = patch
        return self

    def delete(self, *a, **k):
        self._wlog.append(("delete", self._key, None))
        self._del = True
        return self

    def execute(self):
        if getattr(self, "_patch", None) is not None:
            for r in self._apply():
                r.update(self._patch)
            return type("R", (), {"data": []})()
        if getattr(self, "_del", False):
            keep = [r for r in self._store.get(self._key, []) if r not in self._apply()]
            self._store[self._key] = keep
            return type("R", (), {"data": []})()
        return super().execute()


class WSchema:
    def __init__(self, store, schema, log, writes, missing):
        self.store, self.s, self.log, self.writes, self.missing = store, schema, log, writes, set(missing)

    def table(self, t):
        key = f"{self.s}.{t}"
        if key in self.missing:
            return H.MissingTable()
        return WQuery(self.store, key, self.log, self.writes)


class WClient:
    def __init__(self, store, log=None, writes=None, missing=()):
        self.store, self.log, self.missing = store, log, missing
        self.writes = writes if writes is not None else []

    def schema(self, s):
        return WSchema(self.store, s, self.log, self.writes, self.missing)

    def table(self, t):
        return WQuery(self.store, f"public.{t}", self.log, self.writes)


def mount(store, writes=None, missing=()):
    c = WClient(store, None, writes, missing)
    R.sb = lambda: c
    DB.get_supabase = lambda *a, **k: c
    return c


# ── the RBAC surface, faked at the seam the module actually uses ────────────────────────────────
_CALLER = {"mode": "admin"}


def _fake_uid(auth):
    return None if _CALLER["mode"] == "anon" else "uid-1"


def _fake_resolve(client, uid, active_org=None):
    m = _CALLER["mode"]
    if m == "anon":
        return None
    if m == "admin":
        return {"org_id": ORG, "role": "admin", "super_admin": False, "perms": {"scope": "all"}}
    if m == "granted":
        return {"org_id": ORG, "role": "manager", "super_admin": False,
                "perms": {"scope": "market", "settings": {"commission_promote": True}}}
    if m == "denied":
        return {"org_id": ORG, "role": "manager", "super_admin": False,
                "perms": {"scope": "market", "settings": {"commission_promote": False}}}
    if m == "plain":          # a non-admin with NO explicit grant -> the safe default is DENY
        return {"org_id": ORG, "role": "rep", "super_admin": False, "perms": {"scope": "self"}}
    return None


CR._uid_from_token = _fake_uid
CR._resolve_caller = _fake_resolve

STORE = H.store(gate_from=2)
WRITES = []
mount(STORE, writes=WRITES)
client = TestClient(app, raise_server_exceptions=False)

# find a real promotable row through the engine (the same one the page would offer)
_res = sie.compute_sale_installments(WClient(H.store(gate_from=2)), ORG, PER, persist=False)
TGT = next(r for r in _res["ledger"]
           if not r["paid_gate_met"] and r["expected_in_window"] and r["expected_amount"] > 0)

print("\n-- the exact URLs the page fetches ----------------------------------------------------------")
r = client.get(REPORT, params={"org_id": ORG})
check(f"GET {REPORT} -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
for k in ("period", "config", "can_promote", "rows", "by_rep", "totals", "expected_guard",
          "warnings", "money_note", "ready"):
    check(f"UI contract: report carries `{k}`", k in j, sorted(j))
check("the window is the owner's default 2..6",
      j.get("config", {}).get("from_month") == 2 and j.get("config", {}).get("to_month") == 6,
      j.get("config"))
check("EXPECTED and EARNED are separate totals",
      "expected_in_window" in (j.get("totals") or {}) and "earned" in (j.get("totals") or {}),
      j.get("totals"))
check("...and the payload SAYS expected is never added to a payout",
      "never added" in (j.get("money_note") or ""), j.get("money_note"))
row0 = next((x for x in j.get("rows") or [] if x["trans_id"] == TGT["trans_id"]
             and x["month_index"] == TGT["month_index"]), None)
check("a withheld in-window month shows expected > 0 and earned = 0",
      row0 and row0["expected"] > 0 and row0["earned"] == 0.0 and row0["promotable"], row0)
r = client.get(f"/commcalc/expected-commission/{PER}", params={"org_id": ORG})
check("the BARE path (no /api/v1) is 404 — the api() trap", r.status_code == 404, r.status_code)
r = client.get(REPORT, params={"org_id": ""})
check("an empty org_id is rejected 400", r.status_code == 400, r.status_code)

print("\n-- ROUTE ORDER: the literal segments are not swallowed by /{period} --------------------------")
r = client.get(CFG, params={"org_id": ORG})
check("GET /expected-commission/config -> 200 and is the CONFIG, not a period report",
      r.status_code == 200 and "defaults" in r.json() and "rows" not in r.json(), r.text[:200])
r = client.get(LIST, params={"org_id": ORG})
check("GET /expected-commission/promotes -> 200 and is the AUDIT LIST",
      r.status_code == 200 and "promotes" in r.json(), r.text[:200])

print("\n-- THE PERMISSION GATE ----------------------------------------------------------------------")
body = {"period": PER, "trans_id": TGT["trans_id"], "mdn": TGT["mdn"],
        "month_index": TGT["month_index"], "reason": "carrier report not updated on time"}

_CALLER["mode"] = "denied"
WRITES.clear()
r = client.post(PROMOTE, params={"org_id": ORG}, json=body)
check("a user whose role DENIES commission_promote gets 403", r.status_code == 403, r.text[:200])
check("...and nothing was written", not WRITES, WRITES)
check("...and the message names the permission", "commission_promote" in r.text, r.text[:200])
check("...and the config endpoint tells the UI to hide the affordance",
      client.get(CFG, params={"org_id": ORG}).json().get("can_promote") is False)

_CALLER["mode"] = "plain"
r = client.post(PROMOTE, params={"org_id": ORG}, json=body)
check("a plain non-admin with NO explicit grant is DENIED (safe default)", r.status_code == 403,
      r.status_code)

_CALLER["mode"] = "anon"
WRITES.clear()
r = client.post(PROMOTE, params={"org_id": ORG}, json=body)
check("an UNIDENTIFIED caller is refused — an audit row cannot say 'unknown'",
      r.status_code == 403, r.text[:200])
check("...and nothing was written", not WRITES, WRITES)
check("...and the reason is reported to the UI",
      client.get(CFG, params={"org_id": ORG}).json().get("can_promote_reason") == "unidentified_caller")

# ...unless the tenant explicitly opts in (a genuinely RBAC-off deployment)
STORE["commcalc.commission_org_config"] = [
    {"org_id": ORG, "expected_commission_config": {"promote_allow_unidentified": True}}]
check("an unidentified caller IS allowed once the tenant opts in",
      client.get(CFG, params={"org_id": ORG}).json().get("can_promote") is True)
STORE["commcalc.commission_org_config"] = []

_CALLER["mode"] = "granted"
check("a manager EXPLICITLY granted the setting can promote (an admin is not required)",
      client.get(CFG, params={"org_id": ORG}).json().get("can_promote") is True)

print("\n-- the promote itself -----------------------------------------------------------------------")
_CALLER["mode"] = "admin"
WRITES.clear()
r = client.post(PROMOTE, params={"org_id": ORG}, json=body)
check(f"POST {PROMOTE} -> 200", r.status_code == 200, r.text[:300])
check("it wrote ONLY the promote table", {t for _o, t, _r in WRITES} == {"commcalc.installment_promote"},
      {t for _o, t, _r in WRITES})
prow = STORE["commcalc.installment_promote"][0]
check("...org-stamped with the CALLER's org", prow.get("org_id") == ORG, prow)
check("...recording who / when / why", prow.get("promoted_by") == "uid-1"
      and prow.get("promoted_at") and prow.get("reason") == body["reason"], prow)
check("...and the APPROVED figure", round(float(prow.get("expected_at_promote")), 2)
      == round(TGT["expected_amount"], 2), prow)
check("...and the response explains the recompute guarantee",
      "survives every recompute" in (r.json().get("note") or ""), r.json().get("note"))

r2 = client.get(REPORT, params={"org_id": ORG})
j2 = r2.json()
prow2 = next(x for x in j2["rows"] if x["trans_id"] == TGT["trans_id"]
             and x["month_index"] == TGT["month_index"])
check("the month now reads EARNED, over HTTP", prow2["earned"] == TGT["expected_amount"]
      and prow2["promoted"] is True, prow2)
check("...and the earned TOTAL moved by exactly that amount",
      round(j2["totals"]["earned"] - j["totals"]["earned"], 2) == round(TGT["expected_amount"], 2),
      (j2["totals"], j["totals"]))
check("...while the EXPECTED total did not move (expected is not a payment)",
      j2["totals"]["expected_in_window"] == j["totals"]["expected_in_window"])

print("\n-- refusals, all explicit --------------------------------------------------------------------")
r = client.post(PROMOTE, params={"org_id": ORG}, json={**body, "reason": ""})
check("no reason -> 400 naming the audit record", r.status_code == 400 and "reason" in r.text.lower(),
      r.text[:200])
r = client.post(PROMOTE, params={"org_id": ORG}, json={**body, "month_index": 1})
check("month 1 is outside the window -> 400 naming the window",
      r.status_code == 400 and "window" in r.text.lower(), r.text[:200])
r = client.post(PROMOTE, params={"org_id": ORG}, json={**body, "trans_id": "NOPE", "mdn": "0"})
check("a non-existent chain-month -> 404, not a 500", r.status_code == 404, r.text[:200])
# An already-EARNED month that is also IN-WINDOW needs a schedule gated from month 3 (so month 2 is
# both in-window and already paid). Mounted just for this check, then the main store is restored —
# otherwise the window refusal fires first and the assertion proves nothing.
_saved_store, _saved_writes = STORE, WRITES
_s3 = H.store(gate_from=3)
mount(_s3, writes=[])
_j3 = client.get(REPORT, params={"org_id": ORG}).json()
paid = next((x for x in _j3["rows"] if x["gate_met"] and x["expected_in_window"] and not x["promoted"]), None)
check("the fixture really has an already-earned IN-WINDOW month to try", paid is not None, paid)
if paid:
    r = client.post(PROMOTE, params={"org_id": ORG},
                    json={"period": PER, "trans_id": paid["trans_id"], "mdn": paid["mdn"],
                          "month_index": paid["month_index"], "reason": "x"})
    check("an ALREADY-EARNED month -> 400 (promoting it would imply a second payment)",
          r.status_code == 400 and "already" in r.text.lower(), r.text[:200])
mount(_saved_store, writes=_saved_writes)

print("\n-- two-tenant isolation ---------------------------------------------------------------------")
r = client.get(LIST, params={"org_id": ORG_B})
check("tenant B cannot see tenant A's promotes", r.json().get("count") == 0, r.json())
r = client.post(PROMOTE, params={"org_id": ORG_B}, json=body)
check("tenant B cannot promote tenant A's chain (404 — it is not in B's data)",
      r.status_code in (404, 400), r.status_code)
check("...and tenant A's promote is untouched", len(STORE["commcalc.installment_promote"]) == 1)

print("\n-- revoke keeps the audit trail --------------------------------------------------------------")
rid = STORE["commcalc.installment_promote"][0].get("id")
WRITES.clear()
r = client.post(REVOKE, params={"org_id": ORG}, json={"id": rid, "reason": "statement arrived"})
check("POST revoke -> 200", r.status_code == 200, r.text[:200])
check("the row is KEPT with status='revoked' (a deletable audit trail is not one)",
      len(STORE["commcalc.installment_promote"]) == 1
      and STORE["commcalc.installment_promote"][0]["status"] == "revoked",
      STORE["commcalc.installment_promote"])
check("...recording who revoked it", STORE["commcalc.installment_promote"][0].get("revoked_by") == "uid-1")
j3 = client.get(REPORT, params={"org_id": ORG}).json()
check("...and the month goes back to expected-only", round(j3["totals"]["earned"], 2)
      == round(j["totals"]["earned"], 2), (j3["totals"], j["totals"]))
_CALLER["mode"] = "denied"
r = client.post(REVOKE, params={"org_id": ORG}, json={"id": rid})
check("revoke is gated by the SAME permission", r.status_code == 403, r.status_code)
_CALLER["mode"] = "admin"

print("\n-- READ paths write nothing ------------------------------------------------------------------")
WRITES.clear()
client.get(REPORT, params={"org_id": ORG})
client.get(CFG, params={"org_id": ORG})
client.get(LIST, params={"org_id": ORG})
check("zero writes from every GET", not WRITES, WRITES)

print("\n-- config save --------------------------------------------------------------------------------")
r = client.put(CFG, params={"org_id": ORG}, json={"config": {"from_month": 3, "to_month": 5}})
check("PUT config -> 200", r.status_code == 200, r.text[:200])
check("...and it reads back", client.get(CFG, params={"org_id": ORG}).json()["config"]["from_month"] == 3)
r = client.put(CFG, params={"org_id": ORG}, json={"config": {"on_expected_change": "nonsense"}})
check("an unknown on_expected_change is refused 400", r.status_code == 400, r.status_code)
client.put(CFG, params={"org_id": ORG}, json={"reset": True})
check("reset returns to the code default",
      client.get(CFG, params={"org_id": ORG}).json()["is_default"] is True)

print("\n-- pre-migration degradation ------------------------------------------------------------------")
mount(H.store(gate_from=2), writes=[],
      missing=("commcalc.installment_promote", "commcalc.commission_org_config"))
r = client.get(CFG, params={"org_id": ORG})
check("config still 200 with 258 unrun", r.status_code == 200 and r.json().get("ready") is False,
      r.text[:200])
check("...and it names the migration",
      r.json().get("migration") == "258_commission_expected_earned_promote.sql")
r = client.get(REPORT, params={"org_id": ORG})
check("the report still 200 with 258 unrun", r.status_code == 200, r.text[:200])
r = client.get(LIST, params={"org_id": ORG})
check("the audit list still 200 with 258 unrun", r.status_code == 200 and r.json().get("ready") is False)
r = client.post(PROMOTE, params={"org_id": ORG}, json=body)
check("the promote returns a clear 400 naming the migration, never a 500",
      r.status_code == 400 and "258_commission_expected_earned_promote.sql" in r.text, r.text[:250])

print("\n-- route table --------------------------------------------------------------------------------")
total = len([r for r in app.routes if hasattr(r, "path")])
check(f"route count = pinned base {BASE_ROUTES} + exactly {NEW_ROUTES}",
      total == BASE_ROUTES + NEW_ROUTES, total)
try:
    ok_ref = subprocess.check_output(["git", "-C", _REPO, "rev-parse", "--verify", "main"],
                                     stderr=subprocess.DEVNULL).decode().strip()
    check("BASE_ROUTES is pinned to a real ref (local main exists)", bool(ok_ref))
except Exception:
    check("BASE_ROUTES is pinned to a real ref (local main exists)", False)
mine = {r.path for r in app.routes if hasattr(r, "path") and "expected-commission" in r.path}
check("the five expected-commission paths are registered", len(mine) == 5, sorted(mine))

print("\n" + "=" * 94)
print(f"RESULT: {_pass} passed, {_fail} failed")
if _fails:
    print("FAILED:")
    for f in _fails:
        print("  -", f)
print("=" * 94)
sys.exit(1 if _fail else 0)
