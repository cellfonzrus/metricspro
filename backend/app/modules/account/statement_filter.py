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


def resolve_store_set(client, org_id, stores_csv="", markets_csv="", scope="consolidated"):
    """Resolve the active store/market selection to a set of canonical store addresses.

      • explicit stores  → taken as-is (they are the scope store addresses the picker offered).
      • markets          → every store_mapping.store_address in those markets (org-scoped; the
                           market→store authority is commcalc.store_mapping.market).
      • scope            → when a company/store scope is ALSO selected the result is INTERSECTED with
                           that scope's store universe, so the company selector still composes (the
                           core set is ADDED to the existing filter, never substituted). consolidated
                           (or empty scope) imposes no intersection.

    Values are PIPE-separated ('|'), not comma — a canonical store_address may itself contain a comma
    ("123 Main St, Queens NY"). Returns (store_set, resolved_markets) — store_set is a set[str].
    """
    stores = {s.strip() for s in (stores_csv or "").split("|") if s.strip()}
    markets = [m.strip() for m in (markets_csv or "").split("|") if m.strip()]
    S = set(stores)
    if markets:
        try:
            from app.modules.account import coa
            for r in coa._fetch_all(client, "store_mapping", "store_address,market", {"org_id": org_id}):
                mk = (r.get("market") or "").strip()
                sa = (r.get("store_address") or "").strip()
                if sa and mk in markets:
                    S.add(sa)
        except Exception:
            pass
    # compose with the company/store scope selector (intersection) when one is chosen. The company
    # universe is UPPER-cased (store_company_map keys) while store addresses keep their original case,
    # so intersect case-insensitively (keep the original-cased address on the S side).
    universe = _scope_store_universe(client, org_id, scope)
    if universe is not None:
        uni_lower = {u.lower() for u in universe}
        S = {s for s in S if s.lower() in uni_lower}
    return S, markets


def _scope_store_universe(client, org_id, scope):
    """The set of stores a company/store scope covers, or None for consolidated/unknown (= all)."""
    scope = scope or "consolidated"
    if scope == "consolidated":
        return None
    if scope.startswith("store:"):
        return {scope.split(":", 1)[1]}
    if scope.startswith("company:"):
        cid = scope.split(":", 1)[1]
        try:
            from app.modules.account import coa
            store_co, _default, _companies = coa.store_company_map(client, org_id)
            # store_co keys are UPPER-cased addresses → return the original-cased scope addresses by
            # matching case-insensitively against the store snapshots' addresses is handled by the
            # caller (we return the upper set and the caller filters store scopes case-insensitively).
            return {addr_upper for addr_upper, c in store_co.items() if c == cid}
        except Exception:
            return None
    return None


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
    store_set, markets = resolve_store_set(client, org_id, stores_csv, markets_csv, scope)
    # fetch this period's per-store snapshots for st_type, case-insensitively match the scope keys
    rows = (client.schema("commcalc").table("account_statements")
            .select("scope_key,scope_label,payload")
            .eq("org_id", org_id).eq("period", period).eq("statement_type", st_type)
            .like("scope_key", "store:%").execute().data) or []
    want_lower = {s.lower() for s in store_set}
    picked, matched_addrs = [], []
    for r in rows:
        addr = (r.get("scope_key") or "")[len("store:"):]
        if addr.lower() in want_lower or addr.upper() in store_set:
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
