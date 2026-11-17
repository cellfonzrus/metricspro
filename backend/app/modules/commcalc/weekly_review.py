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


def _in_scope(rows, scope):
    """The handed-in rows that belong to this scope's stores. A row with no store code is kept on the
    company pack only — it is money nobody attributed, and attributing it to a market would be a
    guess (the company pack reports it in `detail`)."""
    codes = set(scope.get("store_codes") or ())
    kept, unattached = [], []
    for r in rows or []:
        c = _code(r.get("store_code") or r.get("store"))
        if not c:
            unattached.append(r)
        elif c in codes:
            kept.append(r)
    return kept, unattached


def _profit(fact, scope):
    rows, unattached = _in_scope(fact, scope)
    if not rows:
        return _nothing("profit", "no store carried a gross-profit row for this week")
    total = sum(_f(r.get("net_profit")) or 0.0 for r in rows)
    revenue = sum(_f(r.get("total_rev")) or 0.0 for r in rows)
    out = [{"store_code": _code(r.get("store_code") or r.get("store")),
            "address": r.get("address") or r.get("store") or "",
            "revenue": _money(_f(r.get("total_rev")) or 0.0),
            "net_profit": _money(_f(r.get("net_profit")) or 0.0)} for r in rows]
    losing = [r for r in out if (r["net_profit"] or 0) < 0]
    return _item("profit", state=MEASURED, figure=_money(total), rows=out,
                 detail={"revenue": _money(revenue), "stores": len(out),
                         "losing": losing, "stores_losing_money": len(losing),
                         "unattached_rows": len(unattached)})


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
    rows, _un = _in_scope(fact, scope)
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
                         "target_total": _money(sum(r["target"] or 0.0 for r in out)),
                         "achieved_total": _money(sum(r["achieved"] or 0.0 for r in out)),
                         "stores_behind": sum(1 for r in out if (r["gap"] or 0) < 0)})


def _commission(fact, scope):
    rows, unattached = _in_scope(fact, scope)
    if not rows:
        return _nothing("commission", "no rep carried a commission row for this period")
    out = []
    for r in rows:
        out.append({"rep": r.get("rep") or r.get("storeops_name") or r.get("epay_salesperson") or "",
                    "store_code": _code(r.get("store_code") or r.get("store")),
                    "payout": _money(_f(r.get("total_payout")) or 0.0),
                    "units": _f(r.get("units")) if r.get("units") is not None else None})
    out.sort(key=lambda r: r["payout"] or 0.0, reverse=True)
    total = sum(r["payout"] or 0.0 for r in out)
    return _item("commission", state=MEASURED, figure=_money(total), rows=out,
                 detail={"reps": len(out), "top": out[:5], "bottom": out[-5:],
                         "unattached_rows": len(unattached)})


def _rep_watch(fact, scope):
    rows, _un = _in_scope(fact, scope)
    if rows is None or (not rows and not isinstance(fact, list)):
        return _nothing("rep_watch", "the rep rankings were not measured for this period")
    if not rows:
        return _nothing("rep_watch", "no rep in this scope was ranked this period")
    behind = [{"rep": r.get("rep") or r.get("name") or "",
               "store_code": _code(r.get("store_code") or r.get("store")),
               "verdict": r.get("verdict") or r.get("reason") or "",
               "rank": r.get("rank")} for r in rows if r.get("behind")]
    return _item("rep_watch", state=MEASURED, figure=len(behind), rows=behind,
                 detail={"reps_ranked": len(rows), "reps_behind": len(behind)})


def _aging(fact, scope):
    """Inventory aging folded PER CARRIER, each against ITS OWN declared selling window.

    The buckets arrive already stamped by `commcalc/device_aging_policy` through `/asset/aging` —
    this file never re-bucketes a device, because the edges are the carrier's contract and that fact
    has one home. A carrier that declared no window is REPORTED with `declared: False` rather than
    quietly inheriting another carrier's 60 days, and devices whose carrier is unknown get a row of
    their own instead of being added to somebody.
    """
    if not isinstance(fact, dict):
        return _nothing("aging", "the inventory aging report was not read")
    rows = fact.get("rows") or []
    policies = fact.get("by_carrier") or []
    if not rows and not policies:
        return _nothing("aging", "no unsold device is on inventory for this tenant")
    by_id = {}
    for p in policies:
        by_id[p.get("carrier_id")] = {
            "carrier_id": p.get("carrier_id"), "label": p.get("label") or "(unnamed carrier)",
            "sell_within_days": p.get("sell_within_days"),
            "declared": bool(p.get("declared")),
            "under45": 0, "warn": 0, "missed": 0, "unknown_age": 0, "devices": 0, "owed": 0.0}
    no_carrier = 0
    for r in rows:
        cid = r.get("carrier_id")
        if cid not in by_id:
            if cid is None:
                no_carrier += 1
            by_id[cid] = {"carrier_id": cid,
                          "label": ("Devices with no carrier recorded" if cid is None
                                    else "(carrier not declared)"),
                          "sell_within_days": None, "declared": False,
                          "under45": 0, "warn": 0, "missed": 0, "unknown_age": 0,
                          "devices": 0, "owed": 0.0}
        cell = by_id[cid]
        bucket = str(r.get("aging_bucket") or "unknown_age")
        cell[bucket] = cell.get(bucket, 0) + 1
        cell["devices"] += 1
        cell["owed"] = round(cell["owed"] + (_f(r.get("owed_to_vip")) or 0.0), 2)
    out = sorted(by_id.values(), key=lambda c: (-(c["missed"]), str(c["label"]).lower()))
    missed = sum(c["missed"] for c in out)
    return _item("aging", state=MEASURED, figure=missed, rows=out,
                 detail={"devices": sum(c["devices"] for c in out),
                         "owed": round(sum(c["owed"] for c in out), 2),
                         "carriers": len([c for c in out if c["carrier_id"] is not None]),
                         "carriers_with_no_declared_window":
                             len([c for c in out if not c["declared"]]),
                         "devices_with_no_carrier": no_carrier})


def _payroll(fact, scope):
    rows, unattached = _in_scope(fact, scope)
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
                 detail={"stores": len(out), "unattached": _money(unattached_total)})


#: key -> the fold. ONE dispatch table, so an item cannot exist in `ITEMS` with no fold behind it
#: (and `harness_weekly_review.py` fails the build if the two ever disagree).
FOLDS = {"profit": _profit, "growth": _growth, "accessory": _accessory, "commission": _commission,
         "rep_watch": _rep_watch, "aging": _aging, "payroll": _payroll}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. ONE REVIEW, AND THE PACK
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def review(scope, *, facts, week):
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
    return {"scope": dict(scope), "week": dict(week), "items": items,
            "coverage": {"items": len(items), "measured": len(measured),
                         "not_measured": len(items) - len(measured)}}


def build(scope_list, *, facts, week, as_of, facts_by_scope=None):
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
        r = review(scope, facts=f, week=week)
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
