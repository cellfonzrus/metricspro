"""PER-TENANT AI USAGE + CHARGEABLE PRICE — the pure math behind "what did this tenant's AI cost,
and what do we bill them for it".

OWNER DIRECTIVE 2026-09-05 (sanjot@): *"For every tenant ai usage counter needs to be built and a cost
assigned at the super admin level, the cost for the tenant will be cost of the super admin / platform
per token paid plus % or flat margin assigned by the super admin"*.

So: billable = PLATFORM COST (what we pay Anthropic per token) + MARGIN (super-admin assigned, per
tenant, percentage or flat).

WHAT THIS REUSES RATHER THAN REBUILDS (CLAUDE.md duplicate-check build gate):
  · `core.token_rates` (mig 718) is THE only $/MTok source. There is no fallback rate anywhere in
    this module — when no rate row matches, the cost is None and the reason is recorded, exactly the
    discipline `core/fix_pipeline.py` set. An unpriceable model reports "no active rate", NEVER $0.
  · `fix_pipeline.rate_for(rate_rows, model, org_id, on_date)` resolves WHICH rate row applies —
    tenant row over house row, newest `effective_date <= on_date`, inactive ignored. That is exactly
    the effective-dating this billing needs, already written and already proven, so it is imported
    and called, not re-implemented.
  · `core.ai_call_audit` (mig 972) is the usage source. It already stores input and output tokens
    separately, per org, per purpose, with a timestamp.

WHY THE COST FUNCTION HERE IS NOT `fix_pipeline.compute_cost` (an honest divergence, not a fork).
`compute_cost` prices a single TOTAL token count using a BLENDED in/out rate, because the agent
metadata it was written for reports one number with no split. `core.ai_call_audit` DOES carry the
split, so blending here would deliberately throw away precision we have and bill the tenant on an
assumption (`output_share`, default 0.20) instead of on their actual mix. Output tokens cost 5x input
on every current model, so a tenant whose calls are output-heavy would be systematically under-billed
and one whose calls are input-heavy over-billed. `exact_cost` therefore prices in x rate_in +
out x rate_out, and falls back to the shared blend ONLY when a row genuinely has no split.

MONEY CORRECTNESS — the two rules this module exists to enforce:

  1. HISTORICAL CHARGES NEVER MOVE. `fix_pipeline` recomputes cost from CURRENT rates on every read,
     which is right for a live internal display and WRONG for a billed period. Two mechanisms, both
     needed:
       (a) EFFECTIVE DATING — a call is priced with the rate and margin in force ON THE DAY OF THE
           CALL (`rate_for(..., on_date=call_date)`), so publishing a NEW rate row dated today
           cannot re-price yesterday. The live platform data already exercises this: `claude-sonnet-5`
           carries $2/$10 from 2026-01-01 and $3/$15 from 2026-09-01.
       (b) SNAPSHOT ON CLOSE — effective dating still cannot protect against someone EDITING an
           existing rate row in place (mig 718 allows it; `updated_by` records who). So closing a
           period freezes the applied rate, the applied margin, and the resulting figures onto the
           period record, and a closed period is thereafter READ, never recomputed. `price_period`
           takes `frozen=` for exactly this and returns the stored numbers untouched.

  2. ROUNDING IS DONE ONCE, AT THE END, IN DECIMAL. Sub-cent per-call costs aggregated over thousands
     of calls are where this kind of system quietly drifts: rounding each call to cents and summing
     loses up to half a cent per call (~$5 over 1000 calls). So every per-call cost is computed as an
     exact `Decimal` and carried at full precision; the SUM is rounded once — to 6 dp for the cost
     display (matching fix_pipeline's granularity) and to 2 dp for the amount actually billed.
     ROUND_HALF_UP, because ROUND_HALF_EVEN (Python's default) surprises accountants.

COVERAGE HONESTY (the same rule the control box applies to its lamps). A usage counter fed only by
call sites that happen to be wired would UNDER-REPORT real spend and UNDER-BILL the tenant, while
looking authoritative. `AI_CALL_SITES` therefore declares EVERY known outbound AI call site in the
platform and whether it is metered; `coverage()` turns that into a stated fraction that travels with
every usage figure. An unmetered call site is reported as unmetered — never as zero usage.

PURE: stdlib only (decimal, datetime, re). No DB, no network, no FastAPI. `backend/harness_ai_usage.py`
proves the pricing, the margin variants, effective dating, the no-retroactive-change property, the
rounding rule and the coverage accounting.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

# The house/platform tenant (CLAUDE.md). Its rows are the platform default that a tenant row overrides.
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# Money quantisation. COST is carried to 6 dp (fix_pipeline's granularity — a single call can cost a
# small fraction of a cent); the BILLED amount is quantised to real currency, 2 dp. Both HALF_UP.
_COST_DP = Decimal("0.000001")
_MONEY_DP = Decimal("0.01")
_MTOK = Decimal(1_000_000)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# CALL-SITE REGISTRY — what the meter does and does NOT see
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Every outbound Anthropic call site in the platform, found by grepping `AsyncAnthropic|Anthropic(`
# across app/modules. `metered` says whether that site records usage into core.ai_call_audit.
#
# THIS EXISTS SO THE COUNTER CANNOT LIE. A tenant's bill is only as complete as the metering behind
# it, so the fraction of call sites actually metered is reported ALONGSIDE every usage figure rather
# than being an implementation detail nobody can see. Wiring a site = adding one `ai_meter.record(...)`
# line there and flipping `metered` here; nothing else in this module changes.
#
# `purpose` matches core.ai_call_audit.purpose. NOTE the separation the owner's guard requires:
# METERING IS NOT AUTHORIZATION. Recording usage from a call site does NOT grant that call site the
# control box's super-admin AI permission — `control_box.ai_guard_decision` still governs who may
# SPEND, and it is untouched here.
AI_CALL_SITES = (
    {"key": "control_box_triage", "purpose": "control_box_triage", "metered": True,
     "module": "core", "label": "Control-box AI triage",
     "file": "app/modules/core/control_box_api.py",
     "note": "Metered since mig 972 — the first site on the shared meter."},
    {"key": "account_narrative", "purpose": "account_narrative", "metered": True,
     "module": "account", "label": "P&L narrative",
     "file": "app/modules/account/engine.py (_narrate)",
     "note": "Commentary on a computed statement; every figure is deterministic without it."},
    {"key": "account_recon_missed_days", "purpose": "account_recon_missed_days", "metered": True,
     "module": "account", "label": "VIP credit-memo missed-days note",
     "file": "app/modules/account/recon.py (_missed_days)"},
    {"key": "agency_ocr", "purpose": "agency_ocr", "metered": True,
     "module": "commcalc", "label": "Agency transfer OCR",
     "file": "app/modules/commcalc/agency.py (_ocr_parse_transfer_async)"},
    {"key": "remediation_diagnose", "purpose": "remediation_diagnose", "metered": True,
     "module": "remediation", "label": "Auto-remediation triage",
     "file": "app/modules/remediation/router.py (_ai_diagnose)"},
    {"key": "closing_deposit_slip_ocr", "purpose": "closing_deposit_slip_ocr", "metered": True,
     "module": "closing", "label": "Bank deposit-slip OCR",
     "file": "app/modules/closing/router.py (_ocr_bank_deposit_slip)"},
    {"key": "closing_deposit_amount_ocr", "purpose": "closing_deposit_amount_ocr", "metered": True,
     "module": "closing", "label": "Deposit amount OCR",
     "file": "app/modules/closing/router.py (_ocr_deposit_amount)"},
    {"key": "helpdesk_ai_assist", "purpose": "helpdesk_ai_assist", "metered": True,
     "module": "helpdesk", "label": "Helpdesk AI assistant",
     "file": "app/modules/helpdesk/router.py"},
    {"key": "pos_receipt_ocr", "purpose": "pos_receipt_ocr", "metered": True,
     "module": "pos", "label": "POS receipt OCR",
     "file": "app/modules/pos/receipt_import.py (ocr_receipt)"},
    {"key": "doc_intel_extraction", "purpose": "doc_intel_extraction", "metered": True,
     "module": "storeops", "label": "Lease / insurance document extraction",
     "file": "app/modules/storeops/doc_intel_ai.py",
     "note": "Owned by the insurance/lease agent. Metered only — its authorization stays "
             "can_see_lease; adopting the meter grants it no new permission."},
)

# Sites the platform has but this build does NOT meter. Kept as a first-class list (not an omission)
# so `coverage()` can report them by name. Empty is a claim that must be earned, not assumed.
UNMETERED_SITES = tuple(s for s in AI_CALL_SITES if not s.get("metered"))
METERED_PURPOSES = frozenset(s["purpose"] for s in AI_CALL_SITES if s.get("metered"))


def coverage(observed_purposes=()):
    """How much of the platform's AI spend this counter actually sees. PURE.

    `observed_purposes` are the purposes that appear in the audit rows being reported on. A purpose
    present in the DATA but absent from the registry is surfaced as `unregistered` — that is a call
    site somebody wired without declaring, and it must be visible rather than quietly folded in."""
    total = len(AI_CALL_SITES)
    metered = [s for s in AI_CALL_SITES if s.get("metered")]
    unmetered = [s for s in AI_CALL_SITES if not s.get("metered")]
    known = {s["purpose"] for s in AI_CALL_SITES}
    unregistered = sorted({str(p) for p in (observed_purposes or ()) if p and p not in known})
    complete = not unmetered and not unregistered
    if complete:
        note = ("All %d known AI call sites record usage, so this tenant's counter reflects the "
                "platform's whole AI spend for them." % total)
    else:
        bits = []
        if unmetered:
            bits.append("%d of %d call site(s) do NOT record usage (%s), so real spend is HIGHER "
                        "than the figure shown"
                        % (len(unmetered), total, ", ".join(s["label"] for s in unmetered)))
        if unregistered:
            bits.append("usage arrived from %d undeclared purpose(s) (%s) — a call site was wired "
                        "without being registered here"
                        % (len(unregistered), ", ".join(unregistered)))
        note = "; ".join(bits) + "."
    return {"sites_total": total, "sites_metered": len(metered), "sites_unmetered": len(unmetered),
            "unmetered": [{"key": s["key"], "label": s["label"], "file": s.get("file")}
                          for s in unmetered],
            "unregistered_purposes": unregistered,
            "complete": complete, "note": note}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _dec(v, default=None):
    """Decimal from anything numeric-ish, or `default`. NEVER float-arithmetics money."""
    if v is None or v == "":
        return default
    try:
        return Decimal(str(v))
    except Exception:
        return default


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _day(v):
    """The date part of a timestamp/date, as 'YYYY-MM-DD', or None."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10] or None


def q_cost(d):
    return (d if isinstance(d, Decimal) else Decimal(str(d or 0))).quantize(_COST_DP, ROUND_HALF_UP)


def q_money(d):
    """Quantise to real currency (2 dp), HALF_UP. Called ONCE, on a total — never per call."""
    return (d if isinstance(d, Decimal) else Decimal(str(d or 0))).quantize(_MONEY_DP, ROUND_HALF_UP)


def f(d):
    """Decimal → float for JSON. Only ever applied to an ALREADY-quantised value."""
    return None if d is None else float(d)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# PLATFORM COST — what we pay, per call, from core.token_rates
# ══════════════════════════════════════════════════════════════════════════════════════════════
def exact_cost(input_tokens, output_tokens, rate_row):
    """Platform cost for ONE call, priced on its ACTUAL input/output split. PURE.

    Returns (Decimal cost | None, basis dict). None means "not priceable" — never a guessed zero.
    Unlike fix_pipeline.compute_cost this does NOT blend, because core.ai_call_audit carries the real
    split and output tokens cost ~5x input on every current model (see the module docstring)."""
    tin, tout = max(0, _int(input_tokens)), max(0, _int(output_tokens))
    if not rate_row:
        return None, {"reason": "no active core.token_rates row matches this model — set one at "
                                "/admin/fix-requests to price it",
                      "input_tokens": tin, "output_tokens": tout}
    rin = _dec(rate_row.get("usd_per_mtok_in"))
    rout = _dec(rate_row.get("usd_per_mtok_out"))
    if rin is None or rout is None:
        return None, {"reason": "the matching core.token_rates row has no usable in/out rate",
                      "input_tokens": tin, "output_tokens": tout,
                      "rate_id": rate_row.get("id")}
    cost = (Decimal(tin) * rin + Decimal(tout) * rout) / _MTOK
    return cost, {
        "input_tokens": tin, "output_tokens": tout,
        "model": rate_row.get("model"), "rate_id": rate_row.get("id"),
        "usd_per_mtok_in": str(rin), "usd_per_mtok_out": str(rout),
        "effective_date": rate_row.get("effective_date"),
        "method": "exact: (in x rate_in + out x rate_out) / 1e6 — core.ai_call_audit carries the real "
                  "split, so no output_share blend is used or needed",
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# MARGIN — super-admin assigned, per tenant, effective-dated
# ══════════════════════════════════════════════════════════════════════════════════════════════
# WHAT "FLAT" MEANS — stated plainly for the owner to correct. The directive says "plus % or flat
# margin". A flat margin has three defensible readings (per call, per token, per period) and they are
# not close to each other, so this does NOT guess: `flat_basis` is explicit config.
#
#   flat_basis = 'period' (DEFAULT) — one fixed USD amount added per tenant per billing period. This
#       is the reading shipped as the default because it is what a "service fee on top of pass-through
#       cost" normally means, and it is the only one that stays predictable for the tenant when usage
#       is spiky.
#   flat_basis = 'call'             — a fixed USD amount added per AI call.
#
# A per-TOKEN flat is deliberately NOT offered: that is a rate, not a margin, and it belongs in
# core.token_rates where the owner already controls per-token pricing.
#
# Percentage and flat COMBINE when both are set (percent applied to platform cost, then flat added),
# so "cost + 20% + $50/month" is expressible without a third mode.
MARGIN_MODES = ("percent", "flat", "percent_plus_flat")
FLAT_BASES = ("period", "call")

DEFAULT_MARGIN = {
    "mode": "percent",
    "percent": Decimal("0"),      # 0% — a tenant with NO configured margin is billed pass-through
    "flat_usd": Decimal("0"),
    "flat_basis": "period",
}


def normalize_margin(row):
    """A core.ai_margin_config row (or None) → the margin dict this module's math takes. PURE.

    A missing/garbage row degrades to the house default of ZERO margin — pass-through cost. That is
    the safe direction: never invent a charge the operator did not configure."""
    m = dict(DEFAULT_MARGIN)
    if not row:
        return {**m, "source": "default", "note": "No margin configured — billed at platform cost."}
    mode = str(row.get("mode") or "").strip().lower()
    if mode not in MARGIN_MODES:
        mode = "percent"
    pct = _dec(row.get("percent"), Decimal("0")) or Decimal("0")
    flat = _dec(row.get("flat_usd"), Decimal("0")) or Decimal("0")
    basis = str(row.get("flat_basis") or "period").strip().lower()
    if basis not in FLAT_BASES:
        basis = "period"
    # A negative margin would BILL LESS THAN COST. That is a discount, not a margin, and it is not
    # what the directive asked for — clamp at zero rather than silently selling at a loss.
    if pct < 0:
        pct = Decimal("0")
    if flat < 0:
        flat = Decimal("0")
    if mode == "percent":
        flat = Decimal("0")
    elif mode == "flat":
        pct = Decimal("0")
    return {"mode": mode, "percent": pct, "flat_usd": flat, "flat_basis": basis,
            "source": "config", "effective_date": row.get("effective_date"),
            "config_id": row.get("id")}


def margin_for(margin_rows, org_id, on_date=None, house_org=HOUSE_ORG):
    """WHICH margin applies to this tenant on this date. PURE.

    Deliberately the SAME resolution shape as `fix_pipeline.rate_for`: the tenant's own row beats the
    house row, and within a scope the newest `effective_date <= on_date` wins. That is what stops a
    margin change today from re-pricing last month — and it means margin history is just rows, so the
    config table IS the audit trail (who/when live on each row)."""
    ref = str(on_date or date.today().isoformat())[:10]
    best, best_key = None, None
    for r in (margin_rows or []):
        if r.get("is_active") is False:
            continue
        row_org = r.get("org_id")
        if row_org != org_id and row_org != house_org:
            continue
        eff = str(r.get("effective_date") or "")[:10]
        if eff and eff > ref:
            continue                                   # a future margin does not price today
        scope = 1 if (org_id and row_org == org_id and org_id != house_org) else 0
        key = (scope, eff)
        if best_key is None or key > best_key:
            best, best_key = r, key
    return normalize_margin(best)


def apply_margin(platform_cost, margin, *, call_count=0):
    """platform cost (Decimal) + margin → (billable Decimal, breakdown). PURE, exact, unrounded.

    Rounding is NOT done here on purpose — the caller quantises the TOTAL once (see the module
    docstring on drift). Returns full-precision Decimals."""
    cost = platform_cost if isinstance(platform_cost, Decimal) else (_dec(platform_cost) or Decimal("0"))
    m = margin or DEFAULT_MARGIN
    pct = m.get("percent") or Decimal("0")
    flat = m.get("flat_usd") or Decimal("0")
    basis = m.get("flat_basis") or "period"
    pct_amount = cost * pct / Decimal("100")
    if flat and basis == "call":
        flat_amount = flat * Decimal(max(0, _int(call_count)))
    elif flat:
        flat_amount = flat
    else:
        flat_amount = Decimal("0")
    billable = cost + pct_amount + flat_amount
    return billable, {
        "mode": m.get("mode"), "percent": str(pct), "flat_usd": str(flat), "flat_basis": basis,
        "platform_cost": str(cost), "margin_percent_amount": str(pct_amount),
        "margin_flat_amount": str(flat_amount), "margin_total": str(pct_amount + flat_amount),
        "flat_note": ("flat applied once for the period" if flat and basis == "period"
                      else "flat applied per call x %d" % _int(call_count) if flat
                      else "no flat component"),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE PERIOD BILL
# ══════════════════════════════════════════════════════════════════════════════════════════════
def price_period(rows, rate_rows, margin_rows, *, org_id, period_start=None, period_end=None,
                 house_org=HOUSE_ORG, frozen=None, margin_on=None):
    """One tenant's AI usage for a period → what it cost us and what we bill. PURE.

    `rows` are core.ai_call_audit rows. `rate_rows` are core.token_rates. `margin_rows` are
    core.ai_margin_config. `frozen` is a CLOSED period's stored snapshot.

    THE CLOSED-PERIOD RULE. If `frozen` is given, its stored figures are returned untouched and
    NOTHING is recomputed. A closed period is a historical fact: editing a rate or a margin afterwards
    must not move a number a tenant was already billed. The harness proves this by closing a period,
    changing both the rate and the margin, and re-running.

    EACH CALL IS PRICED ON ITS OWN DAY, with the rate in force then — so even an OPEN period is
    immune to a new effective-dated rate re-pricing calls that already happened.

    Only ALLOWED rows are billed: a refused call (`allowed=false`) spent no tokens and must never
    reach a tenant's invoice."""
    if frozen:
        out = dict(frozen)
        out["recomputed"] = False
        out["note"] = ("This period is CLOSED. The figures are the ones frozen at close, including "
                       "the rate and margin then in force — later rate or margin edits cannot move "
                       "them.")
        return out

    # `fix_pipeline` owns rate RESOLUTION (tenant-over-house, newest effective_date <= the day).
    # Imported, not re-implemented — one resolver or the two will drift.
    from app.modules.core.fix_pipeline import rate_for

    billed = [r for r in (rows or []) if r.get("allowed")]
    total_cost = Decimal("0")
    calls = priced = unpriced = 0
    tok_in = tok_out = 0
    by_purpose = {}
    by_model = {}
    unpriced_models = {}
    observed = set()

    for r in billed:
        calls += 1
        purpose = r.get("purpose") or "unknown"
        observed.add(purpose)
        model = r.get("model") or ""
        ti, to = max(0, _int(r.get("input_tokens"))), max(0, _int(r.get("output_tokens")))
        tok_in += ti
        tok_out += to
        day = _day(r.get("created_at"))
        rate = rate_for(rate_rows, model, org_id=org_id, house_org=house_org, on_date=day)
        cost, _basis = exact_cost(ti, to, rate)
        p = by_purpose.setdefault(purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                            "platform_cost": Decimal("0"), "unpriced_calls": 0})
        p["calls"] += 1
        p["input_tokens"] += ti
        p["output_tokens"] += to
        mk = by_model.setdefault(model or "(none)", {"calls": 0, "tokens": 0,
                                                     "platform_cost": Decimal("0"), "unpriced": 0})
        mk["calls"] += 1
        mk["tokens"] += ti + to
        if cost is None:
            unpriced += 1
            p["unpriced_calls"] += 1
            mk["unpriced"] += 1
            unpriced_models[model or "(none)"] = unpriced_models.get(model or "(none)", 0) + 1
        else:
            priced += 1
            total_cost += cost                    # FULL precision; never rounded per call
            p["platform_cost"] += cost
            mk["platform_cost"] += cost

    margin = margin_for(margin_rows, org_id, on_date=(margin_on or period_end or period_start),
                        house_org=house_org)
    billable, breakdown = apply_margin(total_cost, margin, call_count=calls)

    # ONE rounding, at the end, on the totals.
    cost_q = q_cost(total_cost)
    billable_q = q_money(billable)
    cov = coverage(observed)

    return {
        "org_id": org_id,
        "period_start": period_start, "period_end": period_end,
        "calls": calls, "priced_calls": priced, "unpriced_calls": unpriced,
        "input_tokens": tok_in, "output_tokens": tok_out, "tokens": tok_in + tok_out,
        "platform_cost_usd": f(cost_q),
        "billable_usd": f(billable_q),
        "margin": {"mode": margin.get("mode"), "percent": str(margin.get("percent")),
                   "flat_usd": str(margin.get("flat_usd")), "flat_basis": margin.get("flat_basis"),
                   "source": margin.get("source"), "effective_date": margin.get("effective_date")},
        "margin_breakdown": breakdown,
        "margin_usd": f(q_money(billable - total_cost)),
        "by_purpose": {k: {**v, "platform_cost": f(q_cost(v["platform_cost"]))}
                       for k, v in sorted(by_purpose.items())},
        "by_model": {k: {**v, "platform_cost": f(q_cost(v["platform_cost"]))}
                     for k, v in sorted(by_model.items())},
        # UNPRICEABLE SPEND IS STATED, NEVER ZEROED. A model with no rate row produced real tokens
        # that really cost money; showing $0 for it would understate the bill and hide the missing
        # rate row. (Same discipline as fix_pipeline: "—", not a fabricated number.)
        "unpriced_models": unpriced_models,
        "unpriced_note": (None if not unpriced else
                          "%d call(s) on %d model(s) (%s) have NO core.token_rates row and are "
                          "EXCLUDED from the cost above — the real platform cost is higher. Add a "
                          "rate for them to price this period."
                          % (unpriced, len(unpriced_models), ", ".join(sorted(unpriced_models)))),
        "coverage": cov,
        "recomputed": True,
        "rounding": "per-call costs kept at full Decimal precision; totals quantised ONCE — cost to "
                    "6 dp, billable to 2 dp, ROUND_HALF_UP",
    }


def snapshot_for_close(priced, *, closed_by=None, now=None):
    """Freeze an OPEN period's computed figures into the record stored at close. PURE.

    Everything needed to defend the invoice later travels with it — the applied margin, the per-model
    breakdown, the unpriced accounting and the metering coverage — so the closed period can be read
    back and explained without re-deriving anything from tables that may since have changed."""
    ts = (now or datetime.now(timezone.utc))
    return {
        "org_id": priced.get("org_id"),
        "period_start": priced.get("period_start"), "period_end": priced.get("period_end"),
        "status": "closed",
        "calls": priced.get("calls"), "priced_calls": priced.get("priced_calls"),
        "unpriced_calls": priced.get("unpriced_calls"),
        "input_tokens": priced.get("input_tokens"), "output_tokens": priced.get("output_tokens"),
        "tokens": priced.get("tokens"),
        "platform_cost_usd": priced.get("platform_cost_usd"),
        "billable_usd": priced.get("billable_usd"),
        "margin_usd": priced.get("margin_usd"),
        "margin_snapshot": priced.get("margin"),
        "breakdown_snapshot": {"by_purpose": priced.get("by_purpose"),
                               "by_model": priced.get("by_model"),
                               "margin_breakdown": priced.get("margin_breakdown"),
                               "unpriced_models": priced.get("unpriced_models"),
                               "unpriced_note": priced.get("unpriced_note"),
                               "coverage": priced.get("coverage")},
        "closed_by": closed_by, "closed_at": ts.isoformat(),
    }


def period_bounds(year, month):
    """[first day, last day] of a calendar month as ISO dates. PURE, stdlib only."""
    y, m = int(year), int(month)
    if not 1 <= m <= 12:
        raise ValueError("month must be 1-12")
    start = date(y, m, 1)
    next_month = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    last = date.fromordinal(next_month.toordinal() - 1)
    return start.isoformat(), last.isoformat()


def in_period(row, period_start, period_end):
    """Is this audit row inside [start, end] by CALL DATE? PURE. Inclusive both ends."""
    d = _day(row.get("created_at"))
    if not d:
        return False
    if period_start and d < str(period_start)[:10]:
        return False
    if period_end and d > str(period_end)[:10]:
        return False
    return True


def summarize_tenants(priced_list):
    """Platform-wide roll-up across tenants — the super-admin's total revenue/cost view. PURE.

    Returns totals only plus a per-tenant line; it carries NO tenant business figures (no stores,
    reps, periods or commission data), so it is safe for the cross-org super-admin surface."""
    cost = sum((Decimal(str(p.get("platform_cost_usd") or 0)) for p in priced_list), Decimal("0"))
    bill = sum((Decimal(str(p.get("billable_usd") or 0)) for p in priced_list), Decimal("0"))
    any_incomplete = any(not (p.get("coverage") or {}).get("complete") for p in priced_list)
    return {
        "tenants": len(priced_list),
        "calls": sum(_int(p.get("calls")) for p in priced_list),
        "tokens": sum(_int(p.get("tokens")) for p in priced_list),
        "platform_cost_usd": f(q_cost(cost)),
        "billable_usd": f(q_money(bill)),
        "margin_usd": f(q_money(bill - cost)),
        "coverage_complete": not any_incomplete,
        "note": ("Totals across tenants. Lamps and money only — no tenant business data."
                 if not any_incomplete else
                 "Totals across tenants. At least one tenant's metering is INCOMPLETE, so real "
                 "platform spend is higher than shown."),
    }
