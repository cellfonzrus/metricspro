"""Configurable commission PLAN engine — user-built rules, assigned per scope (migration 059).

A commission PLAN is a set of RULES the user creates. Each rule MATCHES sale lines on any field of the
sales transaction report (contract_type / tender_type / department / category / product_desc / sku /
trans_type / any) and defines how matching lines PAY (flat per unit, % of MRC, % of GP, % of price-over-
cost, or a flat bonus). A plan can TIER: qualifying units → a multiplier (the same idea as the KPI tier in
calculator.py). Plans are ASSIGNED to employees / stores / markets / a default, resolved per rep with
precedence employee > store > market > default.

This is the new system built ALONGSIDE calculator.py — it is READ-ONLY / PREVIEW. It never writes
rep_commissions and never touches the live POST /calculate path. Wiring a plan into the live calc is an
explicit later step the user approves (mirror of installment_engine.py).

Degrades to an empty/ready:false result if migration 059 isn't applied yet (tables absent) — never a 500.
"""
from app.modules.commcalc.calculator import safe_float

ORG_ID = "00000000-0000-0000-0000-000000000001"

MATCH_FIELDS = {"contract_type", "tender_type", "department", "category",
                "product_desc", "sku", "trans_type", "any"}
PAYOUT_KINDS = {"flat_per_unit", "pct_mrc", "pct_gp", "pct_price_over_cost", "flat"}


def _norm_mdn(v):
    """Same normalization the importer uses for mdn/phone (strip '.0' + whitespace)."""
    return ("" if v is None else str(v)).replace(".0", "").strip()


def _line_value(row, field):
    """The raw_sales value for a rule's match_field (lower-cased str)."""
    if field == "any":
        return ""
    return str(row.get(field, "") or "").strip().lower()


def _rule_matches(row, rule):
    """True if this sale line matches the rule's field/op/value."""
    field = (rule.get("match_field") or "any").strip().lower()
    if field == "any":
        return True
    op = (rule.get("match_op") or "equals").strip().lower()
    want = (rule.get("match_value") or "").strip().lower()
    have = _line_value(row, field)
    if op == "contains":
        return want != "" and want in have
    if op == "in":
        opts = [x.strip() for x in want.split(",") if x.strip()]
        return have in opts
    # equals (default)
    return have == want


# ── config loading ────────────────────────────────────────────────────────────────────────────────
def _load_plans(client, org_id):
    """All plans with nested rules / tiers / assignments. ({}, False) if migration 059 isn't applied."""
    try:
        plans = (client.schema("commcalc").table("commission_plan").select("*")
                 .eq("org_id", org_id).order("name").execute().data) or []
        rules = (client.schema("commcalc").table("commission_rule").select("*")
                 .eq("org_id", org_id).execute().data) or []
        tiers = (client.schema("commcalc").table("commission_tier").select("*")
                 .eq("org_id", org_id).execute().data) or []
        assigns = (client.schema("commcalc").table("commission_plan_assignment").select("*")
                   .eq("org_id", org_id).execute().data) or []
    except Exception:
        return [], False
    by_plan_rules, by_plan_tiers, by_plan_assigns = {}, {}, {}
    for r in rules:
        by_plan_rules.setdefault(r.get("plan_id"), []).append(r)
    for t in tiers:
        by_plan_tiers.setdefault(t.get("plan_id"), []).append(t)
    for a in assigns:
        by_plan_assigns.setdefault(a.get("plan_id"), []).append(a)
    out = []
    for p in plans:
        pid = p.get("id")
        rs = sorted(by_plan_rules.get(pid, []), key=lambda x: (x.get("sort") or 0))
        ts = sorted(by_plan_tiers.get(pid, []), key=lambda x: (x.get("min_count") or 0))
        out.append({**p, "rules": rs, "tiers": ts,
                    "assignments": by_plan_assigns.get(pid, [])})
    return out, True


def _resolve_plan_for(rep_name, store, market, plans):
    """Most-specific assignment wins: employee > store > market > default. Returns the plan dict or None.

    employee scope_value is matched to the rep's name (raw_sales.salesperson, case-insensitive); store to
    raw_sales.store; market to the rep's store_mapping market. priority breaks ties within a scope.
    """
    SCOPE_RANK = {"employee": 3, "store": 2, "market": 1, "default": 0}
    rn, sv_store, sv_mkt = (rep_name or "").strip().lower(), (store or "").strip().lower(), (market or "").strip().lower()
    best, best_key = None, (-1, -1)
    for p in plans:
        if not p.get("is_active", True):
            continue
        for a in p.get("assignments", []):
            scope = (a.get("scope") or "default").strip().lower()
            val = (a.get("scope_value") or "").strip().lower()
            ok = ((scope == "default") or
                  (scope == "employee" and val and val == rn) or
                  (scope == "store" and val and val == sv_store) or
                  (scope == "market" and val and val == sv_mkt))
            if not ok:
                continue
            key = (SCOPE_RANK.get(scope, 0), int(a.get("priority") or 0))
            if key > best_key:
                best, best_key = p, key
    return best


# ── reads ─────────────────────────────────────────────────────────────────────────────────────────
def _pvariants(period):
    """Period stored as 'June 2026' or '2026-06' — match both (REST IN list)."""
    from app.modules.commcalc.calculator import parse_period
    import calendar
    p = (period or "").strip()
    out = {p}
    pp = parse_period(p) if p else {}
    y, m = pp.get("year") or 0, pp.get("month") or 0
    if y and m:
        out.add(f"{y}-{m:02d}")
        out.add(f"{calendar.month_name[m]} {y}")
    return [x for x in out if x]


def _read_sales(client, org_id, period):
    """Paginated raw_sales for a period (REST 1000-row cap). select * so a missing column never errors."""
    out, start, page = [], 0, 1000
    while True:
        try:
            rows = (client.schema("commcalc").table("raw_sales").select("*")
                    .eq("org_id", org_id).in_("period", _pvariants(period))
                    .range(start, start + page - 1).execute().data) or []
        except Exception:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _read_mi_mrc(client, org_id, period):
    """{mdn -> MRC, subscriber_id -> MRC} for pct_mrc joins (commissionable_mrc, falls back to base_mrc)."""
    by_mdn, by_sub = {}, {}
    start, page = 0, 1000
    while True:
        try:
            rows = (client.schema("commcalc").table("raw_mi")
                    .select("phone_number,mdn,subscriber_id,base_mrc,commissionable_mrc")
                    .eq("org_id", org_id).in_("period", _pvariants(period))
                    .range(start, start + page - 1).execute().data) or []
        except Exception:
            break
        for r in rows:
            mrc = safe_float(r.get("commissionable_mrc")) or safe_float(r.get("base_mrc"))
            if mrc <= 0:
                continue
            m = _norm_mdn(r.get("phone_number") or r.get("mdn"))
            if m:
                by_mdn.setdefault(m, mrc)
            sub = str(r.get("subscriber_id") or "").strip()
            if sub:
                by_sub.setdefault(sub, mrc)
        if len(rows) < page:
            break
        start += page
    return by_mdn, by_sub


def _read_catalog_cost(client, org_id):
    """{product_id(float) -> cost} for pct_price_over_cost."""
    cost = {}
    try:
        rows = (client.schema("commcalc").table("raw_catalog").select("product_id,cost")
                .eq("org_id", org_id).limit(100000).execute().data) or []
    except Exception:
        return cost
    for c in rows:
        pid, cst = c.get("product_id"), c.get("cost")
        if pid is None:
            continue
        try:
            cost[float(pid)] = safe_float(cst)
        except Exception:
            continue
    return cost


def _read_store_market(client, org_id):
    """{store_address(lower) -> market} so an employee can be resolved to a market for assignment."""
    out = {}
    try:
        rows = (client.schema("commcalc").table("store_mapping")
                .select("store_address,store_code,market").eq("org_id", org_id).execute().data) or []
    except Exception:
        return out
    for s in rows:
        mkt = (s.get("market") or "").strip()
        addr = (s.get("store_address") or "").strip().lower()
        code = (s.get("store_code") or "").strip().lower()
        if addr:
            out[addr] = mkt
        if code:
            out.setdefault(code, mkt)
    return out


# ── per-line payout ─────────────────────────────────────────────────────────────────────────────
def _line_payout(row, rule, mrc_by_mdn, mrc_by_sub, cost_by_pid):
    """Dollar payout this rule produces for ONE matching qualifying line (before tier multiplier).
    flat is handled by the caller (once per rep), so here flat returns 0."""
    kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
    amt, pct = safe_float(rule.get("amount")), safe_float(rule.get("pct"))
    if kind == "flat_per_unit":
        return round(amt, 2)
    if kind == "pct_gp":
        return round(pct * safe_float(row.get("gp")), 2)
    if kind == "pct_price_over_cost":
        pid = row.get("product_id")
        try:
            cost = cost_by_pid.get(float(pid), 0.0) if pid is not None else 0.0
        except Exception:
            cost = 0.0
        return round(pct * max(0.0, safe_float(row.get("ext_price")) - cost), 2)
    if kind == "pct_mrc":
        # join raw_mi by mdn (raw_mi.phone_number) — the reliable per-subscriber key in raw_sales.
        # subscriber_id is a defensive secondary in case a tenant maps it into raw_sales later.
        mrc = mrc_by_mdn.get(_norm_mdn(row.get("mdn")))
        if mrc is None:
            sub = str(row.get("subscriber_id") or "").strip()
            mrc = mrc_by_sub.get(sub, 0.0) if sub else 0.0
        return round(pct * safe_float(mrc), 2)
    # 'flat' is applied once per rep by the caller
    return 0.0


def _tier_multiplier(plan, qualifying_units):
    """Highest min_count ≤ qualifying_units wins → its multiplier (1.0 if no tiers / no metric)."""
    metric = (plan.get("base_tier_metric") or "none").strip().lower()
    tiers = plan.get("tiers") or []
    if metric in ("", "none") or not tiers:
        return 1.0
    best_mult, best_min = 1.0, -1
    for t in tiers:
        mc = int(t.get("min_count") or 0)
        if qualifying_units >= mc and mc >= best_min:
            best_min, best_mult = mc, safe_float(t.get("multiplier")) or 1.0
    return best_mult


# ── preview ────────────────────────────────────────────────────────────────────────────────────
def preview(client, org_id, period, plan_id=None):
    """READ-ONLY: apply plan rules to a period's raw_sales. Writes nothing.

    Returns {ready, period, by_rep:[...], totals, plans, note}. If plan_id is given, that plan is applied
    to ALL reps; otherwise each rep gets the plan resolved by assignment precedence.
    """
    plans, ready = _load_plans(client, org_id)
    if not ready:
        return {"ready": False, "period": period, "by_rep": [], "totals": {},
                "note": "Migration 059_commission_plans.sql not applied — no preview."}
    if not plans:
        return {"ready": True, "period": period, "by_rep": [], "totals": {"payout": 0.0, "reps": 0},
                "note": "No commission plans configured yet."}

    forced_plan = None
    if plan_id:
        forced_plan = next((p for p in plans if str(p.get("id")) == str(plan_id)), None)
        if not forced_plan:
            return {"ready": True, "period": period, "by_rep": [], "totals": {"payout": 0.0, "reps": 0},
                    "note": "plan_id not found."}

    sales = _read_sales(client, org_id, period)
    # only un-voided, non-return lines qualify (same gate as the live calculator)
    valid = [r for r in sales
             if str(r.get("voided", "") or "").upper().strip() != "YES"
             and str(r.get("trans_type", "") or "").strip() != "Return"]

    mrc_by_mdn, mrc_by_sub = _read_mi_mrc(client, org_id, period)
    cost_by_pid = _read_catalog_cost(client, org_id)
    store_market = _read_store_market(client, org_id)

    # group lines per rep
    reps = {}  # key (upper rep name) -> {name, store, lines:[...]}
    for r in valid:
        rep = str(r.get("salesperson", "") or "").strip()
        if not rep or rep.lower() == "admin":
            continue
        key = rep.upper()
        e = reps.get(key)
        if not e:
            e = reps[key] = {"name": rep, "store": str(r.get("store", "") or "").strip(), "lines": []}
        e["lines"].append(r)

    out_rows, grand = [], 0.0
    for key, e in reps.items():
        store = e["store"]
        market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
        plan = forced_plan or _resolve_plan_for(e["name"], store, market, plans)
        if not plan:
            continue
        rules = plan.get("rules") or []

        rule_breakdown = {}   # rule_id -> {label, kind, matched, qualifying, payout, tiered}
        qualifying_units = 0
        flat_pending = {}     # rule_id -> amount (flat bonus, paid once if any qualifying match)
        base_total = 0.0      # payout that is NOT tiered
        tiered_total = 0.0    # payout that IS tiered (scaled by multiplier later)

        for rule in rules:
            kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
            is_tiered = bool(rule.get("tiered"))
            qualifies = bool(rule.get("qualifies", True))
            rid = rule.get("id")
            rb = rule_breakdown.setdefault(rid, {
                "rule_id": rid, "label": rule.get("label") or rule.get("match_value") or rule.get("match_field"),
                "payout_kind": kind, "tiered": is_tiered, "qualifies": qualifies,
                "matched_lines": 0, "qualifying_units": 0, "payout": 0.0})
            for row in e["lines"]:
                if not _rule_matches(row, rule):
                    continue
                rb["matched_lines"] += 1
                if not qualifies:
                    continue
                rb["qualifying_units"] += 1
                if kind == "flat":
                    flat_pending[rid] = safe_float(rule.get("amount"))  # once per rep
                    continue
                pay = _line_payout(row, rule, mrc_by_mdn, mrc_by_sub, cost_by_pid)
                rb["payout"] = round(rb["payout"] + pay, 2)
                if is_tiered:
                    tiered_total += pay
                else:
                    base_total += pay
            qualifying_units += rb["qualifying_units"]

        # flat bonuses (paid once per rep if its rule had any qualifying match)
        for rule in rules:
            rid = rule.get("id")
            if rid in flat_pending:
                amt = flat_pending[rid]
                rule_breakdown[rid]["payout"] = round(rule_breakdown[rid]["payout"] + amt, 2)
                if bool(rule.get("tiered")):
                    tiered_total += amt
                else:
                    base_total += amt

        mult = _tier_multiplier(plan, qualifying_units)
        total = round(base_total + tiered_total * mult, 2)
        grand += total
        out_rows.append({
            "rep": e["name"], "store": store, "market": market,
            "plan_id": plan.get("id"), "plan_name": plan.get("name"),
            "qualifying_units": qualifying_units, "tier_multiplier": mult,
            "base_payout": round(base_total, 2), "tiered_payout": round(tiered_total, 2),
            "total_payout": total,
            "rules": sorted([rb for rb in rule_breakdown.values() if rb["matched_lines"]],
                            key=lambda x: -(x.get("payout") or 0)),
        })

    out_rows.sort(key=lambda x: -(x.get("total_payout") or 0))
    return {"ready": True, "period": period, "by_rep": out_rows,
            "totals": {"payout": round(grand, 2), "reps": len(out_rows),
                       "sale_lines": len(valid), "plans": len(plans)},
            "note": None}
