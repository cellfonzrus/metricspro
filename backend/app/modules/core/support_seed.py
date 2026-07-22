"""Bundled help-doc seeding (mig 715 tech-support platform).

The six domain content packs ship WITH the deploy as `app/data/support_docs_seed.json`
({"domain": "all", "pages": [...]} — the /core/support-docs/import contract shape) and are loaded into
`core.support_doc` for the HOUSE org automatically on the house org's `sync_tenant` pass (SEED_VERSION 6),
so zero manual import is needed after mig 715 + deploy. A support-gated POST /core/support-docs/seed-bundled
re-triggers it on demand.

NEVER-CLOBBER SEMANTICS (the whole point): a page is written ONLY when it is missing OR its existing row's
`updated_by` is NULL or 'seed' (i.e. it was itself seeded, never hand-edited). A human-edited row (any other
`updated_by`, e.g. an email) is SKIPPED — so re-seeding a new pack never overwrites support-desk edits. Every
written row is stamped `updated_by='seed'`. HOUSE org only.

DEGRADES GRACEFULLY: reads are best-effort — a missing bundle file or an un-run mig 715 (support_doc table
absent) is a silent no-op that never raises to the caller (sync_tenant / the endpoint).
"""
import json
import os
from datetime import datetime, timezone

from app.core.database import get_supabase

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "support_docs_seed.json")
# The columns we persist (mirrors the /core/support-docs import contract; is_published handled separately).
_DOC_FIELDS = ("page_key", "title", "module", "user_md", "support_md", "common_issues",
               "permissions_needed", "related_settings")
_UPSERT_CHUNK = 50


def load_seed_pages(path: str = None) -> list:
    """Parse the bundled pack → its `pages` list. Best-effort: a missing/invalid file → [] (never raises)."""
    p = path or _SEED_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        pages = data.get("pages") if isinstance(data, dict) else None
        return pages if isinstance(pages, list) else []
    except Exception:
        return []


def seed_support_docs(client=None, org_id: str = HOUSE_ORG, pages: list = None) -> dict:
    """Upsert the bundled help docs into core.support_doc for `org_id` (HOUSE), never clobbering a
    human-edited row. Returns {inserted, updated, skipped, ok}. `pages` overrides the bundled file (tests).
    Try/except-guarded end-to-end: an un-run mig 715 or any DB error → a silent no-op ({ok: False})."""
    client = client or get_supabase()
    if pages is None:
        pages = load_seed_pages()
    if not pages:
        return {"inserted": 0, "updated": 0, "skipped": 0, "ok": False}
    try:
        rows = (client.schema("core").table("support_doc").select("page_key,updated_by")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {"inserted": 0, "updated": 0, "skipped": 0, "ok": False}  # mig 715 un-run → no-op
    existing = {r.get("page_key"): r.get("updated_by") for r in rows}
    now = datetime.now(timezone.utc).isoformat()
    to_write, inserted, updated, skipped = [], 0, 0, 0
    for p in pages:
        if not isinstance(p, dict):
            skipped += 1
            continue
        pk = (p.get("page_key") or "").strip()
        if not pk:
            skipped += 1
            continue
        if pk in existing:
            ub = existing[pk]
            if ub is not None and str(ub) != "seed":
                skipped += 1        # human-edited → never clobber
                continue
            updated += 1
        else:
            inserted += 1
        row = {k: p[k] for k in _DOC_FIELDS if k in p}
        row.update({"page_key": pk, "org_id": org_id,
                    "is_published": bool(p.get("is_published", True)),
                    "updated_by": "seed", "updated_at": now})
        to_write.append(row)
    if not to_write:
        return {"inserted": inserted, "updated": updated, "skipped": skipped, "ok": True}
    try:
        for i in range(0, len(to_write), _UPSERT_CHUNK):
            (client.schema("core").table("support_doc")
             .upsert(to_write[i:i + _UPSERT_CHUNK], on_conflict="org_id,page_key").execute())
    except Exception:
        return {"inserted": 0, "updated": 0, "skipped": skipped, "ok": False}
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "ok": True}
