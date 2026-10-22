"""ONE home for "how long may a handset sit unsold, and when does it become OUT OF POCKET".

OWNER REPORT 2026-10-22, verbatim: *"under Luxelink it is using Boost - it should not , also it
should not say sell before 60 days as that is boost requirement , total is sell before 12 days"* and
*"if the imei is not sold then the device has to be paid out of pocket whch needs to be highlighted
universally"*.

THE CLASS. "Sell it within 60 days" is a CARRIER'S term, and it was a literal in the code — the
`< 45 / 45-60 / > 60` ladder appeared in `asset/router.get_aging` and again in
`commcalc/device_history.AGING_BUCKETS`, whose comment asked the next reader to "keep the edges in
lock-step" with it. Two copies of one carrier's contract, shown to every tenant including the ones
that never signed it. RULE TWO forbids exactly this (behaviour is per-org config, never a carrier
name or number in code), so the window is now DECLARED per carrier on
`commcalc.payable_source_map.sell_within_days` (mig `1091`) and read here.

THE LADDER IS DERIVED FROM THE WINDOW, NOT LISTED BESIDE IT. A warn edge is "most of the way through
the window", one declared fraction (`warn_fraction`, house default 0.75). That is not a new rule: the
house ladder today is 45 and 60, and 45 / 60 is EXACTLY 0.75, so Boost's buckets are byte-identical
after the change while Total's 12-day window yields 9 and 12 instead of inheriting somebody else's 45.
`harness_device_aging_policy.py` pins that equivalence, so a future edit cannot drift the Boost
ladder while claiming to generalise it.

WHAT "OUT OF POCKET" MEANS, SAID ONCE. A device is paid out of pocket when it is STILL UNSOLD and
its payment is DUE: the invoice has to be settled whether or not the handset moved, so the dealer
funds it from their own cash rather than from the sale. Two surfaces could each have invented a test
for that (`due_date < today`, or `days_held > window`) and they would disagree on every device whose
feed supplies one of the two facts and not the other. So the verdict is computed here, from whichever
evidence exists, and it NAMES its basis — `due_date` when the feed dates the invoice, `window` when
only the holding period is known, and `not_measured` when neither is, which is never silently
rendered as "fine".

PURE. No DB, no app imports, no I/O — provable DB-free.
"""
from datetime import date, timedelta

#: House default when a carrier declares no window. 60 is what the platform has always shown, so a
#: tenant whose map is not yet filled in sees no change (RULE TWO: a house default, never a branch).
DEFAULT_SELL_WITHIN_DAYS = 60

#: How far through the window the "aging" warning starts, as a fraction. 0.75 reproduces the
#: historical 45-of-60 edge exactly.
DEFAULT_WARN_FRACTION = 0.75

#: The bucket keys, unchanged from the ladder they replace so every existing reader, response shape
#: and stored filter keeps working. Only the EDGES are now declared.
BUCKET_FRESH = "under45"       # historical key, kept: renaming it would break stored UI selections
BUCKET_WARN = "warn"
BUCKET_MISSED = "missed"
BUCKET_UNKNOWN = "unknown_age"
BUCKET_ZERO = "zero"

#: `out_of_pocket` bases, in the order they are preferred.
BASIS_DUE_DATE = "due_date"
BASIS_WINDOW = "window"
BASIS_NOT_MEASURED = "not_measured"


def _int(v, default=None):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n


def _float(v, default=None):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n


def resolve_policy(cfg=None) -> dict:
    """A carrier's `payable_source_map` row -> the aging policy to apply to its devices.

    Returns {'sell_within_days', 'warn_after_days', 'warn_fraction', 'declared', 'label'}.
    `declared` is False when the row names no window and the house default answered — a surface that
    shows a deadline should be able to say whether the carrier told us that number or we assumed it.
    """
    cfg = cfg or {}
    days = _int(cfg.get("sell_within_days"))
    declared = days is not None and days > 0
    if not declared:
        days = DEFAULT_SELL_WITHIN_DAYS
    frac = _float(cfg.get("warn_fraction"))
    if frac is None or not (0 < frac < 1):
        frac = DEFAULT_WARN_FRACTION
    # The warn edge is the last whole day still inside the fraction, and it can never equal or pass
    # the window itself — a one-day window has no room for a warning, and a ladder whose middle
    # bucket is empty must not swallow the overdue one.
    warn = int(days * frac)
    if warn >= days:
        warn = days - 1
    if warn < 1:
        warn = 0
    return {"sell_within_days": days, "warn_after_days": warn, "warn_fraction": frac,
            "declared": declared, "label": (cfg.get("label") or "").strip() or None}


def bucket(days_held, policy) -> str:
    """How long has this unsold device been held -> which bucket.

    `days_held` None is `unknown_age` — a real device whose purchase date the feed does not carry,
    never folded into a dated bucket (the 2026-07-28 reconciliation lesson, kept). Otherwise: inside
    the warn edge is fresh, up to and including the window is warn, past the window is missed. The
    edges are the policy's, so Boost's 45/60 and Total's 9/12 come out of ONE comparison.
    """
    d = _int(days_held)
    if d is None:
        return BUCKET_UNKNOWN
    if d < policy["warn_after_days"]:
        return BUCKET_FRESH
    if d <= policy["sell_within_days"]:
        return BUCKET_WARN
    return BUCKET_MISSED


def bucket_ladder(policy) -> tuple:
    """The ladder as (key, label, range) triples, for a legend or a column header. DERIVED from the
    policy, so a screen can never print '45-60 days' over a 12-day carrier's devices."""
    w, s = policy["warn_after_days"], policy["sell_within_days"]
    return (
        (BUCKET_FRESH, "Fresh", f"< {w} days"),
        (BUCKET_WARN, "Aging", f"{w}–{s} days"),
        (BUCKET_MISSED, "Overaged", f"> {s} days"),
    )


def days_held(acquired, today=None):
    """Whole days from a purchase/invoice date to `today`; None when there is no usable date. The
    one place this subtraction is done, so two surfaces cannot disagree by a day."""
    s = str(acquired or "")[:10]
    if len(s) < 10:
        return None
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return ((today or date.today()) - date(y, m, d)).days
    except (ValueError, TypeError):
        return None


def due_on(invoice_date, net_terms_days, today=None):
    """When the invoice for a device bought on `invoice_date` falls due, under declared net terms.
    None when either half is missing — a due date is never invented from a window, because the window
    is a SELLING deadline and the terms are a PAYING one, and on Boost they are not the same number."""
    s = str(invoice_date or "")[:10]
    n = _int(net_terms_days)
    if len(s) < 10 or n is None or n < 0:
        return None
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return (date(y, m, d) + timedelta(days=n)).isoformat()
    except (ValueError, TypeError):
        return None


def out_of_pocket(*, sold, due_date=None, days_held=None, policy=None, today=None) -> dict:
    """THE universal verdict: is this device money the dealer must fund themselves?

    Returns {'out_of_pocket': bool|None, 'basis', 'days_overdue'}. `out_of_pocket` None means
    NOT MEASURED — neither a due date nor a holding period was available — and a surface must render
    that as unknown, never as settled.

    A SOLD device is never out of pocket, whatever its dates say: the sale is what funds the invoice,
    which is the whole point of the deadline. For an unsold one, a real `due_date` decides (the
    invoice is payable on that day regardless of the handset); failing that, the carrier's selling
    window stands in, because a device held past the window is one the carrier will bill for.
    """
    policy = policy or resolve_policy()
    ref = today or date.today()
    if isinstance(ref, str):
        ref = date.fromisoformat(str(ref)[:10])
    if sold:
        return {"out_of_pocket": False, "basis": BASIS_DUE_DATE if due_date else BASIS_WINDOW,
                "days_overdue": 0}
    s = str(due_date or "")[:10]
    if len(s) == 10:
        try:
            y, m, d = (int(x) for x in s.split("-"))
            over = (ref - date(y, m, d)).days
            return {"out_of_pocket": over >= 0, "basis": BASIS_DUE_DATE,
                    "days_overdue": max(0, over)}
        except (ValueError, TypeError):
            pass
    held = _int(days_held)
    if held is None:
        return {"out_of_pocket": None, "basis": BASIS_NOT_MEASURED, "days_overdue": None}
    over = held - policy["sell_within_days"]
    return {"out_of_pocket": over > 0, "basis": BASIS_WINDOW, "days_overdue": max(0, over)}
