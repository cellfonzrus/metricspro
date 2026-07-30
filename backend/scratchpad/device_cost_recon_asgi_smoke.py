"""REAL-ASGI smoke for GET /api/v1/commcalc/device-cost-recon.

Why this exists SEPARATELY from harness_device_cost_recon.py: that harness calls the handler function
directly, which proves the logic but NOT the mount. The `/api/v1` prefix trap is a repeat offender here
(`[[curl-verified-not-ui-verified-apiv1]]`): a path that answers when the function is called can still be
mounted somewhere the frontend's `api()` never reaches. So this drives the whole FastAPI app through
Starlette's TestClient at the EXACT URL the page requests, and asserts:

  • 200 at `/api/v1/commcalc/device-cost-recon` WITH the grant, carrying the documented payload keys
  • 403 at the same URL WITHOUT the grant, with a detail that names `device_cost_recon`
  • the bare `/commcalc/device-cost-recon` (no /api/v1) is 404 — i.e. the page MUST use the prefix
  • org_id really travels as a QUERY PARAM (a second tenant's URL returns that tenant's empty view)
  • an unreachable database degrades to a READY payload with an honest note, never a 500

Run: `python3 scratchpad/device_cost_recon_asgi_smoke.py` from the backend dir.
"""
import io, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")


def _load_harness_helpers():
    """Reuse the harness's fake Supabase client + the four-source fixtures WITHOUT re-running its 187
    assertions (that file is top-level sequential and ends in sys.exit, the house harness style). Only
    the section ABOVE its first `print("── 1.` banner is executed — i.e. the helpers and the fixtures,
    nothing that asserts. Resolved from THIS file's own path so the script runs from any cwd (the
    deleted-worktree sys.path bug that silently broke universal_ingest_proof)."""
    path = os.path.join(_BACKEND, "harness_device_cost_recon.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("\\n── 1. handler contract'
    assert marker in src, "harness layout changed — the helper split marker is gone"
    mod = types.ModuleType("dcr_harness_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_harness_helpers()

from fastapi.testclient import TestClient
from app.main import app
import app.core.database as DB
from app.modules.commcalc import router as R

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


URL = "/api/v1/commcalc/device-cost-recon"
BARE = "/commcalc/device-cost-recon"


def wire(store):
    """Point BOTH the module-level `sb()` and core's `get_supabase()` at the fake client, so whichever
    one a code path uses (the endpoint uses `sb()`; the finance classifier is handed the same client)
    can never reach a real database."""
    fake = H.FakeClient(store)
    R.sb = lambda: fake                                   # noqa: E731
    DB.get_supabase = lambda *a, **k: fake                # noqa: E731
    H.QUERY_LOG.clear()


client = TestClient(app, raise_server_exceptions=False)

print("\n── ASGI: the exact URL the page requests ─────────────────────────────────────────────")
wire(H.full_store(org=H.TEN))
r = client.get(URL, params={"period": "June 2026", "org_id": H.TEN},
               headers={"Authorization": H.ADMIN})
check(f"GET {URL} → 200 with the grant", r.status_code == 200)
body = r.json() if r.status_code == 200 else {}
for k in ("ready", "tiles", "rows", "overlaps", "unlinkable", "policy", "liability", "inventory",
          "delta_rows", "delta_totals", "today", "source_legend", "definition_note", "policy_note",
          "caveat_note", "precedence_label", "market_options", "store_options"):
    check(f"payload carries `{k}`", k in body)
check("the payload has real rows for this tenant", (body.get("unfiltered_rows") or 0) > 0)
check("the delta preview came back as a list", isinstance(body.get("delta_rows"), list))
check("Δ(inventory) is honestly null over HTTP too (JSON null, not 0)",
      (body.get("inventory") or {}).get("delta_inventory") is None)

print("\n── ASGI: the gate over HTTP ──────────────────────────────────────────────────────────")
wire(H.full_store(org=H.TEN))
r403 = client.get(URL, params={"period": "June 2026", "org_id": H.TEN},
                  headers={"Authorization": H.PLAIN})
check(f"GET {URL} → 403 without the grant", r403.status_code == 403)
check("the 403 detail names the grant key (the page's lock-note signal)",
      "device_cost_recon" in str(r403.json().get("detail", "")))
check("no query ran before the refusal", len(H.QUERY_LOG) == 0)

print("\n── ASGI: the /api/v1 prefix trap ─────────────────────────────────────────────────────")
wire(H.full_store(org=H.TEN))
rbare = client.get(BARE, params={"period": "June 2026", "org_id": H.TEN},
                   headers={"Authorization": H.ADMIN})
check(f"the bare path {BARE} is 404 — the page MUST use /api/v1", rbare.status_code == 404)

print("\n── ASGI: org_id really travels as a query param ───────────────────────────────────────")
wire(H.full_store(org=H.TEN))
rh = client.get(URL, params={"period": "June 2026", "org_id": H.HOUSE},
                headers={"Authorization": H.ADMIN})
check("another tenant's org_id in the URL returns THAT tenant's (empty) view",
      rh.status_code == 200 and rh.json().get("unfiltered_rows") == 0 and rh.json().get("ready"))

print("\n── ASGI: an unreachable database degrades, never 500s ─────────────────────────────────")


class Dead:
    def schema(self, *a, **k):
        raise RuntimeError("connection refused")

    def table(self, *a, **k):
        raise RuntimeError("connection refused")

    def rpc(self, *a, **k):
        raise RuntimeError("connection refused")


R.sb = lambda: Dead()                        # noqa: E731
DB.get_supabase = lambda *a, **k: Dead()     # noqa: E731
rd = client.get(URL, params={"period": "June 2026", "org_id": H.TEN},
                headers={"Authorization": H.ADMIN})
# NOTE the two different, both-correct behaviours: in PRODUCTION a dead database also kills
# `_resolve_caller`, so the gate degrades CLOSED with a 403 (proven in the harness with the BROKEN
# caller). Here the caller lookup is stubbed, which isolates the READ path — so this asserts the other
# half: with every read failing the endpoint still answers 200/ready and NAMES each dead source instead
# of 500-ing or printing a confident $0.
check("every read failing → still 200 (no 500)", rd.status_code == 200)
_b = rd.json() if rd.status_code == 200 else {}
check("…the payload is READY with zero rows", _b.get("ready") is True and _b.get("unfiltered_rows") == 0)
check("…and each dead source is NAMED in `degraded`, not silently $0",
      len(_b.get("degraded") or []) >= 4)
check("…and the delta preview says today's leg is unavailable rather than guessing it",
      (_b.get("today") or {}).get("available") is False)

print("\n══════════════════════════════════════════════════════════════════════════════════════")
print(f"  ASGI SMOKE   PASS {_pass}   FAIL {_fail}")
print("══════════════════════════════════════════════════════════════════════════════════════")
sys.exit(1 if _fail else 0)
