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
  • a hung model call is cut off by the wall (no 30-minute freeze), and the endpoint still 200s with
    the same graceful "no rows parsed" degradation
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


def install_fake_anthropic(stall=0.05):
    mod = types.ModuleType("anthropic")

    class _Msgs:
        async def create(self, **kw):
            AsyncAnthropic.seen = kw
            await asyncio.sleep(stall)
            return _Resp(GOOD_JSON)

    class AsyncAnthropic:
        last = None
        seen = None

        def __init__(self, **kw):
            AsyncAnthropic.last = kw
            self.messages = _Msgs()

    mod.AsyncAnthropic = AsyncAnthropic
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

print()
print("=" * 78)
print("C. A hung model call cannot freeze the app for 30 minutes")
print("=" * 78)
install_fake_anthropic(30.0)                 # model that never comes back within the wall
saved = A._AGENCY_AI_WALL_S
A._AGENCY_AI_WALL_S = 0.4                    # stand-in for the 125s default, kept test-fast
t0 = time.monotonic()
r = c.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
dt = time.monotonic() - t0
A._AGENCY_AI_WALL_S = saved
body = r.json() if r.status_code == 200 else {}
check("C1 still 200 (graceful), not a 500", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("C2 cut off at the wall, nowhere near the SDK's ~30 min", dt < 5, f"dt={dt:.2f}s")
check("C3 degrades to zero rows created (nothing half-parsed lands)", body.get("count") == 0, str(body))
check("C4 model recorded as 'error' — same degradation as before the change",
      LANDED.get("model") == "error", str(LANDED.get("model")))

print()
print("=" * 78)
print("D. Unconfigured key still degrades exactly as before")
print("=" * 78)
A.settings.ANTHROPIC_API_KEY = ""
r = c.post(URL, params={"org_id": ORG}, data={"period": "July 2026"}, files=FILES)
body = r.json() if r.status_code == 200 else {}
A.settings.ANTHROPIC_API_KEY = "sk-test"
check("D1 200 with zero rows", r.status_code == 200 and body.get("count") == 0, str(body)[:200])
check("D2 the 'needs ANTHROPIC_API_KEY' notice is still surfaced",
      "ANTHROPIC_API_KEY" in (body.get("notice") or ""), str(body.get("notice")))
check("D3 model recorded as 'deterministic'", LANDED.get("model") == "deterministic",
      str(LANDED.get("model")))

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
