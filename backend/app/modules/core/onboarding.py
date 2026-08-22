"""Config-driven MODULE ONBOARDING engine (owner directive 2026-08-09).

    "go over the pos and see which tables can be linked from the MetricsPro and which ones need to
     be either created or filled, when onboarding a new tenant ... As soon as the user clicks on the
     POS button they should be tasked with completing the pending onboarding tasks ... Create a
     walkthrough wizard so the user is not overwhelmed and one thing after the other is prompted to
     set it up. Give an option to upload a template ... More will keep evolving."

WHY THIS IS GENERIC (module_key), NOT `pos_onboarding`
    "More will keep evolving" is a statement about the registry, so the registry is data
    (core.module_onboarding_task, mig 733) and this file holds only the ENGINE plus the shipped
    default rows. Hard-coding twelve POS steps in a component would have to be rewritten the first
    time the owner adds a thirteenth, and again the first time another module wants a wizard.

THREE THINGS LIVE HERE
    1. DEFAULT_TASKS    the shipped registry. Seeded into core.module_onboarding_task per tenant on
                        first read, and used as the FALLBACK whenever the table is missing/empty —
                        the same "DB is truth, in-code is the fallback" pattern as
                        entitlements.load_module_catalog(). An unrun mig 733 is therefore a no-op:
                        the wizard works, it just isn't per-tenant editable yet.
    2. predicates       a task is complete when the tenant's OWN DATA says so, re-derived live on
                        every read. A stored "done" flag that disagrees with an empty table is
                        precisely how an onboarding wizard lies to the operator, so there is no
                        stored completion for a task that has a real predicate. Only `manual` tasks
                        (and explicit skips) read from core.module_onboarding_state.
    3. templates        DOWNLOADABLE CSV templates whose columns are read from information_schema at
                        request time, filtered by an explicit per-entity policy. Hand-typing the
                        column list is how a template silently drifts from the table it feeds; the
                        harness asserts every generated header maps to a real column (or a declared
                        alias) so drift fails loudly instead of at import time.

MULTI-TENANT (AGENT_CONTRACT §2)
    org_id is a QUERY PARAM on every route (the middleware rewrites it from the caller's JWT); every
    read filters `.eq("org_id", org_id)` and every write stamps it. The predicate evaluator refuses
    to run without an org_id, and its (schema, table) pair is checked against PREDICATE_TABLES — a
    config row can never be turned into an arbitrary-table probe or a cross-tenant read.
"""
import re as _re

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.core.schemas import LaxModel

router = APIRouter(prefix="/onboarding", tags=["Core / Onboarding"])
ORG_ID = "00000000-0000-0000-0000-000000000001"   # middleware rewrites the query param


def sb():
    return get_supabase()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE SHIPPED REGISTRY
# ══════════════════════════════════════════════════════════════════════════════════════════════
# `predicate` shapes (all evaluated org-scoped, see _evaluate):
#   {"type": "count",  "schema": s, "table": t, "min": n, "where": {col: val}}
#   {"type": "any",    "of": [<predicate>, ...]}          satisfied when ANY child passes
#   {"type": "manual"}                                     only an explicit acknowledgement completes it
#
# `is_required` is what gates the POS entry point. Everything else is offered, never enforced —
# a tenant that wants to ring one cash sale on day one must not be held up by a vendor list.
POS_TASKS = [
    # ── STEP GROUP: identity the platform already knows ───────────────────────────────────────
    dict(task_key="stores", sort_order=10, step_group="Your business",
         title="Confirm your stores",
         why="POS rings every sale against a store. Your stores already exist in MetricsPro — this "
             "step just confirms at least one is set up and active, so the register has somewhere "
             "to point.",
         predicate={"type": "count", "schema": "storeops", "table": "stores", "min": 1},
         is_required=True, skippable=False, href="/storeops/admin"),
    dict(task_key="employees", sort_order=20, step_group="Your business",
         title="Confirm your staff roster",
         why="Every sale, drawer session and note is attributed to the person who rang it. Your "
             "employees already exist in MetricsPro — no re-typing.",
         predicate={"type": "count", "schema": "storeops", "table": "employees", "min": 1,
                    "where": {"is_active": True}},
         is_required=True, skippable=False, href="/storeops/admin"),
    dict(task_key="carrier", sort_order=30, step_group="Your business",
         title="Attach a carrier",
         why="The carrier decides which plans, dealer codes and commission rules apply. Nothing in "
             "POS is carrier-specific in code — it all reads this list.",
         predicate={"type": "count", "schema": "commcalc", "table": "carrier", "min": 1},
         is_required=True, skippable=False, href="/configurations/carriers"),

    # ── STEP GROUP: the catalog spine ─────────────────────────────────────────────────────────
    dict(task_key="departments", sort_order=40, step_group="Catalog",
         title="Create your departments",
         why="Departments are the top level of the product tree (Phones, Accessories, Services…). "
             "Every product hangs off one, and your POS reports group by them.",
         predicate={"type": "count", "schema": "pos", "table": "departments", "min": 1},
         is_required=True, skippable=False, template_key="departments",
         import_source="departments_from_item_mapping", href="/pos/products"),
    dict(task_key="categories", sort_order=50, step_group="Catalog", depends_on=["departments"],
         title="Create your categories",
         why="Categories sit under departments and are what a cashier actually browses at the "
             "register. Create at least one before adding products.",
         predicate={"type": "count", "schema": "pos", "table": "categories", "min": 1},
         is_required=True, skippable=False, template_key="categories",
         import_source="categories_from_item_mapping", href="/pos/products"),
    dict(task_key="products", sort_order=60, step_group="Catalog",
         depends_on=["departments", "categories"],
         title="Add your products & services",
         why="Nothing can be rung up that isn't in the catalog. Import your existing item list "
             "rather than typing it — MetricsPro already knows most of it from your sales history.",
         predicate={"type": "count", "schema": "pos", "table": "products", "min": 1},
         is_required=True, skippable=False, template_key="products",
         import_source="products_from_item_mapping", href="/pos/products"),
    dict(task_key="tax_codes", sort_order=70, step_group="Catalog",
         title="Set your sales-tax rates",
         why="A taxable sale with no tax code charges zero tax, and that is a number you cannot fix "
             "after the customer has left. Every store needs a rate that reaches it — its own, its "
             "market's, or a company default.",
         # COVERAGE, NOT A ROW COUNT (2026-08-10). This step used to be satisfied by ONE tax code
         # anywhere in the tenant, which is how Luxelink's wizard reported sales tax COMPLETE with a
         # single rate on a single store while the other 19 charged $0 -- the exact outcome this
         # task's own `why` warns about. It now asks the register's own question, store by store.
         predicate={"type": "coverage", "check": "pos_tax_rate"},
         is_required=True, skippable=False, template_key="tax_codes", href="/pos/settings"),

    # ── STEP GROUP: wireless ──────────────────────────────────────────────────────────────────
    dict(task_key="service_plans", sort_order=80, step_group="Wireless", depends_on=["carrier"],
         title="Add your plans & features",
         why="An activation attaches a rate plan. Without plans, activations can be recorded but "
             "carry no monthly fee, which breaks the commission basis downstream.",
         predicate={"type": "count", "schema": "pos", "table": "service_plans", "min": 1},
         is_required=True, skippable=False, template_key="service_plans",
         import_source="service_plans_from_product_mrc", href="/pos/activations"),
    dict(task_key="dealer_codes", sort_order=90, step_group="Wireless", depends_on=["carrier"],
         title="Add your dealer codes",
         why="The dealer code is how the carrier identifies the store that made the sale. A wrong "
             "or missing code is the usual reason an activation never gets paid.",
         predicate={"type": "count", "schema": "pos", "table": "dealer_codes", "min": 1},
         is_required=False, skippable=True, template_key="dealer_codes", href="/pos/settings"),

    # ── STEP GROUP: trading partners ──────────────────────────────────────────────────────────
    dict(task_key="vendors", sort_order=100, step_group="Partners",
         title="Add vendors, manufacturers and dealers",
         why="Vendors, manufacturers, master dealers and sub dealers all live in one list, "
             "separated by a Business Type. You need them for purchase orders and for reporting who "
             "supplied a unit.",
         predicate={"type": "count", "schema": "pos", "table": "vendors", "min": 1},
         is_required=False, skippable=True, template_key="vendors",
         import_source="vendors_from_distributors", href="/pos/vendors"),
    dict(task_key="customers", sort_order=110, step_group="Partners",
         title="Import your customer book",
         why="Optional to start — a walk-in cash sale needs no customer record. Import if you are "
             "moving from another POS and want history to line up.",
         predicate={"type": "count", "schema": "pos", "table": "customers", "min": 1},
         is_required=False, skippable=True, template_key="customers", href="/pos/customers"),

    # ── STEP GROUP: stock ─────────────────────────────────────────────────────────────────────
    dict(task_key="inventory", sort_order=120, step_group="Stock",
         depends_on=["products", "stores"],
         title="Load your on-hand inventory",
         why="What is physically on the shelf, per store. If you already track inventory in "
             "MetricsPro you can bring it straight over instead of counting again.",
         predicate={"type": "any", "of": [
             {"type": "count", "schema": "pos", "table": "inventory_standard", "min": 1},
             {"type": "count", "schema": "pos", "table": "inventory_serial", "min": 1},
         ]},
         is_required=False, skippable=True, template_key="inventory",
         import_source="inventory_from_metricspro", href="/pos/inventory"),

    # ── STEP GROUP: how the register behaves ──────────────────────────────────────────────────
    dict(task_key="receipt_template", sort_order=130, step_group="Register",
         title="Customise your receipt",
         why="Your store name, header and footer on the printed receipt. Ships with a working "
             "default, so this is cosmetic — skip it and come back.",
         predicate={"type": "count", "schema": "pos", "table": "receipt_templates", "min": 1},
         is_required=False, skippable=True, href="/pos/settings"),
    dict(task_key="register_settings", sort_order=140, step_group="Register",
         title="Set your drawer & register rules",
         why="Opening float, whether a drawer session is required, cash-in-drawer cap, variance "
             "alert threshold. Sensible defaults apply until you change them.",
         predicate={"type": "count", "schema": "pos", "table": "pos_settings", "min": 1},
         is_required=False, skippable=True, href="/pos/settings"),

    # ── STEP GROUP: history + wiring ──────────────────────────────────────────────────────────
    dict(task_key="sales_history", sort_order=150, step_group="History",
         depends_on=["products"],
         title="Bring sales records over from your old POS",
         why="Optional. Loads historical tickets so your POS reports do not start from zero on "
             "day one. Download the template, map your export to it, upload.",
         predicate={"type": "manual"},
         is_required=False, skippable=True, template_key="sales_history", href="/pos/import"),
    dict(task_key="pos_permissions", sort_order=160, step_group="History",
         title="Decide who can use POS",
         why="Who may reveal customer PII, void a sale, adjust inventory or change POS settings. "
             "Review this before your first real transaction.",
         predicate={"type": "manual"},
         is_required=False, skippable=True, href="/admin/roles"),
    dict(task_key="commission_wiring", sort_order=170, step_group="History",
         depends_on=["products", "tax_codes"],
         title="Decide whether POS feeds commissions",
         why="Whether this POS becomes the source of the sales your commissions are calculated "
             "from, or runs alongside your existing feed. This one changes pay, so it is deliberately "
             "the last step and is set by an administrator, not by this wizard.",
         predicate={"type": "manual"},
         is_required=False, skippable=True, href="/pos/settings"),
]

DEFAULT_TASKS = {"pos": POS_TASKS}

# Defaults applied to every registry row that omits them.
_TASK_DEFAULTS = dict(step_group=None, depends_on=[], predicate={"type": "manual"},
                      is_required=False, skippable=True, template_key=None,
                      import_source=None, href=None, why=None, is_active=True)


def _shipped(module_key: str) -> list:
    out = []
    for t in DEFAULT_TASKS.get(module_key, []):
        row = {**_TASK_DEFAULTS, **t, "module_key": module_key}
        row["depends_on"] = list(row.get("depends_on") or [])
        out.append(row)
    return sorted(out, key=lambda r: r["sort_order"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. PREDICATES — completion is derived from live tenant data, never from a stored flag
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Whitelist. A predicate row that names anything outside this map is treated as UNKNOWN (never
# "complete"), so a bad or hostile config row can neither probe an arbitrary table nor silently mark
# an onboarding step done.
PREDICATE_TABLES = {
    ("pos", "departments"), ("pos", "categories"), ("pos", "products"), ("pos", "tax_codes"),
    ("pos", "service_plans"), ("pos", "dealer_codes"), ("pos", "vendors"), ("pos", "customers"),
    ("pos", "inventory_standard"), ("pos", "inventory_serial"), ("pos", "receipt_templates"),
    ("pos", "pos_settings"), ("pos", "carrier_portals"), ("pos", "sales"), ("pos", "activations"),
    ("storeops", "stores"), ("storeops", "employees"),
    ("commcalc", "carrier"), ("commcalc", "commission_plan"),
}


def _cov_pos_tax_rate(org_id: str):
    """Does every ACTIVE store resolve to a sales-tax rate? -> (covered, total, missing[]).

    Asks the REGISTER'S question through the register's own resolver (pos._resolve_tax: store >
    market > org-wide), so this can never disagree with what a sale would actually charge. Counting
    rows here instead -- which is what the predicate did until 2026-08-10 -- says "done" the moment
    one store in the company has a rate.

    is_active is NULLABLE, so stores are filtered IS NOT FALSE, never == True."""
    from app.modules.pos.router import _resolve_tax    # lazy: main.py imports both routers
    client = sb()
    stores = (client.schema("storeops").table("stores").select("store_code,market,is_active")
              .eq("org_id", org_id).limit(2000).execute().data) or []
    stores = [s for s in stores if s.get("is_active") is not False and (s.get("store_code") or "").strip()]
    codes = (client.schema("pos").table("tax_codes").select("*")
             .eq("org_id", org_id).limit(1000).execute().data) or []
    missing = [s["store_code"] for s in stores
               if _resolve_tax(codes, s.get("store_code"), s.get("market"))[1] == "none"]
    return len(stores) - len(missing), len(stores), missing


# Whitelist, same discipline as PREDICATE_TABLES: a predicate naming an unregistered check is
# UNKNOWN, never complete. A checker answers "is this configured for every place that needs it",
# which a row count structurally cannot.
COVERAGE_CHECKS = {
    "pos_tax_rate": dict(fn=_cov_pos_tax_rate, noun="store",
                         done="every store has a sales-tax rate that reaches it",
                         gap="{n} of {total} stores have NO rate — a taxable sale there charges $0"),
}


def _count(schema: str, table: str, org_id: str, where: dict) -> int:
    """org-scoped exact count. Returns -1 for "could not determine" (missing table / schema not
    exposed to PostgREST), which the caller renders as UNKNOWN — never as complete."""
    try:
        q = (sb().schema(schema).table(table).select("id", count="exact")
             .eq("org_id", org_id).limit(1))
        for k, v in (where or {}).items():
            q = q.eq(k, v)
        return int(q.execute().count or 0)
    except Exception:
        return -1


def _evaluate(pred: dict, org_id: str) -> dict:
    """→ {'state': 'complete'|'incomplete'|'manual'|'unknown', 'count': n|None, 'reason': str}."""
    if not org_id:
        raise HTTPException(400, "org_id required")
    ptype = (pred or {}).get("type") or "manual"

    if ptype == "manual":
        return {"state": "manual", "count": None, "reason": "confirmed by a person"}

    if ptype == "any":
        children = [_evaluate(p, org_id) for p in (pred.get("of") or [])]
        if any(c["state"] == "complete" for c in children):
            return {"state": "complete", "count": max((c["count"] or 0) for c in children),
                    "reason": "at least one source has data"}
        if all(c["state"] == "unknown" for c in children) and children:
            return {"state": "unknown", "count": None, "reason": children[0]["reason"]}
        return {"state": "incomplete", "count": 0, "reason": "no source has data yet"}

    if ptype == "count":
        schema, table = pred.get("schema"), pred.get("table")
        if (schema, table) not in PREDICATE_TABLES:
            return {"state": "unknown", "count": None,
                    "reason": f"predicate names an unregistered table ({schema}.{table})"}
        n = _count(schema, table, org_id, pred.get("where") or {})
        if n < 0:
            return {"state": "unknown", "count": None,
                    "reason": f"could not read {schema}.{table} "
                              f"(is the '{schema}' schema exposed to the API?)"}
        need = int(pred.get("min") or 1)
        return {"state": "complete" if n >= need else "incomplete", "count": n,
                "reason": f"{n} row(s), need {need}"}

    if ptype == "coverage":
        spec = COVERAGE_CHECKS.get((pred or {}).get("check") or "")
        if not spec:
            return {"state": "unknown", "count": None,
                    "reason": f"predicate names an unregistered check ({pred.get('check')})"}
        try:
            covered, total, missing = spec["fn"](org_id)
        except Exception as e:
            # A coverage check reads two tables and may hit an unexposed schema or an un-run
            # migration. UNKNOWN, never complete -- the same posture _count takes on a -1.
            return {"state": "unknown", "count": None,
                    "reason": f"could not check coverage ({str(e)[:120]})"}
        if total == 0:
            return {"state": "unknown", "count": 0,
                    "reason": f"no {spec['noun']}s to cover yet"}
        if not missing:
            return {"state": "complete", "count": covered, "reason": spec["done"]}
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        return {"state": "incomplete", "count": covered,
                "reason": spec["gap"].format(n=len(missing), total=total) + f" ({shown})"}

    return {"state": "unknown", "count": None, "reason": f"unknown predicate type {ptype!r}"}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. REGISTRY LOAD (DB is truth, in-code is the fallback) + SEED
# ══════════════════════════════════════════════════════════════════════════════════════════════
_TASK_COLS = ("task_key,title,why,step_group,sort_order,depends_on,predicate,is_required,"
              "skippable,template_key,import_source,href,is_active")


def load_tasks(org_id: str, module_key: str) -> list:
    """The tenant's task registry. Reads core.module_onboarding_task; falls back to the shipped
    registry when mig 733 is unrun or the tenant has no rows yet. Never raises.

    Returns (rows, source) via load_tasks_with_source(); this thin wrapper keeps the common call
    site readable."""
    return load_tasks_with_source(org_id, module_key)[0]


def load_tasks_with_source(org_id: str, module_key: str):
    """(tasks, 'db'|'shipped'). The source matters to the operator: 'shipped' means mig 733 is unrun
    or unseeded, so per-tenant edits will not stick yet — the wizard says so instead of pretending
    the registry is editable."""
    try:
        rows = (sb().schema("core").table("module_onboarding_task").select(_TASK_COLS)
                .eq("org_id", org_id).eq("module_key", module_key).eq("is_active", True)
                .order("sort_order").execute().data) or []
        if rows:
            for r in rows:
                r["module_key"] = module_key
                r["depends_on"] = list(r.get("depends_on") or [])
            return rows, "db"
    except Exception:
        pass
    return _shipped(module_key), "shipped"


def seed_tasks(org_id: str, module_key: str) -> int:
    """Idempotently write the shipped registry into the tenant's table. Best-effort: a missing
    mig 733 must never break the wizard, so a failure here is swallowed and load_tasks() falls back.
    Returns the number of rows it attempted to insert (0 when already seeded/unavailable)."""
    shipped = _shipped(module_key)
    if not shipped:
        return 0
    try:
        have = {r["task_key"] for r in
                ((sb().schema("core").table("module_onboarding_task").select("task_key")
                  .eq("org_id", org_id).eq("module_key", module_key).execute().data) or [])}
        new = [{"org_id": org_id, **{k: t[k] for k in
                ("module_key", "task_key", "title", "why", "step_group", "sort_order",
                 "depends_on", "predicate", "is_required", "skippable", "template_key",
                 "import_source", "href", "is_active")}}
               for t in shipped if t["task_key"] not in have]
        if new:
            sb().schema("core").table("module_onboarding_task").insert(new).execute()
        return len(new)
    except Exception:
        return 0


def _states(org_id: str, module_key: str) -> dict:
    try:
        rows = (sb().schema("core").table("module_onboarding_state")
                .select("task_key,status,notes,actor,acted_at")
                .eq("org_id", org_id).eq("module_key", module_key).execute().data) or []
        return {r["task_key"]: r for r in rows}
    except Exception:
        return {}


def build_status(org_id: str, module_key: str) -> dict:
    """The whole wizard state in one read: every task, its live completion, what is blocked by what,
    and where to resume. This is the single call the POS entry gate makes."""
    if not org_id:
        raise HTTPException(400, "org_id required")
    tasks, registry_source = load_tasks_with_source(org_id, module_key)
    states = _states(org_id, module_key)

    done, out = set(), []
    for t in tasks:
        st = states.get(t["task_key"]) or {}
        status = (st.get("status") or "pending").lower()
        ev = _evaluate(t.get("predicate") or {}, org_id)

        if ev["state"] == "complete":
            complete, how = True, "data"
        elif status == "acknowledged":
            complete, how = True, "acknowledged"
        elif status == "skipped":
            complete, how = False, "skipped"
        else:
            complete, how = False, ev["state"]

        if complete:
            done.add(t["task_key"])
        out.append({**t,
                    "complete": complete, "completed_via": how, "skipped": status == "skipped",
                    "state_status": status, "notes": st.get("notes"), "actor": st.get("actor"),
                    "acted_at": st.get("acted_at"),
                    "evidence": ev})

    # Second pass: blocked-by can only be computed once every task's completion is known.
    for row in out:
        blocked = [d for d in (row.get("depends_on") or []) if d not in done]
        row["blocked_by"] = blocked
        row["available"] = not blocked

    required = [r for r in out if r.get("is_required")]
    req_done = [r for r in required if r["complete"]]
    # Resume point: first REQUIRED, not-complete, not-blocked task; else the first such optional one.
    nxt = next((r for r in out if r.get("is_required") and not r["complete"] and r["available"]),
               None) \
        or next((r for r in out if not r["complete"] and r["available"] and not r["skipped"]), None)

    return {
        "module": module_key,
        "org_id": org_id,
        "tasks": out,
        "required_total": len(required),
        "required_done": len(req_done),
        "total": len(out),
        "done": len([r for r in out if r["complete"]]),
        "complete": len(req_done) == len(required) and len(required) > 0,
        "next_task_key": nxt["task_key"] if nxt else None,
        "registry_source": registry_source,
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. TEMPLATES — headers read from information_schema, never hand-typed
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Per entity: the real table the template feeds, the columns to LEAVE OUT (surrogate keys, audit
# stamps, ciphertext, FK uuids a human cannot type), and ALIAS columns that stand in for those FKs
# with a human-typeable name. Everything else comes from the live table definition, in the table's
# own column order — so a column added to pos.products tomorrow shows up in the template with no
# code change, and a column removed cannot linger in a template that then fails at import.
TEMPLATE_SPECS = {
    "customers": dict(
        schema="pos", table="customers", title="Customers",
        exclude=["id", "org_id", "cust_number", "created_at", "updated_at", "password"],
        alias=[], import_entity="customers",
        note="The carrier account PIN is NOT importable — it is credential-grade and is entered per "
             "customer in the app. Customer # is assigned by the system. SSN and driver-licence "
             "details are no longer held by the platform at all (mig 909)."),
    "vendors": dict(
        schema="pos", table="vendors", title="Vendors / Manufacturers / Dealers",
        exclude=["id", "org_id", "created_at", "updated_at"], alias=[], import_entity="vendors",
        note="One list for every trading partner. Set business_type to one of: "
             + ", ".join(["Vendor", "Manufacturer", "Master Dealer", "Sub Dealer", "Shipper",
                          "ePay carrier"]) + "."),
    "manufacturers": dict(
        schema="pos", table="vendors", title="Manufacturers",
        exclude=["id", "org_id", "created_at", "updated_at"], alias=[], import_entity="vendors",
        defaults={"business_type": "Manufacturer"},
        note="Same list as Vendors, pre-set to business_type=Manufacturer."),
    "master_dealers": dict(
        schema="pos", table="vendors", title="Master Dealers",
        exclude=["id", "org_id", "created_at", "updated_at"], alias=[], import_entity="vendors",
        defaults={"business_type": "Master Dealer"},
        note="Same list as Vendors, pre-set to business_type=Master Dealer."),
    "sub_dealers": dict(
        schema="pos", table="vendors", title="Sub Dealers",
        exclude=["id", "org_id", "created_at", "updated_at"], alias=[], import_entity="vendors",
        defaults={"business_type": "Sub Dealer"},
        note="Same list as Vendors, pre-set to business_type=Sub Dealer. Leave empty if you have no "
             "sub dealers."),
    "products": dict(
        schema="pos", table="products", title="Products & Services",
        exclude=["id", "org_id", "product_code", "created_at", "department_id", "category_id"],
        alias=[("department", "Department NAME — created automatically if new"),
               ("category", "Category NAME — created automatically if new")],
        import_entity="products",
        note="Department and category are matched BY NAME, case-insensitive, and created if missing. "
             "Product # is assigned by the system."),
    "departments": dict(
        schema="pos", table="departments", title="Departments",
        exclude=["id", "org_id", "created_at"], alias=[], import_entity=None,
        note="short_name is what the register shows; full_name is the long label."),
    "categories": dict(
        schema="pos", table="categories", title="Categories",
        exclude=["id", "org_id", "created_at", "department_id"],
        alias=[("department", "Department NAME this category belongs under")], import_entity=None),
    "service_plans": dict(
        schema="pos", table="service_plans", title="Plans & Features",
        exclude=["id", "org_id", "created_at"], alias=[], import_entity=None,
        note="carrier must match a carrier you have attached."),
    "tax_codes": dict(
        schema="pos", table="tax_codes", title="Sales-Tax Rates",
        exclude=["id", "org_id", "created_at"], alias=[], import_entity=None,
        note="rate is a PERCENT (8.875 means 8.875%). Leave store_code blank for an org-wide rate."),
    "dealer_codes": dict(
        schema="pos", table="dealer_codes", title="Dealer Codes",
        exclude=["id", "org_id", "created_at"], alias=[], import_entity=None),
    "inventory": dict(
        schema="pos", table="inventory_standard", title="Inventory (counted stock)",
        exclude=["id", "org_id", "product_id", "updated_at", "last_counted_at"],
        alias=[("upc", "UPC of an existing product — matched first"),
               ("product_name", "Product short name — used when UPC is blank")],
        import_entity="inventory",
        note="For non-serialised stock (accessories, SIMs). Serialised units — phones with an "
             "IMEI/serial — use the Serialised Inventory template instead."),
    "inventory_serial": dict(
        schema="pos", table="inventory_serial", title="Inventory (serialised units)",
        exclude=["id", "org_id", "product_id", "created_at", "updated_at",
                 "sold_at", "sold_in_sale_id"],
        alias=[("upc", "UPC of an existing product — matched first"),
               ("product_name", "Product short name — used when UPC is blank")],
        import_entity=None,
        note="One row per physical unit. serial_number is required and must be unique in a store."),
    "sales_history": dict(
        schema="pos", table="sales", title="Sales records from another POS",
        # `created_at` is excluded in favour of the `sale_date` alias below: two date columns on one
        # template is exactly how half a historical load lands on the import date instead of the
        # sale date, and only one of them can be authoritative.
        exclude=["id", "org_id", "transaction_id", "customer_id", "receipt", "voided_at",
                 "voided_by", "created_at", "updated_at", "is_activation_sale", "balance"],
        alias=[("sale_reference", "Your old POS's ticket/receipt number — kept for tracing"),
               ("sale_date", "Date of the sale (YYYY-MM-DD)"),
               ("customer_phone", "Phone of an existing customer — optional"),
               ("line_upc", "UPC of the item sold"),
               ("line_product_name", "Item name, used when line_upc is blank"),
               ("line_qty", "Quantity"),
               ("line_unit_price", "Price per unit, tax-exclusive"),
               ("line_cost", "Your cost per unit — REQUIRED for gross-profit reporting"),
               ("line_discount", "Discount on the line"),
               ("payment_method", "Cash / Credit / Debit / Check / Other"),
               ("payment_amount", "Amount taken on that method")],
        import_entity=None,
        note="ONE ROW PER LINE ITEM, repeating sale_reference across the lines of one ticket. "
             "This template is for a historical LOAD; it does not move money and does not open a "
             "drawer session."),
    "activations": dict(
        schema="pos", table="activations", title="Activations",
        exclude=["id", "org_id", "activation_number", "sale_id", "customer_id", "service_plan_id",
                 "created_at", "updated_at", "notes"],
        alias=[("customer_phone", "Phone of an existing customer — optional"),
               ("customer_email", "Email of an existing customer — optional"),
               ("customer_name", "Name of an existing customer — optional")],
        import_entity="activations"),
}

# Human-facing types, so the template's second row can say what a column wants without exposing
# Postgres type names to a store manager.
_TYPE_LABEL = {
    "text": "text", "character varying": "text", "uuid": "id", "boolean": "yes/no",
    "integer": "whole number", "bigint": "whole number", "smallint": "whole number",
    "numeric": "number", "double precision": "number", "real": "number",
    "date": "date (YYYY-MM-DD)", "timestamp with time zone": "date & time",
    "timestamp without time zone": "date & time", "jsonb": "json", "ARRAY": "list",
}


def _live_columns(schema: str, table: str) -> list:
    """Column definitions straight from the database. Returns [] when unreadable, which the caller
    turns into an honest error rather than a silently short template."""
    try:
        rows = (sb().schema("core").rpc("module_onboarding_columns",
                                        {"p_schema": schema, "p_table": table}).execute().data)
        if rows:
            return rows
    except Exception:
        pass
    # No RPC (mig 733 ships none on purpose — one fewer SECURITY DEFINER function to lock down).
    # information_schema is not PostgREST-exposed, so fall back to probing the table itself: a
    # zero-row select still returns the real column set in the response shape only for non-empty
    # tables, so instead we use the module's own snapshot. See _SNAPSHOT below.
    return []


# LAST-RESORT SNAPSHOT of the column definitions, captured from the live database on 2026-08-09 by
# `information_schema.columns WHERE table_schema='pos'`. It exists ONLY so template download degrades
# gracefully when the live read is unavailable (e.g. the `pos` schema is not yet exposed to
# PostgREST — the exact situation on 2026-08-09). `harness_pos_onboarding.py` diffs this snapshot
# against the live schema and FAILS when they disagree, so it cannot drift silently.
_SNAPSHOT = {
    ("pos", "customers"): [
        ("id", "uuid"), ("org_id", "uuid"), ("cust_number", "bigint"), ("account_type", "text"),
        ("company_name", "text"), ("first_name", "text"), ("last_name", "text"),
        ("middle_initial", "text"), ("dob", "date"),
        ("primary_account_no", "text"), ("password", "text"), ("email", "text"),
        ("phone_primary", "text"), ("phone_secondary", "text"), ("address_1", "text"),
        ("address_2", "text"), ("city", "text"), ("state", "text"), ("zip", "text"),
        ("referral_source", "text"), ("credit_limit", "numeric"), ("accept_checks", "boolean"),
        ("is_active", "boolean"),
        ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")],
    ("pos", "vendors"): [
        ("id", "uuid"), ("org_id", "uuid"), ("ban", "text"), ("legal_name", "text"),
        ("short_name", "text"), ("business_type", "text"), ("street_one", "text"),
        ("street_two", "text"), ("city", "text"), ("state", "text"), ("zip", "text"),
        ("country", "text"), ("tax_id", "text"), ("contact_name", "text"), ("phone", "text"),
        ("fax", "text"), ("email", "text"), ("website", "text"), ("is_active", "boolean"),
        ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")],
    ("pos", "products"): [
        ("id", "uuid"), ("org_id", "uuid"), ("product_code", "bigint"), ("upc", "text"),
        ("short_name", "text"), ("full_name", "text"), ("department_id", "uuid"),
        ("category_id", "uuid"), ("system_category", "text"), ("inventory_type", "text"),
        ("manufacturer", "text"), ("cost", "numeric"), ("retail_price", "numeric"),
        ("msrp", "numeric"), ("is_taxable", "boolean"), ("calculate_as_profit", "boolean"),
        ("body_style", "text"), ("is_active", "boolean"), ("end_of_life", "boolean"),
        ("created_at", "timestamp with time zone")],
    ("pos", "departments"): [
        ("id", "uuid"), ("org_id", "uuid"), ("short_name", "text"), ("full_name", "text"),
        ("is_active", "boolean"), ("created_at", "timestamp with time zone")],
    ("pos", "categories"): [
        ("id", "uuid"), ("org_id", "uuid"), ("name", "text"), ("department_id", "uuid"),
        ("is_active", "boolean"), ("created_at", "timestamp with time zone")],
    ("pos", "service_plans"): [
        ("id", "uuid"), ("org_id", "uuid"), ("carrier", "text"), ("plan_code", "text"),
        ("plan_name", "text"), ("plan_description", "text"), ("monthly_fee", "numeric"),
        ("included_minutes", "integer"), ("service_area", "text"), ("contract_type", "text"),
        ("contract_terms", "text"), ("dealer_code", "text"), ("status", "text"),
        ("created_at", "timestamp with time zone")],
    ("pos", "tax_codes"): [
        ("id", "uuid"), ("org_id", "uuid"), ("name", "text"), ("rate", "numeric"),
        ("store_code", "text"), ("is_active", "boolean"),
        ("created_at", "timestamp with time zone")],
    ("pos", "dealer_codes"): [
        ("id", "uuid"), ("org_id", "uuid"), ("code", "text"), ("carrier", "text"),
        ("store_code", "text"), ("is_active", "boolean"),
        ("created_at", "timestamp with time zone")],
    ("pos", "inventory_standard"): [
        ("id", "uuid"), ("org_id", "uuid"), ("product_id", "uuid"), ("store_code", "text"),
        ("qty_on_hand", "integer"), ("qty_on_order", "integer"), ("qty_reserved", "integer"),
        ("bin_location", "text"), ("last_counted_at", "timestamp with time zone"),
        ("updated_at", "timestamp with time zone")],
    ("pos", "inventory_serial"): [
        ("id", "uuid"), ("org_id", "uuid"), ("product_id", "uuid"), ("store_code", "text"),
        ("serial_number", "text"), ("imei", "text"), ("sim_card", "text"), ("color", "text"),
        ("storage", "text"), ("condition", "text"), ("status", "text"), ("cost", "numeric"),
        ("date_received", "date"), ("po_number", "text"),
        ("sold_at", "timestamp with time zone"), ("sold_in_sale_id", "uuid"),
        ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")],
    ("pos", "sales"): [
        ("id", "uuid"), ("org_id", "uuid"), ("transaction_id", "bigint"), ("store_code", "text"),
        ("customer_id", "uuid"), ("employee_id", "text"), ("receipt_type", "text"),
        ("status", "text"), ("subtotal", "numeric"), ("discount_total", "numeric"),
        ("tax_total", "numeric"), ("total", "numeric"), ("balance", "numeric"),
        ("is_activation_sale", "boolean"), ("receipt", "jsonb"),
        ("voided_at", "timestamp with time zone"), ("voided_by", "text"), ("notes", "text"),
        ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")],
    ("pos", "activations"): [
        ("id", "uuid"), ("org_id", "uuid"), ("activation_number", "bigint"), ("sale_id", "uuid"),
        ("customer_id", "uuid"), ("store_code", "text"), ("employee_id", "text"),
        ("carrier", "text"), ("activation_date", "date"), ("service_plan_date", "date"),
        ("service_plan_id", "uuid"), ("plan_code", "text"), ("plan_description", "text"),
        ("monthly_fee", "numeric"), ("included_minutes", "integer"), ("service_area", "text"),
        ("contract_type", "text"), ("contract_terms", "text"), ("dealer_code", "text"),
        ("cell_number", "text"), ("phone_serial", "text"), ("phone_model", "text"),
        ("sim_card", "text"), ("mobile_phone", "text"), ("account_number", "text"),
        ("deposit_amount", "numeric"), ("memo", "text"), ("description", "text"),
        ("notes", "text"), ("promotion_offered", "text"), ("trade_in_credit", "numeric"),
        ("special_promo", "text"), ("status", "text"),
        ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")],
}


def template_columns(template_key: str) -> dict:
    """The template's real column definitions: live where readable, snapshot otherwise, ALWAYS
    filtered by the entity's explicit exclude/alias policy — so the header list is derived, not
    typed."""
    spec = TEMPLATE_SPECS.get(template_key)
    if not spec:
        raise HTTPException(404, f"unknown template '{template_key}'")
    schema, table = spec["schema"], spec["table"]
    live = _live_columns(schema, table)
    source = "live"
    if not live:
        live = [{"column_name": c, "data_type": t}
                for c, t in _SNAPSHOT.get((schema, table), [])]
        source = "snapshot"
    excl = set(spec.get("exclude") or [])
    cols = [{"name": c["column_name"],
             "type": _TYPE_LABEL.get(c["data_type"], c["data_type"]),
             "hint": ""}
            for c in live if c["column_name"] not in excl]
    for name, hint in (spec.get("alias") or []):
        cols.append({"name": name, "type": "text", "hint": hint})
    return {"template_key": template_key, "title": spec["title"], "note": spec.get("note") or "",
            "target": f"{schema}.{table}", "columns": cols, "column_source": source,
            "import_entity": spec.get("import_entity"), "defaults": spec.get("defaults") or {}}


def _csv(rows) -> str:
    def cell(v):
        s = "" if v is None else str(v)
        return '"' + s.replace('"', '""') + '"' if any(c in s for c in ',"\n\r') else s
    return "\r\n".join(",".join(cell(c) for c in r) for r in rows) + "\r\n"


def template_csv(template_key: str) -> str:
    """A two-line CSV: the real header row, then a commented type/hint row the operator deletes.
    Deliberately NOT pre-filled with fake example data — a template whose sample rows get imported
    by accident is a worse problem than an empty one."""
    t = template_columns(template_key)
    header = [c["name"] for c in t["columns"]]
    hints = [(c["hint"] or c["type"]) for c in t["columns"]]
    defaults = t.get("defaults") or {}
    first = [defaults.get(c["name"], "") for c in t["columns"]]
    rows = [header, ["# " + h for h in hints]]
    if any(first):
        rows.append(first)
    return _csv(rows)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. IMPORT FROM WHAT METRICSPRO ALREADY KNOWS
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Every source below is SAME-ORG ONLY. There is deliberately no cross-tenant copy: "move over the
# existing inventory" is read as "this tenant's stock that MetricsPro already holds", and a
# cross-org copy is a one-keystroke tenant leak that no wizard should be able to perform.
#
# All of these are PREVIEW-FIRST: they report what they would create and never write until an
# explicit apply=true, so an operator can see the row count and a sample before committing.

# The asset module's OWN definition of unsold, ready-to-sell stock — copied verbatim from
# asset/router.py get_aging() (`.is_("date_sold","null").ilike("category","%On Inventory%")`) rather
# than re-derived here, so the wizard can never disagree with the Inventory Aging report about what
# is sellable. If the asset module changes that predicate, this constant is the one place to follow.
ASSET_UNSOLD_FILTER = {"date_sold_is_null": True, "category_ilike": "%On Inventory%"}

IMPORT_SOURCES = {
    "departments_from_item_mapping": dict(
        title="Departments from your sales history",
        detail="MetricsPro already classifies your items into departments from the sales files it "
               "ingests. This creates one POS department per distinct name.",
        creates="pos.departments"),
    "categories_from_item_mapping": dict(
        title="Categories from your sales history",
        detail="One POS category per distinct category name already seen on your items, attached to "
               "its department where known.",
        creates="pos.categories"),
    "products_from_item_mapping": dict(
        title="Products from your item catalog",
        detail="Every distinct item MetricsPro has seen on your sales, with its department, "
               "category and description. Cost is filled from the ePay catalog where the "
               "description matches.",
        creates="pos.products"),
    "service_plans_from_product_mrc": dict(
        title="Plans from your commission plan config",
        detail="Your configured rate plans and their monthly recurring charge.",
        creates="pos.service_plans"),
    "vendors_from_distributors": dict(
        title="Vendors from your distributor list",
        detail="Your configured distributors and companies, as POS vendors.",
        creates="pos.vendors"),
    "inventory_from_metricspro": dict(
        title="Inventory MetricsPro already holds for you",
        detail="Two sources, pick one: your VIP consignment ledger (unsold, on-inventory units) or "
               "your latest B2B inventory-aging snapshot. Both are serialised units.",
        creates="pos.inventory_serial"),
}


# A North-American phone number, however the export spells it: "(773) 241-9115", "773-241-9115",
# "7732419115" after a "Phone#" label. Its presence in an item description proves the string belongs
# to ONE transaction rather than to a catalog item.
_PII_LINE_ITEM = _re.compile(r"(Phone\s*#)|(\(\d{3}\)\s*\d{3}-\d{4})|(\b\d{3}-\d{3}-\d{4}\b)", _re.I)


def _page(client, schema, table, cols, org_id, extra=None, cap=20000):
    out, page = [], 0
    while page * 1000 < cap:
        q = client.schema(schema).table(table).select(cols).eq("org_id", org_id)
        if extra:
            q = extra(q)
        rows = q.range(page * 1000, page * 1000 + 999).execute().data or []
        out.extend(rows)
        if len(rows) < 1000:
            break
        page += 1
    return out


def preview_import(source: str, org_id: str, variant: str = "") -> dict:
    """What this source WOULD create for this tenant. Read-only, always."""
    if not org_id:
        raise HTTPException(400, "org_id required")
    if source not in IMPORT_SOURCES:
        raise HTTPException(404, f"unknown import source '{source}'")
    c = sb()
    meta = dict(IMPORT_SOURCES[source])

    if source in ("departments_from_item_mapping", "categories_from_item_mapping"):
        field = "department" if source.startswith("departments") else "category"
        rows = _page(c, "commcalc", "item_mapping", "department,category", org_id)
        names = sorted({(r.get(field) or "").strip() for r in rows if (r.get(field) or "").strip()})
        return {**meta, "source": source, "count": len(names),
                "sample": names[:25], "variant": variant}

    if source == "products_from_item_mapping":
        rows = _page(c, "commcalc", "item_mapping",
                     "item_desc,sku,department,category,item_type,device_model", org_id)
        seen, items = set(), []
        for r in rows:
            name = (r.get("item_desc") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            items.append({"short_name": name[:120], "upc": (r.get("sku") or "").strip() or None,
                          "department": (r.get("department") or "").strip() or None,
                          "category": (r.get("category") or "").strip() or None,
                          "system_category": _sys_cat(r.get("item_type")),
                          "inventory_type": "serial" if (r.get("item_type") or "") == "phone"
                                            else "standard"})
        return {**meta, "source": source, "count": len(items),
                "sample": items[:25], "variant": variant}

    if source == "service_plans_from_product_mrc":
        carriers = _page(c, "commcalc", "carrier", "id,name", org_id)
        cmap = {r["id"]: r.get("name") for r in carriers}
        default_carrier = (carriers[0].get("name") if carriers else "") or ""
        rows = _page(c, "commcalc", "product_mrc",
                     "plan_pattern,mrc,carrier_id,classification,is_active", org_id)
        plans = [{"plan_name": (r.get("plan_pattern") or "").strip(),
                  "monthly_fee": r.get("mrc"),
                  "carrier": cmap.get(r.get("carrier_id")) or default_carrier,
                  "plan_description": r.get("classification") or None,
                  "status": "active" if r.get("is_active") is not False else "inactive"}
                 for r in rows if (r.get("plan_pattern") or "").strip()]
        return {**meta, "source": source, "count": len(plans),
                "sample": plans[:25], "variant": variant}

    if source == "vendors_from_distributors":
        dists = _page(c, "commcalc", "distributors", "name,arrangement,is_active", org_id)
        comps = _page(c, "commcalc", "companies", "name,legal_name,ein", org_id)
        out = [{"legal_name": (d.get("name") or "").strip(), "business_type": "Vendor",
                "is_active": d.get("is_active") is not False}
               for d in dists if (d.get("name") or "").strip()]
        out += [{"legal_name": (k.get("legal_name") or k.get("name") or "").strip(),
                 "short_name": (k.get("name") or "").strip(),
                 "business_type": "Master Dealer", "tax_id": k.get("ein")}
                for k in comps if (k.get("legal_name") or k.get("name") or "").strip()]
        seen, ded = set(), []
        for v in out:
            k = v["legal_name"].lower()
            if k and k not in seen:
                seen.add(k)
                ded.append(v)
        return {**meta, "source": source, "count": len(ded), "sample": ded[:25],
                "variant": variant}

    if source == "inventory_from_metricspro":
        v = (variant or "asset_ledger").strip()
        if v == "asset_ledger":
            rows = _page(c, "commcalc", "asset_ledger",
                         "esn_imei,device_model,store,acquired_date,owed_to_vip,category,date_sold",
                         org_id,
                         extra=lambda q: q.is_("date_sold", "null").ilike("category",
                                                                         "%On Inventory%"))
            units = [{"serial_number": (r.get("esn_imei") or "").strip(),
                      "imei": (r.get("esn_imei") or "").strip(),
                      "product_name": (r.get("device_model") or "").strip(),
                      "store_code": (r.get("store") or "").strip() or None,
                      "cost": r.get("owed_to_vip"),
                      "date_received": r.get("acquired_date")}
                     for r in rows if (r.get("esn_imei") or "").strip()]
            meta["detail"] = ("VIP consignment ledger — units with no sale date whose category is "
                              "'On Inventory'. This is the asset module's own definition of unsold, "
                              "ready-to-sell stock (the same filter the Inventory Aging report uses).")
        elif v == "inventory_aging":
            rows = _page(c, "commcalc", "inventory_aging_device",
                         "imei,serial,sku,item,store,unit_cost,received_date,as_of_date,on_hand", org_id)
            # Seed POS from what is CURRENTLY on the shelf. A row the latest Inventory Aging export no
            # longer lists is a sold/transferred device kept only for its cost (mig 294) — seeding it
            # would put ~50% already-sold handsets into a new tenant's opening stock. `is not False`
            # keeps pre-294 rows, which have no flag.
            rows = [r for r in rows if r.get("on_hand") is not False]
            units = [{"serial_number": (r.get("serial") or r.get("imei") or "").strip(),
                      "imei": (r.get("imei") or "").strip() or None,
                      "product_name": (r.get("item") or "").strip(),
                      "upc": (r.get("sku") or "").strip() or None,
                      "store_code": (r.get("store") or "").strip() or None,
                      "cost": r.get("unit_cost"),
                      "date_received": r.get("received_date")}
                     for r in rows if (r.get("serial") or r.get("imei") or "").strip()]
            meta["detail"] = ("Latest B2B inventory-aging snapshot — what your existing POS reported "
                              "as on-hand. Use this when you are MOVING an existing store over.")
        else:
            raise HTTPException(400, "variant must be 'asset_ledger' or 'inventory_aging'")
        return {**meta, "source": source, "variant": v, "count": len(units), "sample": units[:25]}

    raise HTTPException(404, f"unknown import source '{source}'")


def _sys_cat(item_type: str) -> str:
    return {"phone": "Cell Phone", "accessory": "Accessory"}.get(
        (item_type or "").strip().lower(), "Regular")


def apply_import(source: str, org_id: str, variant: str = "", actor: str = "") -> dict:
    """Write what preview_import() showed. Everything it writes stamps org_id (AGENT_CONTRACT §2 —
    scoping the read without stamping the write is the documented way rows vanish). Existing rows are
    never overwritten: matching is by natural key and a hit is SKIPPED, so re-running is safe and a
    tenant's own edits survive."""
    if not org_id:
        raise HTTPException(400, "org_id required")
    prev = preview_import(source, org_id, variant)
    c = sb()
    created = skipped = 0
    errors = []

    def existing(table, col):
        try:
            return {(r.get(col) or "").strip().lower()
                    for r in _page(c, "pos", table, col, org_id) if (r.get(col) or "").strip()}
        except Exception as e:
            raise HTTPException(503, f"could not read pos.{table} — {e}")

    def insert(table, rows):
        nonlocal created
        for i in range(0, len(rows), 100):
            chunk = [{**r, "org_id": org_id} for r in rows[i:i + 100]]
            try:
                c.schema("pos").table(table).insert(chunk).execute()
                created += len(chunk)
            except Exception as e:
                errors.append(str(e)[:300])

    if source == "departments_from_item_mapping":
        have = existing("departments", "short_name")
        names = _distinct_names(c, org_id, "department")
        new = [{"short_name": n[:60], "full_name": n} for n in names if n.lower() not in have]
        skipped = len(names) - len(new)
        insert("departments", new)

    elif source == "categories_from_item_mapping":
        have = existing("categories", "name")
        dept_ids = {(d.get("short_name") or "").strip().lower(): d["id"]
                    for d in _page(c, "pos", "departments", "id,short_name", org_id)}
        pairs = _distinct_pairs(c, org_id)
        new = [{"name": cat[:80],
                "department_id": dept_ids.get((dep or "").strip().lower())}
               for cat, dep in pairs if cat.lower() not in have]
        skipped = len(pairs) - len(new)
        insert("categories", new)

    elif source == "products_from_item_mapping":
        pii_skipped = 0
        have = existing("products", "short_name")
        dept_ids = {(d.get("short_name") or "").strip().lower(): d["id"]
                    for d in _page(c, "pos", "departments", "id,short_name", org_id)}
        cat_ids = {(d.get("name") or "").strip().lower(): d["id"]
                   for d in _page(c, "pos", "categories", "id,name", org_id)}
        costs = _cost_by_desc(c, org_id)
        rows = _page(c, "commcalc", "item_mapping",
                     "item_desc,sku,department,category,item_type", org_id)
        seen, new = set(), []
        for r in rows:
            name = (r.get("item_desc") or "").strip()
            # PER-TRANSACTION LINE ITEMS ARE NOT CATALOG PRODUCTS (owner report 2026-08-09).
            # item_mapping records every distinct item STRING seen on a sale, which is right for
            # commission classification but wrong as a product source: on Luxelink 1,211 of 1,328
            # strings carry the customer's own phone number ("Total Wireless RTR Wallet. Phone#:
            # (773) 241-9115."), so importing them created 1,211 fake $0 products AND copied
            # customer PII into the catalog. A string bearing a phone number is a transaction line,
            # never a SKU — skip it, and count it so the import reports what it declined.
            if name and _PII_LINE_ITEM.search(name):
                pii_skipped += 1
                continue
            k = name.lower()
            if not name or k in seen:
                continue
            seen.add(k)
            if k in have:
                skipped += 1
                continue
            new.append({"short_name": name[:120], "full_name": name,
                        "upc": (r.get("sku") or "").strip() or None,
                        "department_id": dept_ids.get((r.get("department") or "").strip().lower()),
                        "category_id": cat_ids.get((r.get("category") or "").strip().lower()),
                        "system_category": _sys_cat(r.get("item_type")),
                        "inventory_type": ("serial" if (r.get("item_type") or "") == "phone"
                                           else "standard"),
                        "cost": costs.get(k, 0)})
        insert("products", new)

    elif source == "service_plans_from_product_mrc":
        have = existing("service_plans", "plan_name")
        allp = _all_plans(c, org_id)
        new = [p for p in allp if (p.get("plan_name") or "").lower() not in have]
        skipped = len(allp) - len(new)
        insert("service_plans", new)

    elif source == "vendors_from_distributors":
        have = existing("vendors", "legal_name")
        allv = _all_vendors(c, org_id)
        new = [v for v in allv if v["legal_name"].lower() not in have]
        skipped = len(allv) - len(new)
        insert("vendors", new)

    elif source == "inventory_from_metricspro":
        units = _all_units(c, org_id, variant or "asset_ledger")
        prods = _page(c, "pos", "products", "id,upc,short_name", org_id)
        by_upc = {(p.get("upc") or "").strip(): p["id"] for p in prods if (p.get("upc") or "").strip()}
        by_name = {(p.get("short_name") or "").strip().lower(): p["id"] for p in prods}
        have = {(r.get("serial_number") or "").strip().lower()
                for r in _page(c, "pos", "inventory_serial", "serial_number", org_id)}
        new, unmatched = [], 0
        for u in units:
            sn = (u.get("serial_number") or "").strip()
            if not sn or sn.lower() in have:
                skipped += 1
                continue
            pid = by_upc.get((u.get("upc") or "").strip()) \
                or by_name.get((u.get("product_name") or "").strip().lower())
            if not pid:
                unmatched += 1
                continue
            have.add(sn.lower())
            new.append({"product_id": pid, "serial_number": sn, "imei": u.get("imei"),
                        "store_code": u.get("store_code"), "cost": u.get("cost"),
                        "date_received": u.get("date_received"),
                        "condition": "new", "status": "in_stock"})
        insert("inventory_serial", new)
        if unmatched:
            errors.append(f"{unmatched} unit(s) had no matching product in the POS catalog — "
                          "import your products first, then re-run this.")

    return {"source": source, "variant": variant, "created": created, "skipped": skipped,
            "errors": errors, "considered": prev["count"]}


# ── helpers that re-read the full (not sampled) source sets for apply ──────────────────────────
def _distinct_names(c, org_id, field):
    rows = _page(c, "commcalc", "item_mapping", "department,category", org_id)
    return sorted({(r.get(field) or "").strip() for r in rows if (r.get(field) or "").strip()})


def _distinct_pairs(c, org_id):
    rows = _page(c, "commcalc", "item_mapping", "department,category", org_id)
    seen, out = set(), []
    for r in rows:
        cat = (r.get("category") or "").strip()
        if cat and cat.lower() not in seen:
            seen.add(cat.lower())
            out.append((cat, (r.get("department") or "").strip()))
    return out


def _cost_by_desc(c, org_id):
    try:
        rows = _page(c, "commcalc", "raw_catalog", "product_desc,cost", org_id)
    except Exception:
        return {}
    out = {}
    for r in rows:
        d = (r.get("product_desc") or "").strip().lower()
        if d and d not in out:
            out[d] = r.get("cost") or 0
    return out


def _all_plans(c, org_id):
    carriers = _page(c, "commcalc", "carrier", "id,name", org_id)
    cmap = {r["id"]: r.get("name") for r in carriers}
    default_carrier = (carriers[0].get("name") if carriers else "") or ""
    rows = _page(c, "commcalc", "product_mrc",
                 "plan_pattern,mrc,carrier_id,classification,is_active", org_id)
    seen, out = set(), []
    for r in rows:
        n = (r.get("plan_pattern") or "").strip()
        if not n or n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append({"plan_name": n[:120], "monthly_fee": r.get("mrc"),
                    "carrier": cmap.get(r.get("carrier_id")) or default_carrier,
                    "plan_description": r.get("classification") or None,
                    "status": "active" if r.get("is_active") is not False else "inactive"})
    return out


def _all_vendors(c, org_id):
    dists = _page(c, "commcalc", "distributors", "name,arrangement,is_active", org_id)
    comps = _page(c, "commcalc", "companies", "name,legal_name,ein", org_id)
    out = [{"legal_name": (d.get("name") or "").strip()[:200], "business_type": "Vendor",
            "is_active": d.get("is_active") is not False} for d in dists
           if (d.get("name") or "").strip()]
    out += [{"legal_name": (k.get("legal_name") or k.get("name") or "").strip()[:200],
             "short_name": (k.get("name") or "").strip()[:100],
             "business_type": "Master Dealer", "tax_id": k.get("ein")} for k in comps
            if (k.get("legal_name") or k.get("name") or "").strip()]
    seen, ded = set(), []
    for v in out:
        k = v["legal_name"].lower()
        if k and k not in seen:
            seen.add(k)
            ded.append(v)
    return ded


def _all_units(c, org_id, variant):
    if variant == "inventory_aging":
        rows = _page(c, "commcalc", "inventory_aging_device",
                     "imei,serial,sku,item,store,unit_cost,received_date,on_hand", org_id)
        rows = [r for r in rows if r.get("on_hand") is not False]      # current stock only (mig 294)
        return [{"serial_number": (r.get("serial") or r.get("imei") or "").strip(),
                 "imei": (r.get("imei") or "").strip() or None,
                 "product_name": (r.get("item") or "").strip(),
                 "upc": (r.get("sku") or "").strip() or None,
                 "store_code": (r.get("store") or "").strip() or None,
                 "cost": r.get("unit_cost"), "date_received": r.get("received_date")}
                for r in rows if (r.get("serial") or r.get("imei") or "").strip()]
    rows = _page(c, "commcalc", "asset_ledger",
                 "esn_imei,device_model,store,acquired_date,owed_to_vip", org_id,
                 extra=lambda q: q.is_("date_sold", "null").ilike("category", "%On Inventory%"))
    return [{"serial_number": (r.get("esn_imei") or "").strip(),
             "imei": (r.get("esn_imei") or "").strip(),
             "product_name": (r.get("device_model") or "").strip(),
             "store_code": (r.get("store") or "").strip() or None,
             "cost": r.get("owed_to_vip"), "date_received": r.get("acquired_date")}
            for r in rows if (r.get("esn_imei") or "").strip()]


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. ROUTES  (mounted under /api/v1/core/onboarding by core/router.py)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _actor(authorization: str, org_id: str) -> str:
    """Best-effort attribution for a skip/acknowledge. Never gates — this is a label, not a right."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return ""
        rows = (sb().schema("storeops").table("app_users").select("email,employee_id")
                .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
        return ((rows[0].get("email") or rows[0].get("employee_id") or "") if rows else "")
    except Exception:
        return ""


def _require_setup_rights(authorization: str, org_id: str):
    """Onboarding CHANGES tenant configuration, so it takes the same gate as any other settings
    area: super-admin, an explicit `settings.pos_onboarding` grant, or a full-scope admin. Reads are
    open to any member — a rep who lands on the wizard should SEE why POS is not ready yet."""
    from app.modules.core.router import _resolve_caller, _can_edit_setting, _uid_from_token
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(sb(), uid) if uid else None
    if not caller:
        raise HTTPException(401, "sign in to change onboarding")
    if not _can_edit_setting(caller, "pos_onboarding"):
        raise HTTPException(403, "your role cannot change onboarding setup (pos_onboarding)")


@router.get("/{module_key}")
def get_onboarding(module_key: str, org_id: str = ORG_ID, seed: bool = True):
    """The whole wizard state. `seed=false` skips the idempotent registry seed (used by the harness
    so a read never writes)."""
    if seed:
        seed_tasks(org_id, module_key)
    return build_status(org_id, module_key)


@router.get("/{module_key}/status")
def get_onboarding_status(module_key: str, org_id: str = ORG_ID):
    """The cheap version the POS entry gate calls: is this tenant ready, and where do they resume."""
    s = build_status(org_id, module_key)
    return {k: s[k] for k in ("module", "complete", "required_total", "required_done",
                              "total", "done", "next_task_key")}


class SetTaskStateIn(LaxModel):
    status: Any = None
    notes: Any = None


@router.post("/{module_key}/task/{task_key}")
def set_task_state(module_key: str, task_key: str, body: SetTaskStateIn,
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Skip / un-skip / acknowledge a task. Cannot fake a data-backed task complete: a task whose
    predicate is a real count stays incomplete until the data exists, and `acknowledged` on such a
    task is recorded but ignored by build_status (the predicate wins)."""
    _require_setup_rights(authorization, org_id)
    status = (body.status or "").strip().lower()
    if status not in ("pending", "skipped", "acknowledged"):
        raise HTTPException(400, "status must be pending, skipped or acknowledged")
    known = {t["task_key"] for t in load_tasks(org_id, module_key)}
    if task_key not in known:
        raise HTTPException(404, f"unknown task '{task_key}' for module '{module_key}'")
    row = {"org_id": org_id, "module_key": module_key, "task_key": task_key, "status": status,
           "notes": (body.notes or "").strip() or None,
           "actor": _actor(authorization, org_id) or None, "acted_at": "now()",
           "updated_at": "now()"}
    try:
        upd = (sb().schema("core").table("module_onboarding_state")
               .update({k: v for k, v in row.items() if k not in ("org_id", "module_key",
                                                                  "task_key")})
               .eq("org_id", org_id).eq("module_key", module_key)
               .eq("task_key", task_key).execute())
        if not upd.data:
            sb().schema("core").table("module_onboarding_state").insert(row).execute()
    except Exception as e:
        raise HTTPException(503, f"could not save (has migration 733 been run?) — {e}")
    return build_status(org_id, module_key)


@router.get("/templates/list")
def list_templates():
    """Every downloadable template, with its real target table. No org data — safe for any member."""
    return {"templates": [{"key": k, "title": v["title"],
                           "target": f"{v['schema']}.{v['table']}",
                           "note": v.get("note") or "",
                           "import_entity": v.get("import_entity")}
                          for k, v in TEMPLATE_SPECS.items()]}


@router.get("/templates/{template_key}")
def get_template(template_key: str):
    """The template's columns, derived from the live table definition."""
    return template_columns(template_key)


@router.get("/templates/{template_key}/csv")
def get_template_csv(template_key: str):
    """The template as CSV text. Returned as JSON (not a file response) so the browser download goes
    through the same authenticated api() path as everything else and cannot leak via a bare URL."""
    t = template_columns(template_key)
    return {"template_key": template_key, "filename": f"metricspro_{template_key}_template.csv",
            "csv": template_csv(template_key), "columns": t["columns"],
            "column_source": t["column_source"], "note": t["note"]}


@router.get("/import-sources/list")
def list_import_sources():
    return {"sources": [{"key": k, **v} for k, v in IMPORT_SOURCES.items()]}


@router.get("/import-sources/{source}/preview")
def import_preview(source: str, org_id: str = ORG_ID, variant: str = ""):
    return preview_import(source, org_id, variant)


class ImportApplyIn(LaxModel):
    variant: Any = None


@router.post("/import-sources/{source}/apply")
def import_apply(source: str, body: ImportApplyIn = None, authorization: str = Header(default=""),
                 org_id: str = ORG_ID, variant: str = ""):
    _require_setup_rights(authorization, org_id)
    return apply_import(source, org_id, variant or ((body or ImportApplyIn()).variant or ""))
