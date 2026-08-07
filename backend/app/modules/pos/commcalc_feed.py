"""Built-in POS → CommCalc feed (Phase 3; owner design 2026-08-07, see mig 727 header).

THE EXISTING EXTERNAL (b2bsoft) PIPELINE IS NEVER TOUCHED. The built-in POS module writes its
OWN stream tables — commcalc.pos_builtin_daily_sales / commcalc.pos_builtin_sales — in the
same column grain as daily_sales_feed / raw_sales. What happens next is a per-tenant
decision in core.tenant_pos_setup:

  * builtin_role='primary'  → the period is PROMOTED (column-for-column copy) into
    daily_sales_feed (daily mode) or raw_sales (monthly mode), exactly how the external feed
    lands today, so every existing consumer works unchanged. Promotion only ever runs for a
    primary built-in tenant — an external-primary tenant's ledger is never written by this
    module.
  * builtin_role='secondary' → NO promotion. The stream stays separate (POS 2). THE STREAMS
    NEVER MERGE (rules book: SAAS_FRAMEWORK.md §8): 'add' mode counts the stream toward
    end-of-day totals and pays qualifying commissions computed on its own stream; 'parallel'
    mode is comparison-only. The reporting/recon consumers of the secondary stream are
    follow-up work gated on the commissions requirements doc.
  * builtin_role='off' → sync refuses (tenant doesn't use the built-in POS).

Both the own-stream write and the promotion are delete-by-period + insert (idempotent
re-runs) with the EMPTY-ABORT guard (BUG_AUDIT Theme 1): zero source rows abort the sync
instead of wiping a period.

Row-shape conventions (matching the b2bsoft Sales-Transaction-Details grain, one row per
sale item): period '%B %Y' stamped in BUSINESS_TZ America/New_York; store = store_code;
salesperson = the storeops employee NAME (the calculator's rep_map is name-keyed) with
user_login = employee_id; ext_price = TAX-EXCLUSIVE (unit_price − discount) × qty (NOT the
tax-inclusive extended_price column); gp = ext_price − cost × qty; voided = 'Yes'/'No'
(shared VOID_TOKENS treats 'YES' as voided); trans_type = 'Sale'.

Every successful sync writes a commcalc.upload_trace row and idempotently registers
commcalc.pos_profile (pos_key='pos') + core.import_feed (feed_key below) so import-health
monitoring covers the feed.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.core.database import get_supabase

BUSINESS_TZ = ZoneInfo("America/New_York")
FEED_KEY = "pos_internal_daily_sales"

# own-stream table + promotion target per mode
MODE_TABLES = {
    "daily": ("pos_builtin_daily_sales", "daily_sales_feed"),
    "monthly": ("pos_builtin_sales", "raw_sales"),
}


def sb():
    return get_supabase()


def _period_bounds(period: str):
    """('August 2026') → (label, month, year, utc_start_iso, utc_end_iso); month window in
    BUSINESS_TZ, converted to UTC for the created_at filters."""
    try:
        start_local = datetime.strptime(period.strip(), "%B %Y").replace(tzinfo=BUSINESS_TZ)
    except ValueError:
        raise HTTPException(400, f"period must look like 'August 2026' (got {period!r})")
    if start_local.month == 12:
        end_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        end_local = start_local.replace(month=start_local.month + 1)
    return (start_local.strftime("%B %Y"), start_local.month, start_local.year,
            start_local.astimezone(ZoneInfo("UTC")).isoformat(),
            end_local.astimezone(ZoneInfo("UTC")).isoformat())


def _chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def get_pos_setup(org_id: str) -> dict:
    """The tenant's POS-source config; defaults (external primary, built-in off) when the
    row is missing (mig 727 unrun or a brand-new tenant)."""
    try:
        rows = (sb().schema("core").table("tenant_pos_setup").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            return rows[0]
    except Exception as e:
        print(f"WARN tenant_pos_setup read failed (run mig 727?): {e}")
    return {"org_id": org_id, "builtin_role": "off", "external_role": "primary",
            "secondary_mode": "parallel", "separate_registers": False}


def _fetch_sale_rows(org_id: str, utc_start: str, utc_end: str):
    """POS sales joined to items/products/payments/customers/roster names, at item grain."""
    client = sb()
    sales = []
    page = 0
    while True:
        rows = (client.schema("pos").table("sales").select("*")
                .eq("org_id", org_id).gte("created_at", utc_start).lt("created_at", utc_end)
                .order("created_at").range(page * 1000, page * 1000 + 999)
                .execute().data) or []
        sales.extend(rows)
        if len(rows) < 1000:
            break
        page += 1
    if not sales:
        return []

    sale_ids = [s["id"] for s in sales]
    items, payments = [], {}
    for chunk in _chunked(sale_ids, 100):
        items.extend((client.schema("pos").table("sale_items").select("*")
                      .in_("sale_id", chunk).execute().data) or [])
        for p in (client.schema("pos").table("sale_payments").select("sale_id,payment_method")
                  .in_("sale_id", chunk).order("created_at").execute().data) or []:
            payments.setdefault(p["sale_id"], p.get("payment_method"))

    prods = {p["id"]: p for p in (client.schema("pos").table("products")
             .select("id,product_code,upc,department_id,category_id")
             .eq("org_id", org_id).limit(5000).execute().data or [])}
    dnames = {d["id"]: d.get("short_name") for d in (client.schema("pos").table("departments")
              .select("id,short_name").eq("org_id", org_id).limit(500).execute().data or [])}
    cnames = {c["id"]: c.get("name") for c in (client.schema("pos").table("categories")
              .select("id,name").eq("org_id", org_id).limit(1000).execute().data or [])}
    emp_names = {(e.get("employee_id") or "").strip(): e.get("name")
                 for e in (client.table("employees").select("employee_id,name")
                           .eq("org_id", org_id).limit(2000).execute().data or [])}

    cust_ids = sorted({s["customer_id"] for s in sales if s.get("customer_id")})
    custs = {}
    for chunk in _chunked(cust_ids, 100):
        for c in (client.schema("pos").table("customers")
                  .select("id,first_name,last_name,company_name,email,cust_number")
                  .in_("id", chunk).execute().data) or []:
            custs[c["id"]] = c

    by_sale = {s["id"]: s for s in sales}
    out = []
    for it in items:
        s = by_sale.get(it["sale_id"])
        if not s:
            continue
        prod = prods.get(it.get("product_id")) or {}
        eid = (s.get("employee_id") or "").strip()
        cust = custs.get(s.get("customer_id")) or {}
        qty = it.get("qty") or 0
        unit = float(it.get("unit_price") or 0)
        disc = float(it.get("discount") or 0)
        cost = float(it.get("cost") or 0)
        ext = round((unit - disc) * qty, 2)
        created = s.get("created_at") or ""
        try:
            local_dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(BUSINESS_TZ)
            trans_date = local_dt.date().isoformat()
        except ValueError:
            trans_date = (created or "")[:10] or None
        out.append({
            "store": s.get("store_code"),
            "salesperson": emp_names.get(eid) or eid or None,
            "user_login": eid or None,
            "department": dnames.get(prod.get("department_id")),
            "category": cnames.get(prod.get("category_id")),
            "product_desc": it.get("description"),
            "product_id": prod.get("product_code"),
            "sku": prod.get("upc"),
            "gp": round(ext - cost * qty, 2),
            "ext_price": ext,
            "trans_id": str(s.get("transaction_id") or ""),
            "trans_date": trans_date,
            "contract_type": None,
            "mdn": None,
            "serial_1": it.get("serial_number"),
            "register": None,
            "tender_type": payments.get(s["id"]),
            "voided": "Yes" if (s.get("status") == "voided" or s.get("voided_at")) else "No",
            "trans_type": "Sale",
            "customer": (f"{cust.get('first_name') or ''} {cust.get('last_name') or ''}".strip()
                         or cust.get("company_name") or None),
            "email": cust.get("email"),
            "customer_no": str(cust.get("cust_number")) if cust.get("cust_number") else None,
        })
    return out


def _replace_period(table: str, org_id: str, period_label: str, payloads: list):
    """Delete-by-period + insert on ONE table. Callers guarantee payloads is non-empty
    (the empty-abort guard lives in sync_period)."""
    client = sb()
    client.schema("commcalc").table(table).delete() \
        .eq("org_id", org_id).eq("period", period_label).execute()
    for chunk in _chunked(payloads, 500):
        client.schema("commcalc").table(table).insert(chunk).execute()


def _ensure_registrations(org_id: str):
    """Idempotently register this tenant's built-in-POS profile + import-health feed."""
    client = sb()
    try:
        existing = (client.schema("commcalc").table("pos_profile").select("id")
                    .eq("org_id", org_id).eq("pos_key", "pos").limit(1).execute().data) or []
        if not existing:
            client.schema("commcalc").table("pos_profile").insert({
                "org_id": org_id, "pos_key": "pos", "label": "MetricsPro POS (built-in)",
                "imap_defaults": {}, "filename_rules": [],
                "schedule_defaults": {"frequency": "daily", "hour": 23},
                "report_defs": [
                    {"report_key": "sales", "label": "POS Sales (built-in)",
                     "source_name": "pos.sales / pos.sale_items",
                     "target_table": "pos_builtin_daily_sales",
                     "upload_endpoint": "pos/commcalc/sync",
                     "period_mode": "current", "auto": False, "sort_order": 10},
                ],
            }).execute()
    except Exception as e:
        print(f"WARN pos_profile registration skipped: {e}")
    try:
        existing = (client.schema("core").table("import_feed").select("id")
                    .eq("org_id", org_id).eq("feed_key", FEED_KEY).limit(1).execute().data) or []
        if not existing:
            client.schema("core").table("import_feed").insert({
                "org_id": org_id, "feed_key": FEED_KEY,
                "label": "POS module — built-in sales feed",
                "module": "pos", "source_type": "pull",
                "cadence_hours": 24, "grace_hours": 12,
                "deep_link": "/pos/reports",
                "evidence": [{"kind": "upload_trace", "upload_type": "daily_sales"}],
                "enabled": True, "auto_derived": False,
                "derived_from": "pos.commcalc_feed",
                "notes": "Regenerated by POST /api/v1/pos/commcalc/sync (daily mode).",
            }).execute()
    except Exception as e:
        print(f"WARN import_feed registration skipped: {e}")


def sync_period(org_id: str, mode: str, period=None):
    """Regenerate one period of the built-in POS stream (and, for a builtin-PRIMARY tenant,
    promote it). Returns a summary dict."""
    if mode not in MODE_TABLES:
        raise HTTPException(400, "mode must be 'daily' or 'monthly'")
    setup = get_pos_setup(org_id)
    role = setup.get("builtin_role") or "off"
    if role == "off":
        raise HTTPException(400, "this tenant's POS setup has the built-in POS turned off — "
                                 "enable it (primary or secondary) in tenant setup first")

    period_label, p_month, p_year, utc_start, utc_end = _period_bounds(
        period or datetime.now(BUSINESS_TZ).strftime("%B %Y"))

    import time as _time
    t0 = _time.time()
    rows = _fetch_sale_rows(org_id, utc_start, utc_end)
    if not rows:
        raise HTTPException(400, f"no POS sales found for {period_label} — aborting so the "
                                 "existing stream for that period is not wiped")

    own_table, promo_table = MODE_TABLES[mode]
    base = {"org_id": org_id, "period": period_label,
            "period_month": p_month, "period_year": p_year}
    # daily grain carries customer columns but no sku; monthly (raw_sales grain) the reverse.
    drop = ("sku",) if mode == "daily" else ("customer", "email", "customer_no")
    payloads = [{**base, **{k: v for k, v in r.items() if k not in drop}} for r in rows]

    _replace_period(own_table, org_id, period_label, payloads)

    promoted = False
    if role == "primary":
        # Promotion: the built-in stream IS this tenant's POS-1 ledger — land it exactly
        # where the external feed would. Secondary streams NEVER take this path (§8).
        # One ATOMIC transaction via commcalc.pos_promote_period (mig 727): a failure can't
        # leave the live ledger period wiped/half-written. The function re-checks the tenant
        # roles server-side and refuses while an external POS is configured (its feed lands
        # in the same tables and would be destroyed).
        if (setup.get("external_role") or "off") != "off":
            raise HTTPException(409, "promotion is blocked while an external POS is configured "
                                     "for this tenant — its feed lands in the same ledger tables "
                                     "(never-merge rule, SAAS_FRAMEWORK §8). Set external_role "
                                     "to 'off' or run the built-in POS as secondary.")
        try:
            sb().schema("commcalc").rpc("pos_promote_period", {
                "p_org": org_id, "p_period": period_label, "p_mode": mode,
            }).execute()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"promotion failed (stream table is written; ledger "
                                     f"unchanged or rolled back): {e}")
        promoted = True

    _ensure_registrations(org_id)

    result = {"saved": len(payloads), "period": period_label, "mode": mode,
              "stream_table": own_table, "builtin_role": role, "promoted": promoted,
              "promoted_table": promo_table if promoted else None,
              "voided_rows": sum(1 for r in rows if r["voided"] == "Yes")}
    try:
        from app.modules.commcalc.router import _write_upload_trace
        _write_upload_trace(org_id, source="pos_module",
                            filename=f"pos-builtin-{mode}-{period_label.replace(' ', '-')}",
                            upload_type=("daily_sales" if mode == "daily" else "sales"),
                            period=period_label, result=result,
                            duration_ms=int((_time.time() - t0) * 1000))
    except Exception as e:
        print(f"WARN upload_trace skipped: {e}")
    return result


def feed_status(org_id: str):
    """Tenant POS setup + per-period row counts of the built-in stream and (context) the
    promoted tables — the sync page's status strip."""
    client = sb()
    out = {"setup": get_pos_setup(org_id), "streams": {}}
    for table in ("pos_builtin_daily_sales", "pos_builtin_sales",
                  "daily_sales_feed", "raw_sales"):
        try:
            rows = (client.schema("commcalc").table(table).select("period")
                    .eq("org_id", org_id).limit(10000).execute().data) or []
        except Exception:
            rows = []
        counts = {}
        for r in rows:
            counts[r["period"]] = counts.get(r["period"], 0) + 1
        out["streams"][table] = counts
    return out
