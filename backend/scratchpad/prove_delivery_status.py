"""Proof harness — WhatsApp/Meta DELIVERY-STATUS ingestion onto notify.send_log (owner incident 2026-07-18).

Run:  cd backend && python3 scratchpad/prove_delivery_status.py

Covers the pure decision helpers (`_merge_delivery_status`, `_flatten_delivery_errors`) AND the wired
handlers (`_record_delivery_statuses`, `whatsapp_inbound`) against a FAKE Supabase client + fake Request —
the full status-event matrix the task requires:
  · sent→delivered→read ordering (monotonic, never regresses)
  · failed always wins (and is terminal)
  · errors flattened onto delivery_error
  · unknown wamid = no-op, no crash
  · missing column (un-run mig 714) = no-op, webhook still 200
  · malformed payload = 200, no crash
  · an inbound-approval (messages) payload STILL routes to the approval path, untouched.
Pure: no network, no DB — `get_supabase` and `_handle_inbound` are monkeypatched.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.remediation import router as R    # noqa: E402

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


# ── a minimal fake Supabase query builder over an in-memory send_log ─────────────────────────────────
class _Q:
    def __init__(self, tbl):
        self.tbl, self.kind, self.cols, self.payload, self.filters = tbl, None, "", None, {}

    def select(self, cols):
        self.kind, self.cols = "select", cols
        return self

    def update(self, vals):
        self.kind, self.payload = "update", vals
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def execute(self):
        return self.tbl._execute(self)


class _Table:
    def __init__(self, store):
        self.store = store

    def select(self, cols):
        return _Q(self).select(cols)

    def update(self, vals):
        return _Q(self).update(vals)

    def _execute(self, q):
        s = self.store
        s.calls.append((q.kind, dict(q.filters), q.payload))
        if q.kind == "select":
            # un-run mig 714: selecting the new column errors like PostgREST does
            if s.missing_column and "delivery_status" in q.cols:
                raise RuntimeError("column send_log.delivery_status does not exist (42703)")
            wamid = q.filters.get("provider_message_id")
            rows = [dict(r) for r in s.rows if r.get("provider_message_id") == wamid]
            return types.SimpleNamespace(data=rows)
        # update
        if s.missing_column and any(str(k).startswith("delivery_") for k in (q.payload or {})):
            raise RuntimeError("column send_log.delivery_status does not exist (42703)")
        rid = q.filters.get("id")
        for r in s.rows:
            if r.get("id") == rid:
                r.update(q.payload)
                s.updates.append((rid, dict(q.payload)))
        return types.SimpleNamespace(data=[])


class _Store:
    def __init__(self, rows, missing_column=False):
        self.rows = rows
        self.missing_column = missing_column
        self.updates = []
        self.calls = []


class _FakeSB:
    def __init__(self, store):
        self.store = store

    def schema(self, _name):
        return self

    def table(self, _name):
        return _Table(self.store)


def use_store(store):
    R.get_supabase = lambda: _FakeSB(store)   # patches both sb() and _record_delivery_statuses' lookup


class FakeRequest:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}

    async def body(self):
        return self._body


# Signature verification is now FAIL-CLOSED when no app secret is configured (2026-08-05 hardening: the
# POST is on the PUBLIC allowlist, so the HMAC is its only auth). These scenarios exercise the payload
# HANDLING, not the signature — so use the documented break-glass to reach the loop. Signature
# accept/reject (incl. a wrong-signature negative control) is proven in harness_whatsapp_delivery_truth.py.
R.settings.WHATSAPP_APP_SECRET = ""
R.settings.WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE = False
ok("break-glass off + no secret ⇒ unsigned POST is accepted (old behaviour, opt-in)",
   R._valid_signature("", b"{}") is True)


# ── 1. PURE: _merge_delivery_status (monotonic; failed wins & is terminal) ───────────────────────────
ok("None + sent → sent", R._merge_delivery_status(None, "sent") == "sent")
ok("sent + delivered → delivered", R._merge_delivery_status("sent", "delivered") == "delivered")
ok("delivered + read → read", R._merge_delivery_status("delivered", "read") == "read")
ok("read + delivered → read (no regress)", R._merge_delivery_status("read", "delivered") == "read")
ok("read + sent → read (no regress)", R._merge_delivery_status("read", "sent") == "read")
ok("read + failed → failed (failed wins)", R._merge_delivery_status("read", "failed") == "failed")
ok("sent + failed → failed", R._merge_delivery_status("sent", "failed") == "failed")
ok("failed + read → failed (terminal)", R._merge_delivery_status("failed", "read") == "failed")
ok("failed + delivered → failed (terminal)", R._merge_delivery_status("failed", "delivered") == "failed")
ok("same delivered idempotent", R._merge_delivery_status("delivered", "delivered") == "delivered")
ok("unknown incoming keeps current", R._merge_delivery_status("delivered", "bogus") == "delivered")
ok("None + unknown → None", R._merge_delivery_status(None, "bogus") is None)
ok("case-insensitive incoming", R._merge_delivery_status("sent", "DELIVERED") == "delivered")


# ── 2. PURE: _flatten_delivery_errors ────────────────────────────────────────────────────────────────
ok("no errors → ''", R._flatten_delivery_errors(None) == "" and R._flatten_delivery_errors([]) == "")
ok("single code+title", R._flatten_delivery_errors(
    [{"code": 131049, "title": "message undeliverable"}]) == "[131049] message undeliverable")
ok("error_data.details appended", R._flatten_delivery_errors(
    [{"code": 131026, "title": "Undeliverable", "error_data": {"details": "not on WhatsApp"}}])
   == "[131026] Undeliverable — not on WhatsApp")
ok("falls back to message when no title", "[100]" in R._flatten_delivery_errors(
    [{"code": 100, "message": "invalid"}]))
ok("multiple joined with ' | '", R._flatten_delivery_errors(
    [{"code": 1, "title": "a"}, {"code": 2, "title": "b"}]) == "[1] a | [2] b")
ok("non-dict tolerated", R._flatten_delivery_errors(["weird"]) == "weird")
ok("flattened error is capped", len(R._flatten_delivery_errors(
    [{"code": 1, "title": "x" * 900}])) <= 500)


# ── 3. _record_delivery_statuses against the fake store ──────────────────────────────────────────────
def fresh(rows=None, missing=False):
    rows = rows if rows is not None else [
        {"id": "row-1", "provider_message_id": "wamid.AAA", "delivery_status": None},
    ]
    st = _Store(rows, missing_column=missing)
    use_store(st)
    return st

st = fresh()
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "delivered"}])
ok("known wamid → delivery_status written", st.rows[0]["delivery_status"] == "delivered")
ok("delivery_updated_at stamped", bool(st.rows[0].get("delivery_updated_at")))

st = fresh()
for s in ("sent", "delivered", "read"):
    R._record_delivery_statuses([{"id": "wamid.AAA", "status": s}])
ok("sent→delivered→read lands on read", st.rows[0]["delivery_status"] == "read")
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "delivered"}])   # late/out-of-order event
ok("late delivered after read stays read", st.rows[0]["delivery_status"] == "read")

st = fresh()
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "read"}])
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "failed",
                              "errors": [{"code": 131049, "title": "not delivered"}]}])
ok("failed after read → failed", st.rows[0]["delivery_status"] == "failed")
ok("failed writes delivery_error", st.rows[0].get("delivery_error") == "[131049] not delivered")

st = fresh()
R._record_delivery_statuses([{"id": "wamid.UNKNOWN", "status": "delivered"}])
ok("unknown wamid → no update, no crash", st.rows[0]["delivery_status"] is None and not st.updates)

st = fresh()
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "not-a-real-status"}])
ok("unknown status string skipped", st.rows[0]["delivery_status"] is None and not st.updates)

st = fresh(missing=True)
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "delivered"}])
ok("missing column (un-run mig) → no-op, no crash, no updates", not st.updates)

# two send_log rows share one wamid → both updated
st = fresh(rows=[{"id": "r1", "provider_message_id": "wamid.AAA", "delivery_status": None},
                 {"id": "r2", "provider_message_id": "wamid.AAA", "delivery_status": None}])
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "delivered"}])
ok("both matching rows updated", all(r["delivery_status"] == "delivered" for r in st.rows))

# no org filter is applied on the lookup (global wamid) — only provider_message_id filters the select
st = fresh()
R._record_delivery_statuses([{"id": "wamid.AAA", "status": "delivered"}])
sel = [c for c in st.calls if c[0] == "select"][0]
ok("wamid lookup is GLOBAL (no org_id filter)", "org_id" not in sel[1] and "provider_message_id" in sel[1])


# ── 4. whatsapp_inbound end-to-end (async) — always 200; approval path untouched ─────────────────────
async def run_inbound(body_bytes, store=None, missing=False):
    """Call whatsapp_inbound with a fake Request; returns (result, store, handle_calls)."""
    if store is None:
        store = _Store([{"id": "row-1", "provider_message_id": "wamid.AAA", "delivery_status": None}],
                       missing_column=missing)
    use_store(store)
    handle_calls = []

    async def _fake_handle(client, frm, payload, text):
        handle_calls.append((frm, payload, text))
    R._handle_inbound = _fake_handle
    res = await R.whatsapp_inbound(FakeRequest(body_bytes))
    return res, store, handle_calls


import json as _json   # noqa: E402


def statuses_payload(wamid="wamid.AAA", status="delivered", errors=None):
    ev = {"id": wamid, "status": status, "timestamp": "1700000000"}
    if errors:
        ev["errors"] = errors
    return _json.dumps({"entry": [{"changes": [{"value": {"statuses": [ev]}}]}]}).encode()


def messages_payload(frm="15162330422", mtype="button", payload="approve|abc|tok"):
    m = {"from": frm, "type": mtype}
    if mtype == "button":
        m["button"] = {"payload": payload}
    elif mtype == "text":
        m["text"] = {"body": payload}
    return _json.dumps({"entry": [{"changes": [{"value": {"messages": [m]}}]}]}).encode()


# malformed JSON → 200, no crash
res, _s, _h = asyncio.run(run_inbound(b"not json{{{"))
ok("malformed payload → 200 {ok:true}", res == {"ok": True})

# empty body → 200
res, _s, _h = asyncio.run(run_inbound(b""))
ok("empty body → 200", res == {"ok": True})

# well-formed statuses payload → 200 AND the store row updated
res, store, hc = asyncio.run(run_inbound(statuses_payload(status="delivered")))
ok("statuses payload → 200", res == {"ok": True})
ok("statuses payload updates send_log", store.rows[0]["delivery_status"] == "delivered")
ok("statuses payload does NOT hit the approval path", hc == [])

# failed status with errors via the webhook → delivery_error recorded
res, store, hc = asyncio.run(run_inbound(
    statuses_payload(status="failed", errors=[{"code": 131047, "title": "Re-engagement message"}])))
ok("failed webhook records delivery_error",
   store.rows[0]["delivery_status"] == "failed"
   and store.rows[0].get("delivery_error") == "[131047] Re-engagement message")

# inbound-approval (messages) payload → 200 AND routes to _handle_inbound UNTOUCHED
res, store, hc = asyncio.run(run_inbound(messages_payload(mtype="button", payload="approve|req1|tok1")))
ok("messages payload → 200", res == {"ok": True})
ok("messages payload routes to approval path", hc == [("15162330422", "approve|req1|tok1", None)])
ok("messages payload does NOT write delivery status", not store.updates)

# text inbound still routes (byte-identical approval handling)
res, store, hc = asyncio.run(run_inbound(messages_payload(mtype="text", payload="yes")))
ok("text inbound routes to approval path", hc == [("15162330422", None, "yes")])

# a payload carrying BOTH messages and statuses → BOTH handled in one webhook call
both = _json.dumps({"entry": [{"changes": [{"value": {
    "messages": [{"from": "15162330422", "type": "button", "button": {"payload": "reject|r2|t2"}}],
    "statuses": [{"id": "wamid.AAA", "status": "read"}]}}]}]}).encode()
res, store, hc = asyncio.run(run_inbound(both))
ok("mixed payload → 200", res == {"ok": True})
ok("mixed payload handles the status", store.rows[0]["delivery_status"] == "read")
ok("mixed payload handles the approval", hc == [("15162330422", "reject|r2|t2", None)])

# missing column + statuses via the webhook → STILL 200 (Meta must never see a 500)
res, store, hc = asyncio.run(run_inbound(statuses_payload(status="delivered"), missing=True))
ok("missing-column statuses webhook → 200 (graceful)", res == {"ok": True})
ok("missing-column statuses webhook → no updates", not store.updates)

print(f"\nprove_delivery_status: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
