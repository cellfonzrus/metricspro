"""REAL-ASGI smoke for agent/commission/month-boundary-derive.

The unit harness (month_boundary_derive_proof.py) calls the router functions directly, which proves the
logic but NOT the mount. `[[curl-verified-not-ui-verified-apiv1]]`: `client.ts` `api()` needs an explicit
/api/v1 prefix, so a handler that answers when called can still sit at a path the page never reaches.
This drives the WHOLE FastAPI app through Starlette's TestClient at the EXACT URLs
`(platform)/commcalc/sales-derive/page.tsx` fetches, and asserts:

  • every URL the page calls answers 200 over HTTP, with the keys it destructures
  • the BARE paths (no /api/v1) are 404 — the page must use the prefix
  • org_id really travels as a QUERY PARAM: a second tenant's URL returns that tenant's OWN window and
    its OWN gap, and an empty org_id is rejected 400
  • ZERO writes from every GET (the client raises on any write while a GET is in flight)
  • the config PUT persists, round-trips, and clamps a hostile body
  • migration 266 unapplied ⇒ the GETs still answer 200 with the code default and the PUT fails 500
    NAMING the migration — never a silent wrong answer
  • the route table grew by EXACTLY the three endpoints this package adds (pinned literal base count)

Run: `python3 scratchpad/month_boundary_derive_asgi_smoke.py` from the backend dir.
"""
import copy
import io
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)

BASE_ROUTES = 967          # LITERAL route count of the pinned base 3d176fc (measured on that tree)
NEW_ROUTES = 3             # GET/PUT /sales/derive-config + GET /sales/derive-status


def _helpers():
    """Reuse the proof harness's Fake client + fixtures WITHOUT re-running its assertions: execute only
    the source ABOVE its first section banner (house harness style)."""
    path = os.path.join(_HERE, "month_boundary_derive_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'head("A — PURE PERIOD ARITHMETIC'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("mbd_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _helpers()

from fastapi.testclient import TestClient          # noqa: E402
import app.core.database as DB                     # noqa: E402
from app.main import app                           # noqa: E402
from app.modules.commcalc import router as R       # noqa: E402
from app.modules.commcalc import sales_derive as SD  # noqa: E402

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{('   ' + str(extra)) if extra else ''}")


def head(t):
    print(f"\n{'=' * 100}\n{t}\n{'=' * 100}")


ORG_A, ORG_B = H.ORG_A, H.ORG_B
frow = H.frow


class NoWrite(H.Fake):
    """The fixture client with WRITES BANNED — any insert/upsert/delete during a GET blows up loudly."""
    ban = True

    def table(self, t):
        q = H._Q(self, t)
        parent = self

        def _no(*a, **k):
            if parent.ban:
                raise AssertionError(f"WRITE ATTEMPTED FROM A GET on {t}")
            return q
        q_insert, q_upsert, q_delete = q.insert, q.upsert, q.delete

        def insert(rows):
            _no()
            return q_insert(rows)

        def upsert(row, on_conflict=None):
            _no()
            return q_upsert(row, on_conflict=on_conflict)

        def delete():
            _no()
            return q_delete()
        q.insert, q.upsert, q.delete = insert, upsert, delete
        return q


def fixture():
    """Tenant A: 10 July feed transactions, 7 in the basis (gap = 3), default window.
       Tenant B: in step, window explicitly OFF."""
    return {
        "daily_sales_feed": [frow(ORG_A, f"G{i}", "2026-07-10", 7) for i in range(1, 11)]
                            + [frow(ORG_B, f"B{i}", "2026-07-10", 9) for i in range(1, 4)],
        "raw_sales": [frow(ORG_A, f"G{i}", "2026-07-10", 7) for i in range(1, 8)]
                     + [frow(ORG_B, f"B{i}", "2026-07-10", 9) for i in range(1, 4)],
        "commission_org_config": [{"org_id": ORG_A, "sales_derive_grace": None},
                                  {"org_id": ORG_B, "sales_derive_grace": {"enabled": False, "days": 0}}],
        "report_definitions": [{"org_id": ORG_A, "report_key": "sales", "auto": True},
                               {"org_id": ORG_B, "report_key": "sales", "auto": True}],
    }


CLIENT = NoWrite(fixture())
DB.get_supabase = lambda: CLIENT
R.sb = lambda: CLIENT
try:
    from app.modules.commcalc import sales_recon as SR
    SR.get_supabase = lambda: CLIENT
except Exception:
    pass
# never let the RBAC gate 403 the PUT in the harness (it degrades OPEN when the caller is unresolvable,
# which is what an unauthenticated TestClient request is)
tc = TestClient(app)

PRIOR = "July 2026"
V1 = "/api/v1/commcalc"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("1 — THE PAGE'S OWN URLS ANSWER OVER HTTP (with /api/v1, and only with it)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
r = tc.get(f"{V1}/sales/derive-config?org_id={ORG_A}")
check("GET /sales/derive-config → 200", r.status_code == 200, r.text[:200])
cfg = r.json() if r.status_code == 200 else {}
for k in ("config", "default", "max_days", "window_open", "current_period", "prior_period",
          "next_run_periods", "grace_note", "today"):
    check(f"derive-config carries '{k}' (the page destructures it)", k in cfg)
check("config is the code default for a tenant with a NULL column",
      cfg.get("config") == {"enabled": True, "days": 3, "retain": None}, cfg.get("config"))
check("next_run_periods is a real plan", isinstance(cfg.get("next_run_periods"), list)
      and cfg["next_run_periods"][0] == cfg.get("current_period"))

r = tc.get(f"{V1}/sales/derive-status?org_id={ORG_A}&period={PRIOR}")
check("GET /sales/derive-status → 200", r.status_code == 200, r.text[:200])
st = r.json() if r.status_code == 200 else {}
for k in ("period", "feed_trans", "monthly_trans", "missing_in_monthly", "missing_in_daily",
          "has_feed", "sample_missing", "capped", "is_closed_month", "grace_window_open",
          "grace_config", "auto_derive_enabled", "action"):
    check(f"derive-status carries '{k}' (the page destructures it)", k in st)
check("the gap is the real number (10 in feed, 7 in basis ⇒ 3)",
      st.get("feed_trans") == 10 and st.get("monthly_trans") == 7 and st.get("missing_in_monthly") == 3, st)
check("it names an action when behind", bool(st.get("action")))
check("July is reported as a CLOSED month", st.get("is_closed_month") is True)

check("derive-status with NO period defaults to the month that just closed",
      tc.get(f"{V1}/sales/derive-status?org_id={ORG_A}").json().get("period")
      == SD.prior_period_label(R._datetime.now()))

for bare in ("/commcalc/sales/derive-config", "/commcalc/sales/derive-status"):
    check(f"BARE {bare} is 404 (the page must use /api/v1)",
          tc.get(f"{bare}?org_id={ORG_A}").status_code == 404)

check("the pre-existing promote-feed endpoint the page's buttons call still answers",
      tc.post(f"{V1}/sales/promote-feed?org_id={ORG_A}&period={PRIOR}&dry_run=true").status_code == 200)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("2 — org_id IS A QUERY PARAM (RULE ONE), and GETs write NOTHING")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
cb = tc.get(f"{V1}/sales/derive-config?org_id={ORG_B}").json()
check("tenant B gets its OWN window (explicitly off), not A's default",
      cb.get("config", {}).get("enabled") is False, cb.get("config"))
check("tenant B's next run is the current month only",
      cb.get("next_run_periods") == [cb.get("current_period")], cb.get("next_run_periods"))
sb_ = tc.get(f"{V1}/sales/derive-status?org_id={ORG_B}&period={PRIOR}").json()
check("tenant B sees its OWN gap (in step, 3 vs 3)",
      sb_.get("feed_trans") == 3 and sb_.get("monthly_trans") == 3 and sb_.get("missing_in_monthly") == 0, sb_)
check("tenant B never sees tenant A's transactions", sb_.get("sample_missing") == [])

for path in ("sales/derive-config", "sales/derive-status"):
    rr = tc.get(f"{V1}/{path}?org_id=")
    check(f"empty org_id rejected on /{path} (400)", rr.status_code == 400, rr.status_code)

check("no GET wrote anything (the client would have raised)", CLIENT.ban is True and not CLIENT.writes,
      CLIENT.writes)
check("raw_sales untouched by every GET so far",
      len([x for x in CLIENT.tables["raw_sales"] if x["org_id"] == ORG_A]) == 7)

# org_id is declared as a query parameter on every new endpoint (never a Form field / body / constant)
import inspect                                                        # noqa: E402
for fn in (R.get_sales_derive_config, R.put_sales_derive_config, R.get_sales_derive_status):
    sig = inspect.signature(fn)
    p = sig.parameters.get("org_id")
    check(f"{fn.__name__}: org_id is a plain query param defaulting to ORG_ID",
          p is not None and p.default == R.ORG_ID, p)
    src = inspect.getsource(fn)
    check(f"{fn.__name__}: calls require_org(org_id)", "require_org(org_id)" in src)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("3 — THE CONFIG PUT: persists, round-trips, clamps")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
CLIENT.ban = False
r = tc.put(f"{V1}/sales/derive-config?org_id={ORG_A}", json={"enabled": True, "days": 5, "retain": 1.0})
check("PUT → 200", r.status_code == 200, r.text[:300])
saved = r.json().get("config") if r.status_code == 200 else {}
check("PUT returns the saved window", saved == {"enabled": True, "days": 5, "retain": 1.0}, saved)
check("PUT round-trips through a fresh GET",
      tc.get(f"{V1}/sales/derive-config?org_id={ORG_A}").json().get("config") == saved)
row = [x for x in CLIENT.tables["commission_org_config"] if x["org_id"] == ORG_A][0]
check("it landed on THIS tenant's commission_org_config row, org-stamped",
      row.get("org_id") == ORG_A and row.get("sales_derive_grace") == saved)
check("tenant B's row was NOT touched",
      [x for x in CLIENT.tables["commission_org_config"] if x["org_id"] == ORG_B][0]
      ["sales_derive_grace"] == {"enabled": False, "days": 0})

r = tc.put(f"{V1}/sales/derive-config?org_id={ORG_A}", json={"enabled": True, "days": 999, "retain": 0.01})
check("hostile body is clamped, not stored raw",
      r.json().get("config") == {"enabled": True, "days": SD.MAX_GRACE_DAYS, "retain": 0.85},
      r.json().get("config"))
r = tc.put(f"{V1}/sales/derive-config?org_id={ORG_A}", json={"enabled": False})
check("switching it OFF restores the pre-fix plan (current month only)",
      r.json().get("next_run_periods") == [r.json().get("current_period")], r.json().get("next_run_periods"))
check("PUT rejects an empty org_id (400)",
      tc.put(f"{V1}/sales/derive-config?org_id=", json={"enabled": True}).status_code == 400)
# put it back to the default for the remaining sections
tc.put(f"{V1}/sales/derive-config?org_id={ORG_A}", json={"enabled": True, "days": 3, "retain": None})
CLIENT.ban = True


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("4 — MIGRATION 266 UNAPPLIED: still 200, code default, and the PUT names the migration")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
class Pre266(NoWrite):
    """commission_org_config exists but has no sales_derive_grace column — reads AND writes raise."""
    def table(self, t):
        q = super().table(t)
        if t == "commission_org_config":
            def boom(*a, **k):
                raise RuntimeError('column commission_org_config.sales_derive_grace does not exist')
            q.select = boom
            q.upsert = boom
        return q


PRE = Pre266(fixture())
PRE.ban = False
DB.get_supabase = lambda: PRE
R.sb = lambda: PRE
try:
    SR.get_supabase = lambda: PRE
except Exception:
    pass

r = tc.get(f"{V1}/sales/derive-config?org_id={ORG_A}")
check("pre-266 GET derive-config still 200", r.status_code == 200, r.text[:200])
check("pre-266 falls back to the CODE DEFAULT (the fix works before the SQL)",
      r.json().get("config") == dict(SD.DEFAULT), r.json().get("config"))
check("pre-266 GET derive-status still 200",
      tc.get(f"{V1}/sales/derive-status?org_id={ORG_A}&period={PRIOR}").status_code == 200)
r = tc.put(f"{V1}/sales/derive-config?org_id={ORG_A}", json={"enabled": True, "days": 3})
check("pre-266 PUT fails 500 NAMING migration 266 (never a silent wrong answer)",
      r.status_code == 500 and "266" in r.text, (r.status_code, r.text[:200]))

DB.get_supabase = lambda: CLIENT
R.sb = lambda: CLIENT
try:
    SR.get_supabase = lambda: CLIENT
except Exception:
    pass


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("5 — ROUTE TABLE: exactly three new routes, nothing removed")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
paths = sorted({getattr(r_, "path", "") for r_ in app.routes})
check(f"route count == pinned base {BASE_ROUTES} + {NEW_ROUTES}",
      len(app.routes) == BASE_ROUTES + NEW_ROUTES, len(app.routes))
for p in ("/api/v1/commcalc/sales/derive-config", "/api/v1/commcalc/sales/derive-status"):
    check(f"{p} is mounted", p in paths)
check("the pre-existing /sales/promote-feed route is untouched",
      "/api/v1/commcalc/sales/promote-feed" in paths)
check("the pre-existing /sales/promote-due route is untouched",
      "/api/v1/commcalc/sales/promote-due" in paths)
check("route ORDER: derive-config/derive-status are their own routes, not swallowed by a {param}",
      all(any(getattr(r_, "path", "") == p for r_ in app.routes)
          for p in ("/api/v1/commcalc/sales/derive-config", "/api/v1/commcalc/sales/derive-status")))
methods = set()
for r_ in app.routes:
    if getattr(r_, "path", "") == "/api/v1/commcalc/sales/derive-config":
        methods |= set(getattr(r_, "methods", None) or set())
check("derive-config answers GET and PUT on one path", {"GET", "PUT"} <= methods, methods)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
head("6 — THE PAGE ITSELF (static contract: /api/v1 prefix, org_id, keys, no bare fetch)")
# ════════════════════════════════════════════════════════════════════════════════════════════════════
import re                                                             # noqa: E402
PAGE = os.path.abspath(os.path.join(_BACKEND, "..", "frontend", "src", "app", "(platform)",
                                    "commcalc", "sales-derive", "page.tsx"))
src = io.open(PAGE, encoding="utf-8").read()
calls = re.findall(r"api\(\s*`([^`]+)`", src)
check("the page makes API calls at all", len(calls) >= 3, calls)
check("EVERY api() call carries the /api/v1 prefix (curl-verified != UI-verified)",
      all(c.startswith("/api/v1/") for c in calls), [c for c in calls if not c.startswith("/api/v1/")])
check("EVERY api() call passes org_id as a query param",
      all("org_id=${ORG_ID}" in c for c in calls), [c for c in calls if "org_id=${ORG_ID}" not in c])
check("no raw fetch() bypassing the client", "fetch(" not in src)
for p in ("/api/v1/commcalc/sales/derive-config", "/api/v1/commcalc/sales/derive-status",
          "/api/v1/commcalc/sales/promote-feed"):
    check(f"page calls {p}", any(c.startswith(p) for c in calls))
check("the page previews (dry_run=true) before it can commit (dry_run=false)",
      "dry_run=${commit ? 'false' : 'true'}" in src)
check("committing a re-derive is behind a confirm()", "confirm(" in src)
check("the page says re-deriving does NOT recalculate (money moves attended)",
      "does NOT recalculate" in src or "does not recalculate" in src.lower())
check("the page uses the shared period picker (RULE FIVE: pick, don't type)",
      "usePeriod" in src and "periods.map" in src)
check("no house org_id constant is hard-coded in the page",
      "00000000-0000-0000-0000-000000000001" not in src)
for k in ("missing_in_monthly", "feed_trans", "monthly_trans", "next_run_periods", "grace_note",
          "window_open", "max_days", "auto_derive_enabled", "capped", "sample_missing"):
    check(f"page consumes the '{k}' key the API returns", k in src)


print(f"\n{'=' * 100}\nRESULT: {_pass} passed, {_fail} failed\n{'=' * 100}")
sys.exit(1 if _fail else 0)
