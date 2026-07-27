"""HELPDESK attention provider — ticket alerts that go nowhere.

Registered into the shared attention registry from this module's own file (imported for its side effect at
the bottom of helpdesk/router.py, which main.py already mounts) — no SHARED file changes.

WHY (owner ask 2026-07-26, dispatch item 5): `_notify_new_ticket` routes a new ticket to the category's
`notify_emails`, else to `ticket_settings.notify_emails`, and returns silently when BOTH are empty. A tenant
using the helpdesk with no alert email configured therefore collects tickets nobody is told about — a
required-but-unconfigured setting whose only symptom is "nobody answered my ticket".

CONTRACT: LIVE state only; org-scoped; cheap and SHORT-CIRCUITED (1 read when the tenant has no tickets,
2 when alerts are configured, 3 only when there is a real gap); exception-guarded. Clears as soon as an
alert email is saved on /helpdesk/settings.
"""
from app.modules.core.import_health import register_provider, _item


@register_provider("helpdesk_alerts", label="Helpdesk ticket alerts", group="config", cost="cheap")
def _p_helpdesk_alerts(client, org_id, ctx):
    def t(name):
        return client.schema("storeops").table(name)

    try:                                    # 1) does this tenant use the helpdesk at all?
        any_ticket = (t("tickets").select("id").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return []
    if not any_ticket:
        return []
    try:                                    # 2) a company-wide alert list covers every category
        rows = (t("ticket_settings").select("notify_emails").eq("org_id", org_id)
                .limit(1).execute().data) or []
    except Exception:
        return []
    if rows and (rows[0].get("notify_emails") or []):
        return []
    try:                                    # 3) …otherwise every ACTIVE category needs its own list
        cats = (t("ticket_categories").select("name,is_active,notify_emails")
                .eq("org_id", org_id).limit(200).execute().data) or []
    except Exception:
        cats = []
    gaps = [c for c in cats if c.get("is_active") is not False and not (c.get("notify_emails") or [])]
    if cats and not gaps:
        return []                           # fully routed per category — nothing to fix
    detail = ("New helpdesk tickets are not emailed to anyone, so a ticket can sit unseen until someone "
              "opens the queue. Add the alert email(s) on Helpdesk Settings — either one company-wide "
              "list, or a list per category to route (IT → IT lead, HR → HR).")
    if gaps:
        detail += f" Currently unrouted categories: {', '.join(sorted((c.get('name') or '?') for c in gaps)[:4])}."
    return [_item("config", "helpdesk_no_alert_email", "warning", "Helpdesk tickets alert nobody",
                  detail, max(1, len(gaps)), "/helpdesk/settings?tab=settings", "Set alert emails")]
