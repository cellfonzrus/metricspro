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

STORE SYNONYMS MUST BIND TOO (2026-08-07)
─────────────────────────────────────────
A third vocabulary exists: `commcalc.store_aliases` — the explicit "this POS/sales-file spelling IS
this store" map that the Store-Matching UI writes and that every ATTRIBUTION path already honours.
The SPAN path did not, so a scoped manager silently lost every sales row whose store string was a
synonym rather than the canonical address. `widen_codes_to_keys()` now folds a store's synonyms into
its keyset — see the full argument (and the proof it cannot widen out of span) in that function.

A STORE HAS MORE THAN ONE ADDRESS SPELLING (2026-08-07)
──────────────────────────────────────────────────────
The two vocabularies do not just disagree about MARKETS — they disagree about ADDRESSES. The same
store_code can carry `storeops.stores.address = "4801 Armitage Chicago"` and
`commcalc.store_mapping.store_address = "4801 W Armitage Ave"`. `build_market_index` folds a store
into ONE row keeping the FIRST non-empty value per field, and `storeops.stores` is read first — so
the store_mapping spelling was silently DISCARDED from `stores[…]["address"]`, and therefore never
reached a span keyset. Sales rows carrying that spelling were invisible to the very manager who owns
the store. `by_market[…]["keys"]` already accumulated BOTH spellings; only the `stores` list was
lossy, and `widen_codes_to_keys` walks the `stores` list.

`alias_keys`-style sibling map `addr_keys` fixes it WITHOUT changing the shape of `stores` (which is
returned verbatim by `GET /core/markets` and feeds the roles/config grant picker). See
`widen_codes_to_keys` for the proof that this cannot widen to a store outside the span.

THE GRANT MODEL (owner rulings #5 / #6 / #7, 2026-08-08)
───────────────────────────────────────────────────────
Three rulings that are ONE change, because they are three faces of the same object — "what does a
person's grant actually name?".

  #5  "clean the bad vlaues and make it drop down with option to select many instead of free text"
      A grant value is a REFERENCE to an existing entity, so it is picked, never typed (RULE THREE)
      — and the highest-stakes possible place to break that rule, because the value is a PERMISSION.
      `resolve_store_grant()` / `resolve_market_grant()` are the canonical resolvers: they take any
      spelling a human or an old free-text box could have produced and return the ONE real store /
      market it names, or None. `None` is an answer, not a fallback — an unresolvable permission
      value is never silently kept as itself at a WRITE boundary.

  #6  "if it is slected then it is granted of not then separate them and let the managers assign it
      as required"
      A market on a manager's record IS a market grant. The defect is that the market half and the
      store half were fused into one undifferentiated set, so nobody — not the admin UI, not the
      operator, not this module — could say WHICH grant produced WHICH store. `login_grant_breakdown()`
      answers per-kind; `login_grant_codes()` still returns the identical union, so NOTHING is
      narrowed here. Narrowing a live person is the owner's call through the UI.

  #7  "they shoudl see their own store"
      `reporting_span_codes()` returns the EMPTY set for scope 'self', and an empty keyset means
      deny-all at ~54 `in_keyset()` call sites — a rep's own store included. `self_store_codes()`
      resolves the rep's OWN store instead. It is deliberately **opt-in** (`self_own_store=True`):
      flipping it on globally would hand every rep their store's PAYROLL, HOURS and colleagues'
      COMMISSION on the ~54 employee-keyed surfaces that share this primitive. `self_employee_ids()`
      is the paired guard — a self caller resolves to exactly ONE employee id, their own — and a
      surface that mixes store-level rows with per-employee pay MUST apply both.

MULTI-TENANT: every read here is `.eq("org_id", org_id)`. Nothing in this module writes.
"""

from __future__ import annotations

import re
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


_SQUASH_RE = re.compile(r"[^A-Z0-9]")


def _squash(v) -> str:
    """Comparison form for a GRANT VALUE: upper-cased, every non-alphanumeric character dropped.

    Deliberately narrow. It exists so `"B - 2612"` (a live house value) compares equal to the real
    store code `"B-2612"`, and `"ave u"` to `"Ave U"` — i.e. so PUNCTUATION AND CASE DRIFT in a
    hand-typed permission value cannot make it name a different store than the operator meant. It is
    NOT fuzzy matching: nothing here does prefix, edit-distance or token-overlap resolution, because
    a permission value must never be GUESSED at runtime. (The one-time cleanup of the historic
    free-text values used stronger, human-reviewed rules — see migration 740 — precisely because
    those decisions were reviewed by a person and are not safe as code.)

    Collision safety is a property of the tenant's roster, not of this function, so every caller
    that resolves through `_squash` treats a squash that lands on more than ONE physical store as
    UNRESOLVED (see `resolve_store_grant`)."""
    return _SQUASH_RE.sub("", str(v or "").upper())


def build_market_index(store_rows, mapping_rows, alias_rows=None) -> dict:
    """PURE: fold the two market vocabularies into one index. Kept separate from I/O so it is unit
    provable.

    `store_rows`   — storeops.stores rows: store_code / address / market
    `mapping_rows` — commcalc.store_mapping rows: store_code / store_address / market
    `alias_rows`   — commcalc.store_aliases rows: alias / store_code  (OPTIONAL; None/[] reproduces
                     the pre-2026-08-07 index byte-for-byte apart from an empty "alias_keys")

    Returns {"markets": [canonical names, sorted case-insensitively],
             "by_market": {market_lower: {"market": canonical,
                                          "codes": {UPPER store_code, …},
                                          "keys":  {UPPER store_code + UPPER address, …}}},
             "stores": [{"store_code", "address", "market"} …],
             "alias_keys": {UPPER store_code: {UPPER alias, …}},
             "addr_keys": {UPPER store_code: {UPPER address, …}}}

    Canonical casing = the most-common spelling seen (ties → alphabetically first), matching
    `storeops._collect_markets` exactly so the picker and the resolver can never disagree.

    ALIASES ARE NOT STORES. `alias_rows` is folded into `alias_keys` ONLY — deliberately never
    through `add()`. An alias must never invent a store, join a market, appear in `stores` (which
    feeds the `/core/markets` grant picker), or change a market's canonical spelling. It is a pure
    ROW-MATCHING synonym for a store_code that already exists, and nothing else.

    `stores` is UNCHANGED — still exactly one row per store with ONE display `address` (first
    non-empty wins), because it is returned verbatim by `GET /core/markets` to the roles/config
    grant picker and used as a display address by `storeops._dm_target_rows`. BOTH `alias_keys`
    and `addr_keys` are SIBLING maps used for matching only. Nothing that reads `stores`,
    `markets` or `by_market` changes shape or content."""
    variants: dict[str, Counter] = {}
    by_market: dict[str, dict] = {}
    stores: dict[str, dict] = {}   # UPPER store_code (or UPPER address when codeless) → row
    addr_keys: dict[str, set] = {}  # UPPER store_code → EVERY UPPER address spelling seen for it

    def add(code, address, market):
        code, address, market = _norm(code), _norm(address), _norm(market)
        # EVERY spelling, not just the one that wins the `stores` merge. Requires a real code:
        # the keyset is looked up BY CODE, so a codeless row can never be reached anyway.
        if code and address:
            addr_keys.setdefault(_up(code), set()).add(_up(address))
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

    # store_code -> its sales/POS synonyms. commcalc.store_aliases is UNIQUE on
    # (org_id, LOWER(TRIM(alias))), so one alias string can belong to AT MOST ONE store_code — two
    # stores can never both claim the same synonym, which is what makes it safe to fold a synonym
    # into a span (see widen_codes_to_keys).
    alias_keys: dict[str, set] = {}
    for r in (alias_rows or []):
        code, alias = _up(r.get("store_code")), _up(r.get("alias"))
        if code and alias:
            alias_keys.setdefault(code, set()).add(alias)

    for lk, counts in variants.items():
        by_market[lk]["market"] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    markets = sorted((b["market"] for b in by_market.values()), key=lambda s: s.lower())
    store_list = sorted(stores.values(), key=lambda s: (s.get("store_code") or s.get("address") or ""))

    # ── GRANT RESOLUTION SIBLINGS (2026-08-08, ruling #5) ───────────────────────────────────────
    # THREE new TOP-LEVEL keys. `markets`, `by_market`, `stores`, `alias_keys` and `addr_keys` are
    # untouched in shape and content — `GET /core/markets` returns only `markets` + `stores`, so the
    # grant picker's payload is byte-identical and every existing reader is unaffected.
    #
    #   roster_codes — UPPER codes that came from the OPERATIONAL roster (storeops.stores). This is
    #                  the vocabulary shifts / timelog / employees.home_store actually speak, so it
    #                  is the one a normalised grant should land in.
    #   key_index    — SQUASHED spelling (code | address | alias) -> {UPPER codes that own it}. The
    #                  single lookup behind resolve_store_grant.
    #   code_groups  — UPPER code -> {every UPPER code naming the SAME PHYSICAL STORE}. A tenant can
    #                  carry two code vocabularies for one store (live Luxelink: `Diversey` in
    #                  storeops.stores and `LUX-CHI-DIVERSEY` in commcalc.store_mapping, same
    #                  address) — 19 of that tenant's 39 mapping rows are such duplicates. Two codes
    #                  sharing an address are ONE store, and a resolver that treats them as two makes
    #                  a picker offer the same store twice (pick the wrong one and the grant binds
    #                  only half the data).
    roster_codes = {_up(r.get("store_code")) for r in (store_rows or []) if _norm(r.get("store_code"))}
    by_addr: dict[str, set] = {}
    for code, addrs in addr_keys.items():
        for a in addrs:
            sq = _squash(a)
            if sq:
                by_addr.setdefault(sq, set()).add(code)
    code_groups: dict[str, set] = {}
    for codes in by_addr.values():
        merged = set(codes)
        for c in codes:
            merged |= code_groups.get(c, set())
        for c in merged:
            code_groups[c] = merged
    for c in (_up(s.get("store_code")) for s in store_list if _norm(s.get("store_code"))):
        code_groups.setdefault(c, {c})
    key_index: dict[str, set] = {}
    for c in code_groups:
        key_index.setdefault(_squash(c), set()).add(c)
    for sq, codes in by_addr.items():
        key_index.setdefault(sq, set()).update(codes)
    for code, aliases in alias_keys.items():
        for a in aliases:
            sq = _squash(a)
            if sq:
                key_index.setdefault(sq, set()).add(code)
    return {"markets": markets, "by_market": by_market, "stores": store_list,
            "alias_keys": alias_keys, "addr_keys": addr_keys,
            "roster_codes": roster_codes, "key_index": key_index, "code_groups": code_groups}


def market_index(client, org_id: str, *, fresh: bool = False) -> dict:
    """The org's canonical market universe (see build_market_index). Each source is best-effort —
    a read failure on one vocabulary never blanks the other, and a missing table degrades to an
    empty side rather than a 500 (contract §5)."""
    now = time.time()
    if not fresh:
        hit = _market_cache.get(org_id)
        if hit and (now - hit[0]) < _MARKET_TTL_S:
            return hit[1]
    store_rows, mapping_rows, alias_rows = [], [], []
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
    try:
        # Store SYNONYMS (the Store-Matching UI's explicit map). Folded in here — same cache, same
        # TTL, same org key — rather than as a per-request scan, because widen_codes_to_keys() runs
        # on EVERY scoped request. A missing/unreadable table degrades to no aliases, which is
        # exactly the pre-2026-08-07 behaviour. Truncation at the limit can only DROP a synonym
        # (narrow), never invent one, so the failure direction is fail-safe.
        alias_rows = (client.schema("commcalc").table("store_aliases")
                      .select("alias,store_code").eq("org_id", org_id)
                      .limit(20000).execute().data) or []
    except Exception as e:                                          # pragma: no cover - I/O guard
        print(f"WARN core.scope market_index commcalc.store_aliases read failed: {e}")
    idx = build_market_index(store_rows, mapping_rows, alias_rows)
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
    into keys before matching.

    NOTE: this helper has no production caller today (the span path is `widen_codes_to_keys`) and is
    deliberately left SYNONYM-FREE — a market is a property of a store, not of a POS spelling. Any
    future caller that needs to match sales-file store strings must use `widen_codes_to_keys`."""
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


# ── GRANT RESOLUTION — a permission value names a REAL entity or it names nothing (ruling #5) ────
# `RESOLVED` / `AMBIGUOUS` / `UNKNOWN` / `EMPTY` are the four honest answers. Every caller must
# handle AMBIGUOUS and UNKNOWN explicitly; neither may be silently coerced into "keep what was
# typed", which is exactly how `3738 26th Street`, `3248 Lawarance`, `3560 Norstand Ave`,
# `B - 2612` and `Floating` became live permission values.
GRANT_EMPTY = "empty"
GRANT_RESOLVED = "resolved"
GRANT_AMBIGUOUS = "ambiguous"
GRANT_UNKNOWN = "unknown"


def resolve_store_grant(client, org_id: str, value) -> tuple:
    """Resolve ANY spelling of a store into the ONE real store it names.

    Returns `(code|None, status, detail)` where status is one of the GRANT_* constants and `code`
    is the tenant's OPERATIONAL store code (storeops.stores vocabulary preferred, because that is
    what shifts / timelog / employees.home_store speak).

    Accepts, in this order and NOTHING else:
      1. the store CODE itself (case- and punctuation-insensitive: `"b-418"`, `"B - 2612"`),
      2. a POS/sales SYNONYM from `commcalc.store_aliases` (the Store-Matching UI's own map),
      3. an ADDRESS spelling from EITHER vocabulary (`storeops.stores.address` or
         `commcalc.store_mapping.store_address`).

    There is NO prefix, no edit-distance and no token-overlap rule here, on purpose: this function
    decides who may read a store's numbers, so it may never GUESS. `"3738 26th Street"` returns
    (None, UNKNOWN, …) even though a human can see it was meant to be `3735 26th` — that judgement
    belongs to the owner in the UI, not to a matcher at runtime.

    AMBIGUOUS is returned when a spelling names more than one PHYSICAL store. Two codes that share
    an address are the same store (`code_groups`), so a tenant carrying two code vocabularies for
    one store resolves cleanly rather than being rejected."""
    v = _norm(value)
    if not v:
        return None, GRANT_EMPTY, ""
    idx = market_index(client, org_id)
    hits = (idx.get("key_index") or {}).get(_squash(v)) or set()
    if not hits:
        return None, GRANT_UNKNOWN, f"{v!r} is not a store code, synonym or address in this org"
    groups = idx.get("code_groups") or {}
    physical = set()
    for c in hits:
        physical |= (groups.get(c) or {c})
    for c in hits:
        if not (groups.get(c) or {c}) >= hits:
            return None, GRANT_AMBIGUOUS, f"{v!r} names more than one store: {sorted(hits)}"
    roster = idx.get("roster_codes") or set()
    display = {_up(s.get("store_code")): _norm(s.get("store_code"))
               for s in (idx.get("stores") or []) if _norm(s.get("store_code"))}
    preferred = sorted(physical & roster) or sorted(physical)
    code = display.get(preferred[0], preferred[0])
    return code, GRANT_RESOLVED, f"{v!r} -> {code!r}"


def resolve_market_grant(client, org_id: str, value) -> tuple:
    """Resolve a market grant against the canonical market union (the SAME vocabulary
    `market_store_codes` binds, so the picker can never offer what the resolver cannot bind).

    Returns `(canonical|None, status, detail)`. A market that exists but currently contains NO
    store still RESOLVES — it is a real market that happens to be empty, which is a roster problem,
    not a bad permission value. A spelling that is in neither vocabulary is UNKNOWN (live: the `15`
    fragment in one house user's `market = "15, NYC, LI"`)."""
    v = _norm(value)
    if not v:
        return None, GRANT_EMPTY, ""
    b = (market_index(client, org_id).get("by_market") or {}).get(v.lower())
    if not b:
        return None, GRANT_UNKNOWN, f"{v!r} is not a market in this org"
    return b["market"], GRANT_RESOLVED, f"{v!r} -> {b['market']!r}"


def normalize_grants(client, org_id: str, *, market=None, store_code=None, store_codes=None) -> dict:
    """Normalise a WRITE of a person's grants (the `/core/users/assign` boundary).

    Returns
      {"market": "NYC, LI" | None,        canonical spellings, order preserved, de-duplicated
       "store_code": "B-418" | None,      the PRIMARY pin (first of store_codes)
       "store_codes": ["B-418", …],
       "rejected": [{"kind": "store"|"market", "value": …, "status": …, "detail": …}, …]}

    RULE THREE at the SERVER, not just in the picker. The admin UI can only offer real stores and
    real markets, but the UI is not the boundary — a stale tab, a bulk sheet upload or a curl can
    still post anything, and the values it posts become permissions. Anything that does not resolve
    is REJECTED and reported back; nothing unresolvable is ever written.

    `market=None` / `store_codes=None` mean "not supplied, leave alone" and are passed straight
    through, so a caller that only edits one half never clears the other."""
    out = {"market": market, "store_code": store_code, "store_codes": store_codes, "rejected": []}
    if market is not None:
        canon, seen = [], set()
        for part in str(market or "").split(","):
            p = _norm(part)
            if not p:
                continue
            c, status, detail = resolve_market_grant(client, org_id, p)
            if c is None:
                out["rejected"].append({"kind": "market", "value": p, "status": status, "detail": detail})
                continue
            if c.lower() not in seen:
                seen.add(c.lower())
                canon.append(c)
        out["market"] = ", ".join(canon) if canon else None
    if store_codes is not None or store_code is not None:
        raw = list(store_codes) if store_codes is not None else []
        if store_code is not None and _norm(store_code) and not raw:
            raw = [store_code]
        canon, seen = [], set()
        for sc in raw:
            c, status, detail = resolve_store_grant(client, org_id, sc)
            if c is None:
                out["rejected"].append({"kind": "store", "value": _norm(sc), "status": status,
                                        "detail": detail})
                continue
            if c.upper() not in seen:
                seen.add(c.upper())
                canon.append(c)
        if store_codes is not None:
            out["store_codes"] = canon
        # The PRIMARY pin stays the first of the set, so "one store" and "several stores" are the
        # same shape and `store_code` can never disagree with `store_codes` again.
        out["store_code"] = canon[0] if canon else None
    return out


# ── REPORTING span ──────────────────────────────────────────────────────────────────────────────
GRANT_KIND_MARKET = "market"
GRANT_KIND_STORE = "store"
GRANT_KINDS = (GRANT_KIND_MARKET, GRANT_KIND_STORE)


def login_grant_breakdown(client, org_id: str, app_user) -> dict:
    """The SAME grants `login_grant_codes` returns, but ATTRIBUTED to the grant that produced them
    (owner ruling #6, 2026-08-08).

    Returns
      {"market":  {"granted": ["Chicago", …],       what is written on the record
                   "resolved": {"Chicago": {codes}} what each market actually binds
                   "unresolved": ["15"],            markets that bind NOTHING (grant a no-op)
                   "codes": {…}},                   the market half of the span
       "store":   {"granted": ["Diversey", …], "codes": {…},
                   "unresolved": ["Floating"]},     store values that name no real store
       "codes": {…}}                                the UNION — identical to login_grant_codes()

    WHY THIS EXISTS. Store scope and market scope were fused: one undifferentiated set came out and
    nothing could say which half produced which store. Live consequence — all 13 Luxelink
    `store_manager` logins (a scope-'store' role) also carry `market = Chicago` or `NY`, so each one
    silently spans their whole market. The owner's ruling is that the market IS a grant (it was
    selected, so it is granted) and that the two must be **separately assignable**, so that removing
    one does not disturb the other and an administrator can SEE which one is doing the widening.

    THIS FUNCTION NARROWS NOBODY. `codes` is the same union as before, byte for byte. Deciding that
    a particular manager should no longer hold their market is the owner's call, made in the UI."""
    # Built explicitly, NOT from a shared template: a shallow copy would alias the `granted` /
    # `unresolved` lists between the two kinds and re-weld the very halves this function separates.
    out = {
        GRANT_KIND_MARKET: {"granted": [], "resolved": {}, "unresolved": [], "codes": set()},
        GRANT_KIND_STORE: {"granted": [], "resolved": {}, "unresolved": [], "codes": set()},
        "codes": set(),
    }
    if not app_user:
        return out
    for mkt in _norm(app_user.get("market")).split(","):
        m = _norm(mkt)
        if not m:
            continue
        out[GRANT_KIND_MARKET]["granted"].append(m)
        codes = market_store_codes(client, org_id, m)
        out[GRANT_KIND_MARKET]["resolved"][m] = codes
        if codes:
            out[GRANT_KIND_MARKET]["codes"] |= codes
        else:
            out[GRANT_KIND_MARKET]["unresolved"].append(m)
    pinned = []
    if app_user.get("store_code"):
        pinned.append(_norm(app_user["store_code"]))
    for sc in (app_user.get("store_codes") or []):
        if _norm(sc) and _norm(sc) not in pinned:
            pinned.append(_norm(sc))
    for p in pinned:
        out[GRANT_KIND_STORE]["granted"].append(p)
        # The RAW value is always kept — this must not narrow a live span even by one row. The
        # RESOLVED code is added alongside it when the value names a real store in a non-canonical
        # spelling, which widens the keyset to that SAME store's other spellings (widen_codes_to_keys
        # is code-anchored, so a raw address matches only itself). Live: 34 grants on 33 of the 98
        # active logins hold an address/synonym rather than the code.
        out[GRANT_KIND_STORE]["codes"].add(p)
        code, status, _detail = resolve_store_grant(client, org_id, p)
        if code:
            out[GRANT_KIND_STORE]["codes"].add(code)
        elif status in (GRANT_UNKNOWN, GRANT_AMBIGUOUS):
            out[GRANT_KIND_STORE]["unresolved"].append(p)
    out["codes"] = {c for c in (out[GRANT_KIND_MARKET]["codes"] | out[GRANT_KIND_STORE]["codes"]) if c}
    return out


def login_grant_codes(client, org_id: str, app_user) -> set:
    """store_codes implied by an app_user's REPORTING grants: their market(s) + pinned store(s).
    Org-tree independent, so a market/store manager scopes correctly before the org units are wired.

    Drop-in replacement for `storeops._login_extra_codes` — same inputs, same output type, same
    comma-splitting of the `market` column — but the market half now resolves through the canonical
    union, and it costs ONE table read total instead of one per market.

    2026-08-08: delegates to `login_grant_breakdown` (the attributed form). The returned SET is the
    same union it always was, PLUS — for a pinned store written in a non-canonical spelling — that
    store's own canonical code. That addition can only ever name the SAME PHYSICAL STORE the grant
    already named (`resolve_store_grant` refuses anything ambiguous), and it is what makes a grant
    of `"4640 Diversey Chicago"` match rows keyed on `Diversey` / `4640-A W Diversey Ave` instead of
    only the one spelling that happened to be typed. It never removes a code."""
    return login_grant_breakdown(client, org_id, app_user)["codes"]


def reporting_span_codes(client, org_id: str, app_user, role_scope: str, org_unit_codes=None,
                         *, self_own_store: bool = False, employee_home_store=None) -> set:
    """The store_codes whose NUMBERS this login may see.

    `role_scope`      — roles.permissions.scope ('all' | 'market' | 'store' | 'self').
    `org_unit_codes`  — codes from the org-unit subtree(s) the caller manages (pass the result of the
                        `org_span_for_manager` RPC; kept as a parameter so this function stays I/O-
                        thin and unit-provable).
    `self_own_store`  — OPT-IN (default False = byte-identical to every previous caller). When True a
                        scope-'self' caller resolves to their OWN store instead of the empty set —
                        owner ruling #7, "they shoudl see their own store". See `self_store_codes`
                        for why this is opt-in per surface and never a global flip.
    `employee_home_store` — the caller's `storeops.employees.home_store`, when the call site already
                        has it; used only for the 'self' resolution.

    Mirrors `storeops.caller_scope`, including the rule that a 'self' rep gets no login-grant
    widening (their market grants are NEVER consulted). 'all' is handled by the CALLER (it means
    UNRESTRICTED / None, not "every code")."""
    span: set = set(org_unit_codes or [])
    if _norm(role_scope).lower() != "self":
        span |= login_grant_codes(client, org_id, app_user)
    elif self_own_store:
        span |= self_store_codes(client, org_id, app_user, employee_home_store=employee_home_store)
    return {c for c in span if c}


# ── SELF scope — "they shoudl see their own store" (owner ruling #7, 2026-08-08) ─────────────────
def self_store_codes(client, org_id: str, app_user, *, employee_home_store=None) -> set:
    """The store(s) a SELF-scoped person actually works at.

    Sources, all of them "where this person is", NONE of them "what area they cover":
      • `app_users.store_code`  — the primary pin,
      • `app_users.store_codes` — the rest of the pins (a floater legitimately covers several; the
        multi-select picker of ruling #5 exists for exactly this),
      • `employees.home_store`  — the roster's answer, so a rep whose login was created without a
        store pin still resolves (live: 8 of the 65 active reps across both tenants).
    Each value goes through `resolve_store_grant`, and BOTH the raw value and the resolved code are
    returned, so a legacy free-text spelling still matches its own rows.

    **MARKET GRANTS ARE NEVER CONSULTED HERE.** 53 of the 65 active reps carry a market on their
    record (a rep at one Chicago store carries `market = Chicago`, which binds 14 stores). Under
    ruling #6 that market is a real grant — but it is a grant for a scope that can USE a market, and
    "their own store" is not it. Reading the market here would turn one ruling into a 14× widening
    of another.

    WHY THIS IS OPT-IN AND NOT A GLOBAL FLIP. The empty 'self' keyset is currently load-bearing:
    ~54 `in_keyset()` call sites share it, and a good number of them are employee-keyed — payroll,
    hours, time-off, other people's commission. Making 'self' resolve globally would hand every rep
    their colleagues' pay, which is precisely what the ruling's guardrail forbids. A surface adopts
    this deliberately, and any surface that mixes store-level rows with per-employee pay must ALSO
    pin the employee dimension with `self_employee_ids()`."""
    codes: set = set()
    if not app_user and not _norm(employee_home_store):
        return codes
    raw = []
    if (app_user or {}).get("store_code"):
        raw.append(_norm(app_user["store_code"]))
    for sc in ((app_user or {}).get("store_codes") or []):
        if _norm(sc):
            raw.append(_norm(sc))
    if _norm(employee_home_store):
        raw.append(_norm(employee_home_store))
    for v in raw:
        codes.add(v)
        code, _status, _detail = resolve_store_grant(client, org_id, v)
        if code:
            codes.add(code)
    return {c for c in codes if c}


def self_scope_keyset(client, org_id: str, app_user, *, employee_home_store=None) -> set:
    """`self_store_codes` widened to matchable keys (codes + every address spelling + synonyms).

    Returns a SET — possibly EMPTY, never None. Empty means "this rep has no resolvable store", and
    it must stay a deny-all: `None` is the unrestricted sentinel and handing it back for a rep with
    a blank store pin would open the whole tenant. (`commcalc._caller_self_keyset` returns
    `(True, None)` in that case today — see the cross-module note in the handoff.)"""
    return widen_codes_to_keys(client, org_id,
                               self_store_codes(client, org_id, app_user,
                                                employee_home_store=employee_home_store))


def self_employee_ids(app_user) -> set:
    """The employee dimension for a SELF-scoped caller: EXACTLY their own employee_id.

    The guard that makes ruling #7 safe. "Sees their own store" means the store's OWN numbers — it
    does not mean the store's people. Any surface that widens a rep to store level and also carries
    a per-employee pay/commission/PII column filters that column with THIS set, so the rep sees
    their own row and nobody else's. Never derived from the keyset: deriving it would return every
    employee at the store, which is the payroll leak this exists to prevent. Empty set = deny-all
    (an unidentifiable caller gets nothing), never None."""
    eid = _norm((app_user or {}).get("employee_id"))
    return {eid} if eid else set()


def employee_home_store(client, org_id: str, employee_id) -> str:
    """`storeops.employees.home_store` for an employee_id, or "" — best-effort, never raises
    (contract §5: a missing table/column degrades, it does not 500 an unrelated page)."""
    eid = _norm(employee_id)
    if not eid:
        return ""
    try:
        rows = (client.schema("storeops").table("employees").select("home_store")
                .eq("org_id", org_id).eq("employee_id", eid).limit(1).execute().data) or []
        return _norm(rows[0].get("home_store")) if rows else ""
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN core.scope employee_home_store read failed: {e}")
        return ""


def widen_codes_to_keys(client, org_id: str, codes) -> set:
    """UPPER store_codes + their addresses + their SALES-FILE SYNONYMS, so a row whose store column
    holds ANY of the three forms matches. Same contract as `storeops.scope_keyset`'s widening step,
    but served off the cached index (no extra scan per request).

    THE SYNONYM HOLE (fixed 2026-08-07 — owner-reported, class-wide)
    ────────────────────────────────────────────────────────────────
    ~60 `in_keyset(...)` call sites across commcalc/closing/storeops/hr match a row's store STRING,
    and for sales-derived rows that string is whatever the POS/B2B export wrote — which is NOT
    always the store's canonical address. The house's B2B export writes "3 Palisade Ave Yonkers"
    where storeops/store_mapping/asset all say "3 Palisade Ave" (store_code B-3PL). The org already
    records exactly that fact in `commcalc.store_aliases` (the Store-Matching UI), and every
    ATTRIBUTION path already honours it (`_store_code_resolver`, `daily_sales_actuals`,
    `coa.store_resolver`). Only this SPAN path did not — so a DM whose span contains B-3PL had a
    keyset of {"B-3PL", "3 PALISADE AVE"} and every one of that store's sales rows was silently
    dropped from her report, while a super-admin (keyset None) saw them fine. Symptom: DM "Rana"
    could not see 3 Palisade on the Sales Report under market NYC; "2778 Ephraim Ave" (-> B-1598,
    PA) was loaded with the identical bug for any PA-scoped manager.

    SCOPING NOW FOLLOWS ATTRIBUTION: if the org says string S IS store C, and C is in the span,
    then S's rows are in the span. That is the invariant this restores.

    WHY THIS CANNOT WIDEN TO AN OUT-OF-SPAN STORE
    ─────────────────────────────────────────────
    * A synonym is admitted ONLY when its `store_code` is in `span_codes` — the caller's own code
      set, frozen BEFORE any widening, so a synonym can never be reached transitively through an
      address or through another synonym. No fixpoint, one pass, one hop.
    * `store_aliases` is UNIQUE on (org_id, LOWER(TRIM(alias))): a given synonym maps to at most
      ONE store_code, so admitting it can never simultaneously admit another store's synonym.
    * The read is `.eq("org_id", org_id)` on the middleware-rewritten org, so a synonym from
      another tenant is unreachable by construction.
    * `codes` is empty -> we return immediately, unchanged: this never turns an empty (deny-all)
      keyset into a non-empty one, and it never turns any keyset into None (unrestricted).
    * No aliases (or an unreadable table) -> `alias_keys` is empty -> byte-identical to before.
UPPER store_codes + **every** address spelling recorded for them, so a row whose store column
    holds ANY known form matches. Same contract as `storeops.scope_keyset`'s widening step, but
    served off the cached index (no extra scan per request).

    THE DISCARDED-SPELLING HOLE (fixed 2026-08-07)
    ──────────────────────────────────────────────
    `stores[…]["address"]` keeps only the FIRST non-empty address per code and `storeops.stores` is
    read before `commcalc.store_mapping`, so when the two diverge the store_mapping spelling was
    thrown away before the keyset was ever built. Live Luxelink: **18 of the 20 store_codes present
    in BOTH vocabularies diverge** (code `Armitage` → storeops "4801 Armitage Chicago" vs
    store_mapping "4801 W Armitage Ave"; `Cicero` → "2317 Cicero Cicero" vs "2317 S Cicero Ave STE
    A"; `QV` → "21880 Hempstead Ave" vs "218-80 Hempstead Avenue"). The POS/B2B sales rows carry the
    store_mapping spelling, so a scope-'market' Luxelink manager silently lost them — 680 of the 710
    rows (95.8%) on their live July-2026 Sales Report. Only `Utica` (both spellings identical) was
    unaffected. Super-admins never saw it because `ks is None` short-circuits the filter.

    This is the GENERAL case; `commcalc.store_aliases` (the separate `scope-alias-span` branch) is
    the SPECIAL case where the sales spelling is in NEITHER stores table (house's "3 Palisade Ave
    Yonkers"). Neither branch subsumes the other.

    WHY THIS CANNOT WIDEN TO AN OUT-OF-SPAN STORE
    ─────────────────────────────────────────────
    * An address is admitted ONLY under a `store_code` that is in `span_codes` — the caller's own
      code set, frozen BEFORE any widening — so it is one hop from a code the caller already holds.
      No transitive reach: an address never becomes a lookup key for another address.
    * `addr_keys` is built ONLY from rows that carry a non-empty `store_code`, and the address is
      filed under THAT row's own code. A spelling can therefore never migrate to a different store.
      (Two stores that genuinely share an address string both get it — that is the source data
      asserting they are the same address, and it is already true of `by_market[…]["keys"]`.)
    * Both source reads are `.eq("org_id", org_id)` on the middleware-rewritten org, so a spelling
      from another tenant is unreachable by construction.
    * `codes` empty → returns immediately, unchanged: a deny-all keyset never becomes non-empty, and
      the result is never `None` (`ks is None` alone still means unrestricted, so super-admin /
      scope-'all' / rbac-off never execute this at all).
    * Single-vocabulary tenant, or two vocabularies that AGREE → `addr_keys[code]` is exactly the
      one address the old code already added → byte-identical keyset.
    * The original `stores` loop below is left EXACTLY as it was, so the result is a provable
      SUPERSET of today's — this can only reveal rows that were wrongly hidden, never hide one."""
    keys = {_up(c) for c in (codes or []) if _norm(c)}
    if not keys:
        return keys
    span_codes = frozenset(keys)     # FROZEN pre-widening: address hops are anchored on CODES only
    idx = market_index(client, org_id)
    for s in (idx.get("stores") or []):
        sc = _up(s.get("store_code"))
        if sc and sc in keys:
            ad = _up(s.get("address"))
            if ad:
                keys.add(ad)
    for code, addrs in (idx.get("addr_keys") or {}).items():
        if code in span_codes:
            keys |= addrs
    for code, aliases in (idx.get("alias_keys") or {}).items():
        if code in span_codes:
            keys |= aliases
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
