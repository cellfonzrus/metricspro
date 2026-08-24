"""SSOT Phase 1 backfill — seed the unified alias tables from the vocabularies that already exist
(design blueprint Part 3b). PURE builders (no I/O) so idempotency + 1:1 entity coverage are provable
with a fake client and no DB (see backend/harness_backfill_idempotent.py); a thin I/O `seed()`
wrapper reads the sources and inserts WHERE-NOT-EXISTS.

WHAT IT SEEDS
─────────────
Stores — the `storeops.stores` row is the ENTITY ANCHOR (its migration-916 entity_id):
  • code  alias ← stores.store_code            • address alias ← stores.address
  • address / salesforce_id aliases ← commcalc.store_mapping matched by store_code
  • sales_file_spelling alias ← each commcalc.store_aliases row (entity whose code == alias.store_code)
  • merchant_id alias ← each storeops.store_merchant_id row
  • address alias ← each asset-router MARKET_OVERRIDES key that RESOLVES to a real entity
Employees — the `storeops.employees` row is the anchor (its migration-917 entity_id):
  • business_id / numeric_id / epay_login / pos_name / name_variant aliases from the row itself
  • name_variant / epay_login bridges from commcalc.name_map + commcalc.rep_aliases

STAGED, NEVER MERGED (the one human decision): carrier / twin codes for one physical address
(`B-1115`/`T-1115`, `957`/`LUX-NY-PENN`) are paired by IDENTICAL normalized store_address (the exact
1:1 join migration 511 proved) and written to storeops.store_alias_proposal for the owner to confirm —
they are NOT attached to any entity here.

IDEMPOTENT: every alias/proposal is emitted only when its (kind, lower(trim(value))) key is not
already present (the unique index), so a second seed run inserts zero rows. `1115 Liberty` (B-1115,
LI, no store_mapping row) still gets an entity_id + code/address aliases from the stores row alone.

💰 MOVES NO MONEY: only additive alias/proposal rows; no reader consumes them in Phase 1.
"""

from __future__ import annotations

from app.core.identity import _canon_person, _normalize_addr, _norm, _up

# The asset-router hand-patch dict, imported so its entries become aliases (blueprint: retire it).
try:
    from app.modules.asset.router import MARKET_OVERRIDES as _MARKET_OVERRIDES
except Exception:                                   # pragma: no cover - asset module optional
    _MARKET_OVERRIDES = {}

_TWIN_PREFIXES = ("B-", "T-", "LUX-")


def _akey(kind, value) -> tuple:
    return (str(kind or "").lower(), _norm(value).lower())


def build_store_alias_seed(store_rows, mapping_rows, store_alias_rows, merchant_rows,
                           existing_alias_keys=None, market_overrides=None) -> tuple:
    """PURE. Returns (alias_rows_to_insert, proposal_rows_to_insert).

    `store_rows`       — storeops.stores: entity_id / store_code / address
    `mapping_rows`     — commcalc.store_mapping: store_code / store_address / salesforce_id
    `store_alias_rows` — commcalc.store_aliases: alias / store_code
    `merchant_rows`    — storeops.store_merchant_id: store_code / merchant_id
    `existing_alias_keys` — set of (kind, lower(trim(value))) already in storeops.store_alias
    `market_overrides` — {address: market} (defaults to the live asset-router dict)
    """
    existing = set(existing_alias_keys or set())
    overrides = _MARKET_OVERRIDES if market_overrides is None else market_overrides

    code_to_entity: dict[str, str] = {}
    entity_addr_norm: dict[str, str] = {}          # entity_id -> normalized address (twin pairing)
    for r in (store_rows or []):
        eid = _norm(r.get("entity_id"))
        code = _norm(r.get("store_code"))
        if eid and code:
            code_to_entity.setdefault(_up(code), eid)
        if eid and _norm(r.get("address")):
            entity_addr_norm.setdefault(eid, _normalize_addr(r.get("address")))

    aliases: list[dict] = []
    seen = set(existing)                            # in-batch + already-present dedupe

    def _emit(kind, value, eid, source):
        v = _norm(value)
        if not v or not eid:
            return
        k = _akey(kind, v)
        if k in seen:
            return
        seen.add(k)
        aliases.append({"alias_kind": kind, "alias_value": v, "entity_id": eid,
                        "source": source, "confidence": "seeded"})

    # store rows — the anchor's own code + address
    for r in (store_rows or []):
        eid = _norm(r.get("entity_id"))
        _emit("code", r.get("store_code"), eid, "stores")
        _emit("address", r.get("address"), eid, "stores")
    # store_mapping — address + salesforce_id, by store_code
    for r in (mapping_rows or []):
        eid = code_to_entity.get(_up(r.get("store_code")))
        if not eid:
            continue
        _emit("address", r.get("store_address"), eid, "store_mapping")
        _emit("salesforce_id", r.get("salesforce_id"), eid, "store_mapping")
        if _norm(r.get("store_address")):
            entity_addr_norm.setdefault(eid, _normalize_addr(r.get("store_address")))
    # commcalc.store_aliases — sales-file spellings (validated: target code must be a real entity)
    for r in (store_alias_rows or []):
        eid = code_to_entity.get(_up(r.get("store_code")))
        if eid:
            _emit("sales_file_spelling", r.get("alias"), eid, "store_aliases")
    # store_merchant_id — merchant ids
    for r in (merchant_rows or []):
        eid = code_to_entity.get(_up(r.get("store_code")))
        if eid:
            _emit("merchant_id", r.get("merchant_id"), eid, "store_merchant_id")
    # MARKET_OVERRIDES — each address key that RESOLVES to a real entity by normalized address
    norm_to_entity: dict[str, set] = {}
    for eid, nk in entity_addr_norm.items():
        if nk:
            norm_to_entity.setdefault(nk, set()).add(eid)
    for addr in (overrides or {}):
        hits = norm_to_entity.get(_normalize_addr(addr)) or set()
        if len(hits) == 1:                          # unambiguous only — never a coin-flip
            _emit("address", addr, next(iter(hits)), "market_overrides")

    # ── TWIN PROPOSALS (staged, never merged): two DISTINCT entities sharing a normalized address ──
    proposals: list[dict] = []
    prop_seen = set()
    by_addr: dict[str, list[str]] = {}
    for eid, nk in entity_addr_norm.items():
        if nk:
            by_addr.setdefault(nk, []).append(eid)
    ent_code = {eid: _norm(r.get("store_code")) for r in (store_rows or [])
                for eid in [_norm(r.get("entity_id"))] if eid}
    for nk, eids in by_addr.items():
        eids = sorted(set(eids), key=lambda e: (_up(ent_code.get(e, "")), e))
        if len(eids) < 2:
            continue
        # primary = the code that does NOT look like a carrier/LUX twin (else the first, deterministically)
        primary = next((e for e in eids if not _up(ent_code.get(e, "")).startswith(_TWIN_PREFIXES)),
                       eids[0])
        shared = next((_normalize_addr(r.get("address")) and r.get("address")
                       for r in (store_rows or []) if _norm(r.get("entity_id")) == primary), nk)
        for e in eids:
            if e == primary:
                continue
            twin_code = ent_code.get(e, "")
            k = _akey("carrier_code", twin_code)
            if not twin_code or k in prop_seen:
                continue
            prop_seen.add(k)
            proposals.append({"proposal_kind": "carrier_twin", "alias_kind": "carrier_code",
                              "alias_value": twin_code, "entity_id": primary,
                              "primary_code": ent_code.get(primary, ""), "twin_code": twin_code,
                              "shared_address": shared, "source": "twin_pairing"})
    return aliases, proposals


def build_employee_alias_seed(employee_rows, name_map_rows, rep_alias_rows,
                              existing_alias_keys=None) -> list:
    """PURE. Returns alias_rows_to_insert for storeops.employee_alias.

    `employee_rows` — storeops.employees: entity_id / employee_id / id / name / epay_login /
                      epay_salesperson
    `name_map_rows` — commcalc.name_map: epay_login / epay_salesperson / storeops_name
    `rep_alias_rows`— commcalc.rep_aliases: alias / canonical

    Numeric-id vs business-id collisions use the existing guard (business_id_alias_map /
    _emp_id_variants): a numeric id that is some OTHER employee's business id is NOT seeded."""
    existing = set(existing_alias_keys or set())
    all_business = {_norm(e.get("employee_id")) for e in (employee_rows or []) if _norm(e.get("employee_id"))}
    name_canon_to_entity: dict[str, str] = {}
    for r in (employee_rows or []):
        eid = _norm(r.get("entity_id"))
        c = _canon_person(r.get("name"))
        if eid and c:
            name_canon_to_entity.setdefault(c, eid)

    aliases: list[dict] = []
    seen = set(existing)

    def _emit(kind, value, eid, source):
        v = _norm(value)
        if not v or not eid:
            return
        k = _akey(kind, v)
        if k in seen:
            return
        seen.add(k)
        aliases.append({"alias_kind": kind, "alias_value": v, "entity_id": eid,
                        "source": source, "confidence": "seeded"})

    for r in (employee_rows or []):
        eid = _norm(r.get("entity_id"))
        biz = _norm(r.get("employee_id"))
        _emit("business_id", biz, eid, "employees")
        numeric_s = str(r["id"]).strip() if r.get("id") is not None else ""
        if numeric_s and numeric_s != biz and numeric_s not in all_business:
            _emit("numeric_id", numeric_s, eid, "employees")
        _emit("epay_login", r.get("epay_login"), eid, "employees")
        _emit("pos_name", r.get("epay_salesperson"), eid, "employees")
        _emit("name_variant", r.get("name"), eid, "employees")
    for r in (name_map_rows or []):
        target = name_canon_to_entity.get(_canon_person(r.get("storeops_name")))
        if target:
            _emit("pos_name", r.get("epay_salesperson"), target, "name_map")
            _emit("epay_login", r.get("epay_login"), target, "name_map")
    for r in (rep_alias_rows or []):
        target = name_canon_to_entity.get(_canon_person(r.get("canonical")))
        if target:
            _emit("name_variant", r.get("alias"), target, "rep_aliases")
    return aliases


# ── I/O wrapper (best-effort, org-scoped, idempotent) ─────────────────────────────────────────────
def seed(client, org_id: str) -> dict:
    """Read the source vocabularies and INSERT any missing store/employee aliases + twin proposals.
    Idempotent (WHERE-NOT-EXISTS on the unique index). Best-effort; never raises."""
    out = {"store_aliases_inserted": 0, "employee_aliases_inserted": 0, "proposals_inserted": 0}
    try:
        store_rows = (client.schema("storeops").table("stores")
                      .select("entity_id,store_code,address").eq("org_id", org_id).execute().data) or []
        mapping_rows = (client.schema("commcalc").table("store_mapping")
                        .select("store_code,store_address,salesforce_id").eq("org_id", org_id)
                        .execute().data) or []
        store_alias_rows = _safe(client, "commcalc", "store_aliases", "alias,store_code", org_id)
        merchant_rows = _safe(client, "storeops", "store_merchant_id", "store_code,merchant_id", org_id)
        existing_sa = {_akey(r.get("alias_kind"), r.get("alias_value"))
                       for r in _safe(client, "storeops", "store_alias", "alias_kind,alias_value", org_id)}
        aliases, proposals = build_store_alias_seed(store_rows, mapping_rows, store_alias_rows,
                                                    merchant_rows, existing_alias_keys=existing_sa)
        out["store_aliases_inserted"] = _insert(client, "store_alias", org_id, aliases)

        existing_prop = {_akey(r.get("alias_kind"), r.get("alias_value")) for r in
                         _safe(client, "storeops", "store_alias_proposal", "alias_kind,alias_value", org_id)}
        proposals = [p for p in proposals if _akey(p["alias_kind"], p["alias_value"]) not in existing_prop]
        out["proposals_inserted"] = _insert(client, "store_alias_proposal", org_id, proposals)
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN identity_backfill store seed failed: {e}")
    try:
        emp_rows = (client.schema("storeops").table("employees")
                    .select("entity_id,employee_id,id,name,epay_login,epay_salesperson")
                    .eq("org_id", org_id).execute().data) or []
        name_map_rows = _safe(client, "commcalc", "name_map",
                              "epay_login,epay_salesperson,storeops_name", org_id)
        rep_alias_rows = _safe(client, "commcalc", "rep_aliases", "alias,canonical", org_id)
        existing_ea = {_akey(r.get("alias_kind"), r.get("alias_value")) for r in
                       _safe(client, "storeops", "employee_alias", "alias_kind,alias_value", org_id)}
        emp_aliases = build_employee_alias_seed(emp_rows, name_map_rows, rep_alias_rows,
                                                existing_alias_keys=existing_ea)
        out["employee_aliases_inserted"] = _insert(client, "employee_alias", org_id, emp_aliases)
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN identity_backfill employee seed failed: {e}")
    return out


def _safe(client, schema, table, cols, org_id) -> list:
    try:
        return (client.schema(schema).table(table).select(cols)
                .eq("org_id", org_id).execute().data) or []
    except Exception:                                              # pragma: no cover - I/O guard
        return []


def _insert(client, table, org_id, rows) -> int:
    if not rows:
        return 0
    payload = [dict(r, org_id=org_id) for r in rows]
    n = 0
    for i in range(0, len(payload), 500):
        client.schema("storeops").table(table).insert(payload[i:i + 500]).execute()
        n += len(payload[i:i + 500])
    return n
