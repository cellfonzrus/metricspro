"""Offline proof (no live DB/network) for Internal Chat Phase 1 (migration 868). Runs the REAL chat
handlers against an in-memory fake Supabase client, proving:

  1. DM idempotency — open_dm for the same member set returns the SAME channel (created once).
  2. Send — sender is stamped from the token; membership is required; posting marks the sender caught up.
  3. Membership — a non-member cannot read or post in a private conversation; an OPEN channel auto-joins
     a sender.
  4. Messages — returned chronologically; ?before paginates backward.
  5. Unread — a recipient accrues unread; mark_read clears it; unread endpoint totals across channels.
  6. Sidebar — my_channels lists the caller's conversations with last-message preview + unread, newest
     active first.

Run: `python3 harness_chat.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


_CLOCK = [0]


def _tick():
    _CLOCK[0] += 1
    return f"2026-08-19T00:00:{_CLOCK[0]:02d}Z"


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload, self._limit, self._order = "select", None, None, None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode, self._payload = "insert", payload; return self

    def update(self, payload):
        self._mode, self._payload = "update", payload; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                r = dict(p)
                r.setdefault("id", f"{self.key[1]}-{len(rows) + len(out) + 1}")
                # stamp created_at/updated_at monotonically so ordering is deterministic
                if self.key[1] in ("chat_messages", "chat_channels") and "created_at" not in r:
                    r["created_at"] = _tick()
                if self.key[1] == "chat_channels":
                    r.setdefault("archived", False)   # emulate the DB column default
                out.append(r)
            # UNIQUE(dm_key) + UNIQUE(channel,employee) emulation
            if self.key[1] == "chat_channels":
                for r in out:
                    if r.get("dm_key") and any(x.get("dm_key") == r["dm_key"] for x in rows):
                        raise Exception("duplicate dm_key")
            if self.key[1] == "chat_members":
                for r in out:
                    if any(x.get("channel_id") == r["channel_id"] and x.get("employee_id") == r["employee_id"] for x in rows):
                        raise Exception("duplicate member")
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, _n):
        return FakeSchema(self.store)


fake = FakeClient()

import app.modules.chat.router as C  # noqa: E402
import app.modules.storeops.router as S  # noqa: E402
from fastapi import HTTPException  # noqa: E402

C.get_supabase = lambda: fake

ORG = "org-c"
CURRENT = ["E1"]   # the "signed-in" employee; flip to simulate different callers
S._caller_identity = lambda auth: (ORG, CURRENT[0])


def as_user(eid):
    CURRENT[0] = eid


def seed():
    fake.store.clear()
    fake.store[("storeops", "employees")] = [
        {"org_id": ORG, "employee_id": "E1", "name": "Alice", "is_active": True},
        {"org_id": ORG, "employee_id": "E2", "name": "Bob", "is_active": True},
        {"org_id": ORG, "employee_id": "E3", "name": "Cara", "is_active": True},
    ]


AUTH = "Bearer x"


# ── 1: DM idempotency ───────────────────────────────────────────────────────────────────────────
seed(); as_user("E1")
d1 = C.open_dm({"employee_id": "E2"}, authorization=AUTH, org_id=ORG)
check("1a opening a DM creates a channel", d1["created"] is True and d1["channel"]["kind"] == "dm", d1)
as_user("E2")
d2 = C.open_dm({"employee_id": "E1"}, authorization=AUTH, org_id=ORG)   # reversed order, same pair
check("1b re-opening the same DM (either direction) returns the SAME channel, not a new one",
      d2["created"] is False and d2["channel"]["id"] == d1["channel"]["id"], (d1, d2))
DM = d1["channel"]["id"]


# ── 2 + 3: send, sender stamped, membership ──────────────────────────────────────────────────────
as_user("E1")
m1 = C.send_message(DM, {"body": "hi Bob"}, authorization=AUTH, org_id=ORG)
check("2a send stamps the sender from the token (not the body)",
      m1["message"]["sender_employee_id"] == "E1" and m1["message"]["sender_name"] == "Alice", m1)

as_user("E3")   # Cara is NOT in this DM
try:
    C.send_message(DM, {"body": "butting in"}, authorization=AUTH, org_id=ORG)
    check("3a a non-member cannot post to a private DM", False, "no raise")
except HTTPException as e:
    check("3a a non-member cannot post to a private DM", e.status_code == 403, e.status_code)
try:
    C.list_messages(DM, authorization=AUTH, org_id=ORG)
    check("3b a non-member cannot read a private DM", False, "no raise")
except HTTPException as e:
    check("3b a non-member cannot read a private DM", e.status_code == 403, e.status_code)

# an OPEN (public) channel auto-joins a poster
as_user("E1")
pub = C.create_channel({"name": "general", "is_private": False}, authorization=AUTH, org_id=ORG)["channel"]["id"]
as_user("E3")
C.send_message(pub, {"body": "hello all"}, authorization=AUTH, org_id=ORG)
check("3c posting to an OPEN channel auto-joins the sender", C._is_member(ORG, pub, "E3") is True)


# ── 4: messages chronological + pagination ───────────────────────────────────────────────────────
as_user("E1")
C.send_message(DM, {"body": "second"}, authorization=AUTH, org_id=ORG)
C.send_message(DM, {"body": "third"}, authorization=AUTH, org_id=ORG)
msgs = C.list_messages(DM, authorization=AUTH, org_id=ORG)["messages"]
check("4a messages come back oldest→newest", [m["body"] for m in msgs] == ["hi Bob", "second", "third"], [m["body"] for m in msgs])
page = C.list_messages(DM, limit=1, before=msgs[-1]["created_at"], authorization=AUTH, org_id=ORG)["messages"]
check("4b ?before paginates backward", [m["body"] for m in page] == ["second"], [m["body"] for m in page])


# ── 5: unread + mark_read ────────────────────────────────────────────────────────────────────────
as_user("E2")   # Bob has not read the 3 messages Alice sent
u = C.unread_count(authorization=AUTH, org_id=ORG)
check("5a a recipient accrues unread for messages after their last_read", u["by_channel"].get(DM) == 3, u)
C.mark_read(DM, authorization=AUTH, org_id=ORG)
u2 = C.unread_count(authorization=AUTH, org_id=ORG)
check("5b mark_read clears the unread badge", u2["by_channel"].get(DM) == 0, u2)
as_user("E1")   # Alice sent them, so she is already caught up (send marks the sender read)
check("5c the SENDER is caught up on their own messages", C.unread_count(authorization=AUTH, org_id=ORG)["by_channel"].get(DM) == 0)


# ── 6: sidebar (my_channels) ─────────────────────────────────────────────────────────────────────
as_user("E1")
chans = C.my_channels(authorization=AUTH, org_id=ORG)["channels"]
dm = next(c for c in chans if c["id"] == DM)
check("6a my_channels lists the caller's conversations with a last-message preview",
      dm["last_message"]["preview"] == "third", dm.get("last_message"))
check("6b DM membership is surfaced for naming", set(dm["members"]) == {"E1", "E2"}, dm["members"])
check("6c the general channel Alice created is listed too", any(c["id"] == pub for c in chans), [c.get("name") for c in chans])


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
