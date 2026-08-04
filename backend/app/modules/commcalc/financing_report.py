"""FINANCING REPORT — financed units and financed dollars by vendor x store x rep, vs the store's target.

OWNER DIRECTIVE (in-chat 2026-08-04): "need another report for tracking the financing, edge in case of
total and acima in case of boost … this will be called Financing report, should have assignable target
for each store in target area…"

WHAT IT COUNTS AND WHY THAT NUMBER IS THE HONEST ONE
  A financed sale rings as MANY sale lines that all carry the same transaction-level tender (the device,
  the rate plan, the case, the screen protector, the activation fee…). Counting lines would report a
  single financed phone as 8 financings. This module therefore counts UNITS the same way the payout does
  — through `plan_pay_gate.select_paying_lines(..., 'per_device', …)`, the exact collapse the commission
  engine applies to a tender-keyed rule (one payment per DEVICE, anchored on the line carrying a device
  serial; a transaction with no serial counts once and says so). Report units and paid units therefore
  agree by construction rather than by coincidence.

FINANCED AMOUNT IS LABELLED, NOT GUESSED. `raw_sales` does not carry the POS export's own "Financed
Amount" column — and that column is not usable anyway: on a real 78-column April 2026 house export it was
populated on 1 of 12,988 rows (and `Financed` on 1). So the report's amount is the Ext Price of the
financed DEVICE line (`amount_basis='unit_line'`, the default) or of every detected line of the
transaction (`'transaction'`), per vendor, and the page says which.

DETECTION IS CONFIG (financing_registry). A vendor with no usable detection claims NO lines and the page
renders "detection not configured" — never a zero that reads like a business fact. To make mapping a
pick-don't-type job, the report also returns the DISTINCT tender values actually present in the period
with their line/transaction counts.

READ-ONLY, DISPLAY PATH. It never writes, never recomputes, and is not called from any payout path.
MULTI-TENANT: the caller passes org-scoped rows; every loader here takes org_id.
"""
from datetime import date

from app.modules.commcalc import financing_registry as _reg
from app.modules.commcalc import financing_tiers as _ftier

VOID_TOKENS = ("true", "yes", "1", "voided", "void")


def _f(v, default=0.0):
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def is_live_line(r):
    """A sale line that counts: not voided, not a Return. PURE — the same two filters every other
    commcalc report applies before counting anything."""
    if str(r.get("voided") or "").strip().lower() in VOID_TOKENS:
        return False
    if str(r.get("trans_type") or "").strip().lower() == "return":
        return False
    return True


def tender_facets(rows, limit=60):
    """[{value, lines, transactions}] — the DISTINCT tender values actually present, most common first.

    This is what makes detection mapping pick-don't-type (RULE THREE): an operator maps ACIMA by choosing
    the string their POS really writes instead of typing a guess. PURE."""
    agg = {}
    for r in rows:
        v = str(r.get("tender_type") or "").strip()
        if not v:
            continue
        a = agg.setdefault(v, {"value": v, "lines": 0, "trans": set()})
        a["lines"] += 1
        t = str(r.get("trans_id") or "").strip()
        if t:
            a["trans"].add(t)
    out = [{"value": a["value"], "lines": a["lines"], "transactions": len(a["trans"])}
           for a in agg.values()]
    out.sort(key=lambda x: (-x["transactions"], -x["lines"], x["value"]))
    return out[:limit]


def _collapse_units(matched, gate, unit_cfg, is_accessory):
    """The detected lines of ONE vendor collapsed to payable UNITS, using the payout's own collapse.

    Returns (unit_rows, note_codes). Falls back to a plain per-transaction collapse if the gate module is
    unavailable, so the report still counts sanely rather than reporting line counts as units."""
    if not matched:
        return [], []
    if gate is not None:
        try:
            payers, _supp, notes = gate.select_paying_lines(matched, "per_device", unit_cfg or {},
                                                            is_accessory)
            return payers, [n.get("code") for n in (notes or [])]
        except Exception:
            pass
    best = {}
    for r in matched:
        t = str(r.get("trans_id") or "").strip() or id(r)
        cur = best.get(t)
        if cur is None or _f(r.get("ext_price")) > _f(cur.get("ext_price")):
            best[t] = r
    return list(best.values()), ["unit_fallback_per_transaction"]


def build(rows, vendors, targets, store_index, resolve_market, period, today=None,
          gate=None, unit_cfg=None, is_accessory=None, month_days=None, days_elapsed=None):
    """The whole report payload. `rows` are org-scoped sale lines; `vendors` are RESOLVED vendors
    (financing_registry.resolve_vendors); `targets` is financing_tiers.load_targets output.

    Everything below is derived from those arguments only — PURE, so the harness drives it with fixtures.
    """
    live = [r for r in rows if is_live_line(r)]
    facets = tender_facets(live)

    detail, by_key = [], {}
    by_store, by_vendor = {}, {}
    unit_notes = set()
    totals = {"units": 0, "amount": 0.0, "transactions": 0}
    all_trans = set()

    for v in vendors:
        if not v.get("enabled") or not (v.get("matchers") or []):
            continue
        vk, vlabel = v["vendor_key"], v["label"]
        matched = []
        for r in live:
            for m in v["matchers"]:
                if _reg.matcher_hits(r, m):
                    matched.append(r)
                    break
        if not matched:
            by_vendor.setdefault(vk, {"vendor_key": vk, "vendor": vlabel, "units": 0, "amount": 0.0,
                                      "transactions": 0, "stores": 0,
                                      "detection_status": v.get("detection_status"),
                                      "detection_note": v.get("detection_note")})
            continue
        by_tid = {}
        for r in matched:
            by_tid.setdefault(str(r.get("trans_id") or "").strip(), []).append(r)
        units, note_codes = _collapse_units(matched, gate, unit_cfg, is_accessory)
        unit_notes.update(note_codes or [])
        basis = v.get("amount_basis") or "unit_line"
        # units per transaction — used to SPLIT a transaction-basis amount evenly across the devices
        # that transaction financed, so two phones on one contract never each report the full contract.
        units_per_tid = {}
        for u in units:
            units_per_tid[str(u.get("trans_id") or "").strip()] = \
                units_per_tid.get(str(u.get("trans_id") or "").strip(), 0) + 1
        for u in units:
            tid = str(u.get("trans_id") or "").strip()
            store = str(u.get("store") or "").strip()
            code = _ftier.resolve_store_code(store, store_index)
            rep = str(u.get("salesperson") or "").strip() or "(unattributed)"
            if basis == "transaction":
                grp = by_tid.get(tid) or [u]
                amt = round(sum(_f(x.get("ext_price")) for x in grp)
                            / max(1, units_per_tid.get(tid, 1)), 2)
            else:
                amt = round(_f(u.get("ext_price")), 2)
            k = (vk, code, rep)
            d = by_key.get(k)
            if d is None:
                d = by_key[k] = {"vendor_key": vk, "vendor": vlabel, "store_code": code,
                                 "store": store, "market": (resolve_market(store) if resolve_market else ""),
                                 "rep": rep, "units": 0, "amount": 0.0, "transactions": set(),
                                 "first_date": None, "last_date": None}
                detail.append(d)
            d["units"] += 1
            d["amount"] = round(d["amount"] + amt, 2)
            if tid:
                d["transactions"].add(tid)
                all_trans.add((vk, tid))
            day = str(u.get("trans_date") or "")[:10]
            if day:
                d["first_date"] = day if not d["first_date"] else min(d["first_date"], day)
                d["last_date"] = day if not d["last_date"] else max(d["last_date"], day)

            s = by_store.setdefault(code, {"store_code": code, "store": store,
                                           "market": (resolve_market(store) if resolve_market else ""),
                                           "units": 0, "amount": 0.0, "by_vendor": {}})
            s["units"] += 1
            s["amount"] = round(s["amount"] + amt, 2)
            sv = s["by_vendor"].setdefault(vk, {"vendor_key": vk, "vendor": vlabel,
                                                "units": 0, "amount": 0.0})
            sv["units"] += 1
            sv["amount"] = round(sv["amount"] + amt, 2)

            bv = by_vendor.setdefault(vk, {"vendor_key": vk, "vendor": vlabel, "units": 0,
                                           "amount": 0.0, "transactions": 0, "stores": 0,
                                           "detection_status": v.get("detection_status"),
                                           "detection_note": v.get("detection_note")})
            bv["units"] += 1
            bv["amount"] = round(bv["amount"] + amt, 2)

    for d in detail:
        d["transactions"] = len(d["transactions"])
        totals["units"] += d["units"]
        totals["amount"] = round(totals["amount"] + d["amount"], 2)
    totals["transactions"] = len(all_trans)
    for vk, bv in by_vendor.items():
        bv["transactions"] = len({t for (k, t) in all_trans if k == vk})
        bv["stores"] = len({d["store_code"] for d in detail if d["vendor_key"] == vk})

    # ── attainment + pace, per store. A store with NO target is reported as "no target set" —
    #    never as 0%, which would read as a failure the store never signed up for.
    md = month_days or 0
    de = days_elapsed or 0
    stores = []
    seen_codes = set(by_store)
    for (code, vk) in list(targets.keys()):
        seen_codes.add(code)
    for code in sorted(seen_codes):
        s = by_store.get(code) or {"store_code": code, "store": code, "market": "", "units": 0,
                                   "amount": 0.0, "by_vendor": {}}
        t_units, t_source = _ftier.target_for({"targets": targets}, code, None)
        row = dict(s)
        row["by_vendor"] = sorted(s["by_vendor"].values(), key=lambda x: -x["units"]) \
            if isinstance(s.get("by_vendor"), dict) else []
        row["target_units"] = t_units
        row["target_source"] = t_source
        row["attainment_pct"] = (round(100.0 * s["units"] / t_units, 1) if t_units else None)
        row["need_units"] = (max(0.0, t_units - s["units"]) if t_units else None)
        if md and de:
            projected = round(s["units"] * md / de, 1)
            row["projected_units"] = projected
            row["pace_per_day"] = round(s["units"] / de, 2)
            row["on_pace"] = (projected >= t_units) if t_units else None
            row["needed_per_remaining_day"] = (
                round(max(0.0, t_units - s["units"]) / max(1, md - de), 2) if t_units else None)
        else:
            row["projected_units"] = None
            row["pace_per_day"] = None
            row["on_pace"] = None
            row["needed_per_remaining_day"] = None
        # per-vendor targets, when the tenant set any
        vt = []
        for (c, k) in targets:
            if c == code and k:
                vt.append({"vendor_key": k, "target_units": _f(targets[(c, k)].get("units")),
                           "units": next((x["units"] for x in row["by_vendor"] if x["vendor_key"] == k), 0)})
        row["vendor_targets"] = vt
        stores.append(row)
    stores.sort(key=lambda r: (-(r.get("units") or 0), r.get("store_code") or ""))

    detail.sort(key=lambda d: (-(d.get("units") or 0), d.get("store_code") or "", d.get("rep") or ""))
    return {
        "period": period,
        "rows": detail,
        "by_store": stores,
        "by_vendor": sorted(by_vendor.values(), key=lambda x: -x["units"]),
        "totals": totals,
        "tender_values": facets,
        "unit_notes": sorted(unit_notes),
        "vendors": [{"vendor_key": v["vendor_key"], "label": v["label"], "enabled": v["enabled"],
                     "detection_source": v["detection_source"],
                     "detection_status": v.get("detection_status"),
                     "detection_note": v.get("detection_note"),
                     "amount_basis": v.get("amount_basis"),
                     "matchers": [{"match_field": m["match_field"], "match_op": m["match_op"],
                                   "match_value": m["match_value"],
                                   "field_warning": m.get("field_warning"),
                                   "from_rule_label": m.get("from_rule_label")}
                                  for m in (v.get("matchers") or [])],
                     "carriers": v.get("carriers") or []}
                    for v in vendors],
        "amount_note": ("Financed amount = the Ext Price of the financed device line. The POS export's "
                        "own \"Financed Amount\" column is not stored in raw_sales (and is populated on "
                        "well under 1% of rows), so this is a labelled stand-in, not that column."),
        "attainment_note": ("Attainment is measured MONTHLY against the store's financing target "
                            "(Targets - Target Settings). A store with no target set shows \"no target\" "
                            "rather than 0%."),
    }


def month_bounds(period, today=None):
    """(days_in_month, days_elapsed) for MTD pace, or (0, 0) when the period is not the current month.
    PURE apart from `date.today()` when `today` is not supplied."""
    from app.modules.commcalc.calculator import parse_period
    import calendar
    try:
        pm = parse_period(period)
        y, m = int(pm["year"]), int(pm["month"])
    except Exception:
        return 0, 0
    t = today or date.today()
    dim = calendar.monthrange(y, m)[1]
    if (t.year, t.month) == (y, m):
        return dim, max(1, t.day)
    if (y, m) < (t.year, t.month):
        return dim, dim
    return dim, 0
