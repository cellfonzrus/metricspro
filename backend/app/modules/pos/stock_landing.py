"""THE one home for "a single-row entry puts standard stock on hand at one store".

WHY THIS FILE EXISTS (owner 2026-10-16: *"add a product to the inventory if a new product is being
entered manually"*). Before it, `pos.inventory_standard` had **no single-row write path at all** —
qty on hand only ever arrived through the CSV importer (`router.import_entity`, entity
`inventory`) or through migration 726's in-database `pos.receive_transfer`. So the register could
create a product and then had no honest way to say "and we have three of them". Opening stock
typed at the till would have become a third expression of the same upsert rule, and a third
expression is the drift the house rules forbid.

What the rule IS, stated once:

* The identity of a stock row is **(org, product, store)** — the table's own unique key. A second
  row for the same triple is not "more stock", it is a duplicate the qty readers would then have to
  choose between.
* A receipt of `n` units **ADDS** to what is already on hand (`MODE_ADD`). A physical count
  **REPLACES** it (`MODE_SET`). Those are different facts about the world, so the caller declares
  which one it is holding rather than the writer guessing from whether a row happened to exist.
* Negative stock is never landed by this path. "We are short" is an adjustment with a reason, not a
  receipt — that is `inventory_integrity_router.adjust_unit`'s job.

SIBLINGS, CHECKED (the design-fix rule — name every other path that answers the same question):

* `router.import_entity(entity="inventory")` — the CSV count importer. It answers a DIFFERENT
  question: a counted sheet, where a product already counted at a store is SKIPPED as a duplicate
  row of the sheet rather than added to. Left alone deliberately; folding the two would turn a
  re-uploaded count file into a doubling.
* `pos.receive_transfer` / `pos.ship_transfer` (mig 726, PL/pgSQL) — stock moving BETWEEN stores,
  inside one transaction with the transfer's own status machine. It is the same upsert arithmetic
  but it cannot dereference a Python helper; `harness_pos_stock_landing.py` §D pins that its
  ON CONFLICT clause still means "add", so a divergence fails the build instead of going unnoticed.
* The serialised side has its own one home already — `inventory_integrity_router.receive_unit`,
  behind THE landing guard (duplicate IMEI / already-sold evidence, index §11b). This module never
  touches `inventory_serial`: a serialised product's opening stock is one unit through there.

PURE. No DB handle, no clock, no org id, no carrier / tenant / product name (RULE TWO).
Proof: `backend/harness_pos_stock_landing.py`.
"""

MODE_ADD = "add"     # a receipt: n units arrived, add them to what is on hand
MODE_SET = "set"     # a count: on hand IS n, whatever the stored figure said
MODES = (MODE_ADD, MODE_SET)

# What a single-row entry may write. `qty_on_hand` is derived by `plan`, never taken from the caller
# unmediated; the rest pass through when given.
WRITABLE = ("qty_on_order", "qty_reserved", "bin_location")

ACTION_INSERT = "insert"
ACTION_UPDATE = "update"
ACTION_NONE = "none"


class StockError(ValueError):
    """A refused landing. The message is written to be shown to the person at the till."""


def clean_qty(value):
    """The quantity a single-row entry may land: a whole number, zero or more.

    A blank is 0 (creating a product you do not yet physically hold is legitimate — the register
    just records no stock). Anything that is not a whole number, or is negative, is REFUSED rather
    than coerced: `int(2.7)` silently losing a unit, or a stray minus sign putting the shelf into
    negative stock, are both worse than an error the cashier can read.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise StockError("quantity must be a whole number")
    if isinstance(value, float):
        if value != int(value):
            raise StockError("quantity must be a whole number")
        value = int(value)
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            raise StockError("quantity must be a whole number") from None
    if not isinstance(value, int):
        raise StockError("quantity must be a whole number")
    if value < 0:
        raise StockError("quantity cannot be negative — record a shortage as an adjustment, "
                         "with its reason, rather than as a receipt")
    return value


def clean_mode(mode):
    m = (mode or MODE_ADD)
    m = m.strip().lower() if isinstance(m, str) else m
    if m not in MODES:
        raise StockError(f"mode must be one of {', '.join(MODES)}")
    return m


def existing_row(rows, product_id, store_code):
    """The ONE stock row for this (product, store), out of whatever the caller read.

    Rows for other products or other stores are ignored, so a caller may pass a whole store's
    stock. More than one row for the same pair is a broken unique key, and is refused rather
    than resolved by picking one — picking one is how half the units disappear.
    """
    pid = (product_id or "").strip()
    sc = (store_code or "").strip()
    hits = [r for r in (rows or [])
            if (r.get("product_id") or "").strip() == pid
            and (r.get("store_code") or "").strip() == sc]
    if len(hits) > 1:
        raise StockError("this product is already counted twice at this store — the duplicate "
                         "stock rows must be merged before more stock can land")
    return hits[0] if hits else None


def on_hand(row):
    """What a stock row says is on hand, as a whole number. A missing or unreadable figure reads
    as 0 — the one place that coercion is right, because the alternative is refusing to receive
    stock over a column nothing has written yet."""
    if not row:
        return 0
    try:
        v = row.get("qty_on_hand")
        return int(v) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def plan(rows, product_id, store_code, qty, mode=MODE_ADD, extra=None):
    """What to write so that (product, store) holds the right number of units.

    Returns `{"action", "row", "before", "after", "mode"}`:

    * `insert` — no stock row exists for the pair yet; `row` is the full insert (org id is the
      caller's to stamp, exactly as every other writer in this module does).
    * `update` — a row exists; `row` carries ONLY the changed fields, keyed by the unique triple.
    * `none`   — nothing would change (a 0-unit receipt onto an existing row). The caller reports
      the current figure instead of writing, so an idempotent retry cannot double anything.

    The arithmetic never reads the row twice and never trusts a client-sent total: `after` is
    derived here from `before` and the declared quantity, which is what makes the two modes
    distinguishable at all.
    """
    pid = (product_id or "").strip()
    sc = (store_code or "").strip()
    if not pid:
        raise StockError("product_id required")
    if not sc:
        raise StockError("store_code required — stock is held AT a store, never org-wide")
    n = clean_qty(qty)
    m = clean_mode(mode)
    row = existing_row(rows, pid, sc)
    before = on_hand(row)
    after = before + n if m == MODE_ADD else n
    fields = {k: v for k, v in (extra or {}).items() if k in WRITABLE and v not in (None, "")}

    if row is None:
        return {"action": ACTION_INSERT, "mode": m, "before": before, "after": after,
                "row": {"product_id": pid, "store_code": sc, "qty_on_hand": after, **fields}}
    if after == before and not fields:
        return {"action": ACTION_NONE, "mode": m, "before": before, "after": before, "row": {}}
    return {"action": ACTION_UPDATE, "mode": m, "before": before, "after": after,
            "row": {"qty_on_hand": after, **fields},
            "match": {"product_id": pid, "store_code": sc}}


def sentence(planned, product_name=""):
    """One line a cashier can read, saying what the landing did. Never a bare number: the figure
    before and after is what tells them whether their entry was counted twice."""
    name = (product_name or "this product").strip() or "this product"
    a = planned.get("action")
    if a == ACTION_NONE:
        return f"{name}: no change — {planned.get('before', 0)} on hand at this store."
    verb = "received" if planned.get("mode") == MODE_ADD else "counted"
    return (f"{name}: {verb} — on hand at this store goes from "
            f"{planned.get('before', 0)} to {planned.get('after', 0)}.")
