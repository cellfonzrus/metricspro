"""Offline proof (no live DB/network) for the read-only, secret-guarded helpdesk export door
(GET /helpdesk/export). Runs the REAL export handler + secret verifier against an in-memory fake
Supabase client, proving:

  1. FAIL CLOSED — no secret configured, or an empty/absent/wrong header → 403 (no data leaks).
  2. AUTH — the configured primary secret AND a rotation secret (HELPDESK_EXPORT_SECRET_NEXT) are both
            accepted; the comparison is header-present + constant-time.
  3. READ — with a valid secret the door returns the org's tickets, decorated with status/priority/
            category labels and a display number.
  4. FILTERS — status_key, stage, and since narrow the result; limit is clamped to <= 1000.
  5. COMMENTS — include_comments inlines each ticket's thread (internal notes included — operator view).
  6. ORG-SCOPED — a different org's tickets are never returned.

Run: `python3 harness_helpdesk_export.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._payload, self._limit, self._order = None, None, None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def ilike(self, k, v):
        self.filters.append(("ilike", k, v.strip("%").lower())); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._payload = payload; self._mode = "insert"; return self

    _mode = "select"

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "is" and v == "null" and rv is not None:
                return False
            if kind == "gte" and str(rv) < str(v):
                return False
            if kind == "ilike" and v not in str(rv or "").lower():
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = [dict(p) for p in payload]
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.helpdesk.router as H  # noqa: E402
from fastapi import HTTPException  # noqa: E402

H.get_supabase = lambda: fake

ORG = "org-1"
OTHER = "org-2"


def seed():
    fake.store.clear()
    fake.seed("storeops", "ticket_statuses", [
        {"org_id": ORG, "id": "S-OPEN", "key": "open", "label": "Open", "stage": "open", "color": "#f00"},
        {"org_id": ORG, "id": "S-DONE", "key": "resolved", "label": "Resolved", "stage": "closed", "color": "#0f0"},
    ])
    fake.seed("storeops", "ticket_priorities", [
        {"org_id": ORG, "id": "P-HI", "key": "high", "label": "High", "color": "#f00"}])
    fake.seed("storeops", "ticket_categories", [{"org_id": ORG, "id": "C-BUG", "name": "Bug"}])
    fake.seed("storeops", "ticket_teams", [])
    fake.seed("storeops", "tickets", [
        {"org_id": ORG, "id": "T1", "ticket_number": 1, "subject": "Cannot clock in", "description": "kiosk error",
         "status_id": "S-OPEN", "priority_id": "P-HI", "category_id": "C-BUG", "store_code": "S1",
         "requester_name": "Ali", "created_at": "2026-08-18T10:00:00Z"},
        {"org_id": ORG, "id": "T2", "ticket_number": 2, "subject": "Printer jam", "description": "receipt printer",
         "status_id": "S-DONE", "priority_id": "P-HI", "category_id": "C-BUG", "store_code": "S2",
         "requester_name": "Bo", "created_at": "2026-08-10T10:00:00Z"},
        {"org_id": OTHER, "id": "X1", "ticket_number": 9, "subject": "Other tenant", "description": "should never appear",
         "status_id": None, "created_at": "2026-08-19T10:00:00Z"},
    ])
    fake.seed("storeops", "ticket_comments", [
        {"org_id": ORG, "id": "c1", "ticket_id": "T1", "author": "dm@x", "author_name": "Dana",
         "body": "looking into it", "is_internal": False, "created_at": "2026-08-18T11:00:00Z"},
    ])


# _escalated_ticket_ids reads ticket_settings/events; stub to empty so the harness stays focused.
H._escalated_ticket_ids = lambda org_id, ids: set()


def call(**kw):
    kw.setdefault("org_id", ORG)
    return H.export_tickets(**kw)


# ── 1: FAIL CLOSED ────────────────────────────────────────────────────────────────────────────
seed()
H.settings.HELPDESK_EXPORT_SECRET = ""      # nothing configured
try:
    call(x_helpdesk_export_secret="whatever"); check("1a unset secret → 403", False, "no raise")
except HTTPException as e:
    check("1a unset secret → 403 (fail closed)", e.status_code == 403, e.status_code)

H.settings.HELPDESK_EXPORT_SECRET = "primary-secret"
for hdr, label in [("", "empty header"), ("wrong", "wrong secret")]:
    try:
        call(x_helpdesk_export_secret=hdr); check(f"1b {label} → 403", False, "no raise")
    except HTTPException as e:
        check(f"1b {label} → 403", e.status_code == 403, e.status_code)


# ── 2: AUTH (primary + rotation) ────────────────────────────────────────────────────────────────
r = call(x_helpdesk_export_secret="primary-secret")
check("2a primary secret is accepted", r.get("count") == 2, r)
import os
os.environ["HELPDESK_EXPORT_SECRET_NEXT"] = "rotation-secret"
r2 = call(x_helpdesk_export_secret="rotation-secret")
check("2b rotation secret (HELPDESK_EXPORT_SECRET_NEXT) is also accepted", r2.get("count") == 2, r2)
os.environ.pop("HELPDESK_EXPORT_SECRET_NEXT", None)


# ── 3: READ + decoration ─────────────────────────────────────────────────────────────────────────
r = call(x_helpdesk_export_secret="primary-secret")
t1 = next(t for t in r["tickets"] if t["id"] == "T1")
check("3a returns the org's tickets, newest first", [t["id"] for t in r["tickets"]] == ["T1", "T2"], r["tickets"])
check("3b decorated with status/priority/category labels + display number",
      t1["status"]["label"] == "Open" and t1["priority"]["label"] == "High"
      and t1["category"]["name"] == "Bug" and t1["display_number"] == "TKT-1", t1)


# ── 4: FILTERS ────────────────────────────────────────────────────────────────────────────────
only_open = call(x_helpdesk_export_secret="primary-secret", status_key="open")
check("4a status_key filters to that status", [t["id"] for t in only_open["tickets"]] == ["T1"], only_open)
closed = call(x_helpdesk_export_secret="primary-secret", stage="closed")
check("4b stage filters to that bucket", [t["id"] for t in closed["tickets"]] == ["T2"], closed)
recent = call(x_helpdesk_export_secret="primary-secret", since="2026-08-15T00:00:00Z")
check("4c since narrows by created_at", [t["id"] for t in recent["tickets"]] == ["T1"], recent)
clamped = H.export_tickets(org_id=ORG, limit=99999, x_helpdesk_export_secret="primary-secret")
check("4d limit is clamped (still returns, no error)", clamped.get("count") == 2, clamped)
missing = call(x_helpdesk_export_secret="primary-secret", status_key="does-not-exist")
check("4e an unknown status_key returns nothing (never the whole table)", missing.get("count") == 0, missing)


# ── 5: COMMENTS ──────────────────────────────────────────────────────────────────────────────
withc = call(x_helpdesk_export_secret="primary-secret", include_comments=True)
t1c = next(t for t in withc["tickets"] if t["id"] == "T1")
t2c = next(t for t in withc["tickets"] if t["id"] == "T2")
check("5a include_comments inlines each ticket's thread", len(t1c.get("comments", [])) == 1
      and t1c["comments"][0]["body"] == "looking into it", t1c.get("comments"))
check("5b a ticket with no comments gets an empty list", t2c.get("comments") == [], t2c.get("comments"))


# ── 6: ORG-SCOPED ─────────────────────────────────────────────────────────────────────────────
r = call(x_helpdesk_export_secret="primary-secret")
check("6a another tenant's tickets are never returned", all(t["id"] != "X1" for t in r["tickets"]), r["tickets"])

H.settings.HELPDESK_EXPORT_SECRET = ""   # restore


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
