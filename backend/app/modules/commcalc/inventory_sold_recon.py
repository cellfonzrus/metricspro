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


# ── THE SECOND SOLD-SOURCE: the ACTIVATION feed (owner directive 2026-09-20) ────────────────────
# Owner: "inventory report should also auto check itself with the activation report to see which item
# has been sold but not rung out properly from the inventory."
#
# The activation feed is the platform's existing b2b Activation Details capture (raw_custom_import,
# resolved per DEVICE by router._cr_resolve_activation_details — reused, never a sibling read). Its
# device key is `Serial#` (the IMEI), the SAME cross-source key the sales side pairs on. A row that
# carries NO serial but a mobile number is paired THROUGH THE SALES LINE for that number, and the row
# says so (`pairing`) — it is never guessed. A row with neither, or whose number maps to several devices
# or to no sale line at all, is reported UNPAIRABLE with the reason; it counts toward nothing.
#
# The finding this adds is the owner's own sentence: ACTIVATED_NOT_RUNG_OUT — a unit on the shelf that
# appears in the activation feed and in NO sale line at all. A unit that is also in sales is already
# SOLD_NOT_CLEARED (the activation is added as evidence on that row, never a second row — a device is
# one finding). A unit sold and REFUNDED is genuinely on hand when nothing else says otherwise — but
# an activation on it says it went to a customer, so that case IS reported as activated-not-rung-out
# with `sale_state` = sold_then_refunded (and counted, `activated_then_refunded`).
ACTIVATED_NOT_RUNG_OUT = "activated_not_rung_out"   # "sold but not rung out properly from the inventory"
SOURCE_SALES = "sales"
SOURCE_ACTIVATION = "activation"
PAIR_DEVICE_KEY = "device_key"                      # the feed carried the serial / IMEI
PAIR_MOBILE_VIA_SALES = "mobile_number_via_sales"   # no serial; paired through a sale line with that number
UNPAIR_NO_KEY = "no device key and no mobile number on the activation line"
UNPAIR_NO_SALE = "no sale line carries this mobile number, so no unit can be named"
UNPAIR_AMBIGUOUS = "the mobile number appears on sale lines for several devices"
EVIDENCE_CAP = 5


def mobile_key(v):
    """A comparable mobile number: the last 10 digits, or None when fewer than 10 are present. A
    country code or formatting never splits one number into two keys."""
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else None


def sales_evidence(sale_rows, key_of, key_field="serial_1", id_field="trans_id"):
    """`{device_key: [trans ids]}` — the sale lines that name a device (capped), so a finding can cite
    its lines instead of asserting them."""
    out = {}
    for r in sale_rows or []:
        k = key_of((r or {}).get(key_field))
        if not k:
            continue
        lst = out.setdefault(k, [])
        tid = str((r or {}).get(id_field) or "").strip()
        if tid and tid not in lst and len(lst) < EVIDENCE_CAP:
            lst.append(tid)
    return out


def sales_mobile_index(sale_rows, key_of, key_field="serial_1", mobile_field="mdn", id_field="trans_id"):
    """`{mobile_key: {device_key: via}}` — the units a mobile number can name through the sales file:
    a sale line that carries BOTH the number and a device key (via 'same line'), else the device
    lines of the SAME TRANSACTION as a line that carries the number (via 'same transaction' — the
    plan line names the number, the handset line names the serial). Nothing else pairs."""
    out = {}
    by_txn_keys, by_txn_mobiles = {}, {}
    for r in sale_rows or []:
        r = r or {}
        k, m = key_of(r.get(key_field)), mobile_key(r.get(mobile_field))
        tid = str(r.get(id_field) or "").strip()
        if k and m:
            out.setdefault(m, {}).setdefault(k, "same line")
        if tid and k:
            by_txn_keys.setdefault(tid, set()).add(k)
        if tid and m:
            by_txn_mobiles.setdefault(tid, set()).add(m)
    for tid, mobiles in by_txn_mobiles.items():
        for m in mobiles:
            for k in by_txn_keys.get(tid, ()):
                out.setdefault(m, {}).setdefault(k, "same transaction")
    return out


def line_pairings(rows, key_of, sale_rows=None, serial_field="serial", mobile_field="mdn", id_field="trans_id",
                  sale_key_field="serial_1", sale_mobile_field="mdn", mobile_index=None):
    """THE ONE device-pairing rule, per LINE (Stage D, owner 2026-09-20 — "link the different reports
    automatically with each other with common columns"): pair every line of a report to a device key
    by its own serial / IMEI (`PAIR_DEVICE_KEY`), else THROUGH a sale line that carries its mobile
    number (`PAIR_MOBILE_VIA_SALES`, via 'same line' / 'same transaction' — `sales_mobile_index`), else
    say why it cannot be paired (`UNPAIR_*`; an ambiguous number lists its candidates). Nothing is
    guessed. `activation_index` (the inventory auto-check) and `report_links` (the Stage-4 report
    links) both fold THIS list — a second copy of the rule would pair what this one refuses.

    Returns one dict per input line, in input order:
      {"line": <trans id / activation# / row n>, "key": device_key | None, "pairing": PAIR_* | None,
       "via": 'same line' | 'same transaction' | None, "mobile": mobile_key | None, "serial": str | None,
       "reason": UNPAIR_* | None, "candidates": [device keys] (ambiguous only)}
    `mobile_index` = a prebuilt `sales_mobile_index` (else built lazily from `sale_rows` on first need)."""
    out = []
    mob_idx = mobile_index
    for i, r in enumerate(rows or []):
        r = r or {}
        line = str(r.get(id_field) or r.get("activation_no") or f"row {i + 1}").strip()
        serial = r.get(serial_field)
        mob = mobile_key(r.get(mobile_field))
        serial_txt = str(serial or "").strip() or None
        k = key_of(serial)
        if k:
            out.append({"line": line, "key": k, "pairing": PAIR_DEVICE_KEY, "via": None, "mobile": mob,
                        "serial": serial_txt, "reason": None})
            continue
        if not mob:
            out.append({"line": line, "key": None, "pairing": None, "via": None, "mobile": None,
                        "serial": serial_txt, "reason": UNPAIR_NO_KEY})
            continue
        if mob_idx is None:
            mob_idx = sales_mobile_index(sale_rows, key_of, sale_key_field, sale_mobile_field)
        keys = mob_idx.get(mob) or {}
        if len(keys) == 1:
            k, via = next(iter(keys.items()))
            out.append({"line": line, "key": k, "pairing": PAIR_MOBILE_VIA_SALES, "via": via, "mobile": mob,
                        "serial": None, "reason": None})
        elif not keys:
            out.append({"line": line, "key": None, "pairing": None, "via": None, "mobile": mob,
                        "serial": None, "reason": UNPAIR_NO_SALE})
        else:
            out.append({"line": line, "key": None, "pairing": None, "via": None, "mobile": mob,
                        "serial": None, "reason": UNPAIR_AMBIGUOUS, "candidates": sorted(keys)})
    return out


def activation_index(activation_rows, key_of, sale_rows=None, serial_field="serial", mobile_field="mdn",
                     id_field="trans_id", sale_key_field="serial_1", sale_mobile_field="mdn"):
    """Pair every activation line to a device key — by its own serial, else through a sale line that
    carries its mobile number — and say how. Returns
      {"by_key": {device_key: evidence}, "unpairable": [...], "rows": n,
       "paired_by_key": n, "paired_by_mobile": n, "carries_device_key": bool, "carries_mobile": bool}
    An evidence dict is {"source": "activation", "line": <trans id / activation# / row>, "pairing": ...,
    "mobile": ..., "date": ...}. First line per device wins (deterministic).

    A FOLD of `line_pairings` (the one rule) — this function adds only the per-device evidence shape
    the inventory reconciliation reads; it decides nothing about pairing itself."""
    by_key, unpairable = {}, []
    n_key = n_mob = 0
    has_serial = has_mobile = False
    rows = list(activation_rows or [])
    pairs = line_pairings(rows, key_of, sale_rows, serial_field, mobile_field, id_field, sale_key_field, sale_mobile_field)
    for r, p in zip(rows, pairs):
        r = r or {}
        if p["serial"]:
            has_serial = True
        if p["mobile"]:
            has_mobile = True
        if p["pairing"] == PAIR_DEVICE_KEY:
            n_key += 1
            by_key.setdefault(p["key"], {"source": SOURCE_ACTIVATION, "line": p["line"], "pairing": PAIR_DEVICE_KEY,
                                         "mobile": p["mobile"], "date": str(r.get("trans_date") or "")[:10] or None,
                                         "bucket": r.get("bucket")})
        elif p["pairing"] == PAIR_MOBILE_VIA_SALES:
            n_mob += 1
            by_key.setdefault(p["key"], {"source": SOURCE_ACTIVATION, "line": p["line"], "pairing": PAIR_MOBILE_VIA_SALES,
                                         "via": p["via"], "mobile": p["mobile"], "date": str(r.get("trans_date") or "")[:10] or None,
                                         "bucket": r.get("bucket")})
        else:
            u = {"line": p["line"], "reason": p["reason"], "mobile": p["mobile"], "serial": p["serial"]}
            if p.get("candidates") is not None:
                u["candidates"] = p["candidates"]
            unpairable.append(u)
    return {"by_key": by_key, "unpairable": unpairable, "rows": len(rows),
            "paired_by_key": n_key, "paired_by_mobile": n_mob,
            "carries_device_key": has_serial, "carries_mobile": has_mobile}


def reconcile(sale_rows, inv_rows, key_of, qty_field="quantity", sale_key_field="serial_1",
              activation_rows=None, mobile_field="mdn"):
    """The report. Returns `{"rows": [...], "totals": {...}, "activations": {...}}` — and books nothing.

    Three findings — the owner's two asks of 2026-09-12 and the auto-check of 2026-09-20:

    · SOLD_NOT_CLEARED — the unit is on the shelf according to the snapshot AND nets out as sold. This
      is stock to clear, and `cost` is what clearing it is worth. THE PHANTOM.
    · SOLD_NO_INVENTORY — the unit nets out as sold but the snapshot has no present row for it. This
      is the "sold with imei to be adjusted" side: nothing to clear, but the sale exists and the
      inventory never knew about the unit.
    · ACTIVATED_NOT_RUNG_OUT — the unit is on the shelf, the ACTIVATION feed names it, and either NO
      sale line does or its sale was REFUNDED back into stock (`sale_state`): activated, never rung
      out of inventory properly. Each such row carries its evidence (which activation line, and
      whether it was paired by IMEI or through a sale line's mobile number).

    A unit on the shelf that nets to ZERO is NOT reported. That is the sold-then-refunded case and it
    is the difference between a usable report and one that tells someone to write off stock they can
    see. `net_units` is carried on every row so the reader can check that themselves. `activation_rows`
    is None when no feed was read (the report says so) and [] when the feed is loaded and empty.
    """
    sold = net_sold(sale_rows, key_of, qty_field, sale_key_field)
    present = on_hand_index(inv_rows, key_of)
    cites = sales_evidence(sale_rows, key_of, sale_key_field)
    act = activation_index(activation_rows or [], key_of, sale_rows, mobile_field=mobile_field,
                           sale_mobile_field=mobile_field)
    by_act = act["by_key"]
    rows = []
    activated_then_refunded = 0

    for k, row in present.items():
        n = sold.get(k, 0.0)
        base = {
            "device_key": k,
            "sku": row.get("sku"), "item": row.get("item"), "store": row.get("store"),
            "status": row.get("status"), "received_date": row.get("received_date"),
            "as_of_date": row.get("as_of_date"),
            "cost": _num(row.get("total_cost")) or _num(row.get("unit_cost")),
        }
        if n > 0:
            ev = [{"source": SOURCE_SALES, "line": t, "pairing": PAIR_DEVICE_KEY} for t in cites.get(k, [])]
            if k in by_act:
                ev.append(by_act[k])      # the activation is EVIDENCE on the one finding, never a second row
            rows.append({**base, "finding": SOLD_NOT_CLEARED, "net_units": n, "evidence": ev,
                         "also_activated": k in by_act})
            continue
        if k in by_act:
            # activated, and either NO sale line names the unit or its sale was refunded back into
            # stock: either way the feed says it went to a customer and the shelf says it is here
            refunded = k in sold
            if refunded:
                activated_then_refunded += 1
            ev = [{"source": SOURCE_SALES, "line": t, "pairing": PAIR_DEVICE_KEY, "note": "sold and refunded (net 0)"}
                  for t in cites.get(k, [])] + [by_act[k]]
            rows.append({**base, "finding": ACTIVATED_NOT_RUNG_OUT, "net_units": n if refunded else 0.0,
                         "evidence": ev, "also_activated": True, "pairing": by_act[k]["pairing"],
                         "sale_state": "sold_then_refunded" if refunded else "no_sale_line"})
            continue
        # else: never sold (or sold and returned) and never activated — genuinely on hand

    for k, n in sold.items():
        if n <= 0 or k in present:
            continue
        rows.append({
            "finding": SOLD_NO_INVENTORY, "device_key": k,
            "sku": None, "item": None, "store": None, "status": None,
            "received_date": None, "as_of_date": None,
            "net_units": n, "cost": 0.0,
            "evidence": [{"source": SOURCE_SALES, "line": t, "pairing": PAIR_DEVICE_KEY} for t in cites.get(k, [])],
            "also_activated": k in by_act,
        })

    # Deterministic order: biggest exposure first, then by key so two runs of the same data agree.
    rows.sort(key=lambda r: (-r["cost"], r["device_key"]))

    clear = [r for r in rows if r["finding"] == SOLD_NOT_CLEARED]
    adjust = [r for r in rows if r["finding"] == SOLD_NO_INVENTORY]
    activated = [r for r in rows if r["finding"] == ACTIVATED_NOT_RUNG_OUT]
    return {
        "rows": rows,
        "totals": {
            "to_clear": len(clear),
            "to_clear_cost": round(sum(r["cost"] for r in clear), 2),
            "to_adjust": len(adjust),
            "on_hand_considered": len(present),
            "devices_sold": sum(1 for n in sold.values() if n > 0),
            "unkeyed_sale_lines": unkeyed_sales(sale_rows, key_of, sale_key_field),
            # the activation auto-check (owner 2026-09-20)
            "activated_not_rung_out": len(activated),
            "activated_not_rung_out_cost": round(sum(r["cost"] for r in activated), 2),
            "activated_by_mobile": sum(1 for r in activated if r["pairing"] == PAIR_MOBILE_VIA_SALES),
            "activated_then_refunded": activated_then_refunded,
            "activations_considered": act["rows"],
            "activations_paired_by_key": act["paired_by_key"],
            "activations_paired_by_mobile": act["paired_by_mobile"],
            "activations_unpairable": len(act["unpairable"]),
        },
        "activations": {
            "present": activation_rows is not None,
            "rows": act["rows"],
            "carries_device_key": act["carries_device_key"],
            "carries_mobile": act["carries_mobile"],
            "unpairable": act["unpairable"][:200],
        },
    }
