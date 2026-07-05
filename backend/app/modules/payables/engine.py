"""Device Forecasting & Vendor Payables — the config-driven engine (module 095).

Builds a per-IMEI payable ledger (commcalc.device_payable_ledger) from a per-carrier config
(commcalc.payable_source_map) so a NEW carrier is a config row, not code. The IMEI is the universal
tally key: for every carrier it joins the device to (a) SALES (was it sold?) and (b) REIMBURSEMENT
(was the rebate received?). owed is independent — a carrier with no owed source still tallies sold +
reimbursement by IMEI.

HARD CONSTRAINT honored: this only READS commcalc.asset_ledger (+ vip_invoices / raw_payment_detail)
and imports the asset module's helper functions read-only. It never modifies the asset-lending system.
For Boost the DUE report equals /asset/owed-weekly because we COPY billing_friday/bill_path/owed_to_vip
verbatim — we never recompute Friday billing.
"""
from datetime import datetime, timedelta, timezone

from app.core.database import get_supabase
# read-only reuse — additive; does NOT modify the asset module
from app.modules.asset.router import (
    _norm_imei, _vip_invoice_map, _epay_payments_map, _classify_rma,
)

ORG_ID = "00000000-0000-0000-0000-000000000001"
PAGE = 1000


def sb():
    return get_supabase()


# ── small helpers ────────────────────────────────────────────────────────────
def _pdate(v):
    """Parse a cell to a date, or None. Accepts date / 'YYYY-MM-DD...' strings."""
    if v in (None, "", "nan", "NaT", "None"):
        return None
    s = str(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _dstr(d):
    return d.isoformat() if d else None


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def _tenant_priority_pct(client, org_id):
    try:
        r = (client.schema("storeops").table("tenants").select("priority_window_pct")
             .eq("org_id", org_id).limit(1).execute().data) or []
        p = r[0].get("priority_window_pct") if r else None
        return int(p) if p is not None else 25
    except Exception:
        return 25


def _load_source_maps(client, org_id, carrier_id=None):
    q = (client.schema("commcalc").table("payable_source_map").select("*")
         .eq("org_id", org_id).eq("is_active", True))
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    return q.execute().data or []


def _load_model_alias(client, org_id):
    out = {}
    try:
        for r in (client.schema("commcalc").table("device_model_alias")
                  .select("raw_model,canonical_model").eq("org_id", org_id).execute().data or []):
            k = (r.get("raw_model") or "").strip().lower()
            if k:
                out[k] = r.get("canonical_model")
    except Exception:
        pass
    return out


def _terms_days(client, org_id, distributor_id):
    if not distributor_id:
        return None
    try:
        r = (client.schema("commcalc").table("distributors").select("terms_days")
             .eq("org_id", org_id).eq("id", distributor_id).limit(1).execute().data) or []
        return int(r[0]["terms_days"]) if r and r[0].get("terms_days") is not None else None
    except Exception:
        return None


def _reimb_types(client, org_id):
    """payment_type values (lower) that mean 'Re-imbursement' per commcalc.payment_categories."""
    out = set()
    try:
        for r in (client.schema("commcalc").table("payment_categories")
                  .select("payment_type,category").execute().data or []):
            cat = (r.get("category") or "").lower()
            if "imb" in cat:  # Re-imbursement
                out.add((r.get("payment_type") or "").strip().lower())
    except Exception:
        pass
    return out


def _epay_reimb_amount(entries, reimb_types):
    """Sum ePay reimbursement payments for one IMEI's entry list (from _epay_payments_map)."""
    total = 0.0
    for e in entries or []:
        t = (e.get("type") or "").strip().lower()
        if t in reimb_types or "reimb" in t or "re-imb" in t:
            total += float(e.get("amount") or 0)
    return round(total, 2)


def _sold_imei_set(client, org_id, table, imei_field, imeis):
    """Which of these IMEIs appear in a sales/match table (=> sold). Chunked .in_() like _epay_map."""
    if not table or not imei_field:
        return set()
    candidates = set()
    for i in imeis:
        if not i:
            continue
        candidates.add(str(i).strip())
        n = _norm_imei(i)
        candidates.add(n)
        candidates.add(n + ".0")
    candidates.discard("")
    cand = list(candidates)
    found = set()
    for j in range(0, len(cand), 200):
        try:
            chunk = (client.schema("commcalc").table(table).select(imei_field)
                     .eq("org_id", org_id).in_(imei_field, cand[j:j + 200]).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            found.add(_norm_imei(r.get(imei_field)))
    return found


def _needed_cols(cfg):
    cols = set()
    for k in ("imei_field", "model_field", "store_field", "owed_field", "due_date_field",
              "billing_friday_field", "sold_date_field", "reimbursement_field",
              "reimbursement_date_field", "invoice_date_field"):
        v = cfg.get(k)
        if v:
            cols.add(v)
    if cfg.get("billing_friday_field"):     # Boost: copy bill_path verbatim (DUE == /owed-weekly)
        cols.add("bill_path")
    cols.add(cfg["imei_field"])
    return sorted(cols)


# ── the build ────────────────────────────────────────────────────────────────
def build_ledger(client, org_id=ORG_ID, carrier_id=None):
    """Rebuild commcalc.device_payable_ledger for one or all configured carriers (delete+insert
    per carrier). Returns counts. Best-effort per carrier — one bad config never aborts the rest."""
    today = datetime.now(timezone.utc).date()
    pct = _tenant_priority_pct(client, org_id)
    alias = _load_model_alias(client, org_id)
    reimb_types = _reimb_types(client, org_id)
    maps = _load_source_maps(client, org_id, carrier_id)
    result = {"carriers": 0, "written": 0, "status_counts": {}, "per_carrier": []}
    for cfg in maps:
        try:
            n, sc = _build_one_carrier(client, org_id, cfg, today, pct, alias, reimb_types)
        except Exception as e:
            result["per_carrier"].append({"carrier_id": cfg.get("carrier_id"),
                                          "label": cfg.get("label"), "error": str(e)[:200]})
            continue
        result["carriers"] += 1
        result["written"] += n
        for k, v in sc.items():
            result["status_counts"][k] = result["status_counts"].get(k, 0) + v
        result["per_carrier"].append({"carrier_id": cfg.get("carrier_id"),
                                      "label": cfg.get("label"), "rows": n, "status_counts": sc})
    return result


def _build_one_carrier(client, org_id, cfg, today, pct, alias, reimb_types):
    cid = cfg["carrier_id"]
    src_table = cfg["source_table"]
    imei_field = cfg["imei_field"]
    owed_field = cfg.get("owed_field")
    terms = _terms_days(client, org_id, cfg.get("distributor_id"))
    select_str = ",".join(_needed_cols(cfg))

    # delete this carrier's snapshot first (per-carrier scope — never touches other carriers)
    client.schema("commcalc").table("device_payable_ledger").delete() \
        .eq("org_id", org_id).eq("carrier_id", cid).execute()

    status_counts, written, page = {}, 0, 0
    while True:
        q = (client.schema("commcalc").table(src_table).select(select_str)
             .eq("org_id", org_id))
        if owed_field:                       # only actual payables (keeps Boost small + meaningful)
            q = q.gt(owed_field, 0)
        chunk = q.range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        if not chunk:
            break
        rows = _rows_for_page(client, org_id, cfg, chunk, today, pct, alias, reimb_types, terms)
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table("device_payable_ledger").insert(rows[i:i + 500]).execute()
        for r in rows:
            status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        written += len(rows)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 80:                        # safety backstop
            break
    return written, status_counts


def _rows_for_page(client, org_id, cfg, chunk, today, pct, alias, reimb_types, terms):
    cid = cfg["carrier_id"]
    imei_field = cfg["imei_field"]
    owed_field = cfg.get("owed_field")
    imeis = [r.get(imei_field) for r in chunk]

    vip_map = (_vip_invoice_map(client, org_id, imeis)
               if cfg.get("invoice_date_source") == "vip_invoices" else {})
    do_epay = bool(cfg.get("epay_crosscheck")) or cfg.get("reimbursement_source") == "epay"
    epay_map = _epay_payments_map(client, org_id, imeis) if do_epay else {}
    sold_set = (_sold_imei_set(client, org_id, cfg.get("sold_match_table"),
                               cfg.get("sold_match_imei_field"), imeis)
                if cfg.get("sold_source") == "sales_match" else set())

    out = []
    for r in chunk:
        imei = _norm_imei(r.get(imei_field))
        model_raw = (r.get(cfg["model_field"]) if cfg.get("model_field") else None)
        model = alias.get((str(model_raw or "").strip().lower()), model_raw)
        store = r.get(cfg["store_field"]) if cfg.get("store_field") else None
        owed = _f(r.get(owed_field)) if owed_field else None
        owed_source = "asset_ledger" if owed_field else "unconfigured"

        # invoice date
        if cfg.get("invoice_date_source") == "vip_invoices":
            v = vip_map.get(imei)
            invoice_date = _pdate(v["vip_invoice_date"]) if v else None
            invoice_source = "vip_invoices"
        else:
            invoice_date = _pdate(r.get(cfg.get("invoice_date_field")))
            invoice_source = "field"

        # due date: report field first, else invoice_date + net terms
        due_date, due_source = None, None
        if cfg.get("due_date_mode") == "field" and cfg.get("due_date_field"):
            due_date = _pdate(r.get(cfg["due_date_field"]))
            if due_date:
                due_source = "report"
        if not due_date and invoice_date and terms:
            due_date = invoice_date + timedelta(days=terms)
            due_source = "net_terms"

        # Boost: copy the Friday-billing verbatim so DUE == /owed-weekly (never recompute)
        billing_friday = _pdate(r.get(cfg["billing_friday_field"])) if cfg.get("billing_friday_field") else None
        bill_path = r.get("bill_path") if cfg.get("billing_friday_field") else None

        # sold?
        if cfg.get("sold_source") == "asset_field":
            sd = _pdate(r.get(cfg.get("sold_date_field")))
            sold_flag, sold_date = bool(sd), sd
        elif cfg.get("sold_source") == "sales_match":
            sold_flag, sold_date = (imei in sold_set), None
        else:
            sold_flag, sold_date = False, None

        # rebate received (primary), + ePay cross-check
        rebate_amount, rebate_date, rebate_source = 0.0, None, cfg.get("reimbursement_source") or "none"
        if rebate_source == "asset_ledger":
            _b, _o, reimb = _classify_rma(r)
            rebate_amount = reimb or 0.0
            rebate_date = _pdate(r.get(cfg.get("reimbursement_date_field")))
        elif rebate_source == "epay":
            rebate_amount = _epay_reimb_amount(epay_map.get(imei), reimb_types)
        elif rebate_source == "imei_match":
            mt = cfg.get("reimbursement_match_table")
            if mt:  # pending until a Total reimbursement report exists → stays 0 when unset
                rebate_amount = 0.0  # (wired via config; amount source added when the report lands)
        epay_rebate = _epay_reimb_amount(epay_map.get(imei), reimb_types) if do_epay else None
        rebate_mismatch = bool(cfg.get("epay_crosscheck") and epay_rebate
                               and abs((epay_rebate or 0) - rebate_amount) > 0.01)
        rebate_got = (rebate_amount and rebate_amount > 0) or bool(rebate_date)

        # offset math
        if owed is not None:
            net_offset = min(rebate_amount or 0.0, owed)
            net_owed = round(owed - net_offset, 2)
        else:
            net_offset, net_owed = 0.0, None

        # window + priority (final pct% of invoice→due)
        window_start = invoice_date or (due_date - timedelta(days=terms) if (due_date and terms) else None)
        window_end = due_date
        priority = False
        if (not sold_flag) and window_start and window_end and window_end > window_start:
            span = (window_end - window_start).days
            threshold = window_start + timedelta(days=int(span * (1 - pct / 100.0)))
            priority = today >= threshold

        # status routing
        if owed is not None and rebate_got and (rebate_amount or 0) >= (owed - 0.01):
            status = "offset"
        elif sold_flag and not rebate_got:
            status = "discrepancy"
        elif (not sold_flag) and not rebate_got and due_date and today >= due_date:
            status = "due"
        else:
            status = "open"

        out.append({
            "org_id": org_id, "carrier_id": cid, "imei": imei, "store": store,
            "device_model": model, "owed": owed, "owed_source": owed_source,
            "invoice_date": _dstr(invoice_date), "invoice_source": invoice_source,
            "due_date": _dstr(due_date), "due_source": due_source,
            "billing_friday": _dstr(billing_friday), "bill_path": bill_path,
            "sold_flag": sold_flag, "sold_date": _dstr(sold_date),
            "rebate_amount": round(rebate_amount or 0.0, 2), "rebate_date": _dstr(rebate_date),
            "rebate_source": rebate_source, "epay_rebate_amount": epay_rebate,
            "rebate_mismatch": rebate_mismatch, "net_offset": round(net_offset, 2),
            "net_owed": net_owed, "window_start": _dstr(window_start), "window_end": _dstr(window_end),
            "priority": priority, "status": status,
        })
    return out


# ── discrepancy dual-surface (new flags producer; does NOT touch the bounty engine) ───────────────
def sync_payable_flags(client, org_id=ORG_ID):
    """Write one commcalc.flags row (source='device_payable') per 'discrepancy' ledger row so the
    equipment-rebate gap surfaces in the existing flags/discrepancy UI AND this module. Delete-first
    by source (never clobbers asset_rma / asset_appeal / bounty flags). Never writes discrepancy_results
    (owned by the bounty engine) — no double-count, no edit to discrepancy_engine.py."""
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "device_payable").execute()
    rows = (client.schema("commcalc").table("device_payable_ledger")
            .select("imei,store,device_model,net_owed,owed,due_date")
            .eq("org_id", org_id).eq("status", "discrepancy").limit(20000).execute().data) or []
    flags = []
    for r in rows:
        amt = r.get("net_owed")
        if amt is None:
            amt = r.get("owed")
        flags.append({
            "org_id": org_id, "flag_type": "Equipment Rebate Not Received", "source": "device_payable",
            "severity": "critical", "imei": r.get("imei"), "amount": amt,
            "store_address": r.get("store"), "phone_model": r.get("device_model"),
            "description": "Device sold but the equipment rebate was not received — owed to vendor "
                           f"is not offset (due {r.get('due_date') or 'n/a'}).",
        })
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()
    return len(flags)


def priority_for_store(client, org_id, store, limit=50):
    """The per-store priority-sell list (devices in the final pct% of their pay window). A single
    indexed read (dpl_prio) so it's cheap on the clock-in critical path. Empty on any error/None store."""
    if not store:
        return []
    try:
        return (client.schema("commcalc").table("device_payable_ledger")
                .select("imei,device_model,window_end,due_date,net_owed,owed")
                .eq("org_id", org_id).eq("store", store).eq("priority", True)
                .order("window_end").limit(limit).execute().data) or []
    except Exception:
        return []
