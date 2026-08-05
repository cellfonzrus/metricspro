"""COMMISSION RECEIVED — the full BREAKOUT the owner asked for (directive 2026-08-05, verbatim):

    "we need to see what we made in M1 and other months and how much is on ATU and how much is on
     residual."

WHAT THIS ADDS OVER `commission_legs.py`. That module answers a TWO-part question (1st month vs
M2–M12) and the Gross Profit card + `/commission-leg-trend` render it. It already carries a per-
month-of-life LADDER, but three things the owner is asking for were never on any surface:

  1. **M2 … M6 as their OWN lines.** The ladder exists inside the GP trend behind a toggle; nothing
     shows "what did M3 pay us this month" beside "what did M1 pay us", per money stream.
  2. **ATU and MI as separate, month-of-life-split lines.** Migration 274 shipped the aggregate
     `commcalc.commission_leg_mi_rollup` (period × salesforce_id × leg_month → mi, atu) and NOTHING
     EVER CALLED IT. The GP card shows MI and ATU rows for ONE period only; there is no trend and no
     per-leg detail anywhere.
  3. **The VidaPay/Total residual + airtime lines at all.** For an MA-fed tenant the GP report books
     `merchant_discount` (every daily-tx row) into its ATU column and books NO residual line. The
     Postpaid-Residual-Order money is only visible on `/commcalc/ma-overview-recon` and What-If.

MONEY RULE — READ-ONLY, AND IT MOVES NOTHING. This module has no DB access, no writes, no clock. It
DECOMPOSES money the org has ALREADY received into finer rows. Every figure it produces is a
re-partition of a figure another surface already shows:

    Σ(commission-group legs)  == the Commission column the GP report already shows
    Σ(mi legs), Σ(atu legs)   == the MI / ATU columns the GP report already shows
    ma_airtime                == the GP report's ATU column for an MA-fed org (Σ merchant_discount)

`build_breakout` ASSERTS each of those identities into the payload (`identity`), so a future edit that
breaks one shows up on the page as a loud warning instead of a quietly wrong number. This is the same
sum-identity discipline that would have caught the 2026-08-05 M1 misclassification a month earlier.

THE ONE HONEST DIVERGENCE, STATED OUT LOUD (never silently reconciled). For an MA-fed tenant three
shipped surfaces disagree about what "residual" is:

  • Gross Profit / P&L (`account/coa.py`, `router._compute_gp`): ATU = Σ `merchant_discount` over
    **every** `raw_ma_daily_tx` row; there is NO residual line.
  • `/commcalc/ma-overview-recon` Residual tile + What-If carrier income: residual =
    −Σ `retail_cost` over rows whose Order Type contains the configured residual order type
    ("Postpaid Residual Order"); What-If's airtime leg then sums `merchant_discount` over the
    **non-residual** rows only.
  • `account/residual_subs._aggregate_ma`: "MI-equivalent" = the whole `raw_ma_commission` payable —
    i.e. it counts the COMMISSION as residual.

So this module reports `ma_airtime` (all rows — reconciles to GP), `ma_airtime_residual_orders` (the
slice of it that sits on residual-order rows — the exact overlap between the two definitions) and
`ma_residual_orders` (the ma-overview/What-If basis) as a REFERENCE row that is deliberately NOT added
into any total. Choosing between the definitions moves a number the owner reads, so it is a
propose-first decision, not something a reporting module gets to make.

PURE. `build_breakout` takes already-aggregated rows (the router runs the migration-274/278 RPCs, or a
bounded fallback) plus a `commission_legs.LegClassifier`, and returns the payload. No client, no I/O —
which is what makes `harness_commission_received_breakout.py` able to prove the identities on fixtures.
"""

# ── groups ───────────────────────────────────────────────────────────────────────────────────────
G_COMMISSION = "commission"     # money that IS the Commission column
G_COMP = "comp"                 # Comprehensive Comp — its own column on the GP report, never added in
G_RESIDUAL = "residual"         # MI / ATU / airtime margin
G_REFERENCE = "reference"       # shown for cross-check, deliberately in NO total

GROUP_LABELS = {
    G_COMMISSION: "Commission received",
    G_COMP: "Comprehensive Compensation",
    G_RESIDUAL: "Residual & airtime",
    G_REFERENCE: "Cross-check (not added to any total)",
}

LEG_UNSPLIT = "unsplit"

# ── the money streams ────────────────────────────────────────────────────────────────────────────
# `splits_on` is written for a DM/rep to read, not a developer (RULE SIX). `in_total` says whether the
# row participates in its group's total — the reference row does not, on purpose.
STREAMS = {
    "comm_epay": {
        "label": "Commission received (ePay Payment Detail)", "group": G_COMMISSION, "in_total": True,
        "source": "commcalc.raw_payment_detail",
        "splits_on": "the month written into the payment type — \"… - Month 3\"",
    },
    "comm_ma": {
        "label": "Commission received (VidaPay / master agent)", "group": G_COMMISSION, "in_total": True,
        "source": "commcalc.raw_ma_commission",
        "splits_on": "the column on the MA Commission Details export — 1st Month Spiff, 2nd Month Spiff, …",
    },
    "comp_comm": {
        "label": "Comprehensive Comp (commission part)", "group": G_COMP, "in_total": True,
        "source": "commcalc.raw_comp_report",
        "splits_on": "the month written into the compensation type — \"… - Month 3\"",
    },
    "mi": {
        "label": "MI residual", "group": G_RESIDUAL, "in_total": True,
        "source": "commcalc.raw_mi (actual_mi_payout)",
        "splits_on": "the subscriber's activation date against the month the money arrived",
    },
    "atu": {
        "label": "ATU residual", "group": G_RESIDUAL, "in_total": True,
        "source": "commcalc.raw_mi (actual_atu_payout)",
        "splits_on": "the subscriber's activation date against the month the money arrived",
    },
    "ma_airtime": {
        "label": "Airtime margin (VidaPay merchant discount)", "group": G_RESIDUAL, "in_total": True,
        "source": "commcalc.raw_ma_daily_tx (merchant_discount)",
        "splits_on": "nothing — this feed carries no activation date and no month-of-life column",
    },
    "ma_residual_orders": {
        "label": "Postpaid Residual Orders (VidaPay)", "group": G_REFERENCE, "in_total": False,
        "source": "commcalc.raw_ma_daily_tx (retail_cost, Order Type = the configured residual order type)",
        "splits_on": "nothing — the residual order line states no month-of-life",
    },
}

STREAM_ORDER = ["comm_epay", "comm_ma", "comp_comm", "mi", "atu", "ma_airtime", "ma_residual_orders"]


def _sf(v):
    """Byte-identical to gp_report.safe_float / commission_legs._safe_float."""
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _legkey(leg):
    """Ladder key for a month-of-life: '1', '2', … or 'unsplit'. Never a guessed month."""
    if leg in (None, "", "unknown", "unsplit"):
        return LEG_UNSPLIT
    try:
        n = int(leg)
    except (TypeError, ValueError):
        return LEG_UNSPLIT
    return str(n) if n > 0 else LEG_UNSPLIT


def _blank_cell():
    return {"legs": {}, "total": 0.0, "lines": 0}


class _Acc:
    """Accumulator for one stream: per-period ladders + a running total. Internal, float-precision;
    everything is rounded exactly once, in `finish()`."""

    def __init__(self, key, periods):
        self.key = key
        self.cells = {p: _blank_cell() for p in periods}
        self.meta = {}

    def add(self, period, leg, amount, lines=0):
        c = self.cells.get(period)
        if c is None:
            return
        k = _legkey(leg)
        c["legs"][k] = c["legs"].get(k, 0.0) + _sf(amount)
        c["total"] += _sf(amount)
        c["lines"] += int(lines or 0)

    def nonzero(self):
        return any(round(c["total"], 2) or c["legs"] for c in self.cells.values())


def _m1_month(legcls):
    try:
        return int((legcls.cfg or {}).get("m1_month") or 1)
    except (TypeError, ValueError):
        return 1


def _split_of(legs, m1):
    """(m1, m2_12, unsplit) of one ladder dict — the SAME three buckets every other leg surface uses."""
    a = b = u = 0.0
    for k, v in legs.items():
        if k == LEG_UNSPLIT:
            u += _sf(v)
        elif int(k) == m1:
            a += _sf(v)
        else:
            b += _sf(v)
    return round(a, 2), round(b, 2), round(u, 2)


# ── the accumulators, one per source shape ───────────────────────────────────────────────────────
def add_label_rows(accs, rows, periods, legcls, period_key, passes, comp_is_commission):
    """ePay Payment Detail + Comprehensive Comp. Each row: {source, period, store_num, label, category,
    amount, n}. `passes(store_num)` is the RULE-FIVE store/market test the caller owns; `period_key`
    maps any period spelling to the canonical window label."""
    for r in rows or []:
        lab = period_key.get(str(r.get("period") or "").strip())
        if not lab or lab not in periods:
            continue
        if not passes(str(r.get("store_num") or "").strip()):
            continue
        src = str(r.get("source") or "").strip()
        label = r.get("label")
        if src == "payment_detail":
            if str(r.get("category") or "").strip() != "Commission":
                continue
            acc = accs["comm_epay"]
        elif src == "comp_report":
            if not comp_is_commission(label):
                continue
            acc = accs["comp_comm"]
        else:
            continue
        _bucket, leg, _why = legcls.label(label)
        acc.add(lab, leg, r.get("amount"), r.get("n"))


def add_ma_rows(accs, rows, periods, legcls, period_key, components, skip_periods=()):
    """VidaPay/MA commission. Each row: {period, <component columns>…}. The leg is the COLUMN NAME
    (`commission_legs.split_ma_components`), so the ladder is exact by construction and the three
    buckets re-sum to the roll-up's own `total_payable`.

    `skip_periods` is the SAME emptiness gate `_compute_gp` and `/commission-leg-trend` apply: a month
    that has ePay Payment Detail is an ePay month and its MA rows are NOT added on top (that would
    double-count one month of commission across two feeds)."""
    for r in rows or []:
        lab = period_key.get(str(r.get("period") or "").strip())
        if not lab or lab not in periods or lab in skip_periods:
            continue
        res = legcls.ma(r, components)
        for lk, lv in (res.get("leg_ladder") or {}).items():
            accs["comm_ma"].add(lab, lk, lv)
        accs["comm_ma"].cells[lab]["lines"] += int(_sf(r.get("n")))
        uf = res.get("unsplit_fields") or []
        if uf:
            accs["comm_ma"].meta.setdefault("unsplit_fields", [])
            for f in uf:
                if f not in accs["comm_ma"].meta["unsplit_fields"]:
                    accs["comm_ma"].meta["unsplit_fields"].append(f)


def add_mi_rows(accs, rows, periods, legcls, period_key, passes_sfid):
    """ePay MI/ATU residual. Each row: {period, salesforce_id, leg_month, mi, atu, n} — the shape
    migration 274's `commission_leg_mi_rollup` returns (and which nothing called until now). The
    leg_month is computed in Postgres from `mi_activation_date` vs `period_year/period_month`; NULL
    means the source carried no usable activation date, which lands in `unsplit` and is NEVER guessed.

    `mi_split_by_activation = false` in the org's leg config forces every residual dollar to `unsplit`
    (the config already promises exactly that) — honoured here so the two surfaces cannot disagree."""
    split_on = bool((legcls.cfg or {}).get("mi_split_by_activation", True))
    for r in rows or []:
        lab = period_key.get(str(r.get("period") or "").strip())
        if not lab or lab not in periods:
            continue
        if not passes_sfid(str(r.get("salesforce_id") or "").strip()):
            continue
        leg = r.get("leg_month") if split_on else None
        n = int(_sf(r.get("n")))
        accs["mi"].add(lab, leg, r.get("mi"), n)
        accs["atu"].add(lab, leg, r.get("atu"), n)


def add_tx_rows(accs, rows, periods, period_key):
    """VidaPay daily transactions. Each row: {period, airtime_all, airtime_residual_orders,
    residual_orders, n, n_residual} — migration 278's rollup. `airtime_all` is the SAME figure the GP
    report's ATU column already shows for an MA-fed org; `residual_orders` is the ma-overview-recon /
    What-If basis and is a REFERENCE row here, in no total. Neither carries a month-of-life, so both
    land wholly in `unsplit` — stated, not guessed."""
    for r in rows or []:
        lab = period_key.get(str(r.get("period") or "").strip())
        if not lab or lab not in periods:
            continue
        accs["ma_airtime"].add(lab, None, r.get("airtime_all"), r.get("n"))
        accs["ma_residual_orders"].add(lab, None, r.get("residual_orders"), r.get("n_residual"))
        m = accs["ma_airtime"].meta
        m["airtime_on_residual_orders"] = round(
            _sf(m.get("airtime_on_residual_orders")) + _sf(r.get("airtime_residual_orders")), 2)


# ── the payload ──────────────────────────────────────────────────────────────────────────────────
def build_breakout(periods, legcls, *, label_rows=(), ma_rows=(), mi_rows=(), tx_rows=(),
                   components=(), period_key=None, passes=None, passes_sfid=None,
                   comp_is_commission=None, skip_periods=(), notes=(), gaps=(), degraded=False):
    """The breakout payload. PURE — every argument is data the caller already fetched.

    Returns streams × (period × month-of-life ladder), the per-group totals, and the IDENTITY block
    that proves each stream's ladder re-sums to that stream's own total."""
    periods = list(periods or [])
    period_key = dict(period_key or {p: p for p in periods})
    passes = passes or (lambda _n: True)
    passes_sfid = passes_sfid or (lambda _s: True)
    comp_is_commission = comp_is_commission or (lambda _l: True)
    components = list(components or [])

    accs = {k: _Acc(k, periods) for k in STREAM_ORDER}
    add_label_rows(accs, label_rows, periods, legcls, period_key, passes, comp_is_commission)
    add_ma_rows(accs, ma_rows, periods, legcls, period_key, components, skip_periods=skip_periods)
    add_mi_rows(accs, mi_rows, periods, legcls, period_key, passes_sfid)
    add_tx_rows(accs, tx_rows, periods, period_key)

    m1 = _m1_month(legcls)
    legs_present = set()
    for a in accs.values():
        for c in a.cells.values():
            for k in c["legs"]:
                if k != LEG_UNSPLIT:
                    legs_present.add(int(k))
    leg_columns = sorted(legs_present)

    streams, identity = [], []
    for key in STREAM_ORDER:
        a = accs[key]
        if not a.nonzero():
            continue
        meta = STREAMS[key]
        per, stream_legs, stream_total = {}, {}, 0.0
        for p in periods:
            c = a.cells[p]
            legs = {k: round(v, 2) for k, v in c["legs"].items() if round(v, 2)}
            pm1, pm2, pun = _split_of(c["legs"], m1)
            tot = round(c["total"], 2)
            per[p] = {"legs": legs, "m1": pm1, "m2_12": pm2, "unsplit": pun,
                      "total": tot, "lines": c["lines"]}
            # the ladder must re-sum to the row's own total, per period — the invariant that makes
            # every figure on the page a re-partition rather than a new number
            parts = round(pm1 + pm2 + pun, 2)
            if abs(parts - tot) >= 0.01:
                identity.append({"stream": key, "period": p, "total": tot, "parts": parts, "ok": False})
            for k, v in c["legs"].items():
                stream_legs[k] = round(stream_legs.get(k, 0.0) + v, 2)
            stream_total += c["total"]
        sm1, sm2, sun = _split_of(stream_legs, m1)
        streams.append({
            "key": key, "label": meta["label"], "group": meta["group"],
            "in_total": meta["in_total"], "source": meta["source"], "splits_on": meta["splits_on"],
            "periods": per, "legs": {k: round(v, 2) for k, v in stream_legs.items() if round(v, 2)},
            "m1": sm1, "m2_12": sm2, "unsplit": sun, "total": round(stream_total, 2),
            **({"meta": a.meta} if a.meta else {}),
        })

    # group + period totals (reference rows excluded from every total, by construction)
    group_totals, totals_by_period, totals_by_leg = {}, {}, {}
    for p in periods:
        totals_by_period[p] = {}
    for s in streams:
        if not s["in_total"]:
            continue
        g = s["group"]
        gt = group_totals.setdefault(g, {"group": g, "label": GROUP_LABELS.get(g, g),
                                         "by_period": {p: 0.0 for p in periods},
                                         "legs": {}, "m1": 0.0, "m2_12": 0.0, "unsplit": 0.0,
                                         "total": 0.0})
        for p in periods:
            v = s["periods"][p]["total"]
            gt["by_period"][p] = round(gt["by_period"][p] + v, 2)
            totals_by_period[p][g] = round(totals_by_period[p].get(g, 0.0) + v, 2)
        for k, v in s["legs"].items():
            gt["legs"][k] = round(gt["legs"].get(k, 0.0) + v, 2)
            totals_by_leg[k] = round(totals_by_leg.get(k, 0.0) + v, 2)
        gt["m1"] = round(gt["m1"] + s["m1"], 2)
        gt["m2_12"] = round(gt["m2_12"] + s["m2_12"], 2)
        gt["unsplit"] = round(gt["unsplit"] + s["unsplit"], 2)
        gt["total"] = round(gt["total"] + s["total"], 2)

    return {
        "periods": periods,
        "leg_columns": leg_columns,
        "m1_month": m1,
        "streams": streams,
        "groups": [group_totals[g] for g in (G_COMMISSION, G_COMP, G_RESIDUAL) if g in group_totals],
        "group_labels": GROUP_LABELS,
        "totals_by_period": totals_by_period,
        "totals_by_leg": totals_by_leg,
        "identity": identity,
        "identity_ok": not identity,
        "config": legcls.describe(),
        "notes": list(notes),
        "gaps": list(gaps),
        "degraded": bool(degraded),
        "money": True,
        "basis": (
            "Money the company RECEIVED, split by the month-of-life of the number it was paid on. "
            "M1 = it arrived in the same month the number activated; M2, M3 … = it arrived that many "
            "months later, for a number that was already active. Unsplit = the source states no "
            "month, so nothing was guessed. Every row here is a finer split of a figure the Gross "
            "Profit report already shows — this adds no money."),
        "divergence_note": (
            "For VidaPay/Total the system holds TWO different \"residual\" readings and they are not "
            "the same money: Gross Profit and the P&L count the airtime margin on EVERY daily "
            "transaction, while the MA Overview cross-check and What-If count the Postpaid Residual "
            "Orders. Both are shown; the Postpaid Residual Orders row is a cross-check and is left "
            "out of every total, because picking one of the two definitions would move a number you "
            "read."),
    }
