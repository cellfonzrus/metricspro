"""VOIDS AND RETURNS, VISIBLE AND TRACKABLE — the transactions the platform only ever threw away.

OWNER ASK 2026-10-05, verbatim: *"Make the voids visible and track able"*.

THE DEFECT, MEASURED IN THE CODE FIRST
──────────────────────────────────────
`gp_report.is_voided` is dereferenced in about a dozen places, and **every single one of them does
the same thing: EXCLUDE the line so it does not pay.** That is correct for pay and correct for gross
profit. It is also the whole of the platform's relationship with a void:

    commission_engine · sale_installment_engine · flags · router (×6) · royalty · gp_report

There is no void count, no void rate, no void list, and no void report — by rep, by store, by day or
at all. A reversed transaction is a thing a person DID, with money attached, and the only trace it
leaves is an absence. A rep whose voids tripled this month looks identical to a rep who sold less.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix")
───────────────────────────────────────────────────────────────
The class is: **"excluded from pay" was being used as if it meant "dealt with".** It does not. So the
fix is not a void report for one tenant — it is a second reading of the SAME canonical rule. This
module dereferences `gp_report.countable_sale_skip_reason`, the existing one home for "is this a
countable sale line, and if not, why not", and keeps what it returns instead of discarding it:

    skip reason 'voided'        → a void
    skip reason 'return'        → a return
    skip reason 'unattributed'  → a line nobody is named on

One rule, two readings — the pay path asks "does this count?", the watchdog asks "what did we throw
away?" — so the two can never disagree about what a void is. A private void test here would be
exactly the second copy the index rules forbid, and it would drift the first time the carrier changed
a token.

WHAT IT DETECTS
───────────────
  VOID_RATE_HIGH      a person's voided share of lines above the allowed share
  RETURN_RATE_HIGH    the same for returns
  VOID_AFTER_SALE     the same device on both a countable line and a voided line
  VOID_UNATTRIBUTED   voided lines with nobody named on them — how a void escapes accountability

WHAT IT HONESTLY CANNOT DO, STATED SO NOBODY READS IT IN
───────────────────────────────────────────────────────
`raw_sales.trans_date` is a DATE with no clock, so **"voided four minutes after the sale" is not
detectable from this feed.** `VOID_AFTER_SALE` therefore means "sold and voided in the same period",
which is a weaker claim, and its description says so rather than implying a sequence the data cannot
prove. A time-of-day column would make the sharper rule possible; until the feed carries one, the
sharper rule is not pretended.

PURE: no I/O, no framework import. `harness_void_watchdog.py` proves it DB-free.

RULE TWO: no carrier, tenant, store or product name appears here. The allowed share and the minimum
line count are per-org config rows (`commcalc.watchdog_rule`, mig 1056) read through
`flag_registry.rule_params`; the house defaults are declared once, in the registry.

💰 MOVES NO MONEY. Visibility records only — nothing it writes is read by any payout, P&L or GP path.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §52.
"""
from __future__ import annotations

from collections import defaultdict

from app.modules.commcalc import flag_registry as _reg
from app.modules.commcalc.gp_report import countable_sale_skip_reason as _skip

#: The `source` this writer owns, so the retire step is scoped to it and a void finding whose
#: condition clears (the rate comes back down) is retired rather than accusing a rep for ever.
SOURCE = "void_watchdog"

FLAG_TYPES = ("VOID_RATE_HIGH", "RETURN_RATE_HIGH", "VOID_AFTER_SALE", "VOID_UNATTRIBUTED")

#: What a kept line is called. Dereferenced from the skip reasons above, never re-decided.
VOIDED = "voided"
RETURNED = "return"
UNATTRIBUTED = "unattributed"
COUNTABLE = "countable"


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _money(v) -> str:
    return f"${abs(_f(v)):,.2f}"


def _s(v) -> str:
    return str(v or "").strip()


def _imei(row) -> str:
    """The device a sales line carries, normalised the way the rest of commcalc normalises it —
    `serial_1`, trailing '.0' from a spreadsheet float removed, upper-cased."""
    return _s((row or {}).get("serial_1")).replace(".0", "").upper()


def classify(row) -> str:
    """PURE. What this sales line is: 'countable' | 'voided' | 'return' | 'unattributed'.

    Dereferences `gp_report.countable_sale_skip_reason` — the pay path's own rule — so the watchdog's
    idea of a void is the pay path's idea of a void, by construction. A line that is BOTH voided and
    unattributed is counted as voided: the reversal is the stronger fact, and the skip rule already
    orders them that way.
    """
    reason = _skip(row or {})
    if not reason:
        return COUNTABLE
    return reason


def summarise(sales_rows) -> dict:
    """PURE. The void/return picture for a set of sales lines — the numbers the report page shows.

    Returns totals, per-rep and per-store breakdowns. Every count is of LINES, and every amount is
    `ext_price`, so a reader is never shown a share computed on one basis and money on another.

    `lines` is every non-countable line, kept so the report can list them. The caller bounds the read;
    this module does not truncate, because a silently-shortened list of voids is the same class of
    lie as a fabricated zero.
    """
    totals = {COUNTABLE: 0, VOIDED: 0, RETURNED: 0, UNATTRIBUTED: 0}
    amounts = {COUNTABLE: 0.0, VOIDED: 0.0, RETURNED: 0.0, UNATTRIBUTED: 0.0}
    by_rep: dict[str, dict] = defaultdict(lambda: {k: 0 for k in totals} | {"amount_voided": 0.0,
                                                                            "amount_returned": 0.0})
    by_store: dict[str, dict] = defaultdict(lambda: {k: 0 for k in totals} | {"amount_voided": 0.0,
                                                                              "amount_returned": 0.0})
    lines: list[dict] = []

    for r in sales_rows or ():
        kind = classify(r)
        amt = _f((r or {}).get("ext_price"))
        totals[kind] += 1
        amounts[kind] += amt
        rep = _s((r or {}).get("salesperson")) or "(nobody named)"
        store = _s((r or {}).get("store")) or "(no store)"
        for bucket, key in ((by_rep, rep), (by_store, store)):
            bucket[key][kind] += 1
            if kind == VOIDED:
                bucket[key]["amount_voided"] += amt
            elif kind == RETURNED:
                bucket[key]["amount_returned"] += amt
        if kind != COUNTABLE:
            lines.append({
                "kind": kind, "store": store, "salesperson": rep,
                "trans_id": _s((r or {}).get("trans_id")).replace(".0", ""),
                "trans_date": _s((r or {}).get("trans_date")),
                "product_desc": _s((r or {}).get("product_desc")),
                "imei": _imei(r), "mdn": _s((r or {}).get("mdn")).replace(".0", ""),
                "amount": amt, "tender_type": _s((r or {}).get("tender_type")),
                "contract_type": _s((r or {}).get("contract_type")),
            })

    def _share(part, whole):
        """The share of lines, or None when there is no denominator. None is the honest answer — a
        rep with no lines has no void rate, and 0% would read as 'clean'."""
        return round(part / whole, 4) if whole else None

    total_lines = sum(totals.values())
    return {
        "total_lines": total_lines,
        "counts": dict(totals),
        "amounts": {k: round(v, 2) for k, v in amounts.items()},
        "void_share": _share(totals[VOIDED], total_lines),
        "return_share": _share(totals[RETURNED], total_lines),
        "by_rep": {k: dict(v, void_share=_share(v[VOIDED], sum(v[c] for c in totals)),
                           return_share=_share(v[RETURNED], sum(v[c] for c in totals)))
                   for k, v in sorted(by_rep.items())},
        "by_store": {k: dict(v, void_share=_share(v[VOIDED], sum(v[c] for c in totals)),
                             return_share=_share(v[RETURNED], sum(v[c] for c in totals)))
                     for k, v in sorted(by_store.items())},
        "lines": lines,
    }


def calc_void_flags(sales_rows, *, period, period_month=None, period_year=None,
                    rules=None) -> list[dict]:
    """PURE. Every void/return finding for one period, ready for `flag_persist.sync`.

    `sales_rows` are this org's `commcalc.raw_sales` rows for the period, read by the caller — this
    module does no I/O and never sees an org id. `rules` is the org's `commcalc.watchdog_rule` rows.
    """
    rules = list(rules or [])
    on = {t: _reg.is_enabled(t, rules) for t in FLAG_TYPES}
    params = {t: _reg.rule_params(t, rules) for t in FLAG_TYPES}
    s = summarise(sales_rows)

    base = {"period": period, "period_month": period_month, "period_year": period_year,
            "source": SOURCE}
    flags: list[dict] = []

    def _add(flag_type, **kw):
        row = dict(base)
        row["flag_type"] = flag_type
        row["severity"] = _reg.severity_for(flag_type)
        row.update(kw)
        return flags.append(row)

    # ── 1 & 2. A person's void / return share above the allowed share. ──────────────────────────
    # `min_lines` is the floor that stops "1 of 2 lines voided = 50%" being a finding. Without it the
    # board fills with reps who barely sold, which is the alert noise §19.17 removed.
    for kind, ftype, amount_key, word in ((VOIDED, "VOID_RATE_HIGH", "amount_voided", "voided"),
                                          (RETURNED, "RETURN_RATE_HIGH", "amount_returned",
                                           "returned")):
        if not on[ftype]:
            continue
        max_share = _f(params[ftype].get("max_share"))
        min_lines = int(_f(params[ftype].get("min_lines")) or 0)
        for rep, agg in s["by_rep"].items():
            rep_lines = sum(agg[c] for c in (COUNTABLE, VOIDED, RETURNED, UNATTRIBUTED))
            if rep_lines < min_lines or not rep_lines:
                continue
            share = agg[kind] / rep_lines
            if max_share and share < max_share:
                continue
            if not agg[kind]:
                continue
            _add(ftype,
                 epay_salesperson=rep,
                 source_ref=f"{rep}|{period}|{ftype}",
                 amount=round(_f(agg[amount_key]), 2),
                 description=(f"{rep} {word} {agg[kind]} of {rep_lines} lines "
                              f"({share * 100:.1f}%), {_money(agg[amount_key])} in value. The "
                              f"allowed share is {max_share * 100:.1f}%."),
                 coaching_note=(f"A high {word[:-1]} rate is either a selling problem or a way to "
                                f"undo a sale after it has counted. Ask for the reason on each one "
                                f"and check whether the same customer came back."))

    # ── 3. The same device both sold and voided. ────────────────────────────────────────────────
    # Period-grain, not sequence-grain: the feed has no clock (see the docstring).
    if on["VOID_AFTER_SALE"]:
        sold: dict[str, dict] = {}
        voided: dict[str, dict] = {}
        for r in sales_rows or ():
            k = _imei(r)
            if not k:
                continue
            kind = classify(r)
            if kind == COUNTABLE:
                sold.setdefault(k, r)
            elif kind == VOIDED:
                voided.setdefault(k, r)
        for k in sorted(set(sold) & set(voided)):
            sr, vr = sold[k], voided[k]
            rep = _s(vr.get("salesperson")) or _s(sr.get("salesperson"))
            _add("VOID_AFTER_SALE",
                 store_address=_s(sr.get("store")) or _s(vr.get("store")),
                 epay_salesperson=rep,
                 imei=k, mdn=_s(sr.get("mdn")).replace(".0", ""),
                 source_ref=f"{k}|{period}|VOID_AFTER_SALE",
                 transaction_date=_s(vr.get("trans_date")) or None,
                 amount=_f(vr.get("ext_price")) or _f(sr.get("ext_price")),
                 description=(f"Device {k} appears on a counted sale and on a voided line in the "
                              f"same period ({_s(sr.get('product_desc')) or 'no description'}). The "
                              f"feed carries no time of day, so the order of the two is not known "
                              f"from here."),
                 coaching_note=("One device sold and voided in the same month is worth seeing the "
                                "paperwork for. Pull both receipts before drawing a conclusion."))

    # ── 4. Voided lines nobody is named on. ─────────────────────────────────────────────────────
    # The one that matters most and is easiest to miss: a void with no rep cannot be coached, charged
    # back or even asked about. It is counted per store so somebody owns the question.
    if on["VOID_UNATTRIBUTED"]:
        min_lines = int(_f(params["VOID_UNATTRIBUTED"].get("min_lines")) or 1)
        per_store: dict[str, dict] = defaultdict(lambda: {"n": 0, "amount": 0.0})
        for r in sales_rows or ():
            if classify(r) != VOIDED:
                continue
            if _s((r or {}).get("salesperson")) and _s((r or {}).get("salesperson")).lower() != "admin":
                continue
            st = _s((r or {}).get("store")) or "(no store)"
            per_store[st]["n"] += 1
            per_store[st]["amount"] += _f((r or {}).get("ext_price"))
        for st, agg in sorted(per_store.items()):
            if agg["n"] < min_lines:
                continue
            _add("VOID_UNATTRIBUTED",
                 store_address=st,
                 source_ref=f"{st}|{period}|VOID_UNATTRIBUTED",
                 amount=round(agg["amount"], 2),
                 description=(f"{agg['n']} voided line(s) at {st} worth {_money(agg['amount'])} "
                              f"carry nobody's name, so there is no one to ask about them."),
                 coaching_note=("A void with no rep on it cannot be coached or charged back. Find "
                                "out who is ringing under a shared or admin login and stop it."))

    return flags


if __name__ == "__main__":  # pragma: no cover - smoke
    rows = [
        {"salesperson": "Ali", "store": "B-1", "ext_price": 100, "serial_1": "IM1",
         "trans_type": "Activation", "voided": ""},
        {"salesperson": "Ali", "store": "B-1", "ext_price": 100, "serial_1": "IM1",
         "trans_type": "Activation", "voided": "true"},
        {"salesperson": "", "store": "B-1", "ext_price": 50, "serial_1": "IM2", "voided": "yes"},
    ]
    s = summarise(rows)
    assert s["counts"]["voided"] == 2 and s["counts"]["countable"] == 1, s["counts"]
    out = calc_void_flags(rows, period="202610", period_month=10, period_year=2026)
    kinds = sorted(f["flag_type"] for f in out)
    assert kinds == ["VOID_AFTER_SALE", "VOID_UNATTRIBUTED"], kinds
    print("void_watchdog self-test OK —", len(s["lines"]), "non-countable lines,", len(out), "flags")
