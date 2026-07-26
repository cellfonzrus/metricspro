"""HR contributions to the cross-module admin-attention feed (app/modules/core/import_health.py's
`register_provider`) — settings-audit package, 2026-07-26. See storeops/attention.py's module
docstring for the shared conventions (unscoped/schema-qualified client, org_id-filtered, cost rules).
Registered from hr/router.py's own import, guarded by a try/except there.

PII SAFETY: neither provider here ever calls the crypto module's decrypt function — only `crypto.is_encrypted()`
(a cheap string-prefix check) and `crypto.is_enabled()` (whether a key is loaded at all). No sensitive
value is ever read, logged, or returned by this file, matching the contract's "never log, echo, or
return decrypted PII outside that [Reveal] endpoint" rule.
"""
from datetime import datetime, timezone
from app.core import crypto

_STUCK_STATUSES = ("invited", "in_progress", "docs_submitted")   # not yet HR-verified/provisioned
_DEFAULT_STUCK_DAYS = 7


def _item(group, key, severity, label, detail, count, deep_link, deep_link_label):
    return {"group": group, "key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": deep_link_label}


def onboarding_stuck_days(client, org_id):
    """The tenant's configured 'stuck invite' threshold (storeops.tenants.onboarding_stuck_days,
    migration 410), clamped 1-90, defaulting to 7 when unset/un-migrated. Never raises — RULE TWO
    (a tunable threshold, sane default, degrades gracefully pre-migration)."""
    try:
        t = (client.schema("storeops").table("tenants").select("onboarding_stuck_days")
             .eq("org_id", org_id).limit(1).execute().data) or []
        v = t[0].get("onboarding_stuck_days") if t else None
        n = int(v) if v is not None else _DEFAULT_STUCK_DAYS
    except Exception:
        n = _DEFAULT_STUCK_DAYS
    return max(1, min(90, n))


def _sensitive_intake_keys(client, org_id):
    """The org's own configured 'sensitive' intake-field keys (storeops.onboarding_intake_field —
    the SAME table the Reveal endpoint and the encrypt-existing backfill already use), never a
    hard-coded guess at field names."""
    try:
        rows = (client.schema("storeops").table("onboarding_intake_field")
                .select("key,sensitive").eq("org_id", org_id).eq("sensitive", True)
                .execute().data) or []
        return {r.get("key") for r in rows if r.get("key")}
    except Exception:
        return set()


def register(register_provider):
    """Called once, with the REAL decorator from core.import_health (import guarded by the caller)."""

    @register_provider("hr_onboarding_stuck", label="Onboarding invites stuck", group="people",
                       cost="cheap")
    def _p_onboarding_stuck(client, org_id, ctx):
        """PENDING FOLLOW-UP (cheap, bounded per-employee profile table): a new-hire invite that has
        sat in invited/in_progress/docs_submitted (never reaching HR verification) for longer than the
        tenant's configured threshold — the employee may be stuck, or HR may not know a verification
        step is waiting on them."""
        now = ctx.get("now") or datetime.now(timezone.utc)
        days = onboarding_stuck_days(client, org_id)
        try:
            rows = (client.schema("storeops").table("employee_onboarding_profile")
                    .select("employee_id,workflow_status,invited_at")
                    .eq("org_id", org_id).limit(5000).execute().data) or []
        except Exception:
            return []
        stuck = 0
        for r in rows:
            if (r.get("workflow_status") or "invited") not in _STUCK_STATUSES:
                continue
            iv = r.get("invited_at")
            if not iv:
                continue
            try:
                ivd = datetime.fromisoformat(str(iv).replace("Z", "+00:00"))
                if ivd.tzinfo is None:
                    ivd = ivd.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if (now - ivd).total_seconds() / 86400.0 > days:
                stuck += 1
        if not stuck:
            return []
        return [_item("people", "onboarding_stuck", "warning", "Onboarding invites stuck",
                      f"{stuck} new-hire invite(s) have been sitting in invited / in-progress / "
                      f"docs-submitted for more than {days} day(s) without reaching HR verification. "
                      f"Nudge the employee, re-send the invite, or verify their documents from HR → "
                      f"Onboarding. (Threshold is configurable — GET/PUT "
                      f"/hr/onboarding/attention-config.)",
                      stuck, "/hr/onboarding", "Open HR Onboarding")]

    @register_provider("hr_pii_encryption", label="PII encryption key status", group="security",
                       cost="cheap")
    def _p_pii_encryption(client, org_id, ctx):
        """PENDING SETUP / SAFETY (cheap, bounded per-employee profile table): flags the two real
        failure shapes for FIELD_ENCRYPTION_KEY — (1) ciphertext exists but no key is configured to
        read it (ERROR: unrecoverable-in-waiting, restore the key from your secrets backup, never
        generate a NEW one as a 'fix') and (2) a key IS configured but some of this org's own
        configured-sensitive intake fields are still sitting in the clear (WARNING: run the one-time
        backfill). Never decrypts anything (see module docstring)."""
        try:
            rows = (client.schema("storeops").table("employee_onboarding_profile")
                    .select("employee_id,intake_data").eq("org_id", org_id)
                    .limit(5000).execute().data) or []
        except Exception:
            return []
        if not rows:
            return []
        enabled = crypto.is_enabled()
        sens_keys = _sensitive_intake_keys(client, org_id) if enabled else set()
        has_ciphertext = False
        plaintext_count = 0
        for r in rows:
            data = r.get("intake_data")
            if not isinstance(data, dict) or not data:
                continue
            for k, v in data.items():
                if crypto.is_encrypted(v):
                    has_ciphertext = True
                elif enabled and k in sens_keys and str(v or "").strip():
                    plaintext_count += 1
        out = []
        if has_ciphertext and not enabled:
            out.append(_item(
                "security", "pii_key_missing", "error", "PII encryption key is not configured",
                "Encrypted employee PII (SSN / bank / A-Number, etc.) exists in this tenant's intake "
                "data, but the backend has no FIELD_ENCRYPTION_KEY configured right now — that data "
                "can no longer be decrypted (Reveal will show '(unavailable)'). If a key was ever "
                "removed or rotated by mistake, restore it immediately from your secrets backup — "
                "there is NO way to recover the data without it. Never generate a brand-new key as a "
                "'fix': that makes the existing ciphertext permanently unreadable.",
                1, "/hr/onboarding", "Open HR Onboarding"))
        if plaintext_count:
            out.append(_item(
                "security", "pii_plaintext_unbackfilled", "warning",
                "Sensitive employee data stored unencrypted",
                f"Encryption is configured, but {plaintext_count} sensitive intake field value(s) "
                f"across this org's employees are still stored in the clear (never backfilled). Run "
                f"the one-time encryption backfill (HR → Onboarding → Reconcile, or "
                f"POST /hr/onboarding/encrypt-existing) to encrypt them at rest.",
                plaintext_count, "/hr/onboarding", "Open HR Onboarding"))
        return out
