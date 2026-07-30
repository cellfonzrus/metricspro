"""REAL-ASGI smoke for the Commission-Ledger MA refresh endpoints.

Why this exists SEPARATELY from harness_ledger_ma_sync.py: that harness calls the handler functions
directly, which proves the logic but NOT the mount or the parameter binding. Two repeat offenders here:

  • the `/api/v1` prefix trap (`[[curl-verified-not-ui-verified-apiv1]]`) — a path that answers when the
    function is called can still be mounted where the frontend's `api()` never reaches;
  • a POST whose inputs are QUERY params (org_id must be one — RULE ONE) can silently start demanding a
    JSON body once FastAPI binds it, which would 422 the page's `api(url, {method:'POST'})` call.

So this drives the whole FastAPI app through Starlette's TestClient at the EXACT URLs the page requests.

Run: `python3 scratchpad/ledger_ma_sync_asgi_smoke.py` from the backend dir.
"""
import io
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)


def _load_harness_helpers():
    """Reuse the harness's fake Supabase client + fixtures WITHOUT re-running its assertions (that file is
    top-level sequential and ends in sys.exit — the house harness style). Only the section ABOVE its first
    banner is executed. Path resolved from THIS file so the script runs from any cwd."""
    path = os.path.join(_BACKEND, "harness_ledger_ma_sync.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("\\n── A. handler contracts'
    assert marker in src, "harness layout changed — the helper split marker is gone"
    mod = types.ModuleType("lms_harness_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_harness_helpers()

from fastapi.testclient import TestClient                                     # noqa: E402
from app.main import app                                                      # noqa: E402
import app.core.database as DB                                                # noqa: E402
from app.modules.commcalc import router as R                                  # noqa: E402

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")


PREVIEW = "/api/v1/commcalc/commission-ledger/ma-sync/preview"
SYNC = "/api/v1/commcalc/commission-ledger/ma-sync"
PROV = "/api/v1/commcalc/commission-ledger/provenance"
SUMM = "/api/v1/commcalc/commission-ledger/summary"


def wire(store):
    fake = H.FakeClient(store)
    R.sb = lambda: fake                                   # noqa: E731
    DB.get_supabase = lambda *a, **k: fake                # noqa: E731
    H.QUERY_LOG.clear()
    H.WRITE_LOG.clear()
    return store


client = TestClient(app, raise_server_exceptions=False)

print("\n── ASGI: the exact URLs the page requests ────────────────────────────────────────────")
st = wire(H.Store(H.base_tables(ledger=[H.FILE_LEDGER_ROW])))
r = client.get(PREVIEW, params={"source_report": "ma_daily_tx", "period": "June 2026",
                               "org_id": H.HOUSE})
check(f"GET {PREVIEW} → 200", r.status_code == 200, r.text[:200])
body = r.json() if r.status_code == 200 else {}
for k in ("ready", "would_write", "delete_scope", "sources", "summary", "observed", "unmapped", "guard",
          "existing_by_origin", "overlap_note", "warnings", "categories", "category_labels",
          "origin_labels"):
    check(f"preview payload carries `{k}`", k in body)
check("the preview WROTE NOTHING over HTTP either",
      not [w for w in H.WRITE_LOG if w["mode"] in ("insert", "delete", "update")], H.WRITE_LOG)
check("the overlap with the existing file import is stated",
      "counted TOGETHER" in str(body.get("overlap_note")), body.get("overlap_note"))

r = client.get(PROV, params={"source_report": "ma_daily_tx", "org_id": H.HOUSE})
check(f"GET {PROV} → 200", r.status_code == 200, r.text[:200])
check("provenance lists periods + the raw sources feeding them",
      isinstance(r.json().get("periods"), list) and isinstance(r.json().get("raw_sources"), list))

print("\n── ASGI: the POST binds QUERY params (no JSON body demanded) ─────────────────────────")
st = wire(H.Store(H.base_tables(ledger=[H.FILE_LEDGER_ROW])))
r = client.post(SYNC, params={"source_report": "ma_daily_tx", "period": "June 2026",
                             "org_id": H.HOUSE})
check(f"POST {SYNC} (no body at all) → 200, not 422", r.status_code == 200, r.text[:300])
check("it reports what it saved", (r.json() or {}).get("saved") == 4, r.json().get("saved"))
check("the file-imported row is still there after the HTTP refresh",
      any(x["origin"] == "file" for x in st.t["commcalc.commission_ledger"]))
check("both origins are reported back",
      {o["origin"] for o in (r.json().get("existing_by_origin") or [])} == {"file", "ma_sync"},
      r.json().get("existing_by_origin"))

print("\n── ASGI: the origin filter travels on the read surfaces ──────────────────────────────")
rs = client.get(SUMM, params={"source_report": "ma_daily_tx", "period": "June 2026",
                             "origin": "ma_sync", "org_id": H.HOUSE})
ra = client.get(SUMM, params={"source_report": "ma_daily_tx", "period": "June 2026", "org_id": H.HOUSE})
check("origin=ma_sync isolates the synced payouts over HTTP",
      rs.status_code == 200 and rs.json()["payout_total"] == 37.5, rs.text[:200])
check("no origin = both sources (unchanged default)",
      ra.status_code == 200 and ra.json()["payout_total"] == 148.5, ra.text[:200])
check("the response echoes which origin it answered for",
      rs.json().get("origin") == "ma_sync" and ra.json().get("origin") is None)

print("\n── ASGI: the /api/v1 prefix trap ─────────────────────────────────────────────────────")
for bare in ("/commcalc/commission-ledger/ma-sync/preview", "/commcalc/commission-ledger/provenance"):
    rb = client.get(bare, params={"period": "June 2026", "org_id": H.HOUSE})
    check(f"the bare path {bare} is 404 — the page MUST use /api/v1", rb.status_code == 404)
rb = client.post("/commcalc/commission-ledger/ma-sync", params={"period": "June 2026", "org_id": H.HOUSE})
check("the bare POST path is 404 too", rb.status_code == 404)

print("\n── ASGI: refusals are 4xx with a readable detail, never a 500 ────────────────────────")
st = wire(H.Store(H.base_tables(ledger=[]),
                  missing_cols={"commcalc.commission_ledger": H.LEDGER_COLS_251}))
r = client.post(SYNC, params={"source_report": "ma_daily_tx", "period": "June 2026", "org_id": H.HOUSE})
check("pre-251 the POST is 400 (not 500)", r.status_code == 400, r.status_code)
check("...and the detail names the migration",
      "251_commission_ledger_ma_sync.sql" in str(r.json().get("detail")), r.text[:200])
check("...and nothing was written", not [w for w in H.WRITE_LOG if w["mode"] in ("insert", "delete")])

st = wire(H.Store(H.base_tables(tx=[])))
r = client.post(SYNC, params={"source_report": "ma_daily_tx", "period": "May 2026", "org_id": H.HOUSE})
check("an empty period is 400 and deletes nothing", r.status_code == 400
      and not [w for w in H.WRITE_LOG if w["mode"] == "delete"], r.status_code)
r = client.get(PREVIEW, params={"source_report": "ma_daily_tx", "org_id": H.HOUSE})
check("preview with no period is 400", r.status_code == 400, r.status_code)

print("\n── ASGI: a DEAD database degrades instead of 500-ing the page ─────────────────────────")


class Dead:
    def schema(self, *a, **k):
        raise RuntimeError("connection refused")

    def table(self, *a, **k):
        raise RuntimeError("connection refused")

    def rpc(self, *a, **k):
        raise RuntimeError("connection refused")


R.sb = lambda: Dead()                        # noqa: E731
DB.get_supabase = lambda *a, **k: Dead()     # noqa: E731
r = client.get(PREVIEW, params={"source_report": "ma_daily_tx", "period": "June 2026",
                               "org_id": H.HOUSE})
check("preview still answers 200 with a dead DB", r.status_code == 200, r.text[:200])
check("...deriving nothing and saying so",
      r.status_code == 200 and r.json()["would_write"] == 0 and r.json()["warnings"], r.text[:200])
r = client.get(PROV, params={"source_report": "ma_daily_tx", "org_id": H.HOUSE})
check("provenance still answers 200 with a dead DB", r.status_code == 200, r.text[:200])
r = client.post(SYNC, params={"source_report": "ma_daily_tx", "period": "June 2026", "org_id": H.HOUSE})
check("the refresh REFUSES on a dead DB (400) rather than writing blind", r.status_code == 400,
      r.status_code)

print(f"\n══ ledger_ma_sync ASGI smoke: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
