"""Kiosk face-recognition enablement (mod-people, owner directive 2026-08-09):

"disable the face recognition feature for all tenants for now keep the option of turning on at a later
date, when turning on as if the face recognition consent has been signed by all employees and it should
be assigned per employee"

RULE TWO (SAP-configurable): a tenant-wide MASTER switch + a per-employee ASSIGNMENT + a per-employee
CONSENT record (migration 420, storeops.tenants / storeops.employees). Nothing about this is hard-coded
or tenant-named — every tenant gets the same switch, defaulted OFF.

Also implements the actionable half of docs/metricspro-security-plan.md Phase 9 (biometric compliance):
face descriptors are regulated biometric data, the Illinois stores sit under BIPA (private right of
action), and the non-biometric alternative the plan asks for (9.3) already exists in the kiosk — the
selfie-only clock-in path. With the master switch off, that path becomes the ONLY path.

── PRECEDENCE (deliberately NOT migration 418's lunch semantics — read before changing) ──────────────
The tenant flag is a MASTER switch, not merely a default. Lunch-deduction lets a per-employee value win
over the tenant value per field; applying that here would let one stray employee row re-enable biometric
capture on a tenant the owner has switched OFF. So:

    1. tenant.face_recognition_enabled is false        -> OFF for everyone         ('tenant_disabled')
    2. employee.face_consent_status == 'declined'      -> OFF for that employee    ('consent_declined')
    3. employee.face_recognition_enabled is False      -> OFF for that employee    ('employee_unassigned')
    4. employee.face_recognition_enabled is True       -> ON                       ('employee_assigned')
    5. employee.face_recognition_enabled is NULL       -> tenant.face_recognition_default_for_employees
                                                          ('tenant_default' / 'tenant_default_off')

`default_for_employees` defaults to TRUE so that flipping the master switch back on restores exactly
today's behaviour for the whole roster; an admin who wants "only the people I name" flips it to false in
the Time Clock page's ⚙ Face Recognition panel — a config change, not a code change.

── CONSENT ("as if signed by all employees") ─────────────────────────────────────────────────────────
`stamp_assumed_consent_for_all()` runs when the master switch is turned ON: every employee of that
tenant with NO consent record gets status='signed', at=now(), source='assumed_on_enable'. The owner's
instruction, implemented as an explicit dated per-employee row instead of a silent assumption — so
"show me this person's consent and when it was given" has an answer, and the source field states
honestly how it was obtained. An employee already recorded as 'declined' is never re-stamped (a
recorded refusal must survive the switch being toggled off and on again).

── DEGRADE (AGENT_CONTRACT §5) — FAILS CLOSED ────────────────────────────────────────────────────────
Every read here is wrapped in try/except and returns enabled=False when migration 420 hasn't run. This
is the OPPOSITE of lunch_deduction.py's "degrade = change nothing", and it is intentional: the owner's
requested state and the safe state are the same one (off), so the code disables face recognition the
moment it deploys, whether or not the migration has been applied yet. `available` tells the admin UI
which of the two it is, so a tenant is never told "you turned it off" when the real reason is a missing
migration.
"""
import os
from datetime import datetime, timezone

# ── GLOBAL KILL SWITCH (owner directive 2026-08-14: disable face ID app-wide, till further notice) ──
# Face recognition is turned OFF for EVERY tenant and employee, independent of the stored master switch
# / per-employee assignment, so every client (web kiosk + mobile app) and the admin gate see the
# feature as disabled and clock-in takes the no-face path. This is the single choke point every
# enabled-check flows through (get_tenant_face_config + resolve_employee_face below). Re-enable WITHOUT
# a code change by setting env FACE_ID_ENABLED=1 (restores the normal per-tenant behaviour), or revert
# this block. Enrollment/verify simply never run while it is off; no stored face data is touched.
FACE_ID_GLOBALLY_DISABLED = os.environ.get("FACE_ID_ENABLED", "0").strip().lower() not in ("1", "true", "yes", "on")

# What every tenant gets before migration 420 exists, and what a fresh tenant's columns default to.
DEFAULT_TENANT_FACE_CONFIG = {"enabled": False, "default_for_employees": True}

CONSENT_SIGNED = "signed"
CONSENT_DECLINED = "declined"
CONSENT_STATUSES = (CONSENT_SIGNED, CONSENT_DECLINED)
CONSENT_SOURCE_ASSUMED = "assumed_on_enable"   # stamped in bulk when the master switch goes ON
CONSENT_SOURCE_MANUAL = "manual"               # HR recorded a real signed release for this person

_TENANT_COLS = ("face_recognition_enabled,face_recognition_default_for_employees,"
                "face_recognition_enabled_at,face_recognition_enabled_by")
_EMPLOYEE_COLS = ("employee_id,face_recognition_enabled,face_consent_status,"
                  "face_consent_at,face_consent_source")


def get_tenant_face_config(org_id, sb_client):
    """(config, available). `available=False` means migration 420 hasn't run on this tenant — the
    config returned in that case is the OFF default and callers must treat the feature as disabled
    (fail-closed; see module docstring).

    Availability is decided by the COLUMN'S PRESENCE on the fetched row, not merely by "the select
    didn't raise" — the same reasoning lunch_deduction.get_tenant_lunch_config documents: real
    PostgREST raises for an unknown column, but the in-memory fake client used by the harness suite is
    a schemaless dict store that never raises, so an exception check alone would make every old fixture
    look migrated."""
    try:
        rows = (sb_client.table("tenants").select(_TENANT_COLS)
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return dict(DEFAULT_TENANT_FACE_CONFIG), False
    if not rows or "face_recognition_enabled" not in rows[0]:
        return dict(DEFAULT_TENANT_FACE_CONFIG), False
    t = rows[0]
    default_for_employees = t.get("face_recognition_default_for_employees")
    cfg = {
        # Global kill switch wins over the stored master switch (owner directive 2026-08-14).
        "enabled": bool(t.get("face_recognition_enabled")) and not FACE_ID_GLOBALLY_DISABLED,
        "default_for_employees": True if default_for_employees is None else bool(default_for_employees),
        "enabled_at": t.get("face_recognition_enabled_at"),
        "enabled_by": t.get("face_recognition_enabled_by"),
    }
    return cfg, True


def get_employee_face_row(org_id, employee_id, sb_client):
    """One employee's assignment + consent row ({} when absent or pre-migration). Queried on its own,
    never folded into an unrelated employees select, so a missing migration 420 can't 500 a normal
    roster/pay read (the isolation rule migration 418 established)."""
    if not employee_id:
        return {}
    try:
        rows = (sb_client.table("employees").select(_EMPLOYEE_COLS)
                .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    except Exception:
        return {}
    return rows[0] if rows else {}


def get_employee_face_rows(org_id, sb_client):
    """({employee_id: row}, available) for the whole roster — the admin panel + the attention provider."""
    try:
        rows = (sb_client.table("employees").select(_EMPLOYEE_COLS)
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}, False
    out = {}
    available = False
    for e in rows:
        eid = e.get("employee_id")
        if not eid:
            continue
        if "face_recognition_enabled" in e:
            available = True
        out[str(eid)] = e
    return out, available


def resolve_employee_face(tenant_cfg, employee_row, available=True):
    """{enabled, reason} for one employee. `reason` is a stable machine key the kiosk and the admin
    panel both render — never a sentence, so the wording can change without breaking a caller."""
    if FACE_ID_GLOBALLY_DISABLED:
        # App-wide kill switch (owner directive 2026-08-14) — off for everyone regardless of config.
        return {"enabled": False, "reason": "globally_disabled"}
    if not available:
        return {"enabled": False, "reason": "not_configured"}
    if not (tenant_cfg or {}).get("enabled"):
        return {"enabled": False, "reason": "tenant_disabled"}
    row = employee_row or {}
    if row.get("face_consent_status") == CONSENT_DECLINED:
        return {"enabled": False, "reason": "consent_declined"}
    assigned = row.get("face_recognition_enabled")
    if assigned is False:
        return {"enabled": False, "reason": "employee_unassigned"}
    if assigned is True:
        return {"enabled": True, "reason": "employee_assigned"}
    if tenant_cfg.get("default_for_employees", True):
        return {"enabled": True, "reason": "tenant_default"}
    return {"enabled": False, "reason": "tenant_default_off"}


def stamp_assumed_consent_for_all(org_id, sb_client, who=None):
    """Owner's "as if the consent has been signed by all employees", run when the master switch goes ON.

    Stamps ONLY employees with no consent record at all (`face_consent_status IS NULL`) — an employee
    already recorded as 'declined' keeps that refusal, and one with a real 'manual' signature keeps its
    true date/source. Returns the number of rows stamped, or None if the columns don't exist yet.

    Best-effort by design: the caller has already flipped the switch, and a failure here must not undo
    that or 500 the request — it is reported back to the admin UI as an unstamped count instead."""
    now = datetime.now(timezone.utc).isoformat()
    patch = {"face_consent_status": CONSENT_SIGNED, "face_consent_at": now,
             "face_consent_source": CONSENT_SOURCE_ASSUMED}
    if who:
        patch["face_consent_source"] = f"{CONSENT_SOURCE_ASSUMED}:{who}"[:120]
    try:
        r = (sb_client.table("employees").update(patch)
             .eq("org_id", org_id).is_("face_consent_status", "null").execute())
    except Exception:
        return None
    return len(r.data or [])


def consent_summary(employee_rows):
    """{signed, declined, unrecorded, assigned_on, assigned_off, unassigned} for the admin panel."""
    out = {"signed": 0, "declined": 0, "unrecorded": 0,
           "assigned_on": 0, "assigned_off": 0, "unassigned": 0}
    for row in (employee_rows or {}).values():
        status = row.get("face_consent_status")
        if status == CONSENT_SIGNED:
            out["signed"] += 1
        elif status == CONSENT_DECLINED:
            out["declined"] += 1
        else:
            out["unrecorded"] += 1
        assigned = row.get("face_recognition_enabled")
        if assigned is True:
            out["assigned_on"] += 1
        elif assigned is False:
            out["assigned_off"] += 1
        else:
            out["unassigned"] += 1
    return out
