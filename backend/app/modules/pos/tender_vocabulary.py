"""THE register's tender vocabulary — what a till may be paid with (owner ask 2026-10-30, §POS-15).

Owner, verbatim: *"also add another tender type as Terms . 15/30/60"*.

**Why this module has to exist, stated once.** "Which tenders may a register accept" was written
down TWICE before this: a hardcoded array of buttons in `frontend/src/app/(platform)/pos/sales/
page.tsx`, and a `CHECK (payment_method IN (...))` on `pos.sale_payments` (mig 725). The rule for
hiding the integrated card buttons when a tenant suspends card processing was a THIRD fact, living
only in the browser. Adding one tender type therefore meant editing a screen, a constraint and a
condition with nothing anywhere checking that the three still agreed — the duplicate defect
CLAUDE.md forbids, wearing a hat.

So the vocabulary is declared here, ONCE:

  * WHAT THE TENDERS ARE — `REGISTER_TENDERS`. The platform's vocabulary, not a tenant's (RULE TWO:
    no tenant, carrier, bank or product is named). Each carries the four facts a screen and a
    checkout both need: whether it asks for an amount tendered, whether it needs integrated card
    processing, whether it SETTLES AT THE TILL, and which class of the house tender vocabulary it
    reconciles as.
  * WHICH OF THEM AN ORG OFFERS — `offered(cfg)`, over that org's own `pos.pos_settings` rows. An
    org that declared nothing gets the house answer; an org that switched Terms off does not get it
    back from somebody else's declaration.
  * WHAT A TERMS SALE NEEDS BEFORE IT MAY BE RUNG — `plan_payment`. One function, one set of
    refusals, each a SENTENCE written once, so the screen and the endpoint cannot word a refusal
    differently or disagree about whether a sale is allowed.

**The recon class is DEREFERENCED, never restated.** Which canonical tender classes exist and what
each folds to on the closing axis is already one home — `closing.router.TENDER_VOCAB` /
`tender_class`, locked by `harness_tender_vocab_lock.py`. This module spells no class name of its
own: it carries, per tender, the WORDS a feed would print for it and asks that ladder. A tender the
closing axis has no class for (a check) answers `None` and says so, rather than being filed under a
class that would quietly land in somebody's cash recon.

**A terms sale is money OWED, not money taken.** `settles_now` is False for exactly one tender, and
`plan_payment` returns the whole total as a `balance` with a `due_date`. That is what makes it
different from every other button on the screen, and the reason it may not be rung without a
customer: a sale on terms with nobody named is an amount owed by nobody.

PURE: no IO, no database, no app config. The caller passes the resolved settings dict (what
`pos_config_effective` already answers) and gets a decision back.
"""
from __future__ import annotations

import datetime as _dt

#: The setting that switches the Terms tender on or off for an org (or one store).
TERMS_ENABLED_KEY = "tender_terms_enabled"
#: The setting that declares which day counts a Terms sale may be written for.
TERMS_DAYS_KEY = "tender_terms_day_options"
#: The pre-existing setting that says this tenant takes cards on an external terminal.
SUSPEND_CARDS_KEY = "suspend_card_processing"

#: The day counts the owner asked for, and what an org that has declared nothing is offered.
HOUSE_TERMS_DAYS: tuple[int, ...] = (15, 30, 60)
#: A day count outside this range cannot be a payment term — dropped, never clamped (a clamp would
#: silently write a due date nobody chose).
TERMS_DAYS_MIN = 1
TERMS_DAYS_MAX = 365

#: THE tender vocabulary of a register.
#:   key                — what `pos.sale_payments.payment_method` stores
#:   label              — what the cashier reads
#:   settles_now        — is the money in hand when the receipt prints?
#:   needs_amount_tendered — does the screen ask what the customer handed over (and compute change)?
#:   needs_card_processing — is this button only there while integrated card processing is live?
#:   external_card      — is this the button that REPLACES the integrated pair when it is suspended?
#:   recon_words        — the words a feed prints for this tender, handed to the house ladder. NOT a
#:                        class name: the class is the ladder's answer, so there is no second copy.
REGISTER_TENDERS: tuple[dict, ...] = (
    {"key": "cash", "label": "Cash", "icon": "💵", "settles_now": True,
     "needs_amount_tendered": True, "needs_card_processing": False, "external_card": False,
     "recon_words": "Cash"},
    {"key": "credit_card", "label": "Credit", "icon": "💳", "settles_now": True,
     "needs_amount_tendered": False, "needs_card_processing": True, "external_card": False,
     "recon_words": "Credit"},
    {"key": "debit_card", "label": "Debit", "icon": "🏧", "settles_now": True,
     "needs_amount_tendered": False, "needs_card_processing": True, "external_card": False,
     "recon_words": "Debit card (PIN)"},
    {"key": "check", "label": "Check", "icon": "📝", "settles_now": True,
     "needs_amount_tendered": False, "needs_card_processing": False, "external_card": False,
     "recon_words": "Check"},
    {"key": "card_external", "label": "Card (external terminal)", "icon": "💳",
     "settles_now": True, "needs_amount_tendered": False, "needs_card_processing": False,
     "external_card": True, "recon_words": "External Credit Card"},
    {"key": "terms", "label": "Terms (on account)", "icon": "🧾", "settles_now": False,
     "needs_amount_tendered": False, "needs_card_processing": False, "external_card": False,
     "recon_words": "Store Account"},
)

#: The tender that is paid later rather than at the till. Declared, so no caller tests the string.
TERMS_KEY = "terms"
#: Every key the platform knows. DERIVED — a migration's CHECK list is generated from this.
TENDER_KEYS: tuple[str, ...] = tuple(t["key"] for t in REGISTER_TENDERS)


def _house_tender_class(words: str):
    """The house tender ladder, reached lazily. `closing.router` is a large module and importing it
    at module scope would make every POS import pay for it; the lazy import is the pattern
    `commcalc/router._invoice_tender_classes` already uses for the same home."""
    from app.modules.closing import router as _cr
    return _cr.tender_class(words)


def all_tenders() -> list[dict]:
    """The whole vocabulary, each entry carrying its dereferenced `recon_class`."""
    return [{**t, "recon_class": _house_tender_class(t["recon_words"])} for t in REGISTER_TENDERS]


def _flag(cfg, key, default):
    """A setting read the way `pos.pos_settings` stores values — jsonb, so a boolean may arrive as
    a real boolean or as the string a hand-written row left behind."""
    if not isinstance(cfg, dict) or key not in cfg or cfg[key] is None:
        return default
    v = cfg[key]
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "t", "1", "yes", "on")


def terms_day_options(cfg) -> tuple[int, ...]:
    """The day counts this org declared, sorted and de-duplicated; the house three when it declared
    nothing readable. A value that cannot be a day count is DROPPED and the rest stand — but a
    declaration that yields nothing at all reads as "not declared", never as "no terms available",
    because an empty list would take a tender away without anybody asking for that."""
    raw = ""
    if isinstance(cfg, dict) and cfg.get(TERMS_DAYS_KEY) is not None:
        raw = str(cfg[TERMS_DAYS_KEY])
    days = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if TERMS_DAYS_MIN <= n <= TERMS_DAYS_MAX:
            days.append(n)
    return tuple(sorted(set(days))) or HOUSE_TERMS_DAYS


def offered(cfg) -> list[dict]:
    """The tenders THIS org's register shows, in the order the screen lays them out.

    Two declarations shape it, and neither is a code branch: integrated card processing suspended
    swaps the credit/debit pair for the external-terminal button, and the Terms tender can be
    switched off by an org that does not sell on account."""
    suspended = _flag(cfg, SUSPEND_CARDS_KEY, False)
    terms_on = _flag(cfg, TERMS_ENABLED_KEY, True)
    out = []
    for t in all_tenders():
        if t["needs_card_processing"] and suspended:
            continue
        if t["external_card"] and not suspended:
            continue
        if t["key"] == TERMS_KEY and not terms_on:
            continue
        if t["key"] == TERMS_KEY:
            t = {**t, "day_options": list(terms_day_options(cfg))}
        out.append(t)
    return out


def tender(key: str):
    """One entry of the vocabulary by key, or None when the platform does not know that word."""
    return next((t for t in all_tenders() if t["key"] == (key or "").strip()), None)


def _as_date(value):
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value or "").strip()[:10]
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return _dt.date.today()


def due_date(sale_date, days: int) -> _dt.date:
    """When a terms sale falls due: the sale's own date plus the declared day count. The sale date,
    not today — an edit or a late entry must not move a due date the customer was told."""
    return _as_date(sale_date) + _dt.timedelta(days=int(days))


def plan_payment(method: str, cfg, *, customer_id=None, terms_days=None, sale_date=None,
                 total=0) -> dict:
    """THE decision: may this sale be tendered this way, and what does the payment row say?

    Returns `{refusal, tender, settles_now, terms_days, due_date, balance}`. `refusal` is a
    sentence for the cashier when the answer is no, and None when it is yes — every caller reads
    this one field rather than re-deriving the judgement.
    """
    key = (method or "").strip()
    t = tender(key)
    blank = {"tender": t, "settles_now": True, "terms_days": None, "due_date": None, "balance": 0.0}
    if t is None:
        return {**blank, "refusal": f"This register does not know a tender called "
                                    f"“{key or '(blank)'}”."}
    if key not in [o["key"] for o in offered(cfg)]:
        return {**blank, "refusal": f"{t['label']} is not offered at this register — "
                                    f"a manager can turn it on in the register setup."}
    if t["settles_now"]:
        return {**blank, "refusal": None}
    # ── a terms sale: somebody must owe it, on a day count this org declared ──
    if not (str(customer_id or "").strip()):
        return {**blank, "refusal": "A sale on terms has to be in a customer's name — "
                                    "pick or add the customer first."}
    options = terms_day_options(cfg)
    try:
        days = int(str(terms_days).strip())
    except (TypeError, ValueError):
        days = None
    if days not in options:
        offered_words = " / ".join(str(d) for d in options)
        return {**blank, "refusal": f"{terms_days if terms_days not in (None, '') else '(none)'} "
                                    f"is not a payment term this register offers — "
                                    f"choose {offered_words} days."}
    try:
        owed = round(float(total or 0), 2)
    except (TypeError, ValueError):
        owed = 0.0
    return {"refusal": None, "tender": t, "settles_now": False, "terms_days": days,
            "due_date": due_date(sale_date, days), "balance": owed}
