"""WHY a daily closing was REFUSED, and the ONE record of it (index §29.11).

THE DEFECT THIS FIXES (owner bug report 2026-10-02, 117 E Burnside Ave)
──────────────────────────────────────────────────────────────────────
Owner, verbatim: *"abid did the daily closing for the 117 bunrsoide ave … we cannot see the daily
closing which was submitted, also he did it again today it is still not showing"*.

Measured in live data, read-only, before writing a line: B-117 has `daily_closing` rows on 09-28,
09-29 and 09-30 and NONE on 10-01/10-02; `closing_attempt` holds nothing from that submitter at any
store since 09-28; `daily_closing_verification` has no B-117 row either. The closing was not hidden —
it was REFUSED and nothing was kept.

THE CLASS, not the instance. `create_row` runs every validation BEFORE the first write, and
`closing_attempt` — the submit audit trail that exists so management can see what a rep tried — is
written only once they all pass. So EVERY refusal path is invisible:

    the closer gate (403) · the envelope-photo upload (502) · the duplicate guard (409) ·
    an expense line (400) · the photo-required-when-cash gate (400) · the dedup race (409) ·
    an unparseable close_date (400) · a closing with no store or no employee name (400)

A rep says "I submitted it", management sees nothing, and there is no evidence either way — for any
store, any rep, any tenant. "Burnside's closing is missing" is the instance; "a refused closing
leaves no trace" is the class.

ONE FACT, ONE HOME. Every refusal is declared HERE, once: its code, the HTTP status, and the words
the submitter reads. `closing/router._refuse` is the single raise site and it dereferences this
registry; it writes the refusal to the SAME `commcalc.closing_attempt` table the accepted and
blocked tries already use (reusing the existing audit mechanism — never a sibling table), and
`GET /closing/attempts` already reads it, so the Management Review screen gains the refusals with no
second query path. `backend/harness_closing_submit_refusal.py` FAILS THE BUILD if a refusal path in
`create_row` raises `HTTPException` directly instead of going through `_refuse`, or if a code is
raised that this registry does not declare.

PURE: no I/O, no framework import. The router turns a `Refusal` into the HTTP error.
"""

# A refused submit is NOT a counting try. The 3-try close gate derives the rep's attempt number from
# the rows already in closing_attempt, so a refusal row MUST be excluded from that count or a rep
# whose photo failed twice would reach the auto-accept third try without ever having recounted —
# a money-affecting regression. `attempt_no` on a refusal row therefore records how many REAL tries
# preceded it, and `refused = true` is what every counter filters on.
REFUSED_COLUMNS = ("refused", "refusal_code", "refusal_detail")

#: code -> (http_status, the sentence the submitter reads)
#: The message is the submitter-facing copy; `detail` carries anything store-specific. No database,
#: table or hosting name may appear in either (index §19.38).
REFUSALS = {
    "bad_close_date": (
        400, "That closing date could not be read. Pick the date again and resubmit."),
    "closer_not_permitted": (
        403, "You can only submit a closing under your own name. A district manager or above can "
             "submit one for somebody else."),
    "closer_no_name": (
        403, "Your login has no name on file, so this closing cannot be submitted under your name. "
             "Ask an admin to set your name (Admin → Roles & Access)."),
    "envelope_upload_failed": (
        502, "The envelope photo couldn't be saved — please try submitting again. If it keeps "
             "failing, tell your manager."),
    "envelope_photo_required": (
        400, "An envelope photo is required because cash was declared for this closing. Attach a "
             "photo of the envelope and resubmit."),
    "duplicate_already_submitted": (
        409, "Already submitted for this day — ask a manager to release it before resubmitting."),
    "duplicate_multiple": (
        409, "More than one closing already exists for you at this store on this day. Ask a manager "
             "to review them and release the correct one before resubmitting."),
    "duplicate_race": (
        409, "Already submitted for this day — ask a manager to release it before resubmitting."),
    "expense_description_required": (
        400, "A description is required for the expense."),
    "identity_missing": (
        400, "This closing needs a store and an employee name before it can be saved. Pick your "
             "store, check the name, and resubmit."),
    "expense_line_invalid": (
        400, "An expense line is incomplete. Give every line a category, an amount and a "
             "description, then resubmit."),
}


class Refusal(Exception):
    """A refused submit. `code` must be a key of REFUSALS; `detail` is the specific circumstance
    (which date, which store) and is recorded for management, never a stand-in for the message."""

    def __init__(self, code: str, detail: str = "", message_suffix: str = ""):
        if code not in REFUSALS:
            raise KeyError(f"unknown closing refusal code {code!r} — declare it in "
                           f"closing/submit_refusal.REFUSALS")
        self.code = code
        self.status, base = REFUSALS[code]
        self.detail = " ".join(str(detail or "").split())
        self.message = (base + (" " + message_suffix.strip() if message_suffix else "")).strip()
        super().__init__(self.message)


def status_for(code: str) -> int:
    """The HTTP status a refusal code answers with."""
    return REFUSALS[code][0]


def message_for(code: str) -> str:
    """The submitter-facing sentence for a refusal code."""
    return REFUSALS[code][1]


def audit_row(org_id, close_date, body, code: str, detail: str = "",
              real_attempts: int = 0, tenders=None) -> dict:
    """The `commcalc.closing_attempt` row that records ONE refusal — the same table the accepted and
    blocked tries use, so Management Review needs no second source.

    `real_attempts` is how many NON-refused tries this (date, store, rep) already has: the refusal
    did not add a try, so it is recorded as-is rather than incremented. `blocked`/`accepted`/
    `auto_accepted` are all false — a refusal is none of those three, and every existing reader
    already treats all-false as "not a try that counted".

    Amounts are recorded when the submit got far enough to have them (a refusal before the tender
    block has none), because a management question about a refused closing is almost always "what
    did they say they had".
    """
    if code not in REFUSALS:
        raise KeyError(f"unknown closing refusal code {code!r} — declare it in "
                       f"closing/submit_refusal.REFUSALS")
    t = dict(tenders or {})
    row = {
        "org_id": org_id,
        "close_date": close_date,
        "period": (str(close_date)[:7] if close_date else None),
        "store_code": body.get("store_code"),
        "store_address": body.get("store_address"),
        "sfid": body.get("sfid"),
        "employee_name": body.get("employee_name"),
        "attempt_no": max(0, int(real_attempts or 0)),
        "blocked": False, "accepted": False, "auto_accepted": False,
        "refused": True, "refusal_code": code,
        "refusal_detail": " ".join(str(detail or "").split()) or None,
    }
    for k in ("cash", "credit", "ext_cc", "gift", "store_acct", "zelle", "acima"):
        if k in t:
            row[f"t_{k}"] = t[k]
    if "cash" in t:
        row["entered_cash"] = t["cash"]
    if "credit" in t or "ext_cc" in t:
        row["entered_credit"] = round(float(t.get("credit") or 0) + float(t.get("ext_cc") or 0), 2)
    return row


def is_real_try(attempt_row) -> bool:
    """THE one rule for "does this closing_attempt row count as a try the rep made?" — dereferenced
    by every counter (the 3-try gate, Management Review's >1-try grouping) so a refusal can never be
    miscounted as a recount. A row written before the refusal columns existed has no `refused` key
    and is a real try, which is exactly what it was."""
    return not bool((attempt_row or {}).get("refused"))
