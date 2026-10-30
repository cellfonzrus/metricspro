"""DEVICE DEMAND PROJECTION — the ONE home for "how many of this will sell next", and for which
sale line is a device at all.

WHY THIS FILE EXISTS (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix"). The arithmetic below was
written TWICE on this platform before it was written here, and the second copy says so out loud:

  · `payables/router.forecast` (§15, module 095) — `rate = units / lookback`,
    `projected = int(round(rate * horizon))`, `recommend = max(0, projected - on_hand)`, plus its own
    `_is_device_line(dept, category)`.
  · `asset/purchase_orders.po_recommendations` (§36) — the SAME four lines and a near-verbatim copy of
    the same device-line test, whose docstring reads *"same rule as payables/router.py's
    _is_device_line (duplicated here, not imported, to avoid the module-load cycle)"*.

The import-cycle worry that justified the second copy was real and is answered rather than argued
with: this module imports NOTHING (not FastAPI, not the database, not another app module), so either
caller can dereference it from any load order. Two paths answering one question is the duplicate
defect the index rules forbid, and "how many will sell" is a question the platform now has to answer
a THIRD time for the accessory order plan (§COMMCALC-49) — which is exactly the moment the house rule
says to factor, not to copy.

**ONE FACT, ONE HOME, DEREFERENCED.** `harness_demand_projection.py` fails the build when a second
copy of the velocity arithmetic or of the device-line test appears anywhere under `backend/app`, and
when either existing caller stops reading this module.

════════════════════════════════════════════════════════════════════════════════════════════════════
THE ARITHMETIC, AND WHY IT IS COPIED TO THE DIGIT
════════════════════════════════════════════════════════════════════════════════════════════════════
    velocity  = units / window            units sold inside the window, per window unit
    projected = int(round(velocity * horizon))
    recommend = max(0, projected - on_hand)

`int(round(...))` is Python's banker's rounding and it is PRESERVED deliberately. A "tidier"
`floor(x + 0.5)` disagrees with it on every exact .5 — 2.5 goes to 2 here and to 3 there — and the two
live callers have been ordering phones on the banker's answer. An extraction that quietly changes
rounding is not an extraction; it is an unannounced change to order quantities.

**THE UNIT OF THE WINDOW IS THE CALLER'S.** `/payables/forecast` and the PO recommendations count
DAYS; the accessory order plan counts MONTHS. The arithmetic does not care, and does not pretend to:
velocity is per window-unit and the horizon is in the same unit, which is why both callers divide by
their OWN window's count and neither has a second divisor hidden in it.

════════════════════════════════════════════════════════════════════════════════════════════════════
THE HONESTY RULES (each is a check in `harness_demand_projection.py` and a test in
`backend/tests/unit/test_demand_projection.py`)
════════════════════════════════════════════════════════════════════════════════════════════════════
  · **A window of no days cannot be measured, and never reads as zero.** `basis='no_window'` and
    `projected=None`. A 0 would put an order quantity of zero on a screen and call it planning — the
    §19.26 class this platform keeps paying for.
  · **Nothing sold over a real window IS zero.** We looked; the answer is none. That is a measurement
    and the row says `basis='measured'`.
  · **A horizon of nothing is a real order of nothing.** "What do I need for the next 0 months" has
    an answer.
  · **Unmeasured in, unmeasured out.** `recommend_qty(None, on_hand)` is `None`, never the on-hand
    figure negated.
  · **Every term travels with the answer.** A quantity on an order screen has to be arguable, so the
    units, the window and the horizon that produced it are all on the payload.

PURE. No import of anything, so the two existing callers can dereference it from any load order and
`harness_demand_projection.py` drives it under `env -i`.
"""
from __future__ import annotations

# ── WHICH SALE LINE IS A DEVICE (RULE TWO: the DEPARTMENTS ARE THE ORG'S OWN DECLARATION) ────────
# THE DEFECT THIS SHAPE EXISTS TO PREVENT (owner-reported 2026-10-30, and it was mine). This module
# used to carry `HOUSE_DEVICE_DEPARTMENTS = ("android - xp", "iphone - xp", "tablet - xp")` as an
# OPTIONAL default. Those are one tenant's POS labels. Every caller took the default, so for a tenant
# that files handsets under a different department name all three features — this plan, the device
# order forecast and the PO recommendations — reported that it had sold no devices at all: 1,034
# handsets in one month, read as zero, with no error anywhere.
#
# The declaration already existed and was already correct: `accessory_config.box_departments`
# (mig 218) is the org's own list, and the box count, the product mix, the GP report and the
# multi-handset flag all read it. Only this test kept a copy — the duplicate defect the index rules
# forbid, found by the owner rather than by the duplicate check, because I searched for the
# ARITHMETIC's copies and not for this one.
#
# SO THERE IS NOTHING TO FALL BACK TO. `depts` is REQUIRED. A caller that has not asked the org which
# departments are device departments cannot get an answer at all, which is why this cannot silently
# come back. `device_vocabulary.declared_device_departments` is the one home for asking, and the house
# fallback for an org that has declared nothing has its one home in the router (`_BOX_DEPTS`).
#
# The CATEGORY half is different in kind and stays a house tolerance: the older export format marks a
# handset `category='CellPhone'` with a BLANK department. That is a FILE FORMAT marker, not a carrier,
# tenant or product name, so RULE TWO does not reach it — and a tenant whose feed says otherwise
# declares its own through `categories`.
HOUSE_DEVICE_CATEGORIES = ("cellphone",)

#: Why a projection reads the way it does. Returned on every projection, so a caller can never show a
#: quantity without being able to say what it rests on.
PROJECTION_BASES = ("measured", "no_window")


def _lower_tuple(raw, default):
    """A declared vocabulary → a lower-cased tuple. Missing / junk → the house default.

    An EXPLICITLY EMPTY list is honoured and means "this half of the test identifies nothing here",
    which is a real declaration for a tenant whose feed carries only one of the two columns."""
    if raw is None or isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        return tuple(default)
    out = tuple(t for t in (str(v or "").strip().lower() for v in raw) if t)
    return out


def resolve_device_vocabulary(depts, categories=None):
    """The org's declared device vocabulary → `(departments, categories)`, lower-cased. PURE.

    `depts` is the ORG'S OWN list and is required; an empty list resolves to an empty set, which
    identifies nothing. That is the honest answer for an org that has declared nothing — the report
    then says it found no device sales, and a human fixes the declaration, where quietly borrowing
    another tenant's words would hand them a confident wrong number."""
    return (_lower_tuple(depts, ()), _lower_tuple(categories, HOUSE_DEVICE_CATEGORIES))


def is_device_line(dept, category, depts, categories=None) -> bool:
    """Whether ONE sale line sold a physical device, BY THIS ORG'S DECLARATION. PURE, and THE one
    test (§COMMCALC-49).

    `depts` — the org's declared device departments — is REQUIRED, with no default, so that a caller
    cannot answer this question without having asked the org. See the header: an optional default is
    exactly how three features came to be blind to a whole tenant.

    EITHER column may answer, because the two live feed formats each carry only one of them: a line
    whose category names a handset is a handset even with a blank department, and vice versa. That
    tolerance is the whole reason the test exists once rather than being inlined per reader."""
    d, c = resolve_device_vocabulary(depts, categories)
    return (str(category or "").strip().lower() in c) or (str(dept or "").strip().lower() in d)


# ── THE PROJECTION ───────────────────────────────────────────────────────────────────────────────
def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def velocity(units, window):
    """Units per window-unit, or None when there is no window to divide by. PURE."""
    w = _num(window, 0.0)
    if w <= 0:
        return None
    return _num(units) / w


def projection(units, window, horizon) -> dict:
    """`units` sold inside a `window` → the units expected over the next `horizon`. PURE.

    The window and the horizon are in the CALLER's unit (days for the device forecast and the PO
    recommendations, months for the accessory order plan) and must be the same unit as each other.
    Every term is returned beside the answer so the quantity can be argued with."""
    v = velocity(units, window)
    if v is None:
        return {"units": units, "window": window, "horizon": horizon,
                "velocity": None, "projected": None, "basis": "no_window"}
    h = max(0.0, _num(horizon, 0.0))
    return {"units": units, "window": window, "horizon": horizon,
            "velocity": round(v, 2), "projected": int(round(v * h)), "basis": "measured"}


def projected_units(units, window, horizon):
    """Just the number, for a caller that has already stated its basis. PURE."""
    return projection(units, window, horizon)["projected"]


def recommend_qty(projected, on_hand):
    """What to order: the projected demand less what is already on the shelf, never below zero.

    `None` in is `None` out — a demand nobody could measure does not become an order of the on-hand
    count negated, and it does not become zero either."""
    if projected is None:
        return None
    p = int(round(_num(projected)))
    return max(0, p - int(round(_num(on_hand))))
