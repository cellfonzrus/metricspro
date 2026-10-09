"""MONTH FOCUS — the ONE home for "what did management DECLARE for this month, and what is still
outstanding right now".

OWNER ASK 2026-10-09, verbatim: *"in the beginning of the month Market manager or above when they log
in should define the focus for the month -, update which initiative is driving spiffs that month and
assign targets to the store< the notification will come every week on Monday on the platform to update
any new commisison changes or spiff on any new products, assign targets to stores, offer temparoray
spiff, this module needs a creative busines smanager to dessign something out of the box to drive sales
offer spiff keeping the current oppprtunities in mind"*.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT

  It is a DECLARATION and a DUE LIST. One row per (org, month) says what this month is about, which
  pay type is meant to be driving it, and which temporary spiffs management has put on the table. From
  that row plus live measurements it answers one question — "what does management still owe this month
  and this week?" — and every surface that asks reads THIS module.

  It is NOT a second commission engine and it writes NOTHING a rep is paid from. A temporary spiff
  declared here is a STATED INTENT with a status; the money only moves when somebody changes
  `commcalc.payout_config` through the existing commission settings, which this module only ever READS
  (`spiff_reconciliation`). That separation is the point: the declaration and the pay config can
  DISAGREE, and saying so out loud is the whole value — a spiff nobody wired pays nobody, and a spiff
  in the pay config that this month's declaration never named is money moving with no stated reason.

WHY THE DUE LIST IS COMPUTED AND NEVER STORED

  "A notification MUST clear when the check says everything is OK" (owner, 2026-07-26 — the attention
  provider contract in `core/import_health`). A stored reminder rots: it fires after the thing is done,
  or stops firing when it is not. So `outstanding()` is a pure function of (the declaration, today, the
  live measurements) and every item it returns disappears the moment its cause is fixed. The weekly
  Monday reminder is the same function asked on a Monday — there is no cron job, no send record and no
  mailbox in this subsystem at all, because the owner asked for it "on the platform".

WHO MAY DECLARE — ONE HOME, AND THE WRONG HOME FOUND BY PROVING IT

  "Market manager or above" is a SCOPE-TIER question and it now has exactly one home:
  `app.core.scope.is_market_or_wider`. The first draft of this module asked
  `roster_reach(...) != ROSTER_OWN_STORE` instead, on the strength of that function's own docstring —
  and `harness_month_focus.py` §H3 caught it answering YES FOR A SALES REP, because `roster_reach`
  answers a different question (whom may this person see on a roster) and returns ROSTER_ALL for any
  role that has not opted into `scheduling_reach = 'span'`. So the tier tuple moved to `core.scope`,
  `closing/closer_pick.PICK_ANY_SCOPES` was wired to it rather than left as a second copy, and this
  module dereferences it. A tier added or renamed there changes this module with no edit here.

RULE TWO: not one carrier, tenant, store, product or pay-type name appears in this file. The month's
spiff initiative is a key the TENANT'S OWN rows offered (§60's `pay_type_options`), the focus categories
are the tenant's own target categories, and every play below is a rule over MEASURED numbers — never a
sentence about a named product.

PURE: stdlib only, no I/O, no client, never raises on bad input. The caller reads and writes the row;
this module decides. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §63.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from app.core.scope import is_market_or_wider

#: The Postgres table this module's declaration lives in (migration 1065), schema `commcalc`.
TABLE = "month_focus"

#: House defaults for the two knobs. Per-org overrides live on `commcalc.commission_org_config`
#: (`focus_declaration_days`, `focus_checkin_weekday`) — config rows, never code (RULE TWO).
DECLARATION_DAYS_DEFAULT = 7          # the focus is DUE inside the first N days of the month
CHECKIN_WEEKDAY_DEFAULT = 0           # `date.weekday()`: 0 = Monday, which is what the owner asked for

#: A declared temporary spiff's lifecycle. Only `approved`/`live` are EXPECTED to appear in the pay
#: config — a `proposed` one is on the table, not in the money, so it raises no reconciliation gap.
SPIFF_STATUSES = ("proposed", "approved", "live", "ended")
EXPECTED_LIVE = ("approved", "live")

#: Bounds, so a declaration cannot become an unbounded blob on a shared row.
MAX_TEMP_SPIFFS = 12
MAX_CATEGORIES = 12
MAX_TEXT = 2000


# ── little helpers ────────────────────────────────────────────────────────────────────────────────
def _s(v, limit=MAX_TEXT):
    """Any input -> a trimmed, whitespace-folded string, length-bounded. Never raises."""
    try:
        return " ".join(str(v if v is not None else "").split())[:limit]
    except Exception:
        return ""


def fold(v):
    """The ONE comparison key for a spiff name here: trimmed, whitespace-folded, case-insensitive.
    Used on both sides of `spiff_reconciliation`, so 'Tablet Push' and 'tablet  push' are one spiff."""
    return _s(v, MAX_TEXT).casefold()


def _num(v):
    """A number or None. '' / None / junk -> None, so a blank rate is never a 0 rate."""
    if v is None or isinstance(v, bool):
        return None
    try:
        s = str(v).strip().replace(",", "").replace("$", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _iso_day(v):
    """A 'YYYY-MM-DD' string, or '' — never a guess. A full timestamp is accepted and truncated."""
    s = _s(v, 40)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            date.fromisoformat(s[:10])
            return s[:10]
        except Exception:
            return ""
    return ""


def _as_date(v):
    s = _iso_day(v)
    try:
        return date.fromisoformat(s) if s else None
    except Exception:
        return None


# ── the calendar: the month, and the month's check-in days ────────────────────────────────────────
def month_bounds(year, month):
    """(first_day, last_day) of the month as `date`s, or (None, None) for a month that is not one.

    The CALLER canonicalises the period — this repo stores a period under two spellings and
    `router._pvariants` is that fact's home (§19.47), so no second period parser lives here.
    """
    try:
        y, m = int(year), int(month)
        if not (1 <= m <= 12) or not (1900 <= y <= 2999):
            return None, None
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    except Exception:
        return None, None


def checkin_days(year, month, weekday=CHECKIN_WEEKDAY_DEFAULT):
    """Every check-in day inside the month — by default every Monday. Ascending; [] if not a month."""
    first, last = month_bounds(year, month)
    if first is None:
        return []
    try:
        wd = int(weekday) % 7
    except Exception:
        wd = CHECKIN_WEEKDAY_DEFAULT
    d = first + timedelta(days=(wd - first.weekday()) % 7)
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def current_checkin(today, year, month, weekday=CHECKIN_WEEKDAY_DEFAULT):
    """The check-in day this reminder is ABOUT: the latest one on or before `today`, inside the month.

    None before the month's first check-in day (nothing is due yet) and None when `today` is outside
    the month — a manager looking at a past month is not nagged about its Mondays, and a manager
    looking at next month is not asked to confirm a week that has not happened.
    """
    day = _as_date(today) or (today if isinstance(today, date) else None)
    if day is None:
        return None
    days = [d for d in checkin_days(year, month, weekday) if d <= day]
    first, last = month_bounds(year, month)
    if first is None or not (first <= day <= last):
        return None
    return days[-1] if days else None


def declaration_window_end(year, month, days=DECLARATION_DAYS_DEFAULT):
    """The last day the month's focus is 'due' rather than 'overdue'. None if not a month."""
    first, last = month_bounds(year, month)
    if first is None:
        return None
    try:
        n = max(1, int(days))
    except Exception:
        n = DECLARATION_DAYS_DEFAULT
    return min(last, first + timedelta(days=n - 1))


# ── the declaration ───────────────────────────────────────────────────────────────────────────────
def normalise_declaration(payload, *, actor="", now_iso="", existing=None):
    """Whatever the screen sent -> the stored declaration shape. PURE, bounded, never raises.

    `existing` (the stored row's declaration) supplies the fields the caller did not send, so a
    partial save cannot blank the month's focus. Check-ins are NEVER taken from the payload — they
    are appended by `confirm_checkin` alone, so a client cannot mark a week confirmed by saving.
    """
    p = payload if isinstance(payload, dict) else {}
    old = existing if isinstance(existing, dict) else {}

    def pick(key, default):
        return p[key] if key in p else old.get(key, default)

    cats, seen = [], set()
    for c in (pick("categories", []) or []):
        c = _s(c, 80)
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            cats.append(c)
        if len(cats) >= MAX_CATEGORIES:
            break

    init = pick("spiff_initiative", {}) or {}
    init = init if isinstance(init, dict) else {}

    spiffs, names = [], set()
    for s in (pick("temp_spiffs", []) or []):
        s = s if isinstance(s, dict) else {}
        name = _s(s.get("name"), 120)
        if not name or fold(name) in names:
            continue
        names.add(fold(name))
        status = _s(s.get("status"), 20).lower()
        rate = _num(s.get("rate"))
        spiffs.append({
            "name": name,
            # A blank rate stays None. A spiff whose rate is unknown is not a $0 spiff — it is a
            # spiff nobody has priced, and `outstanding` says so.
            "rate": None if rate is None else round(rate, 2),
            "unit": _s(s.get("unit"), 40),
            "window_start": _iso_day(s.get("window_start")),
            "window_end": _iso_day(s.get("window_end")),
            "cap": (lambda c: None if c is None else round(c, 2))(_num(s.get("cap"))),
            "stores": [_s(x, 40) for x in (s.get("stores") or []) if _s(x, 40)][:200],
            "status": status if status in SPIFF_STATUSES else "proposed",
            "note": _s(s.get("note"), 500),
        })
        if len(spiffs) >= MAX_TEMP_SPIFFS:
            break

    return {
        "headline": _s(pick("headline", ""), 300),
        "categories": cats,
        "note": _s(pick("note", ""), MAX_TEXT),
        "spiff_initiative": {
            "pay_type": _s(init.get("pay_type"), 200),
            "label": _s(init.get("label"), 200),
            "rationale": _s(init.get("rationale"), MAX_TEXT),
        },
        "temp_spiffs": spiffs,
        "target_note": _s(pick("target_note", ""), MAX_TEXT),
        "declared_by": _s(actor, 200) or _s(old.get("declared_by"), 200),
        "declared_at": _s(now_iso, 40) or _s(old.get("declared_at"), 40),
        # Carried through untouched — the payload can never write this list.
        "checkins": [c for c in (old.get("checkins") or []) if isinstance(c, dict)][-64:],
    }


def is_declared(decl):
    """Has the month's focus actually been declared? A row that exists with an empty headline has NOT.

    The headline is the test because it is the one field a manager must write in their own words; a
    month with categories ticked and nothing said is a form half-filled, not a declared focus.
    """
    return bool(_s((decl or {}).get("headline"), 300))


def confirmed_checkins(decl):
    """The set of check-in days (ISO strings) somebody has confirmed for this month."""
    out = set()
    for c in ((decl or {}).get("checkins") or []):
        if isinstance(c, dict):
            d = _iso_day(c.get("week_start"))
            if d:
                out.add(d)
    return out


def confirm_checkin(decl, week_start, *, actor="", now_iso="", changes=None, note=""):
    """Append this week's confirmation. IDEMPOTENT: confirming an already-confirmed week returns the
    declaration unchanged, so a double click does not write a second row or move a timestamp.

    Returns (declaration, changed). `week_start` must be a real day; a junk value changes nothing.
    """
    d = _iso_day(week_start)
    base = decl if isinstance(decl, dict) else {}
    if not d:
        return base, False
    if d in confirmed_checkins(base):
        return base, False
    entry = {"week_start": d, "confirmed_at": _s(now_iso, 40), "confirmed_by": _s(actor, 200),
             "changes": [_s(x, 300) for x in (changes or []) if _s(x, 300)][:20],
             "note": _s(note, MAX_TEXT)}
    out = dict(base)
    out["checkins"] = ([c for c in (base.get("checkins") or []) if isinstance(c, dict)] + [entry])[-64:]
    return out, True


# ── declared against live: the pay config is the money, this row is the intent ────────────────────
def spiff_reconciliation(decl, live_spiff_names):
    """Does the pay config carry what management declared, and does management own what it carries?

    `live_spiff_names` is the names on `payout_config.custom_spiffs` for the SAME period, read by the
    caller — this module never touches the engine's config. Three answers, and each is a defect of a
    different kind:

      declared_not_live  a spiff management approved that NOBODY IS BEING PAID. The month's incentive
                         exists only on this screen.
      live_not_declared  a spiff in the pay config that this month's declaration never named. Money is
                         moving with no stated reason — the reverse defect, and the one nobody notices.
      matched            both sides agree.

    A `proposed` spiff is on the table, not in the money, so it is listed under `proposed` and raises
    no gap. That distinction is why the statuses exist.
    """
    live = {fold(n): _s(n, 200) for n in (live_spiff_names or []) if _s(n, 200)}
    expected, proposed = {}, []
    for s in ((decl or {}).get("temp_spiffs") or []):
        if not isinstance(s, dict):
            continue
        name = _s(s.get("name"), 200)
        if not name:
            continue
        if _s(s.get("status"), 20).lower() in EXPECTED_LIVE:
            expected[fold(name)] = name
        elif _s(s.get("status"), 20).lower() == "proposed":
            proposed.append(name)
    return {
        "declared_not_live": sorted(v for k, v in expected.items() if k not in live),
        "live_not_declared": sorted(v for k, v in live.items() if k not in expected),
        "matched": sorted(v for k, v in expected.items() if k in live),
        "proposed": sorted(proposed),
    }


# ── the due list ──────────────────────────────────────────────────────────────────────────────────
#: Where each item is FIXED. The provider contract requires the link to land on a surface where doing
#: the thing makes the item disappear on the next read — so a declaration item links to this module's
#: own page and a target item links to the page that writes targets.
PAGE = "/commcalc/month-focus"
TARGETS_PAGE = "/commcalc/targets"
PAY_CONFIG_PAGE = "/commcalc/commission-settings"
#: §61's page. Assigning targets is NOT re-implemented here: that module already splits ONE company
#: accessory goal across stores on each store's own attachment history, and already writes the target
#: rows (`POST /commcalc/accessory-target-plan/{period}/assign`). The month's declaration points at it
#: rather than growing an allocator of its own — one home for "what should this store's number be".
ACCESSORY_PLAN_PAGE = "/commcalc/accessory-target-plan"


def _item(key, severity, label, detail, count, deep_link, link_label):
    return {"key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": link_label}


def outstanding(decl, today, *, year, month, declaration_days=DECLARATION_DAYS_DEFAULT,
                checkin_weekday=CHECKIN_WEEKDAY_DEFAULT, stores_total=None,
                stores_with_target=None, live_spiff_names=None):
    """What management still owes THIS month and THIS week. PURE; [] when nothing is outstanding.

    Every item clears from its own `deep_link`:
      focus_undeclared        -> write the headline on this module's page
      initiative_unnamed      -> pick the month's spiff initiative there
      spiff_unpriced          -> give the declared spiff a rate there
      checkin_due             -> confirm this week's check-in there
      targets_unassigned      -> assign targets on the targets page (§5 writes them)
      spiff_declared_not_live -> wire it in the commission settings (or mark it proposed again)
      spiff_live_not_declared -> name it here, or take it out of the pay config

    `stores_total` / `stores_with_target` are None when the caller could not measure them. None is NOT
    zero: no target item is raised at all, because "no store has a target" and "I could not count the
    targets" look identical on a screen and only one of them is a management failure.
    """
    day = _as_date(today) or (today if isinstance(today, date) else None)
    first, last = month_bounds(year, month)
    if day is None or first is None:
        return []
    out = []
    window_end = declaration_window_end(year, month, declaration_days)
    in_month = first <= day <= last
    declared = is_declared(decl)

    # 1. THE FOCUS ITSELF. Due from day one; 'error' once the declaration window has passed, because a
    #    month half gone with no stated focus is not a reminder any more.
    if not declared:
        overdue = bool(window_end and day > window_end)
        if in_month or day > last:
            out.append(_item(
                "focus_undeclared", "error" if overdue else "warning",
                "This month's focus is not declared",
                (f"The month is {(day - first).days + 1} days in and nothing has been declared for it."
                 if overdue else
                 f"Declare it by {window_end.isoformat()} — the focus, the initiative driving spiffs, "
                 f"and the store targets."),
                1, PAGE, "Declare the month"))
    else:
        init = (decl or {}).get("spiff_initiative") or {}
        if not (_s(init.get("pay_type"), 200) or _s(init.get("label"), 200)):
            out.append(_item(
                "initiative_unnamed", "warning", "No initiative is named as driving spiffs",
                "The month has a focus but nothing is named as the pay type driving it, so nobody can "
                "tell which sales the month's money is behind.",
                1, PAGE, "Name the initiative"))

    # 2. A DECLARED SPIFF WITH NO RATE. It cannot be costed, so it cannot be approved — and a blank
    #    rate is never read here as $0 (`_num` keeps it None on purpose).
    unpriced = [_s(s.get("name"), 120) for s in ((decl or {}).get("temp_spiffs") or [])
                if isinstance(s, dict) and _s(s.get("name"), 120) and s.get("rate") is None]
    if unpriced:
        out.append(_item(
            "spiff_unpriced", "warning", "A temporary spiff has no rate",
            "No rate is set for " + ", ".join(unpriced[:4])
            + (f" and {len(unpriced) - 4} more" if len(unpriced) > 4 else "")
            + ", so what it would cost cannot be worked out.",
            len(unpriced), PAGE, "Set the rate"))

    # 3. THE WEEKLY CHECK-IN — the owner's Monday reminder, computed rather than sent.
    cur = current_checkin(day, year, month, checkin_weekday)
    if cur is not None and cur.isoformat() not in confirmed_checkins(decl):
        out.append(_item(
            "checkin_due", "warning", "This week's check-in is not done",
            f"Week of {cur.isoformat()}: confirm any commission or spiff change, assign targets to "
            f"stores that have none, and decide whether to put a temporary spiff on the table.",
            1, PAGE, "Do this week's check-in"))

    # 4. TARGETS. Measured by the caller; None means unmeasured and raises nothing (see docstring).
    if stores_total is not None and stores_with_target is not None:
        try:
            tot, have = int(stores_total), int(stores_with_target)
        except Exception:
            tot, have = 0, 0
        if tot > 0 and have < tot:
            out.append(_item(
                "targets_unassigned", "warning", "Stores with no target this month",
                f"{tot - have} of {tot} stores have no target for this month, so nothing they do can "
                f"be measured against a goal — and a spiff offered on top of no target pays for sales "
                f"you would have had anyway.",
                tot - have, TARGETS_PAGE, "Assign targets"))

    # 5. DECLARED AGAINST LIVE. Both directions; see `spiff_reconciliation`.
    rec = spiff_reconciliation(decl, live_spiff_names or [])
    if rec["declared_not_live"]:
        out.append(_item(
            "spiff_declared_not_live", "error", "An approved spiff is not in the pay config",
            "Nobody is being paid " + ", ".join(rec["declared_not_live"][:4])
            + (f" and {len(rec['declared_not_live']) - 4} more" if len(rec["declared_not_live"]) > 4 else "")
            + ". The incentive exists on this screen only.",
            len(rec["declared_not_live"]), PAY_CONFIG_PAGE, "Open commission settings"))
    if rec["live_not_declared"]:
        out.append(_item(
            "spiff_live_not_declared", "warning", "A spiff is being paid that this month never named",
            "The pay config carries " + ", ".join(rec["live_not_declared"][:4])
            + (f" and {len(rec['live_not_declared']) - 4} more" if len(rec["live_not_declared"]) > 4 else "")
            + ", which this month's declaration does not mention.",
            len(rec["live_not_declared"]), PAGE, "Name it, or take it out"))
    return out


# ── THE PLAYS — "something out of the box to drive sales, keeping the current opportunities in mind"
#
# Every play is a RULE OVER A MEASURED NUMBER, and it carries that number with it. There is no
# free-form text here and nothing is generated: a play either has a figure behind it or it does not
# appear. That is deliberate — a manager asked to spend money on a suggestion must be able to see what
# the suggestion is made of, and a sentence with no number behind it is a horoscope.
#
# `signals` is assembled by the caller from the homes that already own each number (§59's bands and
# `lagging()`, §60's pay-type options and per-store units, §5's targets). Nothing is measured in here.
#: Ranked cheapest-and-surest first: fix the free things before spending, spend before inventing.
PLAY_KEYS = ("target_first", "free_money", "unearned_type", "catch_up_band", "attach_ladder",
             "concentration")


def _play(key, rank, title, why, move, evidence, deep_link, also=None):
    return {"key": key, "rank": rank, "title": title, "why": why, "move": move,
            "evidence": evidence, "deep_link": deep_link, "also": also or []}


def cost_at(units, rate):
    """What a per-unit spiff over `units` units would cost. None when either side is unknown — a play
    never prints a cost it cannot stand behind."""
    u, r = _num(units), _num(rate)
    if u is None or r is None:
        return None
    return round(u * r, 2)


def plays(signals):
    """The month's suggested plays, best-first. PURE. [] when no signal fires — never a filler play."""
    s = signals if isinstance(signals, dict) else {}
    out = []

    # 1. TARGET FIRST. The cheapest play in the list: it costs nothing and every other play is worth
    #    less without it, because a spiff with no target pays for sales you would have had anyway.
    no_target = [x for x in (s.get("stores_without_target") or []) if _s(x, 40)]
    total = s.get("stores_total")
    if no_target:
        out.append(_play(
            "target_first", 1,
            "Assign the missing targets before you spend anything",
            f"{len(no_target)} store(s)"
            + (f" of {total}" if isinstance(total, int) and total else "")
            + " have no target this month.",
            "Set a target for each of them first. A temporary spiff on top of no target pays for "
            "sales that were going to happen anyway, and there is nothing to measure it against. For "
            "the accessory number, split one company goal across the stores on their own attachment "
            "history (§61) rather than giving everybody the same figure.",
            {"stores": no_target[:40], "without_target": len(no_target), "stores_total": total},
            TARGETS_PAGE, also=[ACCESSORY_PLAN_PAGE]))

    # 2. FREE MONEY. The carrier is ALREADY paying for something some stores earn nothing of. No new
    #    money is needed, so this outranks every play that spends.
    sp = s.get("spiff") or {}
    rate = _num(sp.get("rate_per_unit"))
    zero = [x for x in (sp.get("zero_stores") or []) if _s(x, 40)]
    if zero and rate and rate > 0:
        median = _num(sp.get("median_units_per_100_boxes"))
        boxes = _num(sp.get("zero_store_boxes"))
        forgone = None
        if median is not None and boxes is not None:
            # What those stores would have earned at the MIDDLE of their own peers, not at the best —
            # an estimate, labelled as one, from the median rather than the leader.
            forgone = cost_at(boxes * median / 100.0, rate)
        out.append(_play(
            "free_money", 2,
            "Coach the spiff the carrier is already paying — it needs no new money",
            f"{len(zero)} store(s) earned none of {_s(sp.get('label'), 120) or 'this pay type'}, "
            f"which pays ${rate:,.2f} per unit."
            + (f" At their own peer median that is about ${forgone:,.2f} left on the table."
               if forgone else ""),
            "Make it the month's focus and coach it store by store. The incentive is already in the "
            "carrier's hands, so the only thing missing is the sale.",
            {"label": _s(sp.get("label"), 120), "rate_per_unit": rate, "zero_stores": zero[:40],
             "median_units_per_100_boxes": median, "estimated_forgone": forgone,
             "estimate_basis": "peer median units per 100 boxes" if forgone else None},
            "/commcalc/spiff-impact"))

    # 3. A PAY TYPE THE TENANT EARNED NOTHING OF. The carrier offers it and no store sold into it at
    #    all — a focus week costs nothing but attention.
    unearned = [u for u in (s.get("unearned_pay_types") or []) if isinstance(u, dict)]
    if unearned:
        named = ", ".join(_s(u.get("label"), 80) for u in unearned[:3] if _s(u.get("label"), 80))
        out.append(_play(
            "unearned_type", 3,
            "Run a focus week on a pay type nobody earned",
            f"{len(unearned)} pay type(s) the carrier paid on this month returned nothing here"
            + (f": {named}" if named else "") + ".",
            "Pick one, make it the week's single ask, and check it on the following Monday. If a week "
            "of attention moves nothing, the pay type is not sellable here and the month's money "
            "belongs somewhere else.",
            {"pay_types": [{"label": _s(u.get("label"), 120), "rate_per_unit": _num(u.get("rate_per_unit"))}
                           for u in unearned[:8]]},
            "/commcalc/spiff-impact"))

    # 4. THE CATCH-UP. Stores behind stores with the SAME footfall — so the gap is sell-through, not
    #    location, and a targeted temporary spiff is defensible.
    lag = [x for x in (s.get("lagging") or []) if isinstance(x, dict)]
    boxes_lag = [x for x in lag if _s(x.get("metric"), 60) in ("", "boxes", "box_count", "total_boxes")]
    if boxes_lag:
        worst = max(boxes_lag, key=lambda x: _num(x.get("gap_pct")) or 0.0)
        units = sum((_num(x.get("gap_units")) or 0.0) for x in boxes_lag) or None
        out.append(_play(
            "catch_up_band", 4,
            "A short, targeted catch-up spiff for the stores behind their own traffic band",
            f"{len(boxes_lag)} store(s) are behind stores with the same footfall, the worst by "
            f"{(_num(worst.get('gap_pct')) or 0.0):.1f}%.",
            "Offer it to THOSE stores only, for the rest of the month, priced per unit of the gap and "
            "capped at the gap — not a company-wide spiff, which pays most to the stores already "
            "ahead. Same footfall on both sides of the comparison, so the gap is sell-through.",
            {"stores": [_s(x.get("store"), 40) for x in boxes_lag[:40]],
             "worst_store": _s(worst.get("store"), 40), "worst_gap_pct": _num(worst.get("gap_pct")),
             "band_median": _num(worst.get("median")), "gap_units_total": units,
             "cost_note": "cost = gap units x whatever rate you set (see cost_at)"},
            "/commcalc/peer-comparison"))

    # 5. THE ATTACH LADDER. The same comparison, on the accessory dollars each box carries.
    acc_lag = [x for x in lag if "accessory" in _s(x.get("metric"), 60).lower()]
    if acc_lag:
        worst = max(acc_lag, key=lambda x: _num(x.get("gap_pct")) or 0.0)
        out.append(_play(
            "attach_ladder", 5,
            "An attach ladder rather than a flat spiff",
            f"{len(acc_lag)} store(s) carry less on each box than their own traffic band's middle, "
            f"the worst by {(_num(worst.get('gap_pct')) or 0.0):.1f}%.",
            "Pay on the dollars ABOVE the band median per box rather than a flat amount per box: a "
            "flat spiff pays the same for a box that carried nothing, a ladder only pays for the "
            "attachment that was actually added.",
            {"stores": [_s(x.get("store"), 40) for x in acc_lag[:40]],
             "worst_store": _s(worst.get("store"), 40), "worst_gap_pct": _num(worst.get("gap_pct")),
             "band_median": _num(worst.get("median"))},
            "/commcalc/peer-comparison"))

    # 6. CONCENTRATION. A spiff whose units all sit in one store is one store's bonus, not an
    #    initiative — and the month's money is buying nothing new.
    top_share = _num(sp.get("top_store_share_pct"))
    earners = sp.get("earning_stores")
    if top_share is not None and top_share > 50.0 and isinstance(earners, int) and earners > 1:
        out.append(_play(
            "concentration", 6,
            "Spread the spiff, or stop paying for one store's habit",
            f"{top_share:.1f}% of this month's units came from one store "
            f"({_s(sp.get('top_store'), 40) or 'unnamed'}) across {earners} earning stores.",
            "Put a floor under it — a per-store minimum before the spiff pays, or a cap per store so "
            "the budget reaches the stores that are not selling it. As it stands the money is "
            "rewarding a habit one store already had.",
            {"top_store": _s(sp.get("top_store"), 40), "top_store_share_pct": top_share,
             "earning_stores": earners},
            "/commcalc/spiff-impact"))

    out.sort(key=lambda p: p["rank"])
    return out


# ── who may declare ───────────────────────────────────────────────────────────────────────────────
def may_declare(perms):
    """May this caller declare the month's focus? "Market manager or above" — scope market / region /
    company-wide, which is exactly `roster_reach`'s own tiering (see the module docstring).

    A platform super admin always may. An explicit per-role page grant is honoured either way, so the
    Roles page stays the override and this is never the last word for a tenant that wants it narrower
    or wider — config, never code.
    """
    # NO perms at all is a REFUSAL, never a pass: the caller could not be resolved, and the cost of
    # guessing wrong here is a rep declaring the company's month.
    if not isinstance(perms, dict) or not perms:
        return False
    p = perms
    if p.get("__super_admin"):
        return True
    explicit = (p.get("pages") or {}).get(PAGE)
    if isinstance(explicit, bool):
        return explicit
    # `default="all"`: in this codebase a role with no scope recorded is company-wide (the same
    # convention `closer_pick` has always used), so a seeded admin role is not locked out.
    return is_market_or_wider(p, default="all")
