"""Pure IMEI/serial reconciliation between B2B inventory (commcalc.inventory_aging_device) and B2B sales
(commcalc.raw_sales.serial_1) for a store + period — a data-quality check that every IMEI is accounted
for. Reuses device_history's normalizers (never a third matcher) and asset.inventory_buckets.inv_bucket
for the uncategorized-sales leg. No DB / FastAPI here — unit-tested; the endpoint resolves the store,
period, and received-date derivation and calls reconcile().
"""
from app.modules.commcalc import device_history as _dh
from app.modules.asset.inventory_buckets import inv_bucket


def norm_forms(v):
    """Comparable forms for a serial/IMEI: norm_key (upper) + digits-only. Two values match when their
    form-sets intersect — the same '.0'/digit normalization the rest of the app matches devices with."""
    forms = set()
    k = _dh.norm_key(v)
    if k:
        forms.add(k.upper())
    d = _dh.norm_digits(v)
    if d:
        forms.add(d)
    return forms


def reconcile(devices, sales, max_days=10):
    """devices: [{imei, on_hand, off_hand_as_of, received}] (received = derived received date, 'YYYY-MM-DD'
    or None). sales: [{serial, trans_date, product_desc}]. Returns the reconciliation legs + sold-within-N
    aging split. Pure."""
    inv_forms_all = set()
    dev_forms = []
    for d in devices:
        f = norm_forms(d.get("imei"))
        dev_forms.append((d, f))
        inv_forms_all |= f

    sale_forms = []
    sale_forms_all = set()
    for s in sales:
        f = norm_forms(s.get("serial"))
        sale_forms.append((s, f))
        sale_forms_all |= f

    # (a) received but off the shelf with NO matching sale — left inventory without a recorded sale (shrink).
    unaccounted = [d for (d, f) in dev_forms
                   if d.get("on_hand") is False and d.get("off_hand_as_of") and not (f & sale_forms_all)]
    # (b) sold but NEVER in inventory — a sale with no matching received device.
    sold_not_in_inv = [s for (s, f) in sale_forms if f and not (f & inv_forms_all)]
    # (c) uncategorized sales — inv_bucket can't classify the product (SIM/accessory/unknown).
    uncategorized = [s for (s, f) in sale_forms if inv_bucket(s.get("product_desc")) is None]

    # (d) sold-within-N: for sales matched to a device, aging = trans_date − received.
    inv_by_form = {}
    for (d, f) in dev_forms:
        for key in f:
            inv_by_form.setdefault(key, d)
    within, over, unknown = [], [], 0
    for (s, f) in sale_forms:
        dev = next((inv_by_form[k] for k in f if k in inv_by_form), None)
        if not dev:
            continue
        aging = _dh.days_between(dev.get("received"), s.get("trans_date"))
        if aging is None:
            unknown += 1
        elif aging <= max_days:
            within.append({**s, "aging_days": aging})
        else:
            over.append({**s, "aging_days": aging})

    return {
        "counts": {
            "devices": len(devices), "sales": len(sales),
            "unaccounted": len(unaccounted), "sold_not_in_inventory": len(sold_not_in_inv),
            "uncategorized_sales": len(uncategorized),
            "sold_within_n": len(within), "sold_over_n": len(over), "aging_unknown": unknown,
        },
        "unaccounted": unaccounted[:200],
        "sold_not_in_inventory": sold_not_in_inv[:200],
        "uncategorized_sales": uncategorized[:200],
        "sold_over_n": sorted(over, key=lambda x: -(x.get("aging_days") or 0))[:200],
        "max_days": max_days,
    }


if __name__ == "__main__":
    devices = [
        {"imei": "111", "on_hand": True, "off_hand_as_of": None, "received": "2026-07-01"},
        {"imei": "222", "on_hand": False, "off_hand_as_of": "2026-07-20", "received": "2026-07-01"},
        {"imei": "333", "on_hand": False, "off_hand_as_of": "2026-07-25", "received": "2026-07-01"},
    ]
    sales = [
        {"serial": "222.0", "trans_date": "2026-07-20", "product_desc": "iPhone 15"},   # matched, 19d over
        {"serial": "111", "trans_date": "2026-07-05", "product_desc": "Galaxy S24"},     # matched, 4d within
        {"serial": "999", "trans_date": "2026-07-10", "product_desc": "iPhone 14"},      # not in inventory
        {"serial": "888", "trans_date": "2026-07-11", "product_desc": "SIM Kit"},        # uncategorized + not in inv
    ]
    r = reconcile(devices, sales, max_days=10)
    c = r["counts"]
    assert c["unaccounted"] == 1, c            # device 333
    assert c["sold_not_in_inventory"] == 2, c  # 999, 888
    assert c["uncategorized_sales"] == 1, c    # SIM Kit
    assert c["sold_within_n"] == 1, c          # 111 @4d
    assert c["sold_over_n"] == 1, c            # 222 @19d
    assert c["aging_unknown"] == 0, c
    assert r["sold_over_n"][0]["aging_days"] == 19
    # '.0' normalization matched serial '222.0' to imei '222'
    print("inventory_recon self-test OK:", c)
