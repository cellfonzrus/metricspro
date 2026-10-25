"""A TURNED-AWAY SUBMIT IS NOT A RECOUNT — the declaration for /closing/management (§CORE-17).

Management Review is the 3-try close-attempt log: every value a rep entered before a close was
accepted, grouped by (date, store, rep), with the true POS variance the rep never saw. It already
shows the tries when a manager expands a row, which is why the audit marked this report as owing
PROVENANCE rather than a figure's panel: the badge says "3 attempts", the expanded list shows three
rows, and nothing on the screen says where either came from or which of them is the closing that
actually stands.

**ONE PANEL PER GROUP, AND THE FIGURE IS THE ATTEMPT COUNT.** The grain is the group — one rep's day
at one store — exactly as the screen's own `key` is, so the panel holds precisely the tries that
badge counted. The same shape `envelope_drilldown.by_employee_drilldowns` and
`cash_variance.group_drilldown` already serve, not a new mechanism.

**A REFUSED SUBMIT IS SHOWN AND NEVER COUNTED, AND THAT IS THE WHOLE POINT OF THE FIGURE.**
`submit_refusal.is_real_try` is the one rule for what counts as a try (index §29.11): a refused
submit stored no closing at all, so counting it would read as a rep who recounted three times when
their photo failed three times. Those rows stay in the panel — they are the evidence that a person
came in and could not file — with `submit_refused` as their stated reason and the refusal's own
submitter-facing sentence, dereferenced from `submit_refusal.message_for`, as the words. This file
spells no refusal code's meaning of its own.

**THE ACCEPTED TRY NAMES THE CLOSING ROW THAT STANDS; NO OTHER TRY DOES.** A try is a record of what
somebody typed, and it lives in `closing_attempt`. Only the try the gate accepted produced a
`daily_closing` row, and only that row carries the release and correction state a manager acts on. So
`daily_closing` is named on that one try and left off the rest, rather than claimed on every row as a
provenance nothing checked.
"""
from app.modules.core import report_drilldown

from . import submit_refusal as _refusal

#: One attempt group's address — the screen's own row key, field for field.
GROUP_KEYS = ("close_date", "store_code", "employee_name")

#: One try's address inside the group.
ROW_KEYS = ("close_date", "store_code", "employee_name", "attempt_no")

#: Where a try is recorded, and where the closing that survived it ended up.
ATTEMPT = "closing_attempt"
CLOSING = "daily_closing"


def _accepted(t):
    """The try that produced the closing row that stands. `auto_accepted` is a kind of acceptance,
    so it counts — the screen flags it for review precisely because it stands without a match."""
    t = t or {}
    return bool(t.get("accepted") or t.get("auto_accepted"))


def _refused(t):
    """A submit the gate turned away. Read through `submit_refusal.is_real_try`, the one home for
    what counts as a try, so this cannot drift from the count the badge shows."""
    return not _refusal.is_real_try(t)


def _tables(group_row_id):
    """A try's provenance, PER TRY: the attempt row it is, plus the closing row it produced where it
    is the one that was accepted AND a closing row was actually matched to this group. A group whose
    closing row could not be matched (a pre-migration database, or a re-filed day) names no closing
    row rather than pointing at one nobody found."""
    def read(t):
        if _accepted(t) and group_row_id:
            return (ATTEMPT, CLOSING)
        return (ATTEMPT,)
    return read


def _label(t):
    t = t or {}
    n = t.get("attempt_no")
    return "try %s · %s" % (n if n is not None else "?",
                            str(t.get("created_at") or "?")[:16].replace("T", " "))


def _words(t):
    """What a refused try means, in the submitter's own words — dereferenced from the refusal
    registry, never restated here. An unknown or absent code says plainly that the reason was not
    recorded, which a pre-migration row genuinely does not carry."""
    code = str((t or {}).get("refusal_code") or "").strip()
    if not code:
        return None
    try:
        return _refusal.message_for(code)
    except KeyError:
        return None


def _fields(t):
    """What identifies and explains one try. Every value is read off the attempt row the endpoint
    already built, so the panel cannot disagree with the tries list beside it."""
    t = t or {}
    return {"attempt_no": t.get("attempt_no"),
            "entered_cash": t.get("entered_cash"),
            "entered_credit": t.get("entered_credit"),
            "blocked": t.get("blocked"),
            "accepted": _accepted(t),
            "auto_accepted": bool(t.get("auto_accepted")),
            "turned_away": _refused(t),
            "why": _words(t) or (t.get("refusal_detail") or None),
            "at": str(t.get("created_at") or "")[:19].replace("T", " ")}


def _one(t):
    """A try's contribution to the attempt count — and None for a turned-away submit, which stored
    nothing and is therefore not a try at all."""
    return None if _refused(t) else 1


def _reason(t):
    return "submit_refused" if _refused(t) else None


_NOTE = ("The badge counts the tries that actually stored something. A submit the system turned away "
         "stored no closing, so it is shown here and not counted — it is a person who came in and "
         "could not file, which is a different thing to chase than a rep who recounted.")


def group_keys(group):
    g = group or {}
    return {"close_date": g.get("close_date"), "store_code": g.get("store_code"),
            "employee_name": g.get("employee_name")}


def group_panel(group, *, cap=None):
    """One panel per group row: the tries behind that group's attempt count, each naming the tables
    it was read out of."""
    g = group or {}
    read_tables = _tables(g.get("row_id"))
    rows = []
    for t in (g.get("tries") or []):
        keys = dict(group_keys(g), attempt_no=(t or {}).get("attempt_no"))
        rows.append(report_drilldown.drill_row(
            _label(t),
            report_drilldown.origins_of([(tb, keys) for tb in read_tables(t)], schema="commcalc"),
            row_key=keys, amount=_one(t), in_figure=not _refused(t), reason=_reason(t),
            fields=_fields(t)))
    return report_drilldown.panel(
        "Submits this person made for this store-day", g.get("attempts"), rows,
        subject=" · ".join(str(x) for x in (g.get("store_address") or g.get("store_code") or "?",
                                            str(g.get("close_date") or "?")[:10],
                                            g.get("employee_name") or "(no name)")),
        subject_kind="store_day_rep", unit=report_drilldown.UNIT_COUNT, note=_NOTE,
        cap=cap or report_drilldown.ROW_CAP)


def group_drilldowns(groups, *, cap=None):
    """One panel per group, keyed EXACTLY as the screen keys its rows (`close_date|store_code|
    employee_name`), so a manager's click and the server's fold address the same group."""
    out = {}
    for g in (groups or []):
        k = "|".join(str((g or {}).get(f) or "") for f in GROUP_KEYS)
        out[k] = group_panel(g, cap=cap)
    return out
