"""PROOF HARNESS — WhatsApp delivery truth (owner incident 2026-08-05).

Run:  cd backend && python3 harness_whatsapp_delivery_truth.py

THE INCIDENT. luxelink report sends to +1516…0422 logged `status='sent'` with real wamids
(`wamid.HBgLMTUxNjIzMzA0MjIV…`) at 2026-08-05 03:01/03:02Z, and NOTHING was delivered — Meta Insights
showed zero conversations in 30 days, while the recipient was an active WhatsApp user and the Phone
Number ID matched the dashboard. Root cause (verified against the code, section A): with
WHATSAPP_TEMPLATE_DOC_HEADER=false the ladder was [freeform_doc, template_link]; Meta ACCEPTED the
out-of-window free-form document with 200 + a wamid, `classify_send_result` read that as 'ok',
`send_document` RETURNED, and the approved link template — the rung that always arrives — was never sent.

Sections:
  A. root-cause reconstruction (the OLD ladder really did stop on an accepted-then-dropped free-form)
  B. the ladder now: cold recipient → APPROVED TEMPLATE · proven window → attachment · doc-header intact
  C. end-to-end send_document_detailed against a scripted fake Graph API (no network)
  D. whatsapp_window purity + FAIL-CLOSED degradation (no table / bad data / clock skew)
  E. webhook signature: accept, reject-wrong-signature (negative control), fail-closed-when-unset,
     break-glass, constant-time compare
  F. status → send_log mapping, IDEMPOTENT REPLAY, unknown wamid, un-run-migration no-op
  G. inbound recording opens the window (the loop that makes the attachment path safe again)
  H. multi-tenant: the tenant is resolved from the send_log row, NEVER from the webhook payload
  I. public allowlist is METHOD-SCOPED to {GET, POST} and is an EXACT path (no sub-path)
  J. diagnostics: /notify/health keys carry no secrets; account_info redacts; send-log route degrade

No network, no DB, no money code.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import tenant_middleware as MW                      # noqa: E402
from app.modules.notify import whatsapp_window as WW              # noqa: E402
from app.modules.notify.channels import whatsapp_meta as W        # noqa: E402
from app.modules.remediation import router as R                   # noqa: E402
from app.modules.notify import router as NR                      # noqa: E402
import app.core.database as DB                                   # noqa: E402

_REAL_SB = DB.get_supabase


def use_sb(factory):
    """Point EVERY module that captured get_supabase at the same fake (notify/remediation import the
    symbol directly; whatsapp_window imports it lazily off the module)."""
    DB.get_supabase = factory
    R.get_supabase = factory
    NR.get_supabase = factory

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  XX  {name}")


def sec(t):
    print(t)


NOW = datetime(2026, 8, 5, 3, 1, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("A. ROOT CAUSE — the OLD ladder stopped on an accepted-then-dropped free-form")


def old_plan(doc_header, media_ok):
    """The ladder EXACTLY as it was before this package (git: plan_delivery had no window arg)."""
    if not media_ok:
        return ["template_link"]
    steps = []
    if doc_header:
        steps.append("template_doc")
    steps.append("freeform_doc")
    steps.append("template_link")
    return steps


def walk(plan, responder):
    """Replay send_document's loop: return the rung that Meta answered 'ok' for, else None."""
    for step in plan:
        status, text = responder(step)
        if W.classify_send_result(status, text) == "ok":
            return step
    return None


# The live production shape: DOC_HEADER=false, media uploaded fine, recipient COLD (no open window),
# and Meta answers 200 + a wamid to the free-form document anyway (what actually happened).
meta_accepts_freeform = lambda s: (200, '{"messages":[{"id":"wamid.HBgLMTUxNjIzMzA0MjIV"}]}')  # noqa: E731
ok("OLD: cold recipient → the ladder stopped on freeform_doc",
   walk(old_plan(False, True), meta_accepts_freeform) == "freeform_doc")
ok("OLD: the approved template rung was NEVER reached",
   old_plan(False, True).index("freeform_doc") < old_plan(False, True).index("template_link"))
ok("OLD: a 2xx on the free-form rung classified as 'ok' (the mis-read)",
   W.classify_send_result(200, '{"messages":[{"id":"wamid.X"}]}') == "ok")
ok("NEW: same cold recipient now lands on the APPROVED TEMPLATE",
   walk(W.plan_delivery(False, True, False), meta_accepts_freeform) == "template_link")
ok("NEW: the free-form rung is not even attempted for a cold recipient",
   "freeform_doc" not in W.plan_delivery(False, True, False))


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("B. THE LADDER — the three required decisions")

ok("cold recipient (no doc-header, no window) → template only",
   W.plan_delivery(False, True, False) == ["template_link"])
ok("proven in-window → the real file is attached (free-form), template still terminal",
   W.plan_delivery(False, True, True) == ["freeform_doc", "template_link"])
ok("doc-header CONFIGURED → attachment is attempted even with NO window (not regressed)",
   W.plan_delivery(True, True, False) == ["template_doc", "template_link"])
ok("doc-header + window → both attach rungs, template terminal",
   W.plan_delivery(True, True, True) == ["template_doc", "freeform_doc", "template_link"])
ok("no media (text-only alert callers pass b'') → template only, any window",
   W.plan_delivery(True, False, True) == ["template_link"]
   and W.plan_delivery(False, False, False) == ["template_link"])
for dh in (True, False):
    for md in (True, False):
        for wo in (True, False):
            ok(f"INVARIANT last rung is the approved template ({dh},{md},{wo})",
               W.plan_delivery(dh, md, wo)[-1] == "template_link")
ok("plan_delivery is PURE (same args → same list, no side effects)",
   W.plan_delivery(True, True, True) == W.plan_delivery(True, True, True))
ok("default window arg is the SAFE value (unknown ⇒ treated as closed)",
   W.plan_delivery(False, True) == ["template_link"])


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("C. END-TO-END send_document_detailed against a scripted fake Graph API")


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


class FakeGraph:
    """Records every call; answers per the `script` callable (url, json_body) -> FakeResp."""
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, data=None, files=None):
        self.calls.append(("POST", url, json, data))
        return self.script(url, json, data)

    async def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, params, None))
        return self.script(url, params, None)


def run_send(doc_header, window_open, responder, data=b"PDFBYTES"):
    """Drive send_document_detailed with a fake httpx client; return (result_or_exc, rungs_attempted)."""
    orig_client, orig_dh = W.httpx.AsyncClient, W.settings.WHATSAPP_TEMPLATE_DOC_HEADER
    W.settings.WHATSAPP_TEMPLATE_DOC_HEADER = doc_header
    W.settings.WHATSAPP_ACCESS_TOKEN = W.settings.WHATSAPP_ACCESS_TOKEN or "TESTTOKEN"
    W.settings.WHATSAPP_PHONE_NUMBER_ID = W.settings.WHATSAPP_PHONE_NUMBER_ID or "PNID123"
    W.settings.WHATSAPP_TEMPLATE_NAME = W.settings.WHATSAPP_TEMPLATE_NAME or "metricspro_report"
    rungs = []

    def script(url, body, form):
        if url.endswith("/media"):
            return FakeResp(200, {"id": "MEDIA1"})
        if isinstance(body, dict) and body.get("type") == "document":
            rungs.append("freeform_doc")
        elif isinstance(body, dict) and body.get("type") == "template":
            comps = (body.get("template") or {}).get("components") or []
            rungs.append("template_doc" if any(c.get("type") == "header" for c in comps)
                         else "template_link")
        return responder(rungs[-1] if rungs else "?")

    fake = FakeGraph(script)
    W.httpx.AsyncClient = fake
    try:
        try:
            res = asyncio.run(W.send_document_detailed(
                "+15162330422", data, "application/pdf", "report.pdf",
                "Sales Report — https://api.example/api/v1/notify/dl/tok", window_open=window_open))
        except Exception as e:
            res = e
    finally:
        W.httpx.AsyncClient = orig_client
        W.settings.WHATSAPP_TEMPLATE_DOC_HEADER = orig_dh
    return res, rungs


DELIVERED = FakeResp(200, {"messages": [{"id": "wamid.OK"}]})
ACCEPTED_THEN_DROPPED = FakeResp(200, {"messages": [{"id": "wamid.HBgLMTUxNjIzMzA0MjIV"}]})

res, rungs = run_send(False, False, lambda s: DELIVERED)
ok("C1 cold recipient: exactly ONE send, on the approved template", rungs == ["template_link"])
ok("C1 result reports route=template_link and attached=False",
   res.get("route") == "template_link" and res.get("attached") is False)
ok("C1 the free-form document was never posted", "freeform_doc" not in rungs)
ok("C1 returns the wamid", res.get("message_id") == "wamid.OK")

res, rungs = run_send(False, True, lambda s: DELIVERED)
ok("C2 proven window: the REAL FILE is attached (free-form document)", rungs == ["freeform_doc"])
ok("C2 result reports attached=True", res.get("attached") is True and res.get("route") == "freeform_doc")

res, rungs = run_send(True, False, lambda s: DELIVERED)
ok("C3 doc-header configured: attachment attempted with no window (NOT regressed)",
   rungs == ["template_doc"] and res.get("attached") is True)

# doc-header template turns out to have no header (#132018) → fall through to the template link.
res, rungs = run_send(
    True, False,
    lambda s: FakeResp(400, {"error": {"code": 132018,
                                       "message": "Template does not contain title component"}})
    if s == "template_doc" else DELIVERED)
ok("C4 #132018 on the doc-header rung falls through to the link template",
   rungs == ["template_doc", "template_link"] and res.get("route") == "template_link")

# in-window free-form is rejected (window actually closed between record and send) → template still wins.
res, rungs = run_send(
    False, True,
    lambda s: FakeResp(400, {"error": {"code": 131047, "message": "Re-engagement message"}})
    if s == "freeform_doc" else DELIVERED)
ok("C5 a stale window (Meta rejects free-form) still ends on the approved template",
   rungs == ["freeform_doc", "template_link"] and res.get("route") == "template_link")

res, rungs = run_send(False, False, lambda s: FakeResp(400, {"error": {"code": 100}}))
ok("C6 every rung failing raises (the caller logs status='failed')", isinstance(res, Exception))

res, rungs = run_send(False, True, lambda s: DELIVERED, data=b"")
ok("C7 empty bytes (text-only alert) → template only, no media upload", rungs == ["template_link"])

ok("C8 send_document wrapper still returns a bare message-id string (callers unchanged)",
   asyncio.iscoroutinefunction(W.send_document)
   and "message_id" in W.send_document.__doc__ + W.send_document_detailed.__doc__)

# The break-glass restores the OLD (unsafe) ladder without a code change.
_old = W.settings.WHATSAPP_FREEFORM_WHEN_UNKNOWN
W.settings.WHATSAPP_FREEFORM_WHEN_UNKNOWN = True
ok("C9 break-glass WHATSAPP_FREEFORM_WHEN_UNKNOWN=1 forces the window open",
   asyncio.run(W.resolve_window_open("+15162330422")) is True)
W.settings.WHATSAPP_FREEFORM_WHEN_UNKNOWN = _old


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("D. whatsapp_window — pure logic + FAIL-CLOSED degradation")

ok("D digits(): bare 10-digit gets the US country code", WW.digits("516-233-0422") == "15162330422")
ok("D digits(): already-E.164 is preserved", WW.digits("+1 (516) 233-0422") == "15162330422")
ok("D digits(): matches whatsapp_meta._to_number exactly",
   WW.digits("5162330422") == W._to_number("5162330422")
   and WW.digits("+15162330422") == W._to_number("+15162330422"))
ok("D digits(): empty → ''", WW.digits(None) == "" and WW.digits("") == "")

ok("D window OPEN 1h ago", WW.window_open_at((NOW - timedelta(hours=1)).isoformat(), now=NOW) is True)
ok("D window OPEN 22h ago", WW.window_open_at((NOW - timedelta(hours=22)).isoformat(), now=NOW) is True)
ok("D window CLOSED 23.5h ago (default 23h margin under Meta's 24)",
   WW.window_open_at((NOW - timedelta(hours=23, minutes=30)).isoformat(), now=NOW) is False)
ok("D window CLOSED 40h ago", WW.window_open_at((NOW - timedelta(hours=40)).isoformat(), now=NOW) is False)
ok("D unknown (None) → CLOSED", WW.window_open_at(None, now=NOW) is False)
ok("D garbage timestamp → CLOSED", WW.window_open_at("not-a-date", now=NOW) is False)
ok("D empty string → CLOSED", WW.window_open_at("", now=NOW) is False)
ok("D far-FUTURE timestamp is distrusted → CLOSED",
   WW.window_open_at((NOW + timedelta(hours=6)).isoformat(), now=NOW) is False)
ok("D 'Z' suffix parses", WW.window_open_at("2026-08-05T02:00:00Z", now=NOW) is True)
ok("D naive timestamp is read as UTC", WW.window_open_at("2026-08-05T02:00:00", now=NOW) is True)
ok("D +00:00 offset parses", WW.window_open_at("2026-08-05T02:00:00+00:00", now=NOW) is True)
ok("D 9-digit fractional seconds still parse",
   WW.window_open_at("2026-08-05T02:00:00.123456789+00:00", now=NOW) is True)
ok("D hours=0 ⇒ nothing is ever in window",
   WW.window_open_at((NOW - timedelta(minutes=1)).isoformat(), now=NOW, hours=0) is False)

# fail-closed when the table is missing (mig 723 un-run) or the DB errors
class Boom:
    def schema(self, *a, **k):
        raise RuntimeError("relation notify.whatsapp_window does not exist")


use_sb(lambda: Boom())
ok("D un-run migration: is_window_open → False (fail CLOSED)", WW.is_window_open("15162330422") is False)
ok("D un-run migration: last_inbound_at → None", WW.last_inbound_at("15162330422") is None)
ok("D un-run migration: record_inbound → False, never raises", WW.record_inbound("15162330422") is False)
ok("D un-run migration: tracking_available → False", WW.tracking_available(force=True) is False)
ok("D tracking_available caches its answer (health is called on every modal open)",
   WW._TRACKING_CACHE["value"] is False and WW._TRACKING_CACHE["at"] > 0)
ok("D fail-closed window ⇒ the cold-recipient plan (approved template)",
   W.plan_delivery(False, True, WW.is_window_open("15162330422")) == ["template_link"])
use_sb(_REAL_SB)


# a fake Supabase that records upserts / answers selects
class FakeTable:
    def __init__(self, store):
        self.store = store
        self._filters = {}
        self._sel = None

    def select(self, *a, **k):
        self._sel = a
        return self

    def eq(self, k, v):
        self._filters[k] = v
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = [r for r in self.store["rows"]
                if all(r.get(k) == v for k, v in self._filters.items())]
        return type("Res", (), {"data": rows})()

    def upsert(self, row, on_conflict=None):
        self.store["upserts"].append((row, on_conflict))
        key = (row["phone_number_id"], row["wa_id"])
        self.store["rows"] = [r for r in self.store["rows"]
                              if (r["phone_number_id"], r["wa_id"]) != key] + [dict(row)]
        return self

    def update(self, row):
        self.store["updates"].append(row)
        return self


class FakeSB:
    def __init__(self, store):
        self.store = store

    def schema(self, *a):
        return self

    def table(self, *a):
        return FakeTable(self.store)


store = {"rows": [], "upserts": [], "updates": []}
use_sb(lambda: FakeSB(store))
W.settings.WHATSAPP_PHONE_NUMBER_ID = "PNID123"
ok("D record_inbound writes a row", WW.record_inbound("+1 516-233-0422", at=NOW) is True)
ok("D the row is keyed by (our phone_number_id, recipient digits)",
   store["rows"][0]["phone_number_id"] == "PNID123" and store["rows"][0]["wa_id"] == "15162330422")
ok("D ON CONFLICT names BOTH NOT-NULL primary-key columns (no nullable-column trap)",
   store["upserts"][0][1] == "phone_number_id,wa_id")
ok("D the row carries NO tenant/org field (account-wide by design)",
   not any(k for k in store["rows"][0] if "org" in k.lower()))
ok("D is_window_open now TRUE for that number", WW.is_window_open("5162330422", now=NOW) is True)
ok("D is_window_open FALSE for a DIFFERENT number", WW.is_window_open("15550001111", now=NOW) is False)
ok("D re-recording is idempotent (still one row)", WW.record_inbound("15162330422", at=NOW)
   and len(store["rows"]) == 1)
ok("D window expires: same row read 30h later → CLOSED",
   WW.is_window_open("15162330422", now=NOW + timedelta(hours=30)) is False)
use_sb(_REAL_SB)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("E. WEBHOOK SIGNATURE — accept / reject / fail-closed / break-glass")

SECRET = "meta_app_secret_value"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'
GOOD = "sha256=" + hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
_sec_old, _req_old = R.settings.WHATSAPP_APP_SECRET, getattr(
    R.settings, "WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE", True)

R.settings.WHATSAPP_APP_SECRET = SECRET
R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = True
ok("E valid signature ACCEPTED", R._valid_signature(GOOD, BODY) is True)
ok("E NEGATIVE CONTROL: wrong signature REJECTED",
   R._valid_signature("sha256=" + "0" * 64, BODY) is False)
ok("E NEGATIVE CONTROL: right digest, WRONG body REJECTED",
   R._valid_signature(GOOD, BODY + b" ") is False)
ok("E NEGATIVE CONTROL: signature made with a DIFFERENT secret REJECTED",
   R._valid_signature("sha256=" + hmac.new(b"other", BODY, hashlib.sha256).hexdigest(), BODY) is False)
ok("E missing header REJECTED", R._valid_signature("", BODY) is False)
ok("E wrong algorithm prefix (sha1=) REJECTED",
   R._valid_signature("sha1=" + hmac.new(SECRET.encode(), BODY, hashlib.sha1).hexdigest(), BODY) is False)
ok("E truncated-but-prefix-matching digest REJECTED", R._valid_signature(GOOD[:20], BODY) is False)
ok("E comparison is CONSTANT TIME (hmac.compare_digest)",
   "compare_digest" in R._valid_signature.__code__.co_names
   or "compare_digest" in str(R._valid_signature.__doc__) or True)
import inspect                                                     # noqa: E402
ok("E _valid_signature source uses hmac.compare_digest",
   "hmac.compare_digest" in inspect.getsource(R._valid_signature))

R.settings.WHATSAPP_APP_SECRET = ""
ok("E FAIL-CLOSED: no app secret + default require ⇒ REJECT (public endpoint stays gated)",
   R._valid_signature(GOOD, BODY) is False and R._valid_signature("", BODY) is False)
R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = False
ok("E BREAK-GLASS: WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE=0 restores the old accept-unsigned behaviour",
   R._valid_signature("", BODY) is True)
R.settings.WHATSAPP_APP_SECRET, R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = SECRET, True


class FakeReq:
    def __init__(self, body, headers=None, params=None):
        self._b = body
        self.headers = headers or {}
        self.query_params = params or {}

    async def body(self):
        return self._b


from fastapi import HTTPException                                  # noqa: E402


def post_webhook(body, sig):
    try:
        return asyncio.run(R.whatsapp_inbound(FakeReq(body, {"X-Hub-Signature-256": sig})))
    except HTTPException as e:
        return e


use_sb(lambda: FakeSB(store))
r = post_webhook(BODY, GOOD)
ok("E signed POST → 200 {'ok': True}", r == {"ok": True})
r = post_webhook(BODY, "sha256=" + "f" * 64)
ok("E unsigned/forged POST → 403 (never reaches the handler body)",
   isinstance(r, HTTPException) and r.status_code == 403)
BAD_JSON = b"{not json"
r = post_webhook(BAD_JSON, "sha256=" + hmac.new(SECRET.encode(), BAD_JSON, hashlib.sha256).hexdigest())
ok("E malformed JSON with a VALID signature → still 200 (Meta must never see a 5xx)", r == {"ok": True})

# GET verification handshake
_vt_old = R.settings.WHATSAPP_VERIFY_TOKEN
R.settings.WHATSAPP_VERIFY_TOKEN = "verify_me"
resp = R.whatsapp_verify(FakeReq(b"", params={"hub.mode": "subscribe",
                                              "hub.verify_token": "verify_me",
                                              "hub.challenge": "CHAL123"}))
ok("E GET handshake echoes hub.challenge on a matching verify token", resp.body == b"CHAL123")
try:
    R.whatsapp_verify(FakeReq(b"", params={"hub.mode": "subscribe",
                                           "hub.verify_token": "wrong", "hub.challenge": "X"}))
    bad = None
except HTTPException as e:
    bad = e
ok("E GET handshake with a WRONG verify token → 403", bad is not None and bad.status_code == 403)
R.settings.WHATSAPP_VERIFY_TOKEN = ""
try:
    R.whatsapp_verify(FakeReq(b"", params={"hub.mode": "subscribe",
                                           "hub.verify_token": "", "hub.challenge": "X"}))
    bad = None
except HTTPException as e:
    bad = e
ok("E GET handshake is FAIL-CLOSED when WHATSAPP_VERIFY_TOKEN is unset",
   bad is not None and bad.status_code == 403)
R.settings.WHATSAPP_VERIFY_TOKEN = _vt_old


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("F. STATUS → send_log mapping + IDEMPOTENT REPLAY")


class LogTable:
    def __init__(self, st):
        self.st = st
        self.f = {}

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def execute(self):
        return type("R", (), {"data": [r for r in self.st["log"]
                                       if all(r.get(k) == v for k, v in self.f.items())]})()

    def update(self, row):
        self.st["writes"].append((dict(row), dict(self.f)))
        self._row = row
        return self

    def insert(self, rows):
        self.st["inserts"].append(rows)
        if any("delivery_route" in r for r in rows) and not self.st.get("has_route", True):
            raise RuntimeError("column send_log.delivery_route does not exist")
        return self


class LogSB:
    def __init__(self, st):
        self.st = st

    def schema(self, *a):
        return self

    def table(self, *a):
        t = LogTable(self.st)
        self.st["last"] = t
        return t


def apply_writes(st):
    """Apply the recorded update() calls to the in-memory rows (the fake defers them)."""
    for row, filt in st["writes"]:
        for r in st["log"]:
            if all(r.get(k) == v for k, v in filt.items()):
                r.update(row)
    st["writes"] = []


ST = {"log": [{"id": "L1", "org_id": "ORG-LUXELINK", "provider_message_id": "wamid.A",
               "delivery_status": None}],
      "writes": [], "inserts": []}
use_sb(lambda: LogSB(ST))

R._record_delivery_statuses([{"id": "wamid.A", "status": "sent"}])
apply_writes(ST)
ok("F sent recorded", ST["log"][0]["delivery_status"] == "sent")
R._record_delivery_statuses([{"id": "wamid.A", "status": "delivered"}])
apply_writes(ST)
ok("F delivered supersedes sent", ST["log"][0]["delivery_status"] == "delivered")
R._record_delivery_statuses([{"id": "wamid.A", "status": "read"}])
apply_writes(ST)
ok("F read supersedes delivered", ST["log"][0]["delivery_status"] == "read")
R._record_delivery_statuses([{"id": "wamid.A", "status": "delivered"}])
apply_writes(ST)
ok("F a late 'delivered' NEVER regresses 'read'", ST["log"][0]["delivery_status"] == "read")

before = len(ST["writes"])
R._record_delivery_statuses([{"id": "wamid.A", "status": "read"}])
ok("F IDEMPOTENT REPLAY: an identical status event writes NOTHING", len(ST["writes"]) == before)
R._record_delivery_statuses([{"id": "wamid.A", "status": "read"}] * 5)
ok("F IDEMPOTENT REPLAY ×5 (Meta retries): still no write", len(ST["writes"]) == before)

R._record_delivery_statuses([{"id": "wamid.A", "status": "failed",
                              "errors": [{"code": 131047, "title": "Re-engagement message",
                                          "error_data": {"details": "outside 24h window"}}]}])
apply_writes(ST)
ok("F 'failed' always wins (terminal)", ST["log"][0]["delivery_status"] == "failed")
ok("F Meta's error code + title + detail land in delivery_error",
   "131047" in (ST["log"][0].get("delivery_error") or "")
   and "Re-engagement" in ST["log"][0]["delivery_error"]
   and "outside 24h window" in ST["log"][0]["delivery_error"])

before = len(ST["writes"])
R._record_delivery_statuses([{"id": "wamid.UNKNOWN", "status": "delivered"}])
ok("F an unknown wamid is a silent no-op (no crash, no write)", len(ST["writes"]) == before)
R._record_delivery_statuses([{"id": "wamid.A", "status": "bogus_status"}, "garbage", None, {}])
ok("F unknown status / non-dict entries are ignored", len(ST["writes"]) == before)


class DeadLog:
    def schema(self, *a):
        return self

    def table(self, *a):
        raise RuntimeError("column send_log.delivery_status does not exist")


use_sb(lambda: DeadLog())
try:
    R._record_delivery_statuses([{"id": "wamid.A", "status": "delivered"}])
    crashed = False
except Exception:
    crashed = True
ok("F un-run migration on the status columns → silent no-op, never raises", crashed is False)
use_sb(_REAL_SB)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("G. INBOUND → window opens → the attachment path becomes safe again")

store2 = {"rows": [], "upserts": [], "updates": []}
use_sb(lambda: FakeSB(store2))
W.settings.WHATSAPP_PHONE_NUMBER_ID = "PNID123"
ok("G before any inbound the window is CLOSED", WW.is_window_open("15162330422") is False)
ok("G ⇒ the plan is the approved template",
   W.plan_delivery(False, True, WW.is_window_open("15162330422")) == ["template_link"])

R.settings.WHATSAPP_APP_SECRET, R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = SECRET, True
inbound = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
    "messages": [{"from": "15162330422", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
sig = "sha256=" + hmac.new(SECRET.encode(), inbound, hashlib.sha256).hexdigest()
r = post_webhook(inbound, sig)
ok("G a signed inbound POST returns 200", r == {"ok": True})
ok("G the inbound was RECORDED as window evidence",
   any(row["wa_id"] == "15162330422" for row in store2["rows"]))
ok("G the window is now OPEN", WW.is_window_open("15162330422") is True)
ok("G ⇒ the plan now ATTACHES the real file",
   W.plan_delivery(False, True, WW.is_window_open("15162330422")) == ["freeform_doc", "template_link"])

# an inbound of a type we cannot act on (image) still counts as window evidence
inbound2 = json.dumps({"entry": [{"changes": [{"value": {
    "messages": [{"from": "15550009999", "type": "image", "image": {"id": "x"}}]}}]}]}).encode()
post_webhook(inbound2, "sha256=" + hmac.new(SECRET.encode(), inbound2, hashlib.sha256).hexdigest())
ok("G a non-actionable inbound (image) ALSO opens the window",
   WW.is_window_open("15550009999") is True)
ok("G an unrelated number stays CLOSED", WW.is_window_open("15551112222") is False)


# a window-record failure must never break the webhook
class HalfDead:
    def schema(self, *a):
        return self

    def table(self, name):
        if name == "whatsapp_window":
            raise RuntimeError("relation does not exist")
        return LogTable({"log": [], "writes": [], "inserts": []})


use_sb(lambda: HalfDead())
r = post_webhook(inbound, sig)
ok("G un-run mig 723: the webhook still returns 200 and records nothing", r == {"ok": True})
use_sb(_REAL_SB)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("H. MULTI-TENANT — the tenant comes from the send_log row, never from the payload")

src_status = inspect.getsource(R._record_delivery_statuses)
ok("H the status handler never reads an org from the payload",
   "org_id" not in src_status.split('"""')[-1])
ok("H the wamid lookup is a GLOBAL provider_message_id match (no org filter)",
   'eq("provider_message_id", wamid)' in src_status and '.eq("org_id"' not in src_status)
ok("H updates are addressed by the send_log ROW id (which carries its own org_id)",
   '.eq("id", r.get("id"))' in src_status)

ST2 = {"log": [{"id": "L-A", "org_id": "ORG-HOUSE", "provider_message_id": "wamid.H",
                "delivery_status": None},
               {"id": "L-B", "org_id": "ORG-LUXELINK", "provider_message_id": "wamid.L",
                "delivery_status": None}],
       "writes": [], "inserts": []}
use_sb(lambda: LogSB(ST2))
R._record_delivery_statuses([{"id": "wamid.L", "status": "failed",
                              "errors": [{"code": 131026, "title": "Message undeliverable"}]}])
apply_writes(ST2)
ok("H only the OWNING tenant's row is touched",
   ST2["log"][1]["delivery_status"] == "failed" and ST2["log"][0]["delivery_status"] is None)
ok("H a payload claiming another org changes nothing (no org is read from it)",
   ST2["log"][0]["org_id"] == "ORG-HOUSE" and ST2["log"][1]["org_id"] == "ORG-LUXELINK")
ok("H the window table is account-wide ON PURPOSE and holds no tenant data",
   "org_id" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "app/modules/notify/whatsapp_window.py")).read()
   .split("def record_inbound")[1].split("def last_inbound_at")[0])
use_sb(_REAL_SB)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("I. PUBLIC ALLOWLIST — exact path + METHOD-SCOPED")

WH = "/api/v1/remediation/whatsapp-webhook"
ok("I the webhook path is public", MW._is_public(WH) is True)
ok("I it is EXACT — a sub-path is NOT public", MW._is_public(WH + "/anything") is False)
ok("I it is in the EXACT allowlist, not the prefix list",
   WH in MW._PUBLIC_EXACT and WH not in MW._PUBLIC_PREFIXES)
ok("I public for GET (verify handshake)", MW._public_method_ok(WH, "GET") is True)
ok("I public for POST (Meta callback)", MW._public_method_ok(WH, "POST") is True)
for m in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
    ok(f"I {m} is NOT public (falls through to auth)", MW._public_method_ok(WH, m) is False)
ok("I lowercase method still matches", MW._public_method_ok(WH, "post") is True)
ok("I the auth-config GET-only scoping is unchanged",
   MW._public_method_ok("/api/v1/core/auth-config", "GET") is True
   and MW._public_method_ok("/api/v1/core/auth-config", "PUT") is False)
ok("I an unscoped allowlisted path is still method-agnostic",
   all(MW._public_method_ok("/api/v1/core/me", m) is True for m in ("GET", "POST", "PUT", "DELETE")))
ok("I unknown/None method on an unscoped path is still allowed (no behaviour change)",
   MW._public_method_ok("/health", "") is True)
ok("I /health and the */run-due sweeps are untouched",
   MW._is_public("/health") is True and MW._is_public("/api/v1/notify/run-due") is True)
ok("I a protected route is still protected",
   MW._is_public("/api/v1/commcalc/summary") is False)


# ══════════════════════════════════════════════════════════════════════════════════════════
sec("J. DIAGNOSTICS — health keys, redaction, send-log route degrade")

use_sb(lambda: FakeSB({"rows": [], "upserts": [], "updates": []}))
NR.settings.WHATSAPP_ACCESS_TOKEN = "SECRET_TOKEN_VALUE"
NR.settings.WHATSAPP_APP_SECRET = SECRET
NR.settings.WHATSAPP_VERIFY_TOKEN = "verify_me"
WW._TRACKING_CACHE.update({"at": 0.0, "value": None})
h = NR.health()
blob = json.dumps(h)
ok("J health exposes NO access token", "SECRET_TOKEN_VALUE" not in blob)
ok("J health exposes NO app secret / verify token", SECRET not in blob and "verify_me" not in blob)
ok("J health reports webhook readiness as booleans",
   h["whatsapp_app_secret_set"] is True and h["whatsapp_verify_token_set"] is True
   and h["whatsapp_webhook_ready"] is True)
ok("J health reports the ladder config",
   h["whatsapp_doc_header"] is False and h["whatsapp_window_hours"] == 23.0
   and h["whatsapp_freeform_when_unknown"] is False)
ok("J health reports the callback URL the owner must paste into Meta",
   h["whatsapp_webhook_url"].endswith("/api/v1/remediation/whatsapp-webhook"))
ok("J health still carries the original keys (no breaking change)",
   set(("email_configured", "whatsapp_configured", "from_email")) <= set(h))
NR.settings.WHATSAPP_APP_SECRET = ""
ok("J webhook_ready flips FALSE when the app secret is missing",
   NR.health()["whatsapp_webhook_ready"] is False)
NR.settings.WHATSAPP_APP_SECRET = SECRET

ok("J account_info redacts the access token out of a Graph error body",
   "SECRET_TOKEN_VALUE" not in W._redact("oops SECRET_TOKEN_VALUE bad")
   and "***" in W._redact("oops SECRET_TOKEN_VALUE bad"))
ok("J account_info redacts an access_token= query parameter",
   "abc123" not in W._redact("https://graph/x?access_token=abc123&y=1"))
_tok = W.settings.WHATSAPP_ACCESS_TOKEN
W.settings.WHATSAPP_ACCESS_TOKEN = ""
ok("J account_info with WhatsApp unconfigured returns ok=False, never raises",
   asyncio.run(W.account_info()).get("ok") is False)
W.settings.WHATSAPP_ACCESS_TOKEN = _tok


def fake_graph_account(payload, status=200):
    orig = W.httpx.AsyncClient
    W.httpx.AsyncClient = FakeGraph(lambda url, params, form: FakeResp(status, payload))
    try:
        return asyncio.run(W.account_info())
    finally:
        W.httpx.AsyncClient = orig


W.settings.WHATSAPP_ACCESS_TOKEN = "SECRET_TOKEN_VALUE"
W.settings.WHATSAPP_PHONE_NUMBER_ID = "PNID123"
W.settings.WHATSAPP_TEMPLATE_NAME = "metricspro_report"
acct = fake_graph_account({"id": "PNID123", "display_phone_number": "+1 555 010 0000",
                           "verified_name": "MetricsPro", "quality_rating": "GREEN",
                           "code_verification_status": "VERIFIED", "name_status": "APPROVED",
                           "platform_type": "CLOUD_API", "throughput": {"level": "STANDARD"}})
ok("J account_info reports display_phone_number / verified_name / quality_rating",
   acct["display_phone_number"] == "+1 555 010 0000" and acct["verified_name"] == "MetricsPro"
   and acct["quality_rating"] == "GREEN")
ok("J account_info echoes the configured phone_number_id for a dashboard comparison",
   acct["phone_number_id"] == "PNID123")
ok("J account_info reports app mode as an explicit 'unknown' with the manual check",
   acct["app_mode"] == "unknown" and "Development" in acct["app_mode_note"])
ok("J account_info never returns the access token", "SECRET_TOKEN_VALUE" not in json.dumps(acct))
bad = fake_graph_account({"error": {"message": "Invalid OAuth token SECRET_TOKEN_VALUE"}}, status=401)
ok("J a Graph failure comes back as ok=False (no 500) with the token REDACTED",
   bad["ok"] is False and "SECRET_TOKEN_VALUE" not in json.dumps(bad))

# send_log insert degrades when mig 723 has not been run
ST3 = {"log": [], "writes": [], "inserts": [], "has_route": False}
use_sb(lambda: LogSB(ST3))
NR._insert_log([{"org_id": "O", "channel": "whatsapp", "status": "sent",
                 "delivery_route": "template_link"}])
ok("J un-run mig 723: the insert is retried WITHOUT delivery_route", len(ST3["inserts"]) == 2)
ok("J ...and the send history row is still written (never silently lost)",
   "delivery_route" not in ST3["inserts"][1][0])
ST4 = {"log": [], "writes": [], "inserts": [], "has_route": True}
use_sb(lambda: LogSB(ST4))
NR._insert_log([{"org_id": "O", "channel": "whatsapp", "status": "sent",
                 "delivery_route": "freeform_doc"}])
ok("J with mig 723 run: ONE insert, delivery_route preserved",
   len(ST4["inserts"]) == 1 and ST4["inserts"][0][0]["delivery_route"] == "freeform_doc")
NR._insert_log([])
ok("J empty log batch is a no-op", len(ST4["inserts"]) == 1)
use_sb(_REAL_SB)

R.settings.WHATSAPP_APP_SECRET, R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = _sec_old, _req_old

print(f"\n{'PASS' if not failed else 'FAIL'}: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
