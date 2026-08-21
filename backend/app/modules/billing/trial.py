"""Free-trial primitives — the ONE place that knows how a trial is started and read.

Deliberately imports nothing from `app.modules.core`: core's router imports THIS module to stamp a
trial at tenant provisioning, and the billing pricing router imports it too, so anything heavier here
would be a circular import.

Shape (migration 907):
    storeops.pricing_settings.trial_days   how long a new company gets for free (default 30)
    storeops.tenants.trial_started_at      stamped once, at provisioning
    storeops.tenants.trial_ends_at         trial_started_at + trial_days
    storeops.tenants.plan_status           trialing | active | trial_expired | cancelled

EVERY function here is best-effort: if migration 907 has not been applied, reads fall back to the
code defaults and the trial stamp is silently skipped. An un-run migration must never 500 a signup
or /core/me — it just means nobody is on a trial yet.

WHAT THIS DOES NOT DO: it does not lock anybody out. `trial_expired` is a REPORTED status, computed
from the clock, that surfaces in the app banner and the super-admin console. Cutting off access
stays the operator's explicit decision through the existing tenant is_active switch — an automatic
lockout would be an irreversible-feeling action taken on a paying customer's behalf.
"""
from datetime import datetime, timedelta, timezone

DEFAULT_TRIAL_DAYS = 30
VALID_PLAN_STATUS = {"trialing", "active", "trial_expired", "cancelled"}

# Defaults used when migration 907 is absent, so every caller sees the same shape either way.
DEFAULT_SETTINGS = {
    "trial_enabled": True,
    "trial_days": DEFAULT_TRIAL_DAYS,
    "currency": "USD",
    "show_pricing": True,
    "pricing_headline": None,
    "pricing_subhead": None,
    "trial_note": None,
}


def _now():
    return datetime.now(timezone.utc)


def _read_settings(client) -> tuple[dict, bool]:
    """(settings, reachable). `reachable` is False when the pricing_settings table cannot be read at
    all — i.e. migration 907 has not been applied. The two are separated because the callers want
    OPPOSITE things from that case: a display caller wants sensible defaults to render, while the
    trial STAMP must write nothing at all (see start_trial_fields)."""
    out = dict(DEFAULT_SETTINGS)
    try:
        rows = (client.schema("storeops").table("pricing_settings")
                .select("*").eq("id", 1).limit(1).execute().data) or []
    except Exception:
        return out, False
    if rows:
        for k in DEFAULT_SETTINGS:
            if rows[0].get(k) is not None:
                out[k] = rows[0][k]
    try:
        out["trial_days"] = max(0, int(out["trial_days"]))
    except (TypeError, ValueError):
        out["trial_days"] = DEFAULT_TRIAL_DAYS
    return out, True


def load_settings(client) -> dict:
    """The singleton storeops.pricing_settings row, merged over DEFAULT_SETTINGS. Never raises — an
    un-run migration 907 yields the code defaults, so the public pricing feed still answers."""
    return _read_settings(client)[0]


def trial_days(client) -> int:
    """How many free days a company signing up right now gets. 0 = trials are switched off."""
    s = load_settings(client)
    return int(s["trial_days"]) if s.get("trial_enabled") else 0


def start_trial_fields(client) -> dict:
    """The columns to write on a BRAND-NEW tenant so it starts on a trial. Empty dict when trials are
    off, OR when migration 907 has not been applied — the caller then inserts the tenant with no
    trial stamp, exactly as it did before 907.

    The un-run-migration case is checked HERE rather than left to the caller's insert to fail: on a
    pre-907 database the trial columns do not exist, so stamping them would make every signup insert
    fail and fall back. Reading the settings table's absence up front keeps that path clean.
    """
    settings, reachable = _read_settings(client)
    if not reachable or not settings.get("trial_enabled"):
        return {}
    days = int(settings["trial_days"])
    if days <= 0:
        return {}
    now = _now()
    return {
        "trial_started_at": now.isoformat(),
        "trial_ends_at": (now + timedelta(days=days)).isoformat(),
        "plan_status": "trialing",
    }


def _parse(ts):
    """Parse a Postgres timestamptz string. Returns None on anything unparseable."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def trial_view(tenant_row: dict) -> dict | None:
    """What the app should show about this tenant's plan, computed from the stored stamp + the clock.

    Returns None for a tenant carrying no plan state at all (pre-907 rows, or a tenant provisioned
    while trials were off) — callers treat None as "say nothing", which is the pre-907 behaviour.

        {status, days_left, ends_at, expired}

    `status` is 'trial_expired' once the clock passes trial_ends_at, even though the stored
    plan_status still reads 'trialing': the clock is the truth, and nothing has to run on a schedule
    to flip a column. Converting a trial to 'active' is an explicit super-admin action, and once
    stored it wins here — a converted customer never falls back into an expired trial.
    """
    if not tenant_row:
        return None
    stored = (tenant_row.get("plan_status") or "").strip() or None
    ends = _parse(tenant_row.get("trial_ends_at"))
    if not stored and not ends:
        return None
    if stored and stored != "trialing":
        # active / cancelled / already-recorded expiry — reported as stored, no clock involved.
        return {"status": stored, "days_left": None,
                "ends_at": tenant_row.get("trial_ends_at"), "expired": stored == "trial_expired"}
    if not ends:
        return {"status": stored or "trialing", "days_left": None, "ends_at": None, "expired": False}
    remaining = ends - _now()
    expired = remaining.total_seconds() <= 0
    # Round the part-day in progress UP: with 6 hours left a customer has "1 day left", not 0.
    part_day = 1 if (remaining.seconds or remaining.microseconds) else 0
    days_left = 0 if expired else max(1, remaining.days + part_day)
    return {"status": "trial_expired" if expired else "trialing",
            "days_left": int(days_left),
            "ends_at": tenant_row.get("trial_ends_at"),
            "expired": expired}
