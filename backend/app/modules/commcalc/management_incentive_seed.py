"""Platform-default Management Incentive plan seeding (migration 852).

Seeds ONE house-org default — the owner's Total Wireless district-manager plan ($2,090 at full
attainment) — so every tenant reads a working default they can clone and edit. Same rationale and
never-clobber semantics as training_seed / support_seed: the shipped amounts/wording are CONTENT, so
they live in code (correctable in a normal deploy) rather than in the migration.

NEVER-CLOBBER: the plan is (re)written only when it is missing OR its existing row's updated_by is NULL
or 'seed' (i.e. it was itself seeded, never hand-edited). A plan an admin edited in the builder is left
alone. Children (components / bonuses / qualifiers / assignment) are delete-and-reinsert for a seeded
plan only, so re-seeding an improved default refreshes them without touching a tenant's own plans.

DEGRADES GRACEFULLY: an un-run migration 852 (tables absent) or any DB error is a silent no-op
({ok: False}); it can never break sync_tenant / a login. HOUSE ORG ONLY — never writes a tenant row.
"""
from datetime import datetime, timezone

from app.core.database import get_supabase

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
DEFAULT_PLAN_NAME = "Total Wireless — District Manager"


def default_plan_spec() -> dict:
    """The owner-approved Total Wireless DM default (2026-08-15). Full attainment = $2,090:
    Accessory $1,120 + VHI/FIOS $120 + Edge $300 + Consolidated $300 + Inventory $250."""
    return {
        "name": DEFAULT_PLAN_NAME,
        "level": "district_manager",
        "period_type": "monthly",
        "consolidated_bonus_amount": 300,
        "is_default": True,
        "is_active": True,
        "notes": "Platform default for Total Wireless district managers. Clone and edit per tenant/level.",
        "components": [
            {"label": "Accessory Sales", "kind": "percent", "rate": 0.02, "metric_source": "accessory_gp",
             "target_per_store": 8000, "store_count": 7, "cap_at_target": True, "sort": 1},
            {"label": "VHI / FIOS Sales", "kind": "per_unit", "rate": 2, "metric_source": "vhi_fios_count",
             "target_per_store": 10, "store_count": 6, "cap_at_target": True, "sort": 2},
            {"label": "Edge Activations", "kind": "per_unit", "rate": 5, "metric_source": "edge_count",
             "target_per_store": 10, "store_count": 6, "cap_at_target": True, "sort": 3},
        ],
        "bonuses": [
            {"label": "Consolidated Bonus", "kind": "consolidated", "amount": 0,
             "gated_by": "qualifiers", "config": {}, "sort": 1},
            {"label": "Inventory Control Bonus", "kind": "inventory_selloff", "amount": 250,
             "gated_by": "inventory_aging", "config": {"max_days": 10}, "sort": 2},
        ],
        "qualifiers": [
            {"metric_key": "zulu", "label": "Zulu", "source": "kpi", "op": "lt", "threshold": 5, "unit": "percent", "sort": 1},
            {"metric_key": "tmr3", "label": "3MR", "source": "kpi", "op": "gt", "threshold": 75, "unit": "percent", "sort": 2},
            {"metric_key": "cash_deposit", "label": "Cash Deposit", "source": "cash_deposit", "op": "lte",
             "threshold": 0, "unit": "usd", "config": {"day": "sat", "max_amount": 0}, "sort": 3},
            {"metric_key": "twp", "label": "TWP", "source": "kpi", "op": "gt", "threshold": 80, "unit": "percent", "sort": 4},
            {"metric_key": "address_checks", "label": "Address Checks", "source": "kpi", "op": "gt",
             "threshold": 50, "unit": "percent", "sort": 5},
        ],
        # Default assignment: every district manager, unless a tenant's own plan / a per-employee
        # assignment (higher rank) overrides it — same basis as the employee commission plan.
        "assignments": [
            {"scope": "role", "scope_value": "district_manager", "priority": 0},
        ],
    }


def seed_management_incentive_defaults(client=None, org_id: str = HOUSE_ORG, spec: dict = None) -> dict:
    """(Re)seed the house-org default plan, never clobbering a hand-edited one. Returns
    {ok, action} — action in ('inserted','refreshed','skipped'). `spec` overrides the bundled default
    (tests). Try/except-guarded end to end: an un-run mig 852 or any DB error → {ok: False}."""
    client = client or get_supabase()
    spec = spec or default_plan_spec()
    now = datetime.now(timezone.utc).isoformat()
    try:
        existing = (client.schema("commcalc").table("management_incentive_plan")
                    .select("id,updated_by").eq("org_id", org_id).eq("name", spec["name"])
                    .limit(1).execute().data) or []
    except Exception:
        return {"ok": False, "action": "unavailable"}   # mig 852 not run

    if existing:
        ub = existing[0].get("updated_by")
        if ub is not None and str(ub) != "seed":
            return {"ok": True, "action": "skipped"}    # hand-edited → leave it alone

    header = {
        "org_id": org_id, "name": spec["name"], "level": spec.get("level"),
        "period_type": spec.get("period_type", "monthly"),
        "consolidated_bonus_amount": spec.get("consolidated_bonus_amount", 300),
        "is_default": bool(spec.get("is_default", True)), "is_active": bool(spec.get("is_active", True)),
        "notes": spec.get("notes"), "updated_by": "seed", "updated_at": now,
    }
    try:
        client.schema("commcalc").table("management_incentive_plan").upsert(
            header, on_conflict="org_id,name").execute()
        got = (client.schema("commcalc").table("management_incentive_plan").select("id")
               .eq("org_id", org_id).eq("name", spec["name"]).limit(1).execute().data) or []
        if not got:
            return {"ok": False, "action": "unavailable"}
        plan_id = got[0]["id"]

        for tbl, rows in (
            ("management_incentive_component", spec.get("components", [])),
            ("management_incentive_bonus", spec.get("bonuses", [])),
            ("management_incentive_qualifier", spec.get("qualifiers", [])),
            ("management_incentive_assignment", spec.get("assignments", [])),
        ):
            client.schema("commcalc").table(tbl).delete().eq("org_id", org_id).eq("plan_id", plan_id).execute()
            if rows:
                client.schema("commcalc").table(tbl).insert(
                    [{**r, "org_id": org_id, "plan_id": plan_id} for r in rows]).execute()
    except Exception:
        return {"ok": False, "action": "error"}

    return {"ok": True, "action": ("refreshed" if existing else "inserted"), "plan_id": plan_id}
