"""CRM contributions to the UNIVERSAL admin-attention feed (core/import_health.py).

Registered from crm/router.py's own bottom-of-file guarded import — no NEEDS CORE, no main.py change.
Read-only: nothing here writes a lead, a task or a config row.

Three things a manager needs surfaced without going looking for them:
  1. leads nobody owns — capture worked, routing did not;
  2. follow-ups that were missed — the reminder fired and still nothing happened;
  3. leads handed to an outside agency that the agency never answered.
Each degrades to "no finding" on any read failure: an attention provider that raises takes the whole
login popup down with it.
"""
try:
    from app.modules.core.import_health import register_provider
except Exception:                       # import_health absent in this deployment
    def register_provider(*_a, **_k):
        def _deco(fn):
            return fn
        return _deco


def _count(client, table, org_id, **eq):
    try:
        q = client.schema("core").table(table).select("id", count="exact").eq("org_id", org_id)
        for k, v in eq.items():
            q = q.eq(k, v)
        return (q.limit(1).execute()).count or 0
    except Exception:
        return None                     # None = "could not read", which is NOT the same as zero


@register_provider("crm_unassigned_leads", label="Leads with no owner", group="ops", cost="cheap")
def _p_unassigned(client, org_id, ctx):
    """An open lead with no owner, no queue and no agency is a lead nobody is working. It is the
    cheapest thing in the whole module to fix and the most expensive to leave."""
    try:
        rows = (client.schema("core").table("crm_lead")
                .select("id,owner_employee_id,queue_id,agency_id")
                .eq("org_id", org_id).eq("status", "open").limit(2000).execute().data) or []
    except Exception:
        return []
    orphans = [r for r in rows if not r.get("owner_employee_id") and not r.get("queue_id")
               and not r.get("agency_id")]
    if not orphans:
        return []
    return [{
        "group": "ops", "key": "crm_unassigned_leads", "severity": "warning",
        "label": "Leads with nobody working them",
        "detail": (f"{len(orphans)} open lead(s) have no owner, no queue and no agency. Either the "
                   f"routing rules do not cover how these leads arrive, or the store they came from "
                   f"has no active user to route to. Set a catch-all rule on CRM Settings → Routing."),
        "count": len(orphans), "deep_link": "/crm/leads?owner=none",
        "deep_link_label": "Open unassigned leads",
    }]


@register_provider("crm_missed_followups", label="Missed follow-ups", group="ops", cost="cheap")
def _p_missed(client, org_id, ctx):
    n = _count(client, "crm_task", org_id, status="missed")
    if not n:
        return []
    return [{
        "group": "ops", "key": "crm_missed_followups", "severity": "warning",
        "label": "Follow-ups nobody completed",
        "detail": (f"{n} follow-up(s) went past their due date without an outcome. These are leads "
                   f"that were captured, reminded, and then dropped — the most recoverable lost "
                   f"business there is."),
        "count": n, "deep_link": "/crm/my-followups?scope=team&status=missed",
        "deep_link_label": "Open missed follow-ups",
    }]


@register_provider("crm_agency_unanswered", label="Agency leads not accepted", group="ops",
                   cost="cheap")
def _p_agency(client, org_id, ctx):
    """A lead pushed to an outside agency that the agency has neither accepted nor declined is worse
    than an unassigned lead: it looks handled on every internal report."""
    try:
        rows = (client.schema("core").table("crm_lead")
                .select("id,agency_id,agency_accepted_at")
                .eq("org_id", org_id).eq("status", "open").limit(2000).execute().data) or []
    except Exception:
        return []
    pending = [r for r in rows if r.get("agency_id") and not r.get("agency_accepted_at")]
    if not pending:
        return []
    return [{
        "group": "ops", "key": "crm_agency_unanswered", "severity": "warning",
        "label": "Outside agencies have not answered",
        "detail": (f"{len(pending)} lead(s) were assigned to an outside agency that has neither "
                   f"accepted nor declined them. They read as 'handled' on every report while "
                   f"nobody is actually calling the customer."),
        "count": len(pending), "deep_link": "/crm/agencies",
        "deep_link_label": "Open agencies",
    }]


@register_provider("crm_no_pipeline", label="CRM not set up", group="config", cost="cheap")
def _p_no_pipeline(client, org_id, ctx):
    """Zero pipelines means the tenant's default-content seed never ran for this org — normally
    impossible, since migration 800 seeds every tenant and the router re-seeds lazily on every
    config read. Seeing this fire at all means something is blocking `core.seed_crm_defaults`."""
    n = _count(client, "crm_pipeline", org_id)
    if n is None or n > 0:
        return []
    return [{
        "group": "config", "key": "crm_no_pipeline", "severity": "warning",
        "label": "No sales pipeline configured",
        "detail": ("This tenant has zero CRM pipelines, so a lead has nowhere to go. The default "
                   "pipeline normally seeds itself on first use — if this persists, the seeding "
                   "function is failing for this org."),
        "count": 1, "deep_link": "/crm/settings", "deep_link_label": "Open CRM Settings",
    }]
