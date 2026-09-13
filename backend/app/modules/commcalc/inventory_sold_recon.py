"""INVENTORY vs SOLD — is a device still on the shelf, or was it already sold?

Owner request 2026-09-12, verbatim: *"i also need to build something which can upload this report and
check against the sales by product to see if the item in inventory is already sold or not and if it is
alsready sold then it should report those items whic are soled with imei to be adjusted and also those
items which are in oventory to be cleared out of the inventory."*

DUPLICATE CHECK (build gate, CLAUDE.md). Searched the index §11 (inventory & aging), §16 (by table) and
§17 (by endpoint) before writing a line. Two neighbours exist and NEITHER answers this question:
  · `GET /account/inventory-recon` (§4) is a BALANCE-SHEET tie-out — emailed report ↔ unsold-phone
    ledger ↔ manual ↔ effective, in DOLLARS per store. It never looks at a device.
  · `GET /commcalc/device-cost-recon` (§11) reconciles what a device COST across four sources and flags
    IMEI overlap between them. It answers "whose number is right", not "is this unit still here".
This is the third question — PRESENCE — and it is the one nothing answered.

REUSED, NOT REBUILT:
  · `device_cost_recon.device_key` — THE canonical cross-source device key (mig 009's spelling: trim →
    drop a trailing '.0' → upper-case, alphanumerics preserved, junk refused). A second normalizer
    would join devices this one refuses, or miss ones it matches. It is INJECTED, not imported, so this
    module stays pure and the harness proves the real pairing.
  · `commcalc.raw_sales` (+ mig 1004's `quantity`) as the sold side and
    `commcalc.inventory_aging_device` (+ mig 1004's `status`) as the on-hand side. No new table.

THE RULE THAT MAKES THIS WORTH BUILDING — NET QUANTITY, NOT PRESENCE IN THE SALES FILE.
A refund line carries a NEGATIVE quantity (mig 1004 added the column for exactly this). A unit that was
sold and then returned nets to zero and is LEGITIMATELY back on the shelf. Measured on the owner's own
first file: a naive "appears in sales ⇒ sold" reads 18 phantom units; the true figure is 10, and 8 of
those 18 are sold-then-refunded stock that is genuinely on hand. Reporting all 18 would send someone to
write off $5,000 of inventory that is sitting in front of them.

AND AN ORDERED UNIT IS NOT A MISSING UNIT. A row whose status is an ORDER (On Order / On Back Order) is
not physically present at all, so it can be neither cleared nor missing. Recognised by SHAPE — a status
naming an order — not by a list of vendor spellings, so a POS that words it differently still works.

THIS BOOKS NOTHING. It is a read-only REPORT: it writes no adjustment, clears no row and moves no money.
A defect it finds in live data is reported, never quietly corrected (CLAUDE.md).

PURE / stdlib only — proof harness `backend/harness_inventory_sold_recon.py`.
"""
import re

# A status that names an ORDER describes a unit that has not arrived. Shape, not a vendor word list.
#
# The alternation is load-bearing: a plain `\border\b` misses 'backorder' written as ONE word, which
# is how plenty of systems spell it — and a unit that has not arrived would then be reported as stock
# to write off. It must NOT over-match either: 'disorder' and 'recorder' both contain the letters and
# neither is an order status, so the prefix is an explicit short list rather than "anything ending in
# order". Caught by running the case rather than assuming the simple regex covered it.
_ORDER_STATUS = re.compile(r"\b(?:back|re|pre)?[-\s]?order(?:ed|s|ing)?\b", re.I)

# What the report calls each finding. Both are the owner's own two asks, kept as separate buckets
# because they are separate actions by separate people.
SOLD_NOT_CLEARED = "sold_not_cleared"      # "items which are in oventory to be cleared out"
SOLD_NO_INVENTORY = "sold_no_inventory"    # "those items whic are soled with imei to be adjusted"


def _num(v):
    """A float from anything a spreadsheet cell can hold, else 0.0. Never raises."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")      # accounting negatives
    if neg:
        s = s[1:-1]
    try:
        return -float(s) if neg else float(s)
    except ValueError:
        return 0.0


def status_is_present(status):
    """Is a unit with this status PHYSICALLY on the shelf?

    An ordered / back-ordered unit is not, so it is neither clearable nor missing — including it would
    report a unit nobody has yet as inventory to write off. A blank status is read as present: the
    feed simply did not say, and refusing to consider it would silently drop rows from the report.
    """
    return not _ORDER_STATUS.search(str(status or ""))


def net_sold(sale_rows, key_of, qty_field="quantity", key_field="serial_1"):
    """`{device_key: net_units_sold}` over the sale lines, refunds NETTED OFF.

    A line with no usable device key is skipped and counted by `unkeyed_sales` below rather than
    guessed at — an un-keyed line can never prove a specific unit was sold.

    A line with NO quantity column at all counts as one unit, because that is what a line IS on a feed
    that does not carry quantity; a feed that DOES carry it (mig 1004) is the one where refunds matter,
    and there the negative is honoured exactly as the file spells it.
    """
    out = {}
    for r in sale_rows or []:
        k = key_of((r or {}).get(key_field))
        if not k:
            continue
        raw = (r or {}).get(qty_field)
        q = _num(raw) if raw not in (None, "") else 1.0
        out[k] = out.get(k, 0.0) + q
    return out


def unkeyed_sales(sale_rows, key_of, key_field="serial_1"):
    """How many sale lines carried no usable device key. REPORTED, never hidden: it is the honest
    ceiling on what this reconciliation can see."""
    return sum(1 for r in (sale_rows or []) if not key_of((r or {}).get(key_field)))


def on_hand_index(inv_rows, key_of):
    """`{device_key: row}` for the units this snapshot says are physically present.

    A row is present when its `on_hand` flag is not explicitly false (mig 294) AND its status does not
    name an order. Duplicate keys keep the FIRST row — `inventory_aging_device` is upserted one row per
    (org, imei) (mig 216), so a duplicate here means the caller passed an unfiltered read, and picking
    a row at random would make the report unstable between runs.
    """
    out = {}
    for r in inv_rows or []:
        row = r or {}
        if row.get("on_hand") is False:
            continue
        if not status_is_present(row.get("status")):
            continue
        k = key_of(row.get("imei") or row.get("serial") or row.get("serial_1"))
        if not k or k in out:
            continue
        out[k] = row
    return out


def reconcile(sale_rows, inv_rows, key_of, qty_field="quantity", sale_key_field="serial_1"):
    """The report. Returns `{"rows": [...], "totals": {...}}` — and books nothing.

    Two findings, the owner's own two asks:

    · SOLD_NOT_CLEARED — the unit is on the shelf according to the snapshot AND nets out as sold. This
      is stock to clear, and `cost` is what clearing it is worth. THE PHANTOM.
    · SOLD_NO_INVENTORY — the unit nets out as sold but the snapshot has no present row for it. This
      is the "sold with imei to be adjusted" side: nothing to clear, but the sale exists and the
      inventory never knew about the unit.

    A unit on the shelf that nets to ZERO is NOT reported. That is the sold-then-refunded case and it
    is the difference between a usable report and one that tells someone to write off stock they can
    see. `net_units` is carried on every row so the reader can check that themselves.
    """
    sold = net_sold(sale_rows, key_of, qty_field, sale_key_field)
    present = on_hand_index(inv_rows, key_of)
    rows = []

    for k, row in present.items():
        n = sold.get(k, 0.0)
        if n <= 0:
            continue                       # never sold, or sold and returned — genuinely on hand
        rows.append({
            "finding": SOLD_NOT_CLEARED, "device_key": k,
            "sku": row.get("sku"), "item": row.get("item"), "store": row.get("store"),
            "status": row.get("status"), "received_date": row.get("received_date"),
            "as_of_date": row.get("as_of_date"),
            "net_units": n,
            "cost": _num(row.get("total_cost")) or _num(row.get("unit_cost")),
        })

    for k, n in sold.items():
        if n <= 0 or k in present:
            continue
        rows.append({
            "finding": SOLD_NO_INVENTORY, "device_key": k,
            "sku": None, "item": None, "store": None, "status": None,
            "received_date": None, "as_of_date": None,
            "net_units": n, "cost": 0.0,
        })

    # Deterministic order: biggest exposure first, then by key so two runs of the same data agree.
    rows.sort(key=lambda r: (-r["cost"], r["device_key"]))

    clear = [r for r in rows if r["finding"] == SOLD_NOT_CLEARED]
    adjust = [r for r in rows if r["finding"] == SOLD_NO_INVENTORY]
    return {
        "rows": rows,
        "totals": {
            "to_clear": len(clear),
            "to_clear_cost": round(sum(r["cost"] for r in clear), 2),
            "to_adjust": len(adjust),
            "on_hand_considered": len(present),
            "devices_sold": sum(1 for n in sold.values() if n > 0),
            "unkeyed_sale_lines": unkeyed_sales(sale_rows, key_of, sale_key_field),
        },
    }
