"""A STORE-DAY SOMEBODY STARTED AND DID NOT FINISH — the one home for that fact (index §29.12).

THE DEFECT THIS FIXES (owner bug report 2026-10-04, 117 E Burnside Ave, 2026-10-01)
───────────────────────────────────────────────────────────────────────────────────
Owner, verbatim: *"If the stops the reform entering the 1st closing and tells them to correct it, the
rep tries again but stops at 2 or even after the 1st attempt, the system should say to correct the
entries like it does but also save the last entered data in thr system so the system is not blank at
any time like what happened with Abid"*

MEASURED LIVE FIRST (read-only, 2026-10-04). B-117 / 2026-10-01, employee "Rana":

    two `commcalc.closing_attempt` rows, two seconds apart, both `blocked`
    entered cash $2,826.00 vs POS $2,631.83  → $194.17 OVER   (cash_dir 'over')
    entered credit  $270.00 vs POS   $146.59 → $123.41 OVER   (credit_dir 'over')
    t_zelle $225.00 · no `commcalc.daily_closing` row for that store-day at all

The 3-try close gate blocks tries 1 and 2 and auto-accepts the 3rd. He stopped at two. So the day has
no closing, and on the Daily Closing dashboard and the DM-verify screen it renders **exactly like a
store-day nobody worked** — which is what "we cannot see it" meant.

THE CLASS, NOT THE INSTANCE. Burnside on Oct 1 is the instance. The class is: **"no closing row"
is being read as "nobody submitted", when it can equally mean "somebody submitted and the gate sent
them back".** Those are opposite operational facts — one needs a nag, the other needs a correction and
holds real declared cash — and the platform could not tell them apart anywhere.

WHAT WAS ALREADY TRUE, AND SO IS NOT REBUILT HERE (the duplicate-check build gate). The money was
never lost. `commcalc.closing_attempt` (mig 103) has carried every entered tender, the POS figure it
was compared against and the direction of the variance since it existed — all eight tender columns,
`entered_cash`/`entered_credit`, `b2b_cash`/`b2b_credit`, `cash_dir`/`credit_dir`. So this module
does NOT store the entry a second time, and in particular does NOT write a provisional
`commcalc.daily_closing` row:

  · a half-finished closing row would be a SECOND home for "what did the rep declare", beside the
    attempt trail that already holds it — the sibling derivation the index rules forbid;
  · and it would put numbers the gate has already judged wrong ($194 over, here) in front of all 51
    readers of `daily_closing`, every money report among them. `is_finished` below is the predicate
    that keeps that impossible, and the harness fails the build if a provisional row ever appears.

So the fix is to READ what is already stored and give the unfinished day a name. One fact, one home,
dereferenced: `state_for` is the only place that decides what a store-day's unfinished state is, and
the dashboard, the verify screen, the missing-closing alert, the stale-store provider and the submit
form's prefill all dereference it.

PURE: no I/O, no framework import. The caller does the read and hands the rows in.
"""
from __future__ import annotations

# ── The vocabulary. Four states, and nothing may invent a fifth. ─────────────────────────────────
#: No closing, and nobody tried. The honest "nobody submitted" — the only one the nag is for.
NOT_STARTED = "not_started"
#: Somebody submitted, the close gate sent them back to recount, and they have not returned.
#: THE STATE THAT DID NOT EXIST. It holds real entered money and a real variance.
AWAITING_CORRECTION = "awaiting_correction"
#: Somebody submitted and every try was turned away before the gate (index §29.11 refusals).
TURNED_AWAY = "turned_away"
#: A closing exists for this store-day. Nothing here applies.
FINISHED = "finished"

STATES = (NOT_STARTED, AWAITING_CORRECTION, TURNED_AWAY, FINISHED)

#: What a manager reads for each state. No database, table or hosting name may appear here
#: (index §19.38) — these strings reach the screen.
LABELS = {
    NOT_STARTED: "Not submitted",
    AWAITING_CORRECTION: "Entered, awaiting correction",
    TURNED_AWAY: "Submits turned away",
    FINISHED: "Submitted",
}

#: The states that mean "this store-day is not closed yet", so the nag and the stale-store provider
#: still fire. An unfinished day is NOT a closed day just because something was typed into it.
OPEN_STATES = (NOT_STARTED, AWAITING_CORRECTION, TURNED_AWAY)

#: The tender columns `closing_attempt` carries, in the order the submit form shows them. The one
#: declaration of "which columns on an attempt row are money the rep entered".
TENDER_COLUMNS = ("t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima")

#: What the rep typed that is NOT money, and that `_log_attempt` did not keep until mig 1052. A
#: resumed try must come back filled in, or the rep retypes it — which is why Abid stopped at two.
ENTRY_COLUMNS = ("acc_sale", "upgrade_count", "new_line_count", "postpaid_count", "remarks",
                 "envelope_picture")


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def is_finished(closing_row) -> bool:
    """THE predicate. True when a real `commcalc.daily_closing` row exists for this store-day.

    A money report counts a store-day's declared cash only when this is True. Because an unfinished
    day writes NO closing row, that is automatically so for all 51 readers of the table — and
    `harness_closing_unfinished_day.py` §H fails the build if a provisional row is ever introduced,
    which is the only way it could stop being true.
    """
    return bool(closing_row)


def is_real_try(attempt_row) -> bool:
    """Whether an attempt row is a real try at the gate, as opposed to a submit turned away before
    it (index §29.11). Dereferences the refusal registry's rule rather than restating it — the two
    answers must never diverge."""
    from . import submit_refusal as _refusal
    return _refusal.is_real_try(attempt_row)


def latest_try(attempt_rows) -> dict:
    """The most recent REAL try, which is the one whose numbers the rep would be resuming. Ordered by
    `attempt_no` then `created_at` so a row written before `attempt_no` was reliable still sorts
    sensibly."""
    real = [r for r in (attempt_rows or []) if is_real_try(r)]
    if not real:
        return {}
    real.sort(key=lambda r: (int(r.get("attempt_no") or 0), str(r.get("created_at") or "")))
    return real[-1]


def entered_money(attempt_row) -> dict:
    """The money on one attempt row: each tender, and the two figures the gate compared. Returns 0.0
    for a column the row does not carry rather than omitting it, so a caller never has to tell
    "zero dollars" from "never fetched" — the `.get()` trap that cost the envelope receipt (§47.8)."""
    r = attempt_row or {}
    out = {k: _f(r.get(k)) for k in TENDER_COLUMNS}
    out["declared_cash"] = _f(r.get("entered_cash"))
    out["declared_credit"] = _f(r.get("entered_credit"))
    return out


def variance(attempt_row) -> dict:
    """The gate's own comparison, as recorded on the try — never recomputed here. `cash` / `credit`
    are declared minus POS, so positive is OVER. `has_pos` is False when the feed had nothing for
    that store-day, in which case the gate never blocked on money and the variances are meaningless
    rather than zero."""
    r = attempt_row or {}
    has_pos = r.get("b2b_cash") is not None or r.get("b2b_credit") is not None
    if not has_pos:
        return {"has_pos": False, "cash": None, "credit": None,
                "cash_dir": r.get("cash_dir"), "credit_dir": r.get("credit_dir")}
    return {
        "has_pos": True,
        "cash": round(_f(r.get("entered_cash")) - _f(r.get("b2b_cash")), 2),
        "credit": round(_f(r.get("entered_credit")) - _f(r.get("b2b_credit")), 2),
        "cash_dir": r.get("cash_dir"),
        "credit_dir": r.get("credit_dir"),
    }


def resume_entry(attempt_row) -> dict:
    """What the submit form should come back filled in with, from the last real try. Money from
    `entered_money`, the rest from `ENTRY_COLUMNS`. A column the attempt row does not carry (mig 1052
    not run) is simply absent, so the form falls back to empty for that field instead of showing a
    fabricated zero."""
    r = attempt_row or {}
    if not r:
        return {}
    out = {k: v for k, v in entered_money(r).items() if k in TENDER_COLUMNS}
    for k in ENTRY_COLUMNS:
        if r.get(k) not in (None, ""):
            out[k] = r.get(k)
    # A tenant with configured tenders (mig 111) keeps the non-standard ones in `tenders`, the same
    # jsonb shape the submit endpoint accepts, so a custom tender resumes like a built-in one rather
    # than silently coming back empty for exactly the tenants who configured their own.
    if isinstance(r.get("tenders"), dict) and r.get("tenders"):
        out["tenders"] = r.get("tenders")
    return out


def state_for(closing_row, attempt_rows) -> str:
    """THE ONE RULE. Which of `STATES` a store-day is in.

    `closing_row` is that store-day's `daily_closing` row (or None/{}), `attempt_rows` its
    `closing_attempt` rows. Order matters: a finished day is finished whatever its history, and a day
    whose only submits were turned away is not the same as one nobody touched.
    """
    if is_finished(closing_row):
        return FINISHED
    rows = list(attempt_rows or [])
    if not rows:
        return NOT_STARTED
    if any(is_real_try(r) for r in rows):
        return AWAITING_CORRECTION
    return TURNED_AWAY


def describe(closing_row, attempt_rows) -> dict:
    """The whole answer for one store-day, for every caller that renders or alerts on it.

    Always carries `state`, `label`, `is_open` and `tries`; an `AWAITING_CORRECTION` day also carries
    the money and the variance, because that is the state whose numbers somebody must act on.
    """
    state = state_for(closing_row, attempt_rows)
    rows = list(attempt_rows or [])
    real = [r for r in rows if is_real_try(r)]
    out = {
        "state": state,
        "label": LABELS[state],
        "is_open": state in OPEN_STATES,
        "tries": len(real),
        "turned_away": len(rows) - len(real),
    }
    if state != AWAITING_CORRECTION:
        return out
    last = latest_try(rows)
    out["entered"] = entered_money(last)
    out["variance"] = variance(last)
    out["last_try_at"] = last.get("created_at")
    out["employee_name"] = last.get("employee_name")
    return out


def summarize(days) -> dict:
    """Counts per state over many store-days, for the dashboard's tiles. `days` are `describe`
    results. Every state is a key even at zero, so a tile never renders blank for want of a key."""
    out = {s: 0 for s in STATES}
    for d in days or []:
        s = (d or {}).get("state")
        if s in out:
            out[s] += 1
    return out
