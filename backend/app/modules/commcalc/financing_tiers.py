"""TIERED, TARGET-BASED per-unit rates for a commission rule (migration 273).

OWNER DIRECTIVE (in-chat 2026-08-04): "…should have assignable target for each store in target area and
target based commission payout right now we have flat payment, need it tiered levels."
OWNER ANSWERS (in-chat 2026-08-04, verbatim): **"achieved rate applies to that months sales, attainment
is monthly"** — so:
  ① the tier a store REACHES sets the per-unit rate for EVERY financing unit of that month
     (whole-month RETROACTIVE, not marginal per band), and
  ② attainment is measured MONTHLY, against the store's monthly financing target for the period.
Both are the implemented default here, not a note on a screen.

⚠️ MONEY — AND INERT BY CONSTRUCTION.
`build_context()` returns `{"active": False}` unless the tenant has at least one commission_tier row that
is BOTH scoped to a rule (`rule_id`) AND carries a `unit_rate`. With no such row — i.e. every tenant
today, and every tenant after migration 273 runs — the commission engine never calls anything else in
this module and its payout is byte-identical, line for line. **No rate, threshold or dollar value is
seeded anywhere in this file.** The levels are the owner's to type.

HOW A TIER IS CHOSEN
  measured units → the STORE's financing units for that rule (default) or the rep's own (`unit_tier_scope`)
  target        → commcalc.financing_target for (period, store[, vendor])   — migration 272
  attainment %  → measured ÷ target × 100
  tier          → the highest threshold the value reaches; its `unit_rate` becomes the per-unit pay
  NO TARGET SET → **no tier applies at all**. The rule keeps paying its flat amount and the run reports
                  "no financing target set for this store". A missing target is never treated as 0% (that
                  would silently drop everyone to the bottom tier) and never as 100%.

WHY IT EXTENDS commission_tier RATHER THAN ADDING A PARALLEL SYSTEM: the plan engine already resolves,
loads and edits tiers; a second tier table would drift. A rule-scoped row with a unit_rate is simply a
tier that answers "what is a unit worth" instead of "what do I multiply by".

PURE where it counts: every resolver takes its config as an argument. The only I/O is the three loaders.
"""

TIER_MODES = ("whole_month", "marginal")
TIER_SCOPES = ("store", "rep")
# OWNER 2026-08-04: the achieved rate applies to that month's sales.
DEFAULT_TIER_MODE = "whole_month"
DEFAULT_TIER_SCOPE = "store"


def _f(v, default=0.0):
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _num_or_none(v):
    """float(v) or None. A BLANK stays None — 'not stated', never 0.0."""
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ══ PURE: tier normalisation + selection ════════════════════════════════════════════════════════
def normalize_tier(row):
    """One commission_tier row → a rule-scoped RATE tier, or None when it is not one. PURE.

    A row without `rule_id` or without `unit_rate` is NOT a rate tier — it is the original plan-wide
    multiplier tier and is left completely alone."""
    if not isinstance(row, dict):
        return None
    rid = row.get("rule_id")
    rate = _num_or_none(row.get("unit_rate"))
    if not rid or rate is None:
        return None
    att = _num_or_none(row.get("min_attainment_pct"))
    try:
        mc = int(row.get("min_count") or 0)
    except (TypeError, ValueError):
        mc = 0
    return {"id": row.get("id"), "rule_id": str(rid), "unit_rate": rate,
            "min_attainment_pct": att, "min_count": mc,
            "label": row.get("label") or None,
            "threshold_kind": "attainment" if att is not None else "count"}


def pick_tier(tiers, units, attainment_pct):
    """(tier, reason) — the highest threshold the value reaches. PURE.

    A tier list that states ANY attainment threshold is an ATTAINMENT ladder: with no attainment number
    available (no target set) nothing is reached and the caller must leave pay alone. A count ladder uses
    the measured unit count.
    """
    tiers = [t for t in (tiers or []) if t]
    if not tiers:
        return None, "no_tiers"
    uses_attainment = any(t["threshold_kind"] == "attainment" for t in tiers)
    if uses_attainment:
        if attainment_pct is None:
            return None, "no_target"
        value = attainment_pct
        def thr(t):
            return t["min_attainment_pct"] if t["min_attainment_pct"] is not None else 0.0
    else:
        value = float(units)
        def thr(t):
            return float(t["min_count"])
    best, best_thr = None, None
    for t in sorted(tiers, key=lambda x: (thr(x), _f(x.get("unit_rate")))):
        if value >= thr(t):
            best, best_thr = t, thr(t)
    if best is None:
        return None, "below_lowest_tier"
    return best, ("attainment" if uses_attainment else "count")


def per_unit_rates(tiers, units, attainment_pct, mode, measured_units=None):
    """(rates, tier, reason) — the rate EACH of the rep's `units` units earns. PURE.

    `measured_units` is what DECIDES the tier (the store's financing units under the default 'store'
    scope); `units` is how many units this rep is paid for. They differ on purpose: the owner's rule is
    that the STORE reaching a level lifts everyone at that store, so a rep with one unit at a store that
    hit tier 2 is paid the tier-2 rate on their one unit. With scope='rep' the caller passes the same
    number twice and the distinction disappears.

    whole_month (the owner's rule, 2026-08-04): every unit earns the achieved tier's rate.
    marginal: unit i earns the rate of the band it falls in — only defined for a COUNT ladder measured on
    the SAME population that is paid (rep scope). Anything else falls back to whole_month rather than
    inventing a per-unit band out of a store percentage."""
    measured = units if measured_units is None else measured_units
    tier, reason = pick_tier(tiers, measured, attainment_pct)
    if tier is None:
        return [], None, reason
    m = mode if mode in TIER_MODES else DEFAULT_TIER_MODE
    if m == "marginal" and reason == "count" and float(measured) == float(units):
        ladder = sorted([t for t in tiers], key=lambda x: float(x["min_count"]))
        rates = []
        for i in range(1, int(units) + 1):
            cur = None
            for t in ladder:
                if i >= float(t["min_count"]):
                    cur = t
            rates.append(_f(cur["unit_rate"]) if cur is not None else None)
        if any(r is None for r in rates):
            # a unit below the lowest band has no stated rate — it keeps the rule's flat amount, so
            # marginal mode can never silently zero the first units of a month.
            return [], tier, "marginal_below_lowest"
        return rates, tier, "marginal"
    return [_f(tier["unit_rate"])] * int(units), tier, ("whole_month" if reason != "no_target" else reason)


def rule_settings(rule):
    """(scope, mode, vendor_key) for one commission_rule. PURE. NULL/unknown → the owner's defaults."""
    scope = str((rule or {}).get("unit_tier_scope") or "").strip().lower()
    mode = str((rule or {}).get("unit_tier_mode") or "").strip().lower()
    vk = str((rule or {}).get("financing_vendor_key") or "").strip().lower() or None
    return (scope if scope in TIER_SCOPES else DEFAULT_TIER_SCOPE,
            mode if mode in TIER_MODES else DEFAULT_TIER_MODE, vk)


# ══ store-key resolution (shared by the targets and the store-unit counts) ══════════════════════
def store_keys(store):
    """Every key one raw POS store string could be known by: the cleaned string, and its leading number.
    PURE — deliberately the same shape the market resolver uses, so a target set against '957' or against
    '957 Pennsylvania Ave' both find the store's sales."""
    s = str(store or "").strip()
    out = []
    if s:
        out.append(s.lower())
    lead = ""
    for ch in s:
        if ch.isdigit():
            lead += ch
        elif lead:
            break
        elif not ch.isspace():
            break
    if lead:
        out.append(lead)
    return out


def build_store_index(mapping_rows, storeops_rows):
    """{alias key → canonical store_code} from commcalc.store_mapping + storeops.stores. PURE."""
    idx = {}
    for r in (mapping_rows or []):
        code = str(r.get("store_code") or "").strip()
        if not code:
            continue
        for k in store_keys(r.get("store_address")) + store_keys(code):
            idx.setdefault(k, code)
    for r in (storeops_rows or []):
        code = str(r.get("store_code") or "").strip()
        if not code:
            continue
        for k in store_keys(r.get("address")) + store_keys(code):
            idx.setdefault(k, code)
    return idx


def resolve_store_code(store, idx):
    """Canonical store_code for a raw POS store string, else the cleaned raw string. PURE."""
    for k in store_keys(store):
        if k in (idx or {}):
            return idx[k]
    ks = store_keys(store)
    return ks[0] if ks else ""


# ══ loaders (org-scoped; degrade to inert; never raise) ═════════════════════════════════════════
def load_rule_tiers(client, org_id):
    """{rule_id: [tier, …]} — ONLY rule-scoped rows that carry a unit_rate (migration 273). Missing
    columns / missing migration → {} → the whole feature stays inert."""
    try:
        rows = (client.schema("commcalc").table("commission_tier").select("*")
                .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        t = normalize_tier(r)
        if t:
            out.setdefault(t["rule_id"], []).append(t)
    return out


def load_targets(client, org_id, period_variants):
    """{(store_code, vendor_key or ''): {'units':…, 'amount':…}} for a period (migration 272)."""
    try:
        rows = (client.schema("commcalc").table("financing_target").select("*")
                .eq("org_id", org_id).in_("period", list(period_variants or []))
                .limit(20000).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        code = str(r.get("store_code") or "").strip()
        if not code:
            continue
        vk = str(r.get("vendor_key") or "").strip().lower()
        out[(code, vk)] = {"units": _f(r.get("target_units")),
                           "amount": _num_or_none(r.get("target_amount"))}
    return out


def load_store_index(client, org_id):
    def _q(schema, table, cols):
        try:
            return (client.schema(schema).table(table).select(cols)
                    .eq("org_id", org_id).limit(5000).execute().data) or []
        except Exception:
            return []
    return build_store_index(_q("commcalc", "store_mapping", "store_code,store_address"),
                             _q("storeops", "stores", "store_code,address"))


# ══ the engine hook ═════════════════════════════════════════════════════════════════════════════
def build_context(client, org_id, period_variants, plans, lines, rule_matches,
                  paying_lines=None, basis_by_rule=None, unit_cfg=None, is_accessory=None,
                  is_excluded=None):
    """Everything the commission engine needs to price financing tiers, or `{"active": False}`.

    `rule_matches` / `paying_lines` are INJECTED by the caller (commission_engine) so this module never
    re-implements the matcher or the per-device unit collapse — the store's unit count is produced by
    exactly the code that decides what pays.

    Returns {active, rules:{rule_id:{rule,tiers,scope,mode,vendor_key}}, store_units:{(rid,code):n},
             targets, store_index, notes:[…]}.
    """
    inactive = {"active": False, "rules": {}, "store_units": {}, "targets": {}, "notes": []}
    try:
        tiers_by_rule = load_rule_tiers(client, org_id)
    except Exception:
        return inactive
    if not tiers_by_rule:
        return inactive

    rule_by_id = {}
    for p in (plans or []):
        for r in (p.get("rules") or []):
            rule_by_id[str(r.get("id"))] = r

    active = {}
    notes = []
    for rid, tiers in tiers_by_rule.items():
        rule = rule_by_id.get(rid)
        if rule is None:
            continue
        kind = str(rule.get("payout_kind") or "flat_per_unit").strip().lower()
        if kind != "flat_per_unit":
            # A per-unit RATE only means something for a per-unit payout. A %-of-basis rule reads each
            # line's own price/GP, so replacing it with a flat rate would silently rewrite the basis.
            notes.append({"code": "tier_rule_not_per_unit", "rule_id": rid,
                          "detail": (f"Rule “{rule.get('label') or rid}” has financing tiers but pays "
                                     f"'{kind}', not a per-unit amount — its tiers were ignored.")})
            continue
        scope, mode, vk = rule_settings(rule)
        active[rid] = {"rule": rule, "tiers": tiers, "scope": scope, "mode": mode, "vendor_key": vk,
                       "label": rule.get("label") or rid, "flat_amount": _f(rule.get("amount"))}
    if not active:
        return dict(inactive, notes=notes)

    store_index = load_store_index(client, org_id)
    targets = load_targets(client, org_id, period_variants)

    # STORE-level unit counts, for the default 'store' scope. Computed with the caller's own matcher and
    # unit-collapse so "the store's financing units" is the same number that pays.
    store_units = {}
    need_store = any(v["scope"] == "store" for v in active.values())
    if need_store and lines:
        by_store = {}
        for r in lines:
            by_store.setdefault(resolve_store_code(r.get("store"), store_index), []).append(r)
        for rid, meta in active.items():
            if meta["scope"] != "store":
                continue
            rule = meta["rule"]
            for code, rows in by_store.items():
                try:
                    matched = [r for r in rows if rule_matches(r, rule)]
                except Exception:
                    matched = []
                if is_excluded is not None:
                    matched = [r for r in matched if not is_excluded(r)]
                n = len(matched)
                if matched and paying_lines is not None:
                    basis = (basis_by_rule or {}).get(id(rule), ("per_line", "default"))[0]
                    if basis != "per_line":
                        try:
                            payers, _supp, _notes = paying_lines(matched, basis, unit_cfg or {},
                                                                is_accessory)
                            n = len(payers)
                        except Exception:
                            n = len(matched)
                if n:
                    store_units[(rid, code)] = n
    return {"active": True, "rules": active, "store_units": store_units, "targets": targets,
            "store_index": store_index, "notes": notes}


def target_for(ctx, store_code, vendor_key):
    """(target_units, source) — the vendor's own target row if the tenant set one, else the store's
    whole-financing target, else (None, 'none'). PURE given ctx."""
    t = ctx.get("targets") or {}
    if vendor_key:
        row = t.get((store_code, vendor_key))
        if row and _f(row.get("units")) > 0:
            return _f(row["units"]), "vendor"
    row = t.get((store_code, ""))
    if row and _f(row.get("units")) > 0:
        return _f(row["units"]), "store"
    return None, "none"


def apply_rule_tiers(ctx, rule_id, store, rep, items):
    """Re-price ONE rule's paying lines for ONE rep under its financing tiers.

    `items` = [(row, line_detail_or_None, paid_amount), …] in the order they paid.
    Returns None when nothing changes (no tier reached, no target, rates equal) — the caller then leaves
    every number exactly as the flat rule computed it.

    Otherwise returns {delta, rates, tier, attainment_pct, measured_units, target_units, reason, report}
    and has already rewritten each supplied line-detail dict's `amount`.
    """
    meta = (ctx.get("rules") or {}).get(str(rule_id))
    if not meta or not items:
        return None
    code = resolve_store_code(store, ctx.get("store_index") or {})
    units = len(items)
    measured = units
    if meta["scope"] == "store":
        measured = (ctx.get("store_units") or {}).get((str(rule_id), code), units)
    target_units, target_source = target_for(ctx, code, meta["vendor_key"])
    attainment = (100.0 * measured / target_units) if target_units else None
    rates, tier, reason = per_unit_rates(meta["tiers"], units, attainment, meta["mode"],
                                         measured_units=measured)
    report = {"rule_id": str(rule_id), "rule": meta["label"], "rep": rep, "store": store,
              "store_code": code, "vendor_key": meta["vendor_key"], "scope": meta["scope"],
              "mode": meta["mode"], "units": units, "measured_units": measured,
              "target_units": target_units, "target_source": target_source,
              "attainment_pct": (round(attainment, 1) if attainment is not None else None),
              "tier": None, "unit_rate": None, "flat_amount": meta["flat_amount"],
              "amount_before": round(sum(_f(p) for (_r, _d, p) in items), 2),
              "amount_after": None, "delta": 0.0, "reason": reason, "applied": False}
    if not rates or tier is None:
        report["note"] = {
            "no_target": (f"No financing target is set for {code} this period, so no attainment tier "
                          f"applies — “{meta['label']}” paid its flat amount."),
            "below_lowest_tier": (f"{code} reached no tier, so “{meta['label']}” paid its flat amount."),
            "no_tiers": "No usable tiers configured.",
            "marginal_below_lowest": ("Marginal mode is configured but the lowest band starts above the "
                                      "first unit — the flat amount was kept."),
        }.get(reason, "")
        return report if reason in ("no_target", "below_lowest_tier", "marginal_below_lowest") else None

    after = 0.0
    delta = 0.0
    for i, (row, ldet, paid) in enumerate(items):
        new_amt = round(_f(rates[i]), 2)
        after += new_amt
        delta += new_amt - _f(paid)
        if ldet is not None:
            ldet["amount"] = new_amt
            ldet["financing_tier"] = tier.get("label") or (
                f"{tier['min_attainment_pct']}% attainment" if tier["threshold_kind"] == "attainment"
                else f"{tier['min_count']}+ units")
            ldet["financing_tier_rate"] = new_amt
            ldet["amount_before_tier"] = round(_f(paid), 2)
    report["tier"] = {"label": tier.get("label"), "min_count": tier["min_count"],
                      "min_attainment_pct": tier["min_attainment_pct"],
                      "unit_rate": tier["unit_rate"], "threshold_kind": tier["threshold_kind"]}
    report["unit_rate"] = tier["unit_rate"]
    report["amount_after"] = round(after, 2)
    report["delta"] = round(delta, 2)
    report["applied"] = abs(delta) > 0.0000001
    report["rates"] = [round(_f(x), 2) for x in rates]
    if not report["applied"]:
        return None
    return report
