"""Proof harness for the failure-triage package (agent/core/failure-triage, mig 716).

Runs the ACTUAL shipped handlers/helpers against a stateful fake Supabase client (same convention as
harness_tech_support.py) — no DB, no network. Run from backend/:
    python3 harness_failure_triage.py

Proves:
  GROUPING (_build_failure_groups, pure):
    1. rows group by KIND with correct count / unreviewed_count / reviewed_count / latest_at / max severity.
    2. affected_orgs + sample_ids captured; a fully-reviewed group has all_reviewed=true (→ collapsed).
    3. sorted most-unreviewed first.
  REGISTRY (_merge_kind_docs / _kind_fallback, pure):
    4. every in-code kind is present (source='code'); a DB row overlays a field (source='db').
    5. an UNKNOWN kind → graceful fallback (known=false, "escalate to tech support").
  FIX-STATUS (fix_status_change, pure):
    6. approve/reject REQUIRE super_admin; working transitions allowed for support; invalid target rejected.
  FETCH degradation (_fetch_failures):
    7. reviewed filter works normally; mig-716 `reviewed` column absent → falls back + filters in Python.
  CORE endpoints (tenant-scoped, admin-gated):
    8. _can_view_failures gate; list/grouped are ORG-SCOPED (admin sees only own org).
    9. bulk-review marks ONLY the selected ids AND only within the caller's org.
   10. create_fix_request → pending_approval, org-scoped; non-admin → 403.
  SUPPORT endpoints (cross-tenant, house-gated; approve = super_admin ONLY):
   11. support_failures: tenant user → 403; house support sees cross-tenant groups + tenant-named rows.
   12. support bulk-review clears across tenants by id.
   13. fix-request lifecycle: create (HOUSE-owned, affected_orgs kept) → approve DENIED to non-super →
       approve OK for super_admin (approved_by stamped) → reject path → resolve+mark_reviewed clears the
       clubbed failures.
  APPROVAL GATE AT CREATION (Gate-1 follow-up — bfe85d1 rework):
   15. a NON-super support agent POSTing status='approved'/'rejected' is CLAMPED to pending_approval and
       never lands in the approved automation queue; a super_admin MAY create a pre-approved request and
       gets approved_by/approved_at stamped (audit parity with the /status path).
  SQL sanity:
   14. mig 716 failure_kind_doc INSERT has matching column/value arity per row.
"""
import asyncio
import os
import re as _re
import sys
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
TEN_B = "bbbbbbbb-0000-0000-0000-000000000002"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []

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
                r = dict(r); r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
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
                r = dict(r); existing = None
                if keys:
                    for er in rows:
                        if all(er.get(k) == r.get(k) for k in keys):
                            existing = er; break
                if existing:
                    existing.update(r); out.append(dict(existing))
                else:
                    r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            self.s[self.t] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"app_users": [], "roles": [],
            "tenants": [{"org_id": TEN_A, "name": "Alpha Retail"}, {"org_id": TEN_B, "name": "Bravo Wireless"},
                        {"org_id": HOUSE, "name": "House"}],
            "failure_log": [], "failure_kind_doc": [], "support_fix_request": []}


def wire(store):
    fake = FakeClient(store)
    hd.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    return fake


def membership(org, role, super_admin=False, email="agent@house.com"):
    return {"id": nid("mem"), "auth_id": "uid-1", "org_id": org, "email": email, "role": role,
            "super_admin": super_admin, "is_default_org": True, "is_active": True,
            "created_at": "2026-01-01T00:00:00+00:00"}


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "display_name": name, "permissions": perms}


def flog(org, cat, sev="warning", reviewed=False, created="2026-07-20T10:00:00+00:00", msg="x", empn=None):
    return {"id": nid("flog"), "org_id": org, "category": cat, "severity": sev, "reviewed": reviewed,
            "created_at": created, "message": msg, "employee_name": empn, "store_code": None,
            "status": "open", "detail": None, "remediation": None}


run = asyncio.run

# ── 1-3: GROUPING (pure) ──────────────────────────────────────────────────────────────────────────
rows = [
    flog(TEN_A, "face_mismatch", "warning", reviewed=False, created="2026-07-20T10:00:00+00:00"),
    flog(TEN_A, "face_mismatch", "warning", reviewed=True,  created="2026-07-21T10:00:00+00:00"),
    flog(TEN_B, "face_mismatch", "info",    reviewed=False, created="2026-07-22T12:00:00+00:00"),
    flog(TEN_A, "system_error",  "error",   reviewed=True,  created="2026-07-19T10:00:00+00:00"),
]
meta = core._merge_kind_docs([])
groups = core._build_failure_groups(rows, meta)
gmap = {g["kind"]: g for g in groups}
fm = gmap.get("face_mismatch"); se = gmap.get("system_error")
check("1a. groups by kind (2 groups)", len(groups) == 2 and fm and se)
check("1b. face_mismatch count=3, unreviewed=2, reviewed=1", fm and fm["count"] == 3 and fm["unreviewed_count"] == 2 and fm["reviewed_count"] == 1)
check("1c. latest_at = newest row", fm and fm["latest_at"] == "2026-07-22T12:00:00+00:00")
check("1d. severity = max across rows (warning > info)", fm and fm["severity"] == "warning")
check("2a. affected_orgs both tenants w/ counts", fm and sorted((o["org_id"], o["count"]) for o in fm["affected_orgs"]) == sorted([(TEN_A, 2), (TEN_B, 1)]))
check("2b. sample_ids = every row id in the group", fm and len(fm["sample_ids"]) == 3)
check("2c. fully-reviewed group → all_reviewed=true (collapse)", se and se["all_reviewed"] is True and fm["all_reviewed"] is False)
check("3. sorted most-unreviewed first", groups[0]["kind"] == "face_mismatch")

# ── 4-5: REGISTRY (pure) ────────────────────────────────────────────────────────────────────────
merged = core._merge_kind_docs([{"kind": "face_mismatch", "layman_fix": "CUSTOM FIX FROM DB"}])
check("4a. every in-code kind present", all(k in merged for k in core.FAILURE_KIND_META))
check("4b. DB row overlays a field + marks source='db'", merged["face_mismatch"]["layman_fix"] == "CUSTOM FIX FROM DB" and merged["face_mismatch"]["source"] == "db")
check("4c. untouched kind stays source='code'", merged["system_error"]["source"] == "code")
fb = core._kind_fallback("totally_unknown_code")
check("5. unknown kind → graceful fallback (known=false, escalate)", fb["known"] is False and "escalate" in (fb["layman_fix"] or "").lower())
grp_unknown = core._build_failure_groups([flog(TEN_A, "totally_unknown_code")], merged)
check("5b. unknown-kind group carries known=false + fallback doc", grp_unknown[0]["known"] is False and "escalate" in (grp_unknown[0]["doc"]["layman_fix"] or "").lower())

# ── 6: FIX-STATUS (pure) ──────────────────────────────────────────────────────────────────────────
check("6a. approve requires super_admin (denied for non-super)", core.fix_status_change("pending_approval", "approved", False) == (False, "only a super-admin can approve or reject a fix request"))
check("6b. approve allowed for super_admin", core.fix_status_change("pending_approval", "approved", True)[0] is True)
check("6c. reject requires super_admin", core.fix_status_change("pending_approval", "rejected", False)[0] is False)
check("6d. working transition allowed for support (in_progress)", core.fix_status_change("approved", "in_progress", False)[0] is True)
check("6e. invalid target rejected", core.fix_status_change("new", "banana", True) == (False, "invalid status"))

# ── 7: _fetch_failures degradation ────────────────────────────────────────────────────────────────
st = fresh_store()
st["failure_log"] = [flog(TEN_A, "face_mismatch", reviewed=False), flog(TEN_A, "face_mismatch", reviewed=True)]
f = wire(st)
only_unrev = core._fetch_failures(f, org_id=TEN_A, reviewed="false")
check("7a. reviewed='false' returns only unreviewed", len(only_unrev) == 1 and only_unrev[0]["reviewed"] is False)


class RaiseOnReviewedClient:
    """A client whose failure_log query RAISES if a `reviewed` filter is applied (simulates the mig-716
    column being absent) but otherwise returns the seeded rows — exercises the fallback path."""
    def __init__(self, rows): self._rows = rows
    def schema(self, _n): return self

    def table(self, _n):
        outer = self

        class _Q(Q):
            def execute(self_inner):
                if any(c == "reviewed" for c, _, _ in self_inner.filters):
                    raise RuntimeError("column failure_log.reviewed does not exist")
                data = [dict(r) for r in outer._rows if self_inner._match(r)]
                return SimpleNamespace(data=data)
        return _Q({"failure_log": outer._rows}, "failure_log")


rc = RaiseOnReviewedClient([flog(TEN_A, "face_mismatch", reviewed=False), flog(TEN_A, "face_mismatch", reviewed=True)])
degraded = core._fetch_failures(rc, org_id=TEN_A, reviewed="false")
check("7b. mig-716 reviewed column absent → falls back + filters in Python", len(degraded) == 1 and degraded[0]["reviewed"] is False)

# ── 8-10: CORE endpoints (tenant-scoped, admin-gated) ──────────────────────────────────────────────
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)

# _can_view_failures
check("8a. non-admin denied", core._can_view_failures({"role": "clerk", "perms": {"scope": "store"}}) is False)
check("8b. scope-all admin allowed", core._can_view_failures({"role": "manager", "perms": {"scope": "all"}}) is True)

st = fresh_store()
st["app_users"] = [membership(TEN_A, "admin")]
st["roles"] = [role_row(TEN_A, "admin", {"scope": "all"})]
st["failure_log"] = [flog(TEN_A, "face_mismatch"), flog(TEN_A, "system_error"), flog(TEN_B, "face_mismatch")]
wire(st)
d = run(core.failures_grouped(authorization="Bearer good", x_active_org="", reviewed=""))
seen_orgs = {o["org_id"] for g in d["groups"] for o in g["affected_orgs"]}
check("8c. /failures/grouped is ORG-SCOPED (admin sees only own org)", seen_orgs == {TEN_A} and d["total"] == 2)
lst = run(core.list_failures(authorization="Bearer good", x_active_org="", reviewed=""))
check("8d. /failures list org-scoped", all(r["org_id"] == TEN_A for r in lst["failures"]) and len(lst["failures"]) == 2)

# bulk-review: only selected ids + only within caller org
ids_A = [r["id"] for r in st["failure_log"] if r["org_id"] == TEN_A]
id_B = next(r["id"] for r in st["failure_log"] if r["org_id"] == TEN_B)
run(core.failures_bulk_review({"ids": [ids_A[0], id_B], "reviewed": True}, authorization="Bearer good", x_active_org=""))
by_id = {r["id"]: r for r in st["failure_log"]}
check("9a. bulk-review marks the selected in-org id reviewed", by_id[ids_A[0]]["reviewed"] is True and by_id[ids_A[0]].get("reviewed_by"))
check("9b. an UNSELECTED in-org id is untouched", by_id[ids_A[1]].get("reviewed") in (False, None))
check("9c. a CROSS-ORG id is NOT touched (org-scoped)", by_id[id_B].get("reviewed") in (False, None))

# create fix request (org-scoped)
r = run(core.create_fix_request({"kind": "face_mismatch", "module": "storeops", "title": "Face rejects",
                                 "sample_failure_ids": ids_A, "failure_count": 2},
                                authorization="Bearer good", x_active_org=""))
fr = st["support_fix_request"][0]
check("10a. create_fix_request → pending_approval, org-scoped", r["status"] == "pending_approval" and fr["org_id"] == TEN_A and fr["failure_count"] == 2)
check("10b. affected_orgs = own org", fr["affected_orgs"] == [{"org_id": TEN_A, "count": 2}])

# non-admin → 403
st2 = fresh_store(); st2["app_users"] = [membership(TEN_A, "clerk")]; st2["roles"] = [role_row(TEN_A, "clerk", {"scope": "store"})]
wire(st2)
try:
    run(core.failures_grouped(authorization="Bearer good", x_active_org="", reviewed=""))
    check("10c. non-admin → 403 on /failures/grouped", False)
except HTTPException as e:
    check("10c. non-admin → 403 on /failures/grouped", e.status_code == 403)

# ── 11-13: SUPPORT endpoints (cross-tenant, house-gated; super-admin approve) ──────────────────────
def support_store():
    st = fresh_store()
    st["failure_log"] = [flog(TEN_A, "face_mismatch"), flog(TEN_B, "face_mismatch"), flog(TEN_B, "system_error", "error")]
    return st

# tenant user → 403
st = support_store()
st["app_users"] = [membership(TEN_A, "admin")]     # tenant admin, NO house row
st["roles"] = [role_row(TEN_A, "admin", {"modules": {"support": True}, "scope": "all"})]
wire(st)
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)
try:
    run(hd.support_failures(authorization="Bearer good", x_active_org=""))
    check("11a. tenant user → 403 on support failures view", False)
except HTTPException as e:
    check("11a. tenant user → 403 on support failures view", e.status_code == 403)

# house support agent → cross-tenant view
st = support_store()
st["app_users"] = [membership(HOUSE, "support_agent")]
st["roles"] = [role_row(HOUSE, "support_agent", {"modules": {"support": True}, "scope": "store"})]
wire(st)
d = run(hd.support_failures(authorization="Bearer good", x_active_org=""))
tnames = {r["tenant_name"] for r in d["rows"]}
check("11b. house support sees CROSS-TENANT rows w/ tenant names", d["total"] == 3 and {"Alpha Retail", "Bravo Wireless"} <= tnames)
fm_g = next((g for g in d["groups"] if g["kind"] == "face_mismatch"), None)
check("11c. group affected_orgs carry tenant names", fm_g and all(o.get("org_name") for o in fm_g["affected_orgs"]))

# cross-tenant bulk review by id
some = [st["failure_log"][0]["id"], st["failure_log"][1]["id"]]  # one A, one B
run(hd.support_failures_bulk_review({"ids": some, "reviewed": True}, authorization="Bearer good", x_active_org=""))
by = {r["id"]: r for r in st["failure_log"]}
check("12. support bulk-review clears across tenants by id", by[some[0]]["reviewed"] is True and by[some[1]]["reviewed"] is True)

# fix-request lifecycle (support-created, HOUSE-owned)
cr = run(hd.support_create_fix_request({
    "kind": "face_mismatch", "module": "storeops", "title": "Face rejects fleet-wide",
    "sample_failure_ids": [st["failure_log"][0]["id"], st["failure_log"][1]["id"]],
    "affected_orgs": [{"org_id": TEN_A, "count": 1}, {"org_id": TEN_B, "count": 1}]},
    authorization="Bearer good", x_active_org=""))
frid = cr["id"]; frrow = next(r for r in st["support_fix_request"] if r["id"] == frid)
check("13a. support create → HOUSE-owned, pending_approval, affected_orgs kept",
      frrow["org_id"] == HOUSE and frrow["status"] == "pending_approval" and len(frrow["affected_orgs"]) == 2)

# non-super support agent CANNOT approve
lst = run(hd.support_list_fix_requests(authorization="Bearer good", x_active_org=""))
check("13b. list can_approve=false for non-super support", lst["can_approve"] is False)
try:
    run(hd.support_fix_request_status(frid, {"status": "approved"}, authorization="Bearer good", x_active_org=""))
    check("13c. non-super approve → 403 (approval gate)", False)
except HTTPException as e:
    check("13c. non-super approve → 403 (approval gate)", e.status_code == 403)

# super_admin CAN approve
st["app_users"] = [membership(HOUSE, "admin", super_admin=True, email="owner@house.com")]
wire(st)
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)
lst = run(hd.support_list_fix_requests(authorization="Bearer good", x_active_org=""))
run(hd.support_fix_request_status(frid, {"status": "approved"}, authorization="Bearer good", x_active_org=""))
frrow = next(r for r in st["support_fix_request"] if r["id"] == frid)
check("13d. super_admin approve OK (can_approve + approved_by stamped)",
      lst["can_approve"] is True and frrow["status"] == "approved" and frrow["approved_by"] == "owner@house.com")

# approved queue is readable via status=approved
queue = run(hd.support_list_fix_requests(authorization="Bearer good", x_active_org="", status="approved"))
check("13e. approved fix requests form the queue", any(x["id"] == frid for x in queue["fix_requests"]))

# resolve + mark_reviewed clears the clubbed failures
run(hd.support_fix_request_status(frid, {"status": "in_progress"}, authorization="Bearer good", x_active_org=""))
run(hd.support_fix_request_status(frid, {"status": "resolved", "resolution": "fixed the enrollment", "mark_reviewed": True},
                                  authorization="Bearer good", x_active_org=""))
frrow = next(r for r in st["support_fix_request"] if r["id"] == frid)
clubbed = frrow["sample_failure_ids"]
by = {r["id"]: r for r in st["failure_log"]}
check("13f. resolve stamps resolution + resolved_at", frrow["status"] == "resolved" and frrow["resolution"] == "fixed the enrollment" and frrow.get("resolved_at"))
check("13g. resolve+mark_reviewed clears the clubbed failure rows", all(by[i]["reviewed"] is True for i in clubbed))

# reject path (fresh request, super_admin)
cr2 = run(hd.support_create_fix_request({"kind": "system_error", "title": "noise"}, authorization="Bearer good", x_active_org=""))
run(hd.support_fix_request_status(cr2["id"], {"status": "rejected"}, authorization="Bearer good", x_active_org=""))
rej = next(r for r in st["support_fix_request"] if r["id"] == cr2["id"])
check("13h. reject path → rejected + approved_by stamped", rej["status"] == "rejected" and rej["approved_by"] == "owner@house.com")

# ── 15: APPROVAL GATE AT CREATION (Gate-1 follow-up) — non-super cannot POST straight to 'approved' ──
st = support_store()
st["app_users"] = [membership(HOUSE, "support_agent", email="agent@house.com")]     # NON-super house support
st["roles"] = [role_row(HOUSE, "support_agent", {"modules": {"support": True}, "scope": "store"})]
wire(st)
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)

# (a) non-super POST status='approved' → clamped to pending_approval, NOT in the approved queue
ca = run(hd.support_create_fix_request({"kind": "face_mismatch", "title": "sneak approve", "status": "approved"},
                                       authorization="Bearer good", x_active_org=""))
rowa = next(r for r in st["support_fix_request"] if r["id"] == ca["id"])
check("15a. non-super create status='approved' → clamped to pending_approval",
      ca["status"] == "pending_approval" and rowa["status"] == "pending_approval")
check("15a2. clamped row carries NO approval stamp", not rowa.get("approved_by") and not rowa.get("approved_at"))
qa = run(hd.support_list_fix_requests(authorization="Bearer good", x_active_org="", status="approved"))
check("15a3. approved automation queue does NOT contain the sneaked request",
      all(x["id"] != ca["id"] for x in qa["fix_requests"]))

# (b) non-super POST status='rejected' → clamped to pending_approval too
cb = run(hd.support_create_fix_request({"kind": "system_error", "title": "sneak reject", "status": "rejected"},
                                       authorization="Bearer good", x_active_org=""))
rowb = next(r for r in st["support_fix_request"] if r["id"] == cb["id"])
check("15b. non-super create status='rejected' → clamped to pending_approval",
      cb["status"] == "pending_approval" and rowb["status"] == "pending_approval")

# (c) super_admin MAY create directly-approved → allowed + approved_by/approved_at stamped
st["app_users"] = [membership(HOUSE, "admin", super_admin=True, email="owner@house.com")]
wire(st)
core._uid_from_token = lambda auth: ("uid-1" if auth == "Bearer good" else None)
cc = run(hd.support_create_fix_request({"kind": "face_mismatch", "title": "owner pre-approved", "status": "approved"},
                                       authorization="Bearer good", x_active_org=""))
rowc = next(r for r in st["support_fix_request"] if r["id"] == cc["id"])
check("15c. super_admin create status='approved' → allowed",
      cc["status"] == "approved" and rowc["status"] == "approved")
check("15c2. super_admin pre-approve-at-create stamps approved_by/approved_at",
      rowc.get("approved_by") == "owner@house.com" and bool(rowc.get("approved_at")))
qc = run(hd.support_list_fix_requests(authorization="Bearer good", x_active_org="", status="approved"))
check("15c3. super_admin's pre-approved request IS in the automation queue",
      any(x["id"] == cc["id"] for x in qc["fix_requests"]))

# ── 14: SQL sanity — mig 716 failure_kind_doc INSERT arity ────────────────────────────────────────
def _split_top_level(inner):
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


_mig = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "migrations", "716_core_failure_triage.sql")
with open(_mig, encoding="utf-8") as _fh:
    _sql = _fh.read()
_m = _re.search(r"INSERT INTO core\.failure_kind_doc\s*\((.*?)\)\s*VALUES(.*?)ON CONFLICT", _sql, _re.S)
check("14a. found the failure_kind_doc INSERT block", bool(_m))
if _m:
    _cols = [c.strip() for c in _m.group(1).split(",") if c.strip()]
    _tuples = _parse_tuples(_m.group(2))
    _arities = [len(_split_top_level(t)) for t in _tuples]
    check(f"14b. every seed row has {len(_cols)} values (== column count); 9 kinds seeded",
          bool(_tuples) and len(_tuples) == 9 and all(x == len(_cols) for x in _arities),
          f"cols={len(_cols)} rows={len(_tuples)} arities={_arities}")

# ── summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
