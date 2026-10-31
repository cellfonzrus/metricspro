"""THE wholesale customer and the W-9 that makes a sale tax-free (owner ask 2026-10-30, §POS-16).

Owner, verbatim: *"add … a customer type as wholesale where the option to not charge tax will be by
default if the user confirms they received the filled out w9 and block the user to make a sale
before the system has an updated w9, only the admin can go past without an updated w9"*.

**ONE ANSWER, NOT TWO.** Tax-free is not a property of a customer TYPE; it is a property of a
customer type **with a current exemption certificate on file**. If a screen zeroed the tax because
the account type says Wholesale while the gate separately decided the W-9 was out of date, a sale
would go out tax-free against a certificate nobody holds — and the house would carry the tax
liability. So `may_sell` answers both in one call, and it states the BASIS: a tax-free sale with no
stated reason is exactly what an auditor asks about, and `exempt_basis` is that reason, recorded on
the sale.

WHAT LIVES HERE, AND NOWHERE ELSE:
  * WHICH CUSTOMER TYPES EXIST — `ACCOUNT_TYPES`, from which mig 1107's CHECK list is generated and
    which the customer form offers. `pos.customers.account_type` carried `Personal` / `Business` in
    a CHECK constraint (mig 725) and the form carried the same two words again; adding a third
    would have been the two-copies defect, so the list is declared once.
  * WHETHER A CUSTOMER IS WHOLESALE — `is_wholesale`. Callers ask; none of them spells the word.
  * WHETHER A W-9 IS CURRENT — `w9_state`. A certificate's OWN expiry wins when it carries one;
    otherwise the org's declared refresh period (`w9_valid_months`, 36 by house default, `0` =
    never goes out of date) decides. The IRS does not expire Form W-9, so "how often do we refresh
    one" is a policy, which is why it is a declaration and not a constant in code.
  * WHETHER THE SALE MAY BE RUNG, AND AT WHAT TAX — `may_sell`, with `taxable_rate` as the one place
    an exemption meets a rate.
  * WHERE THE BLANK FORM IS — `blank_form_url`, the org's declaration with the IRS's own published
    PDF as the house default. Only an `https://` URL is honoured: a link handed to a customer is a
    link we are vouching for.

**The admin override is OFFERED, never assumed.** `override_available` says an admin COULD go past;
the sale is still blocked until an override is actually passed in, and then `override_by` names who
allowed it. Going past a missing certificate never grants the exemption — it permits a TAXABLE sale.
That asymmetry is the whole point: the block protects the sale from stalling, the exemption protects
the house from an unsupported tax position.

PURE: no IO, no database. The caller passes the customer row, its W-9 row (or None) and the org's
resolved settings. RULE TWO: no tenant, carrier or product is named.
"""
from __future__ import annotations

import datetime as _dt

#: Every customer type the platform knows. `Wholesale` is the new third (owner 2026-10-30).
ACCOUNT_TYPES: tuple[str, ...] = ("Personal", "Business", "Wholesale")
#: The type a tax exemption can attach to. Declared, so nothing tests the string.
WHOLESALE = "Wholesale"

#: The document kind this module is about. One row per customer per kind in `pos.customer_tax_doc`.
W9_KIND = "w9"
#: A W-9 row only counts once somebody CONFIRMED the filled-out form arrived.
STATUS_RECEIVED = "received"
#: and a row that merely records that we asked for one.
STATUS_REQUESTED = "requested"

#: The settings this module reads (named once; `pos/router._w9_cfg` resolves exactly these).
EXEMPT_DEFAULT_KEY = "wholesale_tax_exempt_default"
VALID_MONTHS_KEY = "w9_valid_months"
BLOCK_SALE_KEY = "w9_block_sale"
FORM_URL_KEY = "w9_blank_form_url"

#: House policy: refresh a certificate every three years. `0` means it never goes out of date.
HOUSE_VALID_MONTHS = 36
#: The IRS's own published blank Form W-9. A tenant that hosts its own packet declares that instead.
HOUSE_W9_FORM_URL = "https://www.irs.gov/pub/irs-pdf/fw9.pdf"

#: What `exempt_basis` says when a sale is tax-free. One word, recorded on the sale.
BASIS_W9 = "w9_on_file"
#: The three states a certificate can be in.
CURRENT, EXPIRED, MISSING = "current", "expired", "missing"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _flag(cfg, key, default):
    """A setting as `pos.pos_settings` stores it — jsonb, so a boolean may arrive as a string."""
    if not isinstance(cfg, dict) or cfg.get(key) is None:
        return default
    v = cfg[key]
    if isinstance(v, bool):
        return v
    return _s(v).lower() in ("true", "t", "1", "yes", "on")


def _date(value):
    s = _s(value)[:10]
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return None


def valid_months(cfg) -> int:
    """How long this org treats a W-9 as current. `0` = never goes out of date."""
    raw = (cfg or {}).get(VALID_MONTHS_KEY) if isinstance(cfg, dict) else None
    if raw is None or _s(raw) == "":
        return HOUSE_VALID_MONTHS
    try:
        n = int(_s(raw))
    except ValueError:
        return HOUSE_VALID_MONTHS
    return n if n >= 0 else HOUSE_VALID_MONTHS


def is_wholesale(customer) -> bool:
    """Is this customer on wholesale terms? Asked here so no caller spells the word."""
    return _s((customer or {}).get("account_type")).lower() == WHOLESALE.lower()


def _add_months(d: _dt.date, months: int) -> _dt.date:
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return _dt.date(y, m + 1, day)


#: Why a certificate carries no lapse date. A reason, never a date a reader could sort on.
NO_EXPIRY_REASONS = {
    "not_received": "no signed form has been confirmed received, so there is nothing to expire",
    "never_expires": "this company has declared that a W-9 on file does not go out of date",
}


def expiry_date(doc, cfg) -> dict:
    """THE day this certificate stops being good for, and WHY when there is no such day.

    `{date, basis, reason}`. `basis` is `own` when the form carries its own expiry and `declared`
    when the day is the org's refresh period counted from arrival — the same two rungs `w9_state`
    judged by before this was extracted, which is why `w9_state` now reads it instead of repeating
    it. A date with no basis would be the fallback-basis defect §COMMCALC-66 exists to prevent: a
    reader cannot tell a certificate's own deadline from a house refresh rule, and only one of the
    two is the customer's problem to fix.

    EXTRACTED 2026-10-31 for the W-9 status report, which needs the DAY and not just the verdict:
    "it expires some time" cannot be chased, and "it expires on the 14th" can.
    """
    doc = doc or {}
    received = _date(doc.get("received_at"))
    if _s(doc.get("status")) != STATUS_RECEIVED or not received:
        return {"date": None, "basis": None, "reason": NO_EXPIRY_REASONS["not_received"]}
    own = _date(doc.get("expires_at"))
    if own:
        return {"date": own, "basis": "own", "reason": None}
    months = valid_months(cfg)
    if months == 0:
        return {"date": None, "basis": None, "reason": NO_EXPIRY_REASONS["never_expires"]}
    return {"date": _add_months(received, months), "basis": "declared", "reason": None}


def w9_state(doc, cfg, *, now) -> str:
    """`current` / `expired` / `missing` for ONE certificate row.

    A row that only records that we asked is `missing` — the owner's wording is that the exemption
    follows a CONFIRMED receipt of the filled-out form, so an unanswered request is not a document.
    WHEN it lapses is `expiry_date`'s single answer and is dereferenced here rather than re-derived,
    so the verdict a sale is blocked on and the date a report chases can never disagree.
    """
    doc = doc or {}
    received = _date((doc or {}).get("received_at"))
    if _s((doc or {}).get("status")) != STATUS_RECEIVED or not received:
        return MISSING
    exp = expiry_date(doc, cfg)
    if exp["date"] is None:
        # Received, and no day it lapses on: the org declared that it never does.
        return CURRENT
    return CURRENT if exp["date"] >= now else EXPIRED


def days_until_expiry(doc, cfg, *, now):
    """Whole days from `now` until this certificate lapses. Negative once it has, and **None** when
    there is no lapse date at all — never 0, which a screen would sort beside "lapses today"."""
    exp = expiry_date(doc, cfg)
    return None if exp["date"] is None else (exp["date"] - now).days


def blank_form_url(cfg) -> str:
    """Where a cashier (or a customer's email) gets the blank form. Only an `https://` URL is
    honoured — a link we hand a customer is one we are vouching for, so a declared `javascript:`
    or plain-http value is ignored rather than shown."""
    declared = _s((cfg or {}).get(FORM_URL_KEY) if isinstance(cfg, dict) else "")
    if declared.lower().startswith("https://") and " " not in declared:
        return declared
    return HOUSE_W9_FORM_URL


def may_sell(customer, doc, cfg, *, now, is_admin=False, override=None) -> dict:
    """THE answer: may this sale be rung, and is it taxable?

    Returns `{blocked, refusal, tax_exempt, exempt_basis, w9_status, override_available,
    override_by, needs_w9_request}`. Callers read these fields; none of them re-derives one.
    """
    out = {"blocked": False, "refusal": None, "tax_exempt": False, "exempt_basis": None,
           "w9_status": None, "override_available": False, "override_by": None,
           "needs_w9_request": False, "wholesale": False}
    if not is_wholesale(customer):
        return out
    out["wholesale"] = True
    state = w9_state(doc, cfg, now=now)
    out["w9_status"] = state
    if state == CURRENT:
        if _flag(cfg, EXEMPT_DEFAULT_KEY, True):
            out["tax_exempt"] = True
            out["exempt_basis"] = BASIS_W9
        return out
    # No usable certificate. The sale is TAXABLE whatever happens next: going past a missing
    # document permits a sale, it does not support an exemption.
    out["needs_w9_request"] = True
    if not _flag(cfg, BLOCK_SALE_KEY, True):
        return out
    out["blocked"] = True
    out["override_available"] = bool(is_admin)
    if is_admin and _s(override):
        out["blocked"] = False
        out["override_by"] = _s(override)
        return out
    who = _s((customer or {}).get("company_name")) or "this wholesale customer"
    if state == EXPIRED:
        out["refusal"] = (f"The W-9 on file for {who} is out of date. Send a new one for signature "
                          f"before ringing this sale — an admin can allow it as a taxable sale.")
    else:
        out["refusal"] = (f"No signed W-9 is on file for {who}. A wholesale sale needs one — send "
                          f"the form from this screen, or an admin can allow it as a taxable sale.")
    return out


def taxable_rate(rate, decision) -> float:
    """THE one place an exemption meets a tax rate: the resolved store rate, or zero on an exempt
    sale. A screen that applied its own rate to an exempt sale would be the second answer."""
    try:
        r = float(rate or 0)
    except (TypeError, ValueError):
        r = 0.0
    return 0.0 if (decision or {}).get("tax_exempt") else r


# ── the form, the link and the words that go with them ───────────────────────────────────────────
# The request email's subject and body live HERE, beside the decision, for the same reason the
# refusals do: two screens (and one later reminder) must not word one request differently. Nothing
# below does IO — the caller mints nothing, sends nothing and builds no address of its own.

#: How many days a customer's return link stays usable. A credential with no expiry is a key left
#: in a door; a week is long enough for somebody to get a form signed.
RETURN_LINK_DAYS = 14
#: The path the customer's browser opens. One spelling, shared by the link builder and the page.
RETURN_PATH = "/w9"


def new_token() -> str:
    """A fresh return token: 32 bytes from the OS's cryptographic source, url-safe.

    This is the ONLY thing standing between an anonymous caller and an upload slot, so it is never
    derived from a customer id, a name or a timestamp — a guessable token is not a credential.
    """
    import secrets
    return secrets.token_urlsafe(32)


def token_expiry(now, days: int = RETURN_LINK_DAYS):
    """When a freshly minted link stops working."""
    base = now if isinstance(now, _dt.datetime) else _dt.datetime(now.year, now.month, now.day)
    return base + _dt.timedelta(days=days)


def token_live(doc, *, now) -> bool:
    """Is this row's return link still usable? Unknown, expired, cleared — all the same answer, so
    the public endpoint can give one indistinguishable refusal."""
    doc = doc or {}
    if not _s(doc.get("return_token")):
        return False
    exp = _s(doc.get("token_expires_at"))
    if not exp:
        return True
    try:
        when = _dt.datetime.fromisoformat(exp.replace("Z", "").split("+")[0].split(".")[0])
    except ValueError:
        return False
    ref = now if isinstance(now, _dt.datetime) else _dt.datetime(now.year, now.month, now.day)
    return when >= ref


def return_link(app_url: str, token: str) -> str:
    """The address the customer opens to send the form back. Built in one place so the page's route
    and the emailed link cannot disagree."""
    return f"{_s(app_url).rstrip('/')}{RETURN_PATH}/{_s(token)}"


def request_subject(company_name: str = "") -> str:
    who = _s(company_name)
    return f"Form W-9 requested by {who}" if who else "Form W-9 requested"


def request_html(company_name: str, form_url: str, link: str, customer_name: str = "") -> str:
    """The email a wholesale customer gets: the blank form to fill in, and the link to send it
    back. Plain, specific and short — the two things they have to do, in order."""
    import html as _h
    who = _h.escape(_s(company_name) or "our company")
    hello = f"Hello {_h.escape(_s(customer_name))}," if _s(customer_name) else "Hello,"
    return (
        f"<p>{hello}</p>"
        f"<p>Before we can invoice you without sales tax, {who} needs a completed Form W-9 on "
        f"file.</p>"
        f"<p><strong>1.</strong> Download the blank form: "
        f'<a href="{_h.escape(form_url)}">Form W-9 (PDF)</a></p>'
        f"<p><strong>2.</strong> Fill it in, sign it, and send it back here: "
        f'<a href="{_h.escape(link)}">upload your completed W-9</a></p>'
        f"<p>The upload link is personal to you and stops working in {RETURN_LINK_DAYS} days. "
        f"If it has expired, just ask us for a new one.</p>"
        f"<p>Thank you.</p>")


#: The one wording of the public page's refusal. Identical for unknown, expired and already-used, so
#: an anonymous probe cannot tell which it hit (index §POS-16.6).
LINK_DEAD = ("This upload link is no longer valid. Please ask for a new one and we will send it "
             "straight over.")


def summarise(rows) -> dict:
    """The rollup the W-9 status report's header shows, over rows carrying `w9_status` and
    `expiring_soon`.

    `wholesale` is the count of wholesale customers the org has declared, and it is reported even
    when it is zero — an empty table reads as "all clear", and the owner's 2026-10-30 directive is
    that nothing declared means nothing identified and the surface says so. PURE.
    """
    out = {"wholesale": 0, "current": 0, "expired": 0, "missing": 0,
           "expiring_soon": 0, "blocked": 0, "tax_exempt": 0, "never_requested": 0,
           "soonest_days": None}
    for r in rows or []:
        r = r or {}
        out["wholesale"] += 1
        state = _s(r.get("w9_status"))
        if state in (CURRENT, EXPIRED, MISSING):
            out[state] += 1
        if r.get("expiring_soon"):
            out["expiring_soon"] += 1
        if r.get("blocked"):
            out["blocked"] += 1
        if r.get("tax_exempt"):
            out["tax_exempt"] += 1
        if state != CURRENT and not r.get("requested_at"):
            out["never_requested"] += 1
        left = r.get("days_until_expiry")
        if isinstance(left, int) and (out["soonest_days"] is None or left < out["soonest_days"]):
            out["soonest_days"] = left
    return out
