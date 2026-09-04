"""Proof harness — canonical finance entity/scope enumeration is fail-closed (owner 2026-09-04).

THE INCIDENT: the cellfonz r us (house org) Cash Flow scope dropdown offered ANOTHER TENANT'S
companies — "Nova Wave" and "Luxlink" (LuxeLink org 854f6d7b) mixed in with cellfonz's own stores.
The data half was two poisoned `commcalc.companies` rows (LuxeLink entities created under the house
org on 2026-06-27, mig 952 removes them by id); the SYSTEMIC half is the canonical helper this
harness proves:

  • `coa.own_entities(rows, org_id)`      — the PURE fail-closed core of the ONE companies read
    (`coa.org_companies`, CI-pinned as the only `.table('companies')` select by
    harness_org_scope_guard.py's entity-enumeration section);
  • `coa.filter_org_scopes(scopes, ids)`  — the PURE dropdown cross-check: a `company:<id>` scope
    renders ONLY when <id> is in the org's own canonical inventory (used by /account/overview —
    the single scope-picker source for the dashboard/P&L/BS/Cash-Flow pages — and
    analysis.assemble's per-company comparison series).

TRUTH TABLE PROVED
  1. Org A's companies NEVER appear in org B's enumeration — either direction.
  2. The HOUSE org gets ONLY its own companies: house-default inheritance applies to CONFIG,
     never to tenant ENTITIES (no union, no fallback).
  3. Blank/None org → raises (a scope-less enumeration can never silently return rows).
  4. A row missing its org_id is DROPPED, not passed through (fail closed, defense in depth
     over the query's own .eq('org_id', …)).
  5. A stored scope for a foreign or since-deleted company NEVER renders in the dropdown,
     while own-company / consolidated / store scopes pass through byte-identical.

No DB, no network.  Run:  cd backend && python3 harness_finance_entity_enumeration.py
"""
import sys

from app.modules.account.coa import own_entities, filter_org_scopes

HOUSE = "00000000-0000-0000-0000-000000000001"           # cellfonz r us (house org)
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"            # LuxeLink tenant

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def main():
    # A mixed read the .eq() should never produce — but a poisoned client/view/cache might.
    # Shapes mirror the live incident: LuxeLink's entities alongside the house org's own.
    rows = [
        {"id": "co-house-1", "org_id": HOUSE, "name": "WIRELESS 2024 LLC"},
        {"id": "co-house-2", "org_id": HOUSE, "name": "PA PHONE TRADERS LLC"},
        {"id": "co-luxe-1", "org_id": LUXE, "name": "Nova Wave Communications"},
        {"id": "co-luxe-2", "org_id": LUXE, "name": "Luxlink Wireless"},
        {"id": "co-orphan", "name": "No Org Entity"},                 # org_id missing entirely
        {"id": "co-blank", "org_id": "", "name": "Blank Org Entity"},
        "not-a-row",                                                  # junk shape → dropped
    ]

    print("§1 cross-tenant isolation — either direction")
    house = own_entities(rows, HOUSE)
    luxe = own_entities(rows, LUXE)
    ok({r["id"] for r in house} == {"co-house-1", "co-house-2"},
       "house enumeration = exactly the house's own entities")
    ok({r["id"] for r in luxe} == {"co-luxe-1", "co-luxe-2"},
       "LuxeLink enumeration = exactly LuxeLink's own entities")
    ok(not ({r["id"] for r in house} & {r["id"] for r in luxe}),
       "no entity ever appears in both tenants' enumerations")

    print("§2 house org inherits CONFIG, never ENTITIES")
    ok(all(r["org_id"] == HOUSE for r in house),
       "house list contains no foreign-org row (no house union/fallback semantics)")
    only_luxe = [{"id": "x", "org_id": LUXE, "name": "Foreign"}]
    ok(own_entities(only_luxe, HOUSE) == [],
       "a house read over exclusively-foreign rows returns EMPTY, not a fallback")

    print("§3 fail closed on a missing scope")
    for bad in ("", None):
        try:
            own_entities(rows, bad)
            ok(False, f"org_id={bad!r} must raise, not return rows")
        except ValueError:
            ok(True, f"org_id={bad!r} raises ValueError (fail closed)")

    print("§4 malformed rows drop, never pass")
    ok(all(r["id"] not in ("co-orphan", "co-blank") for r in house + luxe),
       "rows with a missing/blank org_id are dropped for every caller")

    print("§5 scope-dropdown cross-check (filter_org_scopes)")
    own_ids = {"co-house-1", "co-house-2"}
    scopes = [
        {"scope_key": "consolidated", "scope_label": "Consolidated (all companies)"},
        {"scope_key": "company:co-house-1", "scope_label": "WIRELESS 2024 LLC"},
        {"scope_key": "company:co-luxe-1", "scope_label": "Novawave Communications LLC"},  # foreign
        {"scope_key": "company:co-deleted", "scope_label": "Ghost Co"},   # stale: entity deleted
        {"scope_key": "company:", "scope_label": "Malformed"},            # empty id
        {"scope_key": "store:T-531", "scope_label": "T-531"},
        {"scope_key": "store:4640-A W Diversey Ave", "scope_label": "4640-A W Diversey Ave"},
    ]
    kept = filter_org_scopes(scopes, own_ids)
    kept_keys = [s["scope_key"] for s in kept]
    ok("company:co-luxe-1" not in kept_keys,
       "a foreign tenant's company scope NEVER renders (the Nova Wave/Luxlink class)")
    ok("company:co-deleted" not in kept_keys,
       "a stale scope for a since-deleted company never renders")
    ok("company:" not in kept_keys, "a malformed company scope never renders")
    ok("company:co-house-1" in kept_keys, "the org's own company scope passes through")
    ok(kept_keys[0] == "consolidated" and "store:T-531" in kept_keys
       and "store:4640-A W Diversey Ave" in kept_keys,
       "consolidated + store scopes pass through byte-identical (companies-only rule — store "
       "hygiene is the §19.15 ingest guard's job, not the dropdown filter's)")
    ok(filter_org_scopes(scopes, set()) == [s for s in scopes
                                            if not s["scope_key"].startswith("company:")],
       "an org with NO companies gets NO company scopes (empty inventory = fail closed)")
    ok(filter_org_scopes([], own_ids) == [] and filter_org_scopes(None, own_ids) == [],
       "empty/None scope lists stay empty")

    print()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
