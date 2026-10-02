"""THE ORG-TREE WALK — one home, answering for many stores at once (index §48.7).

WHAT WAS WRONG. "Which District Manager owns this store" was walked in TWO places:
`storeops.router._dm_for_store` and `storeops.router._managers_above_dm`, the second of which said so
in its own docstring — *"Mirrors _dm_for_store's district resolution"*. Two walks answering one
question is the duplicate defect the index rules forbid: they will drift, and the one that drifts is
the one that sends an email to the wrong manager.

And both are PER STORE: four table reads each (stores, org_levels, org_units, org_managers) plus one
per manager. That is fine for "email this store's DM" and impossible for a report that lists the DM
beside every store-day in a 62-day span — which is what the owner asked for on the Cash Accountability
Chain (2026-10-02: *"it should also show who is the DM assigned to that location"*). A third walk,
written for bulk, would have made it three.

So the walk lives HERE, PURE (rows in, dicts out) and in BULK: the caller reads the four tables ONCE
and asks about every store it cares about. `_dm_for_store` and `_managers_above_dm` keep their exact
signatures and return shapes and dereference this module; the chain endpoint calls it directly.

THE RULE IS UNCHANGED, deliberately. Climb `org_units.parent_id` from the store's unit to the first
node whose LEVEL NAME contains "district"; failing that, match a district-level unit by the store's
market (name contains the market, or code == "district:<market>"). Ported verbatim — including the
20-hop guard and the case-insensitive comparisons — so no tenant's resolution moves on the day this
becomes one function. Config over code (RULE TWO): the level is recognised by its NAME, which is
tenant data, and no tenant/carrier name appears here.

Proof: backend/harness_org_chain.py (stdlib, DB-free). Locked: the same harness fails the build if a
second district walk appears in storeops.
"""

DISTRICT_LEVEL_WORD = "district"
MAX_CLIMB = 20          # the guard both original walks carried, kept to the hop


def _s(v):
    return str(v or "").strip()


def index_tree(levels, units):
    """PURE: ({level_id: level_name}, {unit_id: unit}) from the raw org_levels / org_units rows."""
    return ({l.get("id"): (l.get("name") or "") for l in (levels or [])},
            {u.get("id"): u for u in (units or []) if u.get("id")})


def _is_district(level_name):
    return DISTRICT_LEVEL_WORD in _s(level_name).lower()


def district_for(unit_id, market, level_names, unit_by_id):
    """PURE: the district org_unit for one store, or None.

    Rule 1 — climb parents from the store's own unit to the first district-LEVEL node.
    Rule 2 — no unit, or no district above it: match a district-level unit by the store's MARKET,
             either because the market appears in the unit's name or because its code is
             "district:<market>". Market matching is case-insensitive, as it always was.
    """
    cur = unit_by_id.get(unit_id) if unit_id else None
    guard = 0
    while cur and guard < MAX_CLIMB:
        if _is_district(level_names.get(cur.get("level_id"))):
            return cur
        cur = unit_by_id.get(cur.get("parent_id"))
        guard += 1
    mk = _s(market).lower()
    if not mk:
        return None
    for u in unit_by_id.values():
        if not _is_district(level_names.get(u.get("level_id"))):
            continue
        if mk in _s(u.get("name")).lower() or _s(u.get("code")).lower() == f"district:{mk}":
            return u
    return None


def ancestors_of(unit, unit_by_id):
    """PURE: the units ABOVE `unit`, nearest first, to the root (the same 20-hop guard)."""
    out = []
    cur = unit_by_id.get((unit or {}).get("parent_id"))
    guard = 0
    while cur and guard < MAX_CLIMB:
        out.append(cur)
        cur = unit_by_id.get(cur.get("parent_id"))
        guard += 1
    return out


def managers_at(unit, mgr_eids_by_unit, emp_by_eid, level_names):
    """PURE: [{employee_id, name, email, unit, level}] for one unit — a node may have several."""
    out = []
    for eid in (mgr_eids_by_unit.get((unit or {}).get("id")) or []):
        if not eid:
            continue
        emp = emp_by_eid.get(eid) or {}
        out.append({"employee_id": eid, "name": emp.get("name"), "email": emp.get("email"),
                    "unit": (unit or {}).get("name"), "level": level_names.get((unit or {}).get("level_id"))})
    return out


def chain_for(unit_id, market, level_names, unit_by_id, mgr_eids_by_unit, emp_by_eid):
    """PURE: {"district", "dm", "above"} for ONE store — the district unit, its manager(s), and every
    manager above it to the root, each deduped by employee_id in nearest-first order."""
    district = district_for(unit_id, market, level_names, unit_by_id)
    if not district:
        return {"district": None, "dm": [], "above": []}
    above, seen = [], set()
    for anc in ancestors_of(district, unit_by_id):
        for m in managers_at(anc, mgr_eids_by_unit, emp_by_eid, level_names):
            key = _s(m.get("employee_id"))
            if key and key not in seen:
                seen.add(key)
                above.append(m)
    return {"district": district,
            "dm": managers_at(district, mgr_eids_by_unit, emp_by_eid, level_names),
            "above": above}


def dm_by_store(stores, levels, units, managers, employees, market_by_code=None):
    """PURE: {store_code: {"district", "dm", "above"}} for EVERY store handed in — the bulk answer.

    `stores`      storeops.stores rows (store_code, org_unit_id, market)
    `managers`    org_managers rows (unit_id, employee_id)
    `employees`   employees rows (employee_id, name, email)
    `market_by_code` optional {STORE_CODE: market} overlay for a store whose market lives only in
                  commcalc.store_mapping — the canonical-union fallback both original walks applied
                  (the 2026-09-03 "fix once for all" rule), passed in rather than read here so this
                  module stays DB-free.
    """
    level_names, unit_by_id = index_tree(levels, units)
    mgr_eids_by_unit = {}
    for m in (managers or []):
        mgr_eids_by_unit.setdefault(m.get("unit_id"), []).append(m.get("employee_id"))
    emp_by_eid = {e.get("employee_id"): e for e in (employees or []) if e.get("employee_id")}
    overlay = {_s(k).upper(): v for k, v in (market_by_code or {}).items()}
    out = {}
    for st in (stores or []):
        code = _s(st.get("store_code"))
        if not code:
            continue
        market = _s(st.get("market")) or overlay.get(code.upper()) or ""
        out[code] = chain_for(st.get("org_unit_id"), market, level_names, unit_by_id,
                              mgr_eids_by_unit, emp_by_eid)
    return out


def dm_names(chain):
    """PURE: the DM name(s) for one store's chain, as a list — [] when the tree is not wired.

    A store with no resolvable district reports NOTHING, never a placeholder: "no DM is assigned here"
    and "the org tree was never configured" are different facts, and a report that prints a name it
    guessed is worse than one that prints nothing (the house's absence-is-never-zero doctrine).
    """
    return [n for n in ((m.get("name") or m.get("employee_id")) for m in (chain or {}).get("dm") or []) if n]
