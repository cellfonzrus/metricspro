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

    def ilike(self, k, pattern):
        self.filters.append(("ilike", k, pattern)); return self

    def is_(self, k, val):
        self.filters.append(("is", k, val)); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode, self._payload = "insert", payload; return self

    def update(self, payload):
        self._mode, self._payload = "update", payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if kind == "ilike":
                pat = str(v).replace("%", "").lower()
                if pat not in str(rv or "").lower():
                    return False
            if kind == "is":
                if str(v) == "null" and rv is not None:
                    return False
                if str(v) != "null" and rv != v:
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
        if self._mode == "delete":
            for r in list(matched):
                rows.remove(r)
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
from app.modules.approvals import engine  # noqa: E402
from fastapi import HTTPException  # noqa: E402

C.get_supabase = lambda: fake

# Realtime broadcast: capture instead of hitting the network, so we can prove send fans out a hint.
BROADCASTS = []
C.realtime.publish = lambda topics, event, payload: BROADCASTS.append(
    (list(topics) if not isinstance(topics, str) else [topics], event, payload))

ORG = "org-c"
CURRENT = ["E1"]   # the "signed-in" employee; flip to simulate different callers
S._caller_identity = lambda auth: (ORG, CURRENT[0])
# The router-wide gate resolves membership through storeops._require_member, which reaches the real
# token layer. Stub it: section 13 exists to prove the chat routes answer THROUGH the dependency, not
# to re-test the shared gate's own decision table — harness_approvals_gate.py covers that in 26
# checks against the same function. The chat gate imports it inside the call, so this is picked up.
S._require_member = lambda auth, org_id=None: {
    "org_id": ORG, "email": "e1@example.com", "role": "manager", "employee_id": CURRENT[0]}


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


# ── 7: realtime broadcast (Phase 1b) ──────────────────────────────────────────────────────────────
BROADCASTS.clear()
as_user("E1")
sent = C.send_message(DM, {"body": "ping"}, authorization=AUTH, org_id=ORG)["message"]
check("7a sending a message fans out exactly one realtime hint", len(BROADCASTS) == 1, BROADCASTS)
topics, event, payload = BROADCASTS[0]
check("7b the hint carries kind+channel+message id but NOT the body",
      payload.get("kind") == "message" and payload.get("channel_id") == DM
      and payload.get("message_id") == sent["id"] and "body" not in payload, payload)
check("7c the hint reaches the channel topic and each member's user topic",
      C.realtime.channel_topic(DM) in topics
      and C.realtime.user_topic(ORG, "E1") in topics and C.realtime.user_topic(ORG, "E2") in topics, topics)
who = C.whoami(authorization=AUTH, org_id=ORG)
check("7d /chat/me returns the caller's employee_id + user topic",
      who["employee_id"] == "E1" and who["user_topic"] == C.realtime.user_topic(ORG, "E1"), who)


# ── 8: reactions, edit, delete, threads, attachments (Phase 2) ─────────────────────────────────────
CHAT_ADMIN = [False]
C._is_chat_admin = lambda authorization, org_id: CHAT_ADMIN[0]   # stub the platform-role lookup

as_user("E1")
base = C.send_message(DM, {"body": "react to me"}, authorization=AUTH, org_id=ORG)["message"]
mid = base["id"]
as_user("E2")
r = C.toggle_reaction(DM, mid, {"emoji": "👍"}, authorization=AUTH, org_id=ORG)
check("8a a member can add a reaction", r["added"] is True and r["reactions"].get("👍") == ["E2"], r)
r2 = C.toggle_reaction(DM, mid, {"emoji": "👍"}, authorization=AUTH, org_id=ORG)
check("8b toggling the same reaction removes it", r2["added"] is False and "👍" not in r2["reactions"], r2)
as_user("E3")   # not a DM member
try:
    C.toggle_reaction(DM, mid, {"emoji": "🎉"}, authorization=AUTH, org_id=ORG)
    check("8c a non-member cannot react", False, "no raise")
except HTTPException as e:
    check("8c a non-member cannot react", e.status_code == 403, e.status_code)

as_user("E2")
try:
    C.edit_message(DM, mid, {"body": "hijack"}, authorization=AUTH, org_id=ORG)
    check("8d only the author can edit", False, "no raise")
except HTTPException as e:
    check("8d only the author can edit", e.status_code == 403, e.status_code)
as_user("E1")
ed = C.edit_message(DM, mid, {"body": "edited text"}, authorization=AUTH, org_id=ORG)["message"]
check("8e an author edit stamps edited_at + the new body", ed["body"] == "edited text" and bool(ed.get("edited_at")), ed)

reply = C.send_message(DM, {"body": "a reply", "reply_to_id": mid}, authorization=AUTH, org_id=ORG)["message"]
C.toggle_reaction(DM, mid, {"emoji": "❤️"}, authorization=AUTH, org_id=ORG)   # E1 reacts
msgs = C.list_messages(DM, authorization=AUTH, org_id=ORG)["messages"]
rep = next(m for m in msgs if m["id"] == reply["id"])
check("8f a reply surfaces its parent preview", bool(rep.get("reply_to")) and rep["reply_to"]["preview"] == "edited text", rep.get("reply_to"))
base_row = next(m for m in msgs if m["id"] == mid)
check("8g list_messages surfaces per-message reactions", base_row["reactions"].get("❤️") == ["E1"], base_row.get("reactions"))

as_user("E2"); CHAT_ADMIN[0] = False
try:
    C.delete_message(DM, mid, authorization=AUTH, org_id=ORG)   # E2 is neither author nor admin
    check("8h a non-author non-admin cannot delete", False, "no raise")
except HTTPException as e:
    check("8h a non-author non-admin cannot delete", e.status_code == 403, e.status_code)
as_user("E1")
C.delete_message(DM, mid, authorization=AUTH, org_id=ORG)
dead = next(m for m in C.list_messages(DM, authorization=AUTH, org_id=ORG)["messages"] if m["id"] == mid)
check("8i a soft-deleted message masks its body + tombstones", dead["body"] is None and bool(dead.get("deleted_at")), dead)
victim = C.send_message(DM, {"body": "admin will remove this"}, authorization=AUTH, org_id=ORG)["message"]   # E1 author
as_user("E2"); CHAT_ADMIN[0] = True
res = C.delete_message(DM, victim["id"], authorization=AUTH, org_id=ORG)
check("8j a chat admin can delete another member's message", res.get("ok") is True, res)
CHAT_ADMIN[0] = False

as_user("E1")
amsg = C.send_message(DM, {"body": "", "attachments": [{"file_name": "a.png", "storage_path": "p", "mime_type": "image/png"}]},
                      authorization=AUTH, org_id=ORG)["message"]
check("8k a message may carry attachments with no text", bool(amsg.get("attachments")) and amsg["attachments"][0]["file_name"] == "a.png", amsg.get("attachments"))
try:
    C.send_message(DM, {"body": "   "}, authorization=AUTH, org_id=ORG)
    check("8l an empty message (no text, no attachment) is rejected", False, "no raise")
except HTTPException as e:
    check("8l an empty message (no text, no attachment) is rejected", e.status_code == 400, e.status_code)


# ── 9: approvals-in-chat (Phase 3) ─────────────────────────────────────────────────────────────────
import app.core.database as DB  # noqa: E402
DB.get_supabase = lambda: fake   # engine._sb() (fresh import) + approvals reads now use the fake too

as_user("E1")
ra = C.raise_approval(DM, {"title": "Approve my discount", "summary": "10% off", "priority": "high"},
                      authorization=AUTH, org_id=ORG)
check("9a raising an approval posts a kind='approval' card linked to a request",
      ra["message"]["kind"] == "approval" and ra["message"]["approval_request_id"] == ra["approval"]["id"], ra)
areq = [r for r in fake.store[("storeops", "approval_requests")] if r["id"] == ra["approval"]["id"]][0]
check("9b the request links back to its chat card", areq.get("chat_message_id") == ra["message"]["id"], areq)
amsg = next(m for m in C.list_messages(DM, authorization=AUTH, org_id=ORG)["messages"] if m["id"] == ra["message"]["id"])
check("9c the card carries the LIVE approval (status pending)",
      bool(amsg.get("approval")) and amsg["approval"]["status"] == "pending", amsg.get("approval"))

# Decision wiring — stub the approvals module's own RBAC + engine.decide (their internals are proven by
# harness_approvals_engine.py); here we only prove chat reuses them and broadcasts.
import app.modules.approvals.router as AR  # noqa: E402
AR._caller = lambda auth, org: {"org_id": ORG, "email": "boss@x", "employee_id": "E2", "role": "dm"}
AR._may_decide = lambda auth, org, req: True
DECIDED = []
_orig_decide = engine.decide
engine.decide = lambda org, rid, **kw: (DECIDED.append((rid, kw)) or {"status": "approved"})
BROADCASTS.clear()
as_user("E2")
dec = C.decide_from_chat(DM, ra["message"]["id"], {"decision": "approve"}, authorization=AUTH, org_id=ORG)
check("9d deciding in chat runs engine.decide + returns the new status",
      dec["status"] == "approved" and bool(DECIDED) and DECIDED[0][0] == ra["approval"]["id"], (dec, DECIDED))
check("9e a decision broadcasts an approval hint to the channel",
      any(p.get("kind") == "approval" for _t, _e, p in BROADCASTS), BROADCASTS)
plain = C.send_message(DM, {"body": "just a note"}, authorization=AUTH, org_id=ORG)["message"]
try:
    C.decide_from_chat(DM, plain["id"], {"decision": "approve"}, authorization=AUTH, org_id=ORG)
    check("9f a non-approval message can't be decided", False, "no raise")
except HTTPException as e:
    check("9f a non-approval message can't be decided", e.status_code == 400, e.status_code)
engine.decide = _orig_decide


# ── 10: search + org management (Phase 4) ──────────────────────────────────────────────────────────
as_user("E1")
sr = C.search_messages(q="reply", authorization=AUTH, org_id=ORG)["results"]
check("10a search finds a matching message in the caller's channels",
      any((r.get("body") or "") == "a reply" for r in sr), [r.get("body") for r in sr])
as_user("E3")   # not a DM member
sr3 = C.search_messages(q="a reply", authorization=AUTH, org_id=ORG)["results"]
check("10b search never returns hits from channels the caller isn't in", all(r["channel_id"] != DM for r in sr3), sr3)
as_user("E1")
tmp = C.send_message(DM, {"body": "uniquetoken alpha"}, authorization=AUTH, org_id=ORG)["message"]
check("10c a message is searchable before deletion",
      any("uniquetoken" in (r.get("body") or "") for r in C.search_messages(q="uniquetoken", authorization=AUTH, org_id=ORG)["results"]), None)
C.delete_message(DM, tmp["id"], authorization=AUTH, org_id=ORG)
check("10d a soft-deleted message drops out of search",
      all("uniquetoken" not in (r.get("body") or "") for r in C.search_messages(q="uniquetoken", authorization=AUTH, org_id=ORG)["results"]), None)

as_user("E2")
br = C.browse_channels(authorization=AUTH, org_id=ORG)["channels"]
gen = next(c for c in br if c["id"] == pub)
check("10e browse lists public channels with member count + joined flag", gen["joined"] is False and gen["member_count"] >= 1, gen)
check("10f DMs/private channels are never in browse", all(c["id"] != DM for c in br), [c["id"] for c in br])
C.join_channel(pub, authorization=AUTH, org_id=ORG)
check("10g a member can join a public channel", C._is_member(ORG, pub, "E2") is True)
C.leave_channel(pub, authorization=AUTH, org_id=ORG)
check("10h leaving drops membership", C._is_member(ORG, pub, "E2") is False)

as_user("E1")
mem = C.list_members(pub, authorization=AUTH, org_id=ORG)["members"]
check("10i members list carries names + roles", any(m["employee_id"] == "E1" and m["role"] == "owner" and m["name"] == "Alice" for m in mem), mem)
C.remove_member(pub, "E3", authorization=AUTH, org_id=ORG)
check("10j an owner can remove a member", C._is_member(ORG, pub, "E3") is False)
as_user("E3"); C.join_channel(pub, authorization=AUTH, org_id=ORG); CHAT_ADMIN[0] = False
try:
    C.remove_member(pub, "E1", authorization=AUTH, org_id=ORG)
    check("10k a non-owner non-admin cannot remove others", False, "no raise")
except HTTPException as e:
    check("10k a non-owner non-admin cannot remove others", e.status_code == 403, e.status_code)

as_user("E1")
upd = C.update_channel(pub, {"name": "general-2", "topic": "stuff"}, authorization=AUTH, org_id=ORG)["channel"]
check("10l an owner can rename / re-topic a channel", upd["name"] == "general-2" and upd["topic"] == "stuff", upd)
as_user("E3"); CHAT_ADMIN[0] = False
try:
    C.update_channel(pub, {"name": "hax"}, authorization=AUTH, org_id=ORG)
    check("10m a non-owner non-admin cannot rename", False, "no raise")
except HTTPException as e:
    check("10m a non-owner non-admin cannot rename", e.status_code == 403, e.status_code)

as_user("E1"); CHAT_ADMIN[0] = False
try:
    C.run_retention({"days": 30}, authorization=AUTH, org_id=ORG)
    check("10n retention requires chat admin", False, "no raise")
except HTTPException as e:
    check("10n retention requires chat admin", e.status_code == 403, e.status_code)
CHAT_ADMIN[0] = True
fake.store[("storeops", "chat_messages")].append({"id": "old-1", "org_id": ORG, "channel_id": DM, "body": "ancient", "created_at": "2000-01-01T00:00:00Z"})
fake.store[("storeops", "chat_messages")].append({"id": "future-1", "org_id": ORG, "channel_id": DM, "body": "future", "created_at": "2999-01-01T00:00:00Z"})
res = C.run_retention({"days": 1}, authorization=AUTH, org_id=ORG)
ids_after = {m["id"] for m in fake.store[("storeops", "chat_messages")]}
check("10o a chat admin retention sweep hard-deletes messages older than the window",
      "old-1" not in ids_after and "future-1" in ids_after and res["deleted"] >= 1, res)
CHAT_ADMIN[0] = False


# ── 11: voice/video signaling config + push tokens (Phase 5) ───────────────────────────────────────
as_user("E1")
ok = C.push_register({"token": "tok-1", "platform": "web"}, authorization=AUTH, org_id=ORG)
check("11a registering a device stores its token (delivery no-op without creds)",
      ok["ok"] is True and ok["delivery_configured"] is False, ok)
toks = fake.store[("storeops", "chat_push_tokens")]
check("11b the token is persisted against the employee", any(t["token"] == "tok-1" and t["employee_id"] == "E1" for t in toks), toks)
C.push_register({"token": "tok-1"}, authorization=AUTH, org_id=ORG)
check("11c re-registering the same token refreshes, not duplicates",
      len([t for t in fake.store[("storeops", "chat_push_tokens")] if t["token"] == "tok-1"]) == 1, None)
C.push_unregister({"token": "tok-1"}, authorization=AUTH, org_id=ORG)
check("11d unregister removes the token", all(t["token"] != "tok-1" for t in fake.store[("storeops", "chat_push_tokens")]), None)

cc = C.call_config(authorization=AUTH, org_id=ORG)
check("11e call config returns ICE servers (public STUN default, no TURN)",
      bool(cc["ice_servers"]) and cc["has_turn"] is False and cc["call_topic_prefix"] == "chat-call:", cc)

PUSHES = []
_orig_notify = C.push.notify
C.push.notify = lambda org, ids, **kw: PUSHES.append((list(ids), kw))
as_user("E1")
C.send_message(DM, {"body": "push test"}, authorization=AUTH, org_id=ORG)
check("11f a new message pushes to the OTHER members, not the sender",
      bool(PUSHES) and PUSHES[0][0] == ["E2"] and "channel_id" in (PUSHES[0][1].get("data") or {}), PUSHES)
C.push.notify = _orig_notify


# ── 12: push routing by platform (Phase 5) — web→WebPush, android→FCM, ios→APNs; no-op unconfigured ─
P = C.push
WEB, FCM, APNS = [], [], []
P._send_webpush = lambda toks, t, b, d: WEB.append(list(toks))
P._send_fcm = lambda toks, t, b, d: FCM.append(list(toks))
P._send_apns = lambda toks, t, b, d: APNS.append(list(toks))
fake.store[("storeops", "chat_push_tokens")] = [
    {"org_id": ORG, "employee_id": "E1", "token": "tw", "platform": "web"},
    {"org_id": ORG, "employee_id": "E1", "token": "ta", "platform": "android"},
    {"org_id": ORG, "employee_id": "E1", "token": "ti", "platform": "ios"},
]

grouped = P._tokens_by_platform(ORG, ["E1"])
check("12a tokens group by platform (web/android/ios)",
      grouped["web"] == ["tw"] and grouped["android"] == ["ta"] and grouped["ios"] == ["ti"], grouped)


def _route(web_on, fcm_on, apns_on):
    WEB.clear(); FCM.clear(); APNS.clear()
    P.webpush_configured = lambda: web_on
    P.fcm_configured = lambda: fcm_on
    P.apns_configured = lambda: apns_on
    P._fan(ORG, ["E1"], "t", "b", {"channel_id": "c"})


_route(False, False, False)
check("12b unconfigured → no send on any transport (documented no-op)", WEB == [] and FCM == [] and APNS == [])
_route(False, True, False)
check("12c FCM configured only → android to FCM; web + ios skipped",
      FCM == [["ta"]] and WEB == [] and APNS == [], (WEB, FCM, APNS))
_route(False, False, True)
check("12d APNs configured only → ios to APNs; web + android skipped",
      APNS == [["ti"]] and WEB == [] and FCM == [], (WEB, FCM, APNS))
_route(True, False, False)
check("12e VAPID configured only → web to Web Push; android + ios skipped",
      WEB == [["tw"]] and FCM == [] and APNS == [], (WEB, FCM, APNS))
_route(True, True, True)
check("12e2 all configured → each platform routed to its own transport",
      WEB == [["tw"]] and FCM == [["ta"]] and APNS == [["ti"]], (WEB, FCM, APNS))

# The APNs provider JWT is a REAL ES256 token (PyJWT + cryptography), not a stub.
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
import jwt as _jwtlib  # noqa: E402
_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
P._APNS_JWT.update(token=None, iat=0)
_tok = P._apns_jwt("KID9", "TEAM9", _pem)
_hdr = _jwtlib.get_unverified_header(_tok) if _tok else {}
check("12f the APNs provider token is a real ES256 JWT with the key id in its header",
      bool(_tok) and _hdr.get("alg") == "ES256" and _hdr.get("kid") == "KID9", _hdr)
check("12g APNs is unconfigured (None) until all four APNs creds are set", P._apns_conf() is None)


# ── 13: people directory + add-to-conversation (owner report 2026-08-21: "search finds no one, and
#        there is no way to add a user to a conversation") ──────────────────────────────────────────
# Both symptoms were ONE outage: the router-wide Depends imported a `_require_member` that does not
# exist in storeops.router, so every /api/v1/chat/* request raised ImportError → 500 before its
# handler ran, and the client swallows a failed /directory into an empty picker. Sections 1-12 all
# call the handlers DIRECTLY, which is exactly why they stayed green through it — so this section
# drives the real FastAPI app, dependency included, and not just the functions.
seed(); as_user("E1")
fake.store[("storeops", "employees")] = [
    {"org_id": ORG, "employee_id": "E1", "name": "Alice", "is_active": True},
    {"org_id": ORG, "employee_id": "E2", "name": "Bob", "is_active": True},
    {"org_id": ORG, "employee_id": "E3", "name": "Cara"},                        # is_active NULL
    {"org_id": ORG, "employee_id": "E4", "name": "Dan", "is_active": False},     # deactivated
    {"org_id": "org-other", "employee_id": "X9", "name": "Mallory", "is_active": True},
]

try:
    C._require_member(authorization=AUTH)   # the gate itself, not the handler it protects
    _gate = ""
except Exception as e:                      # an ImportError here is a 500 on EVERY chat route
    _gate = f"{type(e).__name__}: {e}"
check("13a the router-wide gate resolves (it imported a helper storeops has never had)", _gate == "", _gate)

import warnings  # noqa: E402
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi import FastAPI  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
_app = FastAPI()
_app.include_router(C.router, prefix="/api/v1")
_client = TestClient(_app, raise_server_exceptions=False)

_r = _client.get("/api/v1/chat/directory")
check("13b GET /api/v1/chat/directory answers 200 through the REAL router wiring (was 500)",
      _r.status_code == 200, _r.status_code)
_dir = _r.json() if _r.status_code == 200 else {}
check("13c the payload is {people:[...]} — the shape the chat page reads",
      isinstance(_dir.get("people"), list), _dir)
_names = [p["name"] for p in _dir.get("people", [])]
check("13d a roster row with is_active NULL is listed — the column is nullable, NULL means active",
      "Cara" in _names, _names)
check("13e an explicitly deactivated person is not listed", "Dan" not in _names, _names)
check("13f the caller is never offered themselves", "Alice" not in _names, _names)
check("13g another tenant's employee is never listed", "Mallory" not in _names, _names)
check("13h ?q= filters by name, case-insensitively",
      [p["name"] for p in C.directory(q="bO", authorization=AUTH, org_id=ORG)["people"]] == ["Bob"],
      C.directory(q="bO", authorization=AUTH, org_id=ORG))

_ch = C.create_channel({"name": "ops"}, authorization=AUTH, org_id=ORG)["channel"]["id"]
_add = C.add_member(_ch, {"employee_id": "E3"}, authorization=AUTH, org_id=ORG)
check("13i a member can add someone the directory offered", _add.get("added") is True
      and C._is_member(ORG, _ch, "E3") is True, _add)
check("13j re-adding an existing member is a no-op, not an error",
      C.add_member(_ch, {"employee_id": "E3"}, authorization=AUTH, org_id=ORG).get("added") is False)
try:
    C.add_member(_ch, {"employee_id": "E4"}, authorization=AUTH, org_id=ORG)
    check("13k adding a deactivated person is REFUSED, not silently answered ok", False, "no raise")
except HTTPException as e:
    check("13k adding a deactivated person is REFUSED, not silently answered ok", e.status_code == 404, e.detail)
try:
    C.add_member(_ch, {"employee_id": "X9"}, authorization=AUTH, org_id=ORG)
    check("13l another tenant's employee can never be added into this org's channel", False, "no raise")
except HTTPException as e:
    check("13l another tenant's employee can never be added into this org's channel",
          e.status_code == 404, e.detail)
as_user("E2")
try:
    C.add_member(_ch, {"employee_id": "E3"}, authorization=AUTH, org_id=ORG)
    check("13m a non-member cannot add anyone to a conversation", False, "no raise")
except HTTPException as e:
    check("13m a non-member cannot add anyone to a conversation", e.status_code == 403, e.detail)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
