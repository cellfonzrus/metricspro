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
from app.modules.commcalc import installment_month as _im
from app.modules.core import feed_read as _feed_read
from app.core import column_tolerant as _col

ORG_ID = "00000000-0000-0000-0000-000000000001"

# WHICH COLUMN ON A raw_mi ROW IS THE SUBSCRIBER'S OWN ANCHOR (index §19.51). A table-structure
# fact, not per-org policy, so it is named here rather than in a config table — the same standing as
# `residual_subs.MI_STORE_KEY_COLUMN`. Named so the reader, the select and the proof harness cannot
# drift: a month index derived from anything but the row's own anchor is the defect §19.51 fixed.
MI_ACTIVATION_DATE_COLUMN = "mi_activation_date"

# Safety ceiling on how deep a schedule may run — a bound on this reader, not a business rule (the
# per-org `payout_schedule.num_months` is the business rule, RULE TWO). Named once and handed to
# `installment_month.horizon`, which is also where `sale_installment_engine.MAX_SCHEDULE_MONTHS`
# goes, so neither engine re-expresses its own clamp.
MAX_SCHEDULE_MONTHS = 12


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
    """Monotonic month index for a period label, or None if unparseable.

    DEREFERENCES `installment_month.period_index` (index §19.51) rather than re-deriving the
    expression: `month_span` only ever subtracts two of these, so a second copy on a different epoch
    or a 1-based month would shift every schedule by a month without failing anything."""
    p = parse_period(period or "")
    return _im.period_index(p.get("year") or 0, p.get("month") or 0)


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


def _load_product_mrc(client, org_id):
    """Per-product MRC catalog (migration 074). [] if the table is absent. Sorted by priority asc so a
    specific plan rule is consulted before a catch-all."""
    try:
        rows = (client.schema("commcalc").table("product_mrc").select("*")
                .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    except Exception:
        return []
    return sorted(rows, key=lambda r: (r.get("priority") if r.get("priority") is not None else 100))


def _catalog_mrc(catalog, carrier_id, customer_plan):
    """MRC for a subscriber's plan from the catalog, or None if no rule matches. A NULL-carrier rule
    matches any carrier; priority order (specific first) breaks ties. Match is case-insensitive."""
    plan = str(customer_plan or "").strip().lower()
    if not plan:
        return None
    for r in catalog:
        cr = r.get("carrier_id")
        if cr and cr != carrier_id:
            continue
        pat = str(r.get("plan_pattern") or "").strip().lower()
        if not pat:
            continue
        op = r.get("match_op") or "equals"
        hit = (plan == pat) if op == "equals" else (pat in plan)
        if hit:
            return safe_float(r.get("mrc"))
    return None


def _resolve_mrc(row, basis, catalog, carrier_id):
    """Resolve the MRC used for one %-of-MRC installment. Returns (mrc, source) where source ∈
    'commissionable_mrc' | 'base_mrc' | 'product_catalog' | 'none'.

    - basis == 'product_catalog' → look up the per-product catalog by customer_plan (primary source).
    - otherwise read the mapped raw_mi column; if that is <= 0, FALL BACK to the catalog so a carrier whose
      statement carries no MRC (e.g. Total Wireless) still computes a real amount. Boost keeps its real
      commissionable_mrc untouched (column > 0 → catalog never consulted)."""
    plan = row.get("customer_plan")
    if basis == "product_catalog":
        m = _catalog_mrc(catalog, carrier_id, plan)
        return (safe_float(m), "product_catalog") if m is not None else (0.0, "none")
    col = safe_float(row.get(basis))
    if col > 0:
        return col, basis
    m = _catalog_mrc(catalog, carrier_id, plan)
    if m is not None:
        return safe_float(m), "product_catalog"
    return 0.0, "none"


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
    """EVERY raw_mi row for one period (select * so a missing optional column never errors).

    Dereferences `core/feed_read.read_all` (index §19.48) instead of keeping a private page loop.
    `read_all` carries no row ceiling and RAISES rather than returning a short list — which matters
    here specifically: §19.48 measured `raw_mi` at 46,047 rows in September and growing ~4,000 a
    month, so it is the next feed over from the one whose `.limit(50000)` cost July $62,700."""
    return _feed_read.read_all(
        lambda: client.schema("commcalc").table("raw_mi").select("*")
        .eq("org_id", org_id).in_("period", _pvariants(period)))


def compute_installments(client, org_id, pay_period, persist=False):
    """Installments that LAND in `pay_period`. Read-only unless persist=True.
    Returns {pay_period, by_rep:{rep:amount}, ledger:[...], totals, schedules, note}."""
    scheds, lines_by = _load_schedules(client, org_id)
    catalog = _load_product_mrc(client, org_id)
    if not scheds:
        return {"pay_period": pay_period, "by_rep": {}, "ledger": [], "schedules": 0,
                "totals": {"amount": 0.0, "paid": 0, "withheld": 0, "pending": 0, "reps": 0},
                "note": "No payout schedules configured (or migration 057 not applied) — single-month payout unchanged."}

    # User-configurable horizon (default schedules use up to 3, but 6/12+ is allowed), through the
    # ONE home so the ceiling is derived from the org's own `num_months` rows and never re-expressed
    # (index §19.51). This bounds how many prior raw_mi periods we pull for the GATE check only —
    # since §19.51 it is no longer what decides a subscriber's month of life.
    max_n = _im.horizon((s.get("num_months") for s in scheds), MAX_SCHEDULE_MONTHS)
    periods = [pay_period] + [_shift_period(pay_period, -k) for k in range(1, max_n)]
    periods = [p for p in periods if p]

    mi = []
    for p in periods:
        mi.extend(_read_mi(client, org_id, p))

    # `earliest` is now ONLY the lookback FLOOR — the oldest month this read covers for a subscriber,
    # used as the labelled `BASIS_WINDOW_EDGE` fallback for a row that carries no activation date of
    # its own. `anchor_date` is the real answer: the subscriber's OWN activation date off their own
    # row (index §19.51). Before this, `earliest` WAS the activation month, which is why anybody
    # present throughout the window was paid the final instalment of their curve every month forever.
    by_sub_period, earliest, anchor_date = {}, {}, {}
    for r in mi:
        sub = str(r.get("subscriber_id") or "").strip()
        pidx = _period_index(r.get("period"))
        if not sub or pidx is None:
            continue
        by_sub_period[(sub, pidx)] = r
        if sub not in earliest or pidx < earliest[sub]:
            earliest[sub] = pidx
        # EARLIEST stated activation date wins, so a later snapshot that re-states the same
        # subscriber cannot walk their anchor forward a month at a time.
        d = r.get(MI_ACTIVATION_DATE_COLUMN)
        di = _im.index_of_date(d)
        if di is not None and (sub not in anchor_date or di < _im.index_of_date(anchor_date[sub])):
            anchor_date[sub] = d

    pay_idx = _period_index(pay_period)
    by_rep, ledger = {}, []
    n_paid = n_withheld = n_pending = 0
    total_amt = 0.0
    # §19.48's rule applied to the month index: every row says WHICH anchor placed it, and a row
    # placed on the window edge is counted and priced rather than absorbed.
    basis_tally = _im.BasisTally()
    # D1 (index §19.51): a schedule that resolves for NOBODY is a reportable fact, not a silent zero.
    # Counted by the carrier id the rows actually carry, so the operator notice can name what the
    # data says — never papered over by guessing a carrier the row does not name.
    unresolved_subs = 0
    unresolved_carriers = {}
    if pay_idx is not None:
        for sub, floor_idx in earliest.items():
            pay_row = by_sub_period.get((sub, pay_idx))
            resolved = _im.resolve_month(pay_idx, activation_date=anchor_date.get(sub),
                                         window_floor_index=floor_idx)
            month_index = resolved["month_index"]
            if month_index is None or month_index < 1:
                continue
            anchor_row = by_sub_period.get((sub, resolved["anchor_index"])) or pay_row or {}
            carrier_id = anchor_row.get("carrier_id")
            sched = _resolve_schedule(scheds, carrier_id, None, "*")
            if not sched:
                unresolved_subs += 1
                _k = str(carrier_id) if carrier_id else ""
                unresolved_carriers[_k] = unresolved_carriers.get(_k, 0) + 1
                continue
            num_months = min(MAX_SCHEDULE_MONTHS, sched.get("num_months") or 1)
            if not _im.in_schedule(month_index, num_months):
                continue
            line = next((l for l in lines_by.get(sched.get("id"), [])
                         if (l.get("month_index") or 0) == month_index), None)
            if not line:
                continue
            basis = line.get("mrc_basis") or "commissionable_mrc"
            mrc, mrc_source = _resolve_mrc(pay_row or anchor_row, basis, catalog, carrier_id)

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
            basis_tally.add(resolved["basis"], amount)
            ledger.append({
                "subscriber_id": sub, "rep": rep, "store": anchor_row.get("store"),
                # The anchor month itself, derived BACK from the month index — so for an anchored
                # subscriber this is their real activation month, not the window edge it used to be.
                "activation_period": _shift_period(pay_period, -(month_index - 1)),
                "pay_period": pay_period, "month_index": month_index,
                # WHICH anchor placed this row (index §19.51). Stored on the ledger row so a payout
                # can be audited for whether its month of life was PROVEN or assumed.
                "month_basis": resolved["basis"], "month_anchored": resolved["anchored"],
                "payout_kind": line.get("payout_kind"), "mrc_at_pay": round(mrc, 2),
                "mrc_source": mrc_source, "customer_plan": (pay_row or anchor_row).get("customer_plan"),
                "amount": amount, "paid_gate_met": gate_met, "status": status,
                "carrier_id": carrier_id, "schedule_id": sched.get("id"),
            })

    persisted = _persist(client, org_id, pay_period, ledger) if persist else None

    ledger.sort(key=lambda x: -(x.get("amount") or 0))
    # ── WHAT THIS RUN COULD NOT PLACE, SAID OUT LOUD (index §19.51, the §19.48 rule) ─────────────
    # `note: None` used to mean "nothing to say" whether the engine had paid a month of installments
    # or resolved no schedule at all for 27,000 subscribers. Both of those facts are now stated.
    unresolved = {
        "subscribers": unresolved_subs,
        "by_carrier_id": dict(sorted(unresolved_carriers.items())),
        # A row carrying NO carrier id cannot match a schedule that names one. That is DATA (either
        # the feed does not stamp the carrier, or the configured id names no carrier row) and it is
        # reported as data — the engine never guesses a carrier to make a payout happen.
        "subscribers_with_no_carrier_id": unresolved_carriers.get("", 0),
    }
    notes = []
    if unresolved_subs:
        _named = sorted(k for k in unresolved_carriers if k)
        notes.append(
            f"{unresolved_subs:,} subscriber(s) matched NO payout schedule, so they paid $0.00 — "
            f"{unresolved['subscribers_with_no_carrier_id']:,} carry no carrier id on their row"
            + (f" and {sum(unresolved_carriers[k] for k in _named):,} carry a carrier id no active "
               f"schedule names ({', '.join(_named)})" if _named else "")
            + ". No schedule was assumed; check the schedule's carrier against the feed.")
    _basis_note = basis_tally.note()
    if _basis_note:
        notes.append(_basis_note)
    _unpaid_rep = round(sum((d.get("amount") or 0.0) for d in ledger if not (d.get("rep") or "").strip()), 2)
    if _unpaid_rep:
        notes.append(f"${_unpaid_rep:,.2f} of installment payout names NO rep and is credited to "
                     f"nobody in `by_rep` — it is counted in totals.amount.")
    return {"pay_period": pay_period, "by_rep": by_rep, "ledger": ledger,
            "schedules": len(scheds),
            "totals": {"amount": round(total_amt, 2), "paid": n_paid,
                       "withheld": n_withheld, "pending": n_pending, "reps": len(by_rep),
                       # The §19.48 balance: what the ledger totals, and how much of it reaches a
                       # named rep, so "placed" and "credited" can never silently differ again.
                       "amount_no_rep": _unpaid_rep},
            "month_basis": basis_tally.as_dict(),
            "unresolved_schedule": unresolved,
            # Which unique key the ledger write actually landed under, and whether it completed.
            # `conflict_key` naming the mig-057 narrow key means this period OVERWROTE the stored
            # record of the same instalment in another pay period — mig 1059 is what fixes that, and
            # the caller can see from here that it has not been applied.
            "persisted": persisted,
            "note": " ".join(notes) or None}


# The ledger's unique key, widest first. mig `1059` adds the WIDE one (pay_period included, the
# shape `sale_installment_ledger` has had since mig 201); mig `057`'s NARROW one holds only a single
# pay period per instalment and so overwrites history. ADAPTIVE (index §19.51): `_persist` upserts on
# whichever key the database actually has, so merging this code before mig 1059 is applied writes
# exactly what it wrote before — and a database that HAS been migrated keeps every pay period.
LEDGER_CONFLICT_KEYS = (
    "org_id,subscriber_id,activation_type,month_index,pay_period",   # mig 1059
    "org_id,subscriber_id,activation_type,month_index",              # mig 057
)

#: Ledger columns mig 1059 adds. Written only when the column exists, like the mig-258 tier on the
#: sale-installment ledger — never a write that fails against a pre-1059 database.
LEDGER_OPTIONAL_COLUMNS = ("month_basis",)


def _persist(client, org_id, pay_period, ledger):
    """Upsert the ledger for this pay_period (idempotent on the unique key). Opt-in (persist=True).

    Returns `{"rows", "written", "conflict_key", "optional_columns", "error"}` — a REPORT, because
    this used to be `except Exception: pass` per batch, so a ledger that stopped being written looked
    exactly like a period with nothing to record (the §19.48 class). The caller surfaces it.
    """
    rows = [{
        "org_id": org_id, "subscriber_id": d["subscriber_id"],
        "carrier_id": d.get("carrier_id"), "schedule_id": d.get("schedule_id"),
        "store": d.get("store"), "epay_salesperson": d.get("rep"),
        "activation_type": "*", "activation_period": d.get("activation_period"),
        "pay_period": pay_period, "month_index": d.get("month_index"),
        "payout_kind": d.get("payout_kind"), "mrc_at_pay": d.get("mrc_at_pay"),
        "amount": d.get("amount"), "paid_gate_met": d.get("paid_gate_met"),
        "status": d.get("status"), "source_mi_period": pay_period,
        "month_basis": d.get("month_basis"),
    } for d in ledger if d.get("subscriber_id")]
    out = {"rows": len(rows), "written": 0, "conflict_key": None,
           "optional_columns": [], "error": None}
    if not rows:
        return out

    def _table():
        return client.schema("commcalc").table("subscriber_installments")

    # WHICH OPTIONAL COLUMNS EXIST — probed per column through the one home, so a pre-1059 database
    # simply does not receive them instead of failing the whole batch.
    try:
        present = _col.present_columns(_table, lambda q: q.eq("org_id", org_id),
                                       LEDGER_OPTIONAL_COLUMNS)
    except Exception:
        present = frozenset()
    out["optional_columns"] = sorted(present)
    drop = [c for c in LEDGER_OPTIONAL_COLUMNS if c not in present]
    if drop:
        rows = [{k: v for k, v in r.items() if k not in drop} for r in rows]

    # WHICH UNIQUE KEY THE DATABASE HAS — the wide one is tried first, and a failure falls through to
    # the narrow one. Resolved ONCE on the first batch, so the whole period lands under one key.
    last_err = None
    for key in LEDGER_CONFLICT_KEYS:
        try:
            _table().upsert(rows[:500], on_conflict=key).execute()
        except Exception as e:  # noqa: BLE001 - the next key is tried, and the last error is REPORTED
            last_err = e
            continue
        out["conflict_key"] = key
        out["written"] = len(rows[:500])
        break
    if out["conflict_key"] is None:
        out["error"] = f"{type(last_err).__name__}: {last_err}"
        return out
    for i in range(500, len(rows), 500):
        try:
            _table().upsert(rows[i:i + 500], on_conflict=out["conflict_key"]).execute()
            out["written"] += len(rows[i:i + 500])
        except Exception as e:  # noqa: BLE001 - reported, never silently dropped
            out["error"] = f"{type(e).__name__}: {e}"
            break
    return out
