"""ONE read for "what is owed, on which date" — shared by the DAILY surface and the WEEKLY roll-up.

Index §COMMCALC-34. Lock: `backend/harness_billing_period.py`.

WHY THIS EXISTS, and it is not tidiness. `Weekly Owed to Distributor` showed nothing for a Total
tenant because it read one carrier's `asset_ledger.billing_friday` and nothing else (§COMMCALC-34).
The obvious repair — give the weekly report its own read of the payable ledger — would have created a
SECOND derivation of the same money: two paths answering *"what do I owe"*, free to disagree the
moment either learned something the other did not. `CLAUDE.md` calls that the duplicate defect and it
is a build gate, so the money is read ONCE here and grouped twice: by DUE DATE for the daily surface,
and by BILLING PERIOD (`payables/billing_period.py`) for the weekly one. The week cannot total
something the day disagrees with, by construction rather than by care.

WHAT ANSWERS IS NAMED, PER TENANT, AND THE FALLBACK IS NOT A SECRET. Two sources, in order:

  * **`device_ledger`** — `commcalc.device_payable_ledger`, the carrier-agnostic per-IMEI ledger.
    Grain: a DEVICE. Non-payable statuses are excluded through `ledger_status` (§COMMCALC-32), so a
    line that bought no device never reaches this arithmetic.
  * **`vendor_feed`** — the processor's own dated amounts, used when the ledger carries no amounts at
    all, which is every Total / MA tenant: their source map has no owed field because the MA reports
    record what was ACTIVATED, never what was INVOICED. Grain: an ORDER LINE — there is no IMEI on
    this path at all, and saying so is the point. A positive cost is money the DEALER OWES (negative
    is money paid TO the dealer, the same sign convention the commission ledger books from).

An empty result from BOTH is `source: None`, never a $0.00 total dressed as a measurement.

PURE OF POLICY: this module reads rows and names the source. It decides no billing rhythm (that is
`billing_period`), no payability (that is `ledger_status`), no window (that is `date_window`) and no
store identity — the caller injects its own resolvers, so "which store is this" keeps the one home it
already has (§64) instead of gaining a copy here. No carrier, tenant or product name (RULE TWO).
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import date_window
from app.modules.payables import ledger_status as _ls
from app.modules.payables import vendor_invoice as _vi

#: Which source answered. A surface prints these, so they are named once and never spelled twice.
SOURCE_DEVICE_LEDGER = "device_ledger"
SOURCE_VENDOR_FEED = "vendor_feed"

GRAIN_DEVICE = "device"
GRAIN_ORDER_LINE = "order_line"

#: The relations, DEREFERENCED from the feed registry rather than spelled here. The vendor feed's
#: name was a literal on the read this module absorbed; naming a fact is half the fix and deleting
#: the copy is the other half (§19.18), so the copy is gone in the same commit.
DEVICE_LEDGER = "device_payable_ledger"
VENDOR_FEED = _lineage.MA_DAILY_TX

PAGE = 1000
#: A hard stop so a runaway page loop cannot hang a request. Reached means INCOMPLETE, and the
#: caller is told rather than handed a short total that looks whole.
MAX_PAGES = 60

VENDOR_GRAIN_NOTE = (
    "Dated from the processor's own feed, not from the per-IMEI payable ledger — this tenant's "
    "carrier source map carries no owed field, because the MA reports record what was activated, "
    "never what was invoiced. These are ORDER LINES, not devices, so there is no IMEI to drill into")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ledger_rows(client, org_id, win, *, want_store=None, want_markets=None, market_of=None):
    """The per-IMEI payable ledger's dated owed rows, filtered exactly as the daily surface filters.

    `want_store` and `market_of` are the CALLER'S resolvers — store identity (§64) and the market
    home (RULE FIVE) keep their own homes rather than gaining copies here. `net_owed` answers where
    it exists and `owed` is the fallback, which is the daily surface's own precedence, stated once.
    """
    q = _ls.exclude_non_payable(                       # §COMMCALC-32
        client.schema("commcalc").table(DEVICE_LEDGER)
        .select("due_date,net_owed,owed,store,imei,device_model,status").eq("org_id", org_id))
    if win:
        q = date_window.apply_window(q, "due_date", win)
    rows = q.limit(50000).execute().data or []
    out = []
    for r in rows:
        if want_store and not want_store(r.get("store")):   # canonical identity, not a string eq
            continue
        st = (r.get("store") or "").strip()
        mkt = market_of(st) if market_of else None
        if want_markets and str(mkt or "").lower() not in want_markets:
            continue
        amt = r.get("net_owed")
        if amt is None:
            amt = r.get("owed") or 0
        out.append({"due_date": r.get("due_date"), "owed": _num(amt) or 0.0, "count": 1,
                    "store": st or None, "market": mkt, "imei": r.get("imei"),
                    "device_model": r.get("device_model"), "status": r.get("status")})
    return out


def vendor_rows(client, org_id, win):
    """The processor's own dated amounts, per DUE DATE, plus the entities they were billed to.

    Returns (rows, meta). Never raises: a tenant without the table simply gets no fallback, which is
    reported as no source rather than as nothing owed.
    """
    by_date, entities = {}, set()
    start_at, pages, truncated = 0, 0, False
    try:
        while True:
            q = (client.schema("commcalc").table(VENDOR_FEED)
                 .select("due_date,retail_cost,account_name")
                 .eq("org_id", org_id).not_.is_("due_date", "null"))
            # The SAME window home the ledger path reads — this fallback once carried its own
            # gte/lte pair, which is how one screen ends up with two meanings of "inclusive".
            if win:
                q = date_window.apply_window(q, "due_date", win)
            chunk = q.range(start_at, start_at + PAGE - 1).execute().data or []
            for r in chunk:
                # THE DEBT TEST IS DEREFERENCED, NOT RESTATED. `vendor_invoice.is_debt_line`
                # is its one home and its own docstring says a change to it must reach both grains
                # — yet the read this module absorbed carried an inline `amt <= 0` copy of it, which
                # is the divergence that comment was warning about. One test now, both grains.
                if not _vi.is_debt_line(r):
                    continue
                amt = _num(r.get("retail_cost"))
                if amt is None:
                    continue
                d = str(r.get("due_date") or "")[:10]
                if not d:
                    continue
                e = by_date.setdefault(d, {"due_date": d, "count": 0, "owed": 0.0})
                e["count"] += 1
                e["owed"] += amt
                nm = (r.get("account_name") or "").strip()
                if nm:
                    entities.add(nm)
            if len(chunk) < PAGE:
                break
            start_at += PAGE
            pages += 1
            if pages >= MAX_PAGES:
                truncated = True
                break
    except Exception as e:
        print(f"WARN payables owed_read vendor feed unavailable: {e}")
        return [], {}
    rows = [dict(v, owed=round(v["owed"], 2), store=None, market=None) for v in by_date.values()]
    rows.sort(key=lambda x: x["due_date"])
    return rows, {"entities": sorted(entities), "truncated": truncated}


def dated_owed(client, org_id, win, *, want_store=None, want_markets=None, market_of=None,
               store_filtered=False):
    """THE ONE READ. What is owed, on which date, and which source said so.

    Returns {rows, source, grain, note, entities, truncated}. `rows` carry `due_date`, `owed` and
    `count`; the device path also carries store / market / imei / model / status, and the vendor path
    carries none of those because that feed has none — an absent field is left absent rather than
    filled with a blank that would read as "no store".

    The ledger is tried first and the vendor feed answers only when it produced NOTHING, which for a
    Total / MA tenant is not "you owe nothing" but "no device carries an amount". Both empty is
    `source: None`.
    """
    rows = ledger_rows(client, org_id, win, want_store=want_store, want_markets=want_markets,
                       market_of=market_of)
    if rows:
        return {"rows": rows, "source": SOURCE_DEVICE_LEDGER, "grain": GRAIN_DEVICE,
                "note": None, "entities": [], "truncated": False}
    vrows, vmeta = vendor_rows(client, org_id, win)
    if not vrows:
        return {"rows": [], "source": None, "grain": None, "note": None, "entities": [],
                "truncated": False}
    note = VENDOR_GRAIN_NOTE + (
        " · entities: " + ", ".join(vmeta["entities"]) if vmeta.get("entities") else "") + "."
    if store_filtered:
        note += (" The store filter does not apply here: the feed carries no store — an order is "
                 "placed against the dealer account.")
    return {"rows": vrows, "source": SOURCE_VENDOR_FEED, "grain": GRAIN_ORDER_LINE, "note": note,
            "entities": vmeta.get("entities") or [], "truncated": bool(vmeta.get("truncated"))}
