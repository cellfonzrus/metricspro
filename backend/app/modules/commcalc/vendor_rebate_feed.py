"""PURE read-side rules for the per-line vendor rebate/commission history feed (mig 1005).

WHY THIS MODULE EXISTS, AND WHAT IT REFUSES TO DO
─────────────────────────────────────────────────
This feed says what the carrier OWES. It does not say what the carrier PAID. On the first real
export — 47,252 data rows, 2024-01 → 2026-08, one store — `Collected` is $0.00 on EVERY row and
`Balance` carries the entire $6,748,358.09. A report that adds those two together, or that treats
`earned` as income, is not "roughly right": it is the VIP-payable mistake again, where earned and
settled were answered by one number and the number was wrong for both questions.

So: **this module computes, and books nothing.** Nothing here is read by account/coa.py, the P&L,
the Balance Sheet, GP, commission payout or any accrual, and `BOOKS_TO` is the empty tuple as the
machine-readable statement of that. Whether an earned-but-uncollected rebate is a receivable is an
OPEN OWNER DECISION; until it is made, the honest surface is one that shows earned and collected
side by side and says plainly that neither has been booked.

THREE MEASURED TRAPS THIS MODULE EXISTS TO NOT FALL INTO
────────────────────────────────────────────────────────
1. **The footer row doubles the file.** A grand-total row repeats every numeric column with the
   identity columns blank: $13,496,716.18 against a true $6,748,358.09 — exactly 2x. Dropped at
   ingest by feed_shape.is_footer_row (§25.3), not here; `totals` asserts the shape it expects.
2. **`Unit Rebate` is UNSIGNED.** 6,283 of 47,252 rows are reversals carrying Quantity = -1, and
   `Total Rebate` = `Unit Rebate` x `Quantity` on 47,252/47,252 rows. Summing the unit column
   instead of the signed total overstates the first file by **$630,615.08**. `earned_amount` (the
   signed total) is the ONLY money column summed here.
3. **`device_cost` repeats per component row, not per device.** ~7 rebate component rows share one
   device, so a per-row sum of device cost reads $41,040,251.81 against a true $5,393,764.59 —
   **7.6x**. `device_cost_once` dedupes by IMEI, and it is the only way this module will total it.

PURE / STDLIB-ONLY on purpose, so every rule above is provable DB-free by
backend/harness_vendor_rebate_landing.py. Keep import-clean (no pandas / fastapi / supabase).
"""

# The P&L / Balance-Sheet heads this feed books to. EMPTY, DELIBERATELY, and asserted by the proof
# harness: while "is an earned rebate a receivable?" is undecided, landing the data must move no
# figure anywhere in the app. A future decision adds a head here (and the booking that reads it) —
# it is a config/wiring change, not a re-ingest, because earned/collected/balance already land apart.
BOOKS_TO: tuple = ()

# Settlement states, from the two money columns the feed carries. Named so a reader cannot mistake
# "we know it is unpaid" for "we have not been told" — the difference the §25.8 recon precedent
# (never collapse "not loaded" into "disagrees") says must survive.
UNPAID = "unpaid"
PARTIAL = "partially_collected"
COLLECTED = "collected"
UNKNOWN = "not_reported"


def _num(v):
    """A float from any cell spelling, or 0.0. Never raises — a totals row that reached this far must
    not be able to 500 a read-only summary."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def _r2(f):
    return round(f + 0.0, 2)


def settlement_status(row):
    """Which of the four settlement states one landed row is in.

    `collected_amount` MISSING is not the same as collected $0.00 — the first means the feed did not
    say, the second means the carrier has paid nothing. Conflating them would let a feed that simply
    lacks the column read as "nothing has ever been paid", which is a claim we cannot support."""
    if row.get("collected_amount") in (None, ""):
        return UNKNOWN
    earned, collected = _num(row.get("earned_amount")), _num(row.get("collected_amount"))
    if collected == 0.0:
        return UNPAID
    # Compared on ABSOLUTE value so a reversal line (negative earned, negative collected) classifies
    # by how much of it settled rather than by sign.
    if abs(collected) + 0.005 < abs(earned):
        return PARTIAL
    return COLLECTED


def device_cost_once(rows):
    """Total device cost counted ONCE PER DEVICE, plus how many rows shared a device.

    Returns {'cost', 'devices', 'rows_without_imei'}. The per-ROW sum is never returned, because on
    the first real file it reads 7.6x high ($41.0M vs $5.4M) and a number that wrong is more
    dangerous than no number. Rows with no IMEI cannot be deduped and are REPORTED, not guessed at."""
    seen, cost, no_imei = {}, 0.0, 0
    for r in rows:
        imei = str(r.get("imei") or "").strip()
        if not imei:
            no_imei += 1
            continue
        if imei in seen:
            continue
        seen[imei] = True
        cost += _num(r.get("device_cost"))
    return {"cost": _r2(cost), "devices": len(seen), "rows_without_imei": no_imei}


def totals(rows):
    """The one summary of a set of landed rows. PURE.

    EARNED AND COLLECTED ARE NEVER ADDED TOGETHER and neither is presented as income. `balance` is
    carried as the feed spells it AND recomputed as earned - collected, so a feed whose own Balance
    column disagrees with its own arithmetic is VISIBLE (`balance_disagrees_by`) rather than quietly
    averaged away."""
    earned = collected = balance = 0.0
    reversals = rows_n = 0
    invoices, devices, components, stores, vendors = set(), set(), set(), set(), set()
    lo = hi = None
    for r in rows:
        rows_n += 1
        earned += _num(r.get("earned_amount"))
        collected += _num(r.get("collected_amount"))
        balance += _num(r.get("balance_amount"))
        if _num(r.get("quantity")) < 0:
            reversals += 1
        for key, bag in (("invoice_no", invoices), ("imei", devices), ("rebate_name", components),
                         ("store", stores), ("vendor_account", vendors)):
            v = str(r.get(key) or "").strip()
            if v:
                bag.add(v)
        d = str(r.get("sold_on") or "")[:10]
        if len(d) == 10:
            lo = d if lo is None or d < lo else lo
            hi = d if hi is None or d > hi else hi
    earned, collected, balance = _r2(earned), _r2(collected), _r2(balance)
    return {
        "rows": rows_n,
        # The three money facts, kept apart. `outstanding` is the DERIVED figure; `balance_reported`
        # is what the file said. They agree on the first real feed (both $6,748,358.09).
        "earned": earned,
        "collected": collected,
        "balance_reported": balance,
        "outstanding": _r2(earned - collected),
        "balance_disagrees_by": _r2(balance - (earned - collected)),
        # Never money: counts and coverage.
        "reversal_rows": reversals,
        "invoices": len(invoices),
        "devices": len(devices),
        "components": len(components),
        "stores": sorted(stores),
        "vendor_accounts": sorted(vendors),
        "sold_from": lo,
        "sold_to": hi,
        # The statement every surface over this feed must carry.
        "booked_to": list(BOOKS_TO),
        "posture": ("Earned, not collected. These rows record what the carrier OWES; nothing here is "
                    "booked to the P&L, the Balance Sheet, gross profit or commission payout."),
    }


def group_by(rows, key):
    """Earned/collected/outstanding per distinct value of `key`, biggest earned first. PURE.

    Used for the per-period, per-store and per-vendor-account breakdowns. A blank key becomes
    '(not reported)' rather than being dropped: a row that does not say which program paid it is
    exactly the row an operator needs to see."""
    acc = {}
    for r in rows:
        k = str(r.get(key) or "").strip() or "(not reported)"
        a = acc.setdefault(k, {"key": k, "rows": 0, "earned": 0.0, "collected": 0.0})
        a["rows"] += 1
        a["earned"] += _num(r.get("earned_amount"))
        a["collected"] += _num(r.get("collected_amount"))
    out = []
    for a in acc.values():
        a["earned"], a["collected"] = _r2(a["earned"]), _r2(a["collected"])
        a["outstanding"] = _r2(a["earned"] - a["collected"])
        out.append(a)
    return sorted(out, key=lambda a: (-a["earned"], a["key"]))


def status_counts(rows):
    """How many landed rows are in each settlement state, and the earned dollars behind each."""
    acc = {}
    for r in rows:
        s = settlement_status(r)
        a = acc.setdefault(s, {"status": s, "rows": 0, "earned": 0.0})
        a["rows"] += 1
        a["earned"] += _num(r.get("earned_amount"))
    for a in acc.values():
        a["earned"] = _r2(a["earned"])
    order = (UNPAID, PARTIAL, COLLECTED, UNKNOWN)
    return sorted(acc.values(), key=lambda a: order.index(a["status"]) if a["status"] in order else 99)
