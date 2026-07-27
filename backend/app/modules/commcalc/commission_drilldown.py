"""Read-only 'how was this commission calculated' drill-down assembly (migration-free).

The owner needs to SEE why a per-rep number is what it is (or why it is $0) for tenants that pay from
TWO engines (Commission Plan + sale-triggered multi-month). This module ASSEMBLES an explanation. It is
strictly READ-ONLY: it writes nothing, changes no payout number, and never touches the live POST
/calculate path.

Every number is sourced from the SINGLE point of truth for that number — it never re-implements matching
or the paid gate, it only NARRATES them:
  • plan component  → commission_engine.preview(detail=True), whose per-rep resolution comes from
                      commission_engine._resolve_plan_for(explain=True) (the same fn the live calc uses).
  • multi-month     → sale_installment_engine.compute_sale_installments (the authoritative paid/held
                      ledger). The finer hold REASON is derived by re-reading the SAME raw_mi row via the
                      engine's own pure helpers (_mi_index/_match_mi) — a read, not a second gate.
  • MA cross-ref    → commcalc.raw_ma_commission (mig 083) by IMEI. For Total/MA-fed carriers the paid
                      gate matches raw_mi (ePay) while residuals arrive in the MA file, so this surfaces
                      "the MA file shows this line paid" next to a held installment — a diagnostic, NOT a
                      change to what pays.

Multi-tenant: org_id is passed through on EVERY read (caller supplies it from the query param). Degrades
to safe empties on any missing table / unapplied migration — never raises.
"""
import re

from app.modules.commcalc.calculator import safe_float
# ONE shared voided token set for pay + display (owner 2026-07-25) — see gp_report.VOID_TOKENS.
from app.modules.commcalc.gp_report import is_voided as _is_voided
from app.modules.commcalc.commission_engine import _norm_mdn, _canon_person


def _tok(s):
    return set(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _name_match(a, b):
    """rep-name match: canon-equal, else token subset either way (mirrors the commission-drill matcher)."""
    if _canon_person(a) == _canon_person(b):
        return True
    ta, tb = _tok(a), _tok(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


# ── installment hold-reason narration (reuses the engine's pure gate helpers; never a second gate) ──
def _installment_reason(led_row, mi_row, stored=False):
    """(code, human text) for ONE ledger row's gate decision. led_row is the authoritative ledger row
    from compute_sale_installments; mi_row is the raw_mi row the engine's _match_mi found for the pay
    period (or None). This only NARRATES the ledger's own paid/held verdict — it computes no money.

    The held-reason is selected to match the ACTUAL gate criterion for the row's gate_mode:
      active_status    → criterion is Active           → line_inactive
      nonzero_residual → criterion is residual > 0      → residual_not_received (inactivity is NOT the
                          criterion in this mode, so we never label it line_inactive here)
      paid_residual    → criterion is Active AND resid  → line_inactive first, else residual_not_received

    `stored=True` marks a row read back from the PERSISTED sale_installment_ledger (device story). That
    table omits gate_kind (mig 201), so an activation-gated month-1 hold is indistinguishable from a
    dealer-not-paid hold when no raw_mi row matched — narrate it with REDUCED CONFIDENCE and point to the
    rep-explain view (which recomputes live and DOES carry gate_kind) for the precise reason. A proper
    `gate_kind` ledger column is the later fix (200-band; see handoff)."""
    status = str(led_row.get("status") or "").strip()
    gate_kind = str(led_row.get("gate_kind") or "").strip().lower()
    gate_mode = str(led_row.get("gate_mode") or "").strip().lower()
    if status == "paid":
        if gate_kind == "activation_payment":
            return "paid", "Month 1 paid: a first-month payment was collected at activation (activation-payment gate)."
        if gate_mode == "none":
            return "paid", "Paid: this schedule's gate is OFF (pure calendar) for this month."
        if led_row.get("matched_mi_period"):
            return "paid", "Paid: the line is active and receiving residual in the pay period (raw_mi matched)."
        return "paid", "Paid (ungated month)."
    # ── held ──
    if gate_kind == "activation_payment":   # only LIVE rows carry this (persisted ledger omits it)
        return ("activation_payment_missing",
                "Held: no qualifying first-month payment line was found on the activation transaction "
                "(month 1 is gated on the sale's own payment).")
    if mi_row is None:
        if stored:
            return ("held_stored",
                    "Held (stored ledger row): no matching raw_mi row and gate provenance is unavailable "
                    "for a persisted row — this could be dealer-not-paid OR no first-month payment. Open "
                    "this rep in Commission Explain for the precise, live reason.")
        return ("no_mi_match",
                "Held — dealer not shown paid on this line: no matching raw_mi residual row for this "
                "MDN/IMEI in the pay period. NOTE: for Total / MA-fed carriers residuals arrive in the MA "
                "file, not raw_mi — check the MA cross-reference below for this device.")
    active = str(mi_row.get("subscriber_status") or "").strip().lower().startswith("activ")
    resid = safe_float(mi_row.get("actual_mi_payout")) + safe_float(mi_row.get("actual_atu_payout"))
    if gate_mode == "active_status":
        return ("line_inactive",
                f"Held: gate 'active_status' — raw_mi row found but subscriber_status="
                f"'{mi_row.get('subscriber_status') or ''}' is not Active.")
    if gate_mode == "nonzero_residual":
        return ("residual_not_received",
                "Held: gate 'nonzero_residual' — raw_mi row found but no residual (MI+ATU) received for "
                "the pay period (Active status is not the criterion in this mode).")
    # paid_residual (default): Active AND residual > 0
    if not active:
        return ("line_inactive",
                f"Held: raw_mi row found but subscriber_status='{mi_row.get('subscriber_status') or ''}' "
                f"is not Active.")
    if resid <= 0:
        return ("residual_not_received",
                "Held: raw_mi row found and Active, but no residual (MI+ATU) has been received yet for "
                "the pay period.")
    return "withheld", "Held: gate not met."


def _mi_ref(mi_row):
    """Identifying fields of the matched raw_mi row (or None) for the drill-down."""
    if not mi_row:
        return None
    return {
        "phone_number": mi_row.get("phone_number") or mi_row.get("mdn"),
        "subscriber_id": mi_row.get("subscriber_id"),
        "device_serial": mi_row.get("device_serial"),
        "subscriber_status": mi_row.get("subscriber_status"),
        "mi_payout": safe_float(mi_row.get("actual_mi_payout")),
        "atu_payout": safe_float(mi_row.get("actual_atu_payout")),
        "period": mi_row.get("period"),
    }


# ── MA cross-reference (mig 083 raw_ma_commission) — "the MA file says paid" ─────────────────────────
def _ma_norm(v):
    """Sign-normalize a raw_ma_commission money amount to a POSITIVE 'paid to dealer' figure. Payouts are
    stored NEGATIVE in this table (mig 083; e.g. spiff_m2=-48.75, rebate=-529.0), so we NEGATE — the SAME
    convention whatif._normalize_amount uses for MA money. A genuinely positive value (a charge/clawback)
    correctly normalizes to negative, so real clawbacks still read as negative — we just never show a
    routine payout as if it were a clawback."""
    return round(-safe_float(v), 2)


def _ma_rows_for_imei(client, org_id, imei):
    """raw_ma_commission rows for an IMEI (the MA-file reference the owner needs). A single IMEI can have
    MULTIPLE rows per period (base + adjustment) — all are returned. Each row carries the raw signed
    values AND sign-normalized 'paid' figures (payouts read positive). Empty on any error / unapplied
    mig 083. `line_status` is often NULL in real data — nonzero spiff/rebate is the payment evidence,
    never NULL-status."""
    imei_raw = str(imei or "").strip()
    imei_n = _norm_mdn(imei)
    keys = sorted({k for k in (imei_raw, imei_n) if k})
    if not keys:
        return []
    try:
        rows = (client.schema("commcalc").table("raw_ma_commission").select(
            "id,period,period_month,period_year,tx_date,carrier_name,activation_order,merchant_account_id,"
            "imei,sim,ban,activation_type,activation_type2,mrc_net_discount,rebate,line_status,user_name,"
            "spiff_m1,spiff_m2,spiff_m3,spiff_m4,spiff_m5,spiff_m6")
            .eq("org_id", org_id).in_("imei", keys).limit(200).execute().data) or []
    except Exception:
        return []
    out = []
    for r in rows:
        spiffs = {f"m{i}": safe_float(r.get(f"spiff_m{i}")) for i in range(1, 7)}         # raw signed
        spiffs_paid = {f"m{i}": _ma_norm(r.get(f"spiff_m{i}")) for i in range(1, 7)}       # normalized +
        out.append({
            "id": r.get("id"), "period": r.get("period"), "tx_date": str(r.get("tx_date") or "")[:10],
            "carrier_name": r.get("carrier_name"), "activation_order": r.get("activation_order"),
            "merchant_account_id": r.get("merchant_account_id"), "imei": r.get("imei"),
            "sim": r.get("sim"), "ban": r.get("ban"), "line_status": r.get("line_status"),
            "activation_type": r.get("activation_type"), "activation_type2": r.get("activation_type2"),
            "mrc_net_discount": safe_float(r.get("mrc_net_discount")),
            "rebate": safe_float(r.get("rebate")), "rebate_paid": _ma_norm(r.get("rebate")),
            "user_name": r.get("user_name"),
            "spiffs": spiffs, "spiffs_paid": spiffs_paid,
            "spiff_total": round(sum(spiffs.values()), 2),
            "spiff_total_paid": round(sum(spiffs_paid.values()), 2),
        })
    return out


def _ma_says_paid(ma_rows):
    """The MA file shows this device paid if ANY matched row carries a non-zero M1–M6 spiff OR rebate.
    Magnitude only — sign-agnostic. line_status is NOT used (it is NULL in real data; nonzero
    spiff/rebate is the actual payment evidence)."""
    for m in ma_rows:
        if (m.get("spiff_total") or 0) != 0 or (m.get("rebate") or 0) != 0:
            return True
    return False


# ── sale-line reads (device story + enrichment) ──────────────────────────────────────────────────
_SALE_COLS = ("trans_id,trans_date,period,store,salesperson,department,category,product_desc,"
              "contract_type,tender_type,ext_price,gp,mdn,serial_1,voided,trans_type")


def _sale_lines_by_imei(client, org_id, imei):
    """Every sale LINE for an IMEI (raw_sales, falling back to the daily feed). Read-only, org-scoped."""
    imei_raw = str(imei or "").strip()
    imei_n = _norm_mdn(imei)
    keys = sorted({k for k in (imei_raw, imei_n) if k})
    if not keys:
        return []

    def _q(table):
        try:
            return (client.schema("commcalc").table(table).select(_SALE_COLS)
                    .eq("org_id", org_id).in_("serial_1", keys).limit(500).execute().data) or []
        except Exception:
            return []
    rows = _q("raw_sales") or _q("daily_sales_feed")
    out = []
    for r in rows:
        out.append({
            "trans_id": r.get("trans_id"), "date": str(r.get("trans_date") or "")[:10],
            "period": r.get("period"), "store": r.get("store"), "salesperson": r.get("salesperson"),
            "product": r.get("product_desc"), "contract_type": r.get("contract_type"),
            "department": r.get("department"), "category": r.get("category"),
            "ext_price": round(safe_float(r.get("ext_price")), 2), "gp": round(safe_float(r.get("gp")), 2),
            "mdn": r.get("mdn"), "imei": r.get("serial_1"),
            # SHARED token set (owner 2026-07-25) — the label must agree with what the engine skipped.
            "voided": _is_voided(r.get("voided")),
            "returned": str(r.get("trans_type") or "").strip() == "Return",
        })
    return out


def _sale_line_index(client, org_id, periods, ce):
    """{(period, norm_serial): line, (period, norm_mdn): line} for enriching installment devices with the
    original sale line's product/contract/price/gp. One read per involved sale_period."""
    idx = {}
    for per in {p for p in periods if p}:
        try:
            rows = ce._read_sales(client, org_id, per)
        except Exception:
            rows = []
        for r in rows:
            ser, mdn = _norm_mdn(r.get("serial_1")), _norm_mdn(r.get("mdn"))
            rec = {"product_desc": r.get("product_desc"), "contract_type": r.get("contract_type"),
                   "ext_price": safe_float(r.get("ext_price")), "gp": safe_float(r.get("gp"))}
            if ser:
                idx.setdefault((per, ser), rec)
            if mdn:
                idx.setdefault((per, mdn), rec)
    return idx


# ── main: per-rep explanation ─────────────────────────────────────────────────────────────────────
def explain_rep(client, org_id, period, rep, carrier_mode="plan"):
    """Full 'how was this commission calculated' for ONE rep + period. READ-ONLY."""
    from app.modules.commcalc import commission_engine as ce, sale_installment_engine as sie
    from app.modules.commcalc.installment_engine import _read_mi

    out = {"period": period, "rep": rep, "carrier_mode": carrier_mode,
           "plan_component": None, "multimonth_component": None,
           "reconciliation": None, "zero_explanation": [], "note": None}

    # 1. PLAN COMPONENT — one row from the single-source preview (detail mode carries the assignment
    #    narration + per-rule matched lines).
    plan_row = None
    try:
        pv = ce.preview(client, org_id, period, detail=True, only_rep=rep)
        rows = pv.get("by_rep") or []
        # prefer the exact canon match if only_rep's token-subset matched more than one rep
        plan_row = (next((r for r in rows if _canon_person(r.get("rep")) == _canon_person(rep)), None)
                    or (rows[0] if rows else None))
        if not pv.get("ready"):
            out["note"] = pv.get("note")
    except Exception as e:
        out["note"] = f"plan preview error: {e}"

    if plan_row:
        out["plan_component"] = {
            "plan_name": plan_row.get("plan_name"), "plan_id": plan_row.get("plan_id"),
            "assignment": plan_row.get("assignment"), "considered": plan_row.get("considered"),
            "rules": plan_row.get("rules"), "base_payout": plan_row.get("base_payout"),
            "tiered_payout": plan_row.get("tiered_payout"), "tier_multiplier": plan_row.get("tier_multiplier"),
            "base_tier_metric": plan_row.get("base_tier_metric"), "tiers": plan_row.get("tiers"),
            "qualifying_units": plan_row.get("qualifying_units"), "total_payout": plan_row.get("total_payout"),
            "store": plan_row.get("store"), "market": plan_row.get("market"), "has_sale_lines": True,
        }
    else:
        out["plan_component"] = _no_plan_narration(client, org_id, period, rep, ce)

    # 2. MULTI-MONTH COMPONENT — authoritative ledger from the sale-installment engine, per device.
    try:
        sr = sie.compute_sale_installments(client, org_id, period, persist=False)
    except Exception as e:
        sr = {"ledger": [], "totals": {}, "schedules": 0, "note": f"installment error: {e}"}
    ledger = sr.get("ledger") or []
    rep_led = [r for r in ledger if _name_match(r.get("epay_salesperson"), rep)]
    # a HELD ledger row stores amount=0 (nothing paid); the engine's WITHHELD flag carries the would-be
    # amount, so surface it as `withheld_amount` (what is being held back), keyed by device.
    withheld_by_dev = {}
    for f in (sr.get("flags") or []):
        if f.get("source") == "commission_rebate_tracking":
            k = _norm_mdn(f.get("imei")) or _norm_mdn(f.get("mdn"))
            if k:
                withheld_by_dev[k] = safe_float(f.get("amount"))

    try:
        mi_idx = sie._mi_index(_read_mi(client, org_id, period))
    except Exception:
        mi_idx = {"mdn": {}, "serial": {}}
    sale_idx = _sale_line_index(client, org_id, {r.get("sale_period") for r in rep_led}, ce)

    devices = {}
    for r in rep_led:
        ser_n, mdn_n = _norm_mdn(r.get("serial_1")), _norm_mdn(r.get("mdn"))
        key = ser_n or mdn_n or str(r.get("trans_id") or "")
        d = devices.get(key)
        if not d:
            d = devices[key] = {"imei": r.get("serial_1"), "mdn": r.get("mdn"),
                                "trans_id": r.get("trans_id"), "sale_period": r.get("sale_period"),
                                "product": None, "contract_type": None, "installments": []}
            sl = (sale_idx.get((r.get("sale_period"), ser_n))
                  or sale_idx.get((r.get("sale_period"), mdn_n)))
            if sl:
                d["product"] = sl.get("product_desc"); d["contract_type"] = sl.get("contract_type")
                d["ext_price"] = round(safe_float(sl.get("ext_price")), 2)
                d["gp"] = round(safe_float(sl.get("gp")), 2)
            # ONE CONSISTENT LABEL (owner 2026-07-27): the engine resolves BOTH halves of the activation
            # (device line + rate-plan line), so the card no longer shows "whichever line the serial/MDN
            # index happened to hit first" — that is what made some rows show the phone and others the
            # rate plan. Falls back to the sale-line lookup when the engine did not supply one.
            d["device_product"] = r.get("device_product") or None
            d["plan_product"] = r.get("plan_product") or None
            d["device_category"] = r.get("device_category") or None
            d["label"] = (r.get("display_label")
                          or sie.installment_label(d.get("device_product") or d.get("product"),
                                                   d.get("plan_product"), None) or d.get("product"))
        mi_row = sie._match_mi({"mdn": r.get("mdn"), "serial_1": r.get("serial_1")}, mi_idx)
        code, text = _installment_reason(r, mi_row)
        paid = str(r.get("status") or "") == "paid"
        wa = 0.0 if paid else withheld_by_dev.get(ser_n, withheld_by_dev.get(mdn_n))  # None if unknown
        d["installments"].append({
            "label": (r.get("display_label")
                      or sie.installment_label(r.get("device_product"), r.get("plan_product"),
                                               r.get("mrc_at_pay"))),
            "device_product": r.get("device_product"), "plan_product": r.get("plan_product"),
            "device_category": r.get("device_category"),
            "month_index": r.get("month_index"), "pay_period": r.get("pay_period"),
            "sale_period": r.get("sale_period"), "amount": safe_float(r.get("amount")),
            "withheld_amount": wa,
            "status": r.get("status"), "paid": bool(r.get("paid_gate_met")),
            "gate_mode": r.get("gate_mode"), "hold_reason": code, "hold_detail": text,
            "mrc_at_pay": r.get("mrc_at_pay"), "mrc_source": r.get("mrc_source"),
            "payout_kind": r.get("payout_kind"), "mi_ref": _mi_ref(mi_row),
        })

    for d in devices.values():
        d["installments"].sort(key=lambda x: (x.get("month_index") or 0))
        d["ma_matches"] = _ma_rows_for_imei(client, org_id, d.get("imei"))
        d["ma_says_paid"] = _ma_says_paid(d["ma_matches"])
        d["held_but_ma_paid"] = d["ma_says_paid"] and any(i["status"] != "paid" for i in d["installments"])

    out["multimonth_component"] = {
        "devices": sorted(devices.values(),
                          key=lambda x: -sum((i.get("amount") or 0) for i in x["installments"])),
        "totals": {"paid": sum(1 for r in rep_led if r.get("status") == "paid"),
                   "withheld": sum(1 for r in rep_led if r.get("status") != "paid"),
                   "amount": round(sum(safe_float(r.get("amount")) for r in rep_led), 2)},
        "schedules": sr.get("schedules"), "note": sr.get("note"),
    }

    out["zero_explanation"] = _zero_reasons(period, rep, carrier_mode, out)
    out["reconciliation"] = _reconcile(client, org_id, period, rep, ce)
    return out


def _no_plan_narration(client, org_id, period, rep, ce):
    """Rep produced no plan row → explain: no sale lines, or lines present but no assignment matched
    (with the nearest-miss list straight from _resolve_plan_for(explain=True))."""
    plans, ready = ce._load_plans(client, org_id)
    store_market = ce._read_store_market(client, org_id)
    role_by_rep = ce._read_employee_roles(client, org_id)
    store, has_lines = "", False
    try:
        for r in ce._read_sales(client, org_id, period):
            if _name_match(r.get("salesperson"), rep):
                has_lines = True
                store = str(r.get("store") or "").strip()
                break
    except Exception:
        pass
    market = store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
    rep_role = role_by_rep.get(_canon_person(rep))
    res = ce._resolve_plan_for(rep, store, market, plans, rep_role=rep_role, explain=True)
    return {"plan_name": (res.get("winner") or {}).get("plan_name"), "plan_id": None,
            "assignment": res.get("winner"), "considered": res.get("considered"), "rules": [],
            # explicit zeros/1.0 so the UI's "Subtotal … × tier … =" clause never renders blank
            "base_payout": 0.0, "tiered_payout": 0.0, "tier_multiplier": 1.0, "qualifying_units": 0,
            "base_tier_metric": "none", "tiers": [], "total_payout": 0.0,
            "store": store, "market": market, "rep_role": rep_role,
            "has_sale_lines": has_lines, "plans_configured": len(plans), "ready": ready}


def _zero_reasons(period, rep, carrier_mode, out):
    """Plain-language list of WHY the rep is at $0 (or partly). Never empty for a $0 rep."""
    reasons = []
    if carrier_mode == "boost":
        reasons.append("This org runs the BOOST engine (carrier mode = boost): rep pay is the Boost "
                       "KPI-tier calc, not Commission Plans or sale-triggered installments. The plan / "
                       "multi-month components below apply to non-Boost (Total/Luxelink) carriers only.")
    pc = out.get("plan_component") or {}
    mm = out.get("multimonth_component") or {}
    plan_total = safe_float(pc.get("total_payout"))
    mm_total = safe_float((mm.get("totals") or {}).get("amount"))

    if not pc.get("plan_name"):
        if pc.get("has_sale_lines") is False:
            reasons.append(f"No sale lines found for '{rep}' in {period}. Commission-Plan pay is computed "
                           f"from sale LINES, so with no sales there is no plan pay (check the rep's name "
                           f"spelling / that the month's sales file was uploaded).")
        elif not pc.get("plans_configured"):
            reasons.append("No commission plans are configured for this org — configure one on Commission Plans.")
        else:
            misses = [c for c in (pc.get("considered") or []) if not c.get("matched")]
            names = sorted({f"{c.get('plan_name')} [{c.get('scope')}={c.get('scope_value')}]"
                            for c in misses if c.get('scope') != 'default'})
            reasons.append("No Commission-Plan assignment matched this rep → $0 on the plan component. "
                           + (f"Nearest (non-matching) assignments: {', '.join(names[:8])}."
                              if names else "No employee/role/store/market assignments are configured."))
    elif plan_total == 0:
        unmatched = [r for r in (pc.get("rules") or []) if not r.get("matched_lines")]
        if unmatched:
            why = "; ".join(f"rule '{r.get('label')}' expects {r.get('match_field')} "
                            f"{r.get('match_op')} '{r.get('match_value')}'" for r in unmatched[:6])
            reasons.append(f"Plan '{pc.get('plan_name')}' attached but no rule matched any sale line: {why}.")

    tot = (mm.get("totals") or {})
    if tot.get("withheld") and mm_total == 0:
        reasons.append(f"{tot['withheld']} multi-month installment(s) were HELD this period and none paid "
                       f"— see each device's hold reason below (dealer-not-paid / line inactive / residual "
                       f"not received / no first-month payment).")
    return reasons


def _reconcile(client, org_id, period, rep, ce):
    """The rep's last-calc rep_commissions component columns, so the drill-down can be checked against
    what actually paid. Read-only; None if no row / column missing."""
    try:
        rc = (client.schema("commcalc").table("rep_commissions")
              .select("epay_salesperson,storeops_name,plan_comm,installment_comm_sale,"
                      "residual_installment_comm,total_payout")
              .eq("org_id", org_id).in_("period", ce._pvariants(period)).limit(5000).execute().data) or []
    except Exception:
        return None
    for r in rc:
        if _name_match(r.get("storeops_name"), rep) or _name_match(r.get("epay_salesperson"), rep):
            return {"plan_comm": safe_float(r.get("plan_comm")),
                    "installment_comm_sale": safe_float(r.get("installment_comm_sale")),
                    "residual_installment_comm": safe_float(r.get("residual_installment_comm")),
                    "total_payout": safe_float(r.get("total_payout")),
                    "source": "rep_commissions (last Run Calculation)"}
    return None


# ── main: per-device story (IMEI search) ────────────────────────────────────────────────────────
def device_story(client, org_id, imei, period=None):
    """Full commission story for one IMEI across reps/periods: sale line(s), plan pay, installments +
    gate reasons, MA-file matches, rebate. READ-ONLY."""
    from app.modules.commcalc import commission_engine as ce, sale_installment_engine as sie
    from app.modules.commcalc.installment_engine import _read_mi

    imei_n = _norm_mdn(imei)
    out = {"imei": imei, "period": period, "sale_lines": [], "plan_pay": [],
           "installments": [], "ma_matches": [], "ma_says_paid": False, "rebate_total": 0.0, "note": None}

    out["sale_lines"] = _sale_lines_by_imei(client, org_id, imei)

    # installments — the persisted ledger holds every past pay_period in one read; if a live `period`
    # is requested we merge that period's live compute (in case the calc hasn't been re-run for it).
    led = []
    try:
        led = (client.schema("commcalc").table("sale_installment_ledger").select("*")
               .eq("org_id", org_id).eq("serial_1", imei_n).order("pay_period").limit(500).execute().data) or []
    except Exception:
        led = []
    for r in led:
        r["_provenance"] = "stored"   # persisted ledger rows omit gate_kind → reduced-confidence reason
    live_withheld = {}   # {device key -> would-be $} for the live period's held rows (from its flags)
    if period:
        try:
            live = sie.compute_sale_installments(client, org_id, period, persist=False)
            have = {(str(x.get("pay_period")), int(x.get("month_index") or 0)) for x in led}
            for r in (live.get("ledger") or []):
                if _norm_mdn(r.get("serial_1")) == imei_n and \
                   (str(r.get("pay_period")), int(r.get("month_index") or 0)) not in have:
                    r["_provenance"] = "live"   # live compute carries gate_kind → precise reason
                    led.append(r)
            for f in (live.get("flags") or []):
                if f.get("source") == "commission_rebate_tracking":
                    k = _norm_mdn(f.get("imei")) or _norm_mdn(f.get("mdn"))
                    if k:
                        live_withheld[k] = safe_float(f.get("amount"))
        except Exception:
            pass

    mi_cache = {}

    def _mi_for(pp):
        if pp not in mi_cache:
            try:
                mi_cache[pp] = sie._mi_index(_read_mi(client, org_id, pp))
            except Exception:
                mi_cache[pp] = {"mdn": {}, "serial": {}}
        return mi_cache[pp]

    # ONE CONSISTENT LABEL for stored rows too (owner 2026-07-27). The persisted ledger carries no
    # display columns on purpose (money columns only), so derive device + rate plan from the sale lines
    # already read for this IMEI: the device line is the one carrying the serial, the rate-plan line is
    # the one that identifies as a plan and carries no device serial.
    _dev_p = next((s.get("product") for s in (out["sale_lines"] or []) if s.get("imei")), None)
    _plan_p = None
    try:
        _pm = sie._norm_plan_matcher(sie.DEFAULT_PLAN_LINE_MATCHER)
        _plan_p = next((s.get("product") for s in (out["sale_lines"] or [])
                        if not s.get("imei")
                        and sie._line_is_plan_line({"product_desc": s.get("product"),
                                                    "department": s.get("department"),
                                                    "category": s.get("category")}, _pm)), None)
    except Exception:
        _plan_p = None

    inst = []
    for r in led:
        mi_row = sie._match_mi({"mdn": r.get("mdn"), "serial_1": r.get("serial_1")}, _mi_for(r.get("pay_period")))
        code, text = _installment_reason(r, mi_row, stored=(r.get("_provenance") == "stored"))
        paid = str(r.get("status") or "") == "paid"
        wk = _norm_mdn(r.get("serial_1")) or _norm_mdn(r.get("mdn"))
        # withheld_amount: 0.0 when paid; the would-be $ when known (live period); None when unknown
        # (a stored row from a period we didn't live-recompute). Same None-means-unknown convention as
        # explain_rep, so the frontend renders "—" for unknown and "$0.00" only for a genuine zero.
        inst.append({"label": (r.get("display_label")
                               or sie.installment_label(r.get("device_product") or _dev_p,
                                                        r.get("plan_product") or _plan_p,
                                                        r.get("mrc_at_pay"))),
                     "device_product": r.get("device_product") or _dev_p,
                     "plan_product": r.get("plan_product") or _plan_p,
                     "device_category": r.get("device_category"),
                     "month_index": r.get("month_index"), "pay_period": r.get("pay_period"),
                     "sale_period": r.get("sale_period"), "amount": safe_float(r.get("amount")),
                     "withheld_amount": (0.0 if paid else live_withheld.get(wk)),
                     "status": r.get("status"), "paid": bool(r.get("paid_gate_met")),
                     "gate_mode": r.get("gate_mode"), "hold_reason": code, "hold_detail": text,
                     "mrc_at_pay": r.get("mrc_at_pay"), "mrc_source": r.get("mrc_source"),
                     "payout_kind": r.get("payout_kind"), "rep": r.get("epay_salesperson"),
                     "mi_ref": _mi_ref(mi_row)})
    inst.sort(key=lambda x: (str(x.get("pay_period")), x.get("month_index") or 0))
    out["installments"] = inst

    # MA cross-reference (multiple rows per period possible; amounts sign-normalized to 'paid')
    out["ma_matches"] = _ma_rows_for_imei(client, org_id, imei)
    out["ma_says_paid"] = _ma_says_paid(out["ma_matches"])
    out["rebate_total"] = round(sum(safe_float(m.get("rebate_paid")) for m in out["ma_matches"]), 2)
    out["ma_spiff_total"] = round(sum(safe_float(m.get("spiff_total_paid")) for m in out["ma_matches"]), 2)

    # plan pay attributable to this device — replay the single-source preview per (rep, sale period).
    plan_pay, seen = [], set()
    for s in out["sale_lines"]:
        rep, per = s.get("salesperson"), s.get("period")
        if not rep or not per or (str(rep), str(per)) in seen:
            continue
        seen.add((str(rep), str(per)))
        try:
            pv = ce.preview(client, org_id, per, detail=True, only_rep=rep)
        except Exception:
            continue
        for row in (pv.get("by_rep") or []):
            for rule in (row.get("rules") or []):
                for ln in (rule.get("lines") or []):
                    if _norm_mdn(ln.get("imei")) == imei_n:
                        plan_pay.append({"period": per, "rep": row.get("rep"), "plan_name": row.get("plan_name"),
                                         "rule": rule.get("label"), "payout_kind": rule.get("payout_kind"),
                                         "amount": ln.get("amount"), "product": ln.get("product"),
                                         "contract_type": ln.get("contract_type"), "gp": ln.get("gp"),
                                         "ext_price": ln.get("ext_price")})
    out["plan_pay"] = plan_pay
    if not out["sale_lines"] and not inst and not out["ma_matches"]:
        out["note"] = f"No sale line, installment, or MA-file row found for IMEI '{imei}' in this org."
    return out
