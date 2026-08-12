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

THE SECOND ENGINE (added 2026-08-12, owner: "what would I make does not include the multi month"): a
plan-mode tenant is paid by TWO engines. The plan RULES above pay on this month's sales; the
SALE-TRIGGERED CHAIN (`sale_installment_engine.compute_sale_installments`) pays an activation its
M1..MN residual over the following months. Projecting only the first one told a rep an activation was
worth its one-off rule and nothing else. The chain is now projected the same way — synthetic
activations handed to the REAL installment engine through its read-only `_sales_override`, once per
month of the chain — and reported SEPARATELY (`multi_month`), never folded into `total_payout`. See
the MULTI-MONTH section below for why it reads the engine's pre-gate `expected_amount` column.

SELF-ONLY (RULE: an employee must not simulate a coworker). `resolve_self()` reads the identity from
the bearer token via `app_users.auth_id` — the same rule storeops uses for every self-service view
(/timeclock/status, /my-chargebacks). A `rep` query param is accepted ONLY so the 403 is explicit:
anything that is not the caller's own resolved rep name is refused unless the caller is a super-admin
or holds company-wide ('all') scope — those roles already read every rep's pay on the Rep Commission
Report, so refusing them here would be theatre, not security.

MULTI-TENANT (rewritten 2026-08-04 — this used to be the bug): the acting org is the
MIDDLEWARE-VERIFIED `org_id` query param, matched against the login's MEMBERSHIP SET. One login has
one `app_users` row PER TENANT (mig 706), so the previous `.eq("auth_id", uid).limit(1)` picked an
ARBITRARY membership — in practice the house/Boost one — and pinned carrier mode, plan resolution and
every downstream read to the wrong tenant. That is the documented hardcoded-house-ORG_ID leak class:
the acting org was never threaded from the query string. Now:

  • normal user      → tenant_middleware has ALREADY rewritten org_id to a verified membership, so
                       honoring it can never widen access; a requested org the login is not a member
                       of falls back to its default membership exactly as the middleware would.
  • super-admin      → the middleware deliberately does NOT rewrite their org_id (that bypass is what
                       makes "acting as tenant X" work), so the request's org_id IS the tenant they
                       switched into and the simulator follows them there.
  • every read stays `.eq("org_id", org)` against that resolved org.

WHO IS SIMULATED. Self-service by default. A privileged caller (super-admin, or a company-wide
'all' / admin role — the people who already read every rep's pay on the Rep Commission Report) may
name another rep, and then the rep's OWN store/market drive plan resolution, not the caller's. A
super-admin acting in a tenant they don't sell in has no employee record there: that is not an error,
it answers with the tenant's roster so they can PICK whose plan to model (RULE THREE, pick-don't-type).
Owner directive 2026-08-04: "this should be for all employee across all tenants."

CARRIER MODE: this is the PLAN engine (`commission_engine`). A Boost/house tenant is paid by the
legacy `calculator.py` component engine instead, so for a rep whose org resolves to carrier_mode
'boost' the simulator reports an explicit, honest unsupported-state rather than quoting plan-engine
dollars that would never be paid. (What-If's 🎯 Employee Payout tab is the Boost-side tool.)
That unsupported state is keyed on the ACTING tenant's carrier config — it must never appear for a
plan-mode tenant such as Luxelink/Total.
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


def _memberships(client, uid):
    """EVERY `storeops.app_users` row for this auth_id — the login's tenant MEMBERSHIP SET.

    A `.limit(1)` here is a TENANT BUG, not an optimisation: since mig 706 one login has one row per
    tenant, so limit(1) silently picks an arbitrary membership. Column ladder mirrors
    `tenant_middleware._fetch_memberships` so an un-run mig 706/711 column can never break identity
    (pre-706 there is at most one row anyway). The public-schema retry is a defensive fallback for
    deployments that expose app_users there. NOT org-filtered on purpose — auth_id is the key and the
    rows themselves NAME the tenants."""
    for cols in ("org_id,employee_id,store_code,store_codes,full_name,super_admin,is_default_org",
                 "org_id,employee_id,store_code,store_codes,full_name,super_admin",
                 "org_id,employee_id,store_code,store_codes,full_name"):
        for _tbl in (lambda: client.schema("storeops").table("app_users"),
                     lambda: client.table("app_users")):
            try:
                rows = (_tbl().select(cols).eq("auth_id", uid).execute().data) or []
            except Exception:
                continue
            if rows:
                return rows
    return []


def _pick_membership(rows, want_org):
    """Which membership the caller is ACTING as. PURE (no client) so it is directly unit-testable.

    Same rule `tenant_middleware` applies to `x-active-org`: honor the requested org when the login is
    a member of it, else the login's DEFAULT membership (`is_default_org`), else the first row. It
    never invents a membership — a requested org the login doesn't belong to simply loses."""
    if not rows:
        return None
    want = str(want_org or "").strip().lower()
    if want:
        for r in rows:
            if str(r.get("org_id") or "").strip().lower() == want:
                return r
    for r in rows:
        if r.get("is_default_org"):
            return r
    return rows[0]


def resolve_self(client, authorization, period="", requested_org=""):
    """The SIGNED-IN employee's (org_id, employee_id, rep_name, store, market, employee_row) IN THE
    TENANT THEY ARE ACTING AS.

    `requested_org` is the endpoint's `org_id` query param — already rewritten to a verified
    membership by `tenant_middleware` for every normal user, and deliberately left alone for a
    super-admin (that bypass is how "acting as tenant X" works). Honoring it is therefore never a
    widening: see the module docstring.

    `rep_name` is the name the SALES/PAY data uses, not the friendly display name: an employee stored
    as "Ali" sells as "ali, mohammad khalid", and the plan's employee-scope assignment is keyed on the
    latter. Resolution order — epay_salesperson → a matching rep_commissions row → the display name.
    Raises 401/403 with an actionable message, never a bare 500."""
    from fastapi import HTTPException
    from app.modules.commcalc.commission_engine import _canon_person
    uid = _uid(authorization)
    if not uid:
        raise HTTPException(401, "Sign in to use the pay simulator.")
    rows = _memberships(client, uid)
    if not rows:
        raise HTTPException(403, "Your login isn't provisioned in any tenant yet. Ask an admin to add "
                                 "you under Roles & Access.")
    super_admin = any(bool(r.get("super_admin")) for r in rows)
    want = str(requested_org or "").strip()
    u = _pick_membership(rows, want) or {}
    org = str(u.get("org_id") or "").strip() or _HOUSE_ORG
    cross_tenant = False
    if want and super_admin and want.lower() != org.lower():
        # SUPER-ADMIN CROSS-TENANT. The middleware does not rewrite their org_id, so the request's
        # org_id IS the tenant they switched into. Follow them there and DROP the home-tenant
        # membership fields (employee_id / store_code), which name nothing in that tenant.
        org, u, cross_tenant = want, {"org_id": want}, True
    eid = str(u.get("employee_id") or "").strip()
    if not eid and not super_admin:
        raise HTTPException(403, "Your login isn't linked to an employee record, so there is no pay "
                                 "plan to simulate. Ask an admin to set your Employee ID in "
                                 "Roles & Access.")
    emp = {}
    if eid:
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
            "store": store, "market": market, "employee": emp,
            "super_admin": super_admin, "cross_tenant": cross_tenant,
            "memberships": [str(r.get("org_id") or "") for r in rows]}


def require_self(client, authorization, requested_rep, period="", requested_org=""):
    """Resolve self IN THE ACTING TENANT and REFUSE a request aimed at anybody else. Returns the
    `resolve_self` dict (plus `impersonated` / `needs_rep` / `privileged`).

    `requested_rep` blank / equal (canonically) to the caller's own rep name  -> allowed.
    Anything else                                                            -> 403, UNLESS the caller
    is a super-admin or holds company-wide ('all') / admin scope (they already read every rep's pay).

    Two behaviours added 2026-08-04, both scoped to callers who were ALREADY allowed to name a rep:
      • the named rep's OWN store/market are resolved from the acting tenant's roster and used for
        plan resolution — previously the CALLER's store leaked in, which resolves the wrong plan for
        anyone whose plan is store- or market-scoped;
      • a privileged caller with no employee record in the acting tenant (a super-admin who switched
        into a tenant they don't sell in) gets `needs_rep` instead of a 403 dead end.
    Neither widens who may be simulated: `is_privileged` is unchanged and still gates both."""
    from fastapi import HTTPException
    from app.modules.commcalc.commission_engine import _canon_person
    me = resolve_self(client, authorization, period, requested_org)
    privileged = bool(me.get("super_admin")) or is_privileged(
        _caller_perms(client, authorization, me["org_id"]))
    me = dict(me, privileged=privileged)
    want = str(requested_rep or "").strip()
    if not want:
        if me["rep_name"]:
            return me
        if privileged:
            return dict(me, needs_rep=True)
        raise HTTPException(403, "Your login isn't linked to an employee record, so there is no pay "
                                 "plan to simulate. Ask an admin to set your Employee ID in "
                                 "Roles & Access.")
    if _canon_person(want) == _canon_person(me["rep_name"]) or _canon_person(want) == _canon_person(me["display_name"]):
        return me
    if privileged:
        me = dict(me)
        me["rep_name"] = want
        me["display_name"] = want
        me["impersonated"] = True
        store, market = _rep_context(client, me["org_id"], want)
        if store:
            me["store"], me["market"] = store, market
        elif me.get("cross_tenant") or me.get("needs_rep"):
            me["store"], me["market"] = "", ""
        return me
    raise HTTPException(403, "The pay simulator only models YOUR own pay plan — you can't simulate "
                             "another employee's commission.")


def _rep_context(client, org_id, rep_name):
    """(store, market) for a rep NAMED by a privileged caller, resolved from the ACTING tenant's own
    data — roster first (`storeops.employees`, matched on epay_salesperson or name), then the rep's
    `rep_commissions` row. Org-scoped on every read; ('', '') when the name isn't in this tenant, in
    which case plan resolution simply falls back to role/default scope. Never raises."""
    from app.modules.commcalc.commission_engine import _canon_person, _read_store_market
    key = _canon_person(rep_name)
    if not key:
        return "", ""
    store = ""
    try:
        rows = (client.schema("storeops").table("employees")
                .select("name,epay_salesperson,home_store")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    for e in rows:
        if key in _name_keys(e.get("name"), e.get("epay_salesperson")):
            store = str(e.get("home_store") or "").strip()
            if store:
                break
    if not store:
        try:
            rc = (client.schema("commcalc").table("rep_commissions")
                  .select("storeops_name,epay_salesperson,store,store_code")
                  .eq("org_id", org_id).limit(20000).execute().data) or []
        except Exception:
            rc = []
        for r in rc:
            if key in _name_keys(r.get("epay_salesperson"), r.get("storeops_name")):
                store = str(r.get("store") or r.get("store_code") or "").strip()
                if store:
                    break
    market = ""
    if store:
        try:
            sm = _read_store_market(client, org_id)
            market = sm.get(store.lower(), "") or sm.get(store.split(" ")[0].lower(), "")
        except Exception:
            market = ""
    return store, market


def employee_roster(client, org_id, limit=2000):
    """The ACTING tenant's own people, for the privileged employee picker (RULE THREE — pick, don't
    type: the options are the org's REAL roster, never a typed name).

    `value` is the string the plan engine and an employee-scope assignment actually match on
    (`epay_salesperson || name`), so picking a person here resolves the same plan the payout would.
    Same-named people are disambiguated by email (§3b). Inactive people are INCLUDED and flagged — a
    mid-month leaver still has period sales, exactly as `_read_employee_roles` treats them.
    Org-scoped; returns [] on any failure so the page degrades to self-only instead of erroring."""
    rows = []
    for cols in ("id,name,email,epay_salesperson,home_store,is_active",
                 "id,name,epay_salesperson,home_store",
                 "id,name"):
        try:
            rows = (client.schema("storeops").table("employees").select(cols)
                    .eq("org_id", org_id).order("name").execute().data) or []
            break
        except Exception:
            continue
    out, seen = [], set()
    for e in rows:
        value = str(e.get("epay_salesperson") or e.get("name") or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        out.append({"value": value,
                    "label": str(e.get("name") or value).strip(),
                    "email": str(e.get("email") or "").strip(),
                    "store": str(e.get("home_store") or "").strip(),
                    "active": e.get("is_active", True) is not False})
    dupes = {}
    for p in out:
        dupes[p["label"].lower()] = dupes.get(p["label"].lower(), 0) + 1
    for p in out:
        if dupes.get(p["label"].lower(), 0) > 1 and p["email"]:
            p["label"] = "%s — %s" % (p["label"], p["email"])
    return out[:limit]


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
    # % OF THE SALE PRICE — the kind the owner's own accessory rules use (owner 2026-08-04: accessory
    # pay tracks what the rep actually sold it for, not GP). It was MISSING from this map, and a kind
    # that isn't here falls to the (False, None) default: no price input is rendered, so every
    # simulated line is minted at ext_price 0 and the rule pays 17.5% of nothing. That is the "$0 for
    # accessories" defect — the engine was right, the simulator never gave it a price to work with.
    # It is also why accessories had no per-item / per-month choice: `basis_options` only attach to a
    # lever with an amount input.
    "pct_price": (True, "ext_price", "units"),
    "pct_price_over_cost": (True, "ext_price", "units"),
    "pct_mrc": (True, "mrc", "lines"),
}
# Every payout kind the ENGINE pays on. A rule whose kind is not in _KIND_INPUT above pays $0 in a
# simulation no matter what the rep types, so the lever says so out loud instead of quoting zero.
_ENGINE_KINDS = {"flat_per_unit", "flat", "pct_gp", "pct_price", "pct_price_over_cost", "pct_mrc"}


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
        if kind not in _KIND_INPUT:
            # NEVER a silent $0 again (the pct_price defect). A kind this module can't mint a line for
            # is reported as unsimulatable, whether or not the engine knows how to pay it.
            lever["simulatable"] = False
            lever["note"] = (f"This rule pays on '{kind}', which the simulator can't model yet"
                             + (" — it is paid normally, it just can't be projected here."
                                if kind in _ENGINE_KINDS else
                                " and which the pay engine does not recognise either — check the rule "
                                "under Commission Plans."))
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


# ══ THE MULTI-MONTH (RESIDUAL) HALF OF THE PAY ═════════════════════════════════════════════════════
# Owner 2026-08-12: "what would I make does not include the multi month".
#
# A tenant on the plan engine is paid by TWO engines, not one (the same two the commission drill-down
# explains): `commission_engine.preview` pays the plan's RULES on the month's sales, and
# `sale_installment_engine.compute_sale_installments` pays the SALE-TRIGGERED chain — the 3MR/M1-M6
# residual a qualifying activation earns over the following months. The simulator only ever called the
# first one, so an activation showed whatever its one-off rule pays and NOTHING for the months it keeps
# earning: on luxelink's Chicago plan the 3MR chain (M1 5% of MRC, M3 13% of MRC) was simply invisible.
#
# SAME DESIGN RULE AS THE REST OF THIS MODULE: the math is the engine's. We mint synthetic activations
# and hand them to `compute_sale_installments` through its read-only `_sales_override`, once per month of
# the chain, then read the ENGINE's own `expected_amount` off the ledger rows it produced.
#
# WHY `expected_amount` AND NOT `amount`: months 2..N are GATED on proof the carrier actually paid the
# dealer that month (gate_mode 'paid_residual'/'ma_residual' — "we pay as we get paid"). A sale that has
# not happened yet can have no such proof, so every gate is unmet and `amount` is $0 by design. The
# engine already carries the PRE-GATE figure on every ledger row for exactly this question (mig 258,
# "calculate the expected commission as a separate column but not use that to pay out"), so the
# projection reads that column instead of asking the engine to pretend a gate was met. The number is
# therefore honestly labelled: what the chain is WORTH if the line stays active and the carrier pays.
MAX_MULTIMONTH_UNITS = 999


def load_schedules(client, org_id, plan_id):
    """(schedules, {schedule_id: [installment_line, ...]}) for ONE plan — the tenant's own multi-month
    config, org-scoped. [] when migration 201 isn't applied or the plan has no chain. Never raises."""
    try:
        from app.modules.commcalc.sale_installment_engine import _load_schedules
        scheds, lines_by = _load_schedules(client, org_id)
    except Exception:
        return [], {}
    mine = [s for s in scheds if str(s.get("plan_id") or "") == str(plan_id or "")]
    return mine, lines_by


def _schedule_month_rows(sched, ilines):
    """The schedule's own per-month rate card, month 1..N, straight off `plan_installment_line`."""
    n = min(12, int(sched.get("num_months") or 1))
    by_idx = {int(l.get("month_index") or 0): l for l in (ilines or [])}
    out = []
    for m in range(1, n + 1):
        il = by_idx.get(m)
        kind = (str((il or {}).get("payout_kind") or "flat").strip().lower())
        out.append({
            "month_index": m,
            "payout_kind": kind if il else None,
            "pct": safe_float((il or {}).get("mrc_pct")) if kind == "pct_mrc" else 0.0,
            "flat": safe_float((il or {}).get("flat_amount")) if kind != "pct_mrc" else 0.0,
            "configured": il is not None,
        })
    return out


def build_multimonth_levers(client, org_id, plan):
    """One lever per ACTIVE multi-month schedule on this plan: how many qualifying activations, and the
    rate plan's MRC. Everything shown — the label, the trigger, the number of months, each month's
    percentage — is read from the tenant's own schedule rows (RULE TWO: nothing hard-coded)."""
    scheds, lines_by = load_schedules(client, org_id, plan.get("id"))
    levers = []
    bucket_cts = None
    for s in scheds:
        field = str(s.get("trigger_match_field") or "").strip().lower()
        op = str(s.get("trigger_match_op") or "equals").strip().lower()
        raw = str(s.get("trigger_match_value") or "").strip()
        value = ([x.strip() for x in raw.split(",") if x.strip()] or [raw])[0] if op == "in" else raw
        months = _schedule_month_rows(s, lines_by.get(s.get("id")))
        lever = {
            "key": f"sched:{s.get('id')}",
            "schedule_id": s.get("id"),
            "kind": "multi_month",
            "label": s.get("name") or "Multi-month commission",
            "num_months": min(12, int(s.get("num_months") or 1)),
            "trigger_field": field or "any",
            "trigger_value": raw,
            "count_unit": "activations",
            "amount_label": "Plan MRC $/mo",
            "months": months,
            "gate_mode": s.get("gate_mode"),
            "simulatable": True,
            "note": None,
        }
        if field == "activation_bucket":
            if bucket_cts is None:
                bucket_cts = _bucket_contract_types(client, org_id)
            if (value or "").strip().lower() not in bucket_cts:
                lever["simulatable"] = False
                lever["note"] = ("This chain starts on the activation bucket '%s', and no contract type "
                                 "in your tenant's classification map resolves to it yet." % (value or "?"))
        elif not field or field == "any":
            lever["simulatable"] = False
            lever["note"] = "This chain has no trigger configured, so there is nothing to model."
        if not any(m["configured"] for m in months):
            lever["simulatable"] = False
            lever["note"] = ("This chain has no month rows configured under Plan Installments, so it "
                             "pays nothing yet.")
        levers.append(lever)
    return levers


def build_multimonth_lines(client, org_id, lever, rep_name, store, period, spec):
    """Synthetic ACTIVATIONS for one multi-month lever, raw_sales-shaped. Never written anywhere.

    ONE TRANSACTION AND ONE MDN PER ACTIVATION, deliberately: the installment engine collapses a
    transaction to ONE chain per subscriber (the 2026-07-25 money fix), so N activations sharing a
    trans_id would pay ONCE and the projection would under-quote the rep by a factor of N.

    The typed MRC rides on the line under `SIM_MRC_KEY` rather than being written into a description for
    the engine's extractor to re-read — a round-trip through text is a second place for the number to
    change meaning."""
    from app.modules.commcalc.sale_installment_engine import SIM_MRC_KEY
    units = int(max(0, min(MAX_MULTIMONTH_UNITS, int(safe_float((spec or {}).get("units"))))))
    mrc = safe_float((spec or {}).get("amount"))
    if units <= 0:
        return [], None
    field = (lever.get("trigger_field") or "").strip().lower()
    op_raw = str(lever.get("trigger_value") or "")
    base = {
        "salesperson": rep_name, "store": store, "period": _canon_period(period),
        "trans_date": _period_midpoint(period), "voided": None, "trans_type": "Sale",
        "department": "", "category": "", "product_desc": f"Projected {lever.get('label') or 'activation'}",
        "contract_type": "", "ext_price": 0.0, "gp": 0.0, "product_id": None, "subscriber_id": "",
        SIM_MRC_KEY: mrc, "_simulated": True,
    }
    if field == "activation_bucket":
        cts = _bucket_contract_types(client, org_id)
        want = ([x.strip() for x in op_raw.split(",") if x.strip()] or [op_raw])[0].strip().lower()
        ct = cts.get(want)
        if not ct:
            return [], ("No contract type in your classification map resolves to activation bucket "
                        f"'{want}', so this chain could not be projected.")
    elif field and field != "any":
        base[field] = ([x.strip() for x in op_raw.split(",") if x.strip()] or [op_raw])[0]
        ct = None
    else:
        return [], "This chain has no trigger configured, so there is nothing to model."
    if field == "activation_bucket":
        base["contract_type"] = ct
    out = []
    for i in range(units):
        # The MDN is what the engine splits subscribers on; the serial keeps months 2..N joinable in the
        # real world, so a simulated chain carries one too rather than tripping the no-identity warning.
        # It is 15 DIGITS on purpose: `installment_category.serial_kind` reads 14-17 digits as an IMEI,
        # which resolves the chain's device category to 'phone' — what a premium activation actually is.
        # A non-numeric placeholder would resolve to 'unknown', and a tenant that switches the unknown
        # category off would then see the whole projection silently drop to $0.
        out.append(dict(base, trans_id=f"SIMMM-{str(lever.get('schedule_id'))[:8]}-{i + 1:04d}",
                        mdn=f"{_SIM_MDN_PREFIX}{i + 1:03d}"[-10:],
                        serial_1=f"99900000000{i + 1:04d}"[-15:]))
    return out, None


def simulate_multimonth(client, org_id, period, rep_name, store, plan, inputs):
    """{levers, months, total_chain, total_this_month, warnings} — what the multi-month chains this
    month's simulated activations are worth, month by month, computed BY THE INSTALLMENT ENGINE.

    One engine call per month of the chain: the same synthetic sale is fed as the sale period, and the
    pay period walks forward, which is exactly how a real sale earns month 1 now, month 2 next month and
    so on. Read-only — `_sales_override` refuses to persist."""
    from app.modules.commcalc import sale_installment_engine as sie
    from app.modules.commcalc.installment_engine import _shift_period
    from app.modules.commcalc.commission_engine import _canon_person
    levers = build_multimonth_levers(client, org_id, plan)
    sale_period = _canon_period(period)
    warnings, out_levers = [], []
    lines_by_lever, want_months = {}, 1
    for lv in levers:
        spec = (inputs or {}).get(lv["key"]) or {}
        if not lv.get("simulatable"):
            if int(safe_float(spec.get("units"))) > 0:
                warnings.append({"lever": lv["key"], "code": "chain_unsimulatable",
                                 "message": f"'{lv['label']}' — {lv.get('note')}"})
            continue
        lines, err = build_multimonth_lines(client, org_id, lv, rep_name, store, sale_period, spec)
        if err:
            warnings.append({"lever": lv["key"], "code": "chain_no_trigger",
                             "message": f"'{lv['label']}' — {err}"})
            continue
        if not lines:
            continue
        lines_by_lever[lv["key"]] = lines
        want_months = max(want_months, int(lv.get("num_months") or 1))
    if not lines_by_lever:
        return {"levers": levers, "months": [], "by_lever": [], "total_chain": 0.0,
                "total_this_month": 0.0, "warnings": warnings, "engine": _MM_ENGINE}

    all_lines = [ln for lns in lines_by_lever.values() for ln in lns]
    by_month, by_lever_month = [], {}
    for k in range(want_months):
        pay_period = _shift_period(sale_period, k) if k else sale_period
        try:
            res = sie.compute_sale_installments(client, org_id, pay_period, persist=False,
                                                _sales_override={sale_period: all_lines})
        except Exception as e:
            warnings.append({"lever": None, "code": "installment_engine_failed",
                             "message": f"The multi-month engine could not project month {k + 1}: {e}"})
            continue
        rows = [r for r in (res.get("ledger") or [])
                if _canon_person(r.get("epay_salesperson")) == _canon_person(rep_name)]
        # `expected_amount` is the PRE-GATE figure the engine itself carries — see the section header.
        amt = round(sum(safe_float(r.get("expected_amount")) for r in rows), 2)
        by_month.append({"month_index": k + 1, "pay_period": pay_period,
                         "amount": amt, "chains": len(rows)})
        for r in rows:
            per = by_lever_month.setdefault(f"sched:{r.get('schedule_id')}", {})
            per[k + 1] = round(per.get(k + 1, 0.0) + safe_float(r.get("expected_amount")), 2)
        if k == 0 and not rows and all_lines:
            # A month-1 miss is the one worth explaining: the activations were minted but the engine
            # enrolled none of them, so the projection would otherwise read as an honest $0.
            warnings.append({"lever": None, "code": "no_chain_enrolled",
                             "message": ("The multi-month engine enrolled none of the simulated "
                                         "activations. Usually this means the plan that pays this rep "
                                         "has no active installment schedule, or the schedule's "
                                         "effective dates exclude this month.")})
    for lv in levers:
        months = by_lever_month.get(lv["key"]) or {}
        out_levers.append(dict(lv, projected={
            "by_month": [{"month_index": m, "amount": months.get(m, 0.0)}
                         for m in range(1, int(lv.get("num_months") or 1) + 1)],
            "total": round(sum(months.values()), 2),
            "this_month": round(months.get(1, 0.0), 2)}))
    return {
        "levers": out_levers, "months": by_month, "by_lever": out_levers,
        "total_chain": round(sum(m["amount"] for m in by_month), 2),
        "total_this_month": round(by_month[0]["amount"] if by_month else 0.0, 2),
        "warnings": warnings,
        "engine": _MM_ENGINE,
        "gated": True,
    }


_MM_ENGINE = "sale_installment_engine.compute_sale_installments (read-only, _sales_override)"


def multimonth_actuals(client, org_id, period, plan, rep_name):
    """{lever_key: {units, amount, from_actuals}} — the rep's REAL activations this period and the MRC
    they actually carried, read off `sale_installment_ledger` (the engine's own output, month 1 rows for
    sales made in this period). Same principle as `current_actuals`: seed from what happened, never from
    an invented default. Never raises."""
    out = {}
    scheds, _lines_by = load_schedules(client, org_id, plan.get("id"))
    if not scheds:
        return out
    try:
        from app.modules.commcalc.commission_engine import _canon_person
        from app.modules.commcalc.installment_engine import _pvariants
        rows = (client.schema("commcalc").table("sale_installment_ledger")
                .select("schedule_id,epay_salesperson,sale_period,month_index,mrc_at_pay,expected_amount")
                .eq("org_id", org_id).in_("sale_period", list(_pvariants(_canon_period(period))))
                .eq("month_index", 1).limit(20000).execute().data) or []
    except Exception as e:
        print(f"WARN pay_simulator.multimonth_actuals failed (chain levers seed empty): {e}")
        return out
    key = _canon_person(rep_name)
    agg = {}
    for r in rows:
        if _canon_person(r.get("epay_salesperson")) != key:
            continue
        a = agg.setdefault(f"sched:{r.get('schedule_id')}", {"units": 0, "mrc": []})
        a["units"] += 1
        m = safe_float(r.get("mrc_at_pay"))
        if m:
            a["mrc"].append(m)
    for k, a in agg.items():
        out[k] = {"units": a["units"],
                  "amount": round(sum(a["mrc"]) / len(a["mrc"]), 2) if a["mrc"] else 0.0,
                  "from_actuals": True}
    return out


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
        # A 'month' basis carries its whole figure in `amount`, so a blank unit count is a legitimate
        # input there — skipping on units alone would silently drop the lever the owner just filled in.
        if units <= 0 and not (str(spec.get("basis") or "").strip().lower() == "month"
                               and safe_float(spec.get("amount")) > 0):
            continue
        kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
        needs_amt, amt_meaning, _cu = _KIND_INPUT.get(kind, (False, None, "units"))
        amount = safe_float(spec.get("amount"))
        field, value = _match_target(rule)

        # ── BASIS (owner 2026-08-11): "the rate of accessories or accessories per month as a drop
        #    down option … the selling price and the number of accessories, or the acc per month x %".
        #    Two ways to say the same thing, mirroring the What-If page's own 'item' / 'month' choice
        #    so the two surfaces can never disagree about what a number means:
        #      'item'  (default, = today's behaviour byte-for-byte) — `amount` is the price of ONE
        #              accessory; N units ⇒ N lines at that price.
        #      'month' — `amount` is the MONTH'S TOTAL sales $ for this rule. The total is SPREAD
        #              across the unit count rather than booked as a single line, so the percentage
        #              sees the same dollars AND the tier still sees the same unit count. Collapsing
        #              it to one line would silently change unit-based tier qualification — which is
        #              exactly how a simulator starts disagreeing with payroll.
        basis = str(spec.get("basis") or "item").strip().lower()
        if basis == "month" and needs_amt:
            if units <= 0:
                units = 1
            per_unit = round(amount / units, 4) if units else amount
            if not safe_float(spec.get("units")):
                warnings.append({"lever": key, "code": "month_basis_no_count",
                                 "message": (f"'{rule.get('label') or 'This rule'}' was modelled from a "
                                             f"monthly total with no accessory count, so it is one line. "
                                             f"If this rule qualifies on UNIT COUNT, enter the number of "
                                             f"accessories too — otherwise tier qualification is not "
                                             f"being modelled.")})
            amount = per_unit

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
                        "basis": basis if needs_amt else None, "payout_kind": kind})
    return lines, mrc_override, applied, warnings


def current_actuals(client, org_id, period, plan, rep_name):
    """{lever_key: {units, amount, from_actuals}} — what this rep ACTUALLY did this period.

    OWNER 2026-08-11: the simulator "should have the current numbers not just placeholder 10 each".
    It used to seed every lever with a hard-coded 1/10 units and $50/$25 — numbers nobody chose, that
    look like a projection. The figures below come from `commission_engine.preview()` run with NO
    sales_override, i.e. **the same engine over the same real lines that actually pay this rep**, so
    the starting point can never drift from payroll the way a second implementation would.

    A lever the rep has no history for is returned as 0, NOT as a comfortable default: an invented
    quantity is how a simulator starts lying. Never raises — a failure just means "no seed".
    """
    out = {}
    try:
        from app.modules.commcalc import commission_engine as ce
        pv = ce.preview(client, org_id, _canon_period(period), plan_id=plan.get("id"),
                        detail=True, only_rep=rep_name)
        row = None
        for r in (pv.get("by_rep") or []):
            if ce._canon_person(r.get("rep")) == ce._canon_person(rep_name):
                row = r
                break
        if not row:
            return out
        for rb in (row.get("rules") or []):
            key = f"rule:{rb.get('rule_id')}"
            rlines = rb.get("lines") or []
            units = int(safe_float(rb.get("qualifying_units")) or len(rlines)
                        or safe_float(rb.get("matched_lines")))
            amounts = [safe_float(l.get("gp")) or safe_float(l.get("ext_price")) for l in rlines]
            amounts = [a for a in amounts if a]
            out[key] = {
                "units": units,
                # AVERAGE, not total: the 'item' basis asks for the price of ONE unit.
                "amount": round(sum(amounts) / len(amounts), 2) if amounts else 0.0,
                "month_total": round(sum(amounts), 2) if amounts else 0.0,
                "payout": round(safe_float(rb.get("payout")), 2),
                "from_actuals": True,
            }
    except Exception as e:
        print(f"WARN pay_simulator.current_actuals failed (levers will seed empty): {e}")
    return out


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
    # THE SECOND ENGINE. A plan-mode tenant is paid by the plan rules AND by the sale-triggered chain,
    # so a projection that runs only the first one under-quotes every activation by its whole residual.
    mm = simulate_multimonth(client, org_id, _canon_period(period), rep_name, store, plan, inputs)

    if not lines:
        return {"ok": True, "ready": True, "reason": None,
                "no_input": not (mm.get("total_chain") or 0),
                "plan": {"id": plan.get("id"), "name": plan.get("name")},
                "levers": levers, "tier": tier, "applied": applied,
                "warnings": warnings + (mm.get("warnings") or []),
                "multi_month": mm,
                "result": {"total_payout": 0.0, "base_payout": 0.0, "tiered_payout": 0.0,
                           "tier_multiplier": 1.0, "qualifying_units": 0, "rules": [],
                           "multimonth_this_month": mm.get("total_this_month") or 0.0,
                           "multimonth_chain_total": mm.get("total_chain") or 0.0,
                           "this_month_total": mm.get("total_this_month") or 0.0,
                           "chain_grand_total": mm.get("total_chain") or 0.0},
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
                "levers": levers, "tier": tier, "applied": applied,
                "warnings": warnings + (mm.get("warnings") or []), "multi_month": mm,
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
        "levers": levers, "tier": tier, "applied": applied,
        "warnings": warnings + (mm.get("warnings") or []),
        "lines_simulated": len(lines),
        "multi_month": mm,
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
            # BOTH ENGINES, kept as separate named figures so nothing is double-counted downstream:
            # `total_payout` stays exactly what the plan rules pay (the number this page has always
            # shown), and the chain is added alongside it.
            "multimonth_this_month": mm.get("total_this_month") or 0.0,
            "multimonth_chain_total": mm.get("total_chain") or 0.0,
            "this_month_total": round(safe_float(row.get("total_payout"))
                                      + safe_float(mm.get("total_this_month")), 2),
            "chain_grand_total": round(safe_float(row.get("total_payout"))
                                       + safe_float(mm.get("total_chain")), 2),
        },
        "pay_gate": pv.get("pay_gate"),
        "engine": "commission_engine.preview (read-only, sales_override)",
        "no_persist": True,
    }


def context(client, authorization, period, requested_rep="", requested_org=""):
    """GET payload: who am I (in the tenant I'm acting as), which plan pays me, what levers do I have
    — no simulation yet. `requested_org` is the middleware-verified org_id query param."""
    me = require_self(client, authorization, requested_rep, period, requested_org)
    org_id = me["org_id"]
    # CARRIER MODE IS THE ACTING TENANT'S, resolved from that org's OWN carrier config. This is the
    # line that produced the defect: with the org pinned to the house/Boost membership, a plan-mode
    # tenant (Luxelink/Total) was told it is "paid by the Boost component engine".
    mode = _carrier_mode(client, org_id)
    privileged = bool(me.get("privileged"))
    base = {"period": _canon_period(period), "rep": me["display_name"], "rep_name": me["rep_name"],
            "store": me["store"], "market": me["market"], "employee_id": me["employee_id"],
            "carrier_mode": mode, "org_id": org_id, "no_persist": True,
            "can_pick_rep": privileged, "impersonated": bool(me.get("impersonated")),
            "engine": "commission_engine.preview (read-only, sales_override)"}
    if privileged:
        base["reps"] = employee_roster(client, org_id)
    if mode == "boost":
        return {**base, "ok": False, "unsupported": "boost",
                "reason": ("Your tenant is paid by the Boost component engine, not by Commission "
                           "Plans, so a plan-based simulation would not match your real payout. "
                           "Use What-If → Employee Payout for Boost scenarios."),
                "plan": None, "levers": [], "tier": None}
    if not me["rep_name"]:
        return {**base, "ok": False, "ready": True, "needs_rep": True,
                "reason": ("Your login isn't linked to an employee record in this tenant — pick an "
                           "employee above to model their pay plan."),
                "plan": None, "levers": [], "tier": None}
    plan, ready, reason = _resolve_my_plan(client, org_id, me["rep_name"], me["store"], me["market"])
    if plan is None:
        return {**base, "ok": False, "ready": ready, "reason": reason, "plan": None,
                "levers": [], "tier": None}
    levers, tier = build_levers(client, org_id, plan)
    # Seed from THIS rep's real period, not from invented defaults (owner 2026-08-11). Switching rep
    # re-runs context, so the numbers always belong to the person on screen.
    cur = current_actuals(client, org_id, _canon_period(period), plan, me["rep_name"])
    seeded = 0
    for lv in levers:
        c = cur.get(lv["key"])
        if c:
            lv["current"] = c
            seeded += 1
        else:
            lv["current"] = {"units": 0, "amount": 0.0, "month_total": 0.0, "payout": 0.0,
                             "from_actuals": False}
        # The accessory/percent levers are the ones the owner asked to be able to express two ways
        # (owner 2026-08-11 + 2026-08-12: "$30 x 50 = 1500 * 17.5% … or $6000*17.5% — the user should
        # be able to use both to assess"). BOTH readings are offered on every percent lever, and the
        # month total is spread across the count so the tier still sees the units.
        if lv.get("amount_input"):
            _each = {"gp": "GP $ per item", "ext_price": "$ per item",
                     "mrc": "Plan MRC $/mo"}.get(lv.get("amount_meaning") or "",
                                                 lv.get("amount_label") or "$ each")
            lv["basis_options"] = [
                {"value": "item", "label": "Per item", "amount_label": _each},
                {"value": "month", "label": "Monthly goal",
                 "amount_label": "This month's total $"},
            ]
    # THE MULTI-MONTH CHAINS (owner 2026-08-12). Their own levers, seeded from the rep's real
    # activations, because the residual is a separate engine — see the section header above.
    mm_levers = build_multimonth_levers(client, org_id, plan)
    mm_cur = multimonth_actuals(client, org_id, _canon_period(period), plan, me["rep_name"]) if mm_levers else {}
    for lv in mm_levers:
        c = mm_cur.get(lv["key"])
        lv["current"] = c or {"units": 0, "amount": 0.0, "from_actuals": False}
        if c:
            seeded += 1
    return {**base, "ok": True, "ready": True, "reason": None,
            "plan": {"id": plan.get("id"), "name": plan.get("name"),
                     "carrier_id": plan.get("carrier_id")},
            "levers": levers, "tier": tier,
            "multimonth_levers": mm_levers,
            "seeded_from_actuals": seeded,
            "seed_note": (f"Starting numbers are {me['display_name']}'s actual {_canon_period(period)} "
                          f"figures from the pay engine ({seeded} of "
                          f"{len(levers) + len(mm_levers)} levers). Change any "
                          f"of them to model a different month."
                          if seeded else
                          "No paid activity found for this period, so every lever starts at zero — "
                          "type your own numbers to model a month.")}


def run(client, authorization, period, inputs, requested_rep="", requested_org=""):
    """POST payload: the projected pay for the caller's OWN levers, in the tenant they are acting as.
    Read-only, no persist."""
    me = require_self(client, authorization, requested_rep, period, requested_org)
    org_id = me["org_id"]
    mode = _carrier_mode(client, org_id)          # the ACTING tenant's mode — see context()
    head = {"period": _canon_period(period), "rep": me["display_name"], "rep_name": me["rep_name"],
            "store": me["store"], "market": me["market"], "carrier_mode": mode,
            "org_id": org_id, "no_persist": True,
            "can_pick_rep": bool(me.get("privileged")),
            "impersonated": bool(me.get("impersonated"))}
    if mode == "boost":
        return {**head, "ok": False, "unsupported": "boost",
                "reason": ("Your tenant is paid by the Boost component engine, not by Commission "
                           "Plans. Use What-If → Employee Payout for Boost scenarios."),
                "result": None, "levers": [], "tier": None}
    if not me["rep_name"]:
        return {**head, "ok": False, "needs_rep": True,
                "reason": ("Your login isn't linked to an employee record in this tenant — pick an "
                           "employee to model their pay plan."),
                "result": None, "levers": [], "tier": None}
    return {**head, **simulate(client, org_id, _canon_period(period), me["rep_name"],
                               me["store"], me["market"], inputs or {})}
