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

FOLLOW-UPS (owner 2026-09-26, index §6j): *"pay discrepancy should be hidden, managers can see rep incentive, on
paid row show the action / upgrade with the details of the phone number and customer name. also need a report to
export one employees report over a number of selected months. all these need to be platform wide"*
  · WHO, for the screen too — `viewer_payload(caller_is_self, carrier_view)` is what `/me` hands the client: the
    viewer's audience and the pages refused to it. Pages come from ONE registry, `MANAGER_ONLY_SURFACES` — the
    server's refusals (`router._require_carrier_view(key)`, employee-only `_refuse_employee_audience` until
    2026-09-28) and the nav's visibility (`rbac.payoutRefused`) both read it.
  · Pages no longer declare an audience: the server resolves it from who is looking (a manager gets the manager
    report — every line and its ⛔ reason, never carrier money since 2026-09-28 — a rep the employee one).
  · A plan line names its sale: `phone` (`line_phone`, THE phone rule) and `customer` (the sale-customer rule,
    `inventory_sold_recon.sale_customer`) — both on the employee allow-list on purpose.

CARRIER COMMISSION IS FOR MANAGEMENT'S EYES ONLY (owner 2026-09-28, index §6m — reverses the Price / GP part of §6j):
*"the incoming commission is shown on the line item as $150 and $5 on the first transaction and similarly for all
under that, it should not show any commission received on the rep incentive report, that is only for the eyes of
the management, gated out from all levels"*.
  · THE CLASS: "which fields a Rep Incentive surface may show" depended on WHO was looking, so the one audience that
    was not an employee (every manager) was served the carrier's money. Now the money does not depend on the viewer
    at all: every Rep Incentive surface (the rows, the plan drill, the Boost drill, the multi-month table, exports,
    the statements, the emailed Incentives report) goes through the SAME allow-lists for every audience —
    `rep_incentive_explain` / `rep_incentive_row` / `rep_incentive_drill`. `KNOWN_CARRIER_FIELDS` (the carrier's
    money) is on none of them. A manager adds only `MANAGER_REASON_FIELDS` (the ⛔ reasons) and the lines that did
    not pay; an employee keeps paid lines only.
  · Carrier money is shown ONLY on the carrier surfaces (`MANAGER_ONLY_SURFACES`, now incl. the commission-explain
    diagnostic's carrier view) and ONLY to a viewer holding THE one permission `CARRIER_VIEW_GRANT`
    (`carrier_commission_view`) — `carrier_view_allowed(caller, caller_is_self)` is its one home. Its role list is
    per-org config: each org's Roles & Access (`storeops.roles.permissions.data.carrier_commission_view`, true or
    false per role) wins; unset = the house default, top management only (the platform super admin and the
    company-wide scope-'all' roles — admin / owner / master admin). The server refuses a carrier surface to anyone
    else (`router._require_carrier_view`); `/me` hands the nav the same answer (`viewer_payload`).

PURE; no I/O; no tenant, carrier or product name (RULE TWO). Lock: `harness_payout_audience_lock.py`.
"""
import copy

AUDIENCES = ("employee", "manager")
DEFAULT_AUDIENCE = "manager"

# ── THE EMPLOYEE ALLOW-LISTS ─────────────────────────────────────────────────────────────────────────
# a plan rule line (commission_engine.preview(detail=True) → rules[].lines[])
# `phone` / `customer` (index §6j): the rep's own sale — the line and the customer they sold it to. PII shown to
# the rep who made the sale; the name only (never an id number), read org-scoped by `commission_drilldown`.
EMPLOYEE_LINE_FIELDS = ("date", "trans_id", "product", "contract_type", "imei", "mdn", "amount", "qualifies",
                        "flat_once", "event_id", "event_key", "event_key_kind", "event_type", "event_label", "phone",
                        "customer")
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

# THE CARRIER'S MONEY (owner 2026-09-28, index §6m): what the carrier paid the store — the sale line's Price / GP (on
# a rebate / spiff line those ARE the carrier's payment), the cost implied from them and its flags, the MA cross-
# reference and "MA says paid", the carrier MI refs, the dealer figures, the commission-ledger buckets. On NO Rep
# Incentive surface for ANY viewer; only on a carrier surface, to a holder of CARRIER_VIEW_GRANT. The lock asserts
# none is on any allow-list below (the one excused place: DRILL_BASIS_FIELDS in the percentage-paid Boost buckets,
# where the "price" is the customer's accessory / setup price the rep is paid a % of — the rep's own pay basis).
KNOWN_CARRIER_FIELDS = ("ext_price", "gp", "implied_cost", "cost_flags", "cost_flag_labels", "data_quality",
                        "ma_matches", "ma_says_paid", "held_but_ma_paid", "rebate_total", "ma_spiff_total", "mi_ref",
                        "boost_commission", "boost_reimbursement", "buckets")
# THE ⛔ REASONS — why a matched line paid nothing (the pay gate's verdict and the would-be REP pay). Not carrier
# money: a MANAGER reads them on the Rep Incentive drill; an employee (paid lines only) never needs them.
MANAGER_REASON_FIELDS = ("suppressed", "suppressed_by", "suppressed_reason", "would_have_paid", "excluded_by")

# ── THE CARRIER SURFACES — THE one registry (index §6j, §6m) ────────────────────────────────────────
# The management surfaces that show carrier commission (what the carrier paid the store, per rep). Since 2026-09-28
# each is refused to every viewer WITHOUT the carrier permission (`carrier_view_allowed` — reps and store managers
# alike), not only to the employee audience. The server's refusal names its key here
# (`router._require_carrier_view(authorization, org_id, key)`), and the nav hides every `pages` entry from such a
# viewer (`viewer_payload` → `/me` → `rbac.payoutRefused`). One list; a surface refused on the server is never offered
# in the menu. `"menu": False` = a page reached only through in-app links (no NAV entry) — every such link asks
# `payoutRefused` itself (the lock checks it); the route guard (`canAccessPath`) refuses its URL either way.
MANAGER_ONLY_SURFACES = (
    {"key": "carrier_vs_pay", "label": "Carrier Earned vs Employee Paid",
     "endpoints": ("/carrier-vs-pay/{period}",), "pages": ("/commcalc/carrier-vs-pay",)},
    {"key": "pay_discrepancy", "label": "Pay Discrepancy",
     "endpoints": ("/discrepancy/{period}", "/discrepancy/{period}/phantom", "/discrepancy/run"),
     "pages": ("/commcalc/discrepancy",)},
    {"key": "commission_discrepancy", "label": "Commission Discrepancy",
     "endpoints": ("/discrepancy-appeals",), "pages": ("/commcalc/commission-discrepancy",)},
    # Commission Withholding (index §55): the activations the carrier took commission BACK on, with
    # the appeal state and the processor's own payment leg beside each. Carrier money on both legs,
    # so it sits at the same tier as its siblings above rather than on the open Flags page.
    {"key": "commission_withholding", "label": "Commission Withholding",
     "endpoints": ("/commission-withholding", "/commission-withholding/{flag_id}/appeal"),
     "pages": ("/commcalc/commission-withholding",)},
    # a drill inside other pages (no page of its own) — refused on the server, nothing to hide in the nav
    {"key": "commission_device", "label": "Device commission story",
     "endpoints": ("/commission-device",), "pages": ()},
    # the "How was this calculated?" diagnostic's CARRIER view (Price / GP, implied cost, the MA cross-reference).
    # The Rep Incentive drill reads the same endpoint WITHOUT `view=carrier` and is served the carrier-free shape.
    {"key": "commission_explain_carrier", "label": "Commission explain (carrier diagnostic)",
     "endpoints": ("/commission-explain?view=carrier",), "pages": ("/commcalc/commission-explain",), "menu": False},
)
MANAGER_ONLY_PAGES = tuple(p for s_ in MANAGER_ONLY_SURFACES for p in s_["pages"])


def manager_only_label(key):
    """The registered label of a carrier surface — KeyError for an unregistered key (a refusal must be registered,
    or the nav could not hide it)."""
    for s_ in MANAGER_ONLY_SURFACES:
        if s_["key"] == key:
            return s_["label"]
    raise KeyError(f"carrier surface {key!r} is not registered in payout_audience.MANAGER_ONLY_SURFACES")


def refusal_message(key):
    """The 403 a carrier surface answers a viewer without the permission — names the registered surface and THE
    permission, never a role (which roles hold it is each org's config)."""
    return (f"{manager_only_label(key)} shows carrier commission — it is for management only (the "
            f"'{CARRIER_VIEW_GRANT}' permission, set per role in Roles & Access).")


# ── THE CARRIER PERMISSION — its one home (owner 2026-09-28, index §6m) ─────────────────────────────
# ONE permission answers "may this viewer see carrier commission?". It is a Roles & Access data grant (rbac.ts
# DATA_GRANTS registers the key so the Roles editor can tick / untick it per role): the org's own setting on the
# role wins in BOTH directions; unset = the house default, top management only. RULE TWO: no role NAME decides
# here — "top management" is the company-wide scope the platform already defines (index §14: every role above
# market manager is scope 'all'; the seeded 'admin' role is scope 'all'), plus the platform super admin.
CARRIER_VIEW_GRANT = "carrier_commission_view"
CARRIER_VIEW_DEFAULT_SCOPES = ("all",)


def carrier_view_allowed(caller, caller_is_self=False):
    """THE answer. PURE over core's resolved caller ({super_admin, role, perms}):
      · no caller, or a SELF-scoped caller (a rep — always the employee audience)        → False
      · the platform super admin                                                          → True
      · the org set `perms.data.carrier_commission_view` on this role (true / false)      → that value
      · unset → the house default: a company-wide ('all') scope role                       → True, else False
    (the Roles & Access checkbox shows exactly this default: ticked for a company-wide role until unticked)."""
    if not caller or caller_is_self:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    data = perms.get("data") or {}
    if isinstance(data, dict) and CARRIER_VIEW_GRANT in data:
        return bool(data.get(CARRIER_VIEW_GRANT))
    return str(perms.get("scope") or "").strip().lower() in CARRIER_VIEW_DEFAULT_SCOPES


def viewer_payload(caller_is_self, carrier_view=False):
    """What `/me` tells the client about THIS viewer: the audience the server will serve them (`resolve` with no
    page request), whether they hold the carrier permission, and the carrier pages refused to them (every one,
    unless they hold it). The client decides nothing — it hides what it is told."""
    aud = resolve("", caller_is_self)
    allowed = bool(carrier_view) and aud != "employee"
    return {"audience": aud, "carrier_view": allowed, "refused_pages": [] if allowed else list(MANAGER_ONLY_PAGES)}


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


def rep_incentive_line(line, audience):
    """One plan line as a Rep Incentive surface shows it: the line allow-list for every audience; a manager adds
    the ⛔ reasons (`MANAGER_REASON_FIELDS`). Never a carrier field."""
    allow = EMPLOYEE_LINE_FIELDS + (() if audience == "employee" else MANAGER_REASON_FIELDS)
    return {k: v for k, v in (line or {}).items() if k in allow}


def rep_incentive_row(row):
    """A rep_commissions row as EVERY Rep Incentive viewer reads it (owner 2026-09-28, index §6m): the rep's pay and
    the counts / KPIs it was paid on — the carrier-paid dealer figures (`boost_commission` / `boost_reimbursement`)
    are on no audience's row. (carrier-vs-pay reads them from the table itself, behind the carrier permission.)"""
    return _keep(row, "rep_row")


def employee_rep_row(row):
    """A rep_commissions row as an employee may read it — the same allow-list every audience gets (`rep_incentive_row`)."""
    return rep_incentive_row(row)


def employee_explain(explain):
    """The drill-down as the EMPLOYEE reads it — `rep_incentive_explain(explain, 'employee')`."""
    return rep_incentive_explain(explain, "employee")


def rep_incentive_explain(explain, audience):
    """THE Rep Incentive drill-down (`commission_drilldown.explain_rep` + the router's additions) as `audience` reads
    it (owner 2026-09-26 / 2026-09-28, index §6i / §6m): every level through its allow-list, FOR EVERY AUDIENCE — no
    carrier field reaches anyone. 'employee' keeps only the PAID lines (`is_paid_line`); a manager keeps every
    matched line with its ⛔ reason. The rep's pay figures are untouched. Returns a new dict stamped `audience`."""
    aud = "employee" if audience == "employee" else "manager"
    src = copy.deepcopy(explain or {})
    out = _keep(src, "explain")
    pc = src.get("plan_component")
    if isinstance(pc, dict):
        p = _keep(pc, "plan")
        rules = []
        for rb in pc.get("rules") or []:
            r = _keep(rb, "rule")
            if isinstance(rb.get("lines"), list):
                r["lines"] = [rep_incentive_line(ln, aud) for ln in rb["lines"]
                              if aud != "employee" or is_paid_line(ln)]
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
    out["audience"] = aud
    return out


def employee_drill(drill):
    """The Boost component drill as the employee reads it — `rep_incentive_drill(drill, 'employee')`."""
    return rep_incentive_drill(drill, "employee")


def rep_incentive_drill(drill, audience):
    """The Boost component drill (`/commission-drill`) as EVERY Rep Incentive viewer reads it (index §6i / §6m):
    count-paid buckets carry each transaction without its money; percentage-paid buckets keep the sale's price /
    margin (the customer's accessory / setup price — the rep's pay basis, not the carrier's money). The same shape
    for every audience; only the `audience` stamp differs."""
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
    out["audience"] = "employee" if audience == "employee" else "manager"
    return out


def carrier_fields_in(payload, path=""):
    """Every KNOWN_CARRIER_FIELDS key anywhere in a payload (dicts and lists, any depth) — [] for a clean one. The
    proof and the lock run every Rep Incentive surface's output through it, for every audience. (The Boost drill's
    percentage-paid buckets are checked with `disallowed_fields(kind='drill')` instead — their basis is excused.)
    A key that carries nothing (None / empty list or dict) is not money and is not reported; a 0.0 is."""
    hits = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            p = f"{path}.{k}"
            if k in KNOWN_CARRIER_FIELDS and not (v is None or (isinstance(v, (list, tuple, dict)) and not v)):
                hits.append(p)
            hits += carrier_fields_in(v, p)
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            hits += carrier_fields_in(v, f"{path}[{i}]")
    return hits


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


def line_phone(line):
    """The phone line a plan line is about: its activation EVENT's key when the event is keyed by phone, else THE
    phone rule (`line_class.line_event_keys`: the line's MDN, else a phone-shaped tracking #). None when neither."""
    ln = line or {}
    if ln.get("event_key_kind") == "phone" and ln.get("event_key"):
        return str(ln["event_key"])
    from app.modules.commcalc import line_class as _lc      # pure; lazy so this module stays import-light
    return _lc.line_event_keys({"mdn": ln.get("mdn"), "serial_1": ln.get("imei")}).get("phone")


def event_label(line):
    """The action a line's activation EVENT was — THE class label (`line_class.CLASS_LABELS`: 'New activation',
    'Upgrade', …). None for a line that is not part of an activation event."""
    cls = (line or {}).get("event_type")
    if not cls:
        return None
    from app.modules.commcalc import line_class as _lc
    return _lc.CLASS_LABELS.get(str(cls), str(cls))


def stamp_line_identity(explain, customers_by_txn):
    """Set `event_label`, `phone` and `customer` on every plan rule line (in place; returns the explain).
    `customers_by_txn` = {trans_id: customer} from the sale-customer rule. PURE."""
    pc = (explain or {}).get("plan_component") or {}
    cust = customers_by_txn or {}
    for rb in pc.get("rules") or []:
        for ln in rb.get("lines") or []:
            ln["event_label"] = event_label(ln)
            ln["phone"] = line_phone(ln)
            ln["customer"] = cust.get(str(ln.get("trans_id") or "").strip()) or None
    return explain


def rule_line_total(explain):
    """Σ line $ over the plan component's rule lines (flat-once lines carry no per-line amount)."""
    pc = (explain or {}).get("plan_component") or {}
    return round(sum(_f(ln.get("amount")) for rb in pc.get("rules") or [] for ln in rb.get("lines") or []), 2)


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# "IS THIS ROW MINE?" — THE ONE HOME (owner directive 2026-10-05: a rep may use the in-app assistant
# for *"only their own commission, only their action plan"*).
#
# THE CLASS, named rather than the instance. Three surfaces answer "which of these rep rows belongs
# to the signed-in rep", and before this they answered it three different ways:
#   · `/commissions/{period}` matched the caller's name keys against three row fields, canon-aware,
#     in a closure private to that handler — correct, and unreachable by anyone else.
#   · `/targets/{period}/action-plan` took `rep` as a plain string filter with NO identity check, and
#     was saved only by a self rep's store keyset coming out empty. An accident, not a rule.
#   · `/coaching/{period}` did the same, over a payload carrying `total_payout`, `final_payout`,
#     `at_risk` and the chargeback figures.
# So the fix is not "add a check to the action plan". It is: the predicate lives HERE, every caller
# dereferences it, and `harness_payout_audience_lock.py` fails the build if one stops.
#
# WHAT A CALLER STILL OWNS. Resolving WHO the caller is stays in the router adapters that can do I/O
# (`_caller_rep_keys` for the name keys, `_rep_canon_map` for the alias map, `_caller_self_keyset`
# for their store). This module stays PURE: it is handed the keys and a canon callable and answers
# the question. That split is why this file needs no tenant, carrier or product name (RULE TWO).
#
# FAIL-CLOSED, deliberately. `rep_keys=set()` is a self rep we could not map to any rep row, and the
# honest answer is NOTHING — never "then show them everything". Only `rep_keys=None`, meaning "this
# caller is not self-scoped at all", leaves rows untouched.

# Row fields that can carry a rep's name. The epay/POS spelling and the storeops roster spelling are
# both names for one person, which is why a canon pass is part of the match rather than a nicety.
REP_NAME_FIELDS = ("storeops_name", "epay_salesperson", "salesperson", "rep")


def rep_row_keys(row, canon=None, fields=REP_NAME_FIELDS):
    """Every UPPER name key a row could be known by: each name field as written, plus its canonical
    form when a `canon` callable is supplied. PURE."""
    out = set()
    for f in fields or ():
        v = str((row or {}).get(f) or "").strip()
        if not v:
            continue
        out.add(v.upper())
        if canon is not None:
            try:
                c = str(canon(v) or "").strip()
            except Exception:                              # pragma: no cover - a caller's map misbehaving
                c = ""
            if c:
                out.add(c.upper())
    return out


def row_is_mine(row, rep_keys, canon=None, fields=REP_NAME_FIELDS):
    """Whether `row` belongs to the self-scoped caller whose own name keys are `rep_keys`.
    `rep_keys is None` (not a self caller) → True, so a manager's rows are untouched.
    `rep_keys == set()` (a self caller we could not place) → False for every row. PURE."""
    if rep_keys is None:
        return True
    return bool(rep_row_keys(row, canon=canon, fields=fields) & set(rep_keys))


def mine_only(rows, rep_keys, canon=None, fields=REP_NAME_FIELDS):
    """`rows` as the self-scoped caller may read them — their own, in the original order. A manager
    (`rep_keys is None`) gets the SAME LIST OBJECT back, so no existing response can shift. PURE."""
    if rep_keys is None:
        return rows
    return [r for r in (rows or []) if row_is_mine(r, rep_keys, canon=canon, fields=fields)]


def requested_rep_is_mine(requested, rep_keys, canon=None):
    """Whether a `rep=` a caller asked for is the caller themselves. A self caller asking for anyone
    else is False — the handler then ignores the parameter rather than serving a colleague's pay.
    A blank request is True (no narrowing was asked for); a non-self caller is always True."""
    if rep_keys is None:
        return True
    name = str(requested or "").strip()
    if not name:
        return True
    return row_is_mine({"rep": name}, rep_keys, canon=canon, fields=("rep",))
