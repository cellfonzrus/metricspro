"""Device Forecasting & Vendor Payables — HTTP endpoints (module 095).

Mounted at /api/v1/payables (bare router; prefix carried in main.py, like the asset module).
GETs are open for easy curl verification (same convention as /commcalc/sales-diagnostics).
The only mutating endpoint is POST /rebuild.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.asset.router import _norm_imei, _vip_invoice_map, _epay_payments_map
from app.modules.commcalc import date_window
from app.modules.payables import engine
from app.modules.payables import ledger_status as _ls
from app.modules.payables import owed_read as _owed

router = APIRouter()
ORG_ID = "00000000-0000-0000-0000-000000000001"


class PayablesPutSettingsIn(LaxModel):
    priority_ack_enabled: Any = None
    priority_window_pct: Any = None


class UpsertPhoneMapIn(LaxModel):
    raw_model: Any = None
    canonical_model: Any = None
    carrier_id: Any = None
    side: Any = None
PAGE = 1000
DEV_DEPTS = {"android - xp", "iphone - xp", "tablet - xp"}   # device box lines (mig 013 daily_sales_actuals)
DEV_DEPTS_EXACT = ["Android - XP", "IPHONE - XP", "TABLET - XP"]   # exact-case for server-side .in_()


def _is_device_line(dept, category):
    """A physical-phone sale line. The custom 'for Metrics pro' report marks it category='CellPhone'
    (department is blank on that feed); the older format used the 'Android/IPHONE/TABLET - XP' depts.
    Accept EITHER so the forecast counts device units regardless of which report format is flowing."""
    return (str(category or "").strip().lower() == "cellphone") or ((dept or "").strip().lower() in DEV_DEPTS)


def sb():
    return get_supabase()


def _canon(s, alias):
    """Canonical model: strip a ' - promo' suffix + apply the device_model_alias map."""
    base = str(s or "").split(" - ")[0].strip()
    return alias.get(base.lower(), base) or base


# ── STORE / MARKET VOCABULARY (owner report 2026-08-11: "Device Forecasting & Vendor Payables does
#    not pull up anything under market ny and chicago") ───────────────────────────────────────────────
#
# TWO separate defects made every market filter return zero rows:
#
#   1. The rows carried NO `market` FIELD AT ALL. The page's market picker was populated from the org
#      roster (which has markets) while the filter predicate read `row.market` — undefined on every
#      forecast/payables row — so picking NY or Chicago filtered the whole table away.
#
#   2. VOCABULARY SPLIT. `storeops.stores.address` holds "4640 Diversey Chicago" / "2317 Cicero
#      Cicero" / "21880 Hempstead Ave"; the report rows hold "4640-A W Diversey Ave" / "2317 S Cicero
#      Ave STE A" / "218-80 Hempstead Avenue". MEASURED 2026-08-11: **0 of 20 match.** So a store
#      option taken from the roster could never match a row either.
#
# `commcalc.store_mapping` is the resolver for BOTH: its `store_address` is byte-identical to what the
# rows carry (all 20 luxelink values verified present in `raw_sales.store`) AND it carries `market`.
# Same class as the store-code twins and the span keyset vocabulary split — when two tables spell a
# store differently, resolve through the one the DATA uses, never the one the admin UI shows.
def _market_by_store(client, org_id):
    """A store→market RESOLVER — THE canonical union resolution (core.scope.store_market_resolver:
    storeops.stores ∪ commcalc.store_mapping ∪ store_aliases; 2026-09-03 "1115 Liberty Ave"/LI
    class fix — was a store_mapping-only address map). Accepts any spelling the report rows carry
    (address/code/synonym, case+punctuation-insensitive, unambiguous leading street number).
    Degrades to a no-op resolver — an unresolved store renders "(no market)" and stays SELECTABLE,
    never silently dropped."""
    try:
        from app.core import scope as _cscope
        resolve, _ = _cscope.store_market_resolver(client, org_id)
        return resolve
    except Exception as e:
        print(f"WARN payables _market_by_store failed (markets will be blank): {e}")
        return lambda s: ""


# ── STORE IDENTITY (owner report 2026-10-22: "the filter for market brings up the forecasting but
#    the filter for store does not bring up anything") ─────────────────────────────────────────────
#
# SAME CLASS AS THE MARKET DEFECT ABOVE, AND THE REASON ONLY THE STORE HALF WAS STILL BROKEN. The
# market half is RESOLVED: every row gets `market = _market_of(resolver, row.store)`, and that
# resolver accepts any spelling, so picking a market matches rows whatever they call the store. The
# store half was compared as a STRING — the picker's label against `row.store` — so it only ever
# matched the stores whose rows happen to spell themselves the way the roster does.
#
# §13e (2026-10-02) made that failure the normal case rather than the exception: `fold_store_spellings`
# collapses the dropdown to ONE option per physical store, which means for every store spelled two
# ways the surviving option is, by construction, not the spelling the other half of the rows carry.
#
# THE FIX IS THE CLASS, not the store: a store SELECTION binds through the canonical store identity,
# exactly as the market selection already does. Both the rows and the options are stamped with
# `store_key` — `account.store_identity.store_key` over `coa.store_resolver`, THE one home for
# "which store is this string" (§64) — and the comparison is made on that key. Nothing here states a
# spelling rule of its own, so a new alias row teaches this filter and the P&L in the same instant.
def _store_key_resolver(client, org_id):
    """`resolve(raw) -> canonical store spelling` — I/O dereferenced from `coa.store_resolver`, whose
    chain is `store_identity.build_store_resolver` (§64). Degrades to a no-op: an unresolvable store
    keys on its own stripped spelling, so it groups with itself and stays SELECTABLE, never dropped."""
    try:
        from app.modules.account import coa as _coa
        return _coa.store_resolver(client, org_id)
    except Exception as e:
        print(f"WARN payables _store_key_resolver failed (store filter falls back to raw spelling): {e}")
        return None


def _store_key(resolve, store) -> str:
    """The canonical key a store option and a store row are compared ON. One home, read."""
    from app.modules.account import store_identity as _sid
    return _sid.store_key(resolve, store)


def _store_selected(resolve, wanted: str):
    """A predicate for an explicit `?store=` parameter, honoured through the SAME identity as the
    client-side filter. Returns None when nothing was asked for, so a caller can skip the work."""
    if not (wanted or "").strip():
        return None
    want = _store_key(resolve, wanted)
    return lambda st: _store_key(resolve, st) == want


def _market_of(mmap, store):
    if not store:
        return None
    return mmap(store) or None


def _fetch_all(make_query, cap=80):
    out, page = [], 0
    while True:
        chunk = make_query().range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > cap:
            break
    return out


# ── the only mutating endpoint ────────────────────────────────────────────────
@router.post("/rebuild")
def rebuild(carrier_id: str = "", org_id: str = ORG_ID):
    """Rebuild the per-IMEI payable ledger (all carriers, or one) + refresh the discrepancy flags.
    Delete+insert per carrier. May run long on a full Boost rebuild; if the browser 502s it still
    completes server-side (like the commission recalc) — poll /payables/payables afterwards."""
    client = sb()
    # ONE COMPOSITION OF A REBUILD (§COMMCALC-41.2). The post-landing hook rebuilds this ledger too, and a
    # button that rebuilt it differently from the hook would be two answers to one question — so both arrive
    # at `payables/refresh.refresh` and neither knows what a rebuild is made of. `refresh` carries §PAYABLES-01's
    # contract through: when the preflight BLOCKED a carrier it returns `blocked` and does NOT go on to
    # restate the discrepancy flags off a ledger it did not write.
    from app.modules.payables import refresh as _refresh
    built = _refresh.refresh(client, org_id, carrier_id or None)
    # A REFUSED REBUILD IS NOT A SUCCESS (§PAYABLES-01). Before 2026-10-27 this returned 200 with
    # `written: 0` after the build had already emptied a carrier; the delete is now held back until
    # the write is known to be possible, and the remaining half of that defect is this status code —
    # a screen must not be able to render "rebuilt" over a rebuild that did not happen. The ordering that
    # matters (no `sync_payable_flags` after a block) is enforced inside `refresh`, which is where the two
    # steps now live; this raise is what the caller sees.
    if built.get("blocked"):
        raise HTTPException(409, {"message": "rebuild refused — the database is behind the code; "
                                             "nothing was deleted and every carrier is as it was",
                                  "blocked": built["blocked"], "result": built})
    built["auto"] = _build_state(client, org_id)
    return built


@router.get("/auto-build")
def auto_build_state(org_id: str = ORG_ID):
    """When this ledger last rebuilt itself, and whether a rebuild is queued — so the screen can say so
    instead of leaving a tenant to guess whether the button needs pressing (owner 2026-10-27)."""
    return {"auto": _build_state(sb(), org_id)}


def _build_state(client, org_id):
    """The hook's own answer for this build, read through its one home. Never raises into a response."""
    try:
        from app.modules.commcalc import derived_builds as _builds
        return _builds.read_view(client, org_id, "payable_ledger")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Part A — forecast (phones only) ───────────────────────────────────────────
@router.get("/forecast")
def forecast(lookback: int = 7, horizon: int = 7, days: int = 0, store: str = "", carrier: str = "",
             start: str = "", end: str = "", org_id: str = ORG_ID):
    """Phones-only ordering forecast, SEPARATE PER CARRIER. Velocity = units sold inside the SALES
    WINDOW; projected demand = velocity × the next `horizon` days; recommend_order = max(0, projected −
    on_hand). Carrier + canonical model come from the PHONE MAPPING table (commcalc.device_model_alias,
    mig 096); an unmapped model falls back to its source carrier + raw name and is flagged so it can be
    curated (the onboarding to-do). Sales sources: raw_sales (Boost) + raw_ma_commission (Total).
    On-hand: asset_ledger unsold (Boost consignment) — a SNAPSHOT, deliberately not windowed.

    THE SALES WINDOW IS ONE FACT WITH TWO FRONT ENDS (`commcalc/date_window.resolve_window`, the one
    home): `start`/`end` give it explicitly — which is what the shared filter bar's date RANGE sends —
    and `lookback` states the same window as a number of days back from today. Whichever answered,
    velocity is divided by the window's OWN inclusive day count, so the two can never disagree, and
    `window` in the response names which basis was used. On-hand is not windowed: stock on the shelf
    today is not a fact about a past date range.
    """
    client = sb()
    if days:                                     # back-compat: a single `days` sets both windows
        lookback = horizon = days
    horizon = date_window.clamp_days(horizon)
    win = date_window.resolve_window(start, end, lookback)
    lookback = win["days"]                       # the divisor is the WINDOW's day count, never a second number
    # An explicit ?store= is honoured through the canonical store IDENTITY, not a string equality —
    # `raw_sales.store` / `asset_ledger.store` / the roster each spell a store their own way, and a
    # server-side .eq() on one of those spellings is the defect the store filter had (see
    # _store_key_resolver). The scan is therefore unfiltered and the predicate decides.
    skey = _store_key_resolver(client, org_id)
    want_store = _store_selected(skey, store)
    pmap = engine.load_phone_map(client, org_id)
    carriers = client.schema("commcalc").table("carrier").select("id,name,code").eq("org_id", org_id).execute().data or []
    cby = {c["id"]: c.get("name") for c in carriers}

    def _find(sub):
        for c in carriers:
            if sub in (c.get("name") or "").lower() or sub in (c.get("code") or "").lower():
                return c["id"], c.get("name")
        return None, sub.capitalize()
    boost_id, boost_name = _find("boost")
    total_id, total_name = _find("total")
    # Sales-side bounds come from the resolved window, applied through the same helper every other
    # windowed surface uses, so "inclusive at both ends" is stated once (date_window.apply_window).
    _sales_window = date_window.apply_window

    agg = {}   # (carrier, store, canonical) -> row

    def _bucket(raw, default_cid, default_cname, st):
        base = str(raw or "").split(" - ")[0].strip()
        m = pmap.get(base.lower())
        canonical = (m["canonical"] if m and m.get("canonical") else base) or base
        cid = (m["carrier_id"] if m and m.get("carrier_id") else default_cid)
        cname = (cby.get(cid) or default_cname) if cid else default_cname
        key = (cname, st, canonical)
        return agg.setdefault(key, {"carrier": cname, "store": st, "device_model": canonical,
                                    "units": 0, "on_hand": 0, "mapped": bool(m)})

    # velocity — Boost device sales (raw_sales device lines)
    def sq():
        q = (client.schema("commcalc").table("raw_sales")
             .select("store,product_desc,department,category,voided,trans_type,salesperson")
             .eq("org_id", org_id))
        return _sales_window(q, "trans_date", win)
    for r in _fetch_all(sq):
        if (r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
            continue
        if not _is_device_line(r.get("department"), r.get("category")):
            continue
        sp = (r.get("salesperson") or "").strip().lower()
        if not sp or sp == "admin":
            continue
        if want_store and not want_store(r.get("store")):
            continue
        _bucket(r.get("product_desc"), boost_id, boost_name, r.get("store"))["units"] += 1

    # velocity — Total device sales (raw_ma_commission), if the table has rows.
    # A marketplace/MA activation is booked against the DEALER account and carries no store, so it used
    # to bucket under store=None and the whole Total forecast read "—" in the Store column — you could
    # not tell which store the recommended order was FOR (owner report 2026-08-10). Resolve it through
    # the device: IMEI -> the POS line that sold it -> that line's store. Unresolved stays None and
    # renders as "(unassigned)" rather than being attached to a store it wasn't ordered for.
    ma_store = {}
    try:
        for r in _fetch_all(lambda: _sales_window(client.schema("commcalc").table("raw_sales")
                            .select("serial_1,store").eq("org_id", org_id), "trans_date", win)):
            sn = str(r.get("serial_1") or "").strip()
            st = str(r.get("store") or "").strip()
            if sn and st:
                ma_store.setdefault(sn, st)
    except Exception:
        pass
    try:
        # merchant_account_id feeds the mig-314 account→store fallback; a live DB whose
        # raw_ma_commission predates that column still forecasts (defensive two-step select).
        def _maq(cols):
            return lambda: _sales_window(client.schema("commcalc").table("raw_ma_commission")
                                         .select(cols).eq("org_id", org_id), "tx_date", win)
        try:
            ma_rows = _fetch_all(_maq("sku,imei,tx_date,merchant_account_id"))
        except Exception:
            ma_rows = _fetch_all(_maq("sku,imei,tx_date"))
        # 2026-09-04 (owner: "on the luxlink the store name is not showing on the forecasting of
        # the phones"): the POS-line match above starves when the POS feed lags or carries blank
        # serials (luxelink: raw_sales last fed 2026-08-09, serial_1 blank → 0 of 1,120 in-window
        # MA sales resolved). Fall back to the CANONICAL device/account attribution —
        # inventory_aging_device (§11, device grain) then the mig-314 account→store index
        # (engine.ma_store_resolution) — built only when this org actually has MA rows.
        ma_resolve = (lambda i, a=None, pos_store=None: None)
        if ma_rows:
            try:
                ma_resolve, _ = engine.ma_store_resolution(client, org_id)
            except Exception as ex:
                print(f"WARN forecast ma_store_resolution failed: {ex}")
        for r in ma_rows:
            raw_imei = str(r.get("imei") or "").strip()
            st = (ma_store.get(raw_imei)      # POS sale line still wins when it exists
                  or ma_resolve(_norm_imei(raw_imei), r.get("merchant_account_id")))
            if want_store and not want_store(st):   # same identity as the Boost leg, not a string eq
                continue
            _bucket(r.get("sku"), total_id, total_name, st)["units"] += 1
    except Exception:
        pass

    # on-hand — asset_ledger unsold On-Inventory (Boost consignment; the only per-model on-hand we have)
    def oq():
        q = (client.schema("commcalc").table("asset_ledger")
             .select("store,device_model,date_sold,category").eq("org_id", org_id))
        return q
    for r in _fetch_all(oq):
        if r.get("date_sold") or "on inventory" not in (r.get("category") or "").lower():
            continue
        if want_store and not want_store(r.get("store")):
            continue
        _bucket(r.get("device_model"), boost_id, boost_name, r.get("store"))["on_hand"] += 1

    # RULE FIVE: the market filter needs a real value ON THE ROW to filter. Without this the picker
    # offered NY/Chicago and then matched nothing, because `market` did not exist on any row.
    mmap = _market_by_store(client, org_id)
    out = []
    for e in agg.values():
        rate = e["units"] / lookback
        e["avg_daily_velocity"] = round(rate, 2)
        e["projected_demand"] = int(round(rate * horizon))
        e["recommend_order"] = max(0, e["projected_demand"] - e["on_hand"])
        e["market"] = _market_of(mmap, e.get("store"))
        # THE KEY THE STORE FILTER COMPARES ON (owner report 2026-10-22). The row keeps its own
        # spelling for display; `store_key` is what a picked option is matched against, so a store
        # the feeds spell differently from the roster is selectable at last.
        e["store_key"] = _store_key(skey, e.get("store"))
        out.append(e)
    if carrier:
        out = [r for r in out if (r["carrier"] or "").lower() == carrier.lower()]
    out.sort(key=lambda x: (x["recommend_order"], x["units"]), reverse=True)
    return {"lookback": lookback, "horizon": horizon, "store": store or None,
            # The window that was actually measured, named (start/end/days/basis) so the screen can
            # SAY "velocity over 1-31 Oct" instead of implying a range it was never given.
            "window": win,
            "carriers": sorted({r["carrier"] for r in out if r["carrier"]}),
            "unmapped": sum(1 for r in out if not r["mapped"]),
            # How many rows still have no store, so the page can SAY "n rows unassigned" instead of
            # showing a silent '—' the reader has to guess at.
            "unassigned_store": sum(1 for r in out if not r.get("store")),
            "rows": out, "total": len(out)}


# ── Part B — payables + offsets + due ─────────────────────────────────────────
@router.get("/payables")
def list_payables(store: str = "", carrier_id: str = "", status: str = "", limit: int = 500,
                  start: str = "", end: str = "", all_dates: bool = False, org_id: str = ORG_ID):
    """The per-IMEI ledger, optionally narrowed to a DUE-DATE window (`start`/`end`, both ends
    inclusive — the shared filter bar's date range) and to one store.

    THE WINDOW IS DEREFERENCED, not written here (`commcalc/date_window`, the one home), so this
    screen's range means exactly what the Distributor Invoices range and the Daily Owed range mean.
    A ledger row with NO due date is fail-closed out of every window — which is correct and would be
    invisible, so `undated_excluded` REPORTS how many rows the window could not judge rather than
    letting a reader take their absence for "nothing is owed".

    A store is matched through the canonical store identity (see `_store_key_resolver`), never by
    string equality: the scan is therefore unfiltered on store and truncated AFTER the predicate, so
    `limit` still means "this many rows I can see" rather than "this many before filtering".
    """
    client = sb()
    win = _due_window(start, end, all_dates)
    skey = _store_key_resolver(client, org_id)
    want_store = _store_selected(skey, store)
    q = (client.schema("commcalc").table("device_payable_ledger").select("*").eq("org_id", org_id))
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    if status:
        q = q.eq("status", status)
    else:
        # A line that bought no device is not a payable in any state (§COMMCALC-32). Excluded through
        # the one home, so adding a second non-payable status reaches every reader at once. An
        # EXPLICIT status filter is honoured as asked, so the rows stay reachable for an audit.
        q = _ls.exclude_non_payable(q)
    if win:
        q = date_window.apply_window(q, "due_date", win)
    scan = max(limit, 5000) if want_store else limit
    rows = q.order("net_owed", desc=True).limit(scan).execute().data or []
    undated = date_window.undated(rows, "due_date") if win else 0
    if want_store:
        rows = [r for r in rows if want_store(r.get("store"))][:limit]
    # 2026-09-04: fill BLANK stores through the canonical Total/MA attribution (inventory snapshot →
    # mig-314 account index; see engine.ma_store_resolution) at READ time, so the page heals before
    # the next rebuild persists it (measured: 0 of 1,192 luxelink ledger rows carried a store once
    # the POS feed lagged). Only ever fills a blank — a store the build attributed is never touched.
    if any(not (r.get("store") or "").strip() for r in rows):
        try:
            _resolve_ma, _ = engine.ma_store_resolution(client, org_id)
            for r in rows:
                if not (r.get("store") or "").strip():
                    r["store"] = _resolve_ma(_norm_imei(r.get("imei"))) or r.get("store")
        except Exception as ex:
            print(f"WARN payables ma_store_resolution failed: {ex}")
    # decorate with the VIP invoice #/date (join by IMEI == serial)
    vip = _vip_invoice_map(client, org_id, [r.get("imei") for r in rows])
    mmap = _market_by_store(client, org_id)
    for r in rows:
        v = vip.get(_norm_imei(r.get("imei")))
        r["vip_invoice_number"] = v["vip_invoice_number"] if v else None
        r["market"] = _market_of(mmap, r.get("store"))   # RULE FIVE — see _market_by_store
        r["store_key"] = _store_key(skey, r.get("store"))   # the key the store filter compares on
    return {"rows": rows, "total": len(rows),
            "window": win,
            # Fail-closed rows the window could not judge, SAID OUT LOUD (see the docstring).
            "undated_excluded": undated,
            # And the lines left out because no device was bought on them — COUNTED, never silently
            # dropped (§COMMCALC-32). The note names no feed or column, so it is safe on any screen.
            "no_device_excluded": _no_device_count(client, org_id, carrier_id) if not status else 0,
            "no_device_note": _ls.EXCLUDED_NOTE,
            "unassigned_store": sum(1 for r in rows if not (r.get("store") or "").strip())}


def _no_device_count(client, org_id, carrier_id=""):
    """How many ledger lines bought no device — the number the screen SAYS instead of the rows it
    leaves out. ADAPTIVE: any failure reports None, which a screen renders as "not measured" rather
    than as a confident zero."""
    try:
        q = (client.schema("commcalc").table("device_payable_ledger").select("imei", count="exact")
             .eq("org_id", org_id).eq("status", _ls.STATUS_NO_DEVICE).limit(1))
        if carrier_id:
            q = q.eq("carrier_id", carrier_id)
        return q.execute().count
    except Exception as e:                                        # pragma: no cover - defensive
        print(f"WARN payables no_device count failed: {e}")
        return None


def _due_window(start="", end="", all_dates=False):
    """WHICH DAYS A PAYMENT SCREEN OPENS ON — one answer, through the window home (§COMMCALC-32).

    Owner 2026-10-26: the screen should pick today through three days ahead by itself. So an absent
    date range is no longer "every row ever": it is the default ahead window, which the response
    RETURNS so the pickers render what was actually applied instead of holding a second copy of the
    default. An explicit range is honoured as given, and `all_dates=true` is the escape that asks for
    no window at all — without it there would be no way back to the full list."""
    if all_dates:
        return None
    if start or end:
        return date_window.resolve_window(start, end, 0)
    return date_window.ahead_window(datetime.now(timezone.utc).date())


@router.get("/filter-options")
def payables_filter_options(org_id: str = ORG_ID):
    """Store + market options for THIS module's filter bar, in the vocabulary the rows actually use.

    Deliberately NOT `/core/filter-options`: that one keys on `storeops.stores.address`, and MEASURED
    2026-08-11 **0 of luxelink's 20 stores** match what the payables/forecast rows carry — so every
    option it offered was unselectable here. `commcalc.store_mapping.store_address` is the spelling the
    data uses. Pick-don't-type (RULE THREE): every value is a real org row.

    2026-09-04 (owner "B-1115/LI — fixed as a design"): store_mapping is PREFERRED for spelling but is
    no longer the ONLY roster — a store that exists only in storeops.stores (live house B-1115 "1115
    Liberty Ave", market LI: NO store_mapping row, yet 1,000 raw_sales rows carry exactly its storeops
    spelling) was absent from this dropdown entirely. Stores are now the canonical UNION index
    (core.scope.market_index: store_mapping spelling first, storeops address for mapping-less stores),
    and `markets` is the canonical vocabulary ∪ the roster's stamps (core.scope §13c enumeration
    doctrine) so a market typed on a brand-new store appears here with zero extra setup."""
    client = sb()
    try:
        rows = (client.schema("commcalc").table("store_mapping")
                .select("store_address,market").eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception as e:
        print(f"WARN payables filter-options failed: {e}")
        rows = []
    # Market stamped per store through THE canonical union resolver (2026-09-03 LI class fix) — the
    # STORE option vocabulary stays store_mapping.store_address FIRST (measured 2026-08-11:
    # that is the spelling the payables/forecast rows actually carry; see the docstring above).
    _resolve_market = _market_by_store(client, org_id)
    seen, stores, markets = set(), [], set()
    for r in rows:
        addr = (r.get("store_address") or "").strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        mkt = (r.get("market") or "").strip() or _resolve_market(addr) or None
        stores.append({"store": addr, "market": mkt})
        if mkt:
            markets.add(mkt)
    # ADDITIVE: union-index stores with NO store_mapping row (the B-1115 shape) — offered under their
    # storeops address (which is what the sales feeds carry for such stores; the row filter resolves
    # any spelling through the same union anyway). Existing options above are byte-identical.
    source = "store_mapping"
    try:
        from app.core import scope as _cscope
        for s in (_cscope.market_index(client, org_id).get("stores") or []):
            addr = (s.get("address") or s.get("store_code") or "").strip()
            if not addr or addr.lower() in seen:
                continue
            seen.add(addr.lower())
            mkt = (s.get("market") or "").strip() or _resolve_market(addr) or None
            stores.append({"store": addr, "market": mkt})
            if mkt:
                markets.add(mkt)
        market_list = _cscope.org_market_options(client, org_id, markets)
        source = "store_mapping+union"
    except Exception as e:
        print(f"WARN payables filter-options union overlay failed: {e}")
        market_list = sorted(markets)
    if not stores and not market_list:
        return {"stores": [], "markets": [], "source": "unavailable"}
    stores.sort(key=lambda s: s["store"])
    # EVERY OPTION CARRIES THE KEY THE ROWS ARE COMPARED ON (owner report 2026-10-22). The label
    # stays the roster's spelling — that is what a human recognises — but what a selection BINDS is
    # the canonical identity, so the §13e fold (one option per physical store) can no longer leave
    # the surviving option unable to match the other spelling's rows.
    _skey = _store_key_resolver(client, org_id)
    for _s in stores:
        _s["store_key"] = _store_key(_skey, _s["store"])
    # ONE OPTION PER PHYSICAL STORE (owner 2026-10-02). The dedupe above is EXACT-STRING, so one
    # store spelled two ways across the two vocabularies was still offered twice (measured live
    # that day: 32 options for 31 stores — "1 S 60th street" and "1 S 60th St, Philadelphia").
    # Which spellings are one store has ONE home (`build_market_index.code_groups`); this folds
    # through it rather than re-deriving it, and deliberately uses `fold_store_spellings` rather
    # than the canonical composer so THIS module's measured spelling preference (store_mapping
    # first — see the docstring) still picks the label. Additive and fail-soft as everywhere else.
    try:
        from app.core import scope as _cscope
        keep = set(_cscope.fold_store_spellings(_cscope.market_index(client, org_id),
                                                [s["store"] for s in stores]))
        stores = [s for s in stores if s["store"] in keep]
    except Exception as e:
        print(f"WARN payables filter-options store fold failed: {e}")
    return {"stores": stores, "markets": market_list, "source": source}


@router.get("/offsets/{imei}")
def offsets(imei: str, org_id: str = ORG_ID):
    """Drill-down: exactly what offsets the owed amount for one device (owed line + primary rebate +
    each ePay reimbursement) with the cross-check mismatch surfaced."""
    client = sb()
    n = _norm_imei(imei)
    led = (client.schema("commcalc").table("device_payable_ledger").select("*")
           .eq("org_id", org_id).eq("imei", n).limit(1).execute().data) or []
    if not led:
        raise HTTPException(404, f"No payable ledger row for IMEI {n} (rebuild first?)")
    row = led[0]
    reimb_types = engine._reimb_types(client, org_id)
    epay = _epay_payments_map(client, org_id, [n]).get(n, [])
    epay_lines = [e for e in epay if (e.get("type") or "").lower() in reimb_types
                  or "reimb" in (e.get("type") or "").lower()]
    return {
        "imei": n, "store": row.get("store"), "device_model": row.get("device_model"),
        "status": row.get("status"), "owed": row.get("owed"), "owed_source": row.get("owed_source"),
        # The drill-down is an AUDIT view and deliberately still answers for a line the lists leave
        # out, saying which it is rather than 404-ing on it (§COMMCALC-32).
        "is_payable": _ls.is_payable(row.get("status")),
        "primary_rebate": {"amount": row.get("rebate_amount"), "date": row.get("rebate_date"),
                           "source": row.get("rebate_source")},
        "epay_crosscheck": {"amount": row.get("epay_rebate_amount"), "mismatch": row.get("rebate_mismatch"),
                            "lines": epay_lines},
        "net_offset": row.get("net_offset"), "net_owed": row.get("net_owed"),
        "sold": row.get("sold_flag"), "sold_date": row.get("sold_date"),
        "due_date": row.get("due_date"), "due_source": row.get("due_source"),
    }


@router.get("/due")
def due(as_of: str = "", store: str = "", carrier_id: str = "", org_id: str = ORG_ID):
    """DUE report for a billing Friday — grouped by bill_path, mirroring /asset/owed-weekly's aging/sold
    buckets. Because build_ledger copies billing_friday/bill_path/owed verbatim from asset_ledger, the
    aging OWED total here equals get_owed_weekly.due_this_week.aging.owed for the same date."""
    if not as_of:
        raise HTTPException(400, "as_of=<billing friday YYYY-MM-DD> required")
    client = sb()
    q = _ls.exclude_non_payable(                       # §COMMCALC-32, same reason as the list above
        client.schema("commcalc").table("device_payable_ledger")
        .select("store,bill_path,owed,imei,device_model,due_date")
        .eq("org_id", org_id).eq("billing_friday", as_of))
    if carrier_id:
        q = q.eq("carrier_id", carrier_id)
    # SIBLING OF THE SAME DEFECT (2026-10-22). This endpoint narrowed by store with a string equality
    # too, so a store the feeds spell differently from the roster returned an empty DUE report. Same
    # identity, same one home — fixed here in the same change rather than left for the next report to
    # rediscover.
    want_store = _store_selected(_store_key_resolver(client, org_id), store)
    rows = q.limit(20000).execute().data or []
    agg = {"aging": {"count": 0, "owed": 0.0}, "billed": {"count": 0, "owed": 0.0}}
    for r in rows:
        if want_store and not want_store(r.get("store")):
            continue
        b = "aging" if r.get("bill_path") == "aging" else "billed"
        agg[b]["count"] += 1
        agg[b]["owed"] += float(r.get("owed") or 0)
    for b in agg:
        agg[b]["owed"] = round(agg[b]["owed"], 2)
    agg["total"] = {"count": agg["aging"]["count"] + agg["billed"]["count"],
                    "owed": round(agg["aging"]["owed"] + agg["billed"]["owed"], 2)}
    return {"as_of": as_of, "buckets": agg, "rows": rows,
            "note": "aging.owed matches /api/v1/asset/owed-weekly due_this_week.aging.owed"}


@router.get("/owed-by-date")
def owed_by_date(start: str = "", end: str = "", store: str = "", all_dates: bool = False,
                 market: str = "", org_id: str = ORG_ID):
    """Consolidated daily owed: net_owed grouped by due_date.

    Two possible sources, and the response always names which one answered:
      • `device_ledger` — the per-IMEI payable ledger (Boost/VIP: owed_to_vip + the VIP invoice dates);
      • `vendor_feed`  — the processor's own `raw_ma_daily_tx.due_date` + amounts, used when the ledger
        carries no amounts at all (every Total/MA tenant). Per ORDER LINE, not per device.
    """
    client = sb()
    # The window is the SAME one home every other surface here reads, so "both ends inclusive" is
    # stated once (date_window) instead of three times as a gte/lte pair.
    win = _due_window(start, end, all_dates)
    skey = _store_key_resolver(client, org_id)
    want_store = _store_selected(skey, store)
    # The market half of the same filter bar, through the SAME market home the list uses (RULE FIVE),
    # so "North" means one thing on both surfaces.
    mmap = _market_by_store(client, org_id)
    # The bar's market half is MULTI-select (StandardFilterValue.markets is a list), so this accepts a
    # comma-separated list and matches ANY of them. One value is just a list of one — there is no
    # second single-market code path to drift from.
    want_markets = {m.strip().lower() for m in str(market or "").split(",") if m.strip()} or None
    # THE MONEY IS READ ONCE, HERE AND FOR THE WEEKLY ROLL-UP ALIKE (§COMMCALC-34). This endpoint used
    # to hold the ledger read, the status filter, the amount precedence and the vendor-feed fallback
    # itself — and `Weekly Owed` could not reuse any of it, which is exactly why that report read one
    # carrier's own column instead. All of it now lives in `payables/owed_read`; what is left here is
    # the GROUPING BY DATE, which is this surface's own question.
    got = _owed.dated_owed(client, org_id, win, want_store=want_store, want_markets=want_markets,
                           market_of=(lambda st: _market_of(mmap, st)),
                           store_filtered=bool(store))
    if got["source"] == _owed.SOURCE_DEVICE_LEDGER:
        by_date = {}
        for r in got["rows"]:
            d = r.get("due_date")
            if not d:
                continue
            amt = r.get("owed") or 0
            e = by_date.setdefault(d, {"due_date": d, "count": 0, "owed": 0.0, "_by_store": {}})
            e["count"] += 1
            e["owed"] += float(amt)
            # WHO OWES WHAT ON THIS DATE (owner 2026-10-26: "who owes what for eveyr date mentioned on
            # that list , to make sure thee correct account is debited"). A date total cannot answer
            # that — the money is paid per store, so the per-store split rides WITH the date rather
            # than needing a second request that could be filtered differently and disagree.
            st = (r.get("store") or "").strip()
            skey_v = _store_key(skey, st)
            b = e["_by_store"].setdefault(skey_v or "", {
                "store": st or None, "store_key": skey_v, "market": r.get("market"),
                "count": 0, "owed": 0.0})
            b["count"] += 1
            b["owed"] += float(amt)
        out = []
        for v in by_date.values():
            stores = sorted(v.pop("_by_store").values(), key=lambda x: -x["owed"])
            out.append(dict(v, owed=round(v["owed"], 2),
                            by_store=[dict(b, owed=round(b["owed"], 2)) for b in stores],
                            stores=len(stores)))
        out.sort(key=lambda x: x["due_date"])
        return {"rows": out, "total_owed": round(sum(x["owed"] for x in out), 2),
                "source": got["source"], "grain": got["grain"], "window": win,
                # The unattributed money, said out loud: a store the build could not resolve cannot
                # be debited, so it is never folded into another store's figure.
                "unassigned_owed": round(sum(b["owed"] for x in out for b in x["by_store"]
                                             if not b["store"]), 2)}

    # The ledger produced nothing — which for a Total/MA tenant is not "you owe nothing", it is "no
    # device carries an amount". The shared read has already fallen back to the processor's own dated
    # amounts; it SAYS so, including the grain change, rather than showing an empty table.
    if not got["rows"]:
        return {"rows": [], "total_owed": 0.0, "source": None, "grain": None}
    rows = [{k: v for k, v in r.items() if k in ("due_date", "count", "owed")}
            for r in got["rows"]]
    return {"rows": rows, "total_owed": round(sum(x["owed"] for x in rows), 2),
            "source": got["source"], "grain": got["grain"], "note": got["note"],
            "entities": got["entities"]}


# ── Part C — per-store priority list ──────────────────────────────────────────
@router.get("/priority")
def priority(store: str = "", employee_id: str = "", org_id: str = ORG_ID):
    """Per-store priority-sell list (devices in the final pct% of their pay window)."""
    client = sb()
    return {"store": store or None, "rows": engine.priority_for_store(client, org_id, store)}


# ── config: per-carrier payable source mapping (add-a-carrier-by-config) ──────
@router.get("/source-maps")
def list_source_maps(org_id: str = ORG_ID):
    client = sb()
    return {"rows": (client.schema("commcalc").table("payable_source_map").select("*")
                     .eq("org_id", org_id).execute().data) or []}


@router.post("/source-maps")
def upsert_source_map(body: dict, org_id: str = ORG_ID):
    client = sb()
    row = dict(body or {})
    row["org_id"] = org_id
    if not row.get("carrier_id") or not row.get("source_table") or not row.get("imei_field"):
        raise HTTPException(400, "carrier_id, source_table, imei_field are required")
    r = (client.schema("commcalc").table("payable_source_map")
         .upsert(row, on_conflict="org_id,carrier_id").execute())
    return {"saved": True, "row": (r.data or [None])[0]}


@router.delete("/source-maps/{map_id}")
def delete_source_map(map_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema("commcalc").table("payable_source_map").delete() \
        .eq("org_id", org_id).eq("id", map_id).execute()
    return {"deleted": True}


# ── per-tenant settings: the clock-in priority-ack gate + the priority window % ────────────────────
@router.get("/settings")
def get_settings(org_id: str = ORG_ID):
    client = sb()
    t = (client.schema("storeops").table("tenants")
         .select("priority_ack_enabled,priority_window_pct").eq("org_id", org_id).limit(1).execute().data) or []
    r = t[0] if t else {}
    return {"priority_ack_enabled": bool(r.get("priority_ack_enabled")),
            "priority_window_pct": r.get("priority_window_pct") if r.get("priority_window_pct") is not None else 25}


@router.put("/settings")
def put_settings(body: PayablesPutSettingsIn, org_id: str = ORG_ID):
    client = sb()
    upd = {}
    if "priority_ack_enabled" in body.model_fields_set:
        upd["priority_ack_enabled"] = bool(body.priority_ack_enabled)
    if "priority_window_pct" in body.model_fields_set:
        try:
            upd["priority_window_pct"] = max(1, min(100, int(body.priority_window_pct)))
        except Exception:
            pass
    if upd:
        client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return {"saved": True, **upd}


# ── phone mapping table (raw model → canonical + carrier) — the forecast alignment + onboarding to-do ──
def _carriers(client, org_id):
    return client.schema("commcalc").table("carrier").select("id,name,code").eq("org_id", org_id).execute().data or []


def _find_carrier(carriers, sub):
    for c in carriers:
        if sub in (c.get("name") or "").lower() or sub in (c.get("code") or "").lower():
            return c["id"], c.get("name")
    return None, sub.capitalize()


@router.get("/phone-map")
def list_phone_map(org_id: str = ORG_ID):
    client = sb()
    rows = (client.schema("commcalc").table("device_model_alias").select("*")
            .eq("org_id", org_id).order("raw_model").execute().data) or []
    carriers = _carriers(client, org_id)
    cby = {c["id"]: c.get("name") for c in carriers}
    for r in rows:
        r["carrier_name"] = cby.get(r.get("carrier_id"))
    return {"rows": rows, "carriers": carriers}


@router.get("/phone-map/candidates")
def phone_map_candidates(limit: int = 300, days: int = 180, org_id: str = ORG_ID):
    """Distinct raw model strings seen in sales + inventory that are NOT yet in the phone map — the
    onboarding to-do list. Each carries a suggested side + carrier and a frequency (map the big ones first)."""
    client = sb()
    mapped = {(r.get("raw_model") or "").strip().lower()
              for r in (client.schema("commcalc").table("device_model_alias").select("raw_model")
                        .eq("org_id", org_id).execute().data or [])}
    carriers = _carriers(client, org_id)
    boost_id, boost_name = _find_carrier(carriers, "boost")
    total_id, total_name = _find_carrier(carriers, "total")
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()
    cand = {}

    def _add(raw, side, cid, cname):
        base = str(raw or "").split(" - ")[0].strip()
        if not base or base.lower() in mapped:
            return
        e = cand.setdefault(base.lower(), {"raw_model": base, "side": side, "carrier_id": cid,
                                           "carrier": cname, "count": 0})
        e["count"] += 1

    for r in _fetch_all(lambda: client.schema("commcalc").table("raw_sales").select("product_desc")
                        .eq("org_id", org_id).eq("category", "CellPhone").gte("trans_date", cutoff)):
        _add(r.get("product_desc"), "sales", boost_id, boost_name)          # custom report phone lines
    for r in _fetch_all(lambda: client.schema("commcalc").table("raw_sales").select("product_desc")
                        .eq("org_id", org_id).in_("department", DEV_DEPTS_EXACT).gte("trans_date", cutoff)):
        _add(r.get("product_desc"), "sales", boost_id, boost_name)          # legacy-format phone lines
    try:
        for r in _fetch_all(lambda: client.schema("commcalc").table("raw_ma_commission").select("sku")
                            .eq("org_id", org_id).gte("tx_date", cutoff)):
            _add(r.get("sku"), "sales", total_id, total_name)
    except Exception:
        pass
    for r in _fetch_all(lambda: client.schema("commcalc").table("asset_ledger").select("device_model")
                        .eq("org_id", org_id).ilike("category", "%On Inventory%").is_("date_sold", "null")):
        _add(r.get("device_model"), "inventory", boost_id, boost_name)

    out = sorted(cand.values(), key=lambda x: x["count"], reverse=True)[:limit]
    return {"rows": out, "total_unmapped": len(cand), "carriers": carriers}


@router.post("/phone-map")
def upsert_phone_map(body: UpsertPhoneMapIn, org_id: str = ORG_ID):
    client = sb()
    raw = (body.raw_model or "").strip()
    if not raw:
        raise HTTPException(400, "raw_model required")
    row = {"org_id": org_id, "raw_model": raw,
           "canonical_model": (body.canonical_model or raw).strip(),
           "carrier_id": body.carrier_id or None, "side": body.side, "source": "manual"}
    r = (client.schema("commcalc").table("device_model_alias")
         .upsert(row, on_conflict="org_id,raw_model").execute())
    return {"saved": True, "row": (r.data or [None])[0]}


@router.delete("/phone-map/{map_id}")
def delete_phone_map(map_id: str, org_id: str = ORG_ID):
    client = sb()
    client.schema("commcalc").table("device_model_alias").delete() \
        .eq("org_id", org_id).eq("id", map_id).execute()
    return {"deleted": True}
