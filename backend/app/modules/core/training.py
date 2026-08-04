"""TRAINING CENTER (mig 720) — guided in-app walk-throughs, as DATA.

OWNER DIRECTIVE 2026-08-04 (in chat, verbatim): "need to create simulation training videos for all
modules to walk the users through".

WHAT THIS IS (Phase 1). A tour is an ordered list of steps; each step names a page, an anchor on that
page, and a short card of plain English. The frontend engine (src/lib/tours.ts + TourRunner.tsx) walks
the user through the REAL page on their OWN tenant's data: it spotlights the control, explains it, and
steps forward. That is the "simulation" — a click-through the user drives, not a video they watch.

PHASE 2 (scaffold only — no recording infrastructure ships in this package). The SAME rows are the
video script: every step already carries `narration` (the voice-over line) and `action_hint` (what the
recorder does). GET /core/training/script/{slug} renders a tour as a storyboard + a Playwright outline
so a real screen recording can be produced later without re-authoring one word of content.

RULE TWO (SAP-configurable): nothing about a tour is code. A tenant edits the shipped wording, adds its
own tours for its own process, reorders or unpublishes — all from /admin/training, no deploy.

RULE ONE (multi-tenant). org_id is NOT NULL on both tables (contract §2). PLATFORM DEFAULT tours are
owned by the HOUSE org and read by every tenant — the pattern already in production for
core.support_doc (715), core.failure_kind_doc (716), core.token_rates (718).
    READ   rows WHERE org_id IN (HOUSE, <caller tenant>); a TENANT row with the same slug WINS, so a
           tenant customises a shipped tour by saving its own row under that slug. The platform row is
           never mutated and keeps flowing to every other tenant.
    WRITE  a tenant admin may write ONLY org_id = its own tenant. Only a SUPER-ADMIN may write the
           HOUSE (platform-default) rows. The org is resolved from the caller's verified membership —
           never from the body — so a forged org_id in a payload cannot land a row in another tenant.

DEGRADES GRACEFULLY: mig 720 un-run ⇒ every read returns {"tours": [], "ready": false, "hint": …} and
every write returns an honest 500 naming the migration. The Training Center shows an empty state, the
"Walk me through" affordance never renders, and NOTHING else in the app is affected. No page anywhere
else reads these tables.

MOUNTING: this sub-router is mounted ONTO core/router.py's router (which already carries "/core"), so
main.py — a SHARED file — needs no change. Final paths: /api/v1/core/training/*.

NOT MONEY-TOUCHING: reads/writes only core.training_tour[_step]. The engine highlights and explains;
it never clicks a control for the user and never submits a form.
"""
import json
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase

# NO prefix of its own beyond /training: mounted onto the core router (see module docstring).
router = APIRouter(prefix="/training", tags=["Core / Training"])

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
ORG_ID = HOUSE_ORG
MIG_HINT = "Run migration 720 (core.training_tour + core.training_tour_step) to enable the Training Center."

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "training_tours_seed.json")

# Columns persisted per tour / per step (also the accepted write contract — anything else is dropped).
_TOUR_FIELDS = ("slug", "title", "module", "description", "audience", "start_href",
                "est_minutes", "sort_order", "is_published")
_STEP_FIELDS = ("step_order", "page_href", "target", "target_fragile", "placement",
                "title", "body", "narration", "action_hint")

_AUDIENCES = ("all", "rep", "manager", "admin")
_PLACEMENTS = ("auto", "top", "bottom", "left", "right")


def sb():
    return get_supabase()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── PURE helpers (unit-proven in harness_training_center.py) ──────────────────────────────────────
def slugify(text: str) -> str:
    """Stable, url-safe slug for a human-typed tour title. Empty input → ''."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return s[:80]


def resolve_tours(rows, tenant_org, house_org=HOUSE_ORG):
    """PURE. From tour rows spanning (house ∪ tenant), return ONE row per slug: a TENANT row wins over
    the house row with the same slug. Unpublished rows are dropped AFTER the override is applied, so a
    tenant can hide a platform tour by saving its own unpublished row under that slug. Sorted by
    (sort_order, title)."""
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
    return sorted(out, key=lambda r: ((r.get("sort_order") if r.get("sort_order") is not None else 100),
                                      str(r.get("title") or "")))


def tour_matches_path(tour, steps, path):
    """PURE. True when `path` is the page a tour starts on, or any page it visits — the rule behind the
    "Walk me through" list in the help panel. Boundary-matched prefix (no sloppy startswith), so
    /closing matches /closing and /closing/submit but never /closingx."""
    p = (str(path or "").split("?")[0].split("#")[0]).rstrip("/") or "/"
    hrefs = [tour.get("start_href")] + [s.get("page_href") for s in (steps or [])]
    for h in hrefs:
        h = (str(h or "").split("?")[0]).rstrip("/")
        if not h:
            continue
        if p == h or p.startswith(h + "/") or h.startswith(p + "/"):
            return True
    return False


def clean_step(raw, order):
    """PURE. Normalize ONE inbound step to the storable shape. Unknown keys dropped; enums clamped;
    title/body coerced to strings (they are NOT NULL in the table)."""
    s = {k: raw.get(k) for k in _STEP_FIELDS if k in raw}
    s["step_order"] = int(order)
    s["title"] = str(s.get("title") or "").strip()[:300]
    s["body"] = str(s.get("body") or "").strip()[:4000]
    s["target"] = (str(s["target"]).strip()[:400] or None) if s.get("target") else None
    s["page_href"] = (str(s["page_href"]).strip()[:300] or None) if s.get("page_href") else None
    s["placement"] = s.get("placement") if s.get("placement") in _PLACEMENTS else "auto"
    s["target_fragile"] = bool(s.get("target_fragile"))
    for k in ("narration", "action_hint"):
        s[k] = (str(s[k])[:4000] if s.get(k) else None)
    return s


def clean_tour(raw):
    """PURE. Normalize ONE inbound tour header. org_id is deliberately NOT read from the body."""
    t = {k: raw.get(k) for k in _TOUR_FIELDS if k in raw}
    t["slug"] = slugify(t.get("slug") or raw.get("title"))
    t["title"] = str(t.get("title") or "").strip()[:200]
    t["module"] = (str(t["module"]).strip()[:60] or None) if t.get("module") else None
    t["description"] = (str(t["description"])[:1000] if t.get("description") else None)
    t["audience"] = t.get("audience") if t.get("audience") in _AUDIENCES else "all"
    t["start_href"] = (str(t["start_href"]).strip()[:300] or None) if t.get("start_href") else None
    try:
        t["sort_order"] = int(t.get("sort_order")) if t.get("sort_order") is not None else 100
    except Exception:
        t["sort_order"] = 100
    try:
        t["est_minutes"] = float(t["est_minutes"]) if t.get("est_minutes") not in (None, "") else None
    except Exception:
        t["est_minutes"] = None
    t["is_published"] = bool(t.get("is_published", True))
    return t


def build_script(tour, steps):
    """PURE. PHASE-2 SCAFFOLD: render one tour as a video/recording script.
    Returns {slug, title, storyboard[], narration_text, playwright[]}:
      storyboard   — one entry per step: scene no., page, on-screen card, narration, camera action.
      narration_text — the whole voice-over as one block, ready to hand to a person or a TTS engine.
      playwright   — an OUTLINE (comment lines + goto/hover calls) for whoever later automates the
                     recording. It is intentionally NOT executable code: this package ships no
                     recording infrastructure and must not pretend to.
    """
    story, narr, pw, last_page = [], [], [], None
    for i, s in enumerate(steps or [], 1):
        page = s.get("page_href") or last_page
        story.append({
            "scene": i,
            "page": page,
            "anchor": s.get("target") or "(no anchor — centered card)",
            "on_screen_title": s.get("title"),
            "on_screen_body": s.get("body"),
            "narration": s.get("narration") or s.get("body"),
            "camera_action": s.get("action_hint") or "Hold on the highlighted element.",
        })
        narr.append(str(s.get("narration") or s.get("body") or "").strip())
        if page and page != last_page:
            pw.append(f"await page.goto(BASE + {json.dumps(page)})")
            last_page = page
        tgt = s.get("target")
        if tgt:
            pw.append(f"# scene {i}: highlight {tgt}")
            pw.append(f"await highlight(page, {json.dumps(tgt)})")
        else:
            pw.append(f"# scene {i}: centered card, no anchor")
        pw.append(f"# narrate: {(s.get('narration') or s.get('body') or '')[:120]}")
    return {"slug": tour.get("slug"), "title": tour.get("title"), "module": tour.get("module"),
            "scenes": len(story), "storyboard": story,
            "narration_text": "\n\n".join([n for n in narr if n]),
            "playwright": pw}


# ── Caller / gates ────────────────────────────────────────────────────────────────────────────────
def _caller(authorization, active_org):
    """{org_id, role, super_admin, perms} for the verified caller, or None. Lazy import → no cycle."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        return _resolve_caller(sb(), uid, active_org)
    except Exception:
        return None


def can_edit_tours(caller):
    """PURE. Who may author tours. super_admin always; otherwise the per-setting grant ('training')
    resolved by core._can_edit_setting (explicit role grant → scope-all admin). Mirrors every other
    admin-editable surface; no new gate shape is invented here."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    try:
        from app.modules.core.router import _can_edit_setting
        return bool(_can_edit_setting(caller, "training"))
    except Exception:
        return False


def _write_org(caller, requested_org):
    """The org a WRITE lands in. A super-admin may target the HOUSE org (the platform defaults every
    tenant sees) or act as a tenant; anyone else is pinned to their OWN membership org regardless of
    what the request said. The body's org_id is never trusted."""
    if caller and caller.get("super_admin"):
        return (str(requested_org or "").strip() or caller.get("org_id") or HOUSE_ORG)
    return (caller or {}).get("org_id") or HOUSE_ORG


def _read_orgs(caller):
    """The org set a READ spans: the caller's tenant ∪ HOUSE (the platform defaults). A caller with no
    resolvable tenant sees the platform defaults only — never another tenant's rows."""
    org = (caller or {}).get("org_id") or HOUSE_ORG
    return list({org, HOUSE_ORG}), org


def _fetch(orgs):
    """(tours, steps_by_tour_id) for the given org set. ([], {}) if mig 720 has not been run."""
    client = sb()
    try:
        tours = (client.schema("core").table("training_tour").select("*")
                 .in_("org_id", orgs).execute().data) or []
    except Exception:
        return None, None
    ids = [t["id"] for t in tours if t.get("id")]
    steps = []
    if ids:
        try:
            steps = (client.schema("core").table("training_tour_step").select("*")
                     .in_("org_id", orgs).in_("tour_id", ids).order("step_order").execute().data) or []
        except Exception:
            steps = []
    by_tour = {}
    for s in steps:
        by_tour.setdefault(s.get("tour_id"), []).append(s)
    for k in by_tour:
        by_tour[k].sort(key=lambda s: (s.get("step_order") or 0))
    return tours, by_tour


# ── Endpoints ─────────────────────────────────────────────────────────────────────────────────────
@router.get("/tours")
async def list_tours(path: str = "", module: str = "", authorization: str = Header(default=""),
                     x_active_org: str = Header(default="")):
    """Every tour this caller can take: the platform defaults ∪ this tenant's own, with a tenant row
    overriding the platform row of the same slug. Optional `path` filters to tours that touch that page
    (what the help panel's "Walk me through" list uses); optional `module` filters by module key.
    FAIL-SILENT: mig 720 un-run / any error → an empty list with a hint, never a 500."""
    caller = _caller(authorization, x_active_org)
    orgs, tenant_org = _read_orgs(caller)
    tours, by_tour = _fetch(orgs)
    if tours is None:
        return {"tours": [], "ready": False, "hint": MIG_HINT, "can_edit": False}
    resolved = resolve_tours(tours, tenant_org)
    out = []
    for t in resolved:
        steps = by_tour.get(t.get("id")) or []
        if module and (t.get("module") or "") != module:
            continue
        if path and not tour_matches_path(t, steps, path):
            continue
        out.append({**t, "step_count": len(steps),
                    "is_tenant_override": t.get("org_id") == tenant_org and tenant_org != HOUSE_ORG})
    return {"tours": out, "ready": True, "org_id": tenant_org,
            "can_edit": can_edit_tours(caller)}


@router.get("/tours/{slug}")
async def get_tour(slug: str, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """ONE resolved tour with its ordered steps — what the tour engine actually runs."""
    caller = _caller(authorization, x_active_org)
    orgs, tenant_org = _read_orgs(caller)
    tours, by_tour = _fetch(orgs)
    if tours is None:
        raise HTTPException(503, MIG_HINT)
    match = [t for t in resolve_tours(tours, tenant_org) if (t.get("slug") or "") == slug]
    if not match:
        raise HTTPException(404, "That walk-through isn't available (it may have been unpublished).")
    t = match[0]
    steps = [{k: s.get(k) for k in ("id",) + _STEP_FIELDS} for s in (by_tour.get(t.get("id")) or [])]
    return {"tour": t, "steps": steps,
            "is_tenant_override": t.get("org_id") == tenant_org and tenant_org != HOUSE_ORG}


@router.post("/tours")
async def save_tour(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Create or replace ONE tour AND its steps (steps are replace-all: the payload IS the tour).
    A tenant admin always writes its own org; only a super-admin can write the platform defaults by
    passing ?org_id=<house>. Editing a PLATFORM tour as a tenant admin creates a TENANT COPY under the
    same slug — the platform row is untouched and other tenants keep the original."""
    caller = _caller(authorization, x_active_org)
    if not can_edit_tours(caller):
        raise HTTPException(403, "You don't have permission to edit training walk-throughs.")
    org = _write_org(caller, org_id)
    t = clean_tour(body)
    if not t["slug"] or not t["title"]:
        raise HTTPException(422, "A walk-through needs a title.")
    raw_steps = body.get("steps")
    if not isinstance(raw_steps, list):
        raise HTTPException(422, "steps[] is required (a walk-through with no steps is not a walk-through).")
    steps = [clean_step(s, i) for i, s in enumerate(raw_steps, 1) if isinstance(s, dict)]
    steps = [s for s in steps if s["title"] and s["body"]]
    if not steps:
        raise HTTPException(422, "Every step needs a title and a body.")
    client = sb()
    row = {**t, "org_id": org, "is_seed": False,
           "updated_by": (caller.get("role") or "admin"), "updated_at": _now_iso()}
    try:
        client.schema("core").table("training_tour").upsert(row, on_conflict="org_id,slug").execute()
        got = (client.schema("core").table("training_tour").select("id")
               .eq("org_id", org).eq("slug", t["slug"]).limit(1).execute().data) or []
        tour_id = got[0]["id"]
        # Replace-all steps: delete then insert, both org-scoped on the write itself.
        client.schema("core").table("training_tour_step").delete() \
              .eq("org_id", org).eq("tour_id", tour_id).execute()
        client.schema("core").table("training_tour_step").insert(
            [{**s, "org_id": org, "tour_id": tour_id} for s in steps]).execute()
    except Exception as e:
        raise HTTPException(500, f"Could not save the walk-through ({str(e)[:160]}). {MIG_HINT}")
    return {"ok": True, "slug": t["slug"], "org_id": org, "steps": len(steps),
            "is_platform_default": org == HOUSE_ORG}


@router.delete("/tours/{tour_id}")
async def delete_tour(tour_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """Delete ONE tour (steps cascade). Org-scoped on the DELETE, so a tenant admin can only ever
    remove its OWN row — deleting a tenant override simply restores the platform default."""
    caller = _caller(authorization, x_active_org)
    if not can_edit_tours(caller):
        raise HTTPException(403, "You don't have permission to edit training walk-throughs.")
    org = _write_org(caller, org_id)
    try:
        sb().schema("core").table("training_tour").delete().eq("org_id", org).eq("id", tour_id).execute()
    except Exception as e:
        raise HTTPException(500, f"Could not delete the walk-through ({str(e)[:160]}). {MIG_HINT}")
    return {"deleted": True, "id": tour_id, "org_id": org}


@router.get("/scripts")
async def list_scripts(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """PHASE-2 SCAFFOLD: every resolved tour rendered as a recording script (storyboard + narration +
    a Playwright outline). Edit-gated, because narration/action hints are production notes, not user
    help. This endpoint produces the SOURCE for the videos; it records nothing."""
    caller = _caller(authorization, x_active_org)
    if not can_edit_tours(caller):
        raise HTTPException(403, "The recording scripts are for administrators.")
    orgs, tenant_org = _read_orgs(caller)
    tours, by_tour = _fetch(orgs)
    if tours is None:
        return {"scripts": [], "ready": False, "hint": MIG_HINT}
    scripts = [build_script(t, by_tour.get(t.get("id")) or []) for t in resolve_tours(tours, tenant_org)]
    return {"scripts": scripts, "ready": True, "org_id": tenant_org}


@router.get("/script/{slug}")
async def get_script(slug: str, authorization: str = Header(default=""),
                     x_active_org: str = Header(default="")):
    """PHASE-2 SCAFFOLD: ONE tour's recording script."""
    caller = _caller(authorization, x_active_org)
    if not can_edit_tours(caller):
        raise HTTPException(403, "The recording scripts are for administrators.")
    orgs, tenant_org = _read_orgs(caller)
    tours, by_tour = _fetch(orgs)
    if tours is None:
        raise HTTPException(503, MIG_HINT)
    match = [t for t in resolve_tours(tours, tenant_org) if (t.get("slug") or "") == slug]
    if not match:
        raise HTTPException(404, "No such walk-through.")
    return build_script(match[0], by_tour.get(match[0].get("id")) or [])


@router.post("/seed")
async def reseed(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Re-run the BUNDLED platform-default tour seed into the HOUSE org (super-admin only). This is the
    same never-clobber load that runs automatically on the house org's sync_tenant pass — a tour a human
    has edited (updated_by not NULL/'seed') is skipped. Zero manual steps are needed after mig 720 +
    deploy; this exists so the shipped wording can be refreshed after editing the bundle."""
    caller = _caller(authorization, x_active_org)
    if not (caller and caller.get("super_admin")):
        raise HTTPException(403, "Re-seeding the platform walk-throughs is restricted to super-admins.")
    from app.modules.core.training_seed import seed_training_tours
    return seed_training_tours(sb(), HOUSE_ORG)
