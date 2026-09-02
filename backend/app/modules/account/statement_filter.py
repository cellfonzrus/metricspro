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

COMPANY-WIDE lines (MI/ATU residual, carrier comp without a store, unattributed journal entries) carry
no store, so they are absent from every per-store snapshot's store attribution — in the filtered view
they read $0. That is the documented convention: "company-wide lines are booked company-wide, not to a
store, and read $0 under a store/market filter" (mirrors the existing per-scope note in engine._notes).

NOTHING here changes a booking rule, a rate, or an existing computed number. With NO filter active the
read endpoint never calls this module and returns the stored snapshot byte-for-byte (see router.get_pl/
get_bs). This is a read-only, deterministic, org-scoped display aggregation.
"""
from app.modules.commcalc.calculator import safe_float


def _r(x):
    return round(safe_float(x), 2)


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
    from app.modules.account.coa import _squash_key, _lead_num_key
    idx = idx or {}
    by_market = idx.get("by_market") or {}
    addr_keys = idx.get("addr_keys") or {}
    want = {str(m or "").strip().lower() for m in (markets or []) if str(m or "").strip()}
    upper_keys, member_codes = set(), set()
    for mk in want:
        b = by_market.get(mk)
        if not b:
            continue
        upper_keys |= {str(k).upper() for k in (b.get("keys") or ())}
        member_codes |= {str(c).upper() for c in (b.get("codes") or ())}
    for code in member_codes:
        upper_keys |= {str(a).upper() for a in (addr_keys.get(code) or ())}
    squashed_keys = {_squash_key(k) for k in upper_keys}
    squashed_keys.discard("")
    # index-wide street-number ambiguity: number → owning identity (code, else the address itself)
    num_owner = {}
    for code, addrs in addr_keys.items():
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
    member_idents = member_codes | upper_keys
    member_nums = {n for n, owners in num_owner.items()
                   if len(owners) == 1 and next(iter(owners)) in member_idents}
    return upper_keys, squashed_keys, member_nums


def build_store_matcher(explicit_stores, upper_keys, squashed_keys, member_nums):
    """PURE: fn(snapshot store address) -> bool for the combined store+market selection.
    Explicit stores (the picker offers the exact snapshot addresses) match case-insensitively —
    byte-identical to the old behaviour; market membership matches by any known spelling (exact
    upper → squashed → unambiguous leading street number)."""
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


def resolve_store_matcher(client, org_id, stores_csv="", markets_csv=""):
    """Resolve the active store/market selection to a snapshot-address matcher.

      • explicit stores → matched as-is, case-insensitively (they ARE the scope store addresses the
        picker offered).
      • markets → resolved through the org's canonical union market index
        (core.scope.market_index: storeops.stores ∪ commcalc.store_mapping ∪ store_aliases), so any
        market the picker offers binds — see market_key_expansion. A read failure degrades to a
        best-effort store_mapping-only expansion (the old authority), never to silently-all.

    Values are PIPE-separated ('|'), not comma — a canonical store_address may itself contain a comma
    ("123 Main St, Queens NY"). Returns (matcher, explicit_stores:set, markets:list)."""
    stores = {s.strip() for s in (stores_csv or "").split("|") if s.strip()}
    markets = [m.strip() for m in (markets_csv or "").split("|") if m.strip()]
    upper_keys, squashed_keys, member_nums = set(), set(), set()
    if markets:
        idx = None
        try:
            from app.core import scope as core_scope
            idx = core_scope.market_index(client, org_id)
        except Exception:
            idx = None
        if idx is None:
            # degraded fallback: the pre-2026-09 store_mapping-only vocabulary (case-insensitive now)
            try:
                from app.modules.account import coa
                rows = coa._fetch_all(client, "store_mapping", "store_code,store_address,market",
                                      {"org_id": org_id})
                idx = {"by_market": {}, "addr_keys": {}, "stores": []}
                for r in rows:
                    mk = (r.get("market") or "").strip().lower()
                    sa = (r.get("store_address") or "").strip()
                    code = (r.get("store_code") or "").strip().upper()
                    if not mk or not sa:
                        continue
                    b = idx["by_market"].setdefault(mk, {"codes": set(), "keys": set()})
                    b["keys"].add(sa.upper())
                    if code:
                        b["codes"].add(code)
                        idx["addr_keys"].setdefault(code, set()).add(sa.upper())
            except Exception:
                idx = {}
        upper_keys, squashed_keys, member_nums = market_key_expansion(idx, markets)
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
    rows = (client.schema("commcalc").table("account_statements")
            .select("scope_key,scope_label,payload")
            .eq("org_id", org_id).eq("period", period).eq("statement_type", st_type)
            .like("scope_key", "store:%").execute().data) or []
    picked, matched_addrs = [], []
    for r in rows:
        addr = (r.get("scope_key") or "")[len("store:"):]
        if matcher(addr) and in_scope(addr):
            picked.append(r.get("payload") or {})
            matched_addrs.append(addr)
    # consolidated snapshot supplies the full line skeleton (shape only; amounts seeded at 0)
    cons = (client.schema("commcalc").table("account_statements")
            .select("payload").eq("org_id", org_id).eq("period", period)
            .eq("statement_type", st_type).eq("scope_key", "consolidated").execute().data) or []
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
