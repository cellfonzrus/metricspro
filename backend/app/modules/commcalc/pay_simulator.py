"""EMPLOYEE PAY SIMULATOR — "what would I make if I sold X?" (owner directive 2026-08-03:
"the employee should be able to play the numbers to get an idea of what they will make and a widget
for the same to be added to the employee dashboard with their commission widget").

READ-ONLY. NO WRITES, NO RECOMPUTE, NO PERSIST. Nothing in this module inserts, updates, upserts or
deletes anything, and it never calls the calculate path. It is a projection over the tenant's ALREADY
CONFIGURED payout engine.

═══ THE ONE DESIGN RULE: THE MATH IS THE ENGINE'S, NOT A COPY OF IT ═══════════════════════════════
A pay simulator whose formula is re-implemented (in this file, or worse in the browser) is a formula
that DRIFTS: the day someone adds a tier basis, a pay-gate exclusion, a unit basis or a set-up-fee
item to the real engine, the simulator keeps quoting last month's rules and an employee is told a
number they will not be paid. So this module computes NOTHING. It:

  1. resolves the caller's OWN identity + store from their auth token (never a client-supplied rep);
  2. resolves THEIR plan with `commission_engine._resolve_plan_for` — the same assignment precedence
     (employee > role > store > market > default) the live payout uses;
  3. mints synthetic sale LINES that match that plan's own rules; and
  4. hands those lines to `commission_engine.preview(..., sales_override=lines)` — the *actual*
     function the plan-engine payout runs through (`router._apply_new_engines` calls the same
     `preview()`), with a read-only sales-source hook added for exactly this purpose.

ENGINE FUNCTIONS THE RESULT COMES FROM (all reached via that one preview() call, none re-implemented):
    commission_engine.preview            the whole per-rep loop (the payout number itself)
      ├─ _load_plans                     plans + rules + tiers + assignments
      ├─ _resolve_plan_for               which plan pays this rep
      ├─ _rule_matches                   which lines a rule matches
      ├─ _line_payout                    per-line $ for flat_per_unit / pct_gp / pct_mrc /
      │                                  pct_price_over_cost
      ├─ (flat_pending)                  flat bonuses paid once per rep
      ├─ _tier_basis / _tier_metric_count / _tier_multiplier    tier attainment + multiplier
      ├─ plan_pay_gate                   scope / exclusion / unit-basis / accessory-basis guards
      ├─ accessory_catalog               the 'accessory' synthetic match field
      ├─ _activation_buckets             the 'activation_bucket' synthetic match field
      └─ setup_fee_pay.collected/employee_pay/dealer_share      the set-up-fee pay item
The endpoint returns the engine's OWN `by_rep` row (base_payout / tiered_payout / tier_multiplier /
setup_fee_comm / total_payout / per-rule breakdown) — not a re-derived total.

SELF-ONLY (RULE: an employee must not simulate a coworker). `resolve_self()` reads the identity from
the bearer token via `app_users.auth_id` — the same rule storeops uses for every self-service view
(/timeclock/status, /my-chargebacks). A `rep` query param is accepted ONLY so the 403 is explicit:
anything that is not the caller's own resolved rep name is refused unless the caller is a super-admin
or holds company-wide ('all') scope — those roles already read every rep's pay on the Rep Commission
Report, so refusing them here would be theatre, not security.

MULTI-TENANT: the acting org comes from the caller's own `app_users` row, and every read is
`.eq("org_id", org)`. A caller whose token resolves to tenant B can never simulate against tenant A's
plans even if they pass `org_id=A` — the resolved org WINS over the query param here (this endpoint's
whole subject is "me", and "me" has exactly one tenant).

CARRIER MODE: this is the PLAN engine (`commission_engine`). A Boost/house tenant is paid by the
legacy `calculator.py` component engine instead, so for a rep whose org resolves to carrier_mode
'boost' the simulator reports an explicit, honest unsupported-state rather than quoting plan-engine
dollars that would never be paid. (What-If's 🎯 Employee Payout tab is the Boost-side tool.)
"""
import calendar
import datetime as _dt

from app.modules.commcalc.calculator import safe_float

_HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}

# Synthetic MDNs are minted from this prefix so a simulated pct_mrc line can be given an MRC without
# colliding with any real phone number in raw_mi (555-01xx is the reserved fictional range).
_SIM_MDN_PREFIX = "5550100"
# Cap the modelled volume: a lever is a rep's month, not a load test. Keeps one request bounded.
MAX_UNITS_PER_LEVER = 999
MAX_TOTAL_LINES = 4000


# ── period helpers (period spelling is a recurring bug class — read through variants) ──────────────
def _canon_period(period):
    """'Month YYYY' for a period given in either spelling; today's month when blank/garbage."""
    p = str(period or "").strip()
    parts = p.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return f"{parts[0]} {parts[1]}"
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        try:
            return f"{calendar.month_name[int(p[5:7])]} {p[:4]}"
        except Exception:
            pass
    t = _dt.date.today()
    return f"{calendar.month_name[t.month]} {t.year}"


def _period_midpoint(period):
    """A real date INSIDE the period, for the synthetic lines' trans_date. Mid-month so a simulated
    line can never fall outside a month-bounded read."""
    p = _canon_period(period)
    parts = p.split()
    try:
        return _dt.date(int(parts[1]), _MONTHS[parts[0]], 15).isoformat()
    except Exception:
        return _dt.date.today().isoformat()


# ── SELF IDENTITY (token only — never a client-supplied rep) ───────────────────────────────────────
def _uid(authorization):
    from app.modules.core.router import _uid_from_token   # core is SHARED: imported, never edited
    return _uid_from_token(authorization)


def _caller_perms(client, authorization, org_id):
    """The caller's resolved role/perms for the acting org, or None. Used ONLY to decide whether an
    'all'-scope leader may look at another rep — never to widen the self rule for anyone else."""
    try:
        from app.modules.core.router import _resolve_caller
        uid = _uid(authorization)
        return _resolve_caller(client, uid, org_id) if uid else None
    except Exception:
        return None


def is_privileged(caller):
    """True for a super-admin or a company-wide ('all') / admin role — the people who already read
    every rep's pay on the Rep Commission Report. PURE."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


def _name_keys(*names):
    from app.modules.commcalc.commission_engine import _canon_person
    return {_canon_person(n) for n in names if str(n or "").strip()} - {""}


def resolve_self(client, authorization, period=""):
    """The SIGNED-IN employee's (org_id, employee_id, rep_name, store, market, employee_row).

    Identity + TENANT both come from the token (auth_id is globally unique → exactly one app_users
    row), so a self-service simulation always lands in the employee's own tenant regardless of any
    org_id query param — the same rule storeops' `_caller_identity` uses.

    `rep_name` is the name the SALES/PAY data uses, not the friendly display name: an employee stored
    as "Ali" sells as "ali, mohammad khalid", and the plan's employee-scope assignment is keyed on the
    latter. Resolution order — epay_salesperson → a matching rep_commissions row → the display name.
    Raises 401/403 with an actionable message, never a bare 500."""
    from fastapi import HTTPException
    from app.modules.commcalc.commission_engine import _canon_person
    uid = _uid(authorization)
    if not uid:
        raise HTTPException(401, "Sign in to use the pay simulator.")
    # app_users lives in the STOREOPS schema (migration 003) — the same table storeops'
    # `_caller_identity` reads, and deliberately NOT org-filtered: auth_id is globally unique, so the
    # row itself names the caller's tenant. The public-schema retry is only a defensive fallback for
    # deployments that expose it there.
    rows = []
    for _tbl in (lambda: client.schema("storeops").table("app_users"),
                 lambda: client.table("app_users")):
        try:
            rows = (_tbl().select("org_id,employee_id,store_code,store_codes,full_name")
                    .eq("auth_id", uid).limit(1).execute().data) or []
        except Exception:
            rows = []
        if rows:
            break
    if not rows:
        raise HTTPException(403, "Your login isn't provisioned in any tenant yet. Ask an admin to add "
                                 "you under Roles & Access.")
    u = rows[0]
    org = str(u.get("org_id") or "").strip() or _HOUSE_ORG
    eid = str(u.get("employee_id") or "").strip()
    if not eid:
        raise HTTPException(403, "Your login isn't linked to an employee record, so there is no pay "
                                 "plan to simulate. Ask an admin to set your Employee ID in "
                                 "Roles & Access.")
    emp = {}
    try:
        er = (client.schema("storeops").table("employees").select("*")
              .eq("org_id", org).eq("employee_id", eid).limit(1).execute().data) or []
        emp = er[0] if er else {}
    except Exception:
        emp = {}
    display = str(emp.get("name") or u.get("full_name") or "").strip()
    eslp = str(emp.get("epay_salesperson") or "").strip()
    store = (str(emp.get("home_store") or "").strip()
             or str(u.get("store_code") or "").strip())

    rep_name = eslp or display
    # Prefer the exact string the pay data already uses for this person, when we can find it — that is
    # what an employee-scope plan assignment and _resolve_plan_for are matched on.
    keys = _name_keys(display, eslp)
    if keys:
        try:
            rc = (client.schema("commcalc").table("rep_commissions")
                  .select("storeops_name,epay_salesperson,store,store_code")
                  .eq("org_id", org).limit(20000).execute().data) or []
        except Exception:
            rc = []
        for r in rc:
            for f in ("epay_salesperson", "storeops_name"):
                v = str(r.get(f) or "").strip()
                if v and _canon_person(v) in keys:
                    rep_name = v
                    store = store or str(r.get("store") or r.get("store_code") or "").strip()
                    break
            else:
                continue
            break

    market = ""
    try:
        from app.modules.commcalc.commission_engine import _read_store_market
        sm = _read_store_market(client, org)
        market = sm.get(store.lower(), "") or sm.get(store.split(" ")[0].lower(), "")
    except Exception:
        market = ""
    return {"org_id": org, "employee_id": eid, "rep_name": rep_name, "display_name": display or rep_name,
            "store": store, "market": market, "employee": emp}


def require_self(client, authorization, requested_rep, period=""):
    """Resolve self and REFUSE a request aimed at anybody else. Returns the `resolve_self` dict.

    `requested_rep` blank / equal (canonically) to the caller's own rep name  -> allowed.
    Anything else                                                            -> 403, UNLESS the caller
    is a super-admin or holds company-wide ('all') / admin scope (they already read every rep's pay)."""
    from fastapi import HTTPException
    from app.modules.commcalc.commission_engine import _canon_person
    me = resolve_self(client, authorization, period)
    want = str(requested_rep or "").strip()
    if not want or _canon_person(want) == _canon_person(me["rep_name"]) or _canon_person(want) == _canon_person(me["display_name"]):
        return me
    if is_privileged(_caller_perms(client, authorization, me["org_id"])):
        me = dict(me)
        me["rep_name"] = want
        me["display_name"] = want
        me["impersonated"] = True
        return me
    raise HTTPException(403, "The pay simulator only models YOUR own pay plan — you can't simulate "
                             "another employee's commission.")


# ── which plan pays me ─────────────────────────────────────────────────────────────────────────────
def _resolve_my_plan(client, org_id, rep_name, store, market):
    """(plan|None, plans_ready, reason). The SAME resolver + precedence the live payout uses."""
    from app.modules.commcalc import commission_engine as ce
    plans, ready = ce._load_plans(client, org_id)
    if not ready:
        return None, False, ("The configurable Commission-Plan engine isn't set up for this org yet "
                             "(migration 059 not applied).")
    active = [p for p in plans if p.get("is_active", True)]
    if not active:
        return None, True, "No commission plans are configured for this org yet."
    role = None
    try:
        role = ce._read_employee_roles(client, org_id).get(ce._canon_person(rep_name))
    except Exception:
        role = None
    plan = ce._resolve_plan_for(rep_name, store, market, active, rep_role=role)
    if not plan:
        return None, True, ("No commission plan is assigned to you yet (employee → role → store → "
                            "market → default all missed), so there is nothing to simulate. Ask your "
                            "manager to assign you to a Commission Plan.")
    return plan, True, None


def _carrier_mode(client, org_id):
    """'boost' | 'plan' via the router's single-source resolver — never a carrier-name branch here."""
    try:
        from app.modules.commcalc.router import _resolve_carrier_mode
        carriers = (client.schema("commcalc").table("carrier").select("id,name,code,is_default")
                    .eq("org_id", org_id).execute().data) or []
        return _resolve_carrier_mode(carriers)
    except Exception:
        return "plan"


# ── the LEVERS a rep actually controls, derived from what their plan actually pays on ─────────────
_KIND_INPUT = {
    # payout_kind -> (needs an amount input?, what the amount MEANS, unit label for the count)
    "flat_per_unit": (False, None, "units"),
    "flat": (False, None, "units"),
    "pct_gp": (True, "gp", "units"),
    "pct_price_over_cost": (True, "ext_price", "units"),
    "pct_mrc": (True, "mrc", "lines"),
}


def _match_target(rule):
    """(field, value) a synthetic line must carry to match this rule. 'in' takes the first option;
    'contains' is satisfied by the substring itself. None field = matches anything ('any')."""
    field = (rule.get("match_field") or "any").strip().lower()
    if field == "any":
        return None, None
    op = (rule.get("match_op") or "equals").strip().lower()
    want = str(rule.get("match_value") or "").strip()
    if op == "in":
        opts = [x.strip() for x in want.split(",") if x.strip()]
        want = opts[0] if opts else want
    return field, want


def _accessory_line_hints(client, org_id):
    """department/category/product_desc values that this tenant's OWN accessory classifier calls an
    accessory — probed against `accessory_catalog.build()`, the SAME classifier preview() stamps with.
    Returns {} when nothing can be proven accessory (the lever is then reported unsimulatable rather
    than quietly paying $0)."""
    try:
        from app.modules.commcalc import accessory_catalog as _accat
        clf = _accat.build(client, org_id)
    except Exception:
        return {}
    if clf is None:
        return {}
    cands = []
    try:
        rows = (client.schema("commcalc").table("flag_rules")
                .select("accessory_departments,accessory_categories,accessory_product_keywords")
                .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
        r0 = rows[0] if rows else {}
        for d in (r0.get("accessory_departments") or []):
            if d:
                cands.append({"department": str(d), "category": "", "product_desc": "Simulated accessory"})
        for c in (r0.get("accessory_categories") or []):
            if c:
                cands.append({"department": "", "category": str(c), "product_desc": "Simulated accessory"})
        for k in (r0.get("accessory_product_keywords") or []):
            if k:
                cands.append({"department": "", "category": "", "product_desc": f"Simulated {k}"})
    except Exception:
        pass
    cands.append({"department": "Ondigo", "category": "Accessories", "product_desc": "Simulated accessory"})
    for c in cands:
        try:
            if clf.is_accessory_row(dict(c, ext_price=0, gp=0)):
                return c
        except Exception:
            continue
    return {}


def _bucket_contract_types(client, org_id):
    """{activation_bucket -> a contract_type string that resolves to it} using the ENGINE's own
    `_activation_buckets` resolver (the tenant's mig-213 map included) — never a hard-coded list."""
    from app.modules.commcalc import commission_engine as ce
    cands = ["New Activation", "Premium Activation", "Upgrade", "BYOD", "BYOP", "Port In",
             "premium", "upgrade", "byod"]
    try:
        ct_map, _rules = ce._read_ct_classification_config(client, org_id)
        cands = list(ct_map.keys()) + cands
    except Exception:
        pass
    probe = [{"contract_type": c, "trans_id": "", "ext_price": 0} for c in cands]
    try:
        buckets = ce._activation_buckets(client, org_id, probe)
    except Exception:
        return {}
    out = {}
    for c, b in zip(cands, buckets):
        if b and b not in out:
            out[b] = c
    return out


def build_levers(client, org_id, plan):
    """The input levers for THIS plan: one per rule it actually pays on, plus the tier + set-up-fee
    context. Every lever's label/unit/rate comes from the tenant's own rule row (RULE TWO — nothing
    about a carrier, category or rate is hard-coded here)."""
    levers = []
    acc_hint = None
    bucket_cts = None
    for rule in (plan.get("rules") or []):
        kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
        needs_amt, amt_meaning, count_unit = _KIND_INPUT.get(kind, (False, None, "units"))
        field, value = _match_target(rule)
        lever = {
            "key": f"rule:{rule.get('id')}",
            "rule_id": rule.get("id"),
            "label": (rule.get("label") or rule.get("match_value") or rule.get("match_field")
                      or "Rule"),
            "payout_kind": kind,
            "match_field": field or "any",
            "match_value": value,
            "count_unit": count_unit,
            "amount_input": bool(needs_amt),
            "amount_meaning": amt_meaning,
            "amount_label": {"gp": "GP $ each", "ext_price": "Price $ each",
                             "mrc": "Plan MRC $/mo"}.get(amt_meaning or "", ""),
            "rate": (safe_float(rule.get("pct")) if needs_amt else safe_float(rule.get("amount"))),
            "rate_kind": "pct" if needs_amt else "flat",
            "tiered": bool(rule.get("tiered")),
            "qualifies": bool(rule.get("qualifies", True)),
            "simulatable": True,
            "note": None,
        }
        if kind == "flat":
            lever["note"] = "Bonus — paid once for the month as soon as you have at least one."
        # The two SYNTHETIC match fields can't simply be stamped on the line: preview() OVERWRITES
        # them with the tenant's own classifier/resolver output. Probe for a real line shape that the
        # tenant's config genuinely classifies that way; if none exists, say so instead of silently
        # showing $0 for a lever the rep can in fact earn.
        if (field or "") == "accessory":
            if acc_hint is None:
                acc_hint = _accessory_line_hints(client, org_id)
            if not acc_hint:
                lever["simulatable"] = False
                lever["note"] = ("This rule pays on the accessory classification, and no accessory "
                                 "department/category is configured yet — ask an admin to set it up "
                                 "under Accessory Definition.")
        if (field or "") == "activation_bucket":
            if bucket_cts is None:
                bucket_cts = _bucket_contract_types(client, org_id)
            if (value or "").strip().lower() not in bucket_cts:
                lever["simulatable"] = False
                lever["note"] = ("This rule pays on the activation bucket '%s' and no contract type "
                                 "in your tenant's classification map resolves to it yet."
                                 % (value or "?"))
        levers.append(lever)

    tier = None
    metric = (plan.get("base_tier_metric") or "none").strip().lower()
    tiers = plan.get("tiers") or []
    if tiers and metric not in ("", "none"):
        tier = {
            "metric": plan.get("base_tier_metric"),
            "basis": (plan.get("tier_count_basis") or "rule_units"),
            "below_min_multiplier": plan.get("tier_below_min_multiplier"),
            "steps": [{"min_count": int(t.get("min_count") or 0),
                       "multiplier": safe_float(t.get("multiplier")) or 1.0}
                      for t in sorted(tiers, key=lambda x: int(x.get("min_count") or 0))],
        }
    return levers, tier


# ── synthetic lines: the ONLY thing this module builds. The dollars come from the engine. ─────────
def build_lines(client, org_id, plan, rep_name, store, period, inputs):
    """(lines, mrc_override, applied, warnings). `inputs` = {lever_key: {units, amount}}.

    A line is a plain raw_sales-SHAPED dict. It is never written anywhere; it exists only to be
    handed to preview(). Each rule's lines carry that rule's match field/value so the ENGINE's own
    `_rule_matches` selects them — the simulator does not decide what matches."""
    from app.modules.commcalc import commission_engine as ce
    lines, mrc_override, applied, warnings = [], {}, [], []
    tdate = _period_midpoint(period)
    acc_hint = None
    bucket_cts = None
    seq = 0
    for rule in (plan.get("rules") or []):
        key = f"rule:{rule.get('id')}"
        spec = (inputs or {}).get(key) or {}
        units = int(max(0, min(MAX_UNITS_PER_LEVER, int(safe_float(spec.get("units"))))))
        if units <= 0:
            continue
        kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
        needs_amt, amt_meaning, _cu = _KIND_INPUT.get(kind, (False, None, "units"))
        amount = safe_float(spec.get("amount"))
        field, value = _match_target(rule)

        base = {
            "salesperson": rep_name, "store": store, "period": _canon_period(period),
            "trans_date": tdate, "voided": None, "trans_type": "Sale",
            "department": "", "category": "", "product_desc": (rule.get("label") or "Simulated line"),
            "contract_type": "", "ext_price": 0.0, "gp": 0.0, "mdn": "", "serial_1": "",
            "product_id": None, "subscriber_id": "", "_simulated": True,
        }
        if field == "accessory":
            if acc_hint is None:
                acc_hint = _accessory_line_hints(client, org_id)
            if not acc_hint:
                warnings.append({"lever": key, "code": "accessory_unconfigured",
                                 "message": f"'{base['product_desc']}' pays on the accessory "
                                            f"classification, which isn't configured for this tenant "
                                            f"— it was left out of this projection."})
                continue
            base.update(acc_hint)
        elif field == "activation_bucket":
            if bucket_cts is None:
                bucket_cts = _bucket_contract_types(client, org_id)
            ct = bucket_cts.get((value or "").strip().lower())
            if not ct:
                warnings.append({"lever": key, "code": "bucket_unresolvable",
                                 "message": f"No contract type in your classification map resolves to "
                                            f"activation bucket '{value}', so '{base['product_desc']}' "
                                            f"was left out of this projection."})
                continue
            base["contract_type"] = ct
        elif field:
            base[field] = value

        for i in range(units):
            seq += 1
            if len(lines) >= MAX_TOTAL_LINES:
                warnings.append({"lever": key, "code": "line_cap",
                                 "message": f"Stopped at {MAX_TOTAL_LINES} simulated lines — reduce "
                                            f"the unit counts to model the rest."})
                break
            row = dict(base)
            # One TRANSACTION per simulated unit: a unit-basis gate that collapses a transaction to one
            # paying line must see genuinely distinct sales, not N lines of one receipt.
            row["trans_id"] = f"SIM-{seq:05d}"
            if amt_meaning == "gp":
                row["gp"] = amount
                row["ext_price"] = amount        # a GP-only line still needs a price for the guards
            elif amt_meaning == "ext_price":
                row["ext_price"] = amount
                row["gp"] = amount
            elif amt_meaning == "mrc":
                mdn = f"{_SIM_MDN_PREFIX}{seq:03d}"[-10:]
                row["mdn"] = mdn
                mrc_override[mdn] = amount
            lines.append(row)
        applied.append({"lever": key, "units": units, "amount": amount if needs_amt else None,
                        "payout_kind": kind})
    return lines, mrc_override, applied, warnings


def simulate(client, org_id, period, rep_name, store, market, inputs):
    """Run the REAL engine over synthetic lines and return ITS answer. Writes nothing.

    Every dollar in the reply comes out of `commission_engine.preview()` — see the module docstring
    for the exact call chain. This function's own arithmetic is limited to picking the caller's row
    out of `by_rep`."""
    from app.modules.commcalc import commission_engine as ce
    plan, ready, reason = _resolve_my_plan(client, org_id, rep_name, store, market)
    if plan is None:
        return {"ok": False, "ready": ready, "reason": reason, "plan": None,
                "result": None, "levers": [], "tier": None}
    levers, tier = build_levers(client, org_id, plan)
    lines, mrc_override, applied, warnings = build_lines(
        client, org_id, plan, rep_name, store, _canon_period(period), inputs)

    if not lines:
        return {"ok": True, "ready": True, "reason": None, "no_input": True,
                "plan": {"id": plan.get("id"), "name": plan.get("name")},
                "levers": levers, "tier": tier, "applied": applied, "warnings": warnings,
                "result": {"total_payout": 0.0, "base_payout": 0.0, "tiered_payout": 0.0,
                           "tier_multiplier": 1.0, "qualifying_units": 0, "rules": []},
                "engine": "commission_engine.preview (read-only, sales_override)"}

    pv = ce.preview(client, org_id, _canon_period(period), plan_id=plan.get("id"),
                    detail=True, only_rep=rep_name,
                    sales_override=lines, mrc_override=mrc_override)
    row = None
    for r in (pv.get("by_rep") or []):
        if ce._canon_person(r.get("rep")) == ce._canon_person(rep_name):
            row = r
            break
    if row is None and (pv.get("by_rep") or []):
        row = (pv.get("by_rep") or [])[0]
    if row is None:
        return {"ok": False, "ready": True,
                "reason": ("The engine produced no payout row for you — usually this means no "
                           "commission plan is attached to your name/store."),
                "plan": {"id": plan.get("id"), "name": plan.get("name")},
                "levers": levers, "tier": tier, "applied": applied, "warnings": warnings,
                "result": None, "engine": "commission_engine.preview (read-only, sales_override)"}

    # A rule the engine matched ZERO lines for, despite the rep asking for units, is reported LOUDLY:
    # a silent $0 on a lever is exactly how a simulator starts lying.
    asked = {a["lever"] for a in applied}
    paid_rules = {f"rule:{rb.get('rule_id')}" for rb in (row.get("rules") or [])
                  if int(rb.get("matched_lines") or 0) > 0}
    for k in sorted(asked - paid_rules):
        warnings.append({"lever": k, "code": "no_match",
                         "message": "The engine matched none of the simulated lines to this rule — "
                                    "its matcher may need a value this simulation can't produce."})
    return {
        "ok": True, "ready": True, "reason": None,
        "plan": {"id": plan.get("id"), "name": plan.get("name"),
                 "carrier_id": plan.get("carrier_id")},
        "levers": levers, "tier": tier, "applied": applied, "warnings": warnings,
        "lines_simulated": len(lines),
        "result": {
            "total_payout": row.get("total_payout"),
            "base_payout": row.get("base_payout"),
            "tiered_payout": row.get("tiered_payout"),
            "tier_multiplier": row.get("tier_multiplier"),
            "tier_units": row.get("tier_units"),
            "tier_basis": row.get("tier_basis"),
            "qualifying_units": row.get("qualifying_units"),
            "setup_fee_comm": row.get("setup_fee_comm"),
            "setup_fee_collected": row.get("setup_fee_collected"),
            "rules": [{"rule_id": rb.get("rule_id"), "label": rb.get("label"),
                       "payout_kind": rb.get("payout_kind"), "tiered": rb.get("tiered"),
                       "matched_lines": rb.get("matched_lines"),
                       "qualifying_units": rb.get("qualifying_units"),
                       "payout": rb.get("payout")}
                      for rb in (row.get("rules") or [])],
        },
        "pay_gate": pv.get("pay_gate"),
        "engine": "commission_engine.preview (read-only, sales_override)",
        "no_persist": True,
    }


def context(client, authorization, period, requested_rep=""):
    """GET payload: who am I, which plan pays me, what levers do I have — no simulation yet."""
    me = require_self(client, authorization, requested_rep, period)
    org_id = me["org_id"]
    mode = _carrier_mode(client, org_id)
    base = {"period": _canon_period(period), "rep": me["display_name"], "rep_name": me["rep_name"],
            "store": me["store"], "market": me["market"], "employee_id": me["employee_id"],
            "carrier_mode": mode, "org_id": org_id, "no_persist": True,
            "engine": "commission_engine.preview (read-only, sales_override)"}
    if mode == "boost":
        return {**base, "ok": False, "unsupported": "boost",
                "reason": ("Your tenant is paid by the Boost component engine, not by Commission "
                           "Plans, so a plan-based simulation would not match your real payout. "
                           "Use What-If → Employee Payout for Boost scenarios."),
                "plan": None, "levers": [], "tier": None}
    plan, ready, reason = _resolve_my_plan(client, org_id, me["rep_name"], me["store"], me["market"])
    if plan is None:
        return {**base, "ok": False, "ready": ready, "reason": reason, "plan": None,
                "levers": [], "tier": None}
    levers, tier = build_levers(client, org_id, plan)
    return {**base, "ok": True, "ready": True, "reason": None,
            "plan": {"id": plan.get("id"), "name": plan.get("name"),
                     "carrier_id": plan.get("carrier_id")},
            "levers": levers, "tier": tier}


def run(client, authorization, period, inputs, requested_rep=""):
    """POST payload: the projected pay for the caller's OWN levers. Read-only, no persist."""
    me = require_self(client, authorization, requested_rep, period)
    org_id = me["org_id"]
    mode = _carrier_mode(client, org_id)
    head = {"period": _canon_period(period), "rep": me["display_name"], "rep_name": me["rep_name"],
            "store": me["store"], "market": me["market"], "carrier_mode": mode,
            "org_id": org_id, "no_persist": True}
    if mode == "boost":
        return {**head, "ok": False, "unsupported": "boost",
                "reason": ("Your tenant is paid by the Boost component engine, not by Commission "
                           "Plans. Use What-If → Employee Payout for Boost scenarios."),
                "result": None, "levers": [], "tier": None}
    return {**head, **simulate(client, org_id, _canon_period(period), me["rep_name"],
                               me["store"], me["market"], inputs or {})}
