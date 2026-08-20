"""Per-store payment-processor merchant IDs (owner directive 2026-08-20, migration 902).

A store transacts third-party payments through one or more processors, each identifying the store by its
own merchant/terminal id (Boost → ePay ID, Total → Vidapay ID, …). These ids are how an ingested
processor report (ePay Daily Transaction Detail, Vidapay, …) resolves each transaction back to OUR store.

Pure data access over `storeops.store_merchant_id`; the router exposes the endpoints and the ingest calls
`resolve_map` to turn a report's terminal/merchant id into a store_code. Every read degrades to empty on
error (a missing table pre-migration must never 500 store setup)."""

from app.core.database import get_supabase

# Known processor keys. Free-form is allowed (a new carrier just uses a new key), but these drive the
# store-setup panel's default rows and the ingest lookups.
PROCESSORS = [
    {"key": "epay", "label": "ePay (Boost)", "id_label": "ePay ID"},
    {"key": "vidapay", "label": "Vidapay (Total)", "id_label": "Vidapay ID"},
]


def _sb():
    return get_supabase().schema("storeops")


def _norm(s):
    return str(s or "").strip()


def list_for_store(org_id, store_code):
    """Every processor row configured for one store (for the store-setup panel)."""
    try:
        return (_sb().table("store_merchant_id").select("*")
                .eq("org_id", org_id).eq("store_code", store_code)
                .order("processor").execute().data) or []
    except Exception:
        return []


def list_all(org_id):
    """Every merchant-id row for the tenant (admin grid / coverage view)."""
    try:
        return (_sb().table("store_merchant_id").select("*")
                .eq("org_id", org_id).order("store_code").execute().data) or []
    except Exception:
        return []


def upsert(org_id, store_code, processor, merchant_id=None, not_required=False, note=None):
    """Set one store's id for one processor. `not_required=True` records a deliberate opt-out (the store
    doesn't run that processor) so store setup can enforce 'mandatory unless not required' without a
    blank row reading as 'unconfigured'. Idempotent on (org_id, store_code, processor)."""
    from datetime import datetime, timezone
    store_code, processor = _norm(store_code), _norm(processor)
    if not (store_code and processor):
        raise ValueError("store_code and processor are required")
    mid = _norm(merchant_id) or None
    body = {"org_id": org_id, "store_code": store_code, "processor": processor,
            "merchant_id": mid, "not_required": bool(not_required), "note": note,
            "updated_at": datetime.now(timezone.utc).isoformat()}
    return (_sb().table("store_merchant_id")
            .upsert(body, on_conflict="org_id,store_code,processor").execute()).data


def resolve_map(org_id, processor):
    """{merchant_id: store_code} for one processor — the ingest resolves each report row's terminal/merchant
    id to a store through this. Only rows with a real merchant_id are included."""
    out = {}
    try:
        rows = (_sb().table("store_merchant_id").select("store_code,merchant_id")
                .eq("org_id", org_id).eq("processor", _norm(processor)).execute().data) or []
    except Exception:
        return out
    for r in rows:
        mid = _norm(r.get("merchant_id"))
        if mid:
            out[mid] = r.get("store_code")
    return out


def resolve_store(org_id, processor, merchant_id):
    """One store_code for a processor's merchant/terminal id, or None when unmapped."""
    return resolve_map(org_id, processor).get(_norm(merchant_id))


def coverage(org_id, store_codes, processor):
    """For a store-setup audit: which of `store_codes` still lack a decision (neither an id nor a
    'not required' opt-out) for `processor`. Returns the set of unconfigured store_codes."""
    configured = set()
    try:
        rows = (_sb().table("store_merchant_id").select("store_code,merchant_id,not_required")
                .eq("org_id", org_id).eq("processor", _norm(processor)).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        if _norm(r.get("merchant_id")) or r.get("not_required"):
            configured.add(r.get("store_code"))
    return {c for c in (store_codes or []) if c not in configured}
