"""SUPPLY ORDERING — every database read/write (index §36). Every query is org-scoped.

ONE lander: `land_catalog` is the only writer of the vendor catalog snapshot table — the kit-file upload
(router.catalog_upload) and the portal read (portal.catalog_pull_on_page, live or scheduled) both call it.
`backend/harness_supply_ordering.py` §L fails the build if a second writer appears.

Homes reused (never a sibling): the vendor roster is commcalc.po_vendor (mig 301, extended by mig 1021);
the order is commcalc.purchase_order + purchase_order_line (mig 301) numbered by commcalc.next_po_number
through asset/purchase_orders._next_po_number and moved through its _validate_status_transition; the login
is a commcalc.data_source row whose secrets are dropped with commcalc/router._SOURCE_SECRETS.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.modules.supply import ordering_logic as L

SCHEMA = "commcalc"
VENDOR_TABLE = "po_vendor"
CATALOG_TABLE = "vendor_catalog_price"          # mig 1021 — THE snapshot table
PO_TABLE = "purchase_order"
PO_LINE_TABLE = "purchase_order_line"
LOGIN_TABLE = "data_source"

MIGRATION_MSG = ("Supply ordering migration pending — ask the operator to run "
                 "database/migrations/1021_supply_ordering.sql (after 301).")
_MISSING = ("PGRST202", "PGRST204", "PGRST205", "PGRST203", "schema cache", "does not exist", "42703", "42P01")

VENDOR_COLS = ("id,org_id,name,contact_name,email,phone,terms,notes,is_active,portal_url,catalog_urls,portal_config,"
               "free_shipping_threshold,shipping_fee_below_threshold,delivery_days_min,delivery_days_max,"
               "data_source_id,is_price_source,created_at,updated_at")
# The order list never carries the screenshots (cart_evidence / confirmation) — only the detail read does.
ORDER_LIST_COLS = ("id,po_number,order_date,vendor_id,vendor_name_snapshot,status,subtotal,total,shipping_estimate,"
                   "expected_delivery_date,vendor_order_ref,vendor_order_total,supply_cart_ref,supply_meta,"
                   "submitted_by,submitted_at,created_by,created_at,updated_at,notes")
LOGIN_COLS = ("id,org_id,processor,label,portal_url,username,password,session_state,enabled,frequency,hour,"
              "auth_status,auth_message,last_run_at,last_status,last_attempt_at")


def missing_schema(exc):
    s = str(exc)
    return any(m in s for m in _MISSING)


def _now():
    return datetime.now(timezone.utc).isoformat()


def t(client, table):
    return client.schema(SCHEMA).table(table)


# ── vendors ─────────────────────────────────────────────────────────────────────────────────────────
def load_vendors(client, org_id, ids=None, active_only=False):
    q = t(client, VENDOR_TABLE).select(VENDOR_COLS).eq("org_id", org_id)
    if ids:
        q = q.in_("id", list(ids))
    if active_only:
        q = q.eq("is_active", True)
    return q.order("name").execute().data or []


def vendor_by_id(client, org_id, vendor_id):
    rows = (t(client, VENDOR_TABLE).select(VENDOR_COLS).eq("org_id", org_id).eq("id", vendor_id)
            .limit(1).execute().data) or []
    return rows[0] if rows else None


def vendor_for_source(client, org_id, source_id):
    rows = (t(client, VENDOR_TABLE).select(VENDOR_COLS).eq("org_id", org_id).eq("data_source_id", source_id)
            .limit(1).execute().data) or []
    return rows[0] if rows else None


def insert_vendor(client, org_id, row):
    rec = dict(row)
    rec["org_id"] = org_id
    res = t(client, VENDOR_TABLE).insert(rec).execute()
    return (res.data or [rec])[0]


def update_vendor(client, org_id, vendor_id, patch):
    p = dict(patch)
    p["updated_at"] = _now()
    t(client, VENDOR_TABLE).update(p).eq("org_id", org_id).eq("id", vendor_id).execute()


# ── logins (commcalc.data_source — secrets NEVER leave this function) ───────────────────────────────
def _secret_cols():
    try:
        from app.modules.commcalc.router import _SOURCE_SECRETS
        return tuple(_SOURCE_SECRETS)
    except Exception:                    # the list is the commcalc router's; this fallback only widens
        return ("password", "session_state", "pending_state", "totp_secret")


def public_login(row):
    """A data_source row as the supply pages may see it: presence flags only, every secret dropped."""
    if not row:
        return None
    r = dict(row)
    r["has_password"] = bool(r.get("password"))
    r["has_session"] = bool(r.get("session_state"))
    for k in _secret_cols():
        r.pop(k, None)
    return r


def load_logins(client, org_id, ids):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {}
    try:
        rows = t(client, LOGIN_TABLE).select(LOGIN_COLS).eq("org_id", org_id).in_("id", ids).execute().data or []
    except Exception:
        return {}
    return {r["id"]: public_login(r) for r in rows}


def login_row_full(client, org_id, sid):
    """The full login row (with secrets) for the live session only — never returned over HTTP."""
    rows = (t(client, LOGIN_TABLE).select("*").eq("org_id", org_id).eq("id", sid).limit(1).execute().data) or []
    return rows[0] if rows else None


# ── catalog snapshot — THE ONE LANDER ───────────────────────────────────────────────────────────────
def land_catalog(client, org_id, vendor_id, rows, source, run_id=None):
    """Land one vendor's catalog read as ONE snapshot run. Both ingest routes call this (kit upload, portal
    read). Rows are normalised by ordering_logic.normalize_catalog_rows (no name / no price → rejected with
    the reason, never stored as $0). Returns the honest delivery shape live_login / run_data_source read."""
    clean, rejects = L.normalize_catalog_rows(rows)
    run_id = run_id or str(uuid.uuid4())
    seen = _now()
    recs = [{**r, "org_id": org_id, "vendor_id": vendor_id, "run_id": run_id, "source": source, "seen_at": seen}
            for r in clean]
    for i in range(0, len(recs), 500):
        t(client, CATALOG_TABLE).insert(recs[i:i + 500]).execute()
    return {"ok": True, "delivered": bool(recs), "rows_ingested": len(recs), "rejected": len(rejects),
            "rejects": rejects[:50], "run_id": run_id, "vendor_id": vendor_id, "seen_at": seen,
            "target_table": f"{SCHEMA}.{CATALOG_TABLE}"}


def latest_run(client, org_id, vendor_id):
    rows = (t(client, CATALOG_TABLE).select("run_id,seen_at").eq("org_id", org_id).eq("vendor_id", vendor_id)
            .order("seen_at", desc=True).limit(1).execute().data) or []
    return rows[0] if rows else None


def latest_rows(client, org_id, vendor_ids):
    """The newest snapshot run per vendor (older runs are history, never mixed into today's prices)."""
    out, seen = [], {}
    for vid in vendor_ids or []:
        lr = latest_run(client, org_id, vid)
        if not lr:
            continue
        seen[vid] = lr.get("seen_at")
        page = 0
        while True:
            chunk = (t(client, CATALOG_TABLE).select("*").eq("org_id", org_id).eq("vendor_id", vid)
                     .eq("run_id", lr["run_id"]).range(page * 1000, page * 1000 + 999).execute().data) or []
            out.extend(chunk)
            if len(chunk) < 1000 or page > 50:
                break
            page += 1
    return out, seen


def catalog_rows_by_ids(client, org_id, ids):
    ids = [i for i in (ids or []) if i]
    out = []
    for i in range(0, len(ids), 200):
        out.extend(t(client, CATALOG_TABLE).select("*").eq("org_id", org_id).in_("id", ids[i:i + 200])
                   .execute().data or [])
    return out


# ── orders (commcalc.purchase_order — the mig-301 lifecycle, reused) ────────────────────────────────
def create_order(client, org_id, draft, cart_ref, who=None):
    """One purchase_order (+ lines) for one vendor of a supply cart. Number from next_po_number."""
    from app.modules.asset.purchase_orders import _next_po_number
    po_number = _next_po_number(client, org_id)
    row = {"org_id": org_id, "po_number": po_number, "order_date": datetime.now(timezone.utc).date().isoformat(),
           "vendor_id": draft["vendor_id"], "vendor_name_snapshot": draft.get("vendor_name"), "status": "draft",
           "subtotal": draft["subtotal"], "total": draft["total"], "shipping_estimate": draft["shipping_estimate"],
           "source": L.SUPPLY_SOURCE, "supply_cart_ref": cart_ref, "supply_meta": draft["meta"],
           "buyer": who, "created_by": who}
    res = t(client, PO_TABLE).insert(row).execute()
    po_id = (res.data or [{}])[0].get("id")
    lines = [{**ln, "org_id": org_id, "po_id": po_id, "qty_received": 0} for ln in draft["lines"]]
    if lines:
        t(client, PO_LINE_TABLE).insert(lines).execute()
    return {"id": po_id, "po_number": po_number, "vendor_id": draft["vendor_id"], "total": draft["total"]}


def list_orders(client, org_id, status=None, limit=500):
    q = t(client, PO_TABLE).select(ORDER_LIST_COLS).eq("org_id", org_id).eq("source", L.SUPPLY_SOURCE)
    if status:
        q = q.eq("status", status)
    return q.order("created_at", desc=True).limit(limit).execute().data or []


def order_detail(client, org_id, po_id):
    rows = (t(client, PO_TABLE).select("*").eq("org_id", org_id).eq("id", po_id).eq("source", L.SUPPLY_SOURCE)
            .limit(1).execute().data) or []
    if not rows:
        return None, []
    lines = (t(client, PO_LINE_TABLE).select("*").eq("org_id", org_id).eq("po_id", po_id).order("line_no")
             .execute().data) or []
    return rows[0], lines


def save_cart_evidence(client, org_id, po_id, evidence):
    t(client, PO_TABLE).update({"cart_evidence": evidence, "updated_at": _now()}) \
        .eq("org_id", org_id).eq("id", po_id).execute()


def set_status(client, org_id, po_id, new_status):
    from app.modules.asset.purchase_orders import _validate_status_transition
    head, _ = order_detail(client, org_id, po_id)
    if not head:
        return None
    _validate_status_transition(head["status"], new_status)
    t(client, PO_TABLE).update({"status": new_status, "updated_at": _now()}) \
        .eq("org_id", org_id).eq("id", po_id).execute()
    return new_status


def record_confirmation(client, org_id, po_id, evidence, who=None):
    """Write the vendor's order confirmation back onto the PO: status → submitted (through the mig-301
    lifecycle rule), the vendor's order number + total, the evidence (how it was captured, page, screenshot),
    and who/when. An already-submitted PO may gain its missing order number; a closed/cancelled one refuses."""
    from app.modules.asset.purchase_orders import _validate_status_transition
    head, _ = order_detail(client, org_id, po_id)
    if not head:
        return None
    cur = head.get("status")
    if cur not in ("draft", "submitted"):
        _validate_status_transition(cur, "submitted")         # raises for closed / cancelled
    patch = {"status": "submitted" if cur in ("draft", "submitted") else cur, "confirmation": evidence,
             "updated_at": _now()}
    if evidence.get("order_ref"):
        patch["vendor_order_ref"] = evidence["order_ref"]
    if evidence.get("total") is not None:
        patch["vendor_order_total"] = evidence["total"]
    if cur == "draft" or not head.get("submitted_at"):
        patch["submitted_at"] = evidence.get("captured_at") or _now()
        patch["submitted_by"] = who
    t(client, PO_TABLE).update(patch).eq("org_id", org_id).eq("id", po_id).execute()
    return patch
