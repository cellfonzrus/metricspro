"""ePay (Boost) "Daily Transaction Detail" ingest (owner directive 2026-08-20, migration 903).

The Boost owner-portal report, one row per transaction line. We store the raw lines, resolve each line's
TerminalID to OUR store via the per-store merchant-ID registry (processor 'epay', migration 902), and
aggregate per store-day into PAYMENT ($ of Boost RTR / replenishment / refill lines) and FEE ($ of the
"…FEE" lines) — the two figures the DM Verify recon and the fee-recon report compare against our own
raw_sales (product_desc "boost rtr" = payment, "epay service charge" = fee).

MA/VidaPay (Total) is the sibling of this for the other carrier; identical shape, its own report/table
(raw_ma_daily_tx) — this module is the Boost/ePay half.

Parsing is a pure function over a list of record dicts (pandas `.to_dict('records')` OR a hand-built
list), so it is fully unit-testable without a DB or a file. Persistence is idempotent on
(transaction_id, transaction_source_id) so an hourly re-pull never double-counts.
"""
import re

from app.core.database import get_supabase

PROCESSOR = "epay"

# The report's column headers (see the sample). Kept as a constant so the parser and any header-check
# stay in one place.
COLUMNS = ("TransactionID", "TransactionSourceID", "InvoiceID", "SettlementDate", "SettlementDay",
           "TerminalID", "UserName", "Product", "ProductTitle", "Type", "HostTimeStamp",
           "ControlNumber", "Retail", "Discount", "Cost", "Commission")


def _s(v):
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _f(v):
    try:
        return round(float(_s(v) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm(v):
    return _s(v).upper()


def _date10(v):
    """'2026-08-18 00:00:00' / an Excel serial / a date -> 'YYYY-MM-DD', or None."""
    s = _s(v)
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:                                    # Excel serial day number
        import pandas as pd
        n = float(s)
        return pd.to_datetime(n, origin="1899-12-30", unit="D").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        try:
            import pandas as pd
            ts = pd.to_datetime(s, errors="coerce")
            return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")
        except Exception:
            return None


def is_fee_line(product_title):
    """A fee line is the ePay service charge — the report titles it "… FEE"."""
    return "FEE" in _norm(product_title)


def suggest_store_from_username(user_name):
    """A best-effort store-code SUGGESTION extracted from the UserName, which usually embeds the store
    number (418Uniondale->418, Epay652->652, 6149-epay->6149, 2509bl->2509). Address-style names
    (3PL, 1s60th) have no clean number and return ''. Only a hint for the confirm step — never authoritative."""
    s = _s(user_name)
    if not s:
        return ""
    low = s.lower()
    for pre in ("epay",):                       # strip a leading 'Epay' label before reading the number
        if low.startswith(pre):
            s = s[len(pre):]
    m = re.match(r"\s*(\d{2,6})", s)            # a leading 2-6 digit run is almost always the store number
    return m.group(1) if m else ""


def parse_records(records, source_batch=None):
    """Normalize raw report rows into raw_epay_daily_tx dicts. Skips rows with no TransactionID. Pure."""
    out = []
    for r in records or []:
        tid = _s(r.get("TransactionID"))
        if not tid:
            continue
        title = _s(r.get("ProductTitle"))
        out.append({
            "transaction_id": tid,
            "transaction_source_id": _s(r.get("TransactionSourceID")) or "1",
            "invoice_id": _s(r.get("InvoiceID")) or None,
            "settlement_date": _date10(r.get("SettlementDate")),
            "terminal_id": _s(r.get("TerminalID")) or None,
            "user_name": _s(r.get("UserName")) or None,
            "product": _s(r.get("Product")) or None,
            "product_title": title or None,
            "tx_type": _s(r.get("Type")) or None,
            "host_timestamp": _s(r.get("HostTimeStamp")) or None,
            "control_number": _s(r.get("ControlNumber")) or None,
            "retail": _f(r.get("Retail")),
            "discount": _f(r.get("Discount")),
            "cost": _f(r.get("Cost")),
            "commission": _f(r.get("Commission")),
            "is_fee": is_fee_line(title),
            "source_batch": source_batch,
        })
    return out


def resolve_stores(rows, terminal_to_store):
    """Set store_code on each row from a {terminal_id: store_code} map (built from the merchant registry).
    Returns the set of terminal_ids that could NOT be resolved (need a store mapping)."""
    unresolved = set()
    for row in rows:
        code = terminal_to_store.get(_s(row.get("terminal_id")))
        row["store_code"] = code
        if not code and row.get("terminal_id"):
            unresolved.add(row["terminal_id"])
    return unresolved


def aggregate_store_day(rows):
    """{(store_code, settlement_date): {'payment': $, 'fee': $, 'lines': n}} from parsed+resolved rows.
    PAYMENT excludes fee lines; FEE is the fee lines only (owner: reconcile the two separately)."""
    out = {}
    for row in rows:
        sc, d = row.get("store_code"), row.get("settlement_date")
        if not (sc and d):
            continue
        agg = out.setdefault((sc, d), {"payment": 0.0, "fee": 0.0, "lines": 0})
        if row.get("is_fee"):
            agg["fee"] = round(agg["fee"] + _f(row.get("retail")), 2)
        else:
            agg["payment"] = round(agg["payment"] + _f(row.get("retail")), 2)
        agg["lines"] += 1
    return out


# ── DB-backed helpers (thin; the pure functions above carry the logic) ─────────────────────────────
def _cc():
    return get_supabase().schema("commcalc")


def store_rows(client, org_id, rows):
    """Idempotent upsert into commcalc.raw_epay_daily_tx (dedup on transaction_id + source_id). Batched."""
    saved = 0
    payload = [{**r, "org_id": org_id} for r in rows]
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        client.schema("commcalc").table("raw_epay_daily_tx").upsert(
            chunk, on_conflict="org_id,transaction_id,transaction_source_id").execute()
        saved += len(chunk)
    return saved


def ingest(org_id, records, source_batch=None, client=None):
    """Full manual/auto ingest: parse -> resolve stores via the merchant registry -> persist. Returns
    {saved, unresolved_terminals:[{terminal_id, user_name, suggested_store}]}. The unresolved list drives
    the one-click 'confirm this terminal's store' step so the operator never hand-enters 28 stores."""
    from app.modules.storeops import merchant_ids as _mids
    client = client or get_supabase()
    rows = parse_records(records, source_batch=source_batch)
    tmap = _mids.resolve_map(org_id, PROCESSOR)
    unresolved_ids = resolve_stores(rows, tmap)
    saved = store_rows(client, org_id, rows) if rows else 0
    # Attach a suggested store to each unresolved terminal (from its most common UserName).
    by_terminal = {}
    for row in rows:
        tid = _s(row.get("terminal_id"))
        if tid in unresolved_ids and tid not in by_terminal:
            by_terminal[tid] = {"terminal_id": tid, "user_name": row.get("user_name"),
                                "suggested_store": suggest_store_from_username(row.get("user_name"))}
    return {"saved": saved, "rows": len(rows),
            "unresolved_terminals": sorted(by_terminal.values(), key=lambda x: x["terminal_id"])}


def per_store_day(client, org_id, date_from, date_to, store_codes=None):
    """{(store_code, 'YYYY-MM-DD'): {'payment', 'fee', 'lines'}} over the ingested rows for the range —
    the portal side of the payment + fee recon. Rows with no resolved store are excluded (surfaced
    separately via unmapped_terminals)."""
    try:
        q = (client.schema("commcalc").table("raw_epay_daily_tx")
             .select("store_code,settlement_date,retail,is_fee")
             .eq("org_id", org_id).gte("settlement_date", date_from).lte("settlement_date", date_to))
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return {}
    return aggregate_store_day(rows)


def unmapped_terminals(client, org_id):
    """Terminals with ingested rows but no resolved store — the confirm queue, each with a UserName
    suggestion so the operator can map it in one click."""
    try:
        rows = (client.schema("commcalc").table("raw_epay_daily_tx")
                .select("terminal_id,user_name").eq("org_id", org_id)
                .is_("store_code", "null").limit(200000).execute().data) or []
    except Exception:
        return []
    by_terminal = {}
    for r in rows:
        tid = _s(r.get("terminal_id"))
        if tid and tid not in by_terminal:
            by_terminal[tid] = {"terminal_id": tid, "user_name": r.get("user_name"),
                                "suggested_store": suggest_store_from_username(r.get("user_name"))}
    return sorted(by_terminal.values(), key=lambda x: x["terminal_id"])


def backfill_store_codes(client, org_id, terminal_id, store_code):
    """After a terminal is mapped (merchant registry updated), stamp its already-ingested rows so past
    days reconcile too. Returns the number of rows updated."""
    try:
        res = (client.schema("commcalc").table("raw_epay_daily_tx")
               .update({"store_code": store_code})
               .eq("org_id", org_id).eq("terminal_id", terminal_id).is_("store_code", "null").execute())
        return len(res.data or [])
    except Exception:
        return 0
