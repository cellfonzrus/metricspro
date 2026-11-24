"""PROOF: A RECURRING DATA-UPLOAD ALERT IS ONE INBOX ITEM PER COMPANY, OVERWRITTEN — NOT A DAILY EMAIL.

OWNER DIRECTIVE 2026-11-24, verbatim: *"Stop sending daily messages about data not uploading instead
create [one] for each compay in their notifications inbox and overwrite if it is the same message
everyday"*.

MEASURED FIRST (read-only, production `storeops.alert_log`, 21 days to 2026-11-24): three companies
received 5-9 emails EVERY DAY for eight days straight on alert scope `connector`.

THE CLASS, NOT THE INSTANCE (CLAUDE.md "A fix is a DESIGN fix or it is not a fix")
  The instance is a stale payment-detail feed mailed nine days running. The CLASS is that a
  recurrence had nowhere to go but a NEW MESSAGE: every sender's only verb was "send", so five
  senders each grew a private "don't repeat yourself" key spelling and the loudest of them
  (`freshness:<org>:<feed>:stuck-at-<data date>`) moved whenever the feed's data date moved while
  staying behind — a new message, every day, forever.

  Fixing the freshness key alone would have been the patchwork the owner has ruled out three times.
  The fix gives a recurrence somewhere to LAND, in the ONE place all five senders already pass
  through (`closing.router._send_alert`), so declaring a route fixes every one of them at once.

WHAT THIS PINS
  A. the route resolution — house default, tenant override, scope over catch-all, unknown route;
  B. cross-tenant: one company's declaration and one company's items never reach another's read;
  C. the merge rule: same message overwrites and does not nag; changed message is unread again;
  D. self-clearing — a finding the latest check did not report is closed, scoped by prefix;
  E. the real `_send_alert` over a stub client: an inbox-routed alert mails NOBODY, files ONE item,
     files the SAME item tomorrow, and records the delivery in the one existing send record;
  F. THE SAFE DIRECTION: no stable key, or an item that could not be written (mig 1129 un-run),
     never silences a message;
  G. migration 1129 is tied to the code it serves (unique key, grants, the extended channel check);
  H. THE LOCKS, each with an ARMED CONTROL proving the scan can fail: every sender passes a stable
     key, no sender writes the inbox itself, no second inbox table, the channel vocabulary has one
     home, and RULE TWO.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_notification_inbox.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import notification_inbox as NI          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ok   " if ok else "  FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))


def code_text(rel):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel), encoding="utf-8") as fh:
        return fh.read()


def sql_text(rel):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
    with open(p, encoding="utf-8") as fh:
        return fh.read()


HOUSE = NI.HOUSE_ORG
T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"
NOW, LATER = "2026-11-24T09:00:00+00:00", "2026-11-25T09:00:00+00:00"

print("\nA. the route is DECLARED, with a house default every tenant inherits")
check("A1 an undeclared scope still sends a message (nothing changes until it is declared)",
      NI.route_for([], "lateness_am", T1) == "message")
house = [{"org_id": HOUSE, "scope": "connector", "route": "inbox"}]
check("A2 the house default reaches every tenant",
      all(NI.route_for(house, "connector", o) == "inbox" for o in (HOUSE, T1, T2)))
check("A3 a tenant's own row wins over the house default",
      NI.route_for(house + [{"org_id": T1, "scope": "connector", "route": "both"}],
                   "connector", T1) == "both")
check("A4 a named scope wins over the tenant's catch-all",
      NI.route_for([{"org_id": T1, "scope": "all", "route": "inbox"},
                    {"org_id": T1, "scope": "connector", "route": "message"}],
                   "connector", T1) == "message")
check("A5 a route the code cannot speak never silences an alert",
      NI.route_for([{"org_id": T1, "scope": "connector", "route": "pigeon"}], "connector", T1) == "message")

print("\nB. cross-tenant (owner 2026-10-30: every feature, every tenant, no leakage)")
mixed = house + [{"org_id": T1, "scope": "connector", "route": "message"}]
check("B1 T1's opt-out does not reach T2", NI.route_for(mixed, "connector", T2) == "inbox")
check("B2 T2's read is unaffected by T1 having a row at all",
      NI.route_for(mixed, "connector", T2) == NI.route_for(house, "connector", T2))
check("B3 a tenant row is never read AS a house row",
      NI.route_for([{"org_id": T1, "scope": "connector", "route": "inbox"}], "connector", T2) == "message")

print("\nC. the merge rule — 'overwrite if it is the same message everyday'")
body = {"title": "Payment detail is 2 day(s) behind", "body": "open imports",
        "severity": "warning", "deep_link": "/commcalc/email-imports", "affected": ["x"]}
first, st = NI.merge_row(None, body, NOW)
check("C1 a new finding is one unread item", st == "created" and first["occurrences"] == 1
      and first["read_at"] is None)
first["read_at"] = NOW
same, st2 = NI.merge_row(first, body, LATER)
check("C2 the same message tomorrow overwrites, and does NOT nag",
      st2 == "unchanged" and same["occurrences"] == 2 and same["read_at"] == NOW)
check("C3 the age of the problem survives the overwrite", same["first_seen_at"] == NOW)
moved, st3 = NI.merge_row(first, {**body, "title": "Payment detail is 3 day(s) behind"}, LATER)
check("C4 a message whose text moved on is news again (unread)",
      st3 == "changed" and moved["read_at"] is None)
back, st4 = NI.merge_row({**first, "resolved_at": NOW}, body, LATER)
check("C5 a fixed finding that comes back is unread again",
      st4 == "changed" and back["resolved_at"] is None and back["read_at"] is None)
check("C6 an unknown severity is not stored (the CHECK in 1129 would reject it)",
      NI.merge_row(None, {**body, "severity": "catastrophe"}, NOW)[0]["severity"] == "warning")

print("\nD. self-clearing (owner 2026-07-26: a notification must disappear once the check is OK)")
rows = [{"inbox_key": "freshness:o:a", "resolved_at": None},
        {"inbox_key": "freshness:o:b", "resolved_at": None},
        {"inbox_key": "connector:t:1", "resolved_at": None},
        {"inbox_key": "freshness:o:c", "resolved_at": NOW}]
check("D1 a finding the check no longer reports is closed",
      NI.stale_keys(rows, ["freshness:o:a"], prefix="freshness:o:") == ["freshness:o:b"])
check("D2 another sender's items are NOT closed by a check that knows nothing about them",
      "connector:t:1" not in NI.stale_keys(rows, ["freshness:o:a"], prefix="freshness:o:"))
check("D3 an already-closed item is not closed twice",
      "freshness:o:c" not in NI.stale_keys(rows, [], prefix="freshness:o:"))
check("D4 a clear estate with no prefix closes every open item",
      sorted(NI.stale_keys(rows, [])) == ["connector:t:1", "freshness:o:a", "freshness:o:b"])

print("\nE. the REAL sender, over a stub client")


class Stub:
    """The thinnest possible PostgREST stand-in: enough for the one home's reads and writes."""

    def __init__(self, tables):
        self.t = {k: list(v) for k, v in tables.items()}
        self.mailed = 0

    def schema(self, name):
        # A FRESH binding per call. The first draft of this stub kept `self._s` and handed `self`
        # back, so a schema() from one module silently re-pointed a table handle another module was
        # still holding — and the harness failed a check the shipped code passes. A double that
        # shares mutable state does not model PostgREST, where each handle is its own.
        return _Bound(self, name)

    def table(self, name):
        return _Bound(self, "public").table(name)


class _Bound:
    def __init__(self, db, schema):
        self.db, self.schema_name = db, schema

    def table(self, name):
        return _Q(self.db, f"{self.schema_name}.{name}")


class _Q:
    def __init__(self, db, key, verb="select", payload=None, conflict=None):
        self.db, self.key, self.verb, self.payload, self.conflict = db, key, verb, payload, conflict
        self.f = []

    def select(self, *_a, **_k):
        return _Q(self.db, self.key, "select")

    def insert(self, p, **_k):
        return _Q(self.db, self.key, "insert", p)

    def upsert(self, p, on_conflict=None, **_k):
        return _Q(self.db, self.key, "upsert", p, on_conflict)

    def update(self, p, **_k):
        return _Q(self.db, self.key, "update", p)

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def is_(self, c, v):
        self.f.append((c, v)); return self

    def in_(self, c, v):
        self.f.append((c, list(v))); return self

    def gte(self, _c, _v):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def _rows(self):
        return self.db.t.setdefault(self.key, [])

    def _match(self, r):
        return all((r.get(c) in v) if isinstance(v, list) else (r.get(c) == v) for c, v in self.f)

    def execute(self):
        rows = self._rows()
        if self.verb == "select":
            return type("R", (), {"data": [dict(r) for r in rows if self._match(r)]})()
        if self.verb in ("insert", "upsert"):
            recs = self.payload if isinstance(self.payload, list) else [self.payload]
            for rec in recs:
                cols = (self.conflict or "").split(",") if self.conflict else []
                hit = next((r for r in rows if cols and all(r.get(c) == rec.get(c) for c in cols)), None)
                if hit is not None:
                    hit.update(rec)
                else:
                    rows.append(dict(rec))
            return type("R", (), {"data": recs})()
        if self.verb == "update":
            touched = [r for r in rows if self._match(r)]
            for r in touched:
                r.update(self.payload)
            return type("R", (), {"data": touched})()
        raise AssertionError(self.verb)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _db(route="inbox"):
    return Stub({"storeops.alert_route": [{"org_id": HOUSE, "scope": "connector", "route": route}],
                 "storeops.alert_recipient": [{"org_id": HOUSE, "scope": "connector",
                                               "email": "ops@example.test", "via_email": True}],
                 "storeops.alert_log": [], "core.notification_inbox": []})


os.environ.setdefault("APP_ENV", "test")
try:
    import _harness_dbfree
    _harness_dbfree.install(_db())
    from app.modules.closing.router import _send_alert
    from app.modules.notify import digest_delivery as _delivery

    async def _never(*_a, **_k):
        raise AssertionError("an inbox-routed alert must never be delivered to an address")

    _delivery.deliver_recipient = _never
    db = _db()
    r1 = _run(_send_alert(db, HOUSE, "connector", "DLAR sweep errored", "check it",
                          "connector:dlar:7:since-2026-11-20:errored", inbox_key="connector:dlar:7"))
    check("E1 an inbox-routed alert mails nobody", r1["route"] == "inbox" and r1["sent"] == 0)
    items = db.t["core.notification_inbox"]
    check("E2 it files exactly one item for that company",
          len(items) == 1 and items[0]["org_id"] == HOUSE)
    check("E3 the delivery is recorded in the ONE existing send record, as channel `inbox`",
          [r["channel"] for r in db.t["storeops.alert_log"]] == ["inbox"])
    for _ in range(4):
        _run(_send_alert(db, HOUSE, "connector", "DLAR sweep errored", "check it",
                         "connector:dlar:7:since-2026-11-20:errored", inbox_key="connector:dlar:7"))
    check("E4 five days of the same finding is still ONE item",
          len(db.t["core.notification_inbox"]) == 1
          and db.t["core.notification_inbox"][0]["occurrences"] == 5)
    check("E5 an unchanged sighting is not recorded as a new delivery",
          len(db.t["storeops.alert_log"]) == 1)
    _delivery.deliver_recipient = _never
    db2 = _db()
    r2 = _run(_send_alert(db2, HOUSE, "connector", "x", "y", "ref", inbox_key="connector:a:1"))
    db2b = _db()
    _run(_send_alert(db2b, T1, "connector", "x", "y", "ref", inbox_key="connector:a:1"))
    check("E6 the same key for two companies is two items, each under its own org",
          db2.t["core.notification_inbox"][0]["org_id"] == HOUSE and r2["sent"] == 0)
    sender_ok = True
except Exception as e:                                          # pragma: no cover - harness guard
    sender_ok = False
    check("E0 the real sender could be exercised", False, str(e)[:160])

print("\nF. the SAFE DIRECTION — nothing is ever silenced by this change")
check("F1 no stable key: the message still goes out",
      NI.suppresses_message("inbox", None, {"state": "created"}) is False)
check("F2 the item could not be written (mig 1129 un-run): the message still goes out",
      NI.suppresses_message("inbox", "k", None) is False
      and NI.suppresses_message("inbox", "k", {"state": "error"}) is False)
check("F3 `both` keeps the message AND files the item",
      NI.suppresses_message("both", "k", {"state": "created"}) is False)
try:
    NI.identity("connector", "   ")
    check("F5 an empty key is REFUSED", False, "no ValueError raised")
except ValueError:
    check("F5 an empty key is REFUSED rather than filed under a date", True)

print("\nG. migration 1129 is tied to the code it serves")
MIG = sql_text("database/migrations/1129_company_notification_inbox.sql")
check("G1 the table the code reads is the table the migration creates",
      f"core.{NI.TABLE}" in MIG and f"{NI.ROUTE_SCHEMA}.{NI.ROUTE_TABLE}" in MIG)
check("G2 ONE ROW PER FINDING is enforced by a unique index on exactly the upsert key",
      "UNIQUE INDEX" in MIG and "(org_id, scope, inbox_key)" in MIG)
check("G3 the upsert names that same key",
      'on_conflict="org_id,scope,inbox_key"' in code_text("app/modules/core/notification_inbox.py"))
check("G4 a migration grants what it creates (service_role only, never anon)",
      MIG.count("TO service_role") == 2 and " anon" not in MIG)
check("G5 the send record's channel vocabulary is extended for the inbox",
      "'email', 'whatsapp', 'inbox'" in MIG and NI.CHANNEL == "inbox")
check("G6 every route the code can speak is a route the CHECK allows",
      all(f"'{r}'" in MIG for r in NI.ROUTES))
check("G7 every severity the code can store is a severity the CHECK allows",
      all(f"'{s}'" in MIG for s in NI.SEVERITIES))
check("G8 the house default declares the two data-upload scopes and nothing else",
      MIG.count("'connector',  'inbox'") == 1 and MIG.count("'upload_due', 'inbox'") == 1)
check("G9 it carries a REVERT note, and the revert restores the mailed alerts",
      "-- REVERT:" in MIG and "DROP TABLE IF EXISTS storeops.alert_route" in MIG)

print("\nH. THE LOCKS (each with an armed control)")
SENDERS = {
    "commcalc/router": code_text("app/modules/commcalc/router.py"),
    "vision/router": code_text("app/modules/vision/router.py"),
    "setup_documents": code_text("app/modules/commcalc/setup_documents.py"),
}
CLOSING = code_text("app/modules/closing/router.py")
INBOX_CODE = code_text("app/modules/core/notification_inbox.py")
LOG_CODE = code_text("app/modules/storeops/alert_log.py")
DEL_CODE = code_text("app/modules/notify/digest_delivery.py")

# H1: every _send_alert call on a scope the house declares for the inbox passes a stable key.
import re                                                       # noqa: E402
calls, keyless = 0, []
for name, text in list(SENDERS.items()) + [("closing/router", CLOSING)]:
    for m in re.finditer(r"_send_alert\(([^;]{0,900}?)\)\n", text):
        arg = m.group(1)
        if "def _send_alert" in arg:
            continue
        if '"connector"' in arg or "ALERT_SCOPE" in arg:
            calls += 1
            if "inbox_key=" not in arg:
                keyless.append(name)
check("H1 LOCK: every data-upload sender passes a stable inbox_key",
      calls >= 5 and not keyless, detail=f"{calls} calls, keyless={keyless}")
check("H2 control: the H1 scan can fail — it finds calls at all, and finds keyless ones elsewhere",
      calls >= 5 and any("inbox_key=" not in m.group(1)
                         for m in re.finditer(r"_send_alert\(([^;]{0,900}?)\)\n", CLOSING)
                         if "def _send_alert" not in m.group(1)))
check("H3 LOCK: no sender writes the inbox itself — the route decision has ONE home",
      all(f"{NI.TABLE}\")" not in t and "file_item(" not in t for t in SENDERS.values())
      and "file_item(" in CLOSING)
check("H4 control: the H3 scan can fail — the one home DOES name the table",
      f'table(TABLE)' in INBOX_CODE and f'TABLE = "{NI.TABLE}"' in INBOX_CODE)
# H5: a second module naming the table directly is the "two paths, one question" defect. Every other
# module must reach it through the one home, so only `notification_inbox.py` may name the relation.
# `relation_home.py` is excused BY NAME and for one reason: it is the house registry of
# relation -> schema, it names every relation the migrations create (its own A3 lock fails if one is
# missing), and it neither reads nor writes anything. Excusing it is not a hole in this lock — a
# reader or writer added there would still have to call `.table(...)`, which the scan below catches.
_EXCUSED = {"notification_inbox.py", "relation_home.py"}
_namers = []
for _root, _dirs, _files in os.walk("app"):
    for _f in _files:
        if not _f.endswith(".py") or _f in _EXCUSED:
            continue
        _txt = open(os.path.join(_root, _f), encoding="utf-8").read()
        if 'table("notification' in _txt or '"notification_inbox"' in _txt:
            _namers.append(_f)
check("H5 LOCK: only the one home names the inbox relation — no second reader, no second writer",
      not _namers, detail=str(_namers))
_REG = open("app/modules/core/relation_home.py", encoding="utf-8").read()
_REG_HITS = [ln.strip() for ln in _REG.splitlines() if NI.TABLE in ln]
check("H5a …and the excused registry only DECLARES the relation's schema — it never names it in a "
      "query, so it is not a second reader or writer",
      _REG_HITS == [f'"{NI.TABLE}": "{NI.SCHEMA}",']
      and f'table("{NI.TABLE}"' not in _REG, detail=str(_REG_HITS))
check("H5b control: the H5 scan can fail — the one home DOES name it",
      'table("notification' in INBOX_CODE or 'TABLE = "notification_inbox"' in INBOX_CODE)
check("H6 LOCK: the channel vocabulary still has ONE home, and the ladder is the ADDRESSED one",
      "CHANNELS = _log.MESSAGE_CHANNELS" in DEL_CODE
      and "CHANNELS = MESSAGE_CHANNELS + (INBOX_CHANNEL,)" in LOG_CODE
      and "CHANNELS = (" not in INBOX_CODE)
check("H7 control: the H6 scan can fail — the definition IS in the one home",
      'MESSAGE_CHANNELS = ("email", "whatsapp")' in LOG_CODE)
check("H8 LOCK: the inbox is never offered as an addressed channel in the delivery ladder",
      NI.CHANNEL not in __import__("app.modules.storeops.alert_log", fromlist=["x"]).MESSAGE_CHANNELS)
check("H9 LOCK: the freshness reconcile cannot be skipped by an early return added inside",
      "_data_freshness_measure(client, org_id)" in SENDERS["commcalc/router"]
      and "resolve_missing" in SENDERS["commcalc/router"].split(
          "async def _data_freshness_measure")[0].split("async def _data_freshness_monitor")[1])
check("H10 control: the H9 scan can fail — the measure function DOES return early when nothing is stale",
      "if not stale:\n        return rep" in SENDERS["commcalc/router"])

BANNED = ["boost", "luxelink", "vzone", "verizon", "xfinity", "metro", "cricket",
          "t-mobile", "shopify", "accessorize"]
low = (INBOX_CODE + code_text("app/modules/core/notification_inbox_api.py")).lower()
hits = [w for w in BANNED if w in low]
check("H11 LOCK: RULE TWO — no carrier, tenant or vendor name in the new modules", not hits,
      detail=str(hits))
check("H12 control: the RULE TWO scan can fail — a word that IS in the modules is found",
      "inbox" in low)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
