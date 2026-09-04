"""Endpoint for the Processor Money-Movement Ledger (see processor_ledger.py for the full data
story). A SEPARATE small router (included from app.main) rather than a hunk in commcalc/router.py —
same /api/v1/commcalc/* namespace, same auth idioms (scope_keyset store-span gating, org-scoped
reads). Keeping processor_ledger.py fastapi-free lets harness_processor_ledger.py import its pure
core with stdlib only."""
from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.modules.commcalc import processor_ledger as _pl

ORG_ID = "00000000-0000-0000-0000-000000000001"

router = APIRouter(prefix="/commcalc", tags=["commcalc"])


@router.get("/processor-ledger")
def processor_ledger(date_from: str = "", date_to: str = "", stores: str = "", types: str = "",
                     markets: str = "", authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    """Daily processor debits vs credits by transaction type, from the org's processor feeds
    (per-feed shapes in processor_ledger.FEED_SHAPES; which feed is primary comes from config,
    mig 923/939, and the processor's NAME from the mig-953 report_term vocabulary).
    `stores`/`types`/`markets` are comma-separated optional filters (the page also filters
    client-side for WYSIWYG; these serve the W3 builder + deep links). `market_options` in the
    payload is the canonical §13c option list and is NOT narrowed by the caller's filters — a
    dropdown that shrinks to the selection you already made cannot be un-picked.
    Rows are scoped to the caller's store span;
    cells with NO resolvable store (unmapped feed keys) are visible only to unrestricted callers —
    a store-scoped manager must never see another store's money under an unmapped label."""
    if not date_from:
        raise HTTPException(400, "date_from (YYYY-MM-DD) is required")
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    try:
        out = _pl.assemble(get_supabase(), org_id, date_from, date_to or date_from)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if ks is not None:
        out["cells"] = [c for c in out["cells"]
                        if c.get("store_code") and in_keyset(ks, c.get("store_code"), c.get("store"))]
    want_stores = [s for s in (stores or "").split(",") if s.strip()]
    want_types = [t for t in (types or "").split(",") if t.strip()]
    want_markets = [m for m in (markets or "").split(",") if m.strip()]
    if want_stores or want_types or want_markets:
        out["cells"] = _pl.filter_cells(out["cells"], stores=want_stores, types=want_types,
                                        markets=want_markets)
    out["types"] = sorted({c["tx_type"] for c in out["cells"]}, key=str.lower)
    out["rollup"] = _pl.day_type_rollup(out["cells"])
    return out
