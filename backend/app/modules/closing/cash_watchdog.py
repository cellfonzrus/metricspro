"""THE CASH WATCHDOG — a drawer variance stops being a number in a table and becomes somebody's job.

OWNER ASK 2026-10-05: *"Start the registry + cash watchdog"*, after the 2026-10-05 proposal
identified cash as the area where loss is immediate and unrecoverable.

WHAT WAS ALREADY TRUE, SO IS NOT REBUILT HERE (the duplicate-check build gate, CLAUDE.md)
─────────────────────────────────────────────────────────────────────────────────────────
Everything this module needs has been stored since migration 103 and nothing watched it:

  · `commcalc.closing_attempt` carries, for EVERY try, what the rep entered (`entered_cash` /
    `entered_credit`), the point-of-sale figure it was checked against (`b2b_cash` / `b2b_credit`),
    the direction of each variance, and `auto_accepted` — accepted on the final try WHILE STILL
    MISMATCHED.
  · `closing/unfinished_day.py` is the ONE home for which store-day this is (`store_key`), what
    state it is in (`state_for`), and what the gate's own comparison was (`variance`).

So this module **computes no variance of its own**. It dereferences `unfinished_day.variance`, which
returns the gate's recorded comparison rather than recomputing it — two paths answering "how far out
was this drawer" is the sibling derivation the index rules forbid, and the gate's answer is the one
the rep was judged against. Likewise the store-day key comes from `unfinished_day.store_key` (which
dereferences the §13 store resolver), so a watchdog can never re-open the case-folding trap that made
`b-1115` a phantom store.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix")
───────────────────────────────────────────────────────────────
The instance is B-117 / 2026-10-01: $194.17 cash over and $123.41 credit over, entered, blocked
twice, never corrected, and still waiting on a human on 2026-10-05 — four days later, because nothing
raises its hand.

The class is: **a variance the gate RECORDED is not the same as a variance anybody OWNS.** The gate's
job ends when it accepts or blocks a submit. Nothing then says "this day cost money and no manager
has ruled on it". That is true of every store, every tenant and all four ways a day can carry a
variance, so all four are detected here:

  accepted and out of tolerance          CASH_OVER / CASH_SHORT / CREDIT_VARIANCE
  accepted on the last try, still wrong  CASH_AUTO_ACCEPTED   (size-independent — being waved
                                         through IS the finding)
  entered, sent back, never returned     CASH_AWAITING_CORRECTION  (the Burnside case)
  the same person on repeated bad days   CASH_REPEAT_VARIANCE (the pattern, not the day)

A SHORT IS WORSE THAN AN OVER, and the registry grades it so: `CASH_SHORT` is CRITICAL, `CASH_OVER`
is HIGH. An over is usually a miscount; a short is money that is not there.

PURE: no I/O, no framework import, no clock of its own. The caller reads the rows, hands them in, and
takes the flag dicts to `flag_persist.sync`. `harness_cash_watchdog.py` proves it DB-free.

RULE TWO: no carrier, tenant or store name appears here. Tolerances are per-org config rows
(`commcalc.watchdog_rule`, mig 1056) read through `flag_registry.rule_params`; the house defaults are
declared once, in the registry.

💰 MOVES NO MONEY. This module writes visibility records only. It books nothing to the P&L, GP,
payroll or any payout, and no payout path reads what it writes.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §52.
"""
from __future__ import annotations

from collections import defaultdict

from app.modules.commcalc import flag_registry as _reg
from . import unfinished_day as _day

#: The `source` this writer owns, so `flag_persist.sync`'s retire step can never touch another
#: module's findings — and so a cash finding whose condition CLEARS (the day gets corrected) is
#: retired rather than accusing a manager for ever.
SOURCE = "cash_watchdog"

#: Every type this module can emit. Static on purpose: the retire step must still cover a type that
#: produced ZERO findings this run — precisely the case where every one of them has cleared.
FLAG_TYPES = ("CASH_OVER", "CASH_SHORT", "CREDIT_VARIANCE", "CASH_AUTO_ACCEPTED",
              "CASH_AWAITING_CORRECTION", "CASH_REPEAT_VARIANCE")


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _money(v) -> str:
    """'$194.17'. Display only; never used as a key."""
    return f"${abs(_f(v)):,.2f}"


#: The period LABEL spelling. '%B %Y' ("October 2026"), which is what every existing writer of
#: `commcalc.flags` stores and therefore what `commcalc/router._pvariants` expands when a reader asks
#: for a period. A canonical '202610' here would be a THIRD spelling in the same column: `_pvariants`
#: cannot parse it, so it would fall through unchanged and the board would never find these rows.
#: The period never participates in the finding's identity — `flag_persist.period_canon` derives that
#: from `period_month` / `period_year` — so the label is display and query only.
_MONTHS = ("", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


def _period_of(close_date) -> tuple[str, int | None, int | None]:
    """('October 2026', 10, 2026) from a 'YYYY-MM-DD' close date.

    An unparseable date returns ('', None, None) and the caller SKIPS the day rather than filing it
    under a guessed period — §19.26's "missing beats wrong" applied to a write, the same posture the
    stale-batch reaper takes on an unreadable timestamp.
    """
    s = str(close_date or "").strip()
    if len(s) < 7 or s[4] != "-":
        return "", None, None
    try:
        y, m = int(s[0:4]), int(s[5:7])
    except ValueError:
        return "", None, None
    if not (1 <= m <= 12) or not (2000 <= y <= 2100):
        return "", None, None
    return f"{_MONTHS[m]} {y:04d}", m, y


def group_store_days(attempt_rows, closing_rows, resolve=None) -> dict:
    """PURE. Both tables keyed onto the SAME store-day, so a variance is matched to its closing.

    Returns {(store_key, close_date): {'attempts': [...], 'closing': row|None, 'store_code': raw}}.

    The join goes through `unfinished_day.store_key`, which is the measured reason a raw-string join
    is wrong here: on 2026-10-04 seven store-days had the attempt trail spelled `1800GreatNeckRd` and
    the accepted closing spelled `B-1800`, so a string join reported seven banked days as unfinished.
    """
    out: dict[tuple[str, str], dict] = {}

    def _slot(code, date):
        k = (_day.store_key(resolve, code), str(date or "").strip())
        if k not in out:
            out[k] = {"attempts": [], "closing": None, "store_code": str(code or "").strip()}
        return out[k]

    for r in attempt_rows or ():
        _slot((r or {}).get("store_code"), (r or {}).get("close_date"))["attempts"].append(r)
    for r in closing_rows or ():
        s = _slot((r or {}).get("store_code"), (r or {}).get("close_date"))
        s["closing"] = r
        # A closing row carries the canonical address; prefer it for display.
        if (r or {}).get("store_address"):
            s["store_address"] = r.get("store_address")
    return out


def _base(store_key, store_code, close_date, store_address, employee):
    period, pm, py = _period_of(close_date)
    return {
        "period": period, "period_month": pm, "period_year": py,
        "source": SOURCE,
        "store_code": store_key or store_code,
        "store_address": store_address or "",
        "epay_salesperson": str(employee or "").strip(),
        # The store-day IS the identity of a cash finding — it carries no device or subscriber — so
        # `source_ref` is what `flag_persist.ident_of` keys on. Stable across runs, which is what
        # makes a manager's ruling survive the nightly recalculation.
        "source_ref": f"{store_key or store_code}|{close_date}",
        "transaction_date": str(close_date or "").strip() or None,
    }


def _flag(base, flag_type, *, amount, description, coaching_note, severity=None, ref_suffix=""):
    row = dict(base)
    row["flag_type"] = flag_type
    row["severity"] = _reg.severity_for(flag_type, severity)
    row["amount"] = abs(_f(amount)) if amount is not None else None
    row["description"] = description
    row["coaching_note"] = coaching_note
    if ref_suffix:
        row["source_ref"] = f"{base['source_ref']}|{ref_suffix}"
    return row


def calc_cash_flags(attempt_rows, closing_rows, *, resolve=None, rules=None) -> list[dict]:
    """PURE. Every cash finding the handed-in store-days carry, ready for `flag_persist.sync`.

    `attempt_rows`  `commcalc.closing_attempt` rows for the window, already org-scoped by the caller.
    `closing_rows`  `commcalc.daily_closing` rows for the same window.
    `resolve`       the §13 store resolver callable, or None (then raw codes group with themselves).
    `rules`         this org's `commcalc.watchdog_rule` rows. This module never sees an org id — it
                    does no I/O — so it cannot mix one tenant's thresholds into another's run.

    A watchdog the tenant switched off emits nothing of that type, and a day whose close date cannot
    be read is skipped rather than filed under a guessed period.
    """
    rules = list(rules or [])
    on = {t: _reg.is_enabled(t, rules) for t in FLAG_TYPES}
    params = {t: _reg.rule_params(t, rules) for t in FLAG_TYPES}

    flags: list[dict] = []
    # (employee, period label, month, year) → the days that person was on when the drawer was out.
    # The month/year ride along rather than being re-parsed out of the label, so the pattern finding
    # carries the same period columns as the per-day findings it is built from.
    repeat: dict[tuple[str, str, int, int], list[tuple[str, str, float]]] = defaultdict(list)

    for (skey, cdate), slot in group_store_days(attempt_rows, closing_rows, resolve).items():
        period, _pm, _py = _period_of(cdate)
        if not skey or not period:
            continue

        attempts, closing = slot["attempts"], slot["closing"]
        state = _day.state_for(closing, attempts)
        last = _day.latest_try(attempts)
        if not last:
            # No real try: either nobody submitted, or every submit was turned away before the gate.
            # Both are the missing-closing nag's job (`ops_chargebacks`), not a variance finding —
            # raising one here would be a second home for "this day was never closed".
            continue

        var = _day.variance(last)
        employee = last.get("employee_name")
        base = _base(skey, slot.get("store_code"), cdate, slot.get("store_address"), employee)
        who = str(employee or "").strip() or "the person who submitted"

        # ── 1. Entered, sent back to recount, never returned. ───────────────────────────────────
        # Real declared money sitting in no closing row at all. Independent of tolerance: the
        # finding is that the day is unresolved, not that the figure is large.
        if state == _day.AWAITING_CORRECTION and on["CASH_AWAITING_CORRECTION"]:
            entered = _day.entered_money(last)
            bits = []
            if var.get("has_pos") and var.get("cash") is not None and _f(var["cash"]):
                bits.append(f"cash {_money(var['cash'])} "
                            f"{'over' if _f(var['cash']) > 0 else 'short'}")
            if var.get("has_pos") and var.get("credit") is not None and _f(var["credit"]):
                bits.append(f"card {_money(var['credit'])} "
                            f"{'over' if _f(var['credit']) > 0 else 'short'}")
            detail = ", ".join(bits) or "no point-of-sale figure to compare against"
            flags.append(_flag(
                base, "CASH_AWAITING_CORRECTION",
                amount=entered.get("declared_cash"),
                description=(f"{who} entered {_money(entered.get('declared_cash'))} in cash, was "
                             f"sent back to recount and has not returned ({detail}). The day is not "
                             f"closed."),
                coaching_note=("The money was declared and the count was refused, so nobody has "
                               "agreed what is in the drawer. Have the day recounted and resubmitted, "
                               "or rule on it yourself."),
            ))

        # The remaining rules are about a day that WAS accepted. An unfinished day has no agreed
        # figure to hold anyone to.
        if state != _day.FINISHED:
            continue

        # ── 2. Accepted on the final try while still mismatched. ────────────────────────────────
        if last.get("auto_accepted") and on["CASH_AUTO_ACCEPTED"]:
            cash_v = var.get("cash") if var.get("has_pos") else None
            cred_v = var.get("credit") if var.get("has_pos") else None
            bits = []
            if cash_v is not None and _f(cash_v):
                bits.append(f"cash {_money(cash_v)} {'over' if _f(cash_v) > 0 else 'short'}")
            if cred_v is not None and _f(cred_v):
                bits.append(f"card {_money(cred_v)} {'over' if _f(cred_v) > 0 else 'short'}")
            detail = ", ".join(bits) or "the variance was not recorded"
            flags.append(_flag(
                base, "CASH_AUTO_ACCEPTED",
                amount=cash_v if cash_v is not None else cred_v,
                description=(f"{who} ran out of tries and the day was accepted while still out "
                             f"({detail})."),
                coaching_note=("Nobody agreed this count — it was let through because it was the "
                               "last attempt. Confirm the drawer before this day is banked."),
            ))

        if not var.get("has_pos"):
            # The feed carried nothing for this store-day, so the gate never judged the money. A
            # variance of "zero" here would be a fabricated agreement, which is worse than silence.
            continue

        # ── 3. Drawer out of tolerance. ─────────────────────────────────────────────────────────
        cash_v = _f(var.get("cash"))
        if cash_v > 0 and on["CASH_OVER"] and abs(cash_v) >= _f(params["CASH_OVER"].get("tolerance")):
            flags.append(_flag(
                base, "CASH_OVER", amount=cash_v,
                description=f"Drawer {_money(cash_v)} over the point-of-sale figure, with {who} closing.",
                coaching_note=("Extra cash is still unexplained cash. Check for a bill payment or a "
                               "sale rung on the wrong tender before accepting the day."),
            ))
        if cash_v < 0 and on["CASH_SHORT"] and abs(cash_v) >= _f(params["CASH_SHORT"].get("tolerance")):
            flags.append(_flag(
                base, "CASH_SHORT", amount=cash_v,
                description=f"Drawer {_money(cash_v)} short of the point-of-sale figure, with {who} closing.",
                coaching_note=("Money the point of sale says was taken is not in the drawer. Count "
                               "it again with a second person and record what was found."),
            ))

        # ── 4. Card total out of tolerance. ─────────────────────────────────────────────────────
        cred_v = _f(var.get("credit"))
        if (on["CREDIT_VARIANCE"]
                and abs(cred_v) >= _f(params["CREDIT_VARIANCE"].get("tolerance"))
                and cred_v):
            flags.append(_flag(
                base, "CREDIT_VARIANCE", amount=cred_v,
                description=(f"Card total {_money(cred_v)} "
                             f"{'over' if cred_v > 0 else 'short'} of the point-of-sale figure, "
                             f"with {who} closing."),
                coaching_note=("A card total that does not match usually means a sale rung on the "
                               "wrong tender. Find the transaction before the day is banked."),
            ))

        # Collect for the pattern rule — any out-of-tolerance day this person was on.
        if employee and (abs(cash_v) >= _f(params["CASH_SHORT"].get("tolerance"))
                         or abs(cred_v) >= _f(params["CREDIT_VARIANCE"].get("tolerance"))):
            pm, py = _period_of(cdate)[1], _period_of(cdate)[2]
            repeat[(str(employee).strip(), period, pm, py)].append((skey, cdate, cash_v))

    # ── 5. The pattern: the same person on repeated variance days. ──────────────────────────────
    # This is a rep_period finding and the registry says so, which is the honest answer to "which
    # day was this": it is about several days and names them in the description rather than
    # pretending to be one transaction.
    if on["CASH_REPEAT_VARIANCE"]:
        min_days = int(_f(params["CASH_REPEAT_VARIANCE"].get("min_days")) or 0)
        for (employee, period, pm, py), days in sorted(repeat.items()):
            if min_days and len(days) < min_days:
                continue
            total = sum(abs(_f(d[2])) for d in days)
            stores = sorted({d[0] for d in days})
            flags.append({
                "period": period, "period_month": pm, "period_year": py,
                "source": SOURCE, "flag_type": "CASH_REPEAT_VARIANCE",
                "severity": _reg.severity_for("CASH_REPEAT_VARIANCE"),
                "store_code": stores[0] if len(stores) == 1 else "",
                "store_address": "",
                "epay_salesperson": employee,
                "source_ref": f"{employee}|{period}|repeat",
                "amount": round(total, 2),
                "description": (f"{employee} was closing on {len(days)} days this period where the "
                                f"drawer or card total was out, {_money(total)} in total across "
                                f"{len(stores)} store(s): "
                                + ", ".join(f"{d[0]} {d[1]}" for d in sorted(days)[:6])
                                + ("…" if len(days) > 6 else "")),
                "coaching_note": ("One bad count is a mistake; a run of them is a habit or a "
                                  "procedure problem. Watch a close in person before treating it as "
                                  "either."),
            })

    return flags


if __name__ == "__main__":  # pragma: no cover - smoke
    # The Burnside case, from the measured 2026-10-04 read: two blocked tries, no closing row.
    att = [{"store_code": "B-117", "close_date": "2026-10-01", "employee_name": "Rana",
            "attempt_no": 1, "entered_cash": 2826.00, "entered_credit": 270.00,
            "b2b_cash": 2631.83, "b2b_credit": 146.59, "cash_dir": "over", "credit_dir": "over",
            "blocked": True},
           {"store_code": "B-117", "close_date": "2026-10-01", "employee_name": "Rana",
            "attempt_no": 2, "entered_cash": 2826.00, "entered_credit": 270.00,
            "b2b_cash": 2631.83, "b2b_credit": 146.59, "cash_dir": "over", "credit_dir": "over",
            "blocked": True}]
    out = calc_cash_flags(att, [])
    assert [f["flag_type"] for f in out] == ["CASH_AWAITING_CORRECTION"], out
    print("cash_watchdog self-test OK —", out[0]["description"])
