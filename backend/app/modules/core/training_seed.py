"""Bundled PLATFORM-DEFAULT tour seeding (mig 720 Training Center).

The shipped walk-throughs live in `app/data/training_tours_seed.json` and are loaded into
`core.training_tour` / `core.training_tour_step` for the HOUSE org automatically on the house org's
`sync_tenant()` pass (SEED_VERSION 8) — so zero manual import is needed after mig 720 + deploy. A
super-admin POST /core/training/seed re-triggers it on demand.

WHY CODE AND NOT THE MIGRATION. Exactly the mig-715/support_seed precedent: the shipped WORDING is
content, and content should be correctable in a normal deploy rather than by writing another migration.
It also makes the seed idempotent in a way a re-run of the SQL file could never be — see below.

NEVER-CLOBBER SEMANTICS (the whole point): a tour is written ONLY when it is missing OR its existing
row's `updated_by` is NULL or 'seed' (i.e. it was itself seeded, never hand-edited). A tour someone has
edited in /admin/training is SKIPPED — re-seeding an improved pack never overwrites a tenant's work.
Every written row is stamped `updated_by='seed'`, `is_seed=true`.

TENANT ROWS ARE NEVER TOUCHED. This only ever writes the HOUSE org. A tenant that overrode a tour keeps
its override, because resolution (training.resolve_tours) prefers the tenant row for that slug.

DEGRADES GRACEFULLY: a missing bundle file or an un-run mig 720 is a silent no-op that never raises to
the caller (sync_tenant / the endpoint). It can never break a login.
"""
import json
import os
from datetime import datetime, timezone

from app.core.database import get_supabase

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "training_tours_seed.json")


def load_seed_tours(path: str = None) -> list:
    """Parse the bundled pack → its `tours` list. Best-effort: a missing/invalid file → [] (never raises)."""
    try:
        with open(path or _SEED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tours = data.get("tours") if isinstance(data, dict) else None
        return tours if isinstance(tours, list) else []
    except Exception:
        return []


def seed_training_tours(client=None, org_id: str = HOUSE_ORG, tours: list = None) -> dict:
    """Load the bundled platform-default tours into `org_id` (HOUSE), never clobbering an edited row.
    Returns {inserted, updated, skipped, steps, ok}. `tours` overrides the bundled file (tests).
    Try/except-guarded end to end: an un-run mig 720 or any DB error → a silent no-op ({ok: False})."""
    from app.modules.core.training import clean_tour, clean_step   # lazy: no import cycle at module load
    client = client or get_supabase()
    if tours is None:
        tours = load_seed_tours()
    if not tours:
        return {"inserted": 0, "updated": 0, "skipped": 0, "steps": 0, "ok": False}
    try:
        existing_rows = (client.schema("core").table("training_tour").select("id,slug,updated_by")
                         .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {"inserted": 0, "updated": 0, "skipped": 0, "steps": 0, "ok": False}   # mig 720 un-run
    existing = {r.get("slug"): r for r in existing_rows}
    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = skipped = step_rows = 0
    for raw in tours:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        t = clean_tour(raw)
        if not t["slug"] or not t["title"]:
            skipped += 1
            continue
        prior = existing.get(t["slug"])
        if prior is not None:
            ub = prior.get("updated_by")
            if ub is not None and str(ub) != "seed":
                skipped += 1          # hand-edited → leave it alone
                continue
        steps = [clean_step(s, i) for i, s in enumerate(raw.get("steps") or [], 1) if isinstance(s, dict)]
        steps = [s for s in steps if s["title"] and s["body"]]
        if not steps:
            skipped += 1
            continue
        row = {**t, "org_id": org_id, "is_seed": True, "updated_by": "seed", "updated_at": now}
        try:
            client.schema("core").table("training_tour").upsert(row, on_conflict="org_id,slug").execute()
            got = (client.schema("core").table("training_tour").select("id")
                   .eq("org_id", org_id).eq("slug", t["slug"]).limit(1).execute().data) or []
            if not got:
                skipped += 1
                continue
            tour_id = got[0]["id"]
            client.schema("core").table("training_tour_step").delete() \
                  .eq("org_id", org_id).eq("tour_id", tour_id).execute()
            client.schema("core").table("training_tour_step").insert(
                [{**s, "org_id": org_id, "tour_id": tour_id} for s in steps]).execute()
        except Exception:
            skipped += 1
            continue
        step_rows += len(steps)
        if prior is None:
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "steps": step_rows, "ok": True}
