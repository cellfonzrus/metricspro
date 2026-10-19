"""Device / handset COGS — INVOICE-FIRST recognition (owner policy 2026-07-30 §9, ruling K3 2026-08-10).

WHY THIS MODULE EXISTS
----------------------
`coa.build_inputs` derived device cost from the POS only, as sale-line `ext_price − gp`. On a
subsidised handset that is not merely imprecise, it is the wrong sign: measured on luxelink July 2026,
`raw_sales` dept `BrandedHandset` / cat `KittedBranded` gives ext 23,289.18 − gp 25,625.51 =
**−$2,336.33**, because B2B Soft records the POST-SUBSIDY cost. So the phones a dealer buys were
absent from the books entirely (luxelink July `device_cost` = $234.00, all of it SIM kits) while the
distributor's rebate sat in income with nothing to offset.

The policy of record already resolved how to fix it and the flip was HELD as "Option C". Owner ruling
K3 released it. This module is that flip, isolated so the recognition rule is testable on its own.

THE RULE (policy §9 C1/C2/C3)
----------------------------
1. **Invoice-first.** Cost comes from the distributor invoice whenever one is in the system.
2. **Sale-time fallback.** POS `ext − gp` ONLY when no invoice covers the period.
3. **Dedup by IMEI.** One physical handset is charged to COGS exactly once.
4. **Consignment (VIP).** The amount VIP billed IS the consignment COGS; `owed_to_vip` stays a
   liability until the unit sells (that treatment is untouched here).
5. **Periodic inventory.** Period-end unsold units are a balance-sheet asset, not COGS.

CARRIER-AGNOSTIC BY DATA, NEVER BY TENANT NAME
---------------------------------------------
Same discipline as `coa`'s residual fallback: each source is tried, and whichever the org actually HAS
is the one that answers. A VidaPay tenant has `raw_ma_*`; a VIP/Boost tenant has
`asset_ledger`/`vip_invoices`. No branch anywhere reads a tenant name, and the whole module is gated by
`account_config.device_cogs_mode` (mig 621) which DEFAULTS TO 'off' — so every org is byte-identical
until a tenant is explicitly switched on.

⚠️ THE UN-LINKABLE REMAINDER IS COUNTED, NEVER ASSUMED ZERO
-----------------------------------------------------------
Policy §9 C1 carries an explicit caveat: the owner's "IMEI means never duplicate" premise fails for
rows that cannot be linked. `raw_ma_fulfillment` has **no IMEI column** — it prices by product name and
reaches an IMEI only through an activation. So every return carries `meta` reporting exactly what could
NOT be priced. luxelink July 2026, measured: 787 activation rows → **746 distinct IMEIs** (41 duplicate
rows dropped — verified same SKU, same date, i.e. a line/AAL pair that would otherwise charge one
handset twice) → **599 priced = $137,185.10**, with **147 IMEIs unpriced** (mostly
`sku='Product Not Available'`, which are SIM-only/BYOD activations carrying $0 rebate, not handsets).

📌 $137,185.10 is the IMEI-DEDUPED figure and is the policy-correct one. The $142,033.93 quoted in the
formula book §C was measured WITHOUT dedup (all 787 rows) and is $4,848.83 too high. Dedup is a policy
requirement, so the lower figure governs; see the handoff for the net-income consequence.

GRAIN — READ THIS BEFORE TRUSTING A PER-STORE DEVICE MARGIN
-----------------------------------------------------------
`raw_ma_commission` carries `merchant_account_id`, **not a store address**. MA device COGS was
therefore booked **company-wide**, exactly like PayGo and the MA residual — resolving an account id
as a store by GUESSING is the phantom-store failure PayGo taught us. The condition this paragraph
set ("until account_id → store_address is mapped") is now MET where the data provides it: the
dealer's own `raw_ma_fulfillment` sheet carries both `tspid` (the account) and `business_address`
on every order row, and mig 314 adds an owner-pinned override table
(`ma_account_store_map`). When the caller passes that index (`ma_acct_index`, built by
`ma_store_pnl.load_store_index` and gated by `commission_org_config.pl_ma_store_attribution`),
MA device cost books per store; any account the index cannot name STAYS company-wide — mapped
beats guessed, honest beats mis-attributed. No index passed ⇒ company-wide, byte-identical to
the pre-314 behaviour. (Owner spec 2026-09-02: "rebates and phone cost are not being captured
per store".)
"""
import logging

from app.modules.commcalc.calculator import safe_float
# The SAME literal honest-absence marker the MA daily-tx coverage read-out uses (mig 312 /
# §COMMCALC-11), for `ma_fulfillment_coverage` below. Dereferenced, never copied: one fact, one
# home (owner directive 2026-09-20).
from app.modules.account.ma_store_pnl import NO_RULE_REASON as _NO_RULE_REASON

_log = logging.getLogger(__name__)

# ── WHICH FULFILLMENT ROWS PRICE A HANDSET — THE ONE TEST (owner report 2026-10-19) ─────────────
# Owner, on LuxeLink September 2026: *"how come the device cost is less than the device rebate"* —
# Device cost $164,730.35 against Device purchase rebates −$180,191.91 on the Chicago company.
#
# THE CLASS, named rather than the instance. A handset's REBATE books off `raw_ma_commission`, one
# row at a time, unconditionally. Its COST books only when the same handset's `sku` can be priced
# off `raw_ma_fulfillment` — and an order-type ALLOWLIST decided, silently, which fulfillment rows
# were allowed to carry a price at all. A vendor order-type family nobody had added to it priced
# NOTHING, so every handset bought under it booked a rebate against a cost of zero and the
# contra-COGS overtook the COGS it was meant to net against. Measured on LuxeLink September 2026:
# 71 of the 77 unpriced handsets were certified-pre-owned iPhones bought under a second order-type
# family the distributor opened after the allowlist was written — $26,299.29 of real cost dropped,
# against $27,449.27 of rebate that booked regardless.
#
# So the NAME LIST IS NOT THE MECHANISM ANY MORE. A fulfillment row prices a handset when the two
# feeds AGREE that it is one: its `product_name` is a `sku` that a handset actually ACTIVATED under
# on `raw_ma_commission`. That is evidence, not vocabulary — it carries no distributor, carrier or
# tenant name (RULE TWO), it cannot drop a price the cost side would have used (a sku that is not
# activated is never looked up), and the day the distributor opens a third family it prices itself.
# Verified on LuxeLink's whole fulfillment sheet, Jun–Oct 2026: the derived test reproduces the
# allowlist exactly where the allowlist was right (917 'Branded Handset' rows: 24 of 24 product
# names matched) and supplies the family it had missed (108 rows: 8 of 8 matched), while the SIM
# kits and the charge cables match nothing and stay out, as they always did.
#
# The allowlist stays as the FALLBACK, byte-identical, for the one caller that prices without a
# commission feed to take evidence from. Evidence only ever ADDS.
_MA_DEVICE_ORDER_TYPES = {"branded handset"}


def ma_device_product_names(comm_rows):
    """PURE: `raw_ma_commission` rows -> the lowercased set of `sku`s handsets actually activated
    under. This is the evidence `ma_unit_price_from_rows` prices on; `_MA_UNKNOWN_SKU` (the
    distributor's own "we could not name it", which is a SIM-only/BYOD line and never a handset)
    is excluded, so it can never licence a price."""
    out = set()
    for r in comm_rows or []:
        sku = str((r or {}).get("sku") or "").strip().lower()
        if sku and sku != _MA_UNKNOWN_SKU:
            out.add(sku)
    return out


def ma_fulfillment_coverage(rows, device_names=None, reasons=None):
    """PURE: `raw_ma_fulfillment` rows -> every order-type family, named, with what it did.

    The fulfillment-side twin of `ma_store_pnl.ma_tx_coverage` (§COMMCALC-11), and a COVERAGE
    READ-OUT only: it re-runs the very same test `ma_unit_price_from_rows` runs and reports the
    result per family. It prices nothing and books nothing.

    `reasons` is the org's own {order_type: stated reason} — the SAME config the daily-tx coverage
    reads (`ma_store_pnl.load_unbooked_reasons`), dereferenced rather than copied — for the
    families that are deliberately not handsets. A family that prices nothing and carries no stated
    reason is the honest-absence case: it is REPORTED, with `NO_RULE_REASON`, never papered over.

    Returns {"families": [ {order_type, rows, qty, ext, device, reason}, ... ] (|ext| desc),
             "device_ext": float, "unruled_ext": float, "unruled": [order_type, ...]}
    """
    names = {str(n).strip().lower() for n in (device_names or ())}
    reasons = {str(k).strip().lower(): str(v) for k, v in (reasons or {}).items()}
    fam = {}
    for r in rows or []:
        r = r or {}
        ot = str(r.get("order_type") or "").strip()
        n = safe_float(r.get("number_ordered")) or 0.0
        p = safe_float(r.get("price")) or 0.0
        slot = fam.setdefault(ot.lower(), {"order_type": ot or "(blank)", "rows": 0, "qty": 0.0,
                                           "ext": 0.0, "priced": 0})
        slot["rows"] += 1
        slot["qty"] += n
        slot["ext"] += n * p
        if _ma_row_prices_a_device(r, names):
            slot["priced"] += 1
    out, unruled = [], []
    for low, slot in fam.items():
        slot["qty"], slot["ext"] = round(slot["qty"], 2), round(slot["ext"], 2)
        slot["device"] = bool(slot.pop("priced"))
        if slot["device"]:
            slot["reason"] = "handsets — priced into device cost"
        elif low in reasons:
            slot["reason"] = reasons[low]
        else:
            slot["reason"] = _NO_RULE_REASON
            unruled.append(slot["order_type"])
        out.append(slot)
    out.sort(key=lambda s: (-abs(s["ext"]), s["order_type"]))
    return {"families": out,
            "device_ext": round(sum(s["ext"] for s in out if s["device"]), 2),
            "unruled_ext": round(sum(s["ext"] for s in out
                                     if s["reason"] == _NO_RULE_REASON), 2),
            "unruled": sorted(unruled)}


def _ma_row_prices_a_device(r, device_names):
    """PURE: does this fulfillment row carry a handset's price? Evidence first (the two feeds agree
    the product is something a handset activated under), the order-type allowlist as the fallback
    for a caller with no commission feed to take evidence from. ONE test, two readers."""
    name = str((r or {}).get("product_name") or "").strip().lower()
    if not name:
        return False
    if device_names:
        return name in device_names
    return str((r or {}).get("order_type") or "").strip().lower() in _MA_DEVICE_ORDER_TYPES


# A `sku` the distributor could not name. These rows are SIM-only / BYOD activations, not handsets:
# on luxelink July all 160 carry $0 rebate. Excluded from the device count AND reported in `meta`.
_MA_UNKNOWN_SKU = "product not available"

# Fee-only categories on the consignment/asset ledger. These are already booked on `vip_fees` by
# `coa` and are NOT device cost. ONE HOME, dereferenced (owner directive 2026-09-20): the ASSET side
# of the same ledger (`balance_sheet.asset_ledger_unsold_cells` / `asset_ledger_purchases`) must take
# its population over EXACTLY this vocabulary, or the asset and the expense are two readings of two
# populations and the periodic-inventory identity stops being an identity. A second literal copy of
# this tuple anywhere in the account module is a build failure
# (`harness_device_inventory_cogs.py` §G).
FEE_CATEGORIES = ("PROCESSING FEE", "SHIPPING", "SIM KIT")

# ── THE ONE DEVICE-COST BASIS (owner report 2026-10-03, mig 1041, index §42.5) ──────────────────
# Owner, verbatim: *"these are not the right numbers, these numbers come from teh asset landing -
# first check how other stores are getting thier numbers and then fix platform wide - no bandaid"*
#
# ONE question — "on what basis does this org expense a handset?" — so ONE declaration per org and
# ONE resolution of it, of exactly the shape and precedence `distributor_payable_basis` already uses
# (mig 954): ORG OVERRIDE > CARRIER PRESET > the legacy floor. Never a carrier or tenant branch.
#
#   'pos'           sale-time point-of-sale cost (`ext − gp`). What every org books today.
#   'asset_ledger'  the ACCRUAL basis the owner asked for: the distributor's own per-unit charge off
#                   the asset-landing ledger, recognised in the month the unit SOLD, with the unsold
#                   units carried as a balance-sheet asset derived from the SAME ledger.
#
# NULL/'' is NOT a third basis — it is "not declared", and it resolves to `undeclared`, which
# reproduces today's behaviour for that org byte for byte, including the known double-book it leaves
# in place. That is what makes this change inert until the owner declares a basis per org.
DEVICE_COST_BASES = ("pos", "asset_ledger")
BASIS_UNDECLARED = "undeclared"


def resolve_device_cost_basis(org_basis, carrier_preset_basis=None, legacy_cogs_mode=None):
    """PURE. The WHOLE device-cost decision, as one dict, from one declaration.

    `org_basis`            the org's own `account_config.device_cost_basis` (mig 1041). None/'' =
                           NOT DECLARED (fall through), exactly like mig 954.
    `carrier_preset_basis` the basis the org's CARRIER declares, read through the ONE carrier-preset
                           store (`statement_engine.carrier_finance_preset`, key 'device_cost') — the
                           same lazy auto-assign mig 945/953/954 use, so a new tenant that picks its
                           carrier at onboarding is correct with no setup hook.
    `legacy_cogs_mode`     the org's pre-existing `account_config.device_cogs_mode` (mig 621). Read
                           ONLY here, so no caller can branch on it and reach a different answer.

    WHY ONE DICT AND NOT A FLAG. The accrual basis has three inseparable consequences, and shipping
    them as three config rows is how the platform would acquire a half-switched org: a tenant on
    accrual COGS whose cash line still books double-expenses every handset (index §42.1, ~$400k a
    month on the house org), and a tenant on accrual COGS without the asset side relieves an
    inventory nothing ever booked (§42.3). So the resolution returns all three together and
    `harness_device_inventory_cogs.py` §G fails the build if they can ever disagree:

      accrual              → recognise the distributor's per-unit charge at `date_sold`
      suppress_cash_cogs   → `vip_device_pay` (the PayGo cash settlement) leaves COGS. It is not
                             lost: the liability it settles is already carried on `owed_vip`, so
                             cash paid against a booked payable is a settlement, not an expense.
      ledger_inventory     → the BS `inventory` line is the as-of unsold value of the SAME ledger,
                             overriding `inventory_basis`, so the unit leaves inventory in the same
                             month and at the same cost basis it enters COGS.

    `cogs_mode` is what `device_cogs.resolve` is handed. On the accrual basis it is 'invoice', never
    'auto': when the landing report has not swept (live, 2026-10-03: the ledger's newest
    `acquired_date`/`date_sold` are both September) the month must render a DECLARED zero through the
    existing ruling-K3(b) `honest_zero` → `L['device_cost']['note']` passthrough, never a POS figure
    silently substituted for a stale feed and never a measured-looking $0.

    NEVER RAISES. Returns the undeclared resolution for anything it cannot read."""
    def _norm(v):
        v = str(v or "").strip().lower()
        return v if v in DEVICE_COST_BASES else ""

    basis, source = _norm(org_basis), "org override"
    if not basis:
        basis, source = _norm(carrier_preset_basis), "carrier preset"
    if not basis:
        legacy = str(legacy_cogs_mode or "off").strip().lower()
        if legacy not in ("off", "pos", "auto", "invoice"):
            legacy = "off"
        return {
            "basis": BASIS_UNDECLARED, "source": "legacy device_cogs_mode (no basis declared)",
            "cogs_mode": legacy, "accrual": False,
            "suppress_cash_cogs": False, "ledger_inventory": False,
            "legacy_cogs_mode": legacy,
            # An org left undeclared on 'auto'/'invoice' recognises the distributor's charge AND
            # still books the cash settlement in COGS. REPORTED on every statement rather than
            # silently corrected, because correcting it unasked would restate that org's books.
            "double_book_risk": legacy in ("auto", "invoice"),
        }
    accrual = basis == "asset_ledger"
    return {
        "basis": basis, "source": source,
        "cogs_mode": "invoice" if accrual else "off",
        "accrual": accrual,
        "suppress_cash_cogs": accrual,
        "ledger_inventory": accrual,
        "legacy_cogs_mode": str(legacy_cogs_mode or "").strip().lower() or None,
        "double_book_risk": False,
    }


# ── WHICH STORE CARRIES A HANDSET'S COST (owner directive 2026-10-10, index §70) ─────────────────
# OWNER, on the decision card: *"Move the cost"* — move device cost to the store the carrier paid,
# for the proven transfers.
#
# THE CLASS, named before the fix. "Which store does this handset's cost belong to?" had TWO
# authorities and nothing reconciled them per device: the distributor ledger books the cost to the
# STOCKING store (`asset_ledger.store`, the row this module reads), and the carrier statement pays
# the ACTIVATING store. A phone stocked at one door and sold at another therefore charged one store
# a cost whose reimbursement the carrier paid another — measured live (house org, Jun-Oct 2026): 468
# devices and $195,155.32 of cost, nets to $0.00 company-wide and restates per-store gross profit.
#
# ONE FACT, ONE HOME, DEREFERENCED. "Where was this device activated" is NOT answered here. It is
# §65's per-device verdict (`commcalc/device_reimb_recon.device_lines` -> `.activation_store_moves`),
# the same reconciliation the owner reads on the report, and this module takes its answer as DATA.
# A second opinion here — a join of its own, a store guess from the sale row — is the duplicate
# defect the index rules forbid, and it would let the report and the books disagree about where one
# phone was sold. `cost_store` below is the ONLY place a caller may turn that answer into a store.
#
# DECLARED, NEVER INFERRED, and OFF until declared: `account_config.device_cost_store_basis`
# (migration 1069). NULL/'' is not a third basis, it is "not declared", and it resolves to
# `stocking`, which is byte-identical to every figure this module produced before today.
DEVICE_COST_STORE_BASES = ("stocking", "activating")
STORE_BASIS_STOCKING = "stocking"
STORE_BASIS_ACTIVATING = "activating"
#: Why a device's cost sits where it does. Carried on every placement so a reader never has to guess
#: whether a store's cost moved, stayed, or could not be judged.
PLACED_LEDGER = "distributor_ledger_store"
PLACED_ACTIVATION = "carrier_paid_store"
PLACED_WITHHELD = "unmeasured_stays_put"


def resolve_device_cost_store_basis(org_value):
    """PURE. The whole "which store" decision, from one declaration. NEVER RAISES.

    `org_value` the org's own `account_config.device_cost_store_basis`. None/'' = NOT DECLARED.

    Returns `{"basis", "move", "source"}`. `move` is the single boolean every caller reads — kept
    with the basis rather than derived beside it, the same reason `resolve_device_cost_basis` returns
    its three consequences together: a caller that recomputes "should I move?" from the basis string
    is a second copy of this rule and will drift from it."""
    v = str(org_value or "").strip().lower()
    if v not in DEVICE_COST_STORE_BASES:
        return {"basis": STORE_BASIS_STOCKING, "move": False,
                "source": "house default (no basis declared)"}
    return {"basis": v, "move": v == STORE_BASIS_ACTIVATING, "source": "org declaration"}


def load_device_cost_store_basis(client, org_id):
    """The one READ of the one declaration. Each column its own defensive read (the mig-954 pattern):
    a pre-1069 database has no `device_cost_store_basis` column, every org stays undeclared, and
    every figure is byte-identical. NEVER raises."""
    raw = None
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("device_cost_store_basis").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            raw = rows[0].get("device_cost_store_basis")
    except Exception:
        raw = None
    return resolve_device_cost_store_basis(raw)


def cost_store(ledger_store, device_id, activation):
    """PURE. THE ONE PLACE a device's cost is given a store. NEVER RAISES.

    `ledger_store`  the store the distributor ledger books this unit to (already resolved).
    `device_id`     the unit's identifier on that same row; falsy means no move is possible.
    `activation`    what `load_activation_index` returned — `{"move": bool, "move_to": {...},
                    "withheld": {...}}`, whose `move_to` is §65's own per-device verdict.

    Returns `{"store", "basis", "moved_from", "withheld"}`. `basis` names WHY, from the three
    PLACED_* reasons, so a caller can report the placement instead of inferring it. On the stocking
    basis, on a device §65 proved nothing about, and on any unreadable input, the answer is the ledger
    store — the figure this module has always produced. A cost is never left unplaced."""
    st = (str(ledger_store).strip() if ledger_store is not None else "") or None
    out = {"store": st, "basis": PLACED_LEDGER, "moved_from": None, "withheld": False}
    a = activation if isinstance(activation, dict) else {}
    if not a.get("move") or not st:
        return out
    dev = str(device_id or "").strip()
    if not dev:
        return out
    if dev in (a.get("withheld") or {}):
        out["basis"], out["withheld"] = PLACED_WITHHELD, True
        return out
    mv = (a.get("move_to") or {}).get(dev)
    to = str((mv or {}).get("to") or "").strip()
    if not to or to == st:
        return out
    return {"store": to, "basis": PLACED_ACTIVATION, "moved_from": st, "withheld": False}


def load_basis(client, org_id, carrier_preset=None):
    """The one READ of the one declaration. Every caller (coa.build_inputs, statement_engine,
    account/router) dereferences THIS — nothing else reads `device_cost_basis` or branches on
    `device_cogs_mode` (§G locks that). Each column is its own defensive read, the mig-954 pattern:
    a pre-1041 database simply has no `device_cost_basis` column and every org stays undeclared,
    i.e. byte-identical. NEVER raises."""
    org_basis, legacy = None, "off"
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("device_cost_basis").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            org_basis = rows[0].get("device_cost_basis")
    except Exception:
        org_basis = None
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("device_cogs_mode").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and str(rows[0].get("device_cogs_mode") or "").strip():
            legacy = str(rows[0]["device_cogs_mode"]).strip().lower()
    except Exception:
        legacy = "off"
    if carrier_preset is None:
        try:
            from app.modules.account import statement_engine as _se
            carrier_preset = _se.carrier_finance_preset(client, org_id, "device_cost")
        except Exception:
            carrier_preset = ""
    return resolve_device_cost_basis(org_basis, carrier_preset, legacy)


def load_activation_index(client, org_id, period, basis=None):
    """The one READ of "where did the carrier activate each device this period".

    I/O ONLY. It dereferences §65's own assembly (`commcalc.router._device_line_reimb_run`, the one
    place that reads the carrier's per-line feed over the reimbursement lag window and reconciles it
    against the distributor ledger per device) and §65's own pure verdict reader
    (`device_reimb_recon.activation_store_moves`). It reconciles nothing itself and reads neither
    feed directly — a second assembly here would be two paths answering one question.

    Returns `{"move", "move_to", "withheld", "totals", "basis", "reason"}` — the shape
    `cost_store` consumes. NEVER RAISES, and it fails in the SAFE direction: on the stocking basis,
    or when the reconciliation cannot be read, `move` is False with the reason recorded, so every
    cost books exactly where it books today rather than half-moving on a partial answer."""
    res = basis if isinstance(basis, dict) else load_device_cost_store_basis(client, org_id)
    out = {"move": False, "move_to": {}, "withheld": {}, "totals": {},
           "basis": res.get("basis"), "basis_source": res.get("source"), "reason": ""}
    if not res.get("move"):
        out["reason"] = ("device cost books to the distributor ledger's store — no activating-store "
                         "basis is declared for this org")
        return out
    if not str(period or "").strip():
        out["reason"] = "no period to reconcile — cost books to the distributor ledger's store"
        return out
    try:
        from app.modules.commcalc import router as _crouter
        from app.modules.commcalc import device_reimb_recon as _drr
        run = _crouter._device_line_reimb_run(client, org_id, period)
        if not (run or {}).get("configured"):
            out["reason"] = ("the org declares no device-financing routing, so no device activation "
                             "is proven — cost books to the distributor ledger's store")
            return out
        moves = _drr.activation_store_moves((run or {}).get("rows") or [])
    except Exception as e:
        _log.warning("device_cogs: activation index unavailable — %s: %s", type(e).__name__, e)
        out["reason"] = ("the per-device reimbursement reconciliation could not be read (%s), so no "
                         "cost was moved this run — reported, never guessed" % type(e).__name__)
        return out
    out.update({"move": True, "move_to": moves["move_to"], "withheld": moves["withheld"],
                "totals": moves["totals"],
                "reason": ("device cost follows the store the carrier paid, for the %d device(s) the "
                           "per-device reconciliation proved; %d unmeasured device(s) worth %.2f stay "
                           "on the distributor ledger's store"
                           % (moves["totals"]["devices"], moves["totals"]["withheld_devices"],
                              moves["totals"]["withheld_cost"]))})
    return out


def _page(client, table, select, eqs=None, page=1000, cap=200000):
    """Paginated org-scoped select. Mirrors `coa._fetch_all` (supabase caps a query at 1000 rows).
    A list/tuple/set filter value becomes an IN (...) clause — used for the period-spelling duality."""
    out, start = [], 0
    while start < cap:
        q = client.schema("commcalc").table(table).select(select)
        for k, v in (eqs or {}).items():
            q = q.in_(k, list(v)) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def ma_unit_price_from_rows(rows, device_names=None):
    """PURE: handset fulfillment rows → {product_name(lower) -> unit cost}.

    FACTORED OUT of `ma_unit_price_map` below (2026-10-12, §COMMCALC-03) so the takeover evaluation
    prices a PROSPECT's handsets with this exact arithmetic instead of a second copy of it. The I/O
    wrapper is the only caller that reads a table; the rule lives here and is proved DB-free
    (`harness_prospect_eval.py` §D asserts the two agree over one fixture).

    `device_names` is the EVIDENCE a caller holds that a product is a handset — the skus its
    commission feed shows handsets activating under (`ma_device_product_names`). Passing it is how
    a second order-type family gets priced without anyone naming it in code (2026-10-19). Omitting
    it is byte-identical to the pre-2026-10-19 behaviour: the order-type allowlist decides.
    """
    qty, ext = {}, {}
    names = {str(n).strip().lower() for n in (device_names or ())}
    for r in rows or []:
        r = r or {}
        if not _ma_row_prices_a_device(r, names):
            continue
        name = (r.get("product_name") or "").strip().lower()
        if not name:
            continue
        n = safe_float(r.get("number_ordered")) or 0.0
        p = safe_float(r.get("price")) or 0.0
        if n <= 0 or p <= 0:
            continue
        qty[name] = qty.get(name, 0.0) + n
        ext[name] = ext.get(name, 0.0) + (p * n)
    return {k: round(ext[k] / qty[k], 2) for k in qty if qty[k]}


def ma_unit_price_map(client, org_id, device_names=None):
    """{product_name(lower) -> unit cost} from the distributor's handset fulfillment sheet.

    ALL dates, deliberately: a device ordered in June and activated in July must still be priced, and
    the sheet is a price list as much as a purchase log. Unit = Σ(price × qty) / Σ(qty), which is a
    weighted average when the same model was bought at two prices. Measured spot-checks on luxelink:
    iPhone 16e $599.99, Galaxy Tab A11+ $279.99, A16 $129.99, TCL Tab 10 $219.99 — and the VidaPay
    wallet charges (`raw_ma_daily_tx` 'Postpaid Branded MarketPlace') are at EXACTLY those prices, so
    the two independent sources agree and either could have sourced this.

    The arithmetic is `ma_unit_price_from_rows` above — this reads the rows and nothing else.
    """
    return ma_unit_price_from_rows(_page(
        client, "raw_ma_fulfillment",
        "product_name,number_ordered,price,order_type", {"org_id": org_id}), device_names)


def _ma_sold_cost(client, org_id, period_keys, ma_acct_index=None):
    """Cost of the devices ACTIVATED (= sold) in the period, from the distributor's own records.

    `raw_ma_commission` is one row per activated line and carries the `imei` and the `sku`. Dedup by
    IMEI first (a line/AAL pair repeats the same handset), then price each distinct IMEI off the
    fulfillment sheet. Grain: per store where `ma_acct_index` names the row's merchant account
    (mig 314 — see the module docstring), company-wide for everything else. No index ⇒ all
    company-wide, byte-identical to pre-314.
    """
    # The COMMISSION rows come first: they carry the evidence of which products are handsets at
    # all (`ma_device_product_names`), and without it a fulfillment family nobody named prices
    # nothing while its rebate books in full — the 2026-10-19 defect.
    rows = _page(client, "raw_ma_commission", "imei,sku,activation_type,merchant_account_id",
                 {"org_id": org_id, "period": period_keys})
    names = ma_device_product_names(rows)
    # ONE read of the fulfillment sheet answers BOTH questions — what a model costs, and what each
    # order-type family did. Reading it twice would let the price list and its coverage read-out
    # drift, which is the divergence this whole mechanism exists to stop.
    _ful = _page(client, "raw_ma_fulfillment", "product_name,number_ordered,price,order_type",
                 {"org_id": org_id})
    out = ma_sold_cost_from_rows(rows, ma_unit_price_from_rows(_ful, names), ma_acct_index)
    out["meta"]["fulfillment_coverage"] = ma_fulfillment_coverage(
        _ful, names, _unbooked_reasons(client, org_id))
    return out


def _unbooked_reasons(client, org_id):
    """The org's own {order_type: stated reason}, through the daily-tx coverage's ONE loader —
    never a second copy of the same config read. ADAPTIVE: any failure degrades to {}."""
    try:
        from app.modules.account.ma_store_pnl import load_unbooked_reasons
        return load_unbooked_reasons(client, org_id)
    except Exception:                                             # pragma: no cover - defensive
        return {}


def ma_sold_cost_from_rows(rows, price, ma_acct_index=None, skip_imeis=None):
    """PURE: the body of `_ma_sold_cost` above — commission rows × a price map → device cost.

    FACTORED OUT 2026-10-12 (§COMMCALC-03) so the takeover evaluation costs a PROSPECT's activated
    devices through this one answer rather than a second copy. `_ma_sold_cost` is now the I/O wrapper
    and is byte-identical to its pre-factoring self (`harness_prospect_eval.py` §D).

    `skip_imeis` is the ONLY addition, and it is inert for every existing caller (`None` ⇒ the
    pre-existing behaviour exactly): the takeover report's "take these phones out of profitability"
    sub-report drops the handsets sitting on a flagged multi-line account, and a dropped line's
    handset has to leave the COGS side too or its revenue would be measured against a cost that
    stayed. What was skipped is COUNTED on `meta.skipped_imeis`, never silent.
    """
    idx = ma_acct_index or {}
    skip = {str(i).strip() for i in (skip_imeis or ()) if str(i).strip()}
    by_imei = {}
    no_imei = 0
    for r in rows:
        imei = (r.get("imei") or "").strip()
        if not imei:
            no_imei += 1
            continue
        # first row wins; a repeat of the same IMEI is the SAME physical handset
        by_imei.setdefault(imei, ((r.get("sku") or "").strip(),
                                  str(r.get("merchant_account_id") or "").strip()))
    cost, priced, unknown_sku, unpriced, skipped = 0.0, 0, 0, 0, 0
    detail, by_store, unpriced_detail = {}, {}, {}
    for imei, (sku, acct) in by_imei.items():
        if imei in skip:
            skipped += 1
            continue
        low = sku.lower()
        if not sku or low == _MA_UNKNOWN_SKU:
            unknown_sku += 1
            continue
        unit = price.get(low)
        if unit is None:
            unpriced += 1
            # NAMED, not just counted (owner report 2026-10-19). A bare count cannot be acted on:
            # the models are what say whether a price list is missing a family or a month.
            unpriced_detail[sku] = unpriced_detail.get(sku, 0) + 1
            continue
        cost += unit
        priced += 1
        detail[sku] = round(detail.get(sku, 0.0) + unit, 2)
        addr = idx.get(acct)
        if addr:
            by_store[addr] = round(by_store.get(addr, 0.0) + unit, 2)
    return {
        "cost": round(cost, 2),
        "detail": detail,
        # per-store slice of `cost` (the remainder is company-wide); {} without an index
        "by_store": by_store,
        "meta": {"source": "raw_ma_commission x raw_ma_fulfillment",
                 "rows": len(rows), "distinct_imei": len(by_imei), "dedup_dropped": len(rows) - len(by_imei),
                 "priced": priced, "unknown_sku": unknown_sku, "unpriced_sku": unpriced,
                 "rows_without_imei": no_imei, "price_list_skus": len(price),
                 "skipped_imeis": skipped, "unpriced_sku_detail": unpriced_detail,
                 "store_mapped_accounts": len(idx)},
    }


def _vip_sold_cost(client, org_id, pm, py, in_period, resolve_store, activation=None):
    """Consignment COGS: the amount the distributor BILLED for each unit, recognised when the unit
    SOLD (policy §9 C2). Per-store, because `asset_ledger` carries a store.

    Dedup by `esn_imei` for the same reason as the MA path. Fee-only categories are excluded — those
    are already booked on `vip_fees` by `coa` and are not device cost.

    WHICH store a unit's cost lands on is NOT decided here: every unit goes through `cost_store`
    (§70), the one home, with `activation` carrying §65's per-device verdict. Undeclared, that
    returns the ledger's own store for every unit and this function is byte-identical. `moved` counts
    what changed hands and `withheld` what stayed put unmeasured, both reported on `meta` so a
    restated store figure is never silent.
    """
    _FEE_CATS = {c.strip().upper() for c in FEE_CATEGORIES}      # ONE HOME, dereferenced
    rows = _page(client, "asset_ledger",
                 "esn_imei,store,category,owed_to_vip,date_sold", {"org_id": org_id})
    seen, by_store, detail = set(), {}, {}
    sold, dup = 0, 0
    moved = {"devices": 0, "amount": 0.0, "pairs": set()}
    held = {"devices": 0, "amount": 0.0}
    for r in rows:
        cat = (r.get("category") or "").strip().upper()
        if cat in _FEE_CATS:
            continue
        if not in_period(r.get("date_sold"), pm, py):
            continue
        imei = (r.get("esn_imei") or "").strip()
        if imei:
            if imei in seen:
                dup += 1
                continue
            seen.add(imei)
        owed = safe_float(r.get("owed_to_vip"))
        if not owed:
            continue
        st = resolve_store(r.get("store")) if r.get("store") else None
        placed = cost_store(st, imei, activation)
        if placed["basis"] == PLACED_ACTIVATION:
            moved["devices"] += 1
            moved["amount"] = round(moved["amount"] + owed, 2)
            moved["pairs"].add((placed["moved_from"], placed["store"]))
        elif placed["withheld"]:
            held["devices"] += 1
            held["amount"] = round(held["amount"] + owed, 2)
        key = placed["store"] or "(company-wide)"
        by_store[key] = round(by_store.get(key, 0.0) + owed, 2)
        label = (r.get("category") or "Consignment device").strip()
        detail[label] = round(detail.get(label, 0.0) + owed, 2)
        sold += 1
    total = round(sum(by_store.values()), 2)
    return {
        "cost": total,
        "by_store": by_store,
        "detail": detail,
        "meta": {"source": "asset_ledger (consignment, billed=COGS)",
                 "rows": len(rows), "sold_in_period": sold, "dedup_dropped": dup,
                 # The restatement, stated: what moved to the activating store, between how many
                 # store pairs, and what stayed put because §65 could not measure it.
                 "cost_store_basis": (activation or {}).get("basis") or STORE_BASIS_STOCKING,
                 "cost_store_reason": (activation or {}).get("reason") or "",
                 "moved_to_activating_store": {"devices": moved["devices"],
                                               "amount": moved["amount"],
                                               "store_pairs": len(moved["pairs"])},
                 "unmeasured_stayed_put": {"devices": held["devices"], "amount": held["amount"]}},
    }


def unpriced_handsets_note(meta):
    """PURE: the device-cost `meta` -> the sentence a reader needs, or None when nothing was dropped.

    RULING K3(b) GENERALISED (owner directive 2026-09-20, "one fact, one home, dereferenced"). A
    declared zero already had to say so on the statement because `meta` alone never reaches a
    reader — `engine._assemble` rebuilds each line from key/label/amount/kind/detail. A handset
    whose cost could not be priced is the SAME shape and was silent: its REBATE books regardless,
    so the contra-COGS stands against a cost that is short by exactly the dropped handsets. That is
    how LuxeLink September 2026 came to show −$180,191.91 of rebates against $164,730.35 of cost.

    Says the count, the money it cannot name (it has no price — that is the point), and the models,
    so the fix is visible as a missing price list rather than as a negative cost of goods.
    """
    ma = (meta or {}).get("ma") or {}
    n = int(ma.get("unpriced_sku") or 0)
    if not n:
        return None
    det = ma.get("unpriced_sku_detail") or {}
    models = sorted(det.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    named = ", ".join("%s x%d" % (m, c) for m, c in models)
    more = len(det) - len(models)
    if named and more > 0:
        named = " Biggest: %s, and %d more model(s)." % (named, more)
    elif named:
        named = " Biggest: %s." % named
    return ("%s handset(s) activated this period are NOT on the distributor's fulfillment sheet, so "
            "they book NO device cost while their purchase rebate books in full — device cost is "
            "understated by whatever those handsets cost, and the rebate line is not short by the "
            "same amount.%s Upload the fulfillment sheet covering them to measure it."
            % ("{:,}".format(n), named))


def resolve(client, org_id, period_keys, pm, py, in_period, resolve_store, mode,
            ma_acct_index=None, period=None):
    """The one entry point `coa.build_inputs` calls.

    Returns a dict:
      {"active": bool,          # did an INVOICE source answer? (False ⇒ caller keeps POS cost)
       "company_wide": float,   # MA device cost the account→store index could not place
       "by_store": {store: amt},# VIP consignment cost + mig-314 store-mapped MA cost
       "detail": {label: amt},
       "meta": {...}}           # always populated, including the un-linkable remainder

    `mode` comes from `account_config.device_cogs_mode` (mig 621):
      'off' / 'pos'  → returns active=False immediately; caller books POS cost. PRE-621 BEHAVIOUR.
      'auto'         → invoice-first; caller falls back to POS only if no invoice source answered.
      'invoice'      → invoice only; caller NEVER books POS cost (correct where POS cost is negative).

    `period` is the period SPELLING, needed only to ask §65 where each device was activated (§70).
    None means no activation index is read and every cost books to the distributor ledger's store —
    the pre-§70 behaviour.

    NEVER RAISES. Any failure degrades to active=False, i.e. exactly the pre-621 POS behaviour, with
    the reason recorded in `meta` so a silent $0 is never mistaken for a real zero.
    """
    out = {"active": False, "company_wide": 0.0, "by_store": {}, "detail": {},
           "meta": {"mode": mode}}
    if mode not in ("auto", "invoice"):
        out["meta"]["skipped"] = "mode=%s (POS recognition)" % mode
        return out

    # WHERE each device was activated, read ONCE for the period through the one home (§70). On the
    # undeclared default this is a cheap no-op that moves nothing; the expensive reconciliation runs
    # only for an org that has declared the activating basis.
    _act = {"move": False}
    try:
        _act = load_activation_index(client, org_id, period)
    except Exception as e:                                        # pragma: no cover - defensive
        _log.warning("device_cogs: activation index unavailable — %s: %s", type(e).__name__, e)
    out["meta"]["cost_store"] = {k: _act.get(k) for k in ("basis", "basis_source", "move", "reason")}
    out["meta"]["cost_store"]["totals"] = _act.get("totals") or {}

    ma, vip = None, None
    try:
        ma = _ma_sold_cost(client, org_id, period_keys, ma_acct_index=ma_acct_index)
    except Exception as e:
        _log.warning("device_cogs: MA/VidaPay invoice source unavailable — %s: %s", type(e).__name__, e)
        out["meta"]["ma_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        vip = _vip_sold_cost(client, org_id, pm, py, in_period, resolve_store, activation=_act)
    except Exception as e:
        _log.warning("device_cogs: VIP consignment source unavailable — %s: %s", type(e).__name__, e)
        out["meta"]["vip_error"] = "%s: %s" % (type(e).__name__, e)

    if ma and ma["cost"]:
        # per-store slice first (mig 314, only when an account→store index was passed);
        # whatever the index could not name stays company-wide — total unchanged either way.
        _ma_store = ma.get("by_store") or {}
        _store_total = round(sum(_ma_store.values()), 2)
        for st, amt in _ma_store.items():
            out["by_store"][st] = round(out["by_store"].get(st, 0.0) + amt, 2)
        out["company_wide"] = round(ma["cost"] - _store_total, 2)
        for k, v in ma["detail"].items():
            out["detail"][k] = round(out["detail"].get(k, 0.0) + v, 2)
        out["meta"]["ma"] = ma["meta"]
        out["active"] = True
    elif ma:
        out["meta"]["ma"] = ma["meta"]

    if vip and vip["cost"]:
        for st, amt in vip["by_store"].items():
            if st == "(company-wide)":
                out["company_wide"] = round(out["company_wide"] + amt, 2)
            else:
                out["by_store"][st] = round(out["by_store"].get(st, 0.0) + amt, 2)
        for k, v in vip["detail"].items():
            out["detail"][k] = round(out["detail"].get(k, 0.0) + v, 2)
        out["meta"]["vip"] = vip["meta"]
        out["active"] = True
    elif vip:
        out["meta"]["vip"] = vip["meta"]

    # 'invoice' mode suppresses the POS fallback even when no invoice answered — the caller must not
    # book a NEGATIVE POS device cost just because the distributor sheet is missing for a month. That
    # is the honest-zero-with-reason ruling K3(b) requires for the pre-`raw_ma_commission` months.
    if mode == "invoice" and not out["active"]:
        out["active"] = True
        out["meta"]["honest_zero"] = (
            "mode=invoice and no invoice source produced a cost for this period — device COGS is $0.00 "
            "BY DECLARATION, not by measurement. The POS fallback is deliberately NOT used because POS "
            "cost on a subsidised handset is negative.")
    return out
