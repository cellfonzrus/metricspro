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


def load_phone_map(client, org_id):
    """The phone mapping table (commcalc.device_model_alias, extended in mig 096): raw model string →
    {canonical, carrier_id}. Keyed by lower(raw_model). Empty until the user curates it (onboarding to-do)."""
    out = {}
    try:
        for r in (client.schema("commcalc").table("device_model_alias")
                  .select("raw_model,canonical_model,carrier_id").eq("org_id", org_id).execute().data or []):
            k = (r.get("raw_model") or "").strip().lower()
            if k:
                out[k] = {"canonical": r.get("canonical_model"), "carrier_id": r.get("carrier_id")}
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
                  .select("payment_type,category").eq("org_id", org_id).execute().data or []):
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
    return set(_sold_imei_store_map(client, org_id, table, imei_field, imeis))


def _owed_link_map(client, org_id, link, keys):
    """{source key -> (amount, date)} from a RELATED report, per `payable_source_map.owed_link` (mig 620).

    The MA reports say what was ACTIVATED, never what was INVOICED, so a Total source map has no
    owed_field and every device priced at NULL — which is why Daily Owed grouped nothing and 1,147
    devices sat in `discrepancy` at $0.00. The amount was one table over the whole time
    (raw_ma_fulfillment.price + .date_shipped). This resolves that join from CONFIG rather than
    hard-coding "Total reads the fulfillment report", so another processor is described, not coded.

    Chunked .in_() like the other match helpers. Returns {} for a missing/incomplete link or an
    unreadable table — the caller then leaves owed as NULL, which is honest ("nobody has priced this
    device") and is NOT the same as $0."""
    if not isinstance(link, dict):
        return {}
    table = str(link.get("table") or "").strip()
    ref_field = str(link.get("ref_field") or "").strip()
    amount_field = str(link.get("amount_field") or "").strip()
    date_field = str(link.get("date_field") or "").strip()
    if not (table and ref_field and amount_field):
        return {}
    cand = sorted({str(k).strip() for k in (keys or []) if str(k or "").strip()})
    if not cand:
        return {}
    cols = ",".join([c for c in (ref_field, amount_field, date_field) if c])
    out = {}
    for j in range(0, len(cand), 200):
        try:
            chunk = (client.schema("commcalc").table(table).select(cols)
                     .eq("org_id", org_id).in_(ref_field, cand[j:j + 200]).execute().data) or []
        except Exception as e:
            print(f"WARN owed_link read failed ({table}.{ref_field}): {e}")
            return {}
        for r in chunk:
            k = str(r.get(ref_field) or "").strip()
            if not k:
                continue
            amt = _f(r.get(amount_field))
            dt = _pdate(r.get(date_field)) if date_field else None
            # One order can carry several lines; the device's cost is the line, and the earliest ship
            # date is when the dealer took it on. First priced row wins, then fill a missing date.
            if k not in out:
                out[k] = (amt, dt)
            else:
                a0, d0 = out[k]
                out[k] = (a0 if a0 is not None else amt, d0 or dt)
    return out


def _sold_imei_store_map(client, org_id, table, imei_field, imeis):
    """{normalized imei -> the store that sold it} over a sales/match table. Chunked .in_() like
    _epay_map. Membership alone answers "sold?"; the STORE is what a source map with no `store_field`
    needs — a Total/MA row is booked against the DEALER account and carries no store of its own, so
    without this the whole ledger (and therefore the store filter, the per-store payables view and the
    forecast's "which store is this order for") reads '—' for every Total device. Owner report
    2026-08-10: "Phone forecast should show which store the inventory is being ordered for".
    Measured coverage on the luxelink ledger: 1,081 of 1,118 IMEIs (97%) resolve. The rest stay None —
    an unresolved device is never attached to an arbitrary store."""
    if not table or not imei_field:
        return {}
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
    found = {}
    # `store` is selected alongside the key so this costs the SAME queries it always did. A table with
    # no store column would 400 the select, so fall back to the key-only read and keep the sold answer.
    for j in range(0, len(cand), 200):
        chunk, with_store = [], True
        try:
            chunk = (client.schema("commcalc").table(table).select(f"{imei_field},store")
                     .eq("org_id", org_id).in_(imei_field, cand[j:j + 200]).execute().data) or []
        except Exception:
            with_store = False
            try:
                chunk = (client.schema("commcalc").table(table).select(imei_field)
                         .eq("org_id", org_id).in_(imei_field, cand[j:j + 200]).execute().data) or []
            except Exception:
                chunk = []
        for r in chunk:
            k = _norm_imei(r.get(imei_field))
            st = (str(r.get("store") or "").strip() or None) if with_store else None
            if k not in found or (found[k] is None and st):
                found[k] = st
    return found


# ── Total/MA device→store attribution (owner report 2026-09-04: "on the luxlink the store name is
#    not showing on the forecasting of the phones") ───────────────────────────────────────────────
#
# WHY THE POS MATCH ALONE WENT DARK, measured live 2026-09-04 (luxelink): the sold-match source
# (`raw_sales`) last fed 2026-08-09 and its `serial_1` is blank on the newer rows, so BOTH the
# forecast's in-window IMEI→POS-store map and the ledger's sold-match fill resolve NOTHING —
# 0 of 1,192 luxelink ledger rows carried a store, and every forecast row read "(unassigned)".
# A Total/MA activation is booked against the DEALER account and carries no store of its own, so
# when the POS feed lags, store attribution needs the platform's OTHER canonical device/account
# sources — never a new derivation (duplicate-check 2026-09-04):
#
#   1. POS sale line (existing `_sold_imei_store_map` / the forecast's raw_sales serial match) —
#      where the device was actually RUNG UP. Always wins when present.
#   2. `commcalc.inventory_aging_device` (§11, mig 216) — the per-IMEI stock snapshot; its `store`
#      is already in the store_mapping vocabulary (measured 20/20 exact) and resolves 880/1,120
#      of luxelink's last-30d MA sales at DEVICE grain.
#   3. The mig-314 account→store index (`ma_store_pnl.load_store_index`: raw_ma_fulfillment
#      tspid×business_address ∪ ma_account_store_map override) keyed by the MA row's
#      `merchant_account_id` — ACCOUNT grain, covers 1,120/1,120 (20 accounts, 0 ambiguous);
#      addresses collapse onto the canonical store_mapping spelling through `coa.store_resolver`
#      (the same normalization mig 314 itself prescribes).
#   4. Nothing resolves → None. An unresolved device renders "(unassigned)", never an arbitrary
#      store (the phantom-store lesson).
def resolve_ma_store(imei, account, pos_store, inv_by_imei, store_by_account, account_by_imei):
    """PURE precedence: POS sale line → inventory device row → mig-314 account → None.
    `imei` must already be normalized (_norm_imei); `account` may be None — it then falls back to
    the device's own account (`account_by_imei`). Truth table:
    backend/harness_device_forecast_store_filter.py."""
    if pos_store:
        return pos_store
    st = (inv_by_imei or {}).get(imei)
    if st:
        return st
    acct = str(account or "").strip() or (account_by_imei or {}).get(imei)
    if acct:
        return (store_by_account or {}).get(acct)
    return None


def ma_store_resolution(client, org_id):
    """I/O: build the Total/MA store-attribution maps for an org and return
    (resolve(imei_norm, account=None) -> store_or_None, meta). Composes ONLY canonical sources —
    inventory_aging_device (§11), ma_store_pnl.load_store_index (mig 314), coa.store_resolver —
    each best-effort: a missing table degrades that source to {} rather than raising, and an org
    with none of them gets a resolver that answers None (rows stay "(unassigned)", page still up)."""
    inv_by_imei, store_by_account, account_by_imei = {}, {}, {}
    try:                                        # 2 — inventory snapshot, device grain
        page = 0
        while page <= 40:
            chunk = (client.schema("commcalc").table("inventory_aging_device")
                     .select("imei,store").eq("org_id", org_id)
                     .range(page * PAGE, page * PAGE + PAGE - 1).execute().data) or []
            for r in chunk:
                k = _norm_imei(r.get("imei"))
                st = str(r.get("store") or "").strip()
                if k and st and k not in inv_by_imei:
                    inv_by_imei[k] = st
            if len(chunk) < PAGE:
                break
            page += 1
    except Exception as e:
        print(f"WARN payables ma_store_resolution inventory read failed: {e}")
    try:                                        # 3 — mig-314 account index, canonical spelling
        from app.modules.account import ma_store_pnl as _msp
        from app.modules.account import coa as _coa
        raw_idx = _msp.load_store_index(client, org_id) or {}
        if raw_idx:
            _resolve_addr = _coa.store_resolver(client, org_id)
            store_by_account = {a: (_resolve_addr(addr) or addr) for a, addr in raw_idx.items()}
    except Exception as e:
        print(f"WARN payables ma_store_resolution mig-314 index failed: {e}")
    if store_by_account:
        try:                                    # device → its own MA account (for account fallback
            page = 0                            # on rows that don't carry the account themselves)
            while page <= 40:
                chunk = (client.schema("commcalc").table("raw_ma_commission")
                         .select("imei,merchant_account_id").eq("org_id", org_id)
                         .range(page * PAGE, page * PAGE + PAGE - 1).execute().data) or []
                for r in chunk:
                    k = _norm_imei(r.get("imei"))
                    a = str(r.get("merchant_account_id") or "").strip()
                    if k and a and k not in account_by_imei:
                        account_by_imei[k] = a
                if len(chunk) < PAGE:
                    break
                page += 1
        except Exception as e:
            print(f"WARN payables ma_store_resolution imei->account read failed: {e}")

    def resolve(imei, account=None, pos_store=None):
        return resolve_ma_store(imei, account, pos_store,
                                inv_by_imei, store_by_account, account_by_imei)
    meta = {"inventory_imeis": len(inv_by_imei), "accounts": len(store_by_account),
            "imei_accounts": len(account_by_imei)}
    return resolve, meta


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

    # Store fallback for a source with no store of its own (every Total/MA map) — the POS sold-match
    # still wins per row; this only fills what it leaves blank (see ma_store_resolution). Built ONCE
    # per carrier, and only for the store-less shape: a Boost-style config with a store_field never
    # builds or consults it (byte-identical).
    ma_resolve = None
    if not cfg.get("store_field"):
        ma_resolve, _ = ma_store_resolution(client, org_id)

    status_counts, written, page = {}, 0, 0
    while True:
        q = (client.schema("commcalc").table(src_table).select(select_str)
             .eq("org_id", org_id))
        if owed_field:                       # only actual payables (keeps Boost small + meaningful)
            q = q.gt(owed_field, 0)
        chunk = q.range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        if not chunk:
            break
        rows = _rows_for_page(client, org_id, cfg, chunk, today, pct, alias, reimb_types, terms,
                              ma_resolve=ma_resolve)
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


def _rows_for_page(client, org_id, cfg, chunk, today, pct, alias, reimb_types, terms,
                   ma_resolve=None):
    cid = cfg["carrier_id"]
    imei_field = cfg["imei_field"]
    owed_field = cfg.get("owed_field")
    imeis = [r.get(imei_field) for r in chunk]

    vip_map = (_vip_invoice_map(client, org_id, imeis)
               if cfg.get("invoice_date_source") == "vip_invoices" else {})
    do_epay = bool(cfg.get("epay_crosscheck")) or cfg.get("reimbursement_source") == "epay"
    epay_map = _epay_payments_map(client, org_id, imeis) if do_epay else {}
    sold_store = (_sold_imei_store_map(client, org_id, cfg.get("sold_match_table"),
                                       cfg.get("sold_match_imei_field"), imeis)
                  if cfg.get("sold_source") == "sales_match" else {})
    sold_set = set(sold_store)

    # owed_link (mig 620): price the device from a RELATED report when its own row has no amount.
    # Resolved once per chunk, keyed on the source row's own key field (e.g. activation_order).
    owed_link = cfg.get("owed_link") if not owed_field else None
    link_key_field = str((owed_link or {}).get("key_field") or "").strip()
    link_map = (_owed_link_map(client, org_id, owed_link,
                               [r.get(link_key_field) for r in chunk]) if link_key_field else {})
    link_terms = None
    try:
        if owed_link and owed_link.get("terms_days") is not None:
            link_terms = max(0, int(owed_link["terms_days"]))
    except (TypeError, ValueError):
        link_terms = None

    out = []
    for r in chunk:
        imei = _norm_imei(r.get(imei_field))
        model_raw = (r.get(cfg["model_field"]) if cfg.get("model_field") else None)
        model = alias.get((str(model_raw or "").strip().lower()), model_raw)
        store = r.get(cfg["store_field"]) if cfg.get("store_field") else None
        # No store on the source row (every Total/MA map: the activation is booked against the dealer
        # account) -> use the POS store that sold the device. Only ever FILLS a blank; a configured
        # store_field always wins.
        if not store:
            store = sold_store.get(imei)
        # Still blank (the POS feed lags/blank serials — the 2026-09-04 luxelink shape) -> the
        # canonical inventory-snapshot / mig-314 account attribution. Fills blanks only.
        if not store and ma_resolve is not None:
            store = ma_resolve(imei, r.get("merchant_account_id"))
        owed = _f(r.get(owed_field)) if owed_field else None
        owed_source = "asset_ledger" if owed_field else "unconfigured"
        # A configured owed_field always WINS; the link only fills a map that has none.
        link_date = None
        if owed is None and link_map:
            amt, dt = link_map.get(str(r.get(link_key_field) or "").strip(), (None, None))
            if amt is not None:
                owed, link_date = amt, dt
                owed_source = f"{owed_link.get('table')}.{owed_link.get('amount_field')}"
            elif dt:
                link_date = dt

        # invoice date
        if cfg.get("invoice_date_source") == "vip_invoices":
            v = vip_map.get(imei)
            invoice_date = _pdate(v["vip_invoice_date"]) if v else None
            invoice_source = "vip_invoices"
        else:
            invoice_date = _pdate(r.get(cfg.get("invoice_date_field")))
            invoice_source = "field"
            # The linked report's own date (e.g. date_shipped) is when the dealer took the device on,
            # so it is the recognition date the net terms run from.
            if invoice_date is None and link_date:
                invoice_date, invoice_source = link_date, f"{owed_link.get('table')}.{owed_link.get('date_field')}"

        # due date: report field first, else invoice_date + net terms
        due_date, due_source = None, None
        if cfg.get("due_date_mode") == "field" and cfg.get("due_date_field"):
            due_date = _pdate(r.get(cfg["due_date_field"]))
            if due_date:
                due_source = "report"
        _terms = link_terms if (link_terms is not None and link_date) else terms
        if not due_date and invoice_date and _terms:
            due_date = invoice_date + timedelta(days=_terms)
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
