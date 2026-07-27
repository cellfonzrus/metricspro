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
# ONE shared voided token set for pay + display (owner 2026-07-25) — see gp_report.VOID_TOKENS.
from app.modules.commcalc.gp_report import is_voided as _is_voided, VOID_TOKENS as _VOID_TOKENS

ORG_ID = "00000000-0000-0000-0000-000000000001"

MATCH_FIELDS = {"contract_type", "tender_type", "department", "category",
                "product_desc", "sku", "trans_type", "any",
                # SYNTHETIC (migs 230/231): 'accessory' resolves to 'yes'/'no' per line via the shared
                # AccessoryClassifier (dept/category/keyword OR catalog category). Lets a Commission Plan
                # rule PAY on the accessory classification through the EXISTING engine (owner 2026-07-24).
                # Stamped in preview() ONLY when a rule actually uses it → inert + byte-identical otherwise.
                "accessory",
                # SYNTHETIC (mig 232): 'activation_bucket' resolves each line to 'premium'/'upgrade'/'byod'
                # (or '') using the TENANT'S OWN existing display config — accessory_config.contract_type_map
                # (mig 213) + accessory_config.activation_rules (mig 224) — so a plan can pay/tier on
                # activations even when raw_sales.contract_type is BLANK (~77% of luxelink's July lines) or
                # carries a carrier-specific label the hard-coded classifier never saw. NOT a new classifier:
                # it calls the SAME resolver the Sales Report / Exec MTD / Daily Targets already use.
                # Stamped in preview() ONLY when a rule/tier actually uses it → inert + byte-identical
                # otherwise. MONEY-ADJACENT: pay moves only after an owner writes such a rule AND recalcs.
                "activation_bucket"}
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
    """True if this sale line matches the rule's field/op/value.

    CONTRACT-TYPE RESOLUTION (mig 232, opt-in): when the row carries a `_ct_resolved` stamp — which
    preview() writes ONLY for a tenant whose commission_org_config.plan_ct_resolution == 'mapped' — a
    match_field='contract_type' rule matches the RAW value OR that resolved activation bucket
    ('premium'/'upgrade'/'byod'). Without the stamp (every tenant by default, and every non-preview caller
    such as the installment engine's trigger matcher) this is BYTE-IDENTICAL to before."""
    field = (rule.get("match_field") or "any").strip().lower()
    if field == "any":
        return True
    op = (rule.get("match_op") or "equals").strip().lower()
    want = (rule.get("match_value") or "").strip().lower()
    have = _line_value(row, field)
    candidates = [have]
    if field == "contract_type" and "_ct_resolved" in row:
        alt = str(row.get("_ct_resolved") or "").strip().lower()
        if alt and alt != have:
            candidates.append(alt)
    for have in candidates:
        if op == "contains":
            if want != "" and want in have:
                return True
        elif op == "in":
            opts = [x.strip() for x in want.split(",") if x.strip()]
            if have in opts:
                return True
        elif have == want:      # equals (default)
            return True
    return False


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


def _plan_pay_config(client, org_id):
    """Per-tenant PAY-path options (mig 232). Today: plan_ct_resolution 'raw' (default, byte-identical) |
    'mapped'. Degrades to the defaults when the column/table/row is absent — never raises."""
    out = {"plan_ct_resolution": "raw"}
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("plan_ct_resolution").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return out
    if rows:
        v = str(rows[0].get("plan_ct_resolution") or "raw").strip().lower()
        out["plan_ct_resolution"] = v if v in ("raw", "mapped") else "raw"
    return out


def _read_ct_classification_config(client, org_id):
    """(contract_type_map, activation_rules) — the tenant's EXISTING display-classification config
    (accessory_config, migs 213 + 224). Read in its OWN defensive query so a missing column/table degrades
    to ({}, []) = the hard-coded classifier only. Never raises."""
    ct_map, rules = {}, []
    for cols in ("contract_type_map,activation_rules", "contract_type_map"):
        try:
            rows = (client.schema("commcalc").table("accessory_config").select(cols)
                    .eq("org_id", org_id).limit(1).execute().data) or []
        except Exception:
            continue
        if rows:
            cm = rows[0].get("contract_type_map")
            if isinstance(cm, dict):
                ct_map = {str(k).strip().lower(): str(v).strip().lower() for k, v in cm.items()}
            ar = rows[0].get("activation_rules")
            if isinstance(ar, list):
                rules = [r for r in ar if isinstance(r, dict)]
        break
    return ct_map, rules


def _activation_buckets(client, org_id, rows):
    """[bucket|None] parallel to `rows` — each sale line's activation bucket ('premium'|'upgrade'|'byod').

    Reuses the SHARED display resolver (router._resolve_ct_bucket honours the tenant's mig-213
    contract_type_map; router._blank_ct_bucket_map applies the tenant's mig-224 transaction-level
    activation_rules to BLANK-contract_type transactions). This is deliberately NOT a sixth classifier —
    it is the same one the Sales Report / Executive MTD / Daily Targets already use, so what a tenant sees
    as an activation and what a plan can pay on cannot disagree. Lazy import (router imports this module);
    on any import failure it falls back to the code classifier alone. Never raises."""
    ct_map, rules = _read_ct_classification_config(client, org_id)
    try:
        from app.modules.commcalc.router import _resolve_ct_bucket, _blank_ct_bucket_map
    except Exception:
        from app.modules.commcalc.calculator import classify_contract_type as _cc

        def _resolve_ct_bucket(ct, cm=None):
            if cm:
                b = cm.get(str(ct or "").strip().lower())
                if b:
                    return None if b == "none" else b
            return _cc(ct)

        def _blank_ct_bucket_map(_rows, _cm, _rules):
            return {}
    try:
        by_tid = _blank_ct_bucket_map(rows, ct_map, rules) if rules else {}
    except Exception:
        by_tid = {}
    # ONE LINE PER RESCUED TRANSACTION. The blank-contract_type rescue (mig 224) classifies a whole
    # TRANSACTION from several lines (device line + rate-plan line + SIM line), so stamping every line of
    # it would make a flat-per-unit rule pay 2-3x for ONE activation. A labelled contract_type behaves
    # per-line exactly as it always has (the POS stamps the label on the line it belongs to); only the
    # RESCUE is collapsed to a single representative line, chosen by a VALUE-BASED key (highest ext_price,
    # then product/sku/serial/mdn) so the same file always picks the same line regardless of row order.
    # Consequence to know when configuring: a per-unit rule on activation_bucket pays ONCE per rescued
    # activation; a %-of-GP style rule would read only that representative line's GP, so % rules should
    # keep keying on department/category/product, not on the bucket.
    rescue_rep = {}
    if by_tid:
        best = {}
        for i, r in enumerate(rows):
            t = str(r.get("trans_id") or "").strip()
            if not t or t not in by_tid:
                continue
            key = (-safe_float(r.get("ext_price")), str(r.get("product_desc") or ""),
                   str(r.get("sku") or ""), str(r.get("serial_1") or ""), str(r.get("mdn") or ""))
            if t not in best or key < best[t][0]:
                best[t] = (key, i)
        rescue_rep = {t: v[1] for t, v in best.items()}
    out = []
    for i, r in enumerate(rows):
        try:
            b = _resolve_ct_bucket(str(r.get("contract_type") or ""), ct_map)
        except Exception:
            b = None
        if not b:
            t = str(r.get("trans_id") or "").strip()
            if t and rescue_rep.get(t) == i:
                b = by_tid.get(t)
        out.append(b or None)
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


def _tier_basis(plan):
    """The plan's tier COUNT BASIS (mig 232). 'rule_units' (NULL/unknown/pre-migration) = the legacy count
    (matched qualifying rule LINES summed across rules). 'lines' / 'transactions' = count the lines the
    plan's own tier matcher selects. PURE."""
    b = str(plan.get("tier_count_basis") or "").strip().lower()
    return b if b in ("lines", "transactions") else "rule_units"


def _tier_metric_count(plan, lines):
    """(count, basis) for the plan's TIER METRIC over one rep's sale lines (mig 232).

    Legacy basis ('rule_units') returns (None, 'rule_units') — the caller keeps using its own
    qualifying-unit total, so a plan without the new columns is BYTE-IDENTICAL.

    'lines' / 'transactions': the plan's tier_match_field/op/value select which lines count (same matcher
    vocabulary as a commission_rule, including the synthetic 'accessory' / 'activation_bucket' fields), and
    'transactions' de-duplicates by trans_id — so "30 activations" means 30 ACTIVATIONS, not 30 line items
    on three sales. PURE."""
    basis = _tier_basis(plan)
    if basis == "rule_units":
        return None, basis
    matcher = {"match_field": plan.get("tier_match_field") or "any",
               "match_op": plan.get("tier_match_op") or "equals",
               "match_value": plan.get("tier_match_value")}
    hits = [r for r in lines if _rule_matches(r, matcher)]
    if basis == "transactions":
        return len({str(r.get("trans_id") or "").strip() for r in hits
                    if str(r.get("trans_id") or "").strip()}), basis
    return len(hits), basis


def _tier_multiplier(plan, qualifying_units):
    """Highest min_count ≤ qualifying_units wins → its multiplier (1.0 if no tiers / no metric).

    BELOW-LOWEST-TIER (mig 232): when the rep reaches NO tier, the historic result is 1.0 — a plan whose
    lowest tier is "30 units → 0.5×" silently pays FULL to a rep who sold 5. `tier_below_min_multiplier`
    makes that floor explicit; NULL/absent keeps the historic 1.0 (byte-identical)."""
    metric = (plan.get("base_tier_metric") or "none").strip().lower()
    tiers = plan.get("tiers") or []
    if metric in ("", "none") or not tiers:
        return 1.0
    best_mult, best_min = 1.0, -1
    for t in tiers:
        mc = int(t.get("min_count") or 0)
        if qualifying_units >= mc and mc >= best_min:
            best_min, best_mult = mc, safe_float(t.get("multiplier")) or 1.0
    if best_min < 0:
        below = plan.get("tier_below_min_multiplier")
        if below is not None and str(below).strip() != "":
            return safe_float(below)
    return best_mult


def _apply_rule_overrides(plans, overrides):
    """A DEEP COPY of `plans` with each rule's matcher replaced per `overrides`. PURE (no I/O, no mutation
    of the input). Keys are rule ids (str-compared); a rule id not present is left exactly as loaded.

    Supported per-rule keys: match_field / match_op / match_value / qualifies, plus `disabled: true`
    which removes the rule entirely (the "what if this rule did not exist" case). An unknown match_field
    is REJECTED (kept as-is) so a what-if can never model a matcher the engine cannot actually run.

    EVERY rule dict in the result is a fresh object, including the ones NOT overridden (Gate-1 N2). Sharing
    a non-overridden rule by reference was harmless today — preview() only reads rules — but it hands a
    what-if caller a live handle on the plan structure the money path loads, and one `rules[i]['amount']=…`
    in some future caller would silently rewrite a stored rule through the preview path. Copy-on-read
    removes the sharp edge entirely; the cost is one dict per rule per what-if call."""
    ov = {str(k): (v or {}) for k, v in (overrides or {}).items()}
    out = []
    for p in plans:
        rules = []
        for r in (p.get("rules") or []):
            o = ov.get(str(r.get("id")))
            if not o:
                rules.append(dict(r))          # N2: never share a stored rule dict with the caller
                continue
            if o.get("disabled"):
                continue
            nr = dict(r)
            if "match_field" in o:
                mf = str(o.get("match_field") or "any").strip().lower()
                if mf in MATCH_FIELDS:
                    nr["match_field"] = mf
            if "match_op" in o:
                nr["match_op"] = str(o.get("match_op") or "equals").strip().lower()
            if "match_value" in o:
                nr["match_value"] = o.get("match_value")
            if "qualifies" in o:
                nr["qualifies"] = bool(o.get("qualifies"))
            rules.append(nr)
        # tiers/assignments get the same copy-on-read treatment — a what-if caller must not be able to
        # reach ANY stored config object through this structure.
        out.append({**p, "rules": rules,
                    "tiers": [dict(t) for t in (p.get("tiers") or [])],
                    "assignments": [dict(a) for a in (p.get("assignments") or [])]})
    return out


# ── preview ────────────────────────────────────────────────────────────────────────────────────
def preview(client, org_id, period, plan_id=None, detail=False, only_rep=None, coverage=False,
            rule_overrides=None):
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

    coverage=True (diagnostics only; mig 232) additionally returns a top-level "coverage" block: every
    seller with sales but NO plan attached (today they are silently skipped → a legit-looking $0), the
    lines a covered rep's plan matched with NO rule, blank/unrecognised Contract Type counts, and
    plain-language PLAN WARNINGS (tiers configured with metric 'none', tiers with no rule marked
    "tiered", a contract_type-keyed rule on a tenant whose lines have blank Contract Type). It NEVER
    changes by_rep/totals — the money output with coverage=False is byte-identical.
    """
    # MONEY-PATH FRESHNESS (Gate-1 rework finding 1b): preview() is what a plan-driven payout is computed
    # from, and the shared accessory/classification memo is bounded only by a TTL — `get_supabase()` is a
    # process-wide singleton, so there is no request boundary to expire it, and a hand-run SQL-Editor edit
    # fires no invalidate at all. Drop this org's memo at ENTRY so the classifier below is built from a
    # FRESH read every time. Cheap (a dict pop) and it runs once per preview, not per line.
    try:
        from app.modules.commcalc import accessory_catalog as _accat_fresh
        _accat_fresh.invalidate(org_id)
    except Exception as _cfe:
        print(f"WARN preview could not refresh accessory config cache: {_cfe}")
    plans, ready = _load_plans(client, org_id)
    if not ready:
        return {"ready": False, "period": period, "by_rep": [], "totals": {},
                "note": "Migration 059_commission_plans.sql not applied — no preview."}
    # WHAT-IF MATCHER OVERRIDE (read-only; mod-commission 2026-07-27). `rule_overrides` is a
    # {rule_id -> {match_field?, match_op?, match_value?, qualifies?, disabled?}} map applied to the
    # IN-MEMORY plan copy only — nothing is written and no other caller passes it, so with the default
    # None this whole block is skipped and preview() is BYTE-IDENTICAL to before. It exists so a
    # money-touching rule re-key can be measured (per rep, per line) BEFORE anyone edits the config.
    if rule_overrides:
        plans = _apply_rule_overrides(plans, rule_overrides)
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
    # only un-voided, non-return lines qualify (same gate as the live calculator). VOIDED uses the
    # SHARED token set (owner 2026-07-25) so a 'true'/'1'/'void' line can never be paid while the Sales
    # Report excludes it.
    valid = [r for r in sales
             if not _is_voided(r.get("voided"))
             and str(r.get("trans_type", "") or "").strip() != "Return"]

    # SYNTHETIC 'accessory' match_field (migs 230/231): stamp each line 'yes'/'no' via the shared
    # AccessoryClassifier so a rule with match_field='accessory' pays on the accessory classification
    # (dept/category/keyword OR the catalog category). Built + stamped ONLY when at least one rule across
    # the loaded plans actually uses it — so for EVERY existing plan (none reference 'accessory') this is a
    # complete no-op (byte-identical, zero cost). MONEY-ADJACENT: pay moves only after an owner creates such
    # a rule AND runs a recalc.
    _uses_acc = any((rule.get("match_field") or "").strip().lower() == "accessory"
                    for p in plans for rule in (p.get("rules") or []))
    if _uses_acc:
        try:
            from app.modules.commcalc import accessory_catalog as _accat
            _clf = _accat.build(client, org_id)
        except Exception:
            _clf = None
        if _clf is not None:
            for r in valid:
                r["accessory"] = "yes" if _clf.is_accessory_row(r) else "no"

    # SYNTHETIC 'activation_bucket' + optional 'mapped' contract-type resolution (mig 232). Both reuse the
    # tenant's EXISTING display classification config (contract_type_map mig 213 + activation_rules mig 224)
    # via the SHARED resolver — no new classifier. Built ONLY when a rule/tier actually references
    # 'activation_bucket' OR the tenant set plan_ct_resolution='mapped'; otherwise this whole block is a
    # no-op (no extra reads, no stamps → _rule_matches is byte-identical). MONEY-ADJACENT: nothing moves
    # until an owner writes such a rule / flips the setting AND runs a recalc.
    _pay_cfg = _plan_pay_config(client, org_id)
    _ct_mapped = (_pay_cfg.get("plan_ct_resolution") == "mapped")
    _uses_bucket = any(
        (rule.get("match_field") or "").strip().lower() == "activation_bucket"
        for p in plans for rule in (p.get("rules") or [])) or any(
        (p.get("tier_match_field") or "").strip().lower() == "activation_bucket" for p in plans)
    _bucket_lines = 0
    if _uses_bucket or _ct_mapped:
        _buckets = _activation_buckets(client, org_id, valid)
        for r, b in zip(valid, _buckets):
            if b:
                _bucket_lines += 1
            if _uses_bucket:
                r["activation_bucket"] = b or ""
            if _ct_mapped and b:
                r["_ct_resolved"] = b

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
    unassigned = []
    for key, e in reps.items():
        store = e["store"]
        market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
        rep_role = role_by_rep.get(_canon_person(e["name"]))
        resolution = None
        if detail:
            resolution = _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role, explain=True)
            plan = forced_plan or resolution.get("plan")
        else:
            # money path: EXACTLY the original lazy short-circuit — when plan_id forces a plan,
            # _resolve_plan_for is never called (so the delta vs the pre-drill engine is exactly zero,
            # incl. the case where a non-numeric assignment field would make the resolver raise).
            plan = forced_plan or _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role)
        if not plan:
            # COVERAGE (mig 232): a seller with real sales and NO plan attached is skipped here — which is
            # exactly how a carrier_mode='plan' tenant ends up with a legitimate-looking $0 for that rep.
            # Record them so the gap is VISIBLE instead of silent. No effect on by_rep/totals.
            if coverage:
                unassigned.append({
                    "rep": e["name"], "store": store, "market": market,
                    "role": rep_role or None, "lines": len(e["lines"]),
                    "transactions": len({str(r.get("trans_id") or "").strip() for r in e["lines"]
                                         if str(r.get("trans_id") or "").strip()}),
                    "ext_price": round(sum(safe_float(r.get("ext_price")) for r in e["lines"]), 2),
                    "reason": ("no commission-plan assignment matched this rep "
                               "(employee > role > store > market > default all missed)"),
                })
            continue
        rules = plan.get("rules") or []

        rule_breakdown = {}   # rule_id -> {label, kind, matched, qualifying, payout, tiered}
        matched_ids = set() if coverage else None   # coverage: which lines ANY rule matched
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
                if matched_ids is not None:
                    matched_ids.add(id(row))
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

        # TIER ATTAINMENT (mig 232): a plan may DEFINE what its tier counts (distinct activation
        # transactions, matched lines, …). Legacy plans return (None, 'rule_units') → the historic
        # qualifying-unit total is used, byte-identical.
        _tier_n, _tier_basis_used = _tier_metric_count(plan, e["lines"])
        tier_units = qualifying_units if _tier_n is None else _tier_n
        mult = _tier_multiplier(plan, tier_units)
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
        if coverage:
            _un = [r for r in e["lines"] if id(r) not in matched_ids]
            out_rows[-1]["tier_units"] = tier_units
            out_rows[-1]["tier_basis"] = _tier_basis_used
            out_rows[-1]["unmatched_lines"] = len(_un)
            out_rows[-1]["unmatched_ext_price"] = round(
                sum(safe_float(r.get("ext_price")) for r in _un), 2)
            out_rows[-1]["unmatched_sample"] = [
                {"trans_id": str(r.get("trans_id") or "").strip(),
                 "date": str(r.get("trans_date") or "")[:10],
                 "department": r.get("department"), "category": r.get("category"),
                 "contract_type": r.get("contract_type"), "product": r.get("product_desc"),
                 "ext_price": round(safe_float(r.get("ext_price")), 2)} for r in _un[:5]]
        if detail:
            out_rows[-1]["assignment"] = (resolution or {}).get("winner")
            out_rows[-1]["considered"] = (resolution or {}).get("considered")
            out_rows[-1]["base_tier_metric"] = (plan.get("base_tier_metric") or "none")
            out_rows[-1]["tiers"] = [{"min_count": int(t.get("min_count") or 0),
                                      "multiplier": safe_float(t.get("multiplier"))}
                                     for t in (plan.get("tiers") or [])]

    out_rows.sort(key=lambda x: -(x.get("total_payout") or 0))
    out = {"ready": True, "period": period, "by_rep": out_rows,
           "totals": {"payout": round(grand, 2), "reps": len(out_rows),
                      "sale_lines": len(valid), "plans": len(plans)},
           "note": None}
    if coverage:
        unassigned.sort(key=lambda x: -(x.get("ext_price") or 0))
        out["coverage"] = _coverage_block(plans, valid, out_rows, unassigned, _pay_cfg,
                                          _uses_bucket or _ct_mapped, _bucket_lines)
    return out


def _coverage_block(plans, valid, out_rows, unassigned, pay_cfg, bucket_built, bucket_lines):
    """Diagnostics for "why doesn't my plan pay what I configured?" — PURE (everything passed in) and
    money-free: it reads the already-computed rows and never changes a payout. Returns
    {unassigned_reps, unmatched, contract_type, plan_warnings, settings}."""
    blank_ct = sum(1 for r in valid if not str(r.get("contract_type") or "").strip())
    ct_values = {}
    for r in valid:
        c = str(r.get("contract_type") or "").strip()
        if c:
            ct_values[c] = ct_values.get(c, 0) + 1
    warnings = []
    for p in plans:
        if not p.get("is_active", True):
            continue
        nm = p.get("name")
        tiers = p.get("tiers") or []
        rules = p.get("rules") or []
        metric = (p.get("base_tier_metric") or "none").strip().lower()
        if tiers and metric in ("", "none"):
            warnings.append({
                "plan": nm, "severity": "high", "code": "tiers_without_metric",
                "message": (f"'{nm}' has {len(tiers)} tier(s) but its Tier metric is 'none' — the tier "
                            f"multiplier is FORCED to 1.0, so the tiers never change anyone's pay. Set the "
                            f"plan's Tier metric.")})
        if tiers and metric not in ("", "none") and not any(bool(r.get("tiered")) for r in rules):
            warnings.append({
                "plan": nm, "severity": "high", "code": "tiers_without_tiered_rule",
                "message": (f"'{nm}' has {len(tiers)} tier(s) and a tier metric, but NO rule is marked "
                            f"'Tiered' — the multiplier scales nothing. Tick 'Tiered' on the rules the "
                            f"tier should scale.")})
        if tiers and _tier_basis(p) == "rule_units":
            warnings.append({
                "plan": nm, "severity": "medium", "code": "tier_basis_legacy",
                "message": (f"'{nm}' counts tier attainment the legacy way: every qualifying rule-matched "
                            f"LINE, summed across rules (one activation that rings 3 lines counts 3; a line "
                            f"matched by two rules counts twice). Set the plan's Tier count basis to "
                            f"'transactions' with a tier matcher to count real activations.")})
        ct_rules = [r for r in rules
                    if (r.get("match_field") or "").strip().lower() == "contract_type"]
        if ct_rules and blank_ct and pay_cfg.get("plan_ct_resolution") != "mapped":
            warnings.append({
                "plan": nm, "severity": "high", "code": "ct_rules_vs_blank_ct",
                "message": (f"'{nm}' has {len(ct_rules)} rule(s) keyed on Contract Type, but {blank_ct} of "
                            f"{len(valid)} sale lines this period have a BLANK Contract Type — those lines "
                            f"can never match, so they pay $0. Either key the rules on 'activation_bucket', "
                            f"or set Contract-type resolution to 'mapped' (Commission settings) after "
                            f"configuring the tenant's activation rules.")})
        if not rules:
            warnings.append({"plan": nm, "severity": "high", "code": "plan_without_rules",
                             "message": f"'{nm}' has no rules — every rep it covers pays $0."})
        if not (p.get("assignments") or []):
            warnings.append({"plan": nm, "severity": "medium", "code": "plan_without_assignment",
                             "message": f"'{nm}' has no assignments — it covers nobody."})
    return {
        "unassigned_reps": unassigned,
        "unassigned_count": len(unassigned),
        "unassigned_ext_price": round(sum(x.get("ext_price") or 0 for x in unassigned), 2),
        "unmatched": {
            "reps": [{"rep": r.get("rep"), "plan_name": r.get("plan_name"),
                      "unmatched_lines": r.get("unmatched_lines"),
                      "unmatched_ext_price": r.get("unmatched_ext_price"),
                      "sample": r.get("unmatched_sample")}
                     for r in out_rows if (r.get("unmatched_lines") or 0) > 0],
            "total_lines": sum((r.get("unmatched_lines") or 0) for r in out_rows),
        },
        "contract_type": {
            "sale_lines": len(valid), "blank": blank_ct,
            "blank_pct": round((blank_ct / len(valid) * 100.0), 1) if valid else 0.0,
            "values": sorted(({"value": k, "lines": v} for k, v in ct_values.items()),
                             key=lambda x: -x["lines"])[:25],
            "resolution": pay_cfg.get("plan_ct_resolution"),
            "bucket_resolver_ran": bool(bucket_built),
            "bucket_classified_lines": bucket_lines,
        },
        "plan_warnings": warnings,
        "settings": dict(pay_cfg),
    }
