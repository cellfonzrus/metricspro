"""Store/market filtered statement VIEWS (RULE FIVE §3d, finance slice).

The P&L / Balance Sheet are AGGREGATED statements persisted as per-scope snapshots in
`commcalc.account_statements` (consolidated / company:<id> / store:<address>). The standard filter
bar adds a store(s)-multi + market filter; applying it must re-attribute the underlying LINE totals
to the selected stores, not merely hide rows.

This module builds that filtered view WITHOUT recomputing the money engine: it SUMS the already-
computed per-store snapshots for the selected stores. That sum is, by construction, byte-equivalent
to what `engine._assemble(inputs, ..., stores_in_scope=S, include_company_wide=False)` would produce,
because every per-store snapshot is exactly `_assemble(..., stores_in_scope={s}, include_cw=False)`
and every quantity on a statement (line amounts, subtotals, gross profit, net income, asset/liability/
equity totals, imbalance) is a LINEAR function of the per-store line amounts. Summing a subset of the
store snapshots therefore yields the statement attributable to exactly those stores.

COMPANY-WIDE lines carry no store, so they are absent from every per-store snapshot's store
attribution — in the filtered view they read $0. That is the convention for a line whose source
genuinely names no store: "booked company-wide, not to a store, and read $0 under a store/market
filter" (mirrors the per-scope note in engine._notes).

MIG 1033 (owner 2026-10-01) — "MI/ATU residual" used to head that list, and this docstring recorded a
DEFECT as a convention. `raw_mi` names the dealer door on every row (`salesforce_id`); `coa` did not
read it, so the whole Boost residual sat in `company_wide` and every store/market/company view read
$0 residual from the first statement onward. Nothing in THIS module was wrong — it is linear over
whatever per-store amounts the snapshots carry, so the residual appears here automatically once `coa`
attributes it. What belongs on this list is only a line whose FEED states no store.

NOTHING here changes a booking rule, a rate, or an existing computed number. With NO filter active the
read endpoint never calls this module and returns the stored snapshot byte-for-byte (see router.get_pl/
get_bs). This is a read-only, deterministic, org-scoped display aggregation.
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.account import _period as _per
from app.modules.account import analysis as _analysis   # 2026-10-06 — THE "freshest wins" dedupe (§19.50)


def _r(x):
    return round(safe_float(x), 2)


# ── THE store-spelling vocabulary, dereferenced (never copied) ──────────────────────────────────
# Both the MARKET expansion and the EXPLICIT-STORE expansion below need the same three facts: which
# key spellings a store code owns, which store a spelling names, and which leading street numbers
# are unambiguous. Each fact has ONE home here and both callers READ it, so the two halves of the
# filter can never disagree about what counts as the same store.

def _keys_for_codes(idx, codes):
    """PURE: every UPPER key spelling the org's vocabulary knows for these store codes — the code
    itself, every address spelling (`addr_keys`) and every POS synonym (`alias_keys`)."""
    idx = idx or {}
    addr_keys = idx.get("addr_keys") or {}
    alias_keys = idx.get("alias_keys") or {}
    out = set()
    for code in codes or ():
        c = str(code).upper()
        if not c:
            continue
        out.add(c)
        out |= {str(a).upper() for a in (addr_keys.get(c) or ())}
        out |= {str(a).upper() for a in (alias_keys.get(c) or ())}
    out.discard("")
    return out


def _num_owner_index(idx):
    """PURE: leading street number -> {owning identity}. A number claimed by two identities is
    ambiguous and must never match (fail-closed), exactly as `store_resolver` documents."""
    from app.modules.account.coa import _lead_num_key
    idx = idx or {}
    num_owner = {}
    for code, addrs in (idx.get("addr_keys") or {}).items():
        for a in addrs:
            nk = _lead_num_key(a)
            if nk:
                num_owner.setdefault(nk, set()).add(str(code).upper())
    for s in (idx.get("stores") or ()):
        a = str((s or {}).get("address") or "")
        nk = _lead_num_key(a)
        if nk:
            ident = str((s or {}).get("store_code") or "").upper() or a.upper()
            num_owner.setdefault(nk, set()).add(ident)
    return num_owner


def _codes_for_selection(idx, value):
    """PURE: the UPPER store code(s) an arbitrary selected spelling names — exact code, then any
    squashed key spelling (`key_index`: code | address | alias), then an UNAMBIGUOUS leading street
    number. Widened through `code_groups` so a store carried under two code vocabularies resolves to
    both. Empty set when nothing binds (fail-closed — a spelling is never guessed into a store)."""
    from app.modules.account.coa import _squash_key, _lead_num_key
    idx = idx or {}
    key_index = idx.get("key_index") or {}
    code_groups = idx.get("code_groups") or {}
    addr_keys = idx.get("addr_keys") or {}
    v = str(value or "").strip()
    if not v:
        return set()
    found = set()
    up = v.upper()
    if up in addr_keys or up in (idx.get("alias_keys") or {}):
        found.add(up)
    if not found:
        found |= {str(c).upper() for c in (key_index.get(_squash_key(v)) or ())}
    if not found:
        nk = _lead_num_key(v)
        if nk:
            owners = (_num_owner_index(idx) or {}).get(nk) or set()
            if len(owners) == 1:
                found |= {o for o in owners if o in addr_keys}
    widened = set(found)
    for c in found:
        widened |= {str(g).upper() for g in (code_groups.get(c) or ())}
    return widened


def unbound_spellings(idx, spellings):
    """PURE: the given store spellings that the org vocabulary cannot bind to any store, in input
    order, deduped case-insensitively.

    A PICKER needs this. `core.scope.build_store_options(idx, present=…)` offers one option per
    physical store PLUS any `present` spelling it cannot bind — but it tests bindability with its own
    `_squash`, which is not the matcher's `coa._squash_key` (that one folds street-suffix drift, so
    "5135 Bergenline Ave" binds "5135 Bergenline Avenue" while `_squash` sees two strings). Handing
    `build_store_options` a raw feed vocabulary therefore re-offers stores it already lists — the
    §13e "one store offered twice" defect, arriving through the back door.

    So a surface whose rows carry a FEED's own spellings asks THIS function (the matcher's own
    vocabulary) which of them nothing binds, and passes only those as `present`. One fact — "does
    this spelling name a store?" — answered by the one home that also decides what a filter MATCHES,
    so the picker can never offer a spelling the filter resolves differently.
    """
    out, seen = [], set()
    for raw in (spellings or ()):
        v = str(raw or "").strip()
        if not v or v.casefold() in seen:
            continue
        seen.add(v.casefold())
        if not _codes_for_selection(idx, v):
            out.append(v)
    return out


def store_key_expansion(idx, stores):
    """PURE (harness: harness_pl_filter_semantics.py): expand an EXPLICIT store selection to every
    matchable SNAPSHOT key spelling, from the same canonical union index the market expansion reads.

    OWNER BUG 2026-10-03 — *"device cost is not being added to the p&l of the following stores 5619
    6149 6507 1710"*. The P&L store picker is `/core/filter-options`, which offers
    `storeops.stores.address` when the row has one and the STORE CODE when it does not; 26 of the
    house org's 29 storeops rows carry no address, so the picker offered `B-5619` while the snapshot
    is keyed `store:5619 N. Broad St.`. The explicit half of the matcher compared EXACT spellings
    only, so the selection bound ZERO store snapshots and `aggregate` rendered the consolidated
    SKELETON at $0.00 — every line, device cost among them, read as a measured zero.

    This is the SAME defect class as the 2026-09-02 market bug documented in `market_key_expansion`
    (a picker offering a spelling the resolver cannot bind) and it gets the same cure rather than a
    second one: the selection is resolved through the ONE canonical vocabulary, so any spelling the
    picker can offer — code, address variant, POS alias, or a bare street number — binds the store's
    snapshot. Fail-closed: a spelling that names no store contributes only itself, and an ambiguous
    street number never matches.

    Returns (upper_keys, squashed_keys, member_nums) — the same triple `market_key_expansion`
    returns, so the two expansions simply union."""
    from app.modules.account.coa import _squash_key
    idx = idx or {}
    addr_keys = idx.get("addr_keys") or {}
    picked = {str(s or "").strip() for s in (stores or ()) if str(s or "").strip()}
    upper_keys = {p.upper() for p in picked}        # the selection itself always matches (unchanged)
    codes = set()
    for p in picked:
        codes |= _codes_for_selection(idx, p)
    upper_keys |= _keys_for_codes(idx, codes)
    squashed_keys = {_squash_key(k) for k in upper_keys}
    squashed_keys.discard("")
    num_owner = _num_owner_index(idx)
    member_nums = {n for n, owners in num_owner.items()
                   if len(owners) == 1 and next(iter(owners)) in (codes | upper_keys)}
    return upper_keys, squashed_keys, member_nums


def market_key_expansion(idx, markets):
    """PURE (harness: harness_pl_filter_semantics.py): expand a market selection to every matchable
    STORE KEY spelling, from the canonical UNION market index (core.scope.build_market_index shape).

    OWNER BUG 2026-09-02 ("when you filter the market from the p&l it does not show any data"): the
    old resolver read commcalc.store_mapping ALONE with a CASE-SENSITIVE market equality, while the
    market picker (core /filter-options) offers the UNION of storeops.stores.market ∪
    store_mapping.market — so a market that lives (or is only spelled/cased) on the storeops side
    bound ZERO stores and the P&L rendered the all-$0 skeleton. Same defect class as the documented
    /core/markets 'PA' bug; same cure: the ONE canonical union (core.scope.market_index), so the
    picker can never offer a market this resolver cannot bind. A store's snapshot key is a SALES
    spelling, so each member store expands to EVERY spelling either vocabulary knows for it
    (by_market keys + addr_keys per member code).

    Returns (upper_keys, squashed_keys, member_nums):
      upper_keys    — UPPER codes + every UPPER address spelling of the member stores;
      squashed_keys — the same, squashed (alphanumeric-only) for punctuation/whitespace drift;
      member_nums   — leading street numbers that UNAMBIGUOUSLY (index-wide) identify a member
                      store, mirroring store_resolver's documented precedence. A number claimed by
                      two different stores never matches (fail-closed).
    Market names match case-insensitively; an unknown market contributes nothing (fail-closed)."""
    from app.modules.account.coa import _squash_key
    idx = idx or {}
    by_market = idx.get("by_market") or {}
    want = {str(m or "").strip().lower() for m in (markets or []) if str(m or "").strip()}
    upper_keys, member_codes = set(), set()
    for mk in want:
        b = by_market.get(mk)
        if not b:
            continue
        upper_keys |= {str(k).upper() for k in (b.get("keys") or ())}
        member_codes |= {str(c).upper() for c in (b.get("codes") or ())}
    upper_keys |= _keys_for_codes(idx, member_codes)
    squashed_keys = {_squash_key(k) for k in upper_keys}
    squashed_keys.discard("")
    # index-wide street-number ambiguity, from the ONE shared index (number → owning identity)
    num_owner = _num_owner_index(idx)
    member_idents = member_codes | upper_keys
    member_nums = {n for n, owners in num_owner.items()
                   if len(owners) == 1 and next(iter(owners)) in member_idents}
    return upper_keys, squashed_keys, member_nums


def build_store_matcher(explicit_stores, upper_keys, squashed_keys, member_nums):
    """PURE: fn(snapshot store address) -> bool for the combined store+market selection.
    The explicit selection still matches case-insensitively as-is (byte-identical to the old
    behaviour); everything the two expansions resolved — the selected stores' other spellings and
    the selected markets' members — matches by any known spelling (exact upper → squashed →
    unambiguous leading street number)."""
    from app.modules.account.coa import _squash_key, _lead_num_key
    explicit_lower = {str(s).strip().lower() for s in (explicit_stores or ()) if str(s).strip()}
    explicit_upper = {s.upper() for s in explicit_lower}

    def match(addr):
        a = str(addr or "").strip()
        if not a:
            return False
        if a.lower() in explicit_lower or a.upper() in explicit_upper:
            return True
        if a.upper() in upper_keys:
            return True
        if _squash_key(a) in squashed_keys:
            return True
        nk = _lead_num_key(a)
        return bool(nk and nk in member_nums)

    return match


def _org_store_vocabulary(client, org_id):
    """The org's canonical union store vocabulary (core.scope.market_index: storeops.stores ∪
    commcalc.store_mapping ∪ store_aliases) — ONE read, dereferenced by both expansions. A read
    failure degrades to a best-effort store_mapping-only index (the pre-2026-09 authority) shaped
    the same way, never to silently-all. Market-less mapping rows are carried too, because a STORE
    selection must resolve whether or not its row names a market."""
    try:
        from app.core import scope as core_scope
        idx = core_scope.market_index(client, org_id)
        if idx:
            return idx
    except Exception:
        pass
    try:
        from app.modules.account import coa
        rows = coa._fetch_all(client, "store_mapping", "store_code,store_address,market",
                              {"org_id": org_id})
        idx = {"by_market": {}, "addr_keys": {}, "alias_keys": {}, "key_index": {},
               "code_groups": {}, "stores": []}
        for r in rows:
            mk = (r.get("market") or "").strip().lower()
            sa = (r.get("store_address") or "").strip()
            code = (r.get("store_code") or "").strip().upper()
            if not sa and not code:
                continue
            idx["stores"].append({"store_code": code, "address": sa, "market": mk or None})
            if code and sa:
                idx["addr_keys"].setdefault(code, set()).add(sa.upper())
                idx["key_index"].setdefault(coa._squash_key(sa), set()).add(code)
                idx["key_index"].setdefault(coa._squash_key(code), set()).add(code)
            if not mk or not sa:
                continue
            b = idx["by_market"].setdefault(mk, {"codes": set(), "keys": set()})
            b["keys"].add(sa.upper())
            if code:
                b["codes"].add(code)
        return idx
    except Exception:
        return {}


def resolve_store_matcher(client, org_id, stores_csv="", markets_csv=""):
    """Resolve the active store/market selection to a snapshot-address matcher.

      • explicit stores → resolved through the org's canonical union vocabulary, so ANY spelling the
        picker can offer binds the store's snapshot: the exact selection, its store CODE, every
        address variant, every POS alias, and an unambiguous leading street number — see
        `store_key_expansion` (owner bug 2026-10-03).
      • markets → resolved through the SAME index, so any market the picker offers binds — see
        `market_key_expansion` (owner bug 2026-09-02).

    Both halves read one index and union their key triples, so the filter cannot treat a spelling as
    one store for a market selection and a different store for an explicit one.

    Values are PIPE-separated ('|'), not comma — a canonical store_address may itself contain a comma
    ("123 Main St, Queens NY"). Returns (matcher, explicit_stores:set, markets:list)."""
    stores = {s.strip() for s in (stores_csv or "").split("|") if s.strip()}
    markets = [m.strip() for m in (markets_csv or "").split("|") if m.strip()]
    upper_keys, squashed_keys, member_nums = set(), set(), set()
    if stores or markets:
        idx = _org_store_vocabulary(client, org_id)
        if markets:
            u, sq, nums = market_key_expansion(idx, markets)
            upper_keys |= u; squashed_keys |= sq; member_nums |= nums
        if stores:
            u, sq, nums = store_key_expansion(idx, stores)
            upper_keys |= u; squashed_keys |= sq; member_nums |= nums
    return build_store_matcher(stores, upper_keys, squashed_keys, member_nums), stores, markets


def scope_predicate(client, org_id, scope):
    """fn(snapshot store address) -> bool for the company/store SCOPE selector, composed (AND) with
    the store/market filter so the company dropdown still narrows an active filter.

      consolidated / blank / unknown → always True (no narrowing — unchanged).
      store:<addr>  → that store only (case-insensitive).
      company:<id>  → the store's attributed company == <id>, via the SAME canonical attribution
        the compute engine books snapshots with (coa.company_assignment: exact → squashed →
        unambiguous street number → DEFAULT company). The old implementation intersected against
        ONLY explicitly-assigned addresses, so stores held by a company through the DEFAULT rule —
        or assigned under a variant spelling — dropped to an empty view. Fail-CLOSED: a resolution
        failure on a company scope matches nothing (never another company's stores)."""
    scope = (scope or "consolidated").strip()
    if not scope or scope == "consolidated":
        return lambda addr: True
    if scope.startswith("store:"):
        target = scope.split(":", 1)[1].strip().lower()
        return lambda addr: str(addr or "").strip().lower() == target
    if scope.startswith("company:"):
        cid = scope.split(":", 1)[1]
        try:
            from app.modules.account import coa
            company_of, _default, _companies = coa.company_assignment(client, org_id)
            return lambda addr: company_of(addr) == cid
        except Exception:
            return lambda addr: False
    if scope.startswith("profit_center:"):
        # mig 1022 (index §37.1): the stores mapped to the profit center and every center under it, resolved
        # through the SAME store resolver the P&L books under (centers.profit_center_stores). Fail-CLOSED: a
        # resolution failure or an unknown center matches nothing — never another center's stores.
        code = scope.split(":", 1)[1]
        try:
            from app.modules.account import coa, centers as _c
            cs = _c.load_centers(client, org_id)
            if not any(c["center_type"] == "profit" and c["code"] == code for c in cs):
                return lambda addr: False
            idx, _dupes = _c.store_map_index(_c.load_store_map(client, org_id), coa.store_resolver(client, org_id))
            members = {s.lower() for s in _c.profit_center_stores(code, cs, idx)}
            return lambda addr: str(addr or "").strip().lower() in members
        except Exception:
            return lambda addr: False
    return lambda addr: True


def aggregate(payloads, statement_type, structure=None):
    """Sum a list of per-store snapshot payloads into one filtered statement payload.

    `structure` (optional) — a payload (e.g. the consolidated snapshot) used ONLY to seed the full
    line/section SKELETON at $0 so the filtered statement always shows the standard set of lines (even
    lines that are zero across every selected store, and even when the selection matches no store).
    Amounts are NEVER taken from `structure` — only its section/line shape (keys/labels/kind).

    Pure function (no client) → unit-testable. Deterministic; rounds each running total to cents.
    """
    # ordered (section_type -> {name, order:[keys], lines:{key:line}})
    sec_order, sec = [], {}

    def _ensure_sec(s):
        t = s.get("type")
        if t not in sec:
            sec[t] = {"name": s.get("name"), "order": [], "lines": {}}
            sec_order.append(t)
        elif not sec[t]["name"]:
            sec[t]["name"] = s.get("name")
        return sec[t]

    def _ensure_line(bucket, ln, seed_amount):
        key = ln.get("key")
        if key not in bucket["lines"]:
            bucket["order"].append(key)
            bucket["lines"][key] = {"key": key, "label": ln.get("label"),
                                    "kind": ln.get("kind"), "amount": 0.0, "detail": {}}
        return bucket["lines"][key]

    # 1) seed skeleton from `structure` at $0 (shape only, no amounts)
    if structure:
        for s in structure.get("sections", []):
            b = _ensure_sec(s)
            for ln in s.get("lines", []):
                _ensure_line(b, ln, 0.0)

    # 2) add every selected store snapshot's amounts
    for p in payloads:
        for s in p.get("sections", []):
            b = _ensure_sec(s)
            for ln in s.get("lines", []):
                tgt = _ensure_line(b, ln, 0.0)
                if not tgt["label"]:
                    tgt["label"] = ln.get("label")
                if not tgt["kind"]:
                    tgt["kind"] = ln.get("kind")
                tgt["amount"] = _r(tgt["amount"] + safe_float(ln.get("amount")))
                for dk, dv in (ln.get("detail") or {}).items():
                    tgt["detail"][dk] = _r(tgt["detail"].get(dk, 0.0) + safe_float(dv))

    sections, sec_total = [], {}
    for t in sec_order:
        b = sec[t]
        lines = [b["lines"][k] for k in b["order"]]
        # drop empty detail dicts so the shape matches an unfiltered snapshot line
        for ln in lines:
            ln["detail"] = {k: v for k, v in ln["detail"].items() if v}
        sub = _r(sum(l["amount"] for l in lines))
        sec_total[t] = sub
        sections.append({"name": b["name"], "type": t, "lines": lines, "subtotal": sub})

    out = {"statement_type": statement_type, "sections": sections}
    if statement_type == "pl":
        rev, cogs = sec_total.get("revenue", 0), sec_total.get("cogs", 0)
        opex, other = sec_total.get("opex", 0), sec_total.get("other", 0)
        out["gross_profit"] = _r(rev - cogs)
        out["net_operating_income"] = _r(out["gross_profit"] - opex)
        out["net_income"] = _r(out["net_operating_income"] - other)
    else:
        a, l, e = sec_total.get("asset", 0), sec_total.get("liability", 0), sec_total.get("equity", 0)
        out["assets_total"], out["liabilities_total"], out["equity_total"] = a, l, e
        out["imbalance"] = _r(a - (l + e))
        out["balanced"] = abs(out["imbalance"]) < 1.0
    return out


def filtered_statement(client, org_id, period, st_type, scope, stores_csv, markets_csv):
    """Build the store/market-filtered P&L or Balance Sheet for a period.

    Returns a dict shaped like router.get_pl/get_bs' `statement` payload PLUS filter metadata:
      {statement, filtered:True, filtered_stores:[...], filtered_markets:[...], matched_stores:int}.
    Reads only stored snapshots (org-scoped) — no money recompute. `st_type` ∈ {"pl","balance_sheet"}.
    """
    matcher, _explicit, markets = resolve_store_matcher(client, org_id, stores_csv, markets_csv)
    in_scope = scope_predicate(client, org_id, scope)
    # fetch this period's per-store snapshots for st_type and match the scope keys through the
    # canonical matcher (explicit stores case-insensitively; markets by any known spelling) AND the
    # company/store scope predicate (composition — the scope narrows the filter, never replaces it)
    # EVERY SPELLING OF THE MONTH, DEDUPED (owner report 2026-10-06, index §19.50 — the unfixed
    # sibling of §19.47). This read matched ONE spelling, so a month computed under the other form
    # returned no store snapshots at all and the filtered P&L came back `computed: true` with an
    # EMPTY statement and net income $0.00 — a filter that reads as "this subset earned nothing"
    # rather than "not computed". `router._read` already dereferenced `period_keys`; this path did
    # not. Widening alone would be a worse defect than the one it fixes: a month stored under BOTH
    # spellings would contribute each store twice and the filtered figures would DOUBLE, so the
    # widened read goes through the ONE "freshest wins" rule (`analysis.dedupe_latest`) that the
    # analysis payload already uses — never a second copy of it.
    _pkeys = list(_per.period_keys(period))
    rows = (client.schema("commcalc").table("account_statements")
            .select("period,statement_type,scope_key,scope_label,payload,computed_at")
            .eq("org_id", org_id).in_("period", _pkeys).eq("statement_type", st_type)
            .like("scope_key", "store:%").execute().data) or []
    _idx = _analysis.dedupe_latest(rows)
    # A period whose month cannot be parsed has no month key, so the dedupe cannot judge it at all
    # and would return nothing. Keep such rows verbatim — widening must never be able to return LESS
    # than the one-spelling read it replaced.
    rows = list(_idx.values()) if _idx else rows
    picked, matched_addrs = [], []
    for r in rows:
        addr = (r.get("scope_key") or "")[len("store:"):]
        if matcher(addr) and in_scope(addr):
            picked.append(r.get("payload") or {})
            matched_addrs.append(addr)
    # consolidated snapshot supplies the full line skeleton (shape only; amounts seeded at 0)
    cons = (client.schema("commcalc").table("account_statements")
            .select("payload,computed_at").eq("org_id", org_id).in_("period", _pkeys)
            .eq("statement_type", st_type).eq("scope_key", "consolidated")
            .order("computed_at", desc=True).execute().data) or []
    structure = (cons[0].get("payload") if cons else None)
    agg = aggregate(picked, st_type, structure=structure)
    n = len(matched_addrs)
    label_stores = ", ".join(sorted(matched_addrs)[:3]) + (" …" if n > 3 else "")
    scope_label = (f"Filtered — {n} store(s)"
                   + (f" · markets: {', '.join(markets)}" if markets else "")
                   + (f" [{label_stores}]" if label_stores.strip(" …") else ""))
    agg["period"], agg["scope_key"], agg["scope_label"] = period, "filtered", scope_label
    note = ("Store/market filter active — figures are the sum of the selected store(s). "
            "Company-wide lines (MI/ATU residual, carrier comp without a store, and unattributed "
            "journal entries) are booked company-wide, not to a store, so they read $0 here; see the "
            "Consolidated view for them.")
    notes = [note]
    if st_type == "balance_sheet" and not agg.get("balanced"):
        notes.append("Balance sheet is not balanced for this store subset (opening balances / cash "
                     "are typically entered company-wide, not per store).")
    agg["notes"] = notes
    return {"statement": agg, "filtered": True, "scope": "filtered",
            "filtered_stores": sorted(matched_addrs), "filtered_markets": markets,
            "matched_stores": n}
