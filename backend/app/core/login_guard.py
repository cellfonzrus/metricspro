"""Login attempt ledger + per-email soft lockout (Security Controls Spec §1, item 6).

Primary sign-in is browser → Supabase Auth directly, so it never reaches this backend. This is NOT the
authoritative brute-force control (that's Supabase Auth's own rate limits + the per-IP limiter added in
this phase) — it is defense-in-depth and, importantly, VISIBILITY: failed logins are otherwise invisible
to us. The login page calls `/core/auth/login-precheck` before attempting and `/core/auth/login-record`
after, so:
  • every attempt (pass/fail, email, IP, UA) is recorded in core.login_attempt (mig 859), and
  • after N failures in a window the page shows a short lockout instead of hammering Supabase.

Because an attacker can bypass the page and hit Supabase directly, the lockout is a soft/UX control; it
is honest about that. Tradeoff acknowledged: a per-email lockout is inherently DoS-able (someone can lock
a victim by spamming failures) — mitigated by a SHORT lock window and the per-IP rate limiter capping the
failure rate.

  • PURE CORE. `lock_state(failure_epochs, now, ...)` decides locked/retry with an injected clock,
    unit-tested below.
  • GATED. LOGIN_LOCKOUT_ENFORCE (default ON) — LOGIN_LOCKOUT_ENFORCE=0 disables the lockout while
    still recording attempts (visibility is never turned off). Tunable: LOGIN_MAX_FAILURES (8),
    LOGIN_WINDOW_MIN (15), LOGIN_LOCK_MIN (15).
  • BEST EFFORT / FAIL OPEN. Any ledger error → not locked (never lock a user out of their account over
    a logging fault). Recording is best-effort and never raises.
"""
import os
import time


def enforce() -> bool:
    return os.environ.get("LOGIN_LOCKOUT_ENFORCE", "1").lower() not in ("0", "false", "no", "off")


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _params():
    # Default 5 consecutive failures per External Threat Defense Plan §2.1.
    return (_int_env("LOGIN_MAX_FAILURES", 5),
            _int_env("LOGIN_WINDOW_MIN", 15) * 60,
            _int_env("LOGIN_LOCK_MIN", 15) * 60)


# Throttle lockout alerts: one core.failure_log row per email per lock window, not one per request.
_last_alert = {}


def _alert_lockout(email, failures):
    try:
        now = time.time()
        if now - _last_alert.get(email, 0) < 300:
            return
        _last_alert[email] = now
        import os
        from app.core.database import get_supabase
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": os.environ.get("PLATFORM_ORG_ID", "00000000-0000-0000-0000-000000000001"),
            "category": "security_auth", "severity": "warning",
            "source": "core/login_guard:lockout",
            "message": ("Account soft-locked after %d failed sign-in attempts." % failures)[:1000],
            "detail": {"email": email, "failures": failures},
            "remediation": ("Repeated failed logins for this email. If unexpected, the account may be "
                            "under a credential-stuffing attempt — consider a password reset and check "
                            "the access log / login_attempt ledger for the source IPs."),
        }).execute()
    except Exception:
        pass


def lock_state(failure_epochs, now, max_failures, window_seconds, lock_seconds):
    """PURE. `failure_epochs` = epoch times of FAILED attempts since the last success. Returns
    {locked, failures, retry_after}. Locked once failures-in-window ≥ max; the lock then persists
    `lock_seconds` measured from the MOST RECENT failure, so continued hammering keeps extending it."""
    recent = [t for t in failure_epochs if now - t < window_seconds]
    n = len(recent)
    if n < max_failures:
        return {"locked": False, "failures": n, "retry_after": 0}
    newest = max(recent)
    remaining = lock_seconds - (now - newest)
    if remaining <= 0:
        return {"locked": False, "failures": n, "retry_after": 0}
    return {"locked": True, "failures": n, "retry_after": int(remaining) + 1}


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _parse_ts(v):
    if not v:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _failures_since_success(email, window_seconds, now):
    """Failed-attempt epochs for `email` within the window, counting only those AFTER the most recent
    successful login (a success resets the counter). Best-effort; [] on any error."""
    try:
        from app.core.database import get_supabase_admin
        rows = (get_supabase_admin().schema("core").table("login_attempt")
                .select("success,created_at").eq("email", email)
                .gte("created_at", _iso(now - window_seconds))
                .order("created_at", desc=True).limit(50).execute().data) or []
    except Exception:
        return []
    fails = []
    for r in rows:
        if r.get("success"):
            break                                   # hit the last success → stop
        ts = _parse_ts(r.get("created_at"))
        if ts is not None:
            fails.append(ts)
    return fails


def check(email: str):
    """Return the lock state for an email. Fail-open ('not locked') on anything unexpected."""
    try:
        if not enforce() or not email:
            return {"locked": False, "failures": 0, "retry_after": 0}
        max_f, window_s, lock_s = _params()
        now = time.time()
        fails = _failures_since_success(email, max(window_s, lock_s), now)
        st = lock_state(fails, now, max_f, window_s, lock_s)
        if st.get("locked"):
            _alert_lockout(email, st.get("failures"))       # throttled admin alert
        return st
    except Exception:
        return {"locked": False, "failures": 0, "retry_after": 0}


def record(email: str, ip: str, success: bool, user_agent: str = ""):
    """Append an attempt to the ledger. Best-effort; never raises."""
    try:
        if not email:
            return
        from app.core.database import get_supabase_admin
        (get_supabase_admin().schema("core").table("login_attempt").insert({
            "email": email, "ip": (ip or None), "success": bool(success),
            "user_agent": (user_agent or "")[:300] or None,
        }).execute())
    except Exception:
        pass


if __name__ == "__main__":
    now = 10_000.0
    # 7 failures < max(8) → not locked
    fails = [now - i for i in range(7)]
    assert lock_state(fails, now, 8, 900, 900)["locked"] is False
    # 8 failures within window → locked, retry ~ lock window from newest
    fails = [now - i * 10 for i in range(8)]         # newest at now
    st = lock_state(fails, now, 8, 900, 900)
    assert st["locked"] is True and 890 <= st["retry_after"] <= 901, st
    # old failures outside window are ignored
    old = [now - 1000 - i for i in range(20)]
    assert lock_state(old, now, 8, 900, 900)["locked"] is False
    # lock expires once lock_seconds passed since newest failure
    fails = [now - 901 - i for i in range(8)]        # newest 901s ago, window 900 → none recent
    assert lock_state(fails, now, 8, 900, 900)["locked"] is False
    print("login_guard self-tests passed")
