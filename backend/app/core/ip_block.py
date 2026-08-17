"""Application-layer IP blocklist for incident containment (External Threat Defense Plan §1.1/§4.2).

A super-admin can block a malicious source IP instantly (no redeploy); the request path refuses it with
403. Defense-in-depth — the authoritative volumetric/edge block still belongs at a managed WAF/CDN.

  • CHEAP HOT PATH. The blocklist is cached in-process and refreshed at most every ~30s from
    core.ip_block (mig 860); the per-request check is a set lookup. Expired entries are filtered on
    refresh.
  • FAIL OPEN. Any load error leaves the previous snapshot in place (or empty); a blocklist fault must
    never take the site down. Adds/removes bust the cache so a block takes effect within the refresh
    window at worst, immediately in the process that made the change.
"""
import time

_cache = {"ips": frozenset(), "at": 0.0}
_TTL = 30.0


def _load():
    try:
        from app.core.database import get_supabase_admin
        from datetime import datetime, timezone
        rows = (get_supabase_admin().schema("core").table("ip_block")
                .select("ip,expires_at").limit(10000).execute().data) or []
        now = datetime.now(timezone.utc)
        active = set()
        for r in rows:
            ip = (r.get("ip") or "").strip()
            if not ip:
                continue
            exp = r.get("expires_at")
            if exp:
                try:
                    dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt <= now:
                        continue                       # expired → not active
                except Exception:
                    pass
            active.add(ip)
        _cache["ips"] = frozenset(active)
        _cache["at"] = time.time()
    except Exception:
        # keep the previous snapshot; just push the next retry out a little
        _cache["at"] = time.time()


def is_blocked(ip: str) -> bool:
    if not ip:
        return False
    if time.time() - _cache["at"] >= _TTL:
        _load()
    return ip in _cache["ips"]


def invalidate():
    """Force a reload on the next check (call after an add/remove so it takes effect immediately here)."""
    _cache["at"] = 0.0


def add(ip: str, reason: str = "", created_by: str = "", expires_at=None):
    from app.core.database import get_supabase_admin
    (get_supabase_admin().schema("core").table("ip_block").upsert({
        "ip": ip, "reason": (reason or None), "created_by": (created_by or None),
        "expires_at": expires_at,
    }, on_conflict="ip").execute())
    invalidate()


def remove(ip: str):
    from app.core.database import get_supabase_admin
    get_supabase_admin().schema("core").table("ip_block").delete().eq("ip", ip).execute()
    invalidate()


def listing():
    from app.core.database import get_supabase_admin
    return (get_supabase_admin().schema("core").table("ip_block")
            .select("*").order("created_at", desc=True).limit(1000).execute().data) or []
