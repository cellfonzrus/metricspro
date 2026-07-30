"""What-If / Scenario Analysis — CARRIER-AGNOSTIC. Reuses the LIVE commission, residual and sales model so
projections match the real engine, but resolves EVERY comp/residual/income source per the SELECTED carrier
(config-driven, RULE TWO) instead of hard-coding Boost comp payment types.

Four tools, all read-only:
  1. activation_baseline  → an employee-payout scenario TEMPLATE for the selected carrier. Boost carriers
                            keep the legacy 8-component template (byte-identical); non-Boost carriers'
                            components auto-populate from their configured Commission Plans / rules / tiers
                            + payout_schedule installments. No pay source → an explicit empty state.
  2. byod_residual        → BYOD → recurring-residual analysis. Residual source resolved per carrier:
                            Boost → raw_mi MI+ATU (unchanged); MA-fed carriers → raw_ma_daily_tx
                            residual-order rows (sign-normalized) joined with raw_ma_commission (M1-M6 +
                            rebate per IMEI/phone).
  3. accessory_byod_corr  → per store/period BYOD activations vs accessory revenue vs total revenue.
  4. carrier_income       → the COMPANY perspective: what the carrier / master-agent pays the company.
                            Boost → Comprehensive Comp + MI+ATU; MA-fed → raw_ma_commission (M1-M6 spiffs +
                            rebate) + raw_ma_daily_tx (residual + airtime margin).

Source selection is CONFIG (commcalc.whatif_source_config, mig 209 + mig 252): the residual order-type
string, the residual $ column, the sign normalization, the MA-commission sign and which source feeds each
view are all editable, never code constants. Degrades to mode-derived code defaults (Boost byte-identical)
when mig 209 hasn't run.

MONEY-COLUMN RULE (2026-07-30, after the -$492,946,277,716 "residual"): raw_ma_daily_tx.merchant_invoice
is the Merchant Invoice NUMBER, not money. Only _MA_MONEY_COLUMNS may be summed as dollars; the residual
default is `retail_cost` — the same signed column the canonical Commission Ledger books from. Read-only
module: nothing here writes, pays, or recomputes.
"""
import calendar
from app.modules.commcalc.calculator import classify_contract_type, safe_float
from app.modules.account import residual_subs

_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}
_NIL_CARRIER = "00000000-0000-0000-0000-000000000000"
_HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# ─── MA numeric columns: which are MONEY and which are IDENTIFIERS ─────────────────────────────────
# raw_ma_daily_tx carries THREE numeric columns and one of them is not money at all: `merchant_invoice`
# is the Merchant Invoice NUMBER (mig 083 declared it NUMERIC; mig 207 maps the file's "Merchant Invoice"
# header into it). Summing it as the residual amount is what reported -$492,946,277,716 of May-2026
# "residual" on this page (owner report + finance root cause, 2026-07-30). The single source of truth for
# the roles is the MA column catalogue in ma_upload.FIELD_LABELS ("merchant_invoice" -> role "key",
# "retail_cost" -> role "money"); the sets below are CHECKED against it at import so this module cannot
# silently drift back into treating an identifier as dollars. The check is NON-FATAL by design: a
# catalogue rename must surface as a flag, never take the whole commcalc router down on import.
_MA_MONEY_COLUMNS = ("retail_cost", "merchant_discount")    # signed $ (negative = paid to the dealer)
_MA_IDENTIFIER_COLUMNS = ("merchant_invoice",)              # NUMERIC, but an ID — never a $ amount
_MA_SPIFF_FIELDS = tuple(f"spiff_m{i}" for i in range(1, 7))
_MA_COLUMN_ROLE_DRIFT = []
try:
    from app.modules.commcalc.ma_upload import FIELD_LABELS as _MA_FIELD_LABELS
    for _c in _MA_IDENTIFIER_COLUMNS:
        if (_MA_FIELD_LABELS.get(_c) or {}).get("role") != "key":
            _MA_COLUMN_ROLE_DRIFT.append(_c)
    for _c in _MA_MONEY_COLUMNS:
        if (_MA_FIELD_LABELS.get(_c) or {}).get("role") not in (None, "money"):
            _MA_COLUMN_ROLE_DRIFT.append(_c)
except Exception:                                           # catalogue unavailable → the sets stand alone
    pass


def is_ma_money_column(field):
    """True when `field` may be summed as dollars off an MA row. An identifier column (the Merchant
    Invoice NUMBER) is NEVER money, whatever its Postgres type says."""
    f = (field or "").strip()
    return bool(f) and f not in _MA_IDENTIFIER_COLUMNS


def _pvariants(period: str):
    """Match both period spellings: 'June 2026' <-> '2026-06'."""
    p = (period or "").strip()
    out = {p}
    parts = p.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        out.add(f"{parts[1]}-{_MONTHS[parts[0]]:02d}")
    elif len(p) >= 7 and p[:4].isdigit() and p[4] == "-":
        try:
            out.add(f"{calendar.month_name[int(p[5:7])]} {p[:4]}")
        except Exception:
            pass
    return list(out)


def _list_periods(client, org_id, limit=18, tables=("raw_sales", "daily_sales_feed")):
    """Distinct month-name periods that actually have rows, newest first."""
    seen = {}
    for tbl in tables:
        try:
            rows = (client.schema("commcalc").table(tbl).select("period,period_year,period_month")
                    .eq("org_id", org_id).limit(200000).execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            p = r.get("period")
            if not p:
                continue
            y, m = r.get("period_year"), r.get("period_month")
            key = (y or 0, m or 0, p)
            seen[p] = key
    ordered = sorted(seen.values(), reverse=True)
    return [p for (_y, _m, p) in ordered][:limit]


# ─── carrier context + source config (RULE TWO) ────────────────────────────────────────────────────
def _carrier_ctx(client, org_id, carrier_id=None):
    """(carriers[], picked_carrier_or_None, carrier_mode). Lazy-imports the router's single-source
    carrier-mode resolver so this stays free of duplicated (and drift-prone) carrier-name logic."""
    try:
        carriers = (client.schema("commcalc").table("carrier").select("id,name,code,is_default")
                    .eq("org_id", org_id).order("name").execute().data) or []
    except Exception:
        carriers = []
    try:
        from app.modules.commcalc.router import _resolve_carrier_mode
    except Exception:
        def _resolve_carrier_mode(cs):
            def _is_boost(c):
                return 'boost' in ((c.get('code') or '') + ' ' + (c.get('name') or '')).lower()
            cs = cs or []
            if not cs:
                return 'boost'
            d = next((c for c in cs if c.get('is_default')), None)
            if d is not None:
                return 'boost' if _is_boost(d) else 'plan'
            return 'boost' if any(_is_boost(c) for c in cs) else 'plan'
    picked = None
    if carrier_id:
        picked = next((c for c in carriers if str(c.get("id")) == str(carrier_id)), None)
    if picked is None:
        picked = next((c for c in carriers if c.get("is_default")), None)
    mode = _resolve_carrier_mode([picked]) if picked else _resolve_carrier_mode(carriers)
    return carriers, picked, mode


# `residual_amount_field` = retail_cost in BOTH modes (mig 252 applies the same correction to the mig-209
# seed rows). retail_cost is the SAME signed column the canonical Commission Ledger books its MA payout
# lines from (column_mapping.py maps the "Retail Cost" header onto the ledger's raw_amount), so the
# residual view and the ledger can no longer disagree about which number is the money. It is unused in
# boost mode (Boost residual comes from raw_mi) but set anyway, so no future MA-fed carrier inherits the
# wrong default. `ma_commission_sign` normalizes raw_ma_commission money columns to INCOME the way
# residual_sign does for daily-tx rows: on the MA Commission Details export NEGATIVE = paid to the dealer.
_CFG_DEFAULTS = {
    "boost": {"residual_source": "boost_mi_atu", "residual_order_type": None,
              "residual_amount_field": "retail_cost", "residual_sign": "as_is",
              "income_source": "boost_comp_mi_atu", "retail_cost_source": "none",
              "ma_commission_sign": "negate"},
    "plan": {"residual_source": "ma_daily_tx", "residual_order_type": "Postpaid Residual Order",
             "residual_amount_field": "retail_cost", "residual_sign": "negate",
             "income_source": "ma", "retail_cost_source": "ma_pr_activation",
             "ma_commission_sign": "negate"},
}
_CFG_KEYS = ("residual_source", "residual_order_type", "residual_amount_field", "residual_sign",
             "income_source", "retail_cost_source", "ma_commission_sign")


def _whatif_source_config(client, org_id, carrier_id, carrier_mode):
    """Resolve the What-If source config (mig 209) for (org, carrier). Order:
      1. org row for the exact carrier_id
      2. org mode-default row (carrier_id = nil, carrier_mode = mode)
      3. HOUSE mode-default row (every tenant inherits the two seeds)
      4. code fallback per mode (Boost byte-identical)
    Degrades to (4) when the table is absent. Never raises."""
    mode = carrier_mode if carrier_mode in _CFG_DEFAULTS else "boost"
    base = dict(_CFG_DEFAULTS[mode])
    base["_resolved_from"] = "code_default"
    try:
        rows = (client.schema("commcalc").table("whatif_source_config").select("*")
                .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    except Exception:
        rows = []
    house_rows = []
    if org_id != _HOUSE_ORG:
        try:
            house_rows = (client.schema("commcalc").table("whatif_source_config").select("*")
                          .eq("org_id", _HOUSE_ORG).eq("is_active", True).execute().data) or []
        except Exception:
            house_rows = []

    def _pick(candidates, match):
        for r in candidates:
            if match(r):
                return r
        return None

    cid = str(carrier_id) if carrier_id else None
    chosen, src = None, None
    if cid:
        chosen = _pick(rows, lambda r: str(r.get("carrier_id")) == cid and str(r.get("carrier_id")) != _NIL_CARRIER)
        if chosen:
            src = "org_carrier"
    if chosen is None:
        chosen = _pick(rows, lambda r: str(r.get("carrier_id")) == _NIL_CARRIER and (r.get("carrier_mode") or "boost") == mode)
        if chosen:
            src = "org_mode_default"
    if chosen is None:
        chosen = _pick(house_rows, lambda r: str(r.get("carrier_id")) == _NIL_CARRIER and (r.get("carrier_mode") or "boost") == mode)
        if chosen:
            src = "house_mode_default"
    if chosen is not None:
        merged = dict(base)
        for k in _CFG_KEYS:
            v = chosen.get(k)
            if v is not None and v != "":
                merged[k] = v
        merged["_resolved_from"] = src
        return merged
    return base


# ─── 1. Activation-mix employee-payout template ────────────────────────────────────────────────────
_RATE_DEFAULTS = {
    "premium_flat": 5, "byod_flat": 3, "byod_extra_spiff": 0, "upgrade_flat": 20,
    "acc_rate": 0.10, "setup_fee_rate": 0.10, "trade_in_spiff": 20, "acima_spiff": 25,
    "tier_100_min_kpis": 7, "tier_75_min_kpis": 5, "tier_75_pct": 0.75, "tier_50_pct": 0.50,
    "straight_line": False,
}


def _rates(client, org_id, period):
    row = {}
    try:
        r = (client.schema("commcalc").table("payout_config").select("*")
             .eq("org_id", org_id).in_("period", _pvariants(period)).limit(1).execute().data) or []
        if r:
            row = r[0]
    except Exception:
        pass
    return {k: (dv if row.get(k) is None else row.get(k)) for k, dv in _RATE_DEFAULTS.items()}


def _boost_actuals(client, org_id, period, rates):
    """Baseline actuals for the Boost template — UNCHANGED aggregation over rep_commissions."""
    rc = (client.schema("commcalc").table("rep_commissions")
          .select("premium_acts,byod_acts,upgrade_acts,acc_comm,setup_fee_comm,trade_in_comm,"
                  "acima_comm,subtotal,total_payout,tier")
          .eq("org_id", org_id).in_("period", _pvariants(period)).limit(100000).execute().data) or []
    agg = {k: 0.0 for k in ("acc_comm", "setup_fee_comm", "trade_in_comm", "acima_comm", "subtotal", "total_payout")}
    cnt = {k: 0 for k in ("premium_acts", "byod_acts", "upgrade_acts")}
    tiers = []
    for r in rc:
        for k in cnt:
            cnt[k] += int(r.get(k) or 0)
        for k in agg:
            agg[k] += safe_float(r.get(k))
        if r.get("tier") is not None:
            tiers.append(safe_float(r.get("tier")))

    def _div(a, b):
        return round(a / b, 2) if b else 0.0

    return {
        "premium_acts": cnt["premium_acts"], "byod_acts": cnt["byod_acts"], "upgrade_acts": cnt["upgrade_acts"],
        "acc_sales": _div(agg["acc_comm"], rates["acc_rate"]),
        "setup_sales": _div(agg["setup_fee_comm"], rates["setup_fee_rate"]),
        "trade_ins": round(_div(agg["trade_in_comm"], rates["trade_in_spiff"])),
        "acima_count": round(_div(agg["acima_comm"], rates["acima_spiff"])),
        "subtotal": round(agg["subtotal"], 2), "total_payout": round(agg["total_payout"], 2),
        "avg_tier": round(sum(tiers) / len(tiers), 3) if tiers else 1.0,
        "reps": len(rc),
    }


def _boost_template(rates, actuals):
    """The legacy 8-component Boost template, seeded FROM the legacy comp components. Each component's
    (qty, rate, current_comm) reproduces exactly what the frontend hard-coded today → byte-identical."""
    byod_rate = safe_float(rates.get("byod_flat")) + safe_float(rates.get("byod_extra_spiff"))
    rows = [
        ("premium", "Premium / New Activation", "flat", "acts", safe_float(rates["premium_flat"]), actuals["premium_acts"]),
        ("byod", "BYOD Activation", "flat", "acts", byod_rate, actuals["byod_acts"]),
        ("upgrade", "Upgrade", "flat", "acts", safe_float(rates["upgrade_flat"]), actuals["upgrade_acts"]),
        ("trade", "Trade-In", "flat", "units", safe_float(rates["trade_in_spiff"]), actuals["trade_ins"]),
        ("acima", "ACIMA Lease", "flat", "units", safe_float(rates["acima_spiff"]), actuals["acima_count"]),
        ("acc", "Accessory Sales", "pct", "$ sales", safe_float(rates["acc_rate"]), actuals["acc_sales"]),
        ("setup", "Setup Fees", "pct", "$ sales", safe_float(rates["setup_fee_rate"]), actuals["setup_sales"]),
    ]
    comps = []
    for key, label, kind, unit, rate, qty in rows:
        comps.append({"key": key, "label": label, "kind": kind, "unit": unit,
                      "rate": rate, "qty": qty, "current_comm": round(safe_float(qty) * rate, 2),
                      "source": "boost_rates"})
    tier = {
        "baseline": actuals["avg_tier"], "metric": "kpi_count",
        "options": [{"label": "Actual", "value": actuals["avg_tier"]},
                    {"label": "100%", "value": 1}, {"label": "75%", "value": 0.75}, {"label": "50%", "value": 0.5}],
    }
    return comps, tier


def _derive_qty(payout, rate):
    """Base quantity so qty × rate == the measured payout (mirrors how the Boost acc/setup rows back out
    'acc_sales' from acc_comm ÷ acc_rate). Keeps the projector's Σ(qty×rate) consistent with reality."""
    return round(payout / rate, 2) if rate else 0.0


def _plan_template(client, org_id, period, carrier):
    """Build the employee-payout template for a NON-Boost carrier from its configured pay sources:
    Commission Plan rules (+ tiers) and payout_schedule installments. Baseline qty/current-$ come from the
    read-only commission_engine preview. Empty state (R1-style) when the carrier has no pay source."""
    from app.modules.commcalc import commission_engine
    plans, ready = commission_engine._load_plans(client, org_id)
    if not ready:
        return {"template_empty": True,
                "reason": "The configurable Commission-Plan engine isn't set up for this org yet "
                          "(migration 059 not applied).",
                "configure_url": "/commcalc/commission-plans"}
    cid = str(carrier.get("id")) if carrier else None

    def _applies(p):
        pc = p.get("carrier_id")
        return (pc in (None, "")) or (cid is not None and str(pc) == cid)

    cplans = [p for p in plans if p.get("is_active", True) and _applies(p)]
    cplan_ids = {str(p.get("id")) for p in cplans}
    n_assign = sum(len(p.get("assignments") or []) for p in cplans)

    # payout_schedule installments for this carrier (carrier_id match or NULL = any)
    scheds, sched_lines = [], []
    try:
        scheds = (client.schema("commcalc").table("payout_schedule").select("*")
                  .eq("org_id", org_id).eq("is_active", True).execute().data) or []
        sched_lines = (client.schema("commcalc").table("payout_schedule_line").select("*")
                       .eq("org_id", org_id).execute().data) or []
    except Exception:
        scheds, sched_lines = [], []
    cscheds = [s for s in scheds if (s.get("carrier_id") in (None, "")) or (cid is not None and str(s.get("carrier_id")) == cid)]

    if n_assign == 0 and not cscheds:
        return {"template_empty": True,
                "reason": ("No commission plan is assigned to this carrier and it has no payout schedule — "
                           "there is nothing configured to pay reps from, so a scenario can't be modeled."),
                "configure_url": "/commcalc/commission-plans"}

    # baseline from the read-only preview (never writes rep_commissions)
    agg_rule, base_subtotal, total_payout, rep_plan_ids = {}, 0.0, 0.0, set()
    try:
        pv = commission_engine.preview(client, org_id, period)
        for rep in (pv.get("by_rep") or []):
            if cplan_ids and str(rep.get("plan_id")) not in cplan_ids:
                continue
            rep_plan_ids.add(str(rep.get("plan_id")))
            total_payout += safe_float(rep.get("total_payout"))
            base_subtotal += safe_float(rep.get("base_payout")) + safe_float(rep.get("tiered_payout"))
            for rb in (rep.get("rules") or []):
                a = agg_rule.setdefault(str(rb.get("rule_id")),
                                        {"payout": 0.0, "units": 0, "matched": 0})
                a["payout"] += safe_float(rb.get("payout"))
                a["units"] += int(rb.get("qualifying_units") or 0)
                a["matched"] += int(rb.get("matched_lines") or 0)
    except Exception:
        agg_rule = {}

    comps = []
    for p in cplans:
        for rule in (p.get("rules") or []):
            kind = (rule.get("payout_kind") or "flat_per_unit").strip().lower()
            is_pct = kind in ("pct_mrc", "pct_gp", "pct_price_over_cost")
            rate = safe_float(rule.get("pct")) if is_pct else safe_float(rule.get("amount"))
            stats = agg_rule.get(str(rule.get("id")), {})
            payout = safe_float(stats.get("payout"))
            qty = _derive_qty(payout, rate) if payout else (int(stats.get("units") or 0) if not is_pct else 0.0)
            comps.append({
                "key": f"rule:{rule.get('id')}",
                "label": rule.get("label") or rule.get("match_value") or rule.get("match_field") or "Rule",
                "kind": "pct" if is_pct else "flat",
                "unit": (rule.get("match_field") or "units"),
                "rate": rate, "qty": qty, "current_comm": round(payout, 2),
                "payout_kind": kind, "tiered": bool(rule.get("tiered")),
                "plan_name": p.get("name"), "source": "commission_plan",
            })

    lines_by_sched = {}
    for ln in sched_lines:
        lines_by_sched.setdefault(str(ln.get("schedule_id")), []).append(ln)
    for s in cscheds:
        act = s.get("activation_type") or "*"
        for ln in sorted(lines_by_sched.get(str(s.get("id")), []), key=lambda x: int(x.get("month_index") or 0)):
            kind = (ln.get("payout_kind") or "flat").strip().lower()
            is_pct = kind == "pct_mrc"
            rate = safe_float(ln.get("mrc_pct")) if is_pct else safe_float(ln.get("flat_amount"))
            comps.append({
                "key": f"sched:{s.get('id')}:{ln.get('month_index')}",
                "label": f"Installment M{ln.get('month_index')} ({act})",
                "kind": "pct" if is_pct else "flat",
                "unit": "subs", "rate": rate, "qty": 0, "current_comm": 0.0,
                "payout_kind": kind, "tiered": False,
                "plan_name": "Payout schedule", "source": "payout_schedule",
            })

    # tier options from the first tiered plan
    tier = {"baseline": 1.0, "metric": None, "options": [{"label": "×1.00", "value": 1}]}
    for p in cplans:
        ts = p.get("tiers") or []
        if ts and (p.get("base_tier_metric") or "none").strip().lower() not in ("", "none"):
            opts = [{"label": "×1.00", "value": 1}]
            for t in sorted(ts, key=lambda x: int(x.get("min_count") or 0)):
                opts.append({"label": f"≥{int(t.get('min_count') or 0)} → ×{safe_float(t.get('multiplier')) or 1}",
                             "value": safe_float(t.get("multiplier")) or 1})
            tier = {"baseline": 1.0, "metric": p.get("base_tier_metric"), "options": opts}
            break

    actuals = {"subtotal": round(base_subtotal, 2), "total_payout": round(total_payout, 2),
               "avg_tier": 1.0, "reps": len(rep_plan_ids)}
    return {"components": comps, "tier": tier, "actuals": actuals}


def activation_baseline(client, org_id, period, carrier_id=None):
    """What-If tool #1 (carrier-agnostic employee payout). Boost → the legacy 8-component template
    (byte-identical); non-Boost → components from configured plans/rules/tiers + payout_schedule; no pay
    source → an explicit empty state pointing at /commcalc/commission-plans."""
    carriers, picked, mode = _carrier_ctx(client, org_id, carrier_id)
    carrier_meta = ({"id": picked.get("id"), "name": picked.get("name"), "code": picked.get("code"),
                     "is_default": bool(picked.get("is_default"))} if picked else None)
    base = {"period": period, "carrier": carrier_meta, "carrier_mode": mode,
            "carriers": [{"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                          "is_default": bool(c.get("is_default"))} for c in carriers],
            "periods": _list_periods(client, org_id)}

    if mode == "boost":
        rates = _rates(client, org_id, period)
        actuals = _boost_actuals(client, org_id, period, rates)
        comps, tier = _boost_template(rates, actuals)
        base.update({"rates": rates, "actuals": actuals,
                     "template": {"components": comps, "tier": tier, "source_kind": "boost_rates"}})
        return base

    tpl = _plan_template(client, org_id, period, picked)
    if tpl.get("template_empty"):
        base.update({"template": {"components": [], "tier": None, "source_kind": "empty",
                                  "empty": True, "reason": tpl.get("reason"),
                                  "configure_url": tpl.get("configure_url")},
                     "actuals": {"subtotal": 0.0, "total_payout": 0.0, "avg_tier": 1.0, "reps": 0}})
        return base
    base.update({"actuals": tpl["actuals"],
                 "template": {"components": tpl["components"], "tier": tpl["tier"],
                              "source_kind": "commission_plan"}})
    return base


# ─── 2. BYOD → residual (carrier-agnostic) ─────────────────────────────────────────────────────────
def _normalize_amount(v, sign):
    """Sign-normalize a residual amount to INCOME. MA residual rows are negative (they pay us);
    'negate' flips them positive. 'abs' takes magnitude; 'as_is' keeps it."""
    x = safe_float(v)
    if sign == "negate":
        return -x
    if sign == "abs":
        return abs(x)
    return x


def byod_residual(client, org_id, months=6, carrier_id=None):
    """What-If tool #2 — residual trend + BYOD contribution. Residual source resolved per carrier config."""
    carriers, picked, mode = _carrier_ctx(client, org_id, carrier_id)
    cfg = _whatif_source_config(client, org_id, (picked or {}).get("id"), mode)
    carrier_meta = ({"id": picked.get("id"), "name": picked.get("name"), "code": picked.get("code")} if picked else None)
    src = (cfg.get("residual_source") or "boost_mi_atu").strip().lower()
    if src == "boost_mi_atu":
        out = _boost_byod_residual(client, org_id, months)
    elif src == "ma_daily_tx":
        out = _ma_byod_residual(client, org_id, months, cfg)
    else:
        out = {"months": [], "series": [], "avg_residual_per_sub": 0.0, "total_residual": 0.0,
               "total_subs": 0, "latest": None, "byod_specific": None,
               "note": "No residual source is configured for this carrier."}
    out["carrier"] = carrier_meta
    out["carrier_mode"] = mode
    out["carriers"] = [{"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                        "is_default": bool(c.get("is_default"))} for c in carriers]
    out["residual_source"] = src
    return out


def _boost_byod_residual(client, org_id, months=6):
    """UNCHANGED Boost residual (MI+ATU) trend + BYOD-specific join — byte-identical to the legacy tool."""
    res = residual_subs.compute(client, org_id, months=months)
    company = res.get("company") or []
    byod_by_period = {}
    try:
        rc = (client.schema("commcalc").table("rep_commissions").select("period,byod_acts")
              .eq("org_id", org_id).limit(300000).execute().data) or []
    except Exception:
        rc = []
    for r in rc:
        p = r.get("period")
        if p:
            byod_by_period[p] = byod_by_period.get(p, 0) + int(r.get("byod_acts") or 0)

    def _byod_for(period):
        for v in _pvariants(period):
            if v in byod_by_period:
                return byod_by_period[v]
        return 0

    series = [{**c, "byod_acts": _byod_for(c["period"])} for c in company]
    tot_res = sum(c["residual"] for c in company)
    tot_subs = sum(c["subs"] for c in company)
    return {
        "months": res.get("months"), "series": series,
        "avg_residual_per_sub": round(tot_res / tot_subs, 2) if tot_subs else 0.0,
        "total_residual": round(tot_res, 2), "total_subs": tot_subs,
        "latest": series[-1] if series else None,
        "byod_specific": _byod_specific_residual(client, org_id, company),
        "note": res.get("note"),
    }


def _residual_field(cfg):
    """The configured residual $ column. Cheap enough to call per row."""
    return (cfg.get("residual_amount_field") or _CFG_DEFAULTS["plan"]["residual_amount_field"]).strip()


def _residual_amount_field(cfg):
    """(field, warning). The PER-PAYLOAD resolution — the same column `_residual_field` gives, plus the
    loud identifier warning. An org's EXPLICIT choice always wins (RULE TWO), including the old
    `merchant_invoice`: silently overriding a saved config row would be a second lie. But an identifier
    column is flagged for the page, and it is never a DEFAULT (see _CFG_DEFAULTS) and never a FALLBACK
    (see _first_ma_money_value). Called once per response, not per row (it formats a message)."""
    field = _residual_field(cfg)
    if not is_ma_money_column(field):
        return field, ("The residual $ column is configured as `%s`, which is the Merchant Invoice "
                       "NUMBER (an identifier), not money — every residual figure here is a sum of ID "
                       "numbers, not dollars. Fix it in \u2699\ufe0f Sources: set the residual $ column to "
                       "`retail_cost`, the same signed column the Commission Ledger books from." % field)
    return field, None


def _first_ma_money_value(row, columns=_MA_MONEY_COLUMNS):
    """The residual amount to use when the CONFIGURED column is blank on this row. Preference:
      1. a value that LOOKS like money — it carries cents or a negative sign — in `columns` order;
      2. otherwise the first non-zero money column;
      3. otherwise the first present money column, else 0.
    IDENTIFIER columns never participate. The original heuristic was max(|value|) across all three
    numeric columns, which is precisely backwards: an id is always the largest number on the row, so the
    fallback re-picked the Merchant Invoice NUMBER every time it fired (that is where the July figure's
    extra magnitude, and its stray cents, came from)."""
    present = [(c, row.get(c)) for c in columns if row.get(c) not in (None, "")]
    for _c, v in present:
        x = safe_float(v)
        if x < 0 or abs(x - int(x)) > 1e-9:
            return v
    for _c, v in present:
        if safe_float(v) != 0:
            return v
    return present[0][1] if present else 0


def _ma_residual_amount(row, cfg):
    """Residual $ for one raw_ma_daily_tx row: the configured MONEY column, sign-normalized. A blank
    value falls back over the MONEY columns only (never an identifier), preferring a cents/negative-
    bearing value over a bigger one."""
    sign = (cfg.get("residual_sign") or "negate").strip().lower()
    v = row.get(_residual_field(cfg))
    if v in (None, "", 0, 0.0):
        v = _first_ma_money_value(row)
    return _normalize_amount(v, sign)


def _ma_commission_sign(cfg):
    return (cfg.get("ma_commission_sign") or _CFG_DEFAULTS["plan"]["ma_commission_sign"]).strip().lower()


def _ma_commission_amount(row, cfg, fields):
    """Sum of raw_ma_commission money columns for one row, normalized to INCOME (positive = the dealer
    receives) exactly the way RESIDUAL is normalized. On the MA Commission Details export NEGATIVE =
    paid to the dealer, which is why /ma-commission/summary, account.residual_subs._aggregate_ma and
    coa.build_inputs all book -Sigma. This module summed the same columns RAW, so a month that DOES have
    MA commission rows posted NEGATIVE commission/spiff beside a POSITIVE residual on the same row
    (finance escalation 2026-07-30 §④.2 — three surfaces, two conventions). The sign is CONFIG
    (`ma_commission_sign`, default 'negate'), so a tenant whose export already arrives positive sets
    'as_is' on its own carrier row instead of anyone hard-coding a carrier."""
    return _normalize_amount(sum(safe_float(row.get(f)) for f in fields), _ma_commission_sign(cfg))


def _ma_pkey(period):
    for v in _pvariants(period or ""):
        parts = v.split()
        if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
            return (int(parts[1]), _MONTHS[parts[0]])
        if len(v) >= 7 and v[:4].isdigit() and v[4] == "-":
            try:
                return (int(v[:4]), int(v[5:7]))
            except Exception:
                pass
    return (0, 0)


def _ma_byod_residual(client, org_id, months, cfg):
    """MA-fed residual: raw_ma_daily_tx residual-order rows (sign-normalized) + raw_ma_commission M1-M6 +
    rebate per IMEI/phone. 'The combination of ma daily tx and ma commission gives the entire picture
    about a phone activated' (owner). Retail cost from raw_ma_pr_activation when present."""
    order_type = (cfg.get("residual_order_type") or "Postpaid Residual Order").strip().lower()
    per = {}
    start, page = 0, 1000
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_ma_daily_tx")
                     .select("period,order_type,account_id,order_number,merchant_invoice,merchant_discount,retail_cost")
                     .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            ot = str(r.get("order_type") or "").strip().lower()
            if order_type not in ot:
                continue
            p = (r.get("period") or "").strip()
            if not p:
                continue
            d = per.setdefault(p, {"residual": 0.0, "lines": 0, "subs": set()})
            d["residual"] += _ma_residual_amount(r, cfg)
            d["lines"] += 1
            sub = str(r.get("account_id") or r.get("order_number") or "").strip()
            if sub:
                d["subs"].add(sub)
        if len(chunk) < page:
            break
        start += page

    byod_by_period = {}
    comm_stats = {"byod_m16": 0.0, "byod_rebate": 0.0, "byod_lines": 0,
                  "all_m16": 0.0, "all_rebate": 0.0, "all_lines": 0}
    start = 0
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_ma_commission")
                     .select("period,activation_type2,imei,ban,spiff_m1,spiff_m2,spiff_m3,"
                             "spiff_m4,spiff_m5,spiff_m6,rebate")
                     .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            p = (r.get("period") or "").strip()
            is_byod = str(r.get("activation_type2") or "").strip().lower() in ("byop", "byod")
            # Sign-normalized to income, same convention as RESIDUAL above (§④.2).
            m16 = _ma_commission_amount(r, cfg, _MA_SPIFF_FIELDS)
            reb = _ma_commission_amount(r, cfg, ("rebate",))
            comm_stats["all_m16"] += m16
            comm_stats["all_rebate"] += reb
            comm_stats["all_lines"] += 1
            if is_byod:
                comm_stats["byod_m16"] += m16
                comm_stats["byod_rebate"] += reb
                comm_stats["byod_lines"] += 1
                if p:
                    byod_by_period[p] = byod_by_period.get(p, 0) + 1
        if len(chunk) < page:
            break
        start += page

    kept = sorted(per.keys(), key=_ma_pkey)
    if months and months > 0:
        kept = kept[-months:]
    series = []
    for p in kept:
        d = per[p]
        subs = len(d["subs"])
        res = round(d["residual"], 2)
        series.append({"period": p, "residual": res, "subs": subs,
                       "per_sub": round(res / subs, 2) if subs else 0.0,
                       "byod_acts": byod_by_period.get(p, 0)})
    tot_res = sum(s["residual"] for s in series)
    tot_subs = sum(s["subs"] for s in series)

    byod_specific = None
    if comm_stats["byod_lines"]:
        byod_income = comm_stats["byod_m16"] + comm_stats["byod_rebate"]
        other_lines = comm_stats["all_lines"] - comm_stats["byod_lines"]
        other_income = (comm_stats["all_m16"] + comm_stats["all_rebate"]) - byod_income
        byod_specific = {
            "period": "MA commission (all months)",
            "byod_activation_mdns": comm_stats["byod_lines"],
            "byod_subs_with_residual": comm_stats["byod_lines"],
            "byod_residual_month": round(byod_income, 2),
            "avg_residual_per_byod_sub": round(byod_income / comm_stats["byod_lines"], 2),
            "avg_residual_per_other_sub": round(other_income / other_lines, 2) if other_lines else 0.0,
            "match_rate": 1.0,
            "note": "MA-fed: BYOD 1st-6-month commission (M1-M6) + rebate per activated phone (raw_ma_commission).",
        }

    note = None
    if not series:
        note = ("No master-agent residual rows found — pull the MA Daily Tx report (Data Imports → "
                "payment-processor sources), and confirm the residual order type in ⚙️ Sources.")
    field, field_warning = _residual_amount_field(cfg)
    return {
        "months": kept, "series": series,
        "avg_residual_per_sub": round(tot_res / tot_subs, 2) if tot_subs else 0.0,
        "total_residual": round(tot_res, 2), "total_subs": tot_subs,
        "latest": series[-1] if series else None,
        "byod_specific": byod_specific,
        "retail_cost": _ma_retail_cost(client, org_id, cfg),
        "residual_amount_field": field,
        "residual_field_warning": field_warning,
        "ma_commission_sign": _ma_commission_sign(cfg),
        "note": note,
    }


def _ma_retail_cost(client, org_id, cfg):
    """Per-product retail cost from raw_ma_pr_activation (mig 207). Degrades to None when the table is
    absent (mig 207 parked) — the residual view still works without it."""
    if (cfg.get("retail_cost_source") or "none").strip().lower() != "ma_pr_activation":
        return None
    try:
        rows = (client.schema("commcalc").table("raw_ma_pr_activation").select("raw_row")
                .eq("org_id", org_id).limit(20000).execute().data)
    except Exception:
        return None  # table absent (mig 207 unrun) → graceful
    if rows is None:
        return None
    total, lines = 0.0, 0
    for r in (rows or []):
        raw = r.get("raw_row") or {}
        if not isinstance(raw, dict):
            continue
        for k, v in raw.items():
            kl = str(k).lower()
            if ("retail" in kl and "cost" in kl) or kl in ("retail_cost", "retailcost", "cost"):
                total += safe_float(v)
                lines += 1
                break
    if lines == 0:
        return {"available": False, "lines": 0, "total_retail_cost": 0.0,
                "note": "raw_ma_pr_activation present but no recognizable retail-cost column yet (report un-calibrated)."}
    return {"available": True, "lines": lines, "total_retail_cost": round(total, 2)}


def _norm_mdn(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _byod_mdns(client, org_id, periods):
    """Distinct normalized MDNs whose activation classified as BYOD, across the given sales periods."""
    out = set()
    for period in periods:
        for tbl in ("raw_sales", "daily_sales_feed"):
            page, got = 0, False
            while True:
                try:
                    chunk = (client.schema("commcalc").table(tbl)
                             .select("mdn,contract_type,voided,trans_type")
                             .eq("org_id", org_id).in_("period", _pvariants(period))
                             .range(page * 1000, page * 1000 + 999).execute().data) or []
                except Exception:
                    chunk = []
                if chunk:
                    got = True
                for r in chunk:
                    if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                        continue
                    if str(r.get("trans_type") or "").strip() == "Return":
                        continue
                    if classify_contract_type(r.get("contract_type")) == "byod":
                        m = _norm_mdn(r.get("mdn"))
                        if m:
                            out.add(m)
                if len(chunk) < 1000 or page > 60:
                    break
                page += 1
            if got:
                break  # raw_sales preferred; if it had rows, don't double from the feed
    return out


def _residual_by_mdn(client, org_id, period):
    """Sum (MI + ATU) per normalized MDN for ONE raw_mi period (bounded)."""
    per, page = {}, 0
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_mi")
                     .select("phone_number,actual_mi_payout,actual_atu_payout")
                     .eq("org_id", org_id).in_("period", _pvariants(period))
                     .range(page * 1000, page * 1000 + 999).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            m = _norm_mdn(r.get("phone_number"))
            if not m:
                continue
            per[m] = per.get(m, 0.0) + safe_float(r.get("actual_mi_payout")) + safe_float(r.get("actual_atu_payout"))
        if len(chunk) < 1000 or page > 60:
            break
        page += 1
    return per


def _byod_specific_residual(client, org_id, company):
    """Attribute a monthly residual/sub to BYOD subscribers (Boost / raw_mi path). Bounded; None on failure."""
    try:
        if not company:
            return None
        base = max(company, key=lambda c: c["residual"])
        res_by_mdn = _residual_by_mdn(client, org_id, base["period"])
        if not res_by_mdn:
            return None
        byod_mdns = _byod_mdns(client, org_id, _list_periods(client, org_id)[:6])
        matched = {m: v for m, v in res_by_mdn.items() if m in byod_mdns and v}
        others = {m: v for m, v in res_by_mdn.items() if m not in byod_mdns and v}
        byod_res = sum(matched.values())
        return {
            "period": base["period"],
            "byod_activation_mdns": len(byod_mdns),
            "byod_subs_with_residual": len(matched),
            "byod_residual_month": round(byod_res, 2),
            "avg_residual_per_byod_sub": round(byod_res / len(matched), 2) if matched else 0.0,
            "avg_residual_per_other_sub": round(sum(others.values()) / len(others), 2) if others else 0.0,
            "match_rate": round(len(matched) / len(byod_mdns), 3) if byod_mdns else 0.0,
        }
    except Exception:
        return None


# ─── 3. Accessory sales ↔ BYOD activations ↔ total revenue (unchanged) ──────────────────────────────
def _acc_cfg(client, org_id):
    depts, cats, kws = [], [], []
    try:
        rows = (client.schema("commcalc").table("flag_rules")
                .select("accessory_departments,accessory_categories,accessory_product_keywords")
                .eq("org_id", org_id).eq("id", 1).limit(1).execute().data) or []
        if rows:
            depts = [d for d in (rows[0].get("accessory_departments") or []) if d]
            cats = [c for c in (rows[0].get("accessory_categories") or []) if c]
            kws = [k for k in (rows[0].get("accessory_product_keywords") or []) if k]
    except Exception:
        pass
    if not depts and not cats and not kws:
        depts = ["Ondigo"]
    return {"d": {x.strip().lower() for x in depts}, "c": {x.strip().lower() for x in cats},
            "p": {x.strip().lower() for x in kws}}


def _is_acc(dept, cat, product, acc):
    if (dept or "").strip().lower() in acc["d"]:
        return True
    c = (cat or "").strip().lower()
    if c and c in acc["c"]:
        return True
    if acc["p"]:
        p = (product or "").strip().lower()
        if p and any(k in p for k in acc["p"]):
            return True
    return False


def _fetch_period_sales(client, org_id, period):
    cols = "store,contract_type,department,category,product_desc,ext_price,trans_id,voided,trans_type"
    for tbl in ("raw_sales", "daily_sales_feed"):
        out, page = [], 0
        while True:
            start = page * 1000
            try:
                chunk = (client.schema("commcalc").table(tbl).select(cols)
                         .eq("org_id", org_id).in_("period", _pvariants(period))
                         .range(start, start + 999).execute().data) or []
            except Exception:
                chunk = []
            out.extend(chunk)
            if len(chunk) < 1000 or page > 60:
                break
            page += 1
        if out:
            return out
    return []


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / ((sxx * syy) ** 0.5), 3)


def accessory_byod_correlation(client, org_id, months=4):
    acc = _acc_cfg(client, org_id)
    periods = _list_periods(client, org_id)[:max(1, min(months, 12))]
    points = []
    for period in periods:
        rows = _fetch_period_sales(client, org_id, period)
        per_store = {}
        for r in rows:
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            if str(r.get("trans_type") or "").strip() == "Return":
                continue
            store = (r.get("store") or "").strip() or "(unknown)"
            s = per_store.setdefault(store, {"byod": set(), "acc_rev": 0.0, "revenue": 0.0})
            ext = safe_float(r.get("ext_price"))
            s["revenue"] += ext
            if _is_acc(r.get("department"), r.get("category"), r.get("product_desc"), acc):
                s["acc_rev"] += ext
            tid = str(r.get("trans_id") or "").strip()
            if tid and classify_contract_type(r.get("contract_type")) == "byod":
                s["byod"].add(tid)
        for store, s in per_store.items():
            if s["revenue"] <= 0 and not s["byod"]:
                continue
            points.append({"store": store, "period": period, "byod": len(s["byod"]),
                           "accessory_rev": round(s["acc_rev"], 2), "revenue": round(s["revenue"], 2)})

    byod = [p["byod"] for p in points]
    accr = [p["accessory_rev"] for p in points]
    rev = [p["revenue"] for p in points]
    return {
        "periods": periods, "points": points, "n": len(points),
        "correlation": {
            "byod_vs_accessory": _pearson(byod, accr),
            "byod_vs_revenue": _pearson(byod, rev),
            "accessory_vs_revenue": _pearson(accr, rev),
        },
        "totals": {
            "byod": sum(byod), "accessory_rev": round(sum(accr), 2), "revenue": round(sum(rev), 2),
        },
    }


# ─── 4. Carrier income (company perspective, carrier-agnostic) ──────────────────────────────────────
def carrier_income(client, org_id, months=6, carrier_id=None):
    """What-If tool #4 — what the carrier / master-agent pays the COMPANY, by heading, month over month.
    Boost → Comprehensive Comp + MI+ATU (unchanged); MA-fed → raw_ma_commission (M1-M6 + rebate) +
    raw_ma_daily_tx (residual + airtime margin). Same response shape either way so the tab renders both."""
    carriers, picked, mode = _carrier_ctx(client, org_id, carrier_id)
    cfg = _whatif_source_config(client, org_id, (picked or {}).get("id"), mode)
    carrier_meta = ({"id": picked.get("id"), "name": picked.get("name"), "code": picked.get("code")} if picked else None)
    src = (cfg.get("income_source") or "boost_comp_mi_atu").strip().lower()
    if src == "ma":
        out = _ma_carrier_income(client, org_id, months, cfg)
    else:
        from app.modules.commcalc import comp_trend
        out = comp_trend.compute_residual_trend(client, org_id, months=months)
    out["carrier"] = carrier_meta
    out["carrier_mode"] = mode
    out["carriers"] = [{"id": c.get("id"), "name": c.get("name"), "code": c.get("code"),
                        "is_default": bool(c.get("is_default"))} for c in carriers]
    out["income_source"] = src
    return out


def _ma_carrier_income(client, org_id, months, cfg):
    """Master-agent carrier income per period, in comp_trend's totals_by_month shape:
      components.COMMISSION = Σ M1-M6 spiffs (first-6-month commission), SPIFF = Σ rebate,
      UNMAPPED = Σ daily-tx non-residual airtime margin; residual (residual_mi_atu) = Σ daily-tx
      residual-order rows. total_comp = COMMISSION+SPIFF+REIMBURSEMENT+UNMAPPED (matches the tab math)."""
    order_type = (cfg.get("residual_order_type") or "Postpaid Residual Order").strip().lower()
    per = {}

    def _slot(p):
        return per.setdefault(p, {"COMMISSION": 0.0, "SPIFF": 0.0, "REIMBURSEMENT": 0.0,
                                  "RESIDUAL": 0.0, "UNMAPPED": 0.0, "accounts": set(),
                                  "commission_rows": 0, "daily_tx_rows": 0})

    # How the MA Commission Details rows actually arrive, so an operator can tell whether their export
    # matches the negative-is-payable convention `ma_commission_sign` assumes (diagnostic only).
    raw_signs = {"negative": 0, "positive": 0, "zero": 0}

    start, page = 0, 1000
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_ma_commission")
                     .select("period,merchant_account_id,spiff_m1,spiff_m2,spiff_m3,spiff_m4,"
                             "spiff_m5,spiff_m6,rebate")
                     .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            p = (r.get("period") or "").strip()
            if not p:
                continue
            s = _slot(p)
            # Normalized to income (positive = the dealer receives), same as RESIDUAL below (§④.2).
            s["COMMISSION"] += _ma_commission_amount(r, cfg, _MA_SPIFF_FIELDS)
            s["SPIFF"] += _ma_commission_amount(r, cfg, ("rebate",))
            s["commission_rows"] += 1
            _raw = sum(safe_float(r.get(f)) for f in _MA_SPIFF_FIELDS) + safe_float(r.get("rebate"))
            raw_signs["negative" if _raw < 0 else ("positive" if _raw > 0 else "zero")] += 1
            acc = str(r.get("merchant_account_id") or "").strip()
            if acc:
                s["accounts"].add(acc)
        if len(chunk) < page:
            break
        start += page

    start = 0
    while True:
        try:
            chunk = (client.schema("commcalc").table("raw_ma_daily_tx")
                     .select("period,order_type,account_id,merchant_invoice,merchant_discount,retail_cost")
                     .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        except Exception:
            chunk = []
        for r in chunk:
            p = (r.get("period") or "").strip()
            if not p:
                continue
            s = _slot(p)
            s["daily_tx_rows"] += 1
            ot = str(r.get("order_type") or "").strip().lower()
            if order_type in ot:
                s["RESIDUAL"] += _ma_residual_amount(r, cfg)
            else:
                s["UNMAPPED"] += safe_float(r.get("merchant_discount"))
            acc = str(r.get("account_id") or "").strip()
            if acc:
                s["accounts"].add(acc)
        if len(chunk) < page:
            break
        start += page

    kept = sorted(per.keys(), key=_ma_pkey)
    if months and months > 0:
        kept = kept[-months:]
    totals_by_month, prev = [], None
    for p in kept:
        s = per[p]
        comp_total = round(s["COMMISSION"] + s["SPIFF"] + s["REIMBURSEMENT"] + s["UNMAPPED"], 2)
        residual = round(s["RESIDUAL"], 2)
        delta = None if prev is None else round(comp_total - prev, 2)
        pct = None if prev in (None, 0) else round((comp_total - prev) / abs(prev) * 100, 1)
        totals_by_month.append({
            "period": p, "residual": comp_total, "total_comp": comp_total,
            "residual_mi_atu": residual, "accounts": len(s["accounts"]), "qty": len(s["accounts"]),
            "delta_vs_prev": delta, "pct_vs_prev": pct,
            "components": {"COMMISSION": round(s["COMMISSION"], 2), "SPIFF": round(s["SPIFF"], 2),
                          "REIMBURSEMENT": round(s["REIMBURSEMENT"], 2), "RESIDUAL": residual,
                          "UNMAPPED": round(s["UNMAPPED"], 2)},
            # Per-report ingest coverage for THIS month (§④.3): COMMISSION/SPIFF come only from MA
            # Commission Details, RESIDUAL/airtime only from MA Daily Tx. A month with daily-tx rows and
            # no commission rows reads $0 comp HONESTLY — it is a data gap, not a stale ledger.
            "commission_rows": s["commission_rows"], "daily_tx_rows": s["daily_tx_rows"],
            "comp_source_missing": bool(s["daily_tx_rows"] and not s["commission_rows"]),
        })
        prev = comp_total
    coverage = [{"period": p, "commission_rows": per[p]["commission_rows"],
                 "daily_tx_rows": per[p]["daily_tx_rows"]} for p in kept]
    gaps = [c["period"] for c in coverage if c["daily_tx_rows"] and not c["commission_rows"]]
    data_note = None
    if gaps:
        data_note = (
            "DATA GAP (not a calculation error) — " + ", ".join(gaps) + ": these month(s) have MA Daily "
            "Tx rows but NO MA Commission Details rows, so Commission (M1–M6) and Rebate honestly read "
            "$0 for them. Pull MA Commission Details for those months (Data Imports → payment-processor "
            "sources; that report supports up to 12 months back). Note the Commission Ledger classifies "
            "MA Daily Tx payout lines, so its totals for the SAME month can be non-zero while this view "
            "reads $0 — a different, thinner source, NOT a stale ledger.")
    field, field_warning = _residual_amount_field(cfg)
    return {
        "months": kept, "totals_by_month": totals_by_month, "dips": [], "dip_count": 0,
        "params": {"months": months, "source": "ma", "residual_amount_field": field,
                   "ma_commission_sign": _ma_commission_sign(cfg),
                   "commission_row_signs": raw_signs},
        "ma_coverage": coverage,
        "data_note": data_note,
        "residual_amount_field": field,
        "residual_field_warning": field_warning,
        "note": (None if kept else
                 "No master-agent commission/daily-tx rows yet — pull the MA Commission + MA Daily Tx "
                 "reports (Data Imports → payment-processor sources)."),
    }
