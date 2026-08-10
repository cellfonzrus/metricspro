"""Denied-Appeal Commission Recovery engine.

A denied appeal (Missing 1st MRC / Failed Activation / …) is RECOVERABLE when the line eventually PAID
or ACTIVATED after the denial — then the carrier owes the commission, and there's a limited window
(default 45d) to claim it back. This scans denied appeals (asset_ledger appeal categories), looks for
later payment / active-status evidence on that MDN or IMEI (ePay raw_payment_detail + raw_mi), buckets
each device (recoverable | expired | not_recoverable | needs_data), and materializes
commcalc.appeal_recovery. Read-only against source tables; the asset-lending system is untouched.
"""
from datetime import datetime as _dt

# The asset_ledger charge categories that ARE appeals (mirrors CHARGE_GROUPS["appeals"] in asset/router).
APPEAL_CATEGORIES = [
    "Appeal Denied. Details in Boost Appeals Status",
    "Re-Escalation",
    "Over 10 Days Missing Reimbursement (CheckElevate/Submit Appeal)",
    "Missing 1st MRC",
    "Failed Activation. Check Boost Payment Status",
]


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_imei(v):
    s = str(v or "").strip().upper()
    return s[:-2] if s.endswith(".0") else s


def _digits(v):
    return "".join(c for c in str(v or "") if c.isdigit())


def _parse_date(v):
    s = str(v or "")[:10]
    try:
        return _dt.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _read_all(client, table, select, page=1000, cap=2_000_000, **eqs):
    """Read EVERY matching row, paginated. Replaces a single `.limit(N)` shot (2026-08-10).

    A bare `.limit(200000)` is not a safety valve here, it is a silent wrong answer. Measured live:
    `commcalc.raw_mi` holds **234,610** rows and `raw_payment_detail` **205,886**, so the old limit
    dropped 34,610 and 5,886 rows respectively — with no ORDER BY, so WHICH rows vanished was
    arbitrary from one call to the next.

    That matters more here than in a reporting scan. These reads build the `pay_by_imei` /
    `mi_by_serial` evidence dictionaries, and a device whose payment or activation row happened to
    fall outside the truncation reads as "no evidence found" — which is precisely the input that
    buckets an appeal as not_recoverable. Missing evidence and absent evidence are indistinguishable
    downstream, so the engine cannot detect its own blind spot.

    `cap` is a runaway guard set far above any real table, not a business limit; the loop exits on a
    short page long before reaching it.
    """
    out, start = [], 0
    while start < cap:
        q = client.schema("commcalc").table(table).select(select)
        for k, v in eqs.items():
            q = q.in_(k, list(v)) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def build_recovery_ledger(client, org_id, cfg, today):
    """Rebuild commcalc.appeal_recovery for `org_id` as of `today` (a date). Returns a summary dict.
    `cfg` is the appeal_recovery_config row (dict). Delete-then-insert snapshot."""
    cats = [c for c in (cfg.get("recoverable_categories") or []) if c] or APPEAL_CATEGORIES
    window = int(cfg.get("clawback_window_days") or 45)
    mode = cfg.get("evidence_mode") or "payment_or_active"
    match_mdn = cfg.get("match_mdn", True)
    match_imei = cfg.get("match_imei", True)

    appeals = _read_all(client, "asset_ledger",
                        "esn_imei,phone_number,store,market,device_model,category,"
                        "owed_to_vip,payg_date,date_sold,acquired_date",
                        org_id=org_id, category=cats)

    # Payment confirmation — raw_payment_detail (mdn + imei + payment_type + amount + payment_date)
    pays = _read_all(client, "raw_payment_detail",
                     "imei,mdn,payment_type,amount,payment_date", org_id=org_id)
    pay_by_imei, pay_by_mdn = {}, {}
    for p in pays:
        rec = {"type": p.get("payment_type"), "amount": _safe_float(p.get("amount")),
               "date": _parse_date(p.get("payment_date")), "src": "epay_payment"}
        ik, mk = _norm_imei(p.get("imei")), _digits(p.get("mdn"))
        if ik:
            pay_by_imei.setdefault(ik, []).append(rec)
        if mk:
            pay_by_mdn.setdefault(mk, []).append(rec)

    # Active-status confirmation — raw_mi (device_serial + phone_number + subscriber_status + payout)
    mis = _read_all(client, "raw_mi",
                    "device_serial,phone_number,subscriber_status,actual_mi_payout,"
                    "actual_atu_payout,mi_activation_date", org_id=org_id)
    mi_by_serial, mi_by_phone = {}, {}
    for m in mis:
        rec = {"active": str(m.get("subscriber_status") or "").strip().lower().startswith("activ"),
               "resid": _safe_float(m.get("actual_mi_payout")) + _safe_float(m.get("actual_atu_payout")),
               "date": _parse_date(m.get("mi_activation_date")), "src": "epay_mi",
               "status": m.get("subscriber_status")}
        sk, pk = _norm_imei(m.get("device_serial")), _digits(m.get("phone_number"))
        if sk:
            mi_by_serial.setdefault(sk, []).append(rec)
        if pk:
            mi_by_phone.setdefault(pk, []).append(rec)

    have_confirm_data = bool(pays or mis)  # else we genuinely can't confirm → needs_data

    rows, summary = [], {"scanned": len(appeals), "recoverable": 0, "expired": 0,
                         "not_recoverable": 0, "needs_data": 0, "recoverable_amount": 0.0}
    for a in appeals:
        imei, mdn = _norm_imei(a.get("esn_imei")), _digits(a.get("phone_number"))
        denied = (_parse_date(a.get("payg_date")) or _parse_date(a.get("date_sold"))
                  or _parse_date(a.get("acquired_date")))
        owed = _safe_float(a.get("owed_to_vip"))
        evidence = None

        # (1) a later PAYMENT on this MDN/IMEI, dated after the denial
        cand = []
        if match_imei and imei:
            cand += pay_by_imei.get(imei, [])
        if match_mdn and mdn:
            cand += pay_by_mdn.get(mdn, [])
        for c in cand:
            if c["date"] and denied and c["date"] > denied and c["amount"] != 0:
                evidence = {"source": c["src"], "type": c["type"],
                            "date": c["date"].isoformat(), "amount": c["amount"]}
                break

        # (2) an ACTIVE subscriber line with residual (when the mode allows)
        if evidence is None and mode in ("payment_or_active", "any"):
            mcand = []
            if match_imei and imei:
                mcand += mi_by_serial.get(imei, [])
            if match_mdn and mdn:
                mcand += mi_by_phone.get(mdn, [])
            for c in mcand:
                if c["active"] and c["resid"] > 0:
                    evidence = {"source": c["src"], "type": "active_subscriber",
                                "date": c["date"].isoformat() if c["date"] else None,
                                "amount": c["resid"], "status": c["status"]}
                    break

        paid_later = evidence is not None
        if paid_later:
            in_window = denied is not None and (today - denied).days <= window
            status = "recoverable" if in_window else "expired"
        elif not have_confirm_data:
            status = "needs_data"   # no ePay confirmation source loaded at all
        else:
            status = "not_recoverable"

        summary[status] = summary.get(status, 0) + 1
        if status == "recoverable":
            summary["recoverable_amount"] += owed
        rows.append({
            "org_id": org_id, "imei": a.get("esn_imei"), "mdn": a.get("phone_number"),
            "store": a.get("store"), "market": a.get("market"),
            "device_model": a.get("device_model"), "category": a.get("category"),
            "denied_date": denied.isoformat() if denied else None,
            "owed_amount": round(owed, 2), "paid_later": paid_later,
            "evidence": evidence, "status": status,
        })

    client.schema("commcalc").table("appeal_recovery").delete().eq("org_id", org_id).execute()
    for i in range(0, len(rows), 500):
        client.schema("commcalc").table("appeal_recovery").insert(rows[i:i + 500]).execute()
    summary["recoverable_amount"] = round(summary["recoverable_amount"], 2)
    return summary


def rebuttal_for(row):
    """The carrier-facing justification for one recoverable device — why the commission is owed."""
    ev = row.get("evidence") or {}
    when = ev.get("date") or "a later date"
    cat = row.get("category") or "denied appeal"
    if ev.get("type") == "active_subscriber":
        why = f"the line is ACTIVE with recurring residual (subscriber status '{ev.get('status')}')"
    else:
        why = f"a {ev.get('type') or 'qualifying'} payment of ${_safe_float(ev.get('amount')):.2f} posted on {when}"
    return (f"Commission was denied for '{cat}', but {why}. The activation/1st-MRC condition is satisfied "
            f"→ requesting reinstatement of the ${_safe_float(row.get('owed_amount')):.2f} denied commission "
            f"(IMEI {row.get('imei')}, MDN {row.get('mdn')}).")
