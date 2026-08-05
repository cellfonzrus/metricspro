"""Upcoming Invoice Payment Due (mod-asset, migration band 300-399), dispatched 2026-08-05.

═══════════════════════════════ PHASE 1 FINDING — READ BEFORE TOUCHING ═══════════════════════════
The dispatch named the source as "the handset ordering report the owner uploaded earlier." The
LITERAL report of that name in this system is the VidaPay/T-CETRA portal's "MA Handset Ordering"
report ("MA - Marketplace Handset Fulfillment Orders" — see backend/scratchpad/live_login_auth_
detect_proof.py:136 for the portal's own menu text), ingested to `commcalc.raw_ma_fulfillment`
(view `raw_ma_marketplace_orders`, migration 083/207) — a Total-Wireless/VidaPay-side feed, uploaded
via the "MA Handset Fulfillment (Total)" tile on /commcalc/upload or the automated report-pull sweep.

That table was AUDITED and found STRUCTURALLY INCAPABLE of sourcing this report. Its columns are
exactly: id, org_id, carrier_id, source_id, date_ordered, date_filled, order_number, order_status,
order_type, tspid, business_name, business_address, city, state, zip, product_name, number_ordered,
price, tracking_number, date_shipped. There is NO invoice_number, NO due_date, NO grand_total
(invoice-level total), and — decisively — NO per-unit serial/IMEI column: a row is an order-LINE
aggregate ("iPhone 15 x 10 @ $500"), never a per-device record. Per-IMEI sold/reimbursed/commission
tracking (task items 2-4) is impossible against this table by construction, not by a query gap.

This is independently, structurally confirmed by mod-finance's own Device Payables module (migration
095, `commcalc.payable_source_map`): its seeded TOTAL carrier row explicitly sets `owed_field = NULL`
with the comment "NO per-IMEI owed/reimbursement AMOUNT source yet" for exactly this feed — the most
sophisticated prior attempt at a per-IMEI payable ledger in this codebase already hit and documented
the same wall.

What DOES carry every field this report needs — invoice #, due date, grand total, status, and a
per-invoice device (serial/IMEI) list — is `commcalc.vip_invoices` / `vip_invoice_lines` /
`vip_invoice_devices` (the "VIP Wireless Workbook" upload, migration 008; mod-commission-owned
tables, read-only here — the SAME cross-schema read `asset/router.py`'s `_vip_invoice_map` has done
since asset-2, and the SAME join key, `vip_invoice_devices.serial`, not `imei`). This module is
built against THAT data, joined to `commcalc.asset_ledger` (sold/reimbursed/device value, mine) and
`commcalc.raw_payment_detail` (per-IMEI ePay commission, mine to read — same table `_epay_payments_
map` already uses for Appeals evidence) classified via mod-commission's PURE, money-non-mutating
`commission_legs.LegClassifier` (owner directive 2026-08-04: 1st-Month vs M2-M12 commission legs).

**If the owner's intent really was the Total/VidaPay marketplace-order feed, this report does NOT
cover it** — the source data cannot support invoice due-date/total/per-IMEI tracking for that
program today. Flag back to the owner rather than extending this file to guess at missing data.
See docs/handoffs/asset.md for the full field-by-field feasibility matrix.

═══════════════════════════════ WHAT THIS FILE DOES  ══════════════════════════════════════════════
Per VIP invoice: due date, total due (`grand_total`), status, store (`location`), device count, and
a per-IMEI breakdown (serial/IMEI matched against `asset_ledger.esn_imei`) of sold / reimbursed /
still-on-inventory. Per IMEI, the M1-bucket ePay commission earned on that device (spiff/BYOD/
activation-bounty money whose label says "Month 1" — commission_legs' owner-ruled definition;
residual and M2-M12 money are excluded BY THE SAME CLASSIFIER, not by a second copy of its rules).

NET-DEDUCTION VIEW IS INFO-ONLY AND UNVERIFIED. `vip_invoices` carries no "amount actually deducted"
column anywhere in this schema — there is nothing to reconcile this estimate against (unlike the
🔒 Friday billing trigger, which IS verified to the penny against real invoices). `net_due_estimate =
grand_total - commission_earned_on_this_invoices_imeis` is exactly the owner's stated mental model
("VidaPay only deducts the NET"), rendered and labeled as an estimate, never as a verified figure,
and NEVER written anywhere (no table write from this math — pure display).

MONEY-ADJACENT, READ-ONLY: this module writes only to `commcalc.flags` (delete-first by
source='asset_invoice_due', then insert — the exact `_sync_appeal_flags` idiom). It never writes
`asset_ledger`, `vip_invoices`, `raw_payment_detail`, or any pay/commission/payout table.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.modules.asset.market_filter import NO_MARKET_SENTINEL, _market_matches

router = APIRouter()
ORG_ID = "00000000-0000-0000-0000-000000000001"
PAGE = 1000

# Named, documented thresholds (RULE TWO note: not yet an admin-editable setting — same
# "small/surgical, flag for later" posture as asset-14's MISSING_PHONE_STORE_THRESHOLD).
DUE_SOON_DAYS = 7          # inside this many days (or already overdue) -> flags as attention-worthy
FLAG_SOURCE = "asset_invoice_due"

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]
_MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(_MONTHS)}


def sb():
    return get_supabase()


# ── tiny local copies, deliberately NOT imported from asset/router.py ─────────────────────────────
# This file is mounted from the BOTTOM of asset/router.py (`router.include_router(...)`), exactly
# like purchase_orders.py — a top-level `from app.modules.asset.router import X` here would make the
# two modules mutually import each other at load time (fragile: only safe if router.py always loads
# first). Trivial universal helpers get their own local copy (same precedent as purchase_orders.py's
# own `_norm_imei`); the one non-trivial reused helper (`_epay_payments_map`) is imported LAZILY
# inside the function that needs it, safe regardless of load order.
def _norm_imei(v):
    s = str(v or "").strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _pdate(v):
    if v in (None, "", "nan", "NaT", "None"):
        return None
    s = str(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _f(v, d=0.0):
    try:
        return round(float(v), 2)
    except Exception:
        return d


def _period_variants(period):
    """Both spellings a `period` column might hold in this codebase ('June 2026' / '2026-06'),
    PURE, defensive against the documented period-spelling duality bug class. Returns a list
    (always includes the input verbatim) so `.in_('period', _period_variants(p))` never misses
    a row purely because of which spelling a given ingest used."""
    s = str(period or "").strip()
    if not s:
        return []
    out = {s}
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        try:
            y, m = int(s[:4]), int(s[5:7])
            if 1 <= m <= 12:
                out.add(f"{_MONTHS[m - 1]} {y}")
        except Exception:
            pass
    else:
        parts = s.split()
        if len(parts) == 2 and parts[0].lower() in _MONTH_IDX and parts[1].isdigit():
            m, y = _MONTH_IDX[parts[0].lower()], int(parts[1])
            out.add(f"{y:04d}-{m:02d}")
    return sorted(out)


# ── permission gate (mirrors ma_handset_cogs_allowed's shape — the money-report DATA_GRANT idiom,
# and purchase_orders.py's _require_po_admin's degrade-open-on-unresolvable-caller behavior) ───────
GRANT_KEY = "asset_invoice_due"


def invoice_due_allowed(caller):
    """PURE over an already-resolved caller dict. super_admin / scope=='all' / role=='admin' -> allow;
    the 'asset_invoice_due' DATA_GRANT (perms.modules or perms.data) -> allow; caller=None (token
    unresolvable / RBAC off) -> allow (degrade OPEN, same as _require_po_admin, so RBAC being off
    never locks the house org out of its own report); any other caller -> deny.

    NOTE: `asset_invoice_due` is not yet in frontend/src/lib/rbac.ts's DATA_GRANTS metadata array
    (a SHARED file — not edited here per AGENT_CONTRACT). Admins pass via the role=='admin' rule
    regardless; a non-admin role cannot yet be GRANTED this key from the Roles UI until that entry
    is added. Filed under NEEDS CORE in docs/handoffs/asset.md — not blocking (safe default-closed
    posture for non-admins either way)."""
    if caller is None:
        return True
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if GRANT_KEY in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(GRANT_KEY)):
        return True
    return False


def _require_invoice_due_view(authorization: str, org_id: str):
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid) if uid else None
    except Exception:
        return  # RBAC plumbing unavailable -> degrade open, never lock the house org out
    if not invoice_due_allowed(caller):
        raise HTTPException(403, "The Upcoming Invoice Payment Due report is restricted — you need "
                                 "the 'asset_invoice_due' permission to view it. Ask an admin to "
                                 "grant it on your role.")


# ── data access ──────────────────────────────────────────────────────────────────────────────────
def _fetch_invoices(client, org_id, statuses, date_from, date_to, stores, invoice_number_q):
    q = (client.schema("commcalc").table("vip_invoices")
         .select("id,vip_id,invoice_number,order_number,location,status,grand_total,sub_total,"
                  "shipping,discount,other_cost,other_deductions,tax,created_on,due_date,period,"
                  "period_month,period_year")
         .eq("org_id", org_id))
    if statuses:
        q = q.in_("status", statuses)
    if date_from:
        q = q.gte("due_date", date_from)
    if date_to:
        q = q.lte("due_date", date_to)
    if stores:
        q = q.in_("location", stores)
    if invoice_number_q:
        q = q.ilike("invoice_number", f"%{invoice_number_q}%")
    out, page = [], 0
    while True:
        chunk = q.order("due_date").range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 40:  # safety backstop, mirrors _fetch_all's cap convention
            break
    return out


def _fetch_devices_for_invoices(client, org_id, vip_ids):
    """vip_invoice_devices rows for a bounded set of invoices, chunked .in_() (mirrors the
    _vip_invoice_map / _epay_payments_map chunking convention)."""
    ids = [i for i in vip_ids if i is not None]
    if not ids:
        return {}
    by_invoice = {}
    for j in range(0, len(ids), 200):
        chunk = (client.schema("commcalc").table("vip_invoice_devices")
                 .select("vip_invoice_id,serial,imei,product_name,sim")
                 .eq("org_id", org_id).in_("vip_invoice_id", ids[j:j + 200])
                 .execute().data) or []
        for r in chunk:
            by_invoice.setdefault(r.get("vip_invoice_id"), []).append(r)
    return by_invoice


def _fetch_asset_rows_by_serial(client, org_id, serials):
    """asset_ledger rows keyed by normalized esn_imei, for a bounded set of VIP device serials.
    Reverse direction of _vip_invoice_map (which starts from asset IMEIs); same join key
    (asset_ledger.esn_imei == vip_invoice_devices.serial, ~99.6% match rate — asset-vip-invoice-
    join, verified) and the SAME raw/normalized/.0-suffix candidate-widening as every other
    IMEI join in this module, so this join can't silently disagree with the others on matching."""
    keys = {_norm_imei(s) for s in serials if s}
    if not keys:
        return {}
    candidates = set()
    for s in serials:
        if not s:
            continue
        candidates.add(str(s).strip())
        n = _norm_imei(s)
        candidates.add(n)
        candidates.add(n + ".0")
    candidates.discard("")
    cand = list(candidates)
    out = {}
    for j in range(0, len(cand), 200):
        chunk = (client.schema("commcalc").table("asset_ledger")
                 .select("esn_imei,store,market,device_model,category,date_sold,reimbursement,"
                          "reimbursement_date,owed_to_vip,selling_price,status")
                 .eq("org_id", org_id).in_("esn_imei", cand[j:j + 200])
                 .execute().data) or []
        for r in chunk:
            k = _norm_imei(r.get("esn_imei"))
            if k in keys and k not in out:   # keep the first match; asset-vip join is ~1:1 in practice
                out[k] = r
    return out


def _commission_for_imeis(client, org_id, legcls, imeis):
    """Per normalized IMEI: {m1, trailing, unsplit, lines}. `lines` carries the raw ePay type/amount/
    date so a reviewer can see exactly which payments were counted (never a black-box sum). Uses the
    SAME raw_payment_detail table + candidate-widening `_epay_payments_map` (asset/router.py) already
    uses for Appeals evidence — imported LAZILY to avoid the load-order cycle (see module docstring)."""
    from app.modules.asset.router import _epay_payments_map  # lazy — see module docstring
    epay_map = _epay_payments_map(client, org_id, list(imeis))
    out = {}
    for k, entries in epay_map.items():
        buckets = {"m1": 0.0, "trailing": 0.0, "unsplit": 0.0}
        lines = []
        for e in entries:
            bucket = legcls.label_bucket(e.get("type"))
            buckets[bucket] = round(buckets.get(bucket, 0.0) + float(e.get("amount") or 0), 2)
            lines.append({**e, "leg": bucket})
        out[k] = {**buckets, "lines": lines}
    return out


def _period_commission_m1_total(client, org_id, legcls, period):
    """Org-wide M1-bucket ePay commission for ONE period (all IMEIs, not just one invoice's) — the
    INFO-ONLY footer's comparison figure. Paginated read over raw_payment_detail filtered to the
    period (both spellings), capped like every other bounded scan in this module. Never touches
    residual (raw_mi is a different table, never queried here) or M2-M12 (the classifier routes it
    to 'trailing', excluded from the sum)."""
    variants = _period_variants(period)
    if not variants:
        return None
    total, page = 0.0, 0
    while True:
        chunk = (client.schema("commcalc").table("raw_payment_detail")
                 .select("payment_type,amount")
                 .eq("org_id", org_id).in_("period", variants)
                 .range(page * PAGE, page * PAGE + PAGE - 1).execute().data) or []
        for r in chunk:
            if legcls.label_bucket(r.get("payment_type")) == "m1":
                total += float(r.get("amount") or 0)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 80:
            break
    return round(total, 2)


def _invoice_market(devices, asset_by_serial):
    """Most-common market among an invoice's matched asset_ledger devices, else None (-> the
    "(no market)" bucket) — mirrors the mode-based tie-break the filter-options RPC (mig 311) uses
    for a store's market, extended here to an invoice's device set instead of a store's row set."""
    counts = {}
    for d in devices:
        row = asset_by_serial.get(_norm_imei(d.get("serial")))
        m = (row or {}).get("market")
        if m:
            counts[m] = counts.get(m, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _invoice_rollup(inv, devices, asset_by_serial, commission_by_imei):
    """Per-invoice: device evidence rollup + the INFO-ONLY net-deduction estimate. Every dollar in
    `commission_earned_m1` traces to a `lines` entry a reviewer can open (see /invoice-due/{vip_id})."""
    sold = reimbursed = not_sold = unmatched = 0
    commission_m1 = 0.0
    for d in devices:
        k = _norm_imei(d.get("serial"))
        row = asset_by_serial.get(k)
        if row is None:
            unmatched += 1
        else:
            if row.get("date_sold"):
                sold += 1
            else:
                not_sold += 1
            if float(row.get("reimbursement") or 0) > 0:
                reimbursed += 1
        c = commission_by_imei.get(k)
        if c:
            commission_m1 += c.get("m1", 0.0)
    grand_total = float(inv.get("grand_total") or 0)
    commission_m1 = round(commission_m1, 2)
    return {
        "device_count": len(devices), "matched_count": len(devices) - unmatched,
        "unmatched_count": unmatched, "sold_count": sold, "not_sold_count": not_sold,
        "reimbursed_count": reimbursed,
        "commission_earned_m1": commission_m1,
        "net_due_estimate": round(grand_total - commission_m1, 2),
        "net_due_estimate_note": ("INFO ONLY, UNVERIFIED — grand_total minus this invoice's own "
                                  "devices' 1st-Month (M1) ePay commission (spiff/BYOD/activation "
                                  "bounty; residual and M2-M12 excluded). vip_invoices carries no "
                                  "'amount actually deducted' field to check this against — unlike "
                                  "the Friday billing trigger, this number is NOT verified to the "
                                  "penny against a real VidaPay deduction. Treat as a working "
                                  "estimate of the owner's stated netting model, not a ledger figure."),
    }


# ── endpoints ────────────────────────────────────────────────────────────────────────────────────
@router.get("/invoice-due/filter-options")
def invoice_due_filter_options(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """RULE THREE/FIVE: pick-don't-type sources for the filter bar, from the org's REAL vip_invoices
    rows (never a hard-coded list)."""
    _require_invoice_due_view(authorization, org_id)
    client = sb()
    try:
        rows = (client.schema("commcalc").table("vip_invoices")
                .select("status,location").eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception as e:
        return {"available": False, "note": f"vip_invoices not readable yet ({str(e)[:160]}). "
                                            "Has the VIP Wireless Workbook been uploaded? "
                                            "(migration 008 must also be applied.)"}
    statuses = sorted({r["status"] for r in rows if r.get("status")})
    stores = sorted({r["location"] for r in rows if r.get("location")})
    return {"available": True, "statuses": statuses, "stores": stores}


@router.get("/invoice-due")
def invoice_due_list(status: str = "", date_from: str = "", date_to: str = "", store: str = "",
                     market: str = "", invoice_number: str = "", limit: int = 200, offset: int = 0,
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upcoming Invoice Payment Due — one row per VIP invoice with due date, total due, and the
    per-invoice device evidence rollup (sold / reimbursed / not-sold / commission-M1 / net-due
    estimate). `status`/`store` are comma-separated multi-selects (RULE FIVE); `market` accepts
    NO_MARKET_SENTINEL for the "(no market)" bucket, resolved per-invoice from its matched devices'
    asset_ledger.market (an invoice has no market column of its own — see market_filter.py)."""
    _require_invoice_due_view(authorization, org_id)
    client = sb()
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    stores = [s.strip() for s in store.split(",") if s.strip()]
    try:
        invoices = _fetch_invoices(client, org_id, statuses, date_from, date_to, stores, invoice_number)
    except Exception as e:
        return {"available": False, "note": f"vip_invoices not readable yet ({str(e)[:160]}). "
                                            "Has the VIP Wireless Workbook been uploaded? "
                                            "(migration 008 must also be applied.)", "rows": []}
    if not invoices:
        return {"available": True, "rows": [], "total": 0,
                "totals": {"grand_total": 0.0, "commission_earned_m1": 0.0, "net_due_estimate": 0.0}}

    vip_ids = [i.get("vip_id") for i in invoices]
    devices_by_invoice = _fetch_devices_for_invoices(client, org_id, vip_ids)
    all_serials = [d.get("serial") for devs in devices_by_invoice.values() for d in devs]
    asset_by_serial = _fetch_asset_rows_by_serial(client, org_id, all_serials)

    try:
        from app.modules.commcalc import commission_legs as _legs
        legcls = _legs.for_org(client, org_id, carrier_mode="boost")
    except Exception:
        from app.modules.commcalc import commission_legs as _legs
        legcls = _legs.default_classifier()
    commission_by_imei = _commission_for_imeis(client, org_id, legcls, all_serials)

    rows = []
    for inv in invoices:
        devices = devices_by_invoice.get(inv.get("vip_id"), [])
        inv_market = _invoice_market(devices, asset_by_serial)
        if not _market_matches(inv_market, market):
            continue
        rollup = _invoice_rollup(inv, devices, asset_by_serial, commission_by_imei)
        rows.append({**inv, "market": inv_market, **rollup})

    total = len(rows)
    page_rows = rows[offset:offset + limit]
    totals = {
        "grand_total": round(sum(float(r.get("grand_total") or 0) for r in rows), 2),
        "commission_earned_m1": round(sum(r["commission_earned_m1"] for r in rows), 2),
        "net_due_estimate": round(sum(r["net_due_estimate"] for r in rows), 2),
        "invoice_count": total, "device_count": sum(r["device_count"] for r in rows),
        "sold_count": sum(r["sold_count"] for r in rows),
        "not_sold_count": sum(r["not_sold_count"] for r in rows),
        "reimbursed_count": sum(r["reimbursed_count"] for r in rows),
    }
    return {"available": True, "rows": page_rows, "total": total, "offset": offset, "limit": limit,
            "totals": totals,
            "market_bucket": NO_MARKET_SENTINEL,
            "basis_note": ("commission_earned_m1 = 1st-Month (M1) ePay commission only — spiff/BYOD/"
                           "activation-bounty money labeled 'Month 1'. Residual (raw_mi, a separate "
                           "table, never queried here) and M2-M12 trailing money are excluded by the "
                           "SAME classifier the Gross Profit report uses (commcalc.commission_legs), "
                           "not by a second copy of its rules.")}


@router.get("/invoice-due/{vip_id}")
def invoice_due_detail(vip_id: int, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-IMEI drill-down for ONE invoice + the INFO-ONLY period-total reconciliation footer
    (task item 3: this invoice's own commission vs the SAME PERIOD's org-wide M1 commission)."""
    _require_invoice_due_view(authorization, org_id)
    client = sb()
    inv_rows = (client.schema("commcalc").table("vip_invoices").select("*")
                .eq("org_id", org_id).eq("vip_id", vip_id).limit(1).execute().data) or []
    if not inv_rows:
        raise HTTPException(404, f"No VIP invoice vip_id={vip_id} for this org")
    inv = inv_rows[0]
    devices = _fetch_devices_for_invoices(client, org_id, [vip_id]).get(vip_id, [])
    serials = [d.get("serial") for d in devices]
    asset_by_serial = _fetch_asset_rows_by_serial(client, org_id, serials)

    try:
        from app.modules.commcalc import commission_legs as _legs
        legcls = _legs.for_org(client, org_id, carrier_mode="boost")
    except Exception:
        from app.modules.commcalc import commission_legs as _legs
        legcls = _legs.default_classifier()
    commission_by_imei = _commission_for_imeis(client, org_id, legcls, serials)

    device_rows = []
    for d in devices:
        k = _norm_imei(d.get("serial"))
        a = asset_by_serial.get(k) or {}
        c = commission_by_imei.get(k) or {"m1": 0.0, "trailing": 0.0, "unsplit": 0.0, "lines": []}
        device_rows.append({
            "serial": d.get("serial"), "imei": d.get("imei"), "product_name": d.get("product_name"),
            "matched": bool(asset_by_serial.get(k)),
            "store": a.get("store"), "market": a.get("market"), "device_model": a.get("device_model"),
            "sold": bool(a.get("date_sold")), "date_sold": a.get("date_sold"),
            "reimbursed": float(a.get("reimbursement") or 0) > 0,
            "reimbursement": a.get("reimbursement"), "reimbursement_date": a.get("reimbursement_date"),
            "owed_to_vip": a.get("owed_to_vip"),
            "commission_m1": c.get("m1", 0.0), "commission_trailing": c.get("trailing", 0.0),
            "commission_unsplit": c.get("unsplit", 0.0), "commission_lines": c.get("lines", []),
        })

    rollup = _invoice_rollup(inv, devices, asset_by_serial, commission_by_imei)
    period_total_m1 = _period_commission_m1_total(client, org_id, legcls, inv.get("period"))
    footer = {
        "period": inv.get("period"),
        "invoice_commission_m1": rollup["commission_earned_m1"],
        "period_total_commission_m1": period_total_m1,
        "difference": (round(period_total_m1 - rollup["commission_earned_m1"], 2)
                       if period_total_m1 is not None else None),
        "note": ("INFO ONLY — this invoice's devices earned this much of the WHOLE period's M1 "
                 "commission; the remainder was earned by every other device activated/sold in the "
                 "same period, on other invoices. Never used to adjust owed/due/net figures above."),
    }
    return {"invoice": inv, "devices": device_rows, **rollup, "period_commission_footer": footer}


def _sync_invoice_due_flags(client, org_id):
    """Delete-first-by-source then insert (the `_sync_appeal_flags`/`_sync_rma_flags` idiom, mig-free
    — reuses the existing commcalc.flags table). One flag per invoice that is OVERDUE (critical) or
    due within DUE_SOON_DAYS (warning) and not already Voided/Paid In Full. Never touches any other
    source's flags (asset_appeal / asset_rma / device_payable / anything else)."""
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", FLAG_SOURCE).execute()
    invoices = _fetch_invoices(client, org_id, [], "", "", [], "")
    today = datetime.now(timezone.utc).date()
    flags = []
    for inv in invoices:
        status = (inv.get("status") or "").strip().lower()
        if status in ("paid in full", "voided"):
            continue
        due = _pdate(inv.get("due_date"))
        if not due:
            continue
        days = (due - today).days
        if days > DUE_SOON_DAYS:
            continue
        severity = "critical" if days < 0 else "warning"
        flags.append({
            "org_id": org_id, "period": inv.get("period") or "Unknown",
            "period_month": inv.get("period_month"), "period_year": inv.get("period_year"),
            "flag_type": "Upcoming VIP Invoice Due" if days >= 0 else "VIP Invoice Overdue",
            "source": FLAG_SOURCE, "severity": severity,
            "store_address": inv.get("location"), "amount": inv.get("grand_total"),
            "description": (f"Invoice {inv.get('invoice_number') or inv.get('vip_id')} "
                            f"due {inv.get('due_date')} — total due "
                            f"${float(inv.get('grand_total') or 0):,.2f}."),
        })
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()
    return {"flags_written": len(flags)}


@router.post("/sync-invoice-due-flags")
def sync_invoice_due_flags(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Manual trigger (button on the report page). NOT auto-run on VIP invoice upload — that upload
    endpoint (`POST /commcalc/vip/upload`) lives in commcalc/router.py, mod-commission-owned; wiring
    an auto-trigger there is a cross-module ask, filed in docs/handoffs/asset.md, not built here."""
    _require_invoice_due_view(authorization, org_id)
    client = sb()
    try:
        return {"ok": True, **_sync_invoice_due_flags(client, org_id)}
    except Exception as e:
        raise HTTPException(500, f"Flag sync failed: {str(e)[:300]}")
