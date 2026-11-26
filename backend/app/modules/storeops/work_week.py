"""WORK WEEK — the ONE home for "which seven days is a week here, and which days is this store open".

OWNER 2026-11-26 (Sanjot), verbatim: *"Work week for all companies starts on a Monday and the nds kn
a Sunday , luxelink Chicago stores start on Thursday and end on Wednesday , luxelink ny stores are
Monday to Sunday"* and *"All store open 7 days a week"*.

THE INSTANCE. `storeops.tenants.work_week_start_dow` (mig 085) holds ONE week start per tenant.
Luxelink is one tenant with two markets that ARE its two companies, and its column reads 3
(Thursday). Measured on production 2026-11-26: that is right for its 10 open Chicago stores and
wrong for its 10 open NY stores, which the owner says run Mon–Sun. The same number anchors the
schedule grid, the weekly hours budget, the period an approvals board opens on, and the grid the
rota is emailed as, so one wrong answer is wrong in four places at once.

THE CLASS, which is what this module fixes:

  • A FACT MODELLED AT THE WRONG GRAIN cannot be stated at all. The second answer had nowhere to
    live. The grain widens to the MARKET (mig 1132 `storeops.market_work_week`) with the tenant
    behind it; special-casing one tenant in code would be the per-tenant CODE that RULE TWO forbids.

  • THE SAME FACT READ FROM FIVE PLACES. Before this module, `work_week_start_dow` was selected off
    `tenants` in `payroll_approval._week_start_dow`, in `storeops/router.py` TWICE (the
    tenant-settings read and a private `_work_week_bounds` copy), in `payroll_run_api.py` and in
    `schedule_week_api.py`. Widening four of them would have left the fifth answering the old way,
    which is the "one fixed and the other not" defect CLAUDE.md calls the same defect wearing a hat.
    `backend/harness_work_week.py` fails the build if any module outside this one reads that column
    or the new table.

WHAT IS AND IS NOT DEFAULTED. Monday is the week start's documented HOUSE DEFAULT (mig 085) and
stays one: the 2026-10-30 directive names a policy — a rate, a window, a switch — as exactly where a
house default is right. `org_id` is a REQUIRED argument, so a caller that has not asked which tenant
cannot get an answer. Open days are the opposite: they are a tenant's own statement about its own
stores, so NOTHING stands behind them. Undeclared returns `()` with `declared=False`, and the
scheduling flow then NAMES the days with nobody on rather than calling them gaps (§STOREOPS-15).

DEGRADES UNTIL ITS MIGRATION LANDS (§POS-01). Code ships on merge and a migration waits for the
owner, so every read here falls back to the tenant column when `market_work_week` or `open_dows` is
not there yet — and says which level answered, so a surface can always state where its week came
from instead of implying a declaration that does not exist.

PURE CORE, THIN I/O. `resolve_start_dow` and `resolve_open_dows` decide everything from rows handed
to them and touch no database; the two `*_for` functions are the only I/O. Proof:
`backend/harness_work_week.py` (DB-free) + `backend/tests/unit/test_work_week.py`.
"""
from app.core.database import get_supabase

#: The house default week start — Monday. A POLICY default, not a tenant's vocabulary (see above).
HOUSE_START_DOW = 0

#: Where an answer came from, so a surface can say so rather than imply a declaration.
LEVEL_MARKET = "market"
LEVEL_TENANT = "tenant"
LEVEL_STORE = "store"
LEVEL_HOUSE = "house"

#: Every weekday, 0=Mon .. 6=Sun. Used to validate a declaration, never as a default for one.
WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)


def sb():
    """SCHEMA-SCOPED to `storeops`, like every other module here. An unscoped client reads `public`,
    finds nothing, and a missing declaration becomes a confident house answer."""
    return get_supabase().schema("storeops")


def _market_key(value) -> str:
    """A market compared the way the roster spells it, case-folded. A market typed 'chicago' and a
    market resolved 'Chicago' are the SAME market: comparing them raw is how a second spelling
    becomes a phantom entity (§13e, and the store-code case defect of 2026-07)."""
    return str(value or "").strip().casefold()


def _dow(value):
    """A weekday or None. A value outside 0..6 is a typo, and a typo must not silently become
    Monday — it returns None so the caller falls through to the next level and reports it."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 6 else None


def _dow_set(value):
    """A declared weekday set, or None for UNDECLARED. An EMPTY list is a real declaration ("open no
    day") and stays distinguishable from NULL, because "we have not said" and "never" must not read
    the same. A set containing a non-weekday is refused whole rather than silently trimmed."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    out = []
    for v in items:
        d = _dow(v)
        if d is None:
            return None
        out.append(d)
    return tuple(sorted(set(out)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE PURE CORE — every decision, no I/O
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def resolve_start_dow(*, market_rows, tenant_dow, market=None):
    """Which weekday the work week starts on, and WHERE that answer came from.

    `market_rows` is this tenant's `market_work_week` rows (each `{market, work_week_start_dow}`);
    `tenant_dow` is `storeops.tenants.work_week_start_dow`; `market` is the store's canonical
    market, or None when the caller is asking about the tenant as a whole.

    Returns `{"dow", "level", "declared"}`. The order is MARKET, then TENANT, then the house Monday,
    and `declared` is False only for the house answer — because a tenant that has set its own week
    HAS declared one, even when it happens to equal Monday.
    """
    want = _market_key(market)
    if want:
        for row in (market_rows or ()):
            if _market_key((row or {}).get("market")) != want:
                continue
            d = _dow((row or {}).get("work_week_start_dow"))
            if d is not None:
                return {"dow": d, "level": LEVEL_MARKET, "declared": True}
    d = _dow(tenant_dow)
    if d is not None:
        return {"dow": d, "level": LEVEL_TENANT, "declared": True}
    return {"dow": HOUSE_START_DOW, "level": LEVEL_HOUSE, "declared": False}


def resolve_open_dows(*, store_dows, tenant_dows):
    """Which weekdays a store is expected to have somebody on, and where that came from.

    The store's own declaration wins, then the tenant's. NOTHING stands behind them: with neither
    declared this answers `()` with `declared=False`, which is what makes the scheduling flow NAME
    an uncovered day instead of calling it a gap. A declared EMPTY set is `declared=True` with no
    days, which is a different answer and must stay different.
    """
    s = _dow_set(store_dows)
    if s is not None:
        return {"dows": s, "level": LEVEL_STORE, "declared": True}
    t = _dow_set(tenant_dows)
    if t is not None:
        return {"dows": t, "level": LEVEL_TENANT, "declared": True}
    return {"dows": (), "level": None, "declared": False}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE I/O — the only two functions here that touch a database
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _market_rows(org_id):
    """This tenant's declared market week starts, or () when the table is not there yet. An
    unreadable table degrades to the tenant's own answer (§POS-01); it never raises into a caller
    that was asking a calendar question."""
    try:
        return (sb().table("market_work_week").select("market,work_week_start_dow")
                .eq("org_id", org_id).execute().data) or ()
    except Exception:
        return ()


def _tenant_row(org_id, fields):
    try:
        rows = (sb().table("tenants").select(fields)
                .eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def start_dow_for(org_id, *, market=None, store_code=None, client=None):
    """The work-week start for one tenant, or for one of its markets / stores.

    `org_id` is REQUIRED. Pass `market` when the caller already knows it (the roster row carries it),
    or `store_code` to have it resolved through `app/core/scope` — the canonical market index (§14x),
    never a vocabulary of this module's own. Returns `resolve_start_dow`'s dict.
    """
    if store_code and market is None:
        market = market_of(org_id, store_code, client=client)
    return resolve_start_dow(
        market_rows=_market_rows(org_id),
        tenant_dow=_tenant_row(org_id, "work_week_start_dow").get("work_week_start_dow"),
        market=market,
    )


def market_of(org_id, store_code, *, client=None):
    """This store's CANONICAL market, through `app/core/scope.market_by_code` — the union index every
    store/market-filtering report resolves through. '' when it cannot be resolved, which falls the
    caller through to the tenant's answer rather than guessing a market."""
    try:
        from app.core.scope import market_by_code
        by_code = market_by_code(client or get_supabase(), org_id) or {}
        return by_code.get(str(store_code or "").strip().upper(), "")
    except Exception:
        return ""


def open_dows_for(org_id, store_code):
    """Which weekdays this store is expected to be covered. Returns `resolve_open_dows`'s dict."""
    store = {}
    try:
        rows = (sb().table("stores").select("store_code,open_dows")
                .eq("org_id", org_id).eq("store_code", store_code).limit(1).execute().data) or []
        store = rows[0] if rows else {}
    except Exception:
        store = {}
    return resolve_open_dows(store_dows=store.get("open_dows"),
                             tenant_dows=_tenant_row(org_id, "open_dows").get("open_dows"))


def open_dows_map(org_id, store_codes=None):
    """`{store_code: resolve_open_dows(...)}` for a whole board in one read, so a page with twenty
    store-weeks does not make twenty round trips. The tenant's default is read once."""
    tenant_dows = _tenant_row(org_id, "open_dows").get("open_dows")
    rows = ()
    try:
        q = sb().table("stores").select("store_code,open_dows").eq("org_id", org_id)
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.execute().data or ()
    except Exception:
        rows = ()
    out = {}
    for r in rows:
        code = str((r or {}).get("store_code") or "")
        if code:
            out[code] = resolve_open_dows(store_dows=(r or {}).get("open_dows"),
                                          tenant_dows=tenant_dows)
    for code in (store_codes or ()):
        out.setdefault(str(code), resolve_open_dows(store_dows=None, tenant_dows=tenant_dows))
    return out
