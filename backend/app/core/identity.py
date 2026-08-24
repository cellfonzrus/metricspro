"""The SINGLE store/employee identity resolver — the SSOT's one owned module (design blueprint
Part 2.3 / Part 3c).

WHY THIS FILE EXISTS
────────────────────
The audit found FIVE independent re-implementations of "resolve a store string to a store_code /
market" (`commcalc.flag_store_resolver`, `commcalc.ingest_store_guard`, `asset.router`'s market
index, the SQL RPC `flag_store_code_for`, and `commission_engine`'s exact lookup) and an equal
scatter of employee bridges (`_canon_person`, `_rep_canon_map`, `_emp_id_variants`,
`business_id_alias_map`, `_resolve_plan_for`). Each has its own ladder and its own spelling
tolerance, so the SAME store resolves differently in commissions vs assets vs closing vs ingest.
That drift is the disease. This module is the one place that answers the question, so every future
reader converges on one answer instead of drifting.

PHASE 1 POSTURE — WIRED INTO NOTHING
────────────────────────────────────
This module is BUILT but not yet called by any reader (that is Phase 3+). Because nothing imports
`resolve_store` / `resolve_employee` on a money path, it CANNOT change a payout or a market-filtered
dollar. It is dormant exactly like the resolver in migration 249 until deliberately switched on. The
proof harness (`backend/harness_identity_resolver.py`) exercises the PURE builders with a fake
client and no DB, the same testability contract as `flag_store_resolver.build_index` /
`scope.build_market_index` / `harness_luxelink_name_bridge`.

THE LADDER (the UNION of today's five store resolvers, priority order from
flag_store_resolver:89-98, extended per blueprint 2.3)
──────────────────────────────────────────────────────
  1  the key already IS a store_code                        (storeops.stores / store_mapping / alias 'code')
  2  an EXPLICIT alias synonym                              (store_alias carrier_code/sales_file_spelling/
                                                             merchant_id; alias's target entity must be real)
  3  commcalc.store_mapping.store_address / alias 'address' (the commission-side canonical address)
  4  storeops.stores.address                                (the roster address)
  5  a NORMALIZED-address fold                               (asset.router._normalize_addr — Rd/Road, 26th/26TH)
  6  a LEADING-street-number fallback                        (ingest_store_guard's last-resort match)
Lower priority number wins. Within a priority, ambiguity is REFUSED for the fuzzy tiers (5/6): a
folded/leading key that names more than one physical entity resolves to nothing rather than a
coin-flip (mirrors flag_store_resolver's salesforce_id refusal and asset.router's conflict exclusion).

MULTI-TENANT (contract §2): every read is `.eq("org_id", org_id)`; the cache is keyed on org_id,
NEVER on the client object (`get_supabase()` is a process-wide singleton). TTL is the same 30s as
app.core.scope so a newly-created store/alias shows up within seconds.

💰 MOVES NO MONEY in Phase 1: nothing here is on a commission/payroll write or read path.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# ── config-table cache (same shape/TTL as app.core.scope, org-keyed) ──────────────────────────────
_TTL_S = 30.0
_store_cache: dict[str, tuple[float, "StoreIndex"]] = {}
_emp_cache: dict[str, tuple[float, "EmployeeIndex"]] = {}


def _norm(v) -> str:
    return str(v or "").strip()


def _up(v) -> str:
    return _norm(v).upper()


# ── address folding (a byte-for-byte port of asset.router._normalize_addr, kept local so this module
# stays pure and importable without dragging the asset router in) ─────────────────────────────────
_ADDR_WORD_FOLD = {
    "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
    "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy",
    "square": "sq", "terrace": "ter", "circle": "cir", "mount": "mt", "north": "n",
    "south": "s", "east": "e", "west": "w", "saint": "st",
}


def _normalize_addr(s: str) -> str:
    """Loose address key for MATCHING only — case/whitespace/punctuation fold + the fixed
    street-suffix/direction abbreviation map. Identical to asset.router._normalize_addr so the two
    can never disagree once readers converge. Never a geocoder; unresolved text stays unresolved."""
    s = (s or "").strip().lower()
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = [_ADDR_WORD_FOLD.get(w, w) for w in s.split(" ")]
    return " ".join(words)


def _lead_number(v) -> str:
    """The leading street number of an address string, or "" — ingest_store_guard's last-resort
    matcher (`known_store_matcher._lead`)."""
    m = re.match(r"\s*(\d+)", str(v or ""))
    return m.group(1) if m else ""


# ── person-name canon (a port of commission_engine._canon_person: casefold · trim · collapse
# whitespace · reorder a single "Last, First"), kept local for purity ─────────────────────────────
def _canon_person(s) -> str:
    folded = re.sub(r"\s+", " ", ("" if s is None else str(s)).strip().casefold())
    if folded.count(",") == 1:
        last, first = (x.strip() for x in folded.split(","))
        if last and first:
            return f"{first} {last}"
    return folded


# ── returned identities ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StoreIdentity:
    entity_id: str
    store_code: str
    store_address: str
    market: str | None
    timezone: str | None
    is_active: bool


@dataclass(frozen=True)
class EmployeeIdentity:
    entity_id: str
    employee_id: str          # the business id (the human key)
    numeric_id: str | None
    name: str
    home_store: str | None
    pay_rate: float | None
    is_active: bool


# ── PURE store-index build (no I/O → unit-provable) ───────────────────────────────────────────────
@dataclass
class StoreIndex:
    entities: dict          # entity_id -> StoreIdentity
    exact: dict             # UPPER key (code|synonym|address) -> entity_id   (priorities 1-4)
    norm: dict              # _normalize_addr(address) -> entity_id           (priority 5, unambiguous)
    lead: dict              # leading street number -> entity_id              (priority 6, unambiguous)


# priority per store_alias.alias_kind (lower wins; None => not a resolution key on its own)
_STORE_ALIAS_PRI = {
    "code": 1, "carrier_code": 2, "sales_file_spelling": 2, "merchant_id": 2,
    "salesforce_id": 2, "address": 3,
}


def _build_store_index(store_rows, mapping_rows, alias_rows) -> StoreIndex:
    """Fold the org's store vocabulary into ONE lookup keyed on the stable `entity_id`.

    `store_rows`   — storeops.stores: entity_id / store_code / address / market / timezone / is_active
    `mapping_rows` — commcalc.store_mapping: store_code / store_address / salesforce_id / market / is_active
    `alias_rows`   — storeops.store_alias: alias_kind / alias_value / entity_id

    The `storeops.stores` row is the ENTITY ANCHOR (entity_id lives there). store_mapping fills
    address/market gaps by store_code match and contributes address keys; the unified alias table
    contributes explicit synonyms. Returns a StoreIndex; see the module ladder for priorities."""
    store_rows = store_rows or []
    mapping_rows = mapping_rows or []
    alias_rows = alias_rows or []

    entities: dict[str, dict] = {}
    code_to_entity: dict[str, str] = {}      # UPPER store_code -> entity_id
    for r in store_rows:
        eid = _norm(r.get("entity_id"))
        if not eid:
            continue
        code = _norm(r.get("store_code"))
        entities[eid] = {
            "entity_id": eid,
            "store_code": code,
            "store_address": _norm(r.get("address")),
            "market": _norm(r.get("market")) or None,
            "timezone": _norm(r.get("timezone")) or None,
            "is_active": bool(r.get("is_active")) if r.get("is_active") is not None else True,
        }
        if code:
            code_to_entity.setdefault(_up(code), eid)

    # store_mapping fills gaps (address/market) on the entity that owns its store_code, first-non-empty.
    for r in mapping_rows:
        eid = code_to_entity.get(_up(r.get("store_code")))
        if not eid:
            continue
        ent = entities[eid]
        if not ent["store_address"] and _norm(r.get("store_address")):
            ent["store_address"] = _norm(r.get("store_address"))
        if not ent["market"] and _norm(r.get("market")):
            ent["market"] = _norm(r.get("market"))

    # ── priority buckets: key -> {entity_id, …}; ambiguity resolved deterministically at flatten ──
    by_pri: dict[int, dict[str, set]] = {1: {}, 2: {}, 3: {}, 4: {}}

    def _add(pri: int, key: str, eid: str) -> None:
        if key and eid and eid in entities:
            by_pri[pri].setdefault(key, set()).add(eid)

    # pri 1 — key IS a code
    for eid, ent in entities.items():
        _add(1, _up(ent["store_code"]), eid)
    for r in mapping_rows:                                   # a mapping code that maps to a real entity
        _add(1, _up(r.get("store_code")), code_to_entity.get(_up(r.get("store_code"))))

    # pri 2/3 — explicit alias synonyms + alias addresses (target entity must be REAL, else skipped)
    for r in alias_rows:
        kind = _norm(r.get("alias_kind")).lower()
        pri = _STORE_ALIAS_PRI.get(kind)
        if pri is None:
            continue
        _add(pri, _up(r.get("alias_value")), _norm(r.get("entity_id")))

    # pri 3 — store_mapping.store_address
    for r in mapping_rows:
        _add(3, _up(r.get("store_address")), code_to_entity.get(_up(r.get("store_code"))))
    # pri 4 — storeops.stores.address
    for eid, ent in entities.items():
        _add(4, _up(ent["store_address"]), eid)

    exact: dict[str, str] = {}
    for pri in (4, 3, 2, 1):                                 # lower pri number overwrites (wins)
        for key, eids in by_pri[pri].items():
            exact[key] = _pick(eids, entities)

    # pri 5 — normalized-address fold (both address vocabularies); unambiguous only.
    norm_multi: dict[str, set] = {}
    for eid, ent in entities.items():
        nk = _normalize_addr(ent["store_address"])
        if nk:
            norm_multi.setdefault(nk, set()).add(eid)
    for r in mapping_rows:
        eid = code_to_entity.get(_up(r.get("store_code")))
        nk = _normalize_addr(r.get("store_address"))
        if eid and nk:
            norm_multi.setdefault(nk, set()).add(eid)
    norm = {k: next(iter(v)) for k, v in norm_multi.items() if len(v) == 1}

    # pri 6 — leading street number; unambiguous only (weakest, never a coin-flip).
    lead_multi: dict[str, set] = {}
    for eid, ent in entities.items():
        n = _lead_number(ent["store_address"])
        if n:
            lead_multi.setdefault(n, set()).add(eid)
    for r in mapping_rows:
        eid = code_to_entity.get(_up(r.get("store_code")))
        n = _lead_number(r.get("store_address"))
        if eid and n:
            lead_multi.setdefault(n, set()).add(eid)
    lead = {k: next(iter(v)) for k, v in lead_multi.items() if len(v) == 1}

    ident = {eid: StoreIdentity(**ent) for eid, ent in entities.items()}
    return StoreIndex(entities=ident, exact=exact, norm=norm, lead=lead)


def _pick(eids, entities) -> str:
    """Deterministic winner among candidate entity_ids for one key: alphabetically-first store_code
    (byte-identical tiebreak to flag_store_resolver's `sorted(cs)[0]`), then entity_id."""
    return sorted(eids, key=lambda e: (_up(entities[e]["store_code"]), e))[0]


def _resolve_store_in_index(index: StoreIndex, *candidates) -> StoreIdentity | None:
    """First of `candidates` the index recognises → its StoreIdentity, else None. Exact (pri 1-4)
    first for EVERY candidate, then the normalized fold, then the leading-number fallback — so a
    candidate that matches exactly can never be pre-empted by a fuzzier match on a later candidate."""
    if not index:
        return None
    for v in candidates:
        k = _up(v)
        if k and k in index.exact:
            return index.entities.get(index.exact[k])
    for v in candidates:
        nk = _normalize_addr(v)
        if nk and nk in index.norm:
            return index.entities.get(index.norm[nk])
    for v in candidates:
        n = _lead_number(v)
        if n and n in index.lead:
            return index.entities.get(index.lead[n])
    return None


# ── PURE employee-index build (no I/O) ────────────────────────────────────────────────────────────
@dataclass
class EmployeeIndex:
    entities: dict          # entity_id -> EmployeeIdentity
    by_business: dict       # UPPER business employee_id -> entity_id
    by_numeric: dict        # str(numeric id) -> entity_id (collision-guarded)
    by_login: dict          # UPPER epay_login -> entity_id
    by_name: dict           # _canon_person(name/pos/variant) -> entity_id (unambiguous only)


_EMP_ALIAS_KINDS_NAME = {"pos_name", "name_variant"}


def _build_employee_index(employee_rows, name_map_rows, rep_alias_rows, alias_rows=None) -> EmployeeIndex:
    """Fold the org's employee vocabulary into ONE lookup keyed on `entity_id`. Subsumes
    `_rep_canon_map` (name_map + rep_aliases), `_canon_person` (name-order canon) and the
    `_emp_id_variants` / `business_id_alias_map` numeric-vs-business-id collision guard.

    `employee_rows` — storeops.employees: entity_id / employee_id / id / name / epay_login /
                      epay_salesperson / home_store / pay_rate / is_active
    `name_map_rows` — commcalc.name_map: epay_login / epay_salesperson / storeops_name
    `rep_alias_rows`— commcalc.rep_aliases: alias / canonical
    `alias_rows`    — storeops.employee_alias: alias_kind / alias_value / entity_id (optional)
    """
    employee_rows = employee_rows or []
    name_map_rows = name_map_rows or []
    rep_alias_rows = rep_alias_rows or []
    alias_rows = alias_rows or []

    entities: dict[str, EmployeeIdentity] = {}
    by_business: dict[str, str] = {}
    by_login: dict[str, str] = {}
    name_multi: dict[str, set] = {}
    name_canon_to_entity: dict[str, str] = {}   # canon(name) -> eid, for name_map/rep_aliases anchoring

    all_business = {_norm(e.get("employee_id")) for e in employee_rows if _norm(e.get("employee_id"))}

    def _add_name(canon: str, eid: str) -> None:
        if canon and eid:
            name_multi.setdefault(canon, set()).add(eid)

    numeric_pending: list[tuple[str, str]] = []   # (numeric_s, eid) resolved after the collision set
    for r in employee_rows:
        eid = _norm(r.get("entity_id"))
        if not eid:
            continue
        biz = _norm(r.get("employee_id"))
        entities[eid] = EmployeeIdentity(
            entity_id=eid,
            employee_id=biz,
            numeric_id=(str(r["id"]) if r.get("id") is not None else None),
            name=_norm(r.get("name")),
            home_store=_norm(r.get("home_store")) or None,
            pay_rate=(float(r["pay_rate"]) if r.get("pay_rate") not in (None, "") else None),
            is_active=bool(r.get("is_active")) if r.get("is_active") is not None else True,
        )
        if biz:
            by_business.setdefault(_up(biz), eid)
        if _norm(r.get("epay_login")):
            by_login.setdefault(_up(r.get("epay_login")), eid)
        for nm in (r.get("name"), r.get("epay_salesperson")):
            c = _canon_person(nm)
            if c:
                _add_name(c, eid)
        c_name = _canon_person(r.get("name"))
        if c_name:
            name_canon_to_entity.setdefault(c_name, eid)
        # numeric id → entity, but only when NO other employee owns it as their business id
        numeric_s = str(r["id"]).strip() if r.get("id") is not None else ""
        if numeric_s and numeric_s != biz:
            numeric_pending.append((numeric_s, eid))

    by_numeric: dict[str, str] = {}
    for numeric_s, eid in numeric_pending:
        if numeric_s not in all_business:          # collision guard (business_id_alias_map / _emp_id_variants)
            by_numeric.setdefault(numeric_s, eid)

    # name_map: a POS salesperson spelling IS a roster name → the entity whose name canon matches it.
    for r in name_map_rows:
        target = name_canon_to_entity.get(_canon_person(r.get("storeops_name")))
        if target:
            _add_name(_canon_person(r.get("epay_salesperson")), target)
            if _norm(r.get("epay_login")):
                by_login.setdefault(_up(r.get("epay_login")), target)
    # rep_aliases: an alias name IS the canonical rep → the entity whose name canon == canonical.
    for r in rep_alias_rows:
        target = name_canon_to_entity.get(_canon_person(r.get("canonical")))
        if target:
            _add_name(_canon_person(r.get("alias")), target)

    # unified employee_alias rows
    for r in alias_rows:
        kind = _norm(r.get("alias_kind")).lower()
        eid = _norm(r.get("entity_id"))
        if not eid or eid not in entities:
            continue
        val = r.get("alias_value")
        if kind == "business_id":
            by_business.setdefault(_up(val), eid)
        elif kind == "numeric_id":
            if _norm(val) and _norm(val) not in all_business:
                by_numeric.setdefault(_norm(val), eid)
        elif kind == "epay_login":
            by_login.setdefault(_up(val), eid)
        elif kind in _EMP_ALIAS_KINDS_NAME:
            _add_name(_canon_person(val), eid)

    by_name = {k: next(iter(v)) for k, v in name_multi.items() if len(v) == 1}
    return EmployeeIndex(entities=entities, by_business=by_business, by_numeric=by_numeric,
                         by_login=by_login, by_name=by_name)


def _resolve_employee_in_index(index: EmployeeIndex, *candidates) -> EmployeeIdentity | None:
    """First of `candidates` the index recognises → its EmployeeIdentity, else None. A candidate is
    tried as a business id, an epay login, a numeric id, then a canon name — in that order — before
    moving to the next candidate."""
    if not index:
        return None
    for v in candidates:
        eid = (index.by_business.get(_up(v)) or index.by_login.get(_up(v))
               or index.by_numeric.get(_norm(v)) or index.by_name.get(_canon_person(v)))
        if eid:
            return index.entities.get(eid)
    return None


# ── I/O (best-effort, TTL-cached, org-scoped) ─────────────────────────────────────────────────────
def store_index(client, org_id: str, *, fresh: bool = False) -> StoreIndex:
    """The org's store identity index (see _build_store_index). Each source is best-effort — a read
    failure on one vocabulary never blanks the others, and a missing table degrades to an empty side
    rather than a 500 (contract §5)."""
    now = time.time()
    if not fresh:
        hit = _store_cache.get(org_id)
        if hit and (now - hit[0]) < _TTL_S:
            return hit[1]
    store_rows, mapping_rows, alias_rows = [], [], []
    try:
        store_rows = (client.schema("storeops").table("stores")
                      .select("entity_id,store_code,address,market,timezone,is_active")
                      .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity store_index storeops.stores read failed: {e}")
    try:
        mapping_rows = (client.schema("commcalc").table("store_mapping")
                        .select("store_code,store_address,salesforce_id,market,is_active")
                        .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity store_index commcalc.store_mapping read failed: {e}")
    try:
        alias_rows = (client.schema("storeops").table("store_alias")
                      .select("alias_kind,alias_value,entity_id")
                      .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity store_index storeops.store_alias read failed: {e}")
    idx = _build_store_index(store_rows, mapping_rows, alias_rows)
    _store_cache[org_id] = (now, idx)
    return idx


def employee_index(client, org_id: str, *, fresh: bool = False) -> EmployeeIndex:
    """The org's employee identity index (see _build_employee_index). Best-effort, org-scoped."""
    now = time.time()
    if not fresh:
        hit = _emp_cache.get(org_id)
        if hit and (now - hit[0]) < _TTL_S:
            return hit[1]
    emp_rows, name_map_rows, rep_alias_rows, alias_rows = [], [], [], []
    try:
        emp_rows = (client.schema("storeops").table("employees")
                    .select("entity_id,employee_id,id,name,home_store,pay_rate,is_active,"
                            "epay_login,epay_salesperson")
                    .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity employee_index storeops.employees read failed: {e}")
    try:
        name_map_rows = (client.schema("commcalc").table("name_map")
                         .select("epay_login,epay_salesperson,storeops_name")
                         .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity employee_index commcalc.name_map read failed: {e}")
    try:
        rep_alias_rows = (client.schema("commcalc").table("rep_aliases")
                          .select("alias,canonical").eq("org_id", org_id)
                          .limit(20000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity employee_index commcalc.rep_aliases read failed: {e}")
    try:
        alias_rows = (client.schema("storeops").table("employee_alias")
                      .select("alias_kind,alias_value,entity_id")
                      .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.identity employee_index storeops.employee_alias read failed: {e}")
    idx = _build_employee_index(emp_rows, name_map_rows, rep_alias_rows, alias_rows)
    _emp_cache[org_id] = (now, idx)
    return idx


def resolve_store(client, org_id: str, *candidates) -> StoreIdentity | None:
    """Resolve any store spelling (code / address / raw / salesforce_id / merchant_id / synonym) into
    the ONE canonical store entity it names, or None. Reads the cached index; never raises."""
    try:
        return _resolve_store_in_index(store_index(client, org_id), *candidates)
    except Exception as e:                                          # pragma: no cover - defensive
        print(f"WARN core.identity resolve_store failed: {e}")
        return None


def resolve_employee(client, org_id: str, *candidates) -> EmployeeIdentity | None:
    """Resolve any employee spelling (business id / numeric id / POS name / login / name variant) into
    the ONE canonical person it names, or None. Reads the cached index; never raises."""
    try:
        return _resolve_employee_in_index(employee_index(client, org_id), *candidates)
    except Exception as e:                                          # pragma: no cover - defensive
        print(f"WARN core.identity resolve_employee failed: {e}")
        return None


def invalidate(org_id: str = None) -> None:
    """Drop the cached indexes (call after a store/employee/alias write so the next resolve is current)."""
    if org_id is None:
        _store_cache.clear()
        _emp_cache.clear()
    else:
        _store_cache.pop(org_id, None)
        _emp_cache.pop(org_id, None)
