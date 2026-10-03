"""PURE: derive a recurring WEEKLY SHIFT TEMPLATE for a rep from point-of-sale work history.

Owner request 2026-10-03, verbatim: *"Add the 9 sales reps from the 9 stores we worked on a schedule
as they have been working in the past, this needs analysis based on work history and then assign
schedule, again nothing is hard coded just as user entered and editable."*

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This module turns transaction history into a PROPOSED weekly pattern. It is an INFERENCE engine and
says so in its own output: a transaction proves a rep was on the floor at that moment, it does not
prove when the store opened or closed. Every number this module emits carries the sample count it
rests on, and a store with no usable evidence is REPORTED (`evidence='none'`), never handed an
invented pattern that reads like a measurement.

NO NEW TABLE, NO SIBLING MECHANISM (duplicate check, index rules). The output rows are shaped for
the mechanism that already exists:

  * `storeops.shift_templates` (mig `040`) — the per-employee canonical week (weekday -> store +
    times), already written by `POST /storeops/shift-templates/save-week` and already materialised
    into `storeops.shifts` for any week by `POST /storeops/shift-templates/apply`
    (`storeops/router.py`). This module only produces the template rows; applying them stays the
    existing endpoint's job, including its time-off skip and its employee-id canonicalisation.

So the only thing added here is the DERIVATION, and the derivation is pure: no client, no I/O, no
clock. The caller supplies the rows, the store resolver's answers and the config.

RULE TWO (config, never code). No store, rep, carrier or tenant name appears in this file. Every
threshold, percentile, rounding step and day grouping is a key in `DEFAULTS`, overridable per call
(and therefore per org row when a caller wires one). The day groups are config too: "Monday to
Saturday is one shape and Sunday is another" is an observation about a tenant's trade, not a fact
about calendars, so it is passed in rather than written into a branch.

STORE RESOLUTION IS NOT THIS MODULE'S JOB (one fact, one home). A POS feed's `store` string is not
`store_mapping.store_address` -- it can be an alias, a relocated address or a trailing-space variant.
The caller resolves each raw string with the platform's ONE store resolver (index section 13,
`commcalc.router._store_code_resolver`, backed by `store_mapping` + `store_aliases`) and passes the
answer in. A string that resolves to nothing is returned in `unresolved`: reported, never dropped
and never bound to a guessed code.

THE INFERENCE, STATED
---------------------
1. Group the rows into (store, rep, date) days; each day yields a first and a last transaction
   minute (when the feed carries timestamps) and a transaction count.
2. A (store, rep) pair is that store's REGULAR rep when it has at least `regular_min_days` days in
   at least one analysed month; otherwise it is RELIEF -- cover for the regular rep's days off.
   Relief gets NO recurring weekday rows, because a relief day is not a weekly commitment.
3. A regular rep is scheduled on a weekday when it worked at least `weekday_min_fraction` of that
   weekday's occurrences in the analysed window.
   The denominator of that fraction is `weekday_denominator` -- see DEFAULTS, where the one
   judgement call in this module is written out.
4. Start/end come from the STORE, pooled over every rep who transacted there (open and close are
   store facts, not rep facts), per day GROUP: the `open_percentile` of first-transaction minutes
   rounded DOWN to `round_minutes`, and the `close_percentile` of last-transaction minutes rounded
   UP. Rounding is outward on purpose -- the rep is on the floor before the first sale and after the
   last, so the honest envelope is wider than the observed one, never narrower.
5. A day group with fewer than `min_samples_per_group` timed days is not trusted; the store falls
   back to the house pattern (the median of the stores that DO have evidence) and is flagged.
"""

from statistics import median

# ── Config (RULE TWO: house defaults, every key overridable per call / per org row) ───────────────
DEFAULTS = {
    # A (store, rep) needs this many days in one analysed month to be the store's regular rep.
    "regular_min_days": 15,
    # A weekday is scheduled when the rep worked at least this fraction of its occurrences.
    "weekday_min_fraction": 0.5,
    # Which percentile of first/last transaction minutes stands in for open/close.
    "open_percentile": 10,
    "close_percentile": 90,
    # Start rounds DOWN to this many minutes, end rounds UP.
    "round_minutes": 30,
    # Day groups that share a shape. Weekday numbering matches `shift_templates.weekday`
    # (0 = Monday .. 6 = Sunday) and `datetime.date.weekday()`.
    "day_groups": [[0, 1, 2, 3, 4, 5], [6]],
    # A group with fewer timed days than this is not trusted on its own.
    "min_samples_per_group": 3,
    # The DENOMINATOR of the coverage fraction, and the one judgement call in this module:
    #   'traded'   -- the weekday's occurrences on which the store transacted at all. A store shut
    #                 on Sundays then never makes its rep look absent. But at a SINGLE-REP store the
    #                 store only trades when that rep is there, so the fraction is always 1.0 and
    #                 the weekday filter cannot fire. That is the honest reading of the evidence
    #                 (we cannot tell "shut" from "nobody in"), and it is the house default because
    #                 it never invents an absence.
    #   'calendar' -- every occurrence of that weekday between the store's first and last traded
    #                 day. The filter then bites at a single-rep store, at the cost of reading a
    #                 genuine closure as an absence.
    "weekday_denominator": "traded",
}

WEEKDAYS = 7


def config(overrides=None):
    """DEFAULTS merged with `overrides`. Unknown keys raise -- a silently ignored knob is a knob the
    caller thinks it set."""
    cfg = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise KeyError(f"unknown config key: {k}")
        cfg[k] = v
    return cfg


# ── Minute arithmetic ─────────────────────────────────────────────────────────────────────────────
def to_minutes(hhmm):
    """'09:45' -> 585. None/blank -> None. Accepts 'HH:MM' or 'HH:MM:SS'."""
    s = (hhmm or "").strip()
    if len(s) < 4 or ":" not in s:
        return None
    try:
        h, m = s.split(":")[0], s.split(":")[1]
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return None


def to_hhmm(minutes):
    """585 -> '09:45'. Minutes past midnight; 24:00 is allowed as an end-of-day bound."""
    m = int(minutes)
    return f"{m // 60:02d}:{m % 60:02d}"


def floor_to(minutes, step):
    return (int(minutes) // int(step)) * int(step)


def ceil_to(minutes, step):
    step = int(step)
    return -((-int(minutes)) // step) * step


def percentile(values, pct):
    """NEAREST-RANK percentile, pinned so the harness can prove it and so the answer never depends
    on a library's interpolation choice. `pct` 0..100. Empty -> None."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    rank = (float(pct) / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    # Nearest rank: no interpolation between neighbours.
    return xs[lo] if (rank - lo) < 0.5 else xs[hi]


# ── Step 1: rows -> days ──────────────────────────────────────────────────────────────────────────
def day_spans(rows):
    """Fold transaction rows into one record per (store_code, rep, date).

    Each row is a dict: `store_code` (ALREADY RESOLVED by the caller), `rep`, `date` ('YYYY-MM-DD'),
    `month` (the analysed bucket label, any string the caller uses -- a period name or 'YYYY-MM'),
    and `time` ('HH:MM', optional -- a feed month without timestamps still contributes the DAY).

    Returns {(store, rep, date): {"month", "first", "last", "txns", "timed"}}. `first`/`last` are
    minutes past midnight or None when that day carried no timestamp at all.
    """
    out = {}
    for r in rows:
        store, rep, date = r.get("store_code"), r.get("rep"), r.get("date")
        if not store or not rep or not date:
            continue
        key = (store, rep, date)
        d = out.setdefault(key, {"month": r.get("month"), "first": None, "last": None,
                                 "txns": 0, "timed": False})
        d["txns"] += 1
        m = to_minutes(r.get("time"))
        if m is None:
            continue
        d["timed"] = True
        d["first"] = m if d["first"] is None else min(d["first"], m)
        d["last"] = m if d["last"] is None else max(d["last"], m)
    return out


def resolve_rows(raw_rows, resolver):
    """Bind each raw row's feed store string to a store code with the caller's resolver, and REPORT
    what did not bind.

    `resolver` is any callable raw string -> code or None (in production: the platform's one store
    resolver, section 13). Returns (rows, unresolved) where `unresolved` is
    {raw string: row count} -- surfaced by the caller, never silently dropped.
    """
    rows, unresolved = [], {}
    for r in raw_rows:
        raw = r.get("store")
        code = resolver(raw) if raw is not None else None
        if not code:
            unresolved[raw] = unresolved.get(raw, 0) + 1
            continue
        rows.append({"store_code": code, "rep": r.get("rep"), "date": r.get("date"),
                     "month": r.get("month"), "time": r.get("time")})
    return rows, unresolved


# ── Step 2: who is the store's regular rep, and who is relief ─────────────────────────────────────
def weekday_of(date_str):
    """'YYYY-MM-DD' -> 0=Monday .. 6=Sunday, with no datetime import (Zeller's congruence), so this
    module stays stdlib-pure and its answer is pinned by the harness rather than inherited."""
    y, m, d = int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])
    if m < 3:
        m += 12
        y -= 1
    k, j = y % 100, y // 100
    h = (d + (13 * (m + 1)) // 5 + k + k // 4 + j // 4 + 5 * j) % 7   # 0=Saturday
    return (h + 5) % 7                                               # 0=Monday


def rep_profiles(spans, cfg):
    """Classify every (store, rep) as 'regular' or 'relief' and count its weekday coverage.

    Returns {(store, rep): {"kind", "days", "days_by_month", "weekday_days", "timed_days"}}.
    """
    acc = {}
    for (store, rep, date), d in spans.items():
        p = acc.setdefault((store, rep), {"days": 0, "days_by_month": {},
                                          "weekday_days": [0] * WEEKDAYS, "timed_days": 0})
        p["days"] += 1
        mk = d.get("month")
        p["days_by_month"][mk] = p["days_by_month"].get(mk, 0) + 1
        p["weekday_days"][weekday_of(date)] += 1
        if d.get("timed"):
            p["timed_days"] += 1
    for p in acc.values():
        busiest = max(p["days_by_month"].values()) if p["days_by_month"] else 0
        p["kind"] = "regular" if busiest >= cfg["regular_min_days"] else "relief"
    return acc


def _date_range(lo, hi):
    """Every 'YYYY-MM-DD' from lo to hi inclusive. Pure (no datetime import)."""
    out, y, m, d = [], int(lo[0:4]), int(lo[5:7]), int(lo[8:10])
    while True:
        cur = f"{y:04d}-{m:02d}-{d:02d}"
        out.append(cur)
        if cur >= hi:
            return out
        _, length = month_weekday_counts(y, m)
        d += 1
        if d > length:
            d, m = 1, m + 1
            if m > 12:
                m, y = 1, y + 1


def weekday_occurrences(spans, mode="traded"):
    """How many times each weekday occurs in the analysed window, per store -- the DENOMINATOR of
    the coverage fraction. See `DEFAULTS['weekday_denominator']` for what the two modes cost."""
    seen = {}
    for (store, _rep, date) in spans:
        seen.setdefault(store, set()).add(date)
    out = {}
    for store, dates in seen.items():
        counts = [0] * WEEKDAYS
        pool = dates if mode == "traded" else _date_range(min(dates), max(dates))
        for date in pool:
            counts[weekday_of(date)] += 1
        out[store] = counts
    return out


# ── Step 3: the store's hours, per day group ──────────────────────────────────────────────────────
def store_hours(spans, cfg):
    """Per (store, group index): the inferred start/end, pooled over EVERY rep at that store.

    Returns {(store, gi): {"start", "end", "hours", "samples", "evidence",
                           "observed_open", "observed_close"}}
    `evidence` is 'measured' when the group had at least `min_samples_per_group` timed days, else
    'thin'; a group with no timed days at all is absent from the result entirely (the caller falls
    back and flags it). `observed_*` keep the UNROUNDED percentile so the inference stays auditable.
    """
    group_of = {}
    for gi, days in enumerate(cfg["day_groups"]):
        for wd in days:
            group_of[wd] = gi
    firsts, lasts = {}, {}
    for (store, _rep, date), d in spans.items():
        if not d.get("timed"):
            continue
        gi = group_of.get(weekday_of(date))
        if gi is None:
            continue
        firsts.setdefault((store, gi), []).append(d["first"])
        lasts.setdefault((store, gi), []).append(d["last"])
    out = {}
    step = cfg["round_minutes"]
    for key, fs in firsts.items():
        ls = lasts[key]
        op = percentile(fs, cfg["open_percentile"])
        cl = percentile(ls, cfg["close_percentile"])
        start, end = floor_to(op, step), ceil_to(cl, step)
        out[key] = {"start": to_hhmm(start), "end": to_hhmm(end),
                    "hours": round((end - start) / 60.0, 2), "samples": len(fs),
                    "evidence": "measured" if len(fs) >= cfg["min_samples_per_group"] else "thin",
                    "observed_open": to_hhmm(op), "observed_close": to_hhmm(cl)}
    return out


def house_pattern(hours_by_store, cfg):
    """The fallback shape for a store with no (or too little) evidence: the MEDIAN start and end of
    every store whose group IS measured, rounded the same way. Returns {gi: {"start","end","hours",
    "stores"}}. Empty when nothing is measured -- there is then no honest fallback to offer.
    """
    buckets = {}
    for (_store, gi), h in hours_by_store.items():
        if h["evidence"] != "measured":
            continue
        b = buckets.setdefault(gi, {"s": [], "e": []})
        b["s"].append(to_minutes(h["start"]))
        b["e"].append(to_minutes(h["end"]))
    step = cfg["round_minutes"]
    out = {}
    for gi, b in buckets.items():
        start = floor_to(median(b["s"]), step)
        end = ceil_to(median(b["e"]), step)
        out[gi] = {"start": to_hhmm(start), "end": to_hhmm(end),
                   "hours": round((end - start) / 60.0, 2), "stores": len(b["s"])}
    return out


# ── Step 4: the weekly template rows ──────────────────────────────────────────────────────────────
def weekly_template(spans, cfg, assignments=None, fallback=None):
    """Build `storeops.shift_templates`-shaped rows plus the provenance behind each one.

    `assignments` optionally adds (store, rep) pairs that history cannot speak for -- a new store, a
    rep whose feed is missing. Such a pair takes the house pattern on every weekday in
    `day_groups`, and its rows are stamped `evidence='none'` so the caller must present it as an
    ASSUMPTION rather than a finding. `fallback` overrides the computed house pattern.

    Returns {"rows", "profiles", "hours", "house", "relief"}.
      rows: list of dicts (store_code, rep, weekday, start_time, end_time, scheduled_hours,
            evidence, samples) ordered by (store_code, rep, weekday) -- deterministic output, so two
            runs over the same history produce the same SQL.
      relief: the (store, rep) pairs deliberately given NO recurring rows, with their day counts.
    """
    profiles = rep_profiles(spans, cfg)
    hours = store_hours(spans, cfg)
    house = fallback if fallback is not None else house_pattern(hours, cfg)
    occ = weekday_occurrences(spans, cfg["weekday_denominator"])

    group_of = {}
    for gi, days in enumerate(cfg["day_groups"]):
        for wd in days:
            group_of[wd] = gi

    def shape(store, gi):
        h = hours.get((store, gi))
        if h and h["evidence"] == "measured":
            return h["start"], h["end"], h["hours"], "measured", h["samples"]
        hp = house.get(gi)
        if hp:
            why = "thin" if h else "none"
            return hp["start"], hp["end"], hp["hours"], f"house:{why}", (h or {}).get("samples", 0)
        return None

    rows, relief = [], []
    for (store, rep), p in profiles.items():
        if p["kind"] != "regular":
            relief.append({"store_code": store, "rep": rep, "days": p["days"],
                           "days_by_month": dict(p["days_by_month"]),
                           "weekday_days": list(p["weekday_days"])})
            continue
        denom = occ.get(store, [0] * WEEKDAYS)
        for wd in range(WEEKDAYS):
            gi = group_of.get(wd)
            if gi is None:
                continue
            if denom[wd] <= 0:
                continue
            if (p["weekday_days"][wd] / float(denom[wd])) < cfg["weekday_min_fraction"]:
                continue
            sh = shape(store, gi)
            if sh is None:
                continue
            start, end, hrs, evidence, samples = sh
            rows.append({"store_code": store, "rep": rep, "weekday": wd,
                         "start_time": start, "end_time": end, "scheduled_hours": hrs,
                         "evidence": evidence, "samples": samples,
                         "days_worked": p["weekday_days"][wd], "days_possible": denom[wd]})

    for a in (assignments or []):
        store, rep = a["store_code"], a["rep"]
        if any(r["store_code"] == store and r["rep"] == rep for r in rows):
            continue
        for gi, days in enumerate(cfg["day_groups"]):
            hp = house.get(gi)
            if not hp:
                continue
            for wd in days:
                rows.append({"store_code": store, "rep": rep, "weekday": wd,
                             "start_time": hp["start"], "end_time": hp["end"],
                             "scheduled_hours": hp["hours"], "evidence": "none", "samples": 0,
                             "days_worked": 0, "days_possible": 0})

    rows.sort(key=lambda r: (r["store_code"], r["rep"], r["weekday"]))
    relief.sort(key=lambda r: (r["store_code"], r["rep"]))
    return {"rows": rows, "profiles": profiles, "hours": hours, "house": house, "relief": relief}


# ── Step 5: what the template costs -- the cross-check against an entered monthly figure ──────────
def month_weekday_counts(year, month):
    """How many Mondays..Sundays a calendar month holds. Pure (no datetime import)."""
    leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
    lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    n = lengths[month - 1]
    counts = [0] * WEEKDAYS
    for d in range(1, n + 1):
        counts[weekday_of(f"{year:04d}-{month:02d}-{d:02d}")] += 1
    return counts, n


def monthly_hours(rows, year, month, store_code=None, rep=None):
    """Scheduled hours a template produces in a calendar month, optionally for one store or rep."""
    counts, _ = month_weekday_counts(year, month)
    total = 0.0
    for r in rows:
        if store_code is not None and r["store_code"] != store_code:
            continue
        if rep is not None and r["rep"] != rep:
            continue
        total += float(r["scheduled_hours"]) * counts[r["weekday"]]
    return round(total, 2)


def weekly_hours(rows, store_code=None, rep=None):
    return round(sum(float(r["scheduled_hours"]) for r in rows
                     if (store_code is None or r["store_code"] == store_code)
                     and (rep is None or r["rep"] == rep)), 2)


def rate_check(hours, rate, entered_monthly):
    """The sanity check the owner asked for: derived hours x proposed rate against the monthly figure
    already entered. Returns both directions -- the dollars the schedule implies at `rate`, and the
    rate `entered_monthly` implies at these hours -- so neither number is presented as the truth
    without the other beside it."""
    hours = round(float(hours), 2)
    dollars = round(hours * float(rate), 2)
    entered = float(entered_monthly)
    return {"hours": hours, "rate": round(float(rate), 4), "dollars": dollars,
            "entered_monthly": round(entered, 2),
            "delta": round(dollars - entered, 2),
            "delta_pct": round((dollars - entered) / entered * 100.0, 2) if entered else None,
            "rate_implied_by_entered": round(entered / hours, 4) if hours else None}
