"""MANAGEMENT WATCHDOG API — /api/v1/commcalc/watchdog/* (owner ask 2026-10-05).

Owner, verbatim: *"keep these reports in management dashboard under different reports so it is easy
for the management to review each area and take appropriate action, name it Management Watch dog"*.

WHAT THIS IS, AND WHAT IT IS NOT (the duplicate-check build gate, CLAUDE.md)
───────────────────────────────────────────────────────────────────────────
`/compliance` already exists and already answers "how many items are open in each QUEUE" across ten
different tables (`commcalc/compliance_summary.py`, owner directive 2026-09-03). A second dashboard
counting the same queues would be exactly the sibling derivation the index rules forbid.

So the Management Watchdog is a different cut of ONE table, not a second board over many:

    /compliance   one row per QUEUE — ten different tables, ten different owners. "What is open?"
    /watchdog     one page per AREA of `commcalc.flags` — one table, grouped by what a manager
                  reviews together. "Which part of the business do I go and act on?"

And the two are wired to the same registry rather than kept in step by hand: `compliance_summary`'s
commission-flags row and this board's area counts both resolve through
`commcalc/flag_registry.py`, so a new detector appears on both by registering once.

EVERY READ GOES THROUGH THE EXISTING ANSWER
───────────────────────────────────────────
This file **does not query `commcalc.flags`**. It calls `commcalc/router.get_flags`, which is the one
home for "which findings may this caller see" — the active-queue filter (a retired finding drops
out), the period-spelling variants, and the district-manager span filter (`scope_keyset` /
`in_keyset`, so a market manager sees their own stores and a store-scoped user sees theirs). A private
query here would be a second answer to "may this manager see this finding", which is a permission
defect waiting to happen, not a convenience.

The area, label, severity scale and grain all come from `flag_registry`. This file decides nothing.

Mounted by app/main.py. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §53.

RULE TWO: no carrier, tenant, store or product name appears in this file.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header, Query

from app.core.database import get_supabase
from app.core.run_secret import verify_notify_secret

from app.modules.commcalc import flag_registry as _reg
from app.modules.commcalc import void_watchdog as _void
from app.modules.commcalc import multi_handset_day as _handsets
from app.modules.commcalc import byod_upgrade_day as _byod_upg
from app.modules.commcalc import line_class as _lc
from app.modules.closing import cash_watchdog as _cash

router = APIRouter(prefix="/commcalc", tags=["Management Watchdog"])

ORG_ID = "00000000-0000-0000-0000-000000000001"

#: How far back the cash sweep looks on a tick. A drawer variance that nobody has ruled on is still
#: worth raising weeks later (the B-117 case was four days old and climbing when it was found), but an
#: unbounded scan would re-read years of attempts every hour. TENANT-TUNABLE via the rule row's
#: `lookback_days`, read below — the house default is deliberately generous.
LOOKBACK_DAYS_DEFAULT = 45


def _s(v) -> str:
    return str(v or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_now() -> str:
    """This month, in the '%B %Y' spelling every writer of `commcalc.flags` already uses — which is
    therefore the spelling `commcalc/router._pvariants` can expand. A canonical '202610' would be a
    third spelling in the same column that `_pvariants` cannot parse, so it would match nothing."""
    d = datetime.now(timezone.utc)
    return d.strftime("%B %Y")


def _month_year(period: str):
    """(month, year) from a '%B %Y' or 'YYYY-MM' period label, or (None, None) when neither.

    (None, None) is kept and written as NULL rather than guessed: `flag_persist` derives a finding's
    identity from these columns, so a guessed month would re-key every finding of that period.
    """
    q = _s(period)
    if len(q) >= 7 and q[:4].isdigit() and q[4] == "-" and q[5:7].isdigit():
        return int(q[5:7]), int(q[:4])
    parts = q.split()
    if len(parts) == 2 and parts[1].isdigit():
        names = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
        if parts[0].lower() in names:
            return names[parts[0].lower()], int(parts[1])
    return None, None


def _visible_flags(period: str, authorization: str, org_id: str):
    """The findings this caller may see for a period — THE existing answer, not a new one.

    Dereferences `commcalc/router.get_flags`: active queue only, every period spelling, and the
    district-manager span filter. Imported lazily because commcalc/router is a large module and this
    one is mounted beside it (the same lazy-import idiom the closing↔commcalc pair already uses to
    avoid an import cycle).
    """
    from app.modules.commcalc.router import get_flags as _get_flags
    return _get_flags(period, authorization=authorization, org_id=org_id) or []


# ── The board: one row per area. ─────────────────────────────────────────────────────────────────
@router.get("/watchdog/board")
# PLAIN `def`, not `async def`: `_visible_flags` does a BLOCKING supabase round trip. As `async def`
# it would run that on the event loop and stall every other in-flight request — verbatim the SEV-1 of
# 2026-07-30 that `get_flags` itself carries a comment about. Same shape here on purpose.
def watchdog_board(period: str = Query(default=""), authorization: str = Header(default=""),
                   org_id: str = ORG_ID):
    """One row per review area with its open findings counted, in board order.

    EVERY area is returned even at zero, so the board is a statement of what is watched rather than a
    list of today's bad news — a manager must be able to tell "nothing open in cash" from "cash is
    not watched". An `unassigned` row appears ONLY when a finding landed whose kind is not registered,
    and `unregistered_types` names those kinds, so the board is never read as complete when it is not.
    """
    p = _s(period) or _period_now()
    rows = _visible_flags(p, authorization, org_id)
    return {
        "period": p,
        "as_of": _now(),
        "total_open": len(rows),
        "areas": _reg.area_summary(rows),
        "unregistered_types": _reg.unregistered_types(rows),
    }


# ── One area's findings: the page a manager acts on. ─────────────────────────────────────────────
@router.get("/watchdog/area/{area}")
def watchdog_area(area: str, period: str = Query(default=""),
                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The findings for one review area, worst first.

    `grain` on each finding is the honest answer to "which transaction was this": a `rep_period`
    finding counted transactions it did not keep, so there is nothing to click through to, and the
    page says so rather than showing an empty drill-down.
    """
    a = _s(area)
    if a != _reg.UNASSIGNED and a not in _reg.AREA_KEYS:
        raise HTTPException(404, "unknown watchdog area")
    p = _s(period) or _period_now()
    rows = _visible_flags(p, authorization, org_id)
    mine = [r for r in rows if _reg.area_of(r.get("flag_type")) == a]
    mine.sort(key=lambda r: (_reg.sev_rank(r.get("severity")),
                             _s(r.get("flag_type")), _s(r.get("store_code")),
                             _s(r.get("transaction_date"))))
    out = []
    for r in mine:
        d = dict(r)
        d["type_label"] = _reg.label_of(r.get("flag_type"))
        d["grain"] = _reg.grain_of(r.get("flag_type"))
        d["severity"] = _reg.canon_sev(r.get("severity"))
        out.append(d)
    return {
        "area": a,
        "label": _reg.AREA_LABELS.get(a, _reg.UNASSIGNED_LABEL),
        "blurb": _reg.AREA_BLURBS.get(a, ""),
        "period": p, "as_of": _now(),
        "open_count": len(out),
        "types": list(_reg.types_in_area(a)),
        "findings": out,
    }


# ── Which sales table answers for a period: ASKED, never assumed. ───────────────────────────────
def _read_sales(client, org_id: str, period: str, cols: str):
    """(rows, table) — this org's sales lines for `period`, from whichever table holds them.

    `router._open_month_source` is the ONE home for the question (index §19.16): the OPEN month reads
    the daily feed, where the hourly Sales Transaction Details email lands, and a CLOSED month reads
    the authoritative month-end archive, each falling back to the other. Naming a table here instead
    would be a second opinion about which feed is live.

    THE DEFECT THIS CLOSES, in both callers at once. Both the void sweep and the void register used
    to name the month-end archive literally. On the month in progress — the only month anybody can
    still act on — that archive does not exist yet, so both read zero lines and reported no voids
    rather than no data. Every CLOSED month is byte-identical, because the one home returns that same
    archive first for them; the change is that the open month is looked at at all.
    """
    from app.modules.commcalc.router import _open_month_source, _pvariants

    def _q(tbl):
        return (client.schema("commcalc").table(tbl).select(cols)
                .eq("org_id", org_id).in_("period", _pvariants(period)).limit(100000)
                .execute().data) or []

    primary, other = _open_month_source(client, org_id, period)
    rows = _q(primary)
    return (rows, primary) if rows else (_q(other), other)


# ── The void register: the list that did not exist. ──────────────────────────────────────────────
@router.get("/watchdog/voids")
def watchdog_voids(period: str = Query(default=""), authorization: str = Header(default=""),
                   org_id: str = ORG_ID):
    """Every voided, returned and unattributed sales line for a period, with the rates.

    THIS IS THE THING THAT DID NOT EXIST. Before it, `is_voided` was dereferenced in about a dozen
    places and every one of them only EXCLUDED the line from pay, so a void left no trace but an
    absence. Nothing here changes what pays: the classification comes from
    `gp_report.countable_sale_skip_reason`, the pay path's own rule, read a second way.

    A share is `null`, never 0, where there is no denominator — a rep with no lines has no void rate,
    and 0% would read as clean.
    """
    p = _s(period) or _period_now()
    client = get_supabase()
    try:
        rows, _tbl = _read_sales(
            client, org_id, p,
            "store,salesperson,ext_price,trans_id,trans_date,product_desc,"
            "serial_1,mdn,tender_type,contract_type,voided,trans_type")
    except Exception as e:
        raise HTTPException(503, f"sales feed unavailable: {str(e)[:200]}")

    # The SAME district-manager span filter the findings use, so this register cannot show a
    # market manager a store their flag queue hides. `in_keyset` is keyed on the resolved store code
    # and the raw address, exactly as `get_flags` applies it.
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, None, r.get("store"))]

    s = _void.summarise(rows)
    s["period"] = p
    s["as_of"] = _now()
    # The feed genuinely carrying nothing for this period is NOT "no voids" — it is "not reported".
    s["has_feed"] = bool(rows)
    return s


# ── The drill-down: who sold it, for how much, and what was sold alongside. ─────────────────────
@router.get("/watchdog/multi-handset")
def watchdog_multi_handset(period: str = Query(default=""), authorization: str = Header(default=""),
                           org_id: str = ORG_ID):
    """Every flagged buyer-day with its LINES — the rep, the amount, the device, the receipt — plus
    the accessory dollars and fee lines that were on the same sale.

    OWNER ASK 2026-10-12, verbatim: *"for all these flagged activations with more than one phone sold
    we need to have the drilldown to check who made the sale for how much, if no accessories are sold
    with the sale and no activation fee it is definletly leaning towards fraud"*.

    WHY AN ENDPOINT AND NOT A COLUMN. `flag_persist._ROW_FIELDS` is the set of columns the findings
    table holds, and a per-line breakdown is not one of them — the evidence dict is dropped on write
    by design. So the board keeps the one-line finding a manager scans, and this answers "show me the
    lines" from the feed, live, at read time. It is the same shape as `/watchdog/voids` above and goes
    through the same `_read_sales` one home, so the drill-down and the finding cannot disagree about
    what the month contained.

    THE OWNER'S RULE, REPORTED AND NOT ENFORCED. `signal` names which of the two companion signals
    are absent. It is a filter, not a verdict: measured on live October 2026, a set-up or activation
    fee is present on 16 of 17 flagged cases (the house org bills one per handset), so the fee alone
    barely discriminates, while only 2 of 17 carry no accessory at all. The numbers are given; the
    call is the manager's.
    """
    p = _s(period) or _period_now()
    client = get_supabase()
    try:
        rows, tbl = _read_sales(
            client, org_id, p,
            "store,salesperson,ext_price,trans_id,trans_date,product_desc,serial_1,"
            "mdn,voided,trans_type,department,category,customer")
    except Exception as e:
        raise HTTPException(503, f"sales feed unavailable: {str(e)[:200]}")

    # The SAME district-manager span filter the findings use, applied BEFORE anything is counted, so
    # a market manager's drill-down can never show a store their flag queue hides.
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, None, r.get("store"))]

    from app.modules.commcalc.gp_report import is_voided
    from app.modules.commcalc.router import _accessory_config, _is_accessory, _is_setup_fee
    _acfg = _accessory_config(client, org_id)
    box = _acfg.get("box_departments") or set()
    live = [r for r in rows
            if not is_voided(r.get("voided"))
            and _s(r.get("trans_type")) != "Return"]
    # Same org-declared placeholder words as the sweep, through the same I/O home — otherwise the
    # drill-down and the finding would disagree about who counts as a person.
    try:
        from app.modules.pos.receipt_import import identity_config
        ccfg = identity_config(client, org_id)
    except Exception as e:
        print(f"WARN customer-identity config unavailable, house placeholders used: {e}")
        ccfg = None
    grouped = _handsets.group_by_buyer_day(live, box, ccfg)
    rules = _read_rules(client, org_id)
    thr = _reg.rule_params(_handsets.FLAG_TYPE, rules).get(_handsets.THRESHOLD_KEY)
    found = _handsets.cases(
        grouped, thr,
        is_accessory=(lambda r, _a=_acfg: _is_accessory(_s(r.get("department")), r.get("category"),
                                                        r.get("product_desc"), _a)),
        is_fee=(lambda r, _a=_acfg: _is_setup_fee(r.get("product_desc"), _a)))

    out = []
    for c in found:
        missing = []
        if c.get("accessory_dollars") == 0:
            missing.append("no accessories")
        if c.get("fee_lines") == 0:
            missing.append("no set-up or activation fee")
        out.append({**c,
                    "description": _handsets.describe(c),
                    "coaching_note": _handsets.coaching_note(c),
                    "signal": missing})
    return {"org_id": org_id, "period": p, "as_of": _now(),
            "threshold": thr, "enabled": _reg.is_enabled(_handsets.FLAG_TYPE, rules),
            "box_departments": sorted(box),
            "sales_table": tbl,
            # The feed genuinely carrying nothing for this period is NOT "no cases" — it is
            # "not reported", and a screen must be able to say which.
            "has_feed": bool(rows),
            "summary": _handsets.summary(grouped, found),
            "cases": out}


# ── A brought device and an upgrade on one number in one day (index §COMMCALC-43). ──────────────
@router.get("/watchdog/byod-upgrade")
def watchdog_byod_upgrade(period: str = Query(default=""), authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    """Every phone line that took BOTH a brought-device activation AND an upgrade at one store on one
    day, with the two receipts, the handsets on each side, the device, the dollars and the rep.

    OWNER ASK 2026-10-28, verbatim: *"Need to create a new flag report to check if a byod and an
    upgrade is done on the same day for the same phone number at the same store, add standard filters
    and send options"*.

    THE READS ARE ALL SOMEBODY ELSE'S, on purpose — this endpoint resolves nothing of its own:
      · which sales table answers for the period — `_read_sales` above, the ONE home (§19.16), so
        the month in progress is read from the daily feed and a closed month from the archive. The
        void sweep's 2026-10-12 defect was exactly a surface naming a table itself.
      · which store a manager may see — `storeops.scope_keyset` / `in_keyset`, the same span filter
        `get_flags` applies, run BEFORE anything is counted.
      · what one activation is, its phone line and its class — `line_class.activation_events`
        through the detector, with the org's OWN resolved rules (`router._line_rules_of`).
      · which line is a handset — the per-org `box_departments` set the box count uses.

    IT REPORTS AND DOES NOT CONCLUDE. A customer who activates a line on a phone they brought in and
    then buys a handset the same day is an ordinary sale and reads identically. `signal` names what
    was observed, never what it means.

    `numbered_share` IS THE HEADLINE WHEN IT IS LOW, and the screen must say so rather than show a
    clean zero: measured 2026-10-28, one live tenant's feed keys 93.9% of its activation events on a
    DEVICE because it carries no `mdn`, so nothing on those sales can be grouped to a phone line.
    """
    p = _s(period) or _period_now()
    client = get_supabase()
    try:
        rows, tbl = _read_sales(
            client, org_id, p,
            "store,salesperson,ext_price,trans_id,trans_date,product_desc,serial_1,"
            "mdn,voided,trans_type,department,category,contract_type")
    except Exception as e:
        raise HTTPException(503, f"sales feed unavailable: {str(e)[:200]}")

    # The SAME district-manager span filter the findings use, applied BEFORE anything is counted, so
    # a market manager's report can never show a store their flag queue hides.
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, None, r.get("store"))]

    from app.modules.commcalc.router import _accessory_config, _line_rules_of
    _acfg = _accessory_config(client, org_id)
    rules = _line_rules_of(_acfg) or _lc.HOUSE_RULES
    box = _acfg.get("box_departments") or set()
    # REFUSED, not defaulted: an org that has declared no box departments gets handset counts of
    # None ("not measured"), never a house-labelled guess that would disagree with its own box count.
    is_box = ((lambda r, _b=box: _s(r.get("department")) in {_s(d) for d in _b}) if box else None)

    rule_rows = _read_rules(client, org_id)
    grouped = _byod_upg.number_days(rows, rules)
    params = _reg.rule_params(_byod_upg.FLAG_TYPE, rule_rows)
    found = _byod_upg.cases(grouped, params, is_box=is_box)

    out = []
    for c in found:
        signal = []
        if c.get("first_handsets") == 0 and (c.get("second_handsets") or 0) > 0:
            signal.append("no handset on the first activation, one on the second")
        if c["all_zero_dollars"]:
            signal.append("every line at $0.00")
        if c["events"] > len(c.get("classes") or ()):
            signal.append("more than two activations on the number")
        out.append({**c,
                    "description": _byod_upg.describe(c),
                    "coaching_note": _byod_upg.coaching_note(c),
                    "signal": signal})
    return {"org_id": org_id, "period": p, "as_of": _now(),
            "enabled": _reg.is_enabled(_byod_upg.FLAG_TYPE, rule_rows),
            "classes": list(params.get(_byod_upg.CLASSES_KEY) or ()),
            "class_labels": [_lc.CLASS_LABELS.get(x, x)
                             for x in (params.get(_byod_upg.CLASSES_KEY) or ())],
            "box_departments": sorted(box),
            "sales_table": tbl,
            # The feed genuinely carrying nothing for this period is NOT "no cases" — it is
            # "not reported", and a screen must be able to say which.
            "has_feed": bool(rows),
            "summary": _byod_upg.summary(grouped, found, is_box=is_box),
            "cases": out}


# ── The config surface: thresholds are rows, never code (RULE TWO). ─────────────────────────────
@router.get("/watchdog/rules")
def watchdog_rules(org_id: str = ORG_ID):
    """Every watchdog type with the thresholds IN FORCE for this org — house default plus any row.

    `source` says which it is, so an operator can tell a deliberate setting from an inherited
    default. That distinction is the whole reason this endpoint exists: a threshold that silently
    came from code looks identical to one somebody chose.
    """
    rows = _read_rules(get_supabase(), org_id)
    by_type = {_s(r.get("flag_type")) for r in rows}
    out = []
    for t in sorted(_reg.DEFAULT_PARAMS):
        out.append({
            "flag_type": t,
            "label": _reg.label_of(t),
            "area": _reg.area_of(t),
            "severity": _reg.severity_for(t),
            "enabled": _reg.is_enabled(t, rows),
            "params": _reg.rule_params(t, rows),
            "defaults": _reg.default_params(t),
            "source": "org row" if t in by_type else "house default",
        })
    return {"org_id": org_id, "as_of": _now(), "rules": out}


def _read_rules(client, org_id):
    """This org's `commcalc.watchdog_rule` rows, or [] before migration 1056 has been applied.

    Degrades to the house defaults rather than erroring (contract §5): a watchdog must run on the
    day its code ships, not on the day somebody remembers to paste the migration.
    """
    try:
        return (client.schema("commcalc").table("watchdog_rule")
                .select("flag_type,enabled,params").eq("org_id", org_id).execute().data) or []
    except Exception as e:
        print(f"INFO watchdog_rule unavailable, using house defaults (run migration 1056?): {e}")
        return []


# ── The tick: one sweep, every watchdog, one cron line to paste. ────────────────────────────────
def _run_for_org(client, org_id: str, *, period: str = "", lookback_days: int = 0) -> dict:
    """Every detector for one org, written through `flag_persist` so a manager's ruling survives.

    Deliberately ONE function for both: a second `run-due` endpoint would mean a second cron line to
    register, and the 2026-10-04 notes already record alert sweeps sitting switched off because their
    `cron.schedule` line was never pasted. One line is one thing to forget.
    """
    from app.modules.commcalc import flag_persist
    from app.modules.commcalc import flag_store_resolver
    from app.modules.commcalc.router import _pvariants

    rules = _read_rules(client, org_id)
    p = _s(period) or _period_now()
    out = {"org_id": org_id, "period": p}

    # ── Cash. Reads the submit trail and the closings, hands them to the pure detector. ─────────
    try:
        days = int(lookback_days or 0) or LOOKBACK_DAYS_DEFAULT
        from datetime import date, timedelta
        since = (date.today() - timedelta(days=days)).isoformat()
        attempts = (client.schema("commcalc").table("closing_attempt")
                    .select("store_code,store_address,close_date,employee_name,attempt_no,"
                            "entered_cash,entered_credit,b2b_cash,b2b_credit,cash_dir,credit_dir,"
                            "blocked,accepted,auto_accepted,created_at")
                    .eq("org_id", org_id).gte("close_date", since).limit(50000).execute().data) or []
        closings = (client.schema("commcalc").table("daily_closing")
                    .select("store_code,store_address,close_date")
                    .eq("org_id", org_id).gte("close_date", since).limit(50000).execute().data) or []
        # The §13 store resolver, so the two tables join on the same store identity. Without it the
        # measured 2026-10-04 case recurs: seven banked days read as unfinished because the attempt
        # trail and the closing row spelled the same store differently.
        resolve = None
        try:
            from app.modules.account.coa import store_resolver
            resolve = store_resolver(client, org_id)
        except Exception as e:
            print(f"WARN cash watchdog store resolver unavailable, raw codes used: {e}")
        cash_flags = _cash.calc_cash_flags(attempts, closings, resolve=resolve, rules=rules)
        periods = sorted({f["period"] for f in cash_flags} | {p})
        pv = []
        for one in periods:
            pv.extend(_pvariants(one))
        out["cash"] = flag_persist.sync(
            client, org_id, cash_flags, periods=sorted(set(pv)), sources=[_cash.SOURCE],
            reason="the drawer variance was corrected or the day was ruled on")
        out["cash"].pop("run_id", None)
    except flag_persist.FlagPersistUnavailable as e:
        out["cash"] = {"skipped": "findings cannot be recorded yet",
                      "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["cash"] = {"error": str(e)[:300]}

    # ── Sales lines, read ONCE for every sales-reading detector in this sweep, through the one
    # home for which table answers (`_read_sales` above). The owner named the daily feed for the
    # multi-handset check on 2026-10-12 ("it should be daily sales feed"); dereferencing that home
    # is how this gets the feed for the month in progress without a second opinion about it.
    sales_rows, sales_table, sales_err = [], "", ""
    try:
        sales_rows, sales_table = _read_sales(
            client, org_id, p,
            # `category` is here for the accessory predicate, which reads department OR category:
            # a feed that spells accessories only in Category (measured live) is invisible without it.
            # `contract_type` is here for the activation CLASS predicate, and leaving it out is a
            # SILENT ZERO rather than an error: `line_class.HOUSE_FIELDS` is ("contract_type",), so
            # without the column every line classifies as None, the §COMMCALC-43 detector finds no
            # activation events at all, and the sweep reports a clean period it never looked at.
            # Locked by harness_byod_upgrade_wiring §B — every field any tenant's resolved rules can
            # read must be in this select.
            "store,salesperson,ext_price,trans_id,trans_date,product_desc,serial_1,"
            "mdn,voided,trans_type,department,category,customer,contract_type")
    except Exception as e:
        sales_err = str(e)[:300]

    # ── Voids. One period at a time; the sales feed is the biggest table in the schema. ─────────
    try:
        if sales_err:
            raise RuntimeError(sales_err)
        pm, py = _month_year(p)
        void_flags = _void.calc_void_flags(sales_rows, period=p, period_month=pm, period_year=py,
                                           rules=rules)
        out["voids"] = flag_persist.sync(
            client, org_id, void_flags, periods=_pvariants(p), sources=[_void.SOURCE],
            reason="the void or return rate is back inside the allowed share")
        out["voids"].pop("run_id", None)
        out["voids"]["sales_lines_read"] = len(sales_rows)
        out["voids"]["sales_table"] = sales_table
    except flag_persist.FlagPersistUnavailable as e:
        out["voids"] = {"skipped": "findings cannot be recorded yet",
                      "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["voids"] = {"error": str(e)[:300]}

    # ── Several handsets to one buyer in one day (index §COMMCALC-05). ──────────────────────────
    #
    # Two facts are INJECTED rather than decided here, because each already has a home:
    #   • the box-department set — `router._accessory_config(...)['box_departments']` (mig 218), the
    #     same membership test the box count uses, so "is this line a handset" has one answer. The
    #     detector refuses to default it; an org that has declared none gets `box_departments: 0` in
    #     the result and no finding, which is honest rather than a house-labelled guess.
    #   • voided and returned lines — `gp_report.is_voided`, the shared void-token set every display
    #     surface already excludes by. A voided line is not a sale and must not be flagged as one.
    #     `gp_report.countable_sale_skip_reason` is the fuller one home for "is this a countable
    #     sale" and is deliberately NOT used: its third rule drops a line with a blank or 'admin'
    #     rep, which is a PAY question. A handset nobody is credited for was still handed to a
    #     buyer, so that rule would hide exactly the case this detector exists to raise.
    try:
        if sales_err:
            raise RuntimeError(sales_err)
        from app.modules.commcalc.gp_report import is_voided
        from app.modules.commcalc.router import _accessory_config, _is_accessory, _is_setup_fee
        _acfg = _accessory_config(client, org_id)
        box = _acfg.get("box_departments") or set()
        # The owner's two companion signals (2026-10-12), each through its EXISTING owner-owned home
        # over the SAME config row `box` came from — so "handset", "accessory" and "fee" are three
        # readings of one declaration and cannot disagree about the same line.
        _acc = (lambda r, _a=_acfg: _is_accessory(str(r.get("department") or "").strip(),
                                                  r.get("category"), r.get("product_desc"), _a))
        _fee = (lambda r, _a=_acfg: _is_setup_fee(r.get("product_desc"), _a))
        # The org's declared placeholder bill-to words, through the EXISTING I/O home for them
        # (`receipt_import.identity_config` → `pos_settings` → `customer_identity.resolve_config`,
        # cached per client × org). A tenant whose POS types its own word into the customer field
        # declares it there and BOTH the customer master and this detector stop treating it as a
        # person — which is the point of there being one home. Best-effort: the house words on any
        # read failure, which is what every month before this already used.
        try:
            from app.modules.pos.receipt_import import identity_config
            _ccfg = identity_config(client, org_id)
        except Exception as e:
            print(f"WARN customer-identity config unavailable, house placeholders used: {e}")
            _ccfg = None

        live = [r for r in sales_rows
                if not is_voided(r.get("voided"))
                and str(r.get("trans_type") or "").strip() != "Return"]
        pm, py = _month_year(p)
        hs_flags, hs_summary = _handsets.findings(
            live, box, org_id=org_id, period=p, period_month=pm, period_year=py, rule_rows=rules,
            customer_config=_ccfg, is_accessory=_acc, is_fee=_fee)
        # The ONE home that turns a POS store spelling into a store_code a district manager's board
        # can filter on (mig 285). Visibility only — it writes no amount and no pay field.
        try:
            flag_store_resolver.stamp_flags(client, org_id, hs_flags)
        except Exception as e:
            print(f"WARN multi-handset store_code unresolved, address kept: {e}")
        out["multi_handset"] = flag_persist.sync(
            client, org_id, hs_flags, periods=_pvariants(p), sources=[_handsets.SOURCE],
            reason="the day no longer shows several handsets to that buyer")
        out["multi_handset"].pop("run_id", None)
        out["multi_handset"]["box_departments"] = len(box)
        out["multi_handset"]["summary"] = hs_summary
        out["multi_handset"]["sales_table"] = sales_table
    except flag_persist.FlagPersistUnavailable as e:
        out["multi_handset"] = {"skipped": "findings cannot be recorded yet",
                                "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["multi_handset"] = {"error": str(e)[:300]}

    # ── A brought device and an upgrade on one number in one day (index §COMMCALC-43). ──────────
    #
    # Rides the SAME `sales_rows` read above — one feed read serves every detector on this tick, so
    # adding a watchdog costs no extra pass over the biggest table in the schema. Three facts are
    # INJECTED rather than decided here, each already having a home:
    #   • the org's line_class rules — `router._line_rules_of(_accessory_config(...))`, so "which
    #     kind of activation is this" is answered by that company's own declared vocabulary and
    #     cannot disagree with its activation count or its per-activation payout.
    #   • the box-department set — the same membership test the box count uses. REFUSED, not
    #     defaulted: no declaration means handset counts of None ("not measured").
    #   • voided and returned lines — `byod_upgrade_day.line_skip`, the display skip rule, applied
    #     inside the detector. `countable_sale_skip_reason` is deliberately NOT used: its third rule
    #     drops a line with a blank or 'admin' rep, which is a PAY question, and an activation
    #     nobody is credited for still happened to a phone line.
    try:
        if sales_err:
            raise RuntimeError(sales_err)
        from app.modules.commcalc.router import _accessory_config, _line_rules_of
        _bcfg = _accessory_config(client, org_id)
        _brules = _line_rules_of(_bcfg) or _lc.HOUSE_RULES
        _bbox = _bcfg.get("box_departments") or set()
        _bis_box = ((lambda r, _b=_bbox: _s(r.get("department")) in {_s(d) for d in _b})
                    if _bbox else None)
        pm, py = _month_year(p)
        bu_flags, bu_summary = _byod_upg.findings(
            sales_rows, _brules, org_id=org_id, period=p, period_month=pm, period_year=py,
            rule_rows=rules, is_box=_bis_box)
        # The ONE home that turns a POS store spelling into a store_code a district manager's board
        # can filter on (mig 285). Visibility only — it writes no amount and no pay field.
        try:
            flag_store_resolver.stamp_flags(client, org_id, bu_flags)
        except Exception as e:
            print(f"WARN byod-upgrade store_code unresolved, address kept: {e}")
        out["byod_upgrade"] = flag_persist.sync(
            client, org_id, bu_flags, periods=_pvariants(p), sources=[_byod_upg.SOURCE],
            reason="the day no longer shows both kinds of activation on that number")
        out["byod_upgrade"].pop("run_id", None)
        out["byod_upgrade"]["summary"] = bu_summary
        out["byod_upgrade"]["sales_table"] = sales_table
    except flag_persist.FlagPersistUnavailable as e:
        out["byod_upgrade"] = {"skipped": "findings cannot be recorded yet",
                               "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["byod_upgrade"] = {"error": str(e)[:300]}

    return out


@router.post("/watchdog/run-now")
def watchdog_run_now(period: str = Query(default=""), lookback_days: int = Query(default=0),
                     org_id: str = ORG_ID):
    """Run every watchdog for ONE org, now. The operator's button and the test entrypoint."""
    return _run_for_org(get_supabase(), org_id, period=period, lookback_days=lookback_days)


@router.post("/watchdog/run-due")
def watchdog_run_due(x_notify_secret: str = Header(default=""), period: str = Query(default="")):
    """pg_cron entrypoint, guarded by NOTIFY_RUN_SECRET — every tenant on one tick (the mig-433
    convention). One org failing is reported and never stops the others.

    NOTHING RUNS UNTIL ONE `cron.schedule` LINE IS PASTED. That is stated here because the 2026-10-04
    notes record two alert sweeps shipped, correct and silent for exactly this reason. The line is in
    migration 1056's comment header.
    """
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    client = get_supabase()
    try:
        tenants = (client.schema("storeops").table("tenants").select("org_id")
                   .execute().data) or []
    except Exception as e:
        raise HTTPException(503, f"tenant list unavailable: {str(e)[:200]}")
    results = []
    for t in tenants:
        oid = _s(t.get("org_id"))
        if not oid:
            continue
        try:
            results.append(_run_for_org(client, oid, period=period))
        except Exception as e:
            results.append({"org_id": oid, "error": str(e)[:300]})
    return {"tenants": len(results), "as_of": _now(), "results": results}
