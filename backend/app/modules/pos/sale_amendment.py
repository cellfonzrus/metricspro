"""THE one home for editing a sale that is already rung, keeping its number (§POS-12).

Owner 2026-10-29, verbatim: *"im not able to edit the receipt ot add more tiems to teh same
rec3ipt"* — and then, offered re-ringing, a second sale, or editing in place: **edit in place, same
number**.

He was right that it could not be done, and it was never a bug: the register had exactly one write
path for a finished sale (`POST /pos/sales/{id}/void`), `pos.checkout` and `pos.void_sale` were the
only two sale functions in the database (mig 725), and the Sale Log's item table was display-only.

WHAT MAKES AN IN-PLACE EDIT ANSWERABLE. Rewriting a tendered record is the risky option of the
three; it was put to the owner as such and he chose it, so it ships with the thing that makes it
auditable: **nothing is overwritten.** Every edit files a snapshot of the sale as it stood — the
version the customer's printed receipt was struck from — and the sale carries a `revision` that says
how many times it has moved. Any figure that changed can be traced to the edit that changed it.

THE CLASS, NOT THE INSTANCE. Not "the Sale Log needs an edit button". Three facts had no home, and
each is needed by more than one caller, which is why they are here and not in the endpoint:

1. **May this sale be edited** — the Sale Log offering the button, the endpoint allowing it, and the
   till before it saves. One answer, or a sale is editable on one screen and refused on another.
2. **What a line's money comes to** — `extended_price` and `tax_value` were derived in the BROWSER
   only, and `pos.checkout` stores whatever it is handed (mig 725). An edit is exactly the case where
   those must be re-derived rather than trusted, because moving them is the point. The arithmetic
   here was DERIVED from the figures already in `pos.sale_items` and reproduces both live lines to
   the cent — see `line_money`.
3. **What changed between two versions** — the audit row, the confirmation the cashier sees, and the
   reprint that says which revision it is.

ONE CART VOCABULARY. §POS-01 already owns what a cart line IS, so this module spells no line field of
its own: it dereferences `held_sale.LINE_FIELDS` and `held_sale.clean_cart`. A line loaded for
editing and a line restored from a suspended cart are therefore the same shape arriving at one write.

TAX: EACH LINE KEEPS ITS OWN RATE. Unlike a suspended cart (§POS-01.4, which re-derives the rate on
restore because that sale has not happened yet), an edit touches a sale that legally DID happen, on
its date, at the rate in force then. An existing line keeps its stored rate; only a newly added line
takes the store's current one, which the register supplies. Re-deriving every rate would restate the
tax on a sale whose tax was already struck and, for an older sale, remitted.

WHY THERE IS A WINDOW. An edit changes a day's takings. The window is how far back that is allowed,
and it is DECLARED per org (`pos_settings.sale_reopen_window_days`, house default
`HOUSE_WINDOW_DAYS`) rather than decided here — RULE TWO: no tenant's answer lives in code.

PURE. No DB handle, no clock (the caller stamps `now`), no org id, no tenant, carrier or product
name. Proof: `backend/harness_pos_sale_amendment.py`.
"""
from datetime import datetime, timezone

from . import held_sale as _hs

#: The house answer to "how many days back may a rung sale still be edited", when the org has
#: declared none. One day — the sale's own business day and no further, so a figure a manager has
#: already read does not move under them. A tenant widens it with a config row, never a code change.
HOUSE_WINDOW_DAYS = 1

#: The setting an org declares its own window in. Named here so the endpoint reads the key from the
#: one home instead of spelling it beside its own default.
WINDOW_SETTING = "sale_reopen_window_days"

#: The order the write happens in, and the safety argument for it. Mig 725's inventory trigger fires
#: on `sale_items`, so stock follows the lines: the old lines are REVERSED and the new ones applied,
#: which conserves stock by construction instead of by a hand-computed delta. The snapshot is filed
#: FIRST, so a failure anywhere after it still leaves the original version on record.
STEPS = ("file_snapshot", "reverse_old_lines", "apply_new_lines", "restate_totals")


class AmendmentError(ValueError):
    """A refused edit. The message is written to be shown at the till."""


# ── when a sale may be edited ────────────────────────────────────────────────────────────────────
def _when(value):
    """A timestamp as an aware datetime, or `None` when it cannot be read. A date that will not
    parse is never treated as recent — `refusal` turns `None` into a refusal."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def declared_window(value):
    """The declared window as a whole number of days. A blank or unreadable declaration falls back
    to the house default rather than to "forever": an unreadable setting must never WIDEN what may
    be rewritten."""
    if value in (None, ""):
        return HOUSE_WINDOW_DAYS
    try:
        n = int(value)
    except (TypeError, ValueError):
        return HOUSE_WINDOW_DAYS
    return max(n, 0)


def age_days(sale, now=None):
    """How many whole days ago this sale was rung, or `None` when its date cannot be read."""
    rung = _when((sale or {}).get("created_at"))
    at = _when(now) or datetime.now(timezone.utc)
    if rung is None:
        return None
    return max((at.date() - rung.date()).days, 0)


def refusal(sale, now=None, window_days=None):
    """Why this sale may NOT be edited, or `None` when it may.

    A sentence rather than a code: every caller shows it to a cashier, and a second mapping from
    code to sentence is a second chance for two screens to word one refusal differently.
    """
    win = declared_window(window_days)
    s = sale or {}
    num = s.get("transaction_id") or "?"
    if not s.get("id"):
        return "That sale could not be found."
    if s.get("status") == "voided" or s.get("voided_at"):
        return (f"Transaction #{num} is voided — its items are already back on hand, so it cannot "
                "be edited. Ring a new sale instead.")
    if s.get("status") not in (None, "", "completed"):
        return f"Transaction #{num} is {s.get('status')}, and only a completed sale can be edited."
    age = age_days(s, now)
    if age is None:
        return (f"Transaction #{num} does not carry a readable date, so there is no way to tell "
                "whether it is still within the window for editing.")
    if age > win:
        day = "day" if win == 1 else "days"
        return (f"Transaction #{num} was rung {age} days ago, and a sale can only be edited within "
                f"{win} {day}. Editing it now would change takings for a day that has already been "
                "reported. Ring the extra items as their own sale instead.")
    return None


# ── what a line's money comes to (the one home for the arithmetic) ───────────────────────────────
def _num(v):
    if v in (None, "", False) and not isinstance(v, (int, float)):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def line_money(line):
    """ONE line with its `tax_value` and `extended_price` RE-DERIVED from the agreed figures.

    THE DERIVATION, and why this is a one home rather than a second opinion. Mig 725 documents
    `extended_price` as `(unit_price − discount + per_unit_tax) × qty`, tax-inclusive, and `tax_value`
    as the per-unit tax times the quantity. Reproduced against the two lines actually stored for
    transaction 4605: a $175.00 handset at 0.08875 gives $15.53 / $190.53, and a $10.00 case
    discounted $1.00 at quantity 2 gives $1.60 / $19.60 — both to the cent.

    Whatever `extended_price` or `tax_value` the caller sent is DISCARDED. An edit exists to move
    those figures, so a client-supplied total is the one value that must not survive it.
    """
    out = dict(line or {})
    qty = int(_num(out.get("qty")) or 1)
    net_unit = _num(out.get("unit_price")) - _num(out.get("discount"))
    per_unit_tax = net_unit * _num(out.get("tax_rate"))
    out["qty"] = qty
    out["tax_value"] = round(per_unit_tax * qty, 2)
    out["extended_price"] = round((net_unit + per_unit_tax) * qty, 2)
    return out


def priced(lines):
    """Every line, re-priced and cleaned into the §POS-01 cart vocabulary.

    An empty set is REFUSED: emptying a sale is a void, and the void path is the one that puts stock
    back and leaves a record. Letting an edit do it would be a second, silent way to cancel a sale.
    """
    rows = lines or []
    if not rows:
        raise AmendmentError("A sale needs at least one item. To cancel it, void it instead.")
    try:
        return [line_money(l) for l in _hs.clean_cart(rows)]
    except _hs.HeldSaleError as e:
        raise AmendmentError(f"That sale cannot be saved: {e}") from None


def totals(lines):
    """The sale's own figures, as the sum of its lines — never carried as values a client could
    contradict. `subtotal` is tax-exclusive and net of discounts, matching mig 725's column
    comment, so `subtotal + tax_total == total` holds by construction."""
    rows = [line_money(l) for l in (lines or [])]
    subtotal = round(sum((_num(l.get("unit_price")) - _num(l.get("discount"))) * int(_num(l.get("qty")) or 1)
                         for l in rows), 2)
    discount_total = round(sum(_num(l.get("discount")) * int(_num(l.get("qty")) or 1) for l in rows), 2)
    tax_total = round(sum(_num(l.get("tax_value")) for l in rows), 2)
    return {"subtotal": subtotal, "discount_total": discount_total,
            "tax_total": tax_total, "total": round(subtotal + tax_total, 2)}


# ── the lines loaded for editing ─────────────────────────────────────────────────────────────────
def cart_from(items):
    """The sale's stored lines, as the register's cart, ready to edit.

    Cleaned by `held_sale.clean_cart` — the §POS-01 vocabulary — so this is the same shape a
    suspended hold restores into. `tax_rate` is deliberately CARRIED (see the module note): this
    sale happened at that rate.
    """
    rows = items or []
    if not rows:
        raise AmendmentError("That sale has no items, so there is nothing to edit.")
    kept = [{k: v for k, v in dict(it or {}).items() if k in _hs.LINE_FIELDS} for it in rows]
    try:
        return _hs.clean_cart(kept)
    except _hs.HeldSaleError as e:
        raise AmendmentError(f"That sale cannot be edited: {e}") from None


# ── what changed ─────────────────────────────────────────────────────────────────────────────────
def line_key(line):
    """What identifies a line across two versions of a sale.

    A SERIALISED unit is its serial, not its product: two handsets of one model are one product and
    two units, so keying on the product would cancel a swapped IMEI out and the stock move would
    never see it. Everything else is its product.
    """
    l = line or {}
    serial = str(l.get("serial_number") or "").strip()
    return ("serial", serial) if serial else ("product", str(l.get("product_id") or "").strip())


_COMPARED = ("qty", "unit_price", "discount", "tax_rate", "description", "product_type")


def _differs(a, b):
    for k in _COMPARED:
        if k in ("qty",):
            if int(_num((a or {}).get(k)) or 1) != int(_num((b or {}).get(k)) or 1):
                return True
        elif k in ("unit_price", "discount", "tax_rate"):
            if round(_num((a or {}).get(k)), 6) != round(_num((b or {}).get(k)), 6):
                return True
        elif str((a or {}).get(k) or "") != str((b or {}).get(k) or ""):
            return True
    return False


def diff(before, after):
    """What changed between two versions of a sale's lines.

    `{"added", "removed", "changed", "any", "summary"}`, where `changed` carries `{"was", "now"}` per
    line. A quantity or price change is a CHANGE, not an add plus a remove — the audit row and the
    cashier's confirmation both have to read as what the cashier did.
    """
    old = {line_key(l): l for l in (before or [])}
    new = {line_key(l): l for l in (after or [])}
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    changed = [{"was": old[k], "now": new[k]} for k in new
               if k in old and _differs(old[k], new[k])]
    parts = []
    if added:
        parts.append(f"{len(added)} line{'' if len(added) == 1 else 's'} added")
    if removed:
        parts.append(f"{len(removed)} removed")
    if changed:
        parts.append(f"{len(changed)} changed")
    return {"added": added, "removed": removed, "changed": changed,
            "any": bool(added or removed or changed),
            "summary": ", ".join(parts) if parts else "no change"}


# ── the snapshot that makes the edit answerable ──────────────────────────────────────────────────
#: Sale columns the snapshot keeps. The FIGURES AS STORED, never re-derived: the point of the
#: snapshot is to record what the customer's receipt said, and re-deriving it would record what it
#: should have said instead.
SNAPSHOT_SALE_FIELDS = ("transaction_id", "store_code", "customer_id", "employee_id", "receipt_type",
                        "status", "subtotal", "discount_total", "tax_total", "total", "balance",
                        "is_activation_sale", "notes", "created_at")


def current_revision(sale):
    """Which revision this sale is on now. A sale rung before the column existed reads `null`, and
    that is revision ONE, not zero — reading it as "no revisions" would make the first edit file a
    snapshot of nothing and leave the original unrecorded."""
    v = (sale or {}).get("revision")
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return max(n, 1)


def next_revision(sale):
    return current_revision(sale) + 1


def snapshot(sale, items):
    """The sale as it stands, for the history table: the revision being superseded, its own stored
    figures, and every line as stored. This is the version the printed receipt was struck from."""
    s = sale or {}
    return {"revision": current_revision(s),
            **{k: s.get(k) for k in SNAPSHOT_SALE_FIELDS},
            "items": [dict(i or {}) for i in (items or [])]}


# ── the whole plan ───────────────────────────────────────────────────────────────────────────────
def sentence(sale, dif, tots, reason=None):
    """One line the till shows before anything is written, so an edit is a decision and not a
    surprise. It says the number is kept AND that the paper the customer already holds is now out of
    date — the owner asked for the same number and gets it, and that is the consequence."""
    if reason:
        return reason
    s = sale or {}
    return (f"Transaction #{s.get('transaction_id') or '?'} will be saved as revision "
            f"{next_revision(s)}: {(dif or {}).get('summary')}, new total "
            f"${(tots or {}).get('total', 0):.2f}. The number does not change, and the version it is "
            "replacing is kept on file. The receipt the customer already has shows the old total, so "
            "print them a new one.")


def plan(sale, before_items, after_items, now=None, window_days=None):
    """The whole answer in one shape: may it be edited, with what priced lines, what totals, what
    revision, what changed, and what the cashier is told.

    `{"allowed", "reason", "items", "totals", "revision", "diff", "snapshot", "sentence"}`.

    A refused plan carries NO items and NO totals. A caller handed figures alongside a refusal could
    write them anyway, and the refusal would be decoration.
    """
    reason = refusal(sale, now=now, window_days=window_days)
    items, tots, dif = [], {}, {"added": [], "removed": [], "changed": [], "any": False,
                                "summary": "no change"}
    if reason is None:
        try:
            items = priced(after_items)
            before = cart_from(before_items)
        except AmendmentError as e:
            reason = str(e)
        else:
            dif = diff(before, items)
            if not dif["any"]:
                reason = (f"Transaction #{(sale or {}).get('transaction_id') or '?'} has no change "
                          "to save.")
            else:
                tots = totals(items)
    if reason is not None:
        items, tots = [], {}
    return {
        "allowed": reason is None,
        "reason": reason,
        "items": items,
        "totals": tots,
        "revision": next_revision(sale) if reason is None else current_revision(sale),
        "diff": dif,
        "snapshot": snapshot(sale, before_items) if reason is None else {},
        "sentence": sentence(sale, dif, tots, reason),
    }
