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

PURE / stdlib only — proof harness `backend/harness_inventory_sold_recon.py` (+ `harness_inventory_integrity.py`
for the commission source and the POS-unit report, §11b). The one import is the lineage REGISTRY (pure data): it
names the commission feed's columns, so this module never spells them.
"""
import re

from app.modules.commcalc import data_lineage_registry as _dlr

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
              activation_rows=None, mobile_field="mdn", commission_rows=None):
    """The report. Returns `{"rows": [...], "totals": {...}, "activations": {...}}` — and books nothing.

    THE COMMISSION REPORT (owner 2026-09-24, §11b) — `commission_rows` given (not None): every
    SOLD_NOT_CLEARED row carries the commission evidence and the customer, and a present unit no sale line
    nets as sold is classified by THE SAME `classify_unit` the POS-unit report uses — SOLD_NO_RECEIPT (kept
    commission, no sale line) or RETURNED_COMMISSION_KEPT (sale nets to zero, commission kept). None (the
    default) → the report is byte-identical to before.

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
    has_comm = commission_rows is not None
    comm = commission_index(commission_rows, key_of) if has_comm else {}
    sdet = sales_detail_index(sale_rows, key_of, qty_field=qty_field, key_field=sale_key_field) if has_comm else {}

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
            extra = {}
            if has_comm:
                if k in comm:
                    ev.append({"source": SOURCE_COMMISSION, "invoices": comm[k]["invoices"], "sold_on": comm[k]["sold_on"],
                               "net_earned": comm[k]["net_earned"], "kept": comm[k]["kept"]})
                extra = {"commission": comm.get(k), "customer": customer_of(sdet.get(k), comm.get(k))}
            rows.append({**base, "finding": SOLD_NOT_CLEARED, "net_units": n, "evidence": ev,
                         "also_activated": k in by_act, **extra})
            continue
        if has_comm and k in comm:
            kind, _bucket, sold_on = classify_unit(sdet.get(k), comm[k], None)
            if kind in (SOLD_NO_RECEIPT, RETURNED_COMMISSION_KEPT):
                ev = [{"source": SOURCE_SALES, "line": t, "pairing": PAIR_DEVICE_KEY} for t in cites.get(k, [])]
                ev.append({"source": SOURCE_COMMISSION, "invoices": comm[k]["invoices"], "sold_on": sold_on,
                           "net_earned": comm[k]["net_earned"], "kept": comm[k]["kept"]})
                if k in by_act:
                    ev.append(by_act[k])
                rows.append({**base, "finding": kind, "net_units": n, "evidence": ev, "also_activated": k in by_act,
                             "commission": comm[k], "customer": customer_of(sdet.get(k), comm[k])})
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
    comm_totals = {}
    if has_comm:
        comm_totals = {"sold_no_receipt": sum(1 for r in rows if r["finding"] == SOLD_NO_RECEIPT),
                       "returned_commission_kept": sum(1 for r in rows if r["finding"] == RETURNED_COMMISSION_KEPT),
                       "commission_devices": len(comm),
                       "commission_devices_kept": sum(1 for d in comm.values() if d["kept"])}
    return {
        "rows": rows,
        "totals": {
            **comm_totals,
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


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE THIRD SOLD-SOURCE — the COMMISSION REPORT — and the POS's OWN units (owner 2026-09-24, index §11b)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, verbatim: *"it is not possible that 383 imei have not ben sold but appering in the invnetory so
# they have to be crashed agains the sales report by invoice and the commission received reports to, it is
# possoble that the receipt was not made and it sold, 2 things it shoudl apprear as a flag and also
# highlight which customer it was sold to from commisison report and have the ability to assign it tot hte
# customer with one click if the imei was sold and commission revceived to move it out of the inventory,
# the flag still stays there till verified by the management that the imei was actually sold, need to do
# this for all tenants as platform wide"*.
#
# ONE ENGINE, EXTENDED — not a sibling. The sold side is the SAME `net_sold` (a refund nets to zero) and the
# SAME injected `device_key`; this section adds (1) the commission report as a sold-source, per device,
# net of chargebacks (`commission_index`), (2) the sale LINES by invoice with their date and customer
# (`sales_detail_index`), (3) ONE per-unit classifier (`classify_unit`) that `integrity` (the POS's own
# units) and `reconcile(..., commission_rows=)` (the landed aging snapshot) BOTH call, and (4) the landing
# guard's pure half (`receive_check`), so the pop-up on receive and the flag after the fact can never
# disagree about what "already sold" means.
#
# THE DEFINITIONS (each a flag kind; a unit gets at most ONE sold-kind, plus `duplicate_on_hand` when its key
# sits on several live units):
#   · SOLD_STILL_ON_HAND       — the sales lines net SOLD (> 0) and the unit is still live.
#   · SOLD_NO_RECEIPT          — the commission was KEPT (net earned > 0 after chargebacks) and NO sale line
#                                names the unit: "the receipt was not made and it sold".
#   · RETURNED_COMMISSION_KEPT — a sale line names the unit, it nets to ZERO (sold and returned), yet the
#                                commission was kept — for REVIEW (the return may be real; the money says not).
#   · RECEIVED_AFTER_SOLD      — a sold-kind above whose unit was RECEIVED after the sale date (a unit landed —
#                                typically by import — for a device the business had already sold).
#   · DUPLICATE_ON_HAND        — the same device key on more than one LIVE unit of the org.
# NOT FLAGGED: returned / reversed units with no kept commission (legitimately back in stock), and units no
# sale line and no commission names. Both are COUNTED in the totals so the whole population is accounted for.
#
# PURE / stdlib; the POS status vocabulary (which statuses are "live") is INJECTED (`is_live`), as the key is.
SOLD_STILL_ON_HAND = "sold_still_on_hand"
SOLD_NO_RECEIPT = "sold_no_receipt"
RETURNED_COMMISSION_KEPT = "returned_commission_kept"
RECEIVED_AFTER_SOLD = "received_after_sold"
DUPLICATE_ON_HAND = "duplicate_on_hand"
INTEGRITY_KINDS = (SOLD_NO_RECEIPT, SOLD_STILL_ON_HAND, RETURNED_COMMISSION_KEPT, RECEIVED_AFTER_SOLD, DUPLICATE_ON_HAND)
SOLD_KINDS = (SOLD_NO_RECEIPT, SOLD_STILL_ON_HAND, RECEIVED_AFTER_SOLD)
SOURCE_COMMISSION = "commission"
# the not-flagged buckets, counted so "383 on hand" is fully accounted for
NOT_FLAGGED_RETURNED = "returned_no_commission_kept"
NOT_FLAGGED_NO_EVIDENCE = "no_sale_no_commission"

# The feed's own chargeback cell ('Yes' / 'Y' / 'True' / '1' / 'X'). Evidence beside the sign, never instead of it.
_TRUTHY = ("y", "yes", "true", "t", "1", "x")


def _flag_set(v):
    return str(v if v is not None else "").strip().lower() in _TRUTHY


def _day(v):
    """'YYYY-MM-DD' from a date / datetime / ISO string, else None (the comparable day of an event)."""
    s = str(v or "").strip()
    return s[:10] if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-" else None


def commission_index(rows, key_of):
    """`{device_key: commission evidence}` over the commission report's component rows, NET OF CHARGEBACKS.

    Which column is the device, the signed money, the chargeback cell, the customer … is the lineage registry's
    COMMISSION_PER_DEVICE_COLUMNS — DEREFERENCED here, never spelled (the lock fails a copy). Per device: rows,
    net_earned (Σ signed earned — the ONLY money summed), earned_gross (Σ positive), charged_back (Σ |negative|),
    kept (net_earned > 0), reversed (a negative line, or the feed's chargeback cell), and customer_name /
    customer_ref / mobile / invoice_no / sold_on / device_name / device_sku / rate_plan / store from the EARLIEST
    positive line (the sale), else the earliest line; `invoices` (capped). A row with no usable device key is
    skipped (it can never prove a specific unit was sold)."""
    c = _dlr.COMMISSION_PER_DEVICE_COLUMNS
    f_dev, f_earn, f_flag, f_day, f_inv = c["device"], c["earned"], c["reversal_flag"], c["sold_on"], c["invoice"]
    fields = (("customer_name", c["customer"]), ("customer_ref", c["customer_ref"]), ("mobile", c["mobile"]),
              ("invoice_no", f_inv), ("device_name", c["device_name"]), ("device_sku", c["device_sku"]),
              ("rate_plan", c["rate_plan"]), ("store", c["store"]))
    out = {}
    for r in rows or []:
        r = r or {}
        k = key_of(r.get(f_dev))
        if not k:
            continue
        e = _num(r.get(f_earn))
        d = out.setdefault(k, {"source": SOURCE_COMMISSION, "rows": 0, "net_earned": 0.0, "earned_gross": 0.0,
                               "charged_back": 0.0, "reversed": False, "invoices": [], "_first": None})
        d["rows"] += 1
        d["net_earned"] += e
        if e > 0:
            d["earned_gross"] += e
        elif e < 0:
            d["charged_back"] += -e
            d["reversed"] = True
        if _flag_set(r.get(f_flag)):
            d["reversed"] = True
        inv = str(r.get(f_inv) or "").strip()
        if inv and inv not in d["invoices"] and len(d["invoices"]) < EVIDENCE_CAP:
            d["invoices"].append(inv)
        # the SALE line: the earliest positive line (ties → first seen), else the earliest line of any sign
        rank = (0 if e > 0 else 1, _day(r.get(f_day)) or "9999-99-99")
        if d["_first"] is None or rank < d["_first"][0]:
            d["_first"] = (rank, r)
    for d in out.values():
        _rank, first = d.pop("_first")
        for name, f in fields:
            v = first.get(f)
            d[name] = (str(v).strip() or None) if v is not None else None
        d["sold_on"] = _day(first.get(f_day))
        d["net_earned"] = round(d["net_earned"], 2)
        d["earned_gross"] = round(d["earned_gross"], 2)
        d["charged_back"] = round(d["charged_back"], 2)
        d["kept"] = d["net_earned"] > 0
    return out


def sales_detail_index(sale_rows, key_of, invoice_customers=None, qty_field="quantity", key_field="serial_1",
                       id_field="trans_id", date_field="trans_date", customer_field="customer", mobile_field="mdn"):
    """`{device_key: sale evidence}` — the sale LINES by invoice that name a device. Net units are exactly
    `net_sold` (the SAME function, so a refund nets to zero here as it does in `reconcile`). Per device:
      net, lines [{invoice, date, qty, customer}] (capped), last_sold_on (latest day of a POSITIVE line),
      customer / mobile / invoice (from the latest positive line; the customer falls back to its invoice
      header via `invoice_customers` {trans_id: customer} — the sales-by-invoice export's own customer)."""
    net = net_sold(sale_rows, key_of, qty_field, key_field)
    inv_cust = invoice_customers or {}
    out = {}
    for r in sale_rows or []:
        r = r or {}
        k = key_of(r.get(key_field))
        if not k:
            continue
        raw = r.get(qty_field)
        q = _num(raw) if raw not in (None, "") else 1.0
        tid = str(r.get(id_field) or "").strip() or None
        day = _day(r.get(date_field))
        cust = str(r.get(customer_field) or "").strip() or (inv_cust.get(tid) if tid else None) or None
        d = out.setdefault(k, {"source": SOURCE_SALES, "net": net.get(k, 0.0), "lines": [], "last_sold_on": None,
                               "customer": None, "mobile": None, "invoice": None, "_last": None})
        if len(d["lines"]) < EVIDENCE_CAP:
            d["lines"].append({"invoice": tid, "date": day, "qty": q, "customer": cust})
        if q > 0:
            rank = day or ""
            if d["_last"] is None or rank >= d["_last"]:
                d["_last"] = rank
                d["last_sold_on"] = day
                d["customer"] = cust or d["customer"]
                d["mobile"] = str(r.get(mobile_field) or "").strip() or d["mobile"]
                d["invoice"] = tid or d["invoice"]
    for d in out.values():
        d.pop("_last", None)
    return out


def classify_unit(sale=None, commission=None, received_on=None):
    """THE per-unit rule (one home — `integrity`, `receive_check` and `reconcile(..., commission_rows=)` all
    call it). `sale` = a `sales_detail_index` entry (or None), `commission` = a `commission_index` entry (or
    None), `received_on` = the day the unit landed. Returns (kind | None, not_flagged_bucket | None, sold_on).

      sales net > 0                                   → SOLD_STILL_ON_HAND
      no sale line, commission kept                   → SOLD_NO_RECEIPT
      sale line nets ≤ 0, commission kept             → RETURNED_COMMISSION_KEPT  (review)
      a sold kind whose unit landed AFTER the sale    → RECEIVED_AFTER_SOLD
      sale line nets ≤ 0 / commission not kept        → not flagged (returned / reversed — back in stock)
      neither source names the unit                   → not flagged
    """
    kept = bool(commission and commission.get("kept"))
    if sale and sale.get("net", 0) > 0:
        kind = SOLD_STILL_ON_HAND
        sold_on = sale.get("last_sold_on") or (commission or {}).get("sold_on")
    elif kept and not sale:
        kind = SOLD_NO_RECEIPT
        sold_on = commission.get("sold_on")
    elif kept and sale:
        return RETURNED_COMMISSION_KEPT, None, commission.get("sold_on")
    elif sale or commission:
        return None, NOT_FLAGGED_RETURNED, None
    else:
        return None, NOT_FLAGGED_NO_EVIDENCE, None
    rec = _day(received_on)
    if rec and sold_on and rec > sold_on:
        return RECEIVED_AFTER_SOLD, None, sold_on
    return kind, None, sold_on


def unit_keys(unit, key_of):
    """The device keys a POS unit answers to: its IMEI, and its serial number when that differs (a unit whose
    serial IS the IMEI and one whose IMEI column holds it are the same device). Primary (IMEI) first."""
    out = []
    for v in ((unit or {}).get("imei"), (unit or {}).get("serial_number")):
        k = key_of(v)
        if k and k not in out:
            out.append(k)
    return out


def _evidence_for(keys, idx):
    for k in keys:
        if k in idx:
            return idx[k]
    return None


def customer_of(sale, commission):
    """Who the unit was sold to — the COMMISSION report first (the carrier's own record, the owner's ask:
    "highlight which customer it was sold to from commisison report"), else the sale line / its invoice.
    Returns {name, mobile, ref, source} or None."""
    if commission and commission.get("customer_name"):
        return {"name": commission["customer_name"], "mobile": commission.get("mobile"),
                "ref": commission.get("customer_ref"), "source": SOURCE_COMMISSION}
    if sale and sale.get("customer"):
        return {"name": sale["customer"], "mobile": sale.get("mobile"), "ref": None, "source": SOURCE_SALES}
    return None


def _unit_day(u):
    # The day the unit ARRIVED is `date_received` — nothing else. `created_at` is when the RECORD was written: a
    # bring-over / import writes it on the import day, so reading it as "received" made every sold unit of a
    # 2026-09-24 bring-over look "received after it was sold" and hid the sold-no-receipt / still-on-hand split
    # (measured live, 383 units, date_received NULL on all). Unknown arrival → received-after-sold is not decided.
    return _day((u or {}).get("date_received"))


def integrity(units, sale_rows, commission_rows, key_of, is_live, invoice_customers=None):
    """THE INVENTORY-INTEGRITY REPORT over the POS's own serialised units. Books nothing, writes nothing.

    `units` = the org's unit rows (id, serial_number, imei, status, store_code, cost, date_received, created_at,
    product_id, …); `is_live(status)` (INJECTED — the POS's vocabulary, not this module's) says which are on
    hand. Returns {"rows": [finding…], "totals": {…}}. One finding per (device key, kind):
      {kind, device_key, unit_id, unit_ids, serial_number, imei, store_code, status, product_id, product_name,
       cost, received_on, sold_on, customer {name, mobile, ref, source}, invoice_no, commission, sale, evidence[]}
    Deterministic order: kind order, then biggest cost, then key."""
    sales = sales_detail_index(sale_rows, key_of, invoice_customers)
    comm = commission_index(commission_rows, key_of)
    live = [u for u in (units or []) if is_live((u or {}).get("status"))]
    by_key = {}
    for u in live:
        for k in unit_keys(u, key_of):
            grp = by_key.setdefault(k, [])
            if all(x is not u for x in grp):
                grp.append(u)
    rows, seen_dup, unkeyed = [], set(), 0
    buckets = {NOT_FLAGGED_RETURNED: 0, NOT_FLAGGED_NO_EVIDENCE: 0}
    for u in live:
        keys = unit_keys(u, key_of)
        if not keys:
            unkeyed += 1
            continue
        sale, com = _evidence_for(keys, sales), _evidence_for(keys, comm)
        kind, bucket, sold_on = classify_unit(sale, com, _unit_day(u))
        if kind:
            ev = [dict(line, source=SOURCE_SALES) for line in (sale or {}).get("lines", [])]
            if com:
                ev.append({"source": SOURCE_COMMISSION, "invoices": com["invoices"], "sold_on": com["sold_on"],
                           "net_earned": com["net_earned"], "charged_back": com["charged_back"], "kept": com["kept"]})
            rows.append({"device_key": keys[0], "kind": kind, "unit_id": u.get("id"), "unit_ids": [u.get("id")],
                         "serial_number": u.get("serial_number"), "imei": u.get("imei"),
                         "store_code": u.get("store_code"), "status": u.get("status"),
                         "product_id": u.get("product_id"), "product_name": u.get("product_name"),
                         "cost": _num(u.get("cost")), "received_on": _unit_day(u), "sold_on": sold_on,
                         "customer": customer_of(sale, com),
                         "invoice_no": (com or {}).get("invoice_no") or (sale or {}).get("invoice"),
                         "commission": com, "sale": sale, "evidence": ev})
        else:
            buckets[bucket] += 1
        # duplicate_on_hand — ONE finding per key, on the NEWEST unit (the likely second record); all units listed
        for k in keys:
            grp = by_key.get(k) or []
            if len(grp) > 1 and k not in seen_dup:
                seen_dup.add(k)
                grp_sorted = sorted(grp, key=lambda x: (str(x.get("created_at") or ""), str(x.get("id") or "")))
                newest = grp_sorted[-1]
                rows.append({"device_key": k, "kind": DUPLICATE_ON_HAND, "unit_id": newest.get("id"),
                             "unit_ids": [x.get("id") for x in grp_sorted],
                             "serial_number": newest.get("serial_number"), "imei": newest.get("imei"),
                             "store_code": newest.get("store_code"), "status": newest.get("status"),
                             "product_id": newest.get("product_id"), "product_name": newest.get("product_name"),
                             "cost": _num(newest.get("cost")), "received_on": _unit_day(newest),
                             "sold_on": None, "customer": None, "invoice_no": None, "commission": None, "sale": None,
                             "evidence": [{"source": "inventory", "unit_id": x.get("id"),
                                           "serial_number": x.get("serial_number"), "store_code": x.get("store_code"),
                                           "status": x.get("status"), "received_on": _unit_day(x)} for x in grp_sorted]})
    order = {k: i for i, k in enumerate(INTEGRITY_KINDS)}
    rows.sort(key=lambda r: (order.get(r["kind"], 99), -r["cost"], str(r["device_key"])))
    return {
        "rows": rows,
        "totals": {
            "units_live": len(live), "units_unkeyed": unkeyed,
            "by_kind": {k: sum(1 for r in rows if r["kind"] == k) for k in INTEGRITY_KINDS},
            "cost_by_kind": {k: round(sum(r["cost"] for r in rows if r["kind"] == k), 2) for k in INTEGRITY_KINDS},
            "flagged": len(rows), "flagged_cost": round(sum(r["cost"] for r in rows), 2),
            "not_flagged": dict(buckets),
            "sale_lines_unkeyed": unkeyed_sales(sale_rows, key_of),
            "commission_devices": len(comm),
            "commission_devices_kept": sum(1 for d in comm.values() if d["kept"]),
        },
    }


def receive_check(candidate, units, key_of, is_live, sale=None, commission=None):
    """THE LANDING GUARD's pure half — asked BEFORE a unit is written (the single receive, the import, an edit
    that changes a serial / IMEI). `candidate` = {serial_number, imei}; `units` = the org's existing unit rows
    that may answer to its keys (any status); `sale` / `commission` = the `sales_detail_index` /
    `commission_index` entries for its key. Returns
      {keys, blocking, reasons[], existing[unit…], sale, commission, already_sold, sold_on, customer, duplicate_live}
    Blocking = an existing record of the same device (any status — a returned unit is adjusted back IN, never
    received twice), OR the device already nets sold / its commission was kept. The caller answers 409 with
    this payload unless the operator confirms."""
    keys = unit_keys(candidate, key_of)
    kset = set(keys)
    existing = [u for u in (units or []) if kset & set(unit_keys(u, key_of))] if keys else []
    kind, _b, sold_on = classify_unit(sale, commission, None)
    already_sold = kind in SOLD_KINDS
    dup_live = any(is_live((u or {}).get("status")) for u in existing)
    reasons = (["duplicate_on_hand"] if dup_live else ["existing_record"] if existing else []) \
        + (["already_sold"] if already_sold else [])
    return {"keys": keys, "blocking": bool(reasons), "reasons": reasons, "existing": existing,
            "sale": sale, "commission": commission, "already_sold": already_sold, "sold_on": sold_on,
            "customer": customer_of(sale, commission), "duplicate_live": dup_live}
