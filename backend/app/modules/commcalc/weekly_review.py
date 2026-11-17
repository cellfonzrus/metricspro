"""WEEKLY PERFORMANCE REVIEW — the Monday review pack, folded once, for every market and for the
whole company. PURE: no DB, no FastAPI, no clock, stdlib only.

OWNER ASK (2026-11-16, verbatim): *"Add the performance deck to the management tile but allow access
only for the admins , make this report every week at the end of the week so the same can be reviewed
every monday with the dm, it should have invenotry aging for rteh repsective carrier also as one of
the items to review., make a separate report for each market and one consolidated for the entire
company , is there any way we can make this on demand"* — and, the same evening, *"let us make it on
demand"*. So ON DEMAND is the main way to produce it and the weekly run is the standing one.

It is the in-app form of the LuxeLink performance deck built for the 17 Nov 2026 Zoom
(`/mnt/project-files/luxelink-zoom-2026-11-17/METHOD_AND_SOURCES.md`): profit by store, commission by
rep, the accessory gap against each store's own target, sales and lines growth, the reps who are
behind, payroll — plus the item the owner added, inventory aging against EACH CARRIER'S OWN declared
selling window.

THIS MODULE DERIVES NOTHING THAT A ONE HOME ALREADY ANSWERS, and it must never start.
Every figure arrives as a FACT handed in by the router, read from the surface that already owns it:

  · profit by store      ← `GET /commcalc/gp/{period}` store rows (`gp_report`, store identity §64)
  · commission by rep    ← `GET /commcalc/commissions/{period}` (the pay engine's own rows)
  · accessory gap        ← `GET /commcalc/accessory-target-plan/{period}` (the saved targets)
  · sales / lines growth ← `GET /commcalc/sales-comparison` cells (`_sales_cell_agg` → `line_class`)
  · reps behind          ← `GET /commcalc/productivity/rankings/{period}` — already judged there
  · inventory aging      ← `GET /asset/aging` rows + `aging_policy.by_carrier` (§COMMCALC-26)
  · payroll              ← `GET /storeops/payroll-by-store` — the ONE labour authority (§ACCOUNT-03)

A second derivation of any of those is the duplicate defect CLAUDE.md forbids: this report and the
report it quotes would drift within a month and the Monday meeting would spend itself arguing about
which screen is right.

THREE THINGS THIS FOLD REFUSES TO DO
────────────────────────────────────
1. **A fact nobody measured is never a zero.** Every item is MEASURED or NOT_MEASURED, carries its
   reason in words, and a not-measured item's `figure` is None — never 0, which on a review pack
   reads as "we checked and there is nothing wrong". Same doctrine as Zero Sales (§CORE-27) and
   `exec_metric_defs.bucket_coverage`.
2. **A store is never dropped.** A store no market claims gets a scope of its own, named, so the
   market packs and the company pack can never disagree about the estate.
3. **The tenant's own words are a required argument.** `scopes()` takes the markets this tenant
   declared; there is no house market vocabulary behind it, because a default that is really one
   tenant's vocabulary is the per-tenant code RULE TWO forbids (CLAUDE.md, 2026-10-30).
"""

MEASURED = "measured"
NOT_MEASURED = "not_measured"

MONDAY, SATURDAY, SUNDAY = 0, 5, 6


class ConfigRefused(ValueError):
    """A declared value this report will not act on — raised at resolve time, never stored."""


class Unreadable:
    """A fact the router COULD NOT READ, handed in as itself.

    A read that failed and a read that returned nothing are different findings, and only one of them
    is the owner's to fix. Passing `Unreadable("...")` keeps the reason on the item instead of
    letting a failed query render as an empty, healthy-looking section.
    """

    def __init__(self, reason):
        self.reason = str(reason or "the read failed")

    def __repr__(self):                                        # pragma: no cover - debugging aid
        return f"Unreadable({self.reason!r})"


#: THE REVIEW ITEMS, declared once. An eighth item is added HERE and every market pack, the company
#: pack, the export and the weekly email pick it up; there is no second list anywhere.
#:
#:   key     stable API key (never shown to a person)
#:   label   the words a manager reads
#:   unit    'money' | 'count' | 'pct' — how a surface formats the headline figure
#:   window  'week' | 'period' | 'today' — WHAT THE FIGURE COVERS, printed beside it. A monthly
#:           figure on a weekly pack is honest only when the pack says it is monthly.
#:   source  which already-owned read answers it (for the reader of this file, and printed in the
#:           payload so a pack states its own provenance)
ITEMS = [
    {"key": "profit", "window": "period", "label": "Profit by store", "unit": "money",
     "source": "GET /commcalc/gp/{period} store rows (gp_report)"},
    {"key": "growth", "window": "week", "label": "Sales and lines growth", "unit": "pct",
     "source": "GET /commcalc/sales-comparison cells, this week against the week before"},
    {"key": "accessory", "window": "period", "label": "Accessory gap against target", "unit": "money",
     "source": "GET /commcalc/accessory-target-plan/{period} (the targets the tenant saved)"},
    {"key": "commission", "window": "period", "label": "Commission by rep", "unit": "money",
     "source": "GET /commcalc/commissions/{period} (the pay engine's own rows)"},
    {"key": "rep_watch", "window": "period", "label": "Reps behind their peers", "unit": "count",
     "source": "GET /commcalc/productivity/rankings/{period} — judged there, not here"},
    {"key": "aging", "window": "today", "label": "Inventory aging by carrier", "unit": "count",
     "source": "GET /asset/aging rows against each carrier's own declared window"},
    {"key": "payroll", "window": "week", "label": "Payroll", "unit": "money",
     "source": "GET /storeops/payroll-by-store — the one labour authority"},
]
ITEM_KEYS = tuple(i["key"] for i in ITEMS)
ITEM_BY_KEY = {i["key"]: dict(i) for i in ITEMS}

#: HOUSE DEFAULTS — policy, not vocabulary (RULE TWO). A tenant may move the week's closing day and
#: switch the standing run off; nothing here names a carrier, a market or a store.
HOUSE_CONFIG = {
    "week_ends_on": SUNDAY,        # 0=Monday … 6=Sunday
    "weekly_run_enabled": False,   # the standing Saturday/Sunday run; on demand always works
    "recipients": [],              # extra addresses for the standing run (managers resolve anyway)
}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE WEEK
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def _as_date(value):
    from datetime import date
    if isinstance(value, date):
        return value
    try:
        y, m, d = [int(x) for x in str(value)[:10].split("-")]
        return date(y, m, d)
    except Exception:
        raise ConfigRefused(f"{value!r} is not a date this report can read (expected YYYY-MM-DD)")


def week_window(as_of, week_ends_on=None):
    """The LAST COMPLETED week as of `as_of`, on the tenant's declared closing weekday.

    The meeting is on Monday and the week under review is the one that has just closed — never the
    part-week the meeting itself sits in, which would make every Monday's pack a different length.
    Run ON the closing day (the standing Saturday/Sunday run) and that week is complete, so it is the
    one reviewed.
    """
    from datetime import timedelta
    ends = HOUSE_CONFIG["week_ends_on"] if week_ends_on is None else week_ends_on
    try:
        ends = int(ends)
    except (TypeError, ValueError):
        raise ConfigRefused(f"the week's closing day {week_ends_on!r} is not a weekday")
    if ends not in range(7):
        raise ConfigRefused(f"the week's closing day must be 0 (Monday) to 6 (Sunday), not {ends}")
    day = _as_date(as_of)
    back = (day.weekday() - ends) % 7
    end = day - timedelta(days=back)
    start = end - timedelta(days=6)
    return {"date_from": start.isoformat(), "date_to": end.isoformat(),
            "week_ending": end.isoformat(), "label": _week_label(start, end)}


def _week_label(start, end):
    if start.month == end.month:
        return f"{start.day}–{end.day} {_MONTHS[end.month - 1]} {end.year}"
    return (f"{start.day} {_MONTHS[start.month - 1]} – "
            f"{end.day} {_MONTHS[end.month - 1]} {end.year}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. THE SCOPES — one pack per market, one for the company, and nobody dropped
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _code(v):
    return str(v or "").strip().upper()


def scopes(*, markets, store_codes):
    """The packs to produce for ONE tenant: the company, then each market it DECLARED, then the
    stores no market claims.

    `markets` is {market label -> [store codes]} as the tenant's own market index answers it
    (`app.core.scope.market_index` — the one home that unions both market vocabularies). It is a
    REQUIRED argument with no default: a report that could answer without asking the org which
    markets exist would be answering with somebody else's words.
    """
    present = [c for c in dict.fromkeys(_code(c) for c in (store_codes or [])) if c]
    out = [{"kind": "company", "key": "company", "label": "Company (consolidated)",
            "store_codes": list(present), "note": ""}]
    claimed = set()
    for label in sorted((markets or {}).keys(), key=lambda s: str(s).lower()):
        codes = [c for c in dict.fromkeys(_code(c) for c in (markets[label] or [])) if c in present]
        claimed.update(codes)
        out.append({"kind": "market", "key": f"market:{label}", "label": str(label),
                    "store_codes": codes, "note": ""})
    unplaced = [c for c in present if c not in claimed]
    if unplaced or not markets:
        note = ("no market is declared for this tenant, so every store is reviewed here"
                if not markets else
                "these stores are in no declared market, so no market pack can carry them")
        out.append({"kind": "unplaced", "key": "unplaced", "label": "Stores with no market",
                    "store_codes": unplaced, "note": note})
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. THE ITEMS — each one a fold over a fact that was handed in
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _money(v):
    return round(v, 2) if v is not None else None


def _item(key, *, state, figure=None, reason="", rows=None, detail=None):
    spec = ITEM_BY_KEY[key]
    return {"key": key, "label": spec["label"], "unit": spec["unit"], "source": spec["source"],
            "window": spec["window"],
            "state": state, "figure": figure, "reason": reason,
            "rows": list(rows or []), "detail": dict(detail or {})}


def _nothing(key, reason):
    return _item(key, state=NOT_MEASURED, figure=None, reason=reason)


#: The aging item's own reach, said in words once. Every pack carries the company's shelf, because
#: a device is held against a carrier's window and a market does not have one.
_AGING_COVERS = "the whole company — inventory ages against a carrier's window, not a market's"


#: THE FIELD THE ROUTER STAMPS A ROW'S RESOLVED STORE KEY INTO, named once so neither side carries
#: a second copy of it. Every fact row and every scope's store list are put through the SAME
#: resolver before they reach this file (`commcalc.router._canonical_store_key_fn`, the one home for
#: "which store is this string"), because matching a row to a scope in two different key spaces is
#: how a row disappears.
STORE_KEY_FIELD = "_store_key"


def row_store_key(row):
    """The key a row is matched on: the stamped resolved key, or the row's own spelling folded.

    Falling back to the raw spelling is deliberate — a caller that forgot to stamp gets a weaker
    match, never a silently empty pack.
    """
    r = row or {}
    return _code(r.get(STORE_KEY_FIELD) or r.get("store_code") or r.get("store")
                 or r.get("address"))


def fact_rows(fact):
    """The ROWS inside a fact, whichever shape it arrives in.

    Most facts are a plain list of rows. One — profit — also carries the statement's own
    consolidated figure and its coverage beside them, because the company's net income is the P&L's
    answer and not the sum of the store rows (the books hold lines that sit at no store). A caller
    that only wants the rows asks here rather than knowing which is which.
    """
    if isinstance(fact, dict) and isinstance(fact.get("rows"), list):
        return fact["rows"]
    return fact if isinstance(fact, list) else []


def _in_scope(rows, scope):
    """Split the handed-in rows into (this scope's rows, the rows that name no store).

    NOTHING IS DROPPED HERE, and that is the whole point (owner report 2026-11-17: *"it shows no
    commission paid for the month of October"*). The rows existed — 45 reps and $9,829.72 on the
    house org alone — but the pay engine keys a rep by their store's ADDRESS while the scopes were
    built from declared store CODES, so every row failed the match and was thrown away without a
    word. A review item that prints nothing because a join missed is worse than one that prints the
    wrong number, because nothing looks like good news.

    So: the CONSOLIDATED pack holds every row that names a store at all — it is the whole company,
    and a store the market roster has not heard of is still the company's. A market pack holds the
    stores that market declared. The company pack additionally reports, in its own detail, both the
    rows that name no store and the rows whose store no market claims, so a dollar that lands on no
    market pack is still visible somewhere.
    """
    codes = set(scope.get("store_codes") or ())
    whole_company = str(scope.get("kind") or "") == "company"
    kept, unattached, outside = [], [], 0
    for r in rows or []:
        c = row_store_key(r)
        if not c:
            unattached.append(r)
            continue
        if c not in codes:
            outside += 1
            if not whole_company:
                continue
        kept.append(r)
    return kept, unattached, outside


def _profit(fact, scope):
    """PROFIT AS THE BOOKS ANSWER IT — net income, quoted from the P&L.

    Owner, 2026-11-17: *"The profit is not correct it should be same as in the finance dashboard"*.
    It is the finance module's `statement_engine.profit_by_store` that is quoted here, one row per
    store scope plus the consolidated figure, so this report and the finance dashboard cannot
    disagree: every row IS that scope's statement.

    THE COMPANY'S FIGURE IS THE CONSOLIDATED STATEMENT, NOT THE SUM OF THE STORES. The books place
    some lines at no store at all, so the sum of store rows is a near miss and a review that
    headlines a near miss invites the meeting to argue with the P&L. A market's figure IS the sum of
    its stores, which is all a market can be, and the item says so.
    """
    meta = fact.get("meta") or {} if isinstance(fact, dict) else {}
    rows, unattached, outside = _in_scope(fact_rows(fact), scope)
    if not rows:
        return _nothing("profit", "the statement placed no money at a store in this scope")
    out = [{"store_code": _code(r.get("store_code")) or row_store_key(r),
            "address": r.get("address") or r.get("store") or "",
            "revenue": _money(_f(r.get("total_rev"))),
            "net_profit": _money(_f(r.get("net_profit"))),
            "identity_ambiguous": bool(r.get("identity_ambiguous"))} for r in rows]
    store_total = sum(r["net_profit"] or 0.0 for r in out)
    revenue = sum(r["revenue"] or 0.0 for r in out)
    whole_company = str(scope.get("kind") or "") == "company"
    consolidated = (meta.get("consolidated") or {}) if whole_company else {}
    figure = _f(consolidated.get("net_income"))
    if figure is None:
        figure = store_total
    losing = [r for r in out if (r["net_profit"] or 0) < 0]
    recon = meta.get("reconciliation") or {}
    coverage = meta.get("coverage") or {}
    detail = {"revenue": _money(_f(consolidated.get("revenue")) if whole_company and
                                consolidated.get("revenue") is not None else revenue),
              "stores": len(out), "losing": losing, "stores_losing_money": len(losing),
              "unattached_rows": len(unattached), "stores_outside_the_roster": outside,
              "stores_net_income_total": _money(store_total),
              "basis": meta.get("basis") if whole_company else
                       "the sum of this market's stores on the same statement",
              "stores_with_ambiguous_identity": coverage.get("stores_with_ambiguous_identity")
                                                if whole_company else None}
    if whole_company and recon:
        # WHY THE STORES DO NOT ADD UP, in the statement's own words — never absorbed into a store.
        detail["money_at_no_store"] = _money(_f(recon.get("explained")))
        detail["unexplained"] = _money(_f(recon.get("unexplained")))
        detail["reconciles"] = recon.get("reconciles")
    return _item("profit", state=MEASURED, figure=_money(figure), rows=out, detail=detail)


def _growth(fact, scope):
    cur = (fact or {}).get("current") if isinstance(fact, dict) else None
    prior = (fact or {}).get("prior") if isinstance(fact, dict) else None
    if not cur:
        return _nothing("growth", "this week's sales were not measured")
    if not prior:
        return _nothing("growth", "the prior week carried no measured sales, so no growth can be "
                                  "stated — a first measured week is not growth of 100%")
    def pct(now, before):
        now, before = _f(now), _f(before)
        if now is None or before in (None, 0):
            return None
        return round((now - before) / before * 100.0, 1)
    boxes = pct(cur.get("boxes"), prior.get("boxes"))
    rev = pct(cur.get("revenue"), prior.get("revenue"))
    if boxes is None and rev is None:
        return _nothing("growth", "neither week carried a figure both weeks could be compared on")
    return _item("growth", state=MEASURED, figure=boxes if boxes is not None else rev,
                 detail={"boxes": cur.get("boxes"), "boxes_prior": prior.get("boxes"),
                         "boxes_change_pct": boxes,
                         "revenue": _money(_f(cur.get("revenue"))),
                         "revenue_prior": _money(_f(prior.get("revenue"))),
                         "revenue_change_pct": rev})


def _accessory(fact, scope):
    rows, _un, outside = _in_scope(fact, scope)
    if not rows:
        return _nothing("accessory", "no store carried an accessory target plan row this week")
    out, gap_total, no_target = [], 0.0, 0
    for r in rows:
        target, achieved = _f(r.get("target")), _f(r.get("achieved"))
        if target is None:
            no_target += 1
            out.append({"store_code": _code(r.get("store_code") or r.get("store")),
                        "target": None, "achieved": _money(achieved), "gap": None,
                        "state": NOT_MEASURED,
                        "reason": "no accessory target is saved for this store"})
            continue
        gap = (achieved or 0.0) - target
        gap_total += gap
        out.append({"store_code": _code(r.get("store_code") or r.get("store")),
                    "target": _money(target), "achieved": _money(achieved or 0.0),
                    "gap": _money(gap), "state": MEASURED, "reason": ""})
    scored = len(out) - no_target
    if not scored:
        return _item("accessory", state=NOT_MEASURED, figure=None, rows=out,
                     reason="no store in this scope has an accessory target saved",
                     detail={"stores_without_a_target": no_target, "stores_scored": 0})
    return _item("accessory", state=MEASURED, figure=_money(gap_total), rows=out,
                 detail={"stores_without_a_target": no_target, "stores_scored": scored,
                         "stores_outside_the_roster": outside,
                         "target_total": _money(sum(r["target"] or 0.0 for r in out)),
                         "achieved_total": _money(sum(r["achieved"] or 0.0 for r in out)),
                         "stores_behind": sum(1 for r in out if (r["gap"] or 0) < 0)})


def _commission(fact, scope):
    rows, unattached, outside = _in_scope(fact, scope)
    if not rows:
        return _nothing("commission", "no rep carried a commission row for this period")
    out = []
    for r in rows:
        out.append({"rep": r.get("rep") or r.get("storeops_name") or r.get("epay_salesperson") or "",
                    # THE RESOLVED store, not the row's own spelling: the pay engine names a rep's
                    # store by address, and a review that prints that address beside a GP row's
                    # declared code shows one store as two.
                    "store_code": row_store_key(r),
                    "payout": _money(_f(r.get("total_payout")) or 0.0),
                    "units": _f(r.get("units")) if r.get("units") is not None else None})
    out.sort(key=lambda r: r["payout"] or 0.0, reverse=True)
    total = sum(r["payout"] or 0.0 for r in out)
    return _item("commission", state=MEASURED, figure=_money(total), rows=out,
                 detail={"reps": len(out), "top": out[:5], "bottom": out[-5:],
                         "unattached_rows": len(unattached),
                         "stores_outside_the_roster": outside})


def _rep_watch(fact, scope):
    rows, _un, outside = _in_scope(fact, scope)
    if rows is None or (not rows and not isinstance(fact, list)):
        return _nothing("rep_watch", "the rep rankings were not measured for this period")
    if not rows:
        return _nothing("rep_watch", "no rep in this scope was ranked this period")
    behind = [{"rep": r.get("rep") or r.get("name") or "",
               "store_code": _code(r.get("store_code") or r.get("store")),
               "verdict": r.get("verdict") or r.get("reason") or "",
               "rank": r.get("rank")} for r in rows if r.get("behind")]
    return _item("rep_watch", state=MEASURED, figure=len(behind), rows=behind,
                 detail={"reps_ranked": len(rows), "reps_behind": len(behind),
                         "stores_outside_the_roster": outside})


def _aging(fact, scope):
    """Inventory aging, QUOTED from the aging report's own per-carrier fold.

    The cells arrive already folded by `payables/unsold_inventory.aging_by_carrier` through
    `/asset/aging` — every unsold device, each held to ITS OWN carrier's declared window. This file
    counts nothing and buckets nothing, because the window's edges are the carrier's contract and
    that comparison has one home.

    WHAT THIS ITEM IS NOT (owner report 2026-11-17: *"the inventory aging is not correct it shows
    all devices out of the [carrier]'s day window"*). It used to compose the aging report's
    `buckets` — the owed > 0 BILLING-exposure subset — which is almost entirely old stock, so every
    device read as past its window. The shelf and the unpaid-for part of the shelf are two
    questions; this item asks the first.

    It covers the WHOLE COMPANY on every pack, and says so: a device is held by a carrier, not by a
    market, and `/asset/aging` is the surface that narrows by market.
    """
    if not isinstance(fact, dict):
        return _nothing("aging", "the inventory aging report was not read")
    cells = fact.get("by_carrier")
    if cells is None:
        return _nothing("aging", "the inventory aging report answered without its per-carrier fold")
    if not cells:
        return _nothing("aging", "no carrier declared an inventory source, so nothing could be aged")
    if not fact.get("devices"):
        return _item("aging", state=NOT_MEASURED, figure=None, rows=list(cells),
                     reason="no unsold device is on inventory for this tenant",
                     detail={"devices": 0, "covers": _AGING_COVERS})
    return _item("aging", state=MEASURED, figure=fact.get("missed"), rows=list(cells),
                 detail={"devices": fact.get("devices"), "owed": fact.get("owed"),
                         "carriers": fact.get("carriers"),
                         "carriers_with_no_declared_window":
                             fact.get("carriers_with_no_declared_window"),
                         "devices_with_no_carrier": fact.get("devices_with_no_carrier"),
                         "within_window": sum((c.get("under45") or 0) + (c.get("warn") or 0)
                                              for c in cells),
                         "basis": fact.get("basis"), "covers": _AGING_COVERS})


def _payroll(fact, scope):
    rows, unattached, outside = _in_scope(fact, scope)
    if not rows and not unattached:
        return _nothing("payroll", "payroll was not measured for this period")
    out = [{"store_code": _code(r.get("store_code") or r.get("store")),
            "amount": _money(_f(r.get("amount") if r.get("amount") is not None else r.get("total"))
                             or 0.0)} for r in rows]
    total = sum(r["amount"] or 0.0 for r in out)
    unattached_total = sum(_f(r.get("amount") if r.get("amount") is not None else r.get("total"))
                           or 0.0 for r in unattached)
    if not out:
        return _item("payroll", state=NOT_MEASURED, figure=None,
                     reason="payroll was measured but none of it attaches to a store in this scope",
                     detail={"unattached": _money(unattached_total)})
    return _item("payroll", state=MEASURED, figure=_money(total), rows=out,
                 detail={"stores": len(out), "unattached": _money(unattached_total),
                         "stores_outside_the_roster": outside})


#: key -> the fold. ONE dispatch table, so an item cannot exist in `ITEMS` with no fold behind it
#: (and `harness_weekly_review.py` fails the build if the two ever disagree).
FOLDS = {"profit": _profit, "growth": _growth, "accessory": _accessory, "commission": _commission,
         "rep_watch": _rep_watch, "aging": _aging, "payroll": _payroll}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. ONE REVIEW, AND THE PACK
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def review(scope, *, facts, week, period=""):
    """One review pack for ONE scope. Every item in `ITEMS`, in order, always — a missing fact is an
    item that SAYS it was not measured, never an item that is absent from the page."""
    items = []
    for key in ITEM_KEYS:
        fact = (facts or {}).get(key)
        if isinstance(fact, Unreadable):
            items.append(_nothing(key, fact.reason))
            continue
        if fact is None:
            items.append(_nothing(key, "this read was not available for the review"))
            continue
        try:
            items.append(FOLDS[key](fact, scope))
        except Exception as e:                                 # a bad row cannot blank the pack
            items.append(_nothing(key, f"this item could not be folded ({e})"))
    measured = [i for i in items if i["state"] == MEASURED]
    # WHAT EACH NUMBER SAYS, attached to the item itself, so the page, the exported file and the
    # generated deck read ONE set of sentences (§6 below).
    for i in items:
        i["narrative"] = narrative(i, scope)
    out = {"scope": dict(scope), "week": dict(week), "items": items,
           "coverage": {"items": len(items), "measured": len(measured),
                        "not_measured": len(items) - len(measured)}}
    out["headline"] = headline(out, period)
    return out


def build(scope_list, *, facts, week, as_of, facts_by_scope=None, period=""):
    """The whole pack: the company review, then one per market, then the unplaced stores.

    `facts` are the tenant-wide facts (every row, unscoped) and each scope filters them by its own
    stores, so one read serves every pack and a market total can never disagree with the company
    total it is part of. `facts_by_scope` overrides per scope where a fact genuinely cannot be
    filtered by store (a growth cell folded server-side, for instance).
    """
    company, markets, unplaced = None, [], None
    for scope in scope_list or []:
        f = dict(facts or {})
        f.update(((facts_by_scope or {}).get(scope.get("key")) or {}))
        r = review(scope, facts=f, week=week, period=period)
        if scope.get("kind") == "company":
            company = r
        elif scope.get("kind") == "unplaced":
            unplaced = r
        else:
            markets.append(r)
    return {"as_of": as_of, "week": dict(week), "company": company, "markets": markets,
            "unplaced": unplaced, "items": [dict(i) for i in ITEMS]}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. THE GATE — administrators only (owner 2026-11-16: "allow access only for the admins")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def review_allowed(caller):
    """May this caller open the weekly review? ADMINISTRATORS ONLY, enforced on the server.

    An administrator is a login holding the `admin` module on its role, or a platform super-admin.
    A company-wide REPORTING scope ('all') is deliberately NOT enough: a regional leader who may see
    every store's numbers is not thereby an administrator, and this pack carries payroll, rep pay and
    store profitability side by side. Degrades CLOSED — an unresolvable caller is refused.

    Frontend mirror: `canOpenWeeklyReview` in `frontend/src/lib/rbac.ts`.
    """
    try:
        if not caller:
            return False
        if caller.get("super_admin") or caller.get("__super_admin"):
            return True
        perms = caller.get("perms") if isinstance(caller.get("perms"), dict) else caller
        mods = (perms or {}).get("modules") or []
        if isinstance(mods, dict):
            mods = [k for k, v in mods.items() if v]
        return any(str(m).strip().lower() == "admin" for m in mods)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. WHAT EACH NUMBER SAYS — the words, folded once (owner 2026-11-17)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#
# OWNER ASK, verbatim: *"The descriptive ppt is missing like one made before"*. The deck built for the
# 17 Nov Zoom explained every figure in a sentence; the in-app report showed the figure alone. A
# number on its own is not a review — the Monday meeting reads the sentence and the table is there
# for whoever wants to check it.
#
# ONE HOME FOR THE WORDS, and it is here, in the fold, because three surfaces say them: the page, the
# exported file and the generated deck. A pack that described one figure three ways would be the
# duplicate defect in prose.
#
# AN UNMEASURED ITEM GETS A SENTENCE TOO, and it is the REASON — never a blank and never a 0, which
# on a review pack reads as "we checked and there is nothing wrong".

_WINDOW_WORD = {"week": "this week", "period": "month to date", "today": "as of today"}


def _dollars(v):
    """A figure as money, in prose. The ONE place this report spells a dollar amount in a sentence;
    a column of money on a screen is rendered by the platform's money home
    (`frontend/src/lib/report-totals.ts`), which this never duplicates because a sentence is not a
    column. An absent figure is a dash — it is not $0.00."""
    f = _f(v)
    if f is None:
        return "—"
    return f"-${abs(f):,.2f}" if f < 0 else f"${f:,.2f}"


def _count(n, one, many=None):
    """`1 store` / `2 stores`, so no sentence reads '1 stores'."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _row_name(r):
    for k in ("address", "store", "store_code", "rep", "label"):
        v = str((r or {}).get(k) or "").strip()
        if v:
            return v
    return "(unnamed)"


def _pct_words(label, pct, before, after, fmt=str):
    return (f"{label} {'up' if pct >= 0 else 'down'} {abs(pct):.1f}% on the week before "
            f"({fmt(before)} → {fmt(after)}).")


def figure_words(item):
    """THE HEADLINE FIGURE as words — money through `_dollars`, a percentage with its sign, a count
    with its separators. One home, because the deck's title and the sentence beneath it must agree
    about the same number."""
    fig, unit = (item or {}).get("figure"), (item or {}).get("unit")
    if unit == "money":
        return _dollars(fig)
    f = _f(fig)
    if f is None:
        return "—"
    if unit == "pct":
        return f"{'+' if f > 0 else ''}{f:.1f}%"
    return f"{int(f):,}"


def narrative(item, scope=None):
    """The sentences for ONE folded item. PURE: reads only the item it is handed."""
    if not item:
        return []
    when = _WINDOW_WORD.get(item.get("window"), item.get("window") or "")
    if item.get("state") != MEASURED or item.get("figure") is None:
        return [f"Not measured {when}: {item.get('reason') or 'no reason was given'}."]
    d = item.get("detail") or {}
    fig = _f(item.get("figure")) or 0.0
    out = []
    key = item.get("key")
    if key == "profit":
        out.append(f"{_dollars(fig)} {'lost' if fig < 0 else 'earned'} across "
                   f"{_count(d.get('stores'), 'store')}, {when}, on revenue of "
                   f"{_dollars(d.get('revenue'))}.")
        losing = [r for r in (d.get("losing") or [])]
        if losing:
            worst = sorted(losing, key=lambda r: _f(r.get("net_profit")) or 0.0)[0]
            out.append(f"{_count(len(losing), 'store')} lost money, worst {_row_name(worst)} at "
                       f"{_dollars(worst.get('net_profit'))}. Those are the stores the meeting "
                       f"should spend its time on.")
        else:
            out.append("No store lost money this period.")
        if _f(d.get("money_at_no_store")):
            out.append(f"{_dollars(d.get('money_at_no_store'))} of it sits on lines the books place "
                       f"at no store, which is why the stores add to "
                       f"{_dollars(d.get('stores_net_income_total'))} rather than to the company "
                       f"figure.")
        if d.get("reconciles") is False:
            out.append(f"{_dollars(d.get('unexplained'))} of the company figure is not accounted "
                       f"for by the store rows — treat the store list as incomplete this period.")
        amb = d.get("stores_with_ambiguous_identity")
        if amb:
            out.append(f"{_count(len(amb), 'store is', 'stores are')} claimed by more than one "
                       f"declared store code, so their rows name one of them: "
                       f"{', '.join(str(x) for x in list(amb)[:3])}"
                       + ("…" if len(amb) > 3 else "") + ".")
    elif key == "growth":
        bc, rc = _f(d.get("boxes_change_pct")), _f(d.get("revenue_change_pct"))
        if bc is not None:
            out.append(_pct_words("Boxes", bc, f"{int(_f(d.get('boxes_prior')) or 0):,}",
                                  f"{int(_f(d.get('boxes')) or 0):,}"))
        if rc is not None:
            out.append(_pct_words("Revenue", rc, _dollars(d.get("revenue_prior")),
                                  _dollars(d.get("revenue"))))
        if not out:
            out.append("Both weeks were measured, but not on a figure they share.")
    elif key == "accessory":
        out.append(f"{_dollars(fig)} {'short of' if fig < 0 else 'ahead of'} target {when}: "
                   f"{_dollars(d.get('achieved_total'))} sold against "
                   f"{_dollars(d.get('target_total'))} targeted over "
                   f"{_count(d.get('stores_scored'), 'store')}.")
        if d.get("stores_behind"):
            out.append(f"{_count(d.get('stores_behind'), 'store is', 'stores are')} behind their "
                       f"own target.")
        if d.get("stores_without_a_target"):
            out.append(f"{_count(d.get('stores_without_a_target'), 'store has', 'stores have')} no "
                       f"accessory target saved, so nothing can be scored there — that is a setup "
                       f"gap, not a result.")
    elif key == "commission":
        top = (d.get("top") or [None])[0]
        out.append(f"{_dollars(fig)} earned by {_count(d.get('reps'), 'rep')} {when}"
                   + (f", top {_row_name(top)} at {_dollars(top.get('payout'))}" if top else "")
                   + ".")
        if d.get("unattached_rows"):
            out.append(f"{_count(d.get('unattached_rows'), 'row')} name no store, so they are "
                       f"counted in the consolidated pack and on no market pack.")
    elif key == "rep_watch":
        out.append(f"{_count(fig, 'rep is', 'reps are')} behind, of "
                   f"{_count(d.get('reps_ranked'), 'rep')} ranked {when}.")
        out.append("Each verdict is the Daily Action Plan's own — a rep behind their own store's "
                   "conversion, or short on a KPI this tenant declared.")
    elif key == "aging":
        within = d.get("within_window")
        out.append(f"{_count(fig, 'unsold device is', 'unsold devices are')} past the selling "
                   f"window their carrier declared, of "
                   f"{_count(d.get('devices'), 'device')} on the shelf"
                   + (f", with {int(within):,} still inside it" if within is not None else "")
                   + f". {_dollars(d.get('owed'))} is owed on that stock.")
        if d.get("carriers_with_no_declared_window"):
            out.append(f"{_count(d.get('carriers_with_no_declared_window'), 'carrier has', 'carriers have')}"
                       f" declared no selling window, so the house default answered for them.")
        if d.get("devices_with_no_carrier"):
            out.append(f"{_count(d.get('devices_with_no_carrier'), 'device')} could not be "
                       f"attributed to a carrier and is counted on a row of its own rather than "
                       f"added to one.")
        if d.get("covers"):
            out.append(f"Covers {d['covers']}.")
    elif key == "payroll":
        out.append(f"{_dollars(fig)} of payroll {when}, across {_count(d.get('stores'), 'store')}.")
        if _f(d.get("unattached")):
            out.append(f"{_dollars(d.get('unattached'))} attaches to no store and is held "
                       f"company-wide.")
    if d.get("stores_outside_the_roster") and str((scope or {}).get("kind")) == "company":
        out.append(f"{_count(d.get('stores_outside_the_roster'), 'row')} name a store the market "
                   f"roster does not list; the consolidated pack still counts them.")
    return out


def headline(review, period=""):
    """The pack's own opening: what this review is, and how complete it is."""
    if not review:
        return []
    cov = review.get("coverage") or {}
    scope = review.get("scope") or {}
    week = review.get("week") or {}
    out = [f"{scope.get('label') or ''} · week {week.get('label') or ''}"
           + (f" · month-to-date figures for {period}" if period else "") + ".",
           f"{cov.get('measured', 0)} of {cov.get('items', 0)} items measured."]
    missing = [i.get("label") for i in (review.get("items") or []) if i.get("state") != MEASURED]
    if missing:
        out.append("Not measured: " + "; ".join(str(m) for m in missing)
                   + ". Each one says why on its own slide.")
    if scope.get("note"):
        out.append(str(scope["note"]))
    return out
