"""Canonical ACCESS-SCOPE primitives — REPORTING span vs SCHEDULING reach.

WHY THIS FILE EXISTS
────────────────────
Until now the platform had exactly ONE notion of "which stores does this person cover", resolved
by `app.modules.storeops.router.scope_keyset()` — a module-tree function that four other modules
(commcalc, hr, closing, storeops itself) import across the ownership boundary. That single set was
used for BOTH questions at once:

  Q1 (REPORTING)  "whose NUMBERS may this person see?"      → must be NARROW (their stores)
  Q2 (SCHEDULING) "whom may this person put on a shift?"    → must be WIDE  (employees move around)

Because one set answered both, an operator who wanted a District Manager to be able to schedule a
borrowed rep had to grant that DM **every store** — which silently also widened their REPORTING
access to every store. That is the exact complaint filed 2026-08-03:

    "the DM should be able to pick any employee while scheduling but should have only access to
     his stores for reporting — right now I have to give access to all stores because employees
     move around"

This module makes the two questions SEPARATELY answerable, and makes the reporting answer actually
bind (see MARKET GRANTS below). It is deliberately I/O-thin and side-effect free so it can be unit
proven without a database (see `backend/harness_core_scope.py`).

THE SPLIT (least-invasive shape — no migration, no re-granting)
───────────────────────────────────────────────────────────────
* **Existing grants stay exactly what they are: the REPORTING span.** `storeops.app_users.market`
  / `.store_code` / `.store_codes` + the org-unit subtree a manager owns. Nothing to re-enter.
* **Scheduling reach becomes an explicit, named, CONFIGURABLE role property** —
  `roles.permissions.scheduling_reach` ∈ {'org', 'span'} — instead of an unnamed side effect of
  how wide someone's store grant happens to be.
    - `'org'`  (DEFAULT) = the roster/employee-picker endpoints are span-EXEMPT: the role may pick
                any employee in the tenant when scheduling. This is *already* today's live
                behaviour (`GET /storeops/employees?all_company=true` is unconditionally org-wide
                and every scheduling surface calls it that way), so defaulting to 'org' keeps every
                existing tenant byte-identical. What changes is that it is now DECLARED and
                revocable rather than accidental.
    - `'span'` = lock the roster to the reporting span too (tenants that want the old coupling).
  RULE TWO compliance: a tenant-tunable knob in the role config, not a constant.
* A **reporting** read must NEVER consult `scheduling_reach`, and a **scheduling roster** read must
  never consult the reporting keyset. Call sites say which question they are asking by which
  helper they call — `reporting_span_codes()` vs `roster_span_exempt()`.

MARKET GRANTS MUST ACTUALLY BIND (the second half of the fix)
─────────────────────────────────────────────────────────────
Granting "3 markets" only constrains anything if a market resolves to its member stores. The old
resolver (`storeops._market_store_codes`) matched ONLY `storeops.stores.market`. But this codebase
carries TWO market vocabularies — `storeops.stores.market` and `commcalc.store_mapping.market` —
and they are known to diverge (a store created outside the StoreOps Admin editor lands in
`store_mapping` with a market and in `storeops.stores` with `market = NULL`, or is absent from one
side entirely). Consequence: a market that exists only in `store_mapping` resolved to the EMPTY
set, so the grant constrained nothing usable and the operator fell back to granting all stores.
`GET /storeops/markets` already unions both vocabularies for the *picker*, so the picker could
offer a market the *resolver* could not bind — the worst possible combination.

`market_index()` below is the ONE canonical union (same sourcing + case-insensitive canonicalisation
rules as `_collect_markets`), and it returns the member store CODES and ADDRESSES per market, so a
market grant resolves to a real, enforceable keyset.

EMPLOYEES MOVE AROUND (the reason the coupling hurt)
────────────────────────────────────────────────────
`reporting_employee_ids()` resolves "which employees are inside this reporting span" by home store
UNION *where they actually worked* (shifts / time logs at a store inside the span). Resolving by
home store alone is what made a borrowed rep invisible to the DM whose store they covered — the
other half of why the operator over-granted.

MULTI-TENANT: every read here is `.eq("org_id", org_id)`. Nothing in this module writes.
"""

from __future__ import annotations

import time
from collections import Counter

# ── Scheduling reach ────────────────────────────────────────────────────────────────────────────
REACH_ORG = "org"      # roster/picker spans the whole tenant (DEFAULT = today's live behaviour)
REACH_SPAN = "span"    # roster/picker is limited to the caller's reporting span
SCHEDULING_REACHES = (REACH_ORG, REACH_SPAN)
DEFAULT_SCHEDULING_REACH = REACH_ORG


def scheduling_reach(role_perms) -> str:
    """The role's SCHEDULING reach ('org' | 'span'). Unknown/absent/garbage → 'org', which is
    byte-identical to the behaviour every tenant has today. Never raises."""
    try:
        v = str((role_perms or {}).get("scheduling_reach") or "").strip().lower()
    except Exception:
        return DEFAULT_SCHEDULING_REACH
    return v if v in SCHEDULING_REACHES else DEFAULT_SCHEDULING_REACH


def roster_span_exempt(role_perms) -> bool:
    """True when a SCHEDULING roster / employee-picker read must ignore the reporting span.

    Call this — never `reporting_span_codes()` — from an employee-picker endpoint. It is the marker
    that says out loud "this read is a scheduling reach question, not a reporting question"."""
    return scheduling_reach(role_perms) == REACH_ORG


# ── Canonical market universe ───────────────────────────────────────────────────────────────────
# Small config tables (stores ~10²), but these are read on every scoped request, so a short TTL
# cache keyed on ORG_ID (never on the client object — `get_supabase()` is a process-wide singleton,
# see [[get-supabase-new-client-per-call]]) keeps the resolver cheap. TTL is deliberately tiny so a
# newly-created store/market shows up within seconds.
_MARKET_TTL_S = 30.0
_market_cache: dict[str, tuple[float, dict]] = {}


def _norm(v) -> str:
    return str(v or "").strip()


def _up(v) -> str:
    return _norm(v).upper()


def build_market_index(store_rows, mapping_rows) -> dict:
    """PURE: fold the two market vocabularies into one index. Kept separate from I/O so it is unit
    provable.

    `store_rows`   — storeops.stores rows: store_code / address / market
    `mapping_rows` — commcalc.store_mapping rows: store_code / store_address / market

    Returns {"markets": [canonical names, sorted case-insensitively],
             "by_market": {market_lower: {"market": canonical,
                                          "codes": {UPPER store_code, …},
                                          "keys":  {UPPER store_code + UPPER address, …}}},
             "stores": [{"store_code", "address", "market"} …]}

    Canonical casing = the most-common spelling seen (ties → alphabetically first), matching
    `storeops._collect_markets` exactly so the picker and the resolver can never disagree."""
    variants: dict[str, Counter] = {}
    by_market: dict[str, dict] = {}
    stores: dict[str, dict] = {}   # UPPER store_code (or UPPER address when codeless) → row

    def add(code, address, market):
        code, address, market = _norm(code), _norm(address), _norm(market)
        skey = _up(code) or _up(address)
        if skey:
            cur = stores.get(skey)
            if cur is None:
                stores[skey] = {"store_code": code, "address": address, "market": market or None}
            else:
                # First non-empty value wins per field, so a mapping row can fill a gap in the
                # storeops row (and vice-versa) without ever overwriting a real value.
                if not cur.get("store_code") and code:
                    cur["store_code"] = code
                if not cur.get("address") and address:
                    cur["address"] = address
                if not cur.get("market") and market:
                    cur["market"] = market
        if not market:
            return
        variants.setdefault(market.lower(), Counter())[market] += 1
        b = by_market.setdefault(market.lower(), {"market": market, "codes": set(), "keys": set()})
        if code:
            b["codes"].add(_up(code))
            b["keys"].add(_up(code))
        if address:
            b["keys"].add(_up(address))

    for r in (store_rows or []):
        add(r.get("store_code"), r.get("address"), r.get("market"))
    for r in (mapping_rows or []):
        add(r.get("store_code"), r.get("store_address") or r.get("address"), r.get("market"))

    for lk, counts in variants.items():
        by_market[lk]["market"] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    markets = sorted((b["market"] for b in by_market.values()), key=lambda s: s.lower())
    store_list = sorted(stores.values(), key=lambda s: (s.get("store_code") or s.get("address") or ""))
    return {"markets": markets, "by_market": by_market, "stores": store_list}


def market_index(client, org_id: str, *, fresh: bool = False) -> dict:
    """The org's canonical market universe (see build_market_index). Each source is best-effort —
    a read failure on one vocabulary never blanks the other, and a missing table degrades to an
    empty side rather than a 500 (contract §5)."""
    now = time.time()
    if not fresh:
        hit = _market_cache.get(org_id)
        if hit and (now - hit[0]) < _MARKET_TTL_S:
            return hit[1]
    store_rows, mapping_rows = [], []
    try:
        store_rows = (client.schema("storeops").table("stores")
                      .select("store_code,address,market").eq("org_id", org_id)
                      .limit(5000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.scope market_index storeops.stores read failed: {e}")
    try:
        mapping_rows = (client.schema("commcalc").table("store_mapping")
                        .select("store_code,store_address,market").eq("org_id", org_id)
                        .limit(5000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.scope market_index commcalc.store_mapping read failed: {e}")
    idx = build_market_index(store_rows, mapping_rows)
    _market_cache[org_id] = (now, idx)
    return idx


def canonical_markets(client, org_id: str) -> list:
    """Every market the org actually has, from BOTH vocabularies — the pick-don't-type option
    source (RULE THREE) that is guaranteed to match what `market_store_codes()` can bind."""
    return list(market_index(client, org_id).get("markets") or [])


def market_store_codes(client, org_id: str, market) -> set:
    """store_codes in a market (case-insensitive market match), from the canonical union.

    Replaces `storeops._market_store_codes`, which read only `storeops.stores.market` and therefore
    returned the EMPTY set for any market that lives (or is only spelled) in commcalc.store_mapping."""
    lk = _norm(market).lower()
    if not lk:
        return set()
    b = (market_index(client, org_id).get("by_market") or {}).get(lk)
    return set(b["codes"]) if b else set()


def market_store_keys(client, org_id: str, market) -> set:
    """Like `market_store_codes` but ALSO the store addresses — rows across this codebase key their
    store column on either a code or an address, which is exactly why `scope_keyset` widens codes
    into keys before matching."""
    lk = _norm(market).lower()
    if not lk:
        return set()
    b = (market_index(client, org_id).get("by_market") or {}).get(lk)
    return set(b["keys"]) if b else set()


def invalidate_market_index(org_id: str = None) -> None:
    """Drop the cached universe (call after a store/market write so the picker is instantly current)."""
    if org_id is None:
        _market_cache.clear()
    else:
        _market_cache.pop(org_id, None)


# ── REPORTING span ──────────────────────────────────────────────────────────────────────────────
def login_grant_codes(client, org_id: str, app_user) -> set:
    """store_codes implied by an app_user's REPORTING grants: their market(s) + pinned store(s).
    Org-tree independent, so a market/store manager scopes correctly before the org units are wired.

    Drop-in replacement for `storeops._login_extra_codes` — same inputs, same output type, same
    comma-splitting of the `market` column — but the market half now resolves through the canonical
    union, and it costs ONE table read total instead of one per market."""
    codes: set = set()
    if not app_user:
        return codes
    for mkt in _norm(app_user.get("market")).split(","):
        codes |= market_store_codes(client, org_id, mkt)
    if app_user.get("store_code"):
        codes.add(_norm(app_user["store_code"]))
    for sc in (app_user.get("store_codes") or []):
        if _norm(sc):
            codes.add(_norm(sc))
    return {c for c in codes if c}


def reporting_span_codes(client, org_id: str, app_user, role_scope: str, org_unit_codes=None) -> set:
    """The store_codes whose NUMBERS this login may see.

    `role_scope`     — roles.permissions.scope ('all' | 'market' | 'store' | 'self').
    `org_unit_codes` — codes from the org-unit subtree(s) the caller manages (pass the result of the
                       `org_span_for_manager` RPC; kept as a parameter so this function stays I/O-
                       thin and unit-provable).

    Mirrors `storeops.caller_scope` exactly, including the rule that a 'self' rep gets no login-grant
    widening (reps are pinned to their own store by the frontend). 'all' is handled by the CALLER
    (it means UNRESTRICTED / None, not "every code")."""
    span: set = set(org_unit_codes or [])
    if _norm(role_scope).lower() != "self":
        span |= login_grant_codes(client, org_id, app_user)
    return {c for c in span if c}


def widen_codes_to_keys(client, org_id: str, codes) -> set:
    """UPPER store_codes + their addresses, so a row whose store column holds EITHER form matches.
    Same contract as `storeops.scope_keyset`'s widening step, but served off the cached index
    (no extra `stores` scan per request)."""
    keys = {_up(c) for c in (codes or []) if _norm(c)}
    if not keys:
        return keys
    for s in (market_index(client, org_id).get("stores") or []):
        sc = _up(s.get("store_code"))
        if sc and sc in keys:
            ad = _up(s.get("address"))
            if ad:
                keys.add(ad)
    return keys


def in_keyset(keyset, *vals) -> bool:
    """True when unrestricted (keyset None) or any of vals matches an allowed store key.
    Byte-identical to `storeops.in_keyset` — re-exported here so a module can depend on core alone."""
    if keyset is None:
        return True
    return any(_up(v) in keyset for v in vals)


def reporting_employee_ids(client, org_id: str, keyset, *, since=None, until=None) -> set:
    """employee_ids visible inside a REPORTING keyset — by HOME STORE **union WHERE THEY ACTUALLY
    WORKED** (a shift or a time-log at a store inside the span).

    `keyset` None ⇒ returns None (unrestricted).

    WHY the union: resolving by home_store alone hides a borrowed rep from the manager of the store
    they actually covered. "Employees move around" was cited by the owner as the reason they had to
    grant a DM every store; a home-store-only rule makes the narrow grant unusable and pushes the
    operator straight back to over-granting. The worked-at half is bounded by the caller's date
    window so this never turns into a full-history scan.

    Each source is best-effort: a missing table/column degrades that half to empty, never a 500."""
    if keyset is None:
        return None
    ids: set = set()
    try:
        for e in (client.schema("storeops").table("employees")
                  .select("employee_id,home_store").eq("org_id", org_id)
                  .limit(20000).execute().data) or []:
            if e.get("employee_id") and in_keyset(keyset, e.get("home_store")):
                ids.add(str(e["employee_id"]))
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.scope reporting_employee_ids employees read failed: {e}")
    for table, store_col, date_col in (("shifts", "store_code", "shift_date"),
                                       ("timelog", "store_code", "work_date")):
        try:
            q = (client.schema("storeops").table(table)
                 .select(f"employee_id,{store_col},{date_col}").eq("org_id", org_id))
            if table == "shifts":
                q = q.eq("is_deleted", False)   # a deleted shift never widens a span
            if since:
                q = q.gte(date_col, since)
            if until:
                q = q.lte(date_col, until)
            for r in (q.limit(50000).execute().data or []):
                if r.get("employee_id") and in_keyset(keyset, r.get(store_col)):
                    ids.add(str(r["employee_id"]))
        except Exception as e:                                      # pragma: no cover - I/O guard
            print(f"WARN core.scope reporting_employee_ids {table} read failed: {e}")
    return ids
