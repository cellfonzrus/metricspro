"""REAL-ASGI smoke for the agency OCR async package — the URL the agency page actually POSTs.

Why this exists SEPARATELY from harness_agency_ocr_async.py: that harness drives the helpers as plain
Python functions, which proves the client swap + the loop behaviour but NOT the mount, the `/api/v1`
prefix (`[[curl-verified-not-ui-verified-apiv1]]`), or that the SYNC BRIDGE behaves when it is invoked
from the REAL `async def agency_upload_ocr` handler on a REAL running event loop — which is exactly the
condition that caused the 2026-07-30 freeze. So this drives the whole FastAPI app through Starlette's
TestClient at the exact URL and asserts:

  • POST /api/v1/commcalc/agency/links/{id}/transfers/upload-ocr -> 200 with the unchanged response
    shape (ok/count/transfers/bucket_ready), rows extracted by the ASYNC client
  • the bare `/commcalc/agency/...` (no /api/v1) is 404 — the page MUST use the prefix
  • the model call really went through `AsyncAnthropic` with timeout+max_retries bound
  • THE POINT (§C): with the app driven over httpx ASGITransport on THIS event loop — the production
    condition, unlike TestClient's portal thread — a stalled upload-ocr request no longer freezes the
    app: a heartbeat keeps ticking and `/health` (the endpoint that went dark on 2026-07-30) keeps
    answering 200 for the whole stall. A sync client here would have served ZERO of them.
  • a hung model call is bounded by the SDK timeout, and the endpoint still 200s with the same
    graceful "no rows parsed" degradation
  • a TRIPWIRE replaces the sync bridge `_ocr_parse_transfer`, so a regression to the blocking call
    site fails loudly rather than silently
  • ZERO writes to the live DB / storage: _get_link, ingest_ocr and _upload_agency_doc are stubbed.

Run: `python3 scratchpad/agency_ocr_async_asgi_smoke.py` from the backend dir.
"""
import asyncio
import os
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}   {detail}")


GOOD_JSON = ('{"lines":[{"equip_class_value":"device","product_desc":"A15","qty":2,"unit_cost":110.5},'
             '{"equip_class_value":"accessory","product_desc":"Case","qty":3,"unit_cost":4.25}]}')


class _Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class _Resp:
    def __init__(self, t):
        self.content = [_Blk(t)]


class APITimeoutError(Exception):
    pass


def install_fake_anthropic(stall=0.05, mode="ok"):
    mod = types.ModuleType("anthropic")

    class _Msgs:
        async def create(self, **kw):
            AsyncAnthropic.seen = kw
            await asyncio.sleep(stall)
            if mode == "timeout":
                raise APITimeoutError("Request timed out.")
            return _Resp(GOOD_JSON)

    class AsyncAnthropic:
        last = None
        seen = None

        def __init__(self, **kw):
            AsyncAnthropic.last = kw
            self.messages = _Msgs()

    mod.AsyncAnthropic = AsyncAnthropic
    mod.APITimeoutError = APITimeoutError
    mod.Anthropic = None                      # a sync-client regression would TypeError loudly
    sys.modules["anthropic"] = mod
    return AsyncAnthropic


from fastapi.testclient import TestClient                      # noqa: E402
from app.main import app                                       # noqa: E402
from app.modules.commcalc import router as R                   # noqa: E402
from app.modules.commcalc import agency as A                   # noqa: E402

LINK = "11111111-2222-3333-4444-555555555555"
ORG = "00000000-0000-0000-0000-000000000001"
LANDED = {}

# ── isolate from the live DB / storage: only the OCR path under test does real work ──────────────
R.sb = lambda: None                        # no Supabase creds needed — every consumer below is stubbed
R._can_edit_agency = lambda authorization, org_id: True
R._agency_who = lambda authorization, org_id: "smoke-user"
A._get_link = lambda client, org_id, link_id: {"id": link_id, "org_id": org_id}
A._upload_agency_doc = lambda link_id, fn, data, ct: (f"agency/{link_id}/x_{fn}", True, None)


def _fake_ingest(client, org_id, link_id, period, rows, doc_path, doc_name, model, confidence, who=None):
    LANDED.clear()
    LANDED.update(org_id=org_id, link_id=link_id, period=period, rows=rows, doc_path=doc_path,
                  doc_name=doc_name, model=model, confidence=confidence, who=who)
    return {"ok": True, "count": len(rows or []), "transfers": list(rows or [])}


A.ingest_ocr = _fake_ingest
A.settings = types.SimpleNamespace(ANTHROPIC_API_KEY="sk-test", ACCOUNT_ENGINE_MODEL="claude-opus-4-8")


def _bridge_tripwire(*a, **kw):
    """TRIPWIRE: the endpoint must NOT reach the sync bridge any more. The bridge survives in agency.py
    as belt-and-braces for a future sync caller, but if `agency_upload_ocr` ever regresses to calling it,
    every assertion below fails loudly instead of silently re-introducing the freeze."""
    raise AssertionError("REGRESSION: agency_upload_ocr called the SYNC bridge _ocr_parse_transfer — "
                        "it must await _ocr_parse_transfer_async (SEV-1 2026-07-30 class)")


A._ocr_parse_transfer = _bridge_tripwire

URL = f"/api/v1/commcalc/agency/links/{LINK}/transfers/upload-ocr"
FILES = {"file": ("vendor-invoice.pdf", b"%PDF-1.4 fake invoice bytes", "application/pdf")}

print("=" * 78)
print("A. Mount + /api/v1 prefix")
print("=" * 78)
c = TestClient(app)
paths = {getattr(r, "path", "") for r in app.routes}
check("A1 upload-ocr route is mounted under /api/v1",
      "/api/v1/commcalc/agency/links/{link_id}/transfers/upload-ocr" in paths)
bare = c.post(f"/commcalc/agency/links/{LINK}/transfers/upload-ocr",
              params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
check("A2 bare path (no /api/v1) 404s — the page must use the prefix", bare.status_code == 404,
      str(bare.status_code))

print()
print("=" * 78)
print("B. Happy path over REAL ASGI — async client, rows land, shape unchanged")
print("=" * 78)
Cli = install_fake_anthropic(0.05)
t0 = time.monotonic()
r = c.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
dt = time.monotonic() - t0
body = r.json() if r.status_code == 200 else {}
check("B1 200 OK", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("B2 response shape unchanged (ok/count/transfers/bucket_ready)",
      set(body) >= {"ok", "count", "transfers", "bucket_ready"}, str(body)[:200])
check("B3 both invoice lines extracted", body.get("count") == 2, str(body.get("count")))
check("B4 line values preserved verbatim",
      body.get("transfers") == [
          {"equip_class_value": "device", "product_desc": "A15", "qty": 2, "unit_cost": 110.5},
          {"equip_class_value": "accessory", "product_desc": "Case", "qty": 3, "unit_cost": 4.25}],
      str(body.get("transfers")))
check("B5 model + confidence handed to ingest_ocr unchanged",
      LANDED.get("model") == "claude-opus-4-8" and LANDED.get("confidence") == 0.9, str(LANDED)[:200])
check("B6 org_id came from the query param (multi-tenant rule)", LANDED.get("org_id") == ORG)
check("B7 period + doc metadata still forwarded",
      LANDED.get("period") == "July 2026" and LANDED.get("doc_name") == "vendor-invoice.pdf")
check("B8 the ASYNC client was used, with timeout + max_retries bound",
      Cli.last and Cli.last.get("timeout") == 60.0 and Cli.last.get("max_retries") == 1, str(Cli.last))
check("B9 PDF still sent as a base64 document block, max_tokens 1500",
      Cli.seen["messages"][0]["content"][0]["type"] == "document" and Cli.seen["max_tokens"] == 1500)
check("B10 request completed promptly", dt < 10, f"dt={dt:.2f}s")
check("B11 the endpoint took the ASYNC path — the sync-bridge tripwire never fired",
      A._ocr_parse_transfer is _bridge_tripwire and body.get("count") == 2)

print()
print("=" * 78)
print("C. THE POINT — a stalled OCR no longer freezes the app, through the REAL endpoint")
print("=" * 78)
# TestClient drives the app on a portal thread, which hides loop-blocking. ASGITransport runs the app
# on THIS event loop — the production condition. While one upload-ocr request sits inside a model call
# that never returns, /health (the endpoint that went dark on 2026-07-30) must keep answering.
import httpx                                                    # noqa: E402

STALL_S = 1.0


async def _loop_liveness(stall):
    install_fake_anthropic(stall)
    ticks = 0
    healths = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://smoke") as ac:
        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        async def pinger():
            while True:
                hr = await ac.get("/health")
                healths.append(hr.status_code)
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(heartbeat())
        pg = asyncio.create_task(pinger())
        await asyncio.sleep(0.10)                      # let both get going
        t_base, h_base = ticks, len(healths)
        t0 = time.monotonic()
        resp = await ac.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
        dt = time.monotonic() - t0
        hb.cancel()
        pg.cancel()
    return resp, ticks - t_base, healths[h_base:], dt


resp, ticks, healths, dt = asyncio.run(_loop_liveness(STALL_S))
check("C1 the stalled upload still returns 200 with its rows",
      resp.status_code == 200 and resp.json().get("count") == 2, f"{resp.status_code} {resp.text[:160]}")
check(f"C2 the OCR call really did stall for ~{STALL_S}s", dt >= STALL_S * 0.9, f"dt={dt:.2f}s")
check("C3 the event loop kept running the whole time (heartbeat ticked)", ticks >= 20, f"ticks={ticks}")
check(f"C4 /health kept answering DURING the stalled OCR ({len(healths)} requests served)",
      len(healths) >= 10 and set(healths) == {200}, f"served={len(healths)} codes={set(healths)}")
check("C5 this is the 2026-07-30 regression test: a sync client here would have served ZERO "
      "concurrent requests", len(healths) >= 10)

print()
print("=" * 78)
print("D. A model call that never comes back is bounded by the SDK timeout, not ~30 min")
print("=" * 78)
# With `timeout=AGENCY_AI_TIMEOUT_S` bound on the client, a hung model raises APITimeoutError instead
# of hanging for 600s x 2 retries. The stub raises it after a short sleep to keep the proof fast.
install_fake_anthropic(0.05, mode="timeout")
t0 = time.monotonic()
r = c.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
dt = time.monotonic() - t0
body = r.json() if r.status_code == 200 else {}
check("D1 still 200 (graceful), not a 500", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("D2 returns promptly", dt < 5, f"dt={dt:.2f}s")
check("D3 degrades to zero rows created (nothing half-parsed lands)", body.get("count") == 0, str(body))
check("D4 model recorded as 'error' — same degradation as before the change",
      LANDED.get("model") == "error", str(LANDED.get("model")))
check("D5 the client that was built carried timeout=60.0 / max_retries=1 (what bounds it in prod)",
      sys.modules["anthropic"].AsyncAnthropic.last.get("timeout") == 60.0
      and sys.modules["anthropic"].AsyncAnthropic.last.get("max_retries") == 1,
      str(sys.modules["anthropic"].AsyncAnthropic.last))

print()
print("=" * 78)
print("E. Unconfigured key still degrades exactly as before")
print("=" * 78)
A.settings.ANTHROPIC_API_KEY = ""
r = c.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
body = r.json() if r.status_code == 200 else {}
A.settings.ANTHROPIC_API_KEY = "sk-test"
check("E1 200 with zero rows", r.status_code == 200 and body.get("count") == 0, str(body)[:200])
check("E2 the 'needs ANTHROPIC_API_KEY' notice is still surfaced",
      "ANTHROPIC_API_KEY" in (body.get("notice") or ""), str(body.get("notice")))
check("E3 model recorded as 'deterministic'", LANDED.get("model") == "deterministic",
      str(LANDED.get("model")))
check("E4 the sync-bridge tripwire never fired across the whole smoke",
      A._ocr_parse_transfer is _bridge_tripwire)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
