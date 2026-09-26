"""WHO IS READING A PAYOUT — every surface that shows an employee their own commission decides "which lines are
paid" and "which fields an employee may see" HERE, on the server (owner 2026-09-26, index §6i).

Owner, verbatim: *"on the employee commission payout report we only need o show the line they are getting paid
and other lines should be hidden and carrier commission not be displayed"* — and, through the coordinator, that
this is the design (CLAUDE.md "a fix is a DESIGN fix"): not one report, every surface.

THE CLASS. A surface that shows an employee their own commission answered "which lines are paid" and "which
fields may the employee see" on its own — or not at all: the Rep Incentive drill sent every matched line with
the sale line's Price / GP (on a carrier rebate or spiff line those ARE the carrier's payment to the store), the
statement carried the commission-ledger buckets (the carrier's statement, per rep), rep rows carried the
carrier-paid dealer figures, and a self-scoped rep could read any of it — for any rep.

THE THREE FACTS, ONE HOME EACH:
  · WHO — `resolve(requested, caller_is_self)`: a self-scoped caller (a rep) is ALWAYS 'employee'; anyone else
    gets the audience the page declares ('manager' by default — every existing caller byte-identical). The
    router's `_payout_audience` is the one place a handler supplies the caller.
  · WHICH LINES PAID — `is_paid_line(line)` READS the engine's verdict and decides nothing new: the pay gate
    (`plan_pay_gate.select_paying_lines`, one payment per activation event from `line_class.activation_events`)
    stamps a line it did not pay `suppressed`; a non-qualifying line is `qualifies: false`; `amount` is the money
    the engine put on the line (`flat_once` = a flat bonus paid once per rep). Paid = not suppressed, qualifies,
    and carries money. Hiding the rest cannot move a total: an unpaid line's amount is 0.
  · WHICH FIELDS — ALLOW-LISTS, not deny-lists: an employee payload carries ONLY the fields named below, at each
    level. A column added tomorrow (a new carrier figure) is therefore hidden from employees until someone adds it
    here on purpose. `disallowed_fields(payload, kind)` names anything outside the list (the proof and the lock
    use it); `KNOWN_CARRIER_FIELDS` is the sanity list the lock asserts is never allowed.

PURE; no I/O; no tenant, carrier or product name (RULE TWO). Lock: `harness_payout_audience_lock.py`.
"""
import copy

AUDIENCES = ("employee", "manager")
DEFAULT_AUDIENCE = "manager"

# ── THE EMPLOYEE ALLOW-LISTS ─────────────────────────────────────────────────────────────────────────
# a plan rule line (commission_engine.preview(detail=True) → rules[].lines[])
EMPLOYEE_LINE_FIELDS = ("date", "trans_id", "product", "contract_type", "imei", "mdn", "amount", "qualifies",
                        "flat_once", "event_id", "event_key", "event_key_kind", "event_type")
# a plan rule (its rate and what it paid — the rep's own terms)
EMPLOYEE_RULE_FIELDS = ("rule_id", "label", "payout_kind", "tiered", "qualifies", "matched_lines",
                        "qualifying_units", "payout", "match_field", "match_op", "match_value", "amount", "pct",
                        "unit_basis", "unit_basis_source", "scope_reason", "lines", "financing_tier",
                        "financing_unit_rate", "financing_attainment_pct")
# the plan component (which plan, via which assignment, the rep's totals)
EMPLOYEE_PLAN_FIELDS = ("plan_name", "plan_id", "assignment", "considered", "rules", "base_payout",
                        "tiered_payout", "tier_multiplier", "base_tier_metric", "tiers", "qualifying_units",
                        "total_payout", "store", "market", "has_sale_lines", "diagnosis", "zero_diagnosis",
                        "acc_comm", "setup_fee_comm")
# a multi-month device and one of its installments (the rep's installment pay)
EMPLOYEE_DEVICE_FIELDS = ("imei", "mdn", "trans_id", "sale_period", "product", "contract_type", "device_product",
                          "plan_product", "device_category", "label", "installments")
EMPLOYEE_INSTALLMENT_FIELDS = ("label", "device_product", "plan_product", "device_category", "month_index",
                               "pay_period", "sale_period", "amount", "withheld_amount", "status", "paid",
                               "hold_reason", "hold_detail", "mrc_at_pay", "payout_kind", "zero_note",
                               "expected_amount", "expected_in_window", "promoted")
EMPLOYEE_MULTIMONTH_FIELDS = ("devices", "totals", "schedules", "note", "warnings")
# the drill-down's top level (the router's additions included: mtd_breakdown is count × rate + accessory %)
EMPLOYEE_EXPLAIN_FIELDS = ("period", "rep", "carrier_mode", "plan_component", "multimonth_component",
                           "reconciliation", "zero_explanation", "note", "mtd_breakdown", "mtd_supersedes_rules",
                           "audience")
# a rep_commissions row — the rep's pay and the counts / KPIs it was paid on. `carrier_statement_comm` is the
# REP's pay computed by the carrier-statement engine (mig 065, summed into total_payout), not the carrier's.
EMPLOYEE_REP_ROW_FIELDS = ("id", "org_id", "period", "period_month", "period_year", "epay_salesperson", "storeops_name",
                           "store", "store_code", "market", "tier", "tier_source", "kpis_met", "total_kpis",
                           "kpi_values", "premium_acts", "byod_acts", "upgrade_acts", "premium_comm", "byod_comm",
                           "upgrade_comm", "acc_comm", "setup_fee_comm", "trade_in_comm", "acima_comm",
                           "custom_comm", "acc_target", "subtotal", "total_payout", "plan_comm", "plan_name",
                           "residual_installment_comm", "installment_comm_sale", "carrier_statement_comm",
                           "chargeback_deduction", "ops_chargeback_deduction", "ops_chargeback_lines",
                           "final_payout", "calculated_by", "created_at", "updated_at")
# the Boost component drill (/commission-drill): a bucket and one of its items. Price / GP stay ONLY in the
# buckets the rep is paid a PERCENTAGE of (their own sale's price / margin is the basis of that pay);
# count-paid buckets carry the transaction, never its money.
EMPLOYEE_DRILL_ITEM_FIELDS = ("trans_id", "date", "product", "contract_type", "tender_type", "mdn", "serial")
DRILL_PCT_BASIS_BUCKETS = ("accessories", "setup")
DRILL_BASIS_FIELDS = ("ext_price", "gp")

# the fields that ARE carrier commission (or derive from it) — the lock asserts none is ever allowed
KNOWN_CARRIER_FIELDS = ("ext_price", "gp", "implied_cost", "cost_flags", "data_quality", "ma_matches",
                        "ma_says_paid", "held_but_ma_paid", "rebate_total", "ma_spiff_total", "mi_ref",
                        "boost_commission", "boost_reimbursement", "would_have_paid", "suppressed",
                        "suppressed_reason", "buckets")

_ALLOW = {"line": EMPLOYEE_LINE_FIELDS, "rule": EMPLOYEE_RULE_FIELDS, "plan": EMPLOYEE_PLAN_FIELDS,
          "device": EMPLOYEE_DEVICE_FIELDS, "installment": EMPLOYEE_INSTALLMENT_FIELDS,
          "multimonth": EMPLOYEE_MULTIMONTH_FIELDS, "explain": EMPLOYEE_EXPLAIN_FIELDS,
          "rep_row": EMPLOYEE_REP_ROW_FIELDS, "drill_item": EMPLOYEE_DRILL_ITEM_FIELDS}


def resolve(requested, caller_is_self):
    """THE audience decision. A self-scoped caller is always 'employee'; else the declared audience, else the
    default ('manager', byte-identical for every existing caller)."""
    if caller_is_self:
        return "employee"
    a = str(requested or "").strip().lower()
    return a if a in AUDIENCES else DEFAULT_AUDIENCE


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def is_paid_line(line):
    """THE paid-line predicate — the engine's verdict, read (see the module header)."""
    ln = line or {}
    if ln.get("suppressed"):
        return False
    if ln.get("qualifies") is False:
        return False
    if ln.get("flat_once"):
        return True
    return _f(ln.get("amount")) != 0


def _keep(d, kind):
    allow = _ALLOW[kind]
    return {k: v for k, v in (d or {}).items() if k in allow}


def employee_line(line):
    return _keep(line, "line")


def employee_rep_row(row):
    """A rep_commissions row as an employee may read it (the allow-list)."""
    return _keep(row, "rep_row")


def employee_explain(explain):
    """The drill-down (`commission_drilldown.explain_rep` + the router's additions) as the EMPLOYEE reads it:
    every level through its allow-list; each rule keeps only its PAID lines (`is_paid_line`). The rep's pay
    figures are untouched. Returns a new dict."""
    src = copy.deepcopy(explain or {})
    out = _keep(src, "explain")
    pc = src.get("plan_component")
    if isinstance(pc, dict):
        p = _keep(pc, "plan")
        rules = []
        for rb in pc.get("rules") or []:
            r = _keep(rb, "rule")
            if isinstance(rb.get("lines"), list):
                r["lines"] = [employee_line(ln) for ln in rb["lines"] if is_paid_line(ln)]
            rules.append(r)
        if "rules" in pc:
            p["rules"] = rules
        out["plan_component"] = p
    mm = src.get("multimonth_component")
    if isinstance(mm, dict):
        m = _keep(mm, "multimonth")
        if isinstance(mm.get("devices"), list):
            devs = []
            for d in mm["devices"]:
                dd = _keep(d, "device")
                dd["installments"] = [_keep(i, "installment") for i in (d.get("installments") or [])]
                devs.append(dd)
            m["devices"] = devs
        out["multimonth_component"] = m
    out["audience"] = "employee"
    return out


def employee_drill(drill):
    """The Boost component drill (`/commission-drill`) as the employee reads it: count-paid buckets carry each
    transaction without its money; percentage-paid buckets keep the sale's price / margin (the rep's pay basis)."""
    out = copy.deepcopy(drill or {})
    for key, b in list(out.items()):
        if not (isinstance(b, dict) and isinstance(b.get("items"), list)):
            continue
        basis = key in DRILL_PCT_BASIS_BUCKETS
        allow = EMPLOYEE_DRILL_ITEM_FIELDS + (DRILL_BASIS_FIELDS if basis else ())
        b["items"] = [{k: v for k, v in (it or {}).items() if k in allow} for it in b["items"]]
        if not basis:
            b.pop("sales", None)
            b.pop("gp", None)
    out["audience"] = "employee"
    return out


def disallowed_fields(payload, kind="explain", path=""):
    """Every field of an EMPLOYEE payload outside its allow-list — [] for a clean payload. `kind` ∈ 'explain'
    (the drill-down), 'rep_row', 'drill'."""
    hits = []
    if kind == "rep_row":
        return [f"{path}.{k}" for k in (payload or {}) if k not in EMPLOYEE_REP_ROW_FIELDS]
    if kind == "drill":
        for key, b in (payload or {}).items():
            if isinstance(b, dict) and isinstance(b.get("items"), list):
                allow = EMPLOYEE_DRILL_ITEM_FIELDS + (DRILL_BASIS_FIELDS if key in DRILL_PCT_BASIS_BUCKETS else ())
                for i, it in enumerate(b["items"]):
                    hits += [f"{path}.{key}.items[{i}].{k}" for k in it if k not in allow]
                if key not in DRILL_PCT_BASIS_BUCKETS:
                    hits += [f"{path}.{key}.{k}" for k in ("sales", "gp") if k in b]
        return hits
    ex = payload or {}
    hits += [f"{path}.{k}" for k in ex if k not in EMPLOYEE_EXPLAIN_FIELDS]
    pc = ex.get("plan_component") or {}
    hits += [f"{path}.plan_component.{k}" for k in pc if k not in EMPLOYEE_PLAN_FIELDS]
    for i, rb in enumerate(pc.get("rules") or []):
        hits += [f"{path}.rules[{i}].{k}" for k in rb if k not in EMPLOYEE_RULE_FIELDS]
        for j, ln in enumerate(rb.get("lines") or []):
            hits += [f"{path}.rules[{i}].lines[{j}].{k}" for k in ln if k not in EMPLOYEE_LINE_FIELDS]
            if not is_paid_line(ln):
                hits.append(f"{path}.rules[{i}].lines[{j}] (not a paid line)")
    mm = ex.get("multimonth_component") or {}
    hits += [f"{path}.multimonth_component.{k}" for k in mm if k not in EMPLOYEE_MULTIMONTH_FIELDS]
    for i, d in enumerate(mm.get("devices") or []):
        hits += [f"{path}.devices[{i}].{k}" for k in d if k not in EMPLOYEE_DEVICE_FIELDS]
        for j, it in enumerate(d.get("installments") or []):
            hits += [f"{path}.devices[{i}].installments[{j}].{k}" for k in it if k not in EMPLOYEE_INSTALLMENT_FIELDS]
    return hits


def rule_line_total(explain):
    """Σ line $ over the plan component's rule lines (flat-once lines carry no per-line amount)."""
    pc = (explain or {}).get("plan_component") or {}
    return round(sum(_f(ln.get("amount")) for rb in pc.get("rules") or [] for ln in rb.get("lines") or []), 2)
