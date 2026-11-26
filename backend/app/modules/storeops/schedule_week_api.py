"""THE ENDPOINTS for scheduling a store-week as a guided, resumable flow (index §STOREOPS-15).

Owner 2026-11-25: *"...and then simlar workflow for scheduling"*, said of the daily-closing
workflow a new person can start and come back to.

EVERY DECISION ALREADY LIVES IN A PURE MODULE AND IS DEREFERENCED HERE, never re-derived:

    the rail, lamps, resume, progress     `core/guided_workflow`              (index §CORE-51)
    what each of the five steps means     `storeops/schedule_week_flow`       (index §STOREOPS-15)
    a shift's hours, a week's total       `storeops/schedule_hours`
    which seven days a week IS            `storeops/payroll_approval._week_start_dow` — the TENANT'S
                                          declared `work_week_start_dow`, never a house Monday
    which stores reach this login         `storeops/router.scope_keyset`      (index §14x)
    is this store still open for business `core/roster_visibility`            (index §CORE-45)

So this file is I/O and HTTP shape, and NOTHING here writes. The screen it feeds sends people to
the doors that already exist (the hours-budget page, the time-off board, the schedule grid), because
a workflow that typed a shift again would be a second writer for a row that already has one.
"Resume where you left off" needs no saved draft when the state IS the data.

THE TENANT RULE (CLAUDE.md, owner 2026-10-30). `week_start_dow` is the organisation's OWN
declaration and is a REQUIRED argument to the flow — a Monday standing in for a tenant whose week
starts on Thursday mis-buckets every shift at the week boundary. `open_dows` (which days a store is
expected to have somebody on) is NOT declared anywhere in the platform today, so it is sent empty
and the coverage step NAMES the days with nobody on without calling them a gap. Nothing declared
means nothing identified, and the payload says so.

DEGRADES BY NAME, NEVER 500s. Each read reports itself in `blockers` when it fails, because "we
could not look" and "there is nothing to schedule" must never print the same.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Header, HTTPException, Response

from app.core.database import get_supabase
from app.modules.core import guided_workflow as gw
from app.modules.core import roster_visibility as _rv
from app.modules.storeops import schedule_week_flow as _flow

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"

#: The shift columns this surface needs. `scheduled_hours` is NOT among them: a week's hours come
#: from `schedule_hours`, which reads the times — the one home (§STOREOPS-15).
SHIFT_FIELDS = ("id,store_code,employee_id,employee_name,shift_date,start_time,end_time,"
                "is_deleted")


def sb():
    """SCHEMA-SCOPED to `storeops`, like `storeops/router.sb()`. An unscoped client reads `public`,
    finds nothing, and renders an empty board — absence showing up as a clean screen."""
    return get_supabase().schema("storeops")


def _s(v) -> str:
    return str(v or "").strip()


def _day(v):
    return _flow._day(v)


def _rows(table, select, org_id, blockers, *, schema=None, **eqs):
    """A failed read is REPORTED, never silently empty."""
    try:
        client = get_supabase().schema(schema) if schema else sb()
        q = client.table(table).select(select).eq("org_id", org_id)
        for k, v in eqs.items():
            q = q.eq(k, v)
        return q.execute().data or []
    except Exception as e:
        blockers.append({"table": table, "reason": type(e).__name__})
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ORGANISATION'S DECLARATIONS
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _declarations(org_id, blockers) -> dict:
    """The tenant's own week, and the opening days it has not declared.

    `week_start_dow` is read through the ONE home that already answers it
    (`payroll_approval._week_start_dow`, which documents why the column is already 0=Monday), so
    this board and the payroll board can never disagree about which seven days a week is.
    """
    from app.modules.storeops.payroll_approval import _week_start_dow
    declared_week = False
    try:
        rows = (sb().table("tenants").select("work_week_start_dow")
                .eq("org_id", org_id).limit(1).execute().data) or []
        declared_week = bool(rows) and rows[0].get("work_week_start_dow") is not None
    except Exception as e:
        blockers.append({"table": "tenants", "reason": type(e).__name__})
    return {"week_start_dow": _week_start_dow(org_id),
            "week_start_declared": declared_week,
            # NOT DECLARED ANYWHERE TODAY — see the module header. Sent empty on purpose, so the
            # coverage step reports uncovered days rather than judging them.
            "open_dows": ()}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FACTS, read once for the whole board
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _stores(org_id, blockers, *, include_inactive=False, store_code=None):
    """The roster, read once. `market` is carried for DISPLAY beside a store the caller already
    chose — nothing here filters or buckets by it, because the board's market filter is
    `router.scope_keyset` (§14x), the canonical resolution. Pinned ROSTER in
    `harness_market_resolution_guard.py`; overlaying the canonical map here would be a second
    answer to a question already answered upstream."""
    eqs = {"store_code": store_code} if store_code else {}
    rows = _rows("stores", "store_code,address,market,is_active", org_id, blockers, **eqs)
    hidden = _rv.hidden_count(rows) if rows else 0
    return _rv.visible(rows, include_inactive=include_inactive), hidden


def _home_stores(org_id, blockers) -> dict:
    """{employee_id: home store}. Read ONLY so a time-off request for somebody with no shift yet is
    attributed to the store that would schedule them — a pending request is otherwise either lost
    (nobody sees it) or shown against every store (everybody sees it), and both are wrong."""
    rows = _rows("employees", "employee_id,home_store", org_id, blockers)
    return {_s(r.get("employee_id")): _s(r.get("home_store")).upper() for r in rows
            if _s(r.get("employee_id"))}


def _shifts_in_week(org_id, days, blockers):
    """Every store's shifts in this week, in ONE read. The per-store split and the SAME PEOPLE's
    shifts elsewhere (the double-booking check) both come out of this one list — a second read per
    store is how a board comes to disagree with itself."""
    try:
        rows = (sb().table("shifts").select(SHIFT_FIELDS).eq("org_id", org_id)
                .eq("is_deleted", False)
                .gte("shift_date", days[0].isoformat())
                .lte("shift_date", days[-1].isoformat())
                .limit(20000).execute().data) or []
    except Exception as e:
        blockers.append({"table": "shifts", "reason": type(e).__name__})
        return []
    return rows


def _time_off_in_week(org_id, days, blockers):
    """Requests that OVERLAP this week — a request starting before it and ending inside it is time
    off in this week, so the window is an overlap test and not an equality."""
    try:
        rows = (sb().table("time_off_requests")
                .select("employee_id,start_date,end_date,status,type").eq("org_id", org_id)
                .lte("start_date", days[-1].isoformat())
                .gte("end_date", days[0].isoformat())
                .limit(5000).execute().data) or []
    except Exception as e:
        blockers.append({"table": "time_off_requests", "reason": type(e).__name__})
        return []
    return rows


def _swaps_for_week(org_id, week_shift_ids, blockers):
    """Cover swaps are keyed on a SHIFT, not on a date (mig 003), so "this week's swaps" are the
    ones whose shift is in this week. Read once, matched here."""
    rows = _rows("shift_swap_requests", "id,shift_id,target_shift_id,status,requester_id,target_id",
                 org_id, blockers)
    out = []
    for r in rows:
        for key in ("shift_id", "target_shift_id"):
            sid = _s(r.get(key))
            if sid and sid in week_shift_ids:
                out.append(dict(r, store_code=week_shift_ids[sid]))
                break
    return out


def _overrides_for_week(org_id, week_start, blockers):
    rows = _rows("budget_override", "store_code,week_start,approved_hours,status", org_id, blockers)
    out = {}
    for r in rows:
        if _day(r.get("week_start")) != week_start:
            continue
        code = _s(r.get("store_code")).upper()
        # An APPROVED override wins over one still awaiting a decision for the same store-week.
        if code not in out or _s(r.get("status")).lower() == _flow.APPROVED:
            out[code] = r
    return out


def _sends_for_week(org_id, week_start, blockers):
    """Sends of the week's rota, by store. `notify.send_log` records one row per send with the
    FILTERS it was sent under (mig 010), and the schedule report's filters carry `week_start` and
    an optional `store_code` — a send with no store is the whole week, so it counts for every store.
    """
    rows = _rows("send_log", "report_key,channel,target,status,filters,created_at", org_id,
                 blockers, schema="notify", report_key=_flow.SCHEDULE_REPORT_KEY)
    by_store, all_stores = {}, []
    for r in rows:
        f = r.get("filters") if isinstance(r.get("filters"), dict) else {}
        if _day(f.get("week_start")) != week_start:
            continue
        code = _s(f.get("store_code")).upper()
        if code:
            by_store.setdefault(code, []).append(r)
        else:
            all_stores.append(r)
    return by_store, all_stores


def _assemble(org_id, week_start_in, *, include_inactive=False, store_code=None):
    blockers = []
    declared = _declarations(org_id, blockers)
    try:
        days = _flow.week_days(week_start_in or date.today(), declared)
    except ValueError as e:
        raise HTTPException(400, str(e))

    stores, hidden = _stores(org_id, blockers, include_inactive=include_inactive,
                             store_code=store_code)
    # The shifts and the people's whereabouts are read for the WHOLE org's week, because the
    # double-booking check asks where else the same person is — a per-store read cannot answer it.
    shifts = _shifts_in_week(org_id, days, blockers)
    time_off = _time_off_in_week(org_id, days, blockers)
    home = _home_stores(org_id, blockers)
    week_shift_ids = {}
    for s in shifts:
        if _s(s.get("id")):
            week_shift_ids[_s(s.get("id"))] = _s(s.get("store_code")).upper()
    swaps = _swaps_for_week(org_id, week_shift_ids, blockers)
    budgets = {_s(b.get("store_code")).upper(): b
               for b in _rows("hours_budget", "store_code,weekly_hours", org_id, blockers)}
    overrides = _overrides_for_week(org_id, days[0], blockers)
    sends_by_store, sends_all = _sends_for_week(org_id, days[0], blockers)

    return {"declared": declared, "days": days, "stores": stores, "inactive_hidden": hidden,
            "home_stores": home,
            "shifts": shifts, "time_off": time_off, "swaps": swaps, "budgets": budgets,
            "overrides": overrides, "sends_by_store": sends_by_store, "sends_all": sends_all,
            "blockers": blockers}


def _facts_for(store, a) -> dict:
    """One store-week's facts, cut out of the week that was read once."""
    code = _s(store.get("store_code"))
    key = code.upper()
    mine = [s for s in a["shifts"] if _s(s.get("store_code")).upper() == key]
    people = {_s(s.get("employee_id")) for s in mine if _s(s.get("employee_id"))}
    elsewhere = [s for s in a["shifts"]
                 if _s(s.get("store_code")).upper() != key
                 and _s(s.get("employee_id")) in people]
    # A request belongs to this store-week if the person is ON this week's rota here, or if this is
    # their home store — which is what answers for somebody not yet scheduled anywhere.
    home = a["home_stores"]
    off = [r for r in a["time_off"]
           if _s(r.get("employee_id")) in people
           or home.get(_s(r.get("employee_id"))) == key]
    return {"store_code": code, "week_start": a["days"][0],
            "budget": a["budgets"].get(key),
            "time_off": off,
            "swaps": [w for w in a["swaps"] if _s(w.get("store_code")) == key],
            "shifts": mine, "other_shifts": elsewhere,
            "override": a["overrides"].get(key),
            "sends": (a["sends_by_store"].get(key) or []) + a["sends_all"]}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE STANDARD FILTERS (report rule, 2026-10-30)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _apply_filters(stores, *, markets=None, stores_filter=None):
    def _set(v):
        return {x.strip().upper() for x in str(v or "").split(",") if x.strip()}

    want_stores, want_markets = _set(stores_filter), _set(markets)
    if want_stores:
        stores = [s for s in stores if _s(s.get("store_code")).upper() in want_stores]
    if want_markets:
        stores = [s for s in stores if _s(s.get("market")).upper() in want_markets]
    return stores


def _gate(stores, authorization, org_id, days):
    """Which of these stores reach this login at all, through the ONE home (§14x). The evidence scan
    is bounded to the week being read, so "which stores can I see" is "where I was that week"."""
    from app.modules.storeops.router import scope_keyset
    try:
        keyset = scope_keyset(authorization, org_id, since=days[0].isoformat(),
                              until=days[-1].isoformat())
    except Exception:
        # FAIL CLOSED: an unreadable gate shows no store's week, never every store's.
        return []
    if keyset is None:
        return stores
    return [s for s in stores if _s(s.get("store_code")).upper() in keyset]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE BOARD — one row per store-week, and which step each is stuck at
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/schedule-workflow")
def schedule_workflow_board(week_start: str = None, include_inactive: bool = False,
                            markets: str = None, stores: str = None, status: str = None,
                            authorization: str = Header(default=""), org_id: str = ORG_ID,
                            response: Response = None):
    """Every store's week at a glance, with where each one picks up.

    OPEN STORES ONLY BY DEFAULT and `include_inactive` is the one spelling (§CORE-45): a store
    switched off is out of every menu unless the box is ticked, and the count hidden is stated
    rather than left as a silently shorter list.
    """
    a = _assemble(org_id, week_start, include_inactive=include_inactive)
    days, declared = a["days"], a["declared"]
    visible = _apply_filters(a["stores"], markets=markets, stores_filter=stores)
    visible = _gate(visible, authorization, org_id, days)

    rows = []
    for store in visible:
        try:
            row = _flow.describe(declared, _facts_for(store, a))
        except ValueError as e:
            # The flow REFUSES rather than guessing. Report it against the store instead of
            # dropping it off the board.
            rows.append({"store_code": _s(store.get("store_code")),
                         "status": gw.NEEDS_INPUT, "label": gw.LABELS[gw.NEEDS_INPUT],
                         "resume": None, "resume_title": "", "blocking": (), "steps": [],
                         "refused": str(e)})
            continue
        row["address"] = _s(store.get("address"))
        row["market"] = _s(store.get("market"))
        row["is_active"] = store.get("is_active") is not False
        rows.append(row)

    if status:
        want = {x.strip() for x in str(status).split(",") if x.strip()}
        rows = [r for r in rows if r.get("status") in want]
    rows.sort(key=lambda r: (r.get("status") == gw.DONE, _s(r.get("store_code")).casefold()))
    described = [r for r in rows if r.get("steps")]

    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return {"ready": not a["blockers"],
            "week_start": days[0].isoformat(),
            "week_end": days[-1].isoformat(),
            "days": [{"date": d.isoformat(), "name": _flow.DAY_NAMES[d.weekday()]} for d in days],
            "declared": {"week_start_dow": declared["week_start_dow"],
                         "week_start_day": _flow.DAY_NAMES[declared["week_start_dow"]],
                         "week_start_declared": declared["week_start_declared"],
                         "open_dows": list(declared["open_dows"]),
                         # Said in the payload, so the screen never has to assume it.
                         "open_days_declared": bool(declared["open_dows"])},
            "steps": [{"key": s["key"], "title": s["title"], "why": s["why"],
                       "required": s["required"], "screen": s["screen"]}
                      for s in _flow.steps(declared)],
            "statuses": [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES],
            "summary": _flow.summarize(described),
            "rows": rows,
            "inactive_hidden": a["inactive_hidden"],
            "blockers": a["blockers"]}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ONE STORE-WEEK — the rail, and the door for each step
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/schedule-workflow/{store_code}")
def schedule_workflow_one(store_code: str, week_start: str = None,
                          authorization: str = Header(default=""), org_id: str = ORG_ID,
                          response: Response = None):
    """One store's five steps for one week, what each is waiting on, and where to go and do it.

    A store switched off is still readable — somebody arriving from a link must be told the store is
    closed, not shown a 404 that reads like "this store does not exist".
    """
    code = _s(store_code)
    a = _assemble(org_id, week_start, include_inactive=True, store_code=code)
    if not a["stores"]:
        raise HTTPException(404, "No store with that code in this organisation.")
    if not _gate(a["stores"], authorization, org_id, a["days"]):
        raise HTTPException(403, "You cannot read this store's schedule.")

    store = a["stores"][0]
    try:
        out = _flow.describe(a["declared"], _facts_for(store, a))
    except ValueError as e:
        raise HTTPException(409, str(e))
    out["address"] = _s(store.get("address"))
    out["market"] = _s(store.get("market"))
    out["is_active"] = store.get("is_active") is not False
    out["ready"] = not a["blockers"]
    out["blockers"] = a["blockers"]
    out["days"] = [{"date": d.isoformat(), "name": _flow.DAY_NAMES[d.weekday()]}
                   for d in a["days"]]
    out["declared"] = {"week_start_dow": a["declared"]["week_start_dow"],
                       "week_start_day": _flow.DAY_NAMES[a["declared"]["week_start_dow"]],
                       "open_days_declared": bool(a["declared"]["open_dows"])}
    out["statuses"] = [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES]
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return out
