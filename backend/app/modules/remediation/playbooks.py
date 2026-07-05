"""Whitelisted remediation actions — the ONLY things the auto-remediation agent can do.

The AI agent may choose a playbook KEY from this registry and supply PARAMS; it can never run
arbitrary SQL or code. Each playbook exposes:
  • preview(client, org_id, params) -> {"summary": str, "count": int, "detail": ...}   (NO mutation)
  • execute(client, org_id, params) -> {"summary": str, ...result...}                   (bounded mutation)
Adding a capability = adding a function here, reviewed by a human. Every action is org-scoped and
degrades safely (a bad/empty target yields a "nothing to do" preview, never an exception the caller
can't render). Keep actions SMALL and REVERSIBLE.
"""
from app.core.database import get_supabase


def _sb():
    return get_supabase()


# ── dedupe_timeoff ─────────────────────────────────────────────────────────────────────────────────
# The exact class of the 2026-07-05 bug: a time-off was voided (a 'denied' row exists) but a duplicate
# 'approved'/'pending' copy for the same employee+dates survived and keeps blocking scheduling. Deny the
# survivor ONLY where a 'denied' sibling proves the void intent. Optional employee_id/date narrow it.
def _timeoff_targets(client, org_id, params):
    emp = str(params.get("employee_id") or "").strip()
    day = str(params.get("date") or "").strip()[:10]
    rows = (client.table("time_off_requests")
            .select("id,employee_id,employee_name,start_date,end_date,status")
            .eq("org_id", org_id).limit(20000).execute().data) or []
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[(str(r.get("employee_id")), str(r.get("start_date")), str(r.get("end_date")))].append(r)
    targets = []
    for (eid, sdt, edt), g in groups.items():
        statuses = {str(x.get("status") or "").lower() for x in g}
        if "denied" not in statuses:
            continue  # no void intent → never touch a standalone approval
        if emp and eid != emp:
            continue
        if day and not (str(sdt)[:10] <= day <= str(edt)[:10]):
            continue
        for x in g:
            if str(x.get("status") or "").lower() in ("approved", "pending"):
                targets.append(x)
    return targets


def preview_dedupe_timeoff(client, org_id, params):
    t = _timeoff_targets(client, org_id, params)
    if not t:
        return {"summary": "No duplicate voided time-off rows are currently blocking scheduling.", "count": 0}
    who = ", ".join(sorted({str(x.get("employee_name") or x.get("employee_id")) for x in t}))
    ids = [x["id"] for x in t]
    return {"summary": f"Will deny {len(ids)} leftover approved/pending time-off row(s) for {who} "
                       f"(a voided sibling exists) so the rep(s) can be scheduled.",
            "count": len(ids), "detail": {"ids": ids}}


def execute_dedupe_timeoff(client, org_id, params):
    t = _timeoff_targets(client, org_id, params)
    ids = [x["id"] for x in t]
    for i in ids:
        client.table("time_off_requests").update({"status": "denied"}).eq("id", i).execute()
    return {"summary": f"Denied {len(ids)} leftover time-off row(s).", "denied_ids": ids}


# ── add_store_alias ────────────────────────────────────────────────────────────────────────────────
# Map a sales-file store spelling to a canonical store_code so Daily Targets / P&L attach its actuals.
def _validate_store_code(client, org_id, code):
    rows = (client.schema("commcalc").table("store_mapping")
            .select("store_code,store_address").eq("org_id", org_id).eq("store_code", code)
            .limit(1).execute().data) or []
    return rows[0] if rows else None


def preview_add_store_alias(client, org_id, params):
    alias = (params.get("alias") or "").strip()
    code = (params.get("store_code") or "").strip()
    if not alias or not code:
        return {"summary": "Missing alias or store_code — cannot map.", "count": 0}
    store = _validate_store_code(client, org_id, code)
    if not store:
        return {"summary": f"Store code '{code}' is not in store_mapping — pick a real store first.", "count": 0}
    return {"summary": f"Will map store spelling '{alias}' → {code} ({store.get('store_address')}). "
                       f"Its sales then attach to the right store in Daily Targets / P&L.",
            "count": 1, "detail": {"alias": alias, "store_code": code}}


def execute_add_store_alias(client, org_id, params):
    alias = (params.get("alias") or "").strip()
    code = (params.get("store_code") or "").strip()
    if not alias or not code or not _validate_store_code(client, org_id, code):
        raise ValueError("alias and a valid store_code are required")
    # replace any existing alias with the same text (case-insensitive) to keep it unique (mirrors
    # commcalc add_store_alias)
    existing = (client.schema("commcalc").table("store_aliases").select("id,alias")
                .eq("org_id", org_id).execute().data) or []
    for r in existing:
        if (r.get("alias") or "").strip().lower() == alias.lower():
            client.schema("commcalc").table("store_aliases").delete().eq("id", r["id"]).execute()
    row = {"org_id": org_id, "alias": alias, "store_code": code,
           "note": "added via auto-remediation agent"}
    client.schema("commcalc").table("store_aliases").insert(row).execute()
    return {"summary": f"Mapped '{alias}' → {code}.", "alias": alias, "store_code": code}


# ── registry ─────────────────────────────────────────────────────────────────────────────────────
PLAYBOOKS = {
    "dedupe_timeoff": {"preview": preview_dedupe_timeoff, "execute": execute_dedupe_timeoff},
    "add_store_alias": {"preview": preview_add_store_alias, "execute": execute_add_store_alias},
}


def is_implemented(key: str) -> bool:
    return key in PLAYBOOKS


def run_preview(key, client, org_id, params):
    pb = PLAYBOOKS.get(key)
    if not pb:
        return {"summary": f"Playbook '{key}' is on the roadmap but not executable yet.", "count": 0}
    try:
        return pb["preview"](client, org_id, params or {})
    except Exception as e:  # a preview must never hard-fail the propose flow
        return {"summary": f"Could not build a preview: {e}", "count": 0, "error": str(e)}


def run_execute(key, client, org_id, params):
    pb = PLAYBOOKS.get(key)
    if not pb:
        raise ValueError(f"Playbook '{key}' is not executable.")
    return pb["execute"](client, org_id, params or {})
