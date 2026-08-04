"""Bundled PLATFORM-WIDE release-note seeding (mig 721 What's New).

The shipped entries live in `app/data/release_notes_seed.json` and are loaded into `core.release_note`
for the HOUSE org on the house org's `sync_tenant()` pass (SEED_VERSION 9), so every tenant's admins see
them with zero manual import. A super-admin POST /core/whats-new/seed re-triggers it.

NEVER-CLOBBER: an entry is written only when it is missing OR its existing row's `updated_by` is NULL or
'seed'. A hand-edited entry is skipped. Written rows are stamped `updated_by='seed'`, `is_seed=true`.
HOUSE org only — a tenant's own entries are never touched.

DEGRADES GRACEFULLY: a missing bundle file or an un-run mig 721 is a silent no-op that never raises to
the caller (sync_tenant / the endpoint). It can never break a login.
"""
import json
import os
from datetime import datetime, timezone

from app.core.database import get_supabase

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "release_notes_seed.json")


def load_seed_entries(path: str = None) -> list:
    """Parse the bundled pack → its `entries` list. Best-effort: missing/invalid → [] (never raises)."""
    try:
        with open(path or _SEED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("entries") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def seed_release_notes(client=None, org_id: str = HOUSE_ORG, entries: list = None) -> dict:
    """Load the bundled platform entries into `org_id` (HOUSE), never clobbering a hand-edited row.
    Returns {inserted, updated, skipped, ok}. `entries` overrides the bundled file (tests)."""
    from app.modules.core.whats_new import clean_entry   # lazy: no import cycle at module load
    client = client or get_supabase()
    if entries is None:
        entries = load_seed_entries()
    if not entries:
        return {"inserted": 0, "updated": 0, "skipped": 0, "ok": False}
    try:
        existing_rows = (client.schema("core").table("release_note").select("id,slug,updated_by")
                         .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {"inserted": 0, "updated": 0, "skipped": 0, "ok": False}   # mig 721 un-run
    existing = {r.get("slug"): r for r in existing_rows}
    now = datetime.now(timezone.utc).isoformat()
    to_write, inserted, updated, skipped = [], 0, 0, 0
    for raw in entries:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        e = clean_entry(raw)
        if not e["slug"] or not e["title"]:
            skipped += 1
            continue
        prior = existing.get(e["slug"])
        if prior is not None:
            ub = prior.get("updated_by")
            if ub is not None and str(ub) != "seed":
                skipped += 1          # hand-edited → leave it alone
                continue
            updated += 1
        else:
            inserted += 1
        to_write.append({**e, "org_id": org_id, "is_seed": True,
                         "updated_by": "seed", "updated_at": now})
    if not to_write:
        return {"inserted": 0, "updated": 0, "skipped": skipped, "ok": True}
    try:
        client.schema("core").table("release_note").upsert(
            to_write, on_conflict="org_id,slug").execute()
    except Exception:
        return {"inserted": 0, "updated": 0, "skipped": skipped + len(to_write), "ok": False}
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "ok": True}
