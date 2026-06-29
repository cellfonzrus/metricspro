"""Multi-month commission payout — installment engine (generic, per-carrier; migration 057).

A single activation can pay a rep across up to N months. Month 1 always pays; months 2..N pay only
if the subscriber's bill was PAID + residual received that month — proven by the carrier statement
(raw_mi): the subscriber is still present that period, Active, with non-zero residual. Each
installment is FLAT or a % of THAT month's MRC. Config lives in commcalc.payout_schedule(+_line).

Backward-compatible: with NO schedule configured, this returns a no-op (single-month payout, today's
behavior, unchanged). Degrades to a no-op if migration 057 isn't applied yet (tables absent). It is
READ-ONLY/PREVIEW until explicitly wired into _run_calculation (see HANDOFF) — persist=True is the
opt-in that writes the subscriber_installments ledger.

Simplifications in v1 (documented; refine with real data): a subscriber's activation_type is taken as
'*' (MI-only carriers don't reliably carry premium/byod/upgrade), and company is left to the schedule
fallback (NULL company_id) — so a single org-wide schedule with NULL company/carrier + activation_type
'*' is the common, working case. Per-type / per-company resolution can tighten later.
"""
import calendar
from app.modules.commcalc.calculator import parse_period, safe_float

ORG_ID = "00000000-0000-0000-0000-000000000001"


def _pvariants(period):
    """Period stored as 'June 2026' or '2026-06' — match both spellings."""
    p = (period or "").strip()
    out = {p}
    pp = parse_period(p)
    y, m = pp.get("year") or 0, pp.get("month") or 0
    if y and m:
        out.add(f"{y}-{m:02d}")
        out.add(f"{calendar.month_name[m]} {y}")
    return [x for x in out if x]


def _period_index(period):
    """Monotonic month index for a period label, or None if unparseable."""
    p = parse_period(period or "")
    y, m = p.get("year") or 0, p.get("month") or 0
    return y * 12 + (m - 1) if (y and m) else None


def _shift_period(period, k):
    """'June 2026' shifted by k months → 'August 2026'. '' if unparseable."""
    idx = _period_index(period)
    if idx is None:
        return ""
    idx += k
    ny, nm = idx // 12, idx % 12 + 1
    return f"{calendar.month_name[nm]} {ny}"


def _load_schedules(client, org_id):
    """(schedules, lines_by_schedule_id). Empty if migration 057 isn't applied."""
    try:
        scheds = (client.schema("commcalc").table("payout_schedule").select("*")
                  .eq("org_id", org_id).eq("is_active", True).execute().data) or []
        lines = (client.schema("commcalc").table("payout_schedule_line").select("*")
                 .eq("org_id", org_id).execute().data) or []
    except Exception:
        return [], {}
    by_sched = {}
    for ln in lines:
        by_sched.setdefault(ln.get("schedule_id"), []).append(ln)
    return scheds, by_sched


def _resolve_schedule(scheds, carrier_id, company_id, activation_type):
    """Most-specific (company+carrier+type) wins; falls back to NULL company / NULL carrier / '*'."""
    best, best_score = None, -1
    for s in scheds:
        sc, cr = s.get("company_id"), s.get("carrier_id")
        at = s.get("activation_type") or "*"
        if sc and sc != company_id:
            continue
        if cr and cr != carrier_id:
            continue
        if at != "*" and at != activation_type:
            continue
        score = (1 if sc else 0) + (1 if cr else 0) + (1 if at != "*" else 0)
        if score > best_score:
            best, best_score = s, score
    return best


def _read_mi(client, org_id, period):
    """Paginated raw_mi for one period (select * so a missing optional column never errors)."""
    out, start, page = [], 0, 1000
    while True:
        rows = (client.schema("commcalc").table("raw_mi").select("*")
                .eq("org_id", org_id).in_("period", _pvariants(period))
                .range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def compute_installments(client, org_id, pay_period, persist=False):
    """Installments that LAND in `pay_period`. Read-only unless persist=True.
    Returns {pay_period, by_rep:{rep:amount}, ledger:[...], totals, schedules, note}."""
    scheds, lines_by = _load_schedules(client, org_id)
    if not scheds:
        return {"pay_period": pay_period, "by_rep": {}, "ledger": [], "schedules": 0,
                "totals": {"amount": 0.0, "paid": 0, "withheld": 0, "pending": 0, "reps": 0},
                "note": "No payout schedules configured (or migration 057 not applied) — single-month payout unchanged."}

    max_n = min(3, max((s.get("num_months") or 1) for s in scheds))
    periods = [pay_period] + [_shift_period(pay_period, -k) for k in range(1, max_n)]
    periods = [p for p in periods if p]

    mi = []
    for p in periods:
        mi.extend(_read_mi(client, org_id, p))

    by_sub_period, earliest = {}, {}
    for r in mi:
        sub = str(r.get("subscriber_id") or "").strip()
        pidx = _period_index(r.get("period"))
        if not sub or pidx is None:
            continue
        by_sub_period[(sub, pidx)] = r
        if sub not in earliest or pidx < earliest[sub]:
            earliest[sub] = pidx

    pay_idx = _period_index(pay_period)
    by_rep, ledger = {}, []
    n_paid = n_withheld = n_pending = 0
    total_amt = 0.0
    if pay_idx is not None:
        for sub, act_idx in earliest.items():
            month_index = (pay_idx - act_idx) + 1
            if month_index < 1:
                continue
            pay_row = by_sub_period.get((sub, pay_idx))
            anchor_row = by_sub_period.get((sub, act_idx)) or pay_row or {}
            carrier_id = anchor_row.get("carrier_id")
            sched = _resolve_schedule(scheds, carrier_id, None, "*")
            if not sched:
                continue
            num_months = min(3, sched.get("num_months") or 1)
            if month_index > num_months:
                continue
            line = next((l for l in lines_by.get(sched.get("id"), [])
                         if (l.get("month_index") or 0) == month_index), None)
            if not line:
                continue
            basis = line.get("mrc_basis") or "commissionable_mrc"
            mrc = safe_float((pay_row or anchor_row).get(basis))

            requires_paid = bool(line.get("requires_paid")) and month_index > 1
            status, gate_met = "paid", True
            if requires_paid:
                if pay_row is None:
                    status, gate_met = "pending", False
                else:
                    active = str(pay_row.get("subscriber_status") or "").strip().lower().startswith("activ")
                    resid = safe_float(pay_row.get("actual_mi_payout")) + safe_float(pay_row.get("actual_atu_payout"))
                    sig = sched.get("gate_signal") or "paid_residual"
                    if sig == "active_status":
                        gate_met = active
                    elif sig == "nonzero_residual":
                        gate_met = resid > 0
                    else:  # paid_residual (default) / paid_flag fallback = paid AND residual received
                        gate_met = active and resid > 0
                    if not gate_met:
                        status = "withheld_unpaid"

            if not gate_met:
                amount = 0.0
            elif (line.get("payout_kind") or "flat") == "pct_mrc":
                amount = round(safe_float(line.get("mrc_pct")) * mrc, 2)
            else:
                amount = round(safe_float(line.get("flat_amount")), 2)

            rep = (anchor_row.get("epay_salesperson") or anchor_row.get("rep_username")
                   or (pay_row or {}).get("epay_salesperson") or (pay_row or {}).get("rep_username") or "").strip()
            if amount and rep:
                by_rep[rep] = round(by_rep.get(rep, 0.0) + amount, 2)
            total_amt += amount
            n_paid += status == "paid"
            n_withheld += status == "withheld_unpaid"
            n_pending += status == "pending"
            ledger.append({
                "subscriber_id": sub, "rep": rep, "store": anchor_row.get("store"),
                "activation_period": _shift_period(pay_period, -(month_index - 1)),
                "pay_period": pay_period, "month_index": month_index,
                "payout_kind": line.get("payout_kind"), "mrc_at_pay": round(mrc, 2),
                "amount": amount, "paid_gate_met": gate_met, "status": status,
                "carrier_id": carrier_id, "schedule_id": sched.get("id"),
            })

    if persist:
        _persist(client, org_id, pay_period, ledger)

    ledger.sort(key=lambda x: -(x.get("amount") or 0))
    return {"pay_period": pay_period, "by_rep": by_rep, "ledger": ledger,
            "schedules": len(scheds),
            "totals": {"amount": round(total_amt, 2), "paid": n_paid,
                       "withheld": n_withheld, "pending": n_pending, "reps": len(by_rep)},
            "note": None}


def _persist(client, org_id, pay_period, ledger):
    """Upsert the ledger for this pay_period (idempotent on the unique key). Opt-in (persist=True)."""
    rows = [{
        "org_id": org_id, "subscriber_id": d["subscriber_id"],
        "carrier_id": d.get("carrier_id"), "schedule_id": d.get("schedule_id"),
        "store": d.get("store"), "epay_salesperson": d.get("rep"),
        "activation_type": "*", "activation_period": d.get("activation_period"),
        "pay_period": pay_period, "month_index": d.get("month_index"),
        "payout_kind": d.get("payout_kind"), "mrc_at_pay": d.get("mrc_at_pay"),
        "amount": d.get("amount"), "paid_gate_met": d.get("paid_gate_met"),
        "status": d.get("status"), "source_mi_period": pay_period,
    } for d in ledger if d.get("subscriber_id")]
    for i in range(0, len(rows), 500):
        try:
            client.schema("commcalc").table("subscriber_installments").upsert(
                rows[i:i + 500], on_conflict="org_id,subscriber_id,activation_type,month_index").execute()
        except Exception:
            pass
