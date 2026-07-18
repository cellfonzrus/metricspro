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
import re

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


def _canon_person(s):
    """Canonicalize a person-name for name-ORDER-insensitive equality matching. PURE (no I/O).

    Steps, in order: casefold · trim · collapse internal whitespace runs to one space · then
    IF the result contains EXACTLY ONE comma, treat it as "Last, First" and reorder to "First Last"
    (the pre-comma part is the surname — possibly multi-token — and is appended AFTER the post-comma
    first-part, preserving each part's internal token order): "Islam Khan, Ariful" -> "ariful islam khan".
    More than one comma, or an empty side, -> the non-reordered folded string, unchanged.

    Deliberately NOT fuzzy: no token dropping, no middle-name stripping, no spelling tolerance.
    "natasha cabrera" != "natasha nicole cabrera" stays UNMATCHED by design (data hygiene, not matching).
    For comma-free ASCII single-spaced input this equals the previous `.strip().lower()` byte-for-byte,
    so store/market/default and comma-free house employee data are unaffected."""
    folded = re.sub(r"\s+", " ", ("" if s is None else str(s)).strip().casefold())
    if folded.count(",") == 1:
        last, first = (x.strip() for x in folded.split(","))
        if last and first:
            return f"{first} {last}"
    return folded


def _assignment_miss_reason(scope, val, rn_canon, rr, sv_store, sv_mkt, scope_value_raw):
    """Plain-language 'why this assignment did NOT attach to the rep', for the drill-down nearest-miss
    list. PURE. It only NARRATES a branch of the SAME predicate `_resolve_plan_for` evaluates — it is
    never a second matching implementation (the caller passes the exact `ok`; this just explains ¬ok)."""
    if scope == "employee":
        if not val:
            return "employee assignment has no name"
        return f"employee '{scope_value_raw}' (canonical '{_canon_person(scope_value_raw)}') != rep '{rn_canon}'"
    if scope == "role":
        if not val:
            return "role assignment has no value"
        if not rr:
            return f"rep has no roster role, so role '{scope_value_raw}' cannot match"
        return f"role '{val}' != rep role '{rr}'"
    if scope == "store":
        if not val:
            return "store assignment has no value"
        return f"store scope '{val}' != rep store '{sv_store or '(none)'}'"
    if scope == "market":
        if not val:
            return "market assignment has no value"
        return f"market scope '{val}' != rep market '{sv_mkt or '(none)'}'"
    return f"scope '{scope}' did not match"


def _resolve_plan_for(rep_name, store, market, plans, rep_role=None, explain=False):
    """Most-specific assignment wins: employee > role > store > market > default. Returns the plan or None.

    EMPLOYEE scope_value is matched to the rep's name name-order-insensitively via `_canon_person`
    (raw_sales.salesperson emits "Last, First"; assignments are usually "First Last" — both canonicalize
    to the same string). ROLE scope_value is matched (case-insensitively) to the rep's JOB ROLE
    (`rep_role`, resolved from the storeops roster by the SAME `_canon_person` name bridge — see
    `_read_employee_roles`); an EMPLOYEE assignment for the same rep OVERRIDES their role assignment
    (employee outranks role). STORE/MARKET/DEFAULT matching is byte-identical to before (plain
    casefold/trim, no reorder). priority breaks ties within a scope; across equal-key ties the first
    plan in name order (from _load_plans' .order("name")) wins deterministically.

    HOUSE-SAFE: when NO role assignments exist and rep_role is unused, the winner is byte-identical to
    the pre-role ranks — SCOPE_RANK values are only compared RELATIVELY and employee>store>market>default
    order is preserved (role slots strictly between employee and store).

    explain=False (default) → returns the winning plan (or None), BYTE-IDENTICAL to before. The extra
    `best_assign` bookkeeping does not affect the returned value; the `if explain` blocks never run.
    explain=True → returns {"plan", "winner", "considered"} for the drill-down narration (the SINGLE
    source of truth so the narration can never disagree with what pays): `winner` is the winning
    assignment {plan_id, plan_name, scope, scope_value, priority, rank} (None if no plan attached);
    `considered` is EVERY assignment evaluated, each with matched:bool + a miss `reason`."""
    SCOPE_RANK = {"employee": 4, "role": 3, "store": 2, "market": 1, "default": 0}
    rn_canon = _canon_person(rep_name)
    rr = (rep_role or "").strip().lower()
    sv_store, sv_mkt = (store or "").strip().lower(), (market or "").strip().lower()
    best, best_key, best_assign = None, (-1, -1), None
    considered = [] if explain else None
    for p in plans:
        if not p.get("is_active", True):
            if explain:
                for a in p.get("assignments", []):
                    considered.append({"plan_id": p.get("id"), "plan_name": p.get("name"),
                                       "scope": (a.get("scope") or "default"), "scope_value": a.get("scope_value"),
                                       "priority": int(a.get("priority") or 0), "rank": None,
                                       "matched": False, "reason": "plan is inactive"})
            continue
        for a in p.get("assignments", []):
            scope = (a.get("scope") or "default").strip().lower()
            val = (a.get("scope_value") or "").strip().lower()
            if scope == "employee":
                ok = bool(val) and _canon_person(a.get("scope_value")) == rn_canon
            elif scope == "role":
                ok = bool(val) and bool(rr) and val == rr
            else:
                ok = ((scope == "default") or
                      (scope == "store" and val and val == sv_store) or
                      (scope == "market" and val and val == sv_mkt))
            if explain:
                considered.append({
                    "plan_id": p.get("id"), "plan_name": p.get("name"),
                    "scope": scope, "scope_value": a.get("scope_value"),
                    "priority": int(a.get("priority") or 0), "rank": SCOPE_RANK.get(scope, 0),
                    "matched": bool(ok),
                    "reason": None if ok else _assignment_miss_reason(
                        scope, val, rn_canon, rr, sv_store, sv_mkt, a.get("scope_value")),
                })
            if not ok:
                continue
            key = (SCOPE_RANK.get(scope, 0), int(a.get("priority") or 0))
            if key > best_key:
                best, best_key, best_assign = p, key, a
    if explain:
        winner = None
        if best is not None and best_assign is not None:
            winner = {"plan_id": best.get("id"), "plan_name": best.get("name"),
                      "scope": (best_assign.get("scope") or "default"),
                      "scope_value": best_assign.get("scope_value"),
                      "priority": int(best_assign.get("priority") or 0), "rank": best_key[0]}
        return {"plan": best, "winner": winner, "considered": considered}
    return best


def _read_employee_roles(client, org_id):
    """{_canon_person(employee name) -> job role (lower-cased)} from the org's storeops roster.

    The name bridge (`_canon_person`) lets a sales row's POS "Last, First" salesperson find the roster's
    "First Last" row and thus the rep's ROLE, so a scope='role' assignment can pay every rep with that
    role. Org-scoped, one query, called ONCE per preview/installment pass. Degrades to {} (role scope
    simply can't match; employee/store/market/default unaffected) if storeops is unreachable.

    INACTIVE employees are INCLUDED by design (no is_active filter): a mid-month-terminated rep still has
    sale lines in the period, and those sales must still pay under their role — filtering to active would
    silently drop their pay. (The UI role-count preview shows active + inactive separately so the numbers
    agree with what this matches.) ORDERED by `id` so, when two roster rows canonicalize to the SAME name
    (F1: same-named employees with different roles), the LOWEST-id row wins DETERMINISTICALLY across
    recalcs instead of DB heap order; `_role_name_collisions` surfaces such ambiguity in diagnose."""
    out = {}
    try:
        rows = (client.schema("storeops").table("employees")
                .select("id,name,role").eq("org_id", org_id).order("id").execute().data) or []
    except Exception:
        return out
    for e in rows:
        nm = _canon_person(e.get("name"))
        role = (e.get("role") or "").strip().lower()
        if nm and role:
            out.setdefault(nm, role)   # id-ordered → lowest-id roster row wins (deterministic)
    return out


def _role_name_collisions(client, org_id):
    """Roster rows whose NAMES collapse to the same `_canon_person` key → role resolution is ambiguous
    (the lowest-id row wins in `_read_employee_roles`). Returns
    [{canon, winner_role, rows:[{id,name,role}...]}] for the /payout-plans/diagnose panel so the operator
    can SEE the ambiguity ("2 roster rows collapse to 'luis martinez' — role resolution uses the lowest
    id"). Read-only; org-scoped; [] on any failure. Only same-name groups of size > 1 are returned."""
    groups = {}
    try:
        rows = (client.schema("storeops").table("employees")
                .select("id,name,role").eq("org_id", org_id).order("id").execute().data) or []
    except Exception:
        return []
    for e in rows:
        nm = _canon_person(e.get("name"))
        if not nm:
            continue
        groups.setdefault(nm, []).append(
            {"id": e.get("id"), "name": e.get("name"), "role": (e.get("role") or "").strip()})
    out = []
    for canon, rws in groups.items():
        distinct_roles = {r["role"].strip().lower() for r in rws if r["role"]}
        if len(rws) > 1:
            out.append({"canon": canon, "rows": rws,
                        "winner_role": (rws[0].get("role") or "").strip(),
                        "role_conflict": len(distinct_roles) > 1})
    return out


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
    """Paginated sales for a period (REST 1000-row cap). select * so a missing column never errors.

    raw_sales first, FALLING BACK to daily_sales_feed when raw_sales has no rows. The calculator's
    rep roster already reads the feed for the open month, so without this fallback a tenant whose
    feed→raw_sales promotion hasn't run yet gets reps LISTED but paid $0 — the roster and the money
    disagreed on what "sales" means (luxelink, 2026-07-14). When raw_sales exists it wins unchanged."""
    def _page(table):
        out, start, page = [], 0, 1000
        while True:
            try:
                rows = (client.schema("commcalc").table(table).select("*")
                        .eq("org_id", org_id).in_("period", _pvariants(period))
                        .range(start, start + page - 1).execute().data) or []
            except Exception:
                break
            out.extend(rows)
            if len(rows) < page:
                break
            start += page
        return out
    return _page("raw_sales") or _page("daily_sales_feed")


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
def preview(client, org_id, period, plan_id=None, detail=False, only_rep=None):
    """READ-ONLY: apply plan rules to a period's raw_sales. Writes nothing.

    Returns {ready, period, by_rep:[...], totals, plans, note}. If plan_id is given, that plan is applied
    to ALL reps; otherwise each rep gets the plan resolved by assignment precedence.

    detail=False / only_rep=None (the defaults used by the live calc path in _apply_new_engines) →
    output is BYTE-IDENTICAL to before. detail=True (drill-down) additionally attaches, per rep: the
    winning `assignment` + `considered` nearest-miss list (from _resolve_plan_for(explain=True)), the
    plan's tier config, and per-rule `lines` (the individual matched sale lines with date / trans_id /
    imei / mdn / product / contract_type / ext_price / gp / per-line amount) — including rules that
    matched 0 lines, so "plan attached but nothing paid" is explainable. only_rep restricts the rep
    grouping (canon or token-subset match) so a single-rep drill is cheap.
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
    role_by_rep = _read_employee_roles(client, org_id)   # {_canon_person(name) -> role} for scope='role'

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

    # only_rep (drill-down): restrict the rep grouping to the one rep — canon match, else token subset
    # (mirrors the tolerant match the commission-drill endpoint uses). Default None → no filter.
    if only_rep:
        orc = _canon_person(only_rep)
        otok = set(re.sub(r"[^a-z0-9]+", " ", str(only_rep or "").lower()).split())

        def _rep_pick(nm):
            if _canon_person(nm) == orc:
                return True
            st = set(re.sub(r"[^a-z0-9]+", " ", str(nm or "").lower()).split())
            return bool(st and otok) and (st <= otok or otok <= st)
        reps = {k: e for k, e in reps.items() if _rep_pick(e["name"])}

    out_rows, grand = [], 0.0
    for key, e in reps.items():
        store = e["store"]
        market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
        rep_role = role_by_rep.get(_canon_person(e["name"]))
        resolution = None
        if detail:
            resolution = _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role, explain=True)
            resolved = resolution.get("plan")
        else:
            resolved = _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role)
        plan = forced_plan or resolved
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
            if detail and "match_field" not in rb:
                rb["match_field"] = rule.get("match_field") or "any"
                rb["match_op"] = rule.get("match_op") or "equals"
                rb["match_value"] = rule.get("match_value")
                rb["amount"] = safe_float(rule.get("amount"))
                rb["pct"] = safe_float(rule.get("pct"))
            for row in e["lines"]:
                if not _rule_matches(row, rule):
                    continue
                rb["matched_lines"] += 1
                ldet = None
                if detail:
                    ldet = {"date": str(row.get("trans_date") or "")[:10],
                            "trans_id": str(row.get("trans_id") or "").strip(),
                            "imei": _norm_mdn(row.get("serial_1")), "mdn": _norm_mdn(row.get("mdn")),
                            "product": row.get("product_desc"), "contract_type": row.get("contract_type"),
                            "ext_price": round(safe_float(row.get("ext_price")), 2),
                            "gp": round(safe_float(row.get("gp")), 2),
                            "qualifies": bool(qualifies), "amount": 0.0}
                    rb.setdefault("lines", []).append(ldet)
                if not qualifies:
                    continue
                rb["qualifying_units"] += 1
                if kind == "flat":
                    flat_pending[rid] = safe_float(rule.get("amount"))  # once per rep
                    if ldet is not None:
                        ldet["amount"] = None          # flat bonus: paid once per rep, not per line
                        ldet["flat_once"] = True
                    continue
                pay = _line_payout(row, rule, mrc_by_mdn, mrc_by_sub, cost_by_pid)
                rb["payout"] = round(rb["payout"] + pay, 2)
                if ldet is not None:
                    ldet["amount"] = pay
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
            # default: only rules that matched ≥1 line. detail: EVERY rule (so a rule that matched
            # nothing is still shown, to explain why it paid $0).
            "rules": sorted([rb for rb in rule_breakdown.values() if (detail or rb["matched_lines"])],
                            key=lambda x: -(x.get("payout") or 0)),
        })
        if detail:
            out_rows[-1]["assignment"] = (resolution or {}).get("winner")
            out_rows[-1]["considered"] = (resolution or {}).get("considered")
            out_rows[-1]["base_tier_metric"] = (plan.get("base_tier_metric") or "none")
            out_rows[-1]["tiers"] = [{"min_count": int(t.get("min_count") or 0),
                                      "multiplier": safe_float(t.get("multiplier"))}
                                     for t in (plan.get("tiers") or [])]

    out_rows.sort(key=lambda x: -(x.get("total_payout") or 0))
    return {"ready": True, "period": period, "by_rep": out_rows,
            "totals": {"payout": round(grand, 2), "reps": len(out_rows),
                       "sale_lines": len(valid), "plans": len(plans)},
            "note": None}
