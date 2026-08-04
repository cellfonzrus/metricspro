"""WHAT'S NEW — new-features + improvements feed for ADMIN STAFF (mig 721).

OWNER DIRECTIVE 2026-08-04 (in chat, verbatim): "like we have the warnings for the admin who logs in,
there should be 2 more areas new features and improvements and keep them logged somewhere only for admin
staff."

The admin-attention popup (mig 717) tells an administrator what is BROKEN. This is the other half: what
is NEW and what got BETTER, on the same surface, behind the same gate, as data instead of a chat message
that scrolls away — plus a permanent, filterable, exportable log at /admin/whats-new.

GATE. Deliberately the SAME gate as the warnings: import_health.can_view_attention (super-admin, an
explicit /admin/import-health page grant, the `admin` module, or company-wide scope). One gate, not a
second one that can drift — a rep never sees any of this. WRITING additionally requires the per-setting
grant ('whats_new') resolved by core._can_edit_setting, and only a SUPER-ADMIN may write the
PLATFORM-WIDE entries every tenant reads.

RULE ONE (multi-tenant). org_id NOT NULL (contract §2). HOUSE-org rows are the platform-wide entries;
a tenant row is that tenant's own. read = .in_("org_id", [tenant, house]); write = the caller's verified
membership org, NEVER the request body. Identical to core.support_doc / core.training_tour.

SEEN/UNSEEN is a per-user localStorage watermark in v1 (src/lib/whats-new.ts) — the server just returns
entries with their released_at, and the client counts what is newer than the watermark. Upgrade path is
documented in mig 721.

INGEST (POST /whats-new/ingest) is the door a future ship process uses to append an entry automatically.
DUAL-AUTH, default DENY: a valid `x-release-secret` header matching env RELEASE_NOTE_SECRET, OR a
verified super-admin JWT. RELEASE_NOTE_SECRET defaults EMPTY = the secret door is CLOSED. Note that the
secret-only (tokenless) path additionally needs the path allowlisted in tenant_middleware — a SHARED-file
one-liner deliberately NOT applied in this package; it is filed as an operator action, and until it is
applied the endpoint is reachable by a super-admin JWT only. Nothing depends on it.

DEGRADES GRACEFULLY: mig 721 un-run ⇒ empty payload + hint on every route; the popup renders only its
existing Warnings tab, whose semantics are untouched.

MOUNTING: mounted ONTO core/router.py's router, so main.py (SHARED) needs no change. Paths:
/api/v1/core/whats-new*.

NOT MONEY-TOUCHING.
"""
import hmac
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase

router = APIRouter(prefix="/whats-new", tags=["Core / What's new"])

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
ORG_ID = HOUSE_ORG
MIG_HINT = "Run migration 721 (core.release_note) to enable the What's New log."

CATEGORIES = ("new_feature", "improvement", "fix")
STATUSES = ("shipped", "in_progress")
_FIELDS = ("slug", "category", "module", "title", "body", "status", "deep_link",
           "released_at", "is_published")


def sb():
    return get_supabase()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── PURE helpers (unit-proven in harness_whats_new.py) ────────────────────────────────────────────
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")[:80]


def clean_entry(raw: dict) -> dict:
    """PURE. Normalize ONE inbound entry. org_id is deliberately NOT read from the body (RULE ONE)."""
    e = {k: raw.get(k) for k in _FIELDS if k in raw}
    e["slug"] = slugify(e.get("slug") or raw.get("title"))
    e["title"] = str(e.get("title") or "").strip()[:300]
    e["body"] = (str(e["body"]).strip()[:2000] if e.get("body") else None)
    e["category"] = e.get("category") if e.get("category") in CATEGORIES else "new_feature"
    e["status"] = e.get("status") if e.get("status") in STATUSES else "shipped"
    e["module"] = (str(e["module"]).strip()[:60] or None) if e.get("module") else None
    e["deep_link"] = (str(e["deep_link"]).strip()[:300] or None) if e.get("deep_link") else None
    ra = str(e.get("released_at") or "").strip()
    e["released_at"] = ra[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", ra) else datetime.now(timezone.utc).date().isoformat()
    e["is_published"] = bool(e.get("is_published", True))
    return e


def resolve_entries(rows, tenant_org, house_org=HOUSE_ORG):
    """PURE. Platform-wide ∪ this tenant's own, a TENANT row of the same slug winning; unpublished
    dropped; newest first (released_at desc, then title) so the feed reads like a changelog."""
    by_slug = {}
    for r in (rows or []):
        slug = (r.get("slug") or "").strip()
        if not slug:
            continue
        cur = by_slug.get(slug)
        is_tenant = (r.get("org_id") == tenant_org and tenant_org != house_org)
        if cur is None or (is_tenant and not cur[0]):
            by_slug[slug] = (is_tenant, r)
    out = [r for _, r in by_slug.values() if r.get("is_published", True)]
    return sorted(out, key=lambda r: (str(r.get("released_at") or ""), str(r.get("title") or "")),
                  reverse=True)


def unseen(entries, since):
    """PURE. Entries released strictly AFTER the caller's watermark. No watermark (first ever look) ⇒
    everything is unseen, which is the correct first-run behaviour for a changelog."""
    s = str(since or "").strip()[:10]
    if not s:
        return list(entries)
    return [e for e in entries if str(e.get("released_at") or "") > s]


def counts_by_category(entries):
    """PURE. {new_feature: n, improvement: n, fix: n, total: n} — drives the popup's tab badges."""
    out = {c: 0 for c in CATEGORIES}
    for e in entries:
        c = e.get("category")
        if c in out:
            out[c] += 1
    out["total"] = sum(out[c] for c in CATEGORIES)
    return out


# ── Caller / gates ────────────────────────────────────────────────────────────────────────────────
def _caller(authorization, active_org):
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        return _resolve_caller(sb(), uid, active_org)
    except Exception:
        return None


def can_view(caller):
    """PURE. THE SAME gate as the login warnings — reused, never re-implemented."""
    try:
        from app.modules.core.import_health import can_view_attention
        return bool(can_view_attention(caller))
    except Exception:
        return bool(caller and caller.get("super_admin"))


def can_edit(caller):
    """PURE. Who may write an entry: super_admin always, else the per-setting grant."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    if not can_view(caller):
        return False
    try:
        from app.modules.core.router import _can_edit_setting
        return bool(_can_edit_setting(caller, "whats_new"))
    except Exception:
        return False


def _write_org(caller, requested_org):
    """A super-admin may write the PLATFORM-WIDE (house) entries or act as a tenant; anyone else is
    pinned to their own membership org. The body never decides this."""
    if caller and caller.get("super_admin"):
        return (str(requested_org or "").strip() or caller.get("org_id") or HOUSE_ORG)
    return (caller or {}).get("org_id") or HOUSE_ORG


def _read_orgs(caller):
    org = (caller or {}).get("org_id") or HOUSE_ORG
    return list({org, HOUSE_ORG}), org


def _fetch(orgs):
    try:
        return (sb().schema("core").table("release_note").select("*")
                .in_("org_id", orgs).execute().data) or []
    except Exception:
        return None


def _secret_ok(header_value: str) -> bool:
    """Constant-time compare against env RELEASE_NOTE_SECRET. EMPTY env ⇒ always False (door closed)."""
    want = os.environ.get("RELEASE_NOTE_SECRET", "")
    got = str(header_value or "")
    if not want or not got:
        return False
    return hmac.compare_digest(want, got)


# ── Endpoints ─────────────────────────────────────────────────────────────────────────────────────
@router.get("")
def list_notes(since: str = "", category: str = "", module: str = "",
                     from_date: str = "", to_date: str = "",
                     authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The What's New feed for admin staff. `since` (a YYYY-MM-DD watermark) additionally returns the
    UNSEEN slice + its per-category counts, which is what the login popup badges. FAIL-SILENT: an un-run
    migration returns an empty payload with a hint, never a 500."""
    caller = _caller(authorization, x_active_org)
    if not can_view(caller):
        raise HTTPException(403, "What's New is for administrators.")
    orgs, tenant_org = _read_orgs(caller)
    rows = _fetch(orgs)
    if rows is None:
        return {"entries": [], "unseen": [], "counts": counts_by_category([]), "unseen_counts": counts_by_category([]),
                "ready": False, "hint": MIG_HINT, "can_edit": False}
    entries = resolve_entries(rows, tenant_org)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    if module:
        entries = [e for e in entries if (e.get("module") or "") == module]
    if from_date:
        entries = [e for e in entries if str(e.get("released_at") or "") >= from_date[:10]]
    if to_date:
        entries = [e for e in entries if str(e.get("released_at") or "") <= to_date[:10]]
    un = unseen(entries, since)
    return {"entries": entries, "unseen": un,
            "counts": counts_by_category(entries), "unseen_counts": counts_by_category(un),
            "ready": True, "org_id": tenant_org, "can_edit": can_edit(caller),
            "is_super": bool(caller and caller.get("super_admin"))}


@router.post("")
def save_note(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Create or update ONE entry. A super-admin passing ?org_id=<house> writes a PLATFORM-WIDE entry
    every tenant's admins will see; anyone else writes their own organisation's entry."""
    caller = _caller(authorization, x_active_org)
    if not can_edit(caller):
        raise HTTPException(403, "You don't have permission to post updates.")
    e = clean_entry(body)
    if not e["slug"] or not e["title"]:
        raise HTTPException(422, "An update needs a title.")
    org = _write_org(caller, org_id)
    row = {**e, "org_id": org, "is_seed": False,
           "updated_by": (caller.get("role") or "admin"), "updated_at": _now_iso()}
    try:
        sb().schema("core").table("release_note").upsert(row, on_conflict="org_id,slug").execute()
    except Exception as ex:
        raise HTTPException(500, f"Could not save the update ({str(ex)[:160]}). {MIG_HINT}")
    return {"ok": True, "slug": e["slug"], "org_id": org, "is_platform_wide": org == HOUSE_ORG}


@router.delete("/{note_id}")
def delete_note(note_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """Delete ONE entry — org-scoped on the DELETE, so a tenant admin can only remove its own."""
    caller = _caller(authorization, x_active_org)
    if not can_edit(caller):
        raise HTTPException(403, "You don't have permission to remove updates.")
    org = _write_org(caller, org_id)
    try:
        sb().schema("core").table("release_note").delete().eq("org_id", org).eq("id", note_id).execute()
    except Exception as ex:
        raise HTTPException(500, f"Could not remove the update ({str(ex)[:160]}). {MIG_HINT}")
    return {"deleted": True, "id": note_id, "org_id": org}


@router.post("/ingest")
def ingest(body: dict, x_release_secret: str = Header(default=""),
                 authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """INTERNAL receiver so a future ship process can append entries automatically. DUAL-AUTH, default
    DENY: a valid x-release-secret (env RELEASE_NOTE_SECRET, EMPTY = door closed) or a verified
    SUPER-ADMIN JWT. Always writes PLATFORM-WIDE (house) entries — that is the only thing a ship process
    should ever be able to publish, and it can never target a tenant. Body: {entries:[…]} or one entry."""
    by_secret = _secret_ok(x_release_secret)
    caller = None if by_secret else _caller(authorization, x_active_org)
    if not by_secret and not (caller and caller.get("super_admin")):
        raise HTTPException(403, "Not authorised to publish platform updates.")
    raw = body.get("entries") if isinstance(body.get("entries"), list) else [body]
    rows, skipped = [], 0
    for r in raw:
        if not isinstance(r, dict):
            skipped += 1
            continue
        e = clean_entry(r)
        if not e["slug"] or not e["title"]:
            skipped += 1
            continue
        rows.append({**e, "org_id": HOUSE_ORG, "is_seed": False,
                     "updated_by": "ship-process" if by_secret else (caller.get("role") or "admin"),
                     "updated_at": _now_iso()})
    if not rows:
        raise HTTPException(422, "no valid entries (each needs a title)")
    try:
        sb().schema("core").table("release_note").upsert(rows, on_conflict="org_id,slug").execute()
    except Exception as ex:
        raise HTTPException(500, f"Could not publish ({str(ex)[:160]}). {MIG_HINT}")
    return {"ok": True, "published": len(rows), "skipped": skipped,
            "via": "secret" if by_secret else "super_admin"}


@router.post("/seed")
def reseed(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Re-run the BUNDLED platform entry seed into the HOUSE org (super-admin only). Never clobbers a
    hand-edited entry. Same load that runs automatically on the house org's sync_tenant pass."""
    caller = _caller(authorization, x_active_org)
    if not (caller and caller.get("super_admin")):
        raise HTTPException(403, "Re-seeding platform updates is restricted to super-admins.")
    from app.modules.core.whats_new_seed import seed_release_notes
    return seed_release_notes(sb(), HOUSE_ORG)
