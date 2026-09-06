"""THE ITEMIZED TENANT STATEMENT — monthly fee + per-module usage + AI usage, on ONE document.

OWNER DIRECTIVE 2026-09-05 (sanjot@): *"…have an itemized statement for the tenant for a clear
visibility including their monthly fee, the billing right now is very high level… the billing engine
should list all the modules and an option to assign price against them, a drop down menu to assign
what kind of plan could belong to like free, starter, premium etc so those items can be checked off
from the multi user and assigned a price right next to it"*.

WHAT THIS REUSES RATHER THAN REBUILDS (CLAUDE.md duplicate-check build gate):
  · `core.entitlements.MODULE_CATALOG` / `load_module_catalog` is THE module registry. The pricing
    grid is DERIVED from it, so a newly added module appears automatically. Nothing here hand-writes
    a module list — `main.py:_mounted_modules` exists because a hardcoded literal went stale and
    "CONFIDENTLY MISREPRESENTS the deployment"; the same bug here would mean a module silently
    billing nothing.
  · `storeops.pricing_package` (mig 908) IS the plan/tier table — "free / starter / premium" are
    ROWS, not a code enum (RULE TWO), and the operator adds a tier without a deploy. No parallel plan
    table is created.
  · `storeops.tenants.package_key` (mig 908) already assigns a tenant to a plan. Reused as-is.
  · `billing/ai_usage.price_period` supplies the AI lines, so AI spend appears as line items on the
    SAME statement rather than in a second system the tenant has to reconcile by hand.
  · `billing/module_usage.rollup_by_module` supplies the call counts.
  Only two things were genuinely missing: a PRICE PER (plan, module), and the statement itself.

══ HONESTY: UNPRICED IS NOT FREE, AND IT IS NOT $0 ═══════════════════════════════════════════════
"Nothing is for free" means every module must HAVE a price, not that every module is silently zero
until someone notices. Three distinct states, kept distinct all the way onto the statement:

    included   the plan's monthly fee covers it — an explicit operator decision, charged $0 on the
               line and labelled as included.
    priced     a unit price the operator typed. $0.00 is a legitimate price IF the operator typed 0.
    UNPRICED   nobody has set a price. The line shows the usage and the words "not yet priced", is
               EXCLUDED from the total, and the statement is flagged `complete: false`.

An unpriced module is a bill that cannot be sent, and saying so is the only safe behaviour: a quiet
$0.00 would under-charge silently and forever. This is the same rule already applied to grey
"not monitored" control-box lamps and to unmetered AI call sites.

══ MONEY CORRECTNESS ═════════════════════════════════════════════════════════════════════════════
1. NO RETROACTIVE CHANGE. Module prices are EFFECTIVE-DATED and resolved for the period being billed
   (`price_for`, same tenant-over-house / newest-effective-date shape as `fix_pipeline.rate_for` and
   `ai_usage.margin_for` — one resolution idea, used three times, not three implementations). And
   because `storeops.pricing_package.price` (the monthly fee) has NO effective_date column and can be
   edited in place, a CLOSED statement is FROZEN: `build_statement(..., frozen=)` returns the stored
   document untouched and recomputes nothing. Proven in `harness_module_billing.py` by changing both
   a module price and the plan's monthly fee after close and re-reading.

2. ROUNDING — deliberately a DIFFERENT rule from `ai_usage`, for a documented reason. `ai_usage`
   sums per-call costs at full precision and rounds ONCE, because it produces a single figure. A
   STATEMENT is a document a human checks with a calculator: its lines MUST add up to its total. So
   each LINE is quantised once to cents (ROUND_HALF_UP), and the total is the sum of the QUANTISED
   lines — never a separately-rounded grand total, which is how an invoice ends up off by a cent from
   its own lines. Within a line, `calls x unit_price` is computed at full Decimal precision first, so
   thousands of sub-cent per-call charges do not drift. Both properties are proven.

PURE: stdlib only (decimal, datetime). No DB, no network, no FastAPI.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.modules.billing.ai_usage import HOUSE_ORG, q_money, f, _dec, _int

# How a module is charged. `unpriced` is a first-class state, not the absence of one.
PRICE_MODES = ("per_call", "flat", "included", "unpriced")

# The plan key used when a tenant has no package assigned. It is a ROW like any other tier, not a
# special case in code — the operator can price the default tier exactly like `starter` or `premium`.
DEFAULT_PLAN_KEY = "default"


def _q(d):
    """Quantise ONE line to real cents, HALF_UP."""
    return (d if isinstance(d, Decimal) else Decimal(str(d or 0))).quantize(Decimal("0.01"),
                                                                           ROUND_HALF_UP)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# PRICE RESOLUTION — which price applies to (this plan, this module) on this date
# ══════════════════════════════════════════════════════════════════════════════════════════════
def price_for(price_rows, plan_key, module_key, on_date=None, default_plan=DEFAULT_PLAN_KEY):
    """The `core.module_price` row that applies. PURE. None ⇒ the module is UNPRICED.

    Resolution mirrors `fix_pipeline.rate_for` exactly, so the platform has ONE idea of "which
    config row is in force": the tenant's OWN plan beats the default plan, and within a plan the
    newest `effective_date <= on_date` wins. A future-dated price never prices today."""
    ref = str(on_date or date.today().isoformat())[:10]
    best, best_key = None, None
    for r in price_rows or []:
        if r.get("is_active") is False:
            continue
        if (r.get("module_key") or r.get("module")) != module_key:
            continue
        pk = r.get("plan_key")
        if pk != plan_key and pk != default_plan:
            continue
        eff = str(r.get("effective_date") or "")[:10]
        if eff and eff > ref:
            continue
        scope = 1 if (plan_key and pk == plan_key and pk != default_plan) else 0
        key = (scope, eff)
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best


def module_line(module_key, label, usage, price_row, *, period_days=1):
    """One module's statement line. PURE, and honest about not knowing a price.

    `usage` is the rollup for that module ({calls, billable_calls, system_calls, …}).
    Returns a line dict with `amount` (Decimal, already quantised) or `amount=None` when UNPRICED —
    an unpriced line is NEVER $0."""
    billable = max(0, _int((usage or {}).get("billable_calls")))
    total_calls = max(0, _int((usage or {}).get("calls")))
    system_calls = max(0, _int((usage or {}).get("system_calls")))
    base = {
        "kind": "module", "module": module_key, "label": label or module_key,
        "calls": total_calls, "billable_calls": billable, "system_calls": system_calls,
    }
    if price_row is None:
        return {**base, "mode": "unpriced", "unit_price": None, "amount": None, "priced": False,
                "note": "Not yet priced — this module has no price set for this plan, so it is "
                        "EXCLUDED from the total. It is not free; it is unpriced."}
    mode = str(price_row.get("mode") or "").strip().lower()
    unit = _dec(price_row.get("unit_price"), None)
    if mode == "included":
        return {**base, "mode": "included", "unit_price": None, "amount": _q(0), "priced": True,
                "note": "Included in the plan's monthly fee."}
    if mode == "flat":
        if unit is None:
            return {**base, "mode": "unpriced", "unit_price": None, "amount": None, "priced": False,
                    "note": "Priced as a flat charge but no amount is set — excluded from the total."}
        return {**base, "mode": "flat", "unit_price": str(unit), "amount": _q(unit), "priced": True,
                "note": "Flat charge for the period."}
    if mode == "per_call":
        if unit is None:
            return {**base, "mode": "unpriced", "unit_price": None, "amount": None, "priced": False,
                    "note": "Priced per call but no unit price is set — excluded from the total."}
        # FULL precision for calls x unit, quantised ONCE for the line (see the rounding note above).
        return {**base, "mode": "per_call", "unit_price": str(unit),
                "amount": _q(Decimal(billable) * unit), "priced": True,
                "note": "%d billable call(s) x $%s%s" % (
                    billable, unit,
                    "" if not system_calls else
                    " — %d platform-initiated call(s) excluded" % system_calls)}
    return {**base, "mode": "unpriced", "unit_price": None, "amount": None, "priced": False,
            "note": "Unrecognised price mode %r — excluded from the total rather than guessed." % mode}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE STATEMENT
# ══════════════════════════════════════════════════════════════════════════════════════════════
def build_statement(*, org_id, period_start, period_end, catalog, usage_rows=None,
                    price_rows=None, plan=None, plan_key=None, ai_period=None, frozen=None,
                    tenant_name=None, now=None):
    """One tenant, one period → the itemized statement. PURE.

    `catalog`      {module_key: label} — core.entitlements.load_module_catalog(). THE registry; every
                   module in it gets a line, so a module that was never used still shows (at 0 calls,
                   or as unpriced), and a module that exists cannot be silently missing.
    `usage_rows`   core.module_usage_daily rows for this tenant and period.
    `price_rows`   core.module_price rows.
    `plan`         the storeops.pricing_package row (the monthly fee).
    `ai_period`    the output of ai_usage.price_period — AI spend as line items on this same document.
    `frozen`       a CLOSED statement's stored document; when given it is returned untouched.

    A CLOSED statement is a historical fact. Editing a module price or the plan's monthly fee
    afterwards must not move a number the tenant was already billed."""
    if frozen:
        out = dict(frozen)
        out["recomputed"] = False
        out["note"] = ("This statement is CLOSED. Every figure is the one frozen at close, including "
                       "the plan fee and the module prices then in force — later price or plan edits "
                       "cannot move them.")
        return out

    from app.modules.billing.module_usage import rollup_by_module

    pkey = plan_key or (plan or {}).get("key") or DEFAULT_PLAN_KEY
    usage = rollup_by_module(usage_rows or [])
    lines = []

    # ── 1. the monthly fee ───────────────────────────────────────────────────────────────────
    # The owner asked for the monthly fee to be visible ON the statement, not implied by it.
    if plan:
        fee = _dec(plan.get("price"), None)
        if fee is None:
            lines.append({"kind": "plan_fee", "module": None,
                          "label": "%s plan — monthly fee" % (plan.get("name") or pkey),
                          "plan_key": pkey, "mode": "unpriced", "unit_price": None,
                          "amount": None, "priced": False,
                          "note": "This plan has no price set — excluded from the total."})
        else:
            lines.append({"kind": "plan_fee", "module": None,
                          "label": "%s plan — monthly fee" % (plan.get("name") or pkey),
                          "plan_key": pkey, "mode": "flat",
                          "unit_price": str(fee), "amount": _q(fee), "priced": True,
                          "note": (plan.get("unit_label") or "Plan subscription for the period.")})
    else:
        lines.append({"kind": "plan_fee", "module": None, "label": "Monthly fee",
                      "plan_key": pkey, "mode": "unpriced", "unit_price": None, "amount": None,
                      "priced": False,
                      "note": "No plan is assigned to this tenant, so no monthly fee can be billed. "
                              "Assign a plan on the billing screen."})

    # ── 2. one line per MODULE IN THE CATALOG (not per module that happened to be used) ───────
    for mkey, label in sorted((catalog or {}).items(), key=lambda kv: (kv[1] or kv[0]).lower()):
        u = usage.get(mkey) or {}
        row = price_for(price_rows, pkey, mkey, on_date=period_end)
        line = module_line(mkey, label, u, row)
        # A module with no usage AND no price is noise on an invoice; keep it only when it either
        # cost something or needs the operator's attention (used but unpriced).
        if line["calls"] == 0 and not line["priced"]:
            line["suppressed"] = True
        lines.append(line)

    # ── 3. usage the platform could not attribute to a module ────────────────────────────────
    for bucket in ("unmapped",):
        u = usage.get(bucket)
        if u and _int(u.get("calls")):
            lines.append({
                "kind": "unmapped", "module": bucket, "label": "Unrecognised routes",
                "calls": _int(u.get("calls")), "billable_calls": _int(u.get("billable_calls")),
                "system_calls": _int(u.get("system_calls")),
                "mode": "unpriced", "unit_price": None, "amount": None, "priced": False,
                "note": "These calls hit routes with no billable-module mapping. They are shown so "
                        "the usage is visible, and are EXCLUDED from the total until mapped."})

    # ── 4. AI usage, as line items on the SAME statement ─────────────────────────────────────
    if ai_period:
        ai_amount = _dec(ai_period.get("billable_usd"), None)
        ai_line = {
            "kind": "ai_usage", "module": "ai_assistant",
            "label": "AI usage (%s call(s), %s tokens)" % (ai_period.get("calls") or 0,
                                                           ai_period.get("tokens") or 0),
            "calls": _int(ai_period.get("calls")),
            "billable_calls": _int(ai_period.get("priced_calls")),
            "mode": "usage", "unit_price": None,
            "amount": None if ai_amount is None else _q(ai_amount),
            "priced": ai_amount is not None,
            "note": _ai_note(ai_period),
        }
        lines.append(ai_line)

    # ── 5. totals — the sum of the QUANTISED lines, so the document adds up ───────────────────
    priced_lines = [l for l in lines if l.get("priced") and l.get("amount") is not None]
    unpriced_lines = [l for l in lines if not l.get("priced") and not l.get("suppressed")]
    total = sum((l["amount"] for l in priced_lines), Decimal("0"))

    complete = not unpriced_lines
    return {
        "org_id": org_id, "tenant_name": tenant_name,
        "period_start": period_start, "period_end": period_end,
        "plan_key": pkey, "plan_name": (plan or {}).get("name"),
        "currency": (plan or {}).get("currency") or "USD",
        "lines": [_render(l) for l in lines],
        "total_usd": f(_q(total)),
        "billable_calls": sum(_int(l.get("billable_calls")) for l in lines if l.get("kind") == "module"),
        "total_calls": sum(_int(l.get("calls")) for l in lines if l.get("kind") == "module"),
        "unpriced": [{"label": l.get("label"), "module": l.get("module"), "note": l.get("note")}
                     for l in unpriced_lines],
        "complete": complete,
        "complete_note": (
            "Every line on this statement is priced." if complete else
            "%d line(s) are NOT PRICED and are excluded from the total, so this statement is "
            "INCOMPLETE and must not be sent as a final invoice: %s."
            % (len(unpriced_lines), ", ".join(str(l.get("label")) for l in unpriced_lines))),
        "rounding": "each line is quantised once to cents (ROUND_HALF_UP) and the total is the sum "
                    "of the quantised lines, so the document always adds up",
        "recomputed": True,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


def _ai_note(ai):
    bits = []
    if ai.get("margin_usd") is not None:
        bits.append("platform cost $%s + margin $%s" % (ai.get("platform_cost_usd"),
                                                        ai.get("margin_usd")))
    if ai.get("unpriced_note"):
        bits.append(ai["unpriced_note"])
    cov = ai.get("coverage") or {}
    if cov and not cov.get("complete"):
        bits.append(cov.get("note") or "")
    return " — ".join(b for b in bits if b) or "AI usage for the period."


def _render(line):
    """Decimal → float for JSON, once, on an already-quantised amount."""
    out = dict(line)
    if isinstance(out.get("amount"), Decimal):
        out["amount"] = f(out["amount"])
    return out


def freeze_statement(stmt, *, closed_by=None, now=None):
    """Freeze an open statement into the record stored at close. PURE.

    An INCOMPLETE statement can still be frozen (the operator may knowingly close a period with an
    unpriced module), but the incompleteness is frozen with it — so it is never possible to look at a
    closed statement and not know that something was left unpriced."""
    ts = now or datetime.now(timezone.utc)
    return {**stmt, "status": "closed", "closed_by": closed_by, "closed_at": ts.isoformat(),
            "recomputed": False}


def pricing_grid(catalog, price_rows, plans, on_date=None, default_plan=DEFAULT_PLAN_KEY):
    """THE OPERATOR SCREEN'S DATA: every module x every plan, with the price against each. PURE.

    Driven off the ENTITLEMENT CATALOG, so a module added to the platform appears here automatically
    with an explicit `unpriced` cell rather than being absent (and therefore silently unbilled).
    That is the whole point of the grid: the operator can see, at a glance, every hole."""
    plan_keys = [p.get("key") for p in (plans or []) if p.get("key")] or [default_plan]
    grid, holes = [], 0
    for mkey, label in sorted((catalog or {}).items(), key=lambda kv: (kv[1] or kv[0]).lower()):
        cells = {}
        for pk in plan_keys:
            row = price_for(price_rows, pk, mkey, on_date=on_date, default_plan=default_plan)
            if row is None:
                cells[pk] = {"mode": "unpriced", "unit_price": None, "priced": False}
                holes += 1
            else:
                cells[pk] = {"mode": row.get("mode"), "unit_price": (None if row.get("unit_price") is None
                                                                     else str(row.get("unit_price"))),
                             "priced": True, "effective_date": row.get("effective_date"),
                             "included": row.get("mode") == "included"}
        grid.append({"module": mkey, "label": label, "plans": cells})
    return {
        "plans": plan_keys, "modules": grid,
        "unpriced_cells": holes,
        "note": ("Every module is priced on every plan." if not holes else
                 "%d module/plan combination(s) have NO price. Those modules are NOT free — they are "
                 "unpriced, and any statement that uses them is incomplete." % holes),
        "source": "core.entitlements module catalog — a new module appears here automatically",
    }
