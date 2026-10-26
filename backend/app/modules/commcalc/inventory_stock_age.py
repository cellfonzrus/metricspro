"""ONE home for "when did this device arrive on the shelf, and how long has it sat there".

OWNER REPORT 2026-10-26, verbatim: *"invenotry aging in luxeliunk shodul say sell within 10 days of
receiving the phones in stock and it should be on every sales reps daSHBOARDS WHEN TEHY LOG IN"*.

THE CLASS. The owner's sentence contains a fact the platform did not have ONE answer for: the date a
handset was RECEIVED IN STOCK. `commcalc.inventory_aging_device.received_date` exists and is NULL on
all 10,525 rows house-wide (measured 2026-10-26) — the POS export carries the device's AGE
(`raw_row`'s "Age in Store"), landed as `days_in_stock` against the file's `as_of_date`, not a date.
So the receipt date has to be DERIVED, and three separate callers were each deriving it:

  • `commcalc/router.py::_derived_received`   — the `/inventory-recon` endpoint.
  • `commcalc/router.py::_mi_ever_over_days`  — the management-incentive aging bonus gate, whose own
    comment already said *"the luxelink aging export carries age, not a received date"*.
  • `commcalc/device_history.build_aging_inventory` — and this one DISAGREED: it used `days_in_stock`
    verbatim, so a device last seen in a July snapshot reported a July age in October. Three copies of
    one subtraction, one of them silently stale — the divergence the house rules call a defect.

So the derivation lives here and the callers dereference it. `harness_inventory_stock_age_guard.py`
fails the build if a second copy appears, which is the only thing that stops the patchwork returning.

THE SNAPSHOT IS NOT HISTORY, AND A STALE ROW IS NOT A PHONE. `inventory_aging_device` is upserted one
row per `(org, imei)` (mig `216`), so a row's `as_of_date` is the LAST file that device appeared in —
it is not a time series. A device that has left the shelf is flipped `on_hand=false`, but a flip that
never ran leaves a stale `on_hand=true` row behind, and the Balance Sheet already learned that
counting those overstates inventory by ~$129k of ghosts (`account/balance_sheet.py`). The same rule is
therefore the same code here: ON THE SHELF means `on_hand` AND carrying the org's CURRENT snapshot
date. Measured 2026-10-26: Luxelink has 1,444 `on_hand=true` rows and 888 at the current snapshot.

AGE IS COUNTED TO TODAY, NEVER READ OFF THE FILE. A rep is being asked how many days a phone has left
to sell, and `days_in_stock` answers that only on the day the file was pulled. So the age here is
`today − received`, and `age_basis` names which evidence produced it, because a surface that cannot
say where a deadline came from will eventually print somebody else's deadline over these devices
(`device_aging_policy`'s own lesson).

THE WINDOW IS NOT DECIDED HERE. "Sell within N days" is a carrier's term, declared on
`commcalc.payable_source_map.sell_within_days` and resolved by `device_aging_policy` (§COMMCALC-26).
This module only subtracts dates and reports what it could not establish.

PURE. No DB, no app imports beyond the policy one-home, no I/O — provable DB-free.
"""
from datetime import date, timedelta

from app.modules.commcalc import device_aging_policy as _aging

#: `age_basis` values, in the order they are preferred.
BASIS_RECEIVED = "received_date"        # the feed dated the receipt itself
BASIS_AGE_AT_SNAPSHOT = "age_at_snapshot"   # derived: as_of_date − days_in_stock, then aged to today
BASIS_NOT_MEASURED = "not_measured"     # neither — rendered as unknown, NEVER as fresh

#: Why a device carries no deadline. A card prints these instead of an invented number.
NO_WINDOW_NO_CARRIER = "carrier_not_identified"
NO_WINDOW_MIXED = "carrier_windows_differ"


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_date(v):
    """'YYYY-MM-DD…' / date -> date, else None. The one parse, so two readers cannot differ by a day."""
    if isinstance(v, date):
        return v
    s = str(v or "")[:10]
    if len(s) < 10:
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        return None


def _today(today=None):
    return _as_date(today) or date.today()


def received_on(row):
    """THE received-in-stock date for one `inventory_aging_device` row, as 'YYYY-MM-DD' or None.

    `received_date` when the feed dates the receipt; otherwise `as_of_date − days_in_stock`, which is
    the POS's own age-in-store anchored on the file it came from. Nothing else — a device with no age
    and no date has no receipt date, and saying so is the point (the 2026-07-28 reconciliation lesson:
    an unknown age is never folded into a dated bucket).
    """
    row = row or {}
    rd = _as_date(row.get("received_date"))
    if rd is not None:
        return rd.isoformat()
    aod = _as_date(row.get("as_of_date"))
    dis = _int(row.get("days_in_stock"))
    if aod is None or dis is None or dis < 0:
        return None
    return (aod - timedelta(days=dis)).isoformat()


def received_basis(row):
    """Which evidence `received_on` used — so a screen can say where the deadline came from."""
    row = row or {}
    if _as_date(row.get("received_date")) is not None:
        return BASIS_RECEIVED
    if received_on(row) is not None:
        return BASIS_AGE_AT_SNAPSHOT
    return BASIS_NOT_MEASURED


def current_snapshot(rows):
    """The org's CURRENT snapshot date — the newest `as_of_date` across its rows, or None.

    Derived from the rows in hand rather than from `today`, because a tenant whose file was last
    pulled on Friday is not a tenant with no inventory.
    """
    best = None
    for r in rows or []:
        d = _as_date((r or {}).get("as_of_date"))
        if d is not None and (best is None or d > best):
            best = d
    return best.isoformat() if best else None


def on_shelf(row, snapshot):
    """Is this device physically on the shelf? `on_hand` AND carrying the current snapshot date.

    The second half is load-bearing, not belt-and-braces: a failed off-hand flip leaves `on_hand=true`
    on a device that is long gone, and `balance_sheet` already measured ~$129k of such ghosts. A rep
    told to sell a phone that is not there is the same defect wearing a hat.
    """
    row = row or {}
    if not row.get("on_hand"):
        return False
    if not snapshot:
        return False
    d = _as_date(row.get("as_of_date"))
    return d is not None and d.isoformat() == str(snapshot)[:10]


def days_on_shelf(row, today=None):
    """Whole days from the receipt date to TODAY, or None when the receipt date is unknown.

    NOT `days_in_stock`: that is the age on the day the file was pulled, and a rep reading it in
    October would be told a July age. `device_history` did exactly that until this module existed.
    """
    rec = _as_date(received_on(row))
    if rec is None:
        return None
    return (_today(today) - rec).days


def days_left(row, policy, today=None):
    """Days left inside the carrier's selling window, or None when the age is unknown.

    Negative means the window has already closed, by that many days — a card SHOWS the negative rather
    than clamping it to zero, because "4 days over" and "due today" are different conversations.
    """
    held = days_on_shelf(row, today)
    if held is None or not policy:
        return None
    return int(policy["sell_within_days"]) - held


def resolve_window(carrier_id, policies, maps=None):
    """Which selling window applies to one device -> (policy | None, reason | None).

    A device's carrier is resolved by the caller (the existing `device_model_alias` one-home); this
    decides what to do with the answer:

      • carrier known and mapped        -> that carrier's declared policy.
      • carrier unknown, but every one of the org's active maps declares the SAME window -> that
        window. Not a guess: whichever carrier it turns out to be, the deadline is the same number.
      • carrier unknown and the org's windows DIFFER -> no policy, reason `carrier_windows_differ`.
      • carrier unknown and the org has no map at all -> no policy, `carrier_not_identified`.

    Deliberately NOT falling back to the house 60 the way `unsold_inventory.policy_for` does. That
    fallback is right for a payables total, which must not lose a device; it is wrong on a rep's card,
    where printing "45 days left" over a 10-day carrier's phone is the exact defect §COMMCALC-26 was
    opened for. An unestablished deadline is shown as unknown.
    """
    if carrier_id and policies and carrier_id in policies:
        return policies[carrier_id], None
    windows = {int(p["sell_within_days"]) for p in (policies or {}).values() if p}
    if len(windows) == 1:
        only = next(iter((policies or {}).values()))
        return only, None
    if not windows:
        return None, NO_WINDOW_NO_CARRIER
    return None, NO_WINDOW_MIXED


def shelf_row(row, policy, today=None, reason=None):
    """One `inventory_aging_device` row -> the shape a rep's card and its export both render.

    The bucket and the window are `device_aging_policy`'s, so this card, the Inventory Aging report
    and the payables screen cannot ladder the same device differently.
    """
    row = row or {}
    held = days_on_shelf(row, today)
    left = days_left(row, policy, today)
    return {
        "imei": row.get("imei") or row.get("serial"),
        "device_model": (str(row.get("item") or "").strip()
                         or str(row.get("sku") or "").strip() or None),
        "store": (str(row.get("store") or "").strip() or None),
        "unit_cost": row.get("unit_cost"),
        "received_date": received_on(row),
        "age_basis": received_basis(row),
        "days_on_shelf": held,
        "days_left": left,
        "sell_within_days": int(policy["sell_within_days"]) if policy else None,
        "window_declared": bool(policy["declared"]) if policy else False,
        "no_window_reason": reason,
        "aging_bucket": _aging.bucket(held, policy) if policy else _aging.BUCKET_UNKNOWN,
        "overdue": (left is not None and left < 0),
    }


def summarise(rows):
    """`shelf_row` output -> the headline a card leads with.

    `undated` is counted and NEVER rolled into `due_soon` or `ok`; `no_window` is counted separately
    from `undated`, because "we do not know this phone's age" and "we do not know this phone's
    deadline" are different gaps with different fixes (a feed vs a config row).
    """
    devices = len(rows or [])
    overdue = due_today = undated = no_window = 0
    value_overdue = 0.0
    for r in rows or []:
        if r.get("days_on_shelf") is None:
            undated += 1
            continue
        if r.get("days_left") is None:
            no_window += 1
            continue
        if r["days_left"] < 0:
            overdue += 1
            try:
                value_overdue += float(r.get("unit_cost") or 0)
            except (TypeError, ValueError):
                pass
        elif r["days_left"] == 0:
            due_today += 1
    return {"devices": devices, "overdue": overdue, "due_today": due_today,
            "undated": undated, "no_window": no_window,
            "value_overdue": round(value_overdue, 2)}
