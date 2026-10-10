"""CASH VARIANCE — what management actually received, against the drawer and against the POS.

OWNER ASK 2026-10-10, verbatim (OWNER-QUOTE-BEGIN):
  "need to create a report after management recon to assess the shortage and overage based on what
   the management received compared with the b2b data and the cash envelope, the data will never
   match so we ignore upto $5 of variance, the system should then be smart to find the trends of a
   particular store / employee and highlight the same- ti shoudl be all dynamic with all filters"
(OWNER-QUOTE-END)

THE OWNER IS RIGHT THAT THE DATA NEVER MATCHES, AND THE REASON IS NOT SHORTAGE
──────────────────────────────────────────────────────────────────────────────
Measured on live house-org data, 2026-09-01 → 2026-10-10, before a line of this was written:

  · 275 store-days carry a management count (`cash_pickup.actual_picked_amount`).
  · Scored against the figure STORED on the pickup row, 141 of them read SHORT.
  · Scored against the NET store cash (total cash minus the declared bill-payment cash), 194 read
    OK, 70 over, 11 short.
  · Six samples, checked by hand, were short by the bill-payment cash TO THE CENT:
    574−324=250, 866−585=281, 240−200=40, 335−260=75, 1699−929=770, 1378−1083=295.

So the shortages were the bill-payment cash, which leaves the store in a DIFFERENT envelope
(`billpay_pickup`), and a report that called them shortages would have accused nine named employees
of about $20,000 they never took. That is the defect this module exists to make impossible.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix")
──────────────────────────────────────────────────────────────────────────────────
The instance is "the management count reads short by the bill-pay cash". The class is:

  **A VARIANCE BETWEEN TWO CASH FIGURES IS MEANINGLESS UNLESS BOTH ARE ON THE SAME BASIS, AND
  NOTHING MADE A CALLER PROVE IT.** Subtraction does not care, so every reader that holds two bare
  floats can manufacture a shortage out of a definition.

The platform already RECORDS the basis of each figure and had nowhere to use it:
  · `cash_pickup.amount_basis`     (mig 1039) — which basis the pickup's expectation was scored on.
  · `envelope_count.basis`         (mig 1036) — which cash a management count counted.
  · `deposit_recon.cash_for_basis` (mig 509)  — the ONE home for the total/net/bill-pay split.

So the fix here is structural, not a special case: in this module a cash figure is never a float. It
is a `leg(amount, basis, source)`, and `compare()` REFUSES to subtract across bases — it returns
`measured: False` with a reason instead of a number. A caller cannot opt out, because there is no
overload that takes two bares floats.

ABSENCE IS NEVER ZERO. A leg nobody recorded is `measured: False`, never 0.00. Live, that matters
enormously: `billpay_pickup` holds 50 rows of which exactly ONE has been counted, so the whole-drawer
management figure is NOT MEASURABLE on 264 of 275 counted store-days. This module says so. It does
not reconstruct the missing envelope as zero and bill the rep for it.

THE $5 BAND IS CONFIG, NEVER CODE (RULE TWO)
────────────────────────────────────────────
The owner's $5 is this tenant's current answer, not a constant of the universe. `HOUSE_BAND` below is
the ONE literal in the subsystem; every other reader takes the band as an argument, and the per-org
override is `commcalc.cash_pickup_config.cash_variance_band` (mig 1070), resolved by `band()`.
`backend/harness_cash_variance.py` §F FAILS THE BUILD if a second dollar literal appears here or if
the report endpoint spells a band of its own.

NO NEW VARIANCE MATH. The short/over/ok verdict is `deposit_recon.status_for` — the platform's
existing truth table, which already takes a tolerance — called with the resolved band. A second
truth table is the sibling derivation the index rules forbid.

WHAT IS DELIBERATELY NOT HERE
─────────────────────────────
· No chargeback. `envelope_report.chargeback_parent_row` is the one home for turning a counted
  shortage into money off a rep's pay, and it is reached from the Management Envelope Receipt where a
  human ticks a box. A trend report must not create liabilities on its own.
· No flag writes. `cash_watchdog` (§53) owns cash FINDINGS, off the submit trail. This module is a
  report over the RECEIPT side; `trends()` returns highlights for the screen, and the two modules
  compare different pairs of figures on purpose.

💰 MOVES NO MONEY. Read-only derivation for one report. Books nothing, pays nothing, writes nothing.

PURE: no I/O, no framework import, no clock. Proof: backend/harness_cash_variance.py.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §72.
"""
from __future__ import annotations

#: The house answer to "below how many dollars is a cash difference noise rather than a variance".
#: THE ONLY dollar literal in this subsystem — owner 2026-10-10, "we ignore upto $5 of variance".
#: Overridden per org by `commcalc.cash_pickup_config.cash_variance_band` (mig 1070) through `band()`.
HOUSE_BAND = 5.00

#: The house answer to "how many out-of-band days make a trend rather than a bad day". Mirrors
#: `flag_registry.DEFAULT_PARAMS['CASH_REPEAT_VARIANCE']['min_days']` deliberately: the same question
#: already has an answer on the findings side, and two different answers would be the divergence.
HOUSE_MIN_DAYS = 3

#: Which cash a figure is about. The keys are `deposit_recon`'s own basis words (mig 509), NOT a new
#: vocabulary — a third spelling of "the whole drawer" is exactly the defect above.
BASIS_TOTAL = "total_cash"           # the whole drawer, bill-payment cash included
BASIS_STORE = "store_cash"           # the drawer NET of bill-payment cash
BASIS_BILLPAY = "bill_payment_cash"  # the bill-payment cash alone
BASES = (BASIS_TOTAL, BASIS_STORE, BASIS_BILLPAY)

#: Why a comparison produced no number. Each is a sentence a screen can show as-is; none of them is
#: a dollar figure, because "we could not measure this" must never render as money.
UNMEASURED_REASONS = {
    "leg_absent": "one of the two figures was never recorded",
    "basis_unknown": "one of the two figures does not say which cash it counted",
    "basis_mismatch": "the two figures count different cash, so the difference is a definition, "
                      "not a variance",
}

VERDICTS = ("short", "over", "ok")


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm_basis(basis):
    b = str(basis or "").strip().lower()
    return b if b in BASES else None


# ── A cash figure, with its basis. ─────────────────────────────────────────────────────────────
def leg(amount, basis, source, detail=None):
    """PURE: one cash figure as this module carries it — the amount, WHICH cash it is, and where it
    came from. `amount=None` means nobody recorded it, which is not the same as 0.00 and is why
    `present` exists as its own key.

    `basis` outside `BASES` (including None, an un-migrated row, or a word a future migration adds
    without telling this module) resolves to None and makes every comparison with this leg
    `basis_unknown` rather than guessing. A guess here is the $20,000 accusation in the docstring.
    """
    return {
        "amount": None if amount is None else _f(amount),
        "present": amount is not None,
        "basis": _norm_basis(basis),
        "basis_declared": _norm_basis(basis) is not None,
        "source": str(source or "") or None,
        "detail": detail or None,
    }


ABSENT = leg(None, None, None)


def band(config_row=None):
    """PURE: the dollar band in force for an org — the house default with the org's own row over it.

    `config_row` is what the caller read from `commcalc.cash_pickup_config` for ITS OWN org, so this
    module does no I/O and cannot leak one tenant's band into another's report. A blank, negative or
    unparseable value is the house default, NOT zero: an operator clearing a cell must not silently
    turn every rounding difference into a shortage.
    """
    raw = (config_row or {}).get("cash_variance_band")
    if raw in (None, ""):
        return HOUSE_BAND
    try:
        v = round(float(raw), 2)
    except (TypeError, ValueError):
        return HOUSE_BAND
    return v if v >= 0 else HOUSE_BAND


def min_days(config_row=None):
    """PURE: how many out-of-band days make a trend. Same resolution rules as `band`."""
    raw = (config_row or {}).get("cash_variance_min_days")
    if raw in (None, ""):
        return HOUSE_MIN_DAYS
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return HOUSE_MIN_DAYS
    return v if v >= 1 else HOUSE_MIN_DAYS


# ── The comparison. The only place two cash figures are allowed to meet. ───────────────────────
def compare(got, expected, dollar_band):
    """PURE: `got` against `expected`, both `leg()` dicts → the variance and the verdict, or a
    refusal with a reason.

    variance = got − expected, so NEGATIVE IS SHORT on every surface, matching
    `deposit_recon.status_for` and every other closing recon screen.

    IT REFUSES RATHER THAN SUBTRACTING when the two legs do not count the same cash. There is
    deliberately no argument to force it, and no overload taking bare floats — a caller who wants a
    number across bases has to convert a leg first, in the open, with the bill-pay figure in hand.

    The verdict itself is NOT computed here: it is `deposit_recon.status_for(variance, band)`, the
    platform's existing truth table. Imported at call time so this module keeps an empty import list
    and stays provably DB-free.
    """
    from . import deposit_recon
    a, b = got or ABSENT, expected or ABSENT
    out = {
        "got": a, "expected": b,
        "variance": None, "verdict": None, "measured": False,
        "reason": None, "reason_label": None, "basis": None, "band": _f(dollar_band),
    }
    if not (a.get("present") and b.get("present")):
        reason = "leg_absent"
    elif not (a.get("basis_declared") and b.get("basis_declared")):
        reason = "basis_unknown"
    elif a["basis"] != b["basis"]:
        reason = "basis_mismatch"
    else:
        var = round(_f(a["amount"]) - _f(b["amount"]), 2)
        out.update({
            "variance": var,
            # status_for returns 'ok' | 'short' | 'over' — this module adopts its words rather than
            # inventing a fourth spelling of the same three states.
            "verdict": deposit_recon.status_for(var, _f(dollar_band)),
            "measured": True, "basis": a["basis"],
        })
        return out
    out["reason"] = reason
    out["reason_label"] = UNMEASURED_REASONS[reason]
    return out


#: The pairs this report puts side by side, and WHY each one is worth a column. Declared as data so
#: the endpoint and the screen iterate it instead of each spelling the pair names — a new pair gets
#: its column, its rollup and its trend detection for free and cannot get a stale header.
PAIRS = (
    {
        "key": "received_vs_envelope",
        "label": "Management received vs the cash envelope",
        "note": "What was counted out of the envelope against what the closing declared for that "
                "same cash. This is the pair that answers 'did the money that left the store arrive'.",
    },
    {
        "key": "declared_vs_pos",
        "label": "Cash envelope vs B2B",
        "note": "What the rep declared as cash in the drawer against the cash the point of sale "
                "rang. A gap here is a counting or a ringing problem before anyone collected it.",
    },
    {
        "key": "received_vs_pos",
        "label": "Management received vs B2B",
        "note": "The end-to-end check. Measurable only when BOTH envelopes were counted, because "
                "the POS figure is the whole drawer and the cash envelope is only part of it.",
    },
)
PAIR_KEYS = tuple(p["key"] for p in PAIRS)


def day_row(identity, received, declared, pos, dollar_band):
    """PURE: one line of the report — one rep, one store, one day, three legs and three verdicts.

    `identity` carries the keys a filter and a rollup need (close_date, store_code, store_address,
    market, employee_name) and is copied, not referenced, so a caller's dict cannot be mutated.

    EVERY pair is present in the output, measured or not. A pair dropped when it cannot be measured
    reads on screen as a pair that tied, which is the absence-is-zero defect in display form.
    """
    out = dict(identity or {})
    out["legs"] = {"received": received or ABSENT, "declared": declared or ABSENT, "pos": pos or ABSENT}
    out["pairs"] = {
        "received_vs_envelope": compare(received, declared, dollar_band),
        "declared_vs_pos": compare(declared, pos, dollar_band),
        "received_vs_pos": compare(received, pos, dollar_band),
    }
    # The headline is the pair the owner asked about first, so a screen sorting "worst first" and a
    # rollup counting "bad days" agree about which number they mean.
    head = out["pairs"]["received_vs_envelope"]
    out["variance"] = head["variance"]
    out["verdict"] = head["verdict"] if head["measured"] else "not_measured"
    out["measured"] = bool(head["measured"])
    out["reason"] = head["reason"]
    out["reason_label"] = head["reason_label"]
    return out


def verdict_filter(rows, verdict):
    """PURE: the report's own filter — 'short' | 'over' | 'ok' | 'not_measured' | 'discrepancy'
    (short OR over). Unknown or blank leaves the rows alone, never a silent drop on a typo."""
    v = str(verdict or "").strip().lower()
    if v in VERDICTS or v == "not_measured":
        return [r for r in rows if r.get("verdict") == v]
    if v == "discrepancy":
        return [r for r in rows if r.get("verdict") in ("short", "over")]
    return list(rows)


# ── Rollups. ONE summariser, so a per-store line can never disagree with the tiles above it. ───
def totals(rows, pair_keys=PAIR_KEYS):
    """PURE: the summary for any set of rows — the tiles, and the same shape per pair.

    Every pair is counted the same way, from `PAIRS`, so the tiles cannot drift from the columns.
    `not_measured` is a FIRST-CLASS count beside short/over/ok: how much of the question cannot be
    answered yet is the most useful number on this report today (264 of 275 live store-days).
    """
    out = {"days": len(rows), "pairs": {}}
    for key in pair_keys:
        p = {"measured": 0, "not_measured": 0, "ok": 0, "short": 0, "over": 0,
             "short_total": 0.0, "over_total": 0.0, "net_variance": 0.0, "reasons": {}}
        for r in rows:
            c = (r.get("pairs") or {}).get(key) or {}
            if not c.get("measured"):
                p["not_measured"] += 1
                why = c.get("reason") or "leg_absent"
                p["reasons"][why] = p["reasons"].get(why, 0) + 1
                continue
            p["measured"] += 1
            v = _f(c.get("variance"))
            p[c.get("verdict") or "ok"] = p.get(c.get("verdict") or "ok", 0) + 1
            if c.get("verdict") == "short":
                p["short_total"] = round(p["short_total"] + (-v), 2)
            elif c.get("verdict") == "over":
                p["over_total"] = round(p["over_total"] + v, 2)
        # Over MINUS short, beside the two gross figures and never instead of them: a rep $50 short
        # one day and $50 over the next nets to zero while having twice handed over the wrong cash.
        p["net_variance"] = round(p["over_total"] - p["short_total"], 2)
        out["pairs"][key] = p
    return out


def _group(rows, keyfn, label_key):
    groups = {}
    for r in rows or []:
        groups.setdefault(keyfn(r), []).append(r)
    out = []
    for name, grp in groups.items():
        t = totals(grp)
        dates = sorted(d for d in (str(g.get("close_date") or "")[:10] for g in grp) if d)
        head = t["pairs"]["received_vs_envelope"]
        out.append({
            label_key: name, **t,
            "first_close": dates[0] if dates else None,
            "last_close": dates[-1] if dates else None,
            "_rank": (head["short_total"], head["short"] + head["over"], t["days"]),
        })
    # Worst first: biggest shortage, then most bad days, then most days. A group whose envelopes were
    # never counted has nothing measured and sorts to the BOTTOM — never to the top as a clean sheet.
    out.sort(key=lambda g: (-g["_rank"][0], -g["_rank"][1], -g["_rank"][2], str(g[label_key])))
    for g in out:
        g.pop("_rank", None)
    return out


def by_employee(rows):
    """PURE: the same rows per person. Identity is `employee_name`, because that is the only identity
    `commcalc.daily_closing` carries — so same-named people merge and a rename splits a history. That
    is a roster limitation, stated rather than papered over; `stores` makes a merge visible."""
    out = _group(rows, lambda r: (r.get("employee_name") or "").strip() or "(unnamed)", "employee_name")
    byname = {}
    for r in rows or []:
        byname.setdefault((r.get("employee_name") or "").strip() or "(unnamed)", []).append(r)
    for g in out:
        grp = byname.get(g["employee_name"], [])
        g["stores"] = sorted({str(x.get("store_address") or x.get("store_code") or "") for x in grp} - {""})
    return out


def by_store(rows):
    """PURE: the same rows per store. Keyed on `store_code` UPPER-CASED, because the measured
    2026-10-09 defect was `b-1115` becoming a phantom store holding $289 — a comparison that
    case-folds nothing invents stores."""
    out = _group(rows, lambda r: (r.get("store_code") or "").strip().upper() or "(no store)", "store_code")
    byname = {}
    for r in rows or []:
        byname.setdefault((r.get("store_code") or "").strip().upper() or "(no store)", []).append(r)
    for g in out:
        grp = byname.get(g["store_code"], [])
        g["store_address"] = next((str(x.get("store_address")) for x in grp if x.get("store_address")), None)
        g["employees"] = sorted({str(x.get("employee_name") or "") for x in grp} - {""})
    return out


# ── The trend. "Be smart to find the trends … and highlight the same" (owner 2026-10-10). ──────
#: What makes a run of days a trend worth a manager's time, as data rather than as three if-branches
#: buried in a loop. Each test takes the group's own measured numbers; none of them reads a dollar
#: literal, and the thresholds come from `band()` / `min_days()`.
TREND_KINDS = (
    {
        "key": "repeat_short",
        "label": "Short on repeated days",
        "why": "Money missing more than once is a habit or a procedure, not a miscount. Watch a "
               "close in person before treating it as either.",
    },
    {
        "key": "repeat_over",
        "label": "Over on repeated days",
        "why": "A drawer that is repeatedly OVER is usually cash declared short at closing, not a "
               "gift. It understates the day and hides the real shortage.",
    },
    {
        "key": "one_direction",
        "label": "Always out the same way",
        "why": "Random counting error lands on both sides. A run that is only ever one direction is "
               "a systematic cause: a basis, a float, or a step done the same wrong way each day.",
    },
    {
        "key": "nothing_measurable",
        "label": "Nothing can be measured here",
        "why": "Every day in range is unmeasurable, so this store or person is invisible to the "
               "whole control rather than clean. Fix the recording before reading the numbers.",
    },
)
TREND_BY_KEY = {t["key"]: t for t in TREND_KINDS}


def trends(groups, label_key, days_needed=None):
    """PURE: the highlights, over the rollup groups `by_store`/`by_employee` already produced.

    IT RE-DERIVES NOTHING. Every number quoted in a highlight is read off the group it came from, so
    a highlight can never disagree with the table under it — two summarisers over one set of rows is
    the divergence the house rules forbid, and it would surface here as a banner contradicting its
    own row.

    Ordered by dollars at stake, then by how many days are involved, so the top highlight is the one
    worth opening first.
    """
    need = int(days_needed or HOUSE_MIN_DAYS)
    out = []
    for g in groups or []:
        p = (g.get("pairs") or {}).get("received_vs_envelope") or {}
        name = str(g.get(label_key) or "")
        short, over, ok = p.get("short", 0), p.get("over", 0), p.get("ok", 0)
        measured, bad = p.get("measured", 0), p.get("short", 0) + p.get("over", 0)

        def add(kind, days, dollars, detail):
            k = TREND_BY_KEY[kind]
            out.append({
                "kind": kind, "label": k["label"], "why": k["why"],
                label_key: name, "subject": name, "subject_kind": label_key,
                "days": days, "dollars": round(_f(dollars), 2), "detail": detail,
                "first_close": g.get("first_close"), "last_close": g.get("last_close"),
            })

        if measured == 0 and g.get("days"):
            why = sorted(p.get("reasons", {}).items(), key=lambda kv: -kv[1])
            add("nothing_measurable", g.get("days", 0), 0.0,
                f"{g.get('days', 0)} day(s) in range and not one of them could be measured"
                + (f" — mostly because {UNMEASURED_REASONS.get(why[0][0], why[0][0])}" if why else ""))
            continue
        if short >= need:
            add("repeat_short", short, p.get("short_total", 0.0),
                f"short on {short} of {measured} measured day(s), {_money(p.get('short_total'))} in total")
        if over >= need:
            add("repeat_over", over, p.get("over_total", 0.0),
                f"over on {over} of {measured} measured day(s), {_money(p.get('over_total'))} in total")
        # One-directional only when there is a run to speak of AND the other direction never
        # happened. Reported even when neither direction alone reached `need`, because consistency
        # is the signal here and `need` is a count threshold, not a consistency one.
        if bad >= need and (short == 0) != (over == 0):
            direction = "over" if short == 0 else "short"
            add("one_direction", bad, p.get("over_total" if short == 0 else "short_total", 0.0),
                f"{bad} out-of-band day(s) in range, every one of them {direction}, "
                f"none the other way ({ok} day(s) inside the band)")
    out.sort(key=lambda t: (-_f(t["dollars"]), -t["days"], t["subject"], t["kind"]))
    return out


def _money(n):
    return f"${_f(n):,.2f}"


def measurement_blockers(rows):
    """PURE: why the report cannot answer more than it does, counted — so the gap is REPORTED rather
    than hidden behind a shorter table (CLAUDE.md: "a defect found in live data is REPORTED, never
    'fixed' by writing code that hides it").

    Live, this is the most actionable output on the page: the bill-payment envelope is counted almost
    never, so the end-to-end check is unmeasurable on nearly every day, and no amount of report code
    can change that — somebody has to count the envelope.
    """
    out = []
    for pair in PAIRS:
        key = pair["key"]
        reasons = {}
        unmeasured = 0
        for r in rows or []:
            c = (r.get("pairs") or {}).get(key) or {}
            if c.get("measured"):
                continue
            unmeasured += 1
            why = c.get("reason") or "leg_absent"
            reasons[why] = reasons.get(why, 0) + 1
        if not unmeasured:
            continue
        out.append({
            "pair": key, "pair_label": pair["label"],
            "days": unmeasured, "of_days": len(rows or []),
            "reasons": [{"reason": k, "label": UNMEASURED_REASONS.get(k, k), "days": v}
                        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
        })
    out.sort(key=lambda b: -b["days"])
    return out


# ── The three legs, built from the rows that record them. ───────────────────────────────────────
# THIS IS THE WHOLE FIX. Each builder's only real job is to state WHICH CASH its figure is, from the
# column the platform already stores for that purpose — never from an assumption about what the
# figure "obviously" means. Every false shortage in the docstring above came from assuming.

#: `cash_pickup.amount_basis` (mig 1039) says whether the bill-payment cash was netted OUT of the
#: pickup envelope's expectation — and therefore which cash the DM was told to collect and did count.
#: Mapped here, once, to this module's basis words. `billpay_netting.PICKUP_BASES` is the vocabulary;
#: a word it gains without this map gaining an entry resolves to None (unknown), which makes the pair
#: unmeasurable rather than wrong. `harness_cash_variance.py` §C fails the build if the two drift.
#: `envelope_count.basis` — the same column name the receipt module owns, named here rather than
#: spelled twice. Imported as a constant so a rename in mig-1036's owner module cannot leave this
#: reader silently reading a column that no longer exists.
COUNT_BASIS_COLUMN = "basis"


PICKUP_BASIS_TO_CASH = {
    "off": BASIS_TOTAL,       # netting not switched on → the envelope is the whole drawer
    "none": BASIS_TOTAL,      # no POS figure for that store-day → nothing was netted
    "pos": BASIS_STORE,       # the POS bill-pay cash was netted out → the envelope is store cash
    "declared": BASIS_STORE,  # the rep's declared bill-pay cash was netted out → likewise
}


def received_leg(count_row=None, pickup_row=None, billpay_row=None):
    """PURE: what MANAGEMENT received, as a `leg()`.

    THE LADDER, most authoritative first:
      1. `envelope_count` (mig 936) — management's own count, which records WHICH cash it counted
         (mig 1036 `basis`). Preferred because it is management's figure and this report is about
         management's recon.
      2. `cash_pickup.actual_picked_amount` (mig 949) — the count taken at pickup, on the basis
         `amount_basis` (mig 1039) names.

    A PRE-mig-1039 PICKUP ROW HAS NO BASIS, AND THAT IS REPORTED AS UNKNOWN, NOT GUESSED. Measured
    live: 181 of 275 counted pickups carry a NULL basis, and 126 of those 181 tie to the NET store
    cash within $5 — so the basis is *inferable*, and inferring it is still refused. The report says
    "these days predate the basis column" (a blocker a human can act on) instead of scoring 181
    employees' days against a basis nobody recorded. That choice is the entire difference between
    this report and the one that accused nine people of $20,000.

    THE BILL-PAY ENVELOPE IS NOT ADDED IN unless it was actually counted. Where it was, the two
    envelopes together ARE the whole drawer and the leg is promoted to `total_cash` — which is what
    makes the end-to-end check against the POS possible at all. Where it was not, the leg stays on
    its own basis; reconstructing the uncounted envelope as $0.00 is the absence-is-zero defect and
    would read as a shortage the size of the day's bill payments.
    """
    c, p, b = count_row or {}, pickup_row or {}, billpay_row or {}
    if c.get("counted_amount") is not None:
        # The stored count's own basis (mig 1036). A row written before that column resolves to None
        # here on purpose — `envelope_report.counted_basis` degrades to the historical default for
        # DISPLAY, which is right for a receipt and wrong for arithmetic.
        return leg(c.get("counted_amount"), c.get(COUNT_BASIS_COLUMN), "envelope_count",
                   detail="management's own count (Management Envelope Receipt)")
    if p.get("actual_picked_amount") is not None:
        basis = PICKUP_BASIS_TO_CASH.get(str(p.get("amount_basis") or "").strip().lower())
        amount = _f(p.get("actual_picked_amount"))
        detail = "counted at pickup"
        if basis == BASIS_STORE and b.get("actual_picked_amount") is not None:
            # Both envelopes counted: together they are the whole drawer.
            amount = round(amount + _f(b.get("actual_picked_amount")), 2)
            basis, detail = BASIS_TOTAL, "both envelopes counted at pickup, added together"
        return leg(amount, basis, "cash_pickup", detail=detail)
    return leg(None, None, None, detail="no management count recorded")


def declared_leg(closing_row=None):
    """PURE: the cash envelope AS THE REP DECLARED IT, on the TOTAL CASH basis — `daily_closing.t_cash`
    (fallback `store_cash`), which per the owner's 2026-09-02 directive is *"Total cash in store
    including Bill Payments"*. `epay_on_cash` is a breakdown INSIDE that figure, not a second
    envelope, so this leg is `total_cash` by definition and needs no column to tell it so.

    IT DOES NOT NET. Netting here would be the second derivation of `deposit_recon.cash_for_basis`,
    and — measured live — would also produce NEGATIVE cash: 81 store-days in the house org declare
    MORE bill-payment cash than total cash, which `billpay_netting` already documents as the reason
    the declared figure cannot be netted by. That defect is surfaced by the blockers, not hidden by
    arithmetic that turns it into a shortage.
    """
    r = closing_row or {}
    amount = r.get("t_cash")
    if amount is None:
        amount = r.get("store_cash")
    if amount is None:
        return leg(None, None, None, detail="no closing figure for this store-day")
    return leg(amount, BASIS_TOTAL, "daily_closing",
               detail="declared at closing — the whole drawer, bill payments included")


def pos_leg(attempt_row=None):
    """PURE: the cash the point of sale rang, on the TOTAL CASH basis — `closing_attempt.b2b_cash`
    (mig 103), the figure the close gate itself compared the rep against.

    WHY TOTAL AND NOT NET, PROVEN NOT ASSUMED: the gate compares `b2b_cash` against `entered_cash`,
    and `entered_cash` is `t_cash` — identical on all 166 live store-days measured. A figure the
    platform compares to the whole drawer is on the whole-drawer basis. Scored against NET store cash
    instead, 162 of those 166 days read SHORT; scored against the drawer, 36 tie and 130 are a real
    reviewable list.

    NOT RE-DERIVED FROM THE FEED. `router._b2b_day` would recompute this per date — a second
    derivation of a stored number, and the stored one is the number the rep was actually judged by.
    """
    a = attempt_row or {}
    if a.get("b2b_cash") is None:
        return leg(None, None, None, detail="no close-gate attempt recorded for this store-day")
    return leg(a.get("b2b_cash"), BASIS_TOTAL, "closing_attempt",
               detail="cash tender rung at the point of sale, as the close gate recorded it")


def basis_catalog(processor_term=None):
    """PURE: the basis vocabulary as the SCREEN should word it, so no page spells a basis key or a
    basis label. The bill-payment basis takes the tenant's own processor noun when one is declared
    (RULE TWO — 'ePay' on one carrier's side, 'VidaPay' on another's, the neutral noun otherwise)."""
    term = str(processor_term or "").strip()
    labels = {
        BASIS_TOTAL: "Total cash (bill payments included)",
        BASIS_STORE: "Store cash (bill payments taken out)",
        BASIS_BILLPAY: f"{term} cash" if term else "Bill payment cash",
    }
    return [{"key": k, "label": labels[k]} for k in BASES]
