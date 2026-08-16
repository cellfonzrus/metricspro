"""Startup security-posture check (Security Controls Spec §2/§5).

Logs the enforcement posture once at boot and warns — loudly — when a security-critical secret is
missing in production or when a load-bearing protection is running in its break-glass (off) state. This
turns "someone forgot to set FIELD_ENCRYPTION_KEY" from an invisible fail-open into a boot-time signal.

  • BEST EFFORT. Never blocks boot by default — a hard exit would turn a missing env var into an
    outage, the opposite of what a hardening pass should do. STARTUP_STRICT=1 upgrades missing-secret
    findings in production to a hard RuntimeError for operators who prefer fail-to-boot.
  • Writes one core.failure_log row (category 'security_posture') in production when there are
    findings, so the posture is visible in the app, not just the container logs. Wrapped end-to-end;
    a logging fault never breaks boot.
  • Reads ENV only — no secret VALUES are ever logged, only presence/absence.
"""
import os
import logging

log = logging.getLogger("security.posture")

_PLATFORM_ORG_ID = os.environ.get("PLATFORM_ORG_ID", "00000000-0000-0000-0000-000000000001")


def _prod() -> bool:
    return (os.environ.get("APP_ENV", "") or "").strip().lower() in ("production", "prod", "live")


def _on(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() not in ("0", "false", "no", "off")


def _set(name: str) -> bool:
    return bool((os.environ.get(name, "") or "").strip())


def evaluate() -> dict:
    """PURE-ish (reads env only). Returns {posture: {...}, findings: [str, ...]}. Findings are the
    things an operator should see; posture is the full switch snapshot for the log line."""
    posture = {
        "prod": _prod(),
        "MULTI_TENANT_ENFORCE": _on("MULTI_TENANT_ENFORCE", "0"),
        "SESSION_ENFORCE": _on("SESSION_ENFORCE", "0"),
        "RATE_LIMIT_ENFORCE": _on("RATE_LIMIT_ENFORCE", "1"),
        "RBAC_SCOPE_FAILCLOSED": _on("RBAC_SCOPE_FAILCLOSED", "1"),
        "FIELD_ENCRYPTION_STRICT": _on("FIELD_ENCRYPTION_STRICT", "0"),
        "field_key_set": _set("FIELD_ENCRYPTION_KEY") or _set("FIELD_ENCRYPTION_KEYS"),
        "notify_secret_set": _set("NOTIFY_RUN_SECRET"),
    }
    findings = []
    if posture["prod"]:
        if not posture["field_key_set"]:
            findings.append("FIELD_ENCRYPTION_KEY is NOT set — SSN/bank fields are stored in PLAINTEXT "
                            "(field encryption fails open). Set the key; then enable "
                            "FIELD_ENCRYPTION_STRICT=1.")
        if not posture["notify_secret_set"]:
            findings.append("NOTIFY_RUN_SECRET is NOT set — the scheduler/cron endpoints "
                            "(/notify/run-due, /core/audit/prune/run-due) will refuse (403).")
        if not posture["MULTI_TENANT_ENFORCE"]:
            findings.append("MULTI_TENANT_ENFORCE is OFF — tenant isolation is NOT enforced server-side "
                            "(break-glass state). Enable it after the isolation test passes.")
        if posture["field_key_set"] and not posture["FIELD_ENCRYPTION_STRICT"]:
            findings.append("FIELD_ENCRYPTION_STRICT is OFF — a future key removal would silently fall "
                            "back to plaintext. Enable it now that the key is set.")
    return {"posture": posture, "findings": findings}


def _record_failure_log(findings):
    try:
        from app.core.database import get_supabase
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": _PLATFORM_ORG_ID,
            "category": "security_posture",
            "severity": "warning",
            "source": "core/security_posture:startup",
            "message": ("Security posture check found %d item(s) at startup." % len(findings))[:1000],
            "detail": {"findings": findings},
            "remediation": ("Set the missing secret(s) / enable the missing enforcement in Railway. See "
                            "docs/SECURITY_CONTROLS_SPEC.md and docs/SECURITY_DAILY_QUESTIONS.md."),
        }).execute()
    except Exception:
        pass


def check_and_log():
    """Call once at app startup. Logs posture, warns on findings, best-effort records them in prod, and
    (only if STARTUP_STRICT=1 and in prod) raises to fail the boot."""
    try:
        result = evaluate()
        posture, findings = result["posture"], result["findings"]
        log.info("Security posture at startup: %s", posture)
        for f in findings:
            log.warning("SECURITY POSTURE: %s", f)
        if findings and posture["prod"]:
            _record_failure_log(findings)
            if _on("STARTUP_STRICT", "0"):
                raise RuntimeError("STARTUP_STRICT: refusing to boot with security findings: "
                                   + " | ".join(findings))
    except RuntimeError:
        raise
    except Exception:
        # a posture-check fault must never take the app down
        pass


if __name__ == "__main__":
    # Pure evaluate() self-tests via env toggles.
    os.environ["APP_ENV"] = "production"
    os.environ.pop("FIELD_ENCRYPTION_KEY", None)
    os.environ.pop("FIELD_ENCRYPTION_KEYS", None)
    os.environ["NOTIFY_RUN_SECRET"] = ""
    r = evaluate()
    assert any("FIELD_ENCRYPTION_KEY" in f for f in r["findings"]), r
    assert any("NOTIFY_RUN_SECRET" in f for f in r["findings"]), r
    assert any("MULTI_TENANT_ENFORCE" in f for f in r["findings"]), r
    os.environ["APP_ENV"] = "development"
    assert evaluate()["findings"] == [], "no findings outside prod"
    print("security_posture self-tests passed")
