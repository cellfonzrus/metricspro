"""Face-descriptor retention schedule + deletion job (mod-people, owner decision 2026-08-09).

Closes security-plan Phase 9.2 — the last open BIPA item after migration 420 (face recognition OFF
for every tenant, kept in the DB so re-enabling needs no re-enrollment). This module is the "actually
implement the deletion job" the security plan calls for. See docs/BIOMETRIC_RETENTION_POLICY.md for
the written policy a lawyer can read; this file is the code that enforces it.

── THE RULE ("whichever is first" — BIPA, 740 ILCS 14/15(a)) ─────────────────────────────────────────
For each enrolled storeops.face_descriptors row, compute the EARLIEST of:

  1. PURPOSE SATISFIED — the employee's termination_date (storeops.employees, migration 417 = "last
     day of employment") + the tenant's face_retention_days (config, default 90, hard-ceilinged at the
     statutory bound). NULL termination_date (still employed, or never formally terminated) means this
     trigger never fires for that row.
  2. STATUTORY BACKSTOP — the employee's LAST INTERACTION with their own biometric template (the later
     of: enrollment, re-enrollment, or the most recent clock-in that was actually face-matched) + 1095
     days (3 years). ALWAYS computed, not merely "for anyone who never gets a termination date" — that
     phrasing describes the primary use case, but the correct statutory reading is MIN(1, 2), and (2)
     is what still governs an employee who is terminated with `face_retention_days` configured close to
     the 1095-day ceiling.

Whichever of the two dates is EARLIER decides both the destroy date AND the trigger recorded in the
audit log. A row with no termination_date AND no interaction date at all (shouldn't happen —
registered_at is always stamped) is left alone rather than guessed at.

Two OTHER, non-scheduled triggers exist alongside the date math:
  3. EMPLOYEE_REQUEST — `destroy_one_employee_request()`, an immediate single-row destruction an
     admin/HR runs when an employee exercises their BIPA right to demand deletion. Never waits for a
     sweep.
  4. TENANT_DISABLED_PURGE — when a tenant's face recognition master switch (migration 420) is OFF AND
     the tenant has opted into `face_recognition_purge_on_disable` (migration 422), EVERY descriptor
     for that tenant is due, unconditionally, the moment BOTH are true — see `compute_due`'s
     short-circuit. The DEFAULT stays "keep them" (today's behaviour, re-enabling is instant); this is
     an explicit per-tenant opt-in for the stronger posture.

── MULTI-TENANT (AGENT_CONTRACT RULE ONE) ─────────────────────────────────────────────────────────────
Every function here takes `org_id` and scopes EVERY read and EVERY write (select/delete/insert) with
`.eq("org_id", org_id)`. The pg_cron entrypoint (router.py `face_retention_run_due`) loops tenants and
calls these functions once per org — there is no cross-tenant query anywhere in this file.

── WHAT NEVER HAPPENS HERE ────────────────────────────────────────────────────────────────────────────
No function in this file ever selects the `descriptor` column (the 128-float vector) — only
`id,employee_id,registered_at,updated_at` are read off storeops.face_descriptors. The audit log
(`storeops.face_retention_log`, migration 422) is built to make "we destroyed it" evidenceable years
later without ever holding the biometric data itself.

── DEGRADE (AGENT_CONTRACT §5) ─────────────────────────────────────────────────────────────────────
Every DB read is wrapped in try/except and returns `available=False` / an empty result until migration
422 has run — the job is simply inert (destroys nothing) rather than raising. `compute_due` and
`destroy` never delete anything when the tenant config is unavailable.
"""
from datetime import datetime, timezone, timedelta, date as _date

# ── constants (RULE TWO: config-driven, not hard-coded — these are the DEFAULT/CEILING, never the
#    only allowed value except where the law itself fixes the number) ─────────────────────────────
FACE_RETENTION_DAYS_DEFAULT = 90
FACE_RETENTION_DAYS_MIN = 1
# BIPA's own outer bound (740 ILCS 14/15(a)): 3 years since the last interaction. This is NOT a tenant
# setting — it is the ceiling `clamp_retention_days` enforces on the configurable figure, and it is
# ALWAYS evaluated as trigger 2 regardless of what a tenant configures trigger 1's window to.
STATUTORY_BACKSTOP_DAYS = 1095

TRIGGER_PURPOSE = "purpose_satisfied"
TRIGGER_BACKSTOP = "statutory_backstop"
TRIGGER_EMPLOYEE_REQUEST = "employee_request"
TRIGGER_TENANT_PURGE = "tenant_disabled_purge"
TRIGGERS = (TRIGGER_PURPOSE, TRIGGER_BACKSTOP, TRIGGER_EMPLOYEE_REQUEST, TRIGGER_TENANT_PURGE)

DEFAULT_TENANT_RETENTION_CONFIG = {
    "retention_days": FACE_RETENTION_DAYS_DEFAULT,
    "purge_on_disable": False,
    "face_recognition_enabled": False,
}

_TENANT_COLS = "face_retention_days,face_recognition_purge_on_disable,face_recognition_enabled"
_EMPLOYEE_COLS = "employee_id,name,termination_date"
_DESCRIPTOR_COLS = "id,employee_id,registered_at,updated_at"   # NEVER "descriptor" — see module docstring


def clamp_retention_days(v):
    """Coerce + clamp a requested face_retention_days to [1, STATUTORY_BACKSTOP_DAYS]. A tenant
    literally cannot configure itself past the statutory bound — this is the enforcement point."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = FACE_RETENTION_DAYS_DEFAULT
    return max(FACE_RETENTION_DAYS_MIN, min(STATUTORY_BACKSTOP_DAYS, n))


def _parse_dt(s):
    """Parse an ISO timestamp, always returning a TZ-AWARE datetime (naive -> assumed UTC). Same idiom
    as lunch_deduction._parse_dt — real PostgREST timestamptz values always arrive tz-aware; this
    normalization guards hand-built fixtures / future callers, never production data."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _parse_date(s):
    if not s:
        return None
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def get_tenant_retention_config(org_id, sb_client):
    """(config, available). `available=False` means migration 422 hasn't run on this tenant — config
    is the owner-stated default in that case, for display only; callers must never destroy anything
    when `available` is False (see module DEGRADE).

    Availability is decided by column PRESENCE on the fetched row (mirrors face_recognition.py /
    lunch_deduction.py — a schemaless test double never raises for an unknown key, so an exception
    check alone would make every old fixture look migrated)."""
    try:
        rows = (sb_client.table("tenants").select(_TENANT_COLS)
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return dict(DEFAULT_TENANT_RETENTION_CONFIG), False
    if not rows or "face_retention_days" not in rows[0]:
        return dict(DEFAULT_TENANT_RETENTION_CONFIG), False
    t = rows[0]
    return {
        "retention_days": clamp_retention_days(t.get("face_retention_days")),
        "purge_on_disable": bool(t.get("face_recognition_purge_on_disable")),
        "face_recognition_enabled": bool(t.get("face_recognition_enabled")),
    }, True


def get_employees_for_retention(org_id, sb_client):
    """{employee_id: {name, termination_date}} for the whole roster, org-scoped."""
    try:
        rows = (sb_client.table("employees").select(_EMPLOYEE_COLS)
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}
    out = {}
    for e in rows:
        eid = e.get("employee_id")
        if eid:
            out[str(eid)] = e
    return out


def get_descriptors_for_org(org_id, sb_client):
    """Every enrolled descriptor's METADATA for this tenant — never the vector itself (see
    _DESCRIPTOR_COLS)."""
    try:
        rows = (sb_client.table("face_descriptors").select(_DESCRIPTOR_COLS)
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return []
    return rows


def get_last_face_verified_map(org_id, employee_ids, sb_client):
    """{employee_id: last clock_in ISO string} across storeops.timelog rows that actually carried a
    face_match_pct (i.e. a real biometric interaction, not just a selfie-only punch) — the LATEST such
    punch per employee, org-scoped. Empty dict (never raises) pre-migration/on any read failure; a
    missing signal here just means the statutory-backstop clock runs off enrollment/re-enrollment
    alone, which is still a correct (if slightly more conservative — earlier destroy date) answer."""
    ids = sorted({str(e) for e in (employee_ids or []) if e})
    if not ids:
        return {}
    try:
        rows = (sb_client.table("timelog").select("employee_id,clock_in")
                .eq("org_id", org_id).in_("employee_id", ids)
                .not_.is_("face_match_pct", "null").execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        eid = str(r.get("employee_id") or "")
        ci = r.get("clock_in")
        if not eid or not ci:
            continue
        if eid not in out or str(ci) > str(out[eid]):
            out[eid] = ci
    return out


def _last_interaction_at(descriptor_row, last_punch_iso):
    """The latest of: registered_at, updated_at, and the last face-matched punch for this employee
    (may be None). Returns a tz-aware datetime or None."""
    candidates = [_parse_dt(descriptor_row.get("registered_at")),
                  _parse_dt(descriptor_row.get("updated_at")),
                  _parse_dt(last_punch_iso)]
    candidates = [c for c in candidates if c is not None]
    return max(candidates) if candidates else None


def compute_due(org_id, sb_client, today=None):
    """The core "what is due for destruction RIGHT NOW" computation — read-only, deletes nothing.
    Used for both a dry-run preview and as the input to `destroy()`.

    Returns {available, purge_all, purge_reason, items:[...]}. Each item:
      {employee_id, employee_name, trigger, due_date (iso date), retention_days_applied,
       termination_date, last_interaction_at, descriptor_id, descriptor_registered_at,
       descriptor_updated_at}
    """
    today = today or datetime.now(timezone.utc).date()
    tenant_cfg, available = get_tenant_retention_config(org_id, sb_client)
    if not available:
        return {"available": False, "purge_all": False, "purge_reason": None, "items": []}
    descriptors = get_descriptors_for_org(org_id, sb_client)
    if not descriptors:
        return {"available": True, "purge_all": False, "purge_reason": None, "items": []}

    employees_map = get_employees_for_retention(org_id, sb_client)

    # Trigger 3 (tenant_disabled_purge): the master switch is OFF and this tenant has opted into
    # purging on disable — EVERY descriptor is due, unconditionally, regardless of termination/backstop
    # dates. Short-circuits the rest of the per-row date math entirely.
    if tenant_cfg.get("face_recognition_enabled") is False and tenant_cfg.get("purge_on_disable"):
        items = []
        for d in descriptors:
            eid = str(d.get("employee_id") or "")
            emp = employees_map.get(eid) or {}
            items.append({
                "employee_id": eid, "employee_name": emp.get("name"),
                "trigger": TRIGGER_TENANT_PURGE, "due_date": today.isoformat(),
                "retention_days_applied": None,
                "termination_date": emp.get("termination_date"),
                "last_interaction_at": None,
                "descriptor_id": d.get("id"),
                "descriptor_registered_at": d.get("registered_at"),
                "descriptor_updated_at": d.get("updated_at"),
            })
        return {"available": True, "purge_all": True, "purge_reason": TRIGGER_TENANT_PURGE, "items": items}

    retention_days = clamp_retention_days(tenant_cfg.get("retention_days"))
    last_punch_map = get_last_face_verified_map(
        org_id, [d.get("employee_id") for d in descriptors], sb_client)

    items = []
    for d in descriptors:
        eid = str(d.get("employee_id") or "")
        emp = employees_map.get(eid) or {}
        term_date = _parse_date(emp.get("termination_date"))
        last_interact = _last_interaction_at(d, last_punch_map.get(eid))

        candidates = []  # (trigger, due_date: date, retention_days_applied)
        if term_date:
            candidates.append((TRIGGER_PURPOSE, term_date + timedelta(days=retention_days), retention_days))
        if last_interact:
            candidates.append((TRIGGER_BACKSTOP,
                               last_interact.date() + timedelta(days=STATUTORY_BACKSTOP_DAYS),
                               STATUTORY_BACKSTOP_DAYS))
        if not candidates:
            continue  # no termination date and no interaction date at all — nothing to compute; leave in place

        trigger, due_date, applied_days = min(candidates, key=lambda c: c[1])  # "whichever is first"
        if today < due_date:
            continue
        items.append({
            "employee_id": eid, "employee_name": emp.get("name"),
            "trigger": trigger, "due_date": due_date.isoformat(),
            "retention_days_applied": applied_days,
            "termination_date": term_date.isoformat() if term_date else None,
            "last_interaction_at": last_interact.isoformat() if last_interact else None,
            "descriptor_id": d.get("id"),
            "descriptor_registered_at": d.get("registered_at"),
            "descriptor_updated_at": d.get("updated_at"),
        })
    return {"available": True, "purge_all": False, "purge_reason": None, "items": items}


def _write_audit_log(org_id, sb_client, item, destroyed_by, notes=None):
    """ONE audit row per destroyed descriptor. Never the descriptor vector — see module docstring.
    Best-effort: a logging failure must never be allowed to look like "we can't prove we destroyed it"
    by ALSO undoing the delete, so this is called strictly AFTER the delete succeeds and never raises."""
    try:
        row = {
            "org_id": org_id,
            "employee_id": item.get("employee_id"),
            "employee_name": item.get("employee_name"),
            "trigger": item.get("trigger"),
            "descriptor_id": item.get("descriptor_id"),
            "descriptor_registered_at": item.get("descriptor_registered_at"),
            "descriptor_updated_at": item.get("descriptor_updated_at"),
            "last_interaction_at": item.get("last_interaction_at"),
            "termination_date": item.get("termination_date"),
            "retention_days_applied": item.get("retention_days_applied"),
            "dry_run": False,
            "destroyed_by": destroyed_by or "system",
            "notes": notes or item.get("notes"),
        }
        sb_client.table("face_retention_log").insert(row).execute()
    except Exception as e:
        print(f"WARN face_retention_log insert failed (is migration 422 applied?): {e}")


def destroy(org_id, sb_client, computed=None, dry_run=True, destroyed_by=None):
    """Execute (or preview) destruction of every item `compute_due` found due. `computed` may be
    passed in (e.g. already fetched for a preview the caller now wants to apply) or is computed fresh.

    dry_run=True (the default — matches this codebase's dry-run-before-apply convention, see
    hr/router.py onboarding_reconcile): nothing is deleted, nothing is logged, the candidate list is
    just returned for review.

    dry_run=False: each candidate's face_descriptors row is deleted (org+id scoped — RULE ONE) and,
    ONLY on a successful delete, one audit row is written. A delete failure for one row is skipped
    (not retried in-loop) and simply reappears as still-due on the next sweep — never silently marked
    destroyed."""
    computed = computed if computed is not None else compute_due(org_id, sb_client)
    if not computed.get("available"):
        return {"available": False, "dry_run": dry_run, "candidates": 0, "destroyed": 0,
                "purge_all": False, "items": []}
    items = computed.get("items") or []
    if dry_run:
        return {"available": True, "dry_run": True, "candidates": len(items), "destroyed": 0,
                "purge_all": computed.get("purge_all", False),
                "purge_reason": computed.get("purge_reason"), "items": items}
    destroyed = []
    for it in items:
        did = it.get("descriptor_id")
        if not did:
            continue
        try:
            sb_client.table("face_descriptors").delete().eq("org_id", org_id).eq("id", did).execute()
        except Exception as e:
            print(f"WARN face_descriptors delete failed for {did} (org {org_id}): {e}")
            continue
        _write_audit_log(org_id, sb_client, it, destroyed_by)
        destroyed.append(it)
    return {"available": True, "dry_run": False, "candidates": len(items), "destroyed": len(destroyed),
            "purge_all": computed.get("purge_all", False),
            "purge_reason": computed.get("purge_reason"), "items": destroyed}


def destroy_one_employee_request(org_id, employee_id, employee_name, sb_client,
                                  destroyed_by=None, note=None, dry_run=False):
    """Trigger 2 — immediate, single-employee destruction on the employee's own written request. Never
    waits for a sweep. `note` should record how the request was received (the BIPA-relevant "what was
    the ask and when" — this function does not fabricate a default beyond a generic fallback string;
    the caller/endpoint is expected to pass the real detail)."""
    if not employee_id:
        return {"ok": False, "destroyed": 0, "detail": "no employee_id"}
    try:
        rows = (sb_client.table("face_descriptors").select("id,registered_at,updated_at")
                .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    except Exception as e:
        return {"ok": False, "destroyed": 0, "detail": f"could not read face_descriptors: {e}"}
    if not rows:
        return {"ok": True, "destroyed": 0, "detail": "no biometric template on file for this employee"}
    d = rows[0]
    item = {"employee_id": employee_id, "employee_name": employee_name,
            "trigger": TRIGGER_EMPLOYEE_REQUEST, "descriptor_id": d.get("id"),
            "descriptor_registered_at": d.get("registered_at"), "descriptor_updated_at": d.get("updated_at"),
            "last_interaction_at": None, "termination_date": None, "retention_days_applied": None,
            "notes": (note or "employee written request received")[:500]}
    if dry_run:
        return {"ok": True, "destroyed": 0, "dry_run": True, "would_destroy": item}
    try:
        sb_client.table("face_descriptors").delete().eq("org_id", org_id).eq("id", d["id"]).execute()
    except Exception as e:
        return {"ok": False, "destroyed": 0, "detail": f"delete failed: {e}"}
    _write_audit_log(org_id, sb_client, item, destroyed_by, notes=item["notes"])
    return {"ok": True, "destroyed": 1, "detail": "destroyed on employee request"}


def recent_log(org_id, sb_client, limit=100):
    """The admin panel's evidence view — org-scoped, most recent first. Empty list (never raises) when
    migration 422 hasn't run."""
    try:
        rows = (sb_client.table("face_retention_log").select("*").eq("org_id", org_id)
                .order("destroyed_at", desc=True).limit(max(1, min(int(limit or 100), 500)))
                .execute().data) or []
    except Exception:
        return []
    return rows
