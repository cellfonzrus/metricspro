"""Resolve a flag's STORE so it can reach a district manager — the write-side half of the flags
DM-review gate (owner directive 2026-08-07: "all flags need to be fed thru the dm, so yes route it
thru the dm and then visible to the scoped user"; option (a) chosen 2026-08-08).

THE PROBLEM THIS SOLVES
───────────────────────
`commcalc.flags` carried only `store_address` — whatever free-text spelling the producing report
happened to write. The span filter matches that string against the manager's keyset, so:

  * a spelling the org has not recorded ("5619 N Broad St" where store_mapping says
    "5619 N. Broad St.") matches nothing, and
  * a BLANK store_address — 27,428 of the house org's 31,033 rows, 88% — matches nothing at all.

Those rows reached NO district manager. Migration 285 adds a resolved `commcalc.flags.store_code`;
this module is what fills it in application code.

IT FOLLOWS THE SPAN KEYSET'S VOCABULARY, DELIBERATELY
────────────────────────────────────────────────────
`GET /commcalc/store-unmatched` resolves store strings with LEADING-NUMBER matching. The span keyset
(`app.core.scope.widen_codes_to_keys`) does a raw upper/trim compare over
{store_code} ∪ {store_mapping.store_address} ∪ {storeops.stores.address} ∪ {store_aliases.alias}.
The two disagree, and the value produced here exists ONLY to be matched by the keyset — so this
resolver mirrors the KEYSET, exactly, with no fuzzy step. A spelling the org has never recorded stays
UNRESOLVED (None). Mis-routing a flag to the wrong DM is strictly worse than leaving it in the
admin-visible unrouted queue (`GET /commcalc/flags-unrouted/{period}`).

It is the same chain `_store_maps()` builds and `249_commission_store_resolution.sql` documents
(store_aliases → store_code → store_mapping / storeops.stores), MINUS `coa.store_resolver`'s
leading-number step. Alias rows count only when their `store_code` is a REAL store, so a stale or
typo'd alias code can never hijack resolution.

THE MI FALLBACK (`mdn_store_code_map`)
──────────────────────────────────────
Port-out / transfer-out / involuntary-suspension flags take their store from a SALES match on the
customer's MDN, so any line sold in an earlier month lands with a blank store_address. The MI row
those flags are built from carries `salesforce_id`, and `commcalc.store_mapping` already maps
salesforce_id → store_code. That is not a new matcher: it is the dealer door that owns the line, read
out of the same config table the keyset uses. Validated against the rows where both answers exist —
1,067 of 1,122 agree (95.1%), and it independently reproduces all three of the house's
`store_aliases` rows without ever reading that table. The SALES answer stays authoritative; the MI
door only fills rows that had nothing.

SAP-CONFIGURABLE (contract §3): nothing here branches on a carrier, tenant or store name. A tenant
with no `salesforce_id` in `store_mapping` simply gets an empty map and the fallback is a no-op; a
tenant with no `store_aliases` behaves exactly as it did before.

MULTI-TENANT (contract §2): every read is `.eq("org_id", org_id)` on the caller-supplied org, and the
cache is keyed on org_id — never on the client object (`get_supabase()` is a process-wide singleton).

💰 MOVES NO MONEY. `store_code` is a new visibility-only column. Nothing here writes `store_address`,
an amount, a rate, a tier, a plan or a payout basis.
"""

from __future__ import annotations

import time

# Small config tables (tens of rows) but read on every calculation, so a short TTL cache keyed on
# org_id keeps it free. TTL is deliberately tiny so a newly-added store/alias is picked up in seconds.
_TTL_S = 30.0
_cache: dict[str, tuple[float, dict]] = {}


def _norm(v) -> str:
    return str(v or "").strip()


def _up(v) -> str:
    return _norm(v).upper()


def _digits_key(v) -> str:
    """An MDN as the flags/MI tables spell it: trimmed, with the pandas '.0' float artefact removed."""
    return _norm(v).replace(".0", "")


# ── PURE index build (no I/O → unit-provable, see backend/harness_flag_store_resolver.py) ─────────
def build_index(mapping_rows=None, store_rows=None, alias_rows=None) -> dict:
    """Fold the org's store vocabulary into one lookup.

    `mapping_rows` — commcalc.store_mapping: store_code / store_address / salesforce_id
    `store_rows`   — storeops.stores:        store_code / address
    `alias_rows`   — commcalc.store_aliases: alias / store_code

    Returns {"key_to_code": {UPPER key -> store_code},
             "sf_to_code":  {UPPER salesforce_id -> store_code},
             "codes":       {UPPER store_code}}

    Priority when the same key appears in several vocabularies (LOWEST number wins, byte-identical to
    `commcalc.flag_store_code_for`'s `order by pri, store_code limit 1`):

        1  the key already IS a store_code (store_mapping OR storeops.stores)
        2  an EXPLICIT store_aliases synonym
        3  commcalc.store_mapping.store_address
        4  storeops.stores.address

    Within one priority the alphabetically-first store_code wins, so the answer is deterministic and
    cannot depend on row order — exactly what the SQL function does.
    """
    mapping_rows = mapping_rows or []
    store_rows = store_rows or []
    alias_rows = alias_rows or []

    codes: set[str] = set()
    for r in mapping_rows:
        if _norm(r.get("store_code")):
            codes.add(_up(r.get("store_code")))
    for r in store_rows:
        if _norm(r.get("store_code")):
            codes.add(_up(r.get("store_code")))

    by_pri: dict[int, dict[str, set]] = {1: {}, 2: {}, 3: {}, 4: {}}

    def _add(pri: int, key: str, code: str) -> None:
        if key and code:
            by_pri[pri].setdefault(key, set()).add(code)

    for r in mapping_rows:                                   # pri 1 — key IS a code
        _add(1, _up(r.get("store_code")), _norm(r.get("store_code")))
    for r in store_rows:
        _add(1, _up(r.get("store_code")), _norm(r.get("store_code")))
    for r in alias_rows:                                     # pri 2 — explicit synonym, REAL code only
        code = _norm(r.get("store_code"))                    # (same validation `_store_maps()` applies,
        if code and _up(code) in codes:                      #  so a stale/typo alias cannot hijack)
            _add(2, _up(r.get("alias")), code)
    for r in mapping_rows:                                   # pri 3 — commission-side canonical address
        _add(3, _up(r.get("store_address")), _norm(r.get("store_code")))
    for r in store_rows:                                     # pri 4 — storeops roster address
        _add(4, _up(r.get("address")), _norm(r.get("store_code")))

    key_to_code: dict[str, str] = {}
    for pri in (4, 3, 2, 1):                                 # later (lower pri number) overwrites
        for key, cs in by_pri[pri].items():
            key_to_code[key] = sorted(cs)[0]

    # salesforce_id (the dealer door) → store_code. Ambiguity is REFUSED: a door mapped to two
    # different store_codes resolves to nothing rather than to a coin-flip.
    sf_multi: dict[str, set[str]] = {}
    for r in mapping_rows:
        code, sf = _norm(r.get("store_code")), _up(r.get("salesforce_id"))
        if code and sf:
            sf_multi.setdefault(sf, set()).add(code)
    sf_to_code = {sf: sorted(v)[0] for sf, v in sf_multi.items() if len(v) == 1}

    return {"key_to_code": key_to_code, "sf_to_code": sf_to_code, "codes": codes}


def resolve_code(index, *vals):
    """First of `vals` that the org's vocabulary recognises → its store_code. None otherwise.

    Mirrors `commcalc.flag_store_code_for` (mig 285). Never raises."""
    if not index:
        return None
    k2c = index.get("key_to_code") or {}
    for v in vals:
        k = _up(v)
        if k and k in k2c:
            return k2c[k]
    return None


def mdn_store_code_map(index, mi_rows) -> dict:
    """{MDN -> store_code} from MI rows via their `salesforce_id` (the dealer door that owns the line).

    Only UNAMBIGUOUS MDNs are returned: a number that appears at two different doors in the same MI
    pull is dropped rather than assigned to one of them. Empty dict when the tenant's store_mapping
    carries no salesforce_id — the fallback then costs nothing and changes nothing."""
    sf2c = (index or {}).get("sf_to_code") or {}
    if not sf2c:
        return {}
    multi: dict[str, set[str]] = {}
    for m in (mi_rows or []):
        mdn = _digits_key(m.get("phone_number"))
        code = sf2c.get(_up(m.get("salesforce_id")))
        if mdn and code:
            multi.setdefault(mdn, set()).add(code)
    return {mdn: next(iter(v)) for mdn, v in multi.items() if len(v) == 1}


def stamp(index, rows, mdn_to_code=None) -> dict:
    """Set `store_code` on every flag dict in `rows`, IN PLACE. Returns a small counts dict.

    * `store_code` is written on EVERY row (None when unresolvable) so a bulk PostgREST insert sees a
      uniform key set, and so the row reaches the admin-visible unrouted queue rather than vanishing.
    * A value the caller already set is never overwritten.
    * Resolution order: the flag's own store string, then — only for a row that has NO usable store
      string — the MI door via its MDN.
    * Never raises: a resolver failure degrades that row to None (unrouted), it does not break a
      recalculation (contract §5).
    """
    counts = {"total": 0, "by_store_string": 0, "by_mdn": 0, "unresolved": 0}
    for row in (rows or []):
        counts["total"] += 1
        try:
            if _norm(row.get("store_code")):
                continue
            code = resolve_code(index, row.get("store_address"))
            if code:
                row["store_code"] = code
                counts["by_store_string"] += 1
                continue
            if mdn_to_code:
                code = mdn_to_code.get(_digits_key(row.get("mdn")))
                if code:
                    row["store_code"] = code
                    counts["by_mdn"] += 1
                    continue
            row["store_code"] = None
            counts["unresolved"] += 1
        except Exception:                                        # pragma: no cover - defensive
            row["store_code"] = row.get("store_code") or None
            counts["unresolved"] += 1
    return counts


# ── I/O (best-effort, TTL-cached, org-scoped) ────────────────────────────────────────────────────
def store_index(client, org_id: str, *, fresh: bool = False) -> dict:
    """The org's store vocabulary (see build_index). Each source is best-effort — a read failure on
    one vocabulary never blanks the others, and a missing table degrades to an empty side rather than
    a 500 (contract §5)."""
    now = time.time()
    if not fresh:
        hit = _cache.get(org_id)
        if hit and (now - hit[0]) < _TTL_S:
            return hit[1]
    mapping_rows, store_rows, alias_rows = [], [], []
    try:
        mapping_rows = (client.schema("commcalc").table("store_mapping")
                        .select("store_code,store_address,salesforce_id")
                        .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN flag_store_resolver store_mapping read failed: {e}")
    try:
        store_rows = (client.schema("storeops").table("stores")
                      .select("store_code,address")
                      .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN flag_store_resolver storeops.stores read failed: {e}")
    try:
        alias_rows = (client.schema("commcalc").table("store_aliases")
                      .select("alias,store_code")
                      .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN flag_store_resolver store_aliases read failed: {e}")
    idx = build_index(mapping_rows, store_rows, alias_rows)
    _cache[org_id] = (now, idx)
    return idx


def invalidate(org_id: str = None) -> None:
    """Drop the cached vocabulary (call after a store/alias write so the next calc is current)."""
    if org_id is None:
        _cache.clear()
    else:
        _cache.pop(org_id, None)


def stamp_flags(client, org_id: str, rows, mi_rows=None) -> dict:
    """Convenience wrapper used by every commcalc flag writer: build (or reuse) the org's index, derive
    the MI door fallback when MI rows are at hand, and stamp `store_code` on `rows` in place."""
    try:
        idx = store_index(client, org_id)
        m2c = mdn_store_code_map(idx, mi_rows) if mi_rows else None
        return stamp(idx, rows, m2c)
    except Exception as e:                                       # pragma: no cover - defensive
        print(f"WARN flag_store_resolver stamp_flags failed (flags stay unrouted): {e}")
        for r in (rows or []):
            r.setdefault("store_code", None)
        return {"total": len(rows or []), "by_store_string": 0, "by_mdn": 0,
                "unresolved": len(rows or []), "error": str(e)}
