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
PAYOUT_KINDS = {"flat_per_unit", "pct_mrc", "pct_gp", "pct_price", "pct_price_over_cost", "flat"}


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


def _assignment_miss_reason(scope, val, rn_canon, rr, sv_store, sv_mkt, scope_value_raw, skeys=None):
    """Plain-language 'why this assignment did NOT attach to the rep', for the drill-down nearest-miss
    list. PURE. It only NARRATES a branch of the SAME predicate `_resolve_plan_for` evaluates — it is
    never a second matching implementation (the caller passes the exact `ok`; this just explains ¬ok).

    `skeys` (mig 249, store_resolution='alias') is the EXTRA set of store keys the predicate also
    accepted for a store-scope assignment (the alias-resolved store_code / canonical address). Default
    None/empty => the narration string is byte-identical to before."""
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
        if skeys:
            return (f"store scope '{val}' != rep store '{sv_store or '(none)'}' "
                    f"(also tried {', '.join(sorted(skeys))})")
        return f"store scope '{val}' != rep store '{sv_store or '(none)'}'"
    if scope == "market":
        if not val:
            return "market assignment has no value"
        return f"market scope '{val}' != rep market '{sv_mkt or '(none)'}'"
    return f"scope '{scope}' did not match"


def _resolve_plan_for(rep_name, store, market, plans, rep_role=None, explain=False, store_keys=None,
                      identity_map=None):
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
    `considered` is EVERY assignment evaluated, each with matched:bool + a miss `reason`.

    `store_keys` (mig 249) is an OPTIONAL extra set of already-lower-cased store keys a STORE-scope
    assignment may also match — the alias-resolved store_code and canonical store_address for the rep's
    raw POS store string. It is passed ONLY when the tenant set store_resolution='alias'. The default
    None makes `skeys` empty, so `val in skeys` is always False and the predicate — and therefore every
    payout — is BYTE-IDENTICAL to before.

    `identity_map` (luxelink money-path name-bridge) is an OPTIONAL deterministic POS->roster identity map
    {POS salesperson name (UPPER) -> roster name} — the SAME map the calc already loads from
    commcalc.name_map / rep_aliases. EMPLOYEE-scope assignments store the ROSTER name, but `rep_name` here
    is the rep's POS salesperson string; when they differ the exact-canon compare silently misses and the
    rep is skipped ($0). With a map supplied, an assignment pinned under the rep's roster name ALSO matches
    their POS sales via a 1:1, explicit bridge — never fuzzy. The default None/empty makes the bridge inert
    so the employee compare — and every payout — is BYTE-IDENTICAL to before. SAFETY: the bridge only ever
    matches the roster name the map EXPLICITLY connects this POS name to; it can never attach a plan to a
    rep the map does not connect, and a no-bridge case falls straight back to today's exact compare."""
    SCOPE_RANK = {"employee": 4, "role": 3, "store": 2, "market": 1, "default": 0}
    rn_canon = _canon_person(rep_name)
    # Deterministic POS->roster bridge (see `identity_map` above). None when no map is supplied or the map
    # has no entry for this POS name (or it maps back to the same canon) → the employee compare is unchanged.
    rn_bridged_canon = None
    if identity_map:
        _bridged = identity_map.get(str(rep_name or "").strip().upper())
        if _bridged:
            _bc = _canon_person(_bridged)
            if _bc and _bc != rn_canon:
                rn_bridged_canon = _bc
    rr = (rep_role or "").strip().lower()
    sv_store, sv_mkt = (store or "").strip().lower(), (market or "").strip().lower()
    skeys = {str(k).strip().lower() for k in (store_keys or ()) if str(k or "").strip()}
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
                _a_canon = _canon_person(a.get("scope_value"))
                ok = bool(val) and (_a_canon == rn_canon
                                    or (rn_bridged_canon is not None and _a_canon == rn_bridged_canon))
            elif scope == "role":
                ok = bool(val) and bool(rr) and val == rr
            else:
                ok = ((scope == "default") or
                      (scope == "store" and val and (val == sv_store or val in skeys)) or
                      (scope == "market" and val and val == sv_mkt))
            if explain:
                considered.append({
                    "plan_id": p.get("id"), "plan_name": p.get("name"),
                    "scope": scope, "scope_value": a.get("scope_value"),
                    "priority": int(a.get("priority") or 0), "rank": SCOPE_RANK.get(scope, 0),
                    "matched": bool(ok),
                    "reason": None if ok else _assignment_miss_reason(
                        scope, val, rn_canon, rr, sv_store, sv_mkt, a.get("scope_value"), skeys),
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


def audit_flags(winner, considered, rep_market=None, known_markets=None):
    """PURE flag logic for the Plan Assignment Audit — no I/O, so it is unit-testable in isolation.

    It consumes ONLY the `winner` + `considered` structures `_resolve_plan_for(explain=True)` returns
    (the single source of truth for what pays), so the audit can never disagree with the live matcher.

    Flags returned (a list of {"flag": ..., ...} dicts):
      • no_plan          — nothing matched; the rep resolves to no plan (winner is None).
      • by_name_override — THE SILVIA CASE. The winner is an EMPLOYEE-scope (by-name) pin AND a
                           DIFFERENT plan ALSO matched this rep on store or market. The by-name pin
                           (rank 4) outranks the location plan (rank 2/1), so fixing the rep's store /
                           market does nothing while the pin stands. Carries `overridden_plans`
                           (the location-based plan(s) the pin beat), so the operator sees exactly what
                           to remove.
      • location_mismatch — LOW-CONFIDENCE, best-effort text hint. The winning plan's NAME names a
                           market string other than the rep's own resolved market. Never a matcher —
                           purely a "does this look wrong?" nudge computed from `known_markets`.
    """
    flags = []
    if winner is None:
        flags.append({"flag": "no_plan"})
        return flags
    win_scope = (winner.get("scope") or "").strip().lower()
    win_pid = winner.get("plan_id")
    if win_scope == "employee":
        overridden, seen = [], set()
        for c in (considered or []):
            if not c.get("matched"):
                continue
            cscope = (c.get("scope") or "").strip().lower()
            if cscope in ("store", "market") and c.get("plan_id") != win_pid:
                k = (c.get("plan_id"), cscope)
                if k in seen:
                    continue
                seen.add(k)
                overridden.append({"plan_name": c.get("plan_name"), "scope": cscope,
                                   "scope_value": c.get("scope_value")})
        if overridden:
            flags.append({"flag": "by_name_override", "overridden_plans": overridden})
    # optional, low-confidence: the winning plan's NAME names a market that is not the rep's own.
    pname = (winner.get("plan_name") or "").lower()
    rep_mkt = (rep_market or "").strip().lower()
    if pname and known_markets:
        named = sorted({str(m).strip() for m in known_markets
                        if str(m or "").strip() and str(m).strip().lower() in pname})
        # only a mismatch if the name names SOME market, none of which is the rep's own market
        if named and not any(m.lower() == rep_mkt for m in named) and \
                (not rep_mkt or rep_mkt not in pname):
            flags.append({"flag": "location_mismatch", "plan_names_market": named,
                          "rep_market": rep_market or None})
    return flags


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
    """Per-tenant PAY-path options. Today:
      • plan_ct_resolution (mig 232) 'raw' (default, byte-identical) | 'mapped'
      • store_resolution   (mig 249) 'exact' (default, byte-identical) | 'alias'
      • activation_source  (mig 296) 'raw_sales' (default/NULL, byte-identical) | 'activation_details'

    Degrades to the defaults when the table/row is absent — never raises. The column list is tried
    WIDEST-FIRST and falls back to the narrow ones: a single combined select would make the whole read
    fail on a pre-mig-296/249 database and silently revert a tenant's plan_ct_resolution='mapped' back to
    'raw' — i.e. a MONEY change caused by an unrelated missing column. Each fallback keeps the earlier
    migration's setting readable on its own, so a missing activation_source column degrades to
    'raw_sales' (today's behaviour) without disturbing the mig-232 / mig-249 reads."""
    out = {"plan_ct_resolution": "raw", "store_resolution": "exact", "activation_source": "raw_sales"}
    rows = None
    for cols in ("plan_ct_resolution,store_resolution,activation_source",
                 "plan_ct_resolution,store_resolution", "plan_ct_resolution"):
        try:
            rows = (client.schema("commcalc").table("commission_org_config")
                    .select(cols).eq("org_id", org_id).limit(1).execute().data) or []
            break
        except Exception:
            rows = None
            continue
    if rows:
        v = str(rows[0].get("plan_ct_resolution") or "raw").strip().lower()
        out["plan_ct_resolution"] = v if v in ("raw", "mapped") else "raw"
        s = str(rows[0].get("store_resolution") or "exact").strip().lower()
        out["store_resolution"] = s if s in ("exact", "alias") else "exact"
        a = str(rows[0].get("activation_source") or "raw_sales").strip().lower()
        out["activation_source"] = a if a in ("raw_sales", "activation_details") else "raw_sales"
    return out


# ── COVERAGE IDENTITY BRIDGE (mod-commission 2026-07-28) ─────────────────────────────────────────
# Everything in this block is reached ONLY from preview(coverage=True) — the coverage=False money path
# never calls any of it — EXCEPT the alias store resolution, which is gated on the tenant's own
# store_resolution setting (default 'exact' = today's behaviour, byte-identical).
#
# WHY IT EXISTS: the coverage panel listed 15 sellers as "no plan attached" with a BLANK market and no
# role, while the owner had assigned all of them. Three separate identity bridges silently fail:
#   1. NAME   — assignments store the ROSTER value (epay_salesperson || name); the engine compares it to
#               raw_sales.salesperson via _canon_person (comma-flip + casefold, deliberately NOT fuzzy).
#               "Sri ram, Nivas" vs a roster "Nivas Sriram" is a silent miss, and the bulk-assign UI
#               still shows "current plan ✓" because it compares roster-side to roster-side.
#   2. ROLE   — _read_employee_roles keys on the roster NAME column only, so the same miss also erases
#               the rep's role → a scope='role' assignment can never attach.
#   3. STORE  — _read_store_market reads commcalc.store_mapping ONLY (exact lower-cased address/code).
#               The /store-match alias table (commcalc.store_aliases) is never consulted, so a POS store
#               string that differs from store_mapping.store_address yields a BLANK market and a
#               store/market-scope assignment can never attach.
# The helpers below NARRATE all three (Part A) and, behind the store_resolution setting, can BRIDGE the
# third (Part B). None of them is a new matcher: candidate scoring is a diagnostic hint only, and the
# "would this attach?" preview re-runs the REAL _resolve_plan_for.

# POS placeholder words that make a "seller" look like a till/terminal rather than a person
# ("Office, Back"). A HINT ONLY — nothing is hidden or excluded from this list; a tenant confirms real
# artifacts through commission_org_config.coverage_excluded_sellers (mig 248), and may replace this list
# through coverage_artifact_hints (SAP rule: no hard-coded roster semantics).
DEFAULT_ARTIFACT_HINTS = [
    "office", "back office", "backoffice", "admin", "administrator", "store", "house", "system",
    "test", "testing", "training", "demo", "sample", "pos", "register", "till", "counter", "kiosk",
    "cashier", "default", "unknown", "unassigned", "none", "n/a", "na", "employee", "staff", "user",
]


def _name_tokens(s):
    """Alphanumeric word tokens of a canonicalized person-name. PURE."""
    return set(re.sub(r"[^a-z0-9]+", " ", _canon_person(s)).split())


def _name_squash(s):
    """A canonicalized person-name with every non-alphanumeric character removed. PURE.

    This is what catches the owner's real case: POS "Sri ram, Nivas" -> "nivas sri ram" -> "nivassriram"
    and roster "Nivas Sriram" -> "nivassriram" are the SAME string once spacing is discarded. It is used
    ONLY to rank remediation candidates — never to match a payout."""
    return re.sub(r"[^a-z0-9]+", "", _canon_person(s))


def _name_score(a, b):
    """0..1 similarity between two person-names, for the "did you mean" list ONLY. PURE.
    1.0 = identical once punctuation/spacing is discarded; otherwise Jaccard over word tokens."""
    sa, sb = _name_squash(a), _name_squash(b)
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 1.0
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / float(len(ta | tb)), 3)


def _roster_candidates(rep_name, roster, top=3, floor=0.2):
    """Up to `top` roster rows that RESEMBLE a POS seller name, best first. PURE, diagnostic only.
    Each entry reports which roster column produced the score so the remediation can be exact."""
    out = []
    for e in roster:
        s_name = _name_score(rep_name, e.get("name"))
        s_epay = _name_score(rep_name, e.get("epay_salesperson")) if e.get("epay_salesperson") else 0.0
        score = max(s_name, s_epay)
        if score < floor:
            continue
        out.append({"employee_id": e.get("id"), "name": e.get("name"),
                    "role": (e.get("role") or "").strip() or None,
                    "email": (e.get("email") or "").strip() or None,
                    "epay_salesperson": (e.get("epay_salesperson") or "").strip() or None,
                    "home_store": (e.get("home_store") or "").strip() or None,
                    "is_active": bool(e.get("is_active", True)),
                    "score": score, "matched_on": "epay_salesperson" if s_epay > s_name else "name"})
    out.sort(key=lambda x: (-x["score"], str(x.get("name") or "")))
    return out[:top]


def _read_employee_roster(client, org_id):
    """The org's storeops roster rows the coverage diagnosis needs. COVERAGE-ONLY (never called on the
    money path) — the pay path keeps using _read_employee_roles, which is untouched.

    Ordered by id so candidate ranking is deterministic across runs. Returns [] on any failure (the
    diagnosis then honestly reports the roster as unavailable instead of blaming the name)."""
    for cols in ("id,name,role,email,epay_salesperson,home_store,is_active", "id,name,role"):
        try:
            return (client.schema("storeops").table("employees").select(cols)
                    .eq("org_id", org_id).order("id").execute().data) or []
        except Exception:
            continue
    return []


def _coverage_config(client, org_id):
    """Tenant-configurable coverage posture (mig 248) — POS-artifact sellers the owner has confirmed are
    not commissionable, plus the artifact word list. Its OWN defensive read so a missing column can never
    disturb _plan_pay_config (which is money-adjacent). Never raises.

    `excluded_sellers` NEVER changes pay: it only moves a $0 seller out of the 'no plan attached' list
    into a visible 'excluded' note. Nothing is silently hidden."""
    out = {"excluded_sellers": [], "artifact_hints": list(DEFAULT_ARTIFACT_HINTS), "ready": False}
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("coverage_excluded_sellers,coverage_artifact_hints")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return out
    out["ready"] = True
    if rows:
        ex = rows[0].get("coverage_excluded_sellers")
        if isinstance(ex, list):
            out["excluded_sellers"] = [str(x).strip() for x in ex if str(x or "").strip()]
        hints = rows[0].get("coverage_artifact_hints")
        if isinstance(hints, list) and hints:
            out["artifact_hints"] = [str(x).strip().lower() for x in hints if str(x or "").strip()]
    return out


def _store_bridge(client, org_id):
    """The org's store-alias resolution maps, or None if unavailable. Reuses router._store_maps — the
    SAME chain the Store-Matching UI (/store-match) and the Daily-Targets store resolver already use, so
    this can never become yet another store resolver. Lazy import (router imports this module).

    Returns {alias_to_code, addr_to_code, so_addr_to_code, code_to:{CODE -> {store_code,address,market}}}."""
    try:
        from app.modules.commcalc.router import _store_maps
        M = _store_maps(client, org_id)
    except Exception:
        return None
    code_to = {}
    for s in (M.get("stores") or []):
        c = str(s.get("store_code") or "").strip()
        if c and c.upper() not in code_to:
            code_to[c.upper()] = {"store_code": c,
                                  "address": str(s.get("address") or "").strip(),
                                  "market": str(s.get("market") or "").strip(),
                                  "source": s.get("source")}
    return {"alias_to_code": M.get("alias_to_code") or {},
            "addr_to_code": M.get("addr_to_code") or {},
            "so_addr_to_code": M.get("so_addr_to_code") or {},
            "code_to": code_to}


def _store_trace(store_market, bridge, raw_store):
    """PURE narration of EXACTLY what the engine tried to turn one raw POS store string into a market,
    plus what the /store-match alias table WOULD resolve it to. Changes nothing.

    `store_market` is _read_store_market's map (lower address AND lower code -> market, '' when the
    store_mapping row has a blank market) — membership is tested with `in`, not `.get()`, so
    "row exists, market blank" is reported as its own state instead of as a miss.

    Returns {raw, address_hit, code_hit, first_token, first_token_hit, exact_market, alias, alias_market,
             alias_keys, status, message}."""
    raw = str(raw_store or "").strip()
    low = raw.lower()
    first = low.split(" ")[0] if low else ""
    addr_hit = bool(low) and low in store_market
    tok_hit = bool(first) and first in store_market
    # EXACTLY the expression preview() evaluates today (`.get(x) or .get(first, "")`).
    exact_market = (store_market.get(low) or store_market.get(first, "")) if low else ""
    t = {"raw": raw, "address_hit": addr_hit, "code_hit": bool(low) and low in store_market and not addr_hit,
         "first_token": first, "first_token_hit": tok_hit,
         "exact_market": exact_market, "alias": None, "alias_market": "", "alias_keys": [],
         "status": "resolved" if exact_market else ("mapped_no_market" if (addr_hit or tok_hit) else "unmapped"),
         "message": ""}
    if bridge and low:
        hit, via = None, None
        code = bridge["alias_to_code"].get(low)
        if code:
            via = f"store alias '{raw}'"
        if not code:
            code = bridge["addr_to_code"].get(low)
            if code:
                via = "store_mapping address"
        if not code:
            code = bridge["so_addr_to_code"].get(low)
            if code:
                via = "storeops store address"
        if not code and low.upper() in bridge["code_to"]:
            code = bridge["code_to"][low.upper()]["store_code"]
            via = "the raw string already IS a store code"
        if code:
            hit = bridge["code_to"].get(str(code).strip().upper())
        if hit:
            t["alias"] = {"store_code": hit["store_code"], "address": hit["address"],
                          "market": hit["market"], "via": via, "source": hit.get("source")}
            t["alias_market"] = hit["market"]
            t["alias_keys"] = sorted({k for k in (str(hit["store_code"]).strip().lower(),
                                                  str(hit["address"]).strip().lower(), low) if k})
    if t["exact_market"]:
        t["message"] = f"market '{t['exact_market']}' resolved from commcalc.store_mapping."
    elif t["status"] == "mapped_no_market":
        t["message"] = ("a commcalc.store_mapping row exists for this store but its MARKET is blank — "
                        "set the market in Commission settings → Stores & Markets. A market-scope "
                        "assignment cannot attach until it is set.")
    elif t["alias"] and t["alias_market"]:
        t["message"] = (f"store_mapping has no row for this POS string, but the store-match table "
                        f"resolves it via {t['alias']['via']} to {t['alias']['store_code']} "
                        f"({t['alias']['address'] or 'no address'}) in market '{t['alias_market']}' — "
                        f"this only counts once Store resolution is set to 'alias' (Commission settings).")
    elif t["alias"]:
        t["message"] = (f"the store-match table resolves this POS string to "
                        f"{t['alias']['store_code']}, but that store has NO market set — set it in "
                        f"Commission settings → Stores & Markets.")
    else:
        t["message"] = ("this POS store string resolves to nothing — no commcalc.store_mapping address, "
                        "no store code, and no /store-match alias. Map it at /commcalc/store-match. "
                        "Until then the rep's market is blank and no store/market-scope assignment can "
                        "attach.")
    return t


def _artifact_flag(rep_name, hints, best_score):
    """Is this 'seller' more likely a POS artifact (a till/back-office login) than a person? A HINT ONLY —
    nothing is hidden; the owner confirms through the excluded-sellers setting. PURE."""
    toks = _name_tokens(rep_name)
    hint_set = {str(h).strip().lower() for h in (hints or []) if str(h or "").strip()}
    # a hint may be a PHRASE ("back office"): compare the whole canonical name too, not just its words,
    # so "Office, Back" -> "back office" is recognised without adding the risky single word "back".
    squashed_hints = {re.sub(r"[^a-z0-9]+", "", h) for h in hint_set}
    canon, squash = _canon_person(rep_name), _name_squash(rep_name)
    reasons, suspect, conf = [], False, "low"
    if canon and (canon in hint_set or squash in squashed_hints):
        suspect, conf = True, "high"
        reasons.append(f"'{canon}' is a POS placeholder name, not a person")
    elif toks and toks <= hint_set:
        suspect, conf = True, "high"
        reasons.append("every word in this name is a POS placeholder word "
                       f"({', '.join(sorted(toks))})")
    else:
        hit = sorted(toks & hint_set)
        short = sorted(t for t in toks if len(t) <= 2)
        if best_score < 0.34 and (hit or short):
            suspect = True
            if hit:
                reasons.append(f"contains the POS placeholder word(s) {', '.join(hit)}")
            if short:
                reasons.append(f"contains a {len(short[0])}-character word ('{short[0]}') — "
                               f"often a truncated POS entry")
            reasons.append("and no roster person resembles this name")
    return {"suspect": suspect, "confidence": conf if suspect else None, "reasons": reasons}


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


# ── ACTIVATION DETAILS as a PAY SOURCE (mig 296) — per-tenant opt-in, DEFAULT OFF ────────────────
# Maps the b2b "Activation Details" report's activation-TYPE families (New Activation / Port / BYOD /
# Tablet / Home Internet / Edge / Upgrade / Other — router._activation_details_bucket) onto the engine's
# activation_bucket vocabulary ('premium' | 'byod'). This is the SAME split calculator.classify_contract_type
# uses: a bring-your-own-device line is 'byod'; every other new-line activation family (incl. Port and the
# non-phone device categories) is 'premium'. UPGRADE and OTHER are NOT payable here — they return None and
# the line is dropped, exactly the population /activation-counts calls Total Activation (Upgrade excluded).
_AD_ENGINE_BUCKET = {
    "New Activation": "premium", "Port": "premium", "Tablet": "premium",
    "Home Internet": "premium", "Edge": "premium", "BYOD": "byod",
    "Upgrade": None, "Other": None,
}


def _activation_detail_lines(client, org_id, period, id_map=None):
    """Synthetic sale LINES built from the ingested "Activation Details" custom report, for a tenant whose
    commission_org_config.activation_source == 'activation_details' (mig 296). Reached ONLY from that
    opted-in branch of preview(); the default 'raw_sales' path never calls this, so an opted-out tenant is
    byte-identical.

    ONE LINE PER DISTINCT ACTIVATION — the resolver (router._cr_resolve_activation_details) has already
    deduped by device Serial# (else Activation#/Trans ID), collapsed each device's insurance/Plan-Option
    lines into its strongest bucket, and dropped Returns/cancelled. Here we additionally DROP Upgrade and
    Other (not payable — matching /activation-counts' Total Activation population) and map the survivors to
    activation_bucket 'premium'/'byod'.

    Each line carries ONLY the fields an activation_bucket rule needs plus identity:
      • salesperson  — canonicalised through the SAME name bridge the money path uses (id_map:
                       alias(UPPER)->roster) so a report salesperson name that differs from the roster
                       still lands under the rep's roster identity (the Luxelink $0 class) instead of a
                       new orphan group.
      • store / trans_date / trans_id(=activation key) — for grouping, market resolution and drill-down.
      • activation_bucket ('premium'|'byod') — the classifier-resolved match_field these lines expose.
      • department — the report's own "Department" column (its value is the SERVICE PLAN; falls back to the
        SP/PO service-plan name). Carried so the owner can pick those service plans in the plan editor's
        `department` picker and pay $10 per activation on the checked ones (owner 2026-08-28) — the SAME
        values `plan_options._custom_report_values` surfaces in that dropdown from the report's Department
        column, so a selected value matches the line it pays. It is SAFE to expose: the accessory stamp
        runs over `valid` BEFORE these lines are appended, so an `accessory equals yes` rule never matches
        a Detail line; and a POS department string never collides with a b2b service-plan name.
      • mrc — the report-carried monthly recurring charge, for pct_mrc.
      • _actsrc='activation_details' — the marker _line_payout uses to (a) price pct_mrc off the row's own
        mrc and (b) refuse pct_gp/pct_price/pct_price_over_cost (no cost/price columns on this report).
    category / product_desc / contract_type are deliberately left BLANK so no category/product/contract-type
    rule can match a Detail line — those dimensions come exclusively from raw_sales, and activations come
    exclusively from here. `service_plan`, `carrier`, the original report bucket and contract_type are also
    carried under private keys for transparency.
    Never raises: any failure returns ([], meta with an error note) and the caller falls back to raw_sales."""
    meta = {"source": "activation_details", "resolver_rows": 0, "lines": 0, "distinct_activations": 0,
            "by_bucket": {"premium": 0, "byod": 0}, "dropped_upgrade_other": 0, "error": None}
    try:
        from app.modules.commcalc.router import _cr_resolve_activation_details, _market_for_fn
    except Exception as _ie:
        meta["error"] = f"resolver import failed: {_ie}"
        return [], meta
    try:
        try:
            mfor = _market_for_fn(client, org_id)
        except Exception:
            mfor = (lambda s: "")
        rows = _cr_resolve_activation_details(client, org_id, period, {"market_for": mfor}) or []
    except Exception as _re:
        meta["error"] = f"resolver failed: {_re}"
        return [], meta
    meta["resolver_rows"] = len(rows)
    id_map = id_map or {}
    out, seen = [], set()
    for r in rows:
        eng = _AD_ENGINE_BUCKET.get(r.get("bucket") or "Other")
        if eng not in ("premium", "byod"):
            meta["dropped_upgrade_other"] += 1
            continue
        rep_raw = str(r.get("salesperson") or "").strip()
        rep = id_map.get(rep_raw.upper(), rep_raw) if rep_raw else rep_raw
        act_key = (str(r.get("serial") or "").strip() or str(r.get("activation_no") or "").strip()
                   or str(r.get("trans_id") or "").strip())
        line = {
            "salesperson": rep,
            "store": str(r.get("store") or "").strip(),
            "trans_date": str(r.get("trans_date") or "").strip(),
            "trans_id": act_key,
            "activation_bucket": eng,
            "mrc": safe_float(r.get("mrc")),
            "ext_price": 0.0, "gp": 0.0,
            "voided": "", "trans_type": "Sale",
            # department carries the report's Department (= service plan) so a `department in <plans>`
            # $10-per-unit rule pays these activations; category/product/contract_type stay blank.
            "department": str(r.get("department") or r.get("service_plan") or "").strip(),
            "category": "", "product_desc": "", "contract_type": "",
            "_actsrc": "activation_details",
            "_ad_bucket": r.get("bucket"),
            "_ad_contract_type": r.get("contract_type"),
            "_ad_service_plan": r.get("service_plan"),
            "_ad_carrier": r.get("carrier"),
            "_ad_salesperson_raw": rep_raw,
        }
        out.append(line)
        meta["by_bucket"][eng] += 1
        if act_key:
            seen.add(act_key)
    meta["lines"] = len(out)
    meta["distinct_activations"] = len(seen) if seen else len(out)
    return out, meta


# ── per-line payout ─────────────────────────────────────────────────────────────────────────────
def _line_payout(row, rule, mrc_by_mdn, mrc_by_sub, cost_by_pid):
    """Dollar payout this rule produces for ONE matching qualifying line (before tier multiplier).
    flat is handled by the caller (once per rep), so here flat returns 0."""
    kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
    amt, pct = safe_float(rule.get("amount")), safe_float(rule.get("pct"))
    # ACTIVATION-DETAIL LINE GUARD (mig 296). A line sourced from the Activation Details report carries NO
    # cost or sale-price columns, so a %-of-GP / %-of-price / %-of-price-over-cost rule cannot be priced on
    # it. Refuse it (pay $0) rather than silently compute a wrong number off a 0 basis — the caller records
    # the per-line note. flat_per_unit (the $10) and pct_mrc (priced off the report's own mrc) ARE
    # supported. Raw_sales rows never carry _actsrc, so this branch is inert for every non-opted-in caller.
    if row.get("_actsrc") == "activation_details" and kind in (
            "pct_gp", "pct_price", "pct_price_over_cost"):
        return 0.0
    if kind == "flat_per_unit":
        return round(amt, 2)
    if kind == "pct_gp":
        return round(pct * safe_float(row.get("gp")), 2)
    if kind == "pct_price":
        # % of the SALE PRICE, no cost/GP involvement (owner 2026-08-04: accessory pay must track
        # what the employee actually sold it for — GP depends on the B2B catalog cost setup, which
        # is untrustworthy for accessories; cost_equals_price zeroed the whole category).
        return round(pct * safe_float(row.get("ext_price")), 2)
    if kind == "pct_price_over_cost":
        pid = row.get("product_id")
        try:
            cost = cost_by_pid.get(float(pid), 0.0) if pid is not None else 0.0
        except Exception:
            cost = 0.0
        return round(pct * max(0.0, safe_float(row.get("ext_price")) - cost), 2)
    if kind == "pct_mrc":
        # ACTIVATION-DETAIL line (mig 296): the report carries the MRC on the row itself — there is no
        # raw_mi to join to — so price straight off the row's own mrc. Guarded on the _actsrc marker so a
        # raw_sales row (which never carries that marker) still routes through the historic mdn/sub join.
        if row.get("_actsrc") == "activation_details":
            return round(pct * safe_float(row.get("mrc")), 2)
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


def _unassigned_diagnosis(rep_name, store, market, rep_role, roster, plans, trace, hints, store_res):
    """Why THIS seller has no plan attached, in terms an operator can act on. PURE + money-free.

    Answers, in order: (1) is the NAME bridge to the storeops roster intact (and if not, who did the
    owner probably mean)? (2) does an assignment already exist under a DIFFERENT spelling? (3) what did
    the engine do with the raw POS STORE string, and what would the /store-match alias table resolve it
    to? (4) does this even look like a person?

    The "would it attach?" preview re-runs the REAL `_resolve_plan_for` with the alias-resolved market /
    store keys — it is never a second matching implementation, so it cannot promise something the pay
    path would not do."""
    rn = _canon_person(rep_name)
    roster_ok = bool(roster)
    by_name = [e for e in roster if _canon_person(e.get("name")) == rn]
    by_epay = [e for e in roster if str(e.get("epay_salesperson") or "").strip()
               and _canon_person(e.get("epay_salesperson")) == rn]
    cands = _roster_candidates(rep_name, roster)
    best = cands[0]["score"] if cands else 0.0

    def _row(e):
        return {"employee_id": e.get("id"), "name": e.get("name"),
                "role": (e.get("role") or "").strip() or None,
                "email": (e.get("email") or "").strip() or None,
                "epay_salesperson": (e.get("epay_salesperson") or "").strip() or None,
                "is_active": bool(e.get("is_active", True))}

    nb = {"sales_name": rep_name, "canonical": rn, "candidates": cands, "roster_rows": len(roster)}
    if not roster_ok:
        nb["status"] = "roster_unavailable"
        nb["message"] = ("the storeops employee roster could not be read for this tenant, so neither the "
                         "rep's ROLE nor a name match can be resolved. Every role-scope assignment is "
                         "inert until it is readable.")
        nb["remediation"] = "check the tenant's storeops roster (Admin → Employees)."
    elif by_name:
        e = by_name[0]
        nb["status"] = "name_match"
        nb["matched"] = _row(e)
        if not (e.get("role") or "").strip():
            nb["message"] = (f"the roster DOES have '{e.get('name')}' under this exact name, but that row "
                             f"has NO job role — so a role-scope assignment can never attach to them.")
            nb["remediation"] = (f"set a job role on '{e.get('name')}' in the employee roster, or attach "
                                 f"the plan to them by EMPLOYEE scope.")
        else:
            nb["message"] = (f"the roster matches '{e.get('name')}' (role '{(e.get('role') or '').strip()}') "
                             f"— the name bridge is INTACT, so the missing plan is an assignment problem, "
                             f"not a spelling problem.")
            nb["remediation"] = ("assign a plan to this person (employee scope), or add a role-scope "
                                 f"assignment for '{(e.get('role') or '').strip()}'.")
    elif by_epay:
        e = by_epay[0]
        nb["status"] = "epay_match_only"
        nb["matched"] = _row(e)
        nb["message"] = (f"roster row '{e.get('name')}' carries the ePay/POS name "
                         f"'{(e.get('epay_salesperson') or '').strip()}', which DOES match this seller — so an "
                         f"employee-scope assignment written from the roster will attach. ROLE resolution, "
                         f"however, reads the roster NAME column only, so a role-scope assignment still "
                         f"cannot attach to this seller.")
        nb["remediation"] = (f"assign the plan by EMPLOYEE scope, or make the roster NAME match the POS "
                             f"spelling if you want role-scope assignments to cover them.")
    else:
        nb["status"] = "no_match"
        if cands:
            top = cands[0]
            nb["message"] = (f"NO roster person's name matches this POS seller. Closest: "
                             + "; ".join(f"{c['name']}"
                                         + (f" ({c['role']})" if c.get("role") else "")
                                         + f" — {int(round(c['score'] * 100))}% match"
                                         for c in cands) + ".")
            nb["remediation"] = (f"set {top['name']}'s ePay/POS name (epay_salesperson) to exactly "
                                 f"'{rep_name}' on the roster so employee-scope assignments attach — then "
                                 f"RE-APPLY the plan, because an assignment written before the change still "
                                 f"stores the old spelling.")
        else:
            nb["message"] = ("no roster person resembles this POS seller at all — they are either missing "
                             "from the employee roster, or this is not a person.")
            nb["remediation"] = ("add them to the employee roster with this exact ePay/POS name, or mark "
                                 "the seller as not commissionable in Plan-coverage settings.")

    near = []
    for p in (plans or []):
        if not p.get("is_active", True):
            continue
        for a in (p.get("assignments") or []):
            if (a.get("scope") or "").strip().lower() != "employee":
                continue
            sv = str(a.get("scope_value") or "").strip()
            if not sv or _canon_person(sv) == rn:
                continue
            sc = _name_score(rep_name, sv)
            if sc >= 0.34:
                near.append({"plan_id": p.get("id"), "plan_name": p.get("name"), "scope_value": sv,
                             "score": sc,
                             "message": (f"plan '{p.get('name')}' is assigned to '{sv}', but this seller "
                                         f"rings as '{rep_name}' — the engine compares "
                                         f"'{_canon_person(sv)}' to '{rn}', so it does not attach.")})
    near.sort(key=lambda x: (-x["score"], str(x.get("scope_value") or "")))
    near = near[:5]

    alias_preview = None
    if trace and store_res != "alias":
        am = market or (trace.get("alias_market") or "")
        ak = trace.get("alias_keys") or None
        if (am and am != market) or ak:
            try:
                r2 = _resolve_plan_for(rep_name, store, am, plans, rep_role=rep_role, explain=True,
                                       store_keys=ak)
                w = (r2 or {}).get("winner")
            except Exception:
                w = None
            via = (trace.get("alias") or {}).get("via") or "the /store-match alias table"
            alias_preview = {
                "would_attach": bool(w), "market": am, "store_keys": ak or [],
                "plan_name": (w or {}).get("plan_name"), "scope": (w or {}).get("scope"),
                "scope_value": (w or {}).get("scope_value"),
                "message": ((f"with Store resolution set to 'alias', {via} would give this rep market "
                             f"'{am}' and plan '{(w or {}).get('plan_name')}' would attach by "
                             f"{(w or {}).get('scope')} scope.") if w else
                            (f"even with Store resolution set to 'alias' ({via} → market '{am}') no "
                             f"assignment would attach — the gap is not the store.")),
            }

    art = _artifact_flag(rep_name, hints, best)

    if art["suspect"] and art.get("confidence") == "high":
        concl = ("this looks like a POS terminal / back-office login, not a person — mark it "
                 "'not a commissionable seller' in Plan-coverage settings to take it off this list.")
    elif nb["status"] == "no_match" and cands:
        concl = nb["remediation"]
    elif near:
        concl = near[0]["message"] + " Fix the roster/ePay spelling, or re-assign using the POS spelling."
    elif alias_preview and alias_preview.get("would_attach"):
        concl = alias_preview["message"]
    elif nb["status"] in ("no_match", "roster_unavailable"):
        concl = nb["remediation"]
    elif trace and trace.get("status") in ("unmapped", "mapped_no_market") and not market:
        concl = trace.get("message")
    else:
        concl = ("the rep resolves fine — no employee / role / store / market / default assignment "
                 "covers them. Attach a plan on the Commission Plans page.")

    return {"name_bridge": nb, "assignment_near_miss": near, "store_bridge": trace,
            "alias_preview": alias_preview, "artifact": art, "conclusion": concl}


def _unmatched_record(row, why, rep, store, market, plan_name=None):
    """A compact, MATCHER-COMPATIBLE copy of one sale line that will NOT be paid. PURE.

    It carries every field `_rule_matches` reads, so the "lines not paying" explorer can evaluate REAL
    plan rules against the record itself (`_rule_matches(record, rule)`) rather than re-implementing rule
    matching — the definition of "considered for commission" therefore cannot drift from what pays."""
    out = {"why": why, "rep": rep, "store": store, "market": market, "plan_name": plan_name,
           "trans_id": str(row.get("trans_id") or "").strip(),
           "date": str(row.get("trans_date") or "")[:10],
           "ext_price": round(safe_float(row.get("ext_price")), 2),
           "gp": round(safe_float(row.get("gp")), 2)}
    for k in ("contract_type", "tender_type", "department", "category", "product_desc", "sku",
              "trans_type"):
        v = row.get(k)
        out[k] = ("" if v is None else str(v)).strip()
    # synthetic stamps, present only when preview() built them (rules that use them / 'mapped' tenants)
    for k in ("accessory", "activation_bucket", "_ct_resolved"):
        if k in row:
            out[k] = ("" if row.get(k) is None else str(row.get(k))).strip()
    return out


# ── preview ────────────────────────────────────────────────────────────────────────────────────
def preview(client, org_id, period, plan_id=None, detail=False, only_rep=None, coverage=False,
            rule_overrides=None, unmatched_detail=False, gate_override=None,
            setup_fee_override=None, sales_override=None, mrc_override=None,
            definition_pay_override=None, identity_map=None):
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

    sales_override / mrc_override (read-only; the EMPLOYEE PAY SIMULATOR, mod-commission 2026-08-03):
    substitute the period's sale LINES (and the pct_mrc join) with caller-supplied rows instead of
    reading raw_sales / raw_mi. This is the hook that lets the self-service pay simulator answer "what
    would I make if I sold X" by running THIS function — the same matcher, the same _line_payout, the
    same flat-once accumulation, the same pay gate, the same tier basis/multiplier and the same set-up
    fee item that the real payout runs — instead of a second, drift-prone copy of the pay math in
    pay_simulator.py or (worse) in the browser. Everything else is unchanged: plans, assignments, the
    accessory classifier, the exclusion map and the tenant's config are still read from the database, so
    a simulated line is priced by exactly the rules a real line would be. NOTHING IS WRITTEN — preview()
    has never written and still doesn't. Both default to None → this whole feature is inert and the
    result is BYTE-IDENTICAL for every existing caller.

    definition_pay_override (read-only; the accessory-definition PAY-IMPACT endpoint, mig 276):
    True/False forces the "does the tenant's ACCESSORY DEFINITION also decide the synthetic `accessory`
    match_field" switch instead of reading `accessory_config.definition_drives_pay`. It is how the impact
    endpoint quotes an honest before/after — by driving the REAL engine twice rather than arithmetically
    guessing. None (every other caller, always) reads the tenant's stored switch, which defaults FALSE.

    identity_map (luxelink money-path name-bridge) is a deterministic POS->roster identity map
    {POS salesperson (UPPER) -> roster name} threaded straight into `_resolve_plan_for` so an
    employee-scope plan pinned under a rep's ROSTER name still attaches to their POS sales. It is the
    SAME map the calc loads from commcalc.name_map / rep_aliases; the default None makes the bridge inert,
    so the money output is BYTE-IDENTICAL for every caller that does not pass it. Never fuzzy.

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

    # SALES SOURCE. `sales_override` (pay simulator) replaces the raw_sales/feed read with synthetic
    # lines; everything downstream — the voided/Return gate below included — is identical, so a
    # simulated line goes through the same funnel a real one does. None → the historic read.
    sales = list(sales_override) if sales_override is not None else _read_sales(client, org_id, period)
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
    _acc_stamp = None      # DIAGNOSTICS ONLY (detail/coverage) — how each line's `accessory` was decided
    _def_acc_fn = None     # the tenant's ACCESSORY DEFINITION as a predicate, when it drives pay (mig 276)
    if _uses_acc:
        try:
            from app.modules.commcalc import accessory_catalog as _accat
            _clf = _accat.build(client, org_id)
        except Exception:
            _clf = None
        # ── THE ACCESSORY DEFINITION AS A PAY BASIS (mig 276) — per-tenant, DEFAULT OFF ───────────
        # OWNER REPORT 2026-08-05 (luxelink, July): products mapped on /commcalc/accessory-definition
        # still paid $0 under a rule `accessory equals yes`. Cause: TWO surfaces. The owner maps into
        # `accessory_definition_map` (mig 257); this stamp has only ever read
        # `accessory_catalog.AccessoryClassifier` (accessory_config's department/category/keyword lists
        # + the raw_catalog category layer). Migration 257 says so out loud. Nothing was "overriding"
        # the mapping — the pay path could not see it.
        #
        # With the switch ON the stamp becomes  legacy OR catalog OR the tenant's CONFIRMED definition —
        # strictly ADDITIVE, so no line that is an accessory today stops being one, and set-up fees stay
        # out (accessory_definition.classify checks the set-up-fee keywords first, standing owner rule).
        # With it OFF (every tenant until a human flips it, and every tenant pre-mig-276) `_def_acc_fn`
        # is None and the loop below is byte-identical to the pre-2026-08-05 engine.
        # SCOPE: the PAY path only. The Sales Report / GP / P&L / Analyzer accessory classifiers are
        # untouched — unifying those ~8 surfaces is a separate owner decision.
        try:
            from app.modules.commcalc import plan_pay_gate as _gate_def
            _def_on = (bool(definition_pay_override) if definition_pay_override is not None
                       else _gate_def.definition_drives_pay(client, org_id))
            if _def_on:
                _def_acc_fn = _gate_def.accessory_predicate(client, org_id)
        except Exception as _dpe:
            print(f"WARN accessory-definition pay basis unavailable: {_dpe}")
            _def_acc_fn = None
        if _clf is not None or _def_acc_fn is not None:
            _acc_stamp = {"lines": 0, "yes": 0, "by_catalog_or_legacy": 0, "by_definition": 0,
                          "definition_drives_pay": bool(_def_acc_fn),
                          "classifier_loaded": _clf is not None,
                          "migration": None if _def_acc_fn is not None else
                          "276_commission_accessory_definition_pay.sql (switch is OFF or unapplied)"}
            for r in valid:
                _acc_stamp["lines"] += 1
                _y = bool(_clf.is_accessory_row(r)) if _clf is not None else False
                if _y:
                    _acc_stamp["by_catalog_or_legacy"] += 1
                elif _def_acc_fn is not None:
                    try:
                        _y = bool(_def_acc_fn(r))
                    except Exception:
                        _y = False
                    if _y:
                        _acc_stamp["by_definition"] += 1
                if _y:
                    _acc_stamp["yes"] += 1
                r["accessory"] = "yes" if _y else "no"

    # SYNTHETIC 'activation_bucket' + optional 'mapped' contract-type resolution (mig 232). Both reuse the
    # tenant's EXISTING display classification config (contract_type_map mig 213 + activation_rules mig 224)
    # via the SHARED resolver — no new classifier. Built ONLY when a rule/tier actually references
    # 'activation_bucket' OR the tenant set plan_ct_resolution='mapped'; otherwise this whole block is a
    # no-op (no extra reads, no stamps → _rule_matches is byte-identical). MONEY-ADJACENT: nothing moves
    # until an owner writes such a rule / flips the setting AND runs a recalc.
    _pay_cfg = _plan_pay_config(client, org_id)
    _ct_mapped = (_pay_cfg.get("plan_ct_resolution") == "mapped")
    # ── ACTIVATION SOURCE (mig 296 org-level + mig 297 PER-PLAN) — opt-in, DEFAULT byte-identical ──────
    # The activation source is decided PER REP, from the rep's EFFECTIVE (paying) plan, NOT org-wide:
    #   resolution per rep = their paying plan's activation_source, unless 'inherit', then the org-level
    #   commission_org_config.activation_source (mig 296), then 'raw_sales'.
    # This is the whole point of mig 297: the Chicago/Luxelink org holds BOTH the NY reps and 13 Chicago
    # stores in ONE org. The org-wide mig-296 switch would zero Chicago's activations (Chicago is not in
    # the NY-only report). Moving the control to the plan lets the NY plan pay activations from the report
    # while every Chicago rep (on an 'inherit'/'raw_sales' plan) is BYTE-IDENTICAL to today.
    #
    # 'raw_sales' / 'inherit'->org 'raw_sales' — TODAY'S BEHAVIOUR for that rep. activations classified from
    #                        raw_sales exactly as before.
    # 'activation_details' — for a rep whose effective plan resolves here, activations are PAID from the
    #                        ingested "Activation Details" custom report:
    #   • Detail lines (deduped per activation by the resolver, Upgrade/Other/Returns excluded, mapped to
    #     premium/byod) are APPENDED to the line set and attached (by the name bridge) to the rep who owns
    #     them, carrying ONLY the activation_bucket match_field.
    #   • THAT REP'S raw_sales activation_bucket is SUPPRESSED (per-rep, in the payout loop) so an
    #     activation_bucket rule matches ONLY the Detail lines — SINGLE SOURCE per rep, no double-count.
    #   • A Detail line whose owning rep's effective plan is NOT activation_details is DROPPED (report
    #     activations only ever pay a rep who is on the opted-in plan).
    #   • Accessories and every non-activation rule keep reading raw_sales UNCHANGED (Detail lines have
    #     blank department/category/product/contract_type, so no non-activation rule ever matches them).
    # Never on the pay-simulator's sales_override path (that substitutes the whole raw_sales set on purpose).
    # The WHOLE branch is guarded behind `_ad_any`; when no plan resolves to activation_details it is a
    # complete no-op and the result is byte-identical to the pre-296/297 engine.
    _org_act_src = _pay_cfg.get("activation_source", "raw_sales")

    def _plan_activation_source(p):
        """Resolve one plan's activation source: its own value, 'inherit' -> the org-level setting.
        Reads defensively (mig 297 column may be absent) — a missing/blank/unknown value = 'inherit'."""
        v = str(p.get("activation_source") or "inherit").strip().lower()
        if v not in ("inherit", "raw_sales", "activation_details"):
            v = "inherit"
        return _org_act_src if v == "inherit" else v

    # Plan ids whose reps are paid activations from the Activation Details report.
    _ad_plan_ids = {p.get("id") for p in plans
                    if p.get("is_active", True) and _plan_activation_source(p) == "activation_details"}
    _ad_any = bool(_ad_plan_ids) and (sales_override is None)
    _ad_meta, _ad_idmap = None, {}
    _ad_unsupported = {}   # (rule_id) -> {label, kind, lines} — pct_gp/pct_price on a Detail line, refused
    _ad_suppressed_reps, _ad_paid_reps = set(), set()   # per-rep bookkeeping for the result block
    _ad_dropped_non_ad_lines = 0   # Detail lines that resolved to a rep NOT on an activation_details plan
    if _ad_any:
        _ad_idmap = dict(identity_map or {})
        if not _ad_idmap:
            try:
                from app.modules.commcalc.router import _rep_canon_map as _rcm
                _ad_idmap = _rcm(client, org_id) or {}
            except Exception:
                _ad_idmap = {}
        _ad_lines, _ad_meta = _activation_detail_lines(client, org_id, period, id_map=_ad_idmap)
        valid.extend(_ad_lines)
    _uses_bucket = any(
        (rule.get("match_field") or "").strip().lower() == "activation_bucket"
        for p in plans for rule in (p.get("rules") or [])) or any(
        (p.get("tier_match_field") or "").strip().lower() == "activation_bucket" for p in plans)
    _bucket_lines = 0
    if _uses_bucket or _ct_mapped or _ad_any:
        _buckets = _activation_buckets(client, org_id, valid)
        for r, b in zip(valid, _buckets):
            _is_ad = r.get("_actsrc") == "activation_details"
            if b and not _is_ad:
                _bucket_lines += 1
            if _uses_bucket:
                if _is_ad:
                    pass                                   # keep the Detail line's own premium/byod bucket
                else:
                    # raw_sales activation bucket. Under per-plan AD, the SUPPRESSION is deferred to the
                    # per-rep loop (only a rep whose effective plan is activation_details has its raw_sales
                    # activations suppressed), so here the normal bucket is set for everyone — byte-identical
                    # to the pre-296 path when no rep is AD-sourced.
                    r["activation_bucket"] = b or ""
            if _ct_mapped and b and not _is_ad:
                r["_ct_resolved"] = b

    mrc_by_mdn, mrc_by_sub = _read_mi_mrc(client, org_id, period)
    if mrc_override:
        # pct_mrc join for simulated lines: the simulator supplies {normalized_mdn -> MRC} for the
        # phone numbers it minted. MERGED (not replaced) and applied on top, so a real MDN's MRC is
        # never silently changed for any other caller — with mrc_override=None this is a no-op.
        _mo = {_norm_mdn(k): safe_float(v) for k, v in (mrc_override or {}).items() if _norm_mdn(k)}
        mrc_by_mdn = {**mrc_by_mdn, **_mo}
    cost_by_pid = _read_catalog_cost(client, org_id)
    store_market = _read_store_market(client, org_id)
    role_by_rep = _read_employee_roles(client, org_id)   # {_canon_person(name) -> role} for scope='role'

    # STORE RESOLUTION (mig 249) — MONEY-ADJACENT, config-gated, default OFF. 'exact' keeps today's
    # store_mapping-only lookup (byte-identical). 'alias' additionally resolves the raw POS store string
    # through the SHARED /store-match chain (commcalc.store_aliases → store_code → store_mapping /
    # storeops roster) for the rep's MARKET and for store-scope assignment matching. The bridge is also
    # built (read-only) whenever coverage=True so the diagnosis can PREVIEW what flipping it would fix.
    _store_res = _pay_cfg.get("store_resolution", "exact")
    _bridge = _store_bridge(client, org_id) if (_store_res == "alias" or coverage) else None
    # COVERAGE-ONLY reads — never executed on the money path.
    _roster = _read_employee_roster(client, org_id) if coverage else []
    _cov_cfg = _coverage_config(client, org_id) if coverage else {}
    _hints = (_cov_cfg or {}).get("artifact_hints") or DEFAULT_ARTIFACT_HINTS
    _excluded_canon = {_canon_person(x) for x in ((_cov_cfg or {}).get("excluded_sellers") or []) if x}
    _excluded_reps = []
    # Part C: every line NOT considered for payout, tagged with WHY. Built only when asked for.
    _unmatched_rows = [] if (coverage and unmatched_detail) else None
    _unmatched_excluded_lines = 0

    # PAY GATE (owner directives 2026-08-01; engine: plan_pay_gate.py)
    # FOUR concerns, all at the same point - which matched lines pay, how many times, on what basis:
    #   (1) one payment per DEVICE for a rule that matches on a TRANSACTION-LEVEL field (the tender),
    #       so one financed sale can no longer pay once per receipt line;
    #   (2) the tenant's payout-EXCLUSION mapping (code-seeded with the owner's RTR rule);
    #   (3) a rule's optional WHERE-IT-APPLIES scope (unscoped = everywhere = today);
    #   (4) the accessory %-of-GP basis guard (default OFF fleet-wide).
    # Every loader degrades to the code defaults, so a missing migration changes nothing but the
    # tenant's ability to tune it. `_gate is None` (import failure) = the pre-2026-08-01 engine.
    # `gate_override='off'` reproduces the PRE-2026-08-01 engine exactly (no gate at all). It is how
    # the impact endpoints quote an honest before/after: they drive the REAL engine twice rather than
    # arithmetically un-doing the gate, which tiering would make wrong.
    try:
        if gate_override == "off":
            raise RuntimeError("gate disabled by caller")
        from app.modules.commcalc import plan_pay_gate as _gate
        _gate_cfg = (_gate.normalize_gate_config(gate_override) if isinstance(gate_override, dict)
                     else _gate.load_gate_config(client, org_id))
        _excl_rules, _excl_ready = _gate.load_exclusions(client, org_id)
    except Exception:
        _gate, _gate_cfg, _excl_rules, _excl_ready = None, None, [], False
    _ucfg = (_gate_cfg or {}).get("unit_basis") or {}
    _accg = (_gate_cfg or {}).get("accessory_basis_guard") or {}
    _accg_on = bool(_gate is not None and _accg.get("enabled"))
    if _gate is not None and not ((_gate_cfg or {}).get("exclusions") or {}).get("enabled", True):
        _excl_rules = []
    # Resolve every rule's unit basis ONCE (rules are shared by every rep) and decide whether the
    # tenant's accessory definition needs loading at all - for a tenant with nothing deduped and the
    # basis guard off this whole block costs one config read and builds no classifier.
    _basis_by_rule, _needs_acc = {}, _accg_on
    if _gate is not None:
        for _p in plans:
            for _r in (_p.get("rules") or []):
                _b, _s = _gate.resolve_unit_basis(_r, _ucfg)
                _basis_by_rule[id(_r)] = (_b, _s)
                if _b != "per_line" and _ucfg.get("exclude_accessory_units"):
                    _needs_acc = True
    # Same predicate the gate has always built; when the mig-276 switch already built it above we
    # reuse that instance instead of re-reading the definition. Behaviourally identical.
    _acc_fn = None
    if _gate is not None and _needs_acc:
        _acc_fn = _def_acc_fn if _def_acc_fn is not None else _gate.accessory_predicate(client, org_id)
    # ── FINANCING TIERS (mig 273; owner directive + answers 2026-08-04) ────────────────────────
    # "target based commission payout right now we have flat payment, need it tiered levels" +
    # "achieved rate applies to that months sales, attainment is monthly".
    # A rule-scoped commission_tier row carrying a `unit_rate` replaces that rule's flat per-unit amount
    # with the rate the store's MONTHLY attainment earned, applied to EVERY unit of the month.
    # INERT BY CONSTRUCTION: with no such tier row `build_context` returns active=False and not one
    # number below changes - the negative control in financing_tier_proof.py asserts byte identity.
    # The matcher and the per-device unit collapse are INJECTED, so the store's unit count is produced
    # by exactly the code that decides what pays (never a second copy of the matching logic).
    _fin, _fin_ctx = None, None
    try:
        from app.modules.commcalc import financing_tiers as _fin
        _fin_ctx = _fin.build_context(
            client, org_id, _pvariants(period), plans, valid, _rule_matches,
            paying_lines=(_gate.select_paying_lines if _gate is not None else None),
            basis_by_rule=_basis_by_rule, unit_cfg=_ucfg, is_accessory=_acc_fn,
            is_excluded=((lambda _r: _gate.exclusion_hit(_r, _excl_rules) is not None)
                         if (_gate is not None and _excl_rules) else None))
    except Exception as _fte:
        print(f"WARN financing tiers unavailable: {_fte}")
        _fin, _fin_ctx = None, None
    _fin_on = bool(_fin is not None and _fin_ctx and _fin_ctx.get("active"))
    _fin_notices = []
    # DEVICE SET-UP FEE / ACTIVATION FEE as its OWN pay item (owner 2026-08-01, mig 263).
    # The Boost engine has paid a % of the set-up fee COLLECTED since day one (calculator.py); every
    # other carrier had no way to. This is that pay item for the plan engine. It is NOT a commission
    # rule: it needs no `commission_rule` row, and it is NEVER folded into the accessory basis
    # (standing owner rule). It is OFF for every tenant until someone turns it on, so an unconfigured
    # tenant's result is byte-identical.
    try:
        from app.modules.commcalc import setup_fee_pay as _sfp
        _sf_cfg = (_sfp.normalize_pay_config(setup_fee_override)
                   if isinstance(setup_fee_override, dict)
                   else _sfp.load_pay_config(client, org_id))
        _sf_on = bool((_sf_cfg.get("default") or {}).get("include_in_commission")) or any(
            bool(v.get("include_in_commission")) for v in (_sf_cfg.get("by_carrier") or {}).values())
        _sf_kws = _sfp.load_keywords(client, org_id) if _sf_on else None
    except Exception:
        _sfp, _sf_cfg, _sf_on, _sf_kws = None, None, False, None
    _sf_guard = {"collected": 0.0, "lines": 0, "paid": 0.0, "by_rep": {}, "by_status": {},
                 "dealer_share": 0.0, "dealer_share_stated": False, "carriers": {}, "warnings": []}
    _cost_cfg = None
    if _accg_on:
        try:
            from app.modules.commcalc import pay_data_quality as _pdq_cfg
            _cost_cfg = _pdq_cfg.load_cost_config(client, org_id)
        except Exception:
            _cost_cfg = None
    # NEVER SILENT: everything the gate changes is reported here. Emitted as a TOP-LEVEL `pay_gate`
    # key (deliberately OUT of `totals`) ONLY when it actually did something, so a tenant it does not
    # touch receives a byte-identical result dict.
    _guard = {
        "unit": {"transactions": 0, "lines_suppressed": 0, "amount_suppressed": 0.0,
                 "units_paid": 0, "by_rule": {}, "by_rep": {}, "notes": []},
        "excluded": {"lines": 0, "amount_suppressed": 0.0, "by_rule": {}, "by_rep": {}, "samples": []},
        "scope": {"lines": 0, "amount_suppressed": 0.0, "by_rule": {}, "by_rep": {}},
        "accessory_basis": {"lines": 0, "amount_before": 0.0, "amount_after": 0.0,
                            "by_rep": {}, "by_flag": {}, "samples": []},
    }
    _GUARD_CAP = 200

    def _guard_add(sec, rep, amt):
        _guard[sec]["by_rep"][rep] = round(_guard[sec]["by_rep"].get(rep, 0.0) + safe_float(amt), 2)

    # group lines per rep
    reps = {}  # key (upper rep name) -> {name, store, lines:[...]}
    # PASS 1 — group the RAW_SALES lines by their POS name key, exactly as the pre-296 engine did. NO name
    # bridge is applied here, so a raw_sales rep's grouping key is BYTE-IDENTICAL to today even when the org
    # has an activation_details plan (this is what keeps every Chicago/non-AD rep unaffected).
    for r in valid:
        if r.get("_actsrc") == "activation_details":
            continue                                       # Detail lines are attached in PASS 2
        rep = str(r.get("salesperson", "") or "").strip()
        if not rep or rep.lower() == "admin":
            continue
        key = rep.upper()
        e = reps.get(key)
        if not e:
            e = reps[key] = {"name": rep, "store": str(r.get("store", "") or "").strip(), "lines": []}
        e["lines"].append(r)

    # PASS 2 (mig 297) — attach each Activation Details line to the rep who owns it, by IDENTITY, without
    # disturbing any raw_sales grouping key. The Detail line's salesperson is already canonicalised to the
    # roster name in _activation_detail_lines; a raw_sales group's roster identity is its POS name bridged
    # through the SAME alias map. So a rep's raw_sales lines (POS name) and their Detail activations (roster
    # name) land in ONE group — but the raw group's KEY stays the POS key, so non-AD reps are byte-identical.
    # The per-rep payout loop DROPS these Detail lines again for any rep whose effective plan is not
    # activation_details, so a report activation can only ever pay a rep on the opted-in plan.
    if _ad_any:
        _raw_bridge_index = {}   # bridged-roster-key(UPPER) -> raw group key
        for _k, _e in reps.items():
            _bridged = _ad_idmap.get(_e["name"].upper(), _e["name"]).upper() if _ad_idmap else _k
            _raw_bridge_index.setdefault(_bridged, _k)
        for r in valid:
            if r.get("_actsrc") != "activation_details":
                continue
            rep = str(r.get("salesperson", "") or "").strip()
            if not rep or rep.lower() == "admin":
                continue
            rkey = rep.upper()
            tgt = _raw_bridge_index.get(rkey)
            if tgt is not None:
                reps[tgt]["lines"].append(r)               # merge into the rep's existing raw group
            else:
                e = reps.get(rkey)
                if not e:                                  # Detail-only rep (no raw_sales this period)
                    e = reps[rkey] = {"name": rep,
                                      "store": str(r.get("store", "") or "").strip(), "lines": []}
                    _raw_bridge_index.setdefault(rkey, rkey)
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
        # mig 249: with store_resolution='alias' an unresolved POS store string falls back to the shared
        # alias chain for its MARKET, and a store-scope assignment may additionally match the resolved
        # store CODE / canonical ADDRESS. On the default 'exact' `_skeys` stays None and `market` keeps
        # the exact expression above, so `_resolve_plan_for` and every payout are BYTE-IDENTICAL.
        _trace = (_store_trace(store_market, _bridge, store)
                  if (coverage or _store_res == "alias") else None)
        _skeys = None
        if _store_res == "alias" and _trace:
            market = market or (_trace.get("alias_market") or "")
            _skeys = _trace.get("alias_keys") or None
        rep_role = role_by_rep.get(_canon_person(e["name"]))
        resolution = None
        if detail:
            resolution = _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role,
                                           explain=True, store_keys=_skeys, identity_map=identity_map)
            plan = forced_plan or resolution.get("plan")
        else:
            # money path: EXACTLY the original lazy short-circuit — when plan_id forces a plan,
            # _resolve_plan_for is never called (so the delta vs the pre-drill engine is exactly zero,
            # incl. the case where a non-numeric assignment field would make the resolver raise).
            plan = forced_plan or _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role,
                                                    store_keys=_skeys, identity_map=identity_map)
        # ── PER-REP ACTIVATION SOURCE GATE (mig 297) ──────────────────────────────────────────────────
        # Decide THIS rep's activation source from their EFFECTIVE (paying) plan. A rep pays under exactly
        # ONE plan (most-specific assignment wins), so the effective plan is unambiguous — that is the
        # multi-assignment rule: the plan that actually PAYS the rep governs. If the base resolution did not
        # land the activation_details plan but this group carries Detail lines and the org has an AD plan,
        # try the AD name bridge (roster identity) and adopt it ONLY when it yields an AD plan — this never
        # disturbs a non-AD resolution (a rep who resolves to a raw_sales/inherit plan keeps that plan).
        if _ad_any and not forced_plan:
            _has_detail = any(r.get("_actsrc") == "activation_details" for r in e["lines"])
            _plan_is_ad = bool(plan) and plan.get("id") in _ad_plan_ids
            if _has_detail and not _plan_is_ad and _ad_idmap:
                _pb = _resolve_plan_for(e["name"], store, market, plans, rep_role=rep_role,
                                        store_keys=_skeys, identity_map=_ad_idmap)
                if _pb is not None and _pb.get("id") in _ad_plan_ids:
                    plan = _pb
                    if detail:
                        resolution = _resolve_plan_for(
                            e["name"], store, market, plans, rep_role=rep_role, explain=True,
                            store_keys=_skeys, identity_map=_ad_idmap)
        _rep_ad = bool(plan) and (plan.get("id") in _ad_plan_ids)
        if _ad_any:
            if _rep_ad:
                # SINGLE SOURCE for this rep: suppress its raw_sales activations so only the Detail lines
                # (which keep their own premium/byod bucket) can satisfy an activation_bucket rule.
                _sup = 0
                for r in e["lines"]:
                    if r.get("_actsrc") != "activation_details" and r.get("activation_bucket"):
                        r["activation_bucket"] = ""
                        _sup += 1
                if any(r.get("_actsrc") == "activation_details" for r in e["lines"]):
                    _ad_paid_reps.add(e["name"])
                if _sup:
                    _ad_suppressed_reps.add(e["name"])
            else:
                # A report activation resolved to a rep whose effective plan is NOT activation_details —
                # drop those Detail lines so they can never pay a non-opted-in rep, and keep the rep
                # byte-identical to today (raw_sales activations untouched). Counted for the result block.
                _keep, _drop = [], 0
                for r in e["lines"]:
                    if r.get("_actsrc") == "activation_details":
                        _drop += 1
                    else:
                        _keep.append(r)
                if _drop:
                    e["lines"] = _keep
                    _ad_dropped_non_ad_lines += _drop
        if not plan:
            # COVERAGE (mig 232): a seller with real sales and NO plan attached is skipped here — which is
            # exactly how a carrier_mode='plan' tenant ends up with a legitimate-looking $0 for that rep.
            # Record them so the gap is VISIBLE instead of silent. No effect on by_rep/totals.
            if coverage:
                _row = {
                    "rep": e["name"], "store": store, "market": market,
                    "role": rep_role or None, "lines": len(e["lines"]),
                    "transactions": len({str(r.get("trans_id") or "").strip() for r in e["lines"]
                                         if str(r.get("trans_id") or "").strip()}),
                    "ext_price": round(sum(safe_float(r.get("ext_price")) for r in e["lines"]), 2),
                    "reason": ("no commission-plan assignment matched this rep "
                               "(employee > role > store > market > default all missed)"),
                }
                if _canon_person(e["name"]) in _excluded_canon:
                    # Part D: the tenant has confirmed this "seller" is a POS artifact, not a
                    # commissionable person. It leaves the unassigned list but is still REPORTED (a
                    # collapsed note) — never silently hidden. Pay math is untouched: this rep has no
                    # plan, so they contribute $0 either way, and this branch runs only under coverage.
                    _row["excluded"] = True
                    _excluded_reps.append(_row)
                    _unmatched_excluded_lines += len(e["lines"])
                else:
                    _row["diagnosis"] = _unassigned_diagnosis(
                        e["name"], store, market, rep_role, _roster, plans, _trace, _hints, _store_res)
                    unassigned.append(_row)
                    if _unmatched_rows is not None:
                        for _r in e["lines"]:
                            _unmatched_rows.append(_unmatched_record(
                                _r, "rep_unassigned", e["name"], store, market))
            continue
        rules = plan.get("rules") or []

        rule_breakdown = {}   # rule_id -> {label, kind, matched, qualifying, payout, tiered}
        matched_ids = set() if coverage else None   # coverage: which lines ANY rule matched
        qualifying_units = 0
        flat_pending = {}     # rule_id -> amount (flat bonus, paid once if any qualifying match)
        _fin_lines = {}       # rule_id -> [(row, line_detail, paid)] for financing-tier re-pricing
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
            # PAY GATE. The matched set is computed ONCE (same predicate, same order as e["lines"],
            # so the display order of `lines` is unchanged), then three gates decide which of those
            # lines may turn into dollars. With the gate absent/inert `_blocked` stays empty and every
            # branch below is the pre-2026-08-01 code path.
            _matched = [row for row in e["lines"] if _rule_matches(row, rule)]
            _blocked = {}          # id(row) -> (reason_code, extra)
            if _gate is not None and _matched:
                _sc_ok, _sc_why = _gate.rule_applies_here(rule, store, market, e["name"], _skeys)
                if detail:
                    rb["scope_reason"] = _sc_why
                if not _sc_ok:
                    for _row in _matched:
                        _blocked[id(_row)] = ("scope", {"reason": _sc_why})
                else:
                    if _excl_rules:
                        for _row in _matched:
                            _hit = _gate.exclusion_hit(_row, _excl_rules)
                            if _hit is not None:
                                _blocked[id(_row)] = ("excluded", _hit)
                    _ub, _usrc = _basis_by_rule.get(id(rule), ("per_line", "default"))
                    if detail:
                        rb["unit_basis"] = _ub
                        rb["unit_basis_source"] = _usrc
                    if _ub != "per_line":
                        _elig = [r for r in _matched if id(r) not in _blocked]
                        _payers, _supp, _notes = _gate.select_paying_lines(
                            _elig, _ub, _ucfg, _acc_fn)
                        for _r2, _why in _supp:
                            _blocked[id(_r2)] = (_why, None)
                        if _supp:
                            _u = _guard["unit"]
                            _u["units_paid"] += len(_payers)
                            _pr = _u["by_rule"].setdefault(
                                str(rid), {"label": rb.get("label"), "basis": _ub, "source": _usrc,
                                           "matched_lines": 0, "units_paid": 0})
                            _pr["matched_lines"] += len(_elig)
                            _pr["units_paid"] += len(_payers)
                        for _n in _notes:
                            if _n.get("code") == "unit_collapsed":
                                _guard["unit"]["transactions"] += 1
                            if len(_guard["unit"]["notes"]) < _GUARD_CAP:
                                _guard["unit"]["notes"].append(dict(_n, rep=e["name"]))
            for row in _matched:
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
                _blk = _blocked.get(id(row))
                if _blk is not None:
                    # MATCHED AND SHOWN, PAYING NOTHING - with the reason attached. A suppressed line
                    # is never removed from the drill-down: silence is how a $0 becomes unexplainable.
                    _code, _extra = _blk
                    _would = 0.0
                    if qualifies and kind != "flat":
                        _would = _line_payout(row, rule, mrc_by_mdn, mrc_by_sub, cost_by_pid)
                    _sec = ("excluded" if _code == "excluded"
                            else ("scope" if _code == "scope" else "unit"))
                    if _sec == "unit":
                        _guard["unit"]["lines_suppressed"] += 1
                        _guard["unit"]["amount_suppressed"] = round(
                            _guard["unit"]["amount_suppressed"] + _would, 2)
                    else:
                        _guard[_sec]["lines"] += 1
                        _guard[_sec]["amount_suppressed"] = round(
                            _guard[_sec]["amount_suppressed"] + _would, 2)
                        _br = _guard[_sec]["by_rule"].setdefault(
                            str(rid), {"label": rb.get("label"), "lines": 0, "amount": 0.0})
                        _br["lines"] += 1
                        _br["amount"] = round(_br["amount"] + _would, 2)
                    _guard_add(_sec, e["name"], _would)
                    if _code == "excluded" and len(_guard["excluded"]["samples"]) < _GUARD_CAP:
                        _guard["excluded"]["samples"].append({
                            "rep": e["name"], "trans_id": str(row.get("trans_id") or "").strip(),
                            "date": str(row.get("trans_date") or "")[:10],
                            "product": row.get("product_desc"),
                            "matched_field": (_extra or {}).get("match_field"),
                            "matched_value": (_extra or {}).get("match_value"),
                            "code": (_extra or {}).get("code"),
                            "would_have_paid": round(_would, 2)})
                    if ldet is not None:
                        ldet["amount"] = 0.0
                        ldet["suppressed"] = True
                        ldet["suppressed_by"] = _code
                        ldet["suppressed_reason"] = (
                            (_extra or {}).get("reason") or _gate.SUPPRESS_LABELS.get(_code, _code))
                        ldet["would_have_paid"] = round(_would, 2)
                        if _code == "excluded":
                            ldet["excluded_by"] = ((_extra or {}).get("code")
                                                   or (_extra or {}).get("label"))
                    continue
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
                # ACTIVATION-DETAIL PAYOUT-KIND GUARD (mig 296): a %-of-GP / %-of-price rule cannot be
                # priced on an Activation Details line (no cost/price columns). _line_payout already
                # returned $0 for it; record the refusal as a plain-language note so the $0 is explained
                # instead of silent. Never mis-prices; flat_per_unit and pct_mrc are unaffected.
                if (row.get("_actsrc") == "activation_details"
                        and kind in ("pct_gp", "pct_price", "pct_price_over_cost")):
                    _un = _ad_unsupported.setdefault(str(rid), {
                        "rule_id": rid, "label": rb.get("label"), "payout_kind": kind, "lines": 0,
                        "note": (f"Rule '{rb.get('label')}' uses {kind}, which the Activation Details "
                                 f"report cannot price (it carries no cost or sale-price column). These "
                                 f"activation lines are refused ($0); use flat_per_unit or pct_mrc.")})
                    _un["lines"] += 1
                    if ldet is not None:
                        ldet["activation_source_unsupported"] = kind
                        ldet["suppressed_reason"] = _un["note"]
                # ACCESSORY BASIS GUARD (default OFF fleet-wide)
                if _accg_on and kind == "pct_gp":
                    _g_amt, _g_basis, _g_flags, _g_note = _gate.guarded_pct_gp(
                        row, safe_float(rule.get("pct")), _accg, _cost_cfg,
                        bool(_acc_fn(row)) if _acc_fn else False)
                    if _g_amt is not None and round(_g_amt, 2) != round(pay, 2):
                        _ab = _guard["accessory_basis"]
                        _ab["lines"] += 1
                        _ab["amount_before"] = round(_ab["amount_before"] + pay, 2)
                        _ab["amount_after"] = round(_ab["amount_after"] + _g_amt, 2)
                        _guard_add("accessory_basis", e["name"], round(_g_amt - pay, 2))
                        for _fc in (_g_flags or []):
                            _bf = _ab["by_flag"].setdefault(_fc, {"lines": 0, "delta": 0.0})
                            _bf["lines"] += 1
                            _bf["delta"] = round(_bf["delta"] + (_g_amt - pay), 2)
                        if len(_ab["samples"]) < _GUARD_CAP:
                            _ab["samples"].append({
                                "rep": e["name"], "trans_id": str(row.get("trans_id") or "").strip(),
                                "product": row.get("product_desc"),
                                "ext_price": round(safe_float(row.get("ext_price")), 2),
                                "gp": round(safe_float(row.get("gp")), 2),
                                "was": round(pay, 2), "now": round(_g_amt, 2),
                                "basis": _g_basis, "flags": _g_flags, "note": _g_note})
                        if ldet is not None:
                            ldet["basis_guarded"] = True
                            ldet["basis_used"] = _g_basis
                            ldet["basis_flags"] = _g_flags
                            ldet["basis_note"] = _g_note
                            ldet["amount_before_guard"] = round(pay, 2)
                        pay = _g_amt
                rb["payout"] = round(rb["payout"] + pay, 2)
                if ldet is not None:
                    ldet["amount"] = pay
                if _fin_on and kind == "flat_per_unit" and str(rid) in (_fin_ctx.get("rules") or {}):
                    _fin_lines.setdefault(str(rid), []).append((row, ldet, pay))
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

        # FINANCING TIER RATES (mig 273) - replace the flat per-unit amount with the rate the
        # store's MONTHLY attainment earned, on EVERY unit of the month (owner 2026-08-04). Runs only
        # for rules the tenant attached rate tiers to; `_fin_lines` is empty for every other rule and
        # for every tenant that has configured none, so the arithmetic below is untouched. A rule whose
        # store reached NO tier (or whose store has no target) is reported and keeps its flat amount -
        # a missing target never silently drops anyone to a bottom rate.
        _fin_applied = []
        if _fin_on and _fin_lines:
            for _frid, _fitems in _fin_lines.items():
                try:
                    _adj = _fin.apply_rule_tiers(_fin_ctx, _frid, store, e["name"], _fitems)
                except Exception as _fae:
                    print(f"WARN financing tier pricing skipped for rule {_frid}: {_fae}")
                    continue
                if not _adj:
                    continue
                if len(_fin_notices) < _GUARD_CAP:
                    _fin_notices.append(_adj)
                if not _adj.get("applied"):
                    continue
                _fdelta = _adj["delta"]
                _frule = (_fin_ctx["rules"].get(_frid) or {}).get("rule") or {}
                if bool(_frule.get("tiered")):
                    tiered_total += _fdelta
                else:
                    base_total += _fdelta
                _rbf = rule_breakdown.get(_frule.get("id"))
                if _rbf is not None:
                    _rbf["payout"] = round(_rbf["payout"] + _fdelta, 2)
                    _rbf["financing_tier"] = _adj["tier"]
                    _rbf["financing_unit_rate"] = _adj["unit_rate"]
                    _rbf["financing_attainment_pct"] = _adj["attainment_pct"]
                _fin_applied.append(_adj)

        # TIER ATTAINMENT (mig 232): a plan may DEFINE what its tier counts (distinct activation
        # transactions, matched lines, …). Legacy plans return (None, 'rule_units') → the historic
        # qualifying-unit total is used, byte-identical.
        _tier_n, _tier_basis_used = _tier_metric_count(plan, e["lines"])
        tier_units = qualifying_units if _tier_n is None else _tier_n
        mult = _tier_multiplier(plan, tier_units)
        total = round(base_total + tiered_total * mult, 2)
        # ── SET-UP / ACTIVATION FEE PAY ITEM (mig 263) ─────────────────────────────────────────
        # A SEPARATE component, added AFTER the tier multiplier on purpose: the fee is a straight
        # percentage of money the store actually collected, not a spiff whose value depends on how many
        # KPIs the rep hit. It composes with the pay gate — a line the tenant's exclusion map removes
        # (mig 261) is not collected revenue for pay purposes either.
        _sf_pay = 0.0
        if _sf_on and _sfp is not None:
            _sf_set, _sf_src = _sfp.resolve_for_carrier(_sf_cfg, plan.get("carrier_id"))
            _skip = None
            if _gate is not None and _excl_rules:
                def _skip(_r, _rules=_excl_rules, _g=_gate):
                    return _g.exclusion_hit(_r, _rules) is not None
            _sf_amt, _sf_lines = _sfp.collected(
                e["lines"], _sf_kws, _sf_set.get("match_mode"), skip=_skip)
            _sf_pay, _sf_status = _sfp.employee_pay(_sf_amt, _sf_set)
            _sf_dealer, _sf_stated = _sfp.dealer_share(_sf_amt, _sf_set)
            if _sf_amt or _sf_pay:
                _sf_guard["collected"] = round(_sf_guard["collected"] + _sf_amt, 2)
                _sf_guard["lines"] += _sf_lines
                _sf_guard["paid"] = round(_sf_guard["paid"] + _sf_pay, 2)
                _sf_guard["by_rep"][e["name"]] = {
                    "collected": _sf_amt, "lines": _sf_lines, "paid": _sf_pay,
                    "pct": _sf_set.get("employee_pct_of_collected"), "status": _sf_status,
                    "config_source": _sf_src}
                _sf_guard["by_status"][_sf_status] = _sf_guard["by_status"].get(_sf_status, 0) + 1
                _sf_guard["carriers"][str(plan.get("carrier_id") or "(none)")] = _sf_src
                if _sf_stated:
                    _sf_guard["dealer_share"] = round(_sf_guard["dealer_share"] + (_sf_dealer or 0), 2)
                    _sf_guard["dealer_share_stated"] = True
                if _sf_status == "unconfigured":
                    # LOUD, never silent: the tenant said this fee should pay and did not say how much.
                    # Nothing is guessed and nothing is zeroed elsewhere — it simply has not paid yet.
                    _sf_guard["warnings"].append({
                        "type": "setup_fee_pct_unconfigured", "rep": e["name"],
                        "collected": _sf_amt, "plan": plan.get("name"),
                        "message": (f"{e['name']} collected ${_sf_amt:,.2f} in set-up / activation fees "
                                    f"and the employee percentage has not been entered, so it paid $0. "
                                    f"Set it under Commission Plans -> Set-up / activation fee.")})
            total = round(total + _sf_pay, 2)
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
        if _fin_applied:
            # its OWN key, added only when a tier actually re-priced this rep's units - so a tenant with
            # no financing tiers receives a rep row byte-identical to the pre-2026-08-04 engine.
            out_rows[-1]["financing_tiers"] = _fin_applied
        if _sf_pay or (_sf_on and e["name"] in _sf_guard["by_rep"]):
            # its OWN line on the rep row — never blended into base/tiered, never into an accessory total
            out_rows[-1]["setup_fee_comm"] = _sf_pay
            out_rows[-1]["setup_fee_collected"] = _sf_guard["by_rep"][e["name"]]["collected"]
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
            if _unmatched_rows is not None:
                for _r in _un:
                    _unmatched_rows.append(_unmatched_record(
                        _r, "no_rule_matched", e["name"], store, market, plan.get("name")))
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
    # PAY GATE REPORT - emitted ONLY when the gate actually changed something, so every tenant it
    # does not touch receives a result dict byte-identical to the pre-2026-08-01 engine.
    # Emitted when the tenant has configured financing tiers AT ALL - including the case where the
    # context refused to activate them (a rate tier on a %-of-basis rule), so a misconfiguration is
    # visible instead of silently doing nothing. A tenant with NO tier rows produces neither, and its
    # result dict is byte-identical to the pre-2026-08-04 engine.
    if _fin_notices or ((_fin_ctx or {}).get("notes")):
        out["financing_tiers"] = {
            "applied": [n for n in _fin_notices if n.get("applied")],
            "not_applied": [n for n in _fin_notices if not n.get("applied")],
            "rules": [{"rule_id": k, "label": v["label"], "scope": v["scope"], "mode": v["mode"],
                       "vendor_key": v["vendor_key"], "flat_amount": v["flat_amount"],
                       "tiers": v["tiers"]}
                      for k, v in ((_fin_ctx or {}).get("rules") or {}).items()],
            "notes": ((_fin_ctx or {}).get("notes") or []),
            "basis": ("The tier a store reaches sets the per-unit rate for EVERY financing unit of that "
                      "month (whole-month), and attainment is measured monthly against the store's "
                      "financing target - owner decision 2026-08-04."),
        }
    if _sf_on and (_sf_guard["collected"] or _sf_guard["paid"]):
        _sf_guard["config_source"] = "tenant" if (_sf_cfg or {}).get("_stored") else "code_default"
        _sf_guard["keywords"] = _sf_kws
        out["setup_fee"] = _sf_guard
    if _gate is not None and (_guard["unit"]["lines_suppressed"] or _guard["excluded"]["lines"]
                              or _guard["scope"]["lines"] or _guard["accessory_basis"]["lines"]):
        _guard["config_source"] = "tenant" if (_gate_cfg or {}).get("_stored") else "code_default"
        _guard["exclusion_map_ready"] = bool(_excl_ready)
        _guard["exclusions_active"] = [
            {"code": r.get("code"), "label": r.get("label"), "match_field": r.get("match_field"),
             "match_op": r.get("match_op"), "match_value": r.get("match_value"),
             "source": r.get("source")} for r in (_excl_rules or [])]
        _guard["accessory_definition_loaded"] = bool(_acc_fn)
        out["pay_gate"] = _guard
    # ACTIVATION SOURCE REPORT (mig 296 org-level + mig 297 per-plan) — emitted ONLY when at least one plan
    # in this org resolves to activation_details, so an org with no such plan is byte-identical. Makes the
    # single-source substitution visible AND per-rep scoped: which plans are AD-sourced, which reps were paid
    # from the report and had their raw_sales activations suppressed, how many report activations were dropped
    # because they belonged to a rep NOT on an AD plan (the Chicago-safety count), plus the bucket split and
    # any rule refused for an unsupported payout kind.
    if _ad_any:
        out["activation_source"] = {
            "source": "activation_details",
            "scope": "per_plan",
            "org_activation_source": _org_act_src,
            "activation_details_plan_ids": sorted(str(p) for p in _ad_plan_ids),
            "raw_sales_activation_bucket_suppressed": True,   # policy: AD reps' raw activations are suppressed
            "reps_paid_from_report": sorted(_ad_paid_reps),
            "reps_raw_sales_suppressed": sorted(_ad_suppressed_reps),
            "detail_lines_dropped_non_ad_rep": _ad_dropped_non_ad_lines,
            "detail": _ad_meta or {},
            "unsupported_payout_kinds": list(_ad_unsupported.values()),
            "basis": ("Activations are paid from the ingested Activation Details report (deduped per "
                      "activation; Upgrade/Other/Returns excluded) ONLY for reps whose effective plan is "
                      "activation_details; those reps' raw_sales activations are suppressed so nothing is "
                      "counted twice. Every other rep (inherit/raw_sales plan) is unchanged. Accessories "
                      "and every non-activation rule still read raw_sales. Per-plan opt-in, mig 296+297."),
        }
    if _acc_stamp is not None and (detail or coverage):
        # DIAGNOSTICS ONLY — attached for the drill-down / coverage callers, never on the money path,
        # so `_apply_new_engines` receives a byte-identical dict. This is the block that answers
        # "my accessory rule matched nothing — why?" without anyone reading engine source.
        if _acc_stamp["yes"] == 0:
            _acc_stamp["note"] = (
                "NO line was classified as an accessory, so a rule matching `accessory = yes` pays $0. "
                "The pay path reads the accessory DEPARTMENT/CATEGORY/keyword lists and the product "
                "catalog — not the Accessory Definition mapping page — unless this tenant's "
                "'Accessory Definition decides pay' switch is on.")
        elif _acc_stamp["by_definition"]:
            _acc_stamp["note"] = (
                f"{_acc_stamp['by_definition']} line(s) were classified as accessories by this tenant's "
                f"own Accessory Definition mapping (the rest by the department/category/keyword lists or "
                f"the product catalog).")
        out["accessory_stamp"] = _acc_stamp
    if coverage:
        # suspected POS artifacts sink to the BOTTOM (still listed) so the real people needing an
        # assignment are the first thing the owner reads.
        unassigned.sort(key=lambda x: (
            1 if (((x.get("diagnosis") or {}).get("artifact") or {}).get("suspect")) else 0,
            -(x.get("ext_price") or 0)))
        _excluded_reps.sort(key=lambda x: -(x.get("ext_price") or 0))
        out["coverage"] = _coverage_block(plans, valid, out_rows, unassigned, _pay_cfg,
                                          _uses_bucket or _ct_mapped, _bucket_lines,
                                          excluded_reps=_excluded_reps, cov_cfg=_cov_cfg,
                                          store_res=_store_res, bridge=_bridge,
                                          store_market=store_market, acc_stamp=_acc_stamp)
        if _unmatched_rows is not None:
            out["coverage"]["unmatched_detail"] = _unmatched_rows
            out["coverage"]["unmatched_detail_excluded_lines"] = _unmatched_excluded_lines
    return out


def _coverage_block(plans, valid, out_rows, unassigned, pay_cfg, bucket_built, bucket_lines,
                    excluded_reps=None, cov_cfg=None, store_res="exact", bridge=None,
                    store_market=None, acc_stamp=None):
    """Diagnostics for "why doesn't my plan pay what I configured?" — PURE (everything passed in) and
    money-free: it reads the already-computed rows and never changes a payout. Returns
    {unassigned_reps, excluded_reps, orphan_assignments, stores, unmatched, contract_type,
     plan_warnings, settings}.

    `orphan_assignments` is the MIRROR of unassigned_reps and the other half of the owner's 2026-07-28
    report: an employee-scope assignment whose scope_value canon-matches NOBODY who sold this period. The
    bulk-assign roster shows such a person as "current plan ✓" because it compares roster-side values to
    roster-side values — it never checks the sales side — so an assignment can look applied while the
    engine pays nothing."""
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
        acc_rules = [r for r in rules
                     if (r.get("match_field") or "").strip().lower() == "accessory"]
        if acc_rules and isinstance(acc_stamp, dict) and not acc_stamp.get("yes"):
            warnings.append({
                "plan": nm, "severity": "high", "code": "accessory_rule_classifies_nothing",
                "message": (f"'{nm}' has {len(acc_rules)} rule(s) that pay on `accessory = yes`, but NOT "
                            f"ONE of this period's {len(valid)} sale lines is classified as an accessory "
                            f"by the PAY path — so those rules pay $0. The pay path reads the accessory "
                            f"DEPARTMENT / CATEGORY / product-keyword lists (Accessory settings) and the "
                            f"product catalog. It does NOT read the Accessory Definition mapping page "
                            f"unless this tenant's 'Accessory Definition decides pay' switch is turned on "
                            f"(Commissions -> Accessory Definition). Turn that switch on, or add the "
                            f"tenant's real category/department spellings to Accessory settings.")})
        if not rules:
            warnings.append({"plan": nm, "severity": "high", "code": "plan_without_rules",
                             "message": f"'{nm}' has no rules — every rep it covers pays $0."})
        if not (p.get("assignments") or []):
            warnings.append({"plan": nm, "severity": "medium", "code": "plan_without_assignment",
                             "message": f"'{nm}' has no assignments — it covers nobody."})
    # ORPHAN ASSIGNMENTS — assigned to a name nobody sold under this period.
    sellers = {}
    for r in valid:
        nm = str(r.get("salesperson") or "").strip()
        if nm:
            sellers.setdefault(_canon_person(nm), nm)
    orphans = []
    for p in plans:
        if not p.get("is_active", True):
            continue
        for a in (p.get("assignments") or []):
            if (a.get("scope") or "").strip().lower() != "employee":
                continue
            sv = str(a.get("scope_value") or "").strip()
            if not sv or _canon_person(sv) in sellers:
                continue
            near = sorted(({"rep": disp, "score": _name_score(sv, disp)} for disp in sellers.values()),
                          key=lambda x: (-x["score"], x["rep"]))
            near = [n for n in near if n["score"] >= 0.34][:3]
            orphans.append({
                "plan_id": p.get("id"), "plan_name": p.get("name"), "scope_value": sv,
                "nearest_sellers": near,
                "message": ((f"'{sv}' is assigned to plan '{p.get('name')}' but nobody sold under that "
                             f"name this period. Closest seller(s): "
                             + ", ".join(f"'{n['rep']}' ({int(round(n['score'] * 100))}%)" for n in near)
                             + " — the POS spells the name differently, so the plan never attaches.")
                            if near else
                            (f"'{sv}' is assigned to plan '{p.get('name')}' but has no sales this "
                             f"period under that name (nothing to pay, or a different POS spelling).")),
            })
    orphans.sort(key=lambda x: (-(x["nearest_sellers"][0]["score"] if x["nearest_sellers"] else 0),
                                str(x.get("scope_value") or "")))
    orphans = orphans[:100]

    # STORE BRIDGE per distinct POS store string — what resolves today vs what the /store-match alias
    # table WOULD resolve (the store_resolution='alias' preview).
    store_counts = {}
    for r in valid:
        s = str(r.get("store") or "").strip()
        if s:
            store_counts[s] = store_counts.get(s, 0) + 1
    store_rows, n_unmapped, n_would = [], 0, 0
    for s, n in sorted(store_counts.items(), key=lambda x: (-x[1], x[0]))[:150]:
        t = _store_trace(store_market or {}, bridge, s)
        would = bool(t.get("alias_market")) and not t.get("exact_market")
        if not t.get("exact_market"):
            n_unmapped += 1
        if would:
            n_would += 1
        store_rows.append({"store": s, "lines": n, "status": t.get("status"),
                           "market": t.get("exact_market"), "alias_market": t.get("alias_market"),
                           "alias": t.get("alias"), "would_resolve_with_alias": would,
                           "message": t.get("message")})

    _excl = excluded_reps or []
    return {
        "unassigned_reps": unassigned,
        "unassigned_count": len(unassigned),
        "unassigned_ext_price": round(sum(x.get("ext_price") or 0 for x in unassigned), 2),
        # Part D — sellers the TENANT marked "not a commissionable seller". Reported, never hidden.
        "excluded_reps": _excl,
        "excluded_count": len(_excl),
        "excluded_ext_price": round(sum(x.get("ext_price") or 0 for x in _excl), 2),
        "excluded_config": {"sellers": list(((cov_cfg or {}).get("excluded_sellers") or [])),
                            "ready": bool((cov_cfg or {}).get("ready"))},
        "orphan_assignments": orphans,
        "orphan_count": len(orphans),
        "stores": {"mode": store_res, "rows": store_rows, "distinct": len(store_counts),
                   "unresolved": n_unmapped, "would_resolve_with_alias": n_would,
                   "bridge_available": bridge is not None},
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


# ── "LINES NOT PAYING" EXPLORER (mod-commission 2026-07-28) ──────────────────────────────────────
UNMATCHED_LINE_CAP_DEFAULT = 500
UNMATCHED_LINE_CAP_MAX = 5000

# group_by -> the record keys the group is keyed on. 'category' is the default because that is the
# grain a plan rule is normally written at.
UNMATCHED_GROUP_BY = {
    "department": ("department",),
    "category": ("department", "category"),
    "product": ("department", "category", "product_desc"),
    "contract_type": ("contract_type",),
    "rep": ("rep",),
    "store": ("store",),
}
_UNMATCHED_FACETS = ("rep", "store", "market", "department", "category", "contract_type", "why")


def _match_list(v):
    """A filter value list -> a lower-cased set.

    Accepts a list (the endpoint passes repeatable query params) or a PIPE-joined string. The separator
    is deliberately '|' and NOT ',': raw_sales.salesperson is "Last, First" and store strings carry
    commas too, so comma-splitting would shred every real filter value ("Office, Back" -> office/back)."""
    if v is None:
        return set()
    if isinstance(v, str):
        v = v.split("|")
    return {str(x).strip().lower() for x in v if str(x or "").strip()}


def unmatched_explorer(client, org_id, period, filters=None, group_by="category",
                       line_limit=UNMATCHED_LINE_CAP_DEFAULT):
    """READ-ONLY: every sale line this period that is NOT considered for a commission payout, grouped so
    the gap is actionable. Writes nothing and triggers no calculation.

    Two populations, each line tagged with `why`:
      • rep_unassigned   — the seller has NO commission plan attached, so none of their lines are even
                           evaluated (they pay $0 legitimately-but-silently);
      • no_rule_matched  — the seller HAS a plan, but no rule in it matched the line.
    Voided and Return lines are excluded by the SAME gate the pay path uses, because the whole population
    comes from `preview(coverage=True, unmatched_detail=True)` — the money engine itself. Nothing here
    re-implements rule matching, so "considered for commission" cannot drift from what pays.

    Every group also reports which REAL plan rules match its lines (evaluated with `_rule_matches` on the
    group's own record), so "no rule references this category" is a fact rather than a guess.

    Returns {ready, period, totals, groups, facets, lines, line_cap, line_total, truncated, note}.
    The LINE-LEVEL payload is capped (`line_cap`) and the cap is always reported alongside `line_total`
    — there is no silent truncation. Group aggregates are computed over ALL filtered lines."""
    filters = filters or {}
    group_by = (group_by or "category").strip().lower()
    if group_by not in UNMATCHED_GROUP_BY:
        group_by = "category"
    try:
        cap = int(line_limit or UNMATCHED_LINE_CAP_DEFAULT)
    except (TypeError, ValueError):
        cap = UNMATCHED_LINE_CAP_DEFAULT
    cap = max(0, min(cap, UNMATCHED_LINE_CAP_MAX))

    prev = preview(client, org_id, period, coverage=True, unmatched_detail=True)
    cov = prev.get("coverage") or {}
    rows = cov.get("unmatched_detail") or []
    if not prev.get("ready"):
        return {"ready": False, "period": period, "note": prev.get("note"), "group_by": group_by,
                "totals": {}, "groups": [], "facets": {}, "lines": [], "line_cap": cap,
                "line_total": 0, "truncated": False}

    # FACETS come from the UNFILTERED population so the pickers always offer every value present
    # (pick-don't-type, §3b) even after a filter narrows the table.
    facets = {f: {} for f in _UNMATCHED_FACETS}
    for r in rows:
        for f in _UNMATCHED_FACETS:
            v = str(r.get(f) or "").strip()
            key = v if v else "(blank)"
            facets[f][key] = facets[f].get(key, 0) + 1

    want = {f: _match_list(filters.get(f)) for f in _UNMATCHED_FACETS}
    product_q = str(filters.get("product") or "").strip().lower()

    def _keep(r):
        for f, sel in want.items():
            if not sel:
                continue
            v = str(r.get(f) or "").strip().lower()
            if (v or "(blank)") not in sel and v not in sel:
                return False
        if product_q and product_q not in str(r.get("product_desc") or "").lower():
            return False
        return True

    filtered = [r for r in rows if _keep(r)]

    keys = UNMATCHED_GROUP_BY[group_by]
    groups = {}
    for r in filtered:
        k = tuple(str(r.get(x) or "").strip() for x in keys)
        g = groups.get(k)
        if g is None:
            g = groups[k] = {"key": dict(zip(keys, k)), "lines": 0, "ext_price": 0.0, "gp": 0.0,
                             "reps": set(), "why": {}, "_sample": r}
        g["lines"] += 1
        g["ext_price"] += safe_float(r.get("ext_price"))
        g["gp"] += safe_float(r.get("gp"))
        if r.get("rep"):
            g["reps"].add(r["rep"])
        w = r.get("why") or "?"
        g["why"][w] = g["why"].get(w, 0) + 1

    plans, plans_ready = _load_plans(client, org_id)
    active = [p for p in plans if p.get("is_active", True)] if plans_ready else []

    out_groups = []
    for g in groups.values():
        sample = g.pop("_sample")
        hits = []
        for p in active:
            for rule in (p.get("rules") or []):
                try:
                    ok = _rule_matches(sample, rule)
                except Exception:
                    ok = False
                if ok:
                    hits.append({"plan_id": p.get("id"), "plan_name": p.get("name"),
                                 "rule_id": rule.get("id"),
                                 "label": rule.get("label") or rule.get("match_value")
                                 or rule.get("match_field"),
                                 "match_field": rule.get("match_field") or "any",
                                 "match_op": rule.get("match_op") or "equals",
                                 "match_value": rule.get("match_value"),
                                 "payout_kind": rule.get("payout_kind")})
        label = " · ".join(v or "(blank)" for v in
                           (str(g["key"].get(x) or "") for x in keys))
        n_un = g["why"].get("rep_unassigned", 0)
        n_nr = g["why"].get("no_rule_matched", 0)
        if hits and n_un and not n_nr:
            sug = (f"a rule already matches these lines — plan '{hits[0]['plan_name']}' rule "
                   f"'{hits[0]['label']}' ({hits[0]['match_field']} {hits[0]['match_op']} "
                   f"'{hits[0]['match_value']}'). They are unpaid only because the seller has no plan "
                   f"attached. Attach a plan to the rep(s) above.")
        elif hits:
            sug = (f"plan '{hits[0]['plan_name']}' rule '{hits[0]['label']}' "
                   f"({hits[0]['match_field']} {hits[0]['match_op']} '{hits[0]['match_value']}') matches "
                   f"these lines, but it is not in the plan these reps are on — add an equivalent rule to "
                   f"their plan, or move them to that plan.")
        else:
            sug = (f"NO rule in any active plan references {label} — add a rule in the plan editor "
                   f"(match on {keys[-1].replace('_', ' ')} '{g['key'].get(keys[-1]) or '(blank)'}'), or "
                   f"classify these products in the accessory catalog so an accessory rule can pay them.")
        out_groups.append({**g["key"], "label": label, "lines": g["lines"],
                           "ext_price": round(g["ext_price"], 2), "gp": round(g["gp"], 2),
                           "reps": len(g["reps"]), "why": g["why"],
                           "rep_unassigned_lines": n_un, "no_rule_matched_lines": n_nr,
                           "matching_rules": hits[:5], "matching_rule_count": len(hits),
                           "suggestion": sug})
    out_groups.sort(key=lambda x: (-(x.get("ext_price") or 0), -(x.get("lines") or 0), x["label"]))

    by_why = {}
    for r in filtered:
        w = r.get("why") or "?"
        b = by_why.setdefault(w, {"lines": 0, "ext_price": 0.0, "gp": 0.0})
        b["lines"] += 1
        b["ext_price"] += safe_float(r.get("ext_price"))
        b["gp"] += safe_float(r.get("gp"))
    for b in by_why.values():
        b["ext_price"] = round(b["ext_price"], 2)
        b["gp"] = round(b["gp"], 2)

    lines = [{k: v for k, v in r.items() if not k.startswith("_")} for r in filtered[:cap]]
    return {
        "ready": True, "period": period, "group_by": group_by, "note": prev.get("note"),
        "totals": {
            "lines": len(filtered), "lines_unfiltered": len(rows),
            "ext_price": round(sum(safe_float(r.get("ext_price")) for r in filtered), 2),
            "gp": round(sum(safe_float(r.get("gp")) for r in filtered), 2),
            "reps": len({r.get("rep") for r in filtered if r.get("rep")}),
            "groups": len(out_groups), "by_why": by_why,
            "sale_lines": (prev.get("totals") or {}).get("sale_lines"),
            "excluded_seller_lines": cov.get("unmatched_detail_excluded_lines", 0),
        },
        "groups": out_groups,
        "facets": {f: sorted(({"value": k, "lines": n} for k, n in facets[f].items()),
                             key=lambda x: (-x["lines"], x["value"]))[:300]
                   for f in _UNMATCHED_FACETS},
        "lines": lines, "line_cap": cap, "line_total": len(filtered),
        "truncated": len(filtered) > cap,
        "plans_ready": plans_ready,
    }
