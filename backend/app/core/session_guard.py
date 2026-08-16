"""Server-side session controls — idle timeout + absolute lifetime (Security Controls Spec §1, P0).

Authentication is delegated to Supabase (JWT), so until now a token was valid until Supabase's own
expiry: no idle timeout, no absolute cap, no way to say "this session has been alive too long." This
module adds both, keyed on the durable `session_id` claim Supabase carries across access-token
refreshes (so a refresh does NOT reset the clocks — the whole point).

Shape, matching the house style:
  • PURE CORE. `verdict(started_at, last_seen_at, now, idle_seconds, absolute_seconds)` decides
    'ok' | 'idle' | 'absolute' with an injected clock. Unit-tested below.
  • GATED. env SESSION_ENFORCE (default OFF), exactly like MULTI_TENANT_ENFORCE — this changes
    user-visible behaviour (it can end a session), so it ships off and an operator turns it on
    deliberately. Windows: SESSION_IDLE_MINUTES (default 30), SESSION_ABSOLUTE_HOURS (default 12).
  • CHEAP HOT PATH. An in-process cache holds (started_at, last_seen_at) per session; the DB row
    (core.session_activity, mig 858) is written write-throttled (~30s), so a burst of requests is one
    DB write, not one per request. A cache miss reads the row (or creates it).
  • FAIL OPEN. Any DB/parse error → the guard returns 'ok' (never strand the operator over a logging
    fault), same posture as the 2FA marker verifier. The TIMEOUTS themselves fail closed: a session
    past its window is ended.
  • BEST EFFORT WRITES. Every DB touch is wrapped and swallowed; a write fault never fails a request.

The middleware (tenant_middleware) calls `touch(session_id, ...)` on each authenticated request and, if
it returns a non-ok verdict, rejects with 401 `session_idle` / `session_expired`. `session_id` is read
from the already-verified JWT payload (see `session_id_from_token`); if the claim is absent it falls
back to the auth user id, so the control still applies (coarser: one clock per user, not per device).
"""
import os
import time
import json
import base64

# session_id -> {"started": epoch, "last_seen": epoch, "last_flush": epoch}
_cache: dict = {}
_FLUSH_GAP = 30.0            # seconds between DB writes for an active session
_MAX_CACHE = 50_000


def enforce() -> bool:
    return os.environ.get("SESSION_ENFORCE", "").lower() in ("1", "true", "yes")


def _idle_seconds() -> int:
    try:
        m = int(os.environ.get("SESSION_IDLE_MINUTES", "").strip() or 30)
        return (m if m > 0 else 30) * 60
    except (TypeError, ValueError):
        return 30 * 60


def _absolute_seconds() -> int:
    try:
        h = int(os.environ.get("SESSION_ABSOLUTE_HOURS", "").strip() or 12)
        return (h if h > 0 else 12) * 3600
    except (TypeError, ValueError):
        return 12 * 3600


def verdict(started_at: float, last_seen_at: float, now: float,
            idle_seconds: int, absolute_seconds: int) -> str:
    """PURE. 'absolute' if the session has lived past its absolute lifetime; 'idle' if it has been
    quiet longer than the idle window; else 'ok'. Absolute is checked first so a long-lived session
    ends for the right reason. Non-positive windows disable that check (belt-and-braces; env parse
    already floors them)."""
    if absolute_seconds > 0 and (now - started_at) >= absolute_seconds:
        return "absolute"
    if idle_seconds > 0 and (now - last_seen_at) >= idle_seconds:
        return "idle"
    return "ok"


def session_id_from_token(token: str, fallback_uid=None):
    """Read the `session_id` claim from an ALREADY-VERIFIED Supabase access token. The signature was
    verified upstream (get_supabase_admin().auth.get_user), so here we only base64-decode the payload
    segment to read a claim — no trust is placed in it beyond "which durable session is this."

    Falls back to the auth user id when the claim is absent (older tokens / non-Supabase), so the guard
    still functions with one clock per user. Returns None only if there is nothing to key on."""
    try:
        parts = (token or "").split(".")
        if len(parts) >= 2:
            seg = parts[1]
            seg += "=" * (-len(seg) % 4)                       # pad base64url
            claims = json.loads(base64.urlsafe_b64decode(seg.encode()).decode("utf-8"))
            sid = claims.get("session_id") or claims.get("sid")
            if sid:
                return str(sid)
    except Exception:
        pass
    return str(fallback_uid) if fallback_uid else None


# ── DB backing (best-effort) ────────────────────────────────────────────────────────────────────
def _load_row(session_id: str):
    try:
        from app.core.database import get_supabase_admin
        rows = (get_supabase_admin().schema("core").table("session_activity")
                .select("started_at,last_seen_at,ended_at").eq("session_id", session_id)
                .limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _parse_ts(v):
    """Best-effort ISO8601 → epoch seconds. Returns None on anything unparseable."""
    if not v:
        return None
    try:
        from datetime import datetime, timezone
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _upsert(session_id, auth_id, org_id, email, role, ip, started_epoch, seen_epoch, count):
    try:
        from datetime import datetime, timezone
        from app.core.database import get_supabase_admin
        payload = {
            "session_id": session_id,
            "auth_id": auth_id,
            "org_id": org_id,
            "actor_email": email,
            "actor_role": role,
            "last_ip": (ip or None),
            "started_at": datetime.fromtimestamp(started_epoch, timezone.utc).isoformat(),
            "last_seen_at": datetime.fromtimestamp(seen_epoch, timezone.utc).isoformat(),
            "request_count": int(count),
        }
        (get_supabase_admin().schema("core").table("session_activity")
         .upsert(payload, on_conflict="session_id").execute())
    except Exception:
        pass


def _mark_ended(session_id, reason):
    try:
        from datetime import datetime, timezone
        from app.core.database import get_supabase_admin
        (get_supabase_admin().schema("core").table("session_activity")
         .update({"ended_at": datetime.now(timezone.utc).isoformat(), "ended_reason": reason})
         .eq("session_id", session_id).execute())
    except Exception:
        pass


def touch(session_id, *, auth_id=None, org_id=None, email=None, role=None, ip=None):
    """Register activity on a session and return its verdict: 'ok' | 'idle' | 'absolute'.

    BLOCKING (does best-effort PostgREST reads/writes) — call via asyncio.to_thread from the ASGI path.
    Fail-open: any internal error yields 'ok'. Only touches the DB on a cache miss or a throttled flush,
    so the steady-state cost of an active session is a dict update.
    """
    if not session_id:
        return "ok"
    try:
        now = time.time()
        idle_s, abs_s = _idle_seconds(), _absolute_seconds()
        ent = _cache.get(session_id)
        if ent is None:
            row = _load_row(session_id)
            if row and row.get("ended_at"):
                # a previously-closed session that is still presenting a token: stay closed until a
                # fresh session_id appears. Report the reason the client last saw.
                return "idle"
            started = _parse_ts(row and row.get("started_at")) or now
            last_seen = _parse_ts(row and row.get("last_seen_at")) or now
            ent = {"started": started, "last_seen": last_seen, "last_flush": 0.0, "count": 0}
            _cache[session_id] = ent

        v = verdict(ent["started"], ent["last_seen"], now, idle_s, abs_s)
        if v != "ok":
            _mark_ended(session_id, v)
            _cache.pop(session_id, None)
            return v

        ent["last_seen"] = now
        ent["count"] += 1
        if now - ent["last_flush"] >= _FLUSH_GAP:
            ent["last_flush"] = now
            _upsert(session_id, auth_id, org_id, email, role, ip,
                    ent["started"], now, ent["count"])
        # opportunistic cache cap
        if len(_cache) > _MAX_CACHE:
            for k in [k for k, e in list(_cache.items()) if now - e["last_seen"] >= abs_s]:
                _cache.pop(k, None)
        return "ok"
    except Exception:
        return "ok"


if __name__ == "__main__":
    # Pure-core self-tests.
    assert verdict(0, 0, 100, 3600, 86400) == "ok"
    assert verdict(0, 0, 3600, 3600, 86400) == "idle"          # exactly at idle window
    assert verdict(0, 3000, 3300, 3600, 86400) == "ok"          # active within idle window
    assert verdict(0, 3000, 86400, 3600, 86400) == "absolute"   # past absolute even if recently active
    assert verdict(0, 0, 100, 0, 0) == "ok"                     # both checks disabled
    # session_id extraction from a JWT-shaped token (no signature needed).
    payload = base64.urlsafe_b64encode(json.dumps({"session_id": "sess-xyz"}).encode()).decode().rstrip("=")
    tok = "h." + payload + ".sig"
    assert session_id_from_token(tok) == "sess-xyz"
    assert session_id_from_token("not-a-jwt", fallback_uid="u-1") == "u-1"
    assert session_id_from_token("", fallback_uid=None) is None
    print("session_guard self-tests passed")
