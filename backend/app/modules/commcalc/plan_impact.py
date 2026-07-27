"""Rule-change BLAST RADIUS + unpaid-activation warnings for the Commission-Plan engine.

WHY THIS EXISTS (owner ruling 2026-07-27, the "edge" reclassification)
─────────────────────────────────────────────────────────────────────
A commission rule named "edge" was matching the KEYWORD "edge" in the product description — and
"Motorola Edge 2025" is a phone MODEL name. "Edge" as a pay bucket means the device-FINANCING program,
so it must key on the sale's TENDER METHOD, not on a word that happens to appear in a handset's name.

Fixing that is a CONFIG edit (one row in `commcalc.commission_rule`). What was missing is the ability to
SEE what such an edit does to real people's pay BEFORE making it, and the ability to notice afterwards
that an activation now pays nothing at all. Two structural facts make both blind spots dangerous:

  1. THE PLAN ENGINE HAS NO EXCLUSIVITY. `commission_engine.preview` evaluates EVERY rule against EVERY
     line independently — there is no first-match-wins. So a line that stops matching rule A is not
     "released" to rule B; it simply stops earning A's dollars.
  2. THE MULTI-MONTH INCENTIVE IS A SEPARATE, ADDITIVE COMPONENT. `_apply_new_engines` computes
     `total_payout = plan_comm + residual_installment_comm + installment_comm_sale`. A sale-triggered
     installment chain starts only when a `plan_installment_schedule`'s OWN trigger matcher hits the
     line. So "these should qualify for the multi-month incentive instead" is only true if a schedule
     trigger actually matches them — it does NOT follow automatically from turning a rule off.

Everything in this module is READ-ONLY. It writes nothing, triggers no calculation, and computes no new
pay math: it drives the REAL `commission_engine.preview` and the REAL `_rule_matches` the money path uses,
so a number here cannot drift from what a recalculation would produce.
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc import commission_engine
from app.modules.commcalc.commission_engine import (
    _load_plans, _read_sales, _resolve_plan_for, _read_store_market, _read_employee_roles,
    _canon_person, _rule_matches, _activation_buckets,
)
from app.modules.commcalc.gp_report import is_voided as _is_voided

# The warning payload is stored in calc_status.calc_warnings (mig 243) and rendered on the CommCalc
# dashboard. Bounded so a pathological month can never bloat the status row.
MAX_WARN_GROUPS = 200
MAX_SAMPLES = 10


def _line_key(row):
    """Stable identity for one sale line inside one period read (trans_id + product + sku + serial + mdn).
    raw_sales has no per-line primary key exposed through the REST client, and the SAME period is read
    twice (baseline + candidate), so `id(row)` cannot be used across the two passes."""
    return (str(row.get("trans_id") or "").strip(),
            str(row.get("product_desc") or "").strip(),
            str(row.get("sku") or "").strip(),
            str(row.get("serial_1") or "").strip(),
            str(row.get("mdn") or "").strip(),
            str(row.get("trans_date") or "")[:10],
            round(safe_float(row.get("ext_price")), 2))


def _valid_sales(client, org_id, period):
    """The exact line set the plan engine pays on: not voided, not a Return (same gate as preview)."""
    return [r for r in _read_sales(client, org_id, period)
            if not _is_voided(r.get("voided"))
            and str(r.get("trans_type", "") or "").strip() != "Return"]


def _load_sale_schedules(client, org_id):
    """Active sale-triggered installment schedules, as {plan_id: [schedule, …]}. Degrades to {} when
    migration 201 isn't applied. Never raises."""
    try:
        from app.modules.commcalc.sale_installment_engine import _load_schedules
        scheds, _lines = _load_schedules(client, org_id)
    except Exception:
        return {}
    by_plan = {}
    for s in (scheds or []):
        by_plan.setdefault(s.get("plan_id"), []).append(s)
    return by_plan


def _sched_trigger(sched):
    return {"match_field": sched.get("trigger_match_field"),
            "match_op": sched.get("trigger_match_op"),
            "match_value": sched.get("trigger_match_value")}


def _multimonth_hit(row, scheds):
    """The FIRST active schedule whose trigger matches this line, else None. Uses the engine's own
    `_rule_matches`, i.e. the identical evaluation `compute_sale_installments` performs."""
    for s in (scheds or []):
        if s.get("is_active") is False:
            continue
        try:
            if _rule_matches(row, _sched_trigger(s)):
                return s
        except Exception:
            continue
    return None


def _rep_context(client, org_id, rows, plans):
    """({rep_upper -> plan}, {rep_upper -> store}) using the SAME assignment precedence the money path
    uses (employee > role > store > market > default)."""
    store_market = _read_store_market(client, org_id)
    role_by_rep = _read_employee_roles(client, org_id)
    by_rep_store, plan_by_rep = {}, {}
    for r in rows:
        rep = str(r.get("salesperson", "") or "").strip()
        if not rep or rep.lower() == "admin":
            continue
        k = rep.upper()
        if k not in by_rep_store:
            by_rep_store[k] = (rep, str(r.get("store", "") or "").strip())
    for k, (rep, store) in by_rep_store.items():
        market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
        plan_by_rep[k] = _resolve_plan_for(rep, store, market, plans,
                                           rep_role=role_by_rep.get(_canon_person(rep)))
    return plan_by_rep, {k: v[1] for k, v in by_rep_store.items()}


# ── 1. BLAST RADIUS: what would this matcher change do? ──────────────────────────────────────────
def rule_impact(client, org_id, period, overrides, only_rep=None):
    """READ-ONLY before/after for a proposed commission-rule matcher change.

    `overrides` = {rule_id: {match_field?, match_op?, match_value?, qualifies?, disabled?}} — exactly the
    shape `commission_engine.preview(rule_overrides=…)` applies to its in-memory plan copy.

    Returns per-rep payout deltas, per-rule matched-line deltas, and — for every line the change STOPS
    paying — whether a multi-month installment schedule under that rep's plan would pick it up. That last
    column is the honest answer to "these should qualify for the multi-month incentive instead": it is
    true only if a schedule trigger actually matches the line.
    """
    base = commission_engine.preview(client, org_id, period, detail=True, only_rep=only_rep)
    cand = commission_engine.preview(client, org_id, period, detail=True, only_rep=only_rep,
                                     rule_overrides=overrides)
    if not base.get("ready"):
        return {"ready": False, "period": period, "note": base.get("note"), "by_rep": [], "totals": {}}

    sched_by_plan = _load_sale_schedules(client, org_id)

    def _lines_by_rule(prev):
        out = {}
        for r in (prev.get("by_rep") or []):
            rep = str(r.get("rep") or "")
            for rb in (r.get("rules") or []):
                out[(rep, str(rb.get("rule_id")))] = rb
        return out

    b_rules, c_rules = _lines_by_rule(base), _lines_by_rule(cand)
    b_rep = {str(r.get("rep") or ""): r for r in (base.get("by_rep") or [])}
    c_rep = {str(r.get("rep") or ""): r for r in (cand.get("by_rep") or [])}

    # sale lines, indexed by the stable line key, so a "freed" drill row can be re-tested against the
    # installment triggers with the SAME row dict the engine saw.
    rows = _valid_sales(client, org_id, period)
    row_by_key = {}
    for r in rows:
        row_by_key.setdefault(_join_key(r), r)

    by_rep, freed_rows, gained_rows = [], [], []
    n_freed_covered = n_freed_orphan = 0
    for rep in sorted(set(b_rep) | set(c_rep)):
        b, c = b_rep.get(rep) or {}, c_rep.get(rep) or {}
        before = round(safe_float(b.get("total_payout")), 2)
        after = round(safe_float(c.get("total_payout")), 2)
        plan_id = c.get("plan_id") or b.get("plan_id")
        scheds = sched_by_plan.get(plan_id) or []
        rule_rows = []
        for rid in {str(k[1]) for k in list(b_rules) + list(c_rules) if k[0] == rep}:
            rb, rc = b_rules.get((rep, rid)) or {}, c_rules.get((rep, rid)) or {}
            if not rb and not rc:
                continue
            # MULTISET diff, not a set diff. One activation legitimately rings SEVERAL lines that are
            # identical on every visible column but one (the owner's trans 4045 rings a "Port with IDV
            # AAL" line and a "Port with IDV" line for the same device, same MDN, same $0 price) — a set
            # diff silently reports 4 freed lines where 5 stopped paying, i.e. it under-states the money.
            b_lines, c_lines = _detail_index(rb.get("lines")), _detail_index(rc.get("lines"))
            lost = {k: len(v) - len(c_lines.get(k, ())) for k, v in b_lines.items()
                    if len(v) > len(c_lines.get(k, ()))}
            gained = {k: len(v) - len(b_lines.get(k, ())) for k, v in c_lines.items()
                      if len(v) > len(b_lines.get(k, ()))}
            if (rb.get("payout") or 0) == (rc.get("payout") or 0) and not lost and not gained:
                continue
            rule_rows.append({
                "rule_id": rid,
                "label": rb.get("label") or rc.get("label"),
                "before": {"match_field": rb.get("match_field"), "match_op": rb.get("match_op"),
                           "match_value": rb.get("match_value"),
                           "matched_lines": rb.get("matched_lines") or 0,
                           "payout": round(safe_float(rb.get("payout")), 2)},
                "after": {"match_field": rc.get("match_field"), "match_op": rc.get("match_op"),
                          "match_value": rc.get("match_value"),
                          "matched_lines": rc.get("matched_lines") or 0,
                          "payout": round(safe_float(rc.get("payout")), 2)},
            })
            for lk in sorted(lost):
                det = b_lines[lk][0]
                src = row_by_key.get(lk)
                hit = _multimonth_hit(src, scheds) if src is not None else None
                for _ in range(lost[lk]):
                    if hit:
                        n_freed_covered += 1
                    else:
                        n_freed_orphan += 1
                    freed_rows.append({
                        "rep": rep, "rule_id": rid, "rule_label": rb.get("label"),
                        "date": det.get("date"), "trans_id": det.get("trans_id"),
                        "imei": det.get("imei"), "mdn": det.get("mdn"),
                        "product": det.get("product"), "contract_type": det.get("contract_type"),
                        "tender_type": (src or {}).get("tender_type"),
                        "ext_price": det.get("ext_price"), "gp": det.get("gp"),
                        "lost_amount": round(safe_float(det.get("amount")), 2),
                        "multimonth_schedule": ((hit.get("name") or hit.get("id")) if hit else None),
                        "multimonth_trigger": (f"{hit.get('trigger_match_field')} "
                                               f"{hit.get('trigger_match_op')} "
                                               f"{hit.get('trigger_match_value')}") if hit else None,
                        "pays_nothing_after": not hit,
                    })
            for lk in sorted(gained):
                det = c_lines[lk][0]
                for _ in range(gained[lk]):
                    gained_rows.append({
                        "rep": rep, "rule_id": rid, "rule_label": rc.get("label"),
                        "date": det.get("date"), "trans_id": det.get("trans_id"),
                        "product": det.get("product"), "contract_type": det.get("contract_type"),
                        "tender_type": (row_by_key.get(lk) or {}).get("tender_type"),
                        "ext_price": det.get("ext_price"), "gp": det.get("gp"),
                        "gained_amount": round(safe_float(det.get("amount")), 2),
                    })
        if before == after and not rule_rows:
            continue
        by_rep.append({"rep": rep, "store": c.get("store") or b.get("store"),
                       "plan_name": c.get("plan_name") or b.get("plan_name"),
                       "before": before, "after": after, "delta": round(after - before, 2),
                       "rules": sorted(rule_rows, key=lambda x: x["label"] or "")})

    by_rep.sort(key=lambda x: x["delta"])
    b_tot = round(safe_float((base.get("totals") or {}).get("payout")), 2)
    c_tot = round(safe_float((cand.get("totals") or {}).get("payout")), 2)
    return {
        "ready": True, "period": period, "org_id": org_id,
        "totals": {"before": b_tot, "after": c_tot, "delta": round(c_tot - b_tot, 2),
                   "reps_affected": len(by_rep),
                   "lines_freed": len(freed_rows), "lines_gained": len(gained_rows),
                   "freed_covered_by_multimonth": n_freed_covered,
                   "freed_paying_nothing": n_freed_orphan,
                   "sale_lines": (base.get("totals") or {}).get("sale_lines")},
        "by_rep": by_rep,
        "freed_lines": freed_rows[:2000],
        "gained_lines": gained_rows[:2000],
        "note": ("Plan rules have NO exclusivity and the multi-month engine is a SEPARATE additive "
                 "component: a line that stops matching a rule is only re-paid if an installment "
                 "schedule's own trigger matches it. `freed_paying_nothing` is the count that would "
                 "pay $0 from every configured source after this change."),
    }


def _join_key(det):
    """The identity a drill-down detail row and its raw_sales row share. Deliberately NOT unique — one
    activation can ring several lines identical on every column the detail row carries — so callers must
    treat it as a MULTISET key (see `_detail_index`) and never as a primary key."""
    return (str(det.get("trans_id") or "").strip(),
            str(det.get("product") or det.get("product_desc") or "").strip(),
            str(det.get("contract_type") or "").strip(),
            str(det.get("imei") or det.get("serial_1") or "").strip(),
            str(det.get("mdn") or "").strip(),
            str(det.get("date") or det.get("trans_date") or "")[:10],
            round(safe_float(det.get("ext_price")), 2),
            round(safe_float(det.get("gp")), 2))


def _detail_index(lines):
    """{join key -> [detail rows]} preserving multiplicity."""
    out = {}
    for l in (lines or []):
        out.setdefault(_join_key(l), []).append(l)
    return out


# ── 2. CALC WARNINGS: activations that no configured source pays ─────────────────────────────────
def pay_warnings(client, org_id, period, max_groups=MAX_WARN_GROUPS):
    """READ-ONLY: activations that NO commission-plan rule and NO installment-schedule trigger matched.

    Grain = the ACTIVATION (one representative line per activation, via the SHARED activation-bucket
    resolver every display surface already uses), grouped by transaction. An activation appears here only
    when its rep HAS a plan attached — a rep with no plan at all is a different, already-reported gap
    (`commission-plans/coverage` → `unassigned`), and is summarised separately.

    Returns [] for any tenant with no commission plans (every Boost/house rep) → the Boost path never
    sees a warning it cannot act on.
    """
    plans, ready = _load_plans(client, org_id)
    if not ready or not plans:
        return []
    rows = _valid_sales(client, org_id, period)
    if not rows:
        return []
    plan_by_rep, store_by_rep = _rep_context(client, org_id, rows, plans)
    sched_by_plan = _load_sale_schedules(client, org_id)

    # The activation bucket comes from the tenant's OWN classification config (mig 213 + 224) — the same
    # resolver the Sales Report / Exec MTD / Daily Targets use, collapsed to ONE representative line per
    # rescued transaction, so a 3-line activation is reported once, not three times.
    try:
        buckets = _activation_buckets(client, org_id, rows)
    except Exception:
        buckets = [None] * len(rows)

    groups = {}
    for row, bucket in zip(rows, buckets):
        if not bucket:
            continue
        rep = str(row.get("salesperson", "") or "").strip()
        if not rep or rep.lower() == "admin":
            continue
        plan = plan_by_rep.get(rep.upper())
        if not plan:
            continue                        # no plan attached = the 'unassigned' gap, not this warning
        paid_by = None
        for rule in (plan.get("rules") or []):
            if not bool(rule.get("qualifies", True)):
                continue
            try:
                if _rule_matches(row, rule):
                    paid_by = "rule"
                    break
            except Exception:
                continue
        if paid_by:
            continue
        if _multimonth_hit(row, sched_by_plan.get(plan.get("id")) or []):
            continue
        tid = str(row.get("trans_id") or "").strip() or f"line:{_line_key(row)}"
        g = groups.setdefault((rep.upper(), tid), {
            "type": "activation_pays_nothing", "rep": rep, "store": store_by_rep.get(rep.upper()),
            "plan_name": plan.get("name"), "trans_id": tid,
            "date": str(row.get("trans_date") or "")[:10], "activations": 0, "ext_price": 0.0,
            "samples": [],
        })
        g["activations"] += 1
        g["ext_price"] = round(g["ext_price"] + safe_float(row.get("ext_price")), 2)
        if len(g["samples"]) < 3:
            g["samples"].append({
                "product": row.get("product_desc"), "contract_type": row.get("contract_type"),
                "activation_bucket": bucket, "tender_type": row.get("tender_type"),
                "imei": str(row.get("serial_1") or "").strip(), "mdn": str(row.get("mdn") or "").strip(),
            })
    out = sorted(groups.values(), key=lambda g: (-(g.get("ext_price") or 0), g.get("trans_id") or ""))
    for g in out:
        g["detail"] = (f"{g['activations']} activation(s) on transaction {g['trans_id']} matched no rule "
                       f"in plan “{g['plan_name']}” and no multi-month schedule trigger — they pay $0 "
                       f"from every configured source.")
    return out[:max_groups]


def calc_warning_payload(client, org_id, period):
    """The full `calc_status.calc_warnings` payload written after a successful calculation (mig 243).

    Best-effort by construction: every section is independently guarded, so a diagnostic that cannot be
    computed degrades to a note instead of failing a calculation that has already written pay.
    """
    payload = {"period": period, "generated_for": org_id, "unpaid_activations": [],
               "unassigned_reps": [], "installment_warnings": [], "counts": {}}
    try:
        payload["unpaid_activations"] = pay_warnings(client, org_id, period)
    except Exception as e:
        payload["unpaid_activations_error"] = str(e)
    try:
        from app.modules.commcalc import commission_engine as _ce
        prev = _ce.preview(client, org_id, period, coverage=True)
        cov = prev.get("coverage") or {}
        payload["unassigned_reps"] = [
            {"rep": u.get("rep"), "store": u.get("store"), "lines": u.get("lines"),
             "transactions": u.get("transactions"), "ext_price": u.get("ext_price"),
             "reason": u.get("reason")}
            for u in (cov.get("unassigned") or [])][:MAX_WARN_GROUPS]
        payload["plan_warnings"] = (cov.get("warnings") or [])[:MAX_WARN_GROUPS]
    except Exception as e:
        payload["coverage_error"] = str(e)
    try:
        from app.modules.commcalc import sale_installment_engine as _sie
        sr = _sie.compute_sale_installments(client, org_id, period, persist=False)
        payload["installment_warnings"] = (sr.get("warnings") or [])[:MAX_WARN_GROUPS]
    except Exception as e:
        payload["installment_error"] = str(e)
    payload["counts"] = {
        "unpaid_activations": len(payload.get("unpaid_activations") or []),
        "unassigned_reps": len(payload.get("unassigned_reps") or []),
        "installment_warnings": len(payload.get("installment_warnings") or []),
        "plan_warnings": len(payload.get("plan_warnings") or []),
    }
    if not any(payload["counts"].values()):
        return None
    return payload


# ── 3. MODEL-NAME COLLISION AUDIT (the "edge" bug class, tenant-agnostic) ────────────────────────
def keyword_collision_audit(client, org_id, period="", limit_rules=200):
    """READ-ONLY: every commission rule / installment trigger whose matcher is a product-description
    (or SKU) `contains` PATTERN, with the DISTINCT item descriptions it actually hits and whether the same
    pattern also occurs as a value of another match field (tender_type / department / category /
    contract_type / trans_type).

    This is the generic form of the "edge" bug: a keyword chosen to name a PAY PROGRAM that also happens to
    appear inside a device MODEL name. Nothing is hard-coded about "edge" — the collision is computed from
    the tenant's own data, so it flags the same class for any tenant and any keyword.
    """
    if not str(period or "").strip():
        return {"ready": False, "note": "period required (the audit reads that period's sale lines)",
                "rules": []}
    plans, ready = _load_plans(client, org_id)
    if not ready:
        return {"ready": False, "note": "migration 059 not applied", "rules": []}
    rows = _valid_sales(client, org_id, period)
    sched_by_plan = _load_sale_schedules(client, org_id)
    matchers = []
    for p in plans:
        for r in (p.get("rules") or [])[:limit_rules]:
            matchers.append({"kind": "rule", "plan": p.get("name"), "id": str(r.get("id")),
                             "label": r.get("label"), "match_field": r.get("match_field"),
                             "match_op": r.get("match_op"), "match_value": r.get("match_value"),
                             "payout_kind": r.get("payout_kind"), "amount": safe_float(r.get("amount")),
                             "pct": safe_float(r.get("pct"))})
        for s in (sched_by_plan.get(p.get("id")) or []):
            matchers.append({"kind": "installment_trigger", "plan": p.get("name"), "id": str(s.get("id")),
                             "label": s.get("name"), "match_field": s.get("trigger_match_field"),
                             "match_op": s.get("trigger_match_op"),
                             "match_value": s.get("trigger_match_value"),
                             "payout_kind": "multi_month", "amount": 0.0, "pct": 0.0})

    other_fields = ("tender_type", "department", "category", "contract_type", "trans_type")
    out = []
    for m in matchers:
        f = str(m.get("match_field") or "any").strip().lower()
        op = str(m.get("match_op") or "equals").strip().lower()
        val = str(m.get("match_value") or "").strip().lower()
        if f not in ("product_desc", "sku") or op != "contains" or not val:
            continue
        hits, tx = {}, set()
        for r in rows:
            have = str(r.get(f, "") or "").strip().lower()
            if val and val in have:
                d = str(r.get("product_desc") or "").strip()
                hits[d] = hits.get(d, 0) + 1
                t = str(r.get("trans_id") or "").strip()
                if t:
                    tx.add(t)
        collisions = []
        for of in other_fields:
            n = 0
            vals = set()
            for r in rows:
                hv = str(r.get(of, "") or "").strip()
                if hv and val in hv.lower():
                    n += 1
                    vals.add(hv)
            if n:
                collisions.append({"field": of, "lines": n, "values": sorted(vals)[:8]})
        out.append({**m, "matched_lines": sum(hits.values()), "matched_transactions": len(tx),
                    "distinct_items": len(hits),
                    "items": [{"product": k, "lines": v}
                              for k, v in sorted(hits.items(), key=lambda x: -x[1])[:MAX_SAMPLES]],
                    "also_a_value_of": collisions,
                    "suspect": bool(collisions) or len(hits) > 1})
    return {"ready": True, "period": period or "(all periods)", "org_id": org_id,
            "sale_lines": len(rows), "rules": out,
            "note": ("A `contains` pattern on the item description matches on WORDING, so it also catches "
                     "device MODEL names. `also_a_value_of` shows the same word appearing as a real value "
                     "of another match field — usually the field the rule should have keyed on.")}
