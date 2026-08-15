"""Management Incentive — pure computation (migration 852).

A per-MANAGER, store-AGGREGATED incentive: component payouts on production against per-store targets,
plus flat bonuses gated by qualification metrics. One framework for every management level; a plan is
assigned to a manager exactly like the employee commission plan (scope precedence
employee > role > store > market > default), and different plans can be set up for different levels.

This module is PURE — dicts in, dict out, no DB, no network — so it is unit-provable and the router can
feed it whatever actuals/metrics it has resolved. The DB shape (mig 852) maps 1:1 to the plan dict:

    plan = {
      "id", "name", "consolidated_bonus_amount",           # header
      "components": [ {label, kind('percent'|'per_unit'), rate, metric_source,
                       target_per_store, store_count|None, cap_at_target} ],
      "bonuses":    [ {label, kind('consolidated'|'inventory_selloff'|'flat'), amount,
                       gated_by('qualifiers'|'inventory_aging'|'manual'|'none'), config} ],
      "qualifiers": [ {metric_key, label, source, op('lt'|'lte'|'gt'|'gte'|'eq'), threshold,
                       unit, applies_to('consolidated')} ],
    }

MONEY POSTURE: computes numbers only. Persisting/paying is the router's job (writes
management_incentive_payout, draft→approved→paid). A missing metric FAILS a qualifier closed (a gate you
can't verify is not passed), and an unresolved actual counts as 0 production — neither ever silently
inflates pay.
"""

# Same precedence as the employee commission plan (commission_engine._resolve_plan_for) — assigning a
# specific manager is an 'employee'-scope row that beats a 'role'/'market'/'default' plan.
SCOPE_RANK = {"employee": 4, "role": 3, "store": 2, "market": 1, "default": 0}


def _canon(s):
    return " ".join(str(s or "").strip().lower().split())


def _canon_person(name):
    """Name-order-insensitive person key ('Last, First' == 'First Last'), mirroring the commission engine."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first.strip()} {last.strip()}"
    return " ".join(raw.lower().split())


def resolve_plan(plans, *, employee_name=None, role=None, store=None, market=None, store_keys=None):
    """The best-matching active plan for a manager, by SCOPE_RANK then priority (highest wins). Returns
    the plan dict or None. Mirrors commission_engine._resolve_plan_for so 'assign an employee to a plan'
    behaves identically to commissions."""
    rn = _canon_person(employee_name)
    rr = _canon(role)
    sv_store, sv_mkt = _canon(store), _canon(market)
    skeys = {_canon(k) for k in (store_keys or ()) if _canon(k)}
    best, best_key = None, (-1, -1)
    for p in plans:
        if not p.get("is_active", True):
            continue
        for a in p.get("assignments", []):
            scope = _canon(a.get("scope")) or "default"
            val = _canon(a.get("scope_value"))
            if scope == "employee":
                ok = bool(val) and _canon_person(a.get("scope_value")) == rn
            elif scope == "role":
                ok = bool(val) and bool(rr) and val == rr
            else:
                ok = (scope == "default"
                      or (scope == "store" and val and (val == sv_store or val in skeys))
                      or (scope == "market" and val and val == sv_mkt))
            if not ok:
                continue
            key = (SCOPE_RANK.get(scope, 0), int(a.get("priority") or 0))
            if key > best_key:
                best, best_key = p, key
    return best


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def component_payout(component, actual, manager_store_count):
    """One store-performance component. Payout = rate × actual (percent: actual is $, rate 0.02 = 2%;
    per_unit: actual is a count, rate is $/unit). The OPPORTUNITY (goal) = rate × (target_per_store ×
    store_count); when cap_at_target is set, the payout is capped at that opportunity. store_count on the
    component overrides the manager's own store count (some stores don't sell every line — accessory 7,
    VHI/Edge 6)."""
    rate = _num(component.get("rate"))
    tps = _num(component.get("target_per_store"))
    count = component.get("store_count")
    count = int(count) if count is not None else int(manager_store_count or 0)
    act = _num(actual)
    target_qty = tps * count                     # dollars (percent) or units (per_unit)
    opportunity = rate * target_qty              # full payout at 100% of target
    raw = rate * act
    capped = min(raw, opportunity) if (component.get("cap_at_target", True) and opportunity > 0) else raw
    attainment = (act / target_qty) if target_qty > 0 else None
    return {
        "label": component.get("label"),
        "kind": component.get("kind"),
        "metric_source": component.get("metric_source"),
        "rate": rate,
        "target_per_store": tps,
        "store_count": count,
        "target_qty": round(target_qty, 4),
        "actual": round(act, 4),
        "attainment": round(attainment, 4) if attainment is not None else None,
        "opportunity": round(opportunity, 2),
        "payout": round(max(0.0, capped), 2),
    }


def qualifier_pass(op, value, threshold):
    """Evaluate one gate. A missing value (None) FAILS CLOSED — a gate you can't measure is not passed."""
    if value is None or threshold is None:
        return False
    v, t = _num(value, None), _num(threshold, None)
    if v is None or t is None:
        return False
    return {
        "lt": v < t, "lte": v <= t, "gt": v > t, "gte": v >= t, "eq": v == t,
    }.get(str(op or "gte").lower(), False)


def evaluate_qualifiers(qualifiers, values, applies_to="consolidated"):
    """Evaluate every qualifier for a gate group. Returns (all_pass, rows). all_pass over an empty set is
    True (no gate defined = nothing to fail)."""
    rows, all_pass = [], True
    for q in qualifiers:
        if _canon(q.get("applies_to") or "consolidated") != _canon(applies_to):
            continue
        val = (values or {}).get(q.get("metric_key"))
        ok = qualifier_pass(q.get("op"), val, q.get("threshold"))
        all_pass = all_pass and ok
        rows.append({
            "metric_key": q.get("metric_key"),
            "label": q.get("label") or q.get("metric_key"),
            "op": q.get("op"), "threshold": _num(q.get("threshold"), None),
            "value": (None if val is None else _num(val, None)),
            "unit": q.get("unit"), "source": q.get("source"),
            "passed": ok,
        })
    return all_pass, rows


def compute_payout(plan, *, actuals=None, qualifier_values=None, manager_store_count=0,
                   derived=None, overrides=None):
    """Full per-manager payout for one plan + period. Inputs the router resolves:
      actuals          {metric_source: number}   store-performance production (e.g. accessory $, counts)
      qualifier_values {metric_key: number}       the qualification metrics (KPI %s, cash-deposit value)
      manager_store_count int                      the manager's store count (fallback for components)
      derived          {"inventory_aging": bool}   gate results the engine can't compute from a number
      overrides        {"consolidated_amount": n, "consolidated_earned": bool,
                        "bonus_earned": {label: bool}}   management edits from the statement
    Returns a breakdown dict with component/qualifier/bonus detail and the totals."""
    actuals = actuals or {}
    qualifier_values = qualifier_values or {}
    derived = derived or {}
    overrides = overrides or {}

    comp_rows, component_total = [], 0.0
    for c in sorted(plan.get("components", []), key=lambda x: x.get("sort", 0)):
        r = component_payout(c, actuals.get(c.get("metric_source")), manager_store_count)
        comp_rows.append(r)
        component_total += r["payout"]

    consolidated_pass, qual_rows = evaluate_qualifiers(
        plan.get("qualifiers", []), qualifier_values, applies_to="consolidated")

    bonus_rows, bonus_total = [], 0.0
    for b in sorted(plan.get("bonuses", []), key=lambda x: x.get("sort", 0)):
        kind = _canon(b.get("kind"))
        gated_by = _canon(b.get("gated_by") or "none")
        amount = _num(plan.get("consolidated_bonus_amount"), 0.0) if kind == "consolidated" else _num(b.get("amount"))
        if kind == "consolidated" and overrides.get("consolidated_amount") is not None:
            amount = _num(overrides["consolidated_amount"])

        if gated_by == "qualifiers":
            earned = consolidated_pass
        elif gated_by == "inventory_aging":
            earned = bool(derived.get("inventory_aging"))
        elif gated_by == "manual":
            earned = bool((overrides.get("bonus_earned") or {}).get(b.get("label")))
        else:  # 'none'
            earned = True
        # An explicit management override on the consolidated decision wins.
        if kind == "consolidated" and overrides.get("consolidated_earned") is not None:
            earned = bool(overrides["consolidated_earned"])

        pay = round(amount, 2) if earned else 0.0
        bonus_total += pay
        bonus_rows.append({
            "label": b.get("label"), "kind": b.get("kind"), "gated_by": b.get("gated_by"),
            "amount": round(amount, 2), "earned": earned, "payout": pay,
        })

    total = round(component_total + bonus_total, 2)
    return {
        "plan_id": plan.get("id"), "plan_name": plan.get("name"),
        "components": comp_rows,
        "qualifiers": qual_rows,
        "consolidated_qualified": consolidated_pass,
        "bonuses": bonus_rows,
        "component_total": round(component_total, 2),
        "bonus_total": round(bonus_total, 2),
        "total": total,
    }
