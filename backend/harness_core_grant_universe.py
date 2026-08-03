"""Proof harness — GET /core/markets + GET /core/scope-preview (2026-08-03 scope-split package).

Runs the ACTUAL shipped handlers in app.modules.core.router against a fake Supabase client (same
convention as harness_core_bootstrap.py) — no DB, no network. Run from backend/:
    python3 harness_core_grant_universe.py

Proves:
  1. /core/markets offers the tenant's FULL market vocabulary, unioning storeops.stores.market AND
     commcalc.store_mapping.market — i.e. "PA" (which existed only in store_mapping) is now offered.
     This is the exact owner complaint of 2026-08-03: "the option to select PA from the roles and
     config is not there".
  2. /core/markets is NOT span-scoped: an admin handing out a grant sees every market, so they can
     delegate a market they do not personally cover. (The old picker sourced GET /storeops/stores,
     which IS span-scoped.)
  3. /core/markets is ORG-scoped — another tenant's markets/stores never appear.
  4. /core/markets returns the store rows the store picker needs (store_code + address + market),
     including a store whose market is only known to store_mapping.
  5. /core/scope-preview requires authentication (401) and admin rights (403) — it discloses another
     person's grants.
  6. /core/scope-preview shows a 3-market DM resolving to EXACTLY those markets' stores, and NOT to
     the whole org — the proof that a market-only grant constrains.
  7. /core/scope-preview surfaces `unresolved_markets` for a market spelled differently on the
     app_user than on the stores (the silent-empty-grant tell).
  8. /core/scope-preview reports scope 'all' as UNRESTRICTED (admin path unchanged).
  9. /core/scope-preview reports scheduling reach 'org' by DEFAULT for a legacy role with no
     scheduling_reach key — i.e. the DM can still pick any employee while scheduling.
 10. Setting scheduling_reach='span' flips ONLY the scheduling half; reporting is untouched.
 11. The org-unit subtree (org_span_for_manager RPC) still unions into the reporting span.
 12. Degradation: a missing commcalc.store_mapping never 500s either endpoint.
"""
import asyncio
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

import app.modules.core.router as rt          # noqa: E402
from app.core import scope as S               # noqa: E402
from fastapi import HTTPException             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-0000000000ff"


class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeSchema:
    def __init__(self, data, name):
        self._data, self._name = data, name

    def table(self, t):
        tables = self._data.get(self._name, {})
        if t not in tables:
            raise RuntimeError(f"relation {self._name}.{t} does not exist")
        return FakeQuery(tables[t])


class FakeClient:
    def __init__(self, data, rpc_rows=None):
        self._data, self._rpc_rows = data, rpc_rows or {}

    def schema(self, name):
        return FakeSchema(self._data, name)

    def table(self, t):                      # core.sb() == get_supabase() (public/default schema)
        return FakeSchema(self._data, "public").table(t)

    def rpc(self, fn, params):
        return FakeQuery(self._rpc_rows.get((fn, params.get("p_employee_id")), []))


DATA = {
    "storeops": {
        "stores": [
            {"org_id": ORG, "store_code": "B101", "address": "100 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "B102", "address": "200 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "J201", "address": "10 NEWARK AVE", "market": "NJ"},
            {"org_id": ORG, "store_code": "C401", "address": "5 MAIN ST", "market": "CT"},
            {"org_id": ORG, "store_code": "P301", "address": "1 PENN AVE", "market": None},
            {"org_id": OTHER, "store_code": "Z999", "address": "OTHER", "market": "TX"},
        ],
        "app_users": [
            {"org_id": ORG, "email": "dm@x.com", "full_name": "Test DM", "role": "district_manager",
             "employee_id": "E77", "market": "NY,NJ,CT", "store_code": None, "store_codes": []},
            {"org_id": ORG, "email": "typo@x.com", "full_name": "Typo DM", "role": "district_manager",
             "employee_id": None, "market": "NY,Pennsylvania", "store_code": None, "store_codes": []},
            {"org_id": ORG, "email": "boss@x.com", "full_name": "Boss", "role": "admin",
             "employee_id": None, "market": None, "store_code": None, "store_codes": []},
            {"org_id": OTHER, "email": "other@x.com", "role": "admin"},
        ],
        "roles": [
            {"org_id": ORG, "name": "district_manager",
             "permissions": {"scope": "market", "modules": {"commissions": True, "storeops": True}}},
            {"org_id": ORG, "name": "district_manager_locked",
             "permissions": {"scope": "market", "scheduling_reach": "span", "modules": {}}},
            {"org_id": ORG, "name": "admin",
             "permissions": {"scope": "all", "modules": {"admin": True}}},
        ],
    },
    "commcalc": {
        "store_mapping": [
            {"org_id": ORG, "store_code": "B101", "store_address": "100 BROADWAY", "market": "NY"},
            {"org_id": ORG, "store_code": "P301", "store_address": "1 PENN AVE", "market": "PA"},
            {"org_id": ORG, "store_code": "P302", "store_address": "2 PENN AVE", "market": "PA"},
            {"org_id": OTHER, "store_code": "Z999", "store_address": "OTHER", "market": "TX"},
        ],
    },
}

RPC = {("org_span_for_manager", "E77"): [{"store_code": "C401"}, {"store_code": ""}]}


def install(data=DATA, caller_email="boss@x.com", uid="uid-boss"):
    """Point the router's sb()/_uid_from_token/_resolve_caller at the fakes."""
    S.invalidate_market_index()
    client = FakeClient(data, RPC)
    rt.sb = lambda: client
    rt._uid_from_token = lambda auth: (uid if auth else None)

    def _rc(_c, u, _active=None):
        if not u:
            return None
        row = next((r for r in data["storeops"]["app_users"]
                    if r.get("email") == caller_email), None)
        if not row:
            return None
        perms = next((r["permissions"] for r in data["storeops"]["roles"]
                      if r["name"] == row.get("role")), {})
        return {"org_id": ORG, "role": row.get("role"), "super_admin": False, "perms": perms}
    rt._resolve_caller = _rc
    return client


_orig = (rt.sb, rt._uid_from_token, rt._resolve_caller)

print("\n── /core/markets — the canonical GRANT universe ──────────────────────────────────")
install()
u = run(rt.grant_universe(org_id=ORG))
check("1. PA is offered (was missing — store_mapping-only market)", "PA" in u["markets"], u["markets"])
check("1b. every real market offered", u["markets"] == ["CT", "NJ", "NY", "PA"], u["markets"])
check("2. not span-scoped: full vocabulary regardless of caller", len(u["markets"]) == 4)
check("3. org-scoped: other tenant's TX never offered", "TX" not in u["markets"], u["markets"])
check("3b. org-scoped: other tenant's store never listed",
      all(s["store_code"] != "Z999" for s in u["stores"]))
codes = {s["store_code"] for s in u["stores"]}
check("4. store picker gets every store incl. mapping-only P302",
      codes == {"B101", "B102", "J201", "C401", "P301", "P302"}, codes)
p301 = next(s for s in u["stores"] if s["store_code"] == "P301")
check("4b. NULL storeops market backfilled from store_mapping", p301["market"] == "PA", p301)
check("4c. address preserved for the picker label", p301["address"] == "1 PENN AVE", p301)

print("\n── /core/scope-preview — gate ────────────────────────────────────────────────────")
install()
try:
    run(rt.scope_preview(email="dm@x.com", org_id=ORG, authorization=""))
    check("5. anonymous → 401", False, "no raise")
except HTTPException as e:
    check("5. anonymous → 401", e.status_code == 401, str(e.status_code))
install(caller_email="dm@x.com", uid="uid-dm")
try:
    run(rt.scope_preview(email="dm@x.com", org_id=ORG, authorization="Bearer t"))
    check("5b. non-admin (scope=market) → 403", False, "no raise")
except HTTPException as e:
    check("5b. non-admin (scope=market) → 403", e.status_code == 403, str(e.status_code))
install()
r = run(rt.scope_preview(email="dm@x.com", org_id=ORG, authorization="Bearer t"))
check("5c. admin caller allowed", isinstance(r, dict) and r.get("role") == "district_manager")

print("\n── /core/scope-preview — a 3-market DM actually CONSTRAINS ───────────────────────")
check("6. reporting is restricted, not unrestricted", r["reporting"]["unrestricted"] is False)
check("6b. resolves to exactly the granted markets' stores + org-unit",
      r["reporting"]["stores"] == ["B101", "B102", "C401", "J201"], r["reporting"]["stores"])
check("6c. PA store NOT in the DM's reporting span", "P301" not in r["reporting"]["stores"])
check("6d. P302 (mapping-only) NOT in span either", "P302" not in r["reporting"]["stores"])
check("11. org-unit subtree unioned in (C401 via org_span_for_manager)",
      "C401" in r["reporting"]["stores"] and r["org_unit_stores"] == ["C401"], r["org_unit_stores"])
check("6e. granted markets echoed", r["granted_markets"] == ["NY", "NJ", "CT"], r["granted_markets"])
check("6f. nothing unresolved for a correctly-spelled grant", r["unresolved_markets"] == [])
check("6g. org market list returned for comparison", r["org_markets"] == ["CT", "NJ", "NY", "PA"])

print("\n── /core/scope-preview — the silent-empty-grant tell ─────────────────────────────")
install()
r2 = run(rt.scope_preview(email="typo@x.com", org_id=ORG, authorization="Bearer t"))
check("7. misspelled market flagged as unresolved",
      r2["unresolved_markets"] == ["Pennsylvania"], r2["unresolved_markets"])
check("7b. the resolvable half still binds",
      r2["reporting"]["stores"] == ["B101", "B102"], r2["reporting"]["stores"])

print("\n── /core/scope-preview — admin path unchanged ────────────────────────────────────")
install()
r3 = run(rt.scope_preview(email="boss@x.com", org_id=ORG, authorization="Bearer t"))
check("8. scope 'all' → UNRESTRICTED", r3["reporting"]["unrestricted"] is True)
check("8b. no store list implied for an unrestricted caller", r3["reporting"]["stores"] == [])

print("\n── scheduling reach: default 'org', opt-in 'span' ────────────────────────────────")
check("9. legacy DM role (no key) → reach 'org'", r["scheduling"]["reach"] == "org")
check("9b. roster span-exempt → may pick ANY employee", r["scheduling"]["roster_span_exempt"] is True)
install()
r4 = run(rt.scope_preview(role="district_manager_locked", org_id=ORG, authorization="Bearer t"))
check("10. scheduling_reach='span' honored", r4["scheduling"]["reach"] == "span")
check("10b. roster NOT exempt when locked", r4["scheduling"]["roster_span_exempt"] is False)
check("10c. reporting half untouched by the scheduling knob", r4["scope"] == "market")

print("\n── degradation ───────────────────────────────────────────────────────────────────")
nodata = {"storeops": DATA["storeops"]}          # commcalc schema entirely absent
install(nodata)
u2 = run(rt.grant_universe(org_id=ORG))
check("12. store_mapping missing → still answers with storeops markets",
      u2["markets"] == ["CT", "NJ", "NY"], u2["markets"])
install(nodata)
r5 = run(rt.scope_preview(email="dm@x.com", org_id=ORG, authorization="Bearer t"))
check("12b. scope-preview survives the same outage",
      r5["reporting"]["stores"] == ["B101", "B102", "C401", "J201"], r5["reporting"]["stores"])

rt.sb, rt._uid_from_token, rt._resolve_caller = _orig
print(f"\n{'='*72}\n  RESULT: {len(PASS)} passed, {len(FAIL)} failed\n{'='*72}")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
