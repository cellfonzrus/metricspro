"""Vision tenant configuration and the gates every other file in this module must pass through.

WHY THIS FILE IS THE FIRST ONE TO READ
──────────────────────────────────────
This module can watch a live camera in a retail store and can transcribe what a person says while
they are at work. That is the most consequential capability in the platform, so the enablement logic
lives in ONE small, dependency-free, provably-tested file rather than being spread across the router.
`backend/harness_vision_gate.py` proves the table below without a database.

THE GATES, IN THE ORDER THEY BIND
─────────────────────────────────
  1. Global audio kill switch    env VISION_AUDIO_ENABLED unset/0  -> the AUDIO path is off for every
                                 tenant, no matter what any row says. Video/heat map are unaffected.
  2. Tenant master switch        vision_config.enabled = false     -> the WHOLE module is off.
  3. Tenant sub-switch           live_view/traffic/heatmap/audio_analytics/behavior_scoring
  4. Per-camera switch           camera.enabled, camera.analytics_enabled, camera.audio_enabled
  5. Per-employee consent        vision_consent.status = 'signed'  -> required before ANY transcript
                                 segment of that employee is accepted (unless the tenant has
                                 deliberately set audio_consent_mode='off', which is audited).

Every gate is AND-ed. There is no value at a lower level that can re-open a gate a higher level shut
— the exact precedence mistake `storeops/face_recognition.py` documents at length and avoids.

DEGRADE (AGENT_CONTRACT §5) — FAILS CLOSED
──────────────────────────────────────────
Every read is wrapped. If migration 900 has not run, or the DB is unreachable, `resolve_config`
returns the defaults with `enabled=False` and `available=False`. The desired state and the safe state
are the same one (off), so the module disables itself the moment it deploys and stays that way until
an operator turns it on deliberately. `available` lets the admin UI tell "you turned it off" apart
from "the migration hasn't run", so an operator is never given a wrong explanation.
"""
import os
from datetime import datetime, timezone

# ── GLOBAL AUDIO KILL SWITCH ──────────────────────────────────────────────────────────────────────
# Voice capture is off platform-wide unless an operator explicitly opts the deployment in. This is
# deliberately an ENV switch and not only a per-tenant row: turning on transcript capture should
# require touching the deployment, not just clicking a toggle, because in most of the states these
# stores operate in a recording made without the speaker's consent is a statutory violation and not
# merely a policy problem. Set VISION_AUDIO_ENABLED=1 to restore normal per-tenant behaviour.
AUDIO_GLOBALLY_DISABLED = os.environ.get("VISION_AUDIO_ENABLED", "0").strip().lower() not in (
    "1", "true", "yes", "on")

CONSENT_SIGNED = "signed"
CONSENT_DECLINED = "declined"
CONSENT_WITHDRAWN = "withdrawn"
CONSENT_PENDING = "pending"

# What every tenant gets before migration 900 exists, and what a fresh tenant's row defaults to.
# Mirrors the column defaults in the migration exactly — if you change one, change both.
DEFAULT_CONFIG = {
    "enabled": False,
    "live_view_enabled": True,
    "traffic_enabled": True,
    # Google's own person events (mig 907). Free, every camera, no analyzer — but PRESENCE only, so
    # it is a separate switch from traffic, which means directional counting and comes from the edge.
    "google_events_enabled": True,
    "heatmap_enabled": True,
    "audio_analytics_enabled": False,
    "behavior_scoring_enabled": False,
    "audio_consent_mode": "required",
    # Employee activity from pose (mig 910). Both default FALSE for the same reason the audio pair
    # does: the safe state and the default state must be the same one. Coverage defaults TRUE — it
    # names nobody, needs no consent, and carries no per-person content at all.
    "activity_enabled": False,
    "face_state_enabled": False,
    "coverage_enabled": True,
    "video_consent_mode": "required",
    "activity_retention_days": 30,
    "coverage_retention_days": 400,
    "activity_bucket_seconds": 900,
    "activity_sample_seconds": 2.0,
    "walk_speed": 0.05,
    "engage_distance": 0.12,
    "idle_after_seconds": 120,
    "presence_retention_days": 7,
    "visit_retention_days": 90,
    "transcript_retention_days": 30,
    "heat_retention_days": 400,
    "score_retention_days": 400,
    "grid_cols": 24,
    "grid_rows": 16,
    "min_visit_seconds": 20,
    "max_visit_seconds": 5400,
    "stream_max_minutes": 30,
}

_COLS = ",".join(["org_id", "enabled", "enabled_at", "enabled_by"] + [
    k for k in DEFAULT_CONFIG if k != "enabled"])


def _now():
    return datetime.now(timezone.utc).isoformat()


def resolve_config(client, org_id: str) -> dict:
    """The effective vision configuration for one tenant.

    Always returns a complete dict (every DEFAULT_CONFIG key present) plus:
      available  — False when migration 900 has not run / the read failed. Config is the defaults.
      audio_kill_switch — True when the deployment-level audio switch is off, in which case
                          audio_analytics_enabled is forced False regardless of the stored row.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg["available"] = False
    cfg["enabled_at"] = None
    cfg["enabled_by"] = None
    try:
        rows = (client.schema("core").table("vision_config").select(_COLS)
                .eq("org_id", org_id).limit(1).execute().data) or []
        cfg["available"] = True
        if rows:
            row = rows[0]
            for k in list(DEFAULT_CONFIG) + ["enabled_at", "enabled_by"]:
                if row.get(k) is not None:
                    cfg[k] = row[k]
    except Exception:
        # Migration not run, table missing, DB down — every one of these means "we cannot prove this
        # tenant opted in", and the only safe answer to that is no.
        cfg["enabled"] = False

    cfg["audio_kill_switch"] = AUDIO_GLOBALLY_DISABLED
    if AUDIO_GLOBALLY_DISABLED:
        cfg["audio_analytics_enabled"] = False
        cfg["behavior_scoring_enabled"] = False
    if not cfg["enabled"]:
        # A master switch that is off means off — not "off but the sub-switches still say true".
        # Callers read the sub-switches directly, so they are collapsed here rather than at each site.
        for k in ("live_view_enabled", "traffic_enabled", "heatmap_enabled",
                  "audio_analytics_enabled", "behavior_scoring_enabled"):
            cfg[k] = False
    return cfg


def feature_enabled(cfg: dict, feature: str) -> bool:
    """True if `feature` ('live_view' | 'traffic' | 'heatmap' | 'google_events' | 'audio_analytics' |
    'behavior_scoring' | 'activity' | 'face_state' | 'coverage') is usable for this tenant right now. Master switch is already folded into the
    sub-switches by resolve_config, but it is re-checked here so this is correct on a hand-built dict."""
    if not cfg or not cfg.get("enabled"):
        return False
    if feature in ("audio_analytics", "behavior_scoring") and AUDIO_GLOBALLY_DISABLED:
        return False
    return bool(cfg.get(f"{feature}_enabled"))


def camera_allows(cfg: dict, camera: dict, feature: str) -> bool:
    """AND the per-camera switches onto the tenant answer. A camera that is disabled is invisible to
    every path; a camera with analytics_enabled=false may still be watched live but contributes no
    numbers; audio needs the camera's own audio_enabled AND the hardware supporting_audio."""
    if not feature_enabled(cfg, feature):
        return False
    if not camera or not camera.get("enabled", True):
        return False
    if feature in ("traffic", "heatmap", "activity", "coverage", "face_state") \
            and not camera.get("analytics_enabled", True):
        return False
    # Face state rides on activity: a tenant cannot end up measuring mouths on a camera whose
    # activity analysis is off, whichever order the two switches were flipped in.
    if feature == "face_state" and not feature_enabled(cfg, "activity"):
        return False
    if feature == "traffic" and not camera.get("is_entrance", False):
        return False
    if feature == "audio_analytics":
        if not camera.get("analytics_enabled", True):
            return False
        if not camera.get("audio_enabled", False) or not camera.get("supports_audio", False):
            return False
    return True


def consent_ok(cfg: dict, consent_row) -> tuple:
    """May this employee's speech be transcribed? Returns (allowed: bool, reason: str).

    Unlike the kiosk face path there is NO "assumed consent". The only paths to True are a recorded
    'signed' row, or a tenant that has deliberately set audio_consent_mode='off' (an operator
    asserting they hold their own release; the setting change is written to vision_audit).
    A 'withdrawn' row is as binding as a 'declined' one — consent is revocable by definition, and a
    withdrawal must survive the master switch being toggled off and on again."""
    if AUDIO_GLOBALLY_DISABLED:
        return False, "audio_kill_switch"
    if not cfg or not cfg.get("enabled"):
        return False, "tenant_disabled"
    if not cfg.get("audio_analytics_enabled"):
        return False, "audio_analytics_disabled"

    status = (consent_row or {}).get("status") if isinstance(consent_row, dict) else None
    status = (status or "").strip().lower()
    if status in (CONSENT_DECLINED, CONSENT_WITHDRAWN):
        return False, f"consent_{status}"      # never overridable, not even by consent_mode='off'
    if status == CONSENT_SIGNED:
        return True, "consent_signed"
    if (cfg.get("audio_consent_mode") or "required").strip().lower() == "off":
        return True, "consent_mode_off"
    return False, "consent_missing"


def retention_cutoffs(cfg: dict, now=None) -> dict:
    """{table_key: iso timestamp} — anything strictly older than the cutoff is purgeable.
    Zero or negative days disables the purge for that table (an operator opting into keeping it)."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    out = {}
    for key, col in (("presence", "presence_retention_days"), ("visit", "visit_retention_days"),
                     ("transcript", "transcript_retention_days"), ("heat", "heat_retention_days"),
                     ("score", "score_retention_days"),
                     # mig 910. Activity buckets are the most sensitive rows in the module, so they
                     # expire fastest of anything but raw presence: long enough for a monthly
                     # coaching conversation, too short to accumulate a dossier. Coverage names
                     # nobody and keeps the heat map's reporting horizon.
                     ("activity", "activity_retention_days"),
                     ("coverage", "coverage_retention_days")):
        days = cfg.get(col)
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = DEFAULT_CONFIG[col]
        out[key] = (now - timedelta(days=days)).isoformat() if days > 0 else None
    return out
