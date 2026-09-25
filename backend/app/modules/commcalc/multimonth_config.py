"""IS MULTI-MONTH PAY CONFIGURED FOR THIS ORG? — the ONE predicate every surface asks before it OFFERS the
multi-month option (owner 2026-09-25).

Owner, verbatim: *"right now the rep comisison is shown with plan incentive and multi month, if multi month is
not confgured then it should not be shown as an available option."*

WHERE MULTI-MONTH PAY IS CONFIGURED (index §7 / §8 — two engines, per-org rows, RULE TWO):
  · `commcalc.payout_schedule` (mig 057)            — the residual (raw_mi) installment engine, §7;
  · `commcalc.plan_installment_schedule` (mig 201)  — the sale-triggered installment engine, §8.
Each engine reads ONLY its `is_active` rows and produces nothing without one, so "configured" = at least one
ACTIVE schedule in either table for the org. Nothing else is consulted — no carrier, no tenant, no name.

HIDDEN MEANS NOT OFFERED — NEVER MONEY DROPPED. The decision also reads the multi-month MONEY already on the
rep rows (`rep_commissions.residual_installment_comm` + `installment_comm_sale`) for the periods on screen:
  · 'configured'      — offer the option (today's behaviour);
  · 'off'             — no active schedule and no multi-month money: do not offer it;
  · 'off_with_money'  — no active schedule, but multi-month money IS on the rep rows: show the money and SAY
                        that multi-month is not configured (a schedule was switched off after it paid, or the
                        rows predate a change) — never hide it.
`decide` is PURE; `schedule_counts` / `money_by_period` / `load` are the org-scoped reads. The R1
refuse-to-pay guard (`router._has_any_pay_source`) counts schedules through `schedule_counts` too, so "is there
a multi-month schedule" has one answer. Lock: `harness_multimonth_offer_lock.py`.
"""

SCHEDULE_TABLES = (
    ("payout_schedule", "residual", "multi-month residual schedule(s)"),
    ("plan_installment_schedule", "sale", "multi-month sale-triggered installment schedule(s)"),
)
MONEY_FIELDS = ("residual_installment_comm", "installment_comm_sale")
STATES = ("configured", "off", "off_with_money")


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def decide(counts, money_by_period=None):
    """PURE. `counts` = {table: active schedules | None (unreadable)}; `money_by_period` = {period: $}.
    Returns {"configured", "offered", "state", "sources", "money", "note"}. An UNREADABLE table (None) is
    treated as configured — a failed read must never hide an option that may be live."""
    counts = dict(counts or {})
    sources = []
    for table, kind, label in SCHEDULE_TABLES:
        n = counts.get(table, 0)
        sources.append({"table": table, "kind": kind, "label": label,
                        "active": n, "readable": n is not None})
    configured = any((s["active"] is None) or (s["active"] > 0) for s in sources)
    money = {p: round(_f(v), 2) for p, v in (money_by_period or {}).items() if abs(_f(v)) >= 0.005}
    if configured:
        state = "configured"
    elif money:
        state = "off_with_money"
    else:
        state = "off"
    note = None
    if state == "off_with_money":
        tot = round(sum(money.values()), 2)
        note = (f"Multi-month pay is not configured for this organisation (no active multi-month schedule), but "
                f"${tot:,.2f} of multi-month pay is on the rep rows for {', '.join(sorted(money))}. It is shown, "
                f"not hidden — re-activate the schedule or recalculate the period if it should not be there.")
    return {"configured": configured, "offered": state != "off", "state": state, "sources": sources,
            "money": money, "note": note}


def schedule_counts(client, org_id):
    """{table: ACTIVE schedule rows for the org | None when the read fails}. Org-scoped; never raises."""
    out = {}
    for table, _kind, _label in SCHEDULE_TABLES:
        try:
            r = (client.schema("commcalc").table(table).select("org_id", count="exact")
                 .eq("org_id", org_id).eq("is_active", True).limit(1).execute())
            out[table] = int(r.count or 0)
        except Exception:
            out[table] = None
    return out


def money_by_period(client, org_id, periods, period_keys=None):
    """{period: Σ multi-month $ on the org's rep_commissions rows} for each requested period. `period_keys`
    (the router's `_pvariants`) turns one period into every stored spelling. Org-scoped; never raises."""
    out = {}
    for p in periods or ():
        keys = period_keys(p) if period_keys else [p]
        try:
            rows = (client.schema("commcalc").table("rep_commissions").select(",".join(MONEY_FIELDS))
                    .eq("org_id", org_id).in_("period", keys).limit(20000).execute().data) or []
        except Exception:
            rows = []
        out[p] = round(sum(_f(r.get(k)) for r in rows for k in MONEY_FIELDS), 2)
    return out


def load(client, org_id, periods=None, period_keys=None):
    """THE predicate, read: `decide(schedule_counts(...), money_by_period(...))`."""
    return decide(schedule_counts(client, org_id), money_by_period(client, org_id, periods, period_keys))
