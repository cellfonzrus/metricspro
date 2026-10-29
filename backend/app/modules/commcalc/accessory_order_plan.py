"""ACCESSORY ORDER PLAN — what to buy, from what the stores actually sold, once a human has said
which handsets are still current.

OWNER ASK (2026-10-29, verbatim): *"for sales forecasting need to add one more report to process
accessory orders based on the previous 3 months of average sales and showing the best sellign models
on the top and asking the user to enter where the spoecific model is end of life or current , once
tehy check the system calculates the following accessory projected to sell in the next 2 months , the
case mix will be decalred by the user but by defaul you can use this mix - Blacks and Blues - 70%,
pinks / red - 30% - 1 per sale / screen protectors - 1 per sale / Cables type c- type c 1 per sale /
Charging cubes - 1 per sale / report should have all out store filters and market filters , look back
months and future planning months / use the most update planning feature so we can then convert this
into an automated accessory ordering system direclty fed into shopify and recurreing fulfiulling
ssytem when the sales happen in the store"*.

THE QUESTION, in one line: **the stores sold these handsets over the last few months; which of them
are we still selling, and therefore how many cases, protectors, cables and cubes have to be on the
shelf for the next few?**

The step that makes it a plan rather than an extrapolation is the HUMAN ONE. A phone being retired
sells exactly as well in the history as a phone being launched, and no feed on this platform knows
which is which. So the report ranks the models, asks for a ruling, and projects only what has been
ruled on — see "an unruled model is not a zero" below, which is the rule this file exists to hold.

════════════════════════════════════════════════════════════════════════════════════════════════════
DUPLICATE CHECK (build gate, CLAUDE.md) — WHAT WAS SEARCHED AND WHAT IS REUSED
════════════════════════════════════════════════════════════════════════════════════════════════════
Searched `docs/SYSTEM_DATA_FLOW_INDEX.md` before a line was written: §15 / module 095 (Device
Forecasting & Vendor Payables and its `/forecast`), §COMMCALC-25 (that report's date window and store
filter), §36 (Supply ordering, vendor comparison, favourites and reorder quantities), §51 (the vendor
order route and the Shopify draft order), §61 (Accessory Target Allocation), §62 (Product Mix & Ports),
§19.55 (what counts as an accessory), §16-18 (the table / endpoint / metric cross-references).

| the fact this report needs | the ONE home it DEREFERENCES (never re-derives) |
|---|---|
| how many will sell over the next N months | `commcalc/demand_projection.projection` — THE velocity/horizon arithmetic, extracted in this change from the two copies `payables/router.forecast` and `asset/purchase_orders.po_recommendations` kept, and both now dereference it |
| which sale line sold a device | `commcalc/demand_projection.is_device_line` — the same extraction; it was the other duplicated half |
| the device MODEL out of a promo-decorated product description | `commcalc/product_mix.model_of` (§62) — not re-parsed here |
| which month's sale lines to read, and which store a spelling names | `router._sales_rows_union` + `router._store_code_resolver` + `_store_market_resolver` (§3 / §5) — the same readers every sales report uses |
| the raw model → canonical model map | `commcalc.device_model_alias` (mig `096`) — read by the endpoint, which is also where the lifecycle ruling is STORED, so "what we know about this device model" has one home |
| what an accessory IS | NOT asked. This report plans PURCHASES by device unit; it never classifies a sold accessory line, so §19.55's classifier is deliberately untouched |

**NEW here and nowhere else:** the per-model lifecycle ruling, the colour mix of a case order, the
attach plan (how many of each accessory per device sold), and the largest-remainder colour split.

**NO NEW TABLE FOR THE RULING.** `commcalc.device_model_alias` is already the platform's per-model
registry, so the ruling is a COLUMN on it (mig `1104`) rather than a sibling table that would drift
from the canonical-model map within a month. The mix and the attach plan are columns on
`commcalc.accessory_config`, the row that already holds this tenant's accessory vocabulary — the same
extension shape mig `1031` used for the port-out rules.

**FEEDING SHOPIFY LATER IS A SHAPE, NOT A SWITCH.** The owner's last sentence asks for this to become
an automated order. §51 already owns that road (`order_transport` → `shopify_draft_order` →
`vendor_customer`), and nothing here is wired to it or switched on. What this module does is hand back
order lines keyed by a declared accessory key with an integer quantity — the shape a draft order
takes — so wiring it later is a route change and not a re-derivation.

════════════════════════════════════════════════════════════════════════════════════════════════════
THE ARITHMETIC, STATED SO IT CAN BE ARGUED WITH
════════════════════════════════════════════════════════════════════════════════════════════════════
    per model:  avg_monthly = units in the look-back / MONTHS THAT MODEL WAS SELLING
                projected   = avg_monthly x the planning months       (current models only)
    the order:  devices     = Σ projected over the ruled-current models
                qty(item)   = round(devices x the item's per-device rate)
                cases       = split across the declared colour groups by largest remainder

**THE DIVISOR IS THE MONTHS THE MODEL WAS SELLING, NOT THE WHOLE LOOK-BACK.** A handset that first
sold last month has not been selling for three; dividing by three understates its order by two
thirds, which is the quiet way a new best-seller gets under-ordered. The row says which it was
(`history_basis`).

HONESTY RULES (each is a test in `backend/tests/unit/test_accessory_order_plan.py` and a check in
`backend/harness_accessory_order_plan.py`):
  · **An unruled model is not a zero, and not an average either.** No ruling → `projected_units`
    is `None` and the row reads `awaiting_declaration`. A zero would read as a decision to retire the
    model; the average would be the report ruling on the user's behalf. The payload reports how much
    measured demand is still waiting (`units_awaiting_declaration`, `declared_share`).
  · **A retired model keeps its history.** Its projection is zero because that is the DECISION, but
    its units stay on the row — dropping it would make the retired demand invisible, which is how a
    store runs out of a case it is still selling.
  · **Every model retired is a real order of nothing**, and the payload distinguishes that from
    having no rulings at all (`reason`).
  · **A colour mix that does not add up is normalised and SAID.** 60/30 is 90% of the cases: the
    missing tenth is a typo, not a colour.
  · **The colour quantities foot to the case quantity.** Largest remainder, the same rounding rule
    §61 uses for the accessory target split and for the same reason.
  · **No projection means no order quantity, never zero.**
  · **"1 per sale" is a default, not a law.** A tenant that puts two protectors in a bundle declares
    two.

RULE TWO. The colour GROUPS are generic keys with declared labels, the accessory keys name a kind of
thing rather than a product, and the whole vocabulary is config with house defaults. No carrier,
tenant or brand name appears in this file, and a test asserts it.

NOT MONEY. This plans quantities to buy. It reads no payout, writes no ledger and cannot move a
commission.

PURE. Every input is handed in, so `backend/harness_accessory_order_plan.py` drives the whole module
DB-free under `env -i`.
"""
from __future__ import annotations

from app.modules.commcalc import demand_projection as _dp

# ── THE LIFECYCLE RULING (the human step) ────────────────────────────────────────────────────────
#: What a human can say about a model. `undeclared` is not a choice anybody makes — it is the state of
#: a model nobody has ruled on yet, and it is named so the report can report it rather than guess.
LIFECYCLE_CURRENT = "current"
LIFECYCLE_EOL = "eol"
LIFECYCLE_UNDECLARED = "undeclared"
LIFECYCLE_STATES = (LIFECYCLE_CURRENT, LIFECYCLE_EOL, LIFECYCLE_UNDECLARED)
LIFECYCLE_LABELS = {
    LIFECYCLE_CURRENT: "Current — keep ordering accessories for it",
    LIFECYCLE_EOL: "End of life — do not order for it",
    LIFECYCLE_UNDECLARED: "Not yet ruled on",
}
#: The rulings a human may SEND. `undeclared` is a state, never an instruction, so a caller clearing a
#: ruling deletes it rather than declaring the absence.
DECLARABLE_STATES = (LIFECYCLE_CURRENT, LIFECYCLE_EOL)

#: Why a model's projection reads the way it does.
PROJECTION_BASES = ("measured", "end_of_life", "awaiting_declaration", "no_window")
#: Whether the average rests on the whole look-back or only part of it.
HISTORY_BASES = ("full_history", "partial_history")
#: Where the colour mix in force came from.
MIX_BASES = ("house_default", "declared", "declared_normalised")

# ── THE HOUSE COLOUR MIX (the owner's own figures and his own words) ─────────────────────────────
# A colour GROUP, not a colour: a case order is placed in families, and "blacks and blues" is one
# family. The keys are generic so a tenant can relabel without a code change.
HOUSE_CASE_MIX = (
    {"key": "dark", "label": "Blacks and blues", "share": 0.70},
    {"key": "bright", "label": "Pinks and reds", "share": 0.30},
)

# ── THE HOUSE ATTACH PLAN (how many of each per device sold) ─────────────────────────────────────
# `colour_split` is the one structural difference between the items: a case is ordered in colour
# families and a charging cube is not.
HOUSE_ATTACH_PLAN = (
    {"key": "case", "label": "Case", "per_device": 1.0, "colour_split": True},
    {"key": "screen_protector", "label": "Screen protector", "per_device": 1.0, "colour_split": False},
    {"key": "cable_c_to_c", "label": "Cable (USB-C to USB-C)", "per_device": 1.0, "colour_split": False},
    {"key": "charging_cube", "label": "Charging cube", "per_device": 1.0, "colour_split": False},
)
_HOUSE_SPLIT_KEYS = frozenset(i["key"] for i in HOUSE_ATTACH_PLAN if i["colour_split"])

#: The owner's own defaults for the two window controls, so a first load is already a useful answer.
DEFAULT_LOOKBACK_MONTHS = 3
DEFAULT_HORIZON_MONTHS = 2
#: A planning window is a human's horizon, not a query parameter to be abused. Both ends are clamped.
MAX_LOOKBACK_MONTHS = 24
MAX_HORIZON_MONTHS = 12


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def clamp_months(n, default, maximum):
    """A month count from a query string → a usable integer inside the house bounds. PURE."""
    try:
        v = int(round(_num(n, default)))
    except (TypeError, ValueError):
        v = default
    if v < 1:
        v = default
    return max(1, min(v, maximum))


# ── THE DECLARATIONS ─────────────────────────────────────────────────────────────────────────────
def resolve_case_mix(raw=None):
    """A tenant's declared colour mix → `(groups, basis)`, shares summing to 1.0. PURE.

    Shares may be declared as percentages (70) or as fractions (0.7); which was meant is decided by
    the TOTAL, not per row, so a mix of 0.7 / 0.3 and one of 70 / 30 cannot be read differently. A
    total that is not 100% is normalised and the basis says so — the missing share is a typo, not a
    colour nobody named. Missing / junk / no usable row → the house mix."""
    rows = []
    if raw is not None and not isinstance(raw, (str, bytes)) and hasattr(raw, "__iter__"):
        for r in raw:
            if not isinstance(r, dict):
                continue
            key = str(r.get("key") or "").strip().lower()
            share = _num(r.get("share"), 0.0)
            if not key or share <= 0:
                continue
            rows.append({"key": key, "label": str(r.get("label") or key).strip(), "share": share})
    if not rows:
        return tuple(dict(m) for m in HOUSE_CASE_MIX), "house_default"
    total = sum(r["share"] for r in rows)
    # Percentages or fractions, decided once over the whole declaration.
    if total > 1.5:
        total /= 100.0
        for r in rows:
            r["share"] /= 100.0
    basis = "declared" if abs(total - 1.0) <= 0.005 else "declared_normalised"
    for r in rows:
        r["share"] = r["share"] / total if total > 0 else 0.0
    return tuple(rows), basis


def resolve_attach_plan(raw=None):
    """A tenant's declared attach plan → an ordered tuple of items. PURE.

    Missing / junk / no usable row → the house plan (one of each per device). A declared row keeps the
    house answer for `colour_split` when it does not state one, so relabelling an item cannot silently
    stop its order being split by colour."""
    if raw is None or isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        return tuple(dict(i) for i in HOUSE_ATTACH_PLAN)
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        key = str(r.get("key") or "").strip().lower()
        per = _num(r.get("per_device"), 0.0)
        if not key or per <= 0:
            continue
        split = r.get("colour_split")
        out.append({"key": key, "label": str(r.get("label") or key).strip(),
                    "per_device": per,
                    "colour_split": bool(split) if split is not None else key in _HOUSE_SPLIT_KEYS})
    return tuple(out) if out else tuple(dict(i) for i in HOUSE_ATTACH_PLAN)


def resolve_lifecycle(raw=None) -> dict:
    """A declared lifecycle map → `{model: state}` with only the states a human may send. PURE."""
    out = {}
    if isinstance(raw, dict):
        for model, state in raw.items():
            m = str(model or "").strip()
            s = str(state or "").strip().lower()
            if m and s in DECLARABLE_STATES:
                out[m] = s
    return out


# ── THE MODEL TABLE ──────────────────────────────────────────────────────────────────────────────
def model_rows(units_by_model, *, months, lifecycle=None, horizon_months=DEFAULT_HORIZON_MONTHS):
    """Per device model: the look-back months in their own columns, the monthly average, its share of
    the window's demand, the ruling in force and the units projected over the planning months.

    `units_by_model` is `{model: {period: units}}` and `months` is the look-back in ASCENDING order —
    both handed in, so this function reads no table. Best seller first. PURE."""
    periods = [str(p) for p in (months or ())]
    rule = resolve_lifecycle(lifecycle)
    horizon = max(0, int(round(_num(horizon_months, 0))))

    prepared = []
    for model, by_period in (units_by_model or {}).items():
        name = str(model or "").strip()
        if not name:
            continue
        counts = [int(round(_num((by_period or {}).get(p), 0.0))) for p in periods]
        total = sum(counts)
        if total <= 0:
            continue
        # The months the model was SELLING: from the first month it sold anything to the end of the
        # window. A gap in the middle is a month it was on sale and sold none, which is a real zero.
        first = next(i for i, c in enumerate(counts) if c > 0)
        observed = len(periods) - first
        prepared.append((name, counts, total, observed))

    window_total = sum(p[2] for p in prepared)
    rows = []
    for name, counts, total, observed in prepared:
        state = rule.get(name, LIFECYCLE_UNDECLARED)
        proj = _dp.projection(total, observed, horizon)
        if state == LIFECYCLE_UNDECLARED:
            projected, basis = None, "awaiting_declaration"
        elif state == LIFECYCLE_EOL:
            projected, basis = 0, "end_of_life"
        else:
            projected, basis = proj["projected"], proj["basis"]
        rows.append({
            "model": name,
            "units_by_month": [{"period": p, "units": c} for p, c in zip(periods, counts)],
            "units_total": total,
            "months_observed": observed,
            "avg_monthly": round(proj["velocity"], 2) if proj["velocity"] is not None else None,
            "share": (total / window_total) if window_total else 0.0,
            "lifecycle": state,
            "lifecycle_label": LIFECYCLE_LABELS[state],
            "history_basis": "full_history" if observed == len(periods) else "partial_history",
            "projected_units": projected,
            "projection_basis": basis,
        })
    rows.sort(key=lambda r: (-r["units_total"], r["model"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


# ── THE ORDER ────────────────────────────────────────────────────────────────────────────────────
def _largest_remainder(total, shares):
    """`total` whole units split by `shares` so the parts SUM TO `total`. PURE.

    Proportional shares rounded independently miss by units. The floor of each exact share is taken
    first and the residue handed to the largest fractional remainders, which is the same rule §61 uses
    for the accessory target split."""
    if not shares:
        return []
    exact = [total * s for s in shares]
    base = [int(e) for e in exact]
    residue = total - sum(base)
    order = sorted(range(len(shares)), key=lambda i: (-(exact[i] - base[i]), -shares[i], i))
    for i in range(residue):
        base[order[i % len(order)]] += 1
    return base


def accessory_rows(projected_devices, *, mix, plan):
    """The order lines: one row per accessory, the case row carrying its colour split. PURE.

    `projected_devices is None` means the demand could not be planned, and every quantity comes back
    `None` — a zero would be an order of nothing presented as a decision."""
    rows = []
    for item in (plan or ()):
        per = _num(item.get("per_device"), 0.0)
        qty = None if projected_devices is None else int(round(_num(projected_devices) * per))
        row = {"key": item.get("key"), "label": item.get("label"),
               "per_device": per, "qty": qty, "colour_split": bool(item.get("colour_split"))}
        if row["colour_split"]:
            groups = list(mix or ())
            if qty is None:
                row["colours"] = [{"key": g.get("key"), "label": g.get("label"),
                                   "share": g.get("share"), "qty": None} for g in groups]
            else:
                parts = _largest_remainder(qty, [_num(g.get("share"), 0.0) for g in groups])
                row["colours"] = [{"key": g.get("key"), "label": g.get("label"),
                                   "share": g.get("share"), "qty": q}
                                  for g, q in zip(groups, parts)]
        rows.append(row)
    return rows


# ── THE WHOLE PLAN ───────────────────────────────────────────────────────────────────────────────
def plan(*, units_by_model, months, lifecycle=None, horizon_months=DEFAULT_HORIZON_MONTHS,
         case_mix=None, attach_plan=None):
    """The payload the screen reads: the ranked model table, what is still waiting on a ruling, and
    the order the rulings imply. PURE — every input is handed in."""
    periods = [str(p) for p in (months or ())]
    horizon = max(0, int(round(_num(horizon_months, 0))))
    mix, mix_basis = resolve_case_mix(case_mix)
    items = resolve_attach_plan(attach_plan)
    rows = model_rows(units_by_model, months=periods, lifecycle=lifecycle, horizon_months=horizon)

    window_units = sum(r["units_total"] for r in rows)
    declared = [r for r in rows if r["lifecycle"] != LIFECYCLE_UNDECLARED]
    waiting = [r for r in rows if r["lifecycle"] == LIFECYCLE_UNDECLARED]
    declared_units = sum(r["units_total"] for r in declared)
    waiting_units = sum(r["units_total"] for r in waiting)

    # NOTHING RULED ON AT ALL is silence, and silence is not an order of zero. One ruling is enough to
    # make a plan — the rest is reported as waiting rather than assumed either way.
    if rows and not declared:
        devices, reason = None, "awaiting_declaration"
    else:
        devices = sum(int(r["projected_units"] or 0) for r in declared)
        reason = None
    if not rows:
        devices, reason = None, "no_sales_in_window"

    return {
        "months": periods,
        "lookback_months": len(periods),
        "horizon_months": horizon,
        "models": rows,
        "models_awaiting_declaration": sorted(r["model"] for r in waiting),
        "units_awaiting_declaration": waiting_units,
        "units_in_window": window_units,
        "units_declared": declared_units,
        "declared_share": (declared_units / window_units) if window_units else 0.0,
        "projected_devices": devices,
        # DERIVED FROM THE TOTAL so the headline rate and the order can never disagree.
        "projected_devices_per_month": (None if devices is None or horizon <= 0
                                        else round(devices / horizon, 2)),
        "accessories": accessory_rows(devices, mix=mix, plan=items),
        "case_mix": [dict(m) for m in mix],
        "case_mix_basis": mix_basis,
        "attach_plan": [dict(i) for i in items],
        "lifecycle_states": list(DECLARABLE_STATES),
        "lifecycle_labels": dict(LIFECYCLE_LABELS),
        "reason": reason,
    }
