"""Proof harness for the tech-support platform (agent/core/tech-support-platform, mig 715).

Runs the ACTUAL shipped handlers/helpers against a stateful fake Supabase client (same convention as
harness_core_bootstrap.py) — no DB, no network. Run from backend/:
    python3 harness_tech_support.py

Proves:
  GATE (_support_ctx / _require_support):
    1. super_admin login → allowed (cross-tenant console).
    2. HOUSE-org membership whose role grants modules.support → allowed.
    3. HOUSE-org membership, scope 'all' (no explicit support flag) → allowed (default admin/scope-all).
    4. HOUSE-org membership, no support + scope 'store' → DENIED (403).
    5. TENANT-only membership (no house row) → DENIED — nothing here reachable by tenant users.
    6. No/bad token → DENIED.
  DOC RESOLUTION (_resolve_support_doc, pure longest-prefix + tenant override):
    7. longest prefix wins over a shorter one.
    8. TENANT override beats the HOUSE row at EQUAL page_key.
    9. a MORE-SPECIFIC house doc beats a shorter TENANT doc (specificity first).
   10. unpublished docs are excluded.
   11. no match → None.
  ESCALATE (escalate_ticket) idempotency + SLA stamp + visible marker:
   12. first escalate creates ONE support_case, stamps sla_due_at from the HOUSE SLA policy, and posts a
       VISIBLE ticket_comment + a ticket_event on the tenant ticket.
   13. second escalate is a no-op → already_escalated, still exactly ONE case (UNIQUE org,ticket).
  REPLY FAN-OUT (support_case_reply):
   14. a visible reply writes a support_case_event(kind='reply', visible_to_user=true) AND fans into the
       tenant thread (a NON-internal ticket_comment + a ticket_event) so the user sees it.
  SLA (_sla_due_at, pure):
   15. due = created + response_hours from the matching policy row.
   16. no policy row for the priority → None (NO hard-coded hours).
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "harness-dummy-anon-key")

import app.modules.helpdesk.router as hd   # noqa: E402
import app.modules.core.router as core     # noqa: E402
from fastapi import HTTPException           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
TEN_A = "aaaaaaaa-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client ───────────────────────────────────────────────────────────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []          # (col, kind, val)

    # builders
    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def upsert(self, rows, on_conflict=None, **k):
        self.op = "upsert"; self.payload = rows; self.on_conflict = on_conflict; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            return SimpleNamespace(data=[dict(r) for r in rows if self._match(r)])
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r)
                r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            out = []
            for r in payload:
                r = dict(r)
                existing = None
                if keys:
                    for er in rows:
                        if all(er.get(k) == r.get(k) for k in keys):
                            existing = er; break
                if existing:
                    existing.update(r)
                    out.append(dict(existing))
                else:
                    r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.s[self.t] = keep
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {
        "app_users": [],
        "roles": [],
        "tenants": [{"org_id": TEN_A, "name": "Alpha Retail"}, {"org_id": HOUSE, "name": "House"}],
        "tickets": [],
        "ticket_priorities": [],
        "ticket_comments": [],
        "ticket_events": [],
        "ticket_settings": [],
        "support_case": [],
        "support_case_event": [],
        "support_sla_policy": [],
    }


def wire(store):
    fake = FakeClient(store)
    hd.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    return fake


def membership(org, role, super_admin=False):
    return {"id": nid("mem"), "auth_id": "uid-1", "org_id": org, "email": "agent@house.com",
            "role": role, "super_admin": super_admin, "is_default_org": True, "is_active": True,
            "created_at": "2026-01-01T00:00:00+00:00"}


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "display_name": name, "permissions": perms}


run = asyncio.run

# ── 1-6: GATE ────────────────────────────────────────────────────────────────────────────────────
def gate_allows(store, uid_ok=True):
    wire(store)
    core._uid_from_token = lambda auth: ("uid-1" if (uid_ok and auth == "Bearer good") else None)
    return hd._support_ctx("Bearer good", "") is not None


st = fresh_store(); st["app_users"] = [membership(HOUSE, "admin", super_admin=True)]
check("1. super_admin → allowed", gate_allows(st))

st = fresh_store()
st["app_users"] = [membership(HOUSE, "support_agent")]
st["roles"] = [role_row(HOUSE, "support_agent", {"modules": {"support": True}, "scope": "store"})]
check("2. house + modules.support → allowed", gate_allows(st))

st = fresh_store()
st["app_users"] = [membership(HOUSE, "manager")]
st["roles"] = [role_row(HOUSE, "manager", {"modules": {}, "scope": "all"})]
check("3. house + scope 'all' (no explicit flag) → allowed", gate_allows(st))

st = fresh_store()
st["app_users"] = [membership(HOUSE, "clerk")]
st["roles"] = [role_row(HOUSE, "clerk", {"modules": {"helpdesk": True}, "scope": "store"})]
check("4. house, no support + scope 'store' → DENIED", not gate_allows(st))

st = fresh_store()
st["app_users"] = [membership(TEN_A, "admin")]   # tenant admin, NO house row
st["roles"] = [role_row(TEN_A, "admin", {"modules": {"support": True, "admin": True}, "scope": "all"})]
check("5. tenant-only membership → DENIED (no house row)", not gate_allows(st))

st = fresh_store(); st["app_users"] = [membership(HOUSE, "admin", super_admin=True)]
check("6. bad token → DENIED", not gate_allows(st, uid_ok=False))

# _require_support raises 403 on deny
try:
    st = fresh_store(); wire(st)
    core._uid_from_token = lambda a: None
    hd._require_support("Bearer nope", "")
    check("6b. _require_support raises 403 on deny", False)
except HTTPException as e:
    check("6b. _require_support raises 403 on deny", e.status_code == 403)

# ── 7-11: DOC RESOLUTION (pure) ────────────────────────────────────────────────────────────────
def doc(org, pk, pub=True, tag=None):
    return {"id": nid("doc"), "org_id": org, "page_key": pk, "is_published": pub,
            "title": tag or pk, "user_md": "u", "support_md": "s"}

docs = [doc(HOUSE, "/storeops"), doc(HOUSE, "/storeops/payroll", tag="house-payroll")]
r = core._resolve_support_doc(docs, "/storeops/payroll/tax", TEN_A)
check("7. longest prefix wins", r and r["title"] == "house-payroll")

docs = [doc(HOUSE, "/storeops/payroll", tag="house"), doc(TEN_A, "/storeops/payroll", tag="tenant")]
r = core._resolve_support_doc(docs, "/storeops/payroll", TEN_A)
check("8. tenant override beats house at equal page_key", r and r["title"] == "tenant")

docs = [doc(TEN_A, "/storeops", tag="tenant-shallow"), doc(HOUSE, "/storeops/payroll", tag="house-deep")]
r = core._resolve_support_doc(docs, "/storeops/payroll", TEN_A)
check("9. more-specific house beats shorter tenant", r and r["title"] == "house-deep")

docs = [doc(HOUSE, "/storeops/payroll", pub=False)]
r = core._resolve_support_doc(docs, "/storeops/payroll", TEN_A)
check("10. unpublished excluded", r is None)

docs = [doc(HOUSE, "/commcalc")]
r = core._resolve_support_doc(docs, "/storeops/payroll", TEN_A)
check("11. no match → None", r is None)

# ── 12-13: ESCALATE idempotency + SLA stamp + visible marker ────────────────────────────────────
st = fresh_store()
st["ticket_priorities"] = [{"id": "pri-h", "org_id": TEN_A, "key": "high", "label": "High"}]
st["tickets"] = [{"id": "tkt-1", "org_id": TEN_A, "subject": "Payroll wrong", "ticket_number": 42,
                  "priority_id": "pri-h", "requester_email": "u@alpha.com", "created_at": "2026-07-22T10:00:00+00:00"}]
st["support_sla_policy"] = [
    {"org_id": HOUSE, "priority": "high", "response_hours": 8, "resolve_hours": 48},
    {"org_id": HOUSE, "priority": "normal", "response_hours": 24, "resolve_hours": 96},
]
wire(st)
hd._require_module = lambda org, key="helpdesk": None       # entitlement gate out of scope here

r1 = run(hd.escalate_ticket("tkt-1", {"page_key": "/storeops/payroll"}, org_id=TEN_A, actor="agent@alpha.com"))
cases = st["support_case"]
case = cases[0] if cases else {}
check("12a. escalate creates exactly ONE case", len(cases) == 1 and not r1.get("already_escalated"))
check("12b. priority mapped from ticket (high)", case.get("priority") == "high")
# SLA clock starts at ESCALATION time (case.created_at), + the HOUSE 'high' response_hours (8h).
exp = datetime.fromisoformat(case["created_at"]) + timedelta(hours=8)
check("12c. sla_due_at = escalation time + HOUSE response_hours(8h)",
      case.get("sla_due_at") and datetime.fromisoformat(case["sla_due_at"]) == exp)
vis = [c for c in st["ticket_comments"] if not c.get("is_internal")]
check("12d. visible 'escalated' comment on the tenant ticket",
      any("scalat" in (c.get("body") or "") for c in vis))
check("12e. ticket_event 'escalated' recorded",
      any(e.get("event_type") == "escalated" for e in st["ticket_events"]))

r2 = run(hd.escalate_ticket("tkt-1", {}, org_id=TEN_A, actor="agent@alpha.com"))
check("13. second escalate = no-op (already_escalated, still ONE case)",
      r2.get("already_escalated") is True and len(st["support_case"]) == 1)

# ── 14: REPLY FAN-OUT ───────────────────────────────────────────────────────────────────────────
# Gate as a support agent for the reply.
st["app_users"] = [membership(HOUSE, "support_agent")]
st["roles"] = [role_row(HOUSE, "support_agent", {"modules": {"support": True}, "scope": "all"})]
wire(st)
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)
cid = st["support_case"][0]["id"]
before_comments = len(st["ticket_comments"])
run(hd.support_case_reply(cid, {"body": "We fixed the pay rate on your record."},
                          authorization="Bearer good", x_active_org=""))
ev = st["support_case_event"]
check("14a. case event kind='reply' visible_to_user=true",
      any(e.get("kind") == "reply" and e.get("visible_to_user") for e in ev))
new_vis = [c for c in st["ticket_comments"] if not c.get("is_internal") and "fixed the pay rate" in (c.get("body") or "")]
check("14b. reply fanned into tenant ticket thread (non-internal comment)",
      len(new_vis) == 1 and len(st["ticket_comments"]) == before_comments + 1)
check("14c. support_reply ticket_event recorded",
      any(e.get("event_type") == "support_reply" for e in st["ticket_events"]))

# internal note must NOT fan out
before = len(st["ticket_comments"])
run(hd.support_case_note(cid, {"body": "internal: root-caused to a stale rate"},
                         authorization="Bearer good", x_active_org=""))
check("14d. internal note does NOT touch the tenant ticket thread",
      len(st["ticket_comments"]) == before
      and any(e.get("kind") == "internal_note" and not e.get("visible_to_user") for e in st["support_case_event"]))

# resolve without a resolution note is rejected
try:
    run(hd.support_case_status(cid, {"status": "resolved"}, authorization="Bearer good", x_active_org=""))
    check("14e. resolve requires a resolution note", False)
except HTTPException as e:
    check("14e. resolve requires a resolution note", e.status_code == 422)

# ── 15-16: SLA due (pure) ───────────────────────────────────────────────────────────────────────
policy = [{"priority": "urgent", "response_hours": 4}, {"priority": "normal", "response_hours": 24}]
due = hd._sla_due_at(policy, "urgent", "2026-07-22T00:00:00+00:00")
check("15. due = created + response_hours",
      due and datetime.fromisoformat(due) == datetime.fromisoformat("2026-07-22T00:00:00+00:00") + timedelta(hours=4))
check("16. no policy row → None (no hard-coded hours)",
      hd._sla_due_at(policy, "low", "2026-07-22T00:00:00+00:00") is None)

# ── 17-20: BUNDLED SEED (never-clobber, missing-table no-op, real file loads) ───────────────────
import app.modules.core.support_seed as seed   # noqa: E402

st = fresh_store()
f = wire(st)
pages = [{"page_key": "/a", "title": "A", "user_md": "ua", "support_md": "sa", "common_issues": []},
         {"page_key": "/b", "title": "B", "user_md": "ub"},
         {"page_key": "  ", "title": "no key → skip"}]
res = seed.seed_support_docs(f, HOUSE, pages=pages)
check("17. seed inserts valid pages, skips blank page_key, stamps updated_by='seed'",
      res["inserted"] == 2 and res["skipped"] == 1 and res["ok"]
      and len(st["support_doc"]) == 2 and all(r.get("updated_by") == "seed" for r in st["support_doc"]))

st = fresh_store()
st["support_doc"] = [
    {"id": "d1", "org_id": HOUSE, "page_key": "/a", "updated_by": "admin@house.com", "title": "HUMAN"},
    {"id": "d2", "org_id": HOUSE, "page_key": "/b", "updated_by": "seed", "title": "old-seed"},
]
f = wire(st)
res = seed.seed_support_docs(f, HOUSE, pages=[
    {"page_key": "/a", "title": "SEED-A"}, {"page_key": "/b", "title": "SEED-B"}, {"page_key": "/c", "title": "SEED-C"}])
a = next(r for r in st["support_doc"] if r["page_key"] == "/a")
b = next(r for r in st["support_doc"] if r["page_key"] == "/b")
check("18a. human-edited row survives a re-seed (never clobbered)",
      a["title"] == "HUMAN" and a["updated_by"] == "admin@house.com" and res["skipped"] >= 1)
check("18b. a prior seed-owned row IS refreshed", b["title"] == "SEED-B" and b["updated_by"] == "seed" and res["updated"] == 1)
check("18c. a missing page_key is inserted", any(r["page_key"] == "/c" for r in st["support_doc"]) and res["inserted"] == 1)
check("18d. re-seed does not duplicate rows", len(st["support_doc"]) == 3)


class _RaisingClient:
    def schema(self, _n): return self
    def table(self, _n): return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def execute(self): raise RuntimeError("relation core.support_doc does not exist")


res = seed.seed_support_docs(_RaisingClient(), HOUSE, pages=[{"page_key": "/a", "title": "A"}])
check("19. un-run mig 715 (missing table) → silent no-op (ok False, no crash)",
      res["ok"] is False and res["inserted"] == 0)

loaded = seed.load_seed_pages()
check("20. bundled seed file ships + parses (>=150 pages, contract shape)",
      isinstance(loaded, list) and len(loaded) >= 150 and all(isinstance(p, dict) for p in loaded[:5]))

# ── 21: OFFLINE SQL SANITY — the mig 715 exemplar INSERT has matching column/value arity per row ──
# (This is the class of bug Gate-1 caught: 9 columns declared, some rows supplied only 8 values.)
def _split_top_level(inner):
    """Split one VALUES tuple's inner text on TOP-LEVEL commas, respecting '…'/E'…' string literals
    ('' escape) and (…)/[…] nesting (parens/brackets inside string literals are ignored)."""
    vals, cur, depth, in_str, i = [], [], 0, False, 0
    while i < len(inner):
        ch = inner[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(inner) and inner[i + 1] == "'":
                    cur.append("''"); i += 2; continue
                in_str = False
            cur.append(ch); i += 1; continue
        if ch == "'":
            in_str = True; cur.append(ch); i += 1; continue
        if ch in "([":
            depth += 1; cur.append(ch); i += 1; continue
        if ch in ")]":
            depth -= 1; cur.append(ch); i += 1; continue
        if ch == "," and depth == 0:
            vals.append("".join(cur).strip()); cur = []; i += 1; continue
        cur.append(ch); i += 1
    if "".join(cur).strip():
        vals.append("".join(cur).strip())
    return vals


def _parse_tuples(region):
    """Extract each top-level (…) tuple's inner text from a VALUES region (string-literal aware)."""
    tuples, cur, depth, in_str, i = [], [], 0, False, 0
    while i < len(region):
        ch = region[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(region) and region[i + 1] == "'":
                    cur.append("''"); i += 2; continue
                in_str = False
            cur.append(ch); i += 1; continue
        if ch == "'":
            in_str = True; cur.append(ch); i += 1; continue
        if ch == "(":
            depth += 1
            if depth == 1: cur = []; i += 1; continue
            cur.append(ch); i += 1; continue
        if ch == ")":
            depth -= 1
            if depth == 0: tuples.append("".join(cur)); cur = []; i += 1; continue
            cur.append(ch); i += 1; continue
        cur.append(ch); i += 1
    return tuples


import re as _re   # noqa: E402
_mig = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "migrations", "715_tech_support.sql")
with open(_mig, encoding="utf-8") as _fh:
    _sql = _fh.read()
_m = _re.search(r"INSERT INTO core\.support_doc\s*\((.*?)\)\s*VALUES(.*?)ON CONFLICT", _sql, _re.S)
check("21a. found the core.support_doc exemplar INSERT block", bool(_m))
if _m:
    _cols = [c.strip() for c in _m.group(1).split(",") if c.strip()]
    _tuples = _parse_tuples(_m.group(2))
    _arities = [len(_split_top_level(t)) for t in _tuples]
    check(f"21b. every exemplar VALUES row has {len(_cols)} values (== column count)",
          bool(_tuples) and all(x == len(_cols) for x in _arities),
          f"cols={len(_cols)} rows={len(_tuples)} arities={_arities}")

# ── summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
