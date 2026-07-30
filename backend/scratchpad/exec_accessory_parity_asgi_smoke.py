"""REAL-ASGI smoke for the accessory-parity package — the two URLs the two PAGES actually request.

Why this exists SEPARATELY from exec_accessory_parity_proof.py: that harness calls the handlers as
plain Python functions, which proves the math but NOT the mount or the query-param binding. The
`/api/v1` prefix trap is a repeat offender here (`[[curl-verified-not-ui-verified-apiv1]]`), and this
package's whole point is a NEW query param (`today`) plus NEW response fields that the pages read. So
this drives the whole FastAPI app through Starlette's TestClient at the EXACT URLs
`commcalc/exec/mtd/page.tsx` and `commcalc/targets/accessories/page.tsx` request, and asserts:

  • GET /api/v1/commcalc/exec-mtd/{period}?today=…  -> 200, and the response carries `setup_fee` /
    `acc_plus_setup` / `trending_acc_plus_setup` on rows AND on the TOTAL row
  • GET /api/v1/commcalc/targets/{period}/summary?today=…&include_untargeted=1 -> 200 carrying
    `trending_acc_target` per store and `trending.unmapped_*`
  • over HTTP, per store: exec `acc_plus_setup` == targets `categories.accessories.achieved_mtd`
    and exec `trending_acc_plus_setup` == targets `trending_acc_target`
  • the `today` query param really BINDS (a different today changes the trending divisor)
  • the bare `/commcalc/exec-mtd/...` (no /api/v1) is 404 — the page MUST use the prefix

Run: `python3 scratchpad/exec_accessory_parity_asgi_smoke.py` from the backend dir.
"""
import io, os, sys, types
from datetime import date as _date

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")


def _load_proof_helpers():
    """Reuse the parity proof's FakeClient + fixture WITHOUT re-running its assertions (that file is
    top-level sequential and ends in sys.exit, the house harness style). Only the section ABOVE its
    first `print("(S)` banner is executed — helpers and fixture data, nothing that asserts."""
    path = os.path.join(_HERE, "exec_accessory_parity_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'store = base_store()'
    assert marker in src, "parity proof layout changed — the helper split marker is gone"
    mod = types.ModuleType("acc_parity_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_proof_helpers()

from fastapi.testclient import TestClient
from app.main import app
import app.core.database as DB
from app.modules.commcalc import router as R

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


ORG = H.ORG
PERIOD = H.PERIOD
TODAY = H.TODAY_ISO
EX_URL = f"/api/v1/commcalc/exec-mtd/{PERIOD}"
TG_URL = f"/api/v1/commcalc/targets/{PERIOD}/summary"
BARE = f"/commcalc/exec-mtd/{PERIOD}"

fake = H.FakeClient(H.base_store())
R.sb = lambda: fake                       # noqa: E731
DB.get_supabase = lambda *a, **k: fake    # noqa: E731

client = TestClient(app, raise_server_exceptions=False)

print("\n-- ASGI: the exact URLs the two pages request --------------------------------------")
r_ex = client.get(EX_URL, params={"org_id": ORG, "today": TODAY})
check(f"GET {EX_URL}?today= -> 200", r_ex.status_code == 200, r_ex.text[:200])
r_tg = client.get(TG_URL, params={"org_id": ORG, "today": TODAY, "include_untargeted": "1"})
check(f"GET {TG_URL}?today= -> 200", r_tg.status_code == 200, r_tg.text[:200])
check(f"bare {BARE} (no /api/v1) is 404 — the page MUST use the prefix",
      client.get(BARE, params={"org_id": ORG}).status_code == 404)

ex = r_ex.json() if r_ex.status_code == 200 else {}
tg = r_tg.json() if r_tg.status_code == 200 else {}
ex_rows = {r["store"]: r for r in (ex.get("by_location", {}) or {}).get("rows", [])}
ex_tot = (ex.get("by_location", {}) or {}).get("total", {})
tg_by = {s["store_code"]: s for s in (tg.get("stores") or [])}

print("\n-- the NEW fields survive JSON serialization ----------------------------------------")
for k in ("setup_fee", "acc_plus_setup", "trending_acc_plus_setup"):
    check(f"exec-mtd TOTAL carries `{k}`", k in ex_tot)
    check(f"exec-mtd rows carry `{k}`", all(k in r for r in ex_rows.values()))
for k in ("trending_acc_target", "acc_sales_ex_setup", "setup_fee_mtd_exec"):
    check(f"targets summary stores carry `{k}`", all(k in s for s in tg_by.values()))
for k in ("unmapped_stores", "unmapped_acc_sales", "unmapped_acc_plus_setup"):
    check(f"targets summary `trending` carries `{k}`", k in (tg.get("trending") or {}))

print("\n-- OVER HTTP the two surfaces reconcile, per store ----------------------------------")
for code, addr in (("S1", H.S1_ADDR), ("S2", H.S2_ADDR)):
    exr = ex_rows.get(addr, {})
    a = ((tg_by.get(code) or {}).get("categories") or {}).get("accessories") or {}
    check(f"{code}: exec acc_plus_setup == targets accessories.achieved_mtd",
          exr.get("acc_plus_setup") == a.get("achieved_mtd"),
          f"{exr.get('acc_plus_setup')} vs {a.get('achieved_mtd')}")
    check(f"{code}: exec setup_fee == targets accessories.setup_fee_mtd",
          exr.get("setup_fee") == a.get("setup_fee_mtd"),
          f"{exr.get('setup_fee')} vs {a.get('setup_fee_mtd')}")
    check(f"{code}: exec trending_acc_plus_setup == targets trending_acc_target",
          exr.get("trending_acc_plus_setup") == (tg_by.get(code) or {}).get("trending_acc_target"),
          f"{exr.get('trending_acc_plus_setup')} vs {(tg_by.get(code) or {}).get('trending_acc_target')}")
    check(f"{code}: exec acc_sales (pure) == targets acc_sales_ex_setup",
          exr.get("acc_sales") == (tg_by.get(code) or {}).get("acc_sales_ex_setup"),
          f"{exr.get('acc_sales')} vs {(tg_by.get(code) or {}).get('acc_sales_ex_setup')}")
check("the unmapped store's $ is reported and explains the whole total delta",
      round(ex_tot.get("acc_plus_setup", 0)
            - round(sum(((s.get("categories") or {}).get("accessories") or {}).get("achieved_mtd", 0)
                        for s in tg_by.values()), 2), 2)
      == (tg.get("trending") or {}).get("unmapped_acc_plus_setup"),
      f"{ex_tot.get('acc_plus_setup')} / {(tg.get('trending') or {}).get('unmapped_acc_plus_setup')}")

print("\n-- the `today` query param really BINDS (not silently ignored) ----------------------")
other = _date(H.TODAY.year, H.TODAY.month, min(H.TODAY.day + 5, H.DIM)).isoformat()
r2 = client.get(EX_URL, params={"org_id": ORG, "today": other})
check("a different ?today= yields a different trending divisor",
      r2.status_code == 200 and r2.json()["trending"]["elapsed_days"] != ex["trending"]["elapsed_days"],
      f"{r2.json().get('trending')} vs {ex.get('trending')}")
check("exec-mtd and targets summary agree on elapsed_days for the SAME today",
      ex["trending"]["elapsed_days"] == (tg.get("trending") or {}).get("elapsed_days"),
      f"{ex['trending']} vs {tg.get('trending')}")
r3 = client.get(EX_URL, params={"org_id": ORG, "today": "garbage"})
check("a malformed ?today= is ignored, never a 500", r3.status_code == 200, r3.text[:160])

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
